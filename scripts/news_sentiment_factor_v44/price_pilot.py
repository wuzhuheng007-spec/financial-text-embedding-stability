"""Bounded, append-only private WebAPI regular daily-bar probe for NVDA/MU.

No returns are calculated here. An HTTP 439/401/403/429 or provider denial stops
the source immediately. Raw responses stay in ignored artifacts.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from scripts.market_observer_v1.moomoo_webapi_oauth import LocalTokenStore
from scripts.news_sentiment_factor_v44.preflight import PRIVATE, PUBLIC, ROOT, sha

CONFIG = ROOT / "configs/news_sentiment_factor_v44_20261117.json"
LEDGER = PRIVATE / "price_request_ledger_private.jsonl"
TOKEN = ROOT / "artifacts/market_observer_live/moomoo_webapi_oauth.dpapi"
ENDPOINT = "https://webapi.moomoo.com/api/v1.0/quote"


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def append(row: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def used() -> int:
    if not LEDGER.exists():
        return 0
    return sum(json.loads(line).get("phase") == "REQUEST_RESERVED"
               for line in LEDGER.read_text(encoding="utf-8").splitlines() if line.strip())


def call(symbol: str, token: str) -> dict:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if symbol not in config["price_pilot_symbols"]:
        raise ValueError("SYMBOL_OUTSIDE_FROZEN_PILOT")
    if used() >= config["new_supplier_request_budget"]:
        raise RuntimeError("FROZEN_NEW_REQUEST_BUDGET_EXHAUSTED")
    query = urlencode({"start": min(config["price_pilot_sessions"]),
                       "end": max(config["price_pilot_sessions"]), "ktype": 2,
                       "autype": 0, "num": 2, "extended_time": 0})
    url = f"{ENDPOINT}/US.{symbol}/history-kline?{query}"
    attempt = used() + 1
    row = {"attempt": attempt, "symbol": symbol, "endpoint": "history-kline",
           "url": url, "started_at_utc": utc(), "request_config_sha256": sha(CONFIG),
           "response_sha256": None, "response_bytes": 0, "http_status": None,
           "ret_code": None, "provider_error_code": None, "error_class": None}
    # Count the attempt before making the call, so a crash cannot erase budget use.
    append({"attempt": attempt, "symbol": symbol, "endpoint": "history-kline",
            "started_at_utc": row["started_at_utc"], "phase": "REQUEST_RESERVED"})
    body = b""
    try:
        request = Request(url, headers={"Authorization": "Bearer " + token,
                                        "Accept": "application/json"})
        with urlopen(request, timeout=15) as response:
            row["http_status"] = response.status
            body = response.read(2_000_001)
        if len(body) > 2_000_000:
            row["error_class"] = "RESPONSE_BYTE_CAP"
            body = body[:2_000_000]
    except HTTPError as error:
        row["http_status"] = error.code
        row["error_class"] = "HTTPError"
        body = error.read(200_001)[:200_000]
    except (URLError, TimeoutError, OSError) as error:
        row["error_class"] = type(error).__name__
    if body:
        raw = PRIVATE / "price_raw" / f"{symbol}_history_{attempt}.json"
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(body)
        row["response_sha256"] = hashlib.sha256(body).hexdigest()
        row["response_bytes"] = len(body)
        try:
            payload = json.loads(body)
            if isinstance(payload, dict):
                row["ret_code"] = payload.get("ret_code")
                error = payload.get("error")
                row["provider_error_code"] = error.get("code") if isinstance(error, dict) else None
                data = payload.get("data")
                bars = data.get("kline_list") if isinstance(data, dict) else None
                if isinstance(bars, list):
                    wanted = set(config["price_pilot_sessions"])
                    matched = [b for b in bars if str(b.get("date", "")) in wanted or
                               str(b.get("date", "")).replace("-", "") in {d.replace("-", "") for d in wanted}]
                    row["returned_rows"] = len(bars)
                    row["target_session_rows"] = len(matched)
                    row["target_sessions"] = sorted(str(b.get("date")) for b in matched)
        except ValueError:
            row["error_class"] = "NON_JSON_RESPONSE"
    row["completed_at_utc"] = utc()
    # Outcome rows are separate from reservation rows. Count only reservations.
    append({**row, "phase": "RESULT"})
    return row


def run() -> dict:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if config["automatic_price_retry_budget"] != 0:
        raise ValueError("PILOT_MUST_NOT_RETRY")
    # Existing OAuth store may refresh a short-lived token; this is a separate
    # auth operation and is recorded by the store, never printed here.
    token = LocalTokenStore(TOKEN).access_token()
    rows = []
    stop = None
    for symbol in config["price_pilot_symbols"]:
        row = call(symbol, token)
        rows.append(row)
        if row["http_status"] in (401, 403, 429, 439) or row["ret_code"] in (-9, -11):
            stop = "PROVIDER_AUTHORIZATION_SECURITY_OR_RATE_STOP"
        elif row["http_status"] != 200 or row["ret_code"] != 0:
            stop = "PROVIDER_OR_TRANSPORT_FAILURE_STOP"
        if stop:
            break
    result = {"schema": "v44_price_probe_redacted_1", "completed_at_utc": utc(),
              "calls_reserved": used(), "maximum_calls": config["new_supplier_request_budget"],
              "stop_reason": stop, "rows": [{k: row.get(k) for k in
                  ("symbol", "endpoint", "http_status", "ret_code", "provider_error_code",
                   "response_sha256", "returned_rows", "target_session_rows", "target_sessions")}
                   for row in rows]}
    PUBLIC.mkdir(parents=True, exist_ok=True)
    (PUBLIC / "price_pilot_redacted.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def supplement() -> dict:
    """One batch identity query and per-symbol split/dividend histories; no retry."""
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if not (PUBLIC / "price_pilot_redacted.json").is_file():
        raise RuntimeError("PRICE_PILOT_MUST_RUN_FIRST")
    if (PUBLIC / "price_supplement_redacted.json").exists():
        raise FileExistsError("SUPPLEMENT_ALREADY_RUN")
    token = LocalTokenStore(TOKEN).access_token()
    requests = [("basicinfo", None, f"{ENDPOINT}/stock-basicinfo",
                 json.dumps({"code_list": ["US.NVDA", "US.MU"]}).encode("utf-8"))]
    requests.extend((kind, symbol, f"{ENDPOINT}/US.{symbol}/corporate-actions/{kind}", None)
                    for symbol in config["price_pilot_symbols"] for kind in ("rehab", "dividends"))
    rows = []
    stop = None
    for kind, symbol, url, data in requests:
        if used() >= config["new_supplier_request_budget"]:
            stop = "BUDGET_EXHAUSTED"
            break
        number = used() + 1
        start = utc()
        append({"attempt": number, "symbol": symbol, "endpoint": kind,
                "started_at_utc": start, "phase": "REQUEST_RESERVED"})
        row = {"attempt": number, "symbol": symbol, "endpoint": kind,
               "started_at_utc": start, "http_status": None, "ret_code": None,
               "provider_error_code": None, "response_sha256": None,
               "error_class": None, "item_count": None}
        raw_bytes = b""
        try:
            headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
            if data is not None:
                headers["Content-Type"] = "application/json"
            request = Request(url, data=data, method="POST" if data is not None else "GET", headers=headers)
            with urlopen(request, timeout=15) as response:
                row["http_status"] = response.status
                raw_bytes = response.read(2_000_001)
            if len(raw_bytes) > 2_000_000:
                row["error_class"] = "RESPONSE_BYTE_CAP"
                raw_bytes = raw_bytes[:2_000_000]
        except HTTPError as error:
            row["http_status"] = error.code
            row["error_class"] = "HTTPError"
            raw_bytes = error.read(200_001)[:200_000]
        except (URLError, TimeoutError, OSError) as error:
            row["error_class"] = type(error).__name__
        if raw_bytes:
            path = PRIVATE / "price_raw" / f"{symbol or 'BATCH'}_{kind}_{number}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw_bytes)
            row["response_sha256"] = hashlib.sha256(raw_bytes).hexdigest()
            try:
                payload = json.loads(raw_bytes)
                row["ret_code"] = payload.get("ret_code") if isinstance(payload, dict) else None
                error = payload.get("error") if isinstance(payload, dict) else None
                row["provider_error_code"] = error.get("code") if isinstance(error, dict) else None
                content = payload.get("data") if isinstance(payload, dict) else None
                key = {"basicinfo": "basic_list", "rehab": "rehabs", "dividends": "dividend_list"}[kind]
                items = content.get(key) if isinstance(content, dict) else None
                row["item_count"] = len(items) if isinstance(items, list) else None
            except ValueError:
                row["error_class"] = "NON_JSON_RESPONSE"
        row["completed_at_utc"] = utc()
        append({**row, "phase": "RESULT"})
        rows.append(row)
        if row["http_status"] in (401, 403, 429, 439) or row["ret_code"] in (-9, -11):
            stop = "PROVIDER_AUTHORIZATION_SECURITY_OR_RATE_STOP"
        elif row["http_status"] != 200 or row["ret_code"] != 0:
            stop = "PROVIDER_OR_TRANSPORT_FAILURE_STOP"
        if stop:
            break
    receipt = {"schema": "v44_price_supplement_redacted_1", "completed_at_utc": utc(),
               "total_calls_reserved": used(), "maximum_calls": config["new_supplier_request_budget"],
               "stop_reason": stop, "rows": [{k: r.get(k) for k in
                   ("symbol", "endpoint", "http_status", "ret_code", "provider_error_code",
                    "response_sha256", "item_count")} for r in rows]}
    (PUBLIC / "price_supplement_redacted.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    return receipt


if __name__ == "__main__":
    import sys
    print(json.dumps(supplement() if "--supplement" in sys.argv else run(), ensure_ascii=False))
