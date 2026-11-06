"""Synthetic-only structural checks; no historical database or financial labels."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import itertools
import json
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_m5_import_round9.py"
SPEC = importlib.util.spec_from_file_location("validate_m5_import_round9", SCRIPT)
assert SPEC and SPEC.loader
contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contract)


def synthetic_bundle() -> dict:
    source = {"source_id": "SYNTHETIC_TEST_ONLY", "product": "SYNTHETIC_TEST_ONLY",
              "product_version": "SYNTHETIC_TEST_ONLY", "official_url": "https://example.invalid/synthetic",
              "acquired_at_utc": "2026-11-01T00:00:00Z", "local_file_sha256": "a" * 64,
              "access_basis": "SYNTHETIC_TEST_ONLY"}
    bar = {"stable_security_id": "SYNTHETIC_SECURITY", "session_date": "2025-01-02",
           "close": 10.0, "currency": "USD", "price_definition": "regular_close",
           "adjustment_basis": "split_only", "action_source_id": "SYNTHETIC_TEST_ONLY",
           "missing_reason": None, "exit_marker": False, "source_id": "SYNTHETIC_TEST_ONLY",
           "source_record_version": "SYNTHETIC_TEST_ONLY", "retrieved_at_utc": "2026-11-01T00:00:00Z"}
    later_bar = dict(bar, session_date="2025-01-03", close=11.0)
    return {"evidence_class": "SYNTHETIC_TEST_ONLY", "sources": [source],
            "security_master": [{"stable_security_id": "SYNTHETIC_SECURITY",
                                 "firm_id": "SYNTHETIC_FIRM", "security_type": "common_share",
                                 "mic": "XNYS", "listing_status": "listed",
                                 "valid_from": "2020-01-01", "valid_to": "2025-01-04",
                                 "source_id": "SYNTHETIC_TEST_ONLY"}],
            "cik_security_links": [{"cik": "0000000000", "stable_security_id": "SYNTHETIC_SECURITY",
                                    "valid_from": "2020-01-01", "valid_to": "2025-01-04",
                                    "is_primary": True, "primary_basis": "SYNTHETIC_TEST_ONLY",
                                    "ambiguity_flag": False, "source_id": "SYNTHETIC_TEST_ONLY"}],
            "daily_bars": [bar, later_bar],
            "actions_and_exits": [{"stable_security_id": "SYNTHETIC_SECURITY",
                                   "event_date": "2025-01-03", "event_type": "DELISTING",
                                   "event_value": None, "event_value_unit": None,
                                   "terminal_price": None,
                                   "missing_terminal_reason": "SYNTHETIC_TEST_ONLY_UNAVAILABLE",
                                   "source_id": "SYNTHETIC_TEST_ONLY",
                                   "source_record_version": "SYNTHETIC_TEST_ONLY",
                                   "retrieved_at_utc": "2026-11-01T00:00:00Z"}],
            "text_evidence": [{"cik": "0000000000", "accession": "SYNTHETIC_TEST_ONLY",
                               "section": "Item 1 Business", "section_sha256": "b" * 64,
                               "revision_status": "SYNTHETIC_TEST_ONLY",
                               "public_time_evidence_class": "UNKNOWN", "public_time_utc": None,
                               "public_no_later_than_utc": None, "proxy_rule_id": None,
                               "source_id": "SYNTHETIC_TEST_ONLY",
                               "source_record_version": "SYNTHETIC_TEST_ONLY",
                               "retrieved_at_utc": "2026-11-01T00:00:00Z"}],
            "required_security_sessions": {"SYNTHETIC_SECURITY": ["2025-01-02", "2025-01-03"]}}


def codes(result: dict) -> set[str]:
    return {entry["code"] for entry in result["issues"]}


def multiclass_bundle() -> dict:
    bundle = synthetic_bundle()
    primary = bundle["cik_security_links"][0]
    for security_id in ("SYNTHETIC_CLASS_B", "SYNTHETIC_CLASS_C"):
        master = copy.deepcopy(bundle["security_master"][0])
        master["stable_security_id"] = security_id
        bundle["security_master"].append(master)
        link = copy.deepcopy(primary)
        link.update(stable_security_id=security_id, is_primary=False, primary_basis=None)
        bundle["cik_security_links"].append(link)
    return bundle


def test_one_documented_primary_with_two_nonprimary_classes_passes_in_every_order():
    bundle = multiclass_bundle()
    expected = contract.validate_import_bundle(bundle)
    assert expected["status"] == "PASS_STRUCTURE_ONLY"
    assert expected["issues"] == []
    assert expected["admitted_financial_observations"] is None
    assert expected["provenance_or_license_verified"] is False
    for order in itertools.permutations(bundle["cik_security_links"]):
        reordered = copy.deepcopy(bundle)
        reordered["cik_security_links"] = list(order)
        assert contract.validate_import_bundle(reordered) == expected


def test_valid_synthetic_structure_preserves_exit_and_admits_no_finance():
    result = contract.validate_import_bundle(synthetic_bundle())
    assert result["status"] == "PASS_STRUCTURE_ONLY"
    assert result["exit_records_seen"] == 1
    assert result["admitted_financial_observations"] is None
    assert result["returns_or_scores_computed"] is False
    assert result["provenance_or_license_verified"] is False


def test_effective_intervals_are_half_open_and_overlaps_fail():
    bundle = synthetic_bundle()
    adjacent = copy.deepcopy(bundle["security_master"][0])
    adjacent.update(valid_from="2025-01-04", valid_to="2026-01-01")
    bundle["security_master"].append(adjacent)
    assert "OVERLAPPING_SECURITY_INTERVAL" not in codes(contract.validate_import_bundle(bundle))
    bundle["security_master"][1]["valid_from"] = "2025-01-03"
    assert "OVERLAPPING_SECURITY_INTERVAL" in codes(contract.validate_import_bundle(bundle))
    duplicate_link = copy.deepcopy(bundle["cik_security_links"][0])
    bundle["cik_security_links"].append(duplicate_link)
    assert "OVERLAPPING_CIK_SECURITY_LINK" in codes(contract.validate_import_bundle(bundle))


def test_multiple_primary_classes_reported_without_choosing_one():
    bundle = multiclass_bundle()
    bundle["cik_security_links"][1].update(is_primary=True,
                                          primary_basis="SYNTHETIC_TEST_ONLY")
    result = contract.validate_import_bundle(bundle)
    assert result["status"] == "REJECTED_STRUCTURE_ONLY"
    assert "MULTIPLE_PRIMARY_SECURITIES" in codes(result)
    detail = next(item["detail"] for item in result["issues"]
                  if item["code"] == "MULTIPLE_PRIMARY_SECURITIES")
    assert "CIK 0000000000" in detail
    assert "[2020-01-01, 2025-01-04)" in detail
    assert "SYNTHETIC_CLASS_B" in detail
    assert "SYNTHETIC_SECURITY" in detail


def test_all_nonprimary_classes_report_one_grouped_gap():
    bundle = multiclass_bundle()
    bundle["cik_security_links"][0].update(is_primary=False, primary_basis=None)
    result = contract.validate_import_bundle(bundle)
    gaps = [item for item in result["issues"] if item["code"] == "NO_PRIMARY_SECURITY"]
    assert result["status"] == "REJECTED_STRUCTURE_ONLY"
    assert len(gaps) == 1
    assert "CIK 0000000000" in gaps[0]["detail"]
    assert "[2020-01-01, 2025-01-04)" in gaps[0]["detail"]
    assert "SYNTHETIC_CLASS_B" in gaps[0]["detail"]
    assert "SYNTHETIC_CLASS_C" in gaps[0]["detail"]
    assert "AMBIGUOUS_CONCURRENT_SECURITIES" not in codes(result)


def test_single_nonprimary_and_later_uncovered_primary_segment_fail_closed():
    bundle = synthetic_bundle()
    link = bundle["cik_security_links"][0]
    link["is_primary"] = False
    link["primary_basis"] = None
    assert "NO_PRIMARY_SECURITY" in codes(contract.validate_import_bundle(bundle))
    link["is_primary"] = True
    link["primary_basis"] = "SYNTHETIC_TEST_ONLY"
    link["valid_to"] = "2022-01-01"
    later = copy.deepcopy(link)
    later.update(valid_from="2022-01-01", valid_to="2025-01-04",
                 is_primary=False, primary_basis=None)
    bundle["cik_security_links"].append(later)
    result = contract.validate_import_bundle(bundle)
    assert "NO_PRIMARY_SECURITY" in codes(result)
    detail = next(item["detail"] for item in result["issues"]
                  if item["code"] == "NO_PRIMARY_SECURITY")
    assert "[2022-01-01, 2025-01-04)" in detail


def test_adjacent_primary_handoff_with_concurrent_nonprimary_class_passes():
    bundle = multiclass_bundle()
    first, later, nonprimary = bundle["cik_security_links"]
    first["valid_to"] = "2022-01-01"
    later.update(valid_from="2022-01-01", is_primary=True,
                 primary_basis="SYNTHETIC_TEST_ONLY")
    assert nonprimary["is_primary"] is False
    result = contract.validate_import_bundle(bundle)
    assert result["status"] == "PASS_STRUCTURE_ONLY"
    assert result["issues"] == []


def test_open_ended_classes_pass_but_duplicate_link_remains_rejected():
    bundle = multiclass_bundle()
    for row in bundle["security_master"] + bundle["cik_security_links"]:
        row["valid_to"] = None
    assert contract.validate_import_bundle(bundle)["status"] == "PASS_STRUCTURE_ONLY"
    bundle["cik_security_links"].append(copy.deepcopy(bundle["cik_security_links"][1]))
    result = contract.validate_import_bundle(bundle)
    assert "OVERLAPPING_CIK_SECURITY_LINK" in codes(result)
    assert result["status"] == "REJECTED_STRUCTURE_ONLY"


def test_primary_basis_flag_and_link_source_errors_still_reject():
    bundle = multiclass_bundle()
    bundle["cik_security_links"][0]["primary_basis"] = None
    bundle["cik_security_links"][1]["source_id"] = "UNKNOWN_SYNTHETIC_SOURCE"
    bundle["cik_security_links"][2]["is_primary"] = "invalid_flag"
    result = contract.validate_import_bundle(bundle)
    assert result["status"] == "REJECTED_STRUCTURE_ONLY"
    assert {"MISSING_PRIMARY_BASIS", "NO_PRIMARY_SECURITY", "UNKNOWN_SOURCE_ID",
            "INVALID_PRIMARY_FLAG"} <= codes(result)
    assert result["admitted_financial_observations"] is None
    assert result["provenance_or_license_verified"] is False


def test_missing_identity_field_and_unknown_source_reported():
    bundle = synthetic_bundle()
    del bundle["security_master"][0]["mic"]
    bundle["daily_bars"][0]["source_id"] = "UNVERIFIED_SOURCE"
    assert {"MISSING_FIELDS", "UNKNOWN_SOURCE_ID"} <= codes(contract.validate_import_bundle(bundle))
    bundle["security_master"][0]["firm_id"] = None
    assert "MISSING_REQUIRED_VALUE" in codes(contract.validate_import_bundle(bundle))


def test_malformed_identifier_types_are_reported_not_crashes():
    bundle = synthetic_bundle()
    bundle["daily_bars"][0]["stable_security_id"] = ["not-an-id"]
    bundle["daily_bars"][0]["source_id"] = ["not-a-source"]
    result = contract.validate_import_bundle(bundle)
    assert result["status"] == "REJECTED_STRUCTURE_ONLY"
    assert {"INVALID_SECURITY_ID", "INVALID_SOURCE_REFERENCE"} <= codes(result)


def test_missing_required_session_is_reported_without_fill_or_shift():
    bundle = synthetic_bundle()
    bundle["daily_bars"].pop(0)
    result = contract.validate_import_bundle(bundle)
    assert "MISSING_SESSION_BAR" in codes(result)
    assert result["record_counts"]["daily_bars"] == 1
    assert result["exit_records_seen"] == 1


def test_null_required_close_is_reported_even_with_a_missing_reason():
    bundle = synthetic_bundle()
    bundle["daily_bars"][0]["close"] = None
    bundle["daily_bars"][0]["missing_reason"] = "SYNTHETIC_TEST_ONLY_UNAVAILABLE"
    assert "MISSING_REQUIRED_CLOSE" in codes(contract.validate_import_bundle(bundle))


def test_duplicate_and_conflicting_bars_are_rejected():
    bundle = synthetic_bundle()
    bundle["daily_bars"].append(copy.deepcopy(bundle["daily_bars"][0]))
    assert "DUPLICATE_BAR" in codes(contract.validate_import_bundle(bundle))
    other_source = copy.deepcopy(bundle["sources"][0])
    other_source["source_id"] = "SECOND_SYNTHETIC_TEST_ONLY"
    bundle["sources"].append(other_source)
    bundle["daily_bars"][-1]["source_id"] = other_source["source_id"]
    assert "CONFLICTING_BAR_SOURCES" in codes(contract.validate_import_bundle(bundle))


def test_unknown_exit_value_needs_reason_but_exit_record_remains():
    bundle = synthetic_bundle()
    bundle["actions_and_exits"][0]["missing_terminal_reason"] = None
    result = contract.validate_import_bundle(bundle)
    assert "MISSING_EXIT_TERMINAL_REASON" in codes(result)
    assert result["exit_records_seen"] == 1


def test_zero_exit_terminal_requires_explicit_source_not_fill():
    bundle = synthetic_bundle()
    event = bundle["actions_and_exits"][0]
    event["terminal_price"] = 0
    event["missing_terminal_reason"] = None
    assert "ZERO_TERMINAL_NEEDS_SOURCE" in codes(contract.validate_import_bundle(bundle))
    event["terminal_zero_evidence_id"] = "SYNTHETIC_TEST_ONLY_PROOF"
    assert "ZERO_TERMINAL_NEEDS_SOURCE" not in codes(contract.validate_import_bundle(bundle))


def test_public_time_claim_and_hash_require_evidence():
    bundle = synthetic_bundle()
    bundle["text_evidence"][0]["public_time_evidence_class"] = "EXACT_FIRST_PUBLIC"
    bundle["text_evidence"][0]["section_sha256"] = "not-a-hash"
    assert {"MISSING_EXACT_PUBLIC_TIME", "INVALID_SECTION_HASH"} <= codes(
        contract.validate_import_bundle(bundle))


def test_cli_writes_structure_only_receipt_for_synthetic_fixture(tmp_path: Path):
    source = tmp_path / "SYNTHETIC_TEST_ONLY.json"
    output = tmp_path / "receipt.json"
    source.write_text(json.dumps(synthetic_bundle()), encoding="utf-8")
    command = [sys.executable, str(SCRIPT), "--input", str(source), "--output", str(output)]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    assert completed.returncode == 0
    assert "PASS_STRUCTURE_ONLY" in completed.stdout
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["admitted_financial_observations"] is None
    assert result["input_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert result["validator_source_sha256"] == hashlib.sha256(SCRIPT.read_bytes()).hexdigest()
    repeated = subprocess.run(command, capture_output=True, text=True, check=False)
    assert repeated.returncode != 0
    assert output.exists()
