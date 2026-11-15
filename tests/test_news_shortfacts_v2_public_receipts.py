"""Privacy and accounting checks for count-only shortfacts receipts."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/news_shortfacts_v2/public_receipts.py"
spec = importlib.util.spec_from_file_location("shortfacts_v2_receipts", SCRIPT)
assert spec and spec.loader
receipts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receipts)


def test_private_paths_are_rejected_before_public_write(tmp_path: Path) -> None:
    for value in ("C:\\Users\\ROG\\private.txt", "E:/private/model", "artifacts/raw_responses/A1.txt"):
        with pytest.raises(ValueError, match="private or absolute path"):
            receipts.write_new(tmp_path / "public.json", {"unsafe": value})
        assert not (tmp_path / "public.json").exists()


def test_gpu_memory_allowlist_discards_raw_metadata() -> None:
    private = {"gpu_memory": {"before_load": {"free_bytes": 20, "total_bytes": 40,
                                                   "allocated_bytes": 0, "peak_allocated_bytes": 0,
                                                   "raw_prompt": "PRIVATE SOURCE TEXT"}},
               "raw_response_path": "C:\\private\\response.txt"}
    public = receipts.selected_gpu_memory(private)
    assert public["before_load"]["free_bytes"] == 20
    assert "PRIVATE SOURCE TEXT" not in json.dumps(public)
    assert "raw_response_path" not in json.dumps(public)


def test_observed_execution_fields_are_bounded() -> None:
    attempt = {"input_tokens": 1, "generated_tokens": 1, "duration_seconds": 1.0,
               "stop_reason": "EOS"}
    metadata = {"actual_dtype": "torch.float16", "actual_device": "cuda:0",
                "attention_requested": "sdpa", "model_config_attention": "sdpa",
                "actual_sdpa_kernel": "UNKNOWN"}
    selected = receipts.selected_attempt(attempt, metadata)
    assert selected["actual_dtype"] == "torch.float16"
    assert selected["actual_sdpa_kernel"] == "UNKNOWN"
    with pytest.raises(ValueError, match="Unexpected observed execution setting"):
        receipts.selected_attempt(attempt, {**metadata, "actual_device": "PRIVATE SOURCE TEXT"})


def test_corrected_round_strips_private_fact_text_and_checks_totals(tmp_path: Path) -> None:
    eval_dir = tmp_path / "evaluation"
    eval_dir.mkdir()
    counts = {name: 0 for name in receipts.CORRECTED_COUNTS}
    counts.update({"planned_tasks": 6, "attempted_tasks": 1, "generation_started_tasks": 1,
                   "completed_response_tasks": 1, "reference_facts": 6,
                   "strict_raw_json_valid_tasks": 1, "strict_raw_schema_valid_tasks": 1,
                   "schema_valid_after_optional_adapter_tasks": 1,
                   "model_facts": 1, "valid_evidence_id_facts": 1, "quote_contiguous_facts": 1,
                   "semantic_supported_facts": 1, "matched_reference_facts": 1,
                   "fully_correct_facts": 1, "unmatched_reference_facts": 5})
    mechanical, reviews = [], []
    for task_id in receipts.TASK_IDS:
        active = task_id == "A1"
        mechanical.append({"task_id": task_id,
                           "attempt_status": "RESPONSE_COMPLETED" if active else "NOT_ATTEMPTED",
                           "generation_started": active, "response_completed": active,
                           "parse_kind": "RAW_JSON" if active else "NO_RESPONSE",
                           "schema_valid": active,
                           "raw_response_path": "C:\\private\\response.txt",
                           "facts": ([{"evidence_id_valid": True, "quote_contiguous": True,
                                       "model_fact": "PRIVATE SOURCE TEXT"}] if active else [])})
        reviews.append({"task_id": task_id, "reference_count": 1,
                        "review_status": "CODEX_REVIEW_COMPLETE" if active else "NO_SCHEMA_VALID_RESPONSE",
                        "unmatched_reference_indices": [] if active else [0],
                        "facts": ([{"reviewer": "CODEX_REVIEW", "matched_one_to_one": True,
                                    "fully_correct": True, "duplicate": False,
                                    "semantic_support": "SUPPORTED",
                                    "field_checks": {field: "CORRECT" for field in receipts.FIELDS},
                                    "raw_value": "PRIVATE SOURCE TEXT"}] if active else [])})
    objects = {
        "mechanical_validation.json": {"schema": "shortfacts_mechanical_validation_v2",
                                       "manifest_sha256": "a" * 64, "reference_sha256": "b" * 64,
                                       "schema_sha256": "c" * 64, "attempt_ledger_sha256": "d" * 64,
                                       "tasks": mechanical},
        "field_review.json": {"schema": "shortfacts_field_review_v2",
                              "reference_sha256": "b" * 64, "decisions_sha256": "e" * 64,
                              "tasks": reviews},
        "error_summary.json": {"schema": "shortfacts_error_summary_v2", "counts": counts,
                               "fields": {field: {"CORRECT": 1, "WRONG": 0, "UNKNOWN": 0}
                                          for field in receipts.FIELDS},
                               "error_labels": {"MISSING_FACT": 5}},
    }
    for filename, obj in objects.items():
        (eval_dir / filename).write_text(json.dumps(obj), encoding="utf-8")
    result = receipts.corrected_round(eval_dir, {}, None, "TEST")
    assert result["counts"]["fully_correct_facts"] == 1
    assert result["tasks"][0]["omitted_reference_facts"] == 0
    assert result["tasks"][1]["omitted_reference_facts"] == 1
    serialized = json.dumps(result)
    assert "PRIVATE SOURCE TEXT" not in serialized
    assert "C:\\private" not in serialized


def test_comparison_requires_identical_corrected_reference_and_schema() -> None:
    old = {"input_manifest_sha256": "a", "reference_sha256": "b", "schema_sha256": "c"}
    new = {"input_manifest_sha256": "a", "reference_sha256": "other", "schema_sha256": "c"}
    with pytest.raises(ValueError, match="does not share"):
        receipts.comparison(old, old, new)
