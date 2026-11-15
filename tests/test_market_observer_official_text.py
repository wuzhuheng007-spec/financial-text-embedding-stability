"""Focused checks for retrospective source coverage and time alignment."""

from datetime import date, datetime, timedelta, timezone
import hashlib
import json

import numpy as np
import pytest

from scripts.market_observer_official_v1.text_comparison import (
    exclude_cross_split_event_reuse, first_eligible_decision_session,
    fit_common_comparison, fit_tfidf, load_prevalidation_bars,
    load_sec_events, monthly_sec_coverage, sec_proxy_announcement_date,
    transform_tfidf,
)


def test_first_full_session_after_date_skips_half_day_and_weekend():
    sessions = [date(2025, 11, 26), date(2025, 11, 28),
                date(2025, 12, 1), date(2025, 12, 2)]
    full = {sessions[0], sessions[2], sessions[3]}
    assert first_eligible_decision_session(sessions[0], sessions, full) == sessions[3]
    assert first_eligible_decision_session(date(2025, 11, 29), sessions, full) == sessions[3]
    assert first_eligible_decision_session(sessions[3], sessions, full) is None


def test_sec_proxy_uses_later_filing_date_when_acceptance_et_prior_day():
    doc = {"sec_filing_date": "2025-06-26",
           "sec_acceptance_at": "2025-06-26T00:03:10.000Z",
           "announcement_date": "2025-06-26",
           "announcement_date_basis": "MAX_SEC_FILING_DATE_ACCEPTANCE_ET_DATE"}
    assert sec_proxy_announcement_date(doc) == date(2025, 6, 26)
    doc["announcement_date"] = "2025-06-25"
    with pytest.raises(ValueError, match="differs"):
        sec_proxy_announcement_date(doc)


def test_missing_and_partial_months_are_not_zero_news():
    rows = [{"source_kind": "SEC_FILINGS", "symbol": "NVDA", "month": "2025-04", "status": "COMPLETE"},
            {"source_kind": "SEC_FILINGS", "symbol": "NVDA", "month": "2025-05", "status": "INCOMPLETE"}]
    covered, gaps = monthly_sec_coverage(rows, "US.NVDA", [date(2025, 4, 30), date(2025, 5, 1)])
    assert not covered and gaps == ["2025-05:INCOMPLETE"]
    covered, gaps = monthly_sec_coverage(rows[:1], "US.NVDA", [date(2025, 5, 1)])
    assert not covered and gaps == ["2025-05:MISSING"]


def test_fixed_accession_group_places_exhibit_first_and_records_truncation(tmp_path):
    filing = tmp_path / "filing.txt"
    exhibit = tmp_path / "exhibit.txt"
    filing.write_text("A" * 50, encoding="utf-8")
    exhibit.write_text("B" * 50, encoding="utf-8")
    base = {"symbol": "NVDA", "event_id": "accession-1",
            "announcement_date": "2025-05-29", "sec_filing_date": "2025-05-29",
            "sec_acceptance_at": "2025-05-29T20:30:00Z",
            "announcement_date_basis": "MAX_SEC_FILING_DATE_ACCEPTANCE_ET_DATE",
            "version_status": "SEC_ARCHIVE_HASHED", "full_body_status": "FULL_BODY",
            "source_url": "https://www.sec.gov/Archives/edgar/data/1/1/a.htm",
            "sec_accession": "accession-1"}
    docs = [dict(base, document_id="f", source_kind="SEC_FILINGS",
                 body_path=str(filing), body_sha256=hashlib.sha256(filing.read_bytes()).hexdigest()),
            dict(base, document_id="e", source_kind="ISSUER_EARNINGS",
                 body_path=str(exhibit), body_sha256=hashlib.sha256(exhibit.read_bytes()).hexdigest())]
    events, problems = load_sec_events({"documents": docs},
                                      config={"event_text_max_characters": 20},
                                      latest_decision=date(2025, 5, 30))
    assert not problems and len(events) == 1
    assert events[0]["text"].startswith("b" * 10)
    assert events[0]["truncated_document_count"] == 2
    assert events[0]["document_count"] == 2


def test_validation_repeated_event_group_is_removed_from_common_sample():
    rows = [{"symbol": "US.NVDA", "label_day": "2026-03-31", "event_ids": ["A"]},
            {"symbol": "US.NVDA", "label_day": "2026-04-01", "event_ids": ["A"]},
            {"symbol": "US.NVDA", "label_day": "2026-04-02", "event_ids": ["B"]}]
    kept, removed = exclude_cross_split_event_reuse(rows)
    assert [r["label_day"] for r in kept] == ["2026-03-31", "2026-04-02"]
    assert removed[0]["reason"] == "EVENT_GROUP_CROSSES_TRAIN_VALIDATION"


def test_tfidf_vocabulary_and_idf_use_train_text_only():
    vocabulary, idf = fit_tfidf(["alpha alpha", "alpha beta"], max_features=8, min_df=1)
    assert "validationword" not in vocabulary
    train = transform_tfidf(["alpha alpha"], vocabulary, idf)
    validation = transform_tfidf(["validationword"], vocabulary, idf)
    assert np.linalg.norm(train) == pytest.approx(1.0)
    assert np.all(validation == 0)


def test_empty_event_validation_does_not_run_c_even_with_many_price_rows():
    rows = []
    start = date(2025, 4, 1)
    for i in range(32):
        rows.append({"label_day": (start + timedelta(days=i)).isoformat(),
                     "label_up": i % 2, "announcement_text": "test official text",
                     "announcement_count": 1, "sec_source_coverage_complete": True,
                     "price_features": [float(i)] * 6})
    rows.extend([{"label_day": f"2026-04-0{i}", "label_up": i % 2,
                  "announcement_text": "", "announcement_count": 0,
                  "sec_source_coverage_complete": True,
                  "price_features": [float(i)] * 6} for i in (1, 2)])
    with pytest.raises(ValueError, match="no event-present validation row"):
        fit_common_comparison(rows, {"minimum_validation_event_present_rows": 1})


def test_full_window_price_response_does_not_extract_test_ohlcv(tmp_path):
    config = {"start": "2025-04-01", "end": "2026-07-31", "ktype": 2,
              "autype": 1, "extended_time": 0,
              "symbols": ["US.TQQQ", "US.NVDA", "US.MU"]}
    (tmp_path / "run_config.json").write_text(json.dumps(config), encoding="utf-8")
    rows = [{"date": "20250401", "open": "10", "close": "11", "volume": "100"},
            {"date": "20260529", "open": "12", "close": "13", "volume": "120"},
            {"date": "20260601", "open": "TEST_SENTINEL", "close": "TEST_SENTINEL",
             "volume": "TEST_SENTINEL"}]
    payload = {"ret_code": 0, "pagination": {"has_more": False},
               "data": {"kline_list": rows}}
    (tmp_path / "US_NVDA_page1.raw.json").write_text(json.dumps(payload), encoding="utf-8")
    bars, receipt = load_prevalidation_bars(tmp_path, "US.NVDA")
    assert len(bars) == 2 and bars[-1].day == date(2026, 5, 29)
    assert len(receipt["source_sha256"]) == 64
