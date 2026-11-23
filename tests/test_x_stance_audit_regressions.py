"""Reverse regressions for the three defects reproduced by the independent audit of 2026-11-22 (F1 lenient
preflight, F2 rebuild rewrites the signal time, F3 frozen request clock). Each test replays the audit's own
scenario and requires the opposite outcome. All provider responses, posts, labels and prices are synthetic."""
import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from scripts.x_stance_v1 import aggregate, batch, config as C, provider, returns, snapshots, stance, store
from scripts.x_stance_v1.ledger import Ledger, get_meta
from scripts.x_stance_v1.sessions import Calendar

UTC = timezone.utc
NOW = datetime(2026, 11, 22, 5, 0, tzinfo=UTC)
KEY = {"TWITTERAPI_IO_API_KEY": "tw-FAKE-not-a-real-key-000"}


@pytest.fixture()
def cfg():
    c = C.load()
    c["aggregation"]["account_rules"]["enabled"] = False     # these modules pre-date the account rules (tests/test_x_stance_r3.py covers them)
    c["allocation"]["recent"].pop("sub_pools", None)         # ... and the split of the recent pool between watched authors and keywords (tests/test_x_stance_watchlist.py)
    return c


@pytest.fixture()
def db(tmp_path):
    d = store.connect(tmp_path / "x.sqlite3")
    yield d
    d.close()


def tweet(i, ts):
    return {"id": str(i), "url": f"https://x.com/u/status/{i}", "text": f"$QQQ synthetic {i}", "lang": "en",
            "createdAt": ts.strftime("%a %b %d %H:%M:%S +0000 %Y"), "author": {"id": str(7000 + i)}}


class Scripted:
    """One post per page; the page shape depends on the case (the audit's three plus controls)."""

    def __init__(self, case, wall=None, step=timedelta(0)):
        self.case, self.calls, self.wall, self.step = case, 0, wall, step

    def get(self, params):
        self.calls += 1
        if self.wall is not None:
            self.wall["t"] += self.step                      # the request takes time; the response arrives later
        q = params["query"]
        s, e = int(q.split("since_time:")[1].split()[0]), int(q.split("until_time:")[1].split()[0])
        ts = datetime.fromtimestamp(s - 86400 if self.case == "outside_window" else e - 1 - self.calls, UTC)
        page = {"tweets": [] if self.case == "empty" else [tweet(self.calls, ts)], "has_next_page": False, "next_cursor": ""}
        if self.case == "missing_pagination":
            del page["has_next_page"], page["next_cursor"]
        elif self.case == "true_without_cursor":
            page["has_next_page"] = True
        elif self.case == "string_false":
            page["has_next_page"] = "false"
        elif self.case == "null_cursor":                    # what the provider really sends on a last page
            page["next_cursor"] = None
        elif self.case == "null_cursor_with_more":
            page["has_next_page"], page["next_cursor"] = True, None
        elif self.case == "multi":
            page["has_next_page"], page["next_cursor"] = True, f"c{self.calls}"
        return page


# ---------- F1 ----------
@pytest.mark.parametrize("case,reason", [("missing_pagination", "PROTOCOL:HAS_NEXT_PAGE_MISSING"), ("true_without_cursor", "PROTOCOL:NEXT_PAGE_WITHOUT_CURSOR"),
                                         ("string_false", "PROTOCOL:HAS_NEXT_PAGE_NOT_BOOLEAN"), ("null_cursor_with_more", "PROTOCOL:NEXT_CURSOR_NOT_STRING")])
def test_f1_malformed_pagination_never_passes_and_is_never_complete(cfg, db, case, reason):
    f = Scripted(case)
    rec = batch.run_preflight(db, cfg, f, NOW, env=KEY, clock=lambda: NOW)
    assert rec["verdict"] == "FAIL" and reason in rec["fail_reasons"] and rec["unlocks"] == {"recent_job": False, "historical_job": False}
    assert not batch.preflight_passed(db, cfg, KEY) and get_meta(db, "preflight_passed") is None
    assert db.execute("SELECT count(*) FROM x_query_runs WHERE status='COMPLETE'").fetchone()[0] == 0
    assert {r[0] for r in db.execute("SELECT DISTINCT stop_reason FROM x_query_runs")} == {reason}
    assert f.calls == 3 and rec["stopped"] == "PROTOCOL_ANOMALY_REPEATED"          # three malformed answers stop the job; the other six requests are never sent
    led = Ledger(db, cfg, clock=lambda: NOW).usage(NOW)
    assert led["today"]["credits"] == 45 and led["unknown_held"]["requests"] == 0  # the three answered pages are still paid for (one post each)
    out = batch.run_daily(db, cfg, KEY, lambda: pytest.fail("the scheduled job must stay locked"), NOW, clock=lambda: NOW)
    assert out["status"] == "REFUSED_FAIL_CLOSED" and out["gate"]["preflight"]["state"] == "FAILED"


def test_f1_a_null_cursor_is_an_end_of_results_only_next_to_an_explicit_false(cfg, db):
    """The first live collection (2026-11-23) showed that the provider's last page is has_next_page=false with
    next_cursor=null. Four such pages were read as anomalies and stopped the author walk. The end of results is still
    decided by the explicit boolean alone; a null cursor next to has_next_page=true stays an anomaly (case above)."""
    assert provider.page_schema_error({"tweets": [], "has_next_page": False, "next_cursor": None}) is None
    assert provider.page_schema_error({"tweets": [], "has_next_page": True, "next_cursor": None}) == "NEXT_CURSOR_NOT_STRING"
    assert provider.page_schema_error({"tweets": [], "has_next_page": False, "next_cursor": 0}) == "NEXT_CURSOR_NOT_STRING"
    f = Scripted("null_cursor")
    rec = batch.run_preflight(db, cfg, f, NOW, env=KEY, clock=lambda: NOW)
    assert f.calls == 9 and all(r["schema_valid"] for r in rec["requests"]) and rec["stopped"] is None
    assert not any(str(r[0]).startswith("PROTOCOL") for r in db.execute("SELECT DISTINCT stop_reason FROM x_query_runs"))


def test_f1_posts_all_outside_the_window_do_not_pass(cfg, db):
    f = Scripted("outside_window")
    rec = batch.run_preflight(db, cfg, f, NOW, env=KEY, clock=lambda: NOW)
    assert f.calls == 9 and rec["returned_total"] == 9 and rec["in_window_total"] == 0 and db.execute("SELECT count(*) FROM x_posts").fetchone()[0] == 0
    assert rec["verdict"] == "FAIL" and rec["fail_reasons"] == ["ALL_RETURNED_POSTS_OUTSIDE_WINDOW_OR_UNPARSEABLE"] and not batch.preflight_passed(db, cfg, KEY)
    assert all(r["schema_valid"] and r["out_of_window"] == 1 and r["in_window"] == 0 for r in rec["requests"])


def test_f1_legal_empty_pages_are_recorded_but_verify_nothing(cfg, db):
    rec = batch.run_preflight(db, cfg, Scripted("empty"), NOW, env=KEY, clock=lambda: NOW)
    assert rec["verdict"] == "FAIL" and rec["fail_reasons"] == ["ALL_PAGES_EMPTY_NOTHING_VERIFIED"] and all(r["empty"] and r["schema_valid"] for r in rec["requests"])
    assert {r[0] for r in db.execute("SELECT DISTINCT stop_reason FROM x_query_runs")} == {"END_OF_RESULTS_EMPTY"}
    assert rec["continuation"]["state"] == "NOT_OBSERVED" and not batch.preflight_passed(db, cfg, KEY)


def test_f1_a_well_formed_bounded_preflight_passes_and_reports_what_it_did_not_see(cfg, db):
    f = Scripted("ok")                                         # every first page says "no more pages": continuation cannot be observed
    rec = batch.run_preflight(db, cfg, f, NOW, env=KEY, clock=lambda: NOW)
    assert rec["verdict"] == "PASS" and f.calls == 9 and rec["continuation"] == {"state": "NOT_OBSERVED", "reason": "no first page reported a next page"}
    assert rec["checks"]["continuation_verified"] is False and rec["checks"]["history_search_verified"] is True and rec["unlocks"] == {"recent_job": True, "historical_job": True}
    assert rec["binding"]["validator"] == provider.PROTOCOL_VALIDATOR and rec["binding"]["credential_fingerprint"] and "tw-FAKE" not in json.dumps(rec)
    m = Scripted("multi")
    db2 = store.connect(str(db.execute("PRAGMA database_list").fetchone()[2]) + ".multi")
    rec2 = batch.run_preflight(db2, cfg, m, NOW, env=KEY, clock=lambda: NOW)
    assert rec2["verdict"] == "PASS" and m.calls == 10 and rec2["continuation"]["state"] == "VERIFIED" and rec2["usage_after"]["preflight_cumulative"]["returned_posts"] == 10
    db2.close()


def test_f1_history_is_not_unlocked_by_a_recent_only_pass(cfg, db):
    class NoHistory(Scripted):
        def get(self, params):
            page = super().get(params)
            s = int(params["query"].split("since_time:")[1].split()[0])
            if s < int((NOW - timedelta(days=30)).timestamp()):
                page["tweets"] = []
            return page
    rec = batch.run_preflight(db, cfg, NoHistory("ok"), NOW, env=KEY, clock=lambda: NOW)
    assert rec["verdict"] == "PASS" and rec["checks"]["history_search_verified"] is False and rec["unlocks"] == {"recent_job": True, "historical_job": False}
    st = batch.preflight_state(db, cfg, KEY)
    assert st["recent_unlocked"] and not st["history_unlocked"] and st["reasons"] == ["HISTORY_SEARCH_NOT_VERIFIED"]


def test_f1_old_lenient_complete_checkpoints_are_demoted_by_an_audited_migration(cfg, db):
    db.execute("INSERT INTO x_queries VALUES('qh','twitterapi_io','qqq','en','vocab-20261122-a','($QQQ) lang:en')")
    rows = [("r_ok", "2026-11-01T00:00:00Z", {"tweets": [], "has_next_page": False, "next_cursor": ""}),
            ("r_missing", "2026-11-02T00:00:00Z", {"tweets": []}),
            ("r_true_empty", "2026-11-03T00:00:00Z", {"tweets": [], "has_next_page": True, "next_cursor": ""})]
    for rid, ws, page in rows:
        store.upsert_run(db, {"run_id": rid, "provider": "twitterapi_io", "query_hash": "qh", "window_start_utc": ws, "window_end_utc": ws[:8] + "28T00:00:00Z", "status": "COMPLETE", "stop_reason": "END_OF_RESULTS"})
        db.execute("INSERT INTO x_page_cache VALUES('twitterapi_io','qh',?,?,'',?,?,?)", (ws, ws[:8] + "28T00:00:00Z", "h", json.dumps(page), "2026-11-20T00:00:00Z"))
    db.execute("INSERT INTO x_cost_ledger(request_id, job_type, pool, is_preflight, attempt, reserved_credits, reserved_posts, settled_credits, returned, unique_ids, status, cost_source, budget_day, created_at_utc) "
               "VALUES('old','recent','recent',0,1,300,20,15,0,0,'SETTLED','ESTIMATE','2026-11-20','2026-11-20T00:00:00Z')")
    db.commit()
    d = provider.migrate_checkpoints(db)
    assert d["complete_runs_checked"] == 3 and {x["run_id"]: x["reason"] for x in d["demoted"]} == {"r_missing": "HAS_NEXT_PAGE_MISSING", "r_true_empty": "NO_PAGE_WITH_EXPLICIT_END"}
    assert dict(db.execute("SELECT run_id, status FROM x_query_runs").fetchall()) == {"r_ok": "COMPLETE", "r_missing": "PARTIAL", "r_true_empty": "PARTIAL"}
    assert db.execute("SELECT count(*) FROM x_page_cache").fetchone()[0] == 3 and db.execute("SELECT settled_credits FROM x_cost_ledger").fetchone()[0] == 15   # nothing deleted, no cost reset
    again = provider.migrate_checkpoints(db)
    assert again["demoted"] == [] and db.execute("SELECT count(*) FROM x_migrations WHERE name=?", (provider.PROTOCOL_VALIDATOR,)).fetchone()[0] == 1          # idempotent, logged once


# ---------- F2 ----------
def _cal(cfg):
    c = cfg["time"]["exchange_calendar"]
    return Calendar.load(store.REPO_ROOT / c["path"], c["sha256"])


ROW = {"provider": "synthetic", "post_id": "1", "author_id": "2", "created_at_utc": "2026-11-16T20:00:00Z", "first_seen_at_utc": "2026-11-16T21:02:00Z",
       "inferred_at_utc": "2026-11-16T21:05:00Z", "lang": "en", "label": "bullish", "quality_status": "OK", "text_hash": "synthetic1"}
START, END = store.parse("2026-11-13T21:00:00Z"), store.parse("2026-11-16T21:00:00Z")


def commit(db, rows, now, act=None):
    res = aggregate.aggregate(rows, collection_status="PLANNED_SAMPLE", threshold=1)
    return aggregate.commit(db, window_kind="session", window_label="2026-11-16", start=START, end=END, topic_id="qqq", target_id="QQQ", mode="BATCH_AFTER_CLOSE",
                            result=res, rows=rows, now=store.parse(now), act=act)


def test_f2_rebuilding_the_same_evidence_two_days_later_changes_nothing(cfg, db):
    cal = _cal(cfg)
    first = commit(db, [ROW], "2026-11-16T22:00:00Z"); db.commit()
    second = commit(db, [ROW], "2026-11-18T22:00:00Z"); db.commit()
    assert first["signal_committed_at_utc"] == second["signal_committed_at_utc"] == "2026-11-16T22:00:00Z" and second["snapshot_id"] == first["snapshot_id"]
    assert (first["snapshot_reused"], second["snapshot_reused"], second["last_checked_at_utc"]) == (False, True, "2026-11-18T22:00:00Z")
    px = returns.Prices({d: (100.0, 101.0) for d in cal.dates}, series_id="SYNTHETIC_ONLY", input_sha256=None)
    pairs = [returns.pair(cal, px, session_date="2026-11-16", h=1, available_at=store.parse(p["signal_committed_at_utc"]), availability=p["availability"]) for p in (first, second)]
    assert [p["entry_date"] for p in pairs] == ["2026-11-17", "2026-11-17"]            # the audit saw 2026-11-17 then 2026-11-19
    assert db.execute("SELECT count(*) FROM x_signal_snapshots").fetchone()[0] == 1
    view = json.loads(db.execute("SELECT payload_json FROM x_daily_sentiment").fetchone()[0])
    assert view["first_committed_at_utc"] == "2026-11-16T22:00:00Z" and view["last_checked_at_utc"] == "2026-11-18T22:00:00Z"


def test_f2_changed_labels_and_a_changed_model_append_revisions_and_the_old_ones_read_back(cfg, db):
    act = {"model_id": "m1", "model_revision": "r1", "prompt_hash": "p1", "config_hash": "c1", "vocab_version": "v1"}
    a = commit(db, [ROW], "2026-11-16T22:00:00Z", act)
    b = commit(db, [{**ROW, "label": "bearish"}], "2026-11-17T22:00:00Z", act)        # the label was corrected a day later
    c = commit(db, [{**ROW, "label": "bearish"}], "2026-11-18T22:00:00Z", {**act, "model_revision": "r2"})
    db.commit()
    assert [x["revision"] for x in (a, b, c)] == [1, 2, 3] and (b["supersedes"], c["supersedes"]) == (a["snapshot_id"], b["snapshot_id"])
    assert (b["change_reason"], c["change_reason"]) == ("EVIDENCE_CHANGED", "MODEL_OR_PROMPT_CHANGED")
    assert [x["first_committed_at_utc"] for x in (a, b, c)] == ["2026-11-16T22:00:00Z", "2026-11-17T22:00:00Z", "2026-11-18T22:00:00Z"]
    revs = snapshots.revisions(db, a["series_key"])
    assert [(r["revision"], r["state"], r["first_committed_at_utc"]) for r in revs] == [(1, "bullish", "2026-11-16T22:00:00Z"), (2, "bearish", "2026-11-17T22:00:00Z"), (3, "bearish", "2026-11-18T22:00:00Z")]
    assert snapshots.first_formal(db, a["series_key"])["snapshot_id"] == a["snapshot_id"]                          # what was known first is still what a back-test reads
    assert snapshots.as_of(db, a["series_key"], "2026-11-17T12:00:00Z")["state"] == "bullish" and snapshots.as_of(db, a["series_key"], "2026-11-17T23:00:00Z")["state"] == "bearish"
    assert snapshots.as_of(db, a["series_key"], "2026-11-16T21:30:00Z") is None        # before the first commit there was no signal
    assert json.loads(db.execute("SELECT payload_json FROM x_daily_sentiment").fetchone()[0])["revision"] == 3      # the page shows the latest revision


def test_f2_unknown_that_is_labelled_later_becomes_tradable_only_from_the_later_time(cfg, db):
    pending = aggregate.aggregate([], collection_status="PLANNED_SAMPLE", threshold=1, pending=5)
    kw = dict(window_kind="session", window_label="2026-11-16", start=START, end=END, topic_id="qqq", target_id="QQQ", mode="BATCH_AFTER_CLOSE")
    u = aggregate.commit(db, **kw, result=pending, rows=[], now=store.parse("2026-11-16T22:00:00Z"))
    assert u["state"] == "UNKNOWN" and u["state_reason"] == "CLASSIFICATION_INCOMPLETE" and snapshots.first_formal(db, u["series_key"]) is None
    done = commit(db, [{**ROW, "inferred_at_utc": "2026-11-19T03:00:00Z"}], "2026-11-19T03:10:00Z")
    assert done["revision"] == 2 and done["change_reason"] == "CLASSIFICATION_OR_SAMPLE_COMPLETED" and done["first_committed_at_utc"] == "2026-11-19T03:10:00Z"
    ff = snapshots.first_formal(db, u["series_key"])
    assert ff["first_committed_at_utc"] == "2026-11-19T03:10:00Z"                       # never the window end, never the first UNKNOWN build
    cal = _cal(cfg)
    px = returns.Prices({d: (100.0, 101.0) for d in cal.dates}, series_id="SYNTHETIC_ONLY", input_sha256=None)
    assert returns.pair(cal, px, session_date="2026-11-16", h=1, available_at=store.parse(ff["first_committed_at_utc"]), availability=ff["availability"])["entry_date"] == "2026-11-19"


def test_f2_commit_time_is_never_before_the_material_was_received(cfg, db):
    late = {**ROW, "first_seen_at_utc": "2026-11-17T09:00:00Z", "inferred_at_utc": "2026-11-17T09:30:00Z"}
    s = commit(db, [late], "2026-11-16T22:00:00Z")           # a clock that claims an earlier commit cannot pre-date the evidence
    assert s["first_committed_at_utc"] == "2026-11-17T09:30:00Z"


def test_f2_only_the_active_classifier_counts(cfg, db):
    c = copy.deepcopy(cfg)
    c["classifier"].update(model_id="model-a", immutable_revision="rev-a", prompt_version="x-stance-prompt-v1-20261122", temperature=0)
    act = snapshots.activation(c)
    p = provider.normalize_twitterapi_io(tweet(1, store.parse("2026-11-16T15:00:00Z")), now="2026-11-16T22:00:00Z", batch="b", mode=provider.BATCH)
    store.put_post(db, p); stance.map_topics(db, c)
    for model, rev, lab, at in (("model-a", "rev-a", "bullish", "2026-11-16T22:10:00Z"), ("model-b", "rev-b", "bearish", "2026-11-16T22:20:00Z")):
        ident = act["identity"] if model == "model-a" else "identity-of-another-model"       # R3: a label belongs to one complete inference identity
        db.execute(f"INSERT INTO x_stance_labels({','.join(store.LABEL_COLUMNS[:16])}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("twitterapi_io", "1", "qqq", "QQQ", lab, "OK", 1, None, None, model, rev, act["prompt_hash"], at, "raw", f"k{model}", ident))
    rows, _ = aggregate.rows_for_window(db, topic_id="qqq", target_id="QQQ", start=START, end=END, mode="BATCH_AFTER_CLOSE", act=act)
    assert [r["label"] for r in rows] == ["bullish"]                                     # the later label from another model is not mixed in
    other = copy.deepcopy(c); other["classifier"].update(model_id="model-c", immutable_revision="rev-c")
    assert aggregate.rows_for_window(db, topic_id="qqq", target_id="QQQ", start=START, end=END, mode="BATCH_AFTER_CLOSE", act=snapshots.activation(other))[0] == []
    prog = aggregate.progress(db, topic_id="qqq", target_id="QQQ", start=START, end=END, vocab_version=c["topic_vocabulary_version"], act=snapshots.activation(other))
    assert (prog["matched_posts"], prog["pending"]) == (1, 1)                           # under a model that has not run, the post is still pending
    assert aggregate.rows_for_window(db, topic_id="qqq", target_id="QQQ", start=START, end=END, mode="BATCH_AFTER_CLOSE", act=snapshots.activation(cfg))[0] == []   # nothing pinned: nothing counts


def test_f2_legacy_rows_are_marked_timing_unverified_and_the_migration_rolls_back(cfg, db):
    legacy = {"shares": {"bullish": 1.0, "bearish": 0.0, "neutral": 0.0}, "state": "bullish", "lang_scope": "all", "mode": "BATCH_AFTER_CLOSE",
              "signal_committed_at_utc": "2026-11-18T22:00:00Z", "window_start_utc": store.iso(START), "window_end_utc": store.iso(END), "availability": "OBSERVED_AFTER_CLOSE_BATCH"}
    db.execute("INSERT INTO x_daily_sentiment VALUES('session','2026-11-16',?,?,'qqq','QQQ','all','BATCH_AFTER_CLOSE',?,'bullish','2026-11-18T22:00:00Z')", (store.iso(START), store.iso(END), json.dumps(legacy)))
    db.commit()
    m = snapshots.migrate_legacy(db)
    assert m == {"legacy_rows_marked": 1, "snapshots": 1} and snapshots.migrate_legacy(db)["legacy_rows_marked"] == 0
    row = db.execute("SELECT timing_status, formal, first_committed_at_utc, series_key FROM x_signal_snapshots").fetchone()
    assert tuple(row)[:3] == ("TIMING_UNVERIFIED", 0, None) and snapshots.first_formal(db, row[3]) is None
    view = json.loads(db.execute("SELECT payload_json FROM x_daily_sentiment").fetchone()[0])
    assert view["signal_committed_at_utc"] is None and view["legacy_overwritten_time_utc"] == "2026-11-18T22:00:00Z" and view["availability"] == "TIMING_UNVERIFIED_NOT_TRADABLE"
    assert snapshots.rollback_legacy(db) == {"legacy_rows_restored": 1}
    back = json.loads(db.execute("SELECT payload_json FROM x_daily_sentiment").fetchone()[0])
    assert back["signal_committed_at_utc"] == "2026-11-18T22:00:00Z" and "snapshot_id" not in back and db.execute("SELECT count(*) FROM x_signal_snapshots").fetchone()[0] == 0


# ---------- F3 ----------
def test_f3_requests_follow_the_real_clock_across_shanghai_midnight(cfg, db):
    start = datetime(2026, 11, 22, 15, 59, 30, tzinfo=UTC)       # 23:59:30 in Shanghai on 2026-11-22
    wall = {"t": start}
    f = Scripted("multi", wall=wall, step=timedelta(minutes=1))
    batch._store_receipt(db, {"finished_at_utc": store.iso(start), "verdict": "PASS", "binding": batch.binding(db, cfg, KEY), "checks": {"history_search_verified": False}, "fail_reasons": []})
    c = copy.deepcopy(cfg); c["acquisition"]["proposed_recent_slices_per_day"] = 1; c["allocation"]["recent"]["max_returned_posts_per_day"] = 160
    c["budget"]["daily_all_jobs_returned_post_cap"] = 5000
    batch._store_receipt(db, {"finished_at_utc": store.iso(start), "verdict": "PASS", "binding": batch.binding(db, c, KEY), "checks": {"history_search_verified": False}, "fail_reasons": []})
    out = batch.run_daily(db, c, KEY, lambda: f, start, clock=lambda: wall["t"])
    assert out["status"] == "RAN" and out["recent"]["ny_date"] == "2026-11-21" and f.calls == 8      # the sample date comes from the anchor and does not move
    led = db.execute("SELECT budget_day, created_at_utc, settled_at_utc FROM x_cost_ledger ORDER BY created_at_utc").fetchall()
    assert [r[0] for r in led] == ["2026-11-22"] + ["2026-11-23"] * 7                              # reserved before midnight -> old day; after -> new day
    assert led[0][1] == "2026-11-22T15:59:30Z" and led[0][2] == "2026-11-22T16:00:30Z" and led[-1][1] == "2026-11-22T16:06:30Z" and led[-1][2] == "2026-11-22T16:07:30Z"
    seen = [r[0] for r in db.execute("SELECT first_seen_at_utc FROM x_posts ORDER BY first_seen_at_utc")]
    assert seen == [f"2026-11-22T16:0{i}:30Z" for i in range(8)] and seen == [r[0] for r in db.execute("SELECT fetched_at_utc FROM x_posts ORDER BY fetched_at_utc")]
    assert all(r[0] < "2026-11-22T05:00:00Z" for r in db.execute("SELECT created_at_utc FROM x_posts"))        # the posts' own times are untouched
    u22, u23 = Ledger(db, c, clock=lambda: start).usage(start), Ledger(db, c, clock=lambda: wall["t"]).usage(wall["t"])
    assert u22["today"]["returned_posts"] == 1 and u23["today"]["returned_posts"] == 7 and u23["rolling"]["credits"] == 120     # no request is counted twice
    assert out["started_at_utc"] == "2026-11-22T15:59:30Z" and out["finished_at_utc"] == "2026-11-22T16:07:30Z"


def test_f3_cached_pages_keep_their_original_receive_time_after_a_restart(cfg, tmp_path):
    path = tmp_path / "x.sqlite3"
    t0 = datetime(2026, 11, 22, 5, 0, tzinfo=UTC)
    d = store.connect(path)
    batch.run_preflight(d, cfg, Scripted("ok"), t0, env=KEY, clock=lambda: t0); d.close()
    d = store.connect(path)                                        # restart, two days later, same sample plan
    later = t0 + timedelta(days=2)
    d.execute("DELETE FROM x_posts"); d.execute("DELETE FROM x_run_posts"); d.execute("UPDATE x_query_runs SET status='PARTIAL', pages=0, cursor=''"); d.commit()   # force a re-read from the stored pages
    f = Scripted("ok")
    rec = batch.run_preflight(d, cfg, f, t0, env=KEY, clock=lambda: later)
    assert f.calls == 0 and rec["network_requests_total"] == 0 and rec["cache_hits_total"] == 9
    assert {r[0] for r in d.execute("SELECT DISTINCT first_seen_at_utc FROM x_posts")} == {"2026-11-22T05:00:00Z"}             # not the restart time
    assert Ledger(d, cfg, clock=lambda: later).usage(later)["preflight_cumulative"]["returned_posts"] == 9                    # nothing charged again
    d.close()


def test_f3_a_reservation_open_at_a_crash_stays_on_its_own_day_and_is_not_double_counted(cfg, tmp_path):
    path = tmp_path / "x.sqlite3"
    before = datetime(2026, 11, 22, 15, 59, 50, tzinfo=UTC)
    d = store.connect(path); Ledger(d, cfg, clock=lambda: before).reserve(pool="recent"); d.close()        # the process dies with the request in flight
    after = datetime(2026, 11, 22, 16, 5, tzinfo=UTC)
    d = store.connect(path); led = Ledger(d, cfg, clock=lambda: after)
    assert led.recover_open() == 1
    led.settle(led.reserve(pool="recent"), returned=20, unique_ids=20)
    rows = d.execute("SELECT budget_day, status FROM x_cost_ledger ORDER BY created_at_utc").fetchall()
    assert [tuple(r) for r in rows] == [("2026-11-22", "UNKNOWN_HELD"), ("2026-11-23", "SETTLED")]
    assert led.usage(after)["today"]["credits"] == 300 and led.usage(before)["today"]["credits"] == 300 and led.usage(after)["rolling"]["credits"] == 600
    d.close()
