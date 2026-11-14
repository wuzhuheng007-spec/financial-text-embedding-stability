"""Synthetic-only checks for the wire-v2 run auditor."""

from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "summarize_news_grounding_runtime", ROOT / "scripts/summarize_news_grounding_runtime.py")
assert spec is not None and spec.loader is not None
auditor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auditor)


def test_zero_quotes_has_null_rate_and_is_not_grounding_success():
    counts = {"model_calls_attempted": 1, "model_calls_completed": 1,
              "raw_json_valid": 1, "wire_schema_valid": 1,
              "host_body_hash_valid": 1, "canonical_constructible": 1,
              "canonical_schema_valid": 1, "canonical_mechanical_valid": 1,
              "quotes_checked": 0, "quotes_unique_exact": 0}
    rates = auditor.metric_rates(counts)
    assert rates["quote_unique_exact"]["quote_denominator"] == 0
    assert rates["quote_unique_exact"]["quote_rate"] is None
    assert rates["quote_unique_exact"]["zero_quotes_is_success"] is False
    assert rates["canonical_mechanical_valid"]["fixed_target_rate"] == .1


def test_response_stage_keeps_wire_grounding_and_canonical_separate():
    f = auditor.derived_response_status
    assert f(False, None, None, None) == "JSON_PARSE_ERROR"
    assert f(True, False, None, None) == "WIRE_SCHEMA_INVALID"
    assert f(True, True, {"host_body_hash_valid": False}, None) == "HOST_BINDING_ERROR"
    assert f(True, True, {"host_body_hash_valid": True,
                        "grounding_status": "UNRESOLVED_QUOTES"}, None) == "QUOTE_UNRESOLVED"
    assert f(True, True, {"host_body_hash_valid": True,
                        "canonical": {}}, {"mechanical_valid": False}) == "CANONICAL_INVALID"
    assert f(True, True, {"host_body_hash_valid": True,
                        "canonical": {}}, {"mechanical_valid": True}) == "CANONICAL_VALID"


def test_response_set_digest_changes_when_raw_wire_changes(tmp_path: Path):
    path = tmp_path / "response_01.json"
    path.write_text(json.dumps({"raw_text": "first"}), encoding="utf-8")
    launch = [{"ordinal": 1}]
    before = auditor.response_set_sha256(tmp_path, launch)
    path.write_text(json.dumps({"raw_text": "second"}), encoding="utf-8")
    assert auditor.response_set_sha256(tmp_path, launch) != before


def test_memory_summary_reports_observed_values_without_kernel_claim():
    events = {1: [{"event": "CUDA_PEAK_RESET", "stage": "BEFORE_MODEL_LOAD"},
                  {"event": "CUDA_MEMORY", "stage": "AFTER_MODEL_LOAD",
                   "free_bytes": 99, "allocated_bytes": 12},
                  {"event": "CUDA_MEMORY", "stage": "BEFORE_GENERATION",
                   "free_bytes": 80}]}
    out = auditor.memory_summary(events)
    assert out["snapshot_count"] == 2
    assert out["by_stage"]["AFTER_MODEL_LOAD"]["bytes"]["free_bytes"]["minimum"] == 99
    assert out["by_stage"]["BEFORE_GENERATION"]["bytes"]["free_bytes"]["maximum"] == 80
    assert out["peak_reset_stage_counts"] == {"BEFORE_MODEL_LOAD": 1}
    assert out["actual_sdpa_kernel"] == "UNKNOWN"


def test_ordinal_statuses_reject_fabricated_success_and_unattempted(tmp_path: Path):
    (tmp_path / "response_01.json").write_text("{}", encoding="utf-8")
    statuses = [{"ordinal": 1, "status": "CANONICAL_VALID"}]
    statuses += [{"ordinal": i, "status": "NOT_ATTEMPTED_AFTER_OOM_STOP"}
                 for i in range(2, 11)]
    local = {"article_statuses": statuses, "run_status": "OOM_STOP"}
    preflight = [{"ordinal": i, "fits_with_output_budget": True}
                 for i in range(1, 11)]
    launches = [{"ordinal": 1}]
    finished = [{"ordinal": 1, "status": "CANONICAL_VALID"}]
    assert auditor.ordinal_status_issues(tmp_path, local, preflight,
                                         launches, finished, {1: "CANONICAL_VALID"}) == []
    assert "ARTICLE_STATUS_RESPONSE_MISMATCH" in auditor.ordinal_status_issues(
        tmp_path, local, preflight, launches, finished, {1: "CANONICAL_INVALID"})
    statuses[5]["status"] = "CANONICAL_VALID"
    assert "UNATTEMPTED_STATUS_MISMATCH" in auditor.ordinal_status_issues(
        tmp_path, local, preflight, launches, finished, {1: "CANONICAL_VALID"})


def synthetic_audit_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fixture_spec = importlib.util.spec_from_file_location(
        "grounding_runner_synthetic_fixture_for_audit",
        ROOT / "tests/test_news_n1_grounding_runner.py")
    assert fixture_spec is not None and fixture_spec.loader is not None
    fixture = importlib.util.module_from_spec(fixture_spec)
    fixture_spec.loader.exec_module(fixture)
    fixture.test_synthetic_worker_stages_are_saved_without_real_model_calls(
        tmp_path, monkeypatch)

    fake_repo = tmp_path / "repo"
    fake_config = fake_repo / "configs/news_n1_smoke_v4_grounded.json"
    fake_config.parent.mkdir(parents=True)
    fake_config.write_bytes((ROOT / "configs/news_n1_smoke_v4_grounded.json").read_bytes())
    fake_model_receipt = tmp_path / "synthetic_model_receipt.json"
    fake_model_receipt.write_text(json.dumps({
        "remote_metadata": {"revision": fixture.runner.PINNED_MODEL_REVISION},
        "local_verification": {"files": [
            {"name": "generation_config.json", "local_sha256": "a" * 64}]},
    }), encoding="utf-8")
    monkeypatch.setattr(auditor, "REPO", fake_repo)
    monkeypatch.setattr(auditor, "RUNNER", ROOT / "scripts/run_news_n1_grounding.py")
    monkeypatch.setattr(auditor, "SAMPLE", tmp_path / "synthetic_sample.jsonl")
    monkeypatch.setattr(auditor, "SAMPLE_MANIFEST", tmp_path / "synthetic_manifest.json")
    monkeypatch.setattr(auditor, "MODEL_RECEIPT", fake_model_receipt)
    original_import = auditor.import_source
    def import_for_test(path: Path, name: str):
        if path == auditor.RUNNER:
            return fixture.runner
        return original_import(path, name)
    monkeypatch.setattr(auditor, "import_source", import_for_test)
    original_sha = auditor.sha256
    def sha_for_test(path: Path) -> str:
        if path == auditor.SAMPLE:
            return fixture.runner.PINNED_SAMPLE_SHA256
        if path == auditor.SAMPLE_MANIFEST:
            return fixture.runner.PINNED_SAMPLE_MANIFEST_SHA256
        return original_sha(path)
    monkeypatch.setattr(auditor, "sha256", sha_for_test)

    return fake_repo / "artifacts/synthetic_run", fake_config


def test_full_synthetic_runner_artifacts_reaudit_without_model_call(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Replay the runner's stub-worker stages with independent contract checks."""
    run_dir, fake_config = synthetic_audit_context(tmp_path, monkeypatch)
    public, private = auditor.audit(run_dir, fake_config)
    assert public["audit_status"] == "PASS", public["audit_issue_codes"]
    assert public["run_status"] == "OOM_STOP"
    assert public["counts"]["model_calls_attempted"] == 4
    assert public["counts"]["model_calls_completed"] == 2
    assert public["counts"]["wire_schema_valid"] == 1
    assert public["counts"]["quotes_not_found"] == 1
    assert public["counts"]["canonical_mechanical_valid"] == 0
    assert public["counts"]["no_spans_requested"] == 0
    assert public["status_counts"]["INTERRUPTED_PROCESS_UNCLASSIFIED"] == 1
    assert public["recovery"]["original_runner_source_sha256"] == auditor.sha256(
        run_dir.parent / "original_v4_runner_df563dea6878.py")
    assert public["full_text_token_preflight"]["target_articles"] == 10
    assert public["full_text_token_preflight"]["fit_with_output_reserve"] == 10
    assert public["full_text_token_preflight"]["reserved_output_tokens"] == 1024
    assert len(private["attempts"]) == 4


def test_frozen_generation_contract_rejects_self_consistent_tamper(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    run_dir, fake_config = synthetic_audit_context(tmp_path, monkeypatch)
    response_path = run_dir / "response_03.json"
    response = json.loads(response_path.read_text(encoding="utf-8"))
    response["effective_generation_config"]["do_sample"] = True
    digest = hashlib.sha256(json.dumps(response["effective_generation_config"],
                                       sort_keys=True, ensure_ascii=False,
                                       default=str).encode("utf-8")).hexdigest()
    response["effective_generation_config_sha256"] = digest
    response_path.write_text(json.dumps(response), encoding="utf-8")
    for name in ("local_manifest.json", "shareable_summary.json"):
        path = run_dir / name
        data = json.loads(path.read_text(encoding="utf-8"))
        data["effective_generation_config_sha256"] = digest
        path.write_text(json.dumps(data), encoding="utf-8")
    public, _ = auditor.audit(run_dir, fake_config)
    assert public["audit_status"] == "FAIL"
    assert "EFFECTIVE_GENERATION_CONFIG_MISMATCH" in public["audit_issue_codes"]


def test_interrupted_recovery_binds_original_bytes_and_unclassified_attempt(
        tmp_path: Path):
    original_runner_source = tmp_path.parent / "original_v4_runner_df563dea6878.py"
    original_runner_source.write_text("# saved original source\n", encoding="utf-8")
    original_runner_sha = auditor.sha256(original_runner_source)
    def save(name: str, value):
        path = tmp_path / name
        path.write_text(json.dumps(value) + "\n", encoding="utf-8")
        return path
    prior_events = [
        {"event": "RUN_STARTED"}, {"event": "TOKEN_PREFLIGHT_FINISHED"},
        {"event": "WORKER_LAUNCH_COMMITTED", "ordinal": 1},
        {"event": "RESPONSE_READ_BACK", "ordinal": 1},
        {"event": "ATTEMPT_FINISHED", "ordinal": 1,
         "status": "WIRE_SCHEMA_INVALID"},
        {"event": "WORKER_LAUNCH_COMMITTED", "ordinal": 2},
    ]
    prior_local = {"run_status": "PREFLIGHT", "run_finished_at_utc": None,
                   "run_started_at_utc": "2026-11-14T08:00:00+00:00",
                   "runner_sha256": original_runner_sha,
                   "config_sha256": "c" * 64,
                   "input_sample_sha256": "d" * 64,
                   "input_manifest_sha256": "e" * 64,
                   "counts": {"model_calls_attempted": 2,
                              "model_calls_completed": 1},
                   "article_statuses": [{"ordinal": 1,
                                         "status": "WIRE_SCHEMA_INVALID"}]}
    prior_shareable = {"run_status": "PREFLIGHT"}
    snapshots = {
        "local_manifest": save("recovery_prestate_local_manifest.json", prior_local),
        "shareable_summary": save("recovery_prestate_shareable_summary.json",
                                  prior_shareable),
    }
    snapshots["run_events"] = tmp_path / "recovery_prestate_run_events.jsonl"
    event_line = lambda e: json.dumps(e, sort_keys=True) + "\n"
    snapshots["run_events"].write_text("".join(map(event_line, prior_events)),
                                       encoding="utf-8")
    for name in ("phase_02.jsonl", "request_01.json", "request_02.json",
                 "response_01.json"):
        (tmp_path / name).write_text("{}\n", encoding="utf-8")
    recovery = {
        "version": "external_interruption_resume_v1",
        "original_runner_sha256": original_runner_sha,
        "original_runner_source_sha256": original_runner_sha,
        "recovery_runner_sha256": "b" * 64,
        "interrupted_ordinal": 2,
        "interrupted_status": "INTERRUPTED_PROCESS_UNCLASSIFIED",
        "interruption_cause": "UNKNOWN",
        "worker_exit_observed_by_original_parent": False,
        "original_wall_budget_seconds": 1800,
        "wall_seconds_elapsed_at_recovery": 100.0,
        "prestate_sha256": {key: auditor.sha256(path)
                            for key, path in snapshots.items()},
        **{f"{name}_sha256": auditor.sha256(tmp_path / f"{name}.json")
           for name in ("request_01", "request_02", "response_01")},
        "phase_02_sha256": auditor.sha256(tmp_path / "phase_02.jsonl"),
    }
    resumed = [
        {"event": "RUN_RECOVERY_STARTED"},
        {"event": "ATTEMPT_FINISHED", "ordinal": 2,
         "status": "INTERRUPTED_PROCESS_UNCLASSIFIED"},
        {"event": "RUN_FINISHED"},
    ]
    events = prior_events + resumed
    event_path = tmp_path / "run_events.jsonl"
    event_path.write_text("".join(map(event_line, events)), encoding="utf-8")
    local = {**prior_local, "run_status": "TOTAL_TIMEOUT",
             "run_finished_at_utc": "2026-11-14T08:30:00+00:00",
             "runner_sha256": "b" * 64, "recovery": recovery}
    shareable = {"recovery": recovery}
    assert auditor.verify_recovery(tmp_path, events, local, shareable,
                                   "b" * 64) == recovery
    event_path.write_text(event_path.read_text(encoding="utf-8").replace(
        "RUN_STARTED", "RUN_MUTATED", 1), encoding="utf-8")
    with pytest.raises(auditor.GroundingAuditError,
                       match="RECOVERY_PRESTATE_CONTENT_MISMATCH"):
        auditor.verify_recovery(tmp_path, events, local, shareable, "b" * 64)
    event_path.write_text("".join(map(event_line, events)), encoding="utf-8")
    original_runner_source.write_text("# changed source\n", encoding="utf-8")
    with pytest.raises(auditor.GroundingAuditError,
                       match="RECOVERY_ORIGINAL_RUNNER_SOURCE_MISMATCH"):
        auditor.verify_recovery(tmp_path, events, local, shareable, "b" * 64)


def test_existing_different_public_audit_is_not_overwritten(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    out = tmp_path / "aggregate.json"
    out.write_text("previous result", encoding="utf-8")
    run = tmp_path / "run"
    run.mkdir()
    monkeypatch.setattr(auditor, "audit", lambda *_: ({"audit_status": "PASS"}, {}))
    monkeypatch.setattr(sys, "argv", ["auditor", "--run-dir", str(run),
                                      "--config", str(tmp_path / "config.json"),
                                      "--output", str(out)])
    with pytest.raises(auditor.GroundingAuditError,
                       match="EXISTING_AUDIT_OUTPUT_DIFFERS"):
        auditor.main()
    assert out.read_text(encoding="utf-8") == "previous result"
