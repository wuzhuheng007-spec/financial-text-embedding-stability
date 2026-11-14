"""Compare fixed-position v4 and v5 development runs without quality claims.

The wire and host-quote stages use the same contract, but the targeted prompt
and abnormal-exit policy differ. Only aggregate transitions and hashes are
public; row-level pairings remain in ignored artifacts.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
import sys


REPO = Path(__file__).resolve().parents[1]
OLD_RUN = REPO / "artifacts/news_grounding_runtime_20261114/qwen3b_wire_v2_sdpa_run1"
NEW_RUN = REPO / "artifacts/news_targeted_repair_20261115/qwen3b_prompt_v4_targeted_run1"
OLD_AUDIT = REPO / "results/news_grounding_runtime_20261114/inference_aggregate.json"
NEW_AUDIT = REPO / "results/news_targeted_repair_20261115/inference_aggregate.json"
DEFAULT_OUTPUT = REPO / "results/news_targeted_repair_20261115/paired_comparison.json"

spec = importlib.util.spec_from_file_location(
    "targeted_auditor_for_comparison", REPO / "scripts/summarize_news_n1_targeted.py")
assert spec is not None and spec.loader is not None
audit_helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_helpers)


class GroundingComparisonError(RuntimeError):
    """A stable, public-safe failed comparison gate."""


def read_json(path: Path) -> dict:
    if not path.is_file():
        raise GroundingComparisonError("REQUIRED_FILE_MISSING")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise GroundingComparisonError("REQUIRED_OBJECT_INVALID")
    return value


def sha256(path: Path) -> str:
    return audit_helpers.sha256(path)


def read_finished(run_dir: Path, audit_path: Path) -> tuple[dict, dict, list[dict]]:
    if not run_dir.resolve().is_relative_to((REPO / "artifacts").resolve()):
        raise GroundingComparisonError("RUN_OUTSIDE_IGNORED_ARTIFACTS")
    events = audit_helpers.read_jsonl(run_dir / "run_events.jsonl")
    if (not events or events[-1].get("event") != "RUN_FINISHED"
            or sum(event.get("event") == "RUN_FINISHED" for event in events) != 1):
        raise GroundingComparisonError("RUN_NOT_FINISHED")
    local = read_json(run_dir / "local_manifest.json")
    audit = read_json(audit_path)
    hashes = audit.get("private_receipt_hashes", {})
    launches = [item for item in events if item.get("event") == "WORKER_LAUNCH_COMMITTED"]
    if (audit.get("audit_status") != "PASS"
            or audit.get("run_status") != local.get("run_status")
            or not local.get("run_finished_at_utc")
            or audit.get("response_set_sha256")
            != audit_helpers.response_set_sha256(run_dir, launches)
            or hashes.get("local_manifest_sha256") != sha256(run_dir / "local_manifest.json")
            or hashes.get("run_events_sha256") != sha256(run_dir / "run_events.jsonl")
            or hashes.get("token_preflight_sha256") != sha256(run_dir / "token_preflight.json")
            or hashes.get("shareable_summary_sha256")
            != sha256(run_dir / "shareable_summary.json")):
        raise GroundingComparisonError("AUDIT_OR_PRIVATE_RUN_BINDING_MISMATCH")
    return local, audit, events


def statuses(local: dict) -> dict[int, str]:
    rows = local.get("article_statuses", [])
    if len(rows) != 10 or sorted(row.get("ordinal") for row in rows) != list(range(1, 11)):
        raise GroundingComparisonError("TEN_STATUS_COVERAGE_MISMATCH")
    return {row["ordinal"]: row["status"] for row in rows}


def number_summary(values: list[int]) -> dict:
    return {"count": len(values), "minimum": min(values) if values else None,
            "median": statistics.median(values) if values else None,
            "maximum": max(values) if values else None,
            "mean": statistics.mean(values) if values else None}


def compare(old_run: Path, new_run: Path, old_audit_path: Path,
            new_audit_path: Path) -> tuple[dict, dict]:
    old_local, old_audit, _ = read_finished(old_run, old_audit_path)
    new_local, new_audit, _ = read_finished(new_run, new_audit_path)
    if (old_audit.get("pipeline_version") != "wire_v2_exact_quote_host_binding_v1"
            or new_audit.get("pipeline_version") != "wire_v2_exact_quote_targeted_prompt_v1"
            or old_audit.get("sample_sha256") != new_audit.get("sample_sha256")
            or old_audit.get("sample_manifest_sha256")
            != new_audit.get("sample_manifest_sha256")
            or old_audit.get("model_revision") != new_audit.get("model_revision")
            or old_audit.get("contract_sha256", {}).get("wire_schema")
            != new_audit.get("contract_sha256", {}).get("wire_schema")
            or old_audit.get("contract_sha256", {}).get("schema")
            != new_audit.get("contract_sha256", {}).get("schema")
            or old_audit.get("contract_sha256", {}).get("adapter")
            != new_audit.get("contract_sha256", {}).get("adapter")
            or old_audit.get("contract_sha256", {}).get("validator")
            != new_audit.get("contract_sha256", {}).get("validator")):
        raise GroundingComparisonError("SOURCE_MODEL_OR_CONTRACT_CHANGED")
    old_finished = datetime.fromisoformat(old_local["run_finished_at_utc"])
    new_started = datetime.fromisoformat(new_local["run_started_at_utc"])
    new_finished = datetime.fromisoformat(new_local["run_finished_at_utc"])
    if not old_finished < new_started < new_finished:
        raise GroundingComparisonError("RUN_WINDOW_ORDER_MISMATCH")
    old_status, new_status = statuses(old_local), statuses(new_local)
    old_preflight = read_json(old_run / "token_preflight.json")["articles"]
    new_preflight = read_json(new_run / "token_preflight.json")["articles"]
    if ([row.get("ordinal") for row in old_preflight] != list(range(1, 11))
            or [row.get("ordinal") for row in new_preflight] != list(range(1, 11))):
        raise GroundingComparisonError("PREFLIGHT_SOURCE_ORDER_MISMATCH")
    delta = [new_preflight[i]["prompt_tokens"] - old_preflight[i]["prompt_tokens"]
             for i in range(10)]
    transitions = Counter()
    private_rows = []
    completed = audit_helpers.ATTEMPT_RESPONSE_STATUSES
    both_completed = 0
    paired_old_wire_valid = 0
    paired_new_wire_valid = 0
    paired_old_canonical_constructible = 0
    paired_new_canonical_constructible = 0
    for ordinal in range(1, 11):
        before, after = old_status[ordinal], new_status[ordinal]
        transitions[f"{before}->{after}"] += 1
        old_response = old_run / f"response_{ordinal:02d}.json"
        new_response = new_run / f"response_{ordinal:02d}.json"
        old_done = before in completed
        new_done = after in completed
        if old_done != old_response.is_file():
            raise GroundingComparisonError("OLD_RESPONSE_STATUS_MISMATCH")
        if new_done and not new_response.is_file():
            raise GroundingComparisonError("NEW_RESPONSE_STATUS_MISMATCH")
        if new_done:
            response = read_json(new_response)
            response_time = datetime.fromisoformat(response["finished_at_utc"])
            if not new_started <= response_time <= new_finished:
                raise GroundingComparisonError("NEW_RESPONSE_OUTSIDE_RUN_WINDOW")
        if old_response.is_file() and new_response.is_file() and (
                sha256(new_response) == sha256(old_response)):
            raise GroundingComparisonError("OLD_RESPONSE_FILE_REUSED")
        if old_done and new_done:
            both_completed += 1
            def wire_valid(run: Path) -> bool:
                path = run / f"wire_{ordinal:02d}.json"
                return path.is_file() and read_json(path).get(
                    "validation", {}).get("wire_schema_valid") is True
            paired_old_wire_valid += int(wire_valid(old_run))
            paired_new_wire_valid += int(wire_valid(new_run))
            paired_old_canonical_constructible += int(
                (old_run / f"canonical_{ordinal:02d}.json").is_file())
            paired_new_canonical_constructible += int(
                (new_run / f"canonical_{ordinal:02d}.json").is_file())
        private_rows.append({"ordinal": ordinal, "old_status": before,
                             "new_status": after,
                             "old_response_sha256": sha256(old_response)
                             if old_response.is_file() else None,
                             "new_response_sha256": sha256(new_response)
                             if new_response.is_file() else None,
                             "new_response_counted_complete": new_done})
    old_counts, new_counts = old_audit["counts"], new_audit["counts"]
    def pipeline_counts(audit: dict, audit_path: Path) -> dict:
        counts = audit["counts"]
        return {"pipeline_version": audit["pipeline_version"],
                "config_sha256": audit["config_sha256"],
                "audit_sha256": sha256(audit_path),
                "run_status": audit["run_status"],
                "attempted": counts["model_calls_attempted"],
                "generation_started": counts["generation_started"],
                "completed_responses": counts["model_calls_completed"],
                "json_valid": counts["raw_json_valid"],
                "wire_schema_valid_completed": counts["wire_schema_valid"],
                "quotes_checked": counts["quotes_checked"],
                "quotes_unique_exact": counts["quotes_unique_exact"],
                "canonical_constructible": counts["canonical_constructible"],
                "canonical_mechanical_valid": counts["canonical_mechanical_valid"],
                "attention_implementation": "sdpa",
                "actual_sdpa_kernel": audit["actual_sdpa_kernel"]}
    public = {
        "schema": "news_n1_targeted_pair_v1",
        "date": "2026-11-15", "timezone": "Asia/Shanghai",
        "comparison_status": "PASS_BOUND_DEVELOPMENT_PAIR",
        "fixed_target_articles": 10,
        "shared_sample_sha256": old_audit["sample_sha256"],
        "shared_model_revision": old_audit["model_revision"],
        "shared_contract_sha256": {
            k: old_audit["contract_sha256"][k]
            for k in ("schema", "wire_schema", "adapter", "validator")},
        "old_pipeline": pipeline_counts(old_audit, old_audit_path),
        "new_pipeline": pipeline_counts(new_audit, new_audit_path),
        "status_transition_counts": dict(sorted(transitions.items())),
        "paired_completed_response_positions": both_completed,
        "paired_stage_counts": {
            "denominator_completed_in_both_runs": both_completed,
            "old_wire_valid": paired_old_wire_valid,
            "new_wire_valid": paired_new_wire_valid,
            "old_canonical_constructible": paired_old_canonical_constructible,
            "new_canonical_constructible": paired_new_canonical_constructible,
            "interpretation": "Mechanical stages on reused development articles, not event quality."},
        "prompt_token_new_minus_old": number_summary(delta),
        "changed_components": ["targeted_prompt", "abnormal_worker_exit_policy"],
        "metric_comparability": {
            "wire_schema_and_host_quote_contract": "SAME_FROZEN_CONTRACT",
            "event_semantic_quality": "NOT_EVALUATED_NO_HUMAN_GOLD",
            "financial_predictive_value": "NOT_RUN"},
        "limitations": [
            "The first ten articles were reused for development; no blind holdout or human event-quality adjudication.",
            "The targeted prompt and abnormal-worker-exit policy changed together.",
            "Status transitions include failures and unattempted positions; paired stage counts use only completed responses in both runs.",
            "Host quote matching tests exact string presence, not whether it supports a company event or impact direction.",
            "GPU observations do not establish the causal effect of SDPA or exact selected kernel.",
            "No financial labels, predictions, or return evaluation.",
        ],
    }
    private = {"schema": "news_n1_targeted_private_pair_v1",
               "rows": private_rows,
               "old_private_manifest_sha256": sha256(old_run / "local_manifest.json"),
               "new_private_manifest_sha256": sha256(new_run / "local_manifest.json")}
    public["private_pair_sha256"] = hashlib.sha256(
        (json.dumps(private, ensure_ascii=False, indent=2) + "\n").encode("utf-8")).hexdigest()
    return public, private


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-run", type=Path, default=OLD_RUN)
    parser.add_argument("--new-run", type=Path, default=NEW_RUN)
    parser.add_argument("--old-audit", type=Path, default=OLD_AUDIT)
    parser.add_argument("--new-audit", type=Path, default=NEW_AUDIT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    public, private = compare(args.old_run, args.new_run,
                              args.old_audit, args.new_audit)
    encoded_public = (json.dumps(public, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    encoded_private = (json.dumps(private, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    private_path = args.new_run / "postrun_private_pair.json"
    if ((args.output.exists() and args.output.read_bytes() != encoded_public)
            or (private_path.exists() and private_path.read_bytes() != encoded_private)):
        raise GroundingComparisonError("EXISTING_COMPARISON_OUTPUT_DIFFERS")
    if not args.output.exists():
        args.output.write_bytes(encoded_public)
    if not private_path.exists():
        private_path.write_bytes(encoded_private)
    print(json.dumps({"comparison_status": public["comparison_status"],
                      "paired_completed_response_positions": public[
                          "paired_completed_response_positions"],
                      "status_transition_counts": public["status_transition_counts"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
