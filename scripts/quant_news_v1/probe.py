"""One-shot credential/feed probe (writes nothing to the news cache; prints redacted JSON only).

python -m scripts.quant_news_v1.probe --provider benzinga|benzinga_rest|benzinga_stream|massive_benzinga|alpaca_stream [--listen-seconds 30]
"benzinga" runs the REST check and the stream check separately (a working REST key or an "Active" account
does not prove the stream is enabled). The stream is observed WITHOUT replay by default, so everything it
reports as a new push really was pushed after connecting; --replay adds a separate, labelled replay phase.
Every exit path closes the connection; every error text passes redact().
Reports: auth outcome (classified), transport, sample count, which fields are present/empty, sources,
watchlist symbol coverage, provider time fields and receipt-minus-publication lags of the sample.
Never prints keys, tokens, auth headers or URLs that carry a token.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone

from .adapters import AlpacaRest, BenzingaRest, ProviderError, build, redact

WATCH = ["TQQQ", "QQQ", "SOXX", "SPY", "NVDA", "MU"]


def _lag_seconds(created, received):
    try:
        a = datetime.fromisoformat(created.replace("Z", "+00:00")); b = datetime.fromisoformat(received.replace("Z", "+00:00"))
        return round((b - a).total_seconds(), 1)
    except (AttributeError, ValueError):
        return None


def summarize(items: list[dict], received_at: str) -> dict:
    fields = ("title", "teaser", "body", "url", "authors", "symbols", "channels", "provider_created_at", "provider_updated_at")
    lags = [x for x in (_lag_seconds(i.get("provider_created_at"), received_at) for i in items) if x is not None]
    return {"sample_count": len(items),
            "fields_present": {f: sum(1 for i in items if i.get(f)) for f in fields},
            "sources": sorted({i.get("source") or "?" for i in items}),
            "actions": sorted({i.get("action") for i in items}),
            "watchlist_coverage": {s: sum(1 for i in items if s in (i.get("symbols") or [])) for s in WATCH},
            "newest_provider_created_at": max((i.get("provider_created_at") or "" for i in items), default=None),
            "receipt_minus_publication_seconds": ({"min": min(lags), "median": statistics.median(lags), "max": max(lags)} if lags else None),
            "note": "Sample lag = probe receipt minus provider publication time. For REST/backfill samples this includes the age of older articles; it is not a feed latency and never an event-time latency."}


MACRO_CHANNELS = ["Economics", "Macro Economic Events", "Federal Reserve"]


def probe_benzinga_rest(env: dict) -> dict:
    out = {"check": "benzinga_rest", "endpoint": "GET https://api.benzinga.com/api/v2/news"}
    try:
        rest = BenzingaRest(env.get("BENZINGA_TOKEN", ""))
    except ProviderError as e:
        return {**out, "ok": False, "result": e.error_class, "detail": e.detail}
    try:
        items = rest.fetch(limit=20); t = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ProviderError as e:
        return {**out, "ok": False, "result": e.error_class, "detail": e.detail}
    out.update(ok=True, result="AUTH_OK_REST", latest=summarize(items, t),
               sample_titles=[{"id": i["upstream_id"], "created_utc": i["provider_created_at"], "title": (i["title"] or "")[:90],
                               "symbols": i["symbols"][:6], "url_host": (i["url"] or "").split("/")[2] if i.get("url") else None,
                               "body_chars": len(i.get("body") or "")} for i in items[:5]])
    cov = {}
    for sym in WATCH:  # one ticker per query: an ETF with no recent news is coverage information, not a failure
        try:
            r = rest.fetch(limit=20, tickers=[sym], display="headline")
            cov[sym] = {"items": len(r), "newest_created_utc": max((i["provider_created_at"] or "" for i in r), default=None),
                        "direct_mention": sum(1 for i in r if sym in i["symbols"])}
        except ProviderError as e:
            cov[sym] = {"error": e.error_class, "detail": e.detail}
    out["per_symbol"] = cov
    try:
        m = rest.fetch(limit=20, channels=MACRO_CHANNELS, display="headline")
        out["macro_channels"] = {"query": MACRO_CHANNELS, "items": len(m), "channels_seen": sorted({c for i in m for c in i["channels"]})[:15]}
    except ProviderError as e:
        out["macro_channels"] = {"error": e.error_class, "detail": e.detail}
    return out


def _listen(ws, p, seconds, connected_at):
    """Collect frames for `seconds`; split news into new pushes (provider event time at/after connect) and
    older events. Pings on idle. Returns (new_pushes, older_events, non_news_frames, frame_count)."""
    new, older, other, frames = [], [], [], 0
    end = time.monotonic() + seconds
    ws.settimeout(5)
    while time.monotonic() < end:
        try:
            text = ws.recv()
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ == "WebSocketTimeoutException":
                if p.ping_text:
                    ws.send(p.ping_text)
                continue
            raise
        frames += 1
        try:
            items = p.parse(text)
        except ValueError as exc:  # non-JSON text, or JSON of an unexpected shape (structure only, no content)
            other.append(redact(str(exc) if str(exc).startswith("unexpected") else text)[:300]); continue
        for it in items:
            ev = it.get("event_at")
            (new if ev and ev >= connected_at else older).append(it)
    return new, older, other, frames


def probe(provider_name: str, listen_seconds: float, replay: bool = False) -> dict:
    if provider_name == "benzinga":
        env = dict(os.environ)
        return {"provider": "benzinga", "at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "ws_token_source": "BENZINGA_WS_TOKEN" if env.get("BENZINGA_WS_TOKEN") else "BENZINGA_TOKEN (same key as REST)",
                "rest": probe_benzinga_rest(env), "stream": probe("benzinga_stream", listen_seconds, replay),
                "ok": None}
    if provider_name == "benzinga_rest":
        return {"provider": provider_name, **probe_benzinga_rest(dict(os.environ))}
    out = {"provider": provider_name, "at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    try:
        p = build(provider_name, dict(os.environ))
    except ProviderError as e:
        return {**out, "ok": False, "result": e.error_class, "detail": e.detail}
    out["transport"] = p.kind
    ws = None
    try:
        if p.kind == "REST_POLL":
            items = p.fetch(limit=20); t = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            watch = p.fetch(limit=20, tickers=WATCH)
            out.update(ok=True, result="AUTH_OK_REST", latest=summarize(items, t), watchlist_query=summarize(watch, t))
        else:
            ws = p.connect()
            connected_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            out.update(result="AUTH_OK_CONNECTED", connected_at_utc=connected_at)
            # Phase 1: listen WITHOUT replay - only genuinely new pushes can arrive here.
            new, older, other, frames = _listen(ws, p, listen_seconds, connected_at)
            t = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            out.update(ok=True, listened_seconds=listen_seconds, frames=frames, non_news_frames_sample=other[:3],
                       new_pushes=summarize(new, t), older_events_without_replay=len(older),
                       observed=("NEW_PUSH_OBSERVED" if new else
                                 "CONNECTED_NO_NEW_PUSH_IN_WINDOW (connection works; no new article was published to this key in the window)"))
            # Phase 2 (optional): replay, recorded separately - these are cached past messages, not live pushes.
            if replay and p.replay_text:
                ws.send(p.replay_text)
                rnew, rold, rother, rframes = _listen(ws, p, 10, connected_at)
                out["replay"] = {"frames": rframes, "cached_messages": len(rold), "new_pushes_during_replay_window": len(rnew),
                                 "sample": summarize(rold, t), "note": "replay = up to 100 cached past messages; never counted as live receipt"}
            if provider_name == "alpaca_stream":
                rest = AlpacaRest(os.environ.get("ALPACA_KEY_ID", ""), os.environ.get("ALPACA_SECRET_KEY", ""))
                out["rest_latest"] = summarize(rest.fetch(limit=20), t)
    except ProviderError as e:
        out.update(ok=False, result=e.error_class, detail=e.detail)
    except Exception as e:  # noqa: BLE001 - anything unexpected is reported redacted, never with a URL
        out.update(ok=False, result="UNEXPECTED", detail=redact(f"{type(e).__name__}: {e}")[:200])
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass
            out["connection_closed"] = True
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default=os.environ.get("NEWS_PROVIDER", "")); ap.add_argument("--listen-seconds", type=float, default=30)
    ap.add_argument("--replay", action="store_true", help="after the no-replay window, also request the cached replay (reported separately)")
    a = ap.parse_args(argv)
    res = probe(a.provider, a.listen_seconds, a.replay)
    if a.provider == "benzinga":
        res["ok"] = bool(res["rest"].get("ok")) or bool(res["stream"].get("ok"))
    print(redact(json.dumps(res, ensure_ascii=False, indent=1)))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
