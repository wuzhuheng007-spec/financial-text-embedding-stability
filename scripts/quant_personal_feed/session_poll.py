"""One owner-bound, batch read-only moomoo stock-quote poll per configured interval."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from scripts.market_observer_v1.moomoo_webapi_oauth import LocalTokenStore, OAuthError, token_store as platform_token_store

from .collector import ROOT, TOKEN_FILE, under_root, verified_current_symbols
from .session_quotes import SESSIONS, parse_stock_quote_row
from .store import PersonalFeedStore, utc_now

POLICY_PATH = ROOT / "configs/quant_portal_personal_sessions_v13_20261116.json"


def fetch_batch(token_store: LocalTokenStore, codes: list[str], endpoint: str) -> tuple[dict, str, str]:
    """Single POST, 15-second timeout, 2 MiB response ceiling; no retries here."""
    token = token_store.access_token()
    request = Request(endpoint, data=json.dumps({"code_list": codes}).encode("utf-8"),
                      method="POST", headers={"Authorization": "Bearer " + token,
                                               "Content-Type": "application/json"})
    with urlopen(request, timeout=15) as response:
        body = response.read(2_000_001)
        if len(body) > 2_000_000:
            raise ValueError("STOCK_QUOTE_RESPONSE_TOO_LARGE")
    raw = body.decode("utf-8")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("STOCK_QUOTE_NOT_OBJECT")
    return parsed, raw, hashlib.sha256(body).hexdigest()


def ingest_response(store: PersonalFeedStore, owner: int, payload: dict, raw: str,
                    digest: str, requested: dict[str, str], received_at: str) -> dict:
    if payload.get("ret_code") != 0:
        raise ValueError(f"BUSINESS_{payload.get('ret_code')}")
    quote_list = payload.get("data", {}).get("quote_list")
    if not isinstance(quote_list, list):
        raise ValueError("QUOTE_LIST_MISSING")
    rows: list[dict] = []
    states: dict[str, dict[str, str]] = {}
    seen = set()
    for quote in quote_list:
        if not isinstance(quote, dict) or quote.get("code") not in requested.values():
            continue
        code = quote["code"]
        if code in seen:
            raise ValueError("DUPLICATE_PROVIDER_CODE")
        seen.add(code)
        parsed, per_session = parse_stock_quote_row(quote, received_at=received_at, raw_sha256=digest)
        symbol = next(symbol for symbol, candidate in requested.items() if candidate == code)
        if any(item["symbol"] != symbol for item in parsed):
            raise ValueError("MAPPING_MISMATCH")
        rows.extend(parsed)
        states[symbol] = per_session
    for symbol, code in requested.items():
        if code not in seen:
            states[symbol] = {session: "NOT_YET_VERIFIED" for session in SESSIONS}
    saved = store.put_session_quotes(owner, rows, states=states, checked_at=received_at,
                                    raw_json=raw, raw_sha256=digest,
                                    source_channel="REST_STOCK_QUOTE")
    return {"codes_returned":len(seen), "session_values":len(rows), **saved}


def run_moomoo_snapshot(config: dict, owner: int, stop: threading.Event) -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    if (policy.get("schema") != "quant_portal_personal_sessions_v13" or
        policy.get("stock_quote_endpoint") != "https://webapi.moomoo.com/api/v1.0/quote/stock-quote" or
        policy.get("snapshot_poll_seconds", 0) < 60 or
        policy.get("snapshot_batch_limit") != 6):
        raise ValueError("Invalid v1.3 snapshot policy")
    store = PersonalFeedStore(under_root(config["cache_db"]), owner)
    token_store = platform_token_store(TOKEN_FILE)
    mapping = verified_current_symbols(config)
    store.provider_metric("moomoo_snapshot", worker_alive=1)
    failures = 0
    interval = policy["snapshot_poll_seconds"]
    try:
        # A prior HTTP 439 is an explicit WebAPI security-policy block, not a
        # transient transport error. A process restart must not evade it.
        prior = store.db.execute("SELECT last_error_class FROM provider_state WHERE provider=?",
                                 ("moomoo_snapshot",)).fetchone()
        if prior and prior[0] in {"HTTP_439", "WEBAPI_SECURITY_POLICY_BLOCKED"}:
            store.state("moomoo_snapshot", "SECURITY_POLICY_BLOCKED",
                        error_class="WEBAPI_SECURITY_POLICY_BLOCKED")
            while not stop.wait(60):
                store.state("moomoo_snapshot", "SECURITY_POLICY_BLOCKED",
                            error_class="WEBAPI_SECURITY_POLICY_BLOCKED")
            return
        while not stop.is_set():
            started = time.monotonic()
            store.state("moomoo_snapshot", "CONNECTING")
            if store.daily_business_attempts("moomoo_snapshot") >= policy["local_snapshot_attempt_budget_per_utc_day"]:
                store.state("moomoo_snapshot", "REQUEST_BUDGET_PAUSED")
                stop.wait(min(60, interval))
                continue
            _, desired = store.selection()
            requested = {symbol:mapping[symbol] for symbol in policy["symbols"]
                         if symbol in desired and symbol in mapping}
            if not requested:
                store.state("moomoo_snapshot", "NO_SUBSCRIPTIONS")
                stop.wait(interval)
                continue
            if len(requested) > policy["snapshot_batch_limit"]:
                raise ValueError("Snapshot symbol budget exceeded")
            store.state("moomoo_snapshot", "CONNECTING", request_increment=1)
            store.provider_metric("moomoo_snapshot", business_attempts_today=1)
            try:
                payload, raw, digest = fetch_batch(token_store, list(requested.values()), policy["stock_quote_endpoint"])
                received_at = utc_now()
                if payload.get("ret_code") == -11:
                    raise RuntimeError("BUSINESS_RATE_LIMITED")
                result = ingest_response(store, owner, payload, raw, digest, requested, received_at)
                store.state("moomoo_snapshot", "CONNECTED", success=True,received_at=received_at,
                            business_increment=1)
                store.provider_metric("moomoo_snapshot", business_success_total=1)
                if result["inserted"]:
                    store.provider_metric("moomoo_snapshot",last_quote_saved_at=received_at)
                failures = 0
            except (HTTPError, URLError, OAuthError, TimeoutError, ValueError, RuntimeError) as exc:
                failures += 1
                status = exc.code if isinstance(exc, HTTPError) else None
                forbidden = status in (401,403) or isinstance(exc, OAuthError)
                security_blocked = status == 439
                limited = status == 429 or str(exc) == "BUSINESS_RATE_LIMITED"
                state = ("SECURITY_POLICY_BLOCKED" if security_blocked else
                         "AUTH_EXPIRED" if forbidden else
                         "RATE_LIMITED" if limited else "DISCONNECTED")
                error = f"HTTP_{status}" if status else str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
                store.state("moomoo_snapshot",state,error_class=error)
                store.provider_metric("moomoo_snapshot",business_failure_total=1)
                if security_blocked or forbidden:
                    # Remain alive for an honest health state, but issue no
                    # further supplier requests until an explicit recovery.
                    while not stop.wait(60):
                        store.state("moomoo_snapshot", state, error_class=error)
                    return
                if limited:
                    after = exc.headers.get("Retry-After") if isinstance(exc,HTTPError) else None
                    try:
                        delay = max(interval, float(after)) if after else max(interval,300)
                    except ValueError:
                        delay = max(interval,300)
                    if failures >= 3:
                        # A finite consecutive limit ceiling prevents a
                        # permanently blocked endpoint from being hammered.
                        while not stop.wait(60):
                            store.state("moomoo_snapshot", "RATE_LIMITED_PAUSED",
                                        error_class=error)
                        return
                    stop.wait(min(900, delay))
                    continue
                if failures >= 3:
                    store.state("moomoo_snapshot", "RETRY_BACKOFF", error_class=error)
                    stop.wait(900)
                    continue
                stop.wait(min(300, interval * 2 ** failures))
                continue
            elapsed = time.monotonic() - started
            stop.wait(max(0, interval - elapsed))
        store.state("moomoo_snapshot", "DISCONNECTED",error_class="USER_STOP")
    finally:
        store.provider_metric("moomoo_snapshot",worker_alive=0)
        store.close()
