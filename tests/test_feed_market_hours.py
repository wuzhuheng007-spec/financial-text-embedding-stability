"""moomoo push health against the registered XNYS sessions: holiday, early close, reopen, nights, unknown range."""
from datetime import datetime, timezone
from pathlib import Path

from scripts.quant_personal_feed import market_hours as mh

ROOT = Path(__file__).resolve().parents[1]
CAL = mh.load(ROOT)
U = lambda *a: datetime(*a, tzinfo=timezone.utc)


def test_the_session_file_is_the_registered_one():
    assert CAL["registered"]


def test_thanksgiving_is_closed_and_a_stale_push_is_healthy():
    v = mh.judge(CAL, U(2026, 11, 26, 2, 8), 3925, 300)                 # 2026-11-25 21:08 ET, after the after-hours session
    assert v["business"] == "MARKET_CLOSED" and v["ok"] and v["market"]["next_session"] == "2026-11-27"
    v = mh.judge(CAL, U(2026, 11, 26, 18, 0), 70000, 300)                # Thanksgiving afternoon
    assert v["business"] == "MARKET_CLOSED" and v["ok"]


def test_the_early_close_ends_extended_hours_at_17_et():
    assert mh.phase(CAL, U(2026, 11, 27, 17, 59))["phase"] == "REGULAR"
    assert mh.phase(CAL, U(2026, 11, 27, 18, 1))["phase"] == "AFTER_HOURS"
    assert mh.phase(CAL, U(2026, 11, 27, 21, 59))["phase"] == "AFTER_HOURS"
    assert mh.phase(CAL, U(2026, 11, 27, 22, 1))["phase"] == "CLOSED"     # 17:01 ET: no push due
    v = mh.judge(CAL, U(2026, 11, 27, 16, 0), 900, 300)
    assert v["business"] == "STALE_IN_SESSION" and not v["ok"]


def test_pre_market_on_reopen_expects_pushes_from_04_et():
    assert mh.phase(CAL, U(2026, 11, 30, 8, 59))["phase"] == "CLOSED"
    assert mh.phase(CAL, U(2026, 11, 30, 9, 0))["phase"] == "PRE_MARKET"
    assert not mh.judge(CAL, U(2026, 11, 30, 10, 0), None, 300)["ok"]     # open and nothing pushed: a fault


def test_a_fresh_push_is_ok_in_any_phase_and_unknown_range_keeps_the_strict_rule():
    assert mh.judge(CAL, U(2026, 11, 29, 12, 0), 10, 300)["business"] == "OK"
    v = mh.judge(CAL, U(2027, 1, 5, 15, 0), 4000, 300)                   # past the session file's last date
    assert v["market"]["phase"] == "UNKNOWN" and v["business"] == "STALE_OR_NONE" and not v["ok"]
