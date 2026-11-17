"""Persistent single-instance research worker over the existing local feed cache.

No supplier request, return label, or model change is made here. Private text and
scores stay in the existing ignored v4.1 SQLite store; queue metadata is separate.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import traceback
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.news_sentiment_factor_v41.freeze import FEED_DB, ROOT, has_chinese, now, sha
from scripts.news_sentiment_factor_v41.score import DB as SCORE_DB, MODEL_ID, init as score_init, load_model
from scripts.news_sentiment_factor_v41.worker import article_from_row, score_article, utc

CFG = ROOT / "configs/news_sentiment_factor_v42_20261117.json"
PRIVATE = ROOT / "artifacts/news_sentiment_factor_v42_20261117"
PUBLIC = ROOT / "results/news_sentiment_factor_v42_20261117"
QUEUE_DB = PRIVATE / "queue.sqlite3"
STATE = PRIVATE / "worker_state_private.json"
STOP = PRIVATE / "worker_stop.flag"
LOCK = PRIVATE / "worker.lock"
LOG = PRIVATE / "worker_private.log"
PUBLIC_STATE = PUBLIC / "worker_status_redacted.json"
SEGMENT_RULE = "zh_html_source_map_punctuation_numbered_v2"


def config() -> dict:
    return json.loads(CFG.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def filetime(pid: int) -> int | None:
    """OS process creation witness, so a recycled PID cannot pass identity."""
    if not pid or pid <= 0:
        return None
    if os.name == "nt":
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel.GetProcessTimes.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                           ctypes.c_void_p, ctypes.c_void_p]
        handle = kernel.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return None
        try:
            code = ctypes.c_ulong()
            creation = ctypes.c_ulonglong()
            exit_time = ctypes.c_ulonglong()
            kernel_time = ctypes.c_ulonglong()
            user_time = ctypes.c_ulonglong()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
                return None
            if not kernel.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time),
                                          ctypes.byref(kernel_time), ctypes.byref(user_time)):
                return None
            return creation.value
        finally:
            kernel.CloseHandle(handle)
    # Linux test processes: /proc start ticks are stable for this process identity.
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        return int(stat.rsplit(") ", 1)[1].split()[19])
    except (OSError, ValueError, IndexError):
        return None


@contextmanager
def exclusive_lock():
    PRIVATE.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+b") as handle:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("WORKER_ALREADY_RUNNING_LOCKED") from exc
        else:
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("WORKER_ALREADY_RUNNING_LOCKED") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def open_queue() -> sqlite3.Connection:
    PRIVATE.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(QUEUE_DB, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=10000")
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
      CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS jobs (
        provider TEXT NOT NULL,article_id TEXT NOT NULL,version INTEGER NOT NULL,
        segment_rule TEXT NOT NULL,model_revision TEXT NOT NULL,config_sha256 TEXT NOT NULL,
        source_hash TEXT NOT NULL,received_at TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'PENDING',attempts INTEGER NOT NULL DEFAULT 0,
        discovered_at TEXT NOT NULL,completed_at TEXT,error_class TEXT,
        recovery_delayed INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(provider,article_id,version,segment_rule,model_revision,config_sha256));
      CREATE TABLE IF NOT EXISTS attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,provider TEXT NOT NULL,article_id TEXT NOT NULL,
        version INTEGER NOT NULL,attempt_number INTEGER NOT NULL,
        started_at TEXT NOT NULL,ended_at TEXT,state TEXT NOT NULL,error_class TEXT,
        run_id TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS worker_runs (
        run_id TEXT PRIMARY KEY,pid INTEGER,process_creation INTEGER,started_at TEXT NOT NULL,
        stopped_at TEXT,state TEXT NOT NULL,exit_code INTEGER,error_class TEXT,
        last_heartbeat TEXT,stage TEXT,launch_method TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS quarantines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,provider TEXT,article_id TEXT,version INTEGER,
        expected_hash TEXT,observed_hash TEXT,detected_at TEXT NOT NULL,reason TEXT NOT NULL);
    """)
    con.commit()
    return con


def origin_cursor() -> tuple[str, str, int]:
    start = utc(config()["research_origin_utc"]).isoformat(timespec="microseconds").replace("+00:00", "Z")
    return start, "", -1


def feed_after(cursor: tuple[str, str, int]) -> list[dict]:
    with closing(sqlite3.connect(f"file:{FEED_DB.as_posix()}?mode=ro", uri=True, timeout=5)) as con:
        con.row_factory = sqlite3.Row
        owner = con.execute("SELECT owner_id FROM owner_binding WHERE singleton=1").fetchone()
        if owner is None:
            raise ValueError("PERSONAL_FEED_OWNER_MISSING")
        rows = con.execute("""SELECT v.item_key,v.version,v.provider_id,v.title,v.content,v.source_url,
            v.published_raw,v.published_at_utc,v.received_at,v.content_sha256,v.raw_item_json,
            i.first_received_at FROM news_versions v JOIN news_items i USING(item_key)
            WHERE v.owner_id=? AND v.received_at>=?
            ORDER BY v.received_at,v.item_key,v.version""", (owner["owner_id"], cursor[0])).fetchall()
    return [dict(row) for row in rows]


def feed_one(article_id: str, version: int) -> dict | None:
    with closing(sqlite3.connect(f"file:{FEED_DB.as_posix()}?mode=ro", uri=True, timeout=5)) as con:
        con.row_factory = sqlite3.Row
        owner = con.execute("SELECT owner_id FROM owner_binding WHERE singleton=1").fetchone()
        row = con.execute("""SELECT v.item_key,v.version,v.provider_id,v.title,v.content,v.source_url,
            v.published_raw,v.published_at_utc,v.received_at,v.content_sha256,v.raw_item_json,
            i.first_received_at FROM news_versions v JOIN news_items i USING(item_key)
            WHERE v.owner_id=? AND v.item_key=? AND v.version=?""",
            (owner["owner_id"], article_id, version)).fetchone()
    return dict(row) if row else None


def cursor(con: sqlite3.Connection) -> tuple[str, str, int]:
    row = con.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()
    return tuple(json.loads(row[0])) if row else origin_cursor()


def discover(con: sqlite3.Connection, *, full_audit: bool=False) -> int:
    """Materialize jobs and cursor atomically; rescan only the last timestamp tie.

    Once per process start, a full origin audit catches any backdated feed insert
    or source-version mutation that an ordinary forward cursor could miss.
    """
    begin = origin_cursor() if full_audit else cursor(con)
    rows = feed_after(begin)
    if not rows:
        return 0
    latest=cursor(con)
    inserted=0
    from scripts.news_sentiment_factor_v41.prepare_v2 import CFG as SCORE_CFG
    cfg_hash = sha(SCORE_CFG)
    con.execute("BEGIN IMMEDIATE")
    try:
        for row in rows:
            article = article_from_row(row)
            result=con.execute("""INSERT OR IGNORE INTO jobs
                (provider,article_id,version,segment_rule,model_revision,config_sha256,
                 source_hash,received_at,state,discovered_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (article["provider"],article["article_id"],article["version"],SEGMENT_RULE,
                 MODEL_ID,cfg_hash,article["source_text_hash"],article["received_at_utc"],"PENDING",now()))
            inserted+=result.rowcount
            known = con.execute("""SELECT source_hash FROM jobs WHERE provider=? AND article_id=? AND version=?
                AND segment_rule=? AND model_revision=? AND config_sha256=?""",
                (article["provider"],article["article_id"],article["version"],SEGMENT_RULE,MODEL_ID,cfg_hash)).fetchone()
            if known[0] != article["source_text_hash"]:
                quarantine(con, article, known[0], "SOURCE_VERSION_IDENTITY_COLLISION")
            key=(row["received_at"],row["item_key"],row["version"])
            if key>latest:
                latest=key
        con.execute("INSERT INTO meta(key,value) VALUES('cursor',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (json.dumps(latest),))
        con.commit()
    except Exception:
        con.rollback()
        raise
    return inserted


def quarantine(con: sqlite3.Connection, article: dict, expected: str, reason: str) -> None:
    con.execute("""INSERT INTO quarantines(provider,article_id,version,expected_hash,observed_hash,detected_at,reason)
        VALUES(?,?,?,?,?,?,?)""", (article["provider"],article["article_id"],article["version"],expected,
                              article["source_text_hash"],now(),reason))
    con.execute("UPDATE jobs SET state='QUARANTINED',error_class=? WHERE provider=? AND article_id=? AND version=?",
                (reason,article["provider"],article["article_id"],article["version"]))


def committed_score(article: dict) -> tuple[bool, str | None, str | None, bool]:
    with closing(sqlite3.connect(f"file:{SCORE_DB.as_posix()}?mode=ro",uri=True,timeout=5)) as con:
        existing = con.execute("SELECT source_text_hash FROM articles WHERE provider=? AND article_id=? AND version=?",
                               (article["provider"],article["article_id"],article["version"])).fetchone()
        if not existing:
            return False,None,None,False
        if existing[0] != article["source_text_hash"]:
            return False,"SOURCE_VERSION_IDENTITY_COLLISION",None,False
        rows = con.execute("""SELECT status,scored_at_utc FROM scores WHERE provider=? AND article_id=? AND version=?
            AND segment_rule_version=? AND model_revision=?""",
            (article["provider"],article["article_id"],article["version"],SEGMENT_RULE,MODEL_ID)).fetchall()
        latest=max((row[1] for row in rows),key=utc) if rows else None
        partial=any(row[0]!="DONE" for row in rows)
        return bool(rows),None,latest,partial


def reconcile(con: sqlite3.Connection) -> dict:
    repaired = {"committed":0,"interrupted":0,"collision":0}
    rows = con.execute("SELECT * FROM jobs WHERE state IN ('PENDING','STARTED','RECOVERY_PENDING')").fetchall()
    for job in rows:
        raw = feed_one(job["article_id"],job["version"])
        if raw is None:
            con.execute("UPDATE jobs SET state='QUARANTINED',error_class='SOURCE_VERSION_MISSING' WHERE article_id=? AND version=?",
                        (job["article_id"],job["version"]))
            repaired["collision"] += 1
            continue
        article = article_from_row(raw)
        if article["source_text_hash"] != job["source_hash"]:
            quarantine(con,article,job["source_hash"],"SOURCE_VERSION_IDENTITY_COLLISION")
            repaired["collision"] += 1
            continue
        done, collision, actual_scored_at, partial = committed_score(article)
        if collision:
            quarantine(con,article,job["source_hash"],collision)
            repaired["collision"] += 1
        elif done:
            con.execute("UPDATE jobs SET state=?,completed_at=?,error_class=? WHERE article_id=? AND version=?",
                        ("PARTIAL_FAILED" if partial else "DONE",actual_scored_at,
                         "PARTIAL_INFERENCE_FAILURE" if partial else None,job["article_id"],job["version"]))
            con.execute("""UPDATE attempts SET state='DONE_COMMITTED_BEFORE_JOB_UPDATE',ended_at=COALESCE(ended_at,?)
                WHERE article_id=? AND version=? AND state='STARTED'""",(now(),job["article_id"],job["version"]))
            repaired["committed"] += 1
        elif job["state"] == "STARTED":
            con.execute("UPDATE jobs SET state='RECOVERY_PENDING',recovery_delayed=1 WHERE article_id=? AND version=?",
                        (job["article_id"],job["version"]))
            con.execute("""UPDATE attempts SET state='INTERRUPTED',ended_at=COALESCE(ended_at,?)
                WHERE article_id=? AND version=? AND state='STARTED'""",(now(),job["article_id"],job["version"]))
            repaired["interrupted"] += 1
        elif job["state"] == "PENDING":
            # The old worker could have crashed after STARTED. Preserve its
            # attempt and allow only the configured single recovery attempt.
            with closing(sqlite3.connect(f"file:{SCORE_DB.as_posix()}?mode=ro",uri=True,timeout=5)) as old:
                started=old.execute("""SELECT state FROM attempted_versions WHERE provider=? AND article_id=?
                    AND version=? AND segment_rule_version=? AND model_revision=? AND config_sha256=?""",
                    (job["provider"],job["article_id"],job["version"],SEGMENT_RULE,MODEL_ID,job["config_sha256"])).fetchone()
            if started and started[0]=="STARTED":
                con.execute("UPDATE jobs SET state='RECOVERY_PENDING',attempts=1,recovery_delayed=1 WHERE article_id=? AND version=?",
                            (job["article_id"],job["version"]))
                repaired["interrupted"] += 1
    con.commit()
    return repaired


def daily_attempts(con: sqlite3.Connection) -> int:
    shanghai = timezone(timedelta(hours=8))
    today = datetime.now(shanghai).date()
    old = sqlite3.connect(f"file:{SCORE_DB.as_posix()}?mode=ro",uri=True,timeout=5)
    try:
        old_times = [row[0] for row in old.execute("SELECT attempted_at_utc FROM attempted_versions")]
    finally:
        old.close()
    new_times = [row[0] for row in con.execute("SELECT started_at FROM attempts")]
    return sum(utc(value).astimezone(shanghai).date()==today for value in old_times+new_times)


def state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError,ValueError):
        return {"state":"NOT_STARTED"}


def status() -> dict:
    item = state()
    expected = item.get("process_creation")
    actual = filetime(item.get("pid") or 0)
    identity = expected is not None and actual == expected
    heartbeat = item.get("heartbeat_at_utc")
    age = (datetime.now(timezone.utc)-utc(heartbeat)).total_seconds() if heartbeat else None
    result = {k:v for k,v in item.items() if k not in {"pid","process_creation","private_db_path"}}
    result["process_alive_identity"] = identity
    result["heartbeat_age_seconds"] = round(age,1) if age is not None else None
    result["worker_pid"] = item.get("pid")
    if item.get("state") in {"RUNNING","LOADING_MODEL","DAILY_BUDGET_PAUSED","STARTING"}:
        if not identity:
            result["state"] = "STALE_EXIT"
        elif age is not None and age>120:
            result["state"] = "UNRESPONSIVE_HEARTBEAT"
    if QUEUE_DB.exists():
        with closing(sqlite3.connect(f"file:{QUEUE_DB.as_posix()}?mode=ro",uri=True,timeout=3)) as con:
            result["backlog"] = con.execute("SELECT count(*) FROM jobs WHERE state IN ('PENDING','RECOVERY_PENDING')").fetchone()[0]
            result["quarantined"] = con.execute("SELECT count(*) FROM jobs WHERE state='QUARANTINED'").fetchone()[0]
            result["last_success_at_utc"] = con.execute("SELECT max(completed_at) FROM jobs WHERE state='DONE'").fetchone()[0]
            result["daily_attempts"] = daily_attempts(con)
    return result


def update_state(item: dict) -> None:
    write_json(STATE,item)
    write_json(PUBLIC_STATE,{k:v for k,v in status().items() if k not in {"worker_pid"}})


def run(*, minutes: int | None = None, launch_method: str = "CLI") -> dict:
    if minutes is not None and not 1<=minutes<=15:
        raise ValueError("BOUNDED_MINUTES_OUT_OF_RANGE")
    with exclusive_lock():
        from scripts.news_sentiment_factor_v41.worker import status as old_status
        old = old_status()
        if old.get("process_alive") and old.get("state") in {"RUNNING","DAILY_BUDGET_PAUSED"}:
            raise RuntimeError("OLD_V41_WORKER_STILL_ACTIVE")
        score_init()
        con = open_queue()
        run_id = "v42_"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        started = now()
        item = {"schema":"news_sentiment_factor_v42_worker_status_1","run_id":run_id,"state":"STARTING",
                "pid":os.getpid(),"process_creation":filetime(os.getpid()),"started_at_utc":started,
                "heartbeat_at_utc":started,"stage":"QUEUE_RECONCILE","last_success_at_utc":None,
                "daily_limit":config()["queue"]["daily_attempt_budget"],"research_origin_utc":config()["research_origin_utc"],
                "model_revision":MODEL_ID,"segment_rule_version":SEGMENT_RULE,"launch_method":launch_method,
                "no_provider_requests":True,"no_return_labels":True}
        con.execute("""INSERT INTO worker_runs(run_id,pid,process_creation,started_at,state,last_heartbeat,stage,launch_method)
            VALUES(?,?,?,?,?,?,?,?)""",(run_id,item["pid"],item["process_creation"],started,"STARTING",started,item["stage"],launch_method))
        con.commit()
        update_state(item)
        token=model=torch=None
        start_mono=time.monotonic()
        try:
            audited=discover(con,full_audit=True)
            repaired=reconcile(con)
            item["reconciled_at_start"]=repaired
            item["audited_new_jobs_at_start"]=audited
            # A version received while no research process was running is
            # delayed even when discovered for the first time at this start.
            con.execute("""UPDATE jobs SET recovery_delayed=1 WHERE state IN ('PENDING','RECOVERY_PENDING')
                AND received_at<?""",(started.replace("+00:00","Z"),))
            con.commit()
            item["state"]="RUNNING"
            while not STOP.exists() and (minutes is None or time.monotonic()-start_mono<minutes*60):
                item["stage"]="DISCOVER"
                discovered=discover(con)
                con.execute("""UPDATE jobs SET recovery_delayed=1 WHERE state='PENDING' AND received_at<?""",
                            (started.replace("+00:00","Z"),))
                con.commit()
                if discovered:
                    item["last_discovery_at_utc"]=now()
                reconcile(con)
                item["daily_attempts"]=daily_attempts(con)
                jobs=con.execute("""SELECT * FROM jobs WHERE state IN ('PENDING','RECOVERY_PENDING')
                    ORDER BY received_at,article_id,version""").fetchall()
                item["backlog"]=len(jobs)
                item["heartbeat_at_utc"]=now()
                update_state(item)
                for job in jobs:
                    if STOP.exists():
                        break
                    if item["daily_attempts"]>=item["daily_limit"]:
                        item["state"]="DAILY_BUDGET_PAUSED"
                        item["pause_reason"]="DAILY_ATTEMPT_BUDGET"
                        break
                    max_attempts=1+config()["queue"]["recovery_attempts_per_version"]
                    if job["attempts"]>=max_attempts:
                        con.execute("UPDATE jobs SET state='FAILED',error_class='RECOVERY_ATTEMPT_LIMIT' WHERE article_id=? AND version=?",
                                    (job["article_id"],job["version"]))
                        con.commit()
                        continue
                    raw=feed_one(job["article_id"],job["version"])
                    if raw is None:
                        con.execute("UPDATE jobs SET state='QUARANTINED',error_class='SOURCE_VERSION_MISSING' WHERE article_id=? AND version=?",
                                    (job["article_id"],job["version"]))
                        con.commit()
                        continue
                    article=article_from_row(raw)
                    if article["source_text_hash"]!=job["source_hash"]:
                        quarantine(con,article,job["source_hash"],"SOURCE_VERSION_IDENTITY_COLLISION")
                        con.commit()
                        continue
                    if not has_chinese(article["title"]+article["body"]):
                        con.execute("UPDATE jobs SET state='SKIPPED',error_class='NON_CHINESE' WHERE article_id=? AND version=?",
                                    (job["article_id"],job["version"]))
                        con.commit()
                        continue
                    attempt=job["attempts"]+1
                    con.execute("BEGIN IMMEDIATE")
                    con.execute("UPDATE jobs SET state='STARTED',attempts=?,recovery_delayed=? WHERE article_id=? AND version=?",
                                (attempt,int(job["recovery_delayed"] or job["state"]=="RECOVERY_PENDING"),job["article_id"],job["version"]))
                    con.execute("INSERT INTO attempts(provider,article_id,version,attempt_number,started_at,state,run_id) VALUES(?,?,?,?,?,?,?)",
                                (article["provider"],article["article_id"],article["version"],attempt,now(),"STARTED",run_id))
                    attempt_id=con.execute("SELECT last_insert_rowid()").fetchone()[0]
                    con.commit()
                    item["daily_attempts"]+=1
                    item["stage"]="LOADING_MODEL" if token is None else "SCORING"
                    item["state"]="LOADING_MODEL" if token is None else "RUNNING"
                    item["heartbeat_at_utc"]=now()
                    update_state(item)
                    try:
                        if token is None:
                            token,model,torch=load_model()
                        item["stage"]="SCORING"
                        item["state"]="RUNNING"
                        update_state(item)
                        done,failed=score_article(article,token,model,torch,run_id)
                        completed=now()
                        con.execute("UPDATE jobs SET state=?,completed_at=?,error_class=? WHERE article_id=? AND version=?",
                                    ("DONE" if not failed else "PARTIAL_FAILED",completed,
                                     "PARTIAL_INFERENCE_FAILURE" if failed else None,job["article_id"],job["version"]))
                        con.execute("UPDATE attempts SET state=?,ended_at=?,error_class=? WHERE id=?",
                                    ("DONE" if not failed else "PARTIAL_FAILED",completed,
                                     "PARTIAL_INFERENCE_FAILURE" if failed else None,attempt_id))
                        con.commit()
                        item["last_success_at_utc"]=completed if done else item.get("last_success_at_utc")
                        item["last_new_received_at_utc"]=article["received_at_utc"]
                        item["last_completed_units"]=done
                        item["last_failed_units"]=failed
                    except Exception as exc:
                        error=type(exc).__name__
                        transient=isinstance(exc,sqlite3.OperationalError) and "locked" in str(exc).lower()
                        con.execute("UPDATE jobs SET state=?,error_class=? WHERE article_id=? AND version=?",
                                    ("RECOVERY_PENDING" if transient else "FAILED",error,job["article_id"],job["version"]))
                        con.execute("UPDATE attempts SET state=?,ended_at=?,error_class=? WHERE id=?",
                                    ("TRANSIENT_DB_BUSY" if transient else "FAILED",now(),error,attempt_id))
                        con.commit()
                        item["last_error_class"]=error
                        if transient:
                            item["stage"]="DATABASE_BACKOFF"
                            item["pause_reason"]="TRANSIENT_DB_BUSY_RETRY_WITHIN_LIMIT"
                        else:
                            item["state"]="ERROR_PAUSED"
                            item["pause_reason"]="TASK_EXCEPTION"
                        break
                item["heartbeat_at_utc"]=now()
                item["backlog"]=con.execute("SELECT count(*) FROM jobs WHERE state IN ('PENDING','RECOVERY_PENDING')").fetchone()[0]
                try:
                    from .snapshot import maybe_due_snapshot
                    due=maybe_due_snapshot(run_id)
                    item["snapshot_state"]=due.get("state",due.get("kind"))
                    item["next_cutoff_utc"]=due.get("next_cutoff_utc")
                    if due.get("snapshot_id"):
                        item["last_snapshot_id"]=due["snapshot_id"]
                except Exception as exc:
                    item["snapshot_state"]="SNAPSHOT_ERROR"
                    item["snapshot_error_class"]=type(exc).__name__
                try:
                    from scripts.news_sentiment_factor_v43.daily import maybe_due_snapshot as maybe_due_daily, refresh_label_states
                    daily=maybe_due_daily(run_id)
                    refresh_label_states()
                    item["daily_snapshot_rule_version"]="news_sentiment_factor_v43_protocol_1"
                    item["daily_snapshot_state"]=daily.get("state",daily.get("status"))
                    item["daily_next_cutoff_utc"]=daily.get("next_cutoff_utc")
                    if daily.get("snapshot_id"):
                        item["last_daily_snapshot_id"]=daily["snapshot_id"]
                except Exception as exc:
                    item["daily_snapshot_state"]="DAILY_SNAPSHOT_ERROR"
                    item["daily_snapshot_error_class"]=type(exc).__name__
                update_state(item)
                if item["state"]=="ERROR_PAUSED":
                    break
                time.sleep(min(config()["queue"]["poll_seconds"],max(0,minutes*60-(time.monotonic()-start_mono))) if minutes else config()["queue"]["poll_seconds"])
                if item["state"]=="DAILY_BUDGET_PAUSED" and daily_attempts(con)<item["daily_limit"]:
                    item["state"]="RUNNING"
                    item.pop("pause_reason",None)
        except Exception as exc:
            item["state"]="ERROR_EXIT"
            item["last_error_class"]=type(exc).__name__
            item["error_stack_private"]=[{"file":Path(frame.filename).name,"function":frame.name,"line":frame.lineno}
                                         for frame in traceback.extract_tb(exc.__traceback__)]
            raise
        finally:
            item["stopped_at_utc"]=now()
            item["exit_code"]=1 if item["state"] in {"ERROR_EXIT","ERROR_PAUSED"} else 0
            if STOP.exists():
                item["state"]="STOPPED_BY_REQUEST"
            elif minutes and item["state"]=="RUNNING":
                item["state"]="BOUNDED_END"
            item["heartbeat_at_utc"]=now()
            update_state(item)
            con.execute("""UPDATE worker_runs SET stopped_at=?,state=?,exit_code=?,error_class=?,last_heartbeat=?,stage=?
                WHERE run_id=?""",(item["stopped_at_utc"],item["state"],item["exit_code"],item.get("last_error_class"),
                                item["heartbeat_at_utc"],item["stage"],run_id))
            con.commit()
            con.close()
            STOP.unlink(missing_ok=True)
        return status()


def start(*, minutes: int | None = None) -> dict:
    current=status()
    if current.get("process_alive_identity") and current.get("state") in {"RUNNING","LOADING_MODEL","DAILY_BUDGET_PAUSED","STARTING","UNRESPONSIVE_HEARTBEAT"}:
        raise RuntimeError("WORKER_ALREADY_RUNNING")
    STOP.unlink(missing_ok=True)
    flags=subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.DETACHED_PROCESS if os.name=="nt" else 0
    PRIVATE.mkdir(parents=True,exist_ok=True)
    with LOG.open("ab") as log:
        child=subprocess.Popen([sys.executable,"-m","scripts.news_sentiment_factor_v42.worker","run",
                                *([] if minutes is None else ["--minutes",str(minutes)])],cwd=ROOT,
                               stdout=log,stderr=log,stdin=subprocess.DEVNULL,creationflags=flags)
    time.sleep(2)
    return {"spawn_pid":child.pid,"child_exit_code":child.poll(),"worker":status()}


def stop() -> dict:
    STOP.parent.mkdir(parents=True,exist_ok=True)
    STOP.write_text(now(),encoding="utf-8")
    return {"requested_at_utc":now(),"before":status()}


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("action",choices=["start","run","status","stop"])
    parser.add_argument("--minutes",type=int)
    args=parser.parse_args()
    result=run(minutes=args.minutes) if args.action=="run" else start(minutes=args.minutes) if args.action=="start" else status() if args.action=="status" else stop()
    print(json.dumps(result,ensure_ascii=False))
