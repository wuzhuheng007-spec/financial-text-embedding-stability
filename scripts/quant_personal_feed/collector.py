"""Two independent read-only provider loops for the local personal portal."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from scripts.jin10_mcp.engine import unwrap_mcp_payload
from scripts.jin10_mcp_http import McpHttpError, McpProtocolError, McpSession
from scripts.market_observer_v1.moomoo_webapi_oauth import token_store
from scripts.market_observer_v1.moomoo_ws import MoomooWebSocketPush, PROVIDER_NAME, WS_URL, validate_live_config
from scripts.market_observer_v1.service import AuthorizationError, QuotaError

from . import incidents
from .store import PersonalFeedStore, published_utc, utc_now
from .session_quotes import parse_push_children


ROOT = Path(__file__).resolve().parents[2]
TOKEN_FILE = ROOT / "artifacts/market_observer_live/moomoo_webapi_oauth.dpapi"


def under_root(relative: str) -> Path:
    path = (ROOT / relative).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError("Configured path leaves the repository")
    return path


def owner_id(config: dict) -> int:
    # Windows keeps the original read-only SQLite lookup; Linux containers set
    # QUANT_OWNER_RESOLVER=django_orm so the PostgreSQL account table is read
    # through Django rather than by a raw auth_user query.
    from .owner import resolve_owner_id
    return resolve_owner_id(config)


def verified_current_symbols(config: dict) -> dict[str, str]:
    mapping_path = under_root(config["mapping_receipt_path"])
    if hashlib.sha256(mapping_path.read_bytes()).hexdigest() != config["mapping_receipt_sha256"]:
        raise ValueError("Official current security mapping receipt changed")
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    return {row["requested_symbol"]: row["provider_code"] for row in mapping.get("rows", [])
            if row.get("identity_scope") == "CURRENT_ONLY" and row.get("provider_market") == "US"
            and row.get("provider_code")}


def verified_subscriptions(config: dict, desired_symbols: list[str] | None = None) -> tuple[dict, list[dict], dict[str, str]]:
    mapping_path = under_root(config["mapping_receipt_path"])
    if hashlib.sha256(mapping_path.read_bytes()).hexdigest() != config["mapping_receipt_sha256"]:
        raise ValueError("Official current security mapping receipt changed")
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    current = {row.get("requested_symbol"): row for row in mapping.get("rows", [])
               if row.get("identity_scope") == "CURRENT_ONLY"}
    names = {}
    watch_path = ROOT / "configs/market_observer_watchlist_v2_20261115.json"
    if watch_path.is_file():
        watch = json.loads(watch_path.read_text(encoding="utf-8"))
        for row in mapping.get("rows", []):
            security = watch.get("securities", {}).get(row.get("security_id"), {})
            entity = watch.get("entities", {}).get(security.get("entity_id"), {})
            if entity.get("name"):
                names[row.get("requested_symbol")] = entity["name"]
    extended = ROOT / "results/watchlist_live_20261115/security_mapping_redacted.json"
    if extended.is_file():
        for row in json.loads(extended.read_text(encoding="utf-8")).get("rows", []):
            if row.get("provider_name"):
                names[row["requested_symbol"]] = row["provider_name"]
    selected, unavailable = [], []
    for symbol in (desired_symbols if desired_symbols is not None else config["moomoo"]["priority_symbols"]):
        row = current.get(symbol)
        if not row or not row.get("provider_code") or row.get("provider_market") != "US":
            unavailable.append({"symbol": symbol, "reason": "NO_VERIFIED_CURRENT_US_PROVIDER_CODE"})
            continue
        code = row["provider_code"]
        selected.append({"requested_symbol": symbol, "provider_code": code,
                         "instrument_id": code, "market": "US", "kind": "QUOTE",
                         "verification_status": "VERIFIED_EXACT_PROVIDER_CODE_MARKET"})
    budget = config["moomoo"].get("local_subscription_key_budget", 6)
    if not selected or len(selected) > budget or budget > 6:
        raise ValueError("Personal six-key subscription budget invalid")
    live = {"provider": PROVIDER_NAME, "endpoint": WS_URL, "scope": "quote:read",
            "entitlement_state": "BOUNDED_PERSONAL", "subscription_key_budget": budget,
            "mapping_receipt_path": config["mapping_receipt_path"],
            "mapping_receipt_sha256": config["mapping_receipt_sha256"],
            "subscriptions": selected, "unserved_subscriptions": unavailable}
    validate_live_config(live, repo_root=ROOT)
    return live, unavailable, names


def run_moomoo(config: dict, user_id: int, stop: threading.Event) -> None:
    cache = under_root(config["cache_db"])
    store = PersonalFeedStore(cache, user_id)
    max_attempts = config["moomoo"]["max_reconnect_attempts"]
    attempt = 0
    # What the loop was doing when something failed, and since when it has been failing: written into the
    # sanitized incident record so a disconnect can be traced afterwards (phase, reason code, code location).
    phase = "SELECT_SUBSCRIPTIONS"
    failing_since: str | None = None
    failures = 0

    def incident(exc: BaseException, recorded_state: str) -> None:
        nonlocal failing_since, failures
        # never raises: a failure to describe or store the incident must not become a second disconnect
        rec = incidents.record(exc, store, provider="moomoo", phase=phase, recorded_state=recorded_state, attempt=attempt)
        failing_since = failing_since or (rec or {}).get("at_utc") or utc_now(); failures += 1
    try:
        store.ensure_selection(config["moomoo"]["priority_symbols"])
        store.provider_metric("moomoo", worker_alive=1)
        while not stop.is_set() and attempt < max_attempts:
            generation, desired = store.selection()
            if not desired:
                store.state("moomoo", "NO_SUBSCRIPTIONS")
                stop.wait(2)
                continue
            live, unavailable, names = verified_subscriptions(config, desired)
            for row in unavailable:
                store.symbol_state(user_id,row["symbol"],"UNAVAILABLE",row["reason"])
            for row in live["subscriptions"]:
                store.symbol_state(user_id,row["requested_symbol"],"CONNECTING")
            adapter = MoomooWebSocketPush(
                live=live, token_store=token_store(TOKEN_FILE),
                status_path=under_root(config["moomoo"]["status_path"]))
            try:
                phase = "AUTHORIZE_AND_CONNECT"
                store.state("moomoo","CONNECTING")
                adapter.connect()
                phase = "SUBSCRIBE_ACK"
                accepted = {r["provider_code"] for r in adapter._ever_acknowledged}
                skipped = {r["provider_code"]: r.get("reason", "SERVER_REJECTED")
                           for r in adapter._skipped}
                for row in live["subscriptions"]:
                    symbol, code = row["requested_symbol"], row["provider_code"]
                    if code in accepted:
                        store.symbol_state(user_id,symbol,"SUBSCRIBED")
                    else:
                        store.symbol_state(user_id,symbol,"UNAVAILABLE",skipped.get(code,"NO_ACK"))
                state = adapter.connection_state
                store.state("moomoo",state,success=True)
                attempt = 0
                if failures:
                    try:
                        incidents.emit(incidents.recovery(provider="moomoo", state=state, since_utc=failing_since, failures=failures), store)
                    except Exception:
                        pass
                    failing_since, failures = None, 0
                # Closed-market pushes often repeat an identical quote. Keep
                # changed source-time/price values, and batch liveness writes.
                last_parent: dict[str, tuple[str, str]] = {}
                last_raw: dict[str, tuple[str, str]] = {}
                pending_duplicates = 0
                pending_received: str | None = None
                pending_source: str | None = None
                last_activity_write = time.monotonic()
                last_written_state = state
                while not stop.is_set():
                    new_generation, new_desired = store.selection()
                    if new_generation != generation:
                        for old in desired:
                            if old not in new_desired:
                                store.symbol_state(user_id,old,"STOPPED","USER_UNSUBSCRIBED")
                        break
                    phase = "RECEIVE"
                    event = adapter.next_message(timeout_seconds=2)
                    state = adapter.connection_state
                    if event and event.get("kind") == "QUOTE":
                        phase = "STORE_QUOTE"
                        quote = event["quote"]
                        code = quote["instrument_id"]
                        symbol = next((r["requested_symbol"] for r in live["subscriptions"]
                                       if r["provider_code"] == code), None)
                        if symbol:
                            market_code = adapter._market_state.get("US")
                            market_state = (f"US_{market_code}" if market_code is not None else "UNKNOWN")
                            parent_key = (str(quote.get("payload", {}).get("price")),
                                          quote["provider_time"])
                            if last_parent.get(symbol) == parent_key:
                                saved = False
                            else:
                                saved = store.put_quote(user_id,quote,symbol=symbol,
                                    display_name=names.get(symbol,symbol),market_state=market_state,
                                    limit=config["retention"]["quotes_per_symbol"])
                                last_parent[symbol] = parent_key
                            provider_data = quote.get("payload", {}).get("provider_data")
                            if isinstance(provider_data, dict):
                                raw_push = json.dumps(provider_data,ensure_ascii=False,sort_keys=True)
                                digest = hashlib.sha256(raw_push.encode("utf-8")).hexdigest()
                                raw_key = (digest, quote["provider_time"])
                                if last_raw.get(symbol) != raw_key:
                                    phase = "STORE_SESSION_QUOTES"
                                    children = parse_push_children(quote,raw_sha256=digest)
                                    if children:
                                        store.put_session_quotes(user_id,children,
                                            states={symbol:{row["session"]:("HAS_SOURCE_TIME" if row["source_at"]
                                                             else "SNAPSHOT_TIME_UNKNOWN") for row in children}},
                                            checked_at=quote["received_at"],raw_json=raw_push,
                                            raw_sha256=digest,source_channel="WS_QUOTE")
                                    last_raw[symbol] = raw_key
                            pending_received = quote["received_at"]
                            pending_source = quote["provider_time"]
                            if saved:
                                store.symbol_state(user_id,symbol,"RECEIVED")
                                store.provider_metric("moomoo",last_quote_saved_at=quote["received_at"])
                            else:
                                pending_duplicates += 1
                            if (saved or state != last_written_state or
                                time.monotonic() - last_activity_write >= 5):
                                store.state("moomoo",state,success=True,
                                    source_at=pending_source,received_at=pending_received)
                                store.provider_metric("moomoo",last_push_at=pending_received,
                                                      duplicate_quote_packets=pending_duplicates)
                                pending_duplicates = 0
                                pending_received = pending_source = None
                                last_activity_write = time.monotonic()
                                last_written_state = state
                    elif event and event.get("kind") == "GAP":
                        if pending_received:
                            store.provider_metric("moomoo",last_push_at=pending_received,
                                                  duplicate_quote_packets=pending_duplicates)
                            pending_duplicates = 0
                            pending_received = pending_source = None
                        store.state("moomoo","PARTIAL_SYMBOL_UNAVAILABLE",error_class=event["gap"].get("reason"))
                    else:
                        if pending_received or state != last_written_state or time.monotonic() - last_activity_write >= 5:
                            store.state("moomoo",state,success=bool(pending_received),
                                source_at=pending_source,received_at=pending_received)
                            if pending_received:
                                store.provider_metric("moomoo",last_push_at=pending_received,
                                                      duplicate_quote_packets=pending_duplicates)
                                pending_duplicates = 0
                                pending_received = pending_source = None
                            last_activity_write = time.monotonic()
                            last_written_state = state
            except AuthorizationError as exc:
                incident(exc, "AUTH_EXPIRED")
                store.state("moomoo","AUTH_EXPIRED",error_class="AUTHORIZATION")
                return
            except QuotaError as exc:
                incident(exc, "RATE_LIMITED")
                store.state("moomoo","RATE_LIMITED",error_class="SUBSCRIPTION_QUOTA")
                return
            except Exception as exc:
                attempt += 1
                incident(exc, "DISCONNECTED")
                store.state("moomoo","DISCONNECTED",error_class=type(exc).__name__)
                if attempt >= max_attempts:
                    return
                wait = min(config["moomoo"]["reconnect_initial_seconds"] * (2 ** (attempt-1)),
                           config["moomoo"]["reconnect_max_seconds"])
                stop.wait(wait)
            finally:
                adapter.close()
        if stop.is_set():
            store.state("moomoo","DISCONNECTED",error_class="USER_STOP")
    finally:
        store.provider_metric("moomoo",worker_alive=0)
        store.close()


def _jin10_session(config: dict, store: PersonalFeedStore) -> tuple[McpSession, dict]:
    session = McpSession(max_requests=20,
                         timeout_seconds=config["jin10"]["timeout_seconds"],
                         max_response_bytes=config["jin10"]["max_response_bytes"])
    store.state("jin10", "CONNECTING", request_increment=1)
    store.provider_metric("jin10",handshake_attempts_total=1)
    session.initialize()
    store.state("jin10", "CONNECTING", request_increment=1)
    store.provider_metric("jin10",handshake_attempts_total=1)
    session.initialized()
    store.state("jin10", "CONNECTING", request_increment=1)
    store.provider_metric("jin10",handshake_attempts_total=1)
    listing = session.list_tools().payload["result"]
    tools = [t for t in listing.get("tools", []) if t.get("name") == config["jin10"]["tool"]]
    if len(tools) != 1:
        raise McpProtocolError("Negotiated flash tool missing or duplicated")
    schema = tools[0].get("inputSchema", {})
    if schema.get("type") != "object" or set(schema.get("required", [])):
        raise McpProtocolError("Fresh flash input schema does not permit an empty latest-page request")
    store.provider_metric("jin10",handshake_success_total=1)
    return session, schema


def _wait_heartbeat(store: PersonalFeedStore, provider: str, state: str,
                    stop: threading.Event, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while not stop.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        stop.wait(min(5,remaining))
        if not stop.is_set():
            store.state(provider,state)


def _retry_after_seconds(value: str | None, minimum: float) -> float:
    if not value:
        return minimum
    try:
        return max(minimum, float(value))
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is not None:
                return max(minimum, (date - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            pass
    return minimum


def run_jin10(config: dict, user_id: int, stop: threading.Event) -> None:
    from .relevance_v15 import RULE_VERSION as SCREEN_RULE_VERSION, screen
    v15_config = json.loads((ROOT / "configs/quant_portal_personal_v15_20261116.json").read_text(encoding="utf-8"))
    if v15_config["relevance_rule_version"] != SCREEN_RULE_VERSION:
        raise ValueError("Personal news screening rule version mismatch")
    session = None
    failures = 0
    interval = config["jin10"]["poll_interval_seconds"]
    max_calls = config["jin10"]["max_business_calls_per_utc_day"]
    store = PersonalFeedStore(under_root(config["cache_db"]), user_id)
    try:
        store.provider_metric("jin10",worker_alive=1)
        if interval < 60 or not 1 <= max_calls <= 1440:
            raise ValueError("Jin10 local budget requires >=60s spacing and <=1440 business calls/UTC day")
        while not stop.is_set():
            if store.daily_business_attempts("jin10") >= max_calls:
                store.state("jin10","REQUEST_BUDGET_PAUSED",error_class="LOCAL_DAILY_CAP")
                _wait_heartbeat(store,"jin10","REQUEST_BUDGET_PAUSED",stop,min(interval,60))
                continue
            business_attempted = False
            try:
                store.state("jin10","CONNECTING" if session is None else "CONNECTED")
                if session is None or session.requests_used >= 18:
                    session, _schema = _jin10_session(config,store)
                    store.state("jin10","CONNECTED")
                before = time.monotonic()
                # A fresh first page is the latest feed; a returned cursor points
                # backwards. Subsequent polls re-read one page and deduplicate.
                store.state("jin10","CONNECTED",request_increment=1)
                store.provider_metric("jin10",business_attempts_today=1)
                business_attempted = True
                reply = session.call_tool(config["jin10"]["tool"], {})
                received = utc_now()
                payload, _locator = unwrap_mcp_payload(reply.payload)
                if payload.get("status") != 200 or not isinstance(payload.get("data"),dict):
                    raise McpProtocolError("Flash business response rejected")
                items = payload["data"].get("items")
                if not isinstance(items,list):
                    raise McpProtocolError("Flash page lacks items array")
                keys = [hashlib.sha256(json.dumps((item.get("id"),item.get("url"),item.get("time"),
                        item.get("title"),item.get("content")),ensure_ascii=False).encode("utf-8")).hexdigest()
                        for item in items if isinstance(item,dict)]
                prior = store.db.execute("SELECT last_page_fingerprints FROM provider_state WHERE provider='jin10'").fetchone()
                prior_keys = set(json.loads(prior[0])) if prior and prior[0] else set()
                if prior_keys and keys and not prior_keys.intersection(keys):
                    store.provider_metric("jin10",coverage_gap="POSSIBLE_GAP_NO_PAGE_OVERLAP")
                store.provider_metric("jin10",last_page_fingerprints=json.dumps(keys))
                for item in items:
                    if isinstance(item,dict) and (item.get("content") or item.get("title")):
                        try:
                            decision = screen(item).as_dict()
                        except (ValueError, TypeError, KeyError, re.error):
                            decision = {"status": "PENDING_REVIEW", "scope": "NONE",
                                        "reason_key": "screening_error", "evidence_phrase": None,
                                        "evidence_location": None, "rule_version": SCREEN_RULE_VERSION}
                        insertion = None
                        if decision["status"] == "INCLUDE":
                            insertion = store.put_news(user_id,item,received_at=received,
                                max_items=config["retention"]["news_items"],
                                max_versions=config["retention"]["news_versions_per_item"])
                        store.record_news_screening(user_id,item,decision,received_at=received,
                            insertion_result=insertion,
                            pending_limit=v15_config["pending_review_limit"],
                            pending_hours=v15_config["pending_review_hours"])
                source_times = [stamp for item in items if isinstance(item,dict)
                                if (stamp := published_utc(item.get("time")))]
                store.state("jin10","CONNECTED",success=True,received_at=received,
                            source_at=max(source_times) if source_times else None,
                            business_increment=1)
                store.provider_metric("jin10",business_success_total=1)
                failures = 0
                # Each successful latest-page poll is one completed cycle, even
                # when every item was already cached; no synthetic item is made.
                _wait_heartbeat(store,"jin10","CONNECTED",stop,
                                max(0,interval-(time.monotonic()-before)))
            except McpHttpError as exc:
                if business_attempted:
                    store.provider_metric("jin10",business_failure_total=1)
                store.state("jin10","AUTH_EXPIRED" if exc.status in (401,403)
                            else "RATE_LIMITED" if exc.status==429 else "DISCONNECTED",
                            error_class=f"HTTP_{exc.status}")
                if exc.status in (401,403):
                    return
                if exc.status == 429:
                    delay = _retry_after_seconds(exc.retry_after, interval)
                    _wait_heartbeat(store,"jin10","RATE_LIMITED",stop,delay)
                else:
                    failures += 1
                    if failures >= config["jin10"]["max_reconnect_attempts"]:
                        return
                    _wait_heartbeat(store,"jin10","DISCONNECTED",stop,min(60,2**failures*5))
                session = None
            except sqlite3.OperationalError:
                # Reopen through the supervisor rather than trying to record
                # an error using the same locked connection.
                raise
            except Exception as exc:
                if business_attempted:
                    store.provider_metric("jin10",business_failure_total=1)
                failures += 1
                store.state("jin10","DISCONNECTED",error_class=type(exc).__name__)
                if failures >= config["jin10"]["max_reconnect_attempts"]:
                    return
                session = None
                _wait_heartbeat(store,"jin10","DISCONNECTED",stop,min(60,2**failures*5))
        store.state("jin10","DISCONNECTED",error_class="USER_STOP")
    finally:
        try:
            store.provider_metric("jin10",worker_alive=0)
        except sqlite3.OperationalError:
            # Cleanup is best-effort: do not replace the original exception
            # or turn an authentication stop into a SQLite-driven restart.
            # The reader also checks heartbeat freshness independently.
            pass
        finally:
            store.close()
