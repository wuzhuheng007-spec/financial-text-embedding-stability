"""Bounded MCP page intake with append-only versions and honest coverage state."""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlsplit

from .schema import Budget, ToolCallError, ToolResponse, ToolSpec, at_path
from .store import Jin10Store, canonical


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def source_time(raw: Any, supplied_timezone: Any) -> tuple[str | None, str, str]:
    text = "" if raw is None else str(raw).strip()
    timezone_text = str(supplied_timezone).strip() if supplied_timezone else "UNKNOWN"
    if not text:
        return None, timezone_text, "UNKNOWN"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return None, timezone_text, "DATE_ONLY"
    try:
        timestamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None, timezone_text, "UNPARSED_TEXT"
    time_part = text.split("T", 1)[1] if "T" in text else text.split(" ", 1)[1] if " " in text else ""
    time_match = re.match(r"\d{2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?", time_part)
    has_seconds = bool(time_match and time_match.group(0).count(":") == 2)
    if timestamp.tzinfo is None:
        precision = "SECOND_NO_TIMEZONE" if has_seconds else "MINUTE_NO_TIMEZONE"
        return None, timezone_text, precision
    if timezone_text == "UNKNOWN":
        offset = timestamp.strftime("%z")
        timezone_text = "UTC" if offset == "+0000" else f"UTC{offset[:3]}:{offset[3:]}"
    precision = "SECOND_WITH_TIMEZONE" if has_seconds else "MINUTE_WITH_TIMEZONE"
    return timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"), timezone_text, precision


def checked_source_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    candidate = value.strip()
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return None
    if (parsed.scheme != "https" or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path in {"", "/"}):
        return None
    try:
        _ = parsed.port
    except ValueError:
        return None
    return candidate


ISSUER_ALIASES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("NVIDIA", re.compile(r"\bNVIDIA\b|\bNVDA\b|英伟达", re.I)),
    ("AMD", re.compile(r"\bAMD\b|Advanced Micro Devices|超威半导体", re.I)),
    ("INTEL", re.compile(r"\bIntel\b|英特尔", re.I)),
    ("COREWEAVE", re.compile(r"\bCoreWeave\b", re.I)),
    ("SAMSUNG_ELECTRONICS", re.compile(r"三星电子|Samsung Electronics", re.I)),
    ("SK_HYNIX", re.compile(r"SK\s*hynix|SK\s*海力士", re.I)),
)

CATEGORY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("MONETARY_POLICY", re.compile(r"美联储|FOMC|货币政策|rate decision", re.I)),
    ("TECH_REGULATION", re.compile(r"芯片禁令|出口管制|科技监管|export control", re.I)),
    ("GEO_ENERGY", re.compile(r"原油|石油|中东|能源供应|oil supply", re.I)),
    ("FINANCING_CREDIT", re.compile(r"融资|债券|违约|信用利差|credit facility", re.I)),
    ("CAPITAL_EXPENDITURE", re.compile(r"资本支出|资本开支|\bcapex\b", re.I)),
    ("SUPPLY_DEMAND_PRICING", re.compile(r"供应|需求|涨价|价格调整|inventory|supply chain", re.I)),
    ("MERGER_ACQUISITION", re.compile(r"并购|收购|合并|acquisition", re.I)),
    ("MAJOR_GOVERNANCE", re.compile(r"首席执行官|董事会|辞职|CEO transition", re.I)),
    ("PRODUCT_SAFETY", re.compile(r"产品召回|安全漏洞|security vulnerability", re.I)),
    ("MACRO_DATA", re.compile(r"消费者价格指数|非农|通胀率|\bCPI\b|\bPCE\b|payroll", re.I)),
)


def candidate_labels(text: str, kind: str) -> tuple[list[str], str]:
    issuers = [issuer for issuer, pattern in ISSUER_ALIASES if pattern.search(text)]
    if kind == "CALENDAR":
        return issuers, "CANDIDATE_MACRO_DATA"
    for category, pattern in CATEGORY_PATTERNS:
        if pattern.search(text):
            return issuers, "CANDIDATE_" + category
    return issuers, "UNCLASSIFIED"


def _field(item: dict, name: str | None) -> Any:
    return item.get(name) if name else None


def normalize_item(item: dict, spec: ToolSpec, received_at_utc: str, index: int,
                   payload_locator: dict | None = None) -> dict:
    if not isinstance(item, dict):
        raise ValueError("Provider item is not an object")
    source_id_raw = _field(item, spec.source_id_field)
    source_id = str(source_id_raw).strip() if source_id_raw not in (None, "") else None
    source_url = checked_source_url(_field(item, spec.url_field))
    published_raw = _field(item, spec.published_field)
    title_raw = _field(item, spec.title_field)
    content_raw = _field(item, spec.content_field)
    title = "" if title_raw is None else str(title_raw)
    content = "" if content_raw is None else str(content_raw)
    published_text = "" if published_raw is None else str(published_raw)
    published_utc, source_timezone, precision = source_time(
        published_raw, _field(item, spec.timezone_field))
    is_calendar = spec.kind == "CALENDAR"
    publication_raw = None if is_calendar else published_text or None
    publication_utc = None if is_calendar else published_utc
    publication_timezone = "UNKNOWN" if is_calendar else source_timezone
    publication_precision = "UNKNOWN" if is_calendar else precision
    if source_id and spec.source_id_stability_confirmed:
        key_kind, source_key, potential_collision = "SOURCE_ID", source_id, False
    elif source_url and spec.url_semantics_confirmed:
        key_kind, source_key, potential_collision = "CHECKED_URL", source_url, False
    else:
        # The prefix is a documented fallback, not a verified provider identity.
        # Same-time/same-title collisions and title edits remain possible.
        fallback = title.strip().casefold() or content.strip().casefold()[:80]
        source_key = canonical([published_text, fallback])
        key_kind, potential_collision = "TIME_TITLE_OR_CONTENT_PREFIX", True
    logical_key = hashlib.sha256(canonical([spec.name, key_kind, source_key]).encode("utf-8")).hexdigest()
    text = f"{title}\n{content}"
    issuers, category = candidate_labels(text, spec.kind)
    payload_json = canonical(item)
    content_text = content or title or payload_json
    normalized = {
        "source": "jin10_mcp", "tool_name": spec.name,
        "source_id": source_id, "source_url": source_url,
        "source_id_status": ("CONFIRMED_STABLE_ID" if source_id and spec.source_id_stability_confirmed
                             else "ID_CANDIDATE_UNVERIFIED_STABILITY" if source_id else "MISSING"),
        "source_url_status": ("CONFIRMED_ITEM_URL" if source_url and spec.url_semantics_confirmed
                              else "URL_CANDIDATE_UNVERIFIED_ORIGIN" if source_url else "MISSING"),
        "source_published_raw": publication_raw,
        "source_timezone": publication_timezone,
        "published_at_utc": publication_utc,
        "time_precision": publication_precision,
        "version_first_seen_at_utc": received_at_utc,
        "title": title or None, "content": content or None,
        "event_category_candidate": category,
        "issuer_candidates": issuers,
        "association_status": "CANDIDATE_ONLY",
        "source_status": "SOURCE_REPORT_NOT_INDEPENDENTLY_VERIFIED",
        "verified_fact_status": "NOT_VERIFIED",
        "model_hypothesis_status": "NOT_RUN",
        "market_observation_status": "NOT_RUN",
    }
    if is_calendar:
        normalized["scheduled_for_raw"] = published_text or None
        normalized["scheduled_at_utc"] = published_utc
        normalized["scheduled_timezone"] = source_timezone
        normalized["scheduled_time_precision"] = precision
        normalized["scheduled_time_status"] = "PROVIDER_THEORETICAL_PUBLICATION_TIME"
        normalized["calendar_values"] = {
            key: (None if item.get(key) in (None, "") else item.get(key))
            for key in ("actual", "consensus", "previous", "revised")
        }
        normalized["consensus_snapshot_received_at_utc"] = received_at_utc
        normalized["pre_release_consensus_status"] = "NOT_ESTABLISHED_FROM_CURRENT_FETCH"
    locator = {
        "path_to_payload": [] if payload_locator is None else payload_locator["path_to_payload"],
        "decode_json": False if payload_locator is None else payload_locator["decode_json"],
        "wrap_list_as_data": False if payload_locator is None else payload_locator["wrap_list_as_data"],
        "item_path": [*spec.items_path, index],
    }
    return {
        "logical_key": logical_key, "key_kind": key_kind, "source_key": source_key,
        "potential_collision": potential_collision,
        "source_id": source_id, "source_url": source_url,
        "published_raw": publication_raw, "published_at_utc": publication_utc,
        "source_timezone": publication_timezone, "time_precision": publication_precision,
        "content_sha256": hashlib.sha256(content_text.encode("utf-8")).hexdigest(),
        "payload_sha256": hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
        "event_category_candidate": category, "issuer_candidates": issuers,
        "raw_locator": canonical(locator), "normalized": normalized,
    }


def unwrap_mcp_payload(payload: dict) -> tuple[dict, dict]:
    if "error" in payload:
        raise ValueError("MCP JSON-RPC returned an error")
    current = payload.get("result", payload)
    root_path = ["result"] if "result" in payload else []
    if not isinstance(current, dict):
        raise ValueError("MCP result is not an object")
    if current.get("isError"):
        raise ValueError("MCP tool returned isError")
    if isinstance(current.get("structuredContent"), dict):
        return current["structuredContent"], {
            "path_to_payload": [*root_path, "structuredContent"],
            "decode_json": False, "wrap_list_as_data": False,
        }
    if isinstance(current.get("content"), list):
        texts = [(i, entry.get("text")) for i, entry in enumerate(current["content"])
                 if isinstance(entry, dict) and entry.get("type") == "text"]
        if len(texts) == 1:
            index, body = texts[0]
            decoded = json.loads(body)
            locator = {"path_to_payload": [*root_path, "content", index, "text"],
                       "decode_json": True, "wrap_list_as_data": isinstance(decoded, list)}
            if isinstance(decoded, dict):
                return decoded, locator
            if isinstance(decoded, list):
                return {"data": decoded}, locator
    return current, {"path_to_payload": root_path,
                     "decode_json": False, "wrap_list_as_data": False}


def resolve_raw_locator(raw_response: dict, locator_json: str) -> Any:
    """Re-read an item from the exact stored JSON-RPC envelope and locator."""
    locator = json.loads(locator_json)
    value = at_path(raw_response, tuple(locator["path_to_payload"]))
    if locator["decode_json"]:
        value = json.loads(value)
    if locator["wrap_list_as_data"]:
        value = {"data": value}
    return at_path(value, tuple(locator["item_path"]))


def parse_page(response: dict, spec: ToolSpec) -> tuple[list[dict], bool, Any, str | int | None, dict]:
    data, payload_locator = unwrap_mcp_payload(response)
    items = at_path(data, spec.items_path)
    if not isinstance(items, list):
        raise ValueError("Confirmed item path is not an array")
    if spec.has_more_path is None:
        has_more, next_cursor = False, None
    else:
        has_more = at_path(data, spec.has_more_path)
        next_cursor = at_path(data, spec.next_cursor_path or ())
        if not isinstance(has_more, bool):
            raise ValueError("has_more is not a boolean")
        if has_more and next_cursor in (None, ""):
            raise ValueError("has_more without next_cursor")
    business_status = at_path(data, spec.business_status_path) if spec.business_status_path else None
    if spec.business_status_path and business_status not in spec.business_success_values:
        raise ValueError("Provider business status is not a confirmed success value")
    return items, has_more, next_cursor, business_status, payload_locator


def _response(value: Any) -> ToolResponse:
    if isinstance(value, ToolResponse):
        return value
    if isinstance(value, dict):
        return ToolResponse(payload=value)
    raise TypeError("Tool callback must return ToolResponse or a decoded JSON object")


def _duration_ms(start: float) -> int:
    return max(0, round((time.monotonic() - start) * 1000))


def run_bounded(*, store: Jin10Store, stream_key: str, spec: ToolSpec, budget: Budget,
                call_tool: Callable[[str, dict, int], ToolResponse | dict], mode: str = "start",
                sleep: Callable[[float], None] = time.sleep) -> dict:
    spec.validate()
    budget.validate()
    if mode not in {"start", "resume"}:
        raise ValueError("mode must be start or resume")
    store.ensure_stream(stream_key, spec.name)
    initial = store.stream(stream_key)
    if initial["tool_name"] != spec.name:
        raise ValueError("Stream key already belongs to a different tool")
    if mode == "resume" and initial["replay_cursor_json"] is not None:
        cursor = json.loads(initial["replay_cursor_json"])
    else:
        cursor = None
    if mode == "start":
        store.clear_stop(stream_key)
    run_id = store.new_run(stream_key, now_utc())
    started = time.monotonic()
    pages = requests = items_seen = new_versions = 0
    status, error = "ERROR", None
    while True:
        if store.stream(stream_key)["stop_requested"]:
            status = ("STOPPED_WITH_CHECKPOINT" if store.stream(stream_key)["replay_cursor_json"] is not None
                      else "STOPPED")
            break
        if time.monotonic() - started >= budget.max_runtime_seconds:
            status = "TIME_BUDGET_EXHAUSTED"
            break
        if requests >= budget.max_requests or pages >= budget.max_pages:
            status = "BUDGET_EXHAUSTED_INCOMPLETE"
            break
        try:
            arguments = spec.args_for_cursor(cursor)
        except ValueError as exc:
            status, error = "PAGINATION_UNSUPPORTED", str(exc)
            break
        response: ToolResponse | None = None
        for retry in range(budget.max_attempts_per_page):
            if requests >= budget.max_requests:
                status = "BUDGET_EXHAUSTED_INCOMPLETE"
                break
            attempted_at = now_utc()
            t0 = time.monotonic()
            requests += 1
            try:
                response = _response(call_tool(spec.name, arguments, budget.timeout_seconds))
                if response.http_status is not None and response.http_status >= 400:
                    raise ToolCallError("MCP HTTP refusal", http_status=response.http_status)
                store.record_attempt(run_id=run_id, tool_name=spec.name, arguments=arguments,
                                     attempted_at_utc=attempted_at, elapsed_ms=_duration_ms(t0),
                                     http_status=response.http_status, business_status=response.business_status,
                                     outcome="RESPONSE_RECEIVED", error=None)
                break
            except (ToolCallError, TimeoutError, OSError) as exc:
                response = None
                http_status = getattr(exc, "http_status", None)
                business_status = getattr(exc, "business_status", None)
                store.record_attempt(run_id=run_id, tool_name=spec.name, arguments=arguments,
                                     attempted_at_utc=attempted_at, elapsed_ms=_duration_ms(t0),
                                     http_status=http_status, business_status=business_status,
                                     outcome="FAILED", error=f"{type(exc).__name__}: {exc}")
                if http_status in {401, 403}:
                    status, error = "AUTH_DENIED_STOP", f"HTTP {http_status}"
                    break
                if http_status == 429:
                    wait = getattr(exc, "retry_after_seconds", None)
                    if wait is None:
                        wait = min(2 ** retry, 8)
                    if retry + 1 >= budget.max_attempts_per_page or requests >= budget.max_requests or time.monotonic() - started + wait >= budget.max_runtime_seconds:
                        status, error = "RATE_LIMIT_WAIT_REQUIRED", f"HTTP 429; wait at least {wait} seconds"
                        break
                else:
                    wait = min(2 ** retry, 8)
                    if retry + 1 >= budget.max_attempts_per_page or requests >= budget.max_requests or time.monotonic() - started + wait >= budget.max_runtime_seconds:
                        status, error = "ACQUISITION_ERROR", f"{type(exc).__name__}: {exc}"
                        break
                sleep(wait)
            except Exception as exc:
                # A client bug must remain an acquisition error, never an empty page.
                response = None
                store.record_attempt(run_id=run_id, tool_name=spec.name, arguments=arguments,
                                     attempted_at_utc=attempted_at, elapsed_ms=_duration_ms(t0),
                                     http_status=None, business_status=None, outcome="CLIENT_ERROR",
                                     error=f"{type(exc).__name__}: unexpected client failure")
                status, error = "ACQUISITION_ERROR", f"{type(exc).__name__}: unexpected client failure"
                break
        if response is None:
            break
        received_at = now_utc()
        response_bytes = len(canonical(response.payload).encode("utf-8"))
        if response_bytes > budget.max_response_bytes:
            status, error = "RESPONSE_SIZE_EXCEEDED", "Response exceeds configured byte bound"
            break
        try:
            page_items, has_more, next_cursor, business_status, payload_locator = parse_page(response.payload, spec)
            if len(page_items) + items_seen > budget.max_items:
                raise ValueError("Complete page exceeds remaining item budget")
            if has_more and not spec.cursor_argument:
                raise ValueError("Provider has more pages but no negotiated cursor input field")
            normalized_items = [normalize_item(item, spec, received_at, index, payload_locator)
                                for index, item in enumerate(page_items)]
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            store.record_incomplete_response(run_id=run_id, tool_name=spec.name,
                                             arguments=arguments, response=response.payload,
                                             received_at_utc=received_at,
                                             http_status=response.http_status,
                                             business_status=response.business_status,
                                             cursor_in=cursor)
            status, error = "INCOMPLETE_PAGE", f"{type(exc).__name__}: {exc}"
            break
        try:
            receipt = store.record_page(run_id=run_id, stream_key=stream_key, tool_name=spec.name,
                                        arguments=arguments, response=response.payload,
                                        received_at_utc=received_at, http_status=response.http_status,
                                        business_status=(business_status if business_status is not None
                                                         else response.business_status),
                                        cursor_in=cursor, cursor_out=next_cursor,
                                        has_more=has_more, normalized_items=normalized_items)
        except Exception as exc:
            status, error = "STORAGE_ERROR", f"{type(exc).__name__}: page was not committed"
            break
        pages += 1
        items_seen += len(normalized_items)
        new_versions += receipt["new_versions"]
        if not has_more:
            status = "COMPLETE_FOR_REQUESTED_RANGE"
            break
        cursor = next_cursor
    run = store.finish_run(run_id, stream_key, status, now_utc(), error)
    return {
        "schema": "jin10_bounded_run_receipt_v1", "stream_key": stream_key,
        "tool_name": spec.name, "run_id": run_id, "status": status,
        "pages_complete": pages, "items_seen": items_seen,
        "new_versions": new_versions, "requests_attempted": requests,
        "error": error, "coverage_gap": status != "COMPLETE_FOR_REQUESTED_RANGE",
        "checkpoint_available": store.status(stream_key)["has_replay_checkpoint"],
        "started_at_utc": run["started_at_utc"], "finished_at_utc": run["finished_at_utc"],
        "interpretation": "Bounded run only; successful requests do not establish ongoing availability or historic first-public time",
    }


def ingest_probe_pages(*, store: Jin10Store, stream_key: str, spec: ToolSpec,
                       captured_pages: list[dict]) -> dict:
    """Import already captured real MCP replies without another provider request.

    Each page must include the actual request arguments, response JSON-RPC object,
    HTTP status, and retrieval timestamp supplied by the original probe. This
    import never creates a guessed historical first-availability time.
    """
    spec.validate()
    if not 1 <= len(captured_pages) <= 3:
        raise ValueError("Probe import is bounded to at most three captured pages")
    store.ensure_stream(stream_key, spec.name)
    if store.stream(stream_key)["tool_name"] != spec.name:
        raise ValueError("Stream key already belongs to a different tool")
    run_id = store.new_run(stream_key, now_utc())
    expected_cursor = None
    status, error = "CAPTURED_PARTIAL", None
    new_versions = items_seen = pages = 0
    for capture in captured_pages:
        try:
            arguments = capture["arguments"]
            response = capture["response"]
            received_at = capture["received_at_utc"]
            if not isinstance(arguments, dict) or not isinstance(response, dict):
                raise ValueError("Captured arguments and response must be objects")
            timestamp = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                raise ValueError("Captured receipt time must have a timezone")
            if arguments != spec.args_for_cursor(expected_cursor):
                raise ValueError("Captured arguments do not match negotiated schema/cursor chain")
            if capture.get("http_status") not in {200, 202}:
                raise ValueError("Captured page lacks successful HTTP status")
            items, has_more, next_cursor, business_status, payload_locator = parse_page(response, spec)
            if has_more and not spec.cursor_argument:
                raise ValueError("Captured pagination cannot be resumed with negotiated inputs")
            normalized = [normalize_item(item, spec, received_at, i, payload_locator)
                          for i, item in enumerate(items)]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            if isinstance(capture.get("response"), dict):
                store.record_incomplete_response(
                    run_id=run_id, tool_name=spec.name, arguments=capture.get("arguments") or {},
                    response=capture["response"], received_at_utc=capture.get("received_at_utc") or now_utc(),
                    http_status=capture.get("http_status"),
                    business_status=capture.get("business_status"), cursor_in=expected_cursor)
            status, error = "PROBE_IMPORT_INCOMPLETE", f"{type(exc).__name__}: {exc}"
            break
        receipt = store.record_page(
            run_id=run_id, stream_key=stream_key, tool_name=spec.name,
            arguments=arguments, response=response, received_at_utc=received_at,
            http_status=capture.get("http_status"),
            business_status=(business_status if business_status is not None else capture.get("business_status")),
            cursor_in=expected_cursor, cursor_out=next_cursor, has_more=has_more,
            normalized_items=normalized,
        )
        new_versions += receipt["new_versions"]
        items_seen += len(normalized)
        pages += 1
        expected_cursor = next_cursor
        if not has_more:
            status = "COMPLETE_FOR_REQUESTED_RANGE"
            break
    run = store.finish_run(run_id, stream_key, status, now_utc(), error)
    return {
        "schema": "jin10_offline_probe_import_v1", "stream_key": stream_key,
        "tool_name": spec.name, "run_id": run_id, "status": status,
        "captured_pages_imported": pages, "items_seen": items_seen,
        "new_versions": new_versions, "network_requests_in_this_import": 0,
        "upstream_captured_pages_supplied": len(captured_pages),
        "coverage_gap": status != "COMPLETE_FOR_REQUESTED_RANGE",
        "checkpoint_available": store.status(stream_key)["has_replay_checkpoint"],
        "error": error, "imported_at_utc": run["finished_at_utc"],
    }
