from datetime import date, datetime, timedelta, timezone

from scripts.qqq_x_10y import calendar_us as cal, regimes as R


def days(n, start=date(2020, 1, 1)):
    return [start + timedelta(days=i) for i in range(n)]


def flat_then(path, lead=63, level=100.0):
    c = [level] * lead + list(path)
    return days(len(c)), c


def test_ten_percent_boundary_is_inclusive_for_narrow_range_and_for_breach():
    c = [100.0] * 62 + [110.0]
    r = R.rolling_range(c, c, c, 63)
    assert abs(r[-1]["range"] - 0.10) < 1e-12 and r[-1]["range"] <= R.THRESH + 1e-12
    d, c2 = flat_then([90.0])                                  # exactly -10% from the 63-day peak
    s = R.scan(d, c2)
    assert s.flags[-1]["dd_trigger"] and len(s.events) == 1
    d, c3 = flat_then([90.01])
    assert not R.scan(d, c3).events


def test_range_is_not_a_round_trip_slow_rise_keeps_range_but_has_efficiency_one():
    c = [100 + 0.1 * i for i in range(63)]                      # +6.2%, straight line
    r = R.rolling_range(c, c, c, 63)[-1]
    assert r["range"] < 0.10 and abs(r["efficiency"] - 1) < 1e-12 and r["net"] > 0.06
    z = [100, 105] * 31 + [100]                                 # chop: same range class, efficiency ~0
    rz = R.rolling_range(z, z, z, 63)[-1]
    assert rz["range"] < 0.10 and rz["efficiency"] == 0


def test_amplitude_uses_highs_and_lows_and_is_reported_apart_from_close_range():
    c = [100.0] * 63
    h = [101.0] * 63; low = [99.0] * 62 + [90.0]
    r = R.rolling_range(c, h, low, 63)[-1]
    assert r["range"] == 0 and abs(r["amplitude"] - (101 / 90 - 1)) < 1e-12


def test_running_min_and_first_rebound_and_no_retrigger_until_a_new_cross():
    path = [95, 89, 85, 80, 84, 87.9, 88.0, 86, 85, 84, 83]     # trigger at 89; min 80; rebound at 88 (=+10%)
    d, c = flat_then(path)
    s = R.scan(d, c)
    assert len(s.events) == 1
    e = s.events[0]
    assert d[e.trigger_i] == d[64] and e.min_close == 80 and e.rebound_close == 88.0
    assert sum(f["rebound_trigger"] for f in s.flags) == 1     # only the first rebound day
    # after the rebound the close stays under 90% of the peak: no new event without a new down-cross
    assert all(not f["dd_trigger"] for f in s.flags[e.rebound_i + 1:])


def test_new_drawdown_needs_a_fresh_cross_after_rebound():
    path = [89, 80, 88, 91, 89.5]                               # rebound at 88, back above 90 at 91, crosses again
    d, c = flat_then(path)
    s = R.scan(d, c)
    assert len(s.events) == 2 and s.events[1].trigger_close == 89.5


def test_same_day_rebound_runs_before_a_new_trigger():
    path = [89, 80, 95]                                         # 95 >= 88: rebound; 95 is not a breach anyway
    d, c = flat_then(path)
    s = R.scan(d, c)
    f = s.flags[-1]
    assert f["rebound_trigger"] and not f["dd_trigger"]


def test_open_event_and_warmup():
    d, c = flat_then([89, 85])
    s = R.scan(d, c)
    assert s.events[-1].status == "OPEN" and s.events[-1].rebound_i is None
    d2 = days(5); c2 = [100, 50, 50, 50, 50]                    # inside warm-up: no event
    assert not R.scan(d2, c2).events


def test_cross_month_drawdown_peak_known_at_trigger_not_ex_post():
    d = [date(2025, 1, 1) + timedelta(days=i) for i in range(70)]
    c = [100.0] * 60 + [95, 92, 89.9, 85, 82, 86, 91, 92, 90, 89]
    s = R.scan(d, c)
    e = s.events[0]
    assert e.peak_close == 100.0 and e.drop_at_trigger < -0.10 + 1e-9
    w = R.window_stats(d, c, c, c, 55, 69)
    assert w["max_drawdown_trough"] == d[64] and abs(w["max_drawdown"] - (-0.18)) < 1e-9
    assert w["max_runup_trough"] == d[64]


def test_labels_priority():
    path = [89, 80, 88]
    d, c = flat_then(path)
    s = R.scan(d, c)
    lab = R.label_days(s, R.rolling_range(c, c, c, 63))
    assert lab[63]["trigger_label"] == "DRAWDOWN_TRIGGER" and lab[63]["phase"] == "DRAWDOWN"
    assert lab[-1]["trigger_label"] == "REBOUND_TRIGGER" and lab[-1]["phase"] == "REBOUND"
    assert lab[10]["trigger_label"] == "OTHER"                  # warm-up: no range yet


def test_calendar_half_days_weekends_and_dst():
    assert cal.close_utc(date(2026, 11, 27)) == datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc)   # 13:00 EST
    assert cal.close_utc(date(2026, 7, 15)) == datetime(2026, 7, 15, 20, 0, tzinfo=timezone.utc)     # 16:00 EDT
    assert cal.close_utc(date(2026, 1, 15)) == datetime(2026, 1, 15, 21, 0, tzinfo=timezone.utc)     # 16:00 EST
    assert not cal.is_trading_day(date(2026, 11, 26)) and not cal.is_trading_day(date(2026, 11, 28))
    assert not cal.is_trading_day(date(2026, 7, 3)) and cal.is_trading_day(date(2026, 7, 2))          # 4th on Saturday
    assert cal.is_trading_day(date(2021, 12, 31))                                                      # NY Day on Saturday
    assert not cal.is_trading_day(date(2025, 1, 9)) and not cal.is_trading_day(date(2018, 12, 5))
    assert cal.last_closed_trading_day(datetime(2026, 11, 27, 17, 59, tzinfo=timezone.utc)) == date(2026, 11, 25)
    assert cal.last_closed_trading_day(datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc)) == date(2026, 11, 27)
