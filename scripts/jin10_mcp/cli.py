"""Manual Jin10 intake commands. No collector starts unless invoked explicitly.

Example (after real tools/list and response-shape confirmation):
  python -m scripts.jin10_mcp.cli start --stream flash --db artifacts/jin10/jin10.sqlite \
    --shape artifacts/jin10/flash_shape.json --budget artifacts/jin10/bounded_budget.json
  python -m scripts.jin10_mcp.cli status --stream flash --db artifacts/jin10/jin10.sqlite
  python -m scripts.jin10_mcp.cli stop --stream flash --db artifacts/jin10/jin10.sqlite
  python -m scripts.jin10_mcp.cli resume --stream flash --db artifacts/jin10/jin10.sqlite \
    --shape artifacts/jin10/flash_shape.json --budget artifacts/jin10/bounded_budget.json

The optional trial command is dormant without an independently verified quota
policy file. It stops at a configured budget, error, external stop or 24 hours.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from dataclasses import fields
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from scripts.jin10_mcp.engine import ingest_probe_pages, parse_page, run_bounded
from scripts.jin10_mcp.schema import Budget, ToolCallError, ToolResponse, ToolSpec
from scripts.jin10_mcp.store import Jin10Store, canonical
from scripts.jin10_mcp_http import McpHttpError, McpProtocolError, McpSession


ROOT = Path(__file__).resolve().parents[2]
PRIVATE_ROOT = ROOT / "artifacts"


class TrialRequestStop(RuntimeError):
    pass


class RequestGate:
    """Shared cap and spacing across handshakes, list calls and tool calls."""

    def __init__(self, *, max_requests: int, minimum_interval_seconds: float,
                 deadline: float, clock=time.monotonic, sleep=time.sleep):
        self.max_requests = max_requests
        self.minimum_interval_seconds = minimum_interval_seconds
        self.deadline = deadline
        self.clock = clock
        self.sleep = sleep
        self.total = 0
        self.last_request_at: float | None = None

    @property
    def remaining(self) -> int:
        return self.max_requests - self.total

    def before_request(self) -> None:
        if self.remaining <= 0:
            raise TrialRequestStop("Trial HTTP request cap reached")
        current = self.clock()
        wait = (max(0.0, self.minimum_interval_seconds - (current - self.last_request_at))
                if self.last_request_at is not None else 0.0)
        if current + wait >= self.deadline:
            raise TrialRequestStop("Trial deadline reached before next HTTP request")
        if wait:
            self.sleep(wait)
            current = self.clock()
        if current >= self.deadline:
            raise TrialRequestStop("Trial deadline reached")
        self.total += 1  # Attempted requests count even on HTTP failure.
        self.last_request_at = current


class GatedMcpSession(McpSession):
    def __init__(self, *, request_gate: RequestGate, **kwargs: Any):
        super().__init__(**kwargs)
        self.request_gate = request_gate

    def _post(self, message: dict, *, after_initialize: bool):
        self.request_gate.before_request()
        return super()._post(message, after_initialize=after_initialize)


def private_path(value: str, *, must_exist: bool = False) -> Path:
    path = Path(value).resolve()
    if not path.is_relative_to(PRIVATE_ROOT):
        raise ValueError("Database, captured replies and runtime receipts must stay under ignored artifacts")
    if must_exist and not path.is_file():
        raise FileNotFoundError(path)
    return path


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Expected JSON object")
    return value


def tool_from_listing(listing: dict, name: str) -> dict:
    result = listing.get("result", listing)
    tools = result.get("tools") if isinstance(result, dict) else None
    if not isinstance(tools, list):
        raise ValueError("Captured tools/list lacks a tools array")
    matches = [tool for tool in tools if isinstance(tool, dict) and tool.get("name") == name]
    if len(matches) != 1:
        raise ValueError("Named tool is missing or duplicated in actual tools/list")
    return matches[0]


def load_spec(shape_path: Path, tool: dict) -> ToolSpec:
    shape = read_json(shape_path)
    if shape.get("schema") != "jin10_confirmed_tool_shape_v1":
        raise ValueError("Confirmed local shape version required")
    if shape.get("tool_name") != tool.get("name"):
        raise ValueError("Shape does not match negotiated tool name")
    input_schema = tool.get("inputSchema")
    if not isinstance(input_schema, dict):
        raise ValueError("Negotiated tool lacks inputSchema")
    schema_sha = hashlib.sha256(canonical(input_schema).encode("utf-8")).hexdigest()
    if shape.get("input_schema_sha256") != schema_sha:
        raise ValueError("Negotiated tool schema changed; recheck parameters")
    witness = shape.get("confirmed_response_sha256")
    if not isinstance(witness, str) or len(witness) != 64 or any(c not in "0123456789abcdef" for c in witness):
        raise ValueError("An actual response-shape witness hash is required")
    witness_name = shape.get("confirmed_response_capture_file")
    witness_index = shape.get("confirmed_response_capture_index")
    if not isinstance(witness_name, str) or Path(witness_name).name != witness_name:
        raise ValueError("Response witness must be a sibling private capture file")
    if not isinstance(witness_index, int) or witness_index < 0:
        raise ValueError("Response witness index must be a nonnegative integer")
    witness_path = shape_path.parent / witness_name
    captures = json.loads(witness_path.read_text(encoding="utf-8"))
    if not isinstance(captures, list) or witness_index >= len(captures):
        raise ValueError("Response witness capture index is absent")
    raw_witness = captures[witness_index].get("response")
    if not isinstance(raw_witness, dict):
        raise ValueError("Response witness is not a JSON-RPC object")
    actual_witness = hashlib.sha256(canonical(raw_witness).encode("utf-8")).hexdigest()
    if actual_witness != witness:
        raise ValueError("Response witness hash does not match captured reply")
    path_names = {"items_path", "has_more_path", "next_cursor_path", "business_status_path"}
    field_names = {item.name for item in fields(ToolSpec)} - {"input_schema", "name"} - path_names
    kwargs = {key: shape[key] for key in field_names if key in shape}
    for key in path_names:
        if key in shape:
            value = shape[key]
            kwargs[key] = None if value is None else tuple(value)
    spec = ToolSpec(name=tool["name"], input_schema=input_schema, **kwargs)
    spec.validate()
    parse_page(raw_witness, spec)
    return spec


def read_budget(path: Path | None) -> Budget:
    if path is None:
        value = Budget(max_requests=2, max_pages=2, max_items=100,
                       max_runtime_seconds=120, max_attempts_per_page=1)
    else:
        contents = read_json(path)
        allowed = {item.name for item in fields(Budget)}
        if set(contents) - allowed:
            raise ValueError("Unknown budget field")
        value = Budget(**contents)
    value.validate()
    return value


def parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            wait_until = parsedate_to_datetime(value)
            if wait_until.tzinfo is None:
                return None
            return max(0.0, (wait_until - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError):
            return None


def _live_tool_listing(session: McpSession, name: str) -> dict:
    cursor = None
    seen_cursors = set()
    for _ in range(5):
        payload = session.list_tools(cursor).payload or {}
        result = payload.get("result", {})
        try:
            return tool_from_listing(payload, name)
        except ValueError:
            cursor = result.get("nextCursor") if isinstance(result, dict) else None
            if not cursor:
                raise ValueError("Named tool absent from complete tools/list") from None
            if cursor in seen_cursors:
                raise ValueError("Repeated tools/list cursor")
            seen_cursors.add(cursor)
    raise ValueError("Named tool absent within five bounded tools/list pages")


def _session_call(session: McpSession, name: str, arguments: dict, _timeout: int) -> ToolResponse:
    try:
        response = session.call_tool(name, arguments)
    except McpHttpError as exc:
        raise ToolCallError("MCP HTTP refusal", http_status=exc.status,
                            retry_after_seconds=parse_retry_after(exc.retry_after)) from None
    except McpProtocolError as exc:
        raise ToolCallError(f"MCP protocol failure: {exc}") from None
    return ToolResponse(payload=response.payload or {}, http_status=response.http_status)


def _live_once(args: argparse.Namespace, mode: str,
               request_gate: RequestGate | None = None) -> dict:
    if not os.environ.get("JIN10_MCP_TOKEN"):
        raise RuntimeError("JIN10_MCP_TOKEN absent from this process")
    budget = read_budget(private_path(args.budget, must_exist=True) if args.budget else None)
    if not args.quota_policy:
        max_allowed = 2 if args.kind == "FLASH" else 1
        if budget.max_requests > max_allowed or budget.max_runtime_seconds > 120:
            raise ValueError("Unverified quota permits only one bounded calendar or at most two flash pages")
    elif not validated_quota_policy(private_path(args.quota_policy, must_exist=True)):
        raise ValueError("Quota policy is not independently verified")
    if budget.max_requests > 13:
        raise ValueError("Single-session request budget exceeds bounded MCP handshake capacity")
    shape_path = private_path(args.shape, must_exist=True)
    session_kwargs = {"max_requests": budget.max_requests + 7,
                      "timeout_seconds": budget.timeout_seconds,
                      "max_response_bytes": budget.max_response_bytes}
    session = (GatedMcpSession(request_gate=request_gate, **session_kwargs)
               if request_gate is not None else McpSession(**session_kwargs))
    session.initialize()
    session.initialized()
    shape = read_json(shape_path)
    tool = _live_tool_listing(session, shape.get("tool_name", ""))
    spec = load_spec(shape_path, tool)
    if spec.kind != args.kind:
        raise ValueError("Requested kind differs from confirmed response shape")
    with Jin10Store(private_path(args.db)) as store:
        receipt = run_bounded(store=store, stream_key=args.stream, spec=spec, budget=budget,
                              call_tool=lambda n, a, t: _session_call(session, n, a, t), mode=mode)
    receipt["http_requests_including_handshake"] = session.requests_used
    receipt["mcp_protocol_version"] = session.protocol_version
    receipt["session_id_present"] = bool(session.session_id)
    return receipt


def validated_quota_policy(path: Path) -> bool:
    policy = read_json(path)
    return (policy.get("schema") == "jin10_verified_quota_policy_v1"
            and policy.get("official_source_url", "").startswith("https://mcp.jin10.com/")
            and policy.get("verified") is True
            and isinstance(policy.get("minimum_interval_seconds"), (int, float))
            and policy["minimum_interval_seconds"] > 0
            and isinstance(policy.get("minimum_poll_interval_seconds"), (int, float))
            and policy["minimum_poll_interval_seconds"] >= policy["minimum_interval_seconds"]
            and isinstance(policy.get("maximum_http_requests_for_trial"), int)
            and 1 <= policy["maximum_http_requests_for_trial"] <= 1000)


def _trial(args: argparse.Namespace) -> dict:
    if not args.quota_policy:
        raise ValueError("Trial mode requires a verified quota policy file")
    policy_path = private_path(args.quota_policy, must_exist=True)
    if not validated_quota_policy(policy_path):
        raise ValueError("Verified quota policy missing or incomplete")
    policy = read_json(policy_path)
    if not (0 < args.hours <= 24):
        raise ValueError("Trial duration must be >0 and <=24 hours")
    deadline = time.monotonic() + args.hours * 3600
    gate = RequestGate(max_requests=policy["maximum_http_requests_for_trial"],
                       minimum_interval_seconds=policy["minimum_interval_seconds"],
                       deadline=deadline)
    batches = 0
    final_status = "TIME_LIMIT"
    while time.monotonic() < deadline:
        next_budget = read_budget(private_path(args.budget, must_exist=True) if args.budget else None)
        if gate.remaining < next_budget.max_requests + 7:
            final_status = "TRIAL_REQUEST_BUDGET_REACHED"
            break
        if private_path(args.db).exists():
            with Jin10Store(private_path(args.db)) as store:
                try:
                    if store.status(args.stream)["stop_requested"]:
                        final_status = "MANUAL_STOP"
                        break
                except KeyError:
                    pass
        try:
            receipt = _live_once(args, "resume", request_gate=gate)
        except TrialRequestStop:
            final_status = "TRIAL_REQUEST_BUDGET_REACHED" if gate.remaining <= 0 else "TIME_LIMIT"
            break
        batches += 1
        if gate.remaining <= 0:
            final_status = "TRIAL_REQUEST_BUDGET_REACHED"
            break
        if time.monotonic() >= deadline:
            final_status = "TIME_LIMIT"
            break
        if receipt["status"] not in {"COMPLETE_FOR_REQUESTED_RANGE", "BUDGET_EXHAUSTED_INCOMPLETE"}:
            final_status = receipt["status"]
            break
        if gate.remaining < next_budget.max_requests + 7:
            final_status = "TRIAL_REQUEST_BUDGET_REACHED"
            break
        wait = min(policy["minimum_poll_interval_seconds"], max(0, deadline - time.monotonic()))
        if wait > 0:
            time.sleep(wait)
    return {"schema": "jin10_trial_receipt_v1", "status": final_status,
            "batches": batches, "http_requests_including_handshakes": gate.total,
            "runtime_cap_hours": args.hours,
            "interpretation": "Manual trial with automatic stop; no background startup configuration"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("start", "resume", "trial", "ingest-probe", "status", "stop"):
        command = sub.add_parser(name)
        command.add_argument("--db", required=True)
        command.add_argument("--stream", required=True)
        if name in {"start", "resume", "trial", "ingest-probe"}:
            command.add_argument("--shape", required=True)
            command.add_argument("--kind", choices=("FLASH", "CALENDAR"), required=True)
        if name in {"start", "resume", "trial"}:
            command.add_argument("--budget")
            command.add_argument("--quota-policy")
        if name == "trial":
            command.add_argument("--hours", type=float, required=True)
        if name == "ingest-probe":
            command.add_argument("--tool-list-file", required=True)
            command.add_argument("--capture-file", required=True)
        if name in {"start", "resume", "trial", "ingest-probe"}:
            command.add_argument("--receipt")
    args = parser.parse_args()
    db_path = private_path(args.db)
    if args.command == "status":
        if not db_path.is_file():
            result = {"stream_key": args.stream, "coverage_status": "NOT_STARTED",
                      "acquisition_error": None}
        else:
            with Jin10Store(db_path) as store:
                try:
                    result = store.status(args.stream)
                except KeyError:
                    result = {"stream_key": args.stream, "coverage_status": "NOT_STARTED",
                              "acquisition_error": None}
    elif args.command == "stop":
        if not db_path.is_file():
            raise FileNotFoundError("No local intake database to stop")
        with Jin10Store(db_path) as store:
            store.request_stop(args.stream)
            result = store.status(args.stream)
    elif args.command == "ingest-probe":
        shape_path = private_path(args.shape, must_exist=True)
        listing = read_json(private_path(args.tool_list_file, must_exist=True))
        shape = read_json(shape_path)
        spec = load_spec(shape_path, tool_from_listing(listing, shape.get("tool_name", "")))
        if spec.kind != args.kind:
            raise ValueError("Requested kind differs from confirmed response shape")
        captures = json.loads(private_path(args.capture_file, must_exist=True).read_text(encoding="utf-8"))
        if not isinstance(captures, list):
            raise ValueError("Captured probe file must be an array of page receipts")
        with Jin10Store(db_path) as store:
            result = ingest_probe_pages(store=store, stream_key=args.stream, spec=spec,
                                        captured_pages=captures)
    elif args.command == "trial":
        result = _trial(args)
    else:
        result = _live_once(args, args.command)
    if getattr(args, "receipt", None):
        output = private_path(args.receipt)
        if output.exists():
            raise FileExistsError("Preserve earlier runtime receipt")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8", newline="\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
