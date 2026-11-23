"""Request spacing. The provider limits an account to a number of requests per second (0.2 for an account that never
bought credits); on 2026-11-23 the second of two back-to-back preflight requests was answered 429. Spacing is a wait
before a request. It is never a second attempt: a 429 still stops the run."""
import json

import pytest

from scripts.x_stance_v1 import config, provider


class Clock:
    def __init__(self):
        self.t, self.sleeps = 100.0, []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(round(s, 3))
        self.t += s


def _transport(interval, clock, took=1.0, fail_on=None):
    t = provider.UrllibTransport("k" * 20, min_interval=interval, sleep=clock.sleep, clock=clock.now)

    def send(params):
        t.calls += 1
        clock.t += took
        if fail_on == t.calls:
            raise provider.ProviderHTTPError(429, "RATE_LIMITED")
        return {"tweets": []}

    t._send = send
    return t


def test_requests_are_spaced_from_the_end_of_the_previous_one():
    c = Clock()
    t = _transport(6, c)
    for _ in range(3):
        t.get({})
    assert c.sleeps == [6.0, 6.0] and t.calls == 3 and t.waited_seconds == 12.0


def test_no_wait_when_enough_time_already_passed_or_spacing_is_off():
    c = Clock()
    t = _transport(6, c)
    t.get({})
    c.t += 30
    t.get({})
    assert c.sleeps == []
    c2 = Clock()
    t2 = _transport(0, c2)
    t2.get({}), t2.get({})
    assert c2.sleeps == []


def test_a_429_is_not_retried_and_the_next_request_still_waits():
    c = Clock()
    t = _transport(6, c, fail_on=2)
    t.get({})
    with pytest.raises(provider.ProviderHTTPError):
        t.get({})
    assert t.calls == 2                                         # one attempt, no retry
    t.get({})
    assert c.sleeps == [6.0, 6.0] and t.calls == 3


def test_the_live_transport_takes_its_spacing_from_the_config():
    cfg = config.load()
    assert cfg["acquisition"]["min_request_interval_seconds"] >= 5.5      # documented free-tier limit: one per 5 s
    assert provider.live_transport(cfg, "k" * 20).min_interval == cfg["acquisition"]["min_request_interval_seconds"]


# ---------- page size: 22 posts came back on a page documented as 20 (first live history run, 2026-11-23) ----------
import sqlite3
from datetime import datetime, timezone

from scripts.x_stance_v1 import store
from scripts.x_stance_v1.ledger import Ledger, LimitReached, Paused

NOW = datetime(2026, 11, 23, 4, 0, tzinfo=timezone.utc)


def _ledger(tmp_path, tolerated):
    cfg = config.load()
    if tolerated is None:
        cfg["acquisition"].pop("tolerated_max_posts_per_page", None)
    else:
        cfg["acquisition"]["tolerated_max_posts_per_page"] = tolerated
    db = store.connect(tmp_path / "x.sqlite3")
    return Ledger(db, cfg, clock=lambda: NOW)


def test_a_slightly_larger_page_is_charged_at_its_real_size_without_a_pause(tmp_path):
    led = _ledger(tmp_path, 25)
    rid = led.reserve(pool="historical")
    assert led.settle(rid, returned=22, unique_ids=22) == 22 * 15
    u = led.usage(NOW)
    assert u["paused"] is None and u["pools"]["historical"]["returned_posts"] == 22 and u["tolerated_max_posts_per_page"] == 25
    led.reserve(pool="historical")                               # the next request is allowed


def test_a_page_beyond_the_tolerance_still_pauses_everything(tmp_path):
    led = _ledger(tmp_path, 25)
    led.settle(led.reserve(pool="historical"), returned=26, unique_ids=26)
    assert led.usage(NOW)["paused"] == "PAGE_OVER_DOCUMENTED_MAX:26"
    with pytest.raises(Paused):
        led.reserve(pool="historical")


def test_without_the_setting_the_documented_size_is_the_limit(tmp_path):
    led = _ledger(tmp_path, None)
    led.settle(led.reserve(pool="historical"), returned=22, unique_ids=22)
    assert led.usage(NOW)["paused"] == "PAGE_OVER_DOCUMENTED_MAX:22"


def test_cap_checks_keep_headroom_for_a_tolerated_page_so_no_cap_is_exceeded(tmp_path):
    led = _ledger(tmp_path, 25)
    posts = 0
    with pytest.raises(LimitReached) as ex:
        while True:
            led.settle(led.reserve(pool="historical"), returned=25, unique_ids=25)   # every page as large as tolerated
            posts += 25
    cap = led.pool["historical"][0]
    assert ex.value.limit_kind.startswith("POOL_HISTORICAL") and posts <= cap and cap - posts < 25


def test_a_tolerance_below_the_documented_size_is_refused(tmp_path):
    with pytest.raises(ValueError):
        _ledger(tmp_path, 10)


def test_resume_needs_a_reason_and_keeps_a_record(tmp_path):
    led = _ledger(tmp_path, 25)
    assert led.resume("nothing to lift")["status"] == "NOT_PAUSED"
    led.settle(led.reserve(pool="historical"), returned=40, unique_ids=40)
    with pytest.raises(ValueError):
        led.resume("  ")
    out = led.resume("looked at the page: 40 distinct posts")
    assert out["status"] == "RESUMED" and out["was"] == "PAGE_OVER_DOCUMENTED_MAX:40" and led.usage(NOW)["paused"] is None
    log = json.loads(led.db.execute("SELECT value FROM x_meta WHERE key='pause_log'").fetchone()[0])
    assert log == [{"paused": "PAGE_OVER_DOCUMENTED_MAX:40", "resumed_at_utc": "2026-11-23T04:00:00Z", "reason": "looked at the page: 40 distinct posts"}]
    led.reserve(pool="historical")


def test_the_collector_reads_the_same_tolerance():
    cfg = config.load()
    assert cfg["acquisition"]["tolerated_max_posts_per_page"] == 25 and cfg["quote"]["documented_max_posts_per_page"] == 20
