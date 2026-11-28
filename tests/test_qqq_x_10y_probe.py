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
