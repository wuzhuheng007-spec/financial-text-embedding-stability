"""Synthetic-only checks for the independent v5 targeted-run auditor."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from jsonschema import ValidationError


ROOT = Path(__file__).resolve().parents[1]


def import_file(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


auditor = import_file(ROOT / "scripts/summarize_news_n1_targeted.py", "targeted_auditor_test")


def synthetic_native_stop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fixture = import_file(ROOT / "tests/test_news_n1_targeted_runner.py", "targeted_fixture_test")
    fixture.test_native_worker_exit_stops_without_retry_or_later_launch(tmp_path, monkeypatch)
    fake_repo = tmp_path / "repo"
    fake_config = fake_repo / "configs/news_n1_smoke_v5_targeted.json"
    fake_config.parent.mkdir(parents=True)
    fake_config.write_bytes((ROOT / "configs/news_n1_smoke_v5_targeted.json").read_bytes())
    fake_receipt = tmp_path / "synthetic_model_receipt.json"
    fake_receipt.write_text(json.dumps({
        "remote_metadata": {"revision": fixture.runner.PINNED_MODEL_REVISION},
        "local_verification": {"files": [
            {"name": name, "local_sha256": digest}
            for name, digest in fixture.runner.PINNED_MODEL_FILE_HASHES.items()]},
    }), encoding="utf-8")
    run_dir = fake_repo / fixture.runner.TARGET_OUTPUT_RELATIVE
    generation_digest = fixture.runner.PINNED_MODEL_FILE_HASHES["generation_config.json"]
    local_path = run_dir / "local_manifest.json"
    local = json.loads(local_path.read_text(encoding="utf-8"))
    local["model"]["generation_config_sha256"] = generation_digest
    local_path.write_text(json.dumps(local), encoding="utf-8")
    public_path = run_dir / "shareable_summary.json"
    public = json.loads(public_path.read_text(encoding="utf-8"))
    public["model_generation_config_source_sha256"] = generation_digest
    public_path.write_text(json.dumps(public), encoding="utf-8")
    monkeypatch.setattr(auditor, "REPO", fake_repo)
    monkeypatch.setattr(auditor, "RUNNER", ROOT / "scripts/run_news_n1_targeted.py")
    sample = tmp_path / "sample.jsonl"
    manifest = tmp_path / "manifest.json"
    monkeypatch.setattr(auditor, "SAMPLE", sample)
    monkeypatch.setattr(auditor, "SAMPLE_MANIFEST", manifest)
    monkeypatch.setattr(auditor, "MODEL_RECEIPT", fake_receipt)
    original_import = auditor.import_source

    def import_for_test(path: Path, name: str):
        if path == auditor.RUNNER:
            return fixture.runner
        return original_import(path, name)

    monkeypatch.setattr(auditor, "import_source", import_for_test)
    original_sha = auditor.sha256

    def sha_for_test(path: Path) -> str:
        if path == sample:
            return fixture.runner.PINNED_SAMPLE_SHA256
        if path == manifest:
            return fixture.runner.PINNED_SAMPLE_MANIFEST_SHA256
        return original_sha(path)

    monkeypatch.setattr(auditor, "sha256", sha_for_test)
    return run_dir, fake_config


def test_full_native_stop_artifacts_reaudit_without_model_call(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    run_dir, config = synthetic_native_stop(tmp_path, monkeypatch)
    public, private = auditor.audit(run_dir, config)
    assert public["audit_status"] == "PASS", public["audit_issue_codes"]
    assert public["run_status"] == "NATIVE_PROCESS_CRASH_STOP"
    assert public["counts"]["model_calls_attempted"] == 1
    assert public["counts"]["generation_started"] == 0
    assert public["counts"]["model_calls_completed"] == 0
    assert public["counts"]["model_call_failures"] == 1
    assert public["counts"]["orphan_response_files"] == 0
    assert public["status_counts"]["NOT_ATTEMPTED_AFTER_NATIVE_PROCESS_CRASH_STOP"] == 9
    assert len(private["attempts"]) == 1


def test_orphan_response_is_retained_without_counting_completion(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    run_dir, config = synthetic_native_stop(tmp_path, monkeypatch)
    (run_dir / "response_01.json").write_text('{"partial":true}', encoding="utf-8")
    public, private = auditor.audit(run_dir, config)
    assert public["audit_status"] == "PASS", public["audit_issue_codes"]
    assert public["counts"]["model_calls_completed"] == 0
    assert public["counts"]["orphan_response_files"] == 1
    assert private["attempts"][0]["orphan_response_sha256"] == hashlib.sha256(
        (run_dir / "response_01.json").read_bytes()).hexdigest()


def test_zero_quote_denominator_is_null():
    counts = {"model_calls_attempted": 1, "model_calls_completed": 0,
              "generation_started": 0,
              "raw_json_valid": 0, "wire_schema_valid": 0,
              "host_body_hash_valid": 0, "canonical_constructible": 0,
              "canonical_schema_valid": 0, "canonical_mechanical_valid": 0,
              "quotes_checked": 0, "quotes_unique_exact": 0}
    assert auditor.metric_rates(counts)["quote_unique_exact"]["quote_rate"] is None
    assert auditor.metric_rates(counts)["quote_unique_exact"]["quote_status"] == "NOT_EVALUATED"
    assert auditor.metric_rates(counts)["canonical_mechanical_valid"][
        "conditional_rate"] is None
    assert auditor.metric_rates(counts)["model_calls_completed"][
        "conditional_status"] == "NOT_EVALUATED"


def test_error_path_aggregation_omits_row_index_and_invalid_value():
    error = ValidationError("private invalid value", validator="enum",
                            path=["records", 7, "impact_direction"])
    assert auditor.schema_error_field_key(error) == "RECORDS.[].IMPACT_DIRECTION:ENUM"

