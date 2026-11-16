"""Two independent read-only provider loops for the local personal portal."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from scripts.jin10_mcp.engine import unwrap_mcp_payload
from scripts.jin10_mcp_http import McpHttpError, McpProtocolError, McpSession
from scripts.market_observer_v1.moomoo_webapi_oauth import LocalTokenStore
from scripts.market_observer_v1.moomoo_ws import MoomooWebSocketPush, PROVIDER_NAME, WS_URL, validate_live_config
from scripts.market_observer_v1.service import AuthorizationError, QuotaError

from .store import PersonalFeedStore, published_utc, utc_now


ROOT = Path(__file__).resolve().parents[2]
TOKEN_FILE = ROOT / "artifacts/market_observer_live/moomoo_webapi_oauth.dpapi"


def under_root(relative: str) -> Path:
    path = (ROOT / relative).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError("Configured path leaves the repository")
    return path


def owner_id(config: dict) -> int:
    db = under_root(config["portal_db"])
    with closing(sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)) as connection:
        rows = connection.execute("SELECT id FROM auth_user WHERE username=?",
                                  (config["owner_username"],)).fetchall()
    if len(rows) != 1:
        raise ValueError("Expected one existing personal owner account; registration is not changed")
    return int(rows[0][0])


def verified_subscriptions(config: dict) -> tuple[dict, list[dict], dict[str, str]]:
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
    for symbol in config["moomoo"]["priority_symbols"]:
        row = current.get(symbol)
        if not row or not row.get("provider_code") or row.get("provider_market") != "US":
            unavailable.append({"symbol": symbol, "reason": "NO_VERIFIED_CURRENT_US_PROVIDER_CODE"})
            continue
        code = row["provider_code"]
        selected.append({"requested_symbol": symbol, "provider_code": code,
                         "instrument_id": code, "market": "US", "kind": "QUOTE",
                         "verification_status": "VERIFIED_EXACT_PROVIDER_CODE_MARKET"})
    if not selected or len(selected) > 6:
        raise ValueError("Personal six-key subscription budget invalid")
    live = {"provider": PROVIDER_NAME, "endpoint": WS_URL, "scope": "quote:read",
            "entitlement_state": "BOUNDED_PERSONAL", "subscription_key_budget": len(selected),
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
    try:
        live, unavailable, names = verified_subscriptions(config)
        for row in unavailable:
            store.symbol_state(user_id,row["symbol"],"UNAVAILABLE",row["reason"])
        for row in live["subscriptions"]:
            store.symbol_state(user_id,row["requested_symbol"],"CONNECTING")
        while not stop.is_set() and attempt < max_attempts:
            adapter = MoomooWebSocketPush(
                live=live, token_store=LocalTokenStore(TOKEN_FILE),
                status_path=under_root(config["moomoo"]["status_path"]))
            try:
                store.state("moomoo","CONNECTING")
                adapter.connect()
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
                while not stop.is_set():
                    event = adapter.next_message(timeout_seconds=2)
                    state = adapter.connection_state
                    if event and event.get("kind") == "QUOTE":
                        quote = event["quote"]
                        code = quote["instrument_id"]
                        symbol = next((r["requested_symbol"] for r in live["subscriptions"]
                                       if r["provider_code"] == code), None)
                        if symbol:
                            market_code = adapter._market_state.get("US")
                            market_state = (f"US_{market_code}" if market_code is not None else "UNKNOWN")
                            saved = store.put_quote(user_id,quote,symbol=symbol,
                                display_name=names.get(symbol,symbol),market_state=market_state,
                                limit=config["retention"]["quotes_per_symbol"])
                            store.state("moomoo",state,success=True,
                                source_at=quote["provider_time"],received_at=quote["received_at"])
                            if saved:
                                store.symbol_state(user_id,symbol,"RECEIVED")
                    elif event and event.get("kind") == "GAP":
                        store.state("moomoo","PARTIAL_SYMBOL_UNAVAILABLE",error_class=event["gap"].get("reason"))
                    else:
                        store.state("moomoo",state)
            except AuthorizationError:
                store.state("moomoo","AUTH_EXPIRED",error_class="AUTHORIZATION")
                return
            except QuotaError:
                store.state("moomoo","RATE_LIMITED",error_class="SUBSCRIPTION_QUOTA")
                return
            except Exception as exc:
                attempt += 1
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
        store.close()


def _jin10_session(config: dict) -> tuple[McpSession, dict]:
    session = McpSession(max_requests=20,
                         timeout_seconds=config["jin10"]["timeout_seconds"],
                         max_response_bytes=config["jin10"]["max_response_bytes"])
    session.initialize()
    session.initialized()
    listing = session.list_tools().payload["result"]
    tools = [t for t in listing.get("tools", []) if t.get("name") == config["jin10"]["tool"]]
    if len(tools) != 1:
        raise McpProtocolError("Negotiated flash tool missing or duplicated")
    schema = tools[0].get("inputSchema", {})
    if schema.get("type") != "object" or set(schema.get("required", [])):
        raise McpProtocolError("Fresh flash input schema does not permit an empty latest-page request")
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
    store = PersonalFeedStore(under_root(config["cache_db"]), user_id)
    session = None
    failures = 0
    interval = config["jin10"]["poll_interval_seconds"]
    max_calls = config["jin10"]["max_business_calls_per_utc_day"]
    try:
        if interval < 60 or not 1 <= max_calls <= 1440:
            raise ValueError("Jin10 local budget requires >=60s spacing and <=1440 business calls/UTC day")
        while not stop.is_set():
            if store.daily_business_calls("jin10") >= max_calls:
                store.state("jin10","REQUEST_BUDGET_PAUSED",error_class="LOCAL_DAILY_CAP")
                _wait_heartbeat(store,"jin10","REQUEST_BUDGET_PAUSED",stop,min(interval,60))
                continue
            try:
                store.state("jin10","CONNECTING" if session is None else "CONNECTED")
                if session is None or session.requests_used >= 18:
                    session, _schema = _jin10_session(config)
                    store.state("jin10","CONNECTED",success=True,request_increment=3)
                before = time.monotonic()
                # A fresh first page is the latest feed; a returned cursor points
                # backwards. Subsequent polls re-read one page and deduplicate.
                reply = session.call_tool(config["jin10"]["tool"], {})
                received = utc_now()
                store.state("jin10","CONNECTED",success=True,request_increment=1,
                            business_increment=1,received_at=received)
                payload, _locator = unwrap_mcp_payload(reply.payload)
                if payload.get("status") != 200 or not isinstance(payload.get("data"),dict):
                    raise McpProtocolError("Flash business response rejected")
                items = payload["data"].get("items")
                if not isinstance(items,list):
                    raise McpProtocolError("Flash page lacks items array")
                for item in items:
                    if isinstance(item,dict) and (item.get("content") or item.get("title")):
                        store.put_news(user_id,item,received_at=received,
                            max_items=config["retention"]["news_items"],
                            max_versions=config["retention"]["news_versions_per_item"])
                source_times = [stamp for item in items if isinstance(item,dict)
                                if (stamp := published_utc(item.get("time")))]
                store.state("jin10","CONNECTED",success=True,received_at=received,
                            source_at=max(source_times) if source_times else None)
                failures = 0
                # Each successful latest-page poll is one completed cycle, even
                # when every item was already cached; no synthetic item is made.
                _wait_heartbeat(store,"jin10","CONNECTED",stop,
                                max(0,interval-(time.monotonic()-before)))
            except McpHttpError as exc:
                store.state("jin10","AUTH_EXPIRED" if exc.status in (401,403)
                            else "RATE_LIMITED" if exc.status==429 else "DISCONNECTED",
                            error_class=f"HTTP_{exc.status}",request_increment=1)
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
            except Exception as exc:
                failures += 1
                store.state("jin10","DISCONNECTED",error_class=type(exc).__name__)
                if failures >= config["jin10"]["max_reconnect_attempts"]:
                    return
                session = None
                _wait_heartbeat(store,"jin10","DISCONNECTED",stop,min(60,2**failures*5))
        store.state("jin10","DISCONNECTED",error_class="USER_STOP")
    finally:
        store.close()
