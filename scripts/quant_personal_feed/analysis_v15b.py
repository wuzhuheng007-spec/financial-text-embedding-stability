"""Owner-only, bounded Qwen news analysis queue. No provider calls or trading."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .analysis_contract_v15b import PROMPT_VERSION, build_input, parse_response, validate
from .relevance_v15 import rank_reason, screen

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / 'configs/quant_portal_personal_v15_20261116.json'
FEED = ROOT / 'apps/quant_portal/var/personal_feeds.sqlite3'
PORTAL = ROOT / 'apps/quant_portal/var/personal.sqlite3'
ANALYSIS = ROOT / 'apps/quant_portal/var/personal_analysis_v15b.sqlite3'
STOP = ROOT / 'apps/quant_portal/var/personal_analysis_v15b.stop'
PID = ROOT / 'apps/quant_portal/var/personal_analysis_v15b.pid.json'


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def config() -> dict:
    cfg = json.loads(CONFIG.read_text(encoding='utf-8'))
    if cfg['model']['revision'] != 'aa8e72537993ba99e69dfaafa59ed015b17504d1':
        raise ValueError('Model revision changed')
    return cfg


def owner_id() -> int:
    feed = json.loads((ROOT / 'configs/quant_portal_personal_feeds_v11_20261116.json').read_text(encoding='utf-8'))
    portal = ROOT / feed['portal_db']
    with sqlite3.connect(portal.resolve().as_uri()+'?mode=ro', uri=True) as db:
        row = db.execute('SELECT id FROM auth_user WHERE username=?', (feed['owner_username'],)).fetchone()
    if row is None:
        raise RuntimeError('Owner unavailable')
    return row[0]


def open_db(path: Path = ANALYSIS) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA busy_timeout=10000')
    db.executescript('''
      CREATE TABLE IF NOT EXISTS analysis_jobs (
        owner_id INTEGER NOT NULL, item_key TEXT NOT NULL, version INTEGER NOT NULL,
        content_sha256 TEXT NOT NULL, event_group_id TEXT NOT NULL,
        status TEXT NOT NULL, priority INTEGER NOT NULL, enqueued_at TEXT NOT NULL,
        started_at TEXT, completed_at TEXT, model_revision TEXT NOT NULL,
        prompt_version TEXT NOT NULL, input_sha256 TEXT,
        input_characters INTEGER, truncated INTEGER,
        result_json TEXT, validation_json TEXT, error_class TEXT,
        superseded_by_version INTEGER,
        PRIMARY KEY(owner_id,item_key,version));
      CREATE INDEX IF NOT EXISTS analysis_queue ON analysis_jobs(owner_id,status,priority DESC,enqueued_at);
      CREATE TABLE IF NOT EXISTS analysis_attempts (
        id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL, item_key TEXT NOT NULL,
        version INTEGER NOT NULL, attempt INTEGER NOT NULL,
        started_at TEXT NOT NULL, ended_at TEXT, raw_response TEXT,
        raw_sha256 TEXT, parse_mode TEXT, parsed_view_sha256 TEXT,
        error_class TEXT, validation_json TEXT, elapsed_seconds REAL,
        generated_tokens INTEGER, UNIQUE(owner_id,item_key,version,attempt));
      CREATE TABLE IF NOT EXISTS analysis_worker_state (
        owner_id INTEGER PRIMARY KEY, pid INTEGER, heartbeat_at TEXT,
        state TEXT NOT NULL, last_error_class TEXT);
    ''')
    return db


def _feed_rows(owner: int, limit: int = 500) -> list[dict]:
    with sqlite3.connect(FEED.resolve().as_uri()+'?mode=ro', uri=True, timeout=10) as db:
        db.row_factory = sqlite3.Row
        binding = db.execute('SELECT owner_id FROM owner_binding WHERE singleton=1').fetchone()
        if not binding or binding[0] != owner:
            raise RuntimeError('Feed owner mismatch')
        rows = db.execute('''SELECT v.item_key,v.version,v.content_sha256,v.title,v.content,
            v.source_url,v.published_at_utc,v.received_at FROM news_versions v
            JOIN news_items i ON i.item_key=v.item_key AND i.latest_version=v.version
            JOIN news_screening s ON s.content_sha256=v.content_sha256 AND s.status='INCLUDE'
            WHERE v.owner_id=? ORDER BY v.received_at DESC,v.item_key LIMIT ?''', (owner,limit)).fetchall()
        return [dict(row) for row in rows]


def enqueue(db: sqlite3.Connection, owner: int, cfg: dict,
            allowed_items: set[tuple[str,int]] | None = None) -> dict:
    rows = _feed_rows(owner)
    if allowed_items is not None:
        rows = [row for row in rows if (row['item_key'],row['version']) in allowed_items]
    scored = []
    for row in rows:
        decision = screen(row)
        if decision.status != 'INCLUDE':
            continue
        rank = rank_reason(decision,published_at_utc=row['published_at_utc'],
                           received_at=row['received_at'],source_url=row['source_url'],
                           version=row['version'],weights=cfg['top_weights'])
        scored.append((rank['score'],row,decision))
    scored.sort(key=lambda x:(-x[0],x[1]['item_key']))
    top = set()
    for _, row, dec in scored:
        if len(top) >= cfg['top_limit']:
            break
        top.add(dec.event_group_id)
    queued = 0
    active = db.execute("SELECT COUNT(*) FROM analysis_jobs WHERE owner_id=? AND status IN ('PENDING','RUNNING')",(owner,)).fetchone()[0]
    for score,row,dec in scored:
        exists = db.execute('SELECT 1 FROM analysis_jobs WHERE owner_id=? AND item_key=? AND version=?',
                            (owner,row['item_key'],row['version'])).fetchone()
        if exists or active >= cfg['analysis_worker']['queue_active_limit']:
            continue
        priority = score + (1000 if dec.event_group_id in top else 0)
        db.execute('''INSERT INTO analysis_jobs(owner_id,item_key,version,content_sha256,event_group_id,
          status,priority,enqueued_at,model_revision,prompt_version) VALUES(?,?,?,?,?,'PENDING',?,?,?,?)''',
          (owner,row['item_key'],row['version'],row['content_sha256'],dec.event_group_id,priority,now(),
           cfg['model']['revision'],PROMPT_VERSION))
        db.execute('''UPDATE analysis_jobs SET superseded_by_version=?,status='SUPERSEDED'
          WHERE owner_id=? AND item_key=? AND version<? AND status IN ('PENDING','COMPLETE','INSUFFICIENT_EVIDENCE')''',
          (row['version'],owner,row['item_key'],row['version']))
        queued += 1; active += 1
    return {'eligible':len(scored),'new_jobs':queued,'active':active}


def _source(owner:int, key:str, version:int) -> dict | None:
    with sqlite3.connect(FEED.resolve().as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        row=db.execute('''SELECT title,content,content_sha256 FROM news_versions
          WHERE owner_id=? AND item_key=? AND version=?''',(owner,key,version)).fetchone()
        return dict(row) if row else None


def _kill_tree(proc:subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name=='nt':
        subprocess.run(['taskkill.exe','/PID',str(proc.pid),'/T','/F'],capture_output=True,timeout=15)
    else:
        proc.kill()
    proc.wait(timeout=20)


def _generation(request:Path, response:Path, cfg:dict) -> tuple[dict|None,str|None,float]:
    executable=os.environ.get('QUANT_V15_MODEL_PYTHON')
    if not executable or not Path(executable).is_file():
        return None,'MODEL_PYTHON_UNAVAILABLE',0.0
    started=time.monotonic()
    proc=subprocess.Popen([executable,'-m','scripts.quant_personal_feed.generate_v15b',
                           '--request',str(request),'--response',str(response)],
                          cwd=ROOT,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    loaded=request.with_suffix('.loaded')
    loaded.unlink(missing_ok=True)
    load_deadline=started+cfg['analysis_worker']['model_load_timeout_seconds']
    while proc.poll() is None and not loaded.exists() and time.monotonic()<load_deadline:
        time.sleep(0.25)
    if proc.poll() is None and not loaded.exists():
        _kill_tree(proc)
        return None,'MODEL_LOAD_TIMEOUT_PROCESS_TERMINATED',time.monotonic()-started
    if loaded.exists() and proc.poll() is None:
        try:
            proc.wait(timeout=cfg['analysis_worker']['per_item_timeout_seconds'])
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            return None,'HARD_TIMEOUT_PROCESS_TERMINATED',time.monotonic()-started
    elapsed=time.monotonic()-started
    if proc.returncode or not response.exists():
        return None,'MODEL_PROCESS_FAILED',elapsed
    try:
        return json.loads(response.read_text(encoding='utf-8')),None,elapsed
    except (json.JSONDecodeError,OSError):
        return None,'MODEL_RESPONSE_FILE_INVALID',elapsed


def process_one(db:sqlite3.Connection,owner:int,cfg:dict,job:sqlite3.Row) -> dict:
    key,version=job['item_key'],job['version']
    source=_source(owner,key,version)
    if not source or source['content_sha256']!=job['content_sha256']:
        db.execute("UPDATE analysis_jobs SET status='FAILED',error_class='INPUT_VERSION_MISSING',completed_at=? WHERE owner_id=? AND item_key=? AND version=?",
                   (now(),owner,key,version))
        return {'status':'FAILED','error':'INPUT_VERSION_MISSING'}
    selected,meta=build_input(source['title'],source['content'],max_characters=cfg['analysis_worker']['max_input_characters'])
    db.execute("UPDATE analysis_jobs SET status='RUNNING',started_at=?,input_sha256=?,input_characters=?,truncated=? WHERE owner_id=? AND item_key=? AND version=?",
               (now(),meta['input_sha256'],meta['model_input_characters'],int(meta['truncated']),owner,key,version))
    private=ROOT/'apps/quant_portal/var/analysis_v15b_requests'
    private.mkdir(parents=True,exist_ok=True)
    raw_prior=None
    for attempt in range(1,3):
        request=private/f'{key}_{version}_{attempt}.request.json'
        response=private/f'{key}_{version}_{attempt}.response.json'
        request.write_text(json.dumps({'source_text':selected,'repair_response':raw_prior,
                 'model_revision':cfg['model']['revision'],'max_input_tokens':cfg['analysis_worker']['max_input_tokens'],
                 'max_new_tokens':cfg['analysis_worker']['max_new_tokens'],'seed':cfg['analysis_worker']['seed']},
                 ensure_ascii=False),encoding='utf-8')
        start=now(); output,error,elapsed=_generation(request,response,cfg)
        raw=output.get('raw') if isinstance(output,dict) else None
        token_count=output.get('generated_tokens') if isinstance(output,dict) else None
        mode=None; view=None; parsed=None; validation=None
        if error is None and isinstance(output,dict) and output.get('error'):
            error=str(output['error'])
        if error is None and not isinstance(raw,str): error='MODEL_RAW_MISSING'
        if error is None:
            try:
                parsed,mode,view=parse_response(raw)
                validation=validate(parsed,selected)
                if not validation['valid']: error='CONTRACT_INVALID'
            except (ValueError,json.JSONDecodeError) as exc:
                error='PARSE_'+type(exc).__name__
        db.execute('''INSERT INTO analysis_attempts(owner_id,item_key,version,attempt,started_at,ended_at,
          raw_response,raw_sha256,parse_mode,parsed_view_sha256,error_class,validation_json,elapsed_seconds,generated_tokens)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
          (owner,key,version,attempt,start,now(),raw,hashlib.sha256(raw.encode()).hexdigest() if raw else None,
           mode,hashlib.sha256(view.encode()).hexdigest() if view else None,error,
           json.dumps(validation) if validation else None,elapsed,token_count))
        if error is None:
            # Post-batch release gate: a missing exact source quote cannot be
            # marked mechanically complete, even if impact prose was emitted.
            status='INSUFFICIENT_EVIDENCE' if parsed['quote'] is None else 'COMPLETE'
            db.execute('''UPDATE analysis_jobs SET status=?,result_json=?,validation_json=?,completed_at=?,error_class=NULL
              WHERE owner_id=? AND item_key=? AND version=?''',
              (status,json.dumps(parsed,ensure_ascii=False),json.dumps(validation),now(),owner,key,version))
            return {'status':status,'attempts':attempt,'mode':mode}
        if error in {'HARD_TIMEOUT_PROCESS_TERMINATED','MODEL_LOAD_TIMEOUT_PROCESS_TERMINATED','MODEL_PROCESS_FAILED','MODEL_PYTHON_UNAVAILABLE','INPUT_TOKEN_BUDGET'}:
            break
        raw_prior=raw
    db.execute("UPDATE analysis_jobs SET status='FAILED',error_class=?,completed_at=? WHERE owner_id=? AND item_key=? AND version=?",
               (error,now(),owner,key,version))
    return {'status':'FAILED','error':error,'attempts':attempt}


def run(max_items:int|None=None, frozen_batch:Path|None=None) -> None:
    cfg=config(); owner=owner_id(); db=open_db()
    allowed=None
    if frozen_batch is not None:
        batch=json.loads(frozen_batch.read_text(encoding='utf-8'))
        allowed={(x['item_key'],x['version']) for x in batch['items']}
        if len(allowed)!=len(batch['items']) or len(allowed)>20:
            raise ValueError('Invalid bounded frozen batch')
    STOP.unlink(missing_ok=True)
    PID.write_text(json.dumps({'pid':os.getpid(),'started_at':now()}),encoding='utf-8')
    done=0; stopping=False
    def stop(_signal,_frame):
        nonlocal stopping
        stopping=True
    signal.signal(signal.SIGINT,stop)
    signal.signal(signal.SIGTERM,stop)
    try:
        while not stopping and not STOP.exists():
            db.execute("INSERT INTO analysis_worker_state(owner_id,pid,heartbeat_at,state) VALUES(?,?,?,'RUNNING') ON CONFLICT(owner_id) DO UPDATE SET pid=excluded.pid,heartbeat_at=excluded.heartbeat_at,state=excluded.state",(owner,os.getpid(),now()))
            enqueue(db,owner,cfg,allowed_items=allowed)
            if allowed is None:
                job=db.execute("SELECT * FROM analysis_jobs WHERE owner_id=? AND status='PENDING' ORDER BY priority DESC,enqueued_at,item_key LIMIT 1",(owner,)).fetchone()
            else:
                job=next((row for row in db.execute("SELECT * FROM analysis_jobs WHERE owner_id=? AND status='PENDING' ORDER BY priority DESC,enqueued_at,item_key",(owner,))
                         if (row['item_key'],row['version']) in allowed),None)
            if job:
                result=process_one(db,owner,cfg,job)
                done+=1
                if result.get('error') in {'HARD_TIMEOUT_PROCESS_TERMINATED','MODEL_LOAD_TIMEOUT_PROCESS_TERMINATED'}:
                    db.execute("UPDATE analysis_worker_state SET state='STOPPED_TIMEOUT',heartbeat_at=? WHERE owner_id=?",(now(),owner))
                    break
                if max_items is not None and done>=max_items: break
            elif allowed is not None:
                break
            else:
                time.sleep(cfg['analysis_worker']['poll_seconds'])
    finally:
        db.execute("UPDATE analysis_worker_state SET state='STOPPED',heartbeat_at=? WHERE owner_id=?",(now(),owner))
        db.close(); PID.unlink(missing_ok=True); STOP.unlink(missing_ok=True)


def main() -> None:
    p=argparse.ArgumentParser(); p.add_argument('action',choices=['run','status','stop','enqueue'])
    p.add_argument('--max-items',type=int)
    p.add_argument('--frozen-batch',type=Path)
    args=p.parse_args()
    if args.action=='stop': STOP.write_text('stop\n',encoding='ascii'); return
    if args.action=='status':
        with open_db() as db:
            counts={r['status']:r['n'] for r in db.execute('SELECT status,COUNT(*) n FROM analysis_jobs GROUP BY status')}
            state=db.execute('SELECT * FROM analysis_worker_state WHERE owner_id=?',(owner_id(),)).fetchone()
            print(json.dumps({'counts':counts,'worker':dict(state) if state else None},ensure_ascii=False)); return
    if args.action=='enqueue':
        with open_db() as db: print(json.dumps(enqueue(db,owner_id(),config()))); return
    if PID.exists():
        record=json.loads(PID.read_text(encoding='utf-8'))
        try:
            os.kill(record['pid'],0)
        except OSError: pass
        else: raise RuntimeError('Analysis worker already running')
    run(args.max_items,args.frozen_batch)

if __name__=='__main__': main()
