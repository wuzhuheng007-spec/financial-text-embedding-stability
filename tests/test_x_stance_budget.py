"""X stance study, 5,000-posts-per-day tier: the shared ledger, the frozen sample plans, the historical queue, the
bounded preflight and the after-close signal timing. Every provider response here comes from a scripted fake; no
request leaves the machine and no number below describes real posts or a real bill."""
import copy
import json
import sqlite3
import threading
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from scripts.x_stance_v1 import aggregate, batch, config as C, pipeline, provider, stance, store
from scripts.x_stance_v1.ledger import JobBudget, Ledger, LimitReached, Paused, get_meta, set_meta
from scripts.x_stance_v1.sessions import Calendar

UTC = timezone.utc
NOW = datetime(2026, 11, 20, 3, 0, tzinfo=UTC)               # 11:00 in Shanghai on 2026-11-20; 22:00 on 11-19 in New York


@pytest.fixture()
def cfg():
    c = C.load()
    c["aggregation"]["account_rules"]["enabled"] = False     # these modules pre-date the account rules (tests/test_x_stance_r3.py covers them)
    c["allocation"]["recent"].pop("sub_pools", None)         # ... and the split of the recent pool between watched authors and keywords (tests/test_x_stance_watchlist.py)
    c["acquisition"].pop("tolerated_max_posts_per_page", None)   # exact cap edges at the documented page size; the tolerance is covered in tests/test_x_stance_pacing.py
    return c


@pytest.fixture()
def db(tmp_path):
    d = store.connect(tmp_path / "x.sqlite3")
    yield d
    d.close()


def clock(t):
    box = {"t": t}
    return box, (lambda: box["t"])


KEY = {"TWITTERAPI_IO_API_KEY": "tw-FAKE-not-a-real-key-000"}


def unlock(db, cfg, env=KEY, history=True):
    """A stored PASS receipt bound to the current configuration and key, as a strict preflight would leave it."""
    return batch._store_receipt(db, {"finished_at_utc": store.iso(NOW), "verdict": "PASS", "binding": batch.binding(db, cfg, env),
                                     "checks": {"history_search_verified": history}, "fail_reasons": []})


def tweet(i, ts, author=None, text=None):
    return {"id": str(i), "url": f"https://x.com/u/status/{i}", "text": text or f"$QQQ note {i}", "lang": "en",
            "createdAt": ts.strftime("%a %b %d %H:%M:%S +0000 %Y"), "author": {"id": str(author or 9000 + i)}}


class Fake:
    """Returns `per_page` posts inside the requested window for every page, forever (has_next_page stays true)."""

    def __init__(self, per_page=20, fail=None):
        self.per_page, self.fail, self.requests, self.n = per_page, fail, [], 0

    def get(self, params):
        self.requests.append(dict(params))
        if self.fail:
            raise self.fail
        q = params["query"]
        s, e = int(q.split("since_time:")[1].split()[0]), int(q.split("until_time:")[1].split()[0])
        page = int(params["cursor"] or 0)
        out = []
        for j in range(self.per_page):
            self.n += 1
            out.append(tweet(self.n, datetime.fromtimestamp(e - 1 - page * 60 - j, UTC)))
        return {"tweets": out, "has_next_page": True, "next_cursor": str(page + 1)}


# ---------- ledger arithmetic ----------
def test_quote_arithmetic_matches_the_instruction(cfg, db):
    led = Ledger(db, cfg)
    assert (led.day_credits, led.roll_credits, led.pre_credits, led.page_credits) == (75000, 2250000, 3000, 300)
    assert led.pool == {"recent": (3000, 45000), "historical": (2000, 30000)}
    assert Decimal(5000) * Decimal("0.00015") == Decimal("0.75") and Decimal("0.75") * 30 == Decimal("22.50")


def test_settlement_is_fifteen_credits_times_max_returned_one(cfg, db):
    led = Ledger(db, cfg, clock=lambda: NOW)
    got = {}
    for n in (0, 1, 3, 20):
        rid = led.reserve(pool="recent")
        got[n] = led.settle(rid, returned=n, unique_ids=n)
    assert got == {0: 15, 1: 15, 3: 45, 20: 300}             # an empty request costs the minimum; a returned post is not charged twice
    u = led.usage(NOW)
    assert u["today"]["credits"] == 375 and u["today"]["usd"] == "0.00375" and u["today"]["returned_posts"] == 24
    assert u["pools"]["recent"]["returned_posts"] == 24 and u["pools"]["historical"]["credits"] == 0


def test_a_full_page_is_reserved_before_the_request_and_caps_stop_it(cfg, db):
    led = Ledger(db, cfg, clock=lambda: NOW)
    rid = led.reserve(pool="recent")
    u = led.usage(NOW)
    assert u["today"]["credits"] == 300 and u["today"]["returned_posts"] == 20      # the open reservation already counts
    led.settle(rid, returned=2, unique_ids=2)
    assert led.usage(NOW)["today"]["credits"] == 30
    # historical pool: 2,000 posts = 100 full pages; the 101st is refused before any request
    for _ in range(100):
        led.settle(led.reserve(pool="historical"), returned=20, unique_ids=20)
    with pytest.raises(LimitReached) as e:
        led.reserve(pool="historical")
    assert e.value.limit_kind == "POOL_HISTORICAL_POSTS"
    led.reserve(pool="recent")                                # pools do not borrow, and the other pool is not blocked
    assert led.usage(NOW)["pools"]["historical"] == {"credits": 30000, "usd": "0.3", "returned_posts": 2000, "cap_usd": "0.3", "cap_posts": 2000}


def test_duplicates_and_cleaning_never_give_budget_back(cfg, db):
    led = Ledger(db, cfg, clock=lambda: NOW)
    led.settle(led.reserve(pool="recent"), returned=20, unique_ids=3)         # 17 of the 20 were repeats of earlier posts
    row = db.execute("SELECT returned, unique_ids, settled_credits FROM x_cost_ledger").fetchone()
    assert tuple(row) == (20, 3, 300)


def test_unknown_outcome_keeps_the_reservation_and_503_releases_it(cfg, db):
    led = Ledger(db, cfg, clock=lambda: NOW)
    a, b = led.reserve(pool="recent"), led.reserve(pool="recent")
    led.hold_unknown(a, "NETWORK")
    led.release(b, "provider documents HTTP 503 as not charged")
    u = led.usage(NOW)
    assert u["today"]["credits"] == 300 and u["unknown_held"] == {"requests": 1, "usd": "0.003"}
    with pytest.raises(ValueError):
        led.release(a, "")                                    # no evidence, no release
    assert led.usage(NOW)["today"]["credits"] == 300


def test_day_boundary_is_shanghai_and_the_rolling_window_is_720_hours(cfg, db):
    box, ck = clock(datetime(2026, 11, 20, 15, 59, tzinfo=UTC))      # 23:59 Shanghai
    led = Ledger(db, cfg, clock=ck)
    for _ in range(150):                                       # the recent pool for that Shanghai day: 3,000 posts
        led.settle(led.reserve(pool="recent"), returned=20, unique_ids=20)
    with pytest.raises(LimitReached):
        led.reserve(pool="recent")
    box["t"] = datetime(2026, 11, 20, 16, 0, tzinfo=UTC)             # 00:00 Shanghai, next budget day
    led.reserve(pool="recent")
    assert led.usage(box["t"])["budget_day"] == "2026-11-21"
    # rolling window: fill it exactly, then show that only time passing frees it
    db.execute("DELETE FROM x_cost_ledger")
    t0 = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    db.execute("INSERT INTO x_cost_ledger(request_id, job_type, pool, is_preflight, attempt, reserved_credits, reserved_posts, settled_credits, returned, unique_ids, status, cost_source, budget_day, created_at_utc) "
               "VALUES('old','recent','recent',0,1,300,20,?,0,0,'SETTLED','ESTIMATE','2026-10-01',?)", (2250000 - 299, store.iso(t0)))
    db.commit()
    box["t"] = t0 + timedelta(hours=719, minutes=59)
    with pytest.raises(LimitReached) as e:
        led.reserve(pool="recent")
    assert e.value.limit_kind == "ROLLING_USD"
    box["t"] = t0 + timedelta(hours=720)                        # exactly 720 hours later the old spend is outside the window
    led.reserve(pool="recent")


def test_two_processes_cannot_both_take_the_last_page(cfg, tmp_path):
    path = tmp_path / "shared.sqlite3"
    store.connect(path).close()
    seed = store.connect(path); led = Ledger(seed, cfg, clock=lambda: NOW)
    for _ in range(99):
        led.settle(led.reserve(pool="historical"), returned=20, unique_ids=20)
    seed.close()
    results = []

    def worker():
        c = sqlite3.connect(path, timeout=30); c.row_factory = sqlite3.Row
        try:
            Ledger(c, cfg, clock=lambda: NOW).reserve(pool="historical"); results.append("ok")
        except LimitReached as ex:
            results.append(ex.limit_kind)
        finally:
            c.close()

    threads = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert sorted(results) == ["POOL_HISTORICAL_POSTS"] * 5 + ["ok"]       # six concurrent jobs, one page left: exactly one gets it


def test_a_restart_holds_reservations_left_open_by_a_dead_process(cfg, tmp_path):
    path = tmp_path / "x.sqlite3"
    d = store.connect(path); Ledger(d, cfg, clock=lambda: NOW).reserve(pool="recent"); d.close()        # process ends before the response
    d = store.connect(path); led = Ledger(d, cfg, clock=lambda: NOW)
    assert led.usage(NOW)["today"]["credits"] == 300 and led.recover_open() == 1
    assert led.usage(NOW)["unknown_held"]["requests"] == 1 and led.usage(NOW)["today"]["credits"] == 300      # still counted after the restart
    d.close()


def test_a_page_larger_than_documented_is_charged_in_full_and_pauses_everything(cfg, db):
    led = Ledger(db, cfg, clock=lambda: NOW)
    assert led.settle(led.reserve(pool="recent"), returned=35, unique_ids=35) == 525
    with pytest.raises(Paused):
        led.reserve(pool="historical")
    assert led.usage(NOW)["paused"] == "PAGE_OVER_DOCUMENTED_MAX:35"


# ---------- collector on the ledger ----------
Q = {"query_hash": "qh1", "topic_id": "qqq", "lang": "en", "query": "($QQQ) lang:en"}


def reg(db):
    db.execute("INSERT OR IGNORE INTO x_queries VALUES(?,?,?,?,?,?)", ("qh1", "twitterapi_io", "qqq", "en", "vocab-20261122-a", Q["query"]))


def test_collector_timeout_is_held_and_not_retried_in_a_loop(cfg, db):
    reg(db)
    led = Ledger(db, cfg, clock=lambda: NOW)
    f = Fake(fail=provider.ProviderHTTPError(None, "NETWORK"))
    col = provider.Collector(db, cfg, budget=JobBudget(led, pool="recent"), transport=f, clock=lambda: NOW)
    with pytest.raises(provider.StopAll):
        col.collect_pages(Q, 1000, 2000, 3)
    assert len(f.requests) == 1 and led.usage(NOW)["unknown_held"]["requests"] == 1 and led.usage(NOW)["today"]["credits"] == 300
    f.fail = provider.ProviderHTTPError(503, "HTTP_503_NOT_CHARGED")
    with pytest.raises(provider.StopAll):
        col.collect_pages(Q, 1000, 2000, 3)
    row = db.execute("SELECT status, attempt FROM x_cost_ledger ORDER BY created_at_utc, rowid").fetchall()
    assert [tuple(r) for r in row] == [("UNKNOWN_HELD", 1), ("RELEASED", 2)] and led.usage(NOW)["today"]["credits"] == 300


def test_planned_pages_resume_without_buying_a_page_twice(cfg, db):
    reg(db)
    led, f = Ledger(db, cfg, clock=lambda: NOW), Fake()
    s, e = int(NOW.timestamp()) - 7200, int(NOW.timestamp()) - 3600
    col = provider.Collector(db, cfg, budget=JobBudget(led, pool="recent"), transport=f, clock=lambda: NOW)
    r1 = col.collect_pages(Q, s, e, 1)
    assert (r1["status"], r1["pages"], len(f.requests)) == ("PLANNED_SAMPLE", 1, 1)
    assert col.collect_pages(Q, s, e, 1)["from_checkpoint"] and len(f.requests) == 1
    r2 = col.collect_pages(Q, s, e, 2)
    assert (r2["pages"], [p["cursor"] for p in f.requests]) == (2, ["", "1"])
    assert db.execute("SELECT status, stop_reason FROM x_query_runs").fetchone()[:] == ("PLANNED_SAMPLE", "PLANNED_PAGE_LIMIT")
    assert store.coverage(db, "qqq", store.iso(datetime.fromtimestamp(s, UTC)), store.iso(datetime.fromtimestamp(e, UTC)))["status"] == "PLANNED_SAMPLE"   # never COMPLETE


# ---------- plans, daily job, history ----------
def test_recent_plan_is_frozen_breadth_first_and_inside_the_pool(cfg, db):
    d = batch.last_completed_ny_date(NOW)
    assert d == date(2026, 11, 18)                                                # 22:00 on 11-19 in New York: the last COMPLETED date is 11-18
    p = batch.plan_recent(db, cfg, d, NOW)
    assert len(p["units"]) == 4 * 8 and p["pages_per_unit"] == 4 and p["max_posts_if_every_page_is_full"] == 2560 <= 3000
    assert {(u["topic_id"], u["lang"]) for u in p["units"]} == {(t, lang) for t in ("memory", "technology", "market", "qqq") for lang in ("en", "zh")}
    assert [u["slice"] for u in p["units"][:9]] == [0] * 8 + [1]
    again = batch.plan_recent(db, copy.deepcopy(cfg), d, NOW + timedelta(hours=5))
    assert again["reused"] and again["sha256"] == p["sha256"] and again["frozen_at_utc"] == p["frozen_at_utc"]
    spans = sorted({(u["start"], u["end"]) for u in p["units"]})
    assert all(a[1] == b[0] for a, b in zip(spans, spans[1:])) and spans[-1][1] - spans[0][0] == 24 * 3600


def test_daily_job_stays_inside_every_cap_and_reports_the_limit(cfg, db):
    f = Fake()
    out = batch.run_daily(db, cfg, KEY, lambda: f, NOW, clock=lambda: NOW)
    assert out["status"] == "REFUSED_FAIL_CLOSED" and out["requests_sent"] == 0 and "PREFLIGHT_NOT_PASSED" in out["gate"]["reasons"] and f.requests == []
    set_meta(db, "preflight_passed", json.dumps({"at_utc": "x"})); db.commit()        # the old bare marker opens nothing any more
    out = batch.run_daily(db, cfg, KEY, lambda: f, NOW, clock=lambda: NOW)
    assert out["status"] == "REFUSED_FAIL_CLOSED" and out["gate"]["preflight"] == {"state": "STALE", "reasons": ["RECEIPT_FROM_LENIENT_VALIDATOR"], "history_unlocked": False} and f.requests == []
    unlock(db, cfg)
    out = batch.run_daily(db, cfg, KEY, lambda: f, NOW, clock=lambda: NOW)
    u = out["usage"]
    assert out["recent"]["stopped"] is None and out["recent"]["requests"] == 128                      # the frozen plan ran to its end: 32 groups x 4 pages
    assert out["historical"]["stopped"] == "LIMIT_REACHED:POOL_HISTORICAL_POSTS" and out["historical"]["requests"] == 100
    assert u["pools"]["recent"]["returned_posts"] == 2560 and u["pools"]["historical"]["returned_posts"] == 2000
    assert u["today"]["returned_posts"] == 4560 <= 5000 and Decimal(u["today"]["usd"]) == Decimal("0.684") <= Decimal("0.75")
    n = len(f.requests)
    again = batch.run_daily(db, cfg, KEY, lambda: f, NOW + timedelta(hours=1), clock=lambda: NOW + timedelta(hours=1))
    assert len(f.requests) == n and again["historical"]["stopped"].startswith("LIMIT_REACHED")         # same budget day: nothing more is bought
    statuses = {r[0] for r in db.execute("SELECT DISTINCT status FROM x_query_runs")}
    assert statuses <= {"PLANNED_SAMPLE", "NOT_RUN"} and "COMPLETE" not in statuses
    modes = dict(db.execute("SELECT ingest_mode, count(*) FROM x_posts GROUP BY ingest_mode").fetchall())
    assert set(modes) == {"BATCH_AFTER_CLOSE", "RETROSPECTIVE_VISIBLE_NOW"}


def test_history_stays_locked_when_the_preflight_did_not_verify_a_historical_window(cfg, db):
    f = Fake()
    unlock(db, cfg, history=False)
    out = batch.run_daily(db, cfg, KEY, lambda: f, NOW, clock=lambda: NOW)
    assert out["recent"]["requests"] == 128 and out["historical"] == {"job": "historical", "status": "SKIPPED_HISTORY_SEARCH_NOT_VERIFIED", "requests": 0}
    assert db.execute("SELECT count(*) FROM x_cost_ledger WHERE pool='historical'").fetchone()[0] == 0


def test_a_receipt_is_bound_to_key_config_and_validator(cfg, db):
    unlock(db, cfg)
    assert batch.preflight_state(db, cfg, KEY)["state"] == "PASSED" and batch.preflight_passed(db, cfg, KEY)
    assert not batch.preflight_passed(db)                                           # a caller that cannot say what it is bound to gets no
    other = batch.preflight_state(db, cfg, {"TWITTERAPI_IO_API_KEY": "tw-FAKE-another-key-111"})
    assert (other["state"], other["reasons"], other["recent_unlocked"]) == ("STALE", ["BINDING_CHANGED:credential_fingerprint"], False)
    c2 = copy.deepcopy(cfg); c2["allocation"]["recent"]["max_returned_posts_per_day"] = 2000
    assert batch.preflight_state(db, c2, KEY)["reasons"] == ["BINDING_CHANGED:config_sha256"]
    raw = get_meta(db, batch.RECEIPT_KEY)
    assert "tw-FAKE" not in raw and len(batch.key_fingerprint(KEY, cfg)) == 16     # a fingerprint, never the key
    batch._store_receipt(db, {"finished_at_utc": store.iso(NOW + timedelta(hours=1)), "verdict": "FAIL", "binding": batch.binding(db, cfg, KEY), "checks": {}, "fail_reasons": ["STOPPED:ERROR:HTTP_401"]})
    st = batch.preflight_state(db, cfg, KEY)
    assert (st["state"], st["recent_unlocked"]) == ("FAILED", False)               # a newer failure closes the gate an older pass had opened
    assert db.execute("SELECT count(*) FROM x_preflight_receipts").fetchone()[0] == 2   # both receipts are kept


def test_history_range_is_frozen_once_and_walked_date_first(cfg, db):
    r = batch.history_range(db, cfg, NOW)
    assert (r["anchor_ny_date"], r["first_ny_date"], r["last_ny_date"], r["days"]) == ("2026-11-18", "2026-08-20", "2026-11-17", 90)
    assert (r["start_utc"], r["end_utc_exclusive"]) == ("2026-08-20T04:00:00Z", "2026-11-18T05:00:00Z")
    assert batch.history_range(db, cfg, NOW + timedelta(days=30)) == r                # it does not drift with later runs
    set_meta(db, "preflight_passed", "{}"); db.commit()
    f = Fake()
    led = Ledger(db, cfg, clock=lambda: NOW)
    out = batch.run_history(db, cfg, f, NOW, clock=lambda: NOW, ledger=led)
    assert out["requests"] == 100 and out["stopped"] == "LIMIT_REACHED:POOL_HISTORICAL_POSTS"
    rows = db.execute("SELECT substr(window_start_utc,1,10) d, COUNT(*) n, MAX(pages) p FROM x_query_runs WHERE pages>0 GROUP BY d ORDER BY d").fetchall()
    assert len(rows) >= 89 and max(r["p"] for r in rows) == 1                        # the first day's budget touches ~90 dates one page deep, not one date deeply
    day2 = NOW + timedelta(days=1)
    out2 = batch.run_history(db, cfg, f, day2, clock=lambda: day2, ledger=Ledger(db, cfg, clock=lambda: day2))
    assert out2["requests"] == 100 and out2["progress"]["units_with_any_page"] == 200 and out2["progress"]["units_planned"] == 90 * 2 * 8
    assert db.execute("SELECT MAX(pages) FROM x_query_runs").fetchone()[0] == 1        # still breadth before depth


# ---------- preflight ----------
def test_preflight_is_bounded_charged_to_the_pools_and_reused_by_the_jobs(cfg, db):
    f = Fake(per_page=20)
    led = Ledger(db, cfg, clock=lambda: NOW)
    rec = batch.run_preflight(db, cfg, f, NOW, env=KEY, clock=lambda: NOW, ledger=led)
    first = [r for r in rec["requests"] if r["kind"] == "first_page"]
    assert rec["verdict"] == "PASS" and len(first) == 9 and rec["continuation"]["state"] == "VERIFIED" and len(rec["requests"]) == 10
    assert rec["returned_total"] == 200 and rec["network_requests_total"] == 10 and rec["cache_hits_total"] == 0
    assert rec["checks"] == {"all_planned_requests_answered": True, "schema_valid_on_every_page": True, "every_returned_post_parsed": True,
                             "recent_content_verified": True, "history_search_verified": True, "continuation_verified": True}
    u = rec["usage_after"]
    assert u["preflight_cumulative"] == {"credits": 3000, "usd": "0.03", "returned_posts": 200, "cap_usd": "0.03", "cap_posts": 200}
    assert u["pools"]["recent"]["returned_posts"] == 180 and u["pools"]["historical"]["returned_posts"] == 20 and u["today"]["returned_posts"] == 200
    assert {(r["topic_id"], r["lang"]) for r in first[:8]} == {(t, lang) for t in ("memory", "technology", "market", "qqq") for lang in ("en", "zh")}
    older = first[-1]
    assert older["pool"] == "historical" and older["window_start_utc"] < "2026-10-01" and batch.preflight_passed(db, cfg, KEY)
    assert all(r["schema_valid"] and r["in_window"] == r["returned"] and r["parse_failures"] == 0 for r in rec["requests"])
    assert all("since_time:" in r["query"] and r["queryType"] == "Latest" for r in f.requests)
    again = batch.run_preflight(db, cfg, f, NOW + timedelta(minutes=1), env=KEY, clock=lambda: NOW + timedelta(minutes=1), ledger=led)   # everything is already stored
    assert len(f.requests) == 10 and again["network_requests_total"] == 0 and again["usage_after"]["preflight_cumulative"]["returned_posts"] == 200 and again["verdict"] == "PASS"
    fetched = {r[0] for r in db.execute("SELECT DISTINCT fetched_at_utc FROM x_posts")}
    assert fetched == {store.iso(NOW)}                                              # stored pages keep the time they were really received
    out = batch.run_recent(db, cfg, f, NOW, clock=lambda: NOW, ledger=led)          # the daily job continues from those pages
    assert out["requests"] == 128 - 9


def test_preflight_fails_closed_on_errors_and_never_exceeds_its_allowance(cfg, db):
    led = Ledger(db, cfg, clock=lambda: NOW)
    rec = batch.run_preflight(db, cfg, Fake(fail=provider.ProviderHTTPError(401, "HTTP_401")), NOW, env=KEY, clock=lambda: NOW, ledger=led)
    assert rec["verdict"] == "FAIL" and rec["stopped"] == "ERROR:HTTP_401" and not batch.preflight_passed(db, cfg, KEY)
    assert led.usage(NOW)["preflight_cumulative"]["credits"] == 300                   # the one uncertain request stays reserved
    for kind in ("RATE_LIMITED", "NETWORK"):
        r = batch.run_preflight(db, cfg, Fake(fail=provider.ProviderHTTPError(429 if kind == "RATE_LIMITED" else None, kind)), NOW + timedelta(seconds=2), env=KEY, clock=lambda: NOW + timedelta(seconds=2), ledger=led)
        assert r["verdict"] == "FAIL" and len(r["requests"]) == 1                    # one attempt, no retry loop
    r = batch.run_preflight(db, cfg, Fake(fail=provider.ProviderHTTPError(503, "HTTP_503_NOT_CHARGED")), NOW + timedelta(seconds=3), env=KEY, clock=lambda: NOW + timedelta(seconds=3), ledger=led)
    assert r["verdict"] == "FAIL" and led.usage(NOW)["preflight_cumulative"]["credits"] == 900        # 401, 429 and the timeout are held; the documented-free 503 is not
    for _ in range(12):                                        # further attempts cannot pass the cumulative allowance
        batch.run_preflight(db, cfg, Fake(fail=provider.ProviderHTTPError(None, "NETWORK")), NOW + timedelta(minutes=5), env=KEY, clock=lambda: NOW + timedelta(minutes=5), ledger=led)
    u = led.usage(NOW + timedelta(minutes=5))["preflight_cumulative"]
    assert u["credits"] == 3000 and u["returned_posts"] == 200 and not batch.preflight_passed(db, cfg, KEY)
    last = batch.run_preflight(db, cfg, Fake(), NOW + timedelta(minutes=6), env=KEY, clock=lambda: NOW + timedelta(minutes=6), ledger=led)
    assert last["verdict"] == "FAIL" and last["stopped"] == "LIMIT_REACHED:PREFLIGHT_POSTS" and last["network_requests_total"] == 0


# ---------- after-close batch timing and pending classification ----------
def test_batch_signal_uses_the_real_commit_time_and_unclassified_days_have_no_state(cfg, db):
    c = cfg["time"]["exchange_calendar"]
    cal = Calendar.load(store.REPO_ROOT / c["path"], c["sha256"])
    set_meta(db, "preflight_passed", "{}"); db.commit()
    batch.run_recent(db, cfg, Fake(), NOW, clock=lambda: NOW)
    stance.map_topics(db, cfg)
    run_at = datetime(2026, 11, 20, 4, 0, tzinfo=UTC)
    out = pipeline.build_daily(db, cfg, cal, mode="AUTO", now=run_at)
    assert out["session_windows"] >= 1
    rows = [json.loads(r[0]) for r in db.execute("SELECT payload_json FROM x_daily_sentiment WHERE window_kind='session' AND topic_id='qqq' AND lang_scope='all'")]
    assert rows and all(r["mode"] == "BATCH_AFTER_CLOSE" and r["availability"] == "OBSERVED_AFTER_CLOSE_BATCH" for r in rows)
    for r in rows:
        assert r["signal_committed_at_utc"] == "2026-11-20T04:00:00Z" > r["source_window_end_utc"] and r["assumed_available_at_utc"] is None
        assert r["state"] == "UNKNOWN" and r["state_reason"] == "CLASSIFICATION_INCOMPLETE" and r["shares"] == {"bullish": None, "bearish": None, "neutral": None}
        assert r["classification"]["pending"] == r["classification"]["matched_posts"] > 0 and r["classification"]["sample_accounts"] > 0
    assert db.execute("SELECT count(*) FROM x_stance_labels").fetchone()[0] == 0      # nothing was labelled, and nothing was made up


def test_window_mode_follows_the_frozen_history_boundary(cfg, db):
    c = cfg["time"]["exchange_calendar"]
    cal = Calendar.load(store.REPO_ROOT / c["path"], c["sha256"])
    hr = batch.history_range(db, cfg, NOW)
    for pid, created in ((1, "2026-10-01T15:00:00Z"), (2, "2026-11-18T15:00:00Z")):
        p = provider.normalize_twitterapi_io(tweet(pid, store.parse(created), text="$QQQ take"), now="2026-11-20T03:00:00Z", batch="b", mode=provider.RETRO)
        store.put_post(db, p)
    stance.map_topics(db, cfg); db.commit()
    pipeline.build_daily(db, cfg, cal, mode="AUTO", now=datetime(2026, 11, 20, 4, 0, tzinfo=UTC))
    modes = dict(db.execute("SELECT window_label, mode FROM x_daily_sentiment WHERE window_kind='session' AND topic_id='qqq' AND lang_scope='all' AND window_label IN ('2026-10-01','2026-11-18')").fetchall())
    assert modes == {"2026-10-01": "RETROSPECTIVE_VISIBLE_NOW", "2026-11-18": "BATCH_AFTER_CLOSE"} and hr["end_utc_exclusive"] == "2026-11-18T05:00:00Z"
