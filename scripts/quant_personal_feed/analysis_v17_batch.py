"""Bounded private v1.7 source -> extraction -> interpretation -> translation run."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from .analysis_contract_v17 import PROMPT_VERSION, SCHEMA_VERSION, _field, messages, parse_validate, segments, sha
from .analysis_v15b import FEED, owner_id
from .analysis_v16 import PAUSED, RUNTIME, _terminate
from .collector import ROOT

CONFIG = ROOT / "configs/quant_portal_personal_v17_20261117.json"
PRIVATE = ROOT / "artifacts/quant_personal_v17_20261117"
PUBLIC = ROOT / "results/quant_portal_personal_v17_20261117"
REGRESSION = (
    "d50e2388ef232380eb55d2678bb3a10664db7f4d5deec5fe466ef690df5cab54",
    "0f8b8d9d23905f3be317792e12bd8db88e810be48bcb34d70bd1ccd4698176b4",
    "0c87d0a5ff31eb328102ac7e6f9a4f66fe71c6918a09c24424f4fb4c070d53e1",
)
CUTOFF = "2026-11-16T08:05:19Z"
PATTERNS = (
    ("SINGLE_EVENT", None),
    ("MIXED_NUMBERED", re.compile(r"(?m)^\s*\d{1,2}[.、．]")),
    ("ATTRIBUTED", re.compile(r"否认|驳斥|指控|指责|声称|传闻|警告称")),
    ("NUMERIC", re.compile(r"\d+(?:\.\d+)?\s*(?:%|亿美元|亿元|美元|万|亿|基点)")),
)


def utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def cfg():
    data = read(CONFIG)
    if data["prompt_version"] != PROMPT_VERSION or data["schema_version"] != SCHEMA_VERSION:
        raise RuntimeError("CONTRACT_CONFIG_MISMATCH")
    return data


def _rows() -> list[dict]:
    owner = owner_id()
    with sqlite3.connect(FEED.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        binding = db.execute("SELECT owner_id FROM owner_binding WHERE singleton=1").fetchone()
        if not binding or binding[0] != owner:
            raise RuntimeError("OWNER_MISMATCH")
        return [dict(row) for row in db.execute("""SELECT v.item_key,v.version,v.content_sha256,v.title,
            v.content,v.published_at_utc,v.received_at,i.first_received_at FROM news_versions v
            JOIN news_items i ON i.item_key=v.item_key AND i.latest_version=v.version
            WHERE v.owner_id=? ORDER BY i.first_received_at,v.item_key""", (owner,))]


def freeze() -> dict:
    if not PAUSED.exists():
        raise RuntimeError("PAUSE_V16_FIRST")
    if (PRIVATE / "selection_private.json").exists():
        raise FileExistsError("FROZEN_SELECTION_EXISTS")
    config = cfg()
    rows = _rows()
    by_key = {x["item_key"]: x for x in rows}
    selected: list[tuple[str, dict]] = [("V16_REJECTED_REGRESSION", by_key[key]) for key in REGRESSION]
    used = set(REGRESSION)
    eligible = [x for x in rows if x["first_received_at"] >= CUTOFF and x["item_key"] not in used]
    for category, pattern in PATTERNS:
        for row in eligible:
            pieces = segments(row["title"], row["content"])
            yes = (len(pieces) == 1) if category == "SINGLE_EVENT" else (
                len(pieces) > 1 if category == "MIXED_NUMBERED" else bool(pattern.search(row["content"])))
            if yes and row["item_key"] not in used:
                selected.append((category, row)); used.add(row["item_key"]); break
    for row in rows:
        if re.search(r"否认|驳斥|辟谣", row["content"]) and row["item_key"] not in used:
            selected.append(("DENIAL_SAVED_DEVELOPMENT", row)); used.add(row["item_key"]); break
    if len(selected) > config["max_items"]:
        selected = selected[:config["max_items"]]
    frozen = []
    for index, (category, row) in enumerate(selected, 1):
        pieces = segments(row["title"], row["content"])
        # A numbered bulletin is a collection of candidates. The first item is
        # fixed before any new model output; all other items remain unprocessed.
        chosen = pieces[0]
        task_id = f"V17-{index:02d}"
        frozen.append({"task_id": task_id, "selection_category": category,
            "item_key": row["item_key"], "news_version": row["version"],
            "source_hash": row["content_sha256"], "published_at": row["published_at_utc"],
            "first_received_at": row["first_received_at"], "source_text": chosen,
            "source_text_hash": sha(chosen), "candidate_count": len(pieces),
            "selected_candidate_index": 0,
            "excluded_candidate_count": len(pieces) - 1,
            "source_characters": len(chosen),
            "input_gate": "READY" if len(chosen) <= config["max_source_characters_per_candidate"] else "SOURCE_OVER_BUDGET"})
    private = {"frozen_at_utc": utc(), "source": "OWNER_ONLY_SAVED_JIN10_CACHE",
               "config_sha256": hashlib.sha256(CONFIG.read_bytes()).hexdigest(), "tasks": frozen}
    write(PRIVATE / "selection_private.json", private)
    public = {"schema": "personal_news_v17_selection_redacted_v1", "frozen_at_utc": private["frozen_at_utc"],
              "config_sha256": private["config_sha256"], "selection_rule":
              "3 saved v16.4 rejects, then earliest post-cutoff single, numbered mixed, attributed, numeric reports, then earliest saved denial; one first numbered candidate per mixed bulletin",
              "independent_test": False, "tasks": [{k: v for k, v in x.items() if k not in {"source_text"}}
                  for x in frozen]}
    write(PUBLIC / "selection_manifest_redacted.json", public)
    return {"frozen_tasks": len(frozen), "categories": [x["selection_category"] for x in frozen],
            "over_budget": sum(x["input_gate"] != "READY" for x in frozen)}


def _model(req: Path, response: Path, config: dict) -> tuple[dict | None, str | None, float]:
    runtime = read(RUNTIME)
    python = Path(runtime["python_exe"])
    model = Path(runtime["model_dir"])
    if not python.is_file() or not model.is_dir() or model.name != config["model_revision"]:
        return None, "PINNED_RUNTIME_UNAVAILABLE", 0.0
    env = os.environ.copy()
    env["QUANT_V15_MODEL_DIR"] = str(model)
    loaded = req.with_suffix(".loaded")
    loaded.unlink(missing_ok=True)
    started = time.monotonic()
    proc = subprocess.Popen([str(python), "-m", "scripts.quant_personal_feed.generate_v17",
        "--request", str(req), "--response", str(response)], cwd=ROOT, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    while proc.poll() is None and not loaded.exists() and time.monotonic() - started < config["model_load_timeout_seconds"]:
        time.sleep(.2)
    if proc.poll() is None and not loaded.exists():
        okay = _terminate(proc)
        return None, "LOAD_TIMEOUT_TERMINATED" if okay else "LOAD_TIMEOUT_UNCONFIRMED", time.monotonic()-started
    if loaded.exists() and proc.poll() is None:
        try:
            proc.wait(timeout=config["generation_timeout_seconds"])
        except subprocess.TimeoutExpired:
            okay = _terminate(proc)
            return None, "GENERATION_TIMEOUT_TERMINATED" if okay else "GENERATION_TIMEOUT_UNCONFIRMED", time.monotonic()-started
    if proc.returncode or not response.exists():
        return None, "MODEL_PROCESS_FAILED", time.monotonic()-started
    try:
        return read(response), None, time.monotonic()-started
    except (OSError, json.JSONDecodeError):
        return None, "RESPONSE_FILE_INVALID", time.monotonic()-started


def preflight() -> dict:
    config = cfg()
    manifest = read(PRIVATE / "selection_private.json")
    runtime = read(RUNTIME)
    env = os.environ.copy()
    env["QUANT_V15_MODEL_DIR"] = runtime["model_dir"]
    checks = []
    for task in manifest["tasks"]:
        request = PRIVATE / "preflight" / f"{task['task_id']}.request.json"
        response = PRIVATE / "preflight" / f"{task['task_id']}.response.json"
        write(request, {"stage": "A", "payload": {"source_text": task["source_text"]},
            "model_revision": config["model_revision"], "seed": config["seed"],
            "max_input_tokens": config["max_input_tokens"],
            "max_new_tokens": config["max_new_tokens"]["A"]})
        call = subprocess.run([runtime["python_exe"], "-m", "scripts.quant_personal_feed.generate_v17",
            "--request", str(request), "--response", str(response), "--preflight"],
            cwd=ROOT, env=env, capture_output=True, timeout=90)
        outcome = read(response) if response.exists() else {"error": "PREFLIGHT_PROCESS_FAILED"}
        checks.append({"task_id": task["task_id"], "source_hash": task["source_text_hash"],
                       "input_tokens": outcome.get("input_tokens"),
                       "within_budget": bool(outcome.get("within_budget")) and call.returncode == 0,
                       "error": outcome.get("error") if call.returncode == 0 else "PREFLIGHT_PROCESS_FAILED"})
    frozen_files = [CONFIG, ROOT / "scripts/quant_personal_feed/analysis_contract_v17.py",
                    ROOT / "scripts/quant_personal_feed/generate_v17.py",
                    ROOT / "scripts/quant_personal_feed/analysis_v17_batch.py",
                    PRIVATE / "selection_private.json"]
    hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in frozen_files}
    result = {"schema": "personal_news_v17_preflight_redacted_v1", "checked_at_utc": utc(),
              "model_revision": config["model_revision"], "max_input_tokens": config["max_input_tokens"],
              "checks": checks, "frozen_hashes": hashes,
              "all_ready": all(x["within_budget"] for x in checks)}
    write(PUBLIC / "preflight_redacted.json", result)
    return {"checked": len(checks), "all_ready": result["all_ready"], "max_tokens": max(x["input_tokens"] or 0 for x in checks)}


def preflight_stage(stage: str) -> dict:
    config = cfg(); manifest = read(PRIVATE / "selection_private.json")
    runtime = read(RUNTIME); env = os.environ.copy(); env["QUANT_V15_MODEL_DIR"] = runtime["model_dir"]
    checks = []
    for task in manifest["tasks"]:
        payload = _payload(task, stage)
        if payload is None:
            continue
        request = PRIVATE / "preflight" / f"{task['task_id']}_{stage}.request.json"
        response = PRIVATE / "preflight" / f"{task['task_id']}_{stage}.response.json"
        write(request, {"stage": stage, "payload": payload,
            "model_revision": config["model_revision"], "seed": config["seed"],
            "max_input_tokens": config["max_input_tokens"], "max_new_tokens": config["max_new_tokens"][stage]})
        call = subprocess.run([runtime["python_exe"], "-m", "scripts.quant_personal_feed.generate_v17",
            "--request", str(request), "--response", str(response), "--preflight"],
            cwd=ROOT, env=env, capture_output=True, timeout=90)
        outcome = read(response) if response.exists() else {"error": "PREFLIGHT_PROCESS_FAILED"}
        checks.append({"task_id": task["task_id"], "input_tokens": outcome.get("input_tokens"),
            "within_budget": call.returncode == 0 and bool(outcome.get("within_budget")),
            "error": outcome.get("error") if call.returncode == 0 else "PREFLIGHT_PROCESS_FAILED"})
    result = {"stage": stage, "checked_at_utc": utc(), "checks": checks,
              "all_ready": bool(checks) and all(x["within_budget"] for x in checks),
              "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    write(PUBLIC / f"preflight_{stage.lower()}_redacted.json", result)
    return {"checked": len(checks), "all_ready": result["all_ready"]}


def _reviewed(task_id: str, stage: str) -> bool:
    path = PRIVATE / "semantic_review_private.json"
    return path.exists() and read(path).get(task_id, {}).get(stage, {}).get("decision") == "ACCEPT"


def _payload(task: dict, stage: str) -> dict | None:
    if stage == "A":
        return {"source_text": task["source_text"]}
    if stage == "B":
        review_path = PRIVATE / "semantic_review_private.json"
        review = read(review_path).get(task["task_id"], {}).get("A", {}) if review_path.exists() else {}
        previous = read(PRIVATE / "attempts" / f"{task['task_id']}_A.json")
        if review.get("source_text_hash") != task["source_text_hash"] or review.get("raw_sha256") != previous.get("raw_sha256"):
            return None
        if review.get("decision") == "ACCEPT" and previous.get("validation", {}).get("mechanical_valid"):
            fields = previous["parsed"]
        elif review.get("decision") == "ACCEPT_PARTIAL" and review.get("accepted_field_paths"):
            # Postrun reviewer-assisted field subset. The raw Task A result and
            # its failed full-schema score remain immutable; this is a separate
            # development branch and cannot be counted as pure model success.
            fields = {}
            for path in review["accepted_field_paths"]:
                bits = path.split(".")
                if len(bits) == 1:
                    value = previous["parsed"].get(bits[0])
                elif len(bits) == 2 and bits[1].isdigit():
                    values = previous["parsed"].get(bits[0])
                    value = values[int(bits[1])] if isinstance(values, list) and int(bits[1]) < len(values) else None
                else:
                    return None
                if not isinstance(value, dict) or not isinstance(value.get("quote"), str) or value["quote"] not in task["source_text"]:
                    return None
                if bits[0] == "numbers":
                    if not isinstance(value.get("value"), str) or value["value"] not in value["quote"]:
                        return None
                    if value.get("unit") is not None and value["unit"] not in value["quote"]:
                        return None
                elif _field(value, task["source_text"], path, literal=bits[0] in {"actor", "entities", "attribution"}):
                    return None
                fields[path] = value
        else:
            return None
        return {"validated_fields": fields, "source_context": task["source_text"],
                "review_basis": "CODEX_REVIEWED_PARTIAL_FIELDS" if review.get("decision") == "ACCEPT_PARTIAL" else "CODEX_REVIEWED_FULL_EXTRACTION"}
    if stage == "T":
        if not _reviewed(task["task_id"], "B"):
            return None
        previous = read(PRIVATE / "attempts" / f"{task['task_id']}_B.json")
        if not previous.get("validation", {}).get("mechanical_valid"):
            return None
        return {"validated_explanation": previous["parsed"]}
    return None


def run(stage: str) -> dict:
    if not PAUSED.exists():
        raise RuntimeError("OLD_CONTINUOUS_WORKER_NOT_PAUSED")
    config = cfg()
    manifest = read(PRIVATE / "selection_private.json")
    counts = {"tasks": len(manifest["tasks"]), "attempted": 0, "raw_saved": 0,
              "mechanical_valid": 0, "skipped_prior_review": 0, "skipped_budget": 0, "failures": []}
    for task in manifest["tasks"]:
        if task["input_gate"] != "READY":
            counts["skipped_budget"] += 1; continue
        dest = PRIVATE / "attempts" / f"{task['task_id']}_{stage}.json"
        if dest.exists():
            continue  # a frozen task/stage is never silently rerun
        payload = _payload(task, stage)
        if payload is None:
            counts["skipped_prior_review"] += 1; continue
        request = PRIVATE / "requests" / f"{task['task_id']}_{stage}.json"
        response = PRIVATE / "responses" / f"{task['task_id']}_{stage}.json"
        request.parent.mkdir(parents=True, exist_ok=True); response.parent.mkdir(parents=True, exist_ok=True)
        write(request, {"stage": stage, "payload": payload, "model_revision": config["model_revision"],
                        "seed": config["seed"], "max_input_tokens": config["max_input_tokens"],
                        "max_new_tokens": config["max_new_tokens"][stage]})
        started = utc()
        output, error, elapsed = _model(request, response, config)
        counts["attempted"] += 1
        parsed = validation = mode = view = None
        raw = output.get("raw") if isinstance(output, dict) else None
        if isinstance(output, dict) and output.get("error"):
            error = output["error"]
        if isinstance(raw, str):
            counts["raw_saved"] += 1
            try:
                parsed, validation, mode, view = parse_validate(stage, raw, task["source_text"] if stage != "T" else
                    json.dumps(payload, ensure_ascii=False))
            except (ValueError, json.JSONDecodeError, TypeError) as exc:
                error = "PARSE_" + type(exc).__name__
        if validation and validation["mechanical_valid"]:
            counts["mechanical_valid"] += 1
        if error or not validation or not validation["mechanical_valid"]:
            counts["failures"].append({"task_id": task["task_id"], "error": error or validation["errors"]})
        record = {"task_id": task["task_id"], "stage": stage, "started_at": started,
            "completed_at": utc(), "source_text_hash": task["source_text_hash"],
            "request_sha256": hashlib.sha256(request.read_bytes()).hexdigest(),
            "response_sha256": hashlib.sha256(response.read_bytes()).hexdigest() if response.exists() else None,
            "raw_sha256": sha(raw) if isinstance(raw, str) else None,
            "parsed_view_sha256": sha(view) if isinstance(view, str) else None,
            "parse_mode": mode, "model_metadata": {k: output.get(k) for k in
                ("input_tokens", "generated_tokens", "stop_reason", "device", "dtype")}
                if isinstance(output, dict) else None,
            "elapsed_seconds": elapsed, "error": error, "parsed": parsed,
            "validation": validation, "raw": raw}
        write(dest, record)
        if error and ("UNCONFIRMED" in str(error) or error == "MODEL_PROCESS_FAILED"):
            counts["stop_reason"] = error
            break
    public = {"schema": "personal_news_v17_bounded_run_redacted_v1", "stage": stage,
              "run_at_utc": utc(), "prompt_version": PROMPT_VERSION,
              "model_revision": config["model_revision"], "selection_manifest_sha256":
              hashlib.sha256((PRIVATE / "selection_private.json").read_bytes()).hexdigest(),
              "counts": counts, "attempts": []}
    for task in manifest["tasks"]:
        path = PRIVATE / "attempts" / f"{task['task_id']}_{stage}.json"
        if path.exists():
            x = read(path)
            public["attempts"].append({k: x.get(k) for k in ("task_id", "stage", "raw_sha256", "parse_mode", "model_metadata", "elapsed_seconds", "error")}
                | {"mechanical_valid": (x.get("validation") or {}).get("mechanical_valid"),
                   "mechanical_errors": (x.get("validation") or {}).get("errors")})
    write(PUBLIC / f"run_{stage.lower()}_redacted.json", public)
    return counts


def main():
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=("freeze", "preflight", "preflight-b", "preflight-t", "run-a", "run-b", "run-t"))
    a = p.parse_args()
    result = freeze() if a.action == "freeze" else preflight() if a.action == "preflight" else preflight_stage(a.action[-1].upper()) if a.action.startswith("preflight-") else run(a.action[-1].upper())
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
