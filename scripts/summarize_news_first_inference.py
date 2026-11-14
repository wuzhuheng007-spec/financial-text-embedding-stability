"""Audit one finished N1 inference run and publish aggregate, text-free counts.

This script reads ignored article-level requests, responses and checks only after
the runner's final event and manifest are present. It never publishes article
text, row-level results, local paths, model weights or credentials.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys


REPO = Path(__file__).resolve().parents[1]
DEFAULT_RUN = REPO / "artifacts/news_first_inference_20261114/qwen3b_aa8e725_run1"
DEFAULT_OUTPUT = REPO / "results/news_first_inference_20261114/inference_aggregate.json"
DEFAULT_CONFIG = REPO / "configs/news_n1_smoke_v2.json"
RUNNER = REPO / "scripts/run_news_n1_smoke.py"
SAMPLE = REPO / "artifacts/news_n1_20261114/upstream_nasdaq_bf9189c_run1/raw_fields_with_body.jsonl"
SAMPLE_MANIFEST = REPO / "artifacts/news_n1_20261114/upstream_nasdaq_bf9189c_run1/local_manifest.json"
MODEL_RECEIPT = REPO / "results/news_first_inference_20261114/model_integrity_receipt.json"
ENVIRONMENT_RECEIPT = REPO / "results/news_first_inference_20261114/environment_receipt.json"


def schema_property_names(node: object) -> set[str]:
    if isinstance(node, list):
        return set().union(*(schema_property_names(item) for item in node))
    if not isinstance(node, dict):
        return set()
    names = set(node.get("properties", {}))
    for value in node.values():
        names.update(schema_property_names(value))
    return names


class AuditError(RuntimeError):
    """Use stable, public-safe error codes instead of private content."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    if not path.is_file():
        raise AuditError("REQUIRED_FILE_MISSING")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AuditError("REQUIRED_JSON_INVALID") from exc


def read_events(path: Path, *, tolerate_partial_tail: bool = False) -> list[dict]:
    if not path.is_file():
        raise AuditError("RUN_EVENT_LOG_MISSING")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        events = []
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                if tolerate_partial_tail and index == len(lines) - 1:
                    break
                raise
        return events
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AuditError("EVENT_LOG_INVALID") from exc


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AuditError("CODE_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rate(n: int, d: int) -> float | None:
    return n / d if d else None


def safe_schema_path(value: str, allowed_segments: set[str]) -> str:
    parts = value.split("/")
    safe = []
    for part in parts:
        if not part:
            continue
        if part.isdecimal():
            safe.append("*")
        elif part in allowed_segments:
            safe.append(part)
        else:
            safe.append("REDACTED")
    return "/" + "/".join(safe)


def safe_code(value: str) -> str:
    return value if re.fullmatch(r"[A-Z][A-Z0-9_]{0,80}", value) else "REDACTED_CODE"


def expected_stop_reason(ids: list[int], eos: int | list[int] | None, cap: int) -> str:
    eos_set = {eos} if isinstance(eos, int) else set(eos or [])
    if ids and ids[-1] in eos_set:
        return "EOS"
    if len(ids) >= cap:
        return "OUTPUT_TOKEN_BUDGET_POSSIBLY_TRUNCATED"
    return "OTHER_STOP"


def status_consistency_issues(run_dir: Path, status_by_ordinal: dict[int, str],
                              by_ordinal: dict[int, dict], launched: set[int],
                              finished_statuses: dict[int, list[str]],
                              derived_response_statuses: dict[int, str],
                              run_status: str) -> list[str]:
    """Bind published ordinal statuses to attempt events and response checks."""
    if set(status_by_ordinal) != set(range(1, 11)):
        return ["ARTICLE_STATUS_COVERAGE_MISMATCH"]
    not_attempted_status = (
        "NOT_ATTEMPTED_AFTER_RUN_ERROR" if run_status == "PREFLIGHT_OR_RUN_ERROR" else
        "NOT_ATTEMPTED_AFTER_PREFLIGHT" if run_status == "SMOKE_RUN_FINISHED" else
        "NOT_ATTEMPTED_AFTER_" + run_status)
    issues = []
    for ordinal in range(1, 11):
        actual_status = status_by_ordinal[ordinal]
        response_exists = (run_dir / f"response_{ordinal:02d}.json").is_file()
        if ordinal in launched:
            if (finished_statuses.get(ordinal) != [actual_status]
                    or (response_exists and derived_response_statuses.get(ordinal) != actual_status)
                    or (not response_exists and actual_status not in
                        {"CALL_TIMEOUT", "OOM_STOP", "MODEL_CALL_ERROR",
                         "ATTEMPTED_UNCLASSIFIED_RUN_ERROR"})):
                issues.append("ARTICLE_STATUS_RESPONSE_OR_EVENT_MISMATCH")
            if actual_status == "OOM_STOP":
                stderr_path = run_dir / f"stderr_{ordinal:02d}.txt"
                if (not stderr_path.is_file()
                        or "out of memory" not in stderr_path.read_text(
                            encoding="utf-8", errors="replace").lower()):
                    issues.append("OOM_FAILURE_EVIDENCE_MISMATCH")
        elif ((not by_ordinal[ordinal]["fits_with_output_budget"] and
               actual_status != "INPUT_TOO_LONG") or
              (by_ordinal[ordinal]["fits_with_output_budget"] and
               actual_status != not_attempted_status) or
              ordinal in finished_statuses):
            issues.append("ARTICLE_STATUS_PREFLIGHT_OR_ATTEMPT_MISMATCH")
    return issues


def check_finished(run_dir: Path) -> tuple[list[dict], dict, dict]:
    events = read_events(run_dir / "run_events.jsonl")
    if not events or events[-1].get("event") != "RUN_FINISHED":
        raise AuditError("RUN_NOT_FINISHED")
    if sum(e.get("event") == "RUN_STARTED" for e in events) != 1 or sum(
            e.get("event") == "RUN_FINISHED" for e in events) != 1:
        raise AuditError("RUN_EVENT_BOUNDARY_INVALID")
    local = read_json(run_dir / "local_manifest.json")
    shareable = read_json(run_dir / "shareable_summary.json")
    if (not local.get("run_finished_at_utc") or local.get("run_status") == "PREFLIGHT"
            or events[-1].get("status") != local.get("run_status")
            or shareable.get("run_finished_at_utc") != local.get("run_finished_at_utc")
            or shareable.get("counts") != local.get("counts")):
        raise AuditError("FINAL_CHECKPOINT_INCOMPLETE")
    return events, local, shareable


def repository_path(value: str) -> Path:
    """Keep config-selected contract files inside the audited repository."""
    if not isinstance(value, str) or not value:
        raise AuditError("CONTRACT_PATH_INVALID")
    path = (REPO / value).resolve()
    if not path.is_relative_to(REPO.resolve()) or not path.is_file():
        raise AuditError("CONTRACT_PATH_INVALID")
    return path


def audit(run_dir: Path, config_path: Path = DEFAULT_CONFIG) -> tuple[dict, dict]:
    run_dir = run_dir.resolve()
    ignored_root = (REPO / "artifacts").resolve()
    if not run_dir.is_relative_to(ignored_root):
        raise AuditError("RUN_DIR_OUTSIDE_IGNORED_ARTIFACTS")
    config_path = config_path.resolve()
    if not config_path.is_relative_to(REPO.resolve()):
        raise AuditError("CONFIG_OUTSIDE_REPOSITORY")
    events, local, shareable = check_finished(run_dir)
    config = read_json(config_path)
    model_receipt = read_json(MODEL_RECEIPT)
    issues: list[str] = []
    contract = config.get("contract", {})
    schema_path = repository_path(contract.get("schema_path"))
    prompt_path = repository_path(contract.get("prompt_path"))
    validator_path = repository_path(contract.get("validator_path"))
    allowed_schema_paths = schema_property_names(read_json(schema_path))

    bindings = {
        "input_sample_sha256": sha256(SAMPLE),
        "input_manifest_sha256": sha256(SAMPLE_MANIFEST),
        "config_sha256": sha256(config_path),
        "runner_sha256": sha256(RUNNER),
        "schema_sha256": sha256(schema_path),
        "prompt_sha256": sha256(prompt_path),
        "validator_sha256": sha256(validator_path),
    }
    for kind in ("schema", "prompt", "validator"):
        if contract.get(f"{kind}_sha256") != bindings[f"{kind}_sha256"]:
            issues.append("CONFIG_CONTRACT_HASH_MISMATCH")
    for key, actual in bindings.items():
        if local.get(key) != actual or shareable.get(key) != actual:
            issues.append("RUN_INPUT_OR_CODE_BINDING_MISMATCH")
    expected_model = model_receipt["run_input_and_code_binding"]
    for public_key, model_key in (
            ("input_sample_sha256", "sample_sha256"),
            ("input_manifest_sha256", "sample_manifest_sha256"),
            ("runner_sha256", "runner_sha256"),
            ("schema_sha256", "schema_sha256"),
            ("validator_sha256", "validator_sha256")):
        if local.get(public_key) != expected_model.get(model_key):
            issues.append("PRERUN_GATE_BINDING_MISMATCH")
    if config.get("experiment_id") == "news_schema_repair_prompt_v2":
        baseline = read_json(DEFAULT_CONFIG)
        if (config.get("baseline_config_sha256") != sha256(DEFAULT_CONFIG)
                or sha256(DEFAULT_CONFIG) != expected_model.get("smoke_config_sha256")
                or baseline.get("contract", {}).get("prompt_sha256") != expected_model.get("prompt_sha256")):
            issues.append("PROMPT_REPAIR_BASELINE_BINDING_MISMATCH")
        invariant_keys = ("source", "model_preference", "model_path_policy", "device", "dtype",
                          "context_cap_tokens", "generation", "budget", "model_input_fields",
                          "forbidden_model_inputs", "model_revision", "attention_implementation",
                          "runtime_policy")
        if any(config.get(key) != baseline.get(key) for key in invariant_keys):
            issues.append("PROMPT_REPAIR_INVARIANT_MISMATCH")
        if any(contract.get(f"{kind}_{suffix}") != baseline["contract"].get(f"{kind}_{suffix}")
               for kind in ("schema", "validator") for suffix in ("path", "sha256")):
            issues.append("PROMPT_REPAIR_FROZEN_CONTRACT_MISMATCH")
        if (contract.get("prompt_sha256") == baseline["contract"].get("prompt_sha256")
                or contract.get("prompt_path") == baseline["contract"].get("prompt_path")):
            issues.append("PROMPT_REPAIR_PROMPT_NOT_VERSIONED")
    elif (local.get("config_sha256") != expected_model.get("smoke_config_sha256")
          or local.get("prompt_sha256") != expected_model.get("prompt_sha256")):
        issues.append("PRERUN_GATE_BINDING_MISMATCH")
    model = local.get("model", {})
    revision = config.get("model_revision")
    if (model.get("snapshot_revision") != revision
            or model_receipt["remote_metadata"]["revision"] != revision
            or model.get("generation_config_sha256") != expected_model.get("generation_config_sha256")):
        issues.append("MODEL_REVISION_OR_GENERATION_CONFIG_MISMATCH")
    verified_files = {f["name"]: f["local_sha256"] for f in model_receipt[
        "local_verification"]["files"]}
    for name, expected_hash in model.get("file_hashes", {}).items():
        if name not in verified_files or verified_files[name] != expected_hash:
            issues.append("MODEL_FILE_HASH_BINDING_MISMATCH")
    if not model.get("file_hashes"):
        issues.append("MODEL_FILE_HASH_BINDING_MISSING")

    preflight_path = run_dir / "token_preflight.json"
    preflight = read_json(preflight_path)
    articles = preflight.get("articles", [])
    if (len(articles) != 10 or [a.get("ordinal") for a in articles] != list(range(1, 11))
            or local["counts"].get("context_preflight_checked_articles") != 10):
        raise AuditError("TEN_ARTICLE_PREFLIGHT_INCOMPLETE")
    context_limit = preflight.get("context_limit_tokens")
    if context_limit != model.get("effective_context_limit_tokens"):
        issues.append("CONTEXT_CAP_MISMATCH")
    max_new = config["budget"]["max_new_tokens"]
    for a in articles:
        if (not isinstance(a.get("prompt_tokens"), int) or a["prompt_tokens"] <= 0
                or a.get("effective_context_limit_tokens") != context_limit
                or a.get("fits_with_output_budget") != (a["prompt_tokens"] + max_new <= context_limit)):
            issues.append("PREFLIGHT_TOKEN_OR_FIT_MISMATCH")
    by_ordinal = {a["ordinal"]: a for a in articles}
    launch_events = [e for e in events if e.get("event") == "WORKER_LAUNCH_COMMITTED"]
    finished_events = [e for e in events if e.get("event") == "ATTEMPT_FINISHED"]
    response_read_events = [e for e in events if e.get("event") == "RESPONSE_READ_BACK"]
    if (len(launch_events) > 10
            or [e.get("attempt_count") for e in launch_events] != list(range(1, len(launch_events) + 1))
            or len({e.get("ordinal") for e in launch_events}) != len(launch_events)
            or any(e.get("ordinal") not in by_ordinal for e in launch_events)):
        issues.append("ATTEMPT_EVENT_BUDGET_OR_ORDER_MISMATCH")
    if any(e.get("ordinal") not in by_ordinal for e in launch_events):
        raise AuditError("ATTEMPT_EVENT_ORDINAL_INVALID")
    launched = {e.get("ordinal") for e in launch_events}
    if len(finished_events) > len(launch_events) or any(e.get("ordinal") not in launched for e in finished_events):
        issues.append("FINISHED_EVENT_MISMATCH")
    if local["counts"].get("model_calls_attempted") != len(launch_events):
        issues.append("ATTEMPT_COUNT_MISMATCH")
    statuses = local.get("article_statuses", [])
    if len(statuses) != 10 or sorted(s.get("ordinal") for s in statuses) != list(range(1, 11)):
        issues.append("ARTICLE_STATUS_COVERAGE_MISMATCH")
    status_by_ordinal = {s.get("ordinal"): s.get("status") for s in statuses}
    finished_statuses: dict[int, list[str]] = {}
    for event in finished_events:
        finished_statuses.setdefault(event.get("ordinal"), []).append(event.get("status"))
    status_counts = Counter(safe_code(str(s.get("status", "UNKNOWN"))) for s in statuses)
    if (local["counts"].get("input_too_long") != sum(not a["fits_with_output_budget"] for a in articles)
            or any(not by_ordinal[e["ordinal"]]["fits_with_output_budget"] for e in launch_events)):
        issues.append("INPUT_TOO_LONG_COUNT_OR_LAUNCH_MISMATCH")

    runner = load_module(RUNNER, "n1_runner_for_postrun_audit")
    validator = load_module(validator_path, "n1_validator_for_postrun_audit")
    source_manifest = read_json(SAMPLE_MANIFEST)
    rows = runner.first_ten_inputs(SAMPLE, SAMPLE_MANIFEST, config["source"]["sample_sha256"])
    by_row = {row["ordinal"]: row for row in rows}
    prompt_text = prompt_path.read_text(encoding="utf-8")
    phase_counts = Counter()
    response_count = json_count = schema_count = binding_count = mechanical_count = 0
    span_count = exact_span_count = 0
    stop_reasons = Counter()
    schema_keywords = Counter()
    schema_paths = Counter()
    mechanical_issues = Counter()
    response_hashes = []
    derived_response_statuses: dict[int, str] = {}
    private_attempts = []
    for e in launch_events:
        ordinal = e["ordinal"]
        request_path = run_dir / f"request_{ordinal:02d}.json"
        response_path = run_dir / f"response_{ordinal:02d}.json"
        phase_path = run_dir / f"phase_{ordinal:02d}.jsonl"
        request = read_json(request_path)
        expected_input = runner.build_article_input(by_row[ordinal], source_manifest)
        expected_messages = runner.render_messages(prompt_text, expected_input)
        if (request.get("messages") != expected_messages
                or request.get("prompt_tokens") != by_ordinal[ordinal]["prompt_tokens"]
                or request.get("max_new_tokens") != max_new
                or request.get("seed") != config["generation"]["seed"]
                or request.get("device") != config["device"]
                or request.get("dtype") != config["dtype"]
                or request.get("attention_implementation") != config["attention_implementation"]):
            issues.append("REQUEST_INPUT_OR_BUDGET_MISMATCH")
        phases = read_events(phase_path, tolerate_partial_tail=True) if phase_path.is_file() else []
        phase_names = [p.get("event") for p in phases]
        for name in ("WORKER_PROCESS_STARTED", "MODEL_LOADED", "GENERATION_STARTED", "RESPONSE_SAVED"):
            if phase_names.count(name) > 1:
                issues.append("DUPLICATE_WORKER_PHASE")
            phase_counts[name] += int(name in phase_names)
        item = {"ordinal": ordinal, "phases": phase_names, "request_sha256": sha256(request_path)}
        if response_path.is_file():
            raw = read_json(response_path)
            response_count += 1
            digest = sha256(response_path)
            response_hashes.append(f"{ordinal}:{digest}")
            item["response_sha256"] = digest
            ids = raw.get("generated_token_ids")
            if (not isinstance(ids, list) or any(not isinstance(x, int) for x in ids)
                    or raw.get("generated_tokens") != len(ids)
                    or raw.get("prompt_tokens") != by_ordinal[ordinal]["prompt_tokens"]
                    or raw.get("generation_config_source_sha256") != model.get("generation_config_sha256")):
                issues.append("RESPONSE_TOKEN_OR_SOURCE_BINDING_MISMATCH")
            else:
                eos = raw.get("tokenizer_special_ids", {}).get("effective_eos_token_id")
                if raw.get("stop_reason") != expected_stop_reason(ids, eos, max_new):
                    issues.append("RESPONSE_STOP_REASON_MISMATCH")
            effective = raw.get("effective_generation_config")
            if effective is not None:
                effective_bytes = json.dumps(effective, sort_keys=True, ensure_ascii=False,
                                             default=str).encode("utf-8")
                if hashlib.sha256(effective_bytes).hexdigest() != raw.get("effective_generation_config_sha256"):
                    issues.append("EFFECTIVE_GENERATION_CONFIG_HASH_MISMATCH")
            else:
                issues.append("EFFECTIVE_GENERATION_CONFIG_MISSING")
            stop_reasons[safe_code(str(raw.get("stop_reason", "UNKNOWN")))] += 1
            try:
                parsed_output = json.loads(raw["raw_text"])
                json_count += 1
            except (KeyError, TypeError, json.JSONDecodeError):
                parsed_output = None
            parsed_path = run_dir / f"parsed_{ordinal:02d}.json"
            if parsed_output is None:
                derived_response_statuses[ordinal] = "JSON_PARSE_ERROR"
                if parsed_path.is_file():
                    issues.append("PARSED_FILE_WITH_INVALID_JSON_RESPONSE")
            else:
                if not parsed_path.is_file():
                    issues.append("PARSED_FILE_MISSING_FOR_VALID_JSON")
                else:
                    parsed = read_json(parsed_path)
                    if parsed.get("output") != parsed_output:
                        issues.append("PARSED_OUTPUT_DIFFERS_FROM_RESPONSE")
                    check = validator.validate_output(parsed_output, expected_input["body"],
                                                      expected_input["input_binding"], schema_path)
                    derived_response_statuses[ordinal] = (
                        "SCHEMA_INVALID" if not check["schema_valid"] else
                        "MECHANICAL_INVALID" if not check["mechanical_valid"] else
                        "MECHANICAL_VALID")
                    if parsed.get("validation") != check:
                        issues.append("MECHANICAL_RECHECK_MISMATCH")
                    schema_count += int(check["schema_valid"])
                    binding_count += int(check["binding_valid"])
                    mechanical_count += int(check["mechanical_valid"])
                    span_count += check["evidence_span_count"] or 0
                    exact_span_count += check["evidence_span_exact_match_count"] or 0
                    for err in check["schema_error_codes"]:
                        schema_keywords[safe_code(str(err.get("keyword", "UNKNOWN")).upper())] += 1
                        schema_paths[safe_schema_path(str(err.get("path", "/")),
                                                      allowed_schema_paths)] += 1
                    for code in check["mechanical_issue_codes"]:
                        mechanical_issues[safe_code(str(code))] += 1
            item["stop_reason"] = safe_code(str(raw.get("stop_reason", "UNKNOWN")))
        private_attempts.append(item)

    issues.extend(status_consistency_issues(run_dir, status_by_ordinal, by_ordinal,
                                            launched, finished_statuses,
                                            derived_response_statuses, local["run_status"]))

    known_files = {f"{e['ordinal']:02d}" for e in launch_events}
    for prefix in ("request", "response", "phase", "parsed"):
        for path in run_dir.glob(f"{prefix}_*.json*"):
            suffix = path.name[len(prefix) + 1:len(prefix) + 3]
            if suffix not in known_files:
                issues.append("UNATTRIBUTED_PRIVATE_ATTEMPT_FILE")
    expected_counts = {
        "model_calls_completed": response_count,
        "json_parse_valid": json_count,
        "schema_valid": schema_count,
        "binding_valid": binding_count,
        "mechanical_valid": mechanical_count,
        "evidence_spans_checked": span_count,
        "evidence_spans_exact": exact_span_count,
        "worker_process_started": phase_counts["WORKER_PROCESS_STARTED"],
        "model_load_completed": phase_counts["MODEL_LOADED"],
        "generation_started": phase_counts["GENERATION_STARTED"],
    }
    for key, value in expected_counts.items():
        if local["counts"].get(key) != value:
            issues.append("RUNNER_AND_PRIVATE_RESULT_COUNT_MISMATCH")
    if len(response_read_events) != response_count:
        issues.append("RESPONSE_READ_BACK_EVENT_MISMATCH")
    if any(e.get("ordinal") not in launched for e in response_read_events):
        issues.append("RESPONSE_EVENT_ORDINAL_MISMATCH")

    attempted = len(launch_events)
    counts = {"articles_considered": 10, "context_preflight_checked_articles": len(articles),
              "input_too_long": sum(not a["fits_with_output_budget"] for a in articles),
              "worker_launch_committed": attempted,
              "worker_process_started": phase_counts["WORKER_PROCESS_STARTED"],
              "model_load_completed": phase_counts["MODEL_LOADED"],
              "generation_started": phase_counts["GENERATION_STARTED"],
              "response_files_read_back": response_count,
              "attempt_finished_events": len(finished_events),
              "json_parse_valid": json_count, "schema_valid": schema_count,
              "binding_valid": binding_count, "mechanical_valid": mechanical_count,
              "evidence_spans_checked": span_count, "evidence_spans_exact": exact_span_count}
    conditional = {"json_parse_valid": response_count, "schema_valid": json_count,
                   "binding_valid": schema_count, "mechanical_valid": schema_count}
    rates = {name: {"conditional_numerator": counts[name], "conditional_denominator": denominator,
                    "conditional_rate": rate(counts[name], denominator),
                    "attempted_numerator": counts[name], "attempted_denominator": attempted,
                    "attempted_rate": rate(counts[name], attempted)}
             for name, denominator in conditional.items()}
    issues = sorted(set(issues))
    public = {
        "schema": "news_first_inference_aggregate_v1",
        "date": "2026-11-14", "timezone": "Asia/Shanghai",
        "audit_status": "PASS" if not issues else "FAIL",
        "audit_issue_codes": issues,
        "run_status": safe_code(str(local["run_status"])),
        "run_started_at_utc": local["run_started_at_utc"],
        "run_finished_at_utc": local["run_finished_at_utc"],
        "model_repo_id": config["model_preference"],
        "model_revision": revision,
        "development_set": "fixed first ten source-order articles used previously for field design",
        "sample_sha256": bindings["input_sample_sha256"],
        "sample_manifest_sha256": bindings["input_manifest_sha256"],
        "config_sha256": bindings["config_sha256"],
        "runner_sha256": bindings["runner_sha256"],
        "schema_sha256": bindings["schema_sha256"],
        "prompt_sha256": bindings["prompt_sha256"],
        "validator_sha256": bindings["validator_sha256"],
        "model_integrity_receipt_sha256": sha256(MODEL_RECEIPT),
        "environment_receipt_sha256": sha256(ENVIRONMENT_RECEIPT),
        "generation_config_source_sha256": model.get("generation_config_sha256"),
        "effective_generation_config_sha256": local.get("effective_generation_config_sha256"),
        "attention_implementation": config["attention_implementation"],
        "budgets": config["budget"],
        "counts": counts,
        "status_counts": dict(sorted(status_counts.items())),
        "stop_reason_counts": dict(sorted(stop_reasons.items())),
        "mechanical_rates": rates,
        "schema_error_keyword_counts": dict(sorted(schema_keywords.items())),
        "schema_error_path_counts": dict(sorted(schema_paths.items())),
        "mechanical_issue_code_counts": dict(sorted(mechanical_issues.items())),
        "response_set_sha256": hashlib.sha256("\n".join(response_hashes).encode("ascii")).hexdigest(),
        "private_receipt_hashes": {"run_events_sha256": sha256(run_dir / "run_events.jsonl"),
                                   "local_manifest_sha256": sha256(run_dir / "local_manifest.json"),
                                   "token_preflight_sha256": sha256(preflight_path),
                                   "shareable_summary_sha256": sha256(run_dir / "shareable_summary.json")},
        "limits": ["First ten development articles only; no independent human gold labels.",
                   "JSON/schema/span checks are mechanical, not extraction accuracy.",
                   "No historical financial admission, prediction or return evaluation."],
    }
    private = {"schema": "news_first_inference_private_postrun_audit_v1",
               "audit_status": public["audit_status"], "audit_issue_codes": issues,
               "attempts": private_attempts,
               "private_run_dir": str(run_dir)}
    return public, private


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        print("OUTPUT_ALREADY_EXISTS", file=sys.stderr)
        return 2
    try:
        public, private = audit(args.run_dir, args.config)
    except AuditError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(public, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.run_dir / "postrun_audit_private.json").write_text(
        json.dumps(private, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"audit_status": public["audit_status"], "run_status": public["run_status"],
                      "counts": public["counts"], "audit_issue_codes": public["audit_issue_codes"]},
                     ensure_ascii=False))
    return 0 if public["audit_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
