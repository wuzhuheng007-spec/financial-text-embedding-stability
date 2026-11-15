"""Offline protocol regression checks; no Jin10 account or network access."""

from __future__ import annotations

import io
import json

import pytest

from scripts.jin10_mcp_http import McpProtocolError, McpSession


class Reply:
    def __init__(self, status, payload=None, *, content_type="application/json", session=None):
        self.status = status
        self.headers = {"Content-Type": content_type}
        if session:
            self.headers["MCP-Session-Id"] = session
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8") if payload is not None else b""
        self.stream = io.BytesIO(body)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def read(self, count=-1):
        return self.stream.read(count)

    def readline(self, count=-1):
        return self.stream.readline(count)


class Opener:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.messages = []

    def open(self, request, timeout):
        self.messages.append((json.loads(request.data), dict(request.header_items()), timeout))
        return next(self.replies)


def test_handshake_uses_negotiated_version_and_session_without_logging_token(monkeypatch):
    monkeypatch.setenv("JIN10_MCP_TOKEN", "synthetic-token")
    opener = Opener([
        Reply(200, {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-06-18", "capabilities": {}}}, session="synthetic-session"),
        Reply(202),
        Reply(200, {"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "list_flash", "inputSchema": {"type": "object"}}]}}),
    ])
    client = McpSession(max_requests=3)
    client._opener = opener
    assert client.initialize().http_status == 200
    assert client.initialized().http_status == 202
    assert client.list_tools().payload["result"]["tools"][0]["name"] == "list_flash"
    first = opener.messages[0]
    assert first[0]["method"] == "initialize"
    assert "Mcp-protocol-version" not in first[1]
    assert opener.messages[2][1]["Mcp-protocol-version"] == "2025-06-18"
    assert opener.messages[2][1]["Mcp-session-id"] == "synthetic-session"
    with pytest.raises(RuntimeError, match="budget"):
        client.list_tools()


def test_sse_tool_reply_and_id_guard(monkeypatch):
    monkeypatch.setenv("JIN10_MCP_TOKEN", "synthetic-token")
    sse = b'event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{"tools":[]}}\n\n'
    opener = Opener([
        Reply(200, {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-11-25"}}),
        Reply(202),
        Reply(200, sse, content_type="text/event-stream"),
    ])
    client = McpSession()
    client._opener = opener
    client.initialize()
    client.initialized()
    assert client.list_tools().payload["result"]["tools"] == []
    assert client.requests_used == 3


def test_requires_credential_and_rejects_unsupported_version(monkeypatch):
    monkeypatch.delenv("JIN10_MCP_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="absent"):
        McpSession().initialize()
    monkeypatch.setenv("JIN10_MCP_TOKEN", "synthetic-token")
    client = McpSession()
    client._opener = Opener([Reply(200, {"jsonrpc": "2.0", "id": 1,
                                      "result": {"protocolVersion": "2026-07-28"}})])
    with pytest.raises(McpProtocolError, match="unsupported"):
        client.initialize()
