"""Small bounded Streamable HTTP MCP client for the Jin10 development intake.

The bearer token is read only from JIN10_MCP_TOKEN at call time. This module
does not log the token, authorization header, session ID or news content.
Raw tool replies belong in ignored local artifacts; public receipts must be
prepared separately after reviewing the returned schemas and service terms.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


ENDPOINT = "https://mcp.jin10.com/mcp"
PRIVATE_ROOT = Path(__file__).resolve().parents[1] / "artifacts"
OFFERED_VERSION = "2025-11-25"
SUPPORTED_HANDSHAKE_VERSIONS = {"2025-03-26", "2025-06-18", "2025-11-25"}


class McpHttpError(Exception):
    def __init__(self, status: int, retry_after: str | None = None):
        self.status = status
        self.retry_after = retry_after
        super().__init__(f"MCP HTTP status {status}")


class McpProtocolError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class McpResponse:
    http_status: int
    payload: dict | None
    elapsed_ms: int
    content_type: str


class McpSession:
    def __init__(self, endpoint: str = ENDPOINT, max_requests: int = 8,
                 timeout_seconds: int = 25, max_response_bytes: int = 8 * 1024 * 1024):
        if endpoint != ENDPOINT:
            raise ValueError("This intake is restricted to the official Jin10 MCP endpoint")
        if max_requests < 1 or max_requests > 20 or timeout_seconds < 1 or max_response_bytes < 1:
            raise ValueError("Invalid bounded session limits")
        self.endpoint = endpoint
        self.max_requests = max_requests
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.requests_used = 0
        self.protocol_version: str | None = None
        self.session_id: str | None = None
        self.initialized_sent = False
        self._next_id = 1
        self._opener = urllib.request.build_opener(NoRedirect())

    @property
    def session_id_sha256(self) -> str | None:
        return hashlib.sha256(self.session_id.encode()).hexdigest() if self.session_id else None

    @staticmethod
    def _bearer() -> str:
        value = os.environ.get("JIN10_MCP_TOKEN")
        if not value:
            raise RuntimeError("JIN10_MCP_TOKEN is absent from this process")
        if "\n" in value or "\r" in value:
            raise ValueError("Invalid credential format")
        return value

    def _decode(self, response, wanted_id: int | None) -> dict | None:
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
        if response.status == 202:
            return None
        if content_type == "application/json":
            body = response.read(self.max_response_bytes + 1)
            if len(body) > self.max_response_bytes:
                raise McpProtocolError("Response exceeds byte limit")
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise McpProtocolError("Non-object JSON-RPC reply")
            return payload
        if content_type == "text/event-stream":
            read_bytes = 0
            data_lines: list[str] = []
            started = time.monotonic()
            while time.monotonic() - started < self.timeout_seconds:
                line = response.readline(self.max_response_bytes + 1)
                read_bytes += len(line)
                if read_bytes > self.max_response_bytes:
                    raise McpProtocolError("SSE response exceeds byte limit")
                if not line:
                    break
                decoded = line.decode("utf-8").rstrip("\r\n")
                if decoded.startswith("data:"):
                    data_lines.append(decoded[5:].lstrip(" "))
                elif decoded == "" and data_lines:
                    value = "\n".join(data_lines)
                    data_lines.clear()
                    if not value:
                        continue
                    payload = json.loads(value)
                    if isinstance(payload, dict) and (wanted_id is None or payload.get("id") == wanted_id):
                        return payload
            raise McpProtocolError("SSE ended without matching JSON-RPC response")
        raise McpProtocolError("Unsupported response content type")

    def _post(self, message: dict, *, after_initialize: bool) -> McpResponse:
        if self.requests_used >= self.max_requests:
            raise RuntimeError("MCP request budget exhausted")
        token = self._bearer()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "User-Agent": "quant-jin10-development-intake/1.0",
        }
        if after_initialize:
            if not self.protocol_version:
                raise McpProtocolError("Initialize first")
            headers["MCP-Protocol-Version"] = self.protocol_version
            if self.session_id:
                headers["MCP-Session-Id"] = self.session_id
        request = urllib.request.Request(
            self.endpoint, data=json.dumps(message, separators=(",", ":")).encode("utf-8"),
            headers=headers, method="POST")
        self.requests_used += 1
        started = time.monotonic()
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                payload = self._decode(response, message.get("id"))
                if not after_initialize:
                    self.session_id = response.headers.get("MCP-Session-Id")
                return McpResponse(response.status, payload,
                                   round((time.monotonic() - started) * 1000),
                                   response.headers.get("Content-Type", ""))
        except urllib.error.HTTPError as error:
            raise McpHttpError(error.code, error.headers.get("Retry-After")) from None
        except urllib.error.URLError as error:
            raise McpProtocolError(f"Transport failure: {type(error.reason).__name__}") from None

    def _request(self, method: str, params: dict | None = None,
                 *, after_initialize: bool = True) -> McpResponse:
        request_id = self._next_id
        self._next_id += 1
        message = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        reply = self._post(message, after_initialize=after_initialize)
        payload = reply.payload
        if not isinstance(payload, dict) or payload.get("id") != request_id:
            raise McpProtocolError("JSON-RPC response ID mismatch")
        if "error" in payload:
            code = payload["error"].get("code") if isinstance(payload["error"], dict) else None
            raise McpProtocolError(f"JSON-RPC error code {code}")
        if not isinstance(payload.get("result"), dict):
            raise McpProtocolError("Missing JSON-RPC result")
        return reply

    def initialize(self) -> McpResponse:
        reply = self._request("initialize", {
            "protocolVersion": OFFERED_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "quant-jin10-development-intake", "version": "1.0.0"},
        }, after_initialize=False)
        version = reply.payload["result"].get("protocolVersion")
        if version not in SUPPORTED_HANDSHAKE_VERSIONS:
            raise McpProtocolError("Server negotiated unsupported handshake version")
        self.protocol_version = version
        return reply

    def initialized(self) -> McpResponse:
        reply = self._post({"jsonrpc": "2.0", "method": "notifications/initialized"},
                           after_initialize=True)
        if reply.http_status != 202:
            raise McpProtocolError("Initialized notification was not accepted")
        self.initialized_sent = True
        return reply

    def list_tools(self, cursor: str | None = None) -> McpResponse:
        if not self.initialized_sent:
            raise McpProtocolError("Initialized notification required")
        params = {"cursor": cursor} if cursor is not None else None
        return self._request("tools/list", params)

    def call_tool(self, name: str, arguments: dict) -> McpResponse:
        if not self.initialized_sent:
            raise McpProtocolError("Initialized notification required")
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be an object")
        return self._request("tools/call", {"name": name, "arguments": arguments})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-output", type=Path, required=True,
                        help="Ignored local file for the unredacted tools/list schema")
    args = parser.parse_args()
    output = args.private_output.resolve()
    if not output.is_relative_to(PRIVATE_ROOT):
        raise ValueError("The unredacted tools/list schema must stay under ignored artifacts")
    if output.exists():
        raise FileExistsError("Preserve prior probe output")
    client = McpSession()
    initialized = client.initialize()
    notification = client.initialized()
    tools = client.list_tools()
    result = tools.payload["result"]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8", newline="\n")
    print(json.dumps({
        "initialize_http_status": initialized.http_status,
        "initialized_http_status": notification.http_status,
        "tools_list_http_status": tools.http_status,
        "negotiated_protocol_version": client.protocol_version,
        "session_id_present": bool(client.session_id),
        "tools_count": len(result.get("tools", [])),
        "tool_names": [tool.get("name") for tool in result.get("tools", [])],
        "requests_used": client.requests_used,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
