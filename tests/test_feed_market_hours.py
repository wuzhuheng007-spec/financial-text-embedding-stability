"""Replay of the moomoo feed health verdict over the cases the 2026-11-26 audit asked for: holiday, early close,
pre-market start, first-push timeout, a disconnect while closed, an unknown session, and the immediate faults
(authentication, subscription, heartbeat). Pure inputs; no feed, network or clock."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.quant_personal_feed import market_hours as mh

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
ALL_SESSIONS = {s: ["HAS_SOURCE_TIME"] for s in ("PREMARKET", "AFTERHOURS", "OVERNIGHT")}
UP_LONG_AGO = [("CONNECTING", "2026-11-25T00:00:00Z"), ("CONNECTED_DELAY_UNKNOWN", "2026-11-25T00:00:05Z"),
               ("PARTIAL_SYMBOL_UNAVAILABLE", "2026-11-26T00:40:26Z"), ("CONNECTED_DELAY_UNKNOWN", "2026-11-26T00:40:31Z")]


def T(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


@pytest.fixture(scope="module")
def cal():
    c = mh.load(ROOT)
    assert c["registered"] and c["sha256"] == mh.CALENDAR_SHA256
    return c


def run(cal, now, *, state="CONNECTED_DELAY_UNKNOWN", push="2026-11-26T01:02:36Z", hb=None, events=UP_LONG_AGO,
        symbols=("RECEIVED",) * 6, caps=ALL_SESSIONS, **kw):
    now = T(now)
    row = {"state": state, "heartbeat_at": hb or (now - timedelta(seconds=4)).isoformat(), "last_push_at": push, "worker_alive": 1,
           "last_error_class": kw.pop("error_class", None)}
    return mh.classify(row=row, events=list(events), symbols=list(symbols), caps=caps, cal=cal, now=now, push_stale_s=300,
                       heartbeat_stale_s=180, first_push_grace_s=600, reconnect_grace_s=180, **kw)


def test_holiday_is_closed_and_healthy(cal):
    r = run(cal, "2026-11-26T15:00:00Z")                                   # Thanksgiving, 10:00 ET
    assert (r["business"], r["ok"], r["market"]["phase"]) == ("MARKET_CLOSED", True, "CLOSED")
    assert r["market"]["next_session"] == "2026-11-27" and r["last_push_at"] == "2026-11-26T01:02:36Z"


def test_early_close_after_hours_end_at_17_et(cal):
    assert run(cal, "2026-11-27T21:30:00Z", push="2026-11-27T21:29:50Z")["business"] == "OK"
    r = run(cal, "2026-11-27T21:30:00Z", push="2026-11-27T21:00:00Z")                 # pushes came, then stopped
    assert (r["business"], r["ok"], r["market"]["phase"], r["market"]["session_type"]) == ("STALE_IN_SESSION", False, "AFTER_HOURS", "EARLY_CLOSE")
    assert run(cal, "2026-11-27T22:05:00Z", push="2026-11-27T21:59:00Z")["business"] == "MARKET_CLOSED"   # 17:05 ET


def test_pre_market_start_waits_for_the_first_push_then_alarms(cal):
    r = run(cal, "2026-11-27T09:05:00Z")                                   # 04:05 ET, connected since yesterday
    assert (r["business"], r["business_zh"], r["ok"]) == ("WAITING_FIRST_PUSH", "等待首条推送", True)
    assert r["first_push_grace"]["from_utc"].startswith("2026-11-27T09:00:00") and r["grace_left_seconds"] == 300
    r = run(cal, "2026-11-27T09:10:01Z")
    assert (r["business"], r["ok"]) == ("NO_FIRST_PUSH", False)
    assert run(cal, "2026-11-27T09:05:00Z", push="2026-11-27T09:03:00Z")["business"] == "OK"


def test_first_push_grace_counts_from_a_late_connection(cal):
    ev = UP_LONG_AGO + [("DISCONNECTED", "2026-11-27T14:58:00Z"), ("CONNECTING", "2026-11-27T14:59:00Z"), ("CONNECTED_DELAY_UNKNOWN", "2026-11-27T15:00:00Z")]
    r = run(cal, "2026-11-27T15:05:00Z", events=ev, push="2026-11-26T01:02:36Z")
    assert r["business"] == "WAITING_FIRST_PUSH" and r["first_push_grace"]["connected_since_utc"].startswith("2026-11-27T15:00:00")
    assert run(cal, "2026-11-27T15:10:30Z", events=ev)["business"] == "NO_FIRST_PUSH"


def test_regular_hours_without_extended_entitlement(cal):
    r = run(cal, "2026-11-27T10:00:00Z", caps={})                         # pre-market never observed for this account
    assert (r["business"], r["ok"]) == ("NOT_DUE_SESSION_NOT_ENTITLED", True)
    r = run(cal, "2026-11-27T14:35:00Z", caps={})
    assert r["business"] == "WAITING_FIRST_PUSH" and r["first_push_grace"]["from_utc"].startswith("2026-11-27T14:30:00")
    assert run(cal, "2026-11-27T14:35:00Z", caps={}, extended=False)["first_push_grace"]["from_utc"].startswith("2026-11-27T14:30:00")


def test_disconnect_while_closed(cal):
    ev = UP_LONG_AGO + [("DISCONNECTED", "2026-11-26T14:59:00Z")]
    r = run(cal, "2026-11-26T15:00:00Z", state="DISCONNECTED", events=ev)
    assert (r["business"], r["ok"], r["down_seconds"]) == ("RECONNECTING", True, 60.0)
    ev2 = UP_LONG_AGO + [("DISCONNECTED", "2026-11-26T14:50:00Z"), ("CONNECTING", "2026-11-26T14:51:00Z")]
    r = run(cal, "2026-11-26T15:00:00Z", state="CONNECTING", events=ev2)
    assert (r["business"], r["ok"], r["market"]["phase"], r["down_seconds"]) == ("DISCONNECTED", False, "CLOSED", 600.0)
    r = run(cal, "2026-11-26T15:00:00Z", state="DISCONNECTED", events=ev2, hb="2026-11-26T14:51:00Z")   # the worker gave up
    assert r["business"] == "HEARTBEAT_TIMEOUT"


def test_unknown_session_keeps_the_strict_rule(cal):
    r = run(cal, "2027-01-05T15:00:00Z", push="2027-01-05T14:00:00Z")
    assert (r["business"], r["ok"], r["market"]["phase"]) == ("UNKNOWN_SESSION", False, "UNKNOWN")
    assert run(cal, "2027-01-05T15:00:00Z", push="2027-01-05T14:59:00Z")["business"] == "OK"
    assert run(None, "2026-11-26T15:00:00Z")["business"] == "UNKNOWN_SESSION"                  # no calendar at all
    bad = {**cal, "registered": False}
    assert run(bad, "2026-11-26T15:00:00Z")["business"] == "UNKNOWN_SESSION"                   # a changed calendar file


@pytest.mark.parametrize("now", ["2026-11-26T15:00:00Z", "2026-11-27T15:00:00Z"])
def test_auth_subscription_and_heartbeat_faults_are_immediate_in_any_phase(cal, now):
    assert run(cal, now, state="AUTH_EXPIRED", error_class="AUTHORIZATION")["business"] == "AUTH_FAILED"
    assert run(cal, now, state="RATE_LIMITED")["business"] == "SUBSCRIPTION_FAILED"
    r = run(cal, now, symbols=("UNAVAILABLE",) * 6)
    assert (r["business"], r["unavailable_symbols"]) == ("SUBSCRIPTION_FAILED", 6)
    r = run(cal, now, hb=(T(now) - timedelta(seconds=181)).isoformat())
    assert (r["business"], r["ok"]) == ("HEARTBEAT_TIMEOUT", False)
    assert run(cal, now, symbols=("UNAVAILABLE",) * 5 + ("RECEIVED",))["business"] != "SUBSCRIPTION_FAILED"   # one live symbol is enough


def test_overnight_only_when_enabled_and_observed(cal):
    assert run(cal, "2026-11-26T03:00:00Z")["business"] == "MARKET_CLOSED"                       # 22:00 ET, overnight off
    r = run(cal, "2026-11-27T03:00:00Z", overnight=True, push="2026-11-26T01:02:36Z")
    assert r["market"]["push_expected"] is True and r["business"] in ("WAITING_FIRST_PUSH", "NO_FIRST_PUSH")
    assert run(cal, "2026-11-27T03:00:00Z", overnight=True, caps={"PREMARKET": ["HAS_SOURCE_TIME"], "AFTERHOURS": ["HAS_SOURCE_TIME"]})["business"] == "MARKET_CLOSED"
