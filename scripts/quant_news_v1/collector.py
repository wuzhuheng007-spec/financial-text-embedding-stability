"""Single-process news collector for ONE configured provider.

python -m scripts.quant_news_v1.collector run [--provider P] [--max-seconds N]
Provider and credentials come from the environment (NEWS_PROVIDER, BENZINGA_TOKEN / MASSIVE_API_KEY /
ALPACA_KEY_ID + ALPACA_SECRET_KEY), set from the server's private secret file. One process holds an
exclusive lock, so the provider sees one connection; the web app only reads the cache.

States written to provider_state: NOT_CONFIGURED, CONNECTING, CONNECTED_NO_MESSAGES_YET (connected or
polling successfully, no news observed yet; not a failure and not an acceptance), RECEIVING,
AUTH_FAILED, FORBIDDEN, RATE_LIMITED, NETWORK_DNS, NETWORK_TLS, NETWORK_TIMEOUT, NETWORK_OTHER,
HTTP_ERROR, PROTOCOL_ERROR, CONNECTION_LIMIT, STOPPED. Backoff is bounded (5 s .. 300 s; 15 min after
auth/permission errors).
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time

from . import store
from .adapters import AlpacaRest, ProviderError, build


class Stop(Exception):
    pass


def _lock():
    path = store.db_path().parent / "collector.lock"; path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "w")
    try:
        import fcntl
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except ImportError:
        pass  # non-POSIX dev machine
    except OSError:
        raise SystemExit("another news collector holds the lock; refusing to open a second provider connection")
    return fh


def _apply_all(db, provider_name, items, via, online=True) -> dict:
    c = {"messages": 0, "news_new": 0, "news_versions": 0, "duplicates": 0, "tombstones": 0}
    for n in items:
        c["messages"] += 1
        if store.seen_message(db, provider_name, n.get("message_id")):
            c["duplicates"] += 1; continue
        r = store.apply(db, n, provider=provider_name, via=via, received_at=store.utcnow(), online=online)
        c[{"NEW": "news_new", "NEW_VERSION": "news_versions", "DUPLICATE": "duplicates", "TOMBSTONE": "tombstones"}[r]] += 1
    return c


def run(provider_name: str, max_seconds: float | None = None) -> int:
    db = store.connect(); lock = _lock()  # held for the process lifetime
    try:
        return _run(db, provider_name, max_seconds)
    finally:
        db.close(); lock.close()


def _run(db, provider_name: str, max_seconds: float | None) -> int:
    env = dict(os.environ)
    deadline = time.monotonic() + max_seconds if max_seconds else None

    def on_term(*_):
        raise Stop()
    signal.signal(signal.SIGTERM, on_term)
    try:
        provider = build(provider_name, env)
    except ProviderError as e:
        store.set_state(db, provider_name, e.error_class, detail=e.detail, error_class=e.error_class); print(e); return 2
    backoff, had_connection = 5.0, False
    try:
        while deadline is None or time.monotonic() < deadline:
            try:
                store.set_state(db, provider_name, "CONNECTING", detail=provider.kind)
                if provider.kind == "REST_POLL":
                    _poll_loop(db, provider, provider_name, env, deadline)
                else:
                    _stream_loop(db, provider, provider_name, env, deadline, had_connection)
                break
            except ProviderError as e:
                store.set_state(db, provider_name, e.error_class, detail=e.detail, error_class=e.error_class, errors=1)
                wait = 900 if e.error_class in ("AUTH_FAILED", "FORBIDDEN") else (e.retry_after or backoff)
                print(f"{provider_name} {e.error_class}: {e.detail}; retry in {int(wait)}s", flush=True)
                had_connection = True
                _sleep(min(wait, 900), deadline); backoff = min(backoff * 2, 300)
    except Stop:
        pass
    store.set_state(db, provider_name, "STOPPED", detail="collector stopped")
    return 0


def _sleep(seconds, deadline):
    end = time.monotonic() + seconds
    while time.monotonic() < end and (deadline is None or time.monotonic() < deadline):
        time.sleep(min(1.0, end - time.monotonic()))


def _state_after(c, db, provider_name, kind):
    cur = db.execute("SELECT state FROM provider_state WHERE provider=?", (provider_name,)).fetchone()
    got_news = c["news_new"] or c["news_versions"] or (cur and cur[0] == "RECEIVING")
    store.set_state(db, provider_name, "RECEIVING" if got_news else "CONNECTED_NO_MESSAGES_YET", detail=kind, **c)


def _poll_loop(db, provider, provider_name, env, deadline):
    every = max(15, int(env.get("NEWS_POLL_SECONDS", "60")))
    last = db.execute("SELECT MAX(provider_created_at) FROM versions WHERE provider=?", (provider_name,)).fetchone()[0]
    first = last is None
    while deadline is None or time.monotonic() < deadline:
        since = None if first else _minus_minutes(last, 15)  # overlap window catches late updates; duplicates are free
        items = provider.fetch(since_utc=since, limit=100)
        c = _apply_all(db, provider_name, items, via="backfill" if first else "rest_poll", online=not first)
        _state_after(c, db, provider_name, "REST_POLL (polling, not push)")
        first = False
        last = db.execute("SELECT MAX(provider_created_at) FROM versions WHERE provider=?", (provider_name,)).fetchone()[0] or last
        _sleep(every, deadline)


def _stream_loop(db, provider, provider_name, env, deadline, reconnect):
    import websocket
    ws = provider.connect()
    store.set_state(db, provider_name, "CONNECTED_NO_MESSAGES_YET", detail=provider.kind, connects=1)
    try:
        if reconnect and provider.replay_text:      # Benzinga: up to 100 cached messages after a gap
            ws.send(provider.replay_text)
        elif reconnect and provider_name == "alpaca_stream":
            last = db.execute("SELECT MAX(provider_created_at) FROM versions WHERE provider=?", (provider_name,)).fetchone()[0]
            if last:
                rest = AlpacaRest(env.get("ALPACA_KEY_ID", ""), env.get("ALPACA_SECRET_KEY", ""))
                c = _apply_all(db, provider_name, rest.fetch(since_utc=_minus_minutes(last, 15)), via="gap_fill_rest", online=False)
                _state_after(c, db, provider_name, provider.kind)
        ws.settimeout(30)
        while deadline is None or time.monotonic() < deadline:
            try:
                text = ws.recv()
            except websocket.WebSocketTimeoutException:
                if provider.ping_text:
                    ws.send(provider.ping_text)
                _state_after({"messages": 0, "news_new": 0, "news_versions": 0, "duplicates": 0, "tombstones": 0}, db, provider_name, provider.kind)
                continue
            except websocket.WebSocketConnectionClosedException:
                raise ProviderError("NETWORK_OTHER", "connection closed by server")
            if not text:
                continue
            try:
                items = provider.parse(text)
            except ValueError:
                store.set_state(db, provider_name, "PROTOCOL_ERROR", detail="unparseable message", error_class="PROTOCOL_ERROR", bad_messages=1)
                continue
            _state_after(_apply_all(db, provider_name, items, via="stream"), db, provider_name, provider.kind)
    finally:
        try:
            ws.close()
        except Exception:  # noqa: BLE001
            pass


def _minus_minutes(iso, minutes):
    from datetime import datetime, timedelta, timezone
    if not iso:
        return None
    return (datetime.fromisoformat(iso.replace("Z", "+00:00")) - timedelta(minutes=minutes)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--provider", default=os.environ.get("NEWS_PROVIDER", "")); r.add_argument("--max-seconds", type=float)
    a = ap.parse_args(argv)
    if not a.provider:
        db = store.connect(); store.set_state(db, "unconfigured", "NOT_CONFIGURED", detail="NEWS_PROVIDER not set"); print("NEWS_PROVIDER not set"); return 2
    return run(a.provider, a.max_seconds)


if __name__ == "__main__":
    sys.exit(main())
