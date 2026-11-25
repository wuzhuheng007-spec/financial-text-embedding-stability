"""Intraday increments, the hold after an HTTP 402, and the update receipt the page reads.

All transports are fakes; no request leaves the machine."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from scripts.x_stance_v1 import cli, config as C, incremental, provider, store
from scripts.x_stance_v1.ledger import Ledger

UTC = timezone.utc
T0 = datetime(2026, 11, 25, 6, 0, 30, tzinfo=UTC)


@pytest.fixture()
def cfg():
    c = C.load()
    c["topics"] = [t for t in c["topics"] if t["id"] in ("qqq", "memory")]
    c["languages"] = ["en"]
    return c


@pytest.fixture()
def db(tmp_path):
    d = store.connect(tmp_path / "x.sqlite3")
    yield d
    d.close()


def tweet(i, ts):
    return {"id": str(i), "url": f"https://x.com/u/status/{i}", "text": f"$QQQ memory chip note {i}", "lang": "en", "likeCount": 3,
            "createdAt": ts.strftime("%a %b %d %H:%M:%S +0000 %Y"), "author": {"id": str(9000 + i), "userName": f"user{i}"}}


class Net:
    def __init__(self, fail=None, per_page=5):
        self.requests, self.n, self.fail, self.per_page = [], 0, dict(fail or {}), per_page

    def get(self, params):
        self.requests.append(dict(params))
        ex = self.fail.get(len(self.requests))
        if ex:
            raise ex
        q = params["query"]
        s, e = (int(q.split(k)[1].split()[0]) for k in ("since_time:", "until_time:"))
        out = []
        for j in range(self.per_page):
            self.n += 1
            out.append(tweet(self.n, datetime.fromtimestamp(e - 1 - j, UTC)))
        return {"tweets": out, "has_next_page": False, "next_cursor": ""}


def windows(net):
    return [tuple(int(r["query"].split(k)[1].split()[0]) for k in ("since_time:", "until_time:")) for r in net.requests]


def test_first_run_asks_two_hours_and_the_next_run_starts_where_it_ended(db, cfg):
    net = Net()
    a = incremental.run(db, cfg, net, T0, clock=lambda: T0)
    assert a["status"] == "RAN" and a["units_asked"] == 2 and a["advanced"] == 2 and a["new_posts"] == 10
    end0 = int(T0.replace(second=0).timestamp())
    assert set(windows(net)) == {(end0 - 7200, end0)}             # not the whole day
    t1 = T0 + timedelta(hours=2)
    net2 = Net(); net2.n = 100                                   # other posts than the first run's
    b = incremental.run(db, cfg, net2, t1, clock=lambda: t1)
    assert set(windows(net2)) == {(end0, end0 + 7200)}            # exactly the time since the last success: nothing is asked twice
    assert b["duplicates"] == 0
    assert dict(db.execute("SELECT ingest_mode, count(*) FROM x_posts GROUP BY 1")) == {provider.LIVE: 20}
    assert dict(db.execute("SELECT pool, count(*) FROM x_cost_ledger WHERE status='SETTLED' GROUP BY 1")) == {"recent": 4}


def test_a_failed_window_does_not_move_the_query_forward_and_a_long_gap_is_capped(db, cfg):
    e503 = provider.ProviderHTTPError(503, "HTTP_503_NOT_CHARGED")
    cfg["acquisition"]["retry"] = {**(cfg["acquisition"].get("retry") or {}), "max_attempts_per_unit_per_round": 1}
    net = Net(fail={1: e503})
    a = incremental.run(db, cfg, net, T0, clock=lambda: T0)
    assert a["advanced"] == 1 and len(a["interruptions"]) == 1
    st = json.loads(db.execute("SELECT value FROM x_meta WHERE key='incremental_state'").fetchone()[0])
    assert len(st) == 1
    late = T0 + timedelta(hours=20)                               # the job was down for most of a day
    us = incremental.units(db, cfg, late)
    assert all(u["end"] - u["start"] <= 6 * 3600 for u in us) and any(u["gap_left_to_daily_job_seconds"] > 0 for u in us)


def test_a_402_pauses_new_collection_until_the_owner_clears_it(db, cfg):
    e402 = provider.ProviderHTTPError(402, "HTTP_402")
    net = Net(fail={2: e402})
    a = incremental.run(db, cfg, net, T0, clock=lambda: T0)
    assert a["status"] == "PAUSED_PROVIDER_CREDITS" and a["stopped"] == "ERROR:HTTP_402" and a["credit_hold"]["http_status"] == 402
    t1 = T0 + timedelta(hours=2)
    net2 = Net()
    b = incremental.run(db, cfg, net2, t1, clock=lambda: t1)
    assert b["status"] == "PAUSED_PROVIDER_CREDITS" and b["requests"] == 0 and net2.requests == []      # nothing is sent while on hold
    assert incremental.credit_resume(db, "topped up on 2026-11-25", t1)["hold_now"] is None
    c = incremental.run(db, cfg, net2, t1 + timedelta(minutes=5), clock=lambda: t1 + timedelta(minutes=5))
    assert c["status"] == "RAN" and len(net2.requests) == 2


def test_the_receipt_keeps_the_last_successful_update_and_shows_the_hold(db, cfg):
    e402 = provider.ProviderHTTPError(402, "HTTP_402")
    incremental.run(db, cfg, Net(fail={2: e402}), T0, clock=lambda: T0)
    r1 = incremental.write_receipt(db, cfg, T0 + timedelta(minutes=3), {"started_at_utc": store.iso(T0), "collect": {"status": "PAUSED_PROVIDER_CREDITS", "requests": 0, "new_posts": 0}, "build": {"exit": 0, "seconds": 40}})
    assert r1["ok"] is True and r1["last_successful_update_at_utc"] == store.iso(T0 + timedelta(minutes=3)) and r1["credit_hold"]["reason"] == "PROVIDER_CREDITS_EXHAUSTED"
    assert set(r1["pending"]) >= {"new_posts_queue", "history_backlog"}
    r2 = incremental.write_receipt(db, cfg, T0 + timedelta(hours=2), {"started_at_utc": store.iso(T0 + timedelta(hours=2)), "build": {"exit": 1}})
    assert r2["ok"] is False and r2["last_successful_update_at_utc"] == r1["last_successful_update_at_utc"]      # a failed round does not move it
    v = incremental.read_receipt(db, T0 + timedelta(hours=2, minutes=1))
    assert v["state"] == "FRESH" and v["credit_hold"] and v["last_successful_update_at_utc"] == r1["last_successful_update_at_utc"]
    assert incremental.read_receipt(db, T0 + timedelta(hours=9))["state"] == "STALE"
    assert db.execute("SELECT count(*) FROM x_update_log").fetchone()[0] == 2


def test_preview_sends_nothing_and_fits_the_keyword_share(db, cfg):
    full = C.load()
    v = incremental.preview(db, full, T0, Ledger(db, full, clock=lambda: T0))
    assert v["requests_sent"] == 0 and v["queries"]["keyword"] == 8
    assert v["per_day_if_every_page_is_full"]["keyword_posts"] <= v["per_day_if_every_page_is_full"]["keyword_share_cap_posts"]
    assert db.execute("SELECT count(*) FROM x_cost_ledger").fetchone()[0] == 0


def test_cli_incremental_on_hold_sends_nothing_even_without_a_key(db, cfg, tmp_path, capsys, monkeypatch):
    incremental.run(db, cfg, Net(fail={1: provider.ProviderHTTPError(402, "HTTP_402")}), T0, clock=lambda: T0)
    db.commit()
    monkeypatch.delenv("TWITTERAPI_IO_API_KEY", raising=False)
    assert cli.main(["--db", str(tmp_path / "x.sqlite3"), "incremental", "run"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "PAUSED_PROVIDER_CREDITS" and out["requests"] == 0
