"""Recovery of a fixed sample date, bounded retry of HTTP 503, the circuit breaker, and the worker's health receipt.

Everything here follows from the first live collection (2026-11-23): 503 answers that ended whole jobs, units that
were never asked again, and a run that crossed New York midnight and moved on to a new date leaving the old one open.
All transports are fakes; no request leaves the machine."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from scripts.x_stance_v1 import audit, batch, config as C, provider, store
from scripts.x_stance_v1.ledger import JobBudget, Ledger, get_meta

UTC = timezone.utc
D21 = datetime(2026, 11, 22, 6, 0, tzinfo=UTC)                   # 01:00 New York on 11-22: the last completed date is 11-21; 14:00 Shanghai on 11-22
D22 = datetime(2026, 11, 23, 6, 0, tzinfo=UTC)                   # one New York date and one Shanghai budget day later
KEY = {"TWITTERAPI_IO_API_KEY": "tw-FAKE-not-a-real-key-000"}


@pytest.fixture()
def cfg():
    c = C.load()
    c["aggregation"]["account_rules"]["enabled"] = False
    c["topics"] = [t for t in c["topics"] if t["id"] in ("qqq", "memory")]
    c["languages"] = ["en"]
    c["acquisition"]["proposed_recent_slices_per_day"] = 2
    return c


@pytest.fixture()
def db(tmp_path):
    d = store.connect(tmp_path / "x.sqlite3")
    yield d
    d.close()


def tweet(i, ts):
    return {"id": str(i), "url": f"https://x.com/u/status/{i}", "text": f"$QQQ memory note {i}", "lang": "en", "likeCount": 50,
            "createdAt": ts.strftime("%a %b %d %H:%M:%S +0000 %Y"), "author": {"id": str(9000 + i), "userName": f"user{i}"}}


class Net:
    """A scripted provider. `fail` maps the 1-based ordinal of a request to an exception; `rule(params)` may raise
    or return a page for requests that match. Pages hold `per_page` posts and always offer a next page."""

    def __init__(self, fail=None, rule=None, per_page=20):
        self.fail, self.rule, self.per_page, self.requests, self.n = dict(fail or {}), rule, per_page, [], 0

    def get(self, params):
        self.requests.append(dict(params))
        ex = self.fail.get(len(self.requests))
        if ex:
            raise ex
        if self.rule:
            r = self.rule(params)
            if r is not None:
                return r
        q = params["query"]
        e = int(q.split("until_time:")[1].split()[0])
        page = int(params["cursor"] or 0)
        out = []
        for j in range(self.per_page):
            self.n += 1
            out.append(tweet(self.n, datetime.fromtimestamp(e - 1 - page * 60 - j, UTC)))
        return {"tweets": out, "has_next_page": True, "next_cursor": str(page + 1)}


E503 = lambda **k: provider.ProviderHTTPError(503, "HTTP_503_NOT_CHARGED", **k)


def unlock(db, cfg, now):
    return batch._store_receipt(db, {"finished_at_utc": store.iso(now), "verdict": "PASS", "binding": batch.binding(db, cfg, KEY), "checks": {"history_search_verified": False}, "fail_reasons": []})


def unit(db, name="u0"):
    qh = "qh-" + name
    db.execute("INSERT OR IGNORE INTO x_queries VALUES(?,?,?,?,?,?)", (qh, "twitterapi_io", "qqq", "en", "v", f"({name}) lang:en"))
    return {"query_hash": qh, "topic_id": "qqq", "lang": "en", "query": f"({name}) lang:en"}


def col(db, cfg, net, now, **kw):
    return provider.Collector(db, cfg, budget=JobBudget(Ledger(db, cfg, clock=lambda: now), pool="recent", **kw), transport=net, clock=lambda: now)


def ledger_rows(db):
    return [tuple(r) for r in db.execute("SELECT status, attempt, note FROM x_cost_ledger ORDER BY created_at_utc, rowid")]


# ---------- bounded retry of one request ----------
def test_a_503_is_retried_with_growing_waits_and_every_attempt_goes_through_the_ledger(cfg, db, sleeps):
    net = Net(fail={1: E503(summary="s1"), 2: E503(summary="s2")})
    r = col(db, cfg, net, D21).collect_pages(unit(db), 1000, 2000, 1)
    assert r["status"] == "PLANNED_SAMPLE" and r["pages"] == 1 and len(net.requests) == 3 and sleeps == [6.0, 12.0]
    rows = ledger_rows(db)
    assert [(s, a) for s, a, _ in rows] == [("RELEASED", 1), ("RELEASED", 2), ("SETTLED", 3)]
    assert all("documented as not charged" in n and "not reconciled with a bill" in n for s, a, n in rows if s == "RELEASED")
    u = Ledger(db, cfg, clock=lambda: D21).usage(D21)
    assert u["today"]["returned_posts"] == 20 and u["unknown_held"]["requests"] == 0          # released attempts count for nothing
    rs = provider.retry_state(db, r["run_id"])
    assert rs["attempts_total"] == 2 and rs["last_error_class"] == "HTTP_503_NOT_CHARGED" and rs["last_error_summary"] == "s2" and rs["state"] == "RETRYING"


def test_retry_after_is_respected_and_a_long_one_defers_the_unit(cfg, db, sleeps):
    net = Net(fail={1: E503(retry_after=40)})
    col(db, cfg, net, D21).collect_pages(unit(db, "a"), 1000, 2000, 1)
    assert sleeps == [40.0]                                       # longer than the 6 s backoff step
    net = Net(fail={1: E503(retry_after=3600)})
    r = col(db, cfg, net, D21).collect_pages(unit(db, "b"), 1000, 2000, 1)
    rs = provider.retry_state(db, r["run_id"])
    assert r["status"] == "PARTIAL" and len(net.requests) == 1 and rs["state"] == "DEFERRED" and rs["retry_after_seconds"] == 3600
    assert rs["next_retry_at_utc"] == store.iso(D21 + timedelta(seconds=3600)) and sleeps == [40.0]     # no hour-long sleep inside the job


def test_after_three_attempts_the_unit_is_deferred_and_others_get_their_turn(cfg, db, sleeps):
    us = [unit(db, f"u{i}") for i in range(3)]
    steps = [(1, {"query_hash": u["query_hash"], "start": 1000, "end": 2000}) for u in us]
    net = Net(rule=lambda p: (_ for _ in ()).throw(E503()) if "(u0)" in p["query"] else None)
    out = batch._walk(col(db, cfg, net, D21), db, iter(steps + steps))
    assert out["stopped"] is None and out["deferred_units"] == 1 and out["transient_errors"] == 3
    assert [("(u0)" in r["query"]) for r in net.requests] == [True, True, True, False, False]      # three attempts, then the others; not asked again in this run
    rs = provider.retry_state(db, provider.Collector(db, cfg, budget=None)._run_id("qh-u0", 1000, 2000))
    assert rs["state"] == "DEFERRED" and rs["attempts_last_round"] == 3 and rs["next_retry_at_utc"] == store.iso(D21 + timedelta(seconds=900))


def test_a_deferred_unit_waits_for_its_time_and_then_resumes(cfg, db):
    u = unit(db)
    step = [(1, {"query_hash": u["query_hash"], "start": 1000, "end": 2000})]
    net = Net(fail={1: E503(), 2: E503(), 3: E503()})
    batch._walk(col(db, cfg, net, D21), db, iter(step))
    early = D21 + timedelta(minutes=5)
    out = batch._walk(col(db, cfg, net, early), db, iter(step))
    assert out["resumed_units"] == 0 and len(net.requests) == 3   # not due yet: no request
    late = D21 + timedelta(minutes=16)
    out = batch._walk(col(db, cfg, net, late), db, iter(step))
    assert out["resumed_units"] == 1 and len(net.requests) == 4
    assert [a for _, a, _ in ledger_rows(db)] == [1, 2, 3, 4]     # the attempt number carries over the rounds


def test_breaker_stops_the_job_on_units_failing_in_a_row_but_keeps_the_plan(cfg, db):
    us = [unit(db, f"u{i}") for i in range(8)]
    steps = [(1, {"query_hash": u["query_hash"], "start": 1000, "end": 2000}) for u in us]
    net = Net(rule=lambda p: (_ for _ in ()).throw(E503()))
    c = cfg["acquisition"].setdefault("retry", {}); c["breaker_min_requests"] = 1000      # isolate the consecutive rule
    out = batch._walk(col(db, cfg, net, D21), db, iter(steps))
    assert out["stopped"] == "CIRCUIT_OPEN:CONSECUTIVE_FAILED_UNITS" and out["deferred_units"] == 4 and len(net.requests) == 12
    assert db.execute("SELECT COUNT(*) FROM x_query_runs WHERE status='PARTIAL'").fetchone()[0] == 4     # the other four were never touched: still to do
    later = D21 + timedelta(hours=1)
    ok = Net()
    out = batch._walk(col(db, cfg, ok, later), db, iter(steps))
    assert out["stopped"] is None and out["resumed_units"] == 4 and len(ok.requests) == 8 and db.execute("SELECT COUNT(*) FROM x_query_runs WHERE status='PARTIAL'").fetchone()[0] == 0


def test_breaker_failure_rate_rule_and_no_trip_for_scattered_failures(cfg, db):
    us = [unit(db, f"u{i}") for i in range(30)]
    steps = [(1, {"query_hash": u["query_hash"], "start": 1000, "end": 2000}) for u in us]
    scattered = Net(fail={k: E503() for k in (3, 9, 15, 21, 27)})                            # like the live run: a few among many
    out = batch._walk(col(db, cfg, scattered, D21), db, iter(steps))
    assert out["stopped"] is None and out["deferred_units"] == 0 and out["transient_errors"] == 5
    db.execute("DELETE FROM x_query_runs"); db.execute("DELETE FROM x_page_cache"); db.commit()
    n = {"i": 0}

    def half(p):
        n["i"] += 1
        if n["i"] % 2:
            raise E503()

    cfg["acquisition"].setdefault("retry", {})["breaker_consecutive_failed_units"] = 1000
    out = batch._walk(col(db, cfg, Net(rule=half), D21), db, iter(steps))
    assert out["stopped"] == "CIRCUIT_OPEN:FAILURE_RATE"


def test_unknown_outcome_keeps_its_reservation_and_is_not_retried(cfg, db, sleeps):
    for name, ex, kind in (("t", provider.ProviderHTTPError(None, "NETWORK"), "NETWORK"), ("r", provider.ProviderHTTPError(429, "RATE_LIMITED"), "RATE_LIMITED"),
                           ("m", provider.ProviderHTTPError(402, "HTTP_402"), "HTTP_402")):      # 402: the account has no credits left
        net = Net(fail={1: ex})
        with pytest.raises(provider.StopAll):
            col(db, cfg, net, D21).collect_pages(unit(db, name), 1000, 2000, 2)
        assert len(net.requests) == 1
    assert sleeps == [] and [(s, n) for s, _, n in ledger_rows(db)] == [("UNKNOWN_HELD", "NETWORK"), ("UNKNOWN_HELD", "RATE_LIMITED"), ("UNKNOWN_HELD", "HTTP_402")]
    assert Ledger(db, cfg, clock=lambda: D21).usage(D21)["today"]["returned_posts"] == 60        # three full pages stay counted


def test_a_retry_cannot_pass_a_cap(cfg, db):
    cfg["allocation"]["recent"]["sub_pools"]["recent"]["max_returned_posts_per_day"] = 44         # room for one page with headroom, not two
    led = Ledger(db, cfg, clock=lambda: D21)
    c = provider.Collector(db, cfg, budget=JobBudget(led, pool="recent"), transport=Net(fail={2: E503()}), clock=lambda: D21)
    with pytest.raises(provider.StopAll) as ex:
        c.collect_pages(unit(db), 1000, 2000, 3)
    assert ex.value.reason == "LIMIT_REACHED:SUBPOOL_RECENT_POSTS" and led.usage(D21)["sub_pools"]["recent"]["returned_posts"] == 20


def test_the_transport_keeps_retry_after_and_a_redacted_summary(monkeypatch):
    import io
    import urllib.error
    key = "tw-SECRET-0123456789"
    body = f'{{"error":"busy","echo":"x-api-key: {key}"}}'.encode()

    def boom(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 503, "busy", {"Retry-After": "7"}, io.BytesIO(body))

    monkeypatch.setattr(provider.urllib.request, "urlopen", boom)
    with pytest.raises(provider.ProviderHTTPError) as ex:
        provider.UrllibTransport(key).get({"query": "q", "cursor": ""})
    s = json.loads(ex.value.summary)
    assert ex.value.kind == "HTTP_503_NOT_CHARGED" and ex.value.retry_after == 7.0 and s["status"] == 503 and s["bytes"] == len(body) and key not in ex.value.summary
    assert provider._retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None and provider._retry_after("-3") is None


# ---------- recovery of a fixed sample date ----------
def first_day(db, cfg, net, room=None):
    """Runs the daily job for 11-21 (4 units x 30 pages). With `room`, most of that budget day's keyword share is
    already used (by settled requests of some other work), so the run stops early - the state a recovery starts
    from. Nothing in the configuration is changed: a changed allocation would rightly invalidate the preflight."""
    unlock(db, cfg, D21)
    if room is not None:
        led = Ledger(db, cfg, clock=lambda: D21)
        for _ in range((2400 - room) // 20):
            led.settle(led.reserve(pool="recent", job_type="recent"), returned=20, unique_ids=20)
    return batch.run_daily(db, cfg, KEY, lambda: net, D21, clock=lambda: D21)


def test_dry_run_lists_every_open_unit_and_sends_nothing(cfg, db):
    net = Net()
    first_day(db, cfg, net, room=100)                             # the day's share ends the plan early
    n = len(net.requests)
    plan_id = db.execute("SELECT plan_id FROM x_sample_plans WHERE job='recent'").fetchone()[0]
    p = batch.recovery_preview(db, cfg, "2026-11-21", plan_id, D22, Ledger(db, cfg, clock=lambda: D22))
    assert p["status"] == "PREVIEW" and p["requests_sent"] == 0 and len(net.requests) == n and p["plan_id"] == plan_id
    states = {u["state"] for u in p["units"]}
    assert states <= {"BELOW_PLANNED_DEPTH", "BUDGET_CUT", "NEVER_RUN"} and p["open_units"] == len(p["units"]) > 0
    assert p["requests_upper_bound"]["keyword"] == sum(u["planned_pages"] - u["pages_read"] for u in p["units"]) and p["ingest_mode_of_new_posts"] == "RETROSPECTIVE_VISIBLE_NOW"
    assert p["budget_now"]["budget_day"] == "2026-11-23" and "does not mean everything" in p["note"]
    assert batch.recovery_preview(db, cfg, "2026-11-19", None, D22)["status"] == "PLAN_NOT_FOUND"
    assert batch.recovery_preview(db, cfg, "2026-11-21", "ffffffffffffffffffffffff", D22)["status"] == "PLAN_ID_MISMATCH"
    assert batch.run_recover(db, cfg, net, "2026-11-19", None, D22, clock=lambda: D22)["status"] == "PLAN_NOT_FOUND" and len(net.requests) == n


def test_recover_resumes_from_the_stored_cursor_and_charges_today_in_the_recent_pool(cfg, db):
    net = Net()
    first_day(db, cfg, net, room=100)
    n = len(net.requests)
    seen = {r["post_id"]: (r["first_seen_at_utc"], r["ingest_mode"]) for r in db.execute("SELECT * FROM x_posts")}
    out = batch.run_recover(db, cfg, net, "2026-11-21", None, D22, clock=lambda: D22)
    assert out["status"] == "RAN" and out["plan_complete"] and out["open_units_after"] == 0 and "does not mean X was read in full" in out["note"]
    new = net.requests[n:]
    plan = json.loads(db.execute("SELECT plan_json FROM x_sample_plans WHERE job='recent'").fetchone()[0])
    assert len(new) == len(plan["units"]) * plan["pages_per_unit"] - n                           # exactly the missing pages
    assert all(r["cursor"] != "" for r in new if any(r["query"] == o["query"] for o in net.requests[:n]))      # a unit that had a page continues from its cursor
    for r in db.execute("SELECT * FROM x_posts"):
        if r["post_id"] in seen:
            assert (r["first_seen_at_utc"], r["ingest_mode"]) == seen[r["post_id"]]              # nothing already stored is re-dated
        else:
            assert r["ingest_mode"] == "RETROSPECTIVE_VISIBLE_NOW" and r["first_seen_at_utc"] == store.iso(D22)      # obtained later, and said so
    days = dict(db.execute("SELECT budget_day, COUNT(*) FROM x_cost_ledger WHERE run_id IS NOT NULL GROUP BY 1").fetchall())
    assert days == {"2026-11-22": n, "2026-11-23": len(new)}                                     # charged when sent, never to the sample date
    assert {tuple(r) for r in db.execute("SELECT DISTINCT pool, job_type FROM x_cost_ledger WHERE budget_day='2026-11-23'")} == {("recent", "recent")}
    assert db.execute("SELECT COUNT(*) FROM x_recovery_runs WHERE finished_at_utc IS NOT NULL").fetchone()[0] == 1


def test_a_page_rejected_by_the_old_validator_is_read_again_without_a_request(cfg, db):
    net = Net(rule=lambda p: {"tweets": [], "has_next_page": False, "next_cursor": None})
    first_day(db, cfg, net)
    rid = db.execute("SELECT run_id FROM x_query_runs LIMIT 1").fetchone()[0]
    db.execute("UPDATE x_query_runs SET status='PARTIAL', stop_reason='PROTOCOL:NEXT_CURSOR_NOT_STRING', error_class='NEXT_CURSOR_NOT_STRING', pages=0 WHERE run_id=?", (rid,)); db.commit()
    n = len(net.requests)
    p = batch.recovery_preview(db, cfg, "2026-11-21", None, D22)
    assert p["free_rereads_of_stored_pages"] == 1 and p["by_action"] == {"keyword:REREAD_STORED_PAGE": 1} and sum(p["requests_upper_bound"].values()) == 0
    out = batch.run_recover(db, cfg, net, "2026-11-21", None, D22, clock=lambda: D22)
    assert out["plan_complete"] and len(net.requests) == n
    assert db.execute("SELECT status FROM x_query_runs WHERE run_id=?", (rid,)).fetchone()[0] == "COMPLETE"


def test_a_missing_query_or_a_refused_cursor_is_reported_and_rebuilt_only_on_request(cfg, db):
    net = Net()
    first_day(db, cfg, net, room=100)
    runs = db.execute("SELECT run_id, query_hash FROM x_query_runs WHERE pages>0 ORDER BY run_id").fetchall()
    gone = runs[0]
    refused = next(r for r in runs if r["query_hash"] != gone["query_hash"])
    db.execute("UPDATE x_query_runs SET status='PARTIAL', stop_reason='ERROR:HTTP_400', error_class='HTTP_400' WHERE run_id=?", (refused["run_id"],))
    keep = db.execute("SELECT * FROM x_queries WHERE query_hash=?", (gone["query_hash"],)).fetchone()
    others = db.execute("SELECT COUNT(*) FROM x_query_runs WHERE query_hash=?", (gone["query_hash"],)).fetchone()[0]
    db.execute("DELETE FROM x_queries WHERE query_hash=?", (gone["query_hash"],)); db.commit()
    p = batch.recovery_preview(db, cfg, "2026-11-21", None, D22)
    acts = {u["run_id"]: u["action"] for u in p["units"]}
    assert acts[refused["run_id"]] == "NEEDS_REBUILD:ERROR:HTTP_400" and acts[gone["run_id"]] == "BLOCKED:QUERY_NOT_IN_STORE"
    db.execute("INSERT INTO x_queries VALUES(?,?,?,?,?,?)", tuple(keep)); db.commit()
    n = len(net.requests)
    out = batch.run_recover(db, cfg, net, "2026-11-21", None, D22, clock=lambda: D22)
    assert not out["plan_complete"] and out["rebuilt"] == [] and out["by_action_after"] == {"keyword:NEEDS_REBUILD": 1}      # not rebuilt silently
    D23 = D22 + timedelta(days=1)                                # the next budget day: the rebuilt unit is paid for again, in full
    out = batch.run_recover(db, cfg, net, "2026-11-21", None, D23, clock=lambda: D23, rebuild=True)
    att = db.execute("SELECT * FROM x_recovery_attempts").fetchone()
    assert out["plan_complete"] and len(out["rebuilt"]) == 1 and att["parent_run_id"] == refused["run_id"] and att["parent_query_hash"] == refused["query_hash"]
    assert att["new_query_hash"] != refused["query_hash"] and att["status"] == "PLANNED_SAMPLE" and att["returned"] > 0
    assert att["duplicate_returned"] == att["returned"] - att["new_posts"] and att["duplicate_cost_credits"] == att["duplicate_returned"] * 15
    assert others >= 1


# ---------- the daily job across New York midnight and the Shanghai budget day ----------
def test_after_new_york_midnight_the_old_date_is_recovered_first_then_the_new_date(cfg, db):
    net = Net()
    first_day(db, cfg, net, room=100)                                       # 11-21, cut short by the share
    n = len(net.requests)
    out = batch.run_daily(db, cfg, KEY, lambda: net, D22, clock=lambda: D22)
    assert [r["sample_date_ny"] for r in out["recovery"]] == ["2026-11-21"] and out["recovery"][0]["plan_complete"]
    assert out["recent"]["ny_date"] == "2026-11-22" and out["recent"]["plan_reused"] is False
    first_new = next(i for i, r in enumerate(net.requests[n:]) if "since_time:%d" % int(datetime(2026, 11, 22, 5, 0, tzinfo=UTC).timestamp()) in r["query"])
    assert first_new == out["recovery"][0]["parts"]["keyword"]["requests"]                     # every recovery request came before the first request of the new date
    assert out["recovery_todo"]["plans"] == []
    assert json.loads(db.execute("SELECT plan_json FROM x_sample_plans WHERE plan_key='2026-11-21'").fetchone()[0])["ny_date"] == "2026-11-21"   # the old plan was not re-cut


def test_what_does_not_fit_today_is_kept_and_continues_on_the_next_budget_day_at_the_real_time(cfg, db):
    net = Net()
    first_day(db, cfg, net, room=60)                                       # budget day 2026-11-22 (Shanghai)
    same_day = D21 + timedelta(hours=1)                           # still 2026-11-22 in Shanghai: the share is used up
    out = batch.run_recover(db, cfg, net, "2026-11-21", None, same_day, clock=lambda: same_day)
    assert out["parts"]["keyword"]["stopped"] == "LIMIT_REACHED:SUBPOOL_RECENT_POSTS" and out["parts"]["keyword"]["requests"] == 0 and not out["plan_complete"]
    todo = batch.save_recovery_todo(db, cfg, batch.last_completed_ny_date(D22), same_day)
    assert todo["plans"][0]["sample_date_ny"] == "2026-11-21" and json.loads(get_meta(db, "recovery_todo"))["plans"] == todo["plans"]
    out = batch.run_recover(db, cfg, net, "2026-11-21", None, D22, clock=lambda: D22)             # 2026-11-23 in Shanghai: a new day's share
    k = out["parts"]["keyword"]["requests"]
    assert k == 4 * 30 - 2 and out["plan_complete"] and out["usage"]["budget_day"] == "2026-11-23" and out["usage"]["sub_pools"]["recent"]["returned_posts"] == k * 20
    assert db.execute("SELECT COUNT(*) FROM x_cost_ledger WHERE pool='historical'").fetchone()[0] == 0                      # never moved to the other pool


def test_a_process_that_died_mid_request_leaves_a_held_reservation_and_the_next_run_goes_on(cfg, db, tmp_path):
    unlock(db, cfg, D21)
    Ledger(db, cfg, clock=lambda: D21).reserve(pool="recent", job_type="recent")                 # the process ends before the answer
    net = Net()
    out = batch.run_daily(db, cfg, KEY, lambda: net, D21, clock=lambda: D21)
    assert out["recovered_open_reservations"] == 1 and out["usage"]["unknown_held"]["requests"] == 1 and out["recent"]["requests"] > 0
    assert db.execute("SELECT COUNT(*) FROM x_cost_ledger WHERE status='RESERVED'").fetchone()[0] == 0


def test_two_jobs_on_one_store_share_the_caps(cfg, tmp_path):
    cfg["allocation"]["recent"]["sub_pools"]["recent"]["max_returned_posts_per_day"] = 100
    path = tmp_path / "shared.sqlite3"
    a, b = store.connect(path), store.connect(path)
    for d in (a, b):
        unit(d, "u0"); unit(d, "u1"); d.commit()
    ca, cb = col(a, cfg, Net(), D21), col(b, cfg, Net(), D21)
    got = 0
    for c, name in ((ca, "u0"), (cb, "u1")) * 4:
        try:
            got = max(got, c.collect_pages(unit(c.db, name), 1000, 2000, 5)["pages"])
        except provider.StopAll as stop:
            assert stop.reason == "LIMIT_REACHED:SUBPOOL_RECENT_POSTS"
    assert Ledger(a, cfg, clock=lambda: D21).usage(D21)["sub_pools"]["recent"]["returned_posts"] <= 100
    a.close(); b.close()


# ---------- health receipt for the page ----------
def test_the_worker_writes_a_receipt_without_the_key_and_the_page_reads_it(cfg, db):
    unlock(db, cfg, D21)
    h = batch.write_health(db, cfg, KEY, D21, source="test")
    raw = get_meta(db, batch.HEALTH_KEY)
    assert h["credential_available"] and h["credential_matches_preflight_receipt"] and h["preflight"]["state"] == "PASSED" and h["gate"]["allowed"]
    assert KEY["TWITTERAPI_IO_API_KEY"] not in raw and batch.key_fingerprint(KEY, cfg) not in raw
    assert batch.read_health(db, cfg, D21 + timedelta(hours=2))["state"] == "FRESH"
    old = batch.read_health(db, cfg, D21 + timedelta(hours=40))
    assert old["state"] == "STALE_RECHECK_NEEDED" and old["age_hours"] == 40
    h = batch.write_health(db, cfg, {}, D21, source="test")
    assert not h["credential_available"] and not h["gate"]["allowed"] and "PROVIDER_KEY_MISSING" in h["gate"]["reasons"]
    h = batch.write_health(db, cfg, {"TWITTERAPI_IO_API_KEY": "tw-ANOTHER-key-000000000"}, D21, source="test")
    assert h["credential_available"] and not h["credential_matches_preflight_receipt"] and not h["gate"]["allowed"]


def test_no_receipt_reads_as_not_reported(cfg, db):
    Ledger(db, cfg)
    assert batch.read_health(db, cfg, D21)["state"] == "NOT_REPORTED"


def test_the_audit_reads_the_same_store_and_its_parts_add_up(cfg, db):
    net = Net(fail={2: E503(), 5: provider.ProviderHTTPError(None, "NETWORK")})
    first_day(db, cfg, net)
    rows = audit.ledger_rows(db)
    rec = audit.reconcile(db, rows)
    t = rec["totals"]
    assert rec["all_checks_pass"] and t["budget_consumed"] == t["confirmed_returned"] + t["reserved"] + t["unknown_held"] and t["unknown_held"] == 20 and t["released_requests"] == 1
    assert t["budget_consumed"] == Ledger(db, cfg, clock=lambda: D21).usage(D21)["today"]["returned_posts"]      # the display splits the number the caps use; it frees nothing
    g = audit.gaps(db)
    assert g["recent_plans"][0]["units_planned"] == len(json.loads(db.execute("SELECT plan_json FROM x_sample_plans WHERE job='recent'").fetchone()[0])["units"])
    assert all("query" not in k and "handle" not in k for r in rows for k in r) and all(u["state"] != "DONE" for u in g["open_units"])


def test_the_page_shows_the_workers_receipt_and_never_infers_a_missing_key_from_its_own_environment(cfg, db, tmp_path, monkeypatch):
    from scripts.x_stance_v1 import portal_read
    path = db.execute("PRAGMA database_list").fetchone()[2]
    unlock(db, cfg, D21)
    real = datetime.now(UTC)
    batch.write_health(db, cfg, KEY, real, source="test")
    v = portal_read.read_for_portal(cfg, path, env={})            # the web process: no key in its environment
    g = v["settings"]["acquisition_gate"]
    assert g["allowed"] is True and g["reasons"] == [] and g["source"] == "WORKER_HEALTH" and v["worker_health"]["state"] == "FRESH" and v["preflight"]["state"] == "PASSED"
    assert not any(m["input"] in ("PROVIDER_KEY", "WORKER_HEALTH") for m in v["missing_inputs"])
    assert KEY["TWITTERAPI_IO_API_KEY"] not in json.dumps(v) and batch.key_fingerprint(KEY, cfg) not in json.dumps(v)
    batch.write_health(db, cfg, {}, real, source="test")          # the worker itself has no key: now the page may say so
    v = portal_read.read_for_portal(cfg, path, env={})
    assert v["settings"]["acquisition_gate"]["allowed"] is False and "PROVIDER_KEY_MISSING" in v["settings"]["acquisition_gate"]["reasons"]
    batch.write_health(db, cfg, KEY, real - timedelta(hours=48), source="test")
    v = portal_read.read_for_portal(cfg, path, env={})
    g = v["settings"]["acquisition_gate"]
    assert g["allowed"] is None and g["reasons"] == ["WORKER_HEALTH_STALE_RECHECK_NEEDED"] and v["worker_health"]["state"] == "STALE_RECHECK_NEEDED"
    batch.save_recovery_todo(db, cfg, batch.last_completed_ny_date(D22), D22)
    assert portal_read.read_for_portal(cfg, path, env={})["recovery_todo"]["plans"] == []
