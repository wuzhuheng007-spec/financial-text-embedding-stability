"""X stance study: versioned keyword edits and the local-model adapter. The model worker is replaced by a stub
here (no inference happens in this file); texts and labels are made up."""
import copy
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from scripts.x_stance_v1 import batch, config as C, llm, provider, stance, store, vocab

UTC = timezone.utc
NOW = datetime(2026, 11, 20, 3, 0, tzinfo=UTC)               # last completed New York date: 2026-11-18


@pytest.fixture()
def cfg():
    c = C.load()
    c["aggregation"]["account_rules"]["enabled"] = False     # these modules pre-date the account rules (tests/test_x_stance_r3.py covers them)
    c["allocation"]["recent"].pop("sub_pools", None)         # ... and the split of the recent pool between watched authors and keywords (tests/test_x_stance_watchlist.py)
    c["classifier"]["runtime"]["chat_template_sha256"] = store.sha("<tpl>")      # what the stub worker below reports; R3 checks the running template against the pinned one
    return c


@pytest.fixture()
def db(tmp_path):
    d = store.connect(tmp_path / "x.sqlite3")
    yield d
    d.close()


# ---------- vocabulary ----------
def test_an_edit_creates_a_pending_version_and_never_recuts_a_frozen_plan(cfg, db):
    d = batch.last_completed_ny_date(NOW)
    old_plan = batch.plan_recent(db, cfg, d, NOW)
    hist = batch.plan_history(db, cfg, NOW)
    v = vocab.propose(db, cfg, [{"action": "add", "topic_id": "memory", "keyword": "HBM4"}, {"action": "disable", "topic_id": "market", "keyword": "关税"}], NOW)
    assert v["status"] == "PENDING" and v["parent"] == cfg["topic_vocabulary_version"] and v["effective_from_ny_date"] == "2026-11-19"   # the first date without a plan
    assert v["version"].startswith("vocab-") and v["impact"]["budget_changed"] is False and v["impact"]["daily_cap_posts"] == 5000
    same = batch.plan_recent(db, cfg, d, NOW + timedelta(hours=2))                   # same day, after the edit: the frozen plan is returned as it was
    assert same["reused"] and same["sha256"] == old_plan["sha256"] and same["vocab_version"] == cfg["topic_vocabulary_version"]
    assert vocab.pending(db)["version"] == v["version"] and vocab.active(db, cfg)["version"] == cfg["topic_vocabulary_version"]
    nxt = batch.plan_recent(db, cfg, d + timedelta(days=1), NOW + timedelta(days=1))
    assert nxt["vocab_version"] == v["version"] and nxt["sha256"] != old_plan["sha256"] and vocab.pending(db) is None
    texts = {r[0] for r in db.execute("SELECT query_text FROM x_queries WHERE vocab_version=?", (v["version"],))}
    assert any("HBM4" in t for t in texts) and not any("关税" in t for t in texts)
    assert batch.plan_history(db, cfg, NOW + timedelta(days=1))["sha256"] == hist["sha256"]              # the historical plan keeps its own vocabulary
    ov = vocab.overview(db, cfg)
    assert ov["active"]["version"] == v["version"] and [h["status"] for h in ov["history"]] == ["ACTIVE"]
    assert vocab.active(db, cfg, on=d)["version"] == cfg["topic_vocabulary_version"]                     # as of the earlier date the old one was in force


def test_edits_stack_on_the_pending_version_and_superseded_drafts_are_kept(cfg, db):
    a = vocab.propose(db, cfg, [{"action": "add", "topic_id": "qqq", "keyword": "QQQM"}], NOW)
    b = vocab.propose(db, cfg, [{"action": "edit", "topic_id": "qqq", "keyword": "QQQM", "new_keyword": "$QQQM"}], NOW)
    assert b["parent"] == a["version"] and "$QQQM" in next(t for t in b["topics"] if t["id"] == "qqq")["keywords"]
    st = dict(db.execute("SELECT version, status FROM x_vocab_versions").fetchall())
    assert st == {a["version"]: "REPLACED_BEFORE_ACTIVATION", b["version"]: "PENDING"}
    with pytest.raises(vocab.VocabError) as e:                                     # disable then enable again: nothing would change
        vocab.propose(db, cfg, [{"action": "disable", "topic_id": "qqq", "keyword": "$QQQM"}, {"action": "enable", "topic_id": "qqq", "keyword": "$QQQM"}], NOW + timedelta(minutes=1))
    assert str(e.value) == "NO_EFFECTIVE_CHANGE"


@pytest.mark.parametrize("change,reason", [
    ({"action": "add", "topic_id": "qqq", "keyword": "$QQQ"}, "KEYWORD_EXISTS"),
    ({"action": "add", "topic_id": "qqq", "keyword": "x" * 61}, "KEYWORD_LENGTH"),
    ({"action": "add", "topic_id": "qqq", "keyword": "  "}, "KEYWORD_LENGTH"),
    ({"action": "add", "topic_id": "qqq", "keyword": "QQQ) OR (bitcoin"}, "KEYWORD_HAS_QUERY_SYNTAX"),
    ({"action": "add", "topic_id": "qqq", "keyword": "QQQ since_time:1"}, "KEYWORD_HAS_QUERY_SYNTAX"),
    ({"action": "add", "topic_id": "qqq", "keyword": "QQQ min_faves:100"}, "KEYWORD_HAS_QUERY_SYNTAX"),
    ({"action": "add", "topic_id": "qqq", "keyword": 'QQQ "exact"'}, "KEYWORD_HAS_QUERY_SYNTAX"),
    ({"action": "add", "topic_id": "nope", "keyword": "QQQ1"}, "UNKNOWN_ACTION_OR_TOPIC"),
    ({"action": "drop", "topic_id": "qqq", "keyword": "$QQQ"}, "UNKNOWN_ACTION_OR_TOPIC"),
    ({"action": "disable", "topic_id": "qqq", "keyword": "not there"}, "KEYWORD_NOT_FOUND"),
])
def test_invalid_edits_are_rejected_and_write_nothing(cfg, db, change, reason):
    with pytest.raises(vocab.VocabError) as e:
        vocab.propose(db, cfg, [change], NOW)
    assert str(e.value) == reason and db.execute("SELECT count(*) FROM x_vocab_versions").fetchone()[0] == 0


def test_a_topic_cannot_lose_its_last_keyword_and_long_vocabularies_split_under_512(cfg, db):
    kws = next(t for t in cfg["topics"] if t["id"] == "qqq")["keywords"]
    with pytest.raises(vocab.VocabError) as e:
        vocab.propose(db, cfg, [{"action": "disable", "topic_id": "qqq", "keyword": k} for k in kws], NOW)
    assert str(e.value) == "TOPIC_WOULD_HAVE_NO_KEYWORD"
    many = [{"action": "add", "topic_id": "memory", "keyword": f"memory keyword number {i:02d}"} for i in range(40)]
    v = vocab.propose(db, cfg, many, NOW)
    imp = v["impact"]
    assert imp["queries"] > 8 and imp["max_query_chars_with_time_operators"] <= 512 and imp["recent_pages_per_unit"] < 4      # more queries, shallower pages, same budget
    assert imp["recent_posts_if_every_page_is_full"] <= imp["recent_pool_cap_posts"] == 3000
    with pytest.raises(vocab.VocabError) as e:
        vocab.propose(db, cfg, [{"action": "add", "topic_id": "market", "keyword": f"another long market keyword phrase {i:03d}"} for i in range(400)], NOW)
    assert str(e.value) == "TOO_MANY_QUERIES_FOR_THE_DAILY_POOL"


# ---------- model adapter (stubbed worker) ----------
def pinned(cfg):
    return cfg                                                 # the shipped config pins model, revision, prompt and generation


def seed(db, cfg, n=6):
    for i in range(1, n + 1):
        p = provider.normalize_twitterapi_io({"id": str(i), "url": f"https://x.com/u/status/{i}", "text": f"I am buying more $QQQ today, note {i}", "lang": "en",
                                              "createdAt": "Thu Nov 19 15:00:00 +0000 2026", "author": {"id": str(100 + i)}}, now="2026-11-20T04:00:00Z", batch="b", mode=provider.BATCH)
        store.put_post(db, p)
    stance.map_topics(db, cfg); db.commit()


def reply(post_id, stance_="bullish", evidence="buying more $QQQ"):
    return json.dumps({"is_relevant": True, "quality_status": "OK", "stance": stance_, "evidence_text": evidence})    # what the model writes: no ids


def stub(worker, answers):
    def _req(path, body=None, timeout=None):
        if path == "/v1/chat/completions":
            pid = body["messages"][0]["content"].split(", note ")[1].split()[0]
            a = answers(pid, body)
            if isinstance(a, Exception):
                raise a
            return {"choices": [{"message": {"content": a[0]}, "finish_reason": a[1]}], "usage": {"prompt_tokens": 300, "completion_tokens": 40}}
        return {"build_info": "b-test", "model_path": "/models/x.gguf", "default_generation_settings": {"n_ctx": 2048}, "total_slots": 1, "chat_template": "<tpl>"}
    worker._req = _req
    return worker


def test_adapter_sends_only_the_prompt_with_the_pinned_generation_and_schema(cfg, db):
    seed(db, cfg, 1)
    seen = []
    w = stub(llm.LlamaServer("http://unused", generation=cfg["classifier"]["generation"]), lambda pid, body: (seen.append(body), (reply(pid), "stop"))[1])
    out = stance.run(db, pinned(cfg), w)
    b = seen[0]
    assert out["model_calls"] == 1 and out["by_quality"] == {"OK": 1}
    assert (b["temperature"], b["seed"], b["max_tokens"], b["stream"]) == (0, 42, 96, False) and len(b["messages"]) == 1
    ok_shape, unjudged = llm.OUTPUT_SCHEMA["oneOf"]
    assert b["response_format"]["json_schema"]["schema"] == llm.OUTPUT_SCHEMA and set(ok_shape["properties"]["stance"]["enum"]) == {"bullish", "bearish", "neutral"}
    assert ok_shape["properties"]["quality_status"] == {"const": "OK"} and unjudged["properties"]["stance"] == {"type": "null"}        # a stance and "OK" can only occur together
    assert "post_id" not in ok_shape["properties"] and "post_id: " not in b["messages"][0]["content"]                              # the model is not asked to repeat ids
    text = b["messages"][0]["content"]
    assert "<post>" in text and "Target: QQQ" in text and not any(k in text.lower() for k in ("likecount", "price was", "return over"))
    row = db.execute("SELECT model_id, model_revision, label, inferred_at_utc FROM x_stance_labels").fetchone()
    assert row[0] == cfg["classifier"]["model_id"] and row[1] == cfg["classifier"]["immutable_revision"] and row[2] == "bullish"
    assert row[3] >= "2026-11-20T04:00:00Z"                                          # never before the post was received
    assert w.summary()["calls"] == 1 and w.props()["chat_template_sha256"] == store.sha("<tpl>")


def test_truncated_or_wrong_output_is_a_quality_state_not_a_label(cfg, db):
    seed(db, cfg, 4)
    answers = {"1": ("", "length"), "2": ('{"stance": "bullish"}', "stop"), "3": (reply(3, evidence="words the author never wrote"), "stop"), "4": (reply(4, stance_="bearish"), "stop")}
    w = stub(llm.LlamaServer("http://unused", generation=cfg["classifier"]["generation"]), lambda pid, body: answers[pid])
    out = stance.run(db, pinned(cfg), w)
    got = dict(db.execute("SELECT post_id, quality_status FROM x_stance_labels").fetchall())
    assert got == {"1": "JSON_INVALID", "2": "JSON_INVALID", "3": "EVIDENCE_NOT_IN_TEXT", "4": "OK"} and out["by_quality"]["OK"] == 1
    assert [r[0] for r in db.execute("SELECT label FROM x_stance_labels WHERE label IS NOT NULL")] == ["bearish"] and w.summary()["truncated_by_output_limit"] == 1


def test_limit_resumes_and_a_dead_worker_leaves_items_pending(cfg, db):
    seed(db, cfg, 6)
    w = stub(llm.LlamaServer("http://unused", generation=cfg["classifier"]["generation"]), lambda pid, body: (reply(pid), "stop"))
    first = stance.run(db, pinned(cfg), w, limit=2)
    assert (first["model_calls"], first["remaining"]) == (2, 4) and db.execute("SELECT count(*) FROM x_stance_labels").fetchone()[0] == 2
    down = stub(llm.LlamaServer("http://unused", generation=cfg["classifier"]["generation"]), lambda pid, body: stance.ClassifierUnavailable("WORKER_NOT_REACHABLE_OR_TIMEOUT"))
    stopped = stance.run(db, pinned(cfg), down, limit=10)
    assert stopped["model_calls"] == 0 and stopped["stopped"] == "WORKER_NOT_REACHABLE_OR_TIMEOUT" and db.execute("SELECT count(*) FROM x_stance_labels").fetchone()[0] == 2   # nothing guessed
    rest = stance.run(db, pinned(cfg), w, limit=10)
    assert (rest["model_calls"], rest["remaining"], rest["stopped"]) == (4, 0, None) and db.execute("SELECT count(*) FROM x_stance_labels").fetchone()[0] == 6
    assert stance.run(db, pinned(cfg), lambda p: pytest.fail("already labelled"), limit=10)["model_calls"] == 0


def test_smoke_texts_are_marked_synthetic_and_carry_no_expected_labels():
    assert len(llm.SMOKE_TEXTS) == 12 and {t[0] for t in llm.SMOKE_TEXTS} == {"en", "zh"} and all(len(t) == 3 for t in llm.SMOKE_TEXTS)
    assert len(llm.schema_hash()) == 64
