"""SYNTHETIC_TEST_ONLY checks for the M5 design-feasibility diagnostic."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import audit_m5_wrds_intake as audit  # noqa: E402
import diagnose_m5_protocol_feasibility as diagnostic  # noqa: E402


def config() -> dict:
    return {"reference_date": "2020-01-01",
            "endpoint_conventions": ["CLOSED", "LEFT_CLOSED_RIGHT_OPEN"],
            "code_pattern": {"securitytype": ["EQTY"], "securitysubtype": ["COM"],
                             "sharetype": ["NS"], "issuertype": ["ACOR", "CORP"]},
            "quality_screen_status": "CANDIDATE_SCREEN_PASS", "quality_codes": ["2", "3"],
            "strata": {"usincflg_public_values": ["Y", "N"],
                       "primaryexch_public_examples": ["A", "B", "I", "N", "Q", "R", "X"]}}


def issue(n: int, *, security: str | None = None, start: str | None = "2019-12-01",
          end: str | None = "2020-12-31", **fields) -> dict:
    values = {"securitytype": "EQTY", "securitysubtype": "COM", "sharetype": "NS",
              "issuertype": "CORP", "usincflg": "Y", "primaryexch": "N"}
    values.update(fields)
    return {"issue_id": f"SYNTHETIC_TEST_ONLY_{n}",
            "security": security or f"SYNTHETIC_SECURITY_{n}",
            "issuer": f"SYNTHETIC_ISSUER_{n}", "start": start, "end": end,
            "fields": values}


def edge(n: int, source_issue: dict, cik: str, *, quality: str = "2",
         status: str = "CANDIDATE_SCREEN_PASS") -> dict:
    return {"edge_id": f"SYNTHETIC_EDGE_{n}", "issue_id": source_issue["issue_id"],
            "cik": cik, "quality": quality, "screen_status": status}


def test_endpoint_conventions_and_unknown_dates_remain_separate() -> None:
    rows = [issue(1, end="2020-01-01"), issue(2, end="2020-01-02"),
            issue(3, end=None), issue(4, start="BAD"),
            issue(5, start="2020-02-01", end="2020-01-01")]
    _, safe = diagnostic.evaluate(rows, [], config())
    closed = safe["scenarios"]["CLOSED"]["date_state_issue_interval_counts"]
    halfopen = safe["scenarios"]["LEFT_CLOSED_RIGHT_OPEN"]["date_state_issue_interval_counts"]
    assert closed == {"ACTIVE": 2, "NOT_ACTIVE": 0, "UNKNOWN_ENDPOINT": 1,
                      "INVALID_DATE": 1, "REVERSED_INTERVAL": 1}
    assert halfopen == {"ACTIVE": 1, "NOT_ACTIVE": 1, "UNKNOWN_ENDPOINT": 1,
                        "INVALID_DATE": 1, "REVERSED_INTERVAL": 1}
    assert safe["H_time_valid_cik_primary_admitted_count"] is None
    assert safe["R_formal_admitted_count"] is None


def test_marginals_do_not_substitute_for_joint_code_pattern() -> None:
    rows = [issue(1, issuertype="REIT"), issue(2, securitytype="FUND"),
            issue(3, sharetype=None), issue(4)]
    _, safe = diagnostic.evaluate(rows, [], config())
    case = safe["scenarios"]["CLOSED"]
    assert case["code_pattern_stage_issue_interval_counts"] == {
        "securitytype": 3, "securitysubtype": 3, "sharetype": 2, "issuertype": 1}
    assert case["code_pattern_joint_issue_interval_count"] == 1
    reasons = case["active_code_pattern_reason_issue_interval_counts"]
    assert reasons["ISSUERTYPE_OUTSIDE_PROPOSED_PATTERN"] == 1
    assert reasons["SECURITYTYPE_OUTSIDE_PROPOSED_PATTERN"] == 1
    assert reasons["SHARETYPE_MISSING"] == 1


def test_structural_pairs_keep_parallel_edges_and_two_sided_ambiguity() -> None:
    rows = [issue(n) for n in range(1, 6)]
    links = [edge(1, rows[0], "SYNTHETIC_CIK_A"),
             edge(2, rows[0], "SYNTHETIC_CIK_A"),
             edge(3, rows[1], "SYNTHETIC_CIK_B"),
             edge(4, rows[2], "SYNTHETIC_CIK_B"),
             edge(5, rows[3], "SYNTHETIC_CIK_C"),
             edge(6, rows[3], "SYNTHETIC_CIK_D")]
    private, safe = diagnostic.evaluate(rows, links, config())
    case = safe["scenarios"]["CLOSED"]
    assert case["quality_2_3_pass_edge_count_on_joint_pattern"] == 6
    assert case["quality_2_3_pass_distinct_cik_security_pair_count"] == 5
    assert case["quality_2_3_pass_duplicate_edges_same_cik_security_pair_count"] == 1
    assert case["joint_pattern_issue_intervals_with_parallel_same_cik_edges_count"] == 1
    assert case["joint_pattern_issue_intervals_with_multiple_distinct_ciks_count"] == 1
    assert case["distinct_securities_with_multiple_distinct_ciks_count"] == 1
    assert case["distinct_ciks_with_multiple_distinct_securities_count"] == 1
    assert case["joint_pattern_issue_intervals_without_pass_edge_count"] == 1
    assert case["strict_one_to_one_structural_pair_count"] == 1
    assert private["cases"][0]["structural_unique_pairs"][0]["accepted"] is False


def test_multiple_active_source_intervals_exclude_structural_unique_pair() -> None:
    first = issue(1, security="SYNTHETIC_SHARED")
    second = issue(2, security="SYNTHETIC_SHARED")
    _, safe = diagnostic.evaluate(
        [first, second], [edge(1, first, "SYNTHETIC_CIK_A")], config())
    case = safe["scenarios"]["CLOSED"]
    assert case["active_securities_with_multiple_intervals_count"] == 1
    assert case["quality_2_3_pass_distinct_cik_security_pair_count"] == 1
    assert case["strict_one_to_one_structural_pair_count"] == 0


def test_public_strata_are_not_mic_or_eligibility() -> None:
    rows = [issue(1, usincflg="Y", primaryexch="N"),
            issue(2, usincflg="N", primaryexch="X"),
            issue(3, usincflg=None, primaryexch="SYNTHETIC_OTHER")]
    _, safe = diagnostic.evaluate(rows, [], config())
    case = safe["scenarios"]["CLOSED"]
    assert case["usincflg_code_pattern_issue_interval_strata"] == {
        "MISSING": 1, "N": 1, "Y": 1}
    assert case["primaryexch_code_pattern_issue_interval_strata"] == {
        "N": 1, "OTHER_UNMAPPED_NOT_INVALID": 1, "X": 1}
    assert case["historical_primary_security_evidence"] is None
    assert case["historical_cik_link_validity"] is None


def test_rejected_quality_never_enters_structural_graph() -> None:
    row = issue(1)
    links = [edge(1, row, "SYNTHETIC_CIK_A", quality="1",
                  status="CANDIDATE_SCREEN_REJECT_QUALITY")]
    _, safe = diagnostic.evaluate([row], links, config())
    assert safe["full_source_screen"]["quality_2_3_pass_edge_count"] == 0
    assert safe["scenarios"]["CLOSED"]["strict_one_to_one_structural_pair_count"] == 0
    with pytest.raises(audit.AuditError, match="PASSED_QUALITY_IDENTITY_INVALID"):
        diagnostic.evaluate([row], [edge(2, row, "SYNTHETIC_CIK_A", quality="1")], config())


def test_duplicate_issue_or_edge_reference_rejected() -> None:
    row = issue(1)
    with pytest.raises(audit.AuditError, match="ISSUE_REFERENCE_DUPLICATED"):
        diagnostic.evaluate([row, row], [], config())
    duplicate = edge(1, row, "SYNTHETIC_CIK_A")
    with pytest.raises(audit.AuditError, match="EDGE_REFERENCE_INVALID"):
        diagnostic.evaluate([row], [duplicate, duplicate], config())


def test_stream_reader_checks_object_array_identity(tmp_path: Path) -> None:
    path = tmp_path / "SYNTHETIC_TEST_ONLY.json"
    path.write_bytes(diagnostic.json_bytes({"issue_interval_attributes": [{"x": 1}]}))
    assert list(diagnostic.stream_arrays(
        path, frozenset({"issue_interval_attributes"}))) == [
            ("issue_interval_attributes", {"x": 1})]
    with pytest.raises(audit.AuditError, match="SOURCE_ARRAY_COVERAGE_INVALID"):
        list(diagnostic.stream_arrays(path, frozenset({"another_array"})))


@pytest.mark.parametrize(
    ("attribute_count", "candidate_indices", "expected_error"),
    [(1, [1, 1], "CANDIDATE_ISSUE_REFERENCE_DUPLICATED"),
     (2, [1], "SAVED_VIEW_RECORD_COUNTS_MISMATCH")],
)
def test_saved_issue_references_are_unique_and_complete(
        monkeypatch: pytest.MonkeyPatch, attribute_count: int,
        candidate_indices: list[int], expected_error: str) -> None:
    interval = {"start": "2019-01-01", "end": "2021-01-01"}
    attributes = [
        {"security_source_role": "SYNTHETIC_TEST_ONLY",
         "security_source_record": n,
         "issue_permno": f"SYNTHETIC_SECURITY_{n}",
         "issue_permco": f"SYNTHETIC_ISSUER_{n}",
         "source_interval": interval,
         "security_fields": {field: {"raw_value": "SYNTHETIC_TEST_ONLY"}
                             for field in (*diagnostic.REFERENCE_KEYS,
                                           "usincflg", "primaryexch")},
         "admitted": None, "primary_security": None, "historical_mic": None}
        for n in range(1, attribute_count + 1)]
    candidate = [
        {"issue_evidence_id": f"SECURITY:SYNTHETIC_TEST_ONLY:{n}",
         "permno": f"SYNTHETIC_SECURITY_{n}",
         "permco": f"SYNTHETIC_ISSUER_{n}",
         "security_interval": interval, "accepted": False}
        for n in candidate_indices]

    def synthetic_stream(_path: Path, wanted: frozenset[str]):
        if wanted == frozenset({"issue_interval_attributes"}):
            yield from (("issue_interval_attributes", row) for row in attributes)
        else:
            yield from (("issue_evidence", row) for row in candidate)

    monkeypatch.setattr(diagnostic, "stream_arrays", synthetic_stream)
    with pytest.raises(audit.AuditError, match=expected_error):
        diagnostic.project_saved_views()


def test_missing_or_changed_saved_source_rejected_before_projection(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "SYNTHETIC_TEST_ONLY.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(diagnostic, "SOURCE_PATHS", {"attribute_view": path})
    with pytest.raises(audit.AuditError, match="SAVED_SOURCE_HASH_MISMATCH"):
        diagnostic.check_saved_sources({"source_files": {"attribute_view": "0" * 64}})


def test_saved_payload_tamper_rejected_without_private_source_read(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = config()
    cfg["source_files"] = {"SYNTHETIC_ROLE": "a" * 64}
    monkeypatch.setattr(diagnostic, "check_config", lambda: (cfg, "SYNTHETIC_CFG_HASH"))
    monkeypatch.setattr(diagnostic, "check_saved_sources",
                        lambda _: pytest.fail("source read before payload hash check"))
    monkeypatch.setattr(diagnostic, "SOURCE_PATHS", {})
    private = {"scope": "SYNTHETIC_TEST_ONLY"}
    summary = {"status": "SYNTHETIC_TEST_ONLY"}
    registry = {"schema": "SYNTHETIC_TEST_ONLY"}
    values = {"view": private, "summary": summary, "registry": registry}
    entries = {}
    for role, payload in values.items():
        name = diagnostic.OUTPUT_FILES[role]
        data = diagnostic.json_bytes(payload)
        (tmp_path / name).write_bytes(data)
        entries[name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    (tmp_path / diagnostic.OUTPUT_FILES["manifest"]).write_bytes(
        diagnostic.json_bytes({"schema": "m5_protocol_joint_diagnostic_manifest_private_v1",
                               "files": entries}))
    (tmp_path / diagnostic.OUTPUT_FILES["view"]).write_text("{}\n", encoding="utf-8")
    with pytest.raises(audit.AuditError, match="OUTPUT_FILE_HASH_MISMATCH"):
        diagnostic.verify_output(tmp_path)


def test_existing_output_rejected_before_saved_data_read(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(diagnostic, "check_config", lambda: ({}, "SYNTHETIC_CFG_HASH"))
    monkeypatch.setattr(diagnostic, "check_saved_sources",
                        lambda _: pytest.fail("source read before output existence check"))
    monkeypatch.setattr(audit, "check_ignored_output",
                        lambda path: audit.require(not path.exists(), "OUTPUT_ALREADY_EXISTS"))
    with pytest.raises(audit.AuditError, match="OUTPUT_ALREADY_EXISTS"):
        diagnostic.run(tmp_path)


def test_cli_does_not_echo_untrusted_argument(monkeypatch: pytest.MonkeyPatch,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    private_marker = "SYNTHETIC_PRIVATE_TOKEN_DO_NOT_ECHO"
    monkeypatch.setattr(sys, "argv", ["diagnose", "run", "--bad", private_marker])
    assert diagnostic.main() == 2
    captured = capsys.readouterr()
    assert private_marker not in captured.out + captured.err
    assert "CLI_ARGUMENT_INVALID" in captured.out
