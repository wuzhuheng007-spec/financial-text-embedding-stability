"""Versioned owner-only conditional news analysis worker; raw content stays private."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from .analysis_contract_v15c import build_spans
from .analysis_contract_v16 import PROMPT_VERSION, SCHEMA_VERSION, parse_and_validate
from .analysis_v15b import _feed_rows, _source, owner_id
from .collector import ROOT
from .relevance_v15 import rank_reason, screen

CONFIG = ROOT / "configs/quant_portal_personal_v16_20261116.json"
DB = ROOT / "apps/quant_portal/var/personal_analysis_v16.sqlite3"
PID = ROOT / "apps/quant_portal/var/personal_analysis_v16.pid.json"
STOP = ROOT / "apps/quant_portal/var/personal_analysis_v16.stop"
PRIVATE = ROOT / "apps/quant_portal/var/analysis_v16_requests"
RUNTIME = ROOT / "apps/quant_portal/var/analysis_v15b_model_runtime.json"
OLD = ROOT / "apps/quant_portal/var/personal_analysis_v15b.sqlite3"


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def cfg() -> dict:
    value = json.loads(CONFIG.read_text(encoding="utf-8"))
    if value["prompt_version"] != PROMPT_VERSION or value["schema_version"] != SCHEMA_VERSION:
        raise ValueError("CONTRACT_CONFIGURATION_MISMATCH")
    return value


def open_db() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, isolation_level=None, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=10000")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS analysis_jobs (
      owner_id INTEGER NOT NULL, item_key TEXT NOT NULL, version INTEGER NOT NULL,
      content_sha256 TEXT NOT NULL, model_revision TEXT NOT NULL,
      prompt_version TEXT NOT NULL, schema_version TEXT NOT NULL,
      status TEXT NOT NULL, priority INTEGER NOT NULL, enqueued_at TEXT NOT NULL,
      started_at TEXT, completed_at TEXT, input_sha256 TEXT,
      result_json TEXT, validation_json TEXT, error_class TEXT,
      PRIMARY KEY(owner_id,item_key,version,model_revision,prompt_version,schema_version));
    CREATE TABLE IF NOT EXISTS analysis_attempts (
      id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL, item_key TEXT NOT NULL,
      version INTEGER NOT NULL, prompt_version TEXT NOT NULL, attempt INTEGER NOT NULL,
      started_at TEXT NOT NULL, ended_at TEXT NOT NULL, raw_sha256 TEXT,
      raw_response TEXT, parse_mode TEXT, error_class TEXT, validation_json TEXT,
      elapsed_seconds REAL, generated_tokens INTEGER,
      UNIQUE(owner_id,item_key,version,prompt_version,attempt));
    CREATE TABLE IF NOT EXISTS analysis_worker_state (
      owner_id INTEGER PRIMARY KEY, pid INTEGER, heartbeat_at TEXT,
      state TEXT NOT NULL, model_revision TEXT NOT NULL, prompt_version TEXT NOT NULL,
      schema_version TEXT NOT NULL, last_error_class TEXT);
    """)
    return db


def enqueue(db: sqlite3.Connection, owner: int, config: dict,
            only: tuple[str, int] | None = None) -> dict:
    rows = _feed_rows(owner, limit=500)
    if only:
        rows = [row for row in rows if (row["item_key"], row["version"]) == only]
    active = db.execute("""SELECT COUNT(*) FROM analysis_jobs WHERE owner_id=?
      AND model_revision=? AND prompt_version=? AND schema_version=?
      AND status IN ('PENDING','RUNNING')""",
      (owner,config["model_revision"],PROMPT_VERSION,SCHEMA_VERSION)).fetchone()[0]
    selected = []
    for row in rows:
        decision = screen(row)
        if decision.status != "INCLUDE":
            continue
        score = rank_reason(decision, published_at_utc=row["published_at_utc"],
                            received_at=row["received_at"], source_url=row["source_url"],
                            version=row["version"],weights=config["top_weights"])["score"]
        selected.append((score, row))
    selected.sort(key=lambda pair: (-pair[0], pair[1]["item_key"]))
    added = 0
    for score, row in selected:
        if active >= config["queue_limit"]:
            break
        key = (owner, row["item_key"], row["version"], config["model_revision"],
               config["prompt_version"], config["schema_version"])
        if db.execute("SELECT 1 FROM analysis_jobs WHERE owner_id=? AND item_key=? AND version=? AND model_revision=? AND prompt_version=? AND schema_version=?", key).fetchone():
            continue
        db.execute("""INSERT INTO analysis_jobs(owner_id,item_key,version,content_sha256,
          model_revision,prompt_version,schema_version,status,priority,enqueued_at)
          VALUES(?,?,?,?,?,?,?,'PENDING',?,?)""", (*key[:3], row["content_sha256"], *key[3:],
                                             10000 if only else score, now()))
        added += 1
        active += 1
    return {"eligible": len(selected), "new_jobs": added, "active": active}


def enqueue_one_old_failure(db: sqlite3.Connection, owner: int, config: dict) -> dict:
    if not OLD.is_file():
        return {"error": "OLD_FAILURE_DB_MISSING"}
    with sqlite3.connect(OLD.resolve().as_uri() + "?mode=ro", uri=True) as old:
        rows = old.execute("""SELECT item_key,version FROM analysis_jobs
          WHERE owner_id=? AND status='FAILED' ORDER BY completed_at,item_key LIMIT 40""", (owner,)).fetchall()
    for key, version in rows:
        result = enqueue(db, owner, config, only=(key, version))
        if result["new_jobs"]:
            return {"old_failure_requeued": True, "item_key": key, "version": version}
    return {"old_failure_requeued": False}


def _terminate(proc: subprocess.Popen) -> bool:
    if proc.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill.exe", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=15)
        else:
            proc.kill()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        return False
    return proc.poll() is not None


def attempt_paths(identity: tuple, attempt: int) -> tuple[Path, Path]:
    """Keep private raw files distinct across model, prompt and schema versions."""
    file_id = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:24]
    return (PRIVATE / f"{file_id}_{attempt}.request.json",
            PRIVATE / f"{file_id}_{attempt}.response.json")


def generate(request: Path, response: Path, config: dict) -> tuple[dict | None, str | None, float]:
    runtime = json.loads(RUNTIME.read_text(encoding="utf-8"))
    if not Path(runtime["python_exe"]).is_file() or not Path(runtime["model_dir"]).is_dir():
        return None, "MODEL_RUNTIME_UNAVAILABLE", 0.0
    env = os.environ.copy()
    env["QUANT_V15_MODEL_DIR"] = runtime["model_dir"]
    start = time.monotonic()
    proc = subprocess.Popen([runtime["python_exe"], "-m", "scripts.quant_personal_feed.generate_v16",
                             "--request", str(request), "--response", str(response)],
                            cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    loaded = request.with_suffix(".loaded")
    loaded.unlink(missing_ok=True)
    deadline = start + config["model_load_timeout_seconds"]
    while proc.poll() is None and not loaded.exists() and time.monotonic() < deadline:
        time.sleep(.25)
    if proc.poll() is None and not loaded.exists():
        confirmed = _terminate(proc)
        return None, "MODEL_LOAD_TIMEOUT_TERMINATED" if confirmed else "MODEL_LOAD_TIMEOUT_UNCONFIRMED", time.monotonic()-start
    if loaded.exists() and proc.poll() is None:
        try:
            proc.wait(timeout=config["generation_timeout_seconds"])
        except subprocess.TimeoutExpired:
            confirmed = _terminate(proc)
            return None, "GENERATION_TIMEOUT_TERMINATED" if confirmed else "GENERATION_TIMEOUT_UNCONFIRMED", time.monotonic()-start
    if proc.returncode or not response.exists():
        return None, "MODEL_PROCESS_FAILED", time.monotonic()-start
    try:
        return json.loads(response.read_text(encoding="utf-8")), None, time.monotonic()-start
    except (OSError, json.JSONDecodeError):
        return None, "MODEL_RESPONSE_INVALID", time.monotonic()-start


def process_one(db: sqlite3.Connection, owner: int, config: dict, job: sqlite3.Row) -> dict:
    key, version = job["item_key"], job["version"]
    identity = (owner, key, version, job["model_revision"], job["prompt_version"], job["schema_version"])
    where = "owner_id=? AND item_key=? AND version=? AND model_revision=? AND prompt_version=? AND schema_version=?"
    source = _source(owner, key, version)
    if not source or source["content_sha256"] != job["content_sha256"]:
        db.execute(f"UPDATE analysis_jobs SET status='FAILED',error_class='INPUT_VERSION_MISSING',completed_at=? WHERE {where}",
                   (now(), *identity))
        return {"status": "FAILED", "error": "INPUT_VERSION_MISSING"}
    try:
        spans, span_meta = build_spans(source["title"], source["content"],
                            max_source_characters=config["max_source_characters"])
    except ValueError as exc:
        db.execute(f"UPDATE analysis_jobs SET status='FALLBACK',error_class=?,completed_at=? WHERE {where}",
                   (str(exc), now(), *identity))
        return {"status": "FALLBACK", "error": str(exc)}
    input_hash = span_meta["selected_sha256"]
    db.execute(f"UPDATE analysis_jobs SET status='RUNNING',started_at=?,input_sha256=? WHERE {where}",
               (now(), input_hash, *identity))
    PRIVATE.mkdir(parents=True, exist_ok=True)
    outcome = None
    last_error = None
    already = db.execute("SELECT COUNT(*) FROM analysis_attempts WHERE owner_id=? AND item_key=? AND version=? AND prompt_version=?",
                         (owner,key,version,PROMPT_VERSION)).fetchone()[0]
    if already >= config["max_attempts_per_version"]:
        db.execute(f"UPDATE analysis_jobs SET status='FALLBACK',error_class='ATTEMPT_LIMIT_REACHED',completed_at=? WHERE {where}",
                   (now(), *identity))
        return {"status":"FALLBACK","error":"ATTEMPT_LIMIT_REACHED"}
    last_attempt = min(config["max_attempts_per_version"], already + config["automatic_attempts_per_run"])
    for attempt in range(already + 1, last_attempt + 1):
        request, response = attempt_paths(identity, attempt)
        request.write_text(json.dumps({"spans": spans, "repair": attempt > 1,
            "model_revision": config["model_revision"], "max_input_tokens": config["max_input_tokens"],
            "max_new_tokens": config["max_new_tokens"], "seed": config["seed"]}, ensure_ascii=False),
            encoding="utf-8")
        started = now()
        output, error, elapsed = generate(request, response, config)
        raw = output.get("raw") if isinstance(output, dict) else None
        parsed = validation = None
        mode = None
        if error is None and output.get("error"):
            error = str(output["error"])
        if error is None and not isinstance(raw, str):
            error = "MODEL_RAW_MISSING"
        if error is None:
            try:
                parsed, validation, mode = parse_and_validate(raw, spans)
                if not validation["schema_exact"]:
                    error = "SCHEMA_NOT_EXACT"
                elif not validation["evidence_ids_valid"]:
                    error = "EVIDENCE_ID_INVALID"
                elif not validation["display_zh"] and not validation["display_en"]:
                    error = "NO_DISPLAYABLE_LANGUAGE"
            except (ValueError, json.JSONDecodeError, TypeError) as exc:
                error = "PARSE_" + type(exc).__name__
        db.execute("""INSERT INTO analysis_attempts(owner_id,item_key,version,prompt_version,attempt,
          started_at,ended_at,raw_sha256,raw_response,parse_mode,error_class,validation_json,
          elapsed_seconds,generated_tokens) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (owner,key,version,PROMPT_VERSION,attempt,started,now(),hashlib.sha256(raw.encode()).hexdigest() if raw else None,
           raw,mode,error,json.dumps(validation) if validation else None,elapsed,
           output.get("generated_tokens") if isinstance(output,dict) else None))
        if error is None:
            evidence = [{"id": row["id"], "quote": row["text"]} for row in spans
                        if row["id"] in parsed["evidence_ids"]]
            outcome = {"analysis_zh": parsed.get("analysis_zh") if validation["display_zh"] else None,
                       "analysis_en": parsed.get("analysis_en") if validation["display_en"] else None,
                       "watch_zh": parsed.get("watch_zh") if validation["field_checks"]["watch_zh"]["accepted"] else None,
                       "watch_en": parsed.get("watch_en") if validation["field_checks"]["watch_en"]["accepted"] else None,
                       "tone": validation["tone"], "evidence": evidence}
            break
        last_error = error
        if error in {"MODEL_RUNTIME_UNAVAILABLE", "MODEL_PROCESS_FAILED", "MODEL_LOAD_TIMEOUT_TERMINATED",
                     "MODEL_LOAD_TIMEOUT_UNCONFIRMED", "GENERATION_TIMEOUT_TERMINATED",
                     "GENERATION_TIMEOUT_UNCONFIRMED", "INPUT_TOKEN_BUDGET"}:
            break
    status = "DRAFT" if outcome else "FALLBACK"
    db.execute(f"""UPDATE analysis_jobs SET status=?,result_json=?,validation_json=?,error_class=?,completed_at=?
      WHERE {where}""",
      (status,json.dumps(outcome,ensure_ascii=False) if outcome else None,
       json.dumps(validation,ensure_ascii=False) if validation else None,last_error,now(),
       *identity))
    return {"status": status, "error": last_error, "attempts": attempt}


def run(max_items: int | None = None) -> None:
    config = cfg()
    owner = owner_id()
    db = open_db()
    STOP.unlink(missing_ok=True)
    PID.write_text(json.dumps({"pid": os.getpid(), "started_at": now(),
                               "prompt_version": PROMPT_VERSION}), encoding="utf-8")
    count = 0
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        db.execute("""UPDATE analysis_jobs SET status='FALLBACK',error_class='PRIOR_RUN_INTERRUPTED',completed_at=?
          WHERE owner_id=? AND model_revision=? AND prompt_version=? AND schema_version=? AND status='RUNNING'""",
          (now(),owner,config["model_revision"],PROMPT_VERSION,SCHEMA_VERSION))
        while not stopping and not STOP.exists():
            db.execute("""INSERT INTO analysis_worker_state(owner_id,pid,heartbeat_at,state,
              model_revision,prompt_version,schema_version) VALUES(?,?,?,'RUNNING',?,?,?)
              ON CONFLICT(owner_id) DO UPDATE SET pid=excluded.pid,heartbeat_at=excluded.heartbeat_at,
              state=excluded.state,model_revision=excluded.model_revision,
              prompt_version=excluded.prompt_version,schema_version=excluded.schema_version""",
              (owner,os.getpid(),now(),config["model_revision"],PROMPT_VERSION,SCHEMA_VERSION))
            enqueue(db, owner, config)
            job = db.execute("""SELECT * FROM analysis_jobs WHERE owner_id=? AND model_revision=?
              AND prompt_version=? AND schema_version=? AND status='PENDING'
              ORDER BY priority DESC,enqueued_at,item_key LIMIT 1""",
              (owner,config["model_revision"],PROMPT_VERSION,SCHEMA_VERSION)).fetchone()
            if job:
                result = process_one(db, owner, config, job)
                count += 1
                if (result.get("error") or "").endswith("UNCONFIRMED"):
                    break
                if max_items is not None and count >= max_items:
                    break
            else:
                time.sleep(config["poll_seconds"])
    finally:
        db.execute("UPDATE analysis_worker_state SET state='STOPPED',heartbeat_at=? WHERE owner_id=?", (now(),owner))
        db.close()
        PID.unlink(missing_ok=True)
        STOP.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("run", "status", "stop", "reprocess-old-failure", "enqueue"))
    parser.add_argument("--max-items", type=int)
    args = parser.parse_args()
    if args.action == "stop":
        STOP.write_text("stop\n", encoding="ascii")
        return
    db = open_db()
    try:
        if args.action == "status":
            state = db.execute("SELECT * FROM analysis_worker_state WHERE owner_id=?", (owner_id(),)).fetchone()
            config = cfg()
            counts = {row["status"]: row["n"] for row in db.execute("""SELECT status,COUNT(*) AS n
              FROM analysis_jobs WHERE owner_id=? AND model_revision=? AND prompt_version=?
              AND schema_version=? GROUP BY status""",
              (owner_id(),config["model_revision"],PROMPT_VERSION,SCHEMA_VERSION))}
            print(json.dumps({"worker": dict(state) if state else None, "counts": counts}, ensure_ascii=False))
            return
        if args.action == "reprocess-old-failure":
            print(json.dumps(enqueue_one_old_failure(db,owner_id(),cfg()),ensure_ascii=False))
            return
        if args.action == "enqueue":
            print(json.dumps(enqueue(db,owner_id(),cfg()),ensure_ascii=False))
            return
    finally:
        db.close()
    if PID.exists():
        record = json.loads(PID.read_text(encoding="utf-8"))
        try: os.kill(record["pid"], 0)
        except OSError: pass
        else: raise RuntimeError("ANALYSIS_V16_ALREADY_RUNNING")
    run(args.max_items)


if __name__ == "__main__":
    main()
