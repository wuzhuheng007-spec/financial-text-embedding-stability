"""English news judgement: candidates, strict validation, export -> answers -> checked import -> read models."""
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from scripts.quant_news_v1 import stance, store

EN = "Chip stocks rally as demand for artificial intelligence accelerates across the industry this quarter."
ARTICLES = [
    ("1", "Nvidia raises its annual sales forecast as data center demand grows", "<p>Nvidia said on Monday it raised its annual sales forecast. " + EN + "</p>", ["NVDA"], "2026-11-23T14:00:00Z"),
    ("2", "Micron shares fell 3% on Monday", "<p>Micron shares fell 3% on Monday. DRAM prices were unchanged, analysts said in a note to clients.</p>", ["MU"], "2026-11-23T15:00:00Z"),
    ("3", "Retailers prepare for the holiday shopping season across the United States", "<p>Retailers are hiring seasonal workers ahead of the holidays, the trade group said.</p>", ["NVDA"], "2026-11-22T15:00:00Z"),
    ("4", "Nasdaq 100 futures are little changed before the opening bell", None, [], "2026-11-24T12:00:00Z"),
    ("5", "엔비디아 주가가 월요일 상승한 이유는 무엇인가", "<p>엔비디아 Nvidia 주가 상승</p>", ["NVDA"], "2026-11-24T13:00:00Z"),
    ("6", "A MUST read: the mutual fund industry and its fees explained for new investors", "<p>Mutual funds charge fees. This must be understood by investors before they buy.</p>", [], "2026-11-21T15:00:00Z"),
]


@pytest.fixture()
def news(tmp_path):
    path = tmp_path / "news.sqlite3"
    with closing(store.connect(path)) as db, db:
        for i, title, body, syms, at in ARTICLES:
            store.apply(db, {"upstream": "benzinga", "upstream_id": i, "title": title, "body": body, "symbols": syms, "url": f"https://example.com/a/{i}", "source": "Benzinga",
                             "provider_created_at": at, "provider_updated_at": at}, provider="benzinga_stream", via="stream", received_at="2026-11-24T20:00:00Z")
    store._ENGLISH_VERDICT.clear()
    return path


def ndb(path):
    d = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True); d.row_factory = sqlite3.Row
    return d


def ans(stance_="BULLISH", relevant=True, evidence="", reason="理由"):
    return json.dumps({"relevant": relevant, "stance": stance_, "evidence": evidence, "reason": reason}, ensure_ascii=False)


def test_candidates_come_from_the_text_and_a_supplier_tag_only_finds_them(news):
    with closing(ndb(news)) as d:
        cs = {(c["row"]["upstream_id"], c["target_id"]): c["found_by"] for c in stance.candidates(d)}
    assert cs[("1", "NVDA")] == "TEXT" and cs[("1", "industry:semis")] == "TEXT"                    # named in title/body
    assert cs[("2", "MU")] == "TEXT" and cs[("2", "industry:memory")] == "TEXT"
    assert cs[("3", "NVDA")] == "SUPPLIER_TAG_ONLY"                                               # only the supplier's tag: still asked, the model decides relevance
    assert cs[("4", "QQQ")] == "TEXT"
    assert not any(k[0] == "5" for k in cs)                                                        # not English: the page does not show it, it is not analysed
    assert not any(k[0] == "6" for k in cs)                                                        # "MUST" / "mutual" are not the ticker MU
    assert len(cs) == len(set(cs))


@pytest.mark.parametrize("raw,status,label", [
    (ans("BULLISH", evidence="raised its annual sales forecast"), "OK", "bullish"),
    (ans("NEUTRAL"), "OK", "neutral"),
    (ans("BULLISH", evidence="nvidia  RAISES its annual sales forecast"), "OK", "bullish"),          # same words, other letter case and spacing
    (ans("BULLISH", evidence="Nvidia lifts its annual sales forecast"), "EVIDENCE_NOT_IN_TEXT", None),  # a paraphrase is not the text
    (ans(None, relevant=False), "IRRELEVANT", None),
    (ans(None, relevant=True), "INSUFFICIENT_TEXT", None),
    (ans("BULLISH", evidence="a sentence that is not in the article"), "EVIDENCE_NOT_IN_TEXT", None),
    (ans("BULLISH", relevant=False), "JSON_INVALID", None),
    ('{"relevant": true, "stance": "POSITIVE", "evidence": "", "reason": ""}', "JSON_INVALID", None),
    ("```json\n{}\n```", "JSON_INVALID", None), ("", "INFERENCE_ERROR", None), (None, "INFERENCE_ERROR", None),
])
def test_validation_is_strict_and_nothing_else_becomes_neutral(news, raw, status, label):
    with closing(ndb(news)) as d:
        row = next(c["row"] for c in stance.candidates(d) if c["row"]["upstream_id"] == "1")
    got = stance.validate(raw, item=stance.make_item(row, "NVDA"))
    assert (got["status"], got["label"]) == (status, label)
    if "RAISES" in str(raw):
        assert got["evidence"] == "Nvidia raises its annual sales forecast"                       # stored as the article wrote it


def test_with_a_title_only_the_evidence_must_be_in_the_title(news):
    with closing(ndb(news)) as d:
        row = next(c["row"] for c in stance.candidates(d) if c["row"]["upstream_id"] == "4")
    item = stance.make_item(row, "QQQ")
    assert item["title_only"] and "仅有标题" in item["text"] and "{" not in stance.fill(item).split("只输出以下 JSON")[0].replace(item["title"], "")
    assert stance.validate(ans("NEUTRAL", evidence="little changed"), item=item)["status"] == "OK"
    assert stance.validate(ans("NEUTRAL", evidence="仅有标题"), item=item)["status"] == "EVIDENCE_NOT_IN_TEXT"        # the placeholder is not the article


def judge(x):
    t, it = x["item"]["target_id"], x["item"]
    if "holiday" in it["text"]:
        return ans(None, relevant=False, reason="与标的无关")
    if it["title_only"]:
        return ans(None, relevant=True, reason="只有标题，没有方向")
    if "Micron" in it["title"]:
        return "not json" if t == "industry:memory" else ans("NEUTRAL", evidence="Micron shares fell 3% on Monday", reason="只复述已发生的下跌")
    return ans("BULLISH", evidence="raised its annual sales forecast", reason="上调全年销售指引")


def results_for(work: Path, res: Path, model="Qwen/Qwen2.5-3B-Instruct"):
    lines = []
    for line in work.read_text(encoding="utf-8").splitlines():
        x = json.loads(line)
        lines.append(json.dumps({"upstream": x["upstream"], "upstream_id": x["upstream_id"], "version": x["version"], "content_sha256": x["content_sha256"], "target_id": x["item"]["target_id"],
                                 "found_by": x["found_by"], "raw_output": judge(x), "attempts": 1, "started_at": "2026-11-25T01:00:00Z", "completed_at": "2026-11-25T01:00:05Z"}, ensure_ascii=False))
    res.write_text("".join(s + "\n" for s in lines), encoding="utf-8")
    payload = stance.identity_payload({"model_id": model, "revision": "r1"})
    Path(str(res) + ".manifest.json").write_text(json.dumps({"sha256": hashlib.sha256(res.read_bytes()).hexdigest(), "prompt_sha256": stance.prompt_hash(), "targets_sha256": stance.targets_hash(),
                                                              "identity_payload": payload, "identity": stance.identity_hash(payload)}), encoding="utf-8")


def test_export_answers_checked_import_and_the_read_models(news, tmp_path):
    dbp = tmp_path / "stance.sqlite3"
    work, res = tmp_path / "w.jsonl", tmp_path / "r.jsonl"
    with closing(ndb(news)) as d, closing(stance.connect(dbp)) as db:
        man = stance.export_pending(d, db, work, limit_articles=30)
        assert man["articles"] == 4 and man["items"] == 8 and man["pending_articles_left_after_this_file"] == 0
        first = json.loads(work.read_text(encoding="utf-8").splitlines()[0])
        assert first["item"]["target_id"] == "NVDA" and "<p>" not in first["item"]["text"]              # priority order; markup never reaches the model
        results_for(work, res)
        imp = stance.import_results(d, db, res)
        assert imp["labels_written"] == 8 and imp["rejected"] == {} and imp["by_outcome"] == {"bullish": 2, "IRRELEVANT": 2, "neutral": 2, "JSON_INVALID": 1, "INSUFFICIENT_TEXT": 1}
        assert stance.import_results(d, db, res)["already_present"] == 8                                # the same file twice writes nothing twice
        assert stance.export_pending(d, db, tmp_path / "w2.jsonl")["items"] == 0
    v = stance.view(news, dbp)
    assert v["state"] == "READY" and v["note"] == "模型判断，待人工验证" and v["totals"]["pending_pairs"] == 0 and v["totals"]["selected_pairs"] == 8
    by = {t["target_id"]: t for t in v["targets"]}
    assert by["NVDA"]["articles"] == {"n": 1, "bullish": 1, "bearish": 0, "neutral": 0, "bullish_share": 1.0, "bearish_share": 0.0, "neutral_share": 0.0}
    assert by["NVDA"]["irrelevant"] == 1 and by["NVDA"]["selected"] == 2                             # the tag-only article was asked and judged irrelevant: not in the denominator
    assert by["MU"]["articles"]["neutral"] == 1 and by["industry:memory"]["failed"] == 1 and by["industry:memory"]["articles"]["n"] == 0
    assert by["QQQ"]["insufficient"] == 1 and by["QQQ"]["articles"]["n"] == 0                         # insufficient text is its own state, never neutral
    rep = by["NVDA"]["representative"]["bullish"][0]
    assert rep["url"] == "https://example.com/a/1" and rep["evidence"] == "raised its annual sales forecast" and rep["title"].startswith("Nvidia raises")
    day = next(d_ for d_ in v["days"] if d_["target_id"] == "NVDA" and d_["ny_date"] == "2026-11-23")
    assert day["articles"]["bullish"] == 1
    it = next(x for x in v["items"] if x["upstream_id"] == "1" and x["target_id"] == "NVDA")
    assert it["published_at"] == "2026-11-23T14:00:00Z" and it["first_received_at"] == "2026-11-24T20:00:00Z" and it["model_completed_at"] == "2026-11-25T01:00:05Z" and it["model_id"]
    page = stance.for_items(dbp)
    assert {a["target_id"]: a["outcome"] for a in page[("benzinga", "2")]} == {"MU": "neutral", "industry:memory": "failed", "industry:semis": "neutral"}
    assert by["industry:semis"]["articles"] == {"n": 2, "bullish": 1, "bearish": 0, "neutral": 1, "bullish_share": 0.5, "bearish_share": 0.0, "neutral_share": 0.5} and by["industry:semis"]["irrelevant"] == 1
    assert stance.for_items(tmp_path / "absent.sqlite3") == {}


def test_a_changed_file_another_prompt_or_a_vanished_version_is_refused(news, tmp_path):
    dbp, work, res = tmp_path / "s.sqlite3", tmp_path / "w.jsonl", tmp_path / "r.jsonl"
    with closing(ndb(news)) as d, closing(stance.connect(dbp)) as db:
        stance.export_pending(d, db, work)
        results_for(work, res)
        man = json.loads(Path(str(res) + ".manifest.json").read_text(encoding="utf-8"))
        res.write_text(res.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with pytest.raises(ValueError):
            stance.import_results(d, db, res)
        results_for(work, res)
        Path(str(res) + ".manifest.json").write_text(json.dumps({**man, "sha256": hashlib.sha256(res.read_bytes()).hexdigest(), "prompt_sha256": "0" * 64}), encoding="utf-8")
        with pytest.raises(ValueError):
            stance.import_results(d, db, res)
        lines = [json.loads(x) for x in res.read_text(encoding="utf-8").splitlines()]
        lines[0]["content_sha256"] = "f" * 64
        res.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines), encoding="utf-8")
        Path(str(res) + ".manifest.json").write_text(json.dumps({**man, "sha256": hashlib.sha256(res.read_bytes()).hexdigest()}), encoding="utf-8")
        imp = stance.import_results(d, db, res)
        assert imp["rejected"] == {"ARTICLE_VERSION_NOT_IN_STORE": 1} and imp["labels_written"] == 7
