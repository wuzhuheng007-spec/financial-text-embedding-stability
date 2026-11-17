"""Bounded owner-only moomoo English-news observation; never touches Jin10/quotes."""
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from .preflight import CONFIG, PRIVATE, PUBLIC, ROOT, save
from .probe import count_requests, request_once
from .store import DB, ingest_moomoo_probe, summarize

LOCK = PRIVATE / "observe.pid"
STOP = PRIVATE / "observe.stop"
SCORER_PYTHON = ROOT / "artifacts/news_sentiment_factor_v3_20261117/.venv/Scripts/python.exe"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def score_new(limit: int) -> dict:
    """Keep Windows DPAPI in the collector Python and pinned model libs in research venv."""
    if not SCORER_PYTHON.is_file() or limit < 1:
        raise RuntimeError("SCORER_ENVIRONMENT_UNAVAILABLE")
    completed = subprocess.run([str(SCORER_PYTHON), "-m", "scripts.news_sentiment_factor_v4.score",
                                "--limit", str(limit), "--only-prospective"], cwd=ROOT, text=True,
                               capture_output=True, timeout=300, check=False)
    if completed.returncode:
        (PRIVATE / "last_scorer_error_private.log").write_text(completed.stderr[-8000:], encoding="utf-8")
        raise RuntimeError("SCORER_PROCESS_FAILED")
    return json.loads(completed.stdout.splitlines()[-1])


def run(seconds: int, *, interval: int = 60) -> dict:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    if not 1 <= seconds <= cfg["prospective"]["observe_seconds_max"] or interval < 60:
        raise ValueError("BOUNDED_OBSERVATION_ARGUMENTS")
    PRIVATE.mkdir(parents=True, exist_ok=True)
    if LOCK.exists():
        raise RuntimeError("OBSERVER_ALREADY_LOCKED_CHECK_STATUS")
    STOP.unlink(missing_ok=True)
    LOCK.write_text(json.dumps({"pid": os.getpid(), "started_at_utc": now()}), encoding="utf-8")
    started = time.monotonic()
    start_utc = now()
    receipt = {"schema": "news_sentiment_v4_online_observation_redacted_1",
               "started_at_utc": start_utc, "seconds_cap": seconds,
               "new_provider_requests": 0, "response_items": 0, "new_article_versions": 0,
               "duplicate_versions": 0, "new_english_headline_candidates": 0,
               "prospective_scored_titles": 0, "failures": 0,
               "source": "moomoo_webapi_current_keyword_pages_not_historical_archive"}
    symbols = cfg["symbols"]
    try:
        step = 0
        while time.monotonic() - started < seconds and not STOP.exists():
            if count_requests() >= cfg["request_budget"]["moomoo_webapi_max"]:
                receipt["stop_reason"] = "MOOMOO_BUDGET_EXHAUSTED"
                break
            symbol = symbols[step % len(symbols)]
            step += 1
            before = count_requests()
            try:
                response = request_once(symbol)
                receipt["new_provider_requests"] += count_requests() - before
                if response.get("http_status") in cfg["request_budget"]["stop_provider_on_http"]:
                    receipt["stop_reason"] = "AUTH_OR_RATE_LIMIT"
                    break
                if response.get("ret_code") != 0:
                    receipt["failures"] += 1
                    receipt["stop_reason"] = "BUSINESS_REJECTION"
                    break
                else:
                    raw = PRIVATE / f"moomoo_news_probe_{count_requests():02}.json"
                    result = ingest_moomoo_probe(raw, mode="PROSPECTIVE", observation_start_utc=start_utc)
                    receipt["response_items"] += result["response_items"]
                    receipt["new_article_versions"] += result["new_versions"]
                    receipt["duplicate_versions"] += result["duplicate_versions"]
                    receipt["new_english_headline_candidates"] += result["english_title_candidates"]
                    if result["english_title_candidates"]:
                        scored = score_new(max(0, cfg["prospective"]["new_versions_score_max"] - receipt["prospective_scored_titles"]))
                        receipt["prospective_scored_titles"] += scored["actual_finbert_inference_titles"]
                        receipt["failures"] += scored["failures"]
            except (OSError, ValueError, RuntimeError) as exc:
                receipt["failures"] += 1
                receipt["last_error_class"] = type(exc).__name__
                receipt["stop_reason"] = ("LOCAL_AUTH_FAILURE" if type(exc).__name__ == "OAuthError"
                                          else "PROVIDER_STOPPED" if "PROVIDER_STOPPED" in str(exc)
                                          else "UNHANDLED_CYCLE_FAILURE")
                break
            if receipt["prospective_scored_titles"] >= cfg["prospective"]["new_versions_score_max"]:
                receipt["stop_reason"] = "SCORE_CAP"
                break
            remaining = seconds - (time.monotonic() - started)
            if remaining <= 0:
                break
            time.sleep(min(interval, remaining))
        receipt.setdefault("stop_reason", "USER_STOP" if STOP.exists() else "TIME_BOUND")
    finally:
        receipt["completed_at_utc"] = now()
        receipt["elapsed_seconds"] = time.monotonic() - started
        receipt["total_moomoo_requests_in_v4"] = count_requests()
        summarize(DB)
        save(PUBLIC / "online_observation_redacted.json", receipt)
        LOCK.unlink(missing_ok=True)
        STOP.unlink(missing_ok=True)
    return receipt


def status() -> dict:
    if not LOCK.exists():
        return {"state": "STOPPED", "completed_receipt_exists": (PUBLIC / "online_observation_redacted.json").exists()}
    value = {}
    try:
        value = json.loads(LOCK.read_text(encoding="utf-8"))
        import psutil
        alive = psutil.pid_exists(int(value["pid"]))
    except (OSError, ValueError, KeyError, ImportError):
        alive = None
    return {"state": "RUNNING" if alive else "LOCK_UNVERIFIED", "started_at_utc": value.get("started_at_utc"),
            "stop_requested": STOP.exists()}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--seconds", type=int, default=300)
    run_parser.add_argument("--interval", type=int, default=60)
    sub.add_parser("status")
    sub.add_parser("stop")
    args = parser.parse_args()
    if args.command == "run":
        result = run(args.seconds, interval=args.interval)
    elif args.command == "stop":
        STOP.write_text(now(), encoding="utf-8")
        result = {"state": "STOP_REQUESTED"}
    else:
        result = status()
    print(json.dumps(result, ensure_ascii=False))
