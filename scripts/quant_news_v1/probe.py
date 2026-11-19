"""One-shot credential/feed probe (writes nothing to the news cache; prints redacted JSON only).

python -m scripts.quant_news_v1.probe --provider benzinga|benzinga_rest|benzinga_stream|massive_benzinga|alpaca_stream [--listen-seconds 30]
"benzinga" runs the REST check and the stream check separately (a working REST key or an "Active" account
does not prove the stream is enabled).
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


def probe(provider_name: str, listen_seconds: float) -> dict:
    if provider_name == "benzinga":
        env = dict(os.environ)
        return {"provider": "benzinga", "at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "ws_token_source": "BENZINGA_WS_TOKEN" if env.get("BENZINGA_WS_TOKEN") else "BENZINGA_TOKEN (same key as REST)",
                "rest": probe_benzinga_rest(env), "stream": probe("benzinga_stream", listen_seconds),
                "ok": None}
    if provider_name == "benzinga_rest":
        return {"provider": provider_name, **probe_benzinga_rest(dict(os.environ))}
    out = {"provider": provider_name, "at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    try:
        p = build(provider_name, dict(os.environ))
    except ProviderError as e:
        return {**out, "ok": False, "result": e.error_class, "detail": e.detail}
    out["transport"] = p.kind
    try:
        if p.kind == "REST_POLL":
            items = p.fetch(limit=20); t = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            watch = p.fetch(limit=20, tickers=WATCH)
            out.update(ok=True, result="AUTH_OK_REST", latest=summarize(items, t), watchlist_query=summarize(watch, t))
        else:
            ws = p.connect(); out["result"] = "AUTH_OK_CONNECTED"
            if p.replay_text:
                ws.send(p.replay_text)
            ws.settimeout(5); got, other, frames = [], [], 0; end = time.monotonic() + listen_seconds
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
                    got.extend(p.parse(text))
                except ValueError:
                    other.append(redact(text)[:200])
            ws.close()
            out["frames"] = frames; out["non_news_frames_sample"] = other[:3]
            t = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            out.update(ok=True, listened_seconds=listen_seconds, stream=summarize(got, t),
                       observed="NEWS_OBSERVED" if got else "NO_NEWS_OBSERVED_IN_WINDOW (not a failure, not an acceptance)")
            if provider_name == "alpaca_stream":
                rest = AlpacaRest(os.environ.get("ALPACA_KEY_ID", ""), os.environ.get("ALPACA_SECRET_KEY", ""))
                out["rest_latest"] = summarize(rest.fetch(limit=20), t)
    except ProviderError as e:
        out.update(ok=False, result=e.error_class, detail=e.detail)
    except Exception as e:  # noqa: BLE001 - anything unexpected is reported redacted, never with a URL
        out.update(ok=False, result="UNEXPECTED", detail=redact(f"{type(e).__name__}: {e}")[:200])
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default=os.environ.get("NEWS_PROVIDER", "")); ap.add_argument("--listen-seconds", type=float, default=30)
    a = ap.parse_args(argv)
    res = probe(a.provider, a.listen_seconds)
    if a.provider == "benzinga":
        res["ok"] = bool(res["rest"].get("ok")) or bool(res["stream"].get("ok"))
    print(redact(json.dumps(res, ensure_ascii=False, indent=1)))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
