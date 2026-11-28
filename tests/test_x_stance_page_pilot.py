"""The QQQ-only page pilot (owner instruction of 2026-11-28). Fake transports only; no request leaves the machine.

Checks: nothing is sent unless approved and inside the active window; the regular first page stays in production
and is never asked again; pages 2 and 3 continue the same window from its cursor and live only in the pilot store;
production gets ledger rows only; the pilot's own caps and the guard that keeps the regular jobs' remaining need
stop it; the cut-off holds; the report carries counts only."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from scripts.x_stance_v1 import config as C, incremental, page_pilot, store
from scripts.x_stance_v1.ledger import Ledger

UTC = timezone.utc
T0 = datetime(2026, 11, 28, 2, 20, 30, tzinfo=UTC)            # Beijing 10:20


def tweet(i, ts, author=None):
    return {"id": str(i), "url": f"https://x.com/u/status/{i}", "text": f"$QQQ looks fine {i}", "lang": "en", "likeCount": 25,
            "createdAt": ts.strftime("%a %b %d %H:%M:%S +0000 %Y"), "author": {"id": str(author if author is not None else 9000 + i), "userName": f"user{i}"}}


class Net:
    """Every window has three full pages and more after them (cursors c1, c2, c3); `full=False` gives one short last page."""

    def __init__(self, full=True, base=0, clock_box=None):
        self.requests, self.n, self.full, self.clock_box = [], base, full, clock_box

    def get(self, params):
        self.requests.append(dict(params))
        if self.clock_box is not None:
            self.clock_box[0] += timedelta(minutes=30)
        q, cur = params["query"], params.get("cursor") or ""
        e = int(q.split("until_time:")[1].split()[0])
        k = 0 if not cur else int(cur[1:])
        out = []
        for j in range(20 if self.full else 5):
            self.n += 1
            out.append(tweet(self.n, datetime.fromtimestamp(e - 1 - 20 * k - j, UTC)))
        return {"tweets": out, "has_next_page": self.full, "next_cursor": f"c{k + 1}" if self.full else ""}


def window(r):
    q = r["query"]
    return tuple(int(q.split(k)[1].split()[0]) for k in ("since_time:", "until_time:"))


@pytest.fixture()
def cfg():
    c = C.load()
    c["topics"] = [t for t in c["topics"] if t["id"] in ("qqq", "memory")]; c["languages"] = ["en"]
    c["incremental"] = {**c["incremental"], "include_watchlist": False, "control_pairs": [], "query_suffix": "", "lag_hours": 0}
    c["incremental"]["page_pilot"] = {**c["incremental"]["page_pilot"], "approved": "test approval", "active_from_utc": "2026-11-28T00:00:00Z", "active_until_utc": "2026-12-01T00:00:00Z"}
    return c


@pytest.fixture()
def dbs(tmp_path):
    db = store.connect(tmp_path / "x.sqlite3")
    yield db, tmp_path / page_pilot.AUDIT_FILE
    db.close()


def inc(db, cfg, net, t, clock=None, audit=None):
    clk = clock or (lambda: t)
    return incremental.run(db, cfg, net, t, clock=clk, ledger=Ledger(db, cfg, clock=clk), audit_db_pilot=audit)


def test_shipped_pilot_is_inactive_and_sends_nothing(dbs):
    db, a = dbs
    c = C.load()
    assert page_pilot.active(c, T0) == (False, "NOT_APPROVED")
    c["topics"] = [t for t in c["topics"] if t["id"] == "qqq"]; c["languages"] = ["en"]
    c["incremental"] = {**c["incremental"], "include_watchlist": False, "control_pairs": [], "query_suffix": "", "lag_hours": 0}
    net = Net(); out = inc(db, c, net, T0, audit=a)
    assert out["page_pilot"]["state"] == "NOT_APPROVED" and out["page_pilot"]["requests"] == 0
    assert len(net.requests) == 1 and not a.exists()


def test_pages_2_and_3_continue_the_regular_window_and_stay_out_of_production(dbs, cfg):
    db, a = dbs
    net = Net(); out = inc(db, cfg, net, T0, audit=a)
    assert out["requests"] == 2                                   # regular: qqq and memory, one page each
    assert out["page_pilot"]["requests"] == 2 and out["requests_pilot"] == 2
    qqq = [r for r in net.requests if "QQQ" in r["query"]]
    assert [r.get("cursor") or "" for r in qqq] == ["", "c1", "c2"]  # page 1 once, then its own cursor; never page 1 again
    assert len({window(r) for r in qqq}) == 1                      # the same window
    assert all("memory" not in r["query"].lower() or not r.get("cursor") for r in net.requests)   # other topics: one page
    assert db.execute("SELECT count(*) FROM x_page_cache WHERE cursor<>''").fetchone()[0] == 0       # production: first pages only
    assert db.execute("SELECT count(*) FROM x_posts").fetchone()[0] == 40
    adb = store.connect(a, readonly=True)
    assert adb.execute("SELECT count(*) FROM x_page_cache WHERE cursor<>''").fetchone()[0] == 2
    assert adb.execute("SELECT count(*) FROM x_posts").fetchone()[0] == 60                         # its own page 1 copy + 40
    u = adb.execute("SELECT * FROM x_pilot_units").fetchone()
    assert u["status"] == "PLANNED_SAMPLE" and u["pages_after"] == 2 and u["page1_returned"] == 20 and u["requests"] == 2
    led = dict(db.execute("SELECT job_type, count(*) FROM x_cost_ledger WHERE status='SETTLED' GROUP BY 1"))
    assert led == {"recent": 2, "pilot_pages": 2}
    assert db.execute("SELECT DISTINCT pool FROM x_cost_ledger WHERE job_type='pilot_pages'").fetchall()[0][0] == "recent"
    usage = Ledger(db, cfg).usage(T0)
    assert usage["sub_pools"]["recent"]["returned_posts"] == 40      # the keyword share counts the regular pages only


def test_a_window_is_continued_once_and_the_next_round_continues_its_own(dbs, cfg):
    db, a = dbs
    inc(db, cfg, Net(), T0, audit=a)
    again = Net(base=500); out = inc(db, cfg, again, T0, audit=a)
    assert out["page_pilot"]["requests"] == 0 and not again.requests
    t1 = T0 + timedelta(hours=2)
    nxt = Net(base=1000); out = inc(db, cfg, nxt, t1, clock=lambda: t1, audit=a)
    assert out["page_pilot"]["requests"] == 2
    adb = store.connect(a, readonly=True)
    assert adb.execute("SELECT count(DISTINCT window_start_utc) FROM x_pilot_units").fetchone()[0] == 2


def test_a_short_first_page_is_not_continued(dbs, cfg):
    db, a = dbs
    net = Net(full=False); out = inc(db, cfg, net, T0, audit=a)
    assert out["page_pilot"]["requests"] == 0 and out["page_pilot"]["units"] == [] and len(net.requests) == 2


def test_the_pilots_own_daily_request_cap_stops_it_after_page_2(dbs, cfg):
    db, a = dbs
    cfg["incremental"]["page_pilot"]["caps"] = {**cfg["incremental"]["page_pilot"]["caps"], "max_extra_requests_per_day": 1}
    out = inc(db, cfg, Net(), T0, audit=a)
    assert out["page_pilot"]["requests"] == 1 and out["page_pilot"]["stopped"] == "LIMIT_REACHED:PILOT_DAY_REQUESTS"
    assert out["status"] == "RAN" and out["stopped"] is None          # the regular round is not stopped by the pilot
    adb = store.connect(a, readonly=True)
    assert adb.execute("SELECT status, pages_after FROM x_pilot_units").fetchone()[:] == ("STOPPED", 1)


def test_windows_after_a_stop_are_recorded_as_skipped_not_asked(dbs, cfg):
    db, a = dbs
    cfg["languages"] = ["en", "zh"]
    cfg["incremental"]["page_pilot"]["caps"] = {**cfg["incremental"]["page_pilot"]["caps"], "max_extra_requests_per_day": 1}
    net = Net(); out = inc(db, cfg, net, T0, audit=a)
    assert [u["status"] for u in out["page_pilot"]["units"]] == ["STOPPED", "SKIPPED"] and out["page_pilot"]["requests"] == 1
    assert sum(1 for r in net.requests if r.get("cursor")) == 1
    r = page_pilot.report(db, store.connect(a, readonly=True), cfg)
    assert r["by_status"] == {"STOPPED": 1, "SKIPPED": 1} and r["stop_reasons"] == {"LIMIT_REACHED:PILOT_DAY_REQUESTS": 2}


def test_the_guard_keeps_the_regular_jobs_remaining_need_of_the_day(dbs, cfg):
    db, _ = dbs
    cfg["incremental"]["page_pilot"]["caps"] = {"max_extra_requests_per_day": 999, "max_extra_usd_per_day": "9", "max_extra_requests_total": 999, "max_extra_usd_total": "9"}
    led = Ledger(db, cfg, clock=lambda: T0)
    h = page_pilot.headroom(led, cfg, T0, keyword_queries=8, watched_queries=24)
    assert h["rounds_left_today"] == 6                             # Beijing 10:20: 12:20 ... 22:20
    assert h["kept_for_regular"]["keyword"]["credits"] == 6 * 8 * led.check_credits
    assert h["kept_for_regular"]["watched_authors"]["credits"] == 9000   # bounded by what is left of its own share
    room = led.pool["recent"][1] - h["kept_for_regular"]["keyword"]["credits"] - h["kept_for_regular"]["watched_authors"]["credits"]
    assert h["ok"]
    g = page_pilot.guard(cfg, keyword_queries=8, watched_queries=24)
    n = 0
    while True:                                                    # spend pilot pages until the guard refuses
        try:
            rid = led.reserve(pool="recent", job_type=page_pilot.JOB, guard=g)
        except Exception as ex:  # noqa: BLE001
            assert str(ex) == "PILOT_KEEPS_REGULAR:POOL_RECENT_USD"; break
        led.settle(rid, returned=20, unique_ids=20); n += 1
    assert n == (room - led.check_credits) // 300 + 1               # the last page that still leaves the regular need free
    late = T0.replace(hour=15, minute=20)                          # Beijing 23:20: no regular round left today
    assert page_pilot.headroom(led, cfg, late, keyword_queries=8, watched_queries=24)["kept_for_regular"]["keyword"]["credits"] == 0


def test_the_cutoff_is_checked_in_front_of_every_pilot_call(dbs, cfg):
    db, a = dbs
    box = [T0]
    cfg["incremental"]["page_pilot"]["active_until_utc"] = store.iso(T0 + timedelta(minutes=50))
    net = Net(clock_box=box); out = inc(db, cfg, net, T0, clock=lambda: box[0], audit=a)
    assert out["page_pilot"]["refused_after_cutoff"] >= 1
    assert out["page_pilot"]["stopped"].startswith("CONTROL_CUTOFF:")


def test_the_report_has_counts_per_page_and_no_ids(dbs, cfg):
    db, a = dbs
    inc(db, cfg, Net(), T0, audit=a)
    adb = store.connect(a, readonly=True)
    r = page_pilot.report(db, adb, cfg)
    assert r["per_lang"]["en"][2]["returned"] == 20
    assert r["per_page"][1]["returned"] == 20 and r["per_page"][2]["returned"] == 20 and r["per_page"][3]["returned"] == 20
    assert r["per_page"][2]["new_posts"] == 20 and r["per_page"][3]["new_posts"] == 20 and r["per_page"][1]["new_posts"] is None
    assert r["per_page"][2]["authors_not_on_page1_of_the_same_window"] == 20
    assert r["ledger"]["requests"] == 2 and r["ledger"]["credits"] == 600
    body = json.dumps(r)
    assert "9001" not in body and "status/" not in body and "QQQ looks" not in body


def test_preview_sends_nothing_and_shows_the_headroom(dbs, cfg):
    db, _ = dbs
    p = incremental.preview(db, cfg, T0, Ledger(db, cfg, clock=lambda: T0))
    assert p["requests_sent"] == 0 and p["page_pilot"]["state"] == "ACTIVE" and p["page_pilot"]["headroom_now"]["ok"]
    assert db.execute("SELECT count(*) FROM x_cost_ledger").fetchone()[0] == 0


def test_the_preflight_binding_does_not_change_with_the_pilot_block():
    from scripts.x_stance_v1 import batch
    a = C.load(); b = json.loads(json.dumps(a))
    b["incremental"]["page_pilot"]["approved"] = "x"; b["incremental"]["page_pilot"]["caps"]["max_extra_usd_total"] = "1"
    assert batch.binding(None, a, {})["config_sha256"] == batch.binding(None, b, {})["config_sha256"]
