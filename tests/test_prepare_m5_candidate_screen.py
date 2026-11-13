"""SYNTHETIC_TEST_ONLY checks for the M5 preparation candidate screen."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import prepare_m5_candidate_screen as screen  # noqa: E402


def _issue(number: int, starting: bool = True) -> dict:
    return {
        "issue_evidence_id": f"ISSUE:{number}",
        "security_record_id": f"ISSUE:{number}",
        "permno": str(number),
        "permco": str(number),
        "security_interval": {"date_semantics": "VALID_TIME", "start": "2020-01-01",
                              "end": None},
        "starting_snapshot_membership": starting,
        "primary_security": None,
        "mic": None,
        "eligible": None,
        "accepted": False,
    }


def _edge(number: int, issue_number: int, cik: str | None, code: object) -> dict:
    return {
        "candidate_kind": "CIK_CUSIP_OBSERVATION",
        "identity_candidate_id": f"EDGE:{number}",
        "issue_evidence_id": f"ISSUE:{issue_number}",
        "source_record_id": f"CIK_CUSIP:{number}",
        "cik": cik,
        "validation_code": code,
        "issues": ["SYNTHETIC_SOURCE_REVIEW"] if number == 1 else [],
        "observed_filing_dates": {
            "date_semantics": "OBSERVATION_TIME",
            "source_first_filing_observation": "2019-01-01",
            "source_last_filing_observation": "2019-12-31",
            "historical_link_validity_established": False,
        },
        "historical_valid_from": None,
        "historical_valid_to": None,
        "accepted": False,
    }


def _records():
    edges = [
        _edge(1, 1, "0000000001", "2"),
        _edge(2, 1, "0000000001", "3"),
        _edge(3, 1, "0000000002", "2"),
        _edge(4, 1, "0000000003", "1"),
        _edge(5, 1, "0000000004", None),
        _edge(6, 2, "0000000005", "0"),
        _edge(7, 2, None, "3"),
    ]
    return [*(('identity_candidate', edge) for edge in edges),
            ('issue_evidence', _issue(1)),
            ('issue_evidence', _issue(2)),
            ('issue_evidence', _issue(3)),
            ('later_only_security_evidence', {'permno': 'LATER-ONLY'})]


def _old_summary() -> dict:
    return {"cik_observation_edge_count": 7,
            "issue_interval_count": 3,
            "later_only_interval_count": 1,
            "later_only_distinct_security_count": 1,
            "starting_security_count": 3,
            "exception_record_count": 2,
            "prior_receipt_raw_row_counts": {"CIK_CUSIP": 9}}


def test_screen_preserves_unknown_parallel_and_observation_time() -> None:
    view, summary = screen.build_view(_records(), _old_summary())
    assert summary["screened_edge_status_counts"] == {
        "CANDIDATE_SCREEN_PASS": 3,
        "CANDIDATE_SCREEN_REJECT_QUALITY": 2,
        "QUALITY_UNRESOLVED": 1,
        "CIK_UNUSABLE": 1,
    }
    assert summary["issue_interval_status_counts"] == {
        "no_cik_observation_edge": 1,
        "no_screened_cik_candidate": 2,
        "has_screened_cik_candidate": 1,
        "quality_unresolved": 1,
        "quality_rejected": 2,
        "cik_unusable": 1,
        "parallel_edges_same_cik": 1,
        "multiple_distinct_screened_ciks": 1,
    }
    assert summary["distinct_affected_issue_interval_count"] == 3
    assert summary["distinct_screened_cik_count"] == 2
    assert summary["old_export_predicate_verified"] is False
    assert summary["prior_source_exception_record_count"] == 2
    assert view["cik_observation_edge"][-1]["validation_code"] == "3"
    assert view["cik_observation_edge"][-1]["quality_screen_status"] == "CIK_UNUSABLE"
    assert summary["original_validation_code_bucket_counts"]["3"] == 2
    assert summary["source_issue_code_incidence_counts"] == {"SYNTHETIC_SOURCE_REVIEW": 1}
    assert summary["source_issue_distinct_affected_issue_interval_count"] == 1
    assert view["cik_observation_edge"][0]["source_issue_codes"] == ["SYNTHETIC_SOURCE_REVIEW"]
    assert view["cik_observation_edge"][0]["quality_screen_status"] == "CANDIDATE_SCREEN_PASS"
    assert summary["issue_interval_status_counts"]["has_screened_cik_candidate"] == 1
    assert view["cik_observation_edge"][0]["historical_valid_from"] is None
    assert view["cik_observation_edge"][0]["observed_filing_dates"]["date_semantics"] == "OBSERVATION_TIME"
    assert all(row["accepted"] is False for row in view["cik_observation_edge"])
    assert all(row["primary_security"] is row["market_mic"] is
               row["ordinary_share_eligibility"] is None for row in view["issue_evidence"])
    assert view["formal_m5_cohort_count"] is view["admitted_observations"] is None
    assert screen.summarize_view(view, _old_summary()) == summary
    multi_share_records = _records()
    multi_share_records[-2][1]["permco"] = "1"
    _, multi_share_summary = screen.build_view(multi_share_records, _old_summary())
    assert multi_share_summary["starting_issuer_ids_with_multiple_securities_count"] == 1
    assert multi_share_summary["primary_security_unknown_issue_count"] == 3


def test_noncanonical_quality_and_same_issuer_do_not_auto_pass() -> None:
    assert screen._quality_state("2") == "CANDIDATE_SCREEN_PASS"
    assert screen._quality_state(3) == "CANDIDATE_SCREEN_PASS"
    assert screen._quality_state("1") == "CANDIDATE_SCREEN_REJECT_QUALITY"
    assert screen._quality_state("0") == "CANDIDATE_SCREEN_REJECT_QUALITY"
    assert screen._quality_state(None) == "QUALITY_UNRESOLVED"
    assert screen._quality_state("2.0") == "QUALITY_UNRESOLVED"
    assert screen._quality_state(2.0) == "QUALITY_UNRESOLVED"
    assert screen._quality_state(True) == "QUALITY_UNRESOLVED"
    assert screen.audit_module.normalize_id("1", 10).zfill(10) == "0000000001"
    assert screen._screen_state("2", "0000000001") == "CANDIDATE_SCREEN_PASS"
    assert screen._screen_state("3", None) == "CIK_UNUSABLE"
    with pytest.raises(screen.AuditError, match="SOURCE_NORMALIZED_CIK_INVALID"):
        screen._screen_state("3", "bad")
    with pytest.raises(screen.AuditError, match="SOURCE_NORMALIZED_CIK_INVALID"):
        screen._screen_state("3", "0000000000")
    records = _records()
    records.insert(0, ("identity_candidate", {
        "candidate_kind": "SECURITY_ISSUER_CLOSED_DATE_INTERSECTION"}))
    view, _ = screen.build_view(records, _old_summary())
    assert len(view["cik_observation_edge"]) == 7


def test_source_count_and_saved_status_tampering_rejected() -> None:
    with pytest.raises(screen.AuditError, match="SOURCE_AGGREGATE_COVERAGE_MISMATCH"):
        screen.build_view(_records(), {**_old_summary(), "issue_interval_count": 4})
    view, _ = screen.build_view(_records(), _old_summary())
    bad = copy.deepcopy(view)
    bad["cik_observation_edge"][4]["quality_screen_status"] = "CANDIDATE_SCREEN_REJECT_QUALITY"
    with pytest.raises(screen.AuditError, match="SCREENED_EDGE_INVALID"):
        screen.summarize_view(bad, _old_summary())
    bad = copy.deepcopy(view)
    bad["issue_evidence"][0]["primary_security"] = True
    with pytest.raises(screen.AuditError, match="PREPARATION_ISSUE_STATUS_INVALID"):
        screen.summarize_view(bad, _old_summary())


def _write_synthetic_ledger(path: Path, records) -> None:
    data = {"schema": "SYNTHETIC_TEST_ONLY",
            "identity_candidate": [row for kind, row in records if kind == "identity_candidate"],
            "issue_evidence": [row for kind, row in records if kind == "issue_evidence"],
            "later_only_security_evidence": [row for kind, row in records
                                             if kind == "later_only_security_evidence"]}
    path.write_bytes(screen.json_bytes(data))


def test_streamed_sections_and_saved_output_reverification(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(screen, "ROOT", tmp_path)
    monkeypatch.setattr(screen, "check_rules", lambda: None)
    monkeypatch.setattr(screen, "check_ignored_output", lambda path: None)
    source_dir = tmp_path / "artifacts" / "source-SYNTHETIC_TEST_ONLY"
    source_dir.mkdir(parents=True)
    source_ledger = source_dir / screen.SOURCE_FILES["ledger"]
    _write_synthetic_ledger(source_ledger, _records())
    def fake_source(directory: Path):
        assert directory.resolve() == source_dir.resolve()
        return ({"source_manifest": {"registered_source_files": {}}}, _old_summary(),
                {"ledger": screen.sha256_file(source_ledger),
                 "registry": "a" * 64, "summary": "b" * 64,
                 "manifest": "c" * 64})
    monkeypatch.setattr(screen, "check_saved_source", fake_source)
    def fake_write(directory: Path, files: dict):
        directory.mkdir()
        for name, data in files.items():
            (directory / name).write_bytes(screen.json_bytes(data))
    monkeypatch.setattr(screen, "write_directory", fake_write)
    output_dir = tmp_path / "artifacts" / "output-SYNTHETIC_TEST_ONLY"
    summary = screen.run(source_dir, output_dir)
    assert summary["screened_edge_status_counts"]["CANDIDATE_SCREEN_PASS"] == 3
    assert screen.verify_output(output_dir) == summary
    private_path = output_dir / screen.OUTPUT_FILES["index"]
    original_private = private_path.read_bytes()
    private = json.loads(private_path.read_text(encoding="utf-8"))
    private["cik_observation_edge"][0]["cik"] = "TAMPER"
    private_path.write_bytes(screen.json_bytes(private))
    with pytest.raises(screen.AuditError, match="OUTPUT_FILE_HASH_MISMATCH"):
        screen.verify_output(output_dir)
    private_path.write_bytes(original_private)

    registry_path = output_dir / screen.OUTPUT_FILES["registry"]
    manifest_path = output_dir / screen.OUTPUT_FILES["manifest"]
    original_registry = registry_path.read_bytes()
    original_manifest = manifest_path.read_bytes()
    def tamper_registry_and_rehash(updates: dict) -> None:
        registry = json.loads(original_registry)
        registry.update(updates)
        registry_path.write_bytes(screen.json_bytes(registry))
        manifest = json.loads(original_manifest)
        manifest["files"][registry_path.name] = {
            "sha256": screen.sha256_file(registry_path), "bytes": registry_path.stat().st_size}
        manifest_path.write_bytes(screen.json_bytes(manifest))
    tamper_registry_and_rehash({"formal_m5_cohort_count": 1})
    with pytest.raises(screen.AuditError, match="OUTPUT_IDENTITY_OR_STATUS_MISMATCH"):
        screen.verify_output(output_dir)
    tamper_registry_and_rehash({"source_raw_file_sha256": {"wrong": "0" * 64}})
    with pytest.raises(screen.AuditError, match="OUTPUT_SOURCE_BINDING_MISMATCH"):
        screen.verify_output(output_dir)


def test_missing_array_and_decision_scope_rejected(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "SYNTHETIC_TEST_ONLY.json"
    path.write_bytes(screen.json_bytes({"identity_candidate": [], "issue_evidence": []}))
    with pytest.raises(screen.AuditError, match="SOURCE_ARRAY_COVERAGE_INVALID"):
        list(screen.stream_source_arrays(path))
    path.write_bytes(screen.json_bytes({"identity_candidate": [], "issue_evidence": [],
                                        "later_only_security_evidence": []}))
    assert list(screen.stream_source_arrays(path)) == []
    policy = {"schema": "m5_accepted_preparation_policy_v1",
              "preparation_rules_researcher_accepted": True,
              "full_m5_protocol_researcher_accepted": False,
              "rule_ids": ["candidate_link_screening", "fixed_2020_starting_scope",
                           "derived_daily_and_availability"],
              "candidate_link_screening": {
                  "provider_quality_codes_for_candidate_screen": [2, 3],
                  "existing_cusip8_exact_join_preserved": True}}
    candidate = {"status": "PROPOSED", "researcher_accepted": False,
                 "formal_financial_experiment_status": "NOT_RUN"}
    decision = {"accepted_policy_sha256": "", "original_round8_candidate_sha256": "",
                "decision_scope": "WRONG_SCOPE", "accepted_rule_ids": policy["rule_ids"],
                "preparation_rules_researcher_accepted": True,
                "full_m5_protocol_researcher_accepted": False}
    paths = {}
    for role, data in (("POLICY", policy), ("CANDIDATE", candidate),
                       ("DICTIONARY", {}), ("DECISION", decision)):
        p = tmp_path / f"{role}-SYNTHETIC_TEST_ONLY.json"
        p.write_bytes(screen.json_bytes(data))
        paths[role] = p
        monkeypatch.setattr(screen, role, p)
        monkeypatch.setattr(screen, role + "_SHA256", screen.sha256_file(p))
    decision["accepted_policy_sha256"] = screen.POLICY_SHA256
    decision["original_round8_candidate_sha256"] = screen.CANDIDATE_SHA256
    paths["DECISION"].write_bytes(screen.json_bytes(decision))
    monkeypatch.setattr(screen, "DECISION_SHA256", screen.sha256_file(paths["DECISION"]))
    with pytest.raises(screen.AuditError, match="PRIVATE_DECISION_SCOPE_OR_VERSION_MISMATCH"):
        screen.check_rules()
    decision["decision_scope"] = "ACCEPTED_ENGINEERING_PREPARATION_ONLY"
    paths["DECISION"].write_bytes(screen.json_bytes(decision))
    monkeypatch.setattr(screen, "DECISION_SHA256", screen.sha256_file(paths["DECISION"]))
    screen.check_rules()
    helper_hash = screen.AUDIT_HELPER_SHA256
    monkeypatch.setattr(screen, "AUDIT_HELPER_SHA256", "0" * 64)
    with pytest.raises(screen.AuditError, match="IMPORTED_AUDIT_MODULE_HASH_MISMATCH"):
        screen.check_rules()
    monkeypatch.setattr(screen, "AUDIT_HELPER_SHA256", helper_hash)
    paths["DECISION"].unlink()
    with pytest.raises(FileNotFoundError):
        screen.check_rules()


def test_self_consistent_but_different_source_ledger_rejected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(screen, "ROOT", tmp_path)
    source_dir = tmp_path / "artifacts" / "other-SYNTHETIC_TEST_ONLY"
    source_dir.mkdir(parents=True)
    details = {}
    for role in ("ledger", "registry", "summary"):
        path = source_dir / screen.SOURCE_FILES[role]
        path.write_bytes(screen.json_bytes({"schema": "SYNTHETIC_TEST_ONLY", "role": role}))
        details[path.name] = {"sha256": screen.sha256_file(path), "bytes": path.stat().st_size}
    manifest = {"schema": "m5_identity_candidate_output_manifest_private_v1", "files": details}
    (source_dir / screen.SOURCE_FILES["manifest"]).write_bytes(screen.json_bytes(manifest))
    with pytest.raises(screen.AuditError, match="PINNED_SOURCE_LEDGER_HASH_MISMATCH"):
        screen.check_saved_source(source_dir)
