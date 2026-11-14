"""Reaudit a finished N1 wire-v2 grounding run from ignored private artifacts.

Only hashes, bounded counts, status codes and memory summaries leave this
module. Article text, model answers, quotation values and row-level pairings
remain in the ignored run directory. This is a mechanical audit, not a human
event-quality evaluation.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import statistics
import sys

from jsonschema import Draft202012Validator


REPO = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO / "configs/news_n1_smoke_v4_grounded.json"
DEFAULT_RUN = REPO / "artifacts/news_grounding_runtime_20261114/qwen3b_wire_v2_sdpa_run1"
DEFAULT_OUTPUT = REPO / "results/news_grounding_runtime_20261114/inference_aggregate.json"
RUNNER = REPO / "scripts/run_news_n1_grounding.py"
SAMPLE = REPO / "artifacts/news_n1_20261114/upstream_nasdaq_bf9189c_run1/raw_fields_with_body.jsonl"
SAMPLE_MANIFEST = REPO / "artifacts/news_n1_20261114/upstream_nasdaq_bf9189c_run1/local_manifest.json"
MODEL_RECEIPT = REPO / "results/news_first_inference_20261114/model_integrity_receipt.json"
BASELINE_CONFIG = REPO / "configs/news_n1_smoke_v3_prompt_complete.json"

ATTEMPT_RESPONSE_STATUSES = {
    "JSON_PARSE_ERROR", "WIRE_SCHEMA_INVALID", "HOST_BINDING_ERROR",
    "QUOTE_UNRESOLVED", "CANONICAL_INVALID", "CANONICAL_VALID",
}
ATTEMPT_FAILURE_STATUSES = {"CALL_TIMEOUT", "OOM_STOP", "MODEL_CALL_ERROR",
                            "ATTEMPTED_UNCLASSIFIED_RUN_ERROR",
                            "INTERRUPTED_PROCESS_UNCLASSIFIED"}
QUOTE_STATUSES = {"UNIQUE_EXACT", "QUOTE_AMBIGUOUS", "QUOTE_NOT_FOUND", "INVALID_QUOTE"}
PHASES = ("WORKER_PROCESS_STARTED", "MODEL_LOADED", "GENERATION_STARTED", "RESPONSE_SAVED")
MEMORY_FIELDS = ("free_bytes", "total_bytes", "allocated_bytes",
                 "reserved_bytes", "max_allocated_bytes", "max_reserved_bytes")


class GroundingAuditError(RuntimeError):
    """Fail closed with a public-safe reason code."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    if not path.is_file():
        raise GroundingAuditError("REQUIRED_FILE_MISSING")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GroundingAuditError("REQUIRED_JSON_INVALID") from exc
    if not isinstance(value, dict):
        raise GroundingAuditError("REQUIRED_JSON_OBJECT_INVALID")
    return value


def read_jsonl(path: Path, *, tolerate_partial_tail: bool = False) -> list[dict]:
    if not path.is_file():
        raise GroundingAuditError("EVENT_LOG_MISSING")
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    result = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            if tolerate_partial_tail and index == len(lines) - 1:
                break
            raise GroundingAuditError("EVENT_LOG_INVALID") from exc
        if not isinstance(value, dict):
            raise GroundingAuditError("EVENT_LOG_INVALID")
        result.append(value)
    return result


def safe_code(value: object) -> str:
    text = str(value)
    return text if re.fullmatch(r"[A-Z][A-Z0-9_]{0,90}", text) else "REDACTED_CODE"


def ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def repository_path(value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise GroundingAuditError("CONTRACT_PATH_INVALID")
    path = (REPO / value).resolve()
    if not path.is_relative_to(REPO.resolve()) or not path.is_file():
        raise GroundingAuditError("CONTRACT_PATH_INVALID")
    return path


def import_source(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise GroundingAuditError("CODE_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def expected_stop_reason(ids: list[int], eos: int | list[int] | None, cap: int) -> str:
    eos_ids = {eos} if isinstance(eos, int) else set(eos or [])
    if ids and ids[-1] in eos_ids:
        return "EOS"
    if len(ids) >= cap:
        return "OUTPUT_TOKEN_BUDGET_POSSIBLY_TRUNCATED"
    return "OTHER_STOP"


def response_set_sha256(run_dir: Path, launches: list[dict]) -> str:
    elements = []
    for event in launches:
        ordinal = event["ordinal"]
        path = run_dir / f"response_{ordinal:02d}.json"
        if path.is_file():
            elements.append(f"{ordinal}:{sha256(path)}")
    return hashlib.sha256("\n".join(elements).encode("ascii")).hexdigest()


def verify_finished(run_dir: Path) -> tuple[list[dict], dict, dict]:
    events = read_jsonl(run_dir / "run_events.jsonl")
    if (not events or events[0].get("event") != "RUN_STARTED"
            or events[-1].get("event") != "RUN_FINISHED"
            or sum(e.get("event") == "RUN_STARTED" for e in events) != 1
            or sum(e.get("event") == "RUN_FINISHED" for e in events) != 1):
        raise GroundingAuditError("RUN_NOT_FINISHED")
    local = read_json(run_dir / "local_manifest.json")
    shareable = read_json(run_dir / "shareable_summary.json")
    if (not local.get("run_finished_at_utc") or local.get("run_status") == "PREFLIGHT"
            or events[-1].get("status") != local.get("run_status")
            or shareable.get("run_status") != local.get("run_status")
            or shareable.get("counts") != local.get("counts")
            or shareable.get("run_finished_at_utc") != local.get("run_finished_at_utc")):
        raise GroundingAuditError("RUN_FINAL_CHECKPOINT_MISMATCH")
    return events, local, shareable


def verify_recovery(run_dir: Path, events: list[dict], local: dict,
                    shareable: dict, current_runner_sha: str) -> dict | None:
    """Bind an interrupted original run to a narrowly scoped, explicit resume."""
    recovery = local.get("recovery")
    recovery_events = [e for e in events if e.get("event") == "RUN_RECOVERY_STARTED"]
    if recovery is None:
        if recovery_events or shareable.get("recovery") is not None:
            raise GroundingAuditError("UNDECLARED_RECOVERY_EVENT")
        return None
    if (not isinstance(recovery, dict) or len(recovery_events) != 1
            or not isinstance(recovery.get("prestate_sha256"), dict)):
        raise GroundingAuditError("RECOVERY_PROVENANCE_INCOMPLETE")
    names = ("local_manifest", "shareable_summary", "run_events")
    filenames = {"local_manifest": "recovery_prestate_local_manifest.json",
                 "shareable_summary": "recovery_prestate_shareable_summary.json",
                 "run_events": "recovery_prestate_run_events.jsonl"}
    snapshots = {}
    for name in names:
        path = run_dir / filenames[name]
        if not path.is_file() or recovery.get("prestate_sha256", {}).get(name) != sha256(path):
            raise GroundingAuditError("RECOVERY_PRESTATE_HASH_MISMATCH")
        snapshots[name] = path
    prior_local = read_json(snapshots["local_manifest"])
    prior_shareable = read_json(snapshots["shareable_summary"])
    prior_events = read_jsonl(snapshots["run_events"])
    original_runner_source = (run_dir.parent /
                              "original_v4_runner_df563dea6878.py")
    if (not original_runner_source.is_file()
            or recovery.get("original_runner_source_sha256")
            != recovery.get("original_runner_sha256")
            or sha256(original_runner_source)
            != recovery.get("original_runner_sha256")):
        raise GroundingAuditError("RECOVERY_ORIGINAL_RUNNER_SOURCE_MISMATCH")
    current_event_bytes = (run_dir / "run_events.jsonl").read_bytes()
    if (len(events) <= len(prior_events)
            or not current_event_bytes.startswith(snapshots["run_events"].read_bytes())
            or events[:len(prior_events)] != prior_events
            or events[len(prior_events)] != recovery_events[0]
            or prior_local.get("run_status") != "PREFLIGHT"
            or prior_local.get("run_finished_at_utc") is not None
            or prior_local.get("recovery") is not None
            or prior_shareable.get("run_status") != "PREFLIGHT"
            or local.get("run_started_at_utc") != prior_local.get("run_started_at_utc")
            or local.get("config_sha256") != prior_local.get("config_sha256")
            or local.get("input_sample_sha256") != prior_local.get("input_sample_sha256")
            or local.get("input_manifest_sha256") != prior_local.get("input_manifest_sha256")):
        raise GroundingAuditError("RECOVERY_PRESTATE_CONTENT_MISMATCH")
    prior_launches = [e for e in prior_events if e.get("event") == "WORKER_LAUNCH_COMMITTED"]
    prior_finishes = [e for e in prior_events if e.get("event") == "ATTEMPT_FINISHED"]
    if (len(prior_launches) != 2 or [e.get("ordinal") for e in prior_launches] != [1, 2]
            or len(prior_finishes) != 1 or prior_finishes[0].get("ordinal") != 1
            or prior_local.get("counts", {}).get("model_calls_attempted") != 2
            or prior_local.get("counts", {}).get("model_calls_completed") != 1
            or (prior_local.get("article_statuses") or [{}])[0].get("ordinal") != 1
            or len(prior_local.get("article_statuses", [])) != 1
            or (run_dir / "response_02.json").exists()):
        raise GroundingAuditError("RECOVERY_PRIOR_ATTEMPT_SHAPE_MISMATCH")
    if (recovery.get("version") != "external_interruption_resume_v1"
            or recovery.get("original_runner_sha256") != prior_local.get("runner_sha256")
            or recovery.get("recovery_runner_sha256") != current_runner_sha
            or local.get("runner_sha256") != current_runner_sha
            or recovery.get("interrupted_ordinal") != 2
            or recovery.get("interrupted_status") != "INTERRUPTED_PROCESS_UNCLASSIFIED"
            or recovery.get("interruption_cause") != "UNKNOWN"
            or recovery.get("worker_exit_observed_by_original_parent") is not False
            or local.get("run_status") not in {
                "GROUNDED_RUN_FINISHED_WITH_INTERRUPTION", "TOTAL_TIMEOUT", "OOM_STOP",
                "PREFLIGHT_OR_RUN_ERROR"}
            or recovery.get("original_wall_budget_seconds") != 1800
            or not isinstance(recovery.get("wall_seconds_elapsed_at_recovery"), (int, float))
            or isinstance(recovery.get("wall_seconds_elapsed_at_recovery"), bool)
            or recovery["wall_seconds_elapsed_at_recovery"] < 0
            or recovery.get("phase_02_sha256") != sha256(run_dir / "phase_02.jsonl")
            or recovery.get("request_01_sha256") != sha256(run_dir / "request_01.json")
            or recovery.get("request_02_sha256") != sha256(run_dir / "request_02.json")
            or recovery.get("response_01_sha256") != sha256(run_dir / "response_01.json")):
        raise GroundingAuditError("RECOVERY_BINDING_MISMATCH")
    public_recovery = shareable.get("recovery")
    public_keys = ("version", "original_runner_sha256",
                   "original_runner_source_sha256", "recovery_runner_sha256",
                   "interrupted_ordinal", "interrupted_status", "prestate_sha256",
                   "phase_02_sha256", "original_wall_budget_seconds",
                   "wall_seconds_elapsed_at_recovery")
    if not isinstance(public_recovery, dict) or any(
            public_recovery.get(key) != recovery.get(key) for key in public_keys):
        raise GroundingAuditError("PUBLIC_RECOVERY_MISMATCH")
    if (recovery_events[0].get("ordinal") not in (None, 2)
            or [e.get("ordinal") for e in events[len(prior_events) + 1:]
                if e.get("event") == "WORKER_LAUNCH_COMMITTED"]
            != list(range(3, 3 + sum(e.get("event") == "WORKER_LAUNCH_COMMITTED"
                                      for e in events[len(prior_events) + 1:])))):
        raise GroundingAuditError("RECOVERY_ATTEMPT_ORDER_MISMATCH")
    if (not any(e.get("event") == "ATTEMPT_FINISHED"
                and e.get("ordinal") == 2
                and e.get("status") == "INTERRUPTED_PROCESS_UNCLASSIFIED"
                for e in events[len(prior_events) + 1:])
            or any((run_dir / name).exists() for name in
                   ("gpu_worker_02.json", "gpu_host_after_02.json", "stderr_02.txt"))):
        raise GroundingAuditError("RECOVERY_INTERRUPTED_ATTEMPT_MISMATCH")
    return recovery


def memory_summary(phases_by_ordinal: dict[int, list[dict]]) -> dict:
    """Summarize only observed snapshots; never infer a fused SDPA kernel."""
    by_stage: dict[str, list[dict]] = defaultdict(list)
    peak_reset_stages = Counter()
    for events in phases_by_ordinal.values():
        for event in events:
            if event.get("event") == "CUDA_MEMORY":
                stage = safe_code(event.get("stage", "UNKNOWN"))
                by_stage[stage].append(event)
            if event.get("event") == "CUDA_PEAK_RESET":
                peak_reset_stages[safe_code(event.get("stage", "UNKNOWN"))] += 1
    stage_summary = {}
    for stage, snapshots in sorted(by_stage.items()):
        values = {}
        for name in MEMORY_FIELDS:
            observed = [m[name] for m in snapshots if isinstance(m.get(name), int)
                        and not isinstance(m[name], bool) and m[name] >= 0]
            values[name] = {"observed": len(observed),
                            "minimum": min(observed) if observed else None,
                            "maximum": max(observed) if observed else None}
        stage_summary[stage] = {"snapshots": len(snapshots), "bytes": values}
    return {"snapshot_count": sum(len(v) for v in by_stage.values()),
            "by_stage": stage_summary,
            "peak_reset_stage_counts": dict(sorted(peak_reset_stages.items())),
            "configured_attention": "sdpa",
            "actual_sdpa_kernel": "UNKNOWN",
            "interpretation": "CUDA snapshots are observed allocation counters, not a causal OOM attribution; SDPA configuration does not identify the selected kernel."}


def metric_rates(counts: dict) -> dict:
    attempted = counts["model_calls_attempted"]
    denominators = {
        "raw_json_valid": counts["model_calls_completed"],
        "wire_schema_valid": counts["raw_json_valid"],
        "host_body_hash_valid": counts["wire_schema_valid"],
        "canonical_constructible": counts["wire_schema_valid"],
        "canonical_schema_valid": counts["canonical_constructible"],
        "canonical_mechanical_valid": counts["canonical_constructible"],
    }
    result = {}
    for metric, conditional in denominators.items():
        numerator = counts[metric]
        result[metric] = {
            "numerator": numerator,
            "conditional_denominator": conditional,
            "conditional_rate": ratio(numerator, conditional),
            "attempted_denominator": attempted,
            "attempted_rate": ratio(numerator, attempted),
            "fixed_target_denominator": 10,
            "fixed_target_rate": ratio(numerator, 10),
        }
    quote_total = counts["quotes_checked"]
    result["quote_unique_exact"] = {
        "numerator": counts["quotes_unique_exact"], "quote_denominator": quote_total,
        "quote_rate": ratio(counts["quotes_unique_exact"], quote_total),
        "unit": "quotes_not_articles", "zero_quotes_is_success": False,
    }
    return result


def derived_response_status(json_valid: bool, wire_valid: bool | None,
                            grounding: dict | None, canonical_check: dict | None) -> str:
    """Name a response stage without promoting unknown or empty evidence."""
    if not json_valid:
        return "JSON_PARSE_ERROR"
    if wire_valid is not True:
        return "WIRE_SCHEMA_INVALID"
    if grounding is None or not grounding.get("host_body_hash_valid"):
        return "HOST_BINDING_ERROR"
    if grounding.get("grounding_status") == "UNRESOLVED_QUOTES":
        return "QUOTE_UNRESOLVED"
    if grounding.get("canonical") is None or canonical_check is None:
        return "CANONICAL_INVALID"
    return "CANONICAL_VALID" if canonical_check.get("mechanical_valid") else "CANONICAL_INVALID"


def ordinal_status_issues(run_dir: Path, local: dict, preflight: list[dict],
                          launches: list[dict], finished: list[dict],
                          derived: dict[int, str]) -> list[str]:
    """Check every fixed target position against its attempt and result files."""
    issues: list[str] = []
    statuses = local.get("article_statuses", [])
    if (len(statuses) != 10 or sorted(item.get("ordinal") for item in statuses)
            != list(range(1, 11))):
        return ["ARTICLE_STATUS_COVERAGE_MISMATCH"]
    status = {item["ordinal"]: item["status"] for item in statuses}
    fit = {item["ordinal"]: item["fits_with_output_budget"] for item in preflight}
    launched = {item["ordinal"] for item in launches}
    finish_by_ordinal: dict[int, list[str]] = defaultdict(list)
    for item in finished:
        finish_by_ordinal[item["ordinal"]].append(item.get("status"))
    for ordinal in range(1, 11):
        actual = status[ordinal]
        response_exists = (run_dir / f"response_{ordinal:02d}.json").is_file()
        if ordinal in launched:
            if finish_by_ordinal[ordinal] != [actual]:
                issues.append("ARTICLE_STATUS_EVENT_MISMATCH")
            if response_exists:
                if actual != derived.get(ordinal):
                    issues.append("ARTICLE_STATUS_RESPONSE_MISMATCH")
            elif actual not in ATTEMPT_FAILURE_STATUSES:
                issues.append("ARTICLE_STATUS_MISSING_RESPONSE_MISMATCH")
            if actual == "OOM_STOP":
                stderr = run_dir / f"stderr_{ordinal:02d}.txt"
                if (not stderr.is_file()
                        or "out of memory" not in stderr.read_text(
                            encoding="utf-8", errors="replace").lower()):
                    issues.append("OOM_FAILURE_EVIDENCE_MISSING")
        else:
            if ordinal in finish_by_ordinal:
                issues.append("UNLAUNCHED_ATTEMPT_FINISH_EVENT")
            if not fit[ordinal]:
                if actual != "INPUT_TOO_LONG":
                    issues.append("INPUT_TOO_LONG_STATUS_MISMATCH")
            elif not actual.startswith("NOT_ATTEMPTED_AFTER_"):
                issues.append("UNATTEMPTED_STATUS_MISMATCH")
            elif actual != "NOT_ATTEMPTED_AFTER_" + local["run_status"]:
                issues.append("UNATTEMPTED_TERMINAL_STATUS_MISMATCH")
    return sorted(set(issues))


def number_summary(values: list[float]) -> dict:
    return {"count": len(values), "minimum": min(values) if values else None,
            "median": statistics.median(values) if values else None,
            "maximum": max(values) if values else None}


def audit(run_dir: Path, config_path: Path = DEFAULT_CONFIG) -> tuple[dict, dict]:
    run_dir = run_dir.resolve()
    if not run_dir.is_relative_to((REPO / "artifacts").resolve()):
        raise GroundingAuditError("RUN_DIR_OUTSIDE_IGNORED_ARTIFACTS")
    config_path = config_path.resolve()
    if not config_path.is_relative_to(REPO.resolve()):
        raise GroundingAuditError("CONFIG_OUTSIDE_REPOSITORY")
    events, local, shareable = verify_finished(run_dir)
    config = read_json(config_path)
    runner = import_source(RUNNER, "grounded_runner_postrun_audit")
    try:
        runner.load_config(config_path)
    except (KeyError, TypeError, ValueError) as exc:
        raise GroundingAuditError("V4_CONFIG_REJECTED_BY_RUNNER") from exc
    contract = config["contract"]
    paths = {name: repository_path(contract[f"{name}_path"])
             for name in ("schema", "wire_schema", "prompt", "adapter", "validator")}
    adapter = import_source(paths["adapter"], "exact_quote_adapter_postrun_audit")
    validator = import_source(paths["validator"], "canonical_validator_postrun_audit")
    wire_schema = read_json(paths["wire_schema"])
    canonical_schema = read_json(paths["schema"])
    Draft202012Validator.check_schema(wire_schema)
    Draft202012Validator.check_schema(canonical_schema)
    wire_validator = Draft202012Validator(wire_schema)
    model_receipt = read_json(MODEL_RECEIPT)
    issues: list[str] = []

    frozen_hashes = {"input_sample_sha256": sha256(SAMPLE),
                     "input_manifest_sha256": sha256(SAMPLE_MANIFEST),
                     "config_sha256": sha256(config_path),
                     "runner_sha256": sha256(RUNNER),
                     **{f"{name}_sha256": sha256(path) for name, path in paths.items()}}
    recovery = verify_recovery(run_dir, events, local, shareable,
                               frozen_hashes["runner_sha256"])
    for name in paths:
        if contract.get(f"{name}_sha256") != frozen_hashes[f"{name}_sha256"]:
            issues.append("CONFIG_CONTRACT_HASH_MISMATCH")
    for key, digest in frozen_hashes.items():
        if local.get(key) != digest or shareable.get(key) != digest:
            issues.append("RUN_INPUT_OR_CODE_HASH_MISMATCH")
    if (config.get("source", {}).get("sample_sha256") != frozen_hashes["input_sample_sha256"]
            or config["source"].get("sample_manifest_sha256")
            != frozen_hashes["input_manifest_sha256"]
            or config.get("baseline_config_sha256") != sha256(BASELINE_CONFIG)):
        issues.append("SOURCE_OR_BASELINE_BINDING_MISMATCH")
    baseline = read_json(BASELINE_CONFIG)
    for key in ("dataset", "dataset_revision", "target_file", "sample_sha256",
                "first_usable_articles", "set_role"):
        if config["source"].get(key) != baseline["source"].get(key):
            issues.append("SOURCE_SET_CHANGED")
    for key in ("model_preference", "model_revision", "device", "dtype", "context_cap_tokens",
                "budget"):
        if config.get(key) != baseline.get(key):
            issues.append("MODEL_OR_BUDGET_CHANGED")
    if (config.get("pipeline_version") != local.get("pipeline_version")
            or config.get("pipeline_version") != shareable.get("pipeline_version")
            or config.get("attention_implementation") != "sdpa"
            or local.get("attention_implementation") != "sdpa"
            or shareable.get("attention_implementation") != "sdpa"):
        issues.append("PIPELINE_OR_ATTENTION_BINDING_MISMATCH")
    verified_model_files = {item["name"]: item["local_sha256"] for item in model_receipt[
        "local_verification"]["files"]}
    model = local.get("model", {})
    if (model.get("snapshot_revision") != config.get("model_revision")
            or model_receipt["remote_metadata"]["revision"] != config["model_revision"]
            or model.get("generation_config_sha256") != verified_model_files.get("generation_config.json")
            or not model.get("file_hashes")):
        issues.append("MODEL_REVISION_OR_GENERATION_SOURCE_MISMATCH")
    for name, digest in model.get("file_hashes", {}).items():
        if verified_model_files.get(name) != digest:
            issues.append("MODEL_FILE_HASH_MISMATCH")
    if (shareable.get("model_snapshot_revision") != config["model_revision"]
            or shareable.get("model_generation_config_source_sha256")
            != model.get("generation_config_sha256")):
        issues.append("PUBLIC_MODEL_BINDING_MISMATCH")

    preflight_path = run_dir / "token_preflight.json"
    preflight = read_json(preflight_path)
    articles = preflight.get("articles", [])
    if (len(articles) != 10 or [item.get("ordinal") for item in articles]
            != list(range(1, 11))):
        raise GroundingAuditError("TEN_ARTICLE_PREFLIGHT_INCOMPLETE")
    context_limit = preflight.get("context_limit_tokens")
    if (not isinstance(context_limit, int)
            or context_limit != model.get("effective_context_limit_tokens")
            or preflight.get("chat_template_sha256")
            != model.get("tokenizer_chat_template_sha256")):
        issues.append("TOKENIZER_OR_CONTEXT_BINDING_MISMATCH")
    cap = config["budget"]["max_new_tokens"]
    for item in articles:
        if (not isinstance(item.get("prompt_tokens"), int) or item["prompt_tokens"] <= 0
                or item.get("effective_context_limit_tokens") != context_limit
                or item.get("fits_with_output_budget")
                != (item["prompt_tokens"] + cap <= context_limit)):
            issues.append("PREFLIGHT_TOKEN_OR_FIT_MISMATCH")
    by_preflight = {item["ordinal"]: item for item in articles}
    launches = [item for item in events if item.get("event") == "WORKER_LAUNCH_COMMITTED"]
    finished = [item for item in events if item.get("event") == "ATTEMPT_FINISHED"]
    readbacks = [item for item in events if item.get("event") == "RESPONSE_READ_BACK"]
    if (len(launches) > 10 or len({item.get("ordinal") for item in launches}) != len(launches)
            or [item.get("attempt_count") for item in launches] != list(range(1, len(launches) + 1))
            or any(item.get("ordinal") not in by_preflight for item in launches)
            or any(not by_preflight[item["ordinal"]]["fits_with_output_budget"] for item in launches)
            or local["counts"].get("model_calls_attempted") != len(launches)):
        issues.append("ATTEMPT_BUDGET_ORDER_OR_PREFLIGHT_MISMATCH")
    if (len(finished) != len(launches)
            or any(item.get("ordinal") not in {e.get("ordinal") for e in launches}
                   for item in finished)):
        issues.append("ATTEMPT_FINISH_EVENT_MISMATCH")
    source_manifest = read_json(SAMPLE_MANIFEST)
    source_rows = runner.first_ten_inputs(SAMPLE, SAMPLE_MANIFEST,
                                          config["source"]["sample_sha256"])
    if [row.get("ordinal") for row in source_rows] != list(range(1, 11)):
        raise GroundingAuditError("SOURCE_ORDER_MISMATCH")
    by_row = {row["ordinal"]: row for row in source_rows}
    prompt = paths["prompt"].read_text(encoding="utf-8")

    counts = {"articles_considered": 10, "context_preflight_checked_articles": 10,
              "input_too_long": sum(not a["fits_with_output_budget"] for a in articles),
              "model_calls_attempted": len(launches), "worker_process_started": 0,
              "model_load_completed": 0, "generation_started": 0,
              "model_calls_completed": 0, "model_call_failures": 0,
              "raw_json_valid": 0, "wire_schema_valid": 0,
              "host_body_hash_valid": 0, "quotes_checked": 0,
              "quotes_unique_exact": 0, "quotes_ambiguous": 0,
              "quotes_not_found": 0, "quotes_invalid": 0,
              "canonical_constructible": 0, "canonical_schema_valid": 0,
              "canonical_mechanical_valid": 0,
              "no_spans_requested": 0, "empty_records_response": 0}
    wire_error_keywords = Counter()
    canonical_error_keywords = Counter()
    canonical_mechanical_issues = Counter()
    quote_statuses = Counter()
    stop_reasons = Counter()
    dispositions = Counter()
    records_per_response = Counter()
    phase_by_ordinal: dict[int, list[dict]] = {}
    worker_observations: list[tuple[int, dict]] = []
    worker_memory_observations: list[tuple[int, dict]] = []
    host_after_observations: list[tuple[int, dict]] = []
    derived: dict[int, str] = {}
    private_attempts: list[dict] = []
    generated_seconds: list[float] = []
    effective_generation_hashes: list[str] = []
    for launch in launches:
        ordinal = launch["ordinal"]
        request_path = run_dir / f"request_{ordinal:02d}.json"
        response_path = run_dir / f"response_{ordinal:02d}.json"
        phase_path = run_dir / f"phase_{ordinal:02d}.jsonl"
        request = read_json(request_path)
        article = runner.build_article_input(by_row[ordinal], source_manifest)
        model_input = {key: article[key] for key in config["model_input_fields"]}
        expected_messages = runner.render_messages(prompt, model_input)
        if (request.get("messages") != expected_messages
                or request.get("prompt_tokens") != by_preflight[ordinal]["prompt_tokens"]
                or request.get("model_input") != model_input
                or request.get("host_input_binding") != article["input_binding"]
                or request.get("max_new_tokens") != cap
                or request.get("seed") != config["generation"]["seed"]
                or request.get("dtype") != config["dtype"]
                or request.get("device") != config["device"]
                or request.get("attention_implementation") != "sdpa"
                or request.get("use_cache") is not True
                or request.get("generation_output_flags") != config["generation_output_flags"]):
            issues.append("REQUEST_INPUT_OR_GENERATION_MISMATCH")
        phases = read_jsonl(phase_path, tolerate_partial_tail=True) if phase_path.is_file() else []
        phase_by_ordinal[ordinal] = phases
        gpu_worker_path = run_dir / f"gpu_worker_{ordinal:02d}.json"
        gpu_after_path = run_dir / f"gpu_host_after_{ordinal:02d}.json"
        if recovery is not None and ordinal == recovery["interrupted_ordinal"]:
            if gpu_worker_path.exists() or gpu_after_path.exists():
                issues.append("INTERRUPTED_GPU_RECEIPT_UNEXPECTED")
            worker_memory_observations.append((ordinal,
                runner.phase_memory_summary(phase_path)))
        else:
            worker_observation = read_json(gpu_worker_path)
            if worker_observation != runner.phase_memory_summary(phase_path):
                issues.append("WORKER_GPU_TELEMETRY_REPLAY_MISMATCH")
            worker_observations.append((ordinal, worker_observation))
            worker_memory_observations.append((ordinal, worker_observation))
            host_after_observations.append((ordinal, read_json(gpu_after_path)))
        phase_names = [item.get("event") for item in phases]
        for name, counter in (("WORKER_PROCESS_STARTED", "worker_process_started"),
                              ("MODEL_LOADED", "model_load_completed"),
                              ("GENERATION_STARTED", "generation_started")):
            if phase_names.count(name) > 1:
                issues.append("DUPLICATE_WORKER_PHASE")
            counts[counter] += int(name in phase_names)
        if ("MODEL_LOADED" in phase_names
                and phase_names.count("CUDA_PEAK_RESET") != 1):
            issues.append("CUDA_PEAK_RESET_MISSING_OR_DUPLICATED")
        if ("RESPONSE_SAVED" in phase_names
                and not any(p.get("event") == "CUDA_MEMORY"
                            and p.get("stage") == "WORKER_FINALLY" for p in phases)):
            issues.append("WORKER_FINALLY_MEMORY_MISSING")
        private_item = {"ordinal": ordinal, "request_sha256": sha256(request_path),
                        "phase_sha256": sha256(phase_path) if phase_path.is_file() else None,
                        "phase_events": phase_names}
        if not response_path.is_file():
            counts["model_call_failures"] += 1
            stderr_path = run_dir / f"stderr_{ordinal:02d}.txt"
            if stderr_path.is_file():
                private_item["stderr_sha256"] = sha256(stderr_path)
            private_attempts.append(private_item)
            continue

        counts["model_calls_completed"] += 1
        private_item["response_sha256"] = sha256(response_path)
        response = read_json(response_path)
        token_ids = response.get("generated_token_ids")
        if (not isinstance(token_ids, list)
                or any(not isinstance(x, int) or isinstance(x, bool) for x in token_ids)
                or response.get("generated_tokens") != len(token_ids)
                or response.get("prompt_tokens") != by_preflight[ordinal]["prompt_tokens"]
                or response.get("generation_config_source_sha256")
                != model.get("generation_config_sha256")
                or response.get("batch_size") != 1
                or response.get("requested_attention_implementation") != "sdpa"
                or response.get("model_config_attention_implementation") != "sdpa"
                or response.get("actual_sdpa_kernel") != "UNKNOWN"):
            issues.append("RESPONSE_TOKEN_MODEL_OR_ATTENTION_MISMATCH")
        else:
            eos = response.get("tokenizer_special_ids", {}).get("effective_eos_token_id")
            if response.get("stop_reason") != expected_stop_reason(token_ids, eos, cap):
                issues.append("RESPONSE_STOP_REASON_MISMATCH")
        effective = response.get("effective_generation_config")
        if not isinstance(effective, dict):
            issues.append("EFFECTIVE_GENERATION_CONFIG_MISSING")
        else:
            digest = hashlib.sha256(json.dumps(effective, sort_keys=True,
                                               ensure_ascii=False, default=str).encode("utf-8")).hexdigest()
            effective_generation_hashes.append(digest)
            if (digest != response.get("effective_generation_config_sha256")
                    or effective.get("do_sample") is not False
                    or effective.get("num_beams") != 1
                    or effective.get("max_new_tokens") != cap
                    or effective.get("use_cache") is not True
                    or effective.get("output_attentions") is not False
                    or effective.get("output_hidden_states") is not False
                    or effective.get("output_scores") is not False):
                issues.append("EFFECTIVE_GENERATION_CONFIG_MISMATCH")
        seconds = response.get("generation_seconds_synced")
        if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and seconds >= 0:
            generated_seconds.append(float(seconds))
        else:
            issues.append("GENERATION_TIMING_INVALID")
        stop_reasons[safe_code(response.get("stop_reason", "UNKNOWN"))] += 1
        try:
            wire = json.loads(response["raw_text"])
            counts["raw_json_valid"] += 1
            json_valid = True
        except (KeyError, TypeError, json.JSONDecodeError):
            wire = None
            json_valid = False
        wire_path = run_dir / f"wire_{ordinal:02d}.json"
        grounding_path = run_dir / f"grounding_{ordinal:02d}.json"
        canonical_path = run_dir / f"canonical_{ordinal:02d}.json"
        if not json_valid:
            if wire_path.is_file() or grounding_path.is_file() or canonical_path.is_file():
                issues.append("DERIVED_ARTIFACT_WITH_INVALID_JSON")
            derived[ordinal] = "JSON_PARSE_ERROR"
            private_attempts.append(private_item)
            continue

        wire_errors = sorted(wire_validator.iter_errors(wire),
                             key=lambda e: tuple(map(str, e.path)))
        wire_valid = not wire_errors
        stored_wire = read_json(wire_path)
        expected_wire_validation = {
            "wire_schema_valid": wire_valid,
            "wire_error_keywords": sorted(set(str(err.validator) for err in wire_errors)),
        }
        if (stored_wire.get("output") != wire
                or stored_wire.get("validation") != expected_wire_validation):
            issues.append("WIRE_REPLAY_MISMATCH")
        for error in wire_errors:
            wire_error_keywords[safe_code(str(error.validator).upper())] += 1
        grounding = adapter.adapt_wire(wire, article["body"], article["input_binding"],
                                       wire_schema, canonical_schema)
        stored_grounding = read_json(grounding_path)
        if stored_grounding != grounding:
            issues.append("GROUNDING_REPLAY_MISMATCH")
        if wire_valid:
            counts["wire_schema_valid"] += 1
            dispositions[safe_code(wire.get("article_disposition", "UNKNOWN"))] += 1
            n_records = len(wire["records"])
            records_per_response[str(n_records)] += 1
            counts["empty_records_response"] += int(n_records == 0)
            if grounding["host_body_hash_valid"]:
                counts["host_body_hash_valid"] += 1
                spans = grounding["spans"]
                counts["quotes_checked"] += len(spans)
                for span in spans:
                    status = span["status"]
                    if status not in QUOTE_STATUSES:
                        issues.append("UNKNOWN_QUOTE_STATUS")
                    quote_statuses[safe_code(status)] += 1
                counts["quotes_unique_exact"] += sum(
                    s["status"] == "UNIQUE_EXACT" for s in spans)
                counts["quotes_ambiguous"] += sum(
                    s["status"] == "QUOTE_AMBIGUOUS" for s in spans)
                counts["quotes_not_found"] += sum(
                    s["status"] == "QUOTE_NOT_FOUND" for s in spans)
                counts["quotes_invalid"] += sum(
                    s["status"] == "INVALID_QUOTE" for s in spans)
                counts["no_spans_requested"] += int(
                    grounding["grounding_status"] == "NO_SPANS_REQUESTED")
        canonical_check = None
        if grounding.get("canonical") is not None:
            counts["canonical_constructible"] += 1
            canonical = grounding["canonical"]
            canonical_check = validator.validate_output(canonical, article["body"],
                                                        article["input_binding"], paths["schema"])
            stored_canonical = read_json(canonical_path)
            if (stored_canonical.get("output") != canonical
                    or stored_canonical.get("validation") != canonical_check
                    or grounding.get("canonical_schema_valid") != canonical_check["schema_valid"]):
                issues.append("CANONICAL_REPLAY_MISMATCH")
            counts["canonical_schema_valid"] += int(canonical_check["schema_valid"])
            counts["canonical_mechanical_valid"] += int(canonical_check["mechanical_valid"])
            for error in canonical_check["schema_error_codes"]:
                canonical_error_keywords[safe_code(str(error["keyword"]).upper())] += 1
            for code in canonical_check["mechanical_issue_codes"]:
                canonical_mechanical_issues[safe_code(code)] += 1
        elif canonical_path.is_file():
            issues.append("CANONICAL_FILE_WITH_UNCONSTRUCTED_OUTPUT")
        derived[ordinal] = derived_response_status(True, wire_valid, grounding, canonical_check)
        private_item.update({"wire_sha256": sha256(wire_path),
                             "grounding_sha256": sha256(grounding_path),
                             "canonical_sha256": sha256(canonical_path)
                             if canonical_path.is_file() else None,
                             "derived_status": derived[ordinal],
                             "quote_status_counts": dict(Counter(s["status"]
                                                                 for s in grounding["spans"]))})
        private_attempts.append(private_item)

    if effective_generation_hashes and (
            len(set(effective_generation_hashes)) != 1
            or any(digest != local.get("effective_generation_config_sha256")
                   for digest in effective_generation_hashes)
            or shareable.get("effective_generation_config_sha256")
            != local.get("effective_generation_config_sha256")):
        issues.append("EFFECTIVE_GENERATION_RUN_BINDING_MISMATCH")
    issues.extend(ordinal_status_issues(run_dir, local, articles, launches, finished, derived))
    for event in readbacks:
        if (event.get("ordinal") not in {item["ordinal"] for item in launches}
                or not (run_dir / f"response_{event['ordinal']:02d}.json").is_file()):
            issues.append("RESPONSE_READ_BACK_EVENT_MISMATCH")
    if len(readbacks) != counts["model_calls_completed"]:
        issues.append("RESPONSE_READ_BACK_EVENT_MISMATCH")
    for prefix in ("request", "response", "phase", "wire", "grounding", "canonical"):
        for path in run_dir.glob(f"{prefix}_*.json*"):
            suffix = path.name[len(prefix) + 1:len(prefix) + 3]
            if suffix not in {f"{e['ordinal']:02d}" for e in launches}:
                issues.append("UNATTRIBUTED_ATTEMPT_FILE")
    gpu_before_path = run_dir / "gpu_host_preflight.json"
    gpu_final_path = run_dir / "gpu_host_final.json"
    gpu_before = read_json(gpu_before_path)
    gpu_final = read_json(gpu_final_path)
    local_gpu = local.get("gpu_memory_summary", {})
    gpu_recovery_path = run_dir / "gpu_host_resume_preflight.json"
    if recovery is not None:
        gpu_recovery = read_json(gpu_recovery_path)
        if gpu_recovery.get("gpu_rows"):
            first = gpu_recovery["gpu_rows"][0]
            if (local_gpu.get("host_recovery_observed_at_utc")
                    != gpu_recovery.get("at_utc")
                    or local_gpu.get("host_recovery_free_mib") != first.get("free_mib")
                    or local_gpu.get("host_recovery_used_mib") != first.get("used_mib")):
                issues.append("GPU_RECOVERY_HOST_AGGREGATE_MISMATCH")
        elif any(local_gpu.get(key) is not None for key in
                 ("host_recovery_observed_at_utc", "host_recovery_free_mib",
                  "host_recovery_used_mib")):
            issues.append("GPU_RECOVERY_UNOBSERVED_VALUE")
    elif gpu_recovery_path.exists():
        issues.append("UNDECLARED_GPU_RECOVERY_OBSERVATION")
    worker_available = sum(o.get("memory_snapshot_available", 0)
                           for _, o in worker_memory_observations)
    worker_total = sum(o.get("memory_snapshot_count", 0)
                       for _, o in worker_memory_observations)
    if (local_gpu.get("worker_snapshot_count") != worker_total
            or local_gpu.get("worker_snapshot_available") != worker_available
            or shareable.get("gpu_memory_summary") != local_gpu):
        issues.append("GPU_SUMMARY_COUNT_OR_PUBLIC_MISMATCH")
    for key, func in (("maximum_allocated_bytes", max),
                      ("maximum_reserved_bytes", max),
                      ("minimum_free_bytes", min)):
        observed = [o[key] for _, o in worker_memory_observations if o.get(key) is not None]
        if local_gpu.get(key) != (func(observed) if observed else None):
            issues.append("GPU_WORKER_AGGREGATE_MISMATCH")
    if gpu_before.get("gpu_rows"):
        first = gpu_before["gpu_rows"][0]
        if (local_gpu.get("host_before_free_mib") != first.get("free_mib")
                or local_gpu.get("host_before_used_mib") != first.get("used_mib")
                or local_gpu.get("host_total_mib") != first.get("total_mib")):
            issues.append("GPU_HOST_PREFLIGHT_AGGREGATE_MISMATCH")
    if gpu_final.get("gpu_rows"):
        first = gpu_final["gpu_rows"][0]
        if (local_gpu.get("host_final_free_mib") != first.get("free_mib")
                or local_gpu.get("host_final_used_mib") != first.get("used_mib")):
            issues.append("GPU_HOST_FINAL_AGGREGATE_MISMATCH")
    if (local_gpu.get("host_final_observed_at_utc") != gpu_final.get("at_utc")
            or (host_after_observations and local_gpu.get("host_after_last_observed_at_utc")
                != host_after_observations[-1][1].get("at_utc"))):
        issues.append("GPU_HOST_OBSERVATION_TIME_MISMATCH")
    local_counts = local.get("counts", {})
    runner_count_map = {
        "articles_considered": "articles_considered",
        "context_preflight_checked_articles": "context_preflight_checked_articles",
        "input_too_long": "input_too_long",
        "model_calls_attempted": "model_calls_attempted",
        "worker_process_started": "worker_process_started",
        "model_load_completed": "model_load_completed",
        "generation_started": "generation_started",
        "model_calls_completed": "model_calls_completed",
        "model_call_failures": "model_call_failures",
        "json_parse_valid": "raw_json_valid",
        "wire_schema_valid": "wire_schema_valid",
        "host_body_hash_valid": "host_body_hash_valid",
        "quotes_checked": "quotes_checked",
        "quote_unique": "quotes_unique_exact",
        "quote_ambiguous": "quotes_ambiguous",
        "quote_not_found": "quotes_not_found",
        "quote_invalid": "quotes_invalid",
        "canonical_constructible": "canonical_constructible",
        "canonical_schema_valid": "canonical_schema_valid",
        "canonical_mechanical_valid": "canonical_mechanical_valid",
        "no_spans_requested": "no_spans_requested",
        "empty_records_response": "empty_records_response",
    }
    for runner_key, auditor_key in runner_count_map.items():
        if local_counts.get(runner_key) != counts[auditor_key]:
            issues.append("RUNNER_AND_REPLAY_COUNT_MISMATCH")
    if recovery is not None:
        try:
            original_started = datetime.fromisoformat(local["run_started_at_utc"])
            finished_at = datetime.fromisoformat(local["run_finished_at_utc"])
            wall_elapsed = (finished_at - original_started).total_seconds()
        except (KeyError, TypeError, ValueError):
            issues.append("RECOVERY_WALL_CLOCK_INVALID")
        else:
            if (original_started.tzinfo is None or finished_at.tzinfo is None
                    or wall_elapsed + 2 < recovery["wall_seconds_elapsed_at_recovery"]):
                issues.append("RECOVERY_WALL_CLOCK_INVALID")
            for launch in launches:
                if launch["ordinal"] <= recovery["interrupted_ordinal"]:
                    continue
                try:
                    launch_elapsed = (datetime.fromisoformat(
                        launch["at_utc"]) - original_started).total_seconds()
                except (KeyError, TypeError, ValueError):
                    issues.append("RECOVERY_LAUNCH_TIME_INVALID")
                    continue
                if not 0 <= launch_elapsed <= recovery["original_wall_budget_seconds"]:
                    issues.append("RECOVERY_LAUNCH_AFTER_ORIGINAL_BUDGET")
    status_counts = Counter(safe_code(item.get("status", "UNKNOWN"))
                            for item in local.get("article_statuses", []))
    wall_seconds = [float(item["seconds"]) for item in local.get("article_statuses", [])
                    if isinstance(item.get("seconds"), (int, float))
                    and not isinstance(item["seconds"], bool) and item["seconds"] >= 0]
    issues = sorted(set(issues))
    public = {
        "schema": "news_grounding_runtime_aggregate_v1",
        "date": "2026-11-14", "timezone": "Asia/Shanghai",
        "audit_status": "PASS" if not issues else "FAIL", "audit_issue_codes": issues,
        "pipeline_version": config["pipeline_version"],
        "run_status": safe_code(local.get("run_status", "UNKNOWN")),
        "run_started_at_utc": local["run_started_at_utc"],
        "run_finished_at_utc": local["run_finished_at_utc"],
        "model_repo_id": config["model_preference"],
        "model_revision": config["model_revision"],
        "sample_sha256": frozen_hashes["input_sample_sha256"],
        "sample_manifest_sha256": frozen_hashes["input_manifest_sha256"],
        "config_sha256": frozen_hashes["config_sha256"],
        "runner_sha256": frozen_hashes["runner_sha256"],
        "recovery": ({key: shareable["recovery"].get(key) for key in
                      ("version", "original_runner_sha256",
                       "original_runner_source_sha256", "recovery_runner_sha256",
                       "interrupted_ordinal", "interrupted_status", "prestate_sha256",
                       "phase_02_sha256", "original_wall_budget_seconds",
                       "wall_seconds_elapsed_at_recovery", "interpretation")}
                     if recovery is not None else None),
        "contract_sha256": {key: frozen_hashes[f"{key}_sha256"] for key in paths},
        "model_integrity_receipt_sha256": sha256(MODEL_RECEIPT),
        "generation_config_source_sha256": model.get("generation_config_sha256"),
        "effective_generation_config_sha256": local.get("effective_generation_config_sha256"),
        "configured_attention_implementation": "sdpa",
        "actual_sdpa_kernel": "UNKNOWN",
        "budgets": config["budget"],
        "full_text_token_preflight": {
            "target_articles": len(articles),
            "fit_with_output_reserve": sum(item["fits_with_output_budget"]
                                           is True for item in articles),
            "prompt_tokens": number_summary([item["prompt_tokens"]
                                               for item in articles]),
            "configured_context_cap_tokens": config["context_cap_tokens"],
            "effective_context_limit_tokens": context_limit,
            "reserved_output_tokens": cap,
            "maximum_prompt_plus_reserve_tokens": max(
                item["prompt_tokens"] + cap for item in articles),
            "interpretation": "Context fit does not imply GPU-memory fit or identify an OOM cause.",
        },
        "counts": counts,
        "status_counts": dict(sorted(status_counts.items())),
        "stop_reason_counts": dict(sorted(stop_reasons.items())),
        "wire_error_keyword_occurrences": dict(sorted(wire_error_keywords.items())),
        "canonical_error_keyword_occurrences": dict(sorted(canonical_error_keywords.items())),
        "canonical_mechanical_issue_counts": dict(sorted(canonical_mechanical_issues.items())),
        "quote_status_counts": dict(sorted(quote_statuses.items())),
        "quote_rates": metric_rates(counts),
        "wire_valid_output_distribution": {
            "response_denominator": counts["wire_schema_valid"],
            "article_disposition_counts": dict(sorted(dispositions.items())),
            "records_per_response_counts": dict(sorted(records_per_response.items(),
                                                        key=lambda item: int(item[0]))),
            "empty_records_response_count": counts["empty_records_response"],
        },
        "gpu_memory": memory_summary(phase_by_ordinal),
        "gpu_host_observed": {
            "before_free_mib": local_gpu.get("host_before_free_mib"),
            "before_used_mib": local_gpu.get("host_before_used_mib"),
            "final_free_mib": local_gpu.get("host_final_free_mib"),
            "final_used_mib": local_gpu.get("host_final_used_mib"),
            "recovery_free_mib": local_gpu.get("host_recovery_free_mib"),
            "recovery_used_mib": local_gpu.get("host_recovery_used_mib"),
            "after_attempt_snapshots": len(host_after_observations),
            "own_worker_exits_observed": sum(item.get("worker_exit_observed") is True
                                             for item in local.get("article_statuses", [])),
            "caution": "Host free memory can change because of other processes; before/after difference is not causal GPU use.",
        },
        "timing_seconds": {"attempt_wall_includes_cold_load": number_summary(wall_seconds),
                           "generation_synced": number_summary(generated_seconds)},
        "response_set_sha256": response_set_sha256(run_dir, launches),
        "private_receipt_hashes": {
            "run_events_sha256": sha256(run_dir / "run_events.jsonl"),
            "local_manifest_sha256": sha256(run_dir / "local_manifest.json"),
            "token_preflight_sha256": sha256(preflight_path),
            "shareable_summary_sha256": sha256(run_dir / "shareable_summary.json"),
            "gpu_host_preflight_sha256": sha256(gpu_before_path),
            "gpu_host_final_sha256": sha256(gpu_final_path),
            "gpu_host_resume_preflight_sha256": (sha256(gpu_recovery_path)
                                                  if recovery is not None else None),
            "original_runner_source_sha256": (
                recovery.get("original_runner_source_sha256")
                if recovery is not None else None),
            "gpu_worker_set_sha256": hashlib.sha256("\n".join(
                f"{ordinal}:{sha256(run_dir / f'gpu_worker_{ordinal:02d}.json')}"
                for ordinal, _ in worker_observations).encode("ascii")).hexdigest(),
        },
        "limitations": [
            "Same first ten development articles; no blind or independent test.",
            "Host input binding is a program integrity check, not model hash copying skill.",
            "Exact unique quotes and host offsets are not model offset accuracy or semantic support.",
            "Old model-authored offset metric and new quote-grounding metric have different definitions.",
            "Prompt, wire contract, host adapter and attention backend changed together.",
            "CUDA allocation snapshots cannot isolate an OOM cause or prove a fused SDPA kernel.",
            "No independent human extraction-quality adjudication or financial evaluation.",
        ],
    }
    private = {"schema": "news_grounding_runtime_private_audit_v1",
               "audit_status": public["audit_status"], "audit_issue_codes": issues,
               "attempts": private_attempts, "response_set_sha256": public["response_set_sha256"]}
    return public, private


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    public, private = audit(args.run_dir, args.config)
    encoded_public = (json.dumps(public, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    encoded_private = (json.dumps(private, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    private_path = args.run_dir / "postrun_private_audit.json"
    if ((args.output.exists() and args.output.read_bytes() != encoded_public)
            or (private_path.exists() and private_path.read_bytes() != encoded_private)):
        raise GroundingAuditError("EXISTING_AUDIT_OUTPUT_DIFFERS")
    if not args.output.exists():
        args.output.write_bytes(encoded_public)
    if not private_path.exists():
        private_path.write_bytes(encoded_private)
    print(json.dumps({"audit_status": public["audit_status"],
                      "run_status": public["run_status"], "counts": public["counts"]},
                     ensure_ascii=False))
    return 0 if public["audit_status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

