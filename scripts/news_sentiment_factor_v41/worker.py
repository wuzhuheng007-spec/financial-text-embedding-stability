"""Bounded owner-only Chinese tone observer over the existing local Jin10 cache.

It never contacts a supplier. Start/status/stop affect this research worker only.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .freeze import FEED_DB, PRIVATE, PUBLIC, ROOT, digest, has_chinese, now, sha, write
from .prepare_v2 import CFG, article_segments
from .score import DB, MODEL_ID, infer_batch, init, load_model

STATE = PRIVATE / "worker_state_private.json"
STOP = PRIVATE / "worker_stop.flag"
PUBLIC_STATE = PUBLIC / "worker_status_redacted.json"
LOG = PRIVATE / "worker_private.log"


def utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("NAIVE_SOURCE_TIME")
    return parsed.astimezone(timezone.utc)


def state() -> dict:
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.is_file() else {"state": "NOT_STARTED"}


def process_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and exit_code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def status() -> dict:
    previous = state()
    report = {k: previous.get(k) for k in ("run_id", "state", "started_at_utc", "last_checked_at_utc",
              "last_new_received_at_utc", "stopped_at_utc", "new_versions", "attempted_versions", "scored_units", "failures",
              "backlog_count", "daily_new_versions", "daily_limit", "error_class")}
    report["worker_pid"] = previous.get("pid")
    report["process_alive"] = process_alive(previous.get("pid"))
    if report["state"] == "RUNNING" and not report["process_alive"]:
        report["state"] = "STALE_EXIT"
    report["model_revision"] = MODEL_ID
    report["segment_rule_version"] = json.loads(CFG.read_text(encoding="utf-8"))["scoring"]["segment_rule_version"]
    return report


def feed_rows(after: str) -> list[dict]:
    with sqlite3.connect(f"file:{FEED_DB.as_posix()}?mode=ro", uri=True, timeout=3) as con:
        con.row_factory = sqlite3.Row
        owner = con.execute("SELECT owner_id FROM owner_binding WHERE singleton=1").fetchone()
        if owner is None:
            raise ValueError("PERSONAL_FEED_OWNER_MISSING")
        rows = con.execute("""SELECT v.item_key,v.version,v.provider_id,v.title,v.content,v.source_url,
            v.published_raw,v.published_at_utc,v.received_at,v.content_sha256,v.raw_item_json,
            i.first_received_at FROM news_versions v JOIN news_items i USING(item_key)
            WHERE v.owner_id=? ORDER BY v.received_at,v.item_key,v.version""", (owner["owner_id"],)).fetchall()
    after_dt = utc(after)
    return [dict(r) for r in rows if utc(r["received_at"]) > after_dt]


def article_from_row(row: dict) -> dict:
    text = (row["title"] or "") + "\n" + row["content"]
    return {"provider": "jin10", "article_id": row["item_key"], "version": row["version"],
            "title": row["title"] or "", "body": row["content"], "publisher": None,
            "source_url": row["source_url"], "published_at_utc": row["published_at_utc"],
            "first_received_at_utc": row["first_received_at"], "received_at_utc": row["received_at"],
            "source_text_hash": digest(text.encode("utf-8")),
            "raw_response_hash": digest(row["raw_item_json"].encode("utf-8")),
            "acquisition_batch": "BATCH_ID_NOT_STORED_IN_FEED_CACHE", "cohort": "PROSPECTIVE_RECEIPT",
            "source_version_status": "PERSONAL_CACHE_VERSION"}


def daily_count(con: sqlite3.Connection) -> int:
    shanghai = timezone(timedelta(hours=8), "Asia/Shanghai")
    day = datetime.now(shanghai).date()
    rows = con.execute("SELECT attempted_at_utc FROM attempted_versions").fetchall()
    return sum(utc(x[0]).astimezone(shanghai).date() == day for x in rows)


def existing_status(con: sqlite3.Connection, article: dict) -> str | None:
    attempt = con.execute("""SELECT source_text_hash,state FROM attempted_versions
        WHERE provider=? AND article_id=? AND version=? AND segment_rule_version=?
        AND model_revision=? AND config_sha256=?""",
        (article["provider"], article["article_id"], article["version"],
         json.loads(CFG.read_text(encoding="utf-8"))["scoring"]["segment_rule_version"],
         MODEL_ID, sha(CFG))).fetchone()
    if attempt is not None:
        if attempt[0] != article["source_text_hash"]:
            raise ValueError("SOURCE_VERSION_IDENTITY_COLLISION")
        return "ATTEMPTED_" + attempt[1]
    found = con.execute("SELECT source_text_hash,cohort FROM articles WHERE provider=? AND article_id=? AND version=?",
                        (article["provider"], article["article_id"], article["version"])).fetchone()
    if found is None:
        return None
    if found[0] != article["source_text_hash"]:
        raise ValueError("SOURCE_VERSION_IDENTITY_COLLISION")
    return found[1]


def mark_attempt(article: dict, state: str, error_class: str | None = None) -> None:
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    with sqlite3.connect(DB, timeout=15) as con:
        con.execute("""INSERT INTO attempted_versions VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(provider,article_id,version,segment_rule_version,model_revision,config_sha256)
            DO UPDATE SET state=excluded.state,error_class=excluded.error_class""",
            (article["provider"], article["article_id"], article["version"],
             cfg["scoring"]["segment_rule_version"], MODEL_ID, sha(CFG),
             article["source_text_hash"], article["received_at_utc"], now(), state, error_class))


def score_article(article: dict, tokenizer, model, torch, run_id: str) -> tuple[int, int]:
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    segments, _ = article_segments(article, tokenizer, cfg["scoring"]["max_tokens_including_special"])
    error = None
    probs = []
    for offset in range(0, len(segments), cfg["scoring"]["batch_size"]):
        batch = segments[offset: offset + cfg["scoring"]["batch_size"]]
        try:
            probs.extend(infer_batch([x["text"] for x in batch], tokenizer, model, torch))
        except (RuntimeError, ValueError) as exc:
            error = type(exc).__name__
            probs.extend([None] * len(batch))
    scored_at = now()
    with sqlite3.connect(DB, timeout=15) as con:
        con.execute("""INSERT INTO articles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (article["provider"], article["article_id"], article["version"], article["publisher"],
                     article["source_url"], article["title"], article["body"], article["published_at_utc"],
                     article["first_received_at_utc"], article["received_at_utc"], article["source_text_hash"],
                     article["raw_response_hash"], article["acquisition_batch"], article["cohort"],
                     segments[0]["event_group_id"] if segments else None,
                     segments[0]["grouping_basis"] if segments else None, article["source_version_status"]))
        for segment, values in zip(segments, probs):
            con.execute("""INSERT INTO segments VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (segment["provider"], segment["article_id"], segment["version"], segment["segment_id"],
                         segment["kind"], segment["text"], segment["input_sha256"], segment["token_count"],
                         segment["source_field"], segment["source_start"], segment["source_end"],
                         segment["source_mapping_method"], json.dumps(segment["entity_candidates"], ensure_ascii=False),
                         segment["company_association"], segment["association_status"],
                         segment["semantic_kind_candidate"], segment["event_group_id"],
                         cfg["scoring"]["segment_rule_version"]))
            signal_time = max(utc(article["received_at_utc"]), utc(scored_at)).isoformat() if values else None
            con.execute("""INSERT INTO scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (segment["provider"], segment["article_id"], segment["version"], segment["segment_id"],
                         cfg["scoring"]["segment_rule_version"], MODEL_ID, sha(CFG),
                         segment["input_sha256"], segment["token_count"],
                         values[0] if values else None, values[1] if values else None, values[2] if values else None,
                         values[1] - values[2] if values else None,
                         "DONE" if values else "FAILED", error, "cpu", scored_at, scored_at,
                         article["received_at_utc"], signal_time, "PROSPECTIVE_RECEIPT", run_id))
    return sum(x is not None for x in probs), sum(x is None for x in probs)


def update_local(current: dict) -> None:
    write(STATE, current)
    redacted = {k: v for k, v in current.items() if k not in {"pid", "start_barrier", "private_db_path"}}
    write(PUBLIC_STATE, redacted)


def run(minutes: int | None) -> None:
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    if minutes is not None and not 1 <= minutes <= cfg["prospective"]["observe_minutes_max"]:
        raise ValueError("OBSERVE_DURATION_OUT_OF_RANGE")
    if not (PUBLIC / "real_score_run_redacted.json").exists():
        raise ValueError("FROZEN_REPLAY_NOT_COMPLETED")
    init()
    prior = state()
    if prior.get("state") == "RUNNING" and process_alive(prior.get("pid")) and prior.get("pid") != os.getpid():
        raise RuntimeError("WORKER_ALREADY_RUNNING")
    started = now()
    run_id = "v41_observe_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    current = {"schema": "news_sentiment_v41_worker_status_1", "run_id": run_id,
               "state": "RUNNING", "pid": os.getpid(), "started_at_utc": started,
               "start_barrier": started, "last_checked_at_utc": started,
               "new_versions": 0, "scored_units": 0, "failures": 0, "backlog_count": 0,
               "attempted_versions": 0,
               "daily_new_versions": 0, "daily_limit": cfg["prospective"]["daily_new_article_versions_max"],
               "model_revision": MODEL_ID, "segment_rule_version": cfg["scoring"]["segment_rule_version"],
               "config_sha256": sha(CFG), "no_supplier_requests": True,
               "no_return_labels_or_model_fits": True,
               "run_mode": "CONTINUOUS_DAILY_BUDGET" if minutes is None else "BOUNDED_OBSERVATION"}
    update_local(current)
    with sqlite3.connect(DB, timeout=15) as con:
        con.execute("INSERT INTO worker_runs(run_id,started_at_utc,state,last_checked_at_utc) VALUES (?,?,?,?)",
                    (run_id, started, "RUNNING", started))
    tokenizer = model = torch = None
    end_at = None if minutes is None else time.monotonic() + minutes * 60
    try:
        while (end_at is None or time.monotonic() < end_at) and not STOP.exists():
            batch = feed_rows(current["start_barrier"])
            current["last_checked_at_utc"] = now()
            with sqlite3.connect(DB, timeout=15) as con:
                current["daily_new_versions"] = daily_count(con)
                if current["state"] == "DAILY_BUDGET_PAUSED" and current["daily_new_versions"] < current["daily_limit"]:
                    current["state"] = "RUNNING"
                unseen = []
                for row in batch:
                    article = article_from_row(row)
                    try:
                        prior_cohort = existing_status(con, article)
                    except ValueError:
                        current["failures"] += 1
                        current["error_class"] = "SOURCE_VERSION_IDENTITY_COLLISION"
                        continue
                    if prior_cohort is None:
                        unseen.append(article)
                current["backlog_count"] = len(unseen)
            for article in unseen:
                if STOP.exists() or (minutes is not None and current["new_versions"] >= cfg["prospective"]["new_article_versions_max"]):
                    break
                if current["daily_new_versions"] >= current["daily_limit"]:
                    current["state"] = "DAILY_BUDGET_PAUSED"
                    break
                mark_attempt(article, "STARTED")
                current["attempted_versions"] += 1
                current["daily_new_versions"] += 1
                if not has_chinese(article["title"] + article["body"]):
                    current["failures"] += 1
                    current["error_class"] = "NON_CHINESE_NEW_VERSION_NOT_SCORED"
                    mark_attempt(article, "SKIPPED", current["error_class"])
                    continue
                if tokenizer is None:
                    tokenizer, model, torch = load_model()
                try:
                    done, failed = score_article(article, tokenizer, model, torch, run_id)
                    current["new_versions"] += 1
                    current["scored_units"] += done
                    current["failures"] += failed
                    current["last_new_received_at_utc"] = article["received_at_utc"]
                    current["backlog_count"] -= 1
                    mark_attempt(article, "DONE" if not failed else "PARTIAL_FAILED", "INFERENCE_FAILURE" if failed else None)
                    update_local(current)
                except (ValueError, RuntimeError, sqlite3.Error) as exc:
                    current["failures"] += 1
                    current["error_class"] = type(exc).__name__
                    mark_attempt(article, "FAILED", current["error_class"])
                    # Fail closed; no automatic retry for this article/version.
                    current["state"] = "ERROR_PAUSED"
                    break
            update_local(current)
            with sqlite3.connect(DB, timeout=15) as con:
                con.execute("""UPDATE worker_runs SET state=?,new_versions=?,scored_units=?,failures=?,
                    last_checked_at_utc=?,error_class=? WHERE run_id=?""",
                    (current["state"], current["new_versions"], current["scored_units"],
                     current["failures"], current["last_checked_at_utc"], current.get("error_class"), run_id))
            if current["state"] == "ERROR_PAUSED" or (minutes is not None and current["new_versions"] >= cfg["prospective"]["new_article_versions_max"]):
                break
            delay = cfg["prospective"]["poll_seconds"] if current["state"] == "RUNNING" else 300
            time.sleep(delay if end_at is None else min(delay, max(0, end_at - time.monotonic())))
    except Exception as exc:
        current["state"] = "ERROR_PAUSED"
        current["error_class"] = type(exc).__name__
        raise
    finally:
        current["state"] = "STOPPED_BY_REQUEST" if STOP.exists() else (
            "OBSERVATION_LIMIT_REACHED" if current["state"] == "RUNNING" else current["state"])
        current["stopped_at_utc"] = now()
        update_local(current)
        with sqlite3.connect(DB, timeout=15) as con:
            con.execute("""UPDATE worker_runs SET state=?,stopped_at_utc=?,new_versions=?,scored_units=?,
                failures=?,last_checked_at_utc=?,error_class=? WHERE run_id=?""",
                (current["state"], current["stopped_at_utc"], current["new_versions"], current["scored_units"],
                 current["failures"], current["last_checked_at_utc"], current.get("error_class"), run_id))
        STOP.unlink(missing_ok=True)


def start(minutes: int | None) -> dict:
    previous = state()
    if previous.get("state") == "RUNNING" and process_alive(previous.get("pid")):
        raise RuntimeError("WORKER_ALREADY_RUNNING")
    if previous.get("state") == "RUNNING":
        with sqlite3.connect(DB, timeout=15) as con:
            con.execute("UPDATE worker_runs SET state='STALE_EXIT',error_class='PROCESS_EXITED_WITHOUT_FINAL_RECEIPT' WHERE run_id=? AND state='RUNNING'",
                        (previous["run_id"],))
    STOP.unlink(missing_ok=True)
    PRIVATE.mkdir(parents=True, exist_ok=True)
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS if os.name == "nt" else 0
    with LOG.open("ab") as handle:
        child = subprocess.Popen([sys.executable, "-m", "scripts.news_sentiment_factor_v41.worker",
                                  "run", *( ["--continuous"] if minutes is None else ["--minutes", str(minutes)] )], cwd=ROOT,
                                 stdout=handle, stderr=handle, stdin=subprocess.DEVNULL,
                                 creationflags=flags)
    time.sleep(2)
    current = state()
    return {"spawn_pid": child.pid, "worker": status(), "log_private": str(LOG)}


def stop() -> dict:
    STOP.write_text(now(), encoding="utf-8")
    return {"stop_requested_at_utc": now(), "state_before_stop": status()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "run", "status", "stop"))
    parser.add_argument("--minutes", type=int, default=15)
    parser.add_argument("--continuous", action="store_true")
    args = parser.parse_args()
    duration = None if args.continuous else args.minutes
    result = start(duration) if args.action == "start" else status() if args.action == "status" else stop() if args.action == "stop" else run(duration)
    print(json.dumps(result, ensure_ascii=False))
