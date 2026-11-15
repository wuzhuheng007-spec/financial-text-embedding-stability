"""Synthetic failure-risk checks for bounded Jin10 storage; no provider calls."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import pytest

from scripts.jin10_mcp.cli import RequestGate, TrialRequestStop, _live_tool_listing, load_spec
from scripts.jin10_mcp.engine import (checked_source_url, ingest_probe_pages, normalize_item,
                                     resolve_raw_locator, run_bounded, source_time)
from scripts.jin10_mcp.export import export_display
from scripts.jin10_mcp.schema import Budget, ToolCallError, ToolSpec
from scripts.jin10_mcp.store import Jin10Store, canonical


def flash_spec() -> ToolSpec:
    return ToolSpec(
        name="list_flash", kind="FLASH",
        input_schema={"type": "object", "properties": {"limit": {"type": "integer"},
                                                         "cursor": {"type": "string"}},
                      "required": ["limit"]},
        arguments={"limit": 2}, items_path=("data", "items"),
        cursor_argument="cursor", has_more_path=("data", "has_more"),
        next_cursor_path=("data", "next_cursor"),
        url_field="url", url_semantics_confirmed=True,
        published_field="time", content_field="content",
    )


def flash_page(items, has_more=False, cursor=None):
    return {"data": {"items": items, "has_more": has_more, "next_cursor": cursor}}


def budget(**changes):
    values = {"max_requests": 3, "max_pages": 3, "max_items": 10,
              "max_attempts_per_page": 1, "max_runtime_seconds": 20}
    values.update(changes)
    return Budget(**values)


def count(connection: sqlite3.Connection, table: str) -> int:
    return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_overlap_replay_and_content_revision_append_versions(tmp_path):
    spec = flash_spec()
    first = {"url": "https://flash.jin10.com/detail/1", "time": "2026-11-15 09:00:00",
             "content": "英伟达发布新产品"}
    revised = {**first, "content": "英伟达更新新产品说明"}
    pages = [flash_page([first], True, "c1"), flash_page([first], True, "c2")]
    with Jin10Store(tmp_path / "local.sqlite") as store:
        calls = iter(pages)
        receipt = run_bounded(store=store, stream_key="flash", spec=spec,
                              budget=budget(max_requests=2, max_pages=2),
                              call_tool=lambda *_: next(calls))
        assert receipt["status"] == "BUDGET_EXHAUSTED_INCOMPLETE"
        assert store.status("flash")["has_replay_checkpoint"]
        seen = []
        def resumed(_, args, __):
            seen.append(args.get("cursor"))
            return flash_page([revised], False)
        completed = run_bounded(store=store, stream_key="flash", spec=spec,
                                budget=budget(), call_tool=resumed, mode="resume")
        assert seen == ["c1"]  # Replay last complete page, then deduplicate/version.
        assert completed["status"] == "COMPLETE_FOR_REQUESTED_RANGE"
        assert count(store.connection, "raw_responses") == 3
        assert count(store.connection, "logical_items") == 1
        assert count(store.connection, "item_versions") == 2
        assert count(store.connection, "item_fetches") == 3
        assert store.status("flash")["coverage_status"] == "COMPLETE_FOR_REQUESTED_RANGE"


def test_no_id_url_priority_composite_collision_and_samsung_alias(tmp_path):
    spec = flash_spec()
    received = "2026-11-15T01:00:00Z"
    url_item = {"url": "https://flash.jin10.com/detail/2", "time": "2026-11-15",
                "content": "三星扩产；Samsung Electronics 单独披露计划"}
    item = normalize_item(url_item, spec, received, 0)
    assert item["key_kind"] == "CHECKED_URL" and not item["potential_collision"]
    unconfirmed = normalize_item(url_item, replace(spec, url_semantics_confirmed=False), received, 0)
    assert unconfirmed["key_kind"] == "TIME_TITLE_OR_CONTENT_PREFIX" and unconfirmed["potential_collision"]
    assert item["published_at_utc"] is None and item["time_precision"] == "DATE_ONLY"
    assert item["issuer_candidates"] == ["SAMSUNG_ELECTRONICS"]
    assert item["normalized"]["verified_fact_status"] == "NOT_VERIFIED"
    ambiguous = normalize_item({"time": "2026-11-15", "content": "三星扩产"}, spec, received, 0)
    assert ambiguous["key_kind"] == "TIME_TITLE_OR_CONTENT_PREFIX"
    assert ambiguous["potential_collision"]
    assert ambiguous["issuer_candidates"] == []
    with Jin10Store(tmp_path / "local.sqlite") as store:
        result = run_bounded(store=store, stream_key="flash", spec=spec, budget=budget(),
                             call_tool=lambda *_: flash_page([url_item, {"time": "2026-11-15", "content": "三星扩产"}]))
        assert result["status"] == "COMPLETE_FOR_REQUESTED_RANGE"
    public = export_display(tmp_path / "local.sqlite")
    assert not public["raw_provider_text_included"]
    assert all("content" not in row for row in public["records"])
    private = export_display(tmp_path / "local.sqlite", include_private_content=True)
    assert private["raw_provider_text_included"] and any(row.get("content") for row in private["records"])


def test_429_obeys_wait_and_401_stops_without_retry(tmp_path):
    waits = []
    attempts = 0
    def limited(*_):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ToolCallError("rate limit", http_status=429, retry_after_seconds=2)
        return flash_page([])
    with Jin10Store(tmp_path / "local.sqlite") as store:
        receipt = run_bounded(store=store, stream_key="flash", spec=flash_spec(),
                              budget=budget(max_attempts_per_page=2), call_tool=limited,
                              sleep=waits.append)
        assert receipt["status"] == "COMPLETE_FOR_REQUESTED_RANGE"
        assert attempts == 2 and waits == [2]
        assert count(store.connection, "request_attempts") == 2
        auth_calls = 0
        def denied(*_):
            nonlocal auth_calls
            auth_calls += 1
            raise ToolCallError("unauthorized", http_status=401)
        failure = run_bounded(store=store, stream_key="flash", spec=flash_spec(),
                              budget=budget(max_attempts_per_page=3), call_tool=denied)
        assert failure["status"] == "AUTH_DENIED_STOP" and failure["coverage_gap"]
        assert auth_calls == 1


def test_partial_page_is_not_empty_news_and_does_not_advance(tmp_path):
    with Jin10Store(tmp_path / "local.sqlite") as store:
        result = run_bounded(store=store, stream_key="flash", spec=flash_spec(), budget=budget(),
                             call_tool=lambda *_: {"data": {"items": [], "has_more": True}})
        assert result["status"] == "INCOMPLETE_PAGE" and result["coverage_gap"]
        assert count(store.connection, "raw_responses") == 1
        assert store.connection.execute("SELECT page_complete FROM raw_responses").fetchone()[0] == 0
        assert count(store.connection, "item_versions") == 0
        assert store.status("flash")["coverage_status"] == "INCOMPLETE_PAGE"


def test_calendar_missing_consensus_keeps_null_and_snapshot_time(tmp_path):
    spec = ToolSpec(name="list_calendar", kind="CALENDAR",
                    input_schema={"type": "object", "properties": {}, "required": []},
                    arguments={}, items_path=("data",), published_field="pub_time",
                    title_field="title", content_field="title")
    row = {"title": "US CPI", "pub_time": "2026-11-15 08:30:00", "actual": "0.4",
           "consensus": "", "previous": "0.3", "revised": None}
    with Jin10Store(tmp_path / "local.sqlite") as store:
        result = run_bounded(store=store, stream_key="calendar", spec=spec, budget=budget(),
                             call_tool=lambda *_: {"data": [row]})
        assert result["status"] == "COMPLETE_FOR_REQUESTED_RANGE"
        normalized = store.connection.execute("SELECT normalized_json FROM item_versions").fetchone()[0]
        assert '"consensus":null' in normalized
        assert '"pre_release_consensus_status":"NOT_ESTABLISHED_FROM_CURRENT_FETCH"' in normalized
    display = export_display(tmp_path / "local.sqlite", include_private_content=True)
    assert display["records"][0]["calendar_values"]["consensus"] is None
    assert display["records"][0]["source_published_raw"] is None
    assert display["records"][0]["scheduled_for_raw"] == "2026-11-15 08:30:00"
    assert display["records"][0]["scheduled_time_precision"] == "SECOND_NO_TIMEZONE"


def test_external_stop_preserves_partial_checkpoint(tmp_path):
    with Jin10Store(tmp_path / "local.sqlite") as store:
        def one_page(*_):
            store.request_stop("flash")
            return flash_page([{"url": "https://flash.jin10.com/detail/3", "content": "Intel"}], True, "next")
        result = run_bounded(store=store, stream_key="flash", spec=flash_spec(),
                             budget=budget(), call_tool=one_page)
        assert result["status"] == "STOPPED_WITH_CHECKPOINT"
        assert result["checkpoint_available"]


def test_import_captured_probe_uses_original_receive_time_without_new_request(tmp_path):
    spec = flash_spec()
    captured = [{
        "arguments": {"limit": 2}, "http_status": 200,
        "received_at_utc": "2026-11-15T01:23:45Z",
        "response": {"result": {"content": [{"type": "text", "text":
                    '{"data":{"items":[{"content":"AMD news","time":"2026-11-15"}],"has_more":true,"next_cursor":"next"}}'}]}},
    }]
    with Jin10Store(tmp_path / "local.sqlite") as store:
        receipt = ingest_probe_pages(store=store, stream_key="flash", spec=spec,
                                     captured_pages=captured)
        assert receipt["network_requests_in_this_import"] == 0
        assert receipt["status"] == "CAPTURED_PARTIAL"
        assert receipt["checkpoint_available"]
        assert count(store.connection, "request_attempts") == 0
        assert store.connection.execute("SELECT first_received_at_utc FROM logical_items").fetchone()[0] == "2026-11-15T01:23:45Z"
        raw, locator = store.connection.execute(
            "SELECT r.response_json,v.raw_locator FROM raw_responses r JOIN item_versions v ON v.raw_id=r.raw_id"
        ).fetchone()
        assert resolve_raw_locator(json.loads(raw), locator)["content"] == "AMD news"


def test_page_two_failure_keeps_last_complete_checkpoint(tmp_path):
    spec = flash_spec()
    calls = 0
    def callback(*_):
        nonlocal calls
        calls += 1
        if calls == 1:
            return flash_page([{"url": "https://flash.jin10.com/detail/4", "content": "news"}], True, "c1")
        return {"data": {"items": [], "has_more": True}}  # Missing cursor.
    with Jin10Store(tmp_path / "local.sqlite") as store:
        result = run_bounded(store=store, stream_key="flash", spec=spec,
                             budget=budget(), call_tool=callback)
        assert result["status"] == "INCOMPLETE_PAGE"
        assert count(store.connection, "raw_responses") == 2
        assert count(store.connection, "item_versions") == 1
        assert store.stream("flash")["replay_cursor_json"] == "null"
        assert store.stream("flash")["next_cursor_json"] == '"c1"'


def test_explicit_offset_preserves_minute_precision_without_guessing_zone_name():
    minute = source_time("2026-11-15T10:00+08:00", None)
    second = source_time("2026-11-15T10:00:05+08:00", None)
    unknown = source_time("2026-11-15 10:00", "Asia/Shanghai")
    assert minute == ("2026-11-15T02:00:00Z", "UTC+08:00", "MINUTE_WITH_TIMEZONE")
    assert second == ("2026-11-15T02:00:05Z", "UTC+08:00", "SECOND_WITH_TIMEZONE")
    assert unknown == (None, "Asia/Shanghai", "MINUTE_NO_TIMEZONE")


def test_confirmed_shape_must_match_actual_tools_list_schema(tmp_path):
    tool = {"name": "list_flash", "inputSchema": flash_spec().input_schema}
    witness_response = {"result": {"structuredContent": flash_page([
        {"content": "AMD news", "url": "https://flash.jin10.com/detail/5"}])}}
    (tmp_path / "flash_captures.json").write_text(
        json.dumps([{"response": witness_response}]), encoding="utf-8")
    shape = {
        "schema": "jin10_confirmed_tool_shape_v1", "tool_name": "list_flash",
        "input_schema_sha256": hashlib.sha256(canonical(tool["inputSchema"]).encode()).hexdigest(),
        "confirmed_response_sha256": hashlib.sha256(canonical(witness_response).encode()).hexdigest(),
        "confirmed_response_capture_file": "flash_captures.json",
        "confirmed_response_capture_index": 0,
        "kind": "FLASH", "arguments": {"limit": 2},
        "items_path": ["data", "items"], "cursor_argument": "cursor",
        "has_more_path": ["data", "has_more"], "next_cursor_path": ["data", "next_cursor"],
        "url_field": "url", "published_field": "time", "content_field": "content",
        "url_semantics_confirmed": True,
    }
    path = tmp_path / "shape.json"
    path.write_text(json.dumps(shape), encoding="utf-8")
    assert load_spec(path, tool).name == "list_flash"
    with pytest.raises(ValueError, match="schema changed"):
        load_spec(path, {**tool, "inputSchema": {"type": "object", "properties": {}}})
    shape["confirmed_response_sha256"] = "a" * 64
    path.write_text(json.dumps(shape), encoding="utf-8")
    with pytest.raises(ValueError, match="witness hash"):
        load_spec(path, tool)


def test_structured_content_locator_and_pending_resume_stop(tmp_path):
    item = {"content": "CoreWeave financing", "url": "https://flash.jin10.com/detail/5"}
    with Jin10Store(tmp_path / "local.sqlite") as store:
        receipt = run_bounded(store=store, stream_key="flash", spec=flash_spec(),
                              budget=budget(), call_tool=lambda *_: {
                                  "result": {"structuredContent": flash_page([item])}})
        assert receipt["status"] == "COMPLETE_FOR_REQUESTED_RANGE"
        raw, locator = store.connection.execute(
            "SELECT r.response_json,v.raw_locator FROM raw_responses r JOIN item_versions v ON v.raw_id=r.raw_id"
        ).fetchone()
        assert resolve_raw_locator(json.loads(raw), locator) == item
        store.request_stop("flash")
        def should_not_call(*_):
            raise AssertionError("Pending stop must survive resume")
        stopped = run_bounded(store=store, stream_key="flash", spec=flash_spec(),
                              budget=budget(), call_tool=should_not_call, mode="resume")
        assert stopped["status"] == "STOPPED"
        assert stopped["requests_attempted"] == 0


def test_tool_schema_discovery_uses_bounded_paginated_tools_list():
    class FakeSession:
        def __init__(self):
            self.seen = []
        def list_tools(self, cursor=None):
            self.seen.append(cursor)
            if cursor is None:
                return SimpleNamespace(payload={"result": {"tools": [], "nextCursor": "p2"}})
            if cursor == "p2":
                return SimpleNamespace(payload={"result": {"tools": [], "nextCursor": "p3"}})
            return SimpleNamespace(payload={"result": {"tools": [{"name": "list_calendar", "inputSchema": {}}]}})
    session = FakeSession()
    assert _live_tool_listing(session, "list_calendar")["name"] == "list_calendar"
    assert session.seen == [None, "p2", "p3"]


def test_article_url_rejects_userinfo_and_control_characters():
    assert checked_source_url("https://user:secret@flash.jin10.com/detail/1") is None
    assert checked_source_url("https://flash.jin10.com/detail/1\n") is None
    assert checked_source_url("https://flash.jin10.com/detail/1") == "https://flash.jin10.com/detail/1"


def test_trial_gate_counts_handshakes_and_spaces_each_http_attempt():
    state = {"now": 0.0}
    gate = RequestGate(max_requests=2, minimum_interval_seconds=5, deadline=20,
                       clock=lambda: state["now"],
                       sleep=lambda seconds: state.__setitem__("now", state["now"] + seconds))
    gate.before_request()
    gate.before_request()
    assert gate.total == 2 and state["now"] == 5
    with pytest.raises(TrialRequestStop, match="cap"):
        gate.before_request()


def test_missing_database_is_not_reported_as_empty_news(tmp_path):
    path = tmp_path / "not-created.sqlite"
    display = export_display(path)
    assert not path.exists()
    assert display["records"] == []
    assert display["acquisition_status"][0]["coverage_status"] == "NOT_STARTED"
