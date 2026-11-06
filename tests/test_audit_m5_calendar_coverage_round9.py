"""Synthetic-only checks for saved-row versus independent-calendar pairing."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("audit_m5_calendar_coverage_round9",
                                              SCRIPTS / "audit_m5_calendar_coverage_round9.py")
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def write_json(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    return path


def test_calendar_reference_keeps_missing_decision_session_and_reports_difference(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    candidate = Path(__file__).resolve().parents[1] / "configs/m5_financial_protocol_round8.json"
    assert audit.sha256(candidate) == audit.ROUND8_CANDIDATE_SHA256
    calendar_dates = [(date(2025, 1, 1) + timedelta(days=i)).isoformat()
                      for i in range(50)]  # SYNTHETIC_TEST_ONLY dates, not real sessions
    saved_dates = [value for value in calendar_dates if value != "2025-01-09"]
    m4 = tmp_path / "m4"
    masks = write_json(m4 / "common_masks.json", {
        "date_order": saved_dates, "ticker_order": ["TEST"]})
    bars = write_json(m4 / "market/bars_TEST.json", {
        "ticker": "TEST", "adjusted": True,
        "bars": [{"date_et": value, "c": 10.0} for value in saved_dates]})
    data_audit = write_json(tmp_path / "data_audit.json", {
        "sources": {"m4_common_masks_sha256": audit.sha256(masks)},
        "firms": [{"ticker": "TEST", "accession": "SYNTHETIC_TEST_ONLY",
                   "official_filing_date": "2025-01-08",
                   "saved_price": {"processed_sha256": audit.sha256(bars)}}]})
    monkeypatch.setattr(audit, "ROUND7_DATA_AUDIT_SHA256", audit.sha256(data_audit))
    old_rows = {}
    for horizon in (5, 21):
        old = audit.coverage_on_reference_dates(saved_dates, set(saved_dates),
                                                "2025-01-08", horizon, 21)
        old_rows[str(horizon)] = {"firms": [{"ticker": "TEST", **old}]}
    previous = write_json(tmp_path / "round8.json", {
        "candidate_sha256": audit.ROUND8_CANDIDATE_SHA256,
        "round7_data_audit_sha256": audit.sha256(data_audit),
        "horizons": old_rows})
    monkeypatch.setattr(audit, "ROUND8_RECEIPT_SHA256", audit.sha256(previous))
    rules = write_json(tmp_path / "rules.json", {"synthetic": True})
    calendar_dir = tmp_path / "calendar"
    write_json(calendar_dir / "manifest.json", {"synthetic": True})
    (calendar_dir / "sessions.csv").write_text("synthetic\n", encoding="utf-8")
    monkeypatch.setattr(audit, "verify_calendar_dir",
                        lambda *_: ([{"session_date": value} for value in calendar_dates],
                                    {"mic": "XNYS", "market_scope": "NYSE_EQUITIES_CORE"}))
    result = audit.compare(candidate, previous, data_audit, rules, calendar_dir, m4)
    row = result["horizons"]["5"]["firms"][0]
    assert row["xnys_baseline"]["reference_start_date"] == "2025-01-09"
    assert row["xnys_baseline"]["reference_end_date"] == calendar_dates[13]
    assert row["xnys_baseline"]["structural_pair_complete"] is False
    assert row["round8_saved_row_reference"]["reference_start_date"] == "2025-01-10"
    assert {difference["field"] for difference in row["differences"]} >= {
        "reference_start_date", "reference_end_date", "structural_pair_complete"}
    assert result["market_applicability_status"] == "BASELINE_ONLY_UNVERIFIED_EVENT_TIME_LISTING_MIC"
    assert result["horizons"]["5"]["admitted_financial_observations"] is None
    assert result["return_labels_or_scores_computed"] is False
    candidate_copy = tmp_path / "candidate.json"
    candidate_copy.write_bytes(candidate.read_bytes() + b" ")
    with pytest.raises(audit.ComparisonError, match="candidate bytes changed"):
        audit.compare(candidate_copy, previous, data_audit, rules, calendar_dir, m4)
