"""Publish count-only receipts after the private shortfacts v2 review is frozen.

The v1 original score uses only already-public v1 receipts. Corrected v1 and
new v2 scores use the same private v3 development reference, but only allowlisted
counts and status flags leave artifacts/. This script does no model inference.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
OLD_PUBLIC = ROOT / "results/news_shortfacts_20261115"
OLD_PRIVATE = ROOT / "artifacts/news_shortfacts_20261115"
NEW_PRIVATE = ROOT / "artifacts/news_shortfacts_v2_20261115"
NEW_PUBLIC = ROOT / "results/news_shortfacts_v2_20261115"
TASK_IDS = ("A1", "A2", "B1", "B2", "C1", "C2")
FIELDS = ("entity", "metric", "value_text", "unit_text", "period_text", "basis_text")
STATES_V2 = ("CORRECT", "WRONG", "UNKNOWN")
STATUSES = ("SUPPORTED", "PARTIAL", "UNSUPPORTED", "UNRESOLVED")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path.name}")
    return value


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        raise FileNotFoundError(path)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"Expected JSONL objects: {path.name}")
    return rows


def require_under(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"Path outside allowed {root.name} area")
    return resolved


def task_map(rows: list[dict]) -> dict[str, dict]:
    ids = [row.get("task_id") for row in rows]
    if ids != list(TASK_IDS):
        raise ValueError("Task rows must cover six frozen IDs in order")
    return dict(zip(ids, rows))


def safe_enum(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z0-9_]+", value):
        raise ValueError("Unexpected status or stop reason")
    return value


def nonnegative_int(value: Any) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError("Expected nonnegative integer")
    return value


def nonnegative_number(value: Any) -> float | None:
    if value is None:
        return None
    if type(value) not in (int, float) or value < 0:
        raise ValueError("Expected nonnegative duration or memory measurement")
    return float(value)


def safe_counts(source: dict, names: tuple[str, ...]) -> dict[str, int]:
    return {name: nonnegative_int(source[name]) for name in names}


COMMON_COUNTS = ("planned_tasks", "attempted_tasks", "generation_started_tasks",
                 "completed_response_tasks", "reference_facts")
CORRECTED_COUNTS = COMMON_COUNTS + (
    "strict_raw_json_valid_tasks", "fence_adapted_tasks",
    "strict_raw_schema_valid_tasks", "fence_adapted_schema_valid_tasks",
    "schema_valid_after_optional_adapter_tasks", "empty_fact_response_tasks",
    "model_facts", "valid_evidence_id_facts", "quote_contiguous_facts",
    "semantic_supported_facts", "semantic_partial_facts", "semantic_unsupported_facts",
    "semantic_unresolved_facts", "matched_reference_facts", "matched_with_field_error_facts",
    "fully_correct_facts", "unmatched_reference_facts", "unresolved_output_facts",
    "duplicate_output_facts", "unmatched_output_facts")


def public_v1_original(summary: dict, receipt: dict) -> dict:
    """Keep historical v1 reference/contract and its published score separate."""
    counts = summary["counts"]
    by_receipt = task_map(receipt["tasks"])
    task_rows = task_map(summary["tasks"])
    if (summary["manifest_sha256"] != receipt["input_manifest_sha256"] or
            counts["planned_tasks"] != receipt["planned_tasks"] or
            counts["completed_response_tasks"] != receipt["completed_responses"]):
        raise ValueError("Historical public summary and run receipt disagree")
    rows = []
    for task_id in TASK_IDS:
        task, run = task_rows[task_id], by_receipt[task_id]
        if task["attempt_status"] != run["status"]:
            raise ValueError(f"Historical task status mismatch: {task_id}")
        rows.append({
            "task_id": task_id, "attempt_status": safe_enum(task["attempt_status"]),
            "generation_started": bool(task["generation_started"]),
            "response_completed": bool(task["response_completed"]),
            "strict_raw_json_valid": bool(task["strict_raw_json_valid"]),
            "fence_adapted_schema_valid": bool(task["fenced_json_view_valid"] and task["schema_valid"]),
            "schema_valid_after_adapter": bool(task["schema_valid"]),
            "schema_valid_model_facts": nonnegative_int(task["model_fact_count"]),
            "quote_contiguous_facts": nonnegative_int(task["quote_present_count"]),
            "reference_facts": None, "matched_reference_facts": None,
            "fully_correct_facts": None, "omitted_reference_facts": None,
            "duplicate_output_facts": None, "unresolved_output_facts": None,
            "field_status_counts": None,
            "input_tokens": nonnegative_int(run.get("input_tokens")),
            "generated_tokens": nonnegative_int(run.get("generated_tokens")),
            "attempt_duration_seconds": nonnegative_number(run.get("attempt_duration_seconds")),
            "generation_seconds_synchronized": nonnegative_number(run.get("generation_seconds_synchronized")),
            "gpu_memory": None,
        })
    fields = {field: {state: nonnegative_int(value)
                      for state, value in summary["field_status_counts_codex_review"][field].items()}
              for field in FIELDS}
    return {
        "label": "V1_ORIGINAL_PUBLISHED_SCORE_REFERENCE_V1",
        "input_manifest_sha256": summary["manifest_sha256"],
        "reference_sha256": summary["reference_sha256"],
        "counts": {
            **safe_counts(counts, COMMON_COUNTS),
            "strict_raw_json_valid_tasks": nonnegative_int(counts["strict_json_valid_tasks"]),
            "fence_adapted_schema_valid_tasks": nonnegative_int(counts["fenced_view_schema_valid_tasks"]),
            "schema_valid_after_optional_adapter_tasks": nonnegative_int(counts["schema_valid_tasks"]),
            "model_facts": nonnegative_int(counts["model_facts_in_schema_valid_responses"]),
            "quote_contiguous_facts": nonnegative_int(counts["quote_present_facts"]),
            "semantic_supported_facts": nonnegative_int(counts["semantic_supported_facts"]),
            "matched_reference_facts": nonnegative_int(counts["reference_facts_matched_after_review"]),
            "fully_correct_facts": nonnegative_int(counts["all_fields_correct_facts"]),
            "unmatched_reference_facts": nonnegative_int(
                counts["reference_facts"] - counts["reference_facts_matched_after_review"]),
            "duplicate_output_facts": nonnegative_int(summary["error_labels"].get("DUPLICATE", 0)),
            "unresolved_output_facts": None,
        },
        "field_status_counts": fields,
        "error_labels": {key: nonnegative_int(value) for key, value in summary["error_labels"].items()},
        "tasks": rows,
        "provenance": {"public_summary_sha256": None, "public_run_receipt_sha256": None},
        "note": "Historical v1 score uses its original reference and contract. Per-task semantic counts were not published and remain null.",
    }


def selected_gpu_memory(metadata: dict | None) -> dict | None:
    if metadata is None or not isinstance(metadata.get("gpu_memory"), dict):
        return None
    stages = {}
    for stage in ("before_load", "after_load", "after_generation"):
        entry = metadata["gpu_memory"].get(stage)
        stages[stage] = (None if not isinstance(entry, dict) else
                         {key: nonnegative_int(entry.get(key)) for key in
                          ("free_bytes", "total_bytes", "allocated_bytes", "peak_allocated_bytes")})
    return stages


def selected_attempt(attempt: dict | None, metadata: dict | None = None) -> dict:
    if attempt is None:
        return {"input_tokens": None, "generated_tokens": None,
                "attempt_duration_seconds": None, "generation_seconds_synchronized": None,
                "stop_reason": None, "gpu_memory": None,
                "actual_dtype": None, "actual_device": None,
                "attention_requested": None, "model_config_attention": None,
                "actual_sdpa_kernel": None}
    execution = {}
    allowed = {
        "actual_dtype": {"torch.float16"},
        "actual_device": {"cuda:0"},
        "attention_requested": {"sdpa"},
        "model_config_attention": {"sdpa"},
        "actual_sdpa_kernel": {"UNKNOWN"},
    }
    for key, choices in allowed.items():
        value = metadata.get(key) if metadata else None
        if value is not None and value not in choices:
            raise ValueError(f"Unexpected observed execution setting: {key}")
        execution[key] = value
    return {
        "input_tokens": nonnegative_int(attempt.get("input_tokens")),
        "generated_tokens": nonnegative_int(attempt.get("generated_tokens")),
        "attempt_duration_seconds": nonnegative_number(attempt.get("duration_seconds")),
        "generation_seconds_synchronized": nonnegative_number(
            metadata.get("generation_seconds_synchronized") if metadata else None),
        "stop_reason": safe_enum(attempt.get("stop_reason")),
        "gpu_memory": selected_gpu_memory(metadata),
        **execution,
    }


def corrected_round(evaluation_dir: Path, attempts: dict[str, dict],
                    metadata: dict[str, dict] | None, label: str) -> dict:
    mechanical_path = evaluation_dir / "mechanical_validation.json"
    review_path = evaluation_dir / "field_review.json"
    summary_path = evaluation_dir / "error_summary.json"
    mechanical, review, summary = (read_json(mechanical_path), read_json(review_path),
                                   read_json(summary_path))
    if (mechanical.get("schema") != "shortfacts_mechanical_validation_v2" or
            review.get("schema") != "shortfacts_field_review_v2" or
            summary.get("schema") != "shortfacts_error_summary_v2" or
            mechanical["reference_sha256"] != review["reference_sha256"]):
        raise ValueError("Corrected evaluation files disagree on schema/reference")
    mech = task_map(mechanical["tasks"])
    reviews = task_map(review["tasks"])
    counts = safe_counts(summary["counts"], CORRECTED_COUNTS)
    rows = []
    aggregate_fields = {field: {state: 0 for state in STATES_V2} for field in FIELDS}
    for task_id in TASK_IDS:
        m, r = mech[task_id], reviews[task_id]
        if m["schema_valid"] and m["facts"] and r["review_status"] != "CODEX_REVIEW_COMPLETE":
            raise ValueError(f"Semantic review not complete: {task_id}")
        field_counts = {field: {state: 0 for state in STATES_V2} for field in FIELDS}
        support = {state: 0 for state in STATUSES}
        for fact in r["facts"]:
            if fact.get("reviewer") != "CODEX_REVIEW" or set(fact.get("field_checks", {})) != set(FIELDS):
                raise ValueError(f"Missing reviewed fields: {task_id}")
            for field in FIELDS:
                state = fact["field_checks"][field]
                if state not in STATES_V2:
                    raise ValueError(f"Invalid field state: {task_id}/{field}")
                field_counts[field][state] += 1
                aggregate_fields[field][state] += 1
            semantic = fact["semantic_support"]
            if semantic not in STATUSES:
                raise ValueError("Invalid semantic support status")
            support[semantic] += 1
        matched = sum(fact.get("matched_one_to_one") is True for fact in r["facts"])
        full = sum(fact.get("fully_correct") is True for fact in r["facts"])
        duplicate = sum(fact.get("duplicate") is True for fact in r["facts"])
        unresolved = sum(fact.get("semantic_support") == "UNRESOLVED" or
                         "UNKNOWN" in fact.get("field_checks", {}).values() for fact in r["facts"])
        omitted = len(r["unmatched_reference_indices"])
        if matched + omitted != r["reference_count"] or full > matched:
            raise ValueError(f"One-to-one reference accounting mismatch: {task_id}")
        if len(m["facts"]) != len(r["facts"]):
            raise ValueError(f"Mechanical/review fact count mismatch: {task_id}")
        row = {
            "task_id": task_id, "attempt_status": safe_enum(m["attempt_status"]),
            "generation_started": bool(m["generation_started"]),
            "response_completed": bool(m["response_completed"]),
            "strict_raw_json_valid": m["parse_kind"] == "RAW_JSON",
            "fence_adapted_schema_valid": m["parse_kind"] == "FENCE_ADAPTED" and bool(m["schema_valid"]),
            "schema_valid_after_adapter": bool(m["schema_valid"]),
            "schema_valid_model_facts": len(m["facts"]),
            "valid_evidence_id_facts": sum(f["evidence_id_valid"] is True for f in m["facts"]),
            "quote_contiguous_facts": sum(f["quote_contiguous"] is True for f in m["facts"]),
            "semantic_support_counts": support,
            "reference_facts": nonnegative_int(r["reference_count"]),
            "matched_reference_facts": matched,
            "fully_correct_facts": full,
            "omitted_reference_facts": omitted,
            "duplicate_output_facts": duplicate,
            "unresolved_output_facts": unresolved,
            "field_status_counts": field_counts,
            **selected_attempt(attempts.get(task_id), (metadata or {}).get(task_id)),
        }
        rows.append(row)
    for field in FIELDS:
        observed = summary["fields"][field]
        if any(aggregate_fields[field][state] != observed[state] for state in STATES_V2):
            raise ValueError(f"Field totals disagree with semantic review: {field}")
    if (sum(row["reference_facts"] for row in rows) != counts["reference_facts"] or
            sum(row["matched_reference_facts"] for row in rows) != counts["matched_reference_facts"] or
            sum(row["fully_correct_facts"] for row in rows) != counts["fully_correct_facts"] or
            sum(row["omitted_reference_facts"] for row in rows) != counts["unmatched_reference_facts"] or
            sum(row["schema_valid_model_facts"] for row in rows) != counts["model_facts"]):
        raise ValueError("Corrected task totals disagree with error summary")
    return {
        "label": label,
        "input_manifest_sha256": mechanical["manifest_sha256"],
        "reference_sha256": mechanical["reference_sha256"],
        "schema_sha256": mechanical.get("schema_sha256"),
        "counts": counts,
        "field_status_counts": {
            field: {**aggregate_fields[field], "denominator": sum(aggregate_fields[field].values())}
            for field in FIELDS},
        "error_labels": {key: nonnegative_int(value) for key, value in summary["error_labels"].items()},
        "tasks": rows,
        "provenance": {"mechanical_validation_sha256": sha256(mechanical_path),
                       "field_review_sha256": sha256(review_path),
                       "error_summary_sha256": sha256(summary_path),
                       "attempt_ledger_sha256": mechanical["attempt_ledger_sha256"],
                       "review_decisions_sha256": review.get("decisions_sha256")},
    }


def load_new_attempts(run_dir: Path) -> tuple[dict[str, dict], dict[str, dict]]:
    rows = read_jsonl(run_dir / "attempt_ledger.jsonl")
    ids = [row.get("task_id") for row in rows]
    if len(ids) > 6 or ids != list(TASK_IDS[:len(ids)]):
        raise ValueError("New attempt ledger is not a fixed ordered prefix")
    metadata = {}
    for task_id in ids:
        path = run_dir / "worker_metadata" / f"{task_id}.json"
        if path.is_file():
            metadata[task_id] = read_json(path)
    return dict(zip(ids, rows)), metadata


def run_receipt(run_dir: Path, new_round: dict, attempts: dict[str, dict],
                metadata: dict[str, dict]) -> dict:
    config_path, summary_path, ledger_path = (run_dir / "run_config.json",
                                              run_dir / "run_summary.json",
                                              run_dir / "attempt_ledger.jsonl")
    config, summary = read_json(config_path), read_json(summary_path)
    if (config.get("schema") != "news_shortfacts_run_config_v2" or
            summary.get("schema") != "news_shortfacts_run_summary_v2" or
            config["input_manifest_sha256"] != new_round["input_manifest_sha256"] or
            config["private_reference_sha256"] != new_round["reference_sha256"] or
            sha256(ledger_path) != new_round["provenance"]["attempt_ledger_sha256"] or
            summary["attempted"] != len(attempts)):
        raise ValueError("New run and corrected score have different frozen inputs or attempts")
    model = config["model"]
    gpu = config.get("gpu_at_preflight", {})
    rows = []
    score_tasks = task_map(new_round["tasks"])
    for task_id in TASK_IDS:
        attempt = attempts.get(task_id)
        score = score_tasks[task_id]
        if score["response_completed"] != bool(attempt and attempt.get("response_completed")):
            raise ValueError(f"New score/attempt completion mismatch: {task_id}")
        rows.append({"task_id": task_id,
                     "attempt_status": safe_enum(attempt.get("status")) if attempt else "NOT_ATTEMPTED",
                     "generation_started": bool(attempt and attempt.get("generation_started")),
                     "response_completed": bool(attempt and attempt.get("response_completed")),
                     "raw_response_sha256": attempt.get("raw_response_sha256") if attempt else None,
                     "worker_exit_code": attempt.get("worker_exit_code") if attempt else None,
                     "worker_exit_confirmed": attempt.get("worker_exit_confirmed") if attempt else None,
                     "gpu_process_absent_after_timeout": attempt.get("gpu_process_absent_after_timeout") if attempt else None,
                     **selected_attempt(attempt, metadata.get(task_id))})
    return {
        "schema": "shortfacts_v2_run_receipt_redacted_v1", "time_zone": "Asia/Shanghai",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "development_only": True, "model_repo_id": model["repo_id"],
        "model_revision": model["revision"],
        "input_manifest_sha256": config["input_manifest_sha256"],
        "prompt_sha256": config["prompt_sha256"],
        "schema_sha256": config["schema_sha256"],
        "private_reference_sha256": config["private_reference_sha256"],
        "runner_sha256": config["runner_sha256"],
        "evaluator_sha256": config["evaluator_sha256"],
        "run_config_sha256": sha256(config_path),
        "run_summary_sha256": sha256(summary_path),
        "attempt_ledger_sha256": sha256(ledger_path),
        "generation": config["generation"],
        "gpu_at_preflight": {"cuda_device_name": gpu.get("cuda_device_name"),
                             "free_bytes": nonnegative_int(gpu.get("free_bytes")),
                             "total_bytes": nonnegative_int(gpu.get("total_bytes"))},
        "planned_tasks": nonnegative_int(summary["planned_tasks"]),
        "attempted": nonnegative_int(summary["attempted"]),
        "generation_started": nonnegative_int(summary["generation_started"]),
        "completed_responses": nonnegative_int(summary["completed_responses"]),
        "run_status": safe_enum(summary["run_status"]),
        "run_started_at_utc": summary["started_at_utc"],
        "run_finished_at_utc": summary["finished_at_utc"],
        "tasks": rows,
        "raw_passages_responses_references_published": False,
        "financial_evaluation": "NOT_RUN",
    }


def comparison(original: dict, retro: dict, new: dict) -> dict:
    if (original["input_manifest_sha256"] != retro["input_manifest_sha256"] or
            retro["input_manifest_sha256"] != new["input_manifest_sha256"] or
            retro["reference_sha256"] != new["reference_sha256"] or
            retro["schema_sha256"] != new["schema_sha256"]):
        raise ValueError("Comparison does not share frozen input and corrected reference/schema")
    old_tasks, new_tasks = task_map(retro["tasks"]), task_map(new["tasks"])
    per_task = []
    for task_id in TASK_IDS:
        old, current = old_tasks[task_id], new_tasks[task_id]
        keys = ("attempt_status", "generation_started", "response_completed",
                "strict_raw_json_valid", "fence_adapted_schema_valid",
                "schema_valid_after_adapter", "schema_valid_model_facts",
                "valid_evidence_id_facts", "quote_contiguous_facts", "semantic_support_counts",
                "reference_facts", "matched_reference_facts", "fully_correct_facts",
                "omitted_reference_facts", "duplicate_output_facts", "unresolved_output_facts",
                "field_status_counts", "input_tokens", "generated_tokens",
                "attempt_duration_seconds", "generation_seconds_synchronized", "gpu_memory",
                "actual_dtype", "actual_device", "attention_requested",
                "model_config_attention", "actual_sdpa_kernel")
        per_task.append({"task_id": task_id,
                         "old_response_corrected_rule": {key: old.get(key) for key in keys},
                         "new_response_corrected_rule": {key: current.get(key) for key in keys}})
    delta_names = ("strict_raw_json_valid_tasks", "fence_adapted_schema_valid_tasks",
                   "schema_valid_after_optional_adapter_tasks", "model_facts",
                   "fully_correct_facts", "unmatched_reference_facts",
                   "duplicate_output_facts", "unresolved_output_facts")
    delta = {name: new["counts"][name] - retro["counts"][name] for name in delta_names}
    return {
        "schema": "shortfacts_v2_comparison_redacted_v1", "time_zone": "Asia/Shanghai",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "development_material": "SAME_SIX_REUSED_PASSAGES_NOT_BLIND_TEST",
        "evaluation_status": "CODEX_REFERENCE_DRAFT_AND_SAME_EXECUTOR_REVIEW",
        "intervention": "COMBINED_SCHEMA_PROMPT_REFERENCE_REPAIR_NOT_SINGLE_PROMPT_CAUSAL_EFFECT",
        "historical_v1_original_score": original,
        "old_responses_corrected_rule": retro,
        "new_responses_corrected_rule": new,
        "same_rule_per_task": per_task,
        "new_minus_old_same_rule_counts": delta,
        "financial_evaluation": "NOT_RUN",
    }


def reject_private_paths(obj: dict) -> None:
    encoded = json.dumps(obj, ensure_ascii=False, allow_nan=False)
    if (re.search(r"[A-Za-z]:[\\/]", encoded) or
            any(fragment in encoded for fragment in ("artifacts/", "raw_responses/", "/Users/", "/home/"))):
        raise ValueError("Public receipt contains a private or absolute path")


def write_new(path: Path, value: dict) -> None:
    reject_private_paths(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v1-public-summary", type=Path,
                        default=OLD_PUBLIC / "public_validation_summary_v2.json")
    parser.add_argument("--v1-public-run-receipt", type=Path,
                        default=OLD_PUBLIC / "run_receipt_redacted.json")
    parser.add_argument("--retro-eval-dir", type=Path,
                        default=NEW_PRIVATE / "retrospective")
    parser.add_argument("--new-run-dir", type=Path, required=True)
    parser.add_argument("--new-eval-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=NEW_PUBLIC)
    args = parser.parse_args()
    old_summary_path = require_under(args.v1_public_summary, OLD_PUBLIC)
    old_receipt_path = require_under(args.v1_public_run_receipt, OLD_PUBLIC)
    retro_dir = require_under(args.retro_eval_dir, NEW_PRIVATE)
    run_dir = require_under(args.new_run_dir, NEW_PRIVATE)
    new_eval_dir = require_under(args.new_eval_dir, NEW_PRIVATE)
    output_dir = require_under(args.output_dir, NEW_PUBLIC)
    receipt_path, comparison_path = (output_dir / "run_receipt_redacted.json",
                                     output_dir / "comparison_redacted.json")
    if receipt_path.exists() or comparison_path.exists():
        raise FileExistsError("Public receipt already exists; use a new versioned output directory")
    original = public_v1_original(read_json(old_summary_path), read_json(old_receipt_path))
    original["provenance"] = {"public_summary_sha256": sha256(old_summary_path),
                              "public_run_receipt_sha256": sha256(old_receipt_path)}
    old_public_run = read_json(old_receipt_path)
    old_attempts = {}
    for task in old_public_run["tasks"]:
        old_attempts[task["task_id"]] = {
            "input_tokens": task.get("input_tokens"),
            "generated_tokens": task.get("generated_tokens"),
            "duration_seconds": task.get("attempt_duration_seconds"),
            "stop_reason": task.get("stop_reason"),
        }
    retro = corrected_round(retro_dir, old_attempts, None, "V1_RESPONSES_RETROSPECTIVE_CORRECTED_V3")
    new_attempts, metadata = load_new_attempts(run_dir)
    new = corrected_round(new_eval_dir, new_attempts, metadata, "V2_RESPONSES_CORRECTED_V3")
    receipt = run_receipt(run_dir, new, new_attempts, metadata)
    compare = comparison(original, retro, new)
    reject_private_paths(receipt)
    reject_private_paths(compare)
    write_new(receipt_path, receipt)
    write_new(comparison_path, compare)
    print(json.dumps({"status": "PUBLIC_RECEIPTS_WRITTEN", "attempted": receipt["attempted"],
                      "completed_responses": receipt["completed_responses"],
                      "fully_correct_facts": new["counts"]["fully_correct_facts"],
                      "unmatched_reference_facts": new["counts"]["unmatched_reference_facts"]}))


if __name__ == "__main__":
    main()
