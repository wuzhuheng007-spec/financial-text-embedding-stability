"""Isolated synthetic process and SQLite fault drills; owner stores untouched."""
from __future__ import annotations
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from scripts.news_sentiment_factor_v41.score import init as score_init
from scripts.news_sentiment_factor_v42 import worker as w


def child(args):
    mode=args[0]
    if mode=="lock":
        w.LOCK=Path(args[1]); w.PRIVATE=w.LOCK.parent
        try:
            with w.exclusive_lock():
                pass
        except RuntimeError:
            os._exit(31)
        os._exit(0)
    queue,score,key,source_hash,received,config_hash=map(str,args[1:])
    with closing(sqlite3.connect(queue)) as con, con:
        con.execute("UPDATE jobs SET state='STARTED',attempts=1 WHERE article_id=?",(key,))
        con.execute("INSERT INTO attempts(provider,article_id,version,attempt_number,started_at,state,run_id) VALUES('jin10',?,1,1,?,'STARTED','SYNTHETIC')",(key,w.now()))
    if mode=="committed":
        with closing(sqlite3.connect(score)) as con, con:
            con.execute("""INSERT INTO articles(provider,article_id,version,title,body,first_received_at_utc,
                received_at_utc,source_text_hash,raw_response_hash,acquisition_batch,cohort,source_version_status)
                VALUES('jin10',?,1,'合成标题','合成测试新闻。',?,?,?,'dummy','synthetic',
                'PROSPECTIVE_RECEIPT','SYNTHETIC')""",(key,received,received,source_hash))
            con.execute("""INSERT INTO scores(provider,article_id,version,segment_id,segment_rule_version,
                model_revision,config_sha256,input_sha256,token_count,status,device,started_at_utc,
                scored_at_utc,received_at_utc,score_origin,run_id)
                VALUES('jin10',?,1,'B0001',?,?,?,'synthetic-input',10,'DONE','cpu',?,?,?,
                'PROSPECTIVE_RECEIPT','SYNTHETIC')""",
                (key,w.SEGMENT_RULE,w.MODEL_ID,config_hash,received,w.now(),received))
    os._exit(23 if mode=="committed" else 19)


def insert_feed(path,key,stamp,content="合成测试新闻。"):
    with closing(sqlite3.connect(path)) as con, con:
        con.execute("INSERT OR IGNORE INTO news_items(item_key,first_received_at) VALUES(?,?)",(key,stamp))
        con.execute("""INSERT INTO news_versions(item_key,version,provider_id,title,content,source_url,
            published_raw,published_at_utc,received_at,content_sha256,raw_item_json,owner_id)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (key,1,key,"合成标题",content,None,None,stamp,stamp,
             hashlib.sha256(content.encode()).hexdigest(),"{}",1))


def main():
    results=[]
    w.PRIVATE.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="v42_fault_",dir=w.PRIVATE,ignore_cleanup_errors=True) as folder:
        base=Path(folder); feed=base/"feed.sqlite3"; scores=base/"score.sqlite3"; queue=base/"queue.sqlite3"
        with closing(sqlite3.connect(feed)) as con, con:
            con.executescript("""CREATE TABLE owner_binding(singleton INTEGER PRIMARY KEY,owner_id INTEGER);
              INSERT INTO owner_binding VALUES(1,1);
              CREATE TABLE news_items(item_key TEXT PRIMARY KEY,first_received_at TEXT);
              CREATE TABLE news_versions(item_key TEXT,version INTEGER,provider_id TEXT,title TEXT,content TEXT,
                source_url TEXT,published_raw TEXT,published_at_utc TEXT,received_at TEXT,content_sha256 TEXT,
                raw_item_json TEXT,owner_id INTEGER,PRIMARY KEY(item_key,version));""")
        score_init(scores)
        with patch.object(w,"FEED_DB",feed),patch.object(w,"SCORE_DB",scores),patch.object(w,"QUEUE_DB",queue),patch.object(w,"PRIVATE",base),patch.object(w,"LOCK",base/"worker.lock"):
            insert_feed(feed,"B","2026-11-17T07:40:00.000000Z")
            con=w.open_queue()
            assert w.discover(con)==1
            con.close()
            insert_feed(feed,"C","2026-11-17T07:41:00.000000Z")
            con=w.open_queue()
            assert w.discover(con,full_audit=True)==1
            results.append(("normal_stop_new_cache_restart","PASS"))
            insert_feed(feed,"A","2026-11-17T07:41:00.000000Z")
            assert w.discover(con)==1 and w.discover(con)==0
            results.append(("same_timestamp_late_lower_key","PASS"))
            for key,mode in (("B","started"),("C","committed")):
                job=con.execute("SELECT * FROM jobs WHERE article_id=?",(key,)).fetchone()
                proc=subprocess.run([sys.executable,"-m","scripts.news_sentiment_factor_v42.fault_drills",
                                     "--child",mode,str(queue),str(scores),key,job["source_hash"],
                                     job["received_at"],job["config_sha256"]],check=False)
                assert proc.returncode==(19 if mode=="started" else 23)
            repaired=w.reconcile(con)
            assert con.execute("SELECT state FROM jobs WHERE article_id='B'").fetchone()[0]=="RECOVERY_PENDING"
            assert con.execute("SELECT state FROM jobs WHERE article_id='C'").fetchone()[0]=="DONE"
            results.extend([("started_process_interrupted","PASS"),("score_committed_before_job_update","PASS")])
            with patch.object(w,"status",return_value={"process_alive_identity":True,"state":"DAILY_BUDGET_PAUSED"}):
                try:
                    w.start()
                    raise AssertionError("duplicate start accepted")
                except RuntimeError as exc:
                    assert str(exc)=="WORKER_ALREADY_RUNNING"
            results.append(("budget_pause_duplicate_start","PASS"))
            with w.exclusive_lock():
                proc=subprocess.run([sys.executable,"-m","scripts.news_sentiment_factor_v42.fault_drills",
                                     "--child","lock",str(base/"worker.lock")],check=False)
                assert proc.returncode==31
            results.append(("two_process_lock_contention","PASS"))
            blocker=sqlite3.connect(queue,timeout=1)
            blocker.execute("BEGIN IMMEDIATE")
            seen=[]
            def writer():
                try:
                    other=w.open_queue()
                    other.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('busy_probe','ok')")
                    other.commit();other.close();seen.append("RECOVERED")
                except sqlite3.Error as exc:
                    seen.append(type(exc).__name__)
            thread=threading.Thread(target=writer);thread.start()
            time.sleep(.25);blocker.rollback();blocker.close();thread.join(timeout=5)
            assert seen==["RECOVERED"]
            results.append(("transient_database_busy","PASS"))
            with closing(sqlite3.connect(feed)) as raw, raw:
                raw.execute("UPDATE news_versions SET content='变更内容但版本号未变' WHERE item_key='A'")
            w.discover(con,full_audit=True)
            assert con.execute("SELECT state FROM jobs WHERE article_id='A'").fetchone()[0]=="QUARANTINED"
            results.append(("same_version_hash_collision","PASS"))
            con.close()
    payload={"schema":"news_sentiment_v42_synthetic_fault_drills_1",
             "drills":[{"case":name,"actual":status} for name,status in results],
             "passed":len(results),"total":len(results),"isolated_private_database":True,
             "real_supplier_or_model_calls":0}
    w.write_json(w.PUBLIC/"fault_drills_redacted.json",payload)
    return payload


if __name__=="__main__":
    if len(sys.argv)>1 and sys.argv[1]=="--child":
        child(sys.argv[2:])
    else:
        print(json.dumps(main(),ensure_ascii=False))
