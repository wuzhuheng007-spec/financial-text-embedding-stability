"""Synthetic-only checks for candidate-driven date coverage, not return labels."""

from __future__ import annotations

import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_m5_horizon_round8.py"
SPEC = importlib.util.spec_from_file_location("audit_m5_horizon_round8", SCRIPT)
assert SPEC and SPEC.loader
coverage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(coverage)


def session_fixture(count: int = 30) -> list[str]:
    """Ordered synthetic session labels; no real exchange-calendar assertion."""
    return [(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(count)]


def write_json(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    return path


def test_twenty_and_twenty_one_have_distinct_fixed_endpoints():
    axis = session_fixture(30)
    available = set(axis)
    twenty = coverage.coverage_on_reference_dates(axis, available, axis[0], 20, 1)
    twenty_one = coverage.coverage_on_reference_dates(axis, available, axis[0], 21, 1)
    assert twenty["reference_start_date"] == axis[1]
    assert twenty["reference_end_date"] == axis[21]
    assert twenty_one["reference_end_date"] == axis[22]
    assert twenty["reference_end_date"] != twenty_one["reference_end_date"]


def test_twenty_one_is_blocked_when_reference_axis_is_short():
    axis = session_fixture(22)
    result = coverage.coverage_on_reference_dates(axis, set(axis), axis[0], 21, 1)
    assert result["reference_end_date"] is None
    assert result["structural_pair_complete"] is False
    assert result["reason"] == "REFERENCE_AXIS_END_BEFORE_HORIZON"


@pytest.mark.parametrize("bad", [0, -1, 1.5, True, "5", None])
def test_nonpositive_or_noninteger_horizon_rejected(bad):
    with pytest.raises(coverage.CoverageError, match="positive integer"):
        coverage.valid_horizon(bad)


def test_missing_price_does_not_move_the_declared_endpoint():
    axis = session_fixture(12)
    full = coverage.coverage_on_reference_dates(axis, set(axis), axis[0], 5, 1)
    missing = coverage.coverage_on_reference_dates(axis, set(axis) - {axis[4]}, axis[0], 5, 1)
    assert full["reference_end_date"] == axis[6]
    assert missing["reference_end_date"] == axis[6]
    assert missing["missing_window_price_dates"] == [axis[4]]
    assert missing["structural_pair_complete"] is False


def test_missing_endpoint_and_invalid_filing_date_are_rejected_or_reported():
    axis = session_fixture(12)
    result = coverage.coverage_on_reference_dates(axis, set(axis) - {axis[6]}, axis[0], 5, 1)
    assert result["reference_end_date"] == axis[6]
    assert result["missing_window_price_dates"] == [axis[6]]
    assert result["structural_pair_complete"] is False
    with pytest.raises(coverage.CoverageError, match="Filing date"):
        coverage.coverage_on_reference_dates(axis, set(axis), "2025-02-30", 5, 1)
    with pytest.raises(coverage.CoverageError, match="Filing date"):
        coverage.coverage_on_reference_dates(axis, set(axis), "20250101", 5, 1)
    with pytest.raises(coverage.CoverageError, match="Reference dates"):
        coverage.coverage_on_reference_dates(["20250101", *axis[1:]], set(axis), axis[0], 5, 1)


def fixture_files(tmp_path: Path) -> tuple[Path, Path, Path]:
    axis = session_fixture(30)
    candidate = write_json(tmp_path / "candidate.json", {
        "status": "PROPOSED", "researcher_accepted": False,
        "formal_financial_experiment_status": "NOT_RUN",
        "decision_and_label": {"primary_horizon_subsequent_sessions": 5,
                               "alternative_horizon_subsequent_sessions": 21},
        "features": {"minimum_prior_closes_including_d": 1},
    })
    m4_run = tmp_path / "m4"
    masks = write_json(m4_run / "common_masks.json", {
        "date_order": axis, "ticker_order": ["TEST"]})
    bars = write_json(m4_run / "market" / "bars_TEST.json", {
        "ticker": "TEST", "adjusted": True,
        "bars": [{"date_et": day, "c": 10.0} for day in axis]})
    data_audit = write_json(tmp_path / "audit.json", {
        "status": "LOCAL_AVAILABILITY_AUDIT_ONLY_NO_FINANCIAL_EXPERIMENT",
        "sources": {"m4_common_masks_sha256": coverage.sha256(masks)},
        "firms": [{"ticker": "TEST", "accession": "SYNTHETIC_TEST_ONLY",
                   "official_filing_date": axis[0],
                   "saved_price": {"processed_sha256": coverage.sha256(bars)}}],
    })
    return candidate, data_audit, m4_run


def test_cli_audit_reads_candidate_horizons_and_never_admits_financial_rows(tmp_path: Path):
    candidate, data_audit, m4_run = fixture_files(tmp_path)
    result = coverage.audit(candidate, data_audit, m4_run)
    assert set(result["horizons"]) == {"5", "21"}
    assert result["horizons"]["5"]["firms"][0]["reference_end_date"] != result["horizons"]["21"]["firms"][0]["reference_end_date"]
    assert result["horizons"]["5"]["independent_calendar_confirmed_pairs"] is None
    assert result["horizons"]["21"]["admitted_financial_observations"] is None
    assert result["horizons"]["5"]["structural_pairs_with_predecision_lookback"] == 1
    assert result["returns_or_scores_computed"] is False


def test_bound_price_bytes_and_candidate_status_fail_closed(tmp_path: Path):
    candidate, data_audit, m4_run = fixture_files(tmp_path)
    bars = m4_run / "market" / "bars_TEST.json"
    bars.write_text(bars.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(coverage.CoverageError, match="SHA-256 mismatch"):
        coverage.audit(candidate, data_audit, m4_run)
    candidate_data = json.loads(candidate.read_text(encoding="utf-8"))
    candidate_data["researcher_accepted"] = True
    write_json(candidate, candidate_data)
    with pytest.raises(coverage.CoverageError, match="unaccepted"):
        coverage.audit(candidate, data_audit, m4_run)


def test_changed_common_date_axis_is_rejected_before_pairing(tmp_path: Path):
    candidate, data_audit, m4_run = fixture_files(tmp_path)
    masks = m4_run / "common_masks.json"
    masks.write_text(masks.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(coverage.CoverageError, match="SHA-256 mismatch"):
        coverage.audit(candidate, data_audit, m4_run)


def test_round8_candidate_retains_round7_design_and_declares_5_21():
    root = Path(__file__).resolve().parents[1]
    old_path = root / "configs/m5_financial_protocol_round7.json"
    new_path = root / "configs/m5_financial_protocol_round8.json"
    assert coverage.sha256(old_path) == "f49c3800267250a21928c71e2aa0c82dc778195c82a02ec0722e05c59dc38c15"
    old = json.loads(old_path.read_text(encoding="utf-8"))
    new = json.loads(new_path.read_text(encoding="utf-8"))
    assert new["supersedes_proposed_candidate_sha256"] == coverage.sha256(old_path)
    assert new["decision_and_label"]["primary_horizon_subsequent_sessions"] == 5
    assert new["decision_and_label"]["alternative_horizon_subsequent_sessions"] == 21
    for key in ("sample", "time_interpretation", "chronology"):
        assert new[key] == old[key]
    for key in ("alpha_grid", "planned_validation_fits", "planned_post_selection_refits"):
        assert new["prediction_arms"][key] == old["prediction_arms"][key]
    assert new["status"] == "PROPOSED"
    assert new["researcher_accepted"] is False
    assert new["formal_financial_experiment_status"] == "NOT_RUN"
    assert "d through d+h" in new["date_coverage_contract"]["required_future_window_prices"]
    assert new["features"]["lexical"]["svd"]["centering"] is False
    assert new["features"]["dense"]["pca"]["components"] == 32
    assert "rcond=None" in new["prediction_arms"]["numeric_contract"]["solver"]
    assert new["prediction_arms"]["numeric_contract"]["alpha_loss_scale"].startswith("sum")
