"""first_quote_evidence: UTC parsing, the half-open window, sub-second records at the open, and what is kept apart."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.quant_personal_feed import first_quote_evidence as fq, market_hours as mh

ROOT = Path(__file__).resolve().parents[1]
S, E = datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc), datetime(2026, 11, 27, 9, 11, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def cal():
    return mh.load(ROOT)


def row(sym, rec, src, session="PREMARKET", ch="WS_QUOTE", basis="SOURCE_TIME_NEW_YORK_DATE_ONLY"):
    return {"symbol": sym, "session": session, "received_at": rec, "source_at": src, "source_channel": ch, "date_basis": basis}


def test_sub_second_record_in_the_first_second_counts_and_the_window_is_half_open(cal):
    assert "2026-11-27T09:00:00.123Z" < "2026-11-27T09:00:00Z"                 # why text comparison is not used
    rows = [row("AAA", "2026-11-27T09:00:00.123Z", "2026-11-27T09:00:00.050Z"),
            row("BBB", "2026-11-27T08:59:59.999Z", "2026-11-27T08:59:59.900Z"),    # before the window: not seen at all
            row("BBB", "2026-11-27T09:11:00Z", "2026-11-27T09:10:59.900Z"),        # at end: outside [start, end)
            row("CCC", "2026-11-27T09:00:00+00:00", "2026-11-27T09:00:00.000000Z")]  # offset spelling, exactly at start
    out = fq.summarize(session_rows=rows, subscriptions=["AAA", "BBB", "CCC"], start=S, end=E, cal=cal)
    assert out["labels_with_valid_quote"] == ["S1", "S3"] and out["labels_without_valid_quote"] == ["S2"]
    assert out["per_subscription"]["S1"]["first_valid"]["received_at_utc"] == "2026-11-27T09:00:00.123000Z"
    assert out["per_subscription"]["S3"]["first_valid"]["seconds_after_start"] == 0.0
    assert out["first_valid_any_utc"] == "2026-11-27T09:00:00.000000Z" and out["no_valid_quote_from_start_seconds"] == 0.0
    assert out["result"] == "PARTIAL" and out["records_in_window"] == 2


def test_old_snapshot_missing_source_and_unconfirmed_session_are_kept_apart(cal):
    rows = [row("AAA", "2026-11-27T09:00:01Z", "2026-11-26T01:02:36Z"),          # old snapshot pushed on connect
            row("AAA", "2026-11-27T09:00:02Z", None),                             # no child time
            row("AAA", "2026-11-27T09:00:03Z", "2026-11-27T09:00:02Z", ch="REST_STOCK_QUOTE"),
            row("AAA", "2026-11-27T09:00:04Z", "2026-11-27T09:00:03Z", basis="PARENT_DATE_CONFLICT"),
            row("AAA", "2026-11-27T09:00:05Z", "2026-11-27T09:00:04Z", session="OVERNIGHT"),
            row("AAA", "2026-11-27T09:02:00Z", "2026-11-27T09:01:59.5Z")]
    out = fq.summarize(session_rows=rows, subscriptions=["AAA"], start=S, end=E, cal=cal)
    assert out["excluded_records"] == {"OLD_SNAPSHOT": 1, "SOURCE_TIME_MISSING": 1, "SESSION_UNCONFIRMED": 2, "OTHER_SESSION": 1}
    f = out["per_subscription"]["S1"]["first_valid"]
    assert f["received_at_utc"].startswith("2026-11-27T09:02:00") and f["receive_minus_source_ms"] == 500.0
    assert out["result"] == "ALL_SUBSCRIPTIONS_RECEIVED" and out["no_valid_quote_from_start_seconds"] == 120.0


def test_source_time_outside_pre_market_is_unconfirmed(cal):
    rows = [row("AAA", "2026-11-26T15:00:01Z", "2026-11-26T15:00:00Z")]          # holiday: no pre-market phase
    out = fq.summarize(session_rows=rows, subscriptions=["AAA"], start=datetime(2026, 11, 26, 15, tzinfo=timezone.utc),
                       end=datetime(2026, 11, 26, 15, 11, tzinfo=timezone.utc), cal=cal)
    assert out["excluded_records"] == {"SESSION_UNCONFIRMED": 1} and out["result"] == "NO_VALID_PREMARKET_QUOTE"


def test_gap_runs_from_start_across_a_reconnect(cal):
    ev = [("CONNECTED_DELAY_UNKNOWN", "2026-11-26T07:23:00Z"), ("DISCONNECTED", "2026-11-27T09:04:00Z"),
          ("CONNECTING", "2026-11-27T09:04:05Z"), ("CONNECTED_DELAY_UNKNOWN", "2026-11-27T09:04:10Z")]
    rows = [row("AAA", "2026-11-27T09:06:30Z", "2026-11-27T09:06:29.9Z")]
    out = fq.summarize(session_rows=rows, subscriptions=["AAA", "BBB"], start=S, end=E, cal=cal, events=ev)
    assert out["reconnects_after_start"] == 2 and out["no_valid_quote_from_start_seconds"] == 390.0   # not 140 s from the reconnect
    assert [e[0] for e in out["state_events"]] == ["DISCONNECTED", "CONNECTING", "CONNECTED_DELAY_UNKNOWN"]
    none = fq.summarize(session_rows=[], subscriptions=["AAA"], start=S, end=E, cal=cal, events=ev)
    assert none["no_valid_quote_still_open"] and none["no_valid_quote_from_start_seconds"] == 660.0


def test_output_never_names_a_symbol(cal):
    rows = [row("ZZZSECRET", "2026-11-27T09:00:01Z", "2026-11-27T09:00:00.5Z")]
    out = fq.summarize(session_rows=rows, subscriptions=["ZZZSECRET"], start=S, end=E, cal=cal)
    assert "ZZZSECRET" not in repr(out) and out["labels_with_valid_quote"] == ["S1"]
