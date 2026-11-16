"""Focused checks for the owner-only v1.6 display and analysis contract."""
from __future__ import annotations

import json
import sqlite3

from scripts.quant_personal_feed.analysis_contract_v16 import FIELDS, messages, validate
from scripts.quant_personal_feed.news_display_v16 import source_title
from scripts.quant_personal_feed.quote_select_v16 import choose_latest, read_latest
from scripts.quant_personal_feed.view_v16 import overlay
from scripts.quant_personal_feed import analysis_v16
from scripts.quant_personal_feed import cli as feed_cli


def test_latest_uses_source_time_instead_of_receipt_and_keeps_precision():
    base = {"symbol": "NVDA", "session": "REGULAR", "price_text": "100.000",
            "source_at": "2026-11-16T14:00:00Z", "received_at": "2026-11-16T14:00:01Z"}
    newest = dict(base, session="OVERNIGHT", price_text="100.010",
                  source_at="2026-11-16T15:00:00Z", received_at="2026-11-16T15:00:02Z")
    late = dict(base, price_text="99.0", source_at="2026-11-16T13:00:00Z",
                received_at="2026-11-16T16:00:00Z")
    unknown = dict(base, price_text="999", source_at=None,
                   received_at="2026-11-16T17:00:00Z")
    assert choose_latest("NVDA", [base, newest, late, unknown])["price_text"] == "100.010"
    assert choose_latest("NVDA", [unknown]) is None


def test_chart_and_dashboard_share_latest_selector(tmp_path):
    db = sqlite3.connect(tmp_path / "cache.sqlite3")
    db.execute("""CREATE TABLE session_quotes(id INTEGER PRIMARY KEY,owner_id INTEGER,
      symbol TEXT,provider_code TEXT,session TEXT,price_text TEXT,currency TEXT,
      source_at TEXT,received_at TEXT,provider_field_path TEXT,source_time_field_path TEXT,
      trade_date TEXT,date_basis TEXT,parent_date_hint TEXT,parent_source_at_hint TEXT,
      delay_status TEXT,source_channel TEXT)""")
    db.execute("""CREATE TABLE quotes(id INTEGER PRIMARY KEY,owner_id INTEGER,symbol TEXT,
      provider_code TEXT,price_text TEXT,currency TEXT,provider_time TEXT,received_at TEXT,
      market_state TEXT,delay_status TEXT)""")
    for session, price, source, received in [
        ("REGULAR", "50.00", "2026-11-16T14:00:00Z", "2026-11-16T14:00:01Z"),
        ("OVERNIGHT", "50.10", "2026-11-16T15:00:00Z", "2026-11-16T15:00:01Z"),
        ("REGULAR", "49.00", "2026-11-16T13:00:00Z", "2026-11-16T16:00:01Z")]:
        db.execute("""INSERT INTO session_quotes(owner_id,symbol,session,price_text,source_at,received_at)
          VALUES(1,'MU',?,?,?,?)""", (session,price,source,received))
    chart = read_latest(db, 1, "MU")
    cards = choose_latest("MU", [dict(symbol="MU", session="REGULAR", price_text="50.00",
                                     source_at="2026-11-16T14:00:00Z",received_at="2026-11-16T14:00:01Z"),
                                 dict(symbol="MU", session="OVERNIGHT", price_text="50.10",
                                     source_at="2026-11-16T15:00:00Z",received_at="2026-11-16T15:00:01Z")])
    assert (chart["price_text"], chart["source_at"]) == (cards["price_text"], cards["source_at"])


def test_supplier_title_falls_back_to_complete_source_sentence():
    assert source_title("", "报告第一句。第二句。")[0] == "报告第一句。"
    assert source_title("", "尚未形成完整句子")[1] == "SOURCE_EXCERPT_INCOMPLETE"
    assert source_title("供应商标题", "正文。") == ("供应商标题", "SUPPLIER_TITLE")


def _draft(**changes):
    obj = {"evidence_ids": ["S1"],
           "analysis_zh": "若计划落实，设备订单需求可能增加；但融资未落实会限制采购。",
           "analysis_en": "If the plan proceeds, equipment demand may rise; however, funding may constrain purchases.",
           "watch_zh": "关注正式融资公告。", "watch_en": "Watch for a formal funding announcement.",
           "tone": "UNKNOWN"}
    obj.update(changes)
    return obj


def test_schema_evidence_and_specificity_are_distinct_gates():
    spans = [{"id":"S1", "text":"公司计划扩建，但融资仍未确定。"}]
    good = validate(_draft(), spans)
    assert good["schema_exact"] and good["display_zh"] and good["display_en"]
    assert not validate(_draft(extra="x"), spans)["display_zh"]
    assert not validate(_draft(evidence_ids=["S9"]), spans)["display_zh"]
    vague = validate(_draft(analysis_zh="如果发生变化，市场可能受影响。"), spans)
    assert not vague["display_zh"]
    assert "COUNTERPOINT_MISSING" in vague["field_checks"]["analysis_zh"]["reasons"]
    new_number = validate(_draft(analysis_zh="若订单增长 99%，需求可能增加；但融资未落实会限制采购。"), spans)
    assert "NEW_NUMBER" in new_number["field_checks"]["analysis_zh"]["reasons"]
    copied_example = validate(_draft(analysis_zh="若虚构扩建计划落实，设备订单需求可能增加；但融资未落实会限制采购。"), spans)
    assert "EXAMPLE_CONTAMINATION" in copied_example["field_checks"]["analysis_zh"]["reasons"]


def test_english_failure_does_not_erase_supported_chinese():
    spans = [{"id":"S1", "text":"公司计划扩建，但融资仍未确定。"}]
    checks = validate(_draft(analysis_en=None, watch_en=None), spans)
    assert checks["display_zh"] and not checks["display_en"]
    assert set(_draft()) == FIELDS


def test_prompt_names_exact_allowed_evidence_ids_without_news_answer():
    prompt = messages([{"id": "S1", "text": "第一段。"}, {"id": "S2", "text": "第二段。"}])
    assert "Allowed evidence_ids: S1, S2" in prompt[1]["content"]
    assert 'Never output "1-2" as an ID' in prompt[0]["content"]


def test_old_rejected_result_not_promoted_without_matching_new_version(tmp_path):
    data = {"news":[{"item_key":"a", "version":1,"content_sha256":"b",
                     "analysis_status":"REVIEW_REJECTED","analysis_review_decision":"REJECT",
                     "analysis":{"analysis_zh":"old rejected"},"received_at":"2026-11-16T00:00:00Z"}],
            "top10":[]}
    result = overlay(data, owner_id=1, analysis_db=tmp_path / "absent.sqlite3",
                     config={"model_load_timeout_seconds":240,"generation_timeout_seconds":180,
                             "sentiment_windows_hours":[1],"sentiment_min_events":2})
    assert result["news"][0]["analysis"] is None
    assert result["news"][0]["analysis_display_kind"] == "SOURCE_RULE_FALLBACK"


def test_new_contract_cannot_change_prior_version_status(tmp_path, monkeypatch):
    monkeypatch.setattr(analysis_v16, "DB", tmp_path / "analysis.sqlite3")
    monkeypatch.setattr(analysis_v16, "_source", lambda *_: None)
    db = analysis_v16.open_db()
    owner, key, version, model = 1, "a" * 64, 1, "fixed-model"
    for prompt, schema, status in [("personal_news_conditional_v16_1", "personal_news_conditional_schema_v16_1", "DRAFT"),
                                   ("personal_news_conditional_v16_2", "personal_news_conditional_schema_v16_2", "DRAFT"),
                                   ("personal_news_conditional_v16_3", "personal_news_conditional_schema_v16_2", "DRAFT"),
                                   ("personal_news_conditional_v16_4", "personal_news_conditional_schema_v16_2", "PENDING")]:
        db.execute("""INSERT INTO analysis_jobs(owner_id,item_key,version,content_sha256,model_revision,
          prompt_version,schema_version,status,priority,enqueued_at,result_json)
          VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
          (owner,key,version,"b"*64,model,prompt,schema,status,1,"2026-11-16T00:00:00Z",
           '{"analysis_zh":"preserved"}' if status == "DRAFT" else None))
    job = db.execute("SELECT * FROM analysis_jobs WHERE prompt_version=?",(analysis_v16.PROMPT_VERSION,)).fetchone()
    assert analysis_v16.process_one(db,owner,{"model_revision":model},job)["status"] == "FAILED"
    statuses = dict(db.execute("SELECT prompt_version,status FROM analysis_jobs"))
    assert statuses["personal_news_conditional_v16_1"] == "DRAFT"
    assert statuses["personal_news_conditional_v16_2"] == "DRAFT"
    assert statuses["personal_news_conditional_v16_3"] == "DRAFT"
    assert statuses["personal_news_conditional_v16_4"] == "FAILED"
    db.close()


def test_raw_attempt_files_are_separate_for_each_contract_version():
    base = (1, "a" * 64, 1, "fixed-model", "prompt-v1", "schema-v1")
    old_request, old_response = analysis_v16.attempt_paths(base, 1)
    revised_request, revised_response = analysis_v16.attempt_paths(
        (*base[:4], "prompt-v2", "schema-v2"), 1)
    assert old_request != revised_request
    assert old_response != revised_response
    assert old_request != old_response


def test_snapshot_sqlite_failure_has_bounded_recovery(monkeypatch):
    calls = []
    waits = []
    class Stop:
        def is_set(self):
            return False
        def wait(self, seconds):
            waits.append(seconds)
    def transient(*_):
        calls.append(1)
        if len(calls) < 3:
            raise sqlite3.OperationalError("locked")
    monkeypatch.setattr(feed_cli, "run_moomoo_snapshot", transient)
    feed_cli.recover_snapshot_sqlite({}, 1, Stop())
    assert len(calls) == 3 and waits == [2, 4]
