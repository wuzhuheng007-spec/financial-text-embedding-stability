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
Benzinga News REST  GET https://api.benzinga.com/api/v2/news?token=...  JSON array of {id, author, created,
  updated (RFC 2822 with offset), title, teaser, body, url, channels[{name}], stocks[{name}], tags[{name}]};
  params pageSize<=100, displayOutput full|abstract|headline, updatedSince/publishedSince (epoch), tickers,
  channels, sort. 401 = token invalid; 404 no_data_found = empty result, not an error.
Secrets are never logged: every error detail passes through redact(), which masks registered secret values
and any token=/apikey= query value, so an exception that carries a request URL cannot leak a key.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


_SECRETS: set[str] = set()
_QS_SECRET = re.compile(r"(?i)((?:token|apikey|api_key|key)=)[^&\s'\"]+")


def register_secret(value: str | None) -> None:
    if value and len(value) >= 6:
        _SECRETS.add(value)


def redact(text) -> str:
    out = str(text)
    for sec in sorted(_SECRETS, key=len, reverse=True):
        out = out.replace(sec, "***")
        q = urllib.parse.quote(sec, safe="")
        if q != sec:
            out = out.replace(q, "***")
    return _QS_SECRET.sub(r"\1***", out)


class ProviderError(Exception):
    """Classified failure: AUTH_FAILED, FORBIDDEN, RATE_LIMITED, NETWORK_DNS, NETWORK_TLS, NETWORK_TIMEOUT,
    NETWORK_OTHER, HTTP_ERROR, PROTOCOL_ERROR, NOT_CONFIGURED."""

    def __init__(self, error_class: str, detail: str = "", retry_after: float | None = None):
        detail = redact(detail)[:300]
        super().__init__(f"{error_class}: {detail}")
        self.error_class, self.detail, self.retry_after = error_class, detail, retry_after


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
    body = ""
    try:  # the provider's own error text (redacted, short) is what tells us which product is missing
        raw = exc.read() if hasattr(exc, "read") else getattr(exc, "resp_body", b"") or b""
        body = " body=" + redact(raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw)[:200] if raw else ""
    except Exception:  # noqa: BLE001
        body = ""
    if status in (401,):
        return ProviderError("AUTH_FAILED", f"HTTP {status}{body}")
    if status in (402, 403):
        return ProviderError("FORBIDDEN", f"HTTP {status} (plan or permission){body}")
    if status == 429:
        ra = None
        try:
            ra = float(exc.headers.get("Retry-After")) if getattr(exc, "headers", None) else None
        except (TypeError, ValueError):
            ra = None
        return ProviderError("RATE_LIMITED", "HTTP 429", ra)
    if isinstance(status, int):
        return ProviderError("HTTP_ERROR", f"HTTP {status}{body}")
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, socket.gaierror) or isinstance(exc, socket.gaierror):
        return ProviderError("NETWORK_DNS", type(reason).__name__)
    if isinstance(reason, ssl.SSLError) or isinstance(exc, ssl.SSLError):
        return ProviderError("NETWORK_TLS", type(reason).__name__)
    if isinstance(reason, (TimeoutError, socket.timeout)) or isinstance(exc, (TimeoutError, socket.timeout)):
        return ProviderError("NETWORK_TIMEOUT", "timeout")
    return ProviderError("NETWORK_OTHER", type(exc).__name__)


# ---------- normalization (pure functions; covered by fixture tests) ----------
def _names(lst) -> list[str]:
    """Benzinga lists come as [{"name": ...}] or as plain strings; accept both."""
    out = []
    for x in lst or []:
        v = x.get("name") if isinstance(x, dict) else x if isinstance(x, str) else None
        if v:
            out.append(str(v))
    return out


def shape(v, depth=0):
    """Structure only (keys and value types), for diagnosing unexpected frames without logging content."""
    if isinstance(v, dict):
        return {k: shape(x, depth + 1) for k, x in list(v.items())[:25]} if depth < 3 else "dict"
    if isinstance(v, list):
        return [shape(v[0], depth + 1)] if v else []
    return type(v).__name__


def normalize_benzinga(msg: dict) -> dict:
    data = msg.get("data") or {}
    if not isinstance(data, dict):
        raise ValueError(f"unexpected data type {type(data).__name__}")
    c = data.get("content") or {}
    if not isinstance(c, dict):
        raise ValueError(f"unexpected content type {type(c).__name__}")
    action = str(data.get("action", "")).lower()
    return {"upstream": "benzinga", "upstream_id": str(data.get("id") or c.get("id")), "message_id": msg.get("id"),
            "action": {"created": "created", "updated": "updated", "deleted": "deleted", "removed": "deleted"}.get(action, action or "created"),
            "title": c.get("title"), "teaser": c.get("teaser") or None, "body": c.get("body") or None, "url": c.get("url"),
            "source": "Benzinga", "authors": c.get("author"),
            "symbols": sorted(set(_names(c.get("stocks")))),
            "channels": _names(c.get("channels")),
            "provider_created_at": to_utc(c.get("created")),
            # a deletion's own time is the event timestamp; content.updated is the article's last edit
            "provider_updated_at": (to_utc(data.get("timestamp")) or to_utc(c.get("updated"))) if action in ("deleted", "removed")
                                   else (to_utc(c.get("updated")) or to_utc(data.get("timestamp"))),
            "event_at": to_utc(data.get("timestamp")),  # when the provider emitted this stream event
            "raw": msg}


def normalize_benzinga_rest(r: dict) -> dict:
    """REST items have the same shape as the stream's data.content; the article id is the same id space."""
    return normalize_benzinga({"id": None, "data": {"action": "created", "id": r.get("id"), "content": r}}) | {"raw": r}


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
def http_json(url: str, headers: dict, timeout: float = 20.0):
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "quant-news/1", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - classified, never echoed with the URL (may carry a key)
        raise classify_exception(exc) from None


class BenzingaRest:
    name, kind = "benzinga_rest", "REST_POLL"
    BASE = "https://api.benzinga.com/api/v2/news"

    def __init__(self, token: str):
        if not token:
            raise ProviderError("NOT_CONFIGURED", "BENZINGA_TOKEN missing")
        register_secret(token); self.token = token

    def fetch(self, since_utc: str | None = None, limit: int = 100, tickers: list[str] | None = None,
              channels: list[str] | None = None, display: str = "full") -> list[dict]:
        q = {"token": self.token, "pageSize": str(min(limit, 100)), "displayOutput": display, "sort": "updated:desc"}
        if since_utc:
            q["updatedSince"] = str(int(datetime.fromisoformat(since_utc.replace("Z", "+00:00")).timestamp()))
        if tickers:
            q["tickers"] = ",".join(tickers)
        if channels:
            q["channels"] = ",".join(channels)
        try:
            data = http_json(f"{self.BASE}?{urllib.parse.urlencode(q)}", {})
        except ProviderError as e:
            if e.detail.startswith("HTTP 404"):  # no_data_found: an empty window, not a failure
                return []
            raise
        if isinstance(data, dict):  # {"ok": false, "errors": [...]}
            raise ProviderError("HTTP_ERROR", f"error object: {json.dumps(data)[:200]}")
        return [normalize_benzinga_rest(r) for r in data or []]

    def _get(self, path: str, q: dict):
        try:
            return http_json(f"https://api.benzinga.com{path}?{urllib.parse.urlencode({'token': self.token, **q})}", {})
        except ProviderError as e:
            if e.detail.startswith("HTTP 404"):
                return []
            raise

    def updated_since(self, since_epoch: int, max_pages: int = 20, page_size: int = 100) -> tuple[list[dict], bool]:
        """Every article UPDATED (not only created) since since_epoch, oldest update first, paged.
        Returns (items, truncated): truncated=True when max_pages was reached before a short page."""
        out = []
        for page in range(max_pages):
            data = self._get("/api/v2/news", {"updatedSince": str(int(since_epoch)), "sort": "updated:asc", "displayOutput": "full",
                                              "pageSize": str(page_size), "page": str(page)})
            if isinstance(data, dict):
                raise ProviderError("HTTP_ERROR", f"error object: {json.dumps(data)[:200]}")
            out.extend(normalize_benzinga_rest(r) for r in data or [])
            if len(data or []) < page_size:
                return out, False
        return out, True

    def removed_since(self, since_epoch: int, max_pages: int = 10, page_size: int = 1000) -> tuple[list[dict], bool]:
        """GET /api/v2/news-removed: {id, updated=removal time}. The response object's arrays are flattened."""
        out = []
        for page in range(max_pages):
            data = self._get("/api/v2/news-removed", {"updatedSince": str(int(since_epoch)), "pageSize": str(page_size), "page": str(page)})
            rows = [r for v in (data.values() if isinstance(data, dict) else [data]) if isinstance(v, list) for r in v]
            out.extend({"upstream": "benzinga", "upstream_id": str(r.get("id")), "message_id": None, "action": "deleted",
                        "provider_updated_at": to_utc(r.get("updated")), "provider_created_at": None, "symbols": [], "channels": [],
                        "raw": r} for r in rows if r.get("id") is not None)
            if len(rows) < page_size:
                return out, False
        return out, True


class MassiveRest:
    name, kind = "massive_benzinga", "REST_POLL"
    BASE = "https://api.massive.com/benzinga/v2/news"

    def __init__(self, api_key: str):
        if not api_key:
            raise ProviderError("NOT_CONFIGURED", "MASSIVE_API_KEY missing")
        register_secret(api_key); self.headers = {"Authorization": f"Bearer {api_key}"}

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
        register_secret(secret); self.headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret}

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
            raise ProviderError("NOT_CONFIGURED", "BENZINGA_WS_TOKEN / BENZINGA_TOKEN missing")
        register_secret(token); self.token, self.tickers = token, tickers

    def connect(self, timeout: float = 20.0):
        import websocket
        q = {"token": self.token}
        if self.tickers:
            q["tickers"] = ",".join(self.tickers)
        try:
            ws = websocket.create_connection(f"{self.URL}?{urllib.parse.urlencode(q)}", timeout=timeout)
        except websocket.WebSocketBadStatusException as exc:
            raise classify_exception(type("E", (Exception,), {"code": exc.status_code, "resp_body": getattr(exc, "resp_body", b"")})()) from None
        except Exception as exc:  # noqa: BLE001
            raise classify_exception(exc) from None
        return ws

    @staticmethod
    def parse(text: str) -> list[dict]:
        if text.strip().lower() == "pong":
            return []
        msg = json.loads(text)
        msgs = msg if isinstance(msg, list) else [msg]
        out = []
        for m in msgs:
            if not (isinstance(m, dict) and m.get("data")):
                continue
            try:
                out.append(normalize_benzinga(m))
            except (AttributeError, TypeError, KeyError) as exc:
                raise ValueError(f"unexpected frame shape {json.dumps(shape(m))[:300]}") from None
        return out

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
    if provider == "benzinga_stream":  # the stream may use its own WebSocket token; default to the REST key
        return BenzingaStream(env.get("BENZINGA_WS_TOKEN") or env.get("BENZINGA_TOKEN", ""),
                              [t for t in env.get("NEWS_TICKERS", "").split(",") if t] or None)
    if provider == "benzinga_rest":
        return BenzingaRest(env.get("BENZINGA_TOKEN", ""))
    if provider == "massive_benzinga":
        return MassiveRest(env.get("MASSIVE_API_KEY", ""))
    if provider == "alpaca_stream":
        return AlpacaStream(env.get("ALPACA_KEY_ID", ""), env.get("ALPACA_SECRET_KEY", ""))
    raise ProviderError("NOT_CONFIGURED", f"unknown provider {provider!r}")
