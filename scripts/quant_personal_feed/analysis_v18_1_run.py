"""Freeze and execute one owner-only v1.8.1 extraction retest."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from .analysis_contract_v18_1 import PROMPT_VERSION, SCHEMA_VERSION, parse_validate, backfill
from .analysis_v16 import PAUSED, RUNTIME, _terminate
from .analysis_v18_freeze import PRIVATE as OLD_PRIVATE
from .collector import ROOT

CONFIG = ROOT / "configs/quant_portal_personal_v18_1_20261117.json"
PRIVATE = ROOT / "artifacts/quant_personal_v18_1_20261117"
PUBLIC = ROOT / "results/quant_portal_personal_v18_1_20261117"
OLD_SELECTION = OLD_PRIVATE / "selection_private.json"
CODE = (
    "scripts/quant_personal_feed/analysis_contract_v18_1.py",
    "scripts/quant_personal_feed/analysis_v18_1_run.py",
    "scripts/quant_personal_feed/generate_v18_1.py",
    "scripts/quant_personal_feed/analysis_contract_v15b.py",
    "scripts/quant_personal_feed/generate_v15b.py",
    "scripts/quant_personal_feed/analysis_v16.py",
    "scripts/quant_personal_feed/event_rules.py",
    "tests/test_quant_personal_v18_1_contract.py",
    "results/quant_portal_personal_v18_1_20261117/review_protocol.md",
)


def utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tasks() -> list[dict]:
    original = read(OLD_SELECTION)
    out = []
    for news in original["tasks"]:
        if news["source_gate"] != "READY":
            continue
        for event in news["events"]:
            if event["run_gate"] == "READY":
                out.append({
                    "task_id": news["task_id"] + "-" + event["event_id"],
                    "news_id": news["task_id"],
                    "item_key": news["item_key"],
                    "news_version": news["news_version"],
                    "source_hash": news["source_hash"],
                    "payload": {
                        "event_id": event["event_id"],
                        "sentences": event["sentences"],
                    },
                })
    return out


def freeze() -> dict:
    if not PAUSED.exists():
        raise RuntimeError("OLD_INTERPRETATION_NOT_PAUSED")
    target = PRIVATE / "frozen_tasks.json"
    if target.exists():
        raise FileExistsError("FROZEN_INPUT_EXISTS")
    config = read(CONFIG)
    if config["prompt_version"] != PROMPT_VERSION or config["schema_version"] != SCHEMA_VERSION:
        raise RuntimeError("CONTRACT_CONFIG_MISMATCH")
    rows = tasks()
    if len(rows) != config["max_generation_calls"]:
        raise RuntimeError("TASK_COUNT_CHANGED")
    write(target, {"schema": "private_v18_1_tasks_1", "frozen_at_utc": utc(),
                   "development_only": True, "tasks": rows})
    files = {name: sha(ROOT / name) for name in CODE}
    files[str(CONFIG.relative_to(ROOT))] = sha(CONFIG)
    versions = {}
    for name in ("torch", "transformers", "accelerate", "huggingface-hub"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    status = subprocess.check_output(["git", "status", "--short"], cwd=ROOT)
    receipt = {
        "schema": "personal_news_v18_1_freeze_redacted_1",
        "frozen_at_utc": utc(),
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "worktree_status_sha256": hashlib.sha256(status).hexdigest(),
        "code_config_sha256": files,
        "source_selection_sha256": sha(OLD_SELECTION),
        "frozen_private_task_sha256": sha(target),
        "model_runtime_config_sha256": sha(RUNTIME),
        "dependency_versions_current_python": versions,
        "task_ids": [row["task_id"] for row in rows],
        "input_sentence_counts": [len(row["payload"]["sentences"]) for row in rows],
        "planned_generation_calls": len(rows),
        "same_seen_development_material": True,
    }
    write(PUBLIC / "run_freeze_redacted.json", receipt)
    return {"frozen": len(rows), "same_seen_development_material": True}


def frozen_tasks() -> list[dict]:
    freeze_receipt = read(PUBLIC / "run_freeze_redacted.json")
    if sha(PRIVATE / "frozen_tasks.json") != freeze_receipt["frozen_private_task_sha256"]:
        raise RuntimeError("FROZEN_INPUT_HASH_CHANGED")
    return read(PRIVATE / "frozen_tasks.json")["tasks"]


def call(task: dict, preflight: bool) -> tuple[dict | None, str | None, float]:
    config = read(CONFIG)
    runtime = read(RUNTIME)
    if not Path(runtime["python_exe"]).is_file() or not Path(runtime["model_dir"]).is_dir():
        raise RuntimeError("MODEL_RUNTIME_UNAVAILABLE")
    key = task["task_id"]
    folder = PRIVATE / ("preflight" if preflight else "generation")
    request = folder / f"{key}.request.json"
    response = folder / f"{key}.response.json"
    write(request, {
        "stage": "A",
        "payload": task["payload"],
        "model_revision": config["model_revision"],
        "seed": config["seed"],
        "max_input_tokens": config["max_input_tokens"],
        "max_new_tokens": config["max_new_tokens"],
    })
    env = os.environ.copy()
    env["QUANT_V18_1_MODEL_DIR"] = runtime["model_dir"]
    loaded = request.with_suffix(".loaded")
    loaded.unlink(missing_ok=True)
    stderr = folder / f"{key}.stderr.private.log"
    started = time.monotonic()
    with stderr.open("wb") as err:
        proc = subprocess.Popen(
            [runtime["python_exe"], "-m", "scripts.quant_personal_feed.generate_v18_1",
             "--request", str(request), "--response", str(response)] +
            (["--preflight"] if preflight else []),
            cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=err, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if preflight:
            try:
                proc.wait(timeout=config["tokenizer_timeout_seconds"])
            except subprocess.TimeoutExpired:
                killed = _terminate(proc)
                return None, "TOKENIZER_TIMEOUT_TERMINATED" if killed else "TOKENIZER_TIMEOUT_UNCONFIRMED", time.monotonic()-started
        else:
            while proc.poll() is None and not loaded.exists() and time.monotonic()-started < config["model_load_timeout_seconds"]:
                time.sleep(.2)
            if proc.poll() is None and not loaded.exists():
                killed = _terminate(proc)
                return None, "LOAD_TIMEOUT_TERMINATED" if killed else "LOAD_TIMEOUT_UNCONFIRMED", time.monotonic()-started
            if proc.poll() is None:
                try:
                    proc.wait(timeout=config["generation_timeout_seconds"])
                except subprocess.TimeoutExpired:
                    killed = _terminate(proc)
                    return None, "GENERATION_TIMEOUT_TERMINATED" if killed else "GENERATION_TIMEOUT_UNCONFIRMED", time.monotonic()-started
    if response.exists():
        try:
            return read(response), None if proc.returncode == 0 else "MODEL_PROCESS_FAILED", time.monotonic()-started
        except (OSError, ValueError):
            pass
    return None, "MODEL_PROCESS_FAILED", time.monotonic()-started


def preflight() -> dict:
    rows = []
    for task in frozen_tasks():
        output, error, elapsed = call(task, preflight=True)
        rows.append({"task_id": task["task_id"], "input_tokens": (output or {}).get("input_tokens"),
                     "within_budget": bool(output and output.get("within_budget")) and not error,
                     "error": error or (output or {}).get("error"), "elapsed_seconds": elapsed})
        if error and error.endswith("UNCONFIRMED"):
            break
    result = {"schema": "personal_news_v18_1_preflight_redacted_1",
              "checked_at_utc": utc(), "rows": rows,
              "all_ready": len(rows) == len(frozen_tasks()) and all(r["within_budget"] for r in rows)}
    write(PUBLIC / "preflight_redacted.json", result)
    return {"checked": len(rows), "all_ready": result["all_ready"]}


def run() -> dict:
    if not PAUSED.exists() or not read(PUBLIC / "preflight_redacted.json")["all_ready"]:
        raise RuntimeError("PAUSE_AND_PREFLIGHT_REQUIRED")
    rows = frozen_tasks()
    if len(rows) > read(CONFIG)["max_generation_calls"]:
        raise RuntimeError("CALL_BUDGET_EXCEEDED")
    for task in rows:
        key = task["task_id"]
        attempt = PRIVATE / "attempts" / f"{key}.json"
        if attempt.exists():
            continue
        start = utc()
        output, error, elapsed = call(task, preflight=False)
        response = PRIVATE / "generation" / f"{key}.response.json"
        raw = (output or {}).get("raw")
        parsed = validation = mode = view = backfilled = None
        if isinstance(raw, str):
            try:
                parsed, validation, mode, view = parse_validate(raw, task["payload"])
                if validation["valid"]:
                    backfilled = backfill(parsed, task["payload"])
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                error = "PARSE_" + type(exc).__name__
        record = {
            "task_id": key, "started_at_utc": start, "completed_at_utc": utc(),
            "news_id": task["news_id"], "item_key": task["item_key"],
            "news_version": task["news_version"], "source_hash": task["source_hash"],
            "request_sha256": sha(PRIVATE / "generation" / f"{key}.request.json"),
            "response_sha256": sha(response) if response.exists() else None,
            "raw_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest() if isinstance(raw, str) else None,
            "parsed_view_sha256": hashlib.sha256(view.encode("utf-8")).hexdigest() if isinstance(view, str) else None,
            "parse_mode": mode, "validation": validation, "parsed": parsed,
            "backfilled": backfilled, "raw": raw,
            "metadata": {k: output.get(k) for k in
                         ("input_tokens", "generated_tokens", "stop_reason", "device", "dtype",
                          "attention", "peak_cuda_bytes")} if output else None,
            "elapsed_seconds": elapsed, "error": error or (output or {}).get("error"),
            "review_status": "CODEX_REVIEW_PENDING",
        }
        write(attempt, record)
        print(json.dumps({"task_id": key, "response_saved": isinstance(raw, str),
                          "mechanical_valid": (validation or {}).get("valid"),
                          "error": record["error"] or (validation or {}).get("errors")},
                         ensure_ascii=False), flush=True)
        if error in {"MODEL_PROCESS_FAILED", "LOAD_TIMEOUT_UNCONFIRMED",
                     "GENERATION_TIMEOUT_UNCONFIRMED", "TOKENIZER_TIMEOUT_UNCONFIRMED"}:
            break
    attempts = []
    for task in rows:
        path = PRIVATE / "attempts" / f"{task['task_id']}.json"
        if path.exists():
            item = read(path)
            attempts.append({k: item.get(k) for k in
                             ("task_id", "started_at_utc", "completed_at_utc", "raw_sha256",
                              "parse_mode", "metadata", "elapsed_seconds", "error")}
                            | {"mechanical_valid": (item.get("validation") or {}).get("valid"),
                               "mechanical_errors": (item.get("validation") or {}).get("errors"),
                               "abstained": (item.get("validation") or {}).get("abstained")})
    result = {
        "schema": "personal_news_v18_1_retest_redacted_1",
        "generated_at_utc": utc(),
        "planned": len(rows), "attempted": len(attempts),
        "response_saved": sum(bool(a["raw_sha256"]) for a in attempts),
        "strict_raw_json": sum(a["parse_mode"] == "RAW_JSON" for a in attempts),
        "fence_adapted": sum(a["parse_mode"] == "FENCE_ADAPTED" for a in attempts),
        "mechanical_valid": sum(a["mechanical_valid"] is True for a in attempts),
        "abstained": sum(a["abstained"] is True for a in attempts),
        "attempts": attempts,
    }
    write(PUBLIC / "run_redacted.json", result)
    return {k: result[k] for k in ("planned", "attempted", "response_saved", "strict_raw_json", "mechanical_valid", "abstained")}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("freeze", "preflight", "run"))
    args = parser.parse_args()
    print(json.dumps({"freeze": freeze, "preflight": preflight, "run": run}[args.action](),
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
