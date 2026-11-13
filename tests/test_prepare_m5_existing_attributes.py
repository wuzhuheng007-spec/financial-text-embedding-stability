"""Synthetic software checks for the ENGINEERING_ONLY attribute inventory."""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import audit_m5_wrds_intake as audit  # noqa: E402
import prepare_m5_existing_attributes as attributes  # noqa: E402


SECURITY_FIELDS = ["securitytype", "issuertype", "usincflg", "primaryexch",
                   "secinfostartdt", "secinfoenddt"]
ISSUER_FIELDS = ["issuertype", "usincflg", "issinfostartdt", "issinfoenddt"]


def _source_row(*, issuer_type: str = "CORP", inc: str = "Y", exchange: str = "N") -> dict:
    return {"securitytype": "EQTY", "issuertype": issuer_type, "usincflg": inc,
            "primaryexch": exchange, "secinfostartdt": "2020-01-01",
            "secinfoenddt": "2020-12-31"}


def _issuer_record(*, issuer_type: str = "CORP", inc: str = "Y") -> dict:
    raw = {"issuertype": issuer_type, "usincflg": inc,
           "issinfostartdt": "2020-01-01", "issinfoenddt": "2020-12-31"}
    return {"source_sha256": "a" * 64, "normalized_row_sha256": audit.row_digest(raw),
            "raw_fields": raw}


def _issue(matches: list[dict] | None = None) -> dict:
    return {"source_role": "STARTING_SNAPSHOT", "source_record": 1,
            "normalized_row_sha256": "b" * 64,
            "permno": "SYNTHETIC_SECURITY", "permco": "SYNTHETIC_ISSUER",
            "start": "2020-01-01", "end": "2020-12-31",
            "issuer_date_coverage_under_closed_comparison": False,
            "issuer_matches": matches or []}


def _match(record: dict, number: int = 1, *, start: str = "2020-01-01",
           end: str = "2020-12-31", equality: bool = False) -> dict:
    return {"issuer_source_role": "EARLY", "issuer_source_record": number,
            "issuer_row_sha256": record["normalized_row_sha256"],
            "issuer_source_sha256": record["source_sha256"],
            "intersection_start": start, "intersection_end": end,
            "endpoint_equality_review": equality}


def test_source_fact_and_public_reference_do_not_admit_or_select_primary() -> None:
    record = _issuer_record()
    view, summary = attributes.build_view(
        [_issue([_match(record)])], {("STARTING_SNAPSHOT", 1): _source_row()},
        {("EARLY", 1): record}, SECURITY_FIELDS, ISSUER_FIELDS)
    row = view["issue_interval_attributes"][0]
    assert row["security_fields"]["securitytype"]["capability_state"] == "CURRENT_PUBLIC_REFERENCE_CODE_ONLY"
    assert row["security_fields"]["primaryexch"]["capability_state"] == "DEFINITION_UNVERIFIED"
    assert row["primary_security"] is None and row["historical_mic"] is None
    assert row["ordinary_share_eligibility"] is None and row["admitted"] is None
    assert row["admission_status"] == "NOT_ASSESSED"
    assert row["historical_cik_validity"] == "NOT_ESTABLISHED"
    assert row["issuer_candidates"][0]["date_applicability"] == "INTERVAL_APPLICABILITY_UNRESOLVED"
    assert summary["security_count"] == 1 and summary["issuer_count"] == 1
    assert summary["issue_interval_count"] == 1 and summary["issuer_candidate_edge_count"] == 1
    assert summary["formal_cohort_count"] is None


def test_literal_missing_unknown_and_conflicting_issuer_candidate_are_preserved() -> None:
    first = _issuer_record(issuer_type="ACOR")
    second = _issuer_record(issuer_type="CORP", inc="N")
    source = _source_row(issuer_type="", inc="Y")
    view, summary = attributes.build_view(
        [_issue([_match(first, 1), _match(second, 2)])],
        {("STARTING_SNAPSHOT", 1): source},
        {("EARLY", 1): first, ("EARLY", 2): second},
        SECURITY_FIELDS, ISSUER_FIELDS)
    row = view["issue_interval_attributes"][0]
    assert row["security_fields"]["issuertype"]["capability_state"] == "VALUE_MISSING"
    assert row["issuer_candidates"][1]["fields"]["usincflg"]["capability_state"] == "CURRENT_PUBLIC_REFERENCE_CODE_ONLY"
    assert attributes.lexical_state("issuertype", "SYNTHETIC_UNMAPPED") == (
        "OUTSIDE_SELECTED_PUBLIC_REFERENCE_SET")
    assert row["literal_disagreement_within_issue_interval_fields"] == ["issuertype", "usincflg"]
    assert summary["literal_disagreement_distinct_issue_count"] == 1
    assert summary["closed_date_candidate_overlap_disagreement_distinct_issue_count"] == 1
    assert summary["issuer_candidate_edge_count"] == 2
    assert summary["issues_with_issuer_candidate_count"] == 1
    assert summary["issuer_edge_public_reference_code_counts"]["issuertype"] == {"ACOR": 1, "CORP": 1}
    assert summary["issuer_edge_public_reference_code_counts"]["usincflg"] == {"N": 1, "Y": 1}


def test_sequential_issuer_values_are_not_called_concurrent_conflicts() -> None:
    first = _issuer_record(issuer_type="ACOR")
    second = _issuer_record(issuer_type="CORP")
    security = _source_row(issuer_type="")
    view, summary = attributes.build_view(
        [_issue([_match(first, 1, start="2020-01-01", end="2020-05-31"),
                 _match(second, 2, start="2020-06-01", end="2020-12-31")])],
        {("STARTING_SNAPSHOT", 1): security},
        {("EARLY", 1): first, ("EARLY", 2): second},
        SECURITY_FIELDS, ISSUER_FIELDS)
    row = view["issue_interval_attributes"][0]
    assert row["literal_disagreement_within_issue_interval_fields"] == ["issuertype"]
    assert row["closed_date_candidate_overlap_disagreement_fields"] == []
    assert summary["confirmed_source_conflict_count"] is None
    assert summary["distinct_issuer_source_record_count"] == 2


def test_unmatched_issuer_is_not_filled_and_bad_binding_rejected() -> None:
    source = {("STARTING_SNAPSHOT", 1): _source_row()}
    view, summary = attributes.build_view([_issue()], source, {},
                                           SECURITY_FIELDS, ISSUER_FIELDS)
    assert view["issue_interval_attributes"][0]["issuer_candidates"] == []
    assert summary["issues_without_issuer_candidate_count"] == 1
    record = _issuer_record()
    bad = _match(record)
    bad["issuer_row_sha256"] = "0" * 64
    with pytest.raises(audit.AuditError, match="ISSUER_MATCH_SOURCE_BINDING_MISMATCH"):
        attributes.build_view([_issue([bad])], source, {("EARLY", 1): record},
                              SECURITY_FIELDS, ISSUER_FIELDS)


def test_security_source_column_is_recovered_when_absent_from_saved_projection(tmp_path: Path) -> None:
    source_row = _source_row()
    path = tmp_path / "SYNTHETIC_TEST_ONLY.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=SECURITY_FIELDS)
        writer.writeheader()
        writer.writerow(source_row)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    source = {"SECURITY_STARTING": {"path": str(path), "sha256": sha,
                                    "column_names": SECURITY_FIELDS},
              "SECURITY_LATER": {"path": str(path), "sha256": sha,
                                 "column_names": SECURITY_FIELDS}}
    issue = _issue()
    issue["normalized_row_sha256"] = audit.row_digest(source_row)
    projected, status = attributes._scan_security_csvs([issue], source, SECURITY_FIELDS)
    assert projected[("STARTING_SNAPSHOT", 1)] == source_row
    assert status["SECURITY_STARTING"]["saved_identity_projection_field_state"]["primaryexch"] == "DIRECT_RAW_FIELD_ABSENT"
    assert status["SECURITY_STARTING"]["saved_identity_projection_field_state"]["secinfostartdt"] == "DERIVED_INTERVAL_MAPPING_PRESENT"
    assert status["SECURITY_STARTING"]["source_field_present"]["primaryexch"] is True


def test_source_digest_and_missing_column_fail_closed(tmp_path: Path) -> None:
    source_row = _source_row()
    path = tmp_path / "SYNTHETIC_TEST_ONLY.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=SECURITY_FIELDS)
        writer.writeheader()
        writer.writerow(source_row)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    source = {"SECURITY_STARTING": {"path": str(path), "sha256": sha,
                                    "column_names": SECURITY_FIELDS},
              "SECURITY_LATER": {"path": str(path), "sha256": sha,
                                 "column_names": SECURITY_FIELDS}}
    issue = _issue()
    with pytest.raises(audit.AuditError, match="SECURITY_ROW_DIGEST_MISMATCH"):
        attributes._scan_security_csvs([issue], source, SECURITY_FIELDS)
    issue["normalized_row_sha256"] = audit.row_digest(source_row)
    projected, status = attributes._scan_security_csvs([issue], source, SECURITY_FIELDS + ["sharetype"])
    assert projected[("STARTING_SNAPSHOT", 1)]["sharetype"] is None
    assert status["SECURITY_STARTING"]["source_field_present"]["sharetype"] is False
    assert attributes.lexical_state("sharetype", None) == "MISSING_FROM_BOUND_SOURCE"


def test_saved_payload_tamper_is_rejected_without_source_access(tmp_path: Path) -> None:
    values = {"view": {"schema": "SYNTHETIC_TEST_ONLY", "rows": []},
              "registry": {"schema": "SYNTHETIC_TEST_ONLY"},
              "summary": {"schema": "SYNTHETIC_TEST_ONLY", "count": 0}}
    entries = {}
    for role, value in values.items():
        data = attributes.preparation.json_bytes(value)
        (tmp_path / attributes.FILES[role]).write_bytes(data)
        entries[attributes.FILES[role]] = {"sha256": hashlib.sha256(data).hexdigest(),
                                          "bytes": len(data)}
    manifest = {"schema": "m5_attribute_manifest_private_v1", "files": entries}
    (tmp_path / attributes.FILES["manifest"]).write_bytes(
        attributes.preparation.json_bytes(manifest))
    assert attributes._check_saved_payloads(tmp_path) == (values["registry"], values["summary"])
    (tmp_path / attributes.FILES["view"]).write_text("{}\n", encoding="utf-8")
    with pytest.raises(audit.AuditError, match="SAVED_FILE_HASH_MISMATCH"):
        attributes._check_saved_payloads(tmp_path)


def test_existing_output_is_rejected_before_attribute_scan(tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(attributes, "_check_config", lambda: ({}, "SYNTHETIC_HASH"))
    monkeypatch.setattr(attributes, "_load_bound_sources",
                        lambda _: ({}, {}, {}, {}))
    monkeypatch.setattr(audit, "check_ignored_output",
                        lambda path: audit.require(not path.exists(), "OUTPUT_ALREADY_EXISTS"))
    with pytest.raises(audit.AuditError, match="OUTPUT_ALREADY_EXISTS"):
        attributes.run(tmp_path)


def test_cli_error_never_echoes_untrusted_argument(monkeypatch: pytest.MonkeyPatch,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    private_marker = "SYNTHETIC_PRIVATE_TOKEN_DO_NOT_ECHO"
    monkeypatch.setattr(sys, "argv", ["inventory", "run", "--bad-option", private_marker])
    assert attributes.main() == 2
    captured = capsys.readouterr()
    assert "CLI_ARGUMENT_INVALID" in captured.out
    assert private_marker not in captured.out + captured.err


def test_dependency_code_hash_change_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    config = json.loads(attributes.CONFIG.read_text(encoding="utf-8"))
    config["source_identity"]["preparation_screen_code_sha256"] = "0" * 64
    monkeypatch.setattr(attributes, "_load_json", lambda _: config)
    with pytest.raises(audit.AuditError, match="DEPENDENCY_HASH_MISMATCH"):
        attributes._check_config()


def test_saved_config_version_mismatch_rejects_before_source_scan(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(attributes, "_check_config", lambda: ({}, "NEW_CONFIG_HASH"))
    monkeypatch.setattr(attributes, "_check_saved_payloads", lambda _:
                        ({"schema": "m5_attribute_registry_private_v1",
                          "config_sha256": "OLD_CONFIG_HASH"}, {}))
    monkeypatch.setattr(attributes, "_load_bound_sources", lambda _:
                        pytest.fail("source scan occurred before config rejection"))
    with pytest.raises(audit.AuditError, match="SAVED_IDENTITY_OR_STATUS_MISMATCH"):
        attributes.verify_output(tmp_path)


def test_issuer_projection_row_source_must_match_bound_raw_file() -> None:
    record = _issuer_record()
    index = {"related_issuer_records": [
        {"source_role": "EARLY", "source_record": 1, **record}]}
    source = {"ISSUER_EARLY": {"sha256": "0" * 64},
              "ISSUER_LATER": {"sha256": "1" * 64}}
    with pytest.raises(audit.AuditError, match="ISSUER_PROJECTION_INVALID"):
        attributes._issuer_projection(index, [_issue([_match(record)])], source,
                                      ISSUER_FIELDS)


def test_thin_source_manifest_recovers_headers_from_bound_wrds_registry(
        tmp_path: Path) -> None:
    registry = {"local_snapshot": {"sha256": "a" * 64,
                                   "column_names": ["permno", "securitytype"]},
                "local_changes": {"sha256": "b" * 64}}
    raw = attributes.preparation.json_bytes(registry)
    (tmp_path / "m5_wrds_intake_registry.json").write_bytes(raw)
    manifest = {"wrds_artifact_directory": str(tmp_path),
                "wrds_artifact_sha256": {"registry": hashlib.sha256(raw).hexdigest()}}
    source = {"SECURITY_STARTING": {"sha256": "a" * 64},
              "SECURITY_LATER": {"sha256": "b" * 64}}
    bound = attributes._bind_security_headers(manifest, source)
    assert bound["SECURITY_STARTING"]["column_names"] == ["permno", "securitytype"]
    assert bound["SECURITY_LATER"]["column_names"] == ["permno", "securitytype"]
    assert bound["SECURITY_LATER"]["column_names_provenance"] == (
        "SNAPSHOT_COLUMNS_AND_PRIOR_LATER_HEADER_SET_CHECK")
    manifest["wrds_artifact_sha256"]["registry"] = "0" * 64
    with pytest.raises(audit.AuditError, match="WRDS_REGISTRY_BINDING_MISMATCH"):
        attributes._bind_security_headers(manifest, source)
