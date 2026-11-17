"""Freeze, preflight and run at most 16 private extraction development tasks."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from .analysis_contract_v182 import PROMPT_VERSION, SCHEMA_VERSION, parse_validate
from .analysis_contract_v18 import event_spans, sentence_spans
from .analysis_v16 import PAUSED, RUNTIME, _terminate
from .analysis_v17_batch import _rows
from .collector import ROOT
from .event_rules import CATEGORY_RULES, TECH_PRESET_CATEGORIES, TECH_PRESET_ENTITIES, classify, focus

CONFIG = ROOT / "configs/news_extraction_v182_20261117.json"
PRIVATE = ROOT / "artifacts/news_extraction_v182_20261117"
PUBLIC = ROOT / "results/news_extraction_v182_20261117"
OLD = ROOT / "artifacts/quant_personal_v18_1_20261117/frozen_tasks.json"
CODE = ("scripts/quant_personal_feed/analysis_contract_v182.py",
        "scripts/quant_personal_feed/analysis_v182_run.py",
        "scripts/quant_personal_feed/generate_v182.py",
        "scripts/quant_personal_feed/analysis_contract_v18.py",
        "scripts/quant_personal_feed/analysis_contract_v15b.py",
        "scripts/quant_personal_feed/generate_v15b.py",
        "scripts/quant_personal_feed/event_rules.py")


def utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def tasks() -> list[dict]:
    manifest = read(PRIVATE / "new_frozen_tasks_private.json")
    receipt = read(PUBLIC / "new_freeze_redacted.json")
    if sha(PRIVATE / "new_frozen_tasks_private.json") != receipt["private_input_sha256"]:
        raise RuntimeError("FROZEN_INPUT_CHANGED")
    if sha(CONFIG) != receipt["config_sha256"]:
        raise RuntimeError("FROZEN_CONFIG_CHANGED")
    if any(sha(ROOT / name) != digest for name, digest in receipt["code_sha256"].items()):
        raise RuntimeError("FROZEN_CODE_CHANGED")
    return manifest["tasks"]


def freeze() -> dict:
    if not PAUSED.exists():
        raise RuntimeError("OLD_AUTO_INTERPRETATION_NOT_PAUSED")
    target = PRIVATE / "new_frozen_tasks_private.json"
    if target.exists():
        raise FileExistsError("NEW_INPUT_ALREADY_FROZEN")
    config = read(CONFIG)
    if config["new_prompt_version"] != PROMPT_VERSION or config["new_schema_version"] != SCHEMA_VERSION:
        raise RuntimeError("CONTRACT_CONFIG_MISMATCH")
    excluded = {x["source_hash"] for x in read(OLD)["tasks"]}
    chosen, seen_groups = [], set()
    considered = {"cached": 0, "old_hash": 0, "not_relevant": 0, "duplicate_group": 0,
                  "source_over_budget": 0, "source_hash_mismatch": 0, "no_sentence": 0}
    for row in _rows():
        considered["cached"] += 1
        expected = hashlib.sha256((row["title"] + "\n" + row["content"]).encode("utf-8")).hexdigest()
        if expected != row["content_sha256"]:
            considered["source_hash_mismatch"] += 1
            continue
        if expected in excluded:
            considered["old_hash"] += 1
            continue
        labels = classify(row)
        interests = set(TECH_PRESET_CATEGORIES) | {"MACRO_RATES", "GEO_ENERGY"}
        relation = focus(row, labels, set(TECH_PRESET_ENTITIES), interests)
        if not relation["focus_eligible"]:
            considered["not_relevant"] += 1
            continue
        group = labels["event_group_id"]
        if group in seen_groups:
            considered["duplicate_group"] += 1
            continue
        original = row["title"] + "\n" + row["content"]
        if len(original) > config["new_max_source_characters"]:
            considered["source_over_budget"] += 1
            continue
        segments = event_spans(original)
        first = segments[0]
        sentences = sentence_spans(first)
        if not sentences:
            considered["no_sentence"] += 1
            continue
        seen_groups.add(group)
        chosen.append({"task_id": f"V182-{len(chosen)+1:02d}",
                       "item_key": row["item_key"], "news_version": row["version"],
                       "source_hash": expected, "source_original_hash": hashlib.sha256(original.encode()).hexdigest(),
                       "first_received_at": row["first_received_at"], "published_at": row["published_at_utc"],
                       "event_group_id": group, "event_segments_in_bulletin": len(segments),
                       "source_original": original,
                       "payload": {"event_id": first["event_id"], "sentences": sentences}})
        if len(chosen) == config["new_max_generation_calls"]:
            break
    write(target, {"schema": "private_news_extraction_v182_frozen_1", "frozen_at_utc": utc(),
                   "selection": config["new_selection"], "independent_test": False,
                   "tasks": chosen})
    receipt = {"schema": "news_extraction_v182_freeze_redacted_1", "frozen_at_utc": utc(),
               "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
               "worktree_status_sha256": hashlib.sha256(subprocess.check_output(["git", "status", "--short"], cwd=ROOT)).hexdigest(),
               "config_sha256": sha(CONFIG), "old_input_sha256": sha(OLD),
               "model_runtime_config_sha256": sha(RUNTIME),
               "code_sha256": {name: sha(ROOT / name) for name in CODE},
               "private_input_sha256": sha(target), "considered": considered,
               "planned_calls": len(chosen),
               "tasks": [{k: v for k, v in x.items() if k not in {"source_original", "payload"}} |
                         {"sentence_count": len(x["payload"]["sentences"])} for x in chosen],
               "development_only": True}
    write(PUBLIC / "new_freeze_redacted.json", receipt)
    return {"planned_calls": len(chosen), "considered": considered}


def call(task: dict, preflight: bool) -> tuple[dict | None, str | None, float]:
    config = read(CONFIG)
    runtime = read(RUNTIME)
    python, model = Path(runtime["python_exe"]), Path(runtime["model_dir"])
    if not python.is_file() or not model.is_dir() or model.name != config["model_revision"]:
        return None, "PINNED_RUNTIME_UNAVAILABLE", 0.0
    folder = PRIVATE / ("preflight" if preflight else "generation")
    req = folder / f"{task['task_id']}.request.json"
    response = folder / f"{task['task_id']}.response.json"
    write(req, {"payload": task["payload"], "model_revision": config["model_revision"],
                "seed": config["seed"], "max_input_tokens": config["max_input_tokens"],
                "max_new_tokens": config["max_new_tokens"]})
    env = os.environ.copy()
    env["QUANT_V182_MODEL_DIR"] = str(model)
    loaded = req.with_suffix(".loaded")
    loaded.unlink(missing_ok=True)
    started = time.monotonic()
    with (folder / f"{task['task_id']}.stderr.private.log").open("wb") as err:
        proc = subprocess.Popen([str(python), "-m", "scripts.quant_personal_feed.generate_v182",
                                 "--request", str(req), "--response", str(response)] +
                                (["--preflight"] if preflight else []), cwd=ROOT, env=env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if preflight:
            try:
                proc.wait(timeout=90)
            except subprocess.TimeoutExpired:
                okay = _terminate(proc)
                return None, "PREFLIGHT_TIMEOUT_TERMINATED" if okay else "PREFLIGHT_TIMEOUT_UNCONFIRMED", time.monotonic()-started
        else:
            while proc.poll() is None and not loaded.exists() and time.monotonic()-started < config["model_load_timeout_seconds"]:
                time.sleep(.2)
            if proc.poll() is None and not loaded.exists():
                okay = _terminate(proc)
                return None, "LOAD_TIMEOUT_TERMINATED" if okay else "LOAD_TIMEOUT_UNCONFIRMED", time.monotonic()-started
            if proc.poll() is None:
                try:
                    proc.wait(timeout=config["generation_timeout_seconds"])
                except subprocess.TimeoutExpired:
                    okay = _terminate(proc)
                    return None, "GENERATION_TIMEOUT_TERMINATED" if okay else "GENERATION_TIMEOUT_UNCONFIRMED", time.monotonic()-started
    if response.exists():
        try:
            return read(response), None if proc.returncode == 0 else "MODEL_PROCESS_FAILED", time.monotonic()-started
        except (OSError, ValueError):
            pass
    return None, "MODEL_PROCESS_FAILED", time.monotonic()-started


def preflight() -> dict:
    rows = []
    for task in tasks():
        output, error, elapsed = call(task, True)
        rows.append({"task_id": task["task_id"], "input_tokens": (output or {}).get("input_tokens"),
                     "within_budget": bool(output and output.get("within_budget")) and error is None,
                     "error": error or (output or {}).get("error"), "elapsed_seconds": elapsed})
        if error and error.endswith("UNCONFIRMED"):
            break
    receipt = {"schema": "news_extraction_v182_preflight_1", "completed_at_utc": utc(),
               "rows": rows, "all_ready": len(rows) == len(tasks()) and all(x["within_budget"] for x in rows),
               "model_calls": 0}
    write(PUBLIC / "new_preflight_redacted.json", receipt)
    return {"checked": len(rows), "all_ready": receipt["all_ready"]}


def run() -> dict:
    rows = tasks()
    if not PAUSED.exists() or not read(PUBLIC / "new_preflight_redacted.json")["all_ready"]:
        raise RuntimeError("PAUSE_AND_PREFLIGHT_REQUIRED")
    if len(rows) > read(CONFIG)["new_max_generation_calls"]:
        raise RuntimeError("CALL_BUDGET_EXCEEDED")
    for task in rows:
        key = task["task_id"]
        attempt = PRIVATE / "attempts" / f"{key}.json"
        if attempt.exists():
            continue
        start = utc()
        output, error, elapsed = call(task, False)
        raw = (output or {}).get("raw")
        parsed = validation = mode = view = None
        if isinstance(raw, str):
            try:
                parsed, validation, mode, view = parse_validate(raw, task["payload"])
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                error = "PARSE_" + type(exc).__name__
        response = PRIVATE / "generation" / f"{key}.response.json"
        record = {"task_id": key, "started_at_utc": start, "completed_at_utc": utc(),
                  "item_key": task["item_key"], "news_version": task["news_version"],
                  "source_hash": task["source_hash"], "event_group_id": task["event_group_id"],
                  "request_sha256": sha(PRIVATE / "generation" / f"{key}.request.json"),
                  "response_sha256": sha(response) if response.exists() else None,
                  "raw_sha256": hashlib.sha256(raw.encode()).hexdigest() if isinstance(raw, str) else None,
                  "parsed_view_sha256": hashlib.sha256(view.encode()).hexdigest() if isinstance(view, str) else None,
                  "parse_mode": mode, "parsed": parsed, "validation": validation, "raw": raw,
                  "metadata": {k: output.get(k) for k in ("input_tokens", "generated_tokens", "stop_reason",
                             "device", "dtype", "attention", "peak_cuda_bytes")} if output else None,
                  "elapsed_seconds": elapsed, "error": error or (output or {}).get("error"),
                  "review_status": "CODEX_REVIEW_PENDING"}
        write(attempt, record)
        print(json.dumps({"task_id": key, "saved": isinstance(raw, str),
                          "mechanical_valid": (validation or {}).get("valid"),
                          "error": record["error"] or (validation or {}).get("errors")}, ensure_ascii=False), flush=True)
        if error in {"MODEL_PROCESS_FAILED", "LOAD_TIMEOUT_UNCONFIRMED", "GENERATION_TIMEOUT_UNCONFIRMED"}:
            break
    out = []
    for task in rows:
        path = PRIVATE / "attempts" / f"{task['task_id']}.json"
        if path.exists():
            row = read(path)
            out.append({k: row.get(k) for k in ("task_id", "started_at_utc", "raw_sha256", "parse_mode",
                      "metadata", "elapsed_seconds", "error")} |
                       {"mechanical_valid": (row.get("validation") or {}).get("valid"),
                        "mechanical_errors": (row.get("validation") or {}).get("errors"),
                        "abstained": (row.get("validation") or {}).get("abstained")})
    receipt = {"schema": "news_extraction_v182_retest_redacted_1", "completed_at_utc": utc(),
               "planned": len(rows), "attempted": len(out),
               "response_saved": sum(bool(x["raw_sha256"]) for x in out),
               "strict_raw_json": sum(x["parse_mode"] == "RAW_JSON" for x in out),
               "fence_adapted": sum(x["parse_mode"] == "FENCE_ADAPTED" for x in out),
               "mechanical_valid": sum(x["mechanical_valid"] is True for x in out),
               "abstained": sum(x["abstained"] is True for x in out), "attempts": out}
    write(PUBLIC / "new_run_redacted.json", receipt)
    return {k: receipt[k] for k in ("planned", "attempted", "response_saved", "strict_raw_json", "mechanical_valid", "abstained")}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("freeze", "preflight", "run"))
    args = parser.parse_args()
    print(json.dumps({"freeze": freeze, "preflight": preflight, "run": run}[args.stage](), ensure_ascii=False))


if __name__ == "__main__":
    main()
