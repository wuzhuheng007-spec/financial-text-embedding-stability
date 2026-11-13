"""Synthetic-only tests for the saved-evidence M5 identity candidate ledger."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_m5_identity_candidate_ledger as ledger  # noqa: E402


def _write_json(path: Path, value: dict) -> str:
    path.write_bytes(ledger.json_bytes(value))
    return ledger.sha256_file(path)


def _synthetic_sources(base: Path) -> tuple[dict, dict, dict]:
    base.mkdir(parents=True, exist_ok=True)
    source_files = {}
    for role in ("SECURITY_STARTING", "SECURITY_LATER", "CIK_CUSIP",
                 "SECURITY_QUERY_SQL", "SECURITY_V2_RECEIPT", "ISSUER_EARLY",
                 "ISSUER_LATER", "ISSUER_EARLY_SQL", "ISSUER_LATER_SQL"):
        path = base / (role.lower() + ".synthetic")
        path.write_bytes(("SYNTHETIC_TEST_ONLY:" + role + "\n").encode("ascii"))
        source_files[role] = {"path": str(path), "sha256": ledger.sha256_file(path)}
    manifest = {"schema": "m5_identity_source_manifest_private_v1",
                "registered_source_files": source_files,
                "prior_receipt_raw_row_counts": {
                    "SECURITY_STARTING": 1, "SECURITY_LATER": 2,
                    "CIK_CUSIP": 2, "ISSUER_EARLY": 2, "ISSUER_LATER": 0},
                "raw_row_count_provenance": "PRIOR_SAVED_RECEIPTS_NOT_REPARSED_IN_THIS_RUN"}

    def cik(number: int, value: str, first: str | None) -> dict:
        return {"link_source_record": number, "cik": value,
                "historical_cusip": "AAA00000", "validation_code": "2",
                "validation_class": "SYNTHETIC_TEST_ONLY",
                "first_filing_observation": first,
                "last_filing_observation": "2022-01-01" if first else None,
                "issues": [], "historical_link_validity": "NOT_ESTABLISHED",
                "accepted": False}

    first = {"source": "STARTING_SNAPSHOT", "source_record": 1,
             "start": "2020-01-01", "end": "2020-12-31",
             "historical_cusip": "AAA00000", "normalized_row_sha256": "a" * 64,
             "candidates": [cik(1, "0000000001", "2019-01-01"),
                            cik(2, "0000000002", None)],
             "accepted": False}
    second = {"source": "LATER_CHANGES", "source_record": 1,
              "start": "2021-01-01", "end": "2021-12-31",
              "historical_cusip": "AAA00000", "normalized_row_sha256": "b" * 64,
              "candidates": [cik(1, "0000000001", "2019-01-01")],
              "accepted": False}
    wi = {"schema": "m5_wrds_history_candidate_index_v1", "accepted": False,
          "historical_link_validity": "NOT_ESTABLISHED",
          "starting_securities": [{"permno": "11", "history": [first, second],
                                   "accepted": False}],
          "later_only_security_intervals": [{
              "source_record": 2, "permno": "22", "start": "2021-03-01",
              "end": "2021-12-31", "historical_cusip": "BBB00000",
              "normalized_row_sha256": "c" * 64, "issues": [],
              "not_in_starting_snapshot": True, "admitted": False}],
          "exceptions": [{"source": "STARTING_SNAPSHOT", "source_record": 1,
                          "codes": ["SYNTHETIC_PRIOR_REVIEW"]},
                         {"source": "STARTING_SNAPSHOT", "source_record": 1,
                          "codes": ["SYNTHETIC_OVERLAPPING_REVIEW"]}],
          "summary": {"history_interval_count": 2, "interval_candidate_rows": 3}}
    issuer_rows = []
    issuer_matches = []
    for number, start, end in ((1, "2019-01-01", "2020-06-30"),
                               (2, "2020-06-30", "2020-12-31")):
        issuer_rows.append({"source_role": "EARLY", "source_record": number,
                            "source_sha256": source_files["ISSUER_EARLY"]["sha256"],
                            "normalized_row_sha256": str(number) * 64,
                            "permco": "33", "start": start, "end": end})
        issuer_matches.append({
            "issuer_source_role": "EARLY", "issuer_source_record": number,
            "issuer_source_sha256": source_files["ISSUER_EARLY"]["sha256"],
            "issuer_row_sha256": str(number) * 64,
            "intersection_start": "2020-01-01" if number == 1 else "2020-06-30",
            "intersection_end": end, "endpoint_equality_review": number == 2,
            "accepted": False})
    intervals = []
    for row, matches, coverage in ((first, issuer_matches, True),
                                   (second, [], False)):
        intervals.append({
            "source_role": row["source"], "source_record": row["source_record"],
            "source_sha256": source_files[
                "SECURITY_STARTING" if row["source"] == "STARTING_SNAPSHOT"
                else "SECURITY_LATER"]["sha256"],
            "normalized_row_sha256": row["normalized_row_sha256"],
            "permno": "11", "permco": "33", "start": row["start"],
            "end": row["end"], "historical_cusip": row["historical_cusip"],
            "issuer_matches": matches,
            "issuer_date_coverage_under_closed_comparison": coverage,
            "historical_permco_change_candidate": False,
            "accepted": False, "prior_cik_candidates_accepted": False})
    ii = {"schema": "m5_issuer_history_candidates_private_v1", "accepted": False,
          "security_interval_issuer_candidates": intervals,
          "related_issuer_records": issuer_rows,
          "exceptions": [], "summary": {"issuer_candidate_rows": 2}}
    return wi, ii, manifest


def _saved_synthetic_bundle(base: Path) -> tuple[Path, Path, dict, dict]:
    wi, ii, manifest = _synthetic_sources(base / "raw")
    src = manifest["registered_source_files"]
    wrds_dir, issuer_dir = base / "wrds", base / "issuer"
    wrds_dir.mkdir()
    issuer_dir.mkdir()
    wrds_code_hash = ledger.sha256_file(ROOT / "scripts/audit_m5_wrds_intake.py")
    issuer_code_hash = ledger.sha256_file(ROOT / "scripts/audit_m5_issuer_history.py")
    wr = {
        "local_snapshot": {**src["SECURITY_STARTING"], "row_count": 1},
        "local_changes": {**src["SECURITY_LATER"], "row_count": 2},
        "local_links": {**src["CIK_CUSIP"], "row_count": 2},
        "query_sql_path": src["SECURITY_QUERY_SQL"]["path"],
        "query_sql_sha256": src["SECURITY_QUERY_SQL"]["sha256"],
        "saved_v2_receipt": src["SECURITY_V2_RECEIPT"],
        "intake_script_sha256": wrds_code_hash,
        "recorded_at_utc": "2026-01-01T00:00:00+00:00",
        "provider_release_vintage": None,
        "license_or_export_terms_verified": False,
    }
    wi["source_bindings"] = {
        "snapshot_sha256": src["SECURITY_STARTING"]["sha256"],
        "changes_sha256": src["SECURITY_LATER"]["sha256"],
        "links_sha256": src["CIK_CUSIP"]["sha256"],
        "v2_receipt_sha256": src["SECURITY_V2_RECEIPT"]["sha256"],
        "intake_script_sha256": wrds_code_hash}
    ws = {**wi["source_bindings"], "history_interval_count": 2,
          "interval_candidate_rows": 3}
    wrds_hashes = {
        "registry": _write_json(wrds_dir / ledger.WRDS_FILES["registry"], wr),
        "index": _write_json(wrds_dir / ledger.WRDS_FILES["index"], wi),
        "summary": _write_json(wrds_dir / ledger.WRDS_FILES["summary"], ws),
    }
    ir = {
        "formal_m5_cohort_selected": False,
        "prior_artifacts": {"sha256": wrds_hashes},
        "issuer_sources": {
            role: {"csv_path": src[f"ISSUER_{role}"]["path"],
                   "sha256": src[f"ISSUER_{role}"]["sha256"],
                   "sql_path": src[f"ISSUER_{role}_SQL"]["path"],
                   "sql_sha256": src[f"ISSUER_{role}_SQL"]["sha256"],
                   "row_count": 2 if role == "EARLY" else 0}
            for role in ("EARLY", "LATER")},
        "prior_source_sha256": {
            "STARTING_SNAPSHOT": src["SECURITY_STARTING"]["sha256"],
            "LATER_CHANGES": src["SECURITY_LATER"]["sha256"]},
        "prior_links_sha256": src["CIK_CUSIP"]["sha256"],
        "prior_v2_receipt_sha256": src["SECURITY_V2_RECEIPT"]["sha256"],
        "prior_intake_script_sha256": wrds_code_hash,
        "issuer_audit_script_sha256": issuer_code_hash,
        "recorded_at_utc": "2026-01-02T00:00:00+00:00",
        "downloaded_at_utc": None,
        "provider_release_vintage": None,
        "license_or_export_terms_verified": False}
    iss = {"formal_m5_cohort_selected": False,
           "prior_artifact_sha256": wrds_hashes,
           "issuer_csv_sha256": {role: ir["issuer_sources"][role]["sha256"]
                                   for role in ("EARLY", "LATER")},
           "query_sql_sha256": {role: ir["issuer_sources"][role]["sql_sha256"]
                                   for role in ("EARLY", "LATER")},
           "issuer_audit_script_sha256": issuer_code_hash,
           "issuer_candidate_rows": 2}
    issuer_hashes = {
        "registry": _write_json(issuer_dir / ledger.ISSUER_FILES["registry"], ir),
        "index": _write_json(issuer_dir / ledger.ISSUER_FILES["index"], ii),
        "summary": _write_json(issuer_dir / ledger.ISSUER_FILES["summary"], iss),
    }
    return wrds_dir, issuer_dir, wrds_hashes, issuer_hashes


def test_parallel_edges_keep_unknowns_and_later_only(tmp_path: Path) -> None:
    wi, ii, manifest = _synthetic_sources(tmp_path)
    result, summary = ledger.build_ledger(wi, ii, manifest)
    assert len(result["issue_evidence"]) == 2
    assert len(result["identity_candidate"]) == 5
    assert summary["cik_observation_edge_count"] == 3
    assert summary["issuer_intersection_edge_count"] == 2
    assert summary["later_only_interval_count"] == 1
    assert result["later_only_security_evidence"][0]["formal_cohort_member"] is False
    assert all(row["primary_security"] is row["mic"] is row["eligible"] is None
               for row in result["issue_evidence"])
    assert all(row["accepted"] is False for row in result["identity_candidate"])
    cik = next(row for row in result["identity_candidate"]
               if row["candidate_kind"] == "CIK_CUSIP_OBSERVATION")
    assert cik["observed_filing_dates"]["date_semantics"] == "OBSERVATION_TIME"
    assert cik["historical_valid_from"] is None
    assert cik["observed_filing_dates"]["start"] == "2019-01-01"
    assert cik["raw_identifier_refs"][0]["raw_field_name"] == "cik"
    issuer = next(row for row in result["identity_candidate"]
                  if row["candidate_kind"] == "SECURITY_ISSUER_CLOSED_DATE_INTERSECTION")
    assert issuer["closed_date_diagnostic_intersection"]["date_semantics"] == "UNKNOWN"
    assert issuer["closed_date_diagnostic_intersection"]["comparison_method"] == \
        "CLOSED_DATE_DIAGNOSTIC_ONLY"
    assert summary["distinct_affected_source_record_count"] == 2
    assert summary["exception_record_count"] > summary["distinct_affected_source_record_count"]


def test_date_and_interval_binding_failures(tmp_path: Path) -> None:
    wi, ii, manifest = _synthetic_sources(tmp_path)
    with pytest.raises(ledger.AuditError, match="CANDIDATE_DATE_RANGE_REVERSED"):
        ledger._time_evidence("2021-02-01", "2021-01-01", "VALID_TIME")
    assert ledger._time_evidence(None, "2021-01-01", "UNKNOWN")["start_boundary"] == "UNKNOWN"
    bad = copy.deepcopy(ii)
    bad["security_interval_issuer_candidates"][0]["normalized_row_sha256"] = "f" * 64
    with pytest.raises(ledger.AuditError, match="SECURITY_ISSUER_INTERVAL_BINDING_MISMATCH"):
        ledger.build_ledger(wi, bad, manifest)
    bad = copy.deepcopy(wi)
    bad["later_only_security_intervals"][0]["source_record"] = 1
    with pytest.raises(ledger.AuditError, match="LATER_ONLY_SOURCE_REFERENCE_INVALID"):
        ledger.build_ledger(bad, ii, manifest)
    reversed_observation = copy.deepcopy(wi)
    edge = reversed_observation["starting_securities"][0]["history"][0]["candidates"][0]
    edge["first_filing_observation"] = "2022-01-02"
    edge["issues"] = ["LINK_OBSERVATION_DATES_REVERSED"]
    result, _ = ledger.build_ledger(reversed_observation, ii, manifest)
    observed = result["identity_candidate"][0]["observed_filing_dates"]
    assert observed["start"] is observed["end"] is None
    assert observed["source_first_filing_observation"] == "2022-01-02"
    assert observed["observation_date_order_status"] == "REVERSED_SOURCE_DATES_REVIEW_REQUIRED"


def test_exception_only_source_record_refs_are_retained(tmp_path: Path) -> None:
    wi, ii, manifest = _synthetic_sources(tmp_path)
    manifest["prior_receipt_raw_row_counts"]["CIK_CUSIP"] = 3
    manifest["prior_receipt_raw_row_counts"]["ISSUER_EARLY"] = 3
    wi["exceptions"].append({"link_source_record": 3,
                             "codes": ["SYNTHETIC_UNMATCHED_LINK_REVIEW"]})
    ii["exceptions"].append({"source_records": [["EARLY", 3]],
                             "codes": ["SYNTHETIC_UNMATCHED_ISSUER_REVIEW"]})
    result, summary = ledger.build_ledger(wi, ii, manifest)
    refs = {item["source_record_id"] for item in result["source_record"]}
    assert {"CIK_CUSIP:3", "ISSUER:EARLY:3"} <= refs
    assert all(set(item["affected_source_record_refs"]) <= refs
               for item in result["identity_exception"])
    assert summary["retained_source_record_counts"]["CIK_CUSIP_EXCEPTION_SOURCE"] == 1
    manifest["prior_receipt_raw_row_counts"]["CIK_CUSIP"] = 2
    with pytest.raises(ledger.AuditError, match="SOURCE_RECORD_OUTSIDE_PRIOR_ROW_COUNT"):
        ledger.build_ledger(wi, ii, manifest)


def test_saved_hashes_source_change_and_import_shadow(tmp_path: Path, monkeypatch) -> None:
    wrds_dir, issuer_dir, wh, ih = _saved_synthetic_bundle(tmp_path)
    wi, ii, manifest = ledger.load_saved(
        wrds_dir, issuer_dir, wrds_hashes=wh, issuer_hashes=ih)
    assert len(wi["starting_securities"]) == 1
    assert len(ii["security_interval_issuer_candidates"]) == 2
    assert manifest["prior_receipt_raw_row_counts"]["SECURITY_LATER"] == 2
    source = Path(manifest["registered_source_files"]["SECURITY_STARTING"]["path"])
    source.write_bytes(source.read_bytes() + b"tamper")
    with pytest.raises(ledger.AuditError, match="REGISTERED_SOURCE_HASH_MISMATCH"):
        ledger.load_saved(wrds_dir, issuer_dir, wrds_hashes=wh, issuer_hashes=ih)
    source.write_bytes(source.read_bytes()[:-6])
    monkeypatch.setattr(ledger.wrds_audit, "__file__", str(tmp_path / "shadow.py"))
    with pytest.raises(ledger.AuditError, match="IMPORTED_AUDIT_MODULE_PATH_MISMATCH"):
        ledger.load_saved(wrds_dir, issuer_dir, wrds_hashes=wh, issuer_hashes=ih)


def test_run_reverify_and_tamper_rejection() -> None:
    (ROOT / "artifacts").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="m5-ledger-synthetic-", dir=ROOT / "artifacts") as name:
        base = Path(name)
        wrds_dir, issuer_dir, wh, ih = _saved_synthetic_bundle(base)
        output = base / "output"
        summary = ledger.run(wrds_dir, issuer_dir, output,
                             wrds_hashes=wh, issuer_hashes=ih)
        assert summary["status"] == "CANDIDATE_IDENTITY_LEDGER_BUILT_NOT_ADMITTED"
        verified = ledger.verify_output(output, wrds_hashes=wh, issuer_hashes=ih)
        assert verified["identity_candidate_edge_count"] == 5
        with pytest.raises(ledger.AuditError, match="OUTPUT_ALREADY_EXISTS"):
            ledger.run(wrds_dir, issuer_dir, output,
                       wrds_hashes=wh, issuer_hashes=ih)
        extra = output / "unexpected.txt"
        extra.write_text("SYNTHETIC_TEST_ONLY", encoding="ascii")
        with pytest.raises(ledger.AuditError, match="OUTPUT_FILE_SET_MISMATCH"):
            ledger.verify_output(output, wrds_hashes=wh, issuer_hashes=ih)
        extra.unlink()
        saved_registry = json.loads((output / ledger.OUTPUT_FILES["registry"]).read_text(
            encoding="utf-8"))
        registered_source = Path(saved_registry["source_manifest"]["registered_source_files"]
                                 ["SECURITY_STARTING"]["path"])
        before = registered_source.read_bytes()
        registered_source.write_bytes(before + b"tamper")
        with pytest.raises(ledger.AuditError, match="REGISTERED_SOURCE_HASH_MISMATCH"):
            ledger.verify_output(output, wrds_hashes=wh, issuer_hashes=ih)
        registered_source.write_bytes(before)
        index = output / ledger.OUTPUT_FILES["index"]
        index.write_bytes(index.read_bytes() + b"\n")
        with pytest.raises(ledger.AuditError, match="SAVED_OUTPUT_HASH_MISMATCH"):
            ledger.verify_output(output, wrds_hashes=wh, issuer_hashes=ih)


def test_saved_logical_change_rejected_after_hash_manifest_rewrite() -> None:
    (ROOT / "artifacts").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="m5-ledger-synthetic-", dir=ROOT / "artifacts") as name:
        base = Path(name)
        wrds_dir, issuer_dir, wh, ih = _saved_synthetic_bundle(base)
        output = base / "output"
        ledger.run(wrds_dir, issuer_dir, output, wrds_hashes=wh, issuer_hashes=ih)
        index_path = output / ledger.OUTPUT_FILES["index"]
        summary_path = output / ledger.OUTPUT_FILES["summary"]
        manifest_path = output / ledger.OUTPUT_FILES["manifest"]
        index = json.loads(index_path.read_text(encoding="utf-8"))
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        index["identity_candidate"][0]["cik"] = "9999999999"
        index_path.write_bytes(ledger.json_bytes(index))
        summary["ledger_sha256"] = ledger.sha256_file(index_path)
        summary_path.write_bytes(ledger.json_bytes(summary))
        for path in (index_path, summary_path):
            manifest["files"][path.name] = {
                "sha256": ledger.sha256_file(path), "bytes": path.stat().st_size}
        manifest_path.write_bytes(ledger.json_bytes(manifest))
        with pytest.raises(ledger.AuditError, match="SAVED_LEDGER_RECOMPUTATION_MISMATCH"):
            ledger.verify_output(output, wrds_hashes=wh, issuer_hashes=ih)


def test_candidate_hash_change_blocks_before_output(tmp_path: Path, monkeypatch) -> None:
    wrds_dir, issuer_dir, wh, ih = _saved_synthetic_bundle(tmp_path)
    monkeypatch.setattr(ledger, "M5_CANDIDATE_SHA256", "0" * 64)
    with pytest.raises(ledger.AuditError, match="M5_CANDIDATE_HASH_MISMATCH"):
        ledger.load_saved(wrds_dir, issuer_dir, wrds_hashes=wh, issuer_hashes=ih)


def test_logical_draft_contract_hash_change_blocks_before_output(
        tmp_path: Path, monkeypatch) -> None:
    wrds_dir, issuer_dir, wh, ih = _saved_synthetic_bundle(tmp_path)
    monkeypatch.setattr(ledger, "LOGICAL_DRAFT_CONTRACT_SHA256", "0" * 64)
    with pytest.raises(ledger.AuditError, match="LOGICAL_DRAFT_CONTRACT_HASH_MISMATCH"):
        ledger.load_saved(wrds_dir, issuer_dir, wrds_hashes=wh, issuer_hashes=ih)
