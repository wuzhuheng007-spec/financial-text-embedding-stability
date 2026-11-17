"""Independent bounded daily-price worker; start/status/stop from this module."""
from __future__ import annotations

import argparse
import json
import msvcrt
import os
import sqlite3
import subprocess
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from contextlib import closing

import psutil

from scripts.news_sentiment_factor_v44.labels import DB
from .daily_prices import cfg, init, queue_requirements, tick
from .labels import advance
from .preflight import PRIVATE, PUBLIC, ROOT, dump

STATE = PRIVATE / "daily_worker_state_private.json"
PUBLIC_STATE = PUBLIC / "daily_worker_status_redacted.json"
LOCK = PRIVATE / "daily_worker.lock"
STOP = PRIVATE / "daily_worker_stop.json"
LOG = PRIVATE / "daily_worker_private.log"


def _atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    dump(temp, value)
    os.replace(temp, path)


def _alive(state: dict) -> bool:
    try:
        process = psutil.Process(int(state["pid"]))
        return process.is_running() and abs(process.create_time() - float(state["process_created_epoch"])) < 2
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError, KeyError, TypeError):
        return False


def status() -> dict:
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    alive = _alive(state)
    heartbeat = state.get("heartbeat_at_utc")
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(heartbeat)).total_seconds() if heartbeat else None
    result = {k: state.get(k) for k in (
        "run_id", "pid", "started_at_utc", "heartbeat_at_utc", "last_tick_at_utc",
        "last_supplier_success_at_utc", "last_supplier_error_class", "last_supplier_http_status",
        "last_price_import_at_utc", "last_action_import_at_utc", "last_label_check_at_utc",
        "last_label_new_versions", "last_request_count", "last_needed_security_dates",
        "last_queue_rows", "last_error_class", "next_due_utc", "pause_reason",
        "budget_used_today", "daily_budget", "state", "next_conditional_collection_window_utc")}
    result.update({"schema": "news_sentiment_factor_v45_worker_status_1",
                   "checked_at_utc": datetime.now(timezone.utc).isoformat(),
                   "process_alive_identity": alive,
                   "heartbeat_age_seconds": round(age, 1) if age is not None else None})
    if not alive:
        result["state"] = "STOPPED_OR_STALE"
    elif age is not None and age > max(180, cfg()["price_request_schedule"]["worker_scan_seconds"] * 3):
        result["state"] = "HEARTBEAT_STALE"
    return result


def _budget_used() -> int:
    if not DB.exists():
        return 0
    with closing(sqlite3.connect(DB, timeout=2)) as con:
        try:
            return con.execute("SELECT COUNT(*) FROM v45_supplier_requests WHERE utc_day=?",
                               (datetime.now(timezone.utc).date().isoformat(),)).fetchone()[0]
        except sqlite3.OperationalError:
            return 0


def _due() -> str | None:
    requirements, _ = queue_requirements()
    if not requirements:
        return None
    from datetime import date, timedelta
    from scripts.news_sentiment_factor_v43.daily import regular_session_close
    dates = sorted({d for r in requirements for d in
                    (r["entry_session_ny"], r["exit_session_ny"])})
    if not dates:
        return None
    return min(regular_session_close(date.fromisoformat(d)) + timedelta(minutes=45)
               for d in dates).isoformat()


def _next_conditional_collection_window(at: datetime) -> str | None:
    """Next possible close-buffer time; a qualified queue is still required."""
    from datetime import timedelta
    from scripts.news_sentiment_factor_v43.daily import regular_session_close, session_day
    from zoneinfo import ZoneInfo
    ny_day = at.astimezone(ZoneInfo("America/New_York")).date()
    # A newly released 16:15 signal can first enter on the following session.
    # With no released queue, today's bar cannot be a label entry for that signal.
    for step in range(1, 9):
        day = ny_day + timedelta(days=step)
        if session_day(day):
            due = regular_session_close(day) + timedelta(minutes=45)
            if due > at:
                return due.isoformat()
    return None


def run() -> dict:
    PRIVATE.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+b") as lock:
        lock.seek(0, 2)
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return {"state": "ALREADY_RUNNING_LOCKED"}
        init()
        run_id = "v45_" + uuid.uuid4().hex
        state = {"run_id": run_id, "pid": os.getpid(),
                 "process_created_epoch": psutil.Process().create_time(),
                 "started_at_utc": datetime.now(timezone.utc).isoformat(),
                 "heartbeat_at_utc": datetime.now(timezone.utc).isoformat(),
                 "state": "RUNNING", "daily_budget": cfg()["budgets"]["ongoing_supplier_requests_per_utc_day"]}
        _atomic(STATE, state)
        _atomic(PUBLIC_STATE, status())
        try:
            while True:
                if STOP.exists():
                    try:
                        wanted = json.loads(STOP.read_text(encoding="utf-8")).get("run_id")
                    except (ValueError, OSError):
                        wanted = None
                    if wanted == run_id:
                        state["state"] = "STOPPED_REQUESTED"
                        break
                state["state"] = "RUNNING"
                state.pop("last_error_class", None)
                try:
                    price = tick()
                    label = advance()
                    state["last_tick_at_utc"] = datetime.now(timezone.utc).isoformat()
                    state["last_request_count"] = len(price["requests"])
                    state["last_needed_security_dates"] = price["needed_security_dates"]
                    state["last_queue_rows"] = price["qualified_queue_rows"]
                    state["last_label_check_at_utc"] = label["checked_at_utc"]
                    state["last_label_new_versions"] = label["new_labels"]
                    if price["new_price_field_rows"]:
                        state["last_price_import_at_utc"] = state["last_tick_at_utc"]
                    if any(x["status"] == "PROVIDER_LISTS_NO_ACTION_IN_INTERVAL_V45" for x in price["new_action_intervals"]):
                        state["last_action_import_at_utc"] = state["last_tick_at_utc"]
                    for request in price["requests"]:
                        if request["state"] == "SUCCESS":
                            state["last_supplier_success_at_utc"] = request.get("completed_at_utc")
                        elif request["state"] not in ("DAILY_LOCAL_BUDGET_PAUSED",):
                            state["last_supplier_error_class"] = request.get("error_class") or request["state"]
                            state["last_supplier_http_status"] = request.get("http_status")
                    state["next_due_utc"] = _due()
                    state["next_conditional_collection_window_utc"] = _next_conditional_collection_window(datetime.now(timezone.utc))
                    state["pause_reason"] = "WAIT_RELEASED_NONMISSING_N1_QUEUE" if not price["qualified_queue_rows"] else None
                except Exception as exc:
                    state["state"] = "ERROR_BACKOFF"
                    state["last_error_class"] = type(exc).__name__
                    with LOG.open("a", encoding="utf-8") as output:
                        output.write(f"{datetime.now(timezone.utc).isoformat()} {type(exc).__name__}\n")
                        traceback.print_exc(file=output)
                state["budget_used_today"] = _budget_used()
                state["heartbeat_at_utc"] = datetime.now(timezone.utc).isoformat()
                _atomic(STATE, state)
                _atomic(PUBLIC_STATE, status())
                interval = cfg()["price_request_schedule"]["worker_scan_seconds"]
                for _ in range(interval):
                    time.sleep(1)
                    if STOP.exists():
                        try:
                            if json.loads(STOP.read_text(encoding="utf-8")).get("run_id") == run_id:
                                break
                        except (ValueError, OSError):
                            pass
        finally:
            state["heartbeat_at_utc"] = datetime.now(timezone.utc).isoformat()
            _atomic(STATE, state)
            _atomic(PUBLIC_STATE, status())
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
    return {"state": state["state"], "run_id": run_id}


def start() -> dict:
    previous = status()
    if previous["process_alive_identity"]:
        return previous
    PRIVATE.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as output:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen([sys.executable, "-m", "scripts.news_sentiment_factor_v45.worker", "run"],
                         cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                         creationflags=flags, close_fds=True)
    for _ in range(30):
        time.sleep(0.2)
        current = status()
        if current["process_alive_identity"] and current["state"] in ("RUNNING", "ERROR_BACKOFF"):
            return current
    return status()


def stop() -> dict:
    current = status()
    if not current["process_alive_identity"]:
        return current
    _atomic(STOP, {"run_id": current["run_id"], "requested_at_utc": datetime.now(timezone.utc).isoformat()})
    for _ in range(60):
        time.sleep(0.2)
        if not status()["process_alive_identity"]:
            return status()
    return {**status(), "state": "STOP_REQUESTED_WAITING"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["start", "status", "stop", "run", "once"])
    arg = parser.parse_args()
    action = {"start": start, "status": status, "stop": stop, "run": run,
              "once": lambda: {"prices": tick(), "labels": advance()}}[arg.command]
    print(json.dumps(action(), ensure_ascii=False, default=str))
