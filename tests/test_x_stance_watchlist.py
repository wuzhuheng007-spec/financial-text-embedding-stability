"""Watched authors: import, identity, author queries on the shared ledger, the three panels, the reading page.

Everything is synthetic. The handles below are invented; the provider is a scripted fake; no model is called. The
owner's real list is private and is not in this repository - one optional test reads it from X_WATCHLIST_SEED.
"""
from __future__ import annotations

import copy
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.x_stance_v1 import aggregate, batch, config as C, pipeline, provider, sample, snapshots, stance, store, watchlist
from scripts.x_stance_v1.ledger import Ledger
from scripts.x_stance_v1.sessions import Calendar, calendar_window

UTC = timezone.utc
NOW = datetime(2026, 11, 20, 6, 0, tzinfo=UTC)               # 14:00 Shanghai on 2026-11-20; the last completed New York date is 2026-11-19
DAY = date(2026, 11, 19)
KEY = {"TWITTERAPI_IO_API_KEY": "tw-FAKE-not-a-real-key-000"}


def seed(n=6, version="synthetic-list-1", extra=()):
    acc = []
    for i in range(1, n + 1):
        acc.append({"watch_id": f"watch-{i:02d}", "display_name_from_image": f"Author {i}", "handle": f"Synth_User{i}", "source_lists": ["list-a"], "requested_monitoring": True,
                    "source_type": "USER_PROVIDED_SCREENSHOT", "stable_author_id": None, "identity_status": "SCREENSHOT_HANDLE_PENDING_PROVIDER_ID", "scheduling_priority": 2,
                    "aliases": [], "handle_candidates": []})
    acc[-1].update({"scheduling_priority": 1, "aliases": ["口罩哥"]})                 # a named account of the rule set, asked first
    acc.append({"watch_id": "watch-90", "display_name_from_image": "Ambiguous Person", "handle": None, "source_lists": ["list-b"], "requested_monitoring": True,
                "source_type": "USER_PROVIDED_SCREENSHOT", "stable_author_id": None, "identity_status": "AMBIGUOUS_HANDLE_BLOCKED", "scheduling_priority": 2, "aliases": [],
                "handle_candidates": [{"handle": "AmbigCandidat", "basis": "third-party directory"}], "handle_text_in_image": "AmbigCandidate"})
    acc += list(extra)
    return {"schema_version": "x-platform-watchlist-seed-v1", "watchlist_version": version, "scope": "PLATFORM_READ_ONLY_MONITORING", "accounts": acc}


@pytest.fixture()
def cfg():
    c = C.load()
    c["topics"] = [t for t in c["topics"] if t["id"] == "qqq"]
    c["languages"] = ["en"]
    c["aggregation"]["proposed_low_sample_author_threshold"] = 1
    return c


@pytest.fixture()
def db(tmp_path):
    d = store.connect(tmp_path / "x.sqlite3")
    yield d
    d.close()


def unlock(db, cfg):
    batch._store_receipt(db, {"finished_at_utc": store.iso(NOW), "verdict": "PASS", "binding": batch.binding(db, cfg, KEY), "checks": {"history_search_verified": False}, "fail_reasons": []})


def tw(i, ts, author_id, handle, text, likes=100, followers=5000):
    return {"id": str(i), "url": f"https://x.com/{handle}/status/{i}", "text": text, "lang": "en", "likeCount": likes, "createdAt": ts.strftime("%a %b %d %H:%M:%S +0000 %Y"),
            "author": {"id": str(author_id), "userName": handle, "name": f"name of {handle}", "description": f"profile of {handle}", "followers": followers}}


class Fake:
    """`authors`: asked handle (lower case) -> list of tweets it returns, newest first, 20 per page.
    Any other query is a keyword query and returns `keyword` (same paging)."""

    def __init__(self, authors=None, keyword=None):
        self.authors, self.keyword, self.requests = {k.lower(): v for k, v in (authors or {}).items()}, keyword or [], []

    def get(self, params):
        self.requests.append(dict(params))
        q = params["query"]
        s, e = int(q.split("since_time:")[1].split()[0]), int(q.split("until_time:")[1].split()[0])
        rows = self.authors.get(q.split()[0][5:].lower(), []) if q.startswith("from:") else self.keyword
        rows = [t for t in rows if s <= int(datetime.strptime(t["createdAt"], "%a %b %d %H:%M:%S %z %Y").timestamp()) < e]
        page = int(params["cursor"] or 0)
        part = rows[page * 20:(page + 1) * 20]
        more = len(rows) > (page + 1) * 20
        return {"tweets": part, "has_next_page": more, "next_cursor": str(page + 1) if more else ""}

    def asked(self):
        return [r["query"].split()[0] for r in self.requests]


def at(hour, minute=0, day=DAY):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


def ledger_rows(db, job):
    return db.execute("SELECT count(*), COALESCE(sum(returned),0), COALESCE(sum(settled_credits),0) FROM x_cost_ledger WHERE job_type=? AND status='SETTLED'", (job,)).fetchone()[:]


# ============================================================ import
def test_import_is_idempotent_merges_duplicates_and_never_invents_an_id(db, cfg):
    dup = {"watch_id": "watch-77", "display_name_from_image": "Author 2 again", "handle": "SYNTH_user2", "source_lists": ["list-c"], "requested_monitoring": True,
           "source_type": "USER_PROVIDED_SCREENSHOT", "stable_author_id": None, "identity_status": "SCREENSHOT_HANDLE_PENDING_PROVIDER_ID", "scheduling_priority": 2, "aliases": ["nick"], "handle_candidates": []}
    bad_id = {**dup, "watch_id": "watch-78", "handle": "Other_One", "stable_author_id": "12345"}
    bad_syntax = {**dup, "watch_id": "watch-79", "handle": "has space"}
    no_handle = {**dup, "watch_id": "watch-80", "handle": None}
    first = watchlist.import_seed(db, seed(extra=[dup, bad_id, bad_syntax, no_handle]), NOW)
    assert (first["in_seed"], first["added"], first["merged"], first["authors_in_store"]) == (11, 7, 1, 7)
    assert {r["reason"] for r in first["rejected"]} == {"SEED_MAY_NOT_SUPPLY_A_STABLE_ID", "HANDLE_SYNTAX", "NO_HANDLE_AND_NOT_MARKED_AMBIGUOUS"}
    a2 = watchlist.get(db, "watch-02")
    assert a2["source_lists"] == ["list-a", "list-c"] and a2["aliases"] == ["nick"] and a2["display_name"] == "Author 2"       # the same person on a second list: one row, more sources
    again = watchlist.import_seed(db, seed(extra=[dup]), NOW + timedelta(hours=1))
    assert (again["added"], again["merged"], again["unchanged"], again["authors_in_store"]) == (0, 0, 8, 7)
    s = watchlist.summary(db, cfg)
    assert (s["logical_authors"], s["with_handle"], s["blocked_ambiguous"], s["ready_for_queries"], s["stable_ids_bound"], s["collected_at_least_once"]) == (7, 6, 1, 6, 0, 0)
    assert s["selected_at_utc"] == store.iso(NOW) and "nothing is followed on X" in s["scope"]
    assert all(a["stable_author_id"] is None and a["availability"] == "WAITING_FOR_FIRST_COLLECTION" for a in watchlist.authors(db))


@pytest.mark.skipif(not os.environ.get("X_WATCHLIST_SEED"), reason="the owner's list is private; set X_WATCHLIST_SEED to its path")
def test_the_owners_list_imports_as_25_logical_authors_24_ready_and_one_blocked(db, cfg):
    real = json.loads(Path(os.environ["X_WATCHLIST_SEED"]).read_text(encoding="utf-8"))
    res = watchlist.import_seed(db, real, NOW)
    s = watchlist.summary(db, cfg)
    assert (res["added"], res["rejected"], s["logical_authors"], s["with_handle"], s["ready_for_queries"], s["blocked_ambiguous"], s["stable_ids_bound"]) == (25, [], 25, 24, 24, 1, 0)
    blocked = [a for a in watchlist.authors(db) if a["identity_status"] == "AMBIGUOUS_HANDLE_BLOCKED"]
    assert len(blocked) == 1 and blocked[0]["handle"] is None and len(blocked[0]["handle_candidates"]) == 1 and blocked[0]["provenance"]["handle_text_in_image"]
    named = sample.summary(sample.attach_named(db, cfg))
    assert named["named_accounts_not_identified"] == 0 and all(n["identified"] and not n["id_bound"] for n in named["named_accounts"])
    q = watchlist.register_queries(db, cfg)
    assert len(q) == 24 and [x["scheduling_priority"] for x in q[:2]] == [1, 1] and not any(blocked[0]["watch_id"] == x["watch_id"] for x in q)
    assert watchlist.import_seed(db, real, NOW)["unchanged"] == 25


# ============================================================ plan and budget
def test_the_recent_plan_carries_the_author_queries_and_splits_the_pool_without_adding_to_it(db, cfg):
    led = Ledger(db, cfg)
    assert led.pool["recent"] == (3000, 45000) and led.sub == {"watchlist": ("recent", 600, 9000), "recent": ("recent", 2400, 36000)}
    assert 600 + 2400 == 3000 and led.pool["historical"] == (2000, 30000) and led.day_posts == 5000
    watchlist.import_seed(db, seed(), NOW)
    plan = batch.plan_recent(db, cfg, DAY, NOW)
    w = plan["watch"]
    assert [u["watch_id"] for u in w["units"]] == ["watch-06", "watch-01", "watch-02", "watch-03", "watch-04", "watch-05"]       # priority 1 first, then by id
    assert w["not_asked"] == ["watch-90"] and w["posts_budget"] == 600 and w["first_round_max_posts"] == 120 and w["max_pages_per_author"] == 3
    a, b = (int(x.timestamp()) for x in calendar_window(DAY))
    assert all((u["start"], u["end"]) == (a, b) for u in w["units"]) and w["keyword_independent"]
    units = len(plan["units"])
    assert plan["pages_per_unit"] == max(1, 2400 // 20 // units) and plan["keyword_posts_budget"] == 2400      # the keyword depth is cut BEFORE the plan is frozen
    assert batch.plan_recent(db, cfg, DAY, NOW)["sha256"] == plan["sha256"]
    watchlist.set_enabled(db, "watch-01", False, NOW)
    assert len(batch.plan_recent(db, cfg, DAY, NOW)["watch"]["units"]) == 6                                   # a frozen plan keeps its authors; the switch works from the next plan
    assert len(batch.plan_recent(db, cfg, DAY + timedelta(days=1), NOW + timedelta(days=1))["watch"]["units"]) == 5
    over = copy.deepcopy(cfg); over["allocation"]["recent"]["sub_pools"]["watchlist"]["max_returned_posts_per_day"] = 700
    with pytest.raises(ValueError):
        Ledger(db, over)                                                                                     # shares can never add up to more than their pool


def test_collection_binds_ids_only_from_matching_usernames_and_tells_the_outcomes_apart(db, cfg):
    watchlist.import_seed(db, seed(), NOW)
    unlock(db, cfg)
    fake = Fake(authors={
        "Synth_User1": [tw(101, at(15), 9001, "Synth_User1", "$QQQ adding here"), tw(102, at(14), 9001, "Synth_User1", "BTC looks strong today")],
        "Synth_User2": [],                                                                                    # wrote nothing visible that day
        "Synth_User3": [tw(301, at(15), 9003, "SomebodyElse", "$QQQ view")],                                  # the provider answered with another account
        "Synth_User4": [dict(tw(401, at(15), 9004, "x", "$QQQ view"), author={"id": "9004"})],                # no userName in the response
        "Synth_User6": [tw(601, at(13), 9006, "synth_user6", "$QQQ I am selling", followers=3_000_000)]},     # case differs only
        keyword=[tw(900 + i, at(12, i), 8000 + i, f"kw{i}", f"$QQQ keyword post {i}") for i in range(5)])
    out = batch.run_daily(db, cfg, KEY, lambda: fake, NOW, clock=lambda: NOW)
    w = out["recent"]["watch"]
    assert out["status"] == "RAN" and w["authors_asked"] == 6 and w["not_asked"] == ["watch-90"] and not any("Ambig" in q for q in fake.asked())
    assert fake.asked()[:6] == ["from:Synth_User6", "from:Synth_User1", "from:Synth_User2", "from:Synth_User3", "from:Synth_User4", "from:Synth_User5"]
    st = {a["watch_id"]: (a["identity_status"], a["availability"], a["stable_author_id"]) for a in watchlist.authors(db)}
    assert st["watch-01"] == ("RESOLVED_FROM_PROVIDER", "COLLECTED", "9001") and st["watch-06"] == ("RESOLVED_FROM_PROVIDER", "COLLECTED", "9006")
    assert st["watch-02"] == ("SCREENSHOT_HANDLE_PENDING_PROVIDER_ID", "NO_VISIBLE_POSTS_IN_WINDOW", None)     # not "neutral", not "failed", and no id made up
    assert st["watch-03"] == ("AUTHOR_MISMATCH", "AUTHOR_MISMATCH", None) and st["watch-04"][1:] == ("RETURNED_WITHOUT_USERNAME_ID_NOT_BOUND", None)
    assert st["watch-05"][1] == "NO_VISIBLE_POSTS_IN_WINDOW" and st["watch-90"] == ("AMBIGUOUS_HANDLE_BLOCKED", "WAITING_FOR_FIRST_COLLECTION", None)
    mm = watchlist.get(db, "watch-03")["mismatch"]
    assert mm["asked_for"] == "Synth_User3" and mm["returned"][0]["user_names"] == ["SomebodyElse"]
    assert db.execute("SELECT count(*) FROM x_posts WHERE post_id='301'").fetchone()[0] == 1                  # the paid response is kept ...
    assert "9003" not in watchlist.bound_ids(db) and set(watchlist.bound_ids(db)) == {"9001", "9006"}         # ... but that account is not a watched author
    n, returned, credits = ledger_rows(db, "watchlist")
    assert (n, returned, credits) == (6, 5, 15 * (2 + 1 + 1 + 1 + 1 + 1))                                     # two empty pages still cost the 15-credit minimum each
    u = out["usage"]
    assert u["sub_pools"]["watchlist"]["returned_posts"] == 5 and u["sub_pools"]["recent"]["returned_posts"] == u["pools"]["recent"]["returned_posts"] - 5
    assert u["pools"]["recent"]["credits"] == u["sub_pools"]["watchlist"]["credits"] + u["sub_pools"]["recent"]["credits"] == u["today"]["credits"]
    later = batch.plan_recent(db, cfg, DAY + timedelta(days=1), NOW + timedelta(days=1))
    assert "watch-03" not in [x["watch_id"] for x in later["watch"]["units"]] and "watch-03" in later["watch"]["not_asked"]      # a mismatched account is not asked again


def test_the_author_share_stops_at_600_posts_breadth_first_and_leaves_the_keyword_share_alone(db, cfg):
    watchlist.import_seed(db, seed(), NOW)
    unlock(db, cfg)
    many = lambda aid, handle: [tw(aid * 1000 + i, at(20) - timedelta(seconds=i), aid, handle, f"$QQQ note {i}") for i in range(400)]
    fake = Fake(authors={f"Synth_User{i}": many(9000 + i, f"Synth_User{i}") for i in range(1, 7)},
                keyword=[tw(50000 + i, at(20) - timedelta(seconds=i), 70000 + i, f"kw{i}", f"$QQQ keyword {i}") for i in range(5000)])
    out = batch.run_daily(db, cfg, KEY, lambda: fake, NOW, clock=lambda: NOW)
    w = out["recent"]["watch"]
    asked = [q for q in fake.asked() if q.startswith("from:")]
    assert asked[:6] == asked[6:12] == asked[12:18] and len(asked) == 18                                       # every author gets page 1 before anyone gets page 2; three rounds at most
    assert ledger_rows(db, "watchlist")[:2] == (18, 360) and w["stopped"] is None                              # 6 authors x 3 pages x 20: the page limit ended it, inside the 600 share
    kw = ledger_rows(db, "recent")
    depth = batch.plan_recent(db, cfg, DAY, NOW)["pages_per_unit"]
    assert depth == 2400 // 20 // 4 and kw[1] == depth * 20                                                   # the keyword plan was cut to its own 2,400 share (here every post falls in one slice)
    u = out["usage"]
    assert u["pools"]["recent"]["returned_posts"] == 360 + kw[1] <= 3000 and u["today"]["returned_posts"] <= 5000
    tight = copy.deepcopy(cfg); tight["allocation"]["recent"]["sub_pools"]["watchlist"] = {"max_returned_posts_per_day": 60, "cost_cap_usd_per_day": "0.009"}
    db2 = store.connect(Path(db.execute("PRAGMA database_list").fetchone()[2]).with_name("tight.sqlite3"))
    watchlist.import_seed(db2, seed(), NOW); unlock(db2, tight)
    fake2 = Fake(authors=fake.authors)
    out2 = batch.run_daily(db2, tight, KEY, lambda: fake2, NOW, clock=lambda: NOW)
    assert out2["recent"]["watch"]["stopped"] == "LIMIT_REACHED:SUBPOOL_WATCHLIST_POSTS" and ledger_rows(db2, "watchlist")[:2] == (3, 60)
    st = {a["watch_id"]: a["availability"] for a in watchlist.authors(db2)}
    assert [st[k] for k in ("watch-06", "watch-01", "watch-02")] == ["COLLECTED"] * 3 and st["watch-03"].startswith("NOT_REACHED:LIMIT_REACHED")     # "not reached", not "wrote nothing"
    db2.close()


def test_a_rename_is_followed_by_id_and_the_next_plan_asks_the_new_handle(db, cfg):
    watchlist.import_seed(db, seed(2), NOW)
    unlock(db, cfg)
    f1 = Fake(authors={"Synth_User1": [tw(1, at(15), 9001, "Synth_User1", "$QQQ view")]})
    batch.run_daily(db, cfg, KEY, lambda: f1, NOW, clock=lambda: NOW)
    assert watchlist.get(db, "watch-01")["stable_author_id"] == "9001"
    d2, now2 = DAY + timedelta(days=1), NOW + timedelta(days=1)
    f2 = Fake(authors={"Synth_User1": [tw(2, at(15, day=d2), 9001, "Renamed_Now", "$QQQ still here")]})      # X still serves the old name's results under the new user name
    out = batch.run_daily(db, cfg, KEY, lambda: f2, now2, clock=lambda: now2)
    a = watchlist.get(db, "watch-01")
    assert out["recent"]["watch"]["identity"]["renamed"] == [{"watch_id": "watch-01", "from": "Synth_User1", "to": "Renamed_Now"}]
    assert (a["handle"], a["stable_author_id"], a["identity_status"]) == ("Renamed_Now", "9001", "RESOLVED_FROM_PROVIDER")
    assert [h["handle"] for h in a["handle_history"]] == ["Synth_User1", "Renamed_Now"] and a["handle_history"][1]["basis"] == "PROVIDER_REPORTED_NEW_USERNAME_FOR_THE_SAME_ID"
    d3 = d2 + timedelta(days=1)
    assert {u["watch_id"]: u["handle"] for u in batch.plan_recent(db, cfg, d3, now2 + timedelta(days=1))["watch"]["units"]}["watch-01"] == "Renamed_Now"
    assert watchlist.import_seed(db, seed(2), now2)["added"] == 0 and len(watchlist.authors(db)) == 3        # re-importing the old handle does not create a second person
    other = Fake(authors={"Renamed_Now": [tw(3, at(15, day=d3), 4444, "Renamed_Now", "$QQQ impostor")]})     # someone else now holds the name
    now3 = now2 + timedelta(days=1)
    batch.run_daily(db, cfg, KEY, lambda: other, now3, clock=lambda: now3)
    a = watchlist.get(db, "watch-01")
    assert a["stable_author_id"] == "9001" and a["availability"] == "AUTHOR_MISMATCH_IN_THIS_WINDOW" and "4444" not in watchlist.bound_ids(db)


def test_days_and_the_rolling_window_are_one_ledger_for_both_jobs(db, cfg):
    watchlist.import_seed(db, seed(), NOW)
    unlock(db, cfg)
    led = Ledger(db, cfg, clock=lambda: NOW)
    for _ in range(30):
        led.settle(led.reserve(pool="recent", job_type="watchlist"), returned=20, unique_ids=20)
    with pytest.raises(Exception, match="SUBPOOL_WATCHLIST_POSTS"):
        led.reserve(pool="recent", job_type="watchlist")
    for _ in range(120):
        led.settle(led.reserve(pool="recent"), returned=20, unique_ids=20)                                    # the keyword share is untouched by the author share being full ...
    with pytest.raises(Exception, match="SUBPOOL_RECENT_POSTS|POOL_RECENT_POSTS"):
        led.reserve(pool="recent")                                                                             # ... and ends at its own 2,400
    with pytest.raises(Exception, match="POOL_RECENT_POSTS"):
        led.reserve(pool="recent", is_preflight=True, job_type="preflight")                                    # the preflight has no allowance of its own: a full pool refuses it too
    u = led.usage(NOW)
    assert u["sub_pools"]["watchlist"]["returned_posts"] == 600 and u["sub_pools"]["recent"]["returned_posts"] == 2400 and u["pools"]["recent"]["returned_posts"] == 3000
    tomorrow = NOW + timedelta(hours=11)                                                                       # past midnight in Shanghai
    led2 = Ledger(db, cfg, clock=lambda: tomorrow)
    assert led2.usage(tomorrow)["sub_pools"]["watchlist"]["returned_posts"] == 0 and led2.reserve(pool="recent", job_type="watchlist")
    assert led2.usage(tomorrow)["rolling"]["credits"] >= 15 * 3000                                              # ... while the rolling 720 hours still remember yesterday
    small = copy.deepcopy(cfg); small["budget"]["rolling_all_jobs_cost_cap_usd"] = "0.10"
    with pytest.raises(Exception, match="ROLLING_USD"):
        Ledger(db, small, clock=lambda: tomorrow).reserve(pool="recent", job_type="watchlist")


# ============================================================ one stored copy, three panels
def answer(pid, lab):
    return json.dumps({"post_id": str(pid), "topic_id": "qqq", "target_id": "QQQ", "is_relevant": True, "quality_status": "OK", "stance": lab, "evidence_text": "$QQQ", "reason_short": None})


def collected(db, cfg):
    """Day 1: author 1 is watched and bullish (posts 101 keyword-free BTC, 102 $QQQ, 103 $QQQ with 3 likes); author 6 is
    the named account and bearish; the keyword search returns three unwatched accounts AND post 102 again."""
    watchlist.import_seed(db, seed(), NOW)
    unlock(db, cfg)
    p102 = tw(102, at(15), 9001, "Synth_User1", "$QQQ adding here")
    fake = Fake(authors={"Synth_User1": [tw(103, at(16), 9001, "Synth_User1", "$QQQ small aside", likes=3), p102, tw(101, at(14), 9001, "Synth_User1", "BTC looks strong today")],
                         "Synth_User6": [tw(601, at(13), 9006, "Synth_User6", "$QQQ I am selling", followers=3_000_000)]},
                keyword=[p102] + [tw(900 + i, at(12, i), 8000 + i, f"kw{i}", f"$QQQ keyword post {i}") for i in range(3)])
    out = batch.run_daily(db, cfg, KEY, lambda: fake, NOW, clock=lambda: NOW)
    base = sample.attach_named(db, cfg)
    stance.map_all(db, base)
    labels = {"102": "bullish", "601": "bearish", "900": "bearish", "901": "bearish", "902": "neutral"}
    calls = []

    def model(prompt):
        pid = next(k for k in labels if f"keyword post {int(k) - 900}" in prompt or (k == "102" and "adding here" in prompt) or (k == "601" and "I am selling" in prompt))
        calls.append(pid)
        return answer(pid, labels[pid])
    live = [sample.sample_version(c) for c in sample.live_configs(db, base)]
    run = stance.run(db, base, model, now="2026-11-20T07:00:00Z", versions=live)
    return base, out, run, calls, fake


def test_a_post_found_both_ways_is_stored_once_charged_twice_and_inferred_once(db, cfg):
    base, out, run, calls, fake = collected(db, cfg)
    assert db.execute("SELECT count(*) FROM x_posts WHERE post_id='102'").fetchone()[0] == 1
    assert watchlist.post_sources(db, "twitterapi_io", "102") == ["keyword:qqq", "watch:watch-01"] and watchlist.post_sources(db, "twitterapi_io", "103") == ["watch:watch-01"]
    charged = db.execute("SELECT COALESCE(sum(returned),0) FROM x_cost_ledger").fetchone()[0]
    assert charged == 4 + 4 and db.execute("SELECT count(*) FROM x_posts").fetchone()[0] == 7                 # 8 posts were returned and paid for; 7 distinct posts are stored
    assert sorted(calls) == ["102", "601", "900", "901", "902"] and run["model_calls"] == 5                   # post 102 sits in all three panels and is inferred once
    assert db.execute("SELECT count(*) FROM x_stance_labels WHERE post_id='102'").fetchone()[0] == 1
    assert stance.run(db, base, lambda p: 1 / 0, now="2026-11-20T08:00:00Z", versions=[sample.sample_version(c) for c in sample.live_configs(db, base)])["model_calls"] == 0


def test_three_panels_have_their_own_members_denominators_and_series(db, cfg):
    base, *_ = collected(db, cfg)
    kw, wl, co = sample.scoped_configs(db, base)
    assert [sample.scope_of(c) for c in (kw, wl, co)] == ["keyword", "watchlist", "combined"] and len({sample.sample_version(c) for c in (kw, wl, co)}) == 3
    member = lambda c: {r[0]: r[1] for r in db.execute("SELECT post_id, quality FROM x_post_topics WHERE topic_version=?", (sample.sample_version(c),))}
    assert set(member(kw)) == {"102", "900", "901", "902"}                                                    # 103 and 601 came only from author queries: not in the keyword sample
    assert member(wl) == {"102": "PENDING", "103": "FILTERED_LOW_LIKES", "601": "PENDING"}                    # 101 (BTC) names no target; 103 is below the like floor
    assert set(member(co)) == {"102", "103", "601", "900", "901", "902"}
    a, b = calendar_window(DAY)
    res = {}
    for c in (kw, wl, co):
        act = snapshots.activation(c)
        rows = aggregate.rows_for_window(db, topic_id="qqq", target_id="QQQ", start=a, end=b, mode="BATCH_AFTER_CLOSE", act=act)[0]
        prog = aggregate.progress(db, topic_id="qqq", target_id="QQQ", start=a, end=b, vocab_version=sample.sample_version(c), act=act)
        res[sample.scope_of(c)] = (aggregate.aggregate(rows, collection_status="PLANNED_SAMPLE", threshold=1, pending=prog["pending"], cfg=c), prog)
    k, w, m = res["keyword"][0], res["watchlist"][0], res["combined"][0]
    assert k["accounts"] == {"bullish": 1, "bearish": 2, "neutral": 1} and k["accounts_total"] == 4
    assert w["accounts"] == {"bullish": 1, "bearish": 1, "neutral": 0} and res["watchlist"][1]["filtered_low_likes"] == 1 and res["watchlist"][1]["pending"] == 0
    assert m["accounts"] == {"bullish": 1, "bearish": 3, "neutral": 1} and m["accounts_total"] == 5           # author 1 is ONE account in the combined panel, not two
    assert w["accounts"]["neutral"] == 0                                                                      # the filtered post and the BTC post did not become neutral votes
    # weights: the named account (alias in the private list) counts 5; equal-weight shares sit beside the weighted ones
    assert w["weighting"]["weighted_votes"] == {"bullish": 1.0, "bearish": 5.0, "neutral": 0} and w["shares_equal_weight"]["bearish"] == 0.5 and w["state"] == "bearish"
    sens = w["weighting"]["without_heaviest_account"]
    assert sens["removed_weight"] == 5.0 and sens["leader"] == "bullish" and sens["leader_changes"] is True and w["weighting"]["voting_accounts"] == 2
    assert w["weighting"]["largest_single_account_share_of_weight"] == pytest.approx(5 / 6)


def test_adding_the_list_does_not_move_the_keyword_series_and_panels_are_materialised_apart(db, cfg):
    cal = Calendar.load(store.REPO_ROOT / cfg["time"]["exchange_calendar"]["path"], cfg["time"]["exchange_calendar"]["sha256"])
    unlock(db, cfg)
    kw_only = Fake(keyword=[tw(900 + i, at(12, i), 8000 + i, f"kw{i}", f"$QQQ keyword post {i}") for i in range(3)])
    batch.run_daily(db, cfg, KEY, lambda: kw_only, NOW, clock=lambda: NOW)
    stance.map_all(db, cfg)
    stance.run(db, cfg, lambda p: answer(900 + int(p.split("keyword post ")[1].split()[0]), "bearish"), now="2026-11-20T07:00:00Z")
    t1 = datetime(2026, 11, 20, 8, 0, tzinfo=UTC)
    first = pipeline.build_daily(db, cfg, cal, mode="AUTO", now=t1)
    assert first["panels"] == {}                                                                              # no list yet: one panel, as before
    before = {r[0]: (r[1], r[2], r[3]) for r in db.execute("SELECT snapshot_id, series_key, vocab_version, first_committed_at_utc FROM x_signal_snapshots")}
    view_before = db.execute("SELECT count(*) FROM x_daily_sentiment").fetchone()[0]
    watchlist.import_seed(db, seed(), t1)
    d2, now2 = DAY + timedelta(days=1), NOW + timedelta(days=1)
    f2 = Fake(authors={"Synth_User1": [tw(102, at(15, day=d2), 9001, "Synth_User1", "$QQQ adding here")]}, keyword=[])
    batch.run_daily(db, cfg, KEY, lambda: f2, now2, clock=lambda: now2)
    base = sample.attach_named(db, cfg)
    stance.map_all(db, base)
    stance.run(db, base, lambda p: answer(102, "bullish"), now=store.iso(now2 + timedelta(hours=1)), versions=[sample.sample_version(c) for c in sample.live_configs(db, base)])
    out = pipeline.build_daily(db, base, cal, mode="AUTO", now=now2 + timedelta(hours=2))
    assert set(out["panels"]) == {"watchlist", "combined"} and out["panels"]["watchlist"]["calendar_windows"] >= 1
    after = {r[0]: (r[1], r[2], r[3]) for r in db.execute("SELECT snapshot_id, series_key, vocab_version, first_committed_at_utc FROM x_signal_snapshots")}
    assert all(after[k] == v for k, v in before.items())                                                      # every earlier snapshot is still there, unchanged
    kw_ver = sample.sample_version(base)
    labels = {r[0] for r in db.execute("SELECT window_label FROM x_daily_sentiment WHERE window_kind='calendar_day'")}
    assert DAY.isoformat() in labels and db.execute("SELECT count(*) FROM x_daily_sentiment").fetchone()[0] >= view_before
    day1 = json.loads(db.execute("SELECT payload_json FROM x_daily_sentiment WHERE window_kind='calendar_day' AND window_label=? AND lang_scope='all'", (DAY.isoformat(),)).fetchone()[0])
    assert day1["activation"]["sample_version"] == kw_ver and day1["accounts"]["bearish"] == 3 and "scope" not in day1["activation"]     # the keyword day-1 result did not absorb anything
    wl = json.loads(db.execute("SELECT payload_json FROM x_panel_sentiment WHERE scope='watchlist' AND window_kind='calendar_day' AND window_label=? AND lang_scope='all'", (d2.isoformat(),)).fetchone()[0])
    assert wl["vote_post_ids"] == ["102"] and wl["activation"]["scope"] == "watchlist" and "+watchlist:" in wl["activation"]["sample_version"]
    kw2 = db.execute("SELECT payload_json FROM x_daily_sentiment WHERE window_kind='calendar_day' AND window_label=? AND lang_scope='all'", (d2.isoformat(),)).fetchone()
    assert kw2 is None or "102" not in json.loads(kw2[0])["vote_post_ids"]                                      # an author-query post never enters the keyword panel
    r = pipeline.build_research(db, base, cal, {}, mode="AUTO")
    assert sum(t["is_current_sample_version"] for t in r["topics"]) <= 1 and len({t["sample_version"] for t in r["topics"]}) == len(r["topics"])


def test_watched_posts_are_classified_with_the_recent_queue_first(db, cfg):
    watchlist.import_seed(db, seed(2), NOW)
    unlock(db, cfg)
    fake = Fake(authors={"Synth_User1": [tw(102, at(15), 9001, "Synth_User1", "$QQQ adding here")]})
    batch.run_daily(db, cfg, KEY, lambda: fake, NOW, clock=lambda: NOW)
    old = tw(5, datetime(2026, 9, 1, 15, tzinfo=UTC), 7000, "old", "$QQQ old note")
    store.put_post(db, provider.normalize_twitterapi_io(old, now="2026-11-20T06:00:00Z", batch="b", mode=provider.RETRO))
    stance.map_all(db, cfg)
    live = [sample.sample_version(c) for c in sample.live_configs(db, cfg)]
    items = stance.work_items(db, cfg, live)
    assert [(i["post_id"], i["queue"]) for i in items] == [("102", "recent"), ("5", "history")]
    seen = []
    out = stance.run(db, cfg, lambda p: (seen.append(p), answer(102, "bullish"))[1], now="2026-11-20T07:00:00Z", versions=live, limit=1)
    assert out["model_calls_by_queue"] == {"recent": 1, "history": 0} and "adding here" in seen[0] and out["remaining_by_queue"]["history"] == 1


# ============================================================ ambiguous handle
def test_the_ambiguous_author_is_never_queried_until_the_owner_picks_a_recorded_spelling(db, cfg):
    watchlist.import_seed(db, seed(2), NOW)
    unlock(db, cfg)
    with pytest.raises(watchlist.WatchError, match="NOT_ONE_OF_THE_RECORDED_SPELLINGS"):
        watchlist.confirm_handle(db, "watch-90", "Totally_New", NOW)
    with pytest.raises(watchlist.WatchError, match="NOT_AMBIGUOUS"):
        watchlist.confirm_handle(db, "watch-01", "Synth_User1", NOW)
    fake = Fake(authors={"AmbigCandidat": [tw(1, at(15), 5001, "AmbigCandidat", "hello")], "AmbigCandidate": []})
    res = batch.verify_candidates(db, cfg, fake, "watch-90", NOW, clock=lambda: NOW)
    assert res["status"] == "EVIDENCE_STORED_HANDLE_STILL_BLOCKED" and res["requests"] == 2 and res["identity_status"] == "AMBIGUOUS_HANDLE_BLOCKED"
    assert [(s["spelling"], s["returned"]) for s in res["spellings"]] == [("AmbigCandidat", 1), ("AmbigCandidate", 0)]
    assert res["spellings"][0]["authors"][0] == {"author_id": "5001", "user_name": "AmbigCandidat", "display_name": "name of AmbigCandidat", "profile_text": "profile of AmbigCandidat", "user_name_equals_spelling": True}
    assert ledger_rows(db, "watchlist")[:2] == (2, 1) and watchlist.get(db, "watch-90")["stable_author_id"] is None      # ledgered like any author query; nothing bound, nothing decided
    assert watchlist.watch_only_posts(db) == {("twitterapi_io", "1")}                                          # the checked post is not keyword-sample material
    a = watchlist.confirm_handle(db, "watch-90", "AmbigCandidat", NOW)
    assert (a["handle"], a["identity_status"], a["stable_author_id"]) == ("AmbigCandidat", "OWNER_CONFIRMED_HANDLE_PENDING_PROVIDER_ID", None)
    assert "watch-90" in [u["watch_id"] for u in batch.plan_recent(db, cfg, DAY, NOW)["watch"]["units"]] and len(watchlist.authors(db)) == 3      # still one person


# ============================================================ reading page
def test_the_reading_page_shows_raw_posts_and_why_each_is_or_is_not_counted(db, cfg, tmp_path):
    base, *_ = collected(db, cfg)
    cal = Calendar.load(store.REPO_ROOT / cfg["time"]["exchange_calendar"]["path"], cfg["time"]["exchange_calendar"]["sha256"])
    pipeline.build_daily(db, base, cal, mode="AUTO", now=datetime(2026, 11, 20, 9, 0, tzinfo=UTC))
    db.commit()
    v = pipeline.read_for_portal(cfg, tmp_path / "x.sqlite3", env={})
    w = v["watchlist"]
    assert (w["summary"]["logical_authors"], w["summary"]["ready_for_queries"], w["summary"]["blocked_ambiguous"], w["summary"]["stable_ids_bound"]) == (7, 6, 1, 2)
    assert w["plan"]["authors"] == 6 and w["plan"]["not_asked"] == ["watch-90"] and w["plan"]["posts_budget"] == 600
    au = {a["watch_id"]: a for a in w["authors"]}
    assert au["watch-06"]["aliases"] == ["口罩哥"] and au["watch-06"]["profile_url"] == "https://x.com/Synth_User6" and au["watch-06"]["id_bound"] and au["watch-06"]["weight_override"] is None
    assert au["watch-90"]["profile_url"] is None and au["watch-90"]["handle_text_in_image"] == "AmbigCandidate" and not au["watch-90"]["ready_for_queries"]
    assert au["watch-02"]["availability"] == "NO_VISIBLE_POSTS_IN_WINDOW" and not au["watch-02"]["id_bound"]
    read = {r["post_id"]: r for r in w["reading"]}
    assert set(read) == {"101", "102", "103", "601"}                                                           # everything the watched authors wrote that was returned, counted or not
    assert read["103"]["likes_at_fetch"] == 3 and read["103"]["analysis"] == [{"topic_id": "qqq", "target_id": "QQQ", "state": "NOT_IN_ANALYSIS", "reason": "BELOW_LIKE_FLOOR", "stance": None}]
    assert read["101"]["analysis"][0]["reason"] == "NO_TARGET_KEYWORD_IN_TEXT" and read["101"]["text"] == "BTC looks strong today"       # a BTC post is not turned into a QQQ view
    assert read["102"]["analysis"][0] == {"topic_id": "qqq", "target_id": "QQQ", "state": "IN_ANALYSIS", "reason": None, "stance": "bullish"}
    assert read["102"]["sources"] == ["keyword:qqq", "watch:watch-01"] and read["601"]["sources"] == ["watch:watch-06"]
    p = v["panels"]
    assert p["keyword"]["available"] and p["watchlist"]["available"] and p["combined"]["available"] and len({p[s]["sample_version"] for s in p}) == 3
    assert p["watchlist"]["classification"]["matched_pairs"] == 2 and p["watchlist"]["classification"]["filtered_low_likes"] == 1 and p["combined"]["classification"]["matched_pairs"] == 5
    rows_of = lambda s: v["daily"] if s == "keyword" else p[s]["daily"]
    day = lambda s: next(r for r in rows_of(s) if r["window_kind"] == "calendar_day" and r["window_label"] == DAY.isoformat() and r["lang_scope"] == "all")
    assert day("watchlist")["accounts_total"] == 2 and day("combined")["accounts_total"] == 5 and day("keyword")["accounts_total"] == 4
    assert v["account_rules"]["named_accounts"][0]["identified"] and v["account_rules"]["named_accounts"][0]["identity_from"] == "watch list alias" and v["account_rules"]["named_accounts_not_identified"] == 1
    assert v["budget"]["sub_pools"]["watchlist"]["cap_posts"] == 600 and v["budget"]["sub_pools"]["recent"]["cap_posts"] == 2400 and v["budget"]["pools"]["historical"]["cap_posts"] == 2000
    assert all("vote_post_ids" not in r for s in p for r in rows_of(s)) and "daily" not in p["keyword"]


def test_without_a_list_or_a_key_the_page_says_so_and_nothing_is_called_collected(tmp_path, cfg, db):
    v = pipeline.read_for_portal(cfg, tmp_path / "missing.sqlite3", env={})
    assert v["watchlist"]["summary"]["imported"] is False and v["watchlist"]["authors"] == [] and not v["panels"]["watchlist"]["available"]
    watchlist.import_seed(db, seed(), NOW); db.commit()
    out = batch.run_daily(db, cfg, {}, lambda: 1 / 0, NOW, clock=lambda: NOW)
    assert out["status"] == "REFUSED_FAIL_CLOSED" and out["requests_sent"] == 0
    v = pipeline.read_for_portal(cfg, tmp_path / "x.sqlite3", env={})
    s = v["watchlist"]["summary"]
    assert (s["logical_authors"], s["collected_at_least_once"], s["stable_ids_bound"]) == (7, 0, 0) and v["watchlist"]["reading"] == [] and v["watchlist"]["plan"] is None
    assert all(a["availability"] == "WAITING_FOR_FIRST_COLLECTION" for a in v["watchlist"]["authors"]) and db.execute("SELECT count(*) FROM x_cost_ledger").fetchone()[0] == 0
