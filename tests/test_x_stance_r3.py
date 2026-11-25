"""R3 reverse regressions. Everything here is synthetic: no provider, no real model, no market data.

N1  a post is counted only inside a sample version (vocabulary + account rules) it is a member of
N2  a label belongs to one complete inference identity; a changed parameter means a new inference
N3  the completion time is read after the answer came back; a cache hit keeps the original time
Q   the recent queue is worked off before the historical one; history yields
R   the owner's account rules: like floor and follower-tier / named-account weights
O   off-server worker: hashed export, checked and idempotent import
"""
from __future__ import annotations

import copy
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from scripts.x_stance_v1 import aggregate, batch, config as C, llm, offload, pipeline, provider, sample, snapshots, stance, store, vocab
from scripts.x_stance_v1.sessions import Calendar

UTC = timezone.utc
START, END = store.parse("2026-11-16T05:00:00Z"), store.parse("2026-11-17T05:00:00Z")      # one New York calendar date


@pytest.fixture()
def cfg():
    c = C.load()
    c["topics"] = [t for t in c["topics"] if t["id"] == "qqq"]
    c["aggregation"]["proposed_low_sample_author_threshold"] = 1
    return c


@pytest.fixture()
def plain(cfg):
    c = copy.deepcopy(cfg)
    c["aggregation"]["account_rules"]["enabled"] = False
    return c


@pytest.fixture()
def db(tmp_path):
    d = store.connect(tmp_path / "x.sqlite3")
    yield d
    d.close()


def put(db, pid, text="$QQQ I expect strong future returns.", *, author=None, created="2026-11-16T15:00:00Z", fetched="2026-11-17T05:30:00Z", mode=provider.BATCH, likes=500, followers=1000,
        handle=None, lang="en"):
    item = {"id": str(pid), "text": text, "lang": lang, "createdAt": store.parse(created).strftime("%a %b %d %H:%M:%S +0000 %Y"),
            "author": {"id": str(author or 1000 + int(pid)), **({"followers": followers} if followers is not None else {}), **({"userName": handle} if handle else {})}}
    if likes is not None:
        item["likeCount"] = likes
    p = provider.normalize_twitterapi_io(item, now=fetched, batch="SYNTHETIC", mode=mode)
    store.put_post(db, p)
    return p


def answer(pid, lab="bullish", ev="$QQQ"):
    return json.dumps({"post_id": str(pid), "topic_id": "qqq", "target_id": "QQQ", "is_relevant": True, "quality_status": "OK", "stance": lab, "evidence_text": ev, "reason_short": None})


class Model:
    """Test double. Answers by post id found in the prompt; counts calls; can advance an injected clock."""

    def __init__(self, labels=None, box=None, seconds=0, fail_after=None):
        self.labels, self.box, self.seconds, self.calls, self.fail_after = labels or {}, box, seconds, [], fail_after

    def __call__(self, prompt):
        text = prompt.split("<post>\n")[1].split("\n</post>")[0]
        pid = text.split("#")[1].split()[0] if "#" in text else "1"
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            raise stance.ClassifierUnavailable("WORKER_NOT_REACHABLE_OR_TIMEOUT")
        self.calls.append(pid)
        if self.box is not None:
            self.box["t"] = store.iso(store.parse(self.box["t"]) + timedelta(seconds=self.seconds))
        return answer(pid, self.labels.get(pid, "bullish"), f"#{pid}" if "#" in text else "$QQQ")


def rows(db, c, start=START, end=END, mode="BATCH_AFTER_CLOSE"):
    return aggregate.rows_for_window(db, topic_id="qqq", target_id="QQQ", start=start, end=end, mode=mode, act=snapshots.activation(c))[0]


def prog(db, c, start=START, end=END):
    return aggregate.progress(db, topic_id="qqq", target_id="QQQ", start=start, end=end, vocab_version=sample.sample_version(c), act=snapshots.activation(c))


def without(c, keyword, version):
    n = copy.deepcopy(c)
    n["topic_vocabulary_version"] = version
    n["topics"][0]["keywords"] = [k for k in n["topics"][0]["keywords"] if k != keyword]
    return n


# ============================================================ N1
def test_n1_a_post_that_only_matched_a_removed_keyword_is_in_no_aggregate_of_the_new_version(db, plain):
    put(db, 1, "$QQQ #1 I expect strong future returns.")
    stance.map_topics(db, plain)
    assert stance.run(db, plain, Model(), now="2026-11-17T06:00:00Z")["model_calls"] == 1
    old_rows = rows(db, plain)
    old = aggregate.commit(db, window_kind="calendar_day", window_label="2026-11-16", start=START, end=END, topic_id="qqq", target_id="QQQ", mode="BATCH_AFTER_CLOSE",
                           result=aggregate.aggregate(old_rows, collection_status="PLANNED_SAMPLE", threshold=1, cfg=plain), rows=old_rows, now=store.parse("2026-11-17T06:10:00Z"), act=snapshots.activation(plain))
    new = without(plain, "$QQQ", "vocab-r3-new")
    assert stance.map_topics(db, new)["pairs"] == 0
    p = prog(db, new)
    assert (p["matched_posts"], p["sample_accounts"], p["pending"]) == (0, 0, 0)
    assert rows(db, new) == []                                                    # the old label is still stored, and still not counted
    res = aggregate.aggregate(rows(db, new), collection_status="PLANNED_SAMPLE", threshold=1, pending=p["pending"], cfg=new)
    assert res["accounts_total"] == 0 and res["state"] == "UNKNOWN" and res["shares"] == {"bullish": None, "bearish": None, "neutral": None}
    assert aggregate.votes(rows(db, new)) == []                                   # nothing for the views either
    newsnap = aggregate.commit(db, window_kind="calendar_day", window_label="2026-11-16", start=START, end=END, topic_id="qqq", target_id="QQQ", mode="BATCH_AFTER_CLOSE",
                               result=res, rows=[], now=store.parse("2026-11-18T06:10:00Z"), act=snapshots.activation(new))
    assert newsnap["series_key"] != old["series_key"] and newsnap["revision"] == 1
    kept = snapshots.revisions(db, old["series_key"])                             # the old version's snapshot is untouched and still readable
    assert len(kept) == 1 and kept[0]["snapshot_id"] == old["snapshot_id"] and kept[0]["state"] == "bullish" and kept[0]["first_committed_at_utc"] == old["first_committed_at_utc"]
    assert [r["post_id"] for r in rows(db, plain)] == ["1"]                       # and the old version still derives the same thing


def test_n1_one_keyword_off_another_still_matching_keeps_the_post_and_reuses_its_label(db, plain):
    put(db, 1, "$QQQ #1 and the Nasdaq 100 keep climbing")
    put(db, 2, "$QQQ #2 only the ticker here")
    stance.map_topics(db, plain)
    m = Model()
    stance.run(db, plain, m, now="2026-11-17T06:00:00Z")
    new = copy.deepcopy(plain); new["topic_vocabulary_version"] = "vocab-r3-b"; new["topics"][0]["disabled_keywords"] = ["$QQQ"]
    stance.map_topics(db, new)
    assert {r["post_id"] for r in rows(db, new)} == {"1"} and prog(db, new)["matched_posts"] == 1 and prog(db, new)["pending"] == 0
    again = stance.run(db, new, m, now="2026-11-18T06:00:00Z")                    # same text, target and inference identity: nothing is re-inferred
    assert again["model_calls"] == 0 and len(m.calls) == 2


def test_n1_same_account_several_posts_and_the_views_follow_the_membership(db, plain):
    put(db, 1, "$QQQ #1 early note", author=7, created="2026-11-16T14:00:00Z")
    put(db, 2, "纳指100 #2 later note", author=7, created="2026-11-16T18:00:00Z")
    put(db, 3, "$QQQ #3 someone else", author=8)
    stance.map_topics(db, plain)
    stance.run(db, plain, Model({"1": "bearish", "2": "bullish", "3": "bearish"}), now="2026-11-17T06:00:00Z")
    full = aggregate.aggregate(rows(db, plain), collection_status="PLANNED_SAMPLE", threshold=1, cfg=plain)
    assert full["accounts"] == {"bullish": 1, "bearish": 1, "neutral": 0} and full["vote_post_ids"] == ["2", "3"]      # account 7 votes with its last post
    new = without(plain, "纳指100", "vocab-r3-c")
    stance.map_topics(db, new)
    res = aggregate.aggregate(rows(db, new), collection_status="PLANNED_SAMPLE", threshold=1, cfg=new)
    assert res["vote_post_ids"] == ["1", "3"] and res["accounts"] == {"bullish": 0, "bearish": 2, "neutral": 0}       # post 2 is not in this sample; account 7 now votes with post 1
    assert [v["post_id"] for v in aggregate.votes(rows(db, new))] == ["1", "3"]


def test_n1_two_topics_are_isolated_separately(db):
    c = C.load(); c["aggregation"]["account_rules"]["enabled"] = False
    c["topics"] = [t for t in c["topics"] if t["id"] in ("qqq", "technology")]
    put(db, 1, "$QQQ #1 and semiconductor names lead")
    assert stance.map_topics(db, c)["pairs"] == 2
    new = copy.deepcopy(c); new["topic_vocabulary_version"] = "vocab-r3-d"
    semi = next(t for t in new["topics"] if t["id"] == "technology"); semi["keywords"] = [k for k in semi["keywords"] if k.lower() != "semiconductor"]
    stance.map_topics(db, new)
    member = lambda ver: {(r[0], r[1]) for r in db.execute("SELECT topic_id, target_id FROM x_post_topics WHERE topic_version=?", (ver,))}
    assert len(member(c["topic_vocabulary_version"])) == 2 and {t for t, _ in member("vocab-r3-d")} == {"qqq"}


def test_n1_the_historical_range_is_derived_under_its_own_frozen_vocabulary(db, plain):
    """The back-fill plan froze vocabulary A. Vocabulary B (without $QQQ) is in force now. A day inside the
    historical range is derived under A; a recent day under B; the two are different series."""
    c = plain["time"]["exchange_calendar"]
    cal = Calendar.load(store.REPO_ROOT / c["path"], c["sha256"])
    anchor = datetime(2026, 11, 18, 6, 0, tzinfo=UTC)
    plan = batch.plan_history(db, plain, anchor)
    hr = json.loads(db.execute("SELECT value FROM x_meta WHERE key='history_range'").fetchone()[0])
    assert plan["vocab_version"] == plain["topic_vocabulary_version"] and hr["last_ny_date"] == "2026-11-16"
    vocab.propose(db, plain, [{"action": "disable", "topic_id": "qqq", "keyword": "$QQQ"}], anchor)
    vocab.apply(db, plain, date(2026, 11, 19), anchor)
    now_cfg = vocab.with_topics(plain, vocab.active(db, plain))
    assert now_cfg["topic_vocabulary_version"] != plain["topic_vocabulary_version"]
    put(db, 1, "$QQQ #1 history note", created="2026-11-12T15:00:00Z", mode=provider.RETRO)
    put(db, 2, "$QQQ #2 recent note", created="2026-11-19T15:00:00Z", fetched="2026-11-20T05:30:00Z")
    put(db, 3, "Nasdaq 100 #3 recent note", created="2026-11-19T16:00:00Z", fetched="2026-11-20T05:30:00Z")
    mapped = stance.map_all(db, plain)
    assert {m["sample_version"] for m in mapped} == {plain["topic_vocabulary_version"], now_cfg["topic_vocabulary_version"]}
    live = [sample.sample_version(x) for x in sample.live_configs(db, plain)]
    stance.run(db, now_cfg, Model(), now="2026-11-20T06:00:00Z", versions=live)
    assert sample.config_for_date(db, plain, date(2026, 11, 12))["topic_vocabulary_version"] == plain["topic_vocabulary_version"]
    assert sample.config_for_date(db, plain, date(2026, 11, 19))["topic_vocabulary_version"] == now_cfg["topic_vocabulary_version"]
    pipeline.build_daily(db, now_cfg, cal, mode="AUTO", now=datetime(2026, 11, 21, 6, 0, tzinfo=UTC))
    by = {r["window_label"]: json.loads(r["payload_json"]) for r in db.execute("SELECT window_label, payload_json FROM x_daily_sentiment WHERE window_kind='calendar_day' AND lang_scope='all'")}
    assert by["2026-11-12"]["vote_post_ids"] == ["1"] and by["2026-11-12"]["activation"]["sample_version"] == plain["topic_vocabulary_version"]
    assert by["2026-11-19"]["vote_post_ids"] == ["3"] and by["2026-11-19"]["activation"]["sample_version"] == now_cfg["topic_vocabulary_version"]   # post 2 only matched the disabled keyword
    versions = {r[0] for r in db.execute("SELECT DISTINCT vocab_version FROM x_signal_snapshots")}
    assert versions == {plain["topic_vocabulary_version"], now_cfg["topic_vocabulary_version"]}
    res = pipeline.build_research(db, now_cfg, cal, {}, mode="AUTO")
    assert {(t["sample_version"], t["is_current_sample_version"]) for t in res["topics"]} == {(plain["topic_vocabulary_version"], False), (now_cfg["topic_vocabulary_version"], True)}
    eps = [json.loads(r[0]) for r in db.execute("SELECT payload_json FROM x_sentiment_episodes")]
    assert all(e["sample_version"] in versions for e in eps)                       # every research row says which series it belongs to; none spans two


# ============================================================ N2
def test_n2_a_changed_generation_parameter_is_a_new_identity_and_a_new_inference(db, plain):
    p = put(db, 1, "$QQQ #1 I expect strong future returns.")
    stance.map_topics(db, plain)
    a = Model({"1": "bullish"})
    first = stance.run(db, plain, a, now="2026-11-17T06:00:00Z")
    c2 = copy.deepcopy(plain); c2["classifier"]["generation"]["seed"] = 43; c2["classifier"]["generation"]["max_output_tokens"] = 160
    assert stance.identity_hash(c2) != stance.identity_hash(plain) and stance.cache_key(p, "QQQ", "qqq", c2) != stance.cache_key(p, "QQQ", "qqq", plain)
    assert stance.legacy_cache_key(p, "QQQ", "qqq", c2) == stance.legacy_cache_key(p, "QQQ", "qqq", plain)        # this is what the old key missed
    assert rows(db, c2) == [] and prog(db, c2)["pending"] == 1                                                # the new configuration has no result of its own yet
    b = Model({"1": "bearish"})
    second = stance.run(db, c2, b, now="2026-11-17T07:00:00Z")
    assert (first["model_calls"], second["model_calls"], a.calls, b.calls) == (1, 1, ["1"], ["1"])
    assert [r["label"] for r in rows(db, plain)] == ["bullish"] and [r["label"] for r in rows(db, c2)] == ["bearish"]     # each identity reads its own label
    assert stance.run(db, plain, Model(fail_after=0), now="2026-11-17T08:00:00Z")["model_calls"] == 0                   # identical configuration: no call
    assert stance.run(db, c2, Model(fail_after=0), now="2026-11-17T08:00:00Z")["model_calls"] == 0
    assert db.execute("SELECT count(*), count(DISTINCT inference_identity) FROM x_stance_labels").fetchone()[:] == (2, 2)
    assert db.execute("SELECT count(*) FROM x_inference_cache").fetchone()[0] == 2 and db.execute("SELECT count(*) FROM x_posts").fetchone()[0] == 1   # nothing was fetched again


@pytest.mark.parametrize("change", ["seed", "max_output_tokens", "top_p", "context_tokens", "prompt", "revision", "template", "engine", "adapter"])
def test_n2_every_part_of_the_identity_counts(plain, change):
    c = copy.deepcopy(plain)
    if change in ("seed", "max_output_tokens", "top_p", "context_tokens"):
        c["classifier"]["generation"][change] = 12345
    elif change == "prompt":
        c["classifier"]["prompt_version"] = "x-stance-prompt-v1-20261122"
    elif change == "revision":
        c["classifier"]["immutable_revision"] += "x"
    elif change == "template":
        c["classifier"]["runtime"]["chat_template_sha256"] = "0" * 64
    elif change == "engine":
        c["classifier"]["runtime"]["build"] = "b99999"
    else:
        c["classifier"]["adapter_version"] = "another-adapter"
    assert stance.identity_hash(c) != stance.identity_hash(plain)
    same = copy.deepcopy(plain); same["aggregation"]["proposed_low_sample_author_threshold"] = 77; same["classifier"]["quality_note"] = "text that does not affect generation"
    assert stance.identity_hash(same) == stance.identity_hash(plain)


def test_n2_a_changed_output_schema_is_a_new_identity_and_a_cached_format_failure_is_reused_only_under_its_own(db, plain, monkeypatch):
    put(db, 1, "$QQQ #1 note")
    stance.map_topics(db, plain)
    calls = []
    bad = lambda prompt: (calls.append(1), "not json")[1]
    assert stance.run(db, plain, bad, now="2026-11-17T06:00:00Z")["by_quality"] == {"JSON_INVALID": 1}
    db.execute("DELETE FROM x_stance_labels"); db.commit()
    again = stance.run(db, plain, bad, now="2026-11-17T06:30:00Z")                 # the failed raw output is cached evidence of this identity: reused, not retried
    assert (again["model_calls"], again["cache_hits"], again["by_quality"], len(calls)) == (0, 1, {"JSON_INVALID": 1}, 1)
    assert rows(db, plain)[0]["label"] is None                                    # and it is still not a label, least of all neutral
    before = stance.identity_hash(plain)
    monkeypatch.setattr(llm, "OUTPUT_SCHEMA", {**llm.OUTPUT_SCHEMA, "title": "changed"})
    assert stance.identity_hash(plain) != before
    third = stance.run(db, plain, Model(), now="2026-11-17T07:00:00Z")
    assert third["model_calls"] == 1 and third["by_quality"] == {"OK": 1}          # under the new schema the item is inferred afresh


def test_n2_legacy_labels_are_kept_marked_never_counted_and_the_migration_rolls_back(db, plain):
    p = put(db, 1, "$QQQ #1 note")
    stance.map_topics(db, plain)
    old_key = stance.legacy_cache_key(p, "QQQ", "qqq", plain)
    c = plain["classifier"]
    db.execute(f"INSERT INTO x_stance_labels({','.join(store.LABEL_COLUMNS[:15])}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               ("twitterapi_io", "1", "qqq", "QQQ", "bullish", "OK", 1, "$QQQ", None, c["model_id"], c["immutable_revision"], stance.prompt_hash(c["prompt_version"]), "2026-11-17T06:00:00Z", "rawhash", old_key))
    db.execute("INSERT INTO x_inference_cache(cache_key, raw_output, raw_output_hash, created_at_utc) VALUES(?,?,?,?)", (old_key, "RAW OUTPUT OF THE OLD RUN", "rawhash", "2026-11-17T06:00:00Z"))
    db.commit()
    first = stance.migrate_identity(db)
    assert first["legacy_labels_marked"] == 1 and first["legacy_cached_outputs"] == 1
    second = stance.migrate_identity(db)
    assert second["legacy_labels_marked"] == 0 and db.execute("SELECT count(*) FROM x_migrations WHERE name='inference-identity-v1'").fetchone()[0] == 1     # idempotent
    row = db.execute("SELECT label, cache_key, identity_status, inference_identity, inferred_at_utc, inference_completed_at_utc FROM x_stance_labels").fetchone()
    assert tuple(row) == ("bullish", old_key, "LEGACY_INFERENCE_IDENTITY", None, "2026-11-17T06:00:00Z", None)      # nothing back-filled, old key and time kept
    assert db.execute("SELECT raw_output FROM x_inference_cache WHERE cache_key=?", (old_key,)).fetchone()[0] == "RAW OUTPUT OF THE OLD RUN"
    assert rows(db, plain) == [] and prog(db, plain)["pending"] == 1              # same model and prompt, yet not presented as the current configuration's result
    m = Model({"1": "bearish"})
    assert stance.run(db, plain, m, now="2026-11-18T06:00:00Z")["model_calls"] == 1 and [r["label"] for r in rows(db, plain)] == ["bearish"]
    assert db.execute("SELECT count(*) FROM x_stance_labels").fetchone()[0] == 2 and db.execute("SELECT count(*) FROM x_posts").fetchone()[0] == 1
    back = stance.rollback_identity(db)
    assert back["marks_removed"] == 1 and db.execute("SELECT count(*) FROM x_stance_labels WHERE label='bullish' AND cache_key=?", (old_key,)).fetchone()[0] == 1


def test_n2_a_worker_serving_another_chat_template_labels_nothing(db, plain):
    put(db, 1, "$QQQ #1 note")
    stance.map_topics(db, plain)

    class Other(Model):
        def props(self):
            return {"chat_template_sha256": "f" * 64}
    m = Other()
    out = stance.run(db, plain, m, now="2026-11-17T06:00:00Z")
    assert out["stopped"].startswith("RUNTIME_IDENTITY_MISMATCH") and out["model_calls"] == 0 and m.calls == [] and out["remaining"] == 1
    assert db.execute("SELECT count(*) FROM x_stance_labels").fetchone()[0] == 0

    class Right(Model):
        def props(self):
            return {"chat_template_sha256": plain["classifier"]["runtime"]["chat_template_sha256"], "build_info": "b-test"}
    assert stance.run(db, plain, Right(), now="2026-11-17T06:10:00Z")["model_calls"] == 1
    rec = json.loads(db.execute("SELECT payload_json FROM x_inference_identities WHERE identity=?", (stance.identity_hash(plain),)).fetchone()[0])
    assert rec["runtime_reported"]["build_info"] == "b-test" and rec["declared"]["generation"]["seed"] == 42        # what really ran is stored next to what was declared


def test_n2_a_fixed_model_study_reads_its_own_first_formal_revision(db, plain):
    put(db, 1, "$QQQ #1 note")
    stance.map_topics(db, plain)
    c2 = copy.deepcopy(plain); c2["classifier"]["generation"]["seed"] = 43
    kw = dict(window_kind="calendar_day", window_label="2026-11-16", start=START, end=END, topic_id="qqq", target_id="QQQ", mode="BATCH_AFTER_CLOSE")
    stance.run(db, plain, Model({"1": "bullish"}), now="2026-11-17T06:00:00Z")
    r1 = rows(db, plain)
    s1 = aggregate.commit(db, **kw, result=aggregate.aggregate(r1, collection_status="PLANNED_SAMPLE", threshold=1, cfg=plain), rows=r1, now=store.parse("2026-11-17T06:10:00Z"), act=snapshots.activation(plain))
    stance.run(db, c2, Model({"1": "bearish"}), now="2026-11-19T06:00:00Z")
    r2 = rows(db, c2)
    s2 = aggregate.commit(db, **kw, result=aggregate.aggregate(r2, collection_status="PLANNED_SAMPLE", threshold=1, cfg=c2), rows=r2, now=store.parse("2026-11-19T06:10:00Z"), act=snapshots.activation(c2))
    assert s2["series_key"] == s1["series_key"] and s2["revision"] == 2 and s2["change_reason"] == "INFERENCE_IDENTITY_CHANGED"
    key = s1["series_key"]
    assert snapshots.first_formal(db, key)["state"] == "bullish"                                            # production: what was known first
    mine = snapshots.first_formal(db, key, identity=stance.identity_hash(c2))
    assert mine["state"] == "bearish" and mine["first_committed_at_utc"] == "2026-11-19T06:10:00Z"           # the second model's own result and time, not the first model's
    assert snapshots.first_formal(db, key, identity="an-identity-that-never-ran") is None


# ============================================================ N3
def test_n3_completion_is_read_after_the_answer_and_a_twenty_second_call_shows_as_twenty_seconds(db, plain):
    put(db, 1, "$QQQ #1 note")
    stance.map_topics(db, plain)
    box = {"t": "2026-11-18T01:00:00Z"}
    out = stance.run(db, plain, Model(box=box, seconds=20), clock=lambda: box["t"])
    r = db.execute("SELECT inference_started_at_utc s, inference_completed_at_utc c, label_written_at_utc w, inferred_at_utc i, identity_status st FROM x_stance_labels").fetchone()
    assert out["model_calls"] == 1 and (r["s"], r["c"], r["i"], r["st"]) == ("2026-11-18T01:00:00Z", "2026-11-18T01:00:20Z", "2026-11-18T01:00:20Z", "RECORDED")
    assert r["w"] >= r["c"] > r["s"]
    c = db.execute("SELECT started_at_utc, completed_at_utc FROM x_inference_cache").fetchone()
    assert tuple(c) == ("2026-11-18T01:00:00Z", "2026-11-18T01:00:20Z")


def test_n3_a_call_that_starts_before_midnight_and_ends_after_it(db, plain):
    put(db, 1, "$QQQ #1 note")
    stance.map_topics(db, plain)
    box = {"t": "2026-11-18T23:59:50Z"}
    stance.run(db, plain, Model(box=box, seconds=25), clock=lambda: box["t"])
    r = db.execute("SELECT inference_started_at_utc, inference_completed_at_utc FROM x_stance_labels").fetchone()
    assert tuple(r) == ("2026-11-18T23:59:50Z", "2026-11-19T00:00:15Z")


def test_n3_a_worker_that_times_out_leaves_no_label_and_no_time(db, plain):
    for i in (1, 2, 3):
        put(db, i, f"$QQQ #{i} note")
    stance.map_topics(db, plain)
    box = {"t": "2026-11-18T01:00:00Z"}
    out = stance.run(db, plain, Model(box=box, seconds=20, fail_after=1), clock=lambda: box["t"])
    assert out["model_calls"] == 1 and out["stopped"] == "WORKER_NOT_REACHABLE_OR_TIMEOUT" and out["remaining"] == 2
    assert db.execute("SELECT count(*) FROM x_stance_labels").fetchone()[0] == 1 and db.execute("SELECT count(*) FROM x_inference_cache").fetchone()[0] == 1
    assert prog(db, plain)["pending"] == 2                                         # pending stays pending
    res = aggregate.aggregate(rows(db, plain), collection_status="PLANNED_SAMPLE", threshold=1, pending=2, cfg=plain)
    assert res["state"] == "UNKNOWN" and res["state_reason"] == "CLASSIFICATION_INCOMPLETE" and res["accounts"]["neutral"] == 0
    rest = stance.run(db, plain, Model(box=box, seconds=20), clock=lambda: box["t"])      # restart: picks up exactly what was left
    assert rest["model_calls"] == 2 and rest["remaining"] == 0 and prog(db, plain)["pending"] == 0


def test_n3_a_cache_hit_keeps_the_original_completion_time_and_makes_no_call(db, plain):
    put(db, 1, "$QQQ #1 note")
    stance.map_topics(db, plain)
    box = {"t": "2026-11-18T01:00:00Z"}
    stance.run(db, plain, Model(box=box, seconds=20), clock=lambda: box["t"])
    db.execute("DELETE FROM x_stance_labels"); db.commit()
    box["t"] = "2026-11-25T09:00:00Z"                                              # a week later the label is rebuilt from the cache
    m = Model(box=box, seconds=20)
    out = stance.run(db, plain, m, clock=lambda: box["t"])
    r = db.execute("SELECT inference_started_at_utc s, inference_completed_at_utc c, label_written_at_utc w, inferred_at_utc i, identity_status st FROM x_stance_labels").fetchone()
    assert (out["model_calls"], out["cache_hits"], m.calls) == (0, 1, [])
    assert (r["s"], r["c"], r["i"], r["w"], r["st"]) == ("2026-11-18T01:00:00Z", "2026-11-18T01:00:20Z", "2026-11-18T01:00:20Z", "2026-11-25T09:00:00Z", "CACHE_HIT")   # not "inferred today"


def test_n3_the_signal_is_never_available_before_the_inference_behind_it_was_completed(db, plain):
    put(db, 1, "$QQQ #1 note", fetched="2026-11-17T05:30:00Z")
    stance.map_topics(db, plain)
    box = {"t": "2026-11-17T09:00:00Z"}
    stance.run(db, plain, Model(box=box, seconds=30), clock=lambda: box["t"])
    r = rows(db, plain)
    kw = dict(window_kind="calendar_day", window_label="2026-11-16", start=START, end=END, topic_id="qqq", target_id="QQQ", mode="BATCH_AFTER_CLOSE")
    res = aggregate.aggregate(r, collection_status="PLANNED_SAMPLE", threshold=1, cfg=plain)
    early = aggregate.commit(db, **kw, result=res, rows=r, now=store.parse("2026-11-17T06:00:00Z"), act=snapshots.activation(plain))       # a build clock that runs behind
    assert early["first_committed_at_utc"] == "2026-11-17T09:00:30Z" >= r[0]["inference_completed_at_utc"] > r[0]["first_seen_at_utc"]
    later = aggregate.commit(db, **kw, result=res, rows=r, now=store.parse("2026-11-20T06:00:00Z"), act=snapshots.activation(plain))       # same evidence rebuilt: the R2 behaviour is kept
    assert later["snapshot_reused"] and later["first_committed_at_utc"] == early["first_committed_at_utc"] and later["revision"] == 1


# ============================================================ queues
def seed_queues(db, c, *, history_days=5, per_history_day=6, recent_days=("2026-11-18", "2026-11-19"), per_recent_day=4):
    n = 0
    for d in range(history_days):
        day = date(2026, 9, 1) + timedelta(days=d)
        for _ in range(per_history_day):
            n += 1; put(db, n, f"$QQQ #{n} note", created=f"{day.isoformat()}T15:00:00Z", fetched="2026-11-20T05:30:00Z", mode=provider.RETRO, lang="zh" if n % 2 else "en")
    for day in recent_days:
        for _ in range(per_recent_day):
            n += 1; put(db, n, f"$QQQ #{n} note", created=f"{day}T15:00:00Z", fetched="2026-11-20T05:30:00Z", lang="zh" if n % 2 else "en")
    stance.map_topics(db, c)
    return n


def test_q_recent_work_goes_first_and_newest_day_first_whatever_the_language(db, plain):
    seed_queues(db, plain)
    items = stance.work_items(db, plain)
    assert [i["queue"] for i in items] == ["recent"] * 8 + ["history"] * 30
    assert [i["sample_day"] for i in items[:8]] == ["2026-11-19"] * 4 + ["2026-11-18"] * 4
    assert [i["sample_day"] for i in items[8:14]] == ["2026-09-01"] * 6
    day = [i["post_id"] for i in items[:4]]
    assert day == sorted(day, key=store.sha) and len({i["lang"] for i in items[:4]}) == 2      # hash order inside a batch: language and text play no part
    m = Model()
    out = stance.run(db, plain, m, limit=5, now="2026-11-20T06:00:00Z")
    done = [r[0] for r in db.execute("SELECT p.created_at_utc FROM x_stance_labels l JOIN x_posts p USING(provider, post_id)")]
    assert out["model_calls_by_queue"] == {"recent": 5, "history": 0} and all(d >= "2026-11-18" for d in done)
    assert out["remaining_by_queue"] == {"recent": 3, "history": 30}


def test_q_history_yields_while_the_recent_queue_has_work(db, plain):
    seed_queues(db, plain)
    m = Model()
    out = stance.run(db, plain, m, queue="history", limit=100, now="2026-11-20T06:00:00Z")
    assert out["stopped"] == "YIELDED_TO_RECENT_QUEUE" and out["model_calls"] == 0 and m.calls == [] and out["remaining_by_queue"]["recent"] == 8
    assert stance.run(db, plain, m, queue="recent", limit=100, now="2026-11-20T06:00:00Z")["model_calls"] == 8
    hist = stance.run(db, plain, m, queue="history", limit=7, now="2026-11-20T07:00:00Z")
    assert hist["stopped"] is None and hist["model_calls_by_queue"] == {"recent": 0, "history": 7} and hist["remaining_by_queue"]["history"] == 23


def test_q_status_reports_backlog_age_and_completion_by_sample_day_and_pending_is_not_neutral(db, plain):
    seed_queues(db, plain)
    stance.run(db, plain, Model(), limit=6, now="2026-11-20T06:00:00Z")
    q = stance.queue_status(db, plain, now="2026-11-20T17:30:00Z")
    assert (q["recent"]["pairs"], q["recent"]["pending"], q["history"]["pairs"], q["history"]["pending"]) == (8, 2, 30, 30)
    assert q["recent"]["by_sample_day"]["2026-11-19"] == {"pairs": 4, "done": 4, "share_done": 1.0} and q["recent"]["by_sample_day"]["2026-11-18"]["done"] == 2
    assert q["recent"]["oldest_pending_age_hours"] == 12.0 and q["recent"]["sample_days_complete"] == 1 and q["history"]["sample_days_complete"] == 0
    assert q["rates_last_7_days"]["posts_received"] == 38 and q["rates_last_7_days"]["model_inferences_completed"] == 6
    a, b = store.parse("2026-11-18T05:00:00Z"), store.parse("2026-11-19T05:00:00Z")
    res = aggregate.aggregate(rows(db, plain, a, b), collection_status="PLANNED_SAMPLE", threshold=1, pending=prog(db, plain, a, b)["pending"], cfg=plain)
    assert res["state"] == "UNKNOWN" and res["state_reason"] == "CLASSIFICATION_INCOMPLETE" and res["accounts"]["neutral"] == 0


def test_q_replay_recent_days_are_never_starved_by_an_unbounded_history_queue(db, plain):
    """No network, no model. Six simulated days. Every day 40 back-fill pairs and 10 recent pairs arrive; the
    worker manages 12 calls a day, is down on day 3 and is restarted mid-run on day 4."""
    capacity, log, n = 12, [], 0
    m = Model()
    for day in range(6):
        today = date(2026, 11, 10) + timedelta(days=day)
        for _ in range(40):
            n += 1; put(db, n, f"$QQQ #{n} note", created=f"{(date(2026, 8, 1) + timedelta(days=n % 60)).isoformat()}T15:00:00Z", fetched=f"{today.isoformat()}T05:30:00Z", mode=provider.RETRO)
        for _ in range(10):
            n += 1; put(db, n, f"$QQQ #{n} note", created=f"{(today - timedelta(days=1)).isoformat()}T15:00:00Z", fetched=f"{today.isoformat()}T05:30:00Z")
        stance.map_topics(db, plain)
        now = f"{today.isoformat()}T06:00:00Z"
        if day == 2:                                             # worker down all day
            out = stance.run(db, plain, Model(fail_after=0), limit=capacity, now=now)
        elif day == 3:                                           # killed after 5 calls, restarted, continues
            first = stance.run(db, plain, Model(fail_after=5), limit=capacity, now=now)
            second = stance.run(db, plain, m, limit=capacity - first["model_calls"], now=now)
            out = {"model_calls": first["model_calls"] + second["model_calls"], "model_calls_by_queue": {k: first["model_calls_by_queue"][k] + second["model_calls_by_queue"][k] for k in stance.QUEUES}}
        else:
            out = stance.run(db, plain, m, limit=capacity, now=now)
        q = stance.queue_status(db, plain, now=now)
        log.append({"day": day, "calls": out["model_calls"], "by_queue": out["model_calls_by_queue"], "recent_pending": q["recent"]["pending"], "history_pending": q["history"]["pending"],
                    "newest_recent_day_done": next(iter(q["recent"]["by_sample_day"].values()))["share_done"]})
    assert [x["recent_pending"] for x in log] == [0, 0, 10, 8, 6, 4]             # the outage costs one day of recent work, and it is the OLDER recent day that waits
    assert [x["newest_recent_day_done"] for x in log] == [1.0, 1.0, 0.0, 1.0, 1.0, 1.0]
    assert [x["by_queue"]["history"] for x in log] == [2, 2, 0, 0, 0, 0]          # history only ever got what recent did not need
    assert log[-1]["history_pending"] == 240 - 4                                  # capacity (12/day) is below arrivals (50/day): the back-fill backlog grows and is reported, not hidden
    assert sum(x["calls"] for x in log) == 60 and db.execute("SELECT count(*) FROM x_stance_labels").fetchone()[0] == 60    # nothing lost or doubled across the restart


# ============================================================ account rules
def test_r_the_provider_record_keeps_likes_followers_and_handle_as_fetched(db):
    p = put(db, 1, likes=37, followers=2_500_000, handle="SomeBigAccount")
    e = p["engagement_snapshot"]
    assert (e["likeCount"], e["authorFollowers"], e["authorUserName"], e["snapshot_at_utc"]) == (37, 2_500_000, "SomeBigAccount", "2026-11-17T05:30:00Z")
    put(db, 1, likes=9999, followers=1)                                            # seen again later: the first reading stands
    assert json.loads(db.execute("SELECT engagement_snapshot_json FROM x_posts").fetchone()[0])["likeCount"] == 37


def test_r_posts_under_twenty_likes_are_outside_the_sample_and_are_never_classified(db, cfg):
    put(db, 1, "$QQQ #1 popular", likes=20)
    put(db, 2, "$QQQ #2 quiet", likes=19)
    put(db, 3, "$QQQ #3 unknown", likes=None)
    full = stance.map_sample(db, cfg)
    assert (full["pairs"], full["filtered"]) == (3, 2) and "@account-rules-v2-20261125-" in full["sample_version"]
    q = dict(db.execute("SELECT post_id, quality FROM x_post_topics"))
    assert q == {"1": "PENDING", "2": "FILTERED_LOW_LIKES", "3": "FILTERED_LIKES_UNKNOWN"}
    m = Model({"1": "bullish", "2": "bearish", "3": "bearish"})
    out = stance.run(db, cfg, m, now="2026-11-17T06:00:00Z")
    assert out["model_calls"] == 1 and m.calls == ["1"] and db.execute("SELECT count(*) FROM x_stance_labels").fetchone()[0] == 1     # no compute spent on filtered posts
    p = prog(db, cfg)
    assert (p["matched_posts"], p["pending"], p["filtered_low_likes"], p["filtered_likes_unknown"]) == (1, 0, 1, 1)
    assert [r["post_id"] for r in rows(db, cfg)] == ["1"]
    # even a label that somehow exists for a filtered post is not counted
    db.execute(f"INSERT INTO x_stance_labels({','.join(store.LABEL_COLUMNS[:16])}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               ("twitterapi_io", "2", "qqq", "QQQ", "bearish", "OK", 1, None, None, "m", "r", "p", "2026-11-17T06:00:00Z", "raw", "k2", stance.identity_hash(cfg)))
    assert [r["post_id"] for r in rows(db, cfg)] == ["1"]


def test_r_follower_tiers_and_named_accounts_weight_the_vote_and_equal_weight_is_reported_beside_it(db, cfg):
    c = copy.deepcopy(cfg)
    c["aggregation"]["account_rules"]["named_accounts"] = [{"label": "named one", "handle": "@NamedOne", "author_id": None, "weight": 8}, {"label": "no handle yet", "handle": None, "author_id": None, "weight": 99}]
    put(db, 1, "$QQQ #1", followers=2_000_000)                    # tier weight 5
    put(db, 2, "$QQQ #2", followers=500)                          # 1
    put(db, 3, "$QQQ #3", followers=800)                          # 1
    put(db, 4, "$QQQ #4", followers=50_000)                       # 2
    put(db, 5, "$QQQ #5", followers=300, handle="namedone")       # named: 8, whatever the tier
    put(db, 6, "$QQQ #6", followers=None)                         # unknown: 1
    stance.map_topics(db, c)
    stance.run(db, c, Model({"1": "bullish", "2": "bearish", "3": "bearish", "4": "bearish", "5": "neutral", "6": "bearish"}), now="2026-11-17T06:00:00Z")
    r = aggregate.aggregate(rows(db, c), collection_status="PLANNED_SAMPLE", threshold=1, cfg=c)
    assert r["accounts"] == {"bullish": 1, "bearish": 4, "neutral": 1} and r["shares_equal_weight"]["bearish"] == pytest.approx(4 / 6)
    assert r["weighting"]["weighted_votes"] == {"bullish": 5.0, "bearish": 5.0, "neutral": 8.0} and r["weighting"]["weight_total"] == 18.0
    assert r["shares"] == {"bullish": pytest.approx(5 / 18), "bearish": pytest.approx(5 / 18), "neutral": pytest.approx(8 / 18)} and sum(r["shares"].values()) == pytest.approx(1)
    assert r["state"] == "neutral" and r["state_reason"] is None                 # the weighted leader decides; equal weight would have said bearish
    assert r["vote_weights"] == {"1": 5.0, "2": 1.0, "3": 1.0, "4": 2.0, "5": 8.0, "6": 1.0}
    assert r["weighting"]["by_basis"]["NAMED:named one"] == {"accounts": 1, "weight": 8.0} and r["weighting"]["by_basis"]["FOLLOWERS_UNKNOWN"]["accounts"] == 1
    assert r["weighting"]["largest_single_account_share_of_weight"] == pytest.approx(8 / 18)
    s = sample.summary(c)
    assert s["named_accounts_not_identified"] == 1 and [n["identified"] for n in s["named_accounts"]] == [True, False]      # a name without a handle matches nobody
    off = copy.deepcopy(c); off["aggregation"]["account_rules"]["enabled"] = False
    assert aggregate.aggregate(rows(db, c), collection_status="PLANNED_SAMPLE", threshold=1, cfg=off)["shares"]["bearish"] == pytest.approx(4 / 6)


def test_r_heavy_accounts_do_not_make_a_small_sample_look_large(db, cfg):
    put(db, 1, "$QQQ #1", followers=5_000_000)
    put(db, 2, "$QQQ #2", followers=5_000_000)
    stance.map_topics(db, cfg)
    stance.run(db, cfg, Model(), now="2026-11-17T06:00:00Z")
    r = aggregate.aggregate(rows(db, cfg), collection_status="PLANNED_SAMPLE", threshold=3, cfg=cfg)
    assert r["weighting"]["weight_total"] == 10.0 and r["accounts_total"] == 2 and (r["state"], r["state_reason"]) == ("UNKNOWN", "LOW_SAMPLE")


def test_r_changing_a_rule_is_a_new_sample_version_and_the_old_snapshot_stays(db, cfg):
    put(db, 1, "$QQQ #1", likes=25)
    put(db, 2, "$QQQ #2", likes=60, followers=300_000)
    stance.map_topics(db, cfg)
    stance.run(db, cfg, Model({"1": "bullish", "2": "bearish"}), now="2026-11-17T06:00:00Z")
    kw = dict(window_kind="calendar_day", window_label="2026-11-16", start=START, end=END, topic_id="qqq", target_id="QQQ", mode="BATCH_AFTER_CLOSE")
    r1 = rows(db, cfg)
    s1 = aggregate.commit(db, **kw, result=aggregate.aggregate(r1, collection_status="PLANNED_SAMPLE", threshold=1, cfg=cfg), rows=r1, now=store.parse("2026-11-17T06:10:00Z"), act=snapshots.activation(cfg))
    assert s1["state"] == "bearish" and s1["weighting"]["weighted_votes"] == {"bullish": 1.0, "bearish": 3.0, "neutral": 0}
    stricter = copy.deepcopy(cfg); stricter["aggregation"]["account_rules"]["min_likes"] = 50
    assert sample.sample_version(stricter) != sample.sample_version(cfg) and stance.identity_hash(stricter) == stance.identity_hash(cfg)
    stance.map_topics(db, stricter)
    again = stance.run(db, stricter, Model(fail_after=0), now="2026-11-18T06:00:00Z")
    assert again["model_calls"] == 0                                             # a rule change re-derives; it does not re-infer and does not buy posts again
    r2 = rows(db, stricter)
    s2 = aggregate.commit(db, **kw, result=aggregate.aggregate(r2, collection_status="PLANNED_SAMPLE", threshold=1, cfg=stricter), rows=r2, now=store.parse("2026-11-18T06:10:00Z"), act=snapshots.activation(stricter))
    assert [r["post_id"] for r in r2] == ["2"] and s2["series_key"] != s1["series_key"] and s2["revision"] == 1
    assert snapshots.revisions(db, s1["series_key"])[0]["vote_post_ids"] == ["1", "2"]


def test_r_the_model_is_never_shown_likes_or_followers(db, cfg):
    put(db, 1, "$QQQ #1 note", likes=4242, followers=987654, handle="VisibleNowhere")
    stance.map_topics(db, cfg)
    seen = []
    stance.run(db, cfg, lambda prompt: (seen.append(prompt), answer(1))[1], now="2026-11-17T06:00:00Z")
    assert seen and not any(x in seen[0] for x in ("4242", "987654", "VisibleNowhere"))


# ============================================================ off-server worker
def test_o_export_run_elsewhere_import_checked_and_idempotent(db, plain, tmp_path):
    seed_queues(db, plain, history_days=1, per_history_day=3, recent_days=("2026-11-19",), per_recent_day=2)
    work, results = tmp_path / "work.jsonl", tmp_path / "results.jsonl"
    exp = offload.export_pending(db, plain, work, now="2026-11-20T06:00:00Z")
    lines = [json.loads(x) for x in work.read_text(encoding="utf-8").splitlines()]
    assert exp["items"] == 5 and exp["by_queue"] == {"recent": 2, "history": 3} and [x["queue"] for x in lines] == ["recent"] * 2 + ["history"] * 3
    assert all(x["member_of"] == [plain["topic_vocabulary_version"]] for x in lines) and exp["inference_identity"] == stance.identity_hash(plain)
    box = {"t": "2026-11-20T07:00:00Z"}
    man = offload.write_results(work, results, Model(box=box, seconds=2), plain, clock=lambda: box["t"], worker={"host": "local-gpu", "gpu": "synthetic"})
    assert man["items"] == 5 and man["work_sha256"] == exp["sha256"]
    out = offload.import_results(db, plain, results, now="2026-11-20T08:00:00Z")
    assert (out["labels_written"], out["already_present"], out["rejected"]) == (5, 0, {}) and prog(db, plain, store.parse("2026-08-01T00:00:00Z"), store.parse("2026-12-01T00:00:00Z"))["pending"] == 0
    r = db.execute("SELECT inference_started_at_utc s, inference_completed_at_utc c, label_written_at_utc w, identity_status st, inference_identity i FROM x_stance_labels ORDER BY inference_completed_at_utc LIMIT 1").fetchone()
    assert (r["s"], r["c"], r["w"], r["st"], r["i"]) == ("2026-11-20T07:00:00Z", "2026-11-20T07:00:02Z", "2026-11-20T08:00:00Z", "RECORDED", stance.identity_hash(plain))   # the worker's real times, not the import time
    twice = offload.import_results(db, plain, results, now="2026-11-20T09:00:00Z")
    assert (twice["labels_written"], twice["already_present"]) == (0, 5) and db.execute("SELECT count(*) FROM x_stance_labels").fetchone()[0] == 5
    assert stance.run(db, plain, Model(fail_after=0), now="2026-11-20T10:00:00Z")["model_calls"] == 0          # the server has nothing left to infer
    assert offload.export_pending(db, plain, tmp_path / "again.jsonl")["items"] == 0


def test_o_tampered_or_foreign_results_are_refused(db, plain, tmp_path):
    seed_queues(db, plain, history_days=0, recent_days=("2026-11-19",), per_recent_day=3)
    work, results = tmp_path / "work.jsonl", tmp_path / "results.jsonl"
    offload.export_pending(db, plain, work)
    other = copy.deepcopy(plain); other["classifier"]["generation"]["seed"] = 43
    with pytest.raises(offload.OffloadError, match="WORKER_CONFIG_IS_NOT_THE_REQUESTED_IDENTITY"):
        offload.write_results(work, results, Model(), other)                      # a worker configured differently refuses the work file
    offload.write_results(work, results, Model(), plain, clock=lambda: "2026-11-20T07:00:00Z")
    with pytest.raises(offload.OffloadError, match="INFERENCE_IDENTITY_IS_NOT_THE_ACTIVE_ONE"):
        offload.import_results(db, other, results)                                # and the server refuses answers of another identity
    man = results.with_name(results.name + ".manifest.json")
    good = results.read_text(encoding="utf-8")
    results.write_text(good.replace("bullish", "bearish"), encoding="utf-8", newline="\n")
    with pytest.raises(offload.OffloadError, match="RESULTS_FILE_HASH_MISMATCH"):
        offload.import_results(db, plain, results)
    rows_ = [json.loads(x) for x in good.splitlines()]
    rows_[0]["text_hash"] = "0" * 64                                               # answer for a different text
    rows_[1]["post_id"] = "99999"                                                  # a post the store does not have
    rows_[2]["cache_key"] = "f" * 64                                               # computed for something else
    extra = dict(rows_[2]); extra["topic_id"], extra["cache_key"] = "semiconductor", rows_[2]["cache_key"]      # not a member of any live sample
    body = "\n".join(json.dumps(x, sort_keys=True) for x in rows_ + [extra, "junk"]) + "\n"
    results.write_text(body, encoding="utf-8", newline="\n")
    m = json.loads(man.read_text(encoding="utf-8")); m["sha256"] = offload._sha_file(results); man.write_text(json.dumps(m), encoding="utf-8")
    out = offload.import_results(db, plain, results)
    assert out["labels_written"] == 0 and db.execute("SELECT count(*) FROM x_stance_labels").fetchone()[0] == 0
    assert out["rejected"] == {"TEXT_CHANGED_OR_WRONG_POST": 1, "POST_NOT_IN_STORE": 1, "INFERENCE_KEY_MISMATCH": 1, "NOT_A_MEMBER_OF_A_LIVE_SAMPLE_VERSION": 1, "LINE_NOT_READABLE": 1}


# ============================================================ read model
def test_the_page_reads_membership_identity_queues_and_rules_from_the_same_definitions(db, cfg, tmp_path):
    put(db, 1, "$QQQ #1", likes=30, followers=200_000)
    put(db, 2, "$QQQ #2", likes=3)
    put(db, 3, "$QQQ #3", likes=45)
    stance.map_all(db, cfg)
    stance.run(db, cfg, Model(), limit=1, now="2026-11-17T06:00:00Z")
    db.commit()
    v = pipeline.read_for_portal(cfg, tmp_path / "x.sqlite3", env={})
    assert v["classification"] == {"matched_pairs": 2, "classified_or_prefiltered": 1, "pending": 1, "share_done": 0.5, "filtered_low_likes": 1, "filtered_likes_unknown": 0, "legacy_identity_labels": 0}
    assert v["account_rules"]["min_likes"] == 20 and v["account_rules"]["named_accounts_not_identified"] == 2 and v["settings"]["sample_version"] == sample.sample_version(cfg)
    assert v["queues"]["recent"]["pending"] == 1 and v["queues"]["history"]["pairs"] == 0 and v["classifier"]["inference_identity"] == stance.identity_hash(cfg)


# ============================================================ human review task
def test_h_the_review_task_covers_excluded_items_hides_the_model_and_freezes_a_split(db, plain, tmp_path):
    from scripts.x_stance_v1 import review
    n = 0
    for day in ("2026-11-18", "2026-11-19"):
        for lang in ("en", "zh"):
            for _ in range(10):
                n += 1; put(db, n, f"$QQQ #{n} note", created=f"{day}T15:00:00Z", fetched="2026-11-20T05:30:00Z", lang=lang)
    stance.map_topics(db, plain)

    def model(prompt):                                        # every third item is excluded by the model, every seventh fails the format
        pid = int(prompt.split("#")[1].split()[0])
        if pid % 7 == 0:
            return "not json"
        if pid % 3 == 0:
            return json.dumps({"post_id": str(pid), "topic_id": "qqq", "target_id": "QQQ", "is_relevant": False, "quality_status": "IRRELEVANT", "stance": None, "evidence_text": None, "reason_short": None})
        return answer(pid, "bullish" if pid % 2 else "bearish", f"#{pid}")
    stance.run(db, plain, model, now="2026-11-20T06:00:00Z")
    pop = review.candidates(db, plain)
    assert len(pop) == 40 and {r["model_quality"] for r in pop} == {"OK", "IRRELEVANT", "JSON_INVALID"}
    task = review.build_task(pop, n=20, seed=7, identity=stance.identity_hash(plain), created_at_utc="2026-11-20T07:00:00Z")
    man = review.write_task(task, tmp_path / "review", "task_v1")
    assert man["drawn"] == 20 and man["strata"] == 4 and all(x["stratum_drawn"] == 5 and x["inclusion_probability"] == 0.5 for x in task["reviewer"])
    assert set(man["model_outcomes_drawn"]) >= {"IRRELEVANT"} and sum(man["by_split"].values()) == 20 and min(man["by_split"].values()) > 0
    shown = (tmp_path / "review" / "task_v1.reviewer.jsonl").read_text(encoding="utf-8")
    assert "model_label" not in shown and "model_quality" not in shown and "IRRELEVANT" not in shown and "bullish" not in shown     # the reviewer file has no model answer
    again = review.build_task(pop, n=20, seed=7, identity=stance.identity_hash(plain), created_at_utc="2026-11-20T07:00:00Z")
    assert [x["item_id"] for x in again["reviewer"]] == [x["item_id"] for x in task["reviewer"]] and [x["split"] for x in again["reviewer"]] == [x["split"] for x in task["reviewer"]]


def test_h_scoring_keeps_relevance_and_three_class_apart_with_their_own_denominators():
    from scripts.x_stance_v1 import review
    mk = lambda i, hr, hs, ml, lang="en": ({"item_id": str(i), "split": "development", "stratum": {"sample_day": "d", "lang": lang, "topic_id": "qqq"}, "inclusion_probability": 0.5,
                                           "human_relevance": hr, "human_stance": hs}, {"item_id": str(i), "model_label": ml, "model_quality": "OK" if ml else "IRRELEVANT"})
    data = [mk(1, "relevant", "bullish", "bullish"), mk(2, "relevant", "bullish", "bearish"), mk(3, "relevant", "bearish", "bearish"), mk(4, "relevant", "neutral", None, "zh"),
            mk(5, "relevant", "neutral", None, "zh"), mk(6, "not_relevant", None, None), mk(7, "not_relevant", None, "bullish"), mk(8, "cannot_tell", None, None),
            ({"item_id": "9", "split": "locked_test", "stratum": {"sample_day": "d", "lang": "en", "topic_id": "qqq"}, "inclusion_probability": 0.5, "human_relevance": "relevant", "human_stance": "bullish"},
             {"item_id": "9", "model_label": "bullish", "model_quality": "OK"}),
            ({"item_id": "10", "split": "development", "stratum": {"sample_day": "d", "lang": "en", "topic_id": "qqq"}, "inclusion_probability": 0.5, "human_relevance": None, "human_stance": None},
             {"item_id": "10", "model_label": "bullish", "model_quality": "OK"})]
    s = review.score([r for r, _ in data], [k for _, k in data], split="development")
    assert (s["items_in_split"], s["reviewed"], s["human_relevant"], s["human_not_relevant"], s["human_cannot_tell"]) == (9, 8, 5, 2, 1)       # the unreviewed item is not scored
    assert (s["relevance_recall"]["numerator"], s["relevance_recall"]["denominator"]) == (3, 5) and s["wrong_exclusions"]["value"] == pytest.approx(0.4)
    assert (s["three_class"]["n"], s["three_class"]["accuracy"]["value"], s["three_class"]["bull_bear_swaps"]) == (3, pytest.approx(2 / 3), 1)       # 2 of the 3 the model labelled, not 2 of 5
    assert s["confusion_human_rows_model_columns"]["neutral"]["EXCLUDED_OR_UNJUDGED"] == 2 and s["labelled_although_not_relevant"]["numerator"] == 1
    assert s["coverage"]["value"] == pytest.approx(4 / 8) and s["by_language"]["zh"]["relevance_recall"]["value"] == 0.0 and s["three_class"]["accuracy"]["wilson95"][0] < 2 / 3
    with pytest.raises(ValueError):
        review.score([{**data[0][0], "human_stance": None}], [data[0][1]], split="development")


# ============================================================ local GPU adapter (no GPU needed for these)
def test_g_each_local_checkpoint_gets_its_own_identity_and_the_adapter_never_repairs():
    from scripts.x_stance_v1 import local_gpu
    base = C.load()
    facts = lambda repo, commit, sha: {"repo": repo, "hf_snapshot_commit": commit, "weights": [{"file": "model.safetensors", "bytes": 1, "sha256": sha}], "tokenizer_json_sha256": "a" * 64, "chat_template_sha256": "b" * 64}
    ver = {"torch": "2.x", "transformers": "4.x", "cuda": "12.8", "device": "gpu"}
    small = local_gpu.candidate_config(base, facts("Qwen/Qwen2.5-1.5B-Instruct", "1" * 40, "c" * 64), ver, dtype="float16")
    big = local_gpu.candidate_config(base, facts("Qwen/Qwen2.5-3B-Instruct", "2" * 40, "d" * 64), ver, dtype="float16")
    other_file = local_gpu.candidate_config(base, facts("Qwen/Qwen2.5-3B-Instruct", "2" * 40, "e" * 64), ver, dtype="float16")
    other_lib = local_gpu.candidate_config(base, facts("Qwen/Qwen2.5-3B-Instruct", "2" * 40, "d" * 64), {**ver, "torch": "9.9"}, dtype="float16")
    ids = {stance.identity_hash(x) for x in (base, small, big, other_file, other_lib)}
    assert len(ids) == 5                                                           # server reference, both checkpoints, a different weight file, a different library: all distinct
    assert small["classifier"]["adapter_version"] == local_gpu.ADAPTER_VERSION != stance.ADAPTER_VERSION and base["classifier"]["adapter_version"] == stance.ADAPTER_VERSION
    f = local_gpu.first_object
    assert f('{"a": "}{", "b": {"c": 1}}\n```') == '{"a": "}{", "b": {"c": 1}}' and f('{"a": 1') is None
    w = local_gpu.HFClassifier.wrap
    ids_ = {"post_id": 7, "topic_id": "qqq", "target_id": "QQQ"}
    good = '{"is_relevant": true, "quality_status": "OK", "stance": "bullish", "evidence_text": "#7"}'
    assert json.loads(w(good, ids_, False))["post_id"] == "7"
    assert w('```json\n' + good + '\n```', ids_, False).startswith("```")           # fenced text is passed through and fails validation; nothing strips it
    assert w('{"stance": "bullish"}', ids_, False) == '{"stance": "bullish"}' and w(good, ids_, True) == ""       # wrong keys pass through; a cut-off answer is empty
    post = {"post_id": "7", "author_text": "$QQQ #7 note"}
    assert stance.validate_output(w(good, ids_, False), post=post, topic_id="qqq", target_id="QQQ")["label"] == "bullish"
    assert stance.validate_output(w('```json\n' + good + '\n```', ids_, False), post=post, topic_id="qqq", target_id="QQQ")["quality_status"] == "JSON_INVALID"


def test_the_smoke_command_really_calls_the_model_under_the_shipped_rules_and_says_so_when_it_does_not(monkeypatch, capsys):
    from scripts.x_stance_v1 import cli

    class Fake(Model):
        def props(self):
            return {"chat_template_sha256": C.load()["classifier"]["runtime"]["chat_template_sha256"]}

        def summary(self):
            return {"calls": len(self.calls)}

        def __call__(self, prompt):
            self.calls.append(1)
            return "not json"
    fake = Fake()
    monkeypatch.setattr(cli, "_llm", lambda cfg: fake)
    assert cli.main(["smoke-model"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["run"]["model_calls"] == 11 == len(fake.calls) and out["second_run_model_calls"] == 0 and out["matched_to_topic"] == 11

    class Wrong(Fake):
        def props(self):
            return {"chat_template_sha256": "0" * 64}
    monkeypatch.setattr(cli, "_llm", lambda cfg: Wrong())
    assert cli.main(["smoke-model"]) == 1
    assert json.loads(capsys.readouterr().out)["status"].startswith("NO_MODEL_CALL_RUNTIME_IDENTITY_MISMATCH")


# ---------- one pending pair, one worker (added 2026-11-23) ----------
def test_o_an_exported_pair_is_claimed_and_neither_the_server_nor_a_second_export_takes_it(db, plain, tmp_path):
    seed_queues(db, plain, history_days=1, per_history_day=3, recent_days=("2026-11-19",), per_recent_day=2)
    work, results = tmp_path / "work.jsonl", tmp_path / "results.jsonl"
    exp = offload.export_pending(db, plain, work, now="2026-11-20T06:00:00Z", limit=3, claim_hours=24)
    assert exp["items"] == 3 and exp["claim"] == {"pairs": 3, "claimed_at_utc": "2026-11-20T06:00:00Z", "expires_at_utc": "2026-11-21T06:00:00Z"}
    again = offload.export_pending(db, plain, tmp_path / "second.jsonl", now="2026-11-20T06:10:00Z", claim_hours=24)
    assert again["items"] == 2 and again["skipped_claimed_by_another_export"] == 3                     # the other two only
    cpu = stance.run(db, plain, Model(), now="2026-11-20T06:20:00Z")
    assert cpu["model_calls"] == 0 and cpu["claimed_by_another_worker"] == 5                           # the server worker leaves all five alone
    box = {"t": "2026-11-20T07:00:00Z"}
    offload.write_results(work, results, Model(box=box, seconds=2), plain, clock=lambda: box["t"], worker={"host": "local-gpu"})
    out = offload.import_results(db, plain, results, now="2026-11-20T08:00:00Z")
    assert out["labels_written"] == 3 and out["claims_released"] == 3
    late = stance.run(db, plain, Model(), now="2026-11-21T06:10:01Z")                                  # the second export never came back: its claim ran out
    assert late["model_calls"] == 2 and late["claimed_by_another_worker"] == 0
    assert db.execute("SELECT count(*) FROM x_stance_labels").fetchone()[0] == 5


def test_o_a_candidate_model_is_another_identity_and_its_answers_never_enter_the_active_one(db, plain, tmp_path):
    seed_queues(db, plain, history_days=1, per_history_day=2, recent_days=("2026-11-19",), per_recent_day=2)
    cand = copy.deepcopy(plain)
    cand["classifier"]["model_id"] = "local/candidate-3b"; cand["classifier"]["immutable_revision"] = "hf:candidate|weights:abc"
    assert stance.identity_hash(cand) != stance.identity_hash(plain)
    work, results = tmp_path / "w.jsonl", tmp_path / "r.jsonl"
    offload.export_pending(db, cand, work, now="2026-11-20T06:00:00Z")
    box = {"t": "2026-11-20T07:00:00Z"}
    offload.write_results(work, results, Model(box=box, seconds=1), cand, clock=lambda: box["t"])
    with pytest.raises(offload.OffloadError):
        offload.import_results(db, plain, results, now="2026-11-20T08:00:00Z")                         # not the active configuration's answers
    out = offload.import_results(db, cand, results, now="2026-11-20T08:00:00Z")
    assert out["labels_written"] == 4 and {r[0] for r in db.execute("SELECT DISTINCT inference_identity FROM x_stance_labels")} == {stance.identity_hash(cand)}
    span = (store.parse("2026-08-01T00:00:00Z"), store.parse("2026-12-01T00:00:00Z"))
    assert prog(db, plain, *span)["pending"] == 4 and prog(db, cand, *span)["pending"] == 0            # the active identity still has everything to do


def test_r_like_floor_sensitivity_counts_what_each_floor_admits_and_keeps_missing_apart(db, cfg):
    from scripts.x_stance_v1 import like_sensitivity
    for i, likes in enumerate([0, 0, 3, 7, 12, 25, 300, None]):
        put(db, f"s{i}", author=f"acct{i % 5}", likes=likes)
    stance.map_all(db, cfg)
    ver = db.execute("SELECT DISTINCT topic_version FROM x_post_topics ORDER BY 1").fetchone()[0]
    t = like_sensitivity.table(db, ver)
    f = t["floors"]
    assert t["matched_pairs_all"] >= 8 and t["floor_in_force"] == 20 and "must not be chosen" in t["warning"]
    posts = lambda k: f[k]["posts"]
    assert (posts("0"), posts("5"), posts("10"), posts("20")) == (7, 4, 3, 2) and t["like_count_missing"]["posts"] == 1        # missing is not zero likes
    assert t["likes_distribution_known"]["0"] >= 2 and f["0"]["accounts"] >= f["20"]["accounts"]
    chk = t["stored_membership_check"]
    assert chk["FILTERED_LOW_LIKES"] == chk["known_below_floor_in_force"] and chk["FILTERED_LIKES_UNKNOWN"] == chk["missing"]   # agrees with what the rule in force stored
    before = db.execute("SELECT count(*), group_concat(quality) FROM x_post_topics").fetchone()
    like_sensitivity.all_versions(db)
    assert tuple(db.execute("SELECT count(*), group_concat(quality) FROM x_post_topics").fetchone()) == tuple(before)           # reads only


def test_o_posts_with_unicode_line_separators_survive_export_work_and_import(db, plain, tmp_path):
    """The first real work file (2026-11-23) held posts with U+2028; read back with str.splitlines() it broke apart."""
    ls, ps, nel = chr(0x2028), chr(0x2029), chr(0x85)
    odd = "$QQQ I expect strong future returns." + ls + "second paragraph" + ps + "third" + nel + "end"
    put(db, 71, text=odd, fetched="2026-11-19T05:30:00Z", created="2026-11-18T15:00:00Z")
    put(db, 72, fetched="2026-11-19T05:30:00Z", created="2026-11-18T15:00:00Z")
    stance.map_all(db, plain)
    work, results = tmp_path / "w.jsonl", tmp_path / "r.jsonl"
    exp = offload.export_pending(db, plain, work, now="2026-11-20T06:00:00Z")
    raw = work.read_bytes().decode("utf-8")
    assert exp["items"] == 2 and raw.count(chr(10)) == 2 and len(raw.splitlines()) == 2 and ls not in raw and ps not in raw and nel not in raw   # one line per item, even for a naive reader
    assert any(odd in json.loads(x)["prompt"] for x in raw.split(chr(10)) if x)                         # and the text itself is unchanged
    box = {"t": "2026-11-20T07:00:00Z"}
    man = offload.write_results(work, results, Model(box=box, seconds=1), plain, clock=lambda: box["t"])
    out = offload.import_results(db, plain, results, now="2026-11-20T08:00:00Z")
    assert man["items"] == 2 and (out["lines"], out["labels_written"], out["rejected"]) == (2, 2, {})
    assert offload._lines(work) == [x for x in raw.split(chr(10)) if x]
