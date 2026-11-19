"""Single-request Benzinga checks with evidence kept separate (no retries, no replay, prints redacted JSON).

python -m scripts.quant_news_v1.probe_once rest   # GET /api/v2/news?pageSize=1&displayOutput=headline, token=BENZINGA_TOKEN
python -m scripts.quant_news_v1.probe_once ws [--seconds 45]   # stream with BENZINGA_WS_TOKEN, no replay

The WebSocket report keeps four things apart, because a successful handshake is NOT proof of authentication
(Benzinga sends no auth acknowledgement; a bad token can still get HTTP 101 and then be closed):
  transport   - handshake HTTP status and whitelisted response headers (request ids)
  auth        - EVIDENCED only if a news frame arrived; REJECTED_BY_SERVER if the server closed with a code;
                UNPROVEN if the window ended quietly (that is not a failure)
  news        - number of news frames; none = "本窗口未观察到新闻" (never "key invalid")
  close       - server close code and redacted reason, or "closed without close frame"
Every exit path closes the socket. HTTP 429 stops immediately (no retry).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .adapters import BenzingaStream, classify_exception, redact, register_secret

SAFE_HEADERS = ("date", "server", "content-type", "x-request-id", "request-id", "x-amzn-requestid", "x-amz-cf-id", "cf-ray",
                "x-trace-id", "x-correlation-id", "retry-after", "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset")
REST_URL = "https://api.benzinga.com/api/v2/news"
WS_URL = "wss://api.benzinga.com/api/v1/news/stream"


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_headers(h) -> dict:
    items = h.items() if hasattr(h, "items") else []
    return {k.lower(): redact(v)[:120] for k, v in items if k.lower() in SAFE_HEADERS}


def _structure(d) -> dict:
    if isinstance(d, list):
        return {"type": "list", "length": len(d), "item_keys": sorted(d[0].keys())[:20] if d and isinstance(d[0], dict) else None}
    if isinstance(d, dict):
        return {"type": "object", "keys": sorted(d.keys())[:20]}
    return {"type": type(d).__name__}


def rest_once(env: dict, opener=urllib.request.urlopen) -> dict:
    tok = env.get("BENZINGA_TOKEN", "")
    out = {"check": "rest_once", "at_utc": _now(), "endpoint": "GET " + REST_URL,
           "params": {"pageSize": "1", "displayOutput": "headline", "token": "BENZINGA_TOKEN (redacted)"}}
    if not tok:
        return {**out, "result": "NOT_CONFIGURED"}
    register_secret(tok)
    url = REST_URL + "?" + urllib.parse.urlencode({"token": tok, "pageSize": "1", "displayOutput": "headline"})
    try:
        with opener(urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "quant-news/1"}), timeout=20) as r:
            status, headers, body = r.status, r.headers, r.read()
    except Exception as exc:  # noqa: BLE001 - HTTPError carries status/headers/body; everything else is network
        e = classify_exception(exc)
        out.update(result=e.error_class, http_status=getattr(exc, "code", None), detail=e.detail,
                   headers=_safe_headers(getattr(exc, "headers", None) or {}))
        if e.error_class == "RATE_LIMITED":
            out["stopped"] = "HTTP 429: stopped, not retried"
        return out
    out.update(http_status=status, headers=_safe_headers(headers))
    try:
        data = json.loads(body.decode("utf-8"))
    except ValueError:
        return {**out, "result": "UNPARSEABLE_BODY", "body_prefix": redact(body[:120].decode("utf-8", "replace"))}
    out["structure"] = _structure(data)
    if isinstance(data, list) and not data:
        out["result"] = "AUTH_OK_NO_NEWS_SAMPLE"; out["note"] = "没有新闻样本（空列表）"
    elif isinstance(data, list):
        it = data[0]
        out["result"] = "AUTH_OK_NEWS_SAMPLE"
        out["sample"] = {"id": it.get("id"), "created": it.get("created"), "updated": it.get("updated"),
                         "title": (it.get("title") or "")[:100], "url_host": (urllib.parse.urlparse(it.get("url") or "").hostname),
                         "stocks": [s.get("name") if isinstance(s, dict) else s for s in (it.get("stocks") or [])][:8]}
    else:
        out["result"] = "UNEXPECTED_STRUCTURE"; out["body_prefix"] = redact(json.dumps(data)[:200])
    return out


def _collector_running() -> bool:
    """True if the long-running collector holds its lock (it owns the one allowed stream connection)."""
    try:
        import fcntl
        from .store import db_path
        p = db_path().parent / "collector.lock"
        if not p.exists():
            return False
        with open(p, "a") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return True
            fcntl.flock(fh, fcntl.LOCK_UN)
    except (ImportError, OSError):
        return False
    return False


def ws_once(env: dict, seconds: float = 45, ws_factory=None, collector_running=_collector_running) -> dict:
    import websocket
    tok = env.get("BENZINGA_WS_TOKEN") or env.get("BENZINGA_TOKEN", "")
    out = {"check": "ws_once", "at_utc": _now(), "endpoint": WS_URL, "token_var": "BENZINGA_WS_TOKEN" if env.get("BENZINGA_WS_TOKEN") else "BENZINGA_TOKEN",
           "replay_sent": False, "window_seconds": seconds,
           "transport": {}, "auth": "UNPROVEN", "news": {"frames": 0}, "close": None}
    if not tok:
        return {**out, "result": "NOT_CONFIGURED"}
    if collector_running():
        return {**out, "result": "SKIPPED_COLLECTOR_HOLDS_THE_STREAM", "note": "one connection per key: stop the collector first"}
    register_secret(tok)
    ws = (ws_factory or websocket.WebSocket)()
    try:
        try:
            ws.connect(WS_URL + "?" + urllib.parse.urlencode({"token": tok}), timeout=20)
        except websocket.WebSocketBadStatusException as exc:
            code = exc.status_code
            body = getattr(exc, "resp_body", b"") or b""
            out["transport"] = {"handshake": "FAILED", "http_status": code, "headers": _safe_headers(getattr(exc, "resp_headers", None) or {}),
                                "body": redact(body.decode("utf-8", "replace") if isinstance(body, bytes) else body)[:200]}
            out["auth"] = "REJECTED_AT_HANDSHAKE" if code in (401, 403) else "UNPROVEN"
            out["result"] = "RATE_LIMITED_STOPPED" if code == 429 else f"HANDSHAKE_HTTP_{code}"
            return out
        except Exception as exc:  # noqa: BLE001
            out["transport"] = {"handshake": "FAILED", "error": classify_exception(exc).error_class, "detail": redact(f"{type(exc).__name__}: {exc}")[:200]}
            out["result"] = "TRANSPORT_FAILED"
            return out
        out["transport"] = {"handshake": "OK", "http_status": ws.getstatus(), "headers": _safe_headers(ws.getheaders() or {}),
                            "connected_at_utc": _now()}
        end = time.monotonic() + seconds
        frames, news, other = 0, 0, []
        while True:
            left = end - time.monotonic()
            if left <= 0:
                break
            ws.settimeout(min(left, 30))
            try:
                op, frame = ws.recv_data_frame(True)
            except websocket.WebSocketTimeoutException:
                if time.monotonic() < end:
                    ws.send("ping")  # keepalive text command per docs; not a replay
                continue
            except websocket.WebSocketConnectionClosedException:
                out["close"] = {"by": "server", "code": None, "reason": "closed without close frame"}
                break
            if op == websocket.ABNF.OPCODE_CLOSE:
                data = frame.data or b""
                code = int.from_bytes(data[:2], "big") if len(data) >= 2 else None
                out["close"] = {"by": "server", "code": code, "reason": redact(data[2:].decode("utf-8", "replace"))[:200]}
                break
            if op != websocket.ABNF.OPCODE_TEXT:
                continue
            frames += 1
            text = frame.data.decode("utf-8", "replace") if isinstance(frame.data, bytes) else frame.data
            if text.strip().lower() == "pong":
                continue
            try:
                got = BenzingaStream.parse(text)
            except ValueError as exc:
                other.append(redact(str(exc) if str(exc).startswith("unexpected") else text)[:200]); continue
            news += len(got)
        out["news"] = {"frames": frames, "news_messages": news, "non_news_sample": other[:3],
                       "observed": "NEWS_RECEIVED" if news else "本窗口未观察到新闻（不能据此判定密钥无效）"}
        if news:
            out["auth"] = "EVIDENCED_BY_NEWS_FRAME"
        elif out["close"] and out["close"]["code"] is not None:
            c = out["close"]; authish = any(w in (c["reason"] or "").lower() for w in ("auth", "token", "unauthor", "forbidden", "denied"))
            out["auth"] = f"REJECTED_BY_SERVER_CLOSE_{c['code']}" if c["code"] >= 4000 or c["code"] == 1008 or authish else "UNPROVEN"
        out["result"] = "NEWS_RECEIVED" if news else ("SERVER_CLOSED" if out["close"] else "CONNECTED_NO_NEWS_IN_WINDOW")
        return out
    finally:
        try:
            ws.close()
        except Exception:  # noqa: BLE001
            pass
        out["socket_closed"] = True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("check", choices=["rest", "ws"]); ap.add_argument("--seconds", type=float, default=45)
    a = ap.parse_args(argv)
    res = rest_once(dict(os.environ)) if a.check == "rest" else ws_once(dict(os.environ), min(a.seconds, 45))
    print(redact(json.dumps(res, ensure_ascii=False, indent=1)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
