"""The focused per-session plan for one target, and the rule that a target only counts posts that name it.

All transports are fakes; no request leaves the machine."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from scripts.x_stance_v1 import batch, config as C, focus, provider, sample, stance, store
from scripts.x_stance_v1.ledger import Ledger

UTC = timezone.utc
NOW = datetime(2026, 11, 25, 2, 0, tzinfo=UTC)                   # 10:00 Shanghai on 11-25; the 11-24 session closed five hours earlier
NAME = "qqq-20261125"


@pytest.fixture()
def cfg():
    c = C.load()
    c["focus"][NAME]["first_session"] = "2026-11-18"             # five sessions: 11-18, 11-19, 11-20, 11-23, 11-24
    return c


@pytest.fixture()
def db(tmp_path):
    d = store.connect(tmp_path / "x.sqlite3")
    yield d
    d.close()


def tweet(i, ts, likes=50, text=None):
    return {"id": str(i), "url": f"https://x.com/u/status/{i}", "text": text or f"$QQQ note {i}", "lang": "en", "likeCount": likes,
            "createdAt": ts.strftime("%a %b %d %H:%M:%S +0000 %Y"), "author": {"id": str(9000 + i), "userName": f"user{i}"}}


class Net:
    def __init__(self, likes=50, fail=None):
        self.likes, self.requests, self.n, self.fail = likes, [], 0, dict(fail or {})

    def get(self, params):
        self.requests.append(dict(params))
        ex = self.fail.get(len(self.requests))
        if ex:
            raise ex
        e = int(params["query"].split("until_time:")[1].split()[0])
        out, page = [], int(params.get("cursor") or 0)
        for j in range(20):
            self.n += 1
            out.append(tweet(self.n, datetime.fromtimestamp(e - 1 - page * 60 - j, UTC), self.likes))
        return {"tweets": out, "has_next_page": True, "next_cursor": str(int(params.get("cursor") or 0) + 1)}


def history(db, end="2026-11-21T05:00:00Z"):
    from scripts.x_stance_v1.ledger import set_meta
    set_meta(db, "history_range", json.dumps({"end_utc_exclusive": end, "start_utc": "2026-08-23T04:00:00Z", "first_ny_date": "2026-08-23", "last_ny_date": "2026-11-20", "days": 90})); db.commit()


def test_plan_has_two_slices_per_completed_session_and_keeps_pools_apart(db, cfg):
    history(db)
    p = focus.build_plan(db, cfg, NAME, NOW)
    assert p["sessions"] == ["2026-11-18", "2026-11-19", "2026-11-20", "2026-11-23", "2026-11-24"]
    assert len(p["units"]) == 10 and [u["round"] for u in p["units"]] == [1] * 5 + [2] * 5
    assert {u["slice"] for u in p["units"] if u["round"] == 1} == {"regular"}
    pools = {(u["session"], u["slice"]): u["pool"] for u in p["units"]}
    assert pools[("2026-11-20", "regular")] == "historical"      # ends inside the frozen historical range
    assert pools[("2026-11-23", "pre_open")] == "recent"         # starts before the range ends but finishes after it: never charged to the historical pool
    assert pools[("2026-11-24", "regular")] == "recent"
    modes = {(u["session"], u["slice"]): u["ingest_mode"] for u in p["units"]}
    assert modes[("2026-11-24", "regular")] == provider.BATCH and modes[("2026-11-20", "regular")] == provider.RETRO
    for u in p["units"]:                                          # the two slices of a session tile its window exactly
        assert u["start"] < u["end"]
    by = {s: sorted((u["start"], u["end"]) for u in p["units"] if u["session"] == s) for s in p["sessions"]}
    assert all(v[0][1] == v[1][0] for v in by.values())


def test_preview_sends_nothing_and_writes_nothing(db, cfg):
    history(db)
    before = db.execute("SELECT count(*) FROM x_sample_plans").fetchone()[0]
    v = focus.preview(db, cfg, NAME, NOW, Ledger(db, cfg, clock=lambda: NOW))
    assert v["requests_sent"] == 0 and v["frozen"] is False and v["units_open"] == 10
    assert v["if_every_page_is_full"]["all_open_units_posts"] == 200 and v["today"]["units_that_fit_today"] == {"historical": 6, "recent": 4}
    assert db.execute("SELECT count(*) FROM x_sample_plans").fetchone()[0] == before
    assert db.execute("SELECT count(*) FROM x_cost_ledger").fetchone()[0] == 0


def test_run_reads_each_unit_once_and_a_second_run_asks_nothing(db, cfg):
    history(db)
    net = Net()
    out = focus.run(db, cfg, net, NAME, NOW, clock=lambda: NOW)
    assert out["stopped"] is None and out["units_asked"] == 10 and len(net.requests) == 10
    assert out["like_floor_check"]["honoured"] is True
    assert all("$QQQ min_faves:20 -filter:replies lang:en since_time:" in r["query"] for r in net.requests)
    led = {(r[0], r[1]): r[2] for r in db.execute("SELECT pool, job_type, count(*) FROM x_cost_ledger WHERE status='SETTLED' GROUP BY 1,2")}
    assert led == {("historical", "focus"): 6, ("recent", "recent"): 4}       # the recent pool is entered through its keyword share
    modes = dict(db.execute("SELECT ingest_mode, count(*) FROM x_posts GROUP BY 1"))
    assert modes == {provider.RETRO: 160, provider.BATCH: 40}
    again = focus.run(db, cfg, net, NAME, NOW + timedelta(hours=1), clock=lambda: NOW + timedelta(hours=1))
    assert again["units_asked"] == 0 and again["units_skipped_already_read"] == 10 and len(net.requests) == 10 and again["plan_reused"] is True
    pr = focus.progress(db, cfg, NAME)
    assert pr["returned"] == 200 and pr["new_posts"] == 200 and all(s["units_read"] == 2 for s in pr["per_session"])


def test_run_stops_when_enough_new_posts_were_stored(db, cfg):
    history(db)
    cfg["focus"][NAME]["max_new_posts"] = 60
    out = focus.run(db, cfg, Net(), NAME, NOW, clock=lambda: NOW)
    assert out["stopped"] == "TARGET_NEW_POSTS_REACHED" and out["units_asked"] == 3


def test_run_stops_after_one_page_if_the_like_operator_is_not_honoured(db, cfg):
    history(db)
    net = Net(likes=3)
    out = focus.run(db, cfg, net, NAME, NOW, clock=lambda: NOW)
    assert out["stopped"] == "LIKE_OPERATOR_NOT_HONOURED" and len(net.requests) == 1
    assert out["like_floor_check"]["lowest_like_count"] == 3


def test_a_503_leaves_the_unit_open_and_the_job_goes_on(db, cfg):
    history(db)
    e503 = provider.ProviderHTTPError(503, "HTTP_503_NOT_CHARGED")
    cfg["acquisition"]["retry"] = {**(cfg["acquisition"].get("retry") or {}), "max_attempts_per_unit_per_round": 1}
    net = Net(fail={2: e503})
    out = focus.run(db, cfg, net, NAME, NOW, clock=lambda: NOW)
    assert out["transient_errors"] == 1 and len(out["interruptions"]) == 1
    assert out["interruptions"][0]["reason"] == "ERROR:HTTP_503_NOT_CHARGED"
    pr = focus.progress(db, cfg, NAME)
    assert sum(1 for s in pr["per_session"] for o in s["open"]) == 1
    assert [r for r in pr["ledger"] if r["status"] == "RELEASED"][0]["requests"] == 1      # the 503 was not charged


def test_a_post_counts_for_qqq_only_if_its_own_text_names_qqq(db, cfg):
    t0 = datetime(2026, 11, 24, 15, 0, tzinfo=UTC)
    texts = {1: "$QQQ looks strong into the close", 2: "Nasdaq 100 futures are higher", 3: "long $TQQQ here", 4: "qqq puts printing", 5: "The Nasdaq-100 and QQQ, both green"}
    for i, text in texts.items():
        p = provider.normalize_twitterapi_io(tweet(i, t0, text=text), now=store.iso(NOW), batch="t", mode=provider.RETRO)
        store.put_post(db, p)
    db.commit()
    c = sample.with_scope(cfg, "keyword", None)
    stance.map_sample(db, c)
    q = dict(db.execute("SELECT post_id, quality FROM x_post_topics WHERE target_id='QQQ' AND topic_version=?", (sample.sample_version(c),)))
    assert q["1"] == "PENDING" and q["5"] == "PENDING"
    assert q["2"] == sample.FILTERED_NOT_NAMED                    # index talk: stays in the store and under the market target, not under QQQ
    assert "3" not in q and "4" not in q                          # never matched the QQQ topic's keywords at all
    assert sample.target_named("long $TQQQ here", "QQQ", cfg) is False and sample.target_named("qqq puts", "QQQ", cfg) is True
    assert sample.target_named("anything", "industry:memory", cfg) is True


def test_the_rule_set_has_a_new_version_and_the_like_floor_is_unchanged(cfg):
    r = sample.rules(cfg)
    assert r["min_likes"] == 20 and r["version"] == "account-rules-v2-20261125"
    old = json.loads(json.dumps(cfg)); del old["aggregation"]["account_rules"]["target_must_be_named"]; old["aggregation"]["account_rules"]["version"] = "account-rules-v1-20261123"
    c2 = json.loads(json.dumps(cfg)); c2["aggregation"]["account_rules"]["target_must_be_named"] = {}
    assert sample.rules_version(c2).split("-")[-1] == sample.rules_version(old).split("-")[-1]   # without the naming rule the content hash is the earlier one: stored versions keep their names
    assert sample.rules_version(cfg) != sample.rules_version(old)


def test_preflight_binding_is_not_moved_by_the_focus_plan_or_the_new_rule(db, cfg):
    env = {"TWITTERAPI_IO_API_KEY": "tw-FAKE-not-a-real-key-000"}
    old = json.loads(json.dumps(cfg)); old.pop("focus"); del old["aggregation"]["account_rules"]["target_must_be_named"]
    assert batch.binding(db, cfg, env) == batch.binding(db, old, env)


def test_a_deeper_plan_continues_the_first_plans_pages_and_never_rereads_them(db, cfg):
    history(db)
    cfg["focus"]["qqq-20261126-depth"]["first_session"] = "2026-11-18"
    net = Net()
    focus.run(db, cfg, net, NAME, NOW, clock=lambda: NOW)                          # page 1 of every unit
    first = [r["cursor"] for r in net.requests]
    assert len(first) == 10 and all(c == "" for c in first)
    assert focus.query_hash(cfg, "qqq-20261126-depth") == focus.query_hash(cfg, NAME)
    out = focus.run(db, cfg, net, "qqq-20261126-depth", NOW, clock=lambda: NOW)
    second = [r["cursor"] for r in net.requests[10:]]
    assert len(second) == 10 and all(c == "1" for c in second)                     # page 2 only: no first page is paid for twice
    assert out["units_asked"] == 10 and focus.progress(db, cfg, "qqq-20261126-depth")["returned"] == 400
    bad = json.loads(json.dumps(cfg)); bad["focus"]["qqq-20261126-depth"]["query"] = "$QQQ lang:en"
    with pytest.raises(focus.FocusError):
        focus.query_hash(bad, "qqq-20261126-depth")
