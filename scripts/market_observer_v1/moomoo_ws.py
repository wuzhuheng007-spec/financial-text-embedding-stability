"""Read-only moomoo WebAPI quote WebSocket adapter.

The adapter accepts only official, independently verified symbol codes and a
separate WebAPI quote:read credential. It never accesses Codex MCP credentials,
account assets, positions, or trading endpoints.
"""

from __future__ import annotations

import json
import hashlib
import os
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .moomoo_webapi_oauth import LocalTokenStore, OAuthError, OAuthTransientError
from .service import AuthorizationError, QuotaError


WS_URL = "wss://webapi-quote.moomoo.com/ws"
PROVIDER_NAME = "MOOMOO_WEBAPI_WS"
MAX_QUEUE = 512


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _millis_utc(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError, OSError):
        return None


def validate_live_config(live: dict[str, Any], *, repo_root: Path | None = None) -> None:
    if live.get("provider") != PROVIDER_NAME or live.get("endpoint") != WS_URL:
        raise ValueError("live config must use the official moomoo quote WebSocket")
    if live.get("scope") != "quote:read":
        raise ValueError("live config must request only quote:read")
    entitlement_state = live.get("entitlement_state")
    if entitlement_state not in {"ACCOUNT_CONFIRMED", "BOUNDED_PROBE",
                                 "BOUNDED_ACCOUNT_QUOTA_PROBE", "BOUNDED_PERSONAL"}:
        raise ValueError("account-specific quote entitlement has not been confirmed or capped for probing")
    limit = live.get("subscription_key_budget")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("positive account-specific subscription key budget required")
    subscriptions = live.get("subscriptions")
    if not isinstance(subscriptions, list) or not subscriptions:
        raise ValueError("at least one verified subscription is required")
    if len(subscriptions) > limit:
        raise ValueError("configured subscriptions exceed key budget")
    if entitlement_state == "BOUNDED_PROBE" and (len(subscriptions) != 1 or limit != 1):
        raise ValueError("bounded probe must use exactly one subscription key")
    if entitlement_state == "BOUNDED_PERSONAL" and (len(subscriptions) > 6 or limit > 6):
        raise ValueError("unknown-quota personal intake is capped at six verified quote keys")
    if entitlement_state == "BOUNDED_ACCOUNT_QUOTA_PROBE" and not live.get("entitlement_evidence"):
        raise ValueError("account quota probe receipt locator required")
    if entitlement_state == "ACCOUNT_CONFIRMED" and not live.get("entitlement_evidence"):
        raise ValueError("account-specific entitlement evidence locator required")
    if len(subscriptions) != len({(r.get("provider_code"), r.get("kind"))
                                 for r in subscriptions if isinstance(r, dict)}):
        raise ValueError("duplicate provider subscription keys")
    for row in subscriptions:
        if not isinstance(row, dict) or row.get("verification_status") != "VERIFIED_EXACT_PROVIDER_CODE_MARKET":
            raise ValueError("subscription lacks exact official symbol verification")
        code = row.get("provider_code")
        if not isinstance(code, str) or not code or "." not in code:
            raise ValueError("subscription has no official provider code")
        if row.get("instrument_id") != code:
            raise ValueError("instrument ID must equal the verified provider code")
        if row.get("kind") not in {"QUOTE", "KLINE_1M"}:
            raise ValueError("only quote and one-minute K-line subscriptions are permitted")
        if entitlement_state == "BOUNDED_PERSONAL" and row.get("kind") != "QUOTE":
            raise ValueError("unknown-quota personal intake permits basic quotes only")
        if row.get("market") != code.split(".", 1)[0]:
            raise ValueError("subscription market conflicts with verified provider code")
    if not live.get("mapping_receipt_sha256"):
        raise ValueError("verified mapping receipt hash required")
    if repo_root is not None:
        if entitlement_state in {"BOUNDED_ACCOUNT_QUOTA_PROBE", "ACCOUNT_CONFIRMED"}:
            quota_path = (repo_root / str(live["entitlement_evidence"])).resolve()
            artifacts = (repo_root / "artifacts").resolve()
            if not quota_path.is_relative_to(artifacts) or not quota_path.is_file():
                raise ValueError("account quota receipt must be an existing private artifact")
            quota = json.loads(quota_path.read_text(encoding="utf-8"))
            if (quota.get("quota_state") != "ACCOUNT_REPORTED" or
                    not isinstance(quota.get("remaining"), int) or
                    quota["remaining"] < len(subscriptions)):
                raise ValueError("account quota receipt does not support subscriptions")
        receipt_path = repo_root / str(live.get("mapping_receipt_path", ""))
        if not receipt_path.is_file() or not receipt_path.resolve().is_relative_to(repo_root.resolve()):
            raise ValueError("mapping receipt is missing or outside repository")
        if hashlib.sha256(receipt_path.read_bytes()).hexdigest() != live["mapping_receipt_sha256"]:
            raise ValueError("mapping receipt hash changed")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        exact = {(r.get("requested_symbol"), r.get("provider_code"), r.get("provider_market"))
                 for r in receipt.get("rows", []) if isinstance(r, dict)
                 and r.get("identity_scope") == "CURRENT_ONLY"}
        for row in subscriptions:
            if (row.get("requested_symbol"), row["provider_code"], row["market"]) not in exact:
                raise ValueError("subscription does not match current official mapping receipt")


class MoomooWebSocketPush:
    provider_name = PROVIDER_NAME

    def __init__(self, *, live: dict[str, Any], token_store: LocalTokenStore,
                 status_path: Path, ws_factory: Callable[..., Any] | None = None):
        validate_live_config(live)
        self.live = live
        self.token_store = token_store
        self.status_path = status_path
        self._factory = ws_factory
        self._ws: Any = None
        self._queue: deque[dict[str, Any]] = deque()
        self._pending_raw: deque[dict[str, Any]] = deque()
        self._subscriptions: list[dict[str, str]] = []
        self._ever_acknowledged: list[dict[str, str]] = []
        self._skipped: list[dict[str, str]] = []
        self._last_refresh = 0.0
        self._last_ping = 0.0
        self.connection_state = "DISCONNECTED"
        self._market_state: dict[str, int] = {}
        self._release_state = "NOT_APPLICABLE"
        self._last_control_response: dict[str, Any] | None = None

    def _receipt(self, *, note: str | None = None) -> None:
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        receipt = {"schema": "moomoo_webapi_ws_status_v1", "provider": PROVIDER_NAME,
                   "connection_state": self.connection_state,
                   "enabled_subscriptions": self._subscriptions,
                   "accepted_subscriptions_this_session": self._ever_acknowledged,
                   "skipped_subscriptions": self._skipped,
                   "market_state_codes": self._market_state,
                   "release_state": self._release_state,
                   "last_control_response": self._last_control_response,
                   "observed_at_utc": _now_utc(), "note": note,
                   "token_included": False, "quote_values_included": False}
        temp = self.status_path.with_name(self.status_path.name + ".tmp")
        temp.write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temp, self.status_path)

    def _send(self, message: dict[str, Any]) -> None:
        self._ws.send(json.dumps(message, separators=(",", ":")))

    def _response(self, request_id: str, *, timeout_seconds: float = 10.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            self._ws.settimeout(max(0.1, deadline - time.monotonic()))
            try:
                raw = self._ws.recv()
            except Exception as exc:
                # websocket-client timeout is not a business authorization error.
                if type(exc).__name__ == "WebSocketTimeoutException":
                    break
                raise ConnectionError(f"WebSocket receive failed: {type(exc).__name__}") from None
            try:
                message = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(message, dict):
                continue
            if message.get("id") == request_id:
                self._last_control_response = {"protocol": "ID_ECHO",
                                               "code": message.get("code")}
                return message
            # The account's 2026-11-15 live response used the REST-shaped
            # {ret_code, ret_msg, data} envelope without echoing the request
            # ID. There is exactly one outstanding control request here.
            if ("id" not in message and isinstance(message.get("ret_code"), int)
                    and not isinstance(message["ret_code"], bool)
                    and "ret_msg" in message and "data" in message):
                self._last_control_response = {"protocol": "IDLESS_RET_CODE",
                                               "code": message["ret_code"]}
                return {"code": message["ret_code"], "data": message["data"]}
            if message.get("type"):
                if len(self._pending_raw) >= MAX_QUEUE:
                    self._pending_raw.popleft()
                    self._append({"kind": "GAP", "gap": {
                        "stream_key": "push", "reason": "CONTROL_WAIT_QUEUE_OVERFLOW",
                        "detail": {"max_queue": MAX_QUEUE}}})
                self._pending_raw.append(message)
        raise ConnectionError("WebSocket control response timed out")

    def connect(self) -> None:
        if self._ws is not None:
            return
        try:
            token = self.token_store.access_token()
        except OAuthTransientError as exc:
            raise ConnectionError(f"token refresh unavailable: {exc}") from None
        except OAuthError as exc:
            raise AuthorizationError(str(exc)) from None
        if self._factory is None:
            try:
                import websocket
            except ImportError as exc:
                raise RuntimeError("websocket-client dependency is missing") from exc
            self._factory = websocket.create_connection
        self._subscriptions = []
        self._ever_acknowledged = []
        self._skipped = [dict(x) for x in self.live.get("unserved_subscriptions", [])]
        self._queue.clear()
        self._pending_raw.clear()
        self._release_state = "NOT_APPLICABLE"
        self._last_control_response = None
        self.connection_state = "CONNECTING"
        self._receipt()
        try:
            self._ws = self._factory(WS_URL, timeout=10, enable_multithread=True)
            request_id = uuid.uuid4().hex
            self._send({"id": request_id, "action": "auth",
                        "data": {"auth_type": "oauth2", "authorization": "Bearer " + token}})
            response = self._response(request_id)
            if response.get("code") not in (None, 0) or not response.get("session_id"):
                raise AuthorizationError(
                    f"WebSocket quote authentication rejected (code {response.get('code')}, "
                    f"session_id_present={bool(response.get('session_id'))})")
            self._last_refresh = time.monotonic()
            self._last_ping = self._last_refresh
            self.connection_state = "CONNECTED_DELAY_UNKNOWN"
            self._receipt(note="authenticated; subscription result pending")
            quota_exhausted = False
            for row in self.live["subscriptions"]:
                record = {"provider_code": row["provider_code"], "kind": row["kind"]}
                if quota_exhausted:
                    self._skipped.append({**record, "reason": "QUOTA_EXHAUSTED_EARLIER"})
                    continue
                request_id = uuid.uuid4().hex
                request: dict[str, Any] = {"id": request_id, "action": "subscribe"}
                if row["kind"] == "QUOTE":
                    request["quote"] = [row["provider_code"]]
                else:
                    request["kline"] = [{"symbol": row["provider_code"],
                                         "period": "1m", "adjust": "none"}]
                self._send(request)
                response = self._response(request_id)
                code = response.get("code")
                if code == 0:
                    self._subscriptions.append(record)
                    self._ever_acknowledged.append(record)
                elif code in {1, 4001, 4002, 4003}:
                    raise AuthorizationError(f"WebSocket subscription authorization rejected (code {code})")
                elif code == 4:
                    self._skipped.append({**record, "reason": "SERVER_QUOTA_EXCEEDED"})
                    quota_exhausted = True
                else:
                    self._skipped.append({**record, "reason": f"SERVER_REJECTED_CODE_{code}"})
                self._flush_pending_raw()
                self._receipt()
            if not self._subscriptions:
                if quota_exhausted:
                    raise QuotaError("WebSocket subscription quota unavailable")
                raise AuthorizationError("WebSocket accepted no verified quote subscriptions")
            self._receipt(note="subscriptions acknowledged; delay remains unverified")
        except Exception:
            self.close()
            raise

    def _enqueue_push(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == "MARKET_STATE":
            data = message.get("data") or {}
            if (isinstance(data, dict) and isinstance(data.get("market"), str)
                    and isinstance(data.get("status"), int) and not isinstance(data["status"], bool)):
                self._market_state[data["market"]] = data["status"]
                subscribed_markets = {s["provider_code"].split(".", 1)[0] for s in self._subscriptions}
                # AFTER_HOURS_END (11) does not establish that the separate
                # US overnight session has closed.
                if subscribed_markets and all(self._market_state.get(m) in {0, 6}
                                              for m in subscribed_markets):
                    self.connection_state = "CONNECTED_MARKET_CLOSED"
                else:
                    self.connection_state = "CONNECTED_DELAY_UNKNOWN"
                self._receipt()
            return
        symbol = message.get("symbol")
        data = message.get("data")
        if not isinstance(symbol, str) or not isinstance(data, dict):
            return
        enabled = {(s["provider_code"], s["kind"]) for s in self._subscriptions}
        received = _now_utc()
        if kind == "QUOTE" and (symbol, "QUOTE") in enabled:
            provider_time = _millis_utc(data.get("data_time_ms"))
            if provider_time is None:
                self._append({"kind": "GAP", "gap": {"stream_key": symbol,
                    "reason": "QUOTE_MISSING_PROVIDER_TIME",
                    "detail": {"message_type": "QUOTE"}}})
                return
            event = {"kind": "QUOTE", "quote": {
                "instrument_id": symbol, "kind": "QUOTE", "provider_time": provider_time,
                "received_at": received, "source_timezone": "UTC_UNIX_MS",
                "market_session": "UNKNOWN", "delay_status": "UNKNOWN",
                "payload": {"price": data.get("last_price"), "provider_data": data,
                            "provider_time_field": "data_time_ms"}}}
            self._append(event)
        elif kind == "KLINE" and (symbol, "KLINE_1M") in enabled:
            if data.get("period") != "1m" or data.get("adjust") != "none":
                return
            for bar in data.get("kl_list", []):
                if not isinstance(bar, dict):
                    continue
                provider_time = _millis_utc(bar.get("time_ms"))
                if provider_time is None:
                    self._append({"kind": "GAP", "gap": {"stream_key": symbol,
                        "reason": "KLINE_MISSING_PROVIDER_TIME",
                        "detail": {"message_type": "KLINE", "period": "1m"}}})
                    continue
                # The published push schema does not specify whether time_ms is
                # the bar open or close. Do not fabricate a completed BAR_1M.
                event = {"kind": "QUOTE", "quote": {
                    "instrument_id": symbol, "kind": "KLINE_1M_UNBOUND",
                    "provider_time": provider_time, "received_at": received,
                    "source_timezone": "UTC_UNIX_MS", "market_session": "UNKNOWN",
                    "delay_status": "UNKNOWN",
                    "payload": {"price": bar.get("close_price"), "provider_data": bar,
                                "provider_time_field": "time_ms",
                                "period": "1m", "adjust": "none",
                                "bar_boundary_status": "UNCONFIRMED_CURRENT_KLINE"}}}
                self._append(event)

    def _flush_pending_raw(self) -> None:
        while self._pending_raw:
            self._enqueue_push(self._pending_raw.popleft())

    def _append(self, event: dict[str, Any]) -> None:
        if len(self._queue) >= MAX_QUEUE:
            # Fail closed: a dropped push creates an explicit coverage gap.
            self._queue.clear()
            self._queue.append({"kind": "GAP", "gap": {
                "stream_key": "push", "reason": "IN_MEMORY_QUEUE_OVERFLOW",
                "detail": {"max_queue": MAX_QUEUE}}})
        else:
            self._queue.append(event)

    def _refresh_if_due(self) -> None:
        if time.monotonic() - self._last_refresh < 300:
            return
        try:
            token = self.token_store.access_token()
        except OAuthTransientError as exc:
            raise ConnectionError(f"token refresh unavailable: {exc}") from None
        except OAuthError as exc:
            raise AuthorizationError(str(exc)) from None
        request_id = uuid.uuid4().hex
        self._send({"id": request_id, "action": "refresh",
                    "data": {"authorization": "Bearer " + token}})
        response = self._response(request_id)
        if response.get("code") not in (None, 0) or not response.get("session_id"):
            raise AuthorizationError("WebSocket channel refresh rejected")
        self._last_refresh = time.monotonic()
        self._receipt(note="channel refreshed")

    def next_message(self, timeout_seconds: float) -> dict[str, Any] | None:
        if self._ws is None:
            raise ConnectionError("WebSocket is not connected")
        if self._queue:
            return self._queue.popleft()
        self._refresh_if_due()
        if time.monotonic() - self._last_ping >= 30:
            try:
                self._ws.ping()
                self._last_ping = time.monotonic()
            except Exception as exc:
                raise ConnectionError(f"WebSocket protocol ping failed: {type(exc).__name__}") from None
        self._ws.settimeout(timeout_seconds)
        try:
            raw = self._ws.recv()
        except Exception as exc:
            if type(exc).__name__ == "WebSocketTimeoutException":
                return None
            raise ConnectionError(f"WebSocket receive failed: {type(exc).__name__}") from None
        try:
            message = json.loads(raw)
        except (ValueError, TypeError):
            return None
        if isinstance(message, dict):
            self._enqueue_push(message)
        return self._queue.popleft() if self._queue else None

    def close(self) -> None:
        if self._ws is not None:
            if self._subscriptions:
                request_id = uuid.uuid4().hex
                quote = [s["provider_code"] for s in self._subscriptions if s["kind"] == "QUOTE"]
                kline = [{"symbol": s["provider_code"], "period": "1m", "adjust": "none"}
                         for s in self._subscriptions if s["kind"] == "KLINE_1M"]
                request: dict[str, Any] = {"id": request_id, "action": "unsubscribe"}
                if quote:
                    request["quote"] = quote
                if kline:
                    request["kline"] = kline
                try:
                    self._send(request)
                    response = self._response(request_id, timeout_seconds=3)
                    self._release_state = "ACKNOWLEDGED" if response.get("code") == 0 else "UNCONFIRMED"
                except Exception:
                    self._release_state = "UNCONFIRMED"
                self._subscriptions = []
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
        self.connection_state = "DISCONNECTED"
        self._receipt()
