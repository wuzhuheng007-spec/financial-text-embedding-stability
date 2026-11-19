"""Single-process news collector for ONE configured provider.

python -m scripts.quant_news_v1.collector run [--provider P] [--max-seconds N]
Provider and credentials come from the environment (NEWS_PROVIDER, BENZINGA_TOKEN / MASSIVE_API_KEY /
ALPACA_KEY_ID + ALPACA_SECRET_KEY), set from the server's private secret file. One process holds an
exclusive lock, so the provider sees one connection; the web app only reads the cache.

States written to provider_state: NOT_CONFIGURED, CONNECTING, CONNECTED_NO_MESSAGES_YET (connected or
polling successfully, no news observed yet; not a failure and not an acceptance), RECEIVING,
AUTH_FAILED, FORBIDDEN, RATE_LIMITED, NETWORK_DNS, NETWORK_TLS, NETWORK_TIMEOUT, NETWORK_OTHER,
HTTP_ERROR, PROTOCOL_ERROR, CONNECTION_LIMIT, STOPPED. Backoff is bounded (5 s .. 300 s). AUTH_FAILED and
FORBIDDEN are NOT retried: the state is recorded and the process idles without contacting the provider until
it is restarted with a corrected key or plan (a permission error does not fix itself by retrying).
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time

from . import store
from .adapters import AlpacaRest, BenzingaRest, ProviderError, build


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
    c = {"messages": 0, "news_new": 0, "news_versions": 0, "duplicates": 0, "tombstones": 0,
         "history_versions": 0, "conflicts_kept": 0, "stale_after_delete": 0, "restored": 0}
    for n in items:
        c["messages"] += 1
        if store.seen_message(db, provider_name, n.get("message_id")):
            c["duplicates"] += 1; continue
        r = store.apply(db, n, provider=provider_name, via=via, received_at=store.utcnow(), online=online)
        c[{"NEW": "news_new", "NEW_VERSION": "news_versions", "DUPLICATE": "duplicates", "TOMBSTONE": "tombstones",
           "HISTORY_VERSION": "history_versions", "CONFLICT_KEPT": "conflicts_kept", "STALE_AFTER_DELETE": "stale_after_delete",
           "RESTORED": "restored"}[r]] += 1
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
                if e.error_class in ("AUTH_FAILED", "FORBIDDEN"):
                    print(f"{provider_name} {e.error_class}: {e.detail}; not retrying (fix key/plan, then restart)", flush=True)
                    _sleep(float("inf"), deadline)
                    break
                wait = e.retry_after or backoff
                print(f"{provider_name} {e.error_class}: {e.detail}; retry in {int(wait)}s", flush=True)
                had_connection = True
                _sleep(min(wait, 900), deadline); backoff = min(backoff * 2, 300)
    except Stop:
        pass
    cur = db.execute("SELECT state FROM provider_state WHERE provider=?", (provider_name,)).fetchone()
    if not (cur and cur[0] in ("AUTH_FAILED", "FORBIDDEN")):  # keep the permission result visible after stop
        store.set_state(db, provider_name, "STOPPED", detail="collector stopped")
    return 0


def _sleep(seconds, deadline):
    end = time.monotonic() + seconds
    while time.monotonic() < end and (deadline is None or time.monotonic() < deadline):
        time.sleep(min(1.0, end - time.monotonic()))


def _state_after(c, db, provider_name, kind, live=True):
    """RECEIVING only after LIVE news (a genuine new push, or a non-backfill REST poll); backfill, gap fill,
    replay and late stream events never turn a connection into 'receiving'."""
    cur = db.execute("SELECT state FROM provider_state WHERE provider=?", (provider_name,)).fetchone()
    got_news = (live and (c.get("news_new") or c.get("news_versions"))) or (cur and cur[0] == "RECEIVING")
    store.set_state(db, provider_name, "RECEIVING" if got_news else "CONNECTED_NO_MESSAGES_YET", detail=kind, **c)


def _cursor(db, name, value=None):
    db.execute("CREATE TABLE IF NOT EXISTS cursors (name TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)")
    if value is None:
        r = db.execute("SELECT value FROM cursors WHERE name=?", (name,)).fetchone()
        return r[0] if r else None
    db.execute("INSERT INTO cursors VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
               (name, str(value), store.utcnow())); db.commit()


def benzinga_rest_sync(db, provider_name, rest, env, via):
    """Incremental REST sync on the provider's UPDATE time (updatedSince, paged, oldest first) plus removals
    (/news-removed). The cursor is the server clock at the start of the last successful sync minus an overlap,
    so late-indexed updates are not skipped; duplicates are absorbed by the store. First run = bounded backfill."""
    overlap = int(env.get("NEWS_OVERLAP_SECONDS", "300")); max_pages = int(env.get("NEWS_MAX_PAGES", "20"))
    started = time.time()
    cur = _cursor(db, f"{provider_name}:updated_since")
    first = cur is None
    since = int(float(cur)) - overlap if cur else int(started - 60 * int(env.get("NEWS_BACKFILL_MINUTES", "120")))
    items, trunc = rest.updated_since(since, max_pages=max_pages)
    removed, trunc_r = rest.removed_since(since)
    c = _apply_all(db, provider_name, items, via="backfill" if first else via, online=False)
    c2 = _apply_all(db, provider_name, removed, via="removed_poll", online=False)
    for k, v in c2.items():
        c[k] += v
    c["rest_pages_truncated"] = int(trunc or trunc_r); c["first_sync"] = int(first)
    _cursor(db, f"{provider_name}:updated_since", started)
    return c


def _poll_loop(db, provider, provider_name, env, deadline):
    every = max(15, int(env.get("NEWS_POLL_SECONDS", "60")))
    if provider_name == "benzinga_rest":
        while deadline is None or time.monotonic() < deadline:
            c = benzinga_rest_sync(db, provider_name, provider, env, via="rest_poll")
            # REST items are never an "online receipt": the label says polling, latency stats exclude them
            _state_after(c, db, provider_name, f"REST_POLL updatedSince every {every}s (polling, not push)", live=not c["first_sync"])
            _sleep(every, deadline)
        return
    last = db.execute("SELECT MAX(provider_created_at) FROM versions WHERE provider=?", (provider_name,)).fetchone()[0]
    first = last is None
    while deadline is None or time.monotonic() < deadline:
        since = None if first else _minus_minutes(last, 15)
        items = provider.fetch(since_utc=since, limit=100)
        c = _apply_all(db, provider_name, items, via="backfill" if first else "rest_poll", online=not first)
        _state_after(c, db, provider_name, "REST_POLL (polling, not push)")
        first = False
        last = db.execute("SELECT MAX(provider_created_at) FROM versions WHERE provider=?", (provider_name,)).fetchone()[0] or last
        _sleep(every, deadline)


ZERO = {"messages": 0, "news_new": 0, "news_versions": 0, "duplicates": 0, "tombstones": 0}


def _stream_loop(db, provider, provider_name, env, deadline, reconnect):
    """Live stream. Gap recovery is the REST updatedSince + removals sync (complete for the gap), not the
    stream's 'replay' (only the last 100 cached messages); replay is sent only when REST is unavailable and
    its messages are recorded as via='replay'. A stream event whose provider timestamp is older than this
    connection is recorded as 'stream_late' (not a new push)."""
    import websocket
    from datetime import datetime, timezone
    ws = provider.connect()
    connected_at = datetime.now(timezone.utc)
    store.set_state(db, provider_name, "CONNECTED_NO_MESSAGES_YET", detail=provider.kind, connects=1)
    replay_until = None
    try:
        rest_ok = False
        if provider_name == "benzinga_stream" and env.get("BENZINGA_TOKEN"):
            try:
                c = benzinga_rest_sync(db, provider_name, BenzingaRest(env["BENZINGA_TOKEN"]), env, via="gap_fill_rest")
                store.set_state(db, provider_name, _keep_receiving(db, provider_name), detail="stream up; REST updatedSince gap fill done",
                                gap_fill_items=c["messages"], rest_pages_truncated=c["rest_pages_truncated"])
                rest_ok = True
            except ProviderError as e:
                store.set_state(db, provider_name, _keep_receiving(db, provider_name),
                                detail=f"stream up; REST gap fill unavailable ({e.error_class})", gap_fill_errors=1)
        if reconnect and not rest_ok and provider.replay_text:  # partial fallback only (last 100 cached)
            ws.send(provider.replay_text); replay_until = time.monotonic() + 15
            store.set_state(db, provider_name, _keep_receiving(db, provider_name), detail="REST unavailable: replay sent (partial recovery, max 100)",
                            replays_sent=1)
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
                _state_after(dict(ZERO), db, provider_name, provider.kind)
                continue
            except websocket.WebSocketConnectionClosedException:
                raise ProviderError("NETWORK_OTHER", "connection closed by server")
            if not text:
                continue
            try:
                items = provider.parse(text)
            except ValueError as exc:  # recorded (structure only) and skipped; the connection stays up
                store.set_state(db, provider_name, _keep_receiving(db, provider_name), detail=f"skipped frame: {str(exc)[:200]}",
                                error_class="PROTOCOL_ERROR", bad_messages=1)
                continue
            for it in items:
                ev = store._ts(it.get("event_at"))
                if replay_until and time.monotonic() < replay_until and ev and ev < connected_at:
                    via, online = "replay", False
                elif ev and (connected_at - ev).total_seconds() > 5:
                    via, online = "stream_late", False
                else:
                    via, online = "stream", True
                c = _apply_all(db, provider_name, [it], via=via, online=online)
                c[f"via_{via}"] = 1
                _state_after(c, db, provider_name, provider.kind, live=(via == "stream"))
    finally:
        try:
            ws.close()
        except Exception:  # noqa: BLE001
            pass


def _keep_receiving(db, provider_name):
    cur = db.execute("SELECT state FROM provider_state WHERE provider=?", (provider_name,)).fetchone()
    return "RECEIVING" if cur and cur[0] == "RECEIVING" else "CONNECTED_NO_MESSAGES_YET"


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
