"""Owner-only bounded v1.8 generation; raw responses remain in gitignored artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from .analysis_contract_v18 import PROMPT_VERSION, SCHEMA_VERSION, parse_validate
from .analysis_v16 import PAUSED, RUNTIME, _terminate
from .analysis_v17_batch import write
from .analysis_v18_freeze import CONFIG, PRIVATE, PUBLIC
from .collector import ROOT

PYTHON = Path(r"E:\quant_news_first_inference_runtime_20261114\venv\Scripts\python.exe")
FILES = [ROOT / "scripts/quant_personal_feed/analysis_contract_v18.py",
         ROOT / "scripts/quant_personal_feed/generate_v18.py",
         ROOT / "scripts/quant_personal_feed/analysis_v18_freeze.py",
         ROOT / "scripts/quant_personal_feed/analysis_v18_run.py",
         ROOT / "scripts/quant_personal_feed/event_rules.py", CONFIG,
         PRIVATE / "selection_private.json"]


def utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def runtime(kind: str) -> dict:
    config = read(CONFIG)
    if kind == "baseline":
        info = read(RUNTIME)
        return {"model_dir": info["model_dir"], "model_revision": config["baseline_revision"],
                "model_file_hashes": {}, "quantization": "none"}
    info = read(PRIVATE / "candidate_model_runtime.json")
    if info["model_revision"] != config["candidate_revision"]:
        raise RuntimeError("CANDIDATE_REVISION_MISMATCH")
    return info


def tasks(stage: str, model_kind: str) -> list[dict]:
    manifest = read(PRIVATE / "selection_private.json")
    out = []
    for news in manifest["tasks"]:
        if news["source_gate"] != "READY":
            continue
        if stage == "T_SOURCE":
            out.append({"task_id": news["task_id"], "news": news,
                        "payload": {"source_original": news["source_original"]}})
        elif stage == "A":
            for event in news["events"]:
                if event["run_gate"] != "READY":
                    continue
                out.append({"task_id": news["task_id"] + "-" + event["event_id"], "news": news,
                            "payload": {"event_id": event["event_id"],
                                        "sentences": event["sentences"],
                                        "candidate_entities": event["candidate_entities"]}})
        elif stage == "B":
            for event in news["events"]:
                if event["run_gate"] != "READY":
                    continue
                task_id = news["task_id"] + "-" + event["event_id"]
                review_path = PRIVATE / "reviews" / f"{model_kind}_{task_id}_A.json"
                attempt_path = PRIVATE / "attempts" / f"{model_kind}_{task_id}_A.json"
                if not review_path.exists() or not attempt_path.exists():
                    continue
                review, attempt = read(review_path), read(attempt_path)
                if review.get("decision") != "ACCEPT" or review.get("raw_sha256") != attempt.get("raw_sha256"):
                    continue
                payload = {"validated_fields": attempt["parsed"], "source_sentences": event["sentences"],
                           "review_basis": "CODEX_REVIEW_DEVELOPMENT"}
                out.append({"task_id": task_id, "news": news, "payload": payload})
    return out


def freeze() -> dict:
    if not PAUSED.exists():
        raise RuntimeError("V16_NOT_PAUSED")
    dest = PRIVATE / "run_freeze.json"
    if dest.exists():
        raise FileExistsError("RUN_ALREADY_FROZEN")
    config = read(CONFIG)
    import importlib.metadata as metadata
    versions = {}
    for name in ("torch", "transformers", "accelerate", "bitsandbytes", "huggingface-hub"):
        try: versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError: versions[name] = None
    hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in FILES}
    import subprocess
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    status = subprocess.check_output(["git", "status", "--short"], cwd=ROOT, text=True)
    payload = {"frozen_at_utc": utc(), "git_head": head, "worktree_status_sha256": hashlib.sha256(status.encode()).hexdigest(),
               "hashes": hashes, "dependency_versions": versions, "prompt_version": PROMPT_VERSION,
               "schema_version": SCHEMA_VERSION, "seed": config["seed"],
               "task_counts": {stage: len(tasks(stage, "baseline")) for stage in ("A", "T_SOURCE")}}
    write(dest, payload)
    write(PUBLIC / "run_freeze_redacted.json", payload)
    return {"frozen": True, "task_counts": payload["task_counts"], "git_head": head}


def make_request(stage: str, kind: str, task: dict) -> dict:
    config = read(CONFIG)
    info = runtime(kind)
    return {"stage": stage, "payload": task["payload"], "model_kind": kind,
            "model_revision": info["model_revision"], "model_file_hashes": info["model_file_hashes"],
            "quantization": info["quantization"], "seed": config["seed"],
            "max_input_tokens": config["max_input_tokens"], "max_new_tokens": config["max_new_tokens"][stage]}


def _call(request: Path, response: Path, kind: str, *, preflight: bool) -> tuple[dict | None, str | None, float]:
    config = read(CONFIG)
    env = os.environ.copy()
    env["QUANT_V18_MODEL_DIR"] = runtime(kind)["model_dir"]
    loaded = request.with_suffix(".loaded")
    loaded.unlink(missing_ok=True)
    stderr = request.with_suffix(".stderr.private.log")
    started = time.monotonic()
    with stderr.open("wb") as err:
        proc = subprocess.Popen([str(PYTHON), "-m", "scripts.quant_personal_feed.generate_v18",
            "--request", str(request), "--response", str(response)] + (["--preflight"] if preflight else []),
            cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if preflight:
            try: proc.wait(timeout=90)
            except subprocess.TimeoutExpired:
                okay = _terminate(proc)
                return None, "TOKENIZER_TIMEOUT_TERMINATED" if okay else "TOKENIZER_TIMEOUT_UNCONFIRMED", time.monotonic()-started
        else:
            while proc.poll() is None and not loaded.exists() and time.monotonic()-started < config["model_load_timeout_seconds"]:
                time.sleep(.2)
            if proc.poll() is None and not loaded.exists():
                okay = _terminate(proc)
                return None, "LOAD_TIMEOUT_TERMINATED" if okay else "LOAD_TIMEOUT_UNCONFIRMED", time.monotonic()-started
            if proc.poll() is None:
                try: proc.wait(timeout=config["generation_timeout_seconds"])
                except subprocess.TimeoutExpired:
                    okay = _terminate(proc)
                    return None, "GENERATION_TIMEOUT_TERMINATED" if okay else "GENERATION_TIMEOUT_UNCONFIRMED", time.monotonic()-started
    if response.exists():
        try: return read(response), None if proc.returncode == 0 else "MODEL_PROCESS_FAILED", time.monotonic()-started
        except (OSError, ValueError): pass
    return None, "MODEL_PROCESS_FAILED", time.monotonic()-started


def preflight(kind: str) -> dict:
    if not (PRIVATE / "run_freeze.json").exists():
        raise RuntimeError("FREEZE_FIRST")
    rows = []
    for stage in ("T_SOURCE", "A"):
        for task in tasks(stage, kind):
            key = f"{kind}_{task['task_id']}_{stage}"
            request = PRIVATE / "requests" / f"{key}.json"
            response = PRIVATE / "preflight" / f"{key}.json"
            write(request, make_request(stage, kind, task))
            response.parent.mkdir(parents=True, exist_ok=True)
            output, error, elapsed = _call(request, response, kind, preflight=True)
            rows.append({"task_id": task["task_id"], "stage": stage, "kind": kind,
                         "input_tokens": output.get("input_tokens") if output else None,
                         "within_budget": bool(output and output.get("within_budget")) and not error,
                         "error": error or (output or {}).get("error"), "elapsed_seconds": elapsed})
    result = {"checked_at_utc": utc(), "kind": kind, "rows": rows,
              "all_ready": all(row["within_budget"] for row in rows)}
    write(PUBLIC / f"preflight_{kind}_redacted.json", result)
    return {"checked": len(rows), "all_ready": result["all_ready"],
            "max_input_tokens": max((row["input_tokens"] or 0 for row in rows), default=0)}


def run(kind: str, stage: str) -> dict:
    if not PAUSED.exists() or not (PRIVATE / "run_freeze.json").exists():
        raise RuntimeError("PAUSE_AND_FREEZE_REQUIRED")
    check = PUBLIC / f"preflight_{kind}_redacted.json"
    if not check.exists() or not read(check)["all_ready"]:
        raise RuntimeError("PREFLIGHT_REQUIRED")
    counts = {"planned": 0, "attempted": 0, "response_saved": 0, "raw_json": 0,
              "adapted_json": 0, "mechanical_valid": 0, "abstained": 0, "failures": []}
    for task in tasks(stage, kind):
        counts["planned"] += 1
        key = f"{kind}_{task['task_id']}_{stage}"
        dest = PRIVATE / "attempts" / f"{key}.json"
        if dest.exists():
            continue
        request = PRIVATE / "requests" / f"{key}.json"
        response = PRIVATE / "responses" / f"{key}.json"
        write(request, make_request(stage, kind, task))
        response.parent.mkdir(parents=True, exist_ok=True)
        started = utc()
        output, error, elapsed = _call(request, response, kind, preflight=False)
        counts["attempted"] += 1
        raw = output.get("raw") if isinstance(output, dict) else None
        parsed = validation = mode = view = None
        if isinstance(raw, str):
            counts["response_saved"] += 1
            try:
                parsed, validation, mode, view = parse_validate(stage, raw, task["payload"])
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                error = "PARSE_" + type(exc).__name__
        if mode == "RAW_JSON": counts["raw_json"] += 1
        if mode == "FENCE_ADAPTED": counts["adapted_json"] += 1
        if validation and validation["valid"]: counts["mechanical_valid"] += 1
        if validation and validation.get("abstained"): counts["abstained"] += 1
        if error or not validation or not validation["valid"]:
            counts["failures"].append({"task_id": task["task_id"], "error": error or validation["errors"]})
        record = {"task_id": task["task_id"], "stage": stage, "model_kind": kind,
                  "started_at_utc": started, "completed_at_utc": utc(),
                  "item_key": task["news"]["item_key"], "news_version": task["news"]["news_version"],
                  "source_hash": task["news"]["source_hash"],
                  "request_sha256": hashlib.sha256(request.read_bytes()).hexdigest(),
                  "response_sha256": hashlib.sha256(response.read_bytes()).hexdigest() if response.exists() else None,
                  "raw_sha256": hashlib.sha256(raw.encode()).hexdigest() if isinstance(raw, str) else None,
                  "parsed_view_sha256": hashlib.sha256(view.encode()).hexdigest() if isinstance(view, str) else None,
                  "parse_mode": mode, "metadata": {k: output.get(k) for k in
                     ("input_tokens", "generated_tokens", "stop_reason", "device", "dtype", "quantization", "peak_cuda_bytes")}
                     if output else None,
                  "elapsed_seconds": elapsed, "error": error or (output or {}).get("error"),
                  "validation": validation, "parsed": parsed, "raw": raw}
        write(dest, record)
        if error in {"MODEL_PROCESS_FAILED", "LOAD_TIMEOUT_UNCONFIRMED", "GENERATION_TIMEOUT_UNCONFIRMED"}:
            counts["stop_reason"] = error
            break
    public = {"schema": "personal_news_v18_bounded_redacted_1", "kind": kind, "stage": stage,
              "run_at_utc": utc(), "prompt_version": PROMPT_VERSION,
              "model_revision": runtime(kind)["model_revision"], "counts": counts,
              "attempts": []}
    for task in tasks(stage, kind):
        path = PRIVATE / "attempts" / f"{kind}_{task['task_id']}_{stage}.json"
        if path.exists():
            item = read(path)
            public["attempts"].append({k: item.get(k) for k in
                ("task_id", "stage", "model_kind", "raw_sha256", "parse_mode", "metadata", "elapsed_seconds", "error")}
                | {"mechanical_valid": (item.get("validation") or {}).get("valid"),
                   "mechanical_errors": (item.get("validation") or {}).get("errors")})
    write(PUBLIC / f"run_{kind}_{stage.lower()}_redacted.json", public)
    return counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("freeze", "preflight", "run-a", "run-t", "run-b"))
    parser.add_argument("--model", choices=("baseline", "candidate"), default="baseline")
    args = parser.parse_args()
    result = freeze() if args.action == "freeze" else preflight(args.model) if args.action == "preflight" else \
        run(args.model, {"run-a": "A", "run-t": "T_SOURCE", "run-b": "B"}[args.action])
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
