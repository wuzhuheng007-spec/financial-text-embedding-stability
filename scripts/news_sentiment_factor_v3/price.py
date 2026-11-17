"""Bounded read-only moomoo price/action acquisition for the ten new issuers.

The old NVDA and MU raw responses are reused later by the evaluator. Raw market
responses and OAuth material remain in the local ignored artifacts tree.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from scripts.market_observer_v1.moomoo_webapi_oauth import LocalTokenStore
from .source import CONFIG, PRIVATE, PUBLIC, digest, now, write_json

ROOT = Path(__file__).resolve().parents[2]
TOKEN = ROOT / "artifacts/market_observer_live/moomoo_webapi_oauth.dpapi"
BASE = "https://webapi.moomoo.com/api/v1.0/quote"
LEDGER = PRIVATE / "moomoo_request_ledger.jsonl"
NEW_SYMBOLS = ["AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "AMD", "AVGO", "INTC", "SNDK"]
ENDPOINTS = ["history-kline", "corporate-actions/rehab", "corporate-actions/dividends"]


def call(symbol: str, endpoint: str, access_token: str, number: int):
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    budget = config["source_request_budget"]
    if number > budget["moomoo_total"]:
        raise RuntimeError("MOOMOO_REQUEST_BUDGET_EXHAUSTED")
    if endpoint == "history-kline":
        query = urlencode({"start": "2025-04-01", "end": "2026-03-31", "ktype": 2,
                           "autype": 0, "num": 370, "extended_time": 0})
        url = f"{BASE}/US.{symbol}/{endpoint}?{query}"
    else:
        url = f"{BASE}/US.{symbol}/{endpoint}"
    stem = endpoint.replace("/", "_").replace("-", "_")
    raw_path = PRIVATE / "moomoo_raw" / f"{symbol}_{stem}.json"
    if raw_path.exists():
        raise FileExistsError("NO_OVERWRITE_EXISTING_RAW_RESPONSE")
    row = {"symbol": symbol, "endpoint": endpoint, "url": url, "attempt": number,
           "retry_count": 0, "started_at_utc": now(), "raw_path": None,
           "response_sha256": None, "response_bytes": 0}
    raw = b""
    try:
        request = Request(url, headers={"Authorization": "Bearer " + access_token,
                                        "Accept": "application/json"})
        with urlopen(request, timeout=budget["timeout_seconds"]) as response:
            row["http_status"] = response.status
            raw = response.read(budget["per_response_max_bytes"] + 1)
            if len(raw) > budget["per_response_max_bytes"]:
                raise ValueError("MOOMOO_RESPONSE_BYTE_CAP")
    except HTTPError as error:
        row["http_status"] = error.code
        row["error_class"] = "HTTPError"
        raw = error.read(min(1_000_000, budget["per_response_max_bytes"]))
    except (URLError, TimeoutError, ValueError) as error:
        row["error_class"] = type(error).__name__
    payload = None
    if raw:
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(raw)
        row["raw_path"] = str(raw_path)
        row["response_sha256"] = digest(raw_path)
        row["response_bytes"] = len(raw)
        try:
            payload = json.loads(raw)
            row["ret_code"] = payload.get("ret_code") if isinstance(payload, dict) else None
            row["provider_error_code"] = payload.get("error", {}).get("code") if isinstance(payload, dict) and isinstance(payload.get("error"), dict) else None
        except ValueError:
            row["error_class"] = "NON_JSON_RESPONSE"
    row["completed_at_utc"] = now()
    with LEDGER.open("a", encoding="utf-8") as output:
        output.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row, payload


def acquire():
    if LEDGER.exists() or (PUBLIC / "price_acquisition_redacted.json").exists():
        raise FileExistsError("ACQUISITION_LEDGER_EXISTS_NO_SILENT_RETRY")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if config["source_request_budget"]["moomoo_total"] != len(NEW_SYMBOLS) * len(ENDPOINTS):
        raise ValueError("FROZEN_BUDGET_DOES_NOT_MATCH_CALL_GRID")
    token = LocalTokenStore(TOKEN).access_token()
    rows = []
    stop = None
    for symbol in NEW_SYMBOLS:
        for endpoint in ENDPOINTS:
            if stop:
                break
            row, payload = call(symbol, endpoint, token, len(rows) + 1)
            data = payload.get("data") if isinstance(payload, dict) else None
            if endpoint == "history-kline" and isinstance(data, dict):
                bars = data.get("kline_list")
                if isinstance(bars, list):
                    row["bar_count"] = len(bars)
                    dates = [str(r.get("date")) for r in bars if isinstance(r, dict)]
                    row["min_date"] = min(dates) if dates else None
                    row["max_date"] = max(dates) if dates else None
                    row["duplicate_date_count"] = len(dates) - len(set(dates))
                    row["pagination_has_more"] = payload.get("pagination", {}).get("has_more") if isinstance(payload.get("pagination"), dict) else None
            if endpoint.endswith("rehab") and isinstance(data, dict):
                events = data.get("rehabs")
                row["action_rows"] = len(events) if isinstance(events, list) else None
            if endpoint.endswith("dividends") and isinstance(data, dict):
                events = data.get("dividend_list")
                row["action_rows"] = len(events) if isinstance(events, list) else None
            rows.append(row)
            if row.get("http_status") in (401, 403, 429) or row.get("ret_code") in (-11, -12):
                stop = "AUTHORIZATION_OR_QUOTA_STOP"
            elif row.get("http_status") != 200 or row.get("ret_code") != 0:
                stop = "PROVIDER_OR_TRANSPORT_FAILURE_STOP"
        if stop:
            break
    private = {"schema": "sentiment_v3_moomoo_acquisition_private_1", "completed_at_utc": now(),
               "config_sha256": digest(CONFIG), "code_sha256": digest(Path(__file__)),
               "rows": rows, "stop_reason": stop}
    write_json(PRIVATE / "moomoo_acquisition_private.json", private)
    public = {"schema": "sentiment_v3_moomoo_acquisition_redacted_1", "completed_at_utc": now(),
              "config_sha256": digest(CONFIG), "code_sha256": digest(Path(__file__)),
              "private_manifest_sha256": digest(PRIVATE / "moomoo_acquisition_private.json"),
              "calls_used": len(rows), "maximum_calls": config["source_request_budget"]["moomoo_total"],
              "stop_reason": stop, "rows": [{k: row.get(k) for k in ("symbol", "endpoint", "http_status",
                            "ret_code", "provider_error_code", "response_sha256", "bar_count", "min_date", "max_date",
                            "pagination_has_more", "action_rows") } for row in rows]}
    write_json(PUBLIC / "price_acquisition_redacted.json", public)
    return {"calls_used": len(rows), "stop_reason": stop,
            "history_success": sum(r["endpoint"] == "history-kline" and r.get("ret_code") == 0 for r in rows),
            "action_success": sum(r["endpoint"] != "history-kline" and r.get("ret_code") == 0 for r in rows)}


if __name__ == "__main__":
    print(json.dumps(acquire()))
