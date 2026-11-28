import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from scripts.qqq_x_10y import probe as P

T0 = datetime(2026, 11, 28, 10, 30, tzinfo=timezone.utc)


class Err(Exception):
    def __init__(self, status, kind):
        super().__init__(kind); self.status, self.kind = status, kind


class Net:
    def __init__(self, pages_per_unit=1, per_page=20, fail=None):
        self.calls, self.ppu, self.n, self.fail = [], pages_per_unit, per_page, fail or {}
        self.seen = {}

    def get(self, params):
        self.calls.append(params)
        q = params["query"]
        if q in self.fail:
            raise self.fail[q]
        c = params.get("cursor") or ""
        k = int(c[1:]) + 1 if c else 1                                       # the page number follows the cursor
        more = k < self.ppu
        return {"tweets": [{"id": f"{q}-{k}-{i}"} for i in range(self.n)], "has_next_page": more, "next_cursor": f"c{k}" if more else None}


def units(n=4):
    return [{"unit_id": f"m{i}:Q", "query": f"q{i}"} for i in range(n)]


def test_plan_is_frozen_120_months_two_queries_an_hour_on_a_trading_day():
    u = P.plan()
    assert len(u) == 240 and len({x["month"] for x in u}) == 120
    assert all(x["until_time"] - x["since_time"] == 3600 for x in u)
    assert P.plan() == u                                                      # deterministic
    jul = next(x for x in u if x["month"] == "2026-07" and x["query_id"] == "Q_CASH")
    assert datetime.fromtimestamp(jul["since_time"], timezone.utc).hour == 15  # 11:00 EDT


def test_reads_to_the_end_and_charges_per_post_with_a_minimum(tmp_path):
    net = Net(pages_per_unit=3, per_page=20)
    r = P.run(units(2), tmp_path, net, T0 + timedelta(hours=1), clock=lambda: T0)
    assert r["stopped"] == "PLAN_DONE" and r["requests"] == 6 and r["credits"] == 6 * 300 and r["units"] == {"COMPLETE": 2}
    net2 = Net(pages_per_unit=1, per_page=0)
    r2 = P.run(units(1), tmp_path / "b", net2, T0 + timedelta(hours=1), clock=lambda: T0)
    assert r2["credits"] == 15                                               # empty page: the 15-credit minimum


def test_page_limit_marks_truncated(tmp_path):
    r = P.run(units(1), tmp_path, Net(pages_per_unit=99), T0 + timedelta(hours=1), clock=lambda: T0)
    assert r["units"] == {"TRUNCATED": 1} and r["requests"] == 10


def test_credit_cap_stops_before_the_page_that_could_cross_it(tmp_path):
    r = P.run(units(10), tmp_path, Net(pages_per_unit=1), T0 + timedelta(hours=1), clock=lambda: T0, max_credits=375 * 3)
    assert r["stopped"] == "CREDIT_CAP" and r["credits"] <= 375 * 3 and r["requests"] == 3


def test_request_cap_and_cutoff(tmp_path):
    r = P.run(units(10), tmp_path, Net(), T0 + timedelta(hours=1), clock=lambda: T0, max_requests=4)
    assert r["stopped"] == "REQUEST_CAP" and r["requests"] == 4
    r2 = P.run(units(10), tmp_path / "c", Net(), T0, clock=lambda: T0)
    assert r2["stopped"] == "CUTOFF" and r2["requests"] == 0


def test_429_and_402_stop_without_charge_and_without_retry(tmp_path):
    net = Net(fail={"q1": Err(429, "RATE_LIMITED")})
    r = P.run(units(4), tmp_path, net, T0 + timedelta(hours=1), clock=lambda: T0, http_error=Err)
    assert r["stopped"] == "STOPPED_429" and len(net.calls) == 2 and r["credits"] == 300
    net = Net(fail={"q0": Err(402, "HTTP_402")})
    r = P.run(units(4), tmp_path / "d", net, T0 + timedelta(hours=1), clock=lambda: T0, http_error=Err)
    assert r["stopped"] == "STOPPED_402_CREDITS" and len(net.calls) == 1


def test_network_error_counts_worst_case_and_three_failures_stop(tmp_path):
    net = Net(fail={f"q{i}": Err(None, "NETWORK") for i in range(5)})
    r = P.run(units(5), tmp_path, net, T0 + timedelta(hours=1), clock=lambda: T0, http_error=Err)
    assert r["stopped"] == "THREE_FAILED_UNITS" and r["credits"] == 3 * 375 and len(net.calls) == 3


def test_resume_skips_finished_units_and_continues_cursor(tmp_path):
    u = units(3)
    P.run(u, tmp_path, Net(pages_per_unit=2), T0 + timedelta(hours=1), clock=lambda: T0, max_requests=3)
    net = Net(pages_per_unit=2)
    r = P.run(u, tmp_path, net, T0 + timedelta(hours=1), clock=lambda: T0)
    assert r["units"] == {"COMPLETE": 3} and r["requests"] == 6
    assert net.calls[0]["cursor"] == "c1"                                    # unit 2 resumed from its cursor
    with pytest.raises(SystemExit):
        P.run(units(4), tmp_path, Net(), T0 + timedelta(hours=1), clock=lambda: T0)   # a different plan is refused


class Crash(BaseException):
    """Stands for the process dying: not an Exception, so nothing in the probe catches it."""


class ReceivedThenCrash(Net):
    def __init__(self, crash_on_call, **k):
        super().__init__(**k); self.crash_on = crash_on_call

    def get(self, params):
        page = super().get(params)                  # the provider has the request (and would charge it)
        if len(self.calls) == self.crash_on:
            raise Crash()
        return page


def test_interrupted_after_send_counts_worst_case_and_is_never_resent(tmp_path):
    u = units(3)
    net = ReceivedThenCrash(2, pages_per_unit=1)
    with pytest.raises(Crash):
        P.run(u, tmp_path, net, T0 + timedelta(hours=1), clock=lambda: T0)
    (tmp_path / P.LOCK).unlink(missing_ok=True)     # after a kill -9 the lock stays and is removed by hand
    db = sqlite3.connect(tmp_path / "probe_ledger.sqlite3")
    assert db.execute("SELECT status, credits FROM probe_requests ORDER BY n").fetchall() == [("OK", 300), ("RESERVED", 375)]
    db.close()
    net2 = Net(pages_per_unit=1)
    r = P.run(u, tmp_path, net2, T0 + timedelta(hours=1), clock=lambda: T0)
    assert r["recovered_reservations"] == 1
    assert [c["query"] for c in net2.calls] == ["q2"]          # q1 (uncertain) is not sent again
    assert r["units"] == {"COMPLETE": 2, "UNCERTAIN": 1} and r["credits"] == 300 + 375 + 300


def test_reservation_counts_against_the_cap_before_the_answer(tmp_path):
    seen = []

    class Peek(Net):
        def get(self, params):
            db = sqlite3.connect(tmp_path / "probe_ledger.sqlite3")
            seen.append(db.execute("SELECT status, credits FROM probe_requests ORDER BY n DESC LIMIT 1").fetchone()); db.close()
            return super().get(params)
    P.run(units(1), tmp_path, Peek(), T0 + timedelta(hours=1), clock=lambda: T0)
    assert seen == [("RESERVED", 375)]


def test_only_one_instance(tmp_path):
    (tmp_path / P.LOCK).write_text("other")
    with pytest.raises(SystemExit, match="ANOTHER_INSTANCE"):
        P.run(units(1), tmp_path, Net(), T0 + timedelta(hours=1), clock=lambda: T0)


def test_plans_share_the_total_cap_and_keep_their_own(tmp_path):
    P.run(units(3), tmp_path, Net(), T0 + timedelta(hours=1), clock=lambda: T0)              # main: 3 requests, 900 credits
    cal = [{"unit_id": f"cal:{i}", "query": f"c{i}"} for i in range(10)]
    r = P.run(cal, tmp_path, Net(), T0 + timedelta(hours=1), clock=lambda: T0, plan_name="cal", max_requests=5)
    assert r["stopped"] == "REQUEST_CAP" and r["requests"] == 5 and r["plan_requests"] == 2
    r = P.run(cal, tmp_path / "x", Net(), T0 + timedelta(hours=1), clock=lambda: T0, plan_name="cal", plan_max_credits=375 * 2)
    assert r["stopped"] == "PLAN_CREDIT_CAP" and r["plan_requests"] == 2


def test_reopen_only_a_503_unit_and_resume_from_its_cursor(tmp_path):
    net = Net(pages_per_unit=3)
    calls = {"n": 0}

    class Once503(Net):
        def get(self, params):
            calls["n"] += 1
            if calls["n"] == 2:
                raise Err(503, "HTTP_503_NOT_CHARGED")
            return super().get(params)
    r = P.run(units(1), tmp_path, Once503(pages_per_unit=3), T0 + timedelta(hours=1), clock=lambda: T0, http_error=Err)
    assert r["units"] == {"FAILED": 1} and r["credits"] == 300
    P.reopen_503(tmp_path, "m0:Q", "owner: resume the 503 unit")
    r = P.run(units(1), tmp_path, net, T0 + timedelta(hours=1), clock=lambda: T0)
    assert r["units"] == {"COMPLETE": 1} and [c["cursor"] for c in net.calls] == ["c1", "c2"]
    with pytest.raises(SystemExit):
        P.reopen_503(tmp_path, "m0:Q", "again")                                               # complete units are refused


def test_s1_plan_four_days_six_slices_bare_pass_first():
    u = P.plan_s1()
    assert len(u) == 120 * 4 * 6 * 2 and P.plan_s1() == u
    half = len(u) // 2
    assert {x["query_id"] for x in u[:half]} == {"Q_BARE"} and {x["query_id"] for x in u[half:]} == {"Q_CASH"}
    assert [x["slice_id"] for x in u[:half]] == [x["slice_id"] for x in u[half:]]           # same order in both passes
    by_day = {}
    for x in u[:half]:
        by_day.setdefault(x["day"], []).append(x)
    assert len(by_day) == 480 and all(len(v) == 6 for v in by_day.values())
    for v in by_day.values():
        reg = [x for x in v if x["session"] == "REGULAR"]
        assert len(reg) == 4 and all(9 <= x["hour_et"] < (13 if x["early_close"] else 16) for x in reg)
        assert all(x["until_time"] - x["since_time"] == 3600 for x in v)
    assert {x["incl_prob_hour"] for x in u if x["session"] == "REGULAR" and not x["early_close"]} == {round(4 / 7, 6)}
    assert len({x["month"] for x in u}) == 120 and max(x["day"] for x in u) <= "2026-11-27"
    assert [x["day"] for x in u[:half]] != sorted(x["day"] for x in u[:half])


def test_calibration_plan_six_days_24_hours_both_queries_on_main_days():
    main = P.plan(); cal = P.plan_cal(main)
    assert len(cal) == 6 * 24 * 2
    days = sorted({u["day"] for u in cal}); assert len(days) == 6
    assert set(days) <= {u["day"] for u in main}
    for d in days:
        sl = sorted((u["since_time"], u["until_time"]) for u in cal if u["day"] == d and u["query_id"] == "Q_CASH")
        assert all(b == a2 for (_, b), (a2, _) in zip(sl, sl[1:])) and all(b - a == 3600 for a, b in sl)
