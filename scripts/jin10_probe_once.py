"""One bounded, private Jin10 MCP flash/calendar probe after live schema negotiation.

Only a redacted structural receipt is written to results. Full replies, which
may contain licensed text, remain under ignored artifacts. There are no retries
or guesses about required tool arguments.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.jin10_mcp.engine import unwrap_mcp_payload
from scripts.jin10_mcp.store import canonical
from scripts.jin10_mcp_http import McpHttpError, McpProtocolError, McpSession


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOT = ROOT / "artifacts"
PUBLIC_ROOT = ROOT / "results/news_jin10_20261115"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def tool_in_listing(listing: dict, name: str) -> dict:
    tools = listing.get("tools")
    if not isinstance(tools, list):
        raise ValueError("Actual tools/list has no tools array")
    matches = [tool for tool in tools if isinstance(tool, dict) and tool.get("name") == name]
    if len(matches) != 1:
        raise ValueError(f"Expected one actual {name} tool")
    schema = matches[0].get("inputSchema")
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ValueError(f"Actual {name} inputSchema is not an object")
    if schema.get("required"):
        raise ValueError(f"Actual {name} requires parameters; inspect before probing")
    return matches[0]


def inspect_reply(payload: dict, kind: str) -> dict:
    decoded, _ = unwrap_mcp_payload(payload)
    status = decoded.get("status")
    data = decoded.get("data")
    if status != 200 or data is None:
        return {"business_status": status, "items": 0, "item_fields": [],
                "has_more": None, "next_cursor_present": False,
                "business_error": "NON_SUCCESS_OR_MISSING_DATA"}
    if kind == "flash":
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise ValueError("Actual flash data.items is not an array")
        has_more = data.get("has_more")
        cursor = data.get("next_cursor")
        if not isinstance(has_more, bool):
            raise ValueError("Actual flash has_more is not Boolean")
        if has_more and not isinstance(cursor, str):
            raise ValueError("Actual flash next_cursor is missing")
    else:
        items = data
        if not isinstance(items, list):
            raise ValueError("Actual calendar data is not an array")
        has_more, cursor = None, None
    fields = sorted({field for item in items if isinstance(item, dict) for field in item})
    return {"business_status": status, "items": len(items), "item_fields": fields,
            "has_more": has_more, "next_cursor_present": bool(cursor),
            "business_error": None}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-dir", type=Path, required=True)
    parser.add_argument("--public-receipt", type=Path, required=True)
    args = parser.parse_args()
    private_dir = args.private_dir.resolve()
    public_receipt = args.public_receipt.resolve()
    if not private_dir.is_relative_to(PRIVATE_ROOT):
        raise ValueError("Full tool replies must stay under ignored artifacts")
    if not public_receipt.is_relative_to(PUBLIC_ROOT):
        raise ValueError("Redacted receipt must stay in Jin10 results")
    if private_dir.exists() or public_receipt.exists():
        raise FileExistsError("Preserve previous probe and receipt")
    private_dir.mkdir(parents=True)
    client = McpSession(max_requests=6, timeout_seconds=25,
                        max_response_bytes=8 * 1024 * 1024)
    attempts: list[dict] = []
    captures: dict[str, list[dict]] = {"flash": [], "calendar": []}
    session_summary: dict = {"initialize_http_status": None,
                             "initialized_http_status": None,
                             "tools_list_http_status": None,
                             "negotiated_protocol_version": None,
                             "session_id_present": None}
    fatal_error = None
    listing: dict = {}
    try:
        initial = client.initialize()
        session_summary["initialize_http_status"] = initial.http_status
        session_summary["negotiated_protocol_version"] = client.protocol_version
        session_summary["session_id_present"] = bool(client.session_id)
        session_summary["initialized_http_status"] = client.initialized().http_status
        listed = client.list_tools()
        session_summary["tools_list_http_status"] = listed.http_status
        listing = listed.payload["result"]
        flash_tool = tool_in_listing(listing, "list_flash")
        calendar_tool = tool_in_listing(listing, "list_calendar")
        for kind, tool, arguments in (("flash", flash_tool, {}),
                                      ("calendar", calendar_tool, {})):
            name = tool["name"]
            started = utc_now()
            reply = None
            try:
                reply = client.call_tool(name, arguments)
                received = utc_now()
                capture = {"arguments": arguments, "response": reply.payload,
                           "http_status": reply.http_status,
                           "received_at_utc": received,
                           "elapsed_ms": reply.elapsed_ms}
                captures[kind].append(capture)
                summary = inspect_reply(reply.payload, kind)
                capture["business_status"] = summary["business_status"]
                attempts.append({"tool_name": name, "arguments": arguments,
                                 "attempted_at_utc": started, "received_at_utc": received,
                                 "http_status": reply.http_status,
                                 "elapsed_ms": reply.elapsed_ms,
                                 "response_json_utf8_bytes": len(canonical(reply.payload).encode("utf-8")),
                                 **summary, "error": None})
                if summary["business_error"]:
                    break
            except McpHttpError as exc:
                attempts.append({"tool_name": name, "arguments": arguments,
                                 "attempted_at_utc": started, "http_status": exc.status,
                                 "error": "HTTP_REFUSAL",
                                 "retry_after_present": bool(exc.retry_after)})
                break
            except (McpProtocolError, ValueError, TypeError) as exc:
                attempts.append({"tool_name": name, "arguments": arguments,
                                 "attempted_at_utc": started,
                                 "http_status": reply.http_status if reply else None,
                                 "elapsed_ms": reply.elapsed_ms if reply else None,
                                 "raw_response_captured": bool(reply),
                                 "error": type(exc).__name__})
                break
        else:
            flash_summary = attempts[0]
            if flash_summary.get("has_more") and flash_summary.get("next_cursor_present"):
                properties = flash_tool["inputSchema"].get("properties", {})
                if "cursor" not in properties or properties["cursor"].get("type") != "string":
                    fatal_error = "LIVE_CURSOR_HAS_NO_MATCHING_NEGOTIATED_INPUT"
                else:
                    flash_data = unwrap_mcp_payload(captures["flash"][0]["response"])[0]["data"]
                    arguments = {"cursor": flash_data["next_cursor"]}
                    started = utc_now()
                    reply = None
                    try:
                        reply = client.call_tool("list_flash", arguments)
                        received = utc_now()
                        captures["flash"].append({"arguments": arguments,
                                                   "response": reply.payload,
                                                   "http_status": reply.http_status,
                                                   "received_at_utc": received,
                                                   "elapsed_ms": reply.elapsed_ms})
                        summary = inspect_reply(reply.payload, "flash")
                        captures["flash"][-1]["business_status"] = summary["business_status"]
                        attempts.append({"tool_name": "list_flash", "arguments": {"cursor": "REDACTED"},
                                         "attempted_at_utc": started, "received_at_utc": received,
                                         "http_status": reply.http_status,
                                         "elapsed_ms": reply.elapsed_ms,
                                         "response_json_utf8_bytes": len(canonical(reply.payload).encode("utf-8")),
                                         **summary, "error": None})
                    except McpHttpError as exc:
                        attempts.append({"tool_name": "list_flash", "arguments": {"cursor": "REDACTED"},
                                         "attempted_at_utc": started, "http_status": exc.status,
                                         "error": "HTTP_REFUSAL",
                                         "retry_after_present": bool(exc.retry_after)})
                    except (McpProtocolError, ValueError, TypeError) as exc:
                        attempts.append({"tool_name": "list_flash", "arguments": {"cursor": "REDACTED"},
                                         "attempted_at_utc": started,
                                         "http_status": reply.http_status if reply else None,
                                         "elapsed_ms": reply.elapsed_ms if reply else None,
                                         "raw_response_captured": bool(reply),
                                         "error": type(exc).__name__})
    except (McpHttpError, McpProtocolError, ValueError, TypeError, KeyError) as exc:
        fatal_error = f"{type(exc).__name__}: {exc}"
    finally:
        (private_dir / "tools_list.json").write_text(json.dumps(listing, ensure_ascii=False, indent=2) + "\n",
                                                      encoding="utf-8", newline="\n")
        for kind in ("flash", "calendar"):
            (private_dir / f"{kind}_captures.json").write_text(
                json.dumps(captures[kind], ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8", newline="\n")
        receipt = {"schema": "jin10_probe_redacted_v1", "source": "jin10_mcp",
                   "endpoint": "https://mcp.jin10.com/mcp",
                   "executed_at_utc": utc_now(), "session": session_summary,
                   "tools_list_names": sorted(tool.get("name") for tool in listing.get("tools", [])
                                              if isinstance(tool, dict)),
                   "input_schema_sha256": {
                       tool["name"]: hashlib.sha256(canonical(tool["inputSchema"]).encode()).hexdigest()
                       for tool in listing.get("tools", []) if isinstance(tool, dict)
                       and tool.get("name") in {"list_flash", "list_calendar"}},
                   "attempts": attempts, "fatal_error": fatal_error,
                   "total_http_requests_including_handshake": client.requests_used,
                   "private_capture_relative": private_dir.relative_to(ROOT).as_posix(),
                   "interpretation": "A bounded current retrieval, not proof of ongoing availability, prerelease consensus, or redistribution rights"}
        public_receipt.parent.mkdir(parents=True, exist_ok=True)
        public_receipt.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
                                  encoding="utf-8", newline="\n")
        print(json.dumps({"receipt": public_receipt.relative_to(ROOT).as_posix(),
                          "fatal_error": fatal_error,
                          "attempts": [{"tool_name": row["tool_name"],
                                        "http_status": row["http_status"],
                                        "items": row.get("items"), "has_more": row.get("has_more"),
                                        "error": row["error"]} for row in attempts],
                          "total_http_requests_including_handshake": client.requests_used},
                         ensure_ascii=False))


if __name__ == "__main__":
    main()
