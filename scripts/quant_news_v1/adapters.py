"""Provider adapters: connection + normalization. Field names follow the official docs (2026-11-19):

Benzinga News Stream  wss://api.benzinga.com/api/v1/news/stream?token=...  message {id, api_version, kind,
  data:{action created|updated|deleted, id, timestamp, content:{id, author, created, updated (RFC 2822),
  title, teaser, body, url, channels[{name}], stocks[{name,...}], tags[{name}]}}}; text "ping" -> "pong";
  "replay" -> up to 100 cached messages.
Massive Benzinga News  GET https://api.massive.com/benzinga/v2/news  results[{benzinga_id, title, teaser, body,
  url, tickers[], channels[], tags[], author, published, last_updated}], next_url. REST polling, not push.
Alpaca News  wss://stream.data.alpaca.markets/v1beta1/news (auth headers), subscribe {"action":"subscribe",
  "news":["*"]}; arrays of {T:"n", id, headline, summary, author, created_at, updated_at, url, content,
  symbols[], source}. REST GET https://data.alpaca.markets/v1beta1/news for gap fill.
Secrets are never logged; URLs that carry a token are never logged.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


class ProviderError(Exception):
    """Classified failure: AUTH_FAILED, FORBIDDEN, RATE_LIMITED, NETWORK_DNS, NETWORK_TLS, NETWORK_TIMEOUT,
    NETWORK_OTHER, HTTP_ERROR, PROTOCOL_ERROR, NOT_CONFIGURED."""

    def __init__(self, error_class: str, detail: str = "", retry_after: float | None = None):
        super().__init__(f"{error_class}: {detail}")
        self.error_class, self.detail, self.retry_after = error_class, detail[:300], retry_after


def to_utc(value) -> str | None:
    """ISO 8601 / RFC 3339 / RFC 2822 / epoch seconds -> UTC ISO; None if unparseable."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            dt = datetime.fromtimestamp(float(value), timezone.utc)
        elif "," in str(value) and not str(value)[:4].isdigit():
            dt = parsedate_to_datetime(str(value))
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return None
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None


def classify_exception(exc: BaseException) -> ProviderError:
    import socket, ssl
    if isinstance(exc, ProviderError):
        return exc
    status = getattr(exc, "code", None) or getattr(getattr(exc, "resp", None), "status", None) or getattr(exc, "status_code", None)
    if status in (401,):
        return ProviderError("AUTH_FAILED", f"HTTP {status}")
    if status in (402, 403):
        return ProviderError("FORBIDDEN", f"HTTP {status} (plan or permission)")
    if status == 429:
        ra = None
        try:
            ra = float(exc.headers.get("Retry-After")) if getattr(exc, "headers", None) else None
        except (TypeError, ValueError):
            ra = None
        return ProviderError("RATE_LIMITED", "HTTP 429", ra)
    if isinstance(status, int):
        return ProviderError("HTTP_ERROR", f"HTTP {status}")
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, socket.gaierror) or isinstance(exc, socket.gaierror):
        return ProviderError("NETWORK_DNS", type(reason).__name__)
    if isinstance(reason, ssl.SSLError) or isinstance(exc, ssl.SSLError):
        return ProviderError("NETWORK_TLS", type(reason).__name__)
    if isinstance(reason, (TimeoutError, socket.timeout)) or isinstance(exc, (TimeoutError, socket.timeout)):
        return ProviderError("NETWORK_TIMEOUT", "timeout")
    return ProviderError("NETWORK_OTHER", type(exc).__name__)


# ---------- normalization (pure functions; covered by fixture tests) ----------
def normalize_benzinga(msg: dict) -> dict:
    data = msg.get("data") or {}
    c = data.get("content") or {}
    action = str(data.get("action", "")).lower()
    return {"upstream": "benzinga", "upstream_id": str(data.get("id") or c.get("id")), "message_id": msg.get("id"),
            "action": {"created": "created", "updated": "updated", "deleted": "deleted", "removed": "deleted"}.get(action, action or "created"),
            "title": c.get("title"), "teaser": c.get("teaser") or None, "body": c.get("body") or None, "url": c.get("url"),
            "source": "Benzinga", "authors": c.get("author"),
            "symbols": sorted({s.get("name") for s in c.get("stocks") or [] if s.get("name")}),
            "channels": [x.get("name") for x in c.get("channels") or [] if x.get("name")],
            "provider_created_at": to_utc(c.get("created")), "provider_updated_at": to_utc(c.get("updated") or data.get("timestamp")),
            "raw": msg}


def normalize_massive(r: dict) -> dict:
    return {"upstream": "benzinga", "upstream_id": str(r.get("benzinga_id")), "message_id": None, "action": "created",
            "title": r.get("title"), "teaser": r.get("teaser") or None, "body": r.get("body") or None, "url": r.get("url"),
            "source": "Benzinga (via Massive)", "authors": r.get("author"), "symbols": sorted(set(r.get("tickers") or [])),
            "channels": list(r.get("channels") or []), "provider_created_at": to_utc(r.get("published")),
            "provider_updated_at": to_utc(r.get("last_updated")), "raw": r}


def normalize_alpaca(m: dict) -> dict:
    # Alpaca's upstream changed in Nov 2026; keep its own id space and report its `source` as given.
    return {"upstream": "alpaca", "upstream_id": str(m.get("id")), "message_id": None, "action": "created",
            "title": m.get("headline"), "teaser": m.get("summary") or None, "body": m.get("content") or None, "url": m.get("url"),
            "source": m.get("source") or "unknown (Alpaca)", "authors": m.get("author") or None, "symbols": sorted(set(m.get("symbols") or [])),
            "channels": [], "provider_created_at": to_utc(m.get("created_at")), "provider_updated_at": to_utc(m.get("updated_at")), "raw": m}


# ---------- REST helpers ----------
def http_json(url: str, headers: dict, timeout: float = 20.0) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "quant-news/1", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - classified, never echoed with the URL (may carry a key)
        raise classify_exception(exc) from None


class MassiveRest:
    name, kind = "massive_benzinga", "REST_POLL"
    BASE = "https://api.massive.com/benzinga/v2/news"

    def __init__(self, api_key: str):
        if not api_key:
            raise ProviderError("NOT_CONFIGURED", "MASSIVE_API_KEY missing")
        self.headers = {"Authorization": f"Bearer {api_key}"}

    def fetch(self, since_utc: str | None = None, limit: int = 100, tickers: list[str] | None = None) -> list[dict]:
        q = {"sort": "published.desc", "limit": str(limit)}
        if since_utc:
            q["published.gte"] = since_utc
        if tickers:
            q["tickers.any_of"] = ",".join(tickers)
        return [normalize_massive(r) for r in http_json(f"{self.BASE}?{urllib.parse.urlencode(q)}", self.headers).get("results") or []]


class AlpacaRest:
    BASE = "https://data.alpaca.markets/v1beta1/news"

    def __init__(self, key_id: str, secret: str):
        if not (key_id and secret):
            raise ProviderError("NOT_CONFIGURED", "ALPACA_KEY_ID/ALPACA_SECRET_KEY missing")
        self.headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret}

    def fetch(self, since_utc: str | None = None, limit: int = 50, tickers: list[str] | None = None) -> list[dict]:
        q = {"sort": "desc", "limit": str(limit), "include_content": "true"}
        if since_utc:
            q["start"] = since_utc
        if tickers:
            q["symbols"] = ",".join(tickers)
        return [normalize_alpaca(m) for m in http_json(f"{self.BASE}?{urllib.parse.urlencode(q)}", self.headers).get("news") or []]


# ---------- streams ----------
class BenzingaStream:
    name, kind = "benzinga_stream", "WEBSOCKET_PUSH"
    URL = "wss://api.benzinga.com/api/v1/news/stream"

    def __init__(self, token: str, tickers: list[str] | None = None):
        if not token:
            raise ProviderError("NOT_CONFIGURED", "BENZINGA_TOKEN missing")
        self.token, self.tickers = token, tickers

    def connect(self, timeout: float = 20.0):
        import websocket
        q = {"token": self.token}
        if self.tickers:
            q["tickers"] = ",".join(self.tickers)
        try:
            ws = websocket.create_connection(f"{self.URL}?{urllib.parse.urlencode(q)}", timeout=timeout)
        except websocket.WebSocketBadStatusException as exc:
            raise classify_exception(type("E", (Exception,), {"code": exc.status_code})()) from None
        except Exception as exc:  # noqa: BLE001
            raise classify_exception(exc) from None
        return ws

    @staticmethod
    def parse(text: str) -> list[dict]:
        if text.strip().lower() == "pong":
            return []
        msg = json.loads(text)
        return [normalize_benzinga(msg)] if isinstance(msg, dict) and msg.get("data") else []

    ping_text, replay_text = "ping", "replay"


class AlpacaStream:
    name, kind = "alpaca_stream", "WEBSOCKET_PUSH"
    URL = "wss://stream.data.alpaca.markets/v1beta1/news"

    def __init__(self, key_id: str, secret: str):
        if not (key_id and secret):
            raise ProviderError("NOT_CONFIGURED", "ALPACA_KEY_ID/ALPACA_SECRET_KEY missing")
        self.key_id, self.secret = key_id, secret

    def connect(self, timeout: float = 20.0):
        import websocket
        try:
            ws = websocket.create_connection(self.URL, timeout=timeout,
                                             header=[f"APCA-API-KEY-ID: {self.key_id}", f"APCA-API-SECRET-KEY: {self.secret}"])
        except websocket.WebSocketBadStatusException as exc:
            raise classify_exception(type("E", (Exception,), {"code": exc.status_code})()) from None
        except Exception as exc:  # noqa: BLE001
            raise classify_exception(exc) from None
        ws.send(json.dumps({"action": "subscribe", "news": ["*"]}))
        return ws

    @staticmethod
    def parse(text: str) -> list[dict]:
        out = []
        for m in json.loads(text):
            t = m.get("T")
            if t == "error":
                code = m.get("code")
                cls = {401: "AUTH_FAILED", 402: "AUTH_FAILED", 403: "FORBIDDEN", 406: "CONNECTION_LIMIT", 409: "FORBIDDEN", 429: "RATE_LIMITED"}.get(code, "PROTOCOL_ERROR")
                raise ProviderError(cls, f"stream error {code}: {m.get('msg')}")
            if t == "n":
                out.append(normalize_alpaca(m))
        return out

    ping_text, replay_text = None, None


def build(provider: str, env: dict):
    """Provider factory from environment variables (values never logged)."""
    if provider == "benzinga_stream":
        return BenzingaStream(env.get("BENZINGA_TOKEN", ""), [t for t in env.get("NEWS_TICKERS", "").split(",") if t] or None)
    if provider == "massive_benzinga":
        return MassiveRest(env.get("MASSIVE_API_KEY", ""))
    if provider == "alpaca_stream":
        return AlpacaStream(env.get("ALPACA_KEY_ID", ""), env.get("ALPACA_SECRET_KEY", ""))
    raise ProviderError("NOT_CONFIGURED", f"unknown provider {provider!r}")
