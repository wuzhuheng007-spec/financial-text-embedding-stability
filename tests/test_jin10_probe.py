"""Probe checks that protect the real response parser and bounded call plan."""

import pytest

from scripts.jin10_probe_once import inspect_reply, tool_in_listing


def test_structured_content_flash_and_calendar_inspection():
    flash = {"result": {"structuredContent": {"status": 200, "data": {
        "items": [{"content": "synthetic", "time": "2026-11-15T08:00:00+08:00",
                   "url": "https://flash.jin10.com/detail/test"}],
        "has_more": True, "next_cursor": "next"}}}}
    summary = inspect_reply(flash, "flash")
    assert summary["items"] == 1
    assert summary["has_more"] is True
    assert summary["next_cursor_present"] is True
    calendar = {"result": {"structuredContent": {"status": 200, "data": [
        {"title": "synthetic", "consensus": None, "pub_time": "2026-11-15 08:30"}]}}}
    summary = inspect_reply(calendar, "calendar")
    assert summary["items"] == 1
    assert summary["has_more"] is None
    assert summary["item_fields"] == ["consensus", "pub_time", "title"]


def test_no_required_arguments_are_guessed():
    listing = {"tools": [{"name": "list_flash", "inputSchema": {
        "type": "object", "properties": {"date": {"type": "string"}},
        "required": ["date"]}}]}
    with pytest.raises(ValueError, match="requires parameters"):
        tool_in_listing(listing, "list_flash")
