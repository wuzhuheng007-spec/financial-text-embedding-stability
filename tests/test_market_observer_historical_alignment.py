import json
from datetime import date

import pytest

from scripts.market_observer_v1.historical_alignment import align_event, align_events
from scripts.market_observer_v1.prepare_historical_events import convert


DAYS = [date(2026, 7, 30), date(2026, 7, 31)]
BASE = {
    "event_id": "source:event-1",
    "source_type": "OFFICIAL_FILING",
    "source_hash": "a" * 64,
    "published_at": "2026-07-30T16:02:01-04:00",
    "time_precision": "SECOND",
    "source_timezone": "America/New_York",
    "retrieved_at": "2026-11-15T05:50:00Z",
    "first_available_at": None,
}


def test_lags_publication_and_does_not_invent_first_availability():
    row = align_event(BASE, DAYS, end=DAYS[-1])
    assert row["decision_date"] == "2026-07-31"
    assert row["status"] == "PUBLICATION_PROXY_ONLY"
    assert row["strict_pit_eligible"] is False


def test_date_only_requires_declared_timezone_and_never_adds_intraday_time():
    event = {**BASE, "published_at": None, "published_date": "2026-07-30",
             "time_precision": "DATE", "source_timezone": "America/New_York"}
    assert align_event(event, DAYS, end=DAYS[-1])["decision_date"] == "2026-07-31"
    assert align_event({**event, "source_timezone": None}, DAYS,
                       end=DAYS[-1])["status"] == "UNALIGNED_TIME_UNCLEAR"


def test_no_future_session_is_fabricated():
    event = {**BASE, "published_at": "2026-07-31T08:00:00-04:00"}
    assert align_event(event, DAYS, end=DAYS[-1])["decision_date"] is None


def test_duplicate_event_identity_fails():
    with pytest.raises(ValueError, match="duplicate event"):
        align_events([BASE, BASE], DAYS, end=DAYS[-1])


def test_verified_first_availability_must_precede_cutoff():
    early = {**BASE, "first_available_at": "2026-07-30T20:10:00Z",
             "availability_evidence_hash": "b" * 64}
    assert align_event(early, DAYS, end=DAYS[-1])["strict_pit_eligible"] is True
    late = {**BASE, "first_available_at": "2026-08-01T00:00:00Z"}
    assert align_event(late, DAYS, end=DAYS[-1])["strict_pit_eligible"] is False
    assert align_event({**early, "availability_evidence_hash": None}, DAYS,
                       end=DAYS[-1])["strict_pit_eligible"] is False


def test_preparation_excludes_window_future_and_binds_inputs(tmp_path):
    sec = tmp_path / "sec.jsonl"
    jin10 = tmp_path / "jin10.jsonl"
    sec.write_text("\n".join([
        json.dumps({
            "event_id": f"e{i}", "published_at_raw": published,
            "publication_precision": "SECOND", "source_timezone": "UTC",
            "retrieved_at_utc": "2026-11-15T00:00:00Z",
            "source_response_sha256": "a" * 64, "source_hash_scope": "response",
            "first_publicly_available_at": None, "ticker_candidate": "NVDA",
        }) for i, published in enumerate(("2026-07-31T20:00:00Z",
                                         "2026-08-01T20:00:00Z"))]) + "\n", encoding="utf-8")
    jin10.write_text("", encoding="utf-8")
    out = tmp_path / "converted.jsonl"
    manifest = convert(sec, jin10, out, tmp_path / "manifest.json",
                       start=date(2025, 4, 1), end=date(2026, 7, 31))
    assert manifest["converted_rows"] == 1
    assert "2026-08-01" not in out.read_text(encoding="utf-8")
