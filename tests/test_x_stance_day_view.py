"""The material behind one day: model judgement -> daily summary -> main views, on the same posts."""
import json
from datetime import datetime, timezone

import pytest

from scripts.x_stance_v1 import cli, config as C, day_view, pipeline, portal_read, simple_view, stance, store
from scripts.x_stance_v1.ledger import set_meta
from test_x_stance_simple import PROFILE, Fake, judge, run_round, seed


@pytest.fixture()
def built(tmp_path, monkeypatch):
    """A store whose version in force is the simple profile, with labels imported and the daily tables built."""
    path = tmp_path / "x.sqlite3"
    base = C.load()
    monkeypatch.setattr(C, "load", lambda *a, **k: json.loads(json.dumps(base)))
    cfg = C.with_profile(C.load(), PROFILE)
    db = store.connect(path)
    seed(db, cfg)
    run_round(db, cfg, tmp_path, Fake(judge))
    set_meta(db, C.PROFILE_META, json.dumps({"name": PROFILE, "set_at_utc": "2026-11-20T09:00:00Z", "reason": "test", "previous": "default"})); db.commit()
    pipeline.build_daily(db, cfg, cli._cal(cfg), mode="AUTO", now=datetime(2026, 11, 21, 12, tzinfo=timezone.utc))
    db.close()
    return path


def test_a_day_is_rebuilt_from_its_posts_and_equals_the_stored_summary(built):
    j = day_view.read_day(C.load(), built, scope="keyword", target_id="QQQ", window_label="2026-11-19")
    assert j["state"] == "READY" and j["note"] == simple_view.NOTE and j["identity_is_active"] and j["model_id"] == "Qwen/Qwen2.5-3B-Instruct"
    # posts 1,2,3 (one account: bullish, bearish, neutral) and 4 (another account: neutral). 5 was cut by a rule and is not a label
    assert j["posts"] == {"bullish": 1, "bearish": 1, "neutral": 2} and j["posts_total"] == 4
    assert j["accounts"] == {"bullish": 0, "bearish": 0, "neutral": 2} and j["accounts_total"] == 2          # one vote per account: its last valid post
    assert j["check"]["labels_equal_summary"] and all(j["check"]["fields"].values()) and j["check"]["views_evidence_in_material"]
    assert len(j["items"]) == j["items_total"] == 4 and sum(x["counts_as_account_vote"] for x in j["items"]) == 2
    one = next(x for x in j["items"] if x["post_id"] == "1")
    assert one["outcome"] == "bullish" and one["evidence"] == "我准备加仓" and one["reason"] and "source_url" in one
    assert one["published_at_utc"] == "2026-11-19T15:00:00Z" and one["first_received_at_utc"] == "2026-11-20T05:30:00Z" and one["model_completed_at_utc"] > one["first_received_at_utc"]
    assert [x["post_id"] for x in j["representative"]["neutral"]] and j["representative"]["bullish"] == []      # representatives are account votes only
    assert j["empty_reason"] is None and "不是盘中信号" in j["timing_note"] or j["backfilled"]


def test_irrelevant_is_listed_but_not_counted_and_an_empty_day_says_why(built):
    j = day_view.read_day(C.load(), built, scope="keyword", target_id="industry:memory", window_label="2026-11-19")
    assert j["posts"] == {"bullish": 1, "bearish": 0, "neutral": 0} and j["check"]["labels_equal_summary"]
    assert day_view.empty_reason({"matched_posts": 0, "pending": 0}, 0) == "NO_SAMPLE"
    assert day_view.empty_reason({"matched_posts": 3, "pending": 2}, 0) == "PENDING"
    assert day_view.empty_reason({"matched_posts": 3, "pending": 0}, 0) == "NO_VALID_LABEL"
    assert day_view.empty_reason({"matched_posts": 3, "pending": 0}, 2) is None
    assert day_view.read_day(C.load(), built, scope="keyword", target_id="QQQ", window_label="2026-11-18")["state"] in ("READY", "NO_SUMMARY_ROW")
    assert day_view.read_day(C.load(), built, scope="keyword", target_id="QQQ", window_label="2031-01-01")["state"] == "NO_SUMMARY_ROW"
    for bad in ({"scope": "x"}, {"window_label": "2026-11-19; --"}, {"target_id": ""}):
        with pytest.raises(ValueError):
            day_view.read_day(C.load(), built, **{"scope": "keyword", "target_id": "QQQ", "window_label": "2026-11-19", **bad})


def test_the_page_model_puts_drawn_rows_first_names_the_version_and_marks_empty_days(built):
    v = portal_read.read_for_portal(C.load(), built, env={})
    s = v["daily_summary"]
    assert s["note"] == simple_view.NOTE and s["all_rows_from_active_identity"] and s["models"] == ["Qwen/Qwen2.5-3B-Instruct"] and s["active_identity"] == v["classifier"]["inference_identity"]
    drawn = [r for r in v["daily"] if r["window_kind"] in ("session", "session3") and r["lang_scope"] == "all"]
    assert drawn and v["daily"][:len(drawn)] == drawn                                               # the limit can never cut a drawn date in favour of an undrawn row
    assert all("vote_post_ids" not in r and "empty_reason" in r for r in v["daily"])
    day = next(r for r in drawn if r["window_kind"] == "session" and r["target_id"] == "QQQ" and r["window_label"] == "2026-11-19")
    assert day["posts_total"] == 4 and day["empty_reason"] is None and day["posts"]["bullish"] == 1
    # after a switch back, the stored rows are still the other version's: the page says so instead of passing them off
    db = store.connect(built)
    set_meta(db, C.PROFILE_META, json.dumps({"name": "default", "set_at_utc": "2026-11-21T00:00:00Z", "reason": "test", "previous": PROFILE})); db.commit(); db.close()
    v2 = portal_read.read_for_portal(C.load(), built, env={})
    assert not v2["daily_summary"]["all_rows_from_active_identity"]
    j = day_view.read_day(C.load(), built, scope="keyword", target_id="QQQ", window_label="2026-11-19")
    assert j["check"]["labels_equal_summary"] and not j["identity_is_active"] and j["identity"] == stance.identity_hash(C.with_profile(C.load(), PROFILE))


def test_three_session_groups_count_posts_once_and_accounts_once_and_match_their_material(built):
    from scripts.x_stance_v1.sessions import group_of
    cfg = C.load(); cal = cli._cal(cfg)
    n, anchor = pipeline.group_settings(cfg, cal)
    assert n == 3 and cal.dates[anchor] == "2026-08-24"
    idx = group_of(cal, cal.index("2026-11-19"), n, anchor)
    dates = [cal.dates[i] for i in idx]
    assert len(dates) == 3 and "2026-11-19" in dates
    assert group_of(cal, idx[0] - 1, n, anchor)[-1] == idx[0] - 1                  # groups tile the calendar: the one before ends right before
    v = portal_read.read_for_portal(cfg, built, env={})
    rows = {(r["window_kind"], r["window_label"], r["target_id"]): r for r in v["daily"] if r["lang_scope"] == "all"}
    g = rows.get(("session3", dates[-1], "QQQ"))
    if cal.closes[idx[-1]] > datetime(2026, 11, 21, 12, tzinfo=timezone.utc):
        assert g is None                                                              # not every session of the group has closed: no row yet
        return
    assert g["group_sessions"] == dates and g["group_size"] == 3
    days = [rows[("session", d, "QQQ")] for d in dates if ("session", d, "QQQ") in rows]
    assert g["posts_total"] == sum(r["posts_total"] for r in days)                 # every post once
    assert g["accounts_total"] <= sum(r["accounts_total"] for r in days)          # an account that posted on two days votes once
    j = day_view.read_day(cfg, built, scope="keyword", target_id="QQQ", window_label=dates[-1], kind="session3")
    assert j["state"] == "READY" and j["group_sessions"] == dates and j["check"]["labels_equal_summary"] and j["posts_total"] == g["posts_total"]
    with pytest.raises(ValueError):
        day_view.read_day(cfg, built, scope="keyword", target_id="QQQ", window_label=dates[-1], kind="week")
