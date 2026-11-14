"""Synthetic-only checks for fixed-position v4/v5 development comparison."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "targeted_comparison_test", ROOT / "scripts/compare_news_n1_targeted.py")
assert spec is not None and spec.loader is not None
comparison = importlib.util.module_from_spec(spec)
spec.loader.exec_module(comparison)


def fixture_pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    old = tmp_path / "old"
    new = tmp_path / "new"
    for path in (old, new):
        path.mkdir()
        (path / "local_manifest.json").write_text("{}", encoding="utf-8")
    old_status = ["WIRE_SCHEMA_INVALID"] + ["NOT_ATTEMPTED"] * 9
    new_status = ["WIRE_SCHEMA_INVALID", "JSON_PARSE_ERROR"] + ["NOT_ATTEMPTED"] * 8
    old_local = {
        "run_started_at_utc": "2026-11-14T00:00:00+00:00",
        "run_finished_at_utc": "2026-11-14T01:00:00+00:00",
        "article_statuses": [{"ordinal": i, "status": s}
                             for i, s in enumerate(old_status, 1)],
    }
    new_local = {
        "run_started_at_utc": "2026-11-15T00:00:00+00:00",
        "run_finished_at_utc": "2026-11-15T01:00:00+00:00",
        "article_statuses": [{"ordinal": i, "status": s}
                             for i, s in enumerate(new_status, 1)],
    }
    contracts = {key: key * 4 for key in ("schema", "wire_schema", "adapter", "validator")}
    base_audit = {
        "sample_sha256": "sample", "sample_manifest_sha256": "manifest",
        "model_revision": "revision", "contract_sha256": contracts,
        "actual_sdpa_kernel": "UNKNOWN", "config_sha256": "config",
        "counts": {"model_calls_attempted": 2, "generation_started": 2,
                   "model_calls_completed": 2, "raw_json_valid": 1,
                   "wire_schema_valid": 1, "quotes_checked": 0,
                   "quotes_unique_exact": 0, "canonical_constructible": 0,
                   "canonical_mechanical_valid": 0},
    }
    old_audit = base_audit | {
        "pipeline_version": "wire_v2_exact_quote_host_binding_v1",
        "run_status": "GROUNDED_RUN_FINISHED_WITH_INTERRUPTION"}
    new_audit = base_audit | {
        "pipeline_version": "wire_v2_exact_quote_targeted_prompt_v1",
        "run_status": "TARGETED_RUN_FINISHED"}
    monkeypatch.setattr(comparison, "read_finished", lambda run, _audit: (
        (old_local, old_audit, []) if run == old else (new_local, new_audit, [])))
    for run, tokens in ((old, 100), (new, 105)):
        (run / "token_preflight.json").write_text(json.dumps({
            "articles": [{"ordinal": i, "prompt_tokens": tokens}
                         for i in range(1, 11)]}), encoding="utf-8")
    (old / "response_01.json").write_text(json.dumps({
        "finished_at_utc": "2026-11-14T00:10:00+00:00", "raw_text": "old"}),
        encoding="utf-8")
    for i in (1, 2):
        (new / f"response_{i:02d}.json").write_text(json.dumps({
            "finished_at_utc": f"2026-11-15T00:{i:02d}:00+00:00",
            "raw_text": f"new{i}"}), encoding="utf-8")
    (old / "wire_01.json").write_text(json.dumps({
        "validation": {"wire_schema_valid": False}}), encoding="utf-8")
    (new / "wire_01.json").write_text(json.dumps({
        "validation": {"wire_schema_valid": True}}), encoding="utf-8")
    (new / "canonical_01.json").write_text("{}", encoding="utf-8")
    old_audit_file = tmp_path / "old_audit.json"
    new_audit_file = tmp_path / "new_audit.json"
    old_audit_file.write_text("{}", encoding="utf-8")
    new_audit_file.write_text("{}", encoding="utf-8")
    return old, new, old_audit_file, new_audit_file, new_audit


def test_paired_denominator_uses_completed_in_both_runs(tmp_path, monkeypatch):
    old, new, old_audit, new_audit, _ = fixture_pair(tmp_path, monkeypatch)
    public, private = comparison.compare(old, new, old_audit, new_audit)
    assert public["fixed_target_articles"] == 10
    assert public["paired_completed_response_positions"] == 1
    assert public["paired_stage_counts"]["new_wire_valid"] == 1
    assert public["paired_stage_counts"]["new_canonical_constructible"] == 1
    assert public["status_transition_counts"]["NOT_ATTEMPTED->JSON_PARSE_ERROR"] == 1
    assert public["prompt_token_new_minus_old"]["mean"] == 5
    assert len(private["rows"]) == 10
    assert "rows" not in public


def test_changed_sample_binding_rejected(tmp_path, monkeypatch):
    old, new, old_audit, new_audit, audit = fixture_pair(tmp_path, monkeypatch)
    audit["sample_sha256"] = "different"
    with pytest.raises(comparison.GroundingComparisonError,
                       match="SOURCE_MODEL_OR_CONTRACT_CHANGED"):
        comparison.compare(old, new, old_audit, new_audit)
