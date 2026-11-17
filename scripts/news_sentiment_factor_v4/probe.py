"""One bounded, credential-safe moomoo news capability request.

The public reference was removed; this call discovers current response shape
without assuming a historical date filter or claiming `quote:read` news rights.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from scripts.market_observer_v1.moomoo_webapi_oauth import LocalTokenStore, OAuthError
from .preflight import CONFIG, PRIVATE, PUBLIC, ROOT, save, sha

ENDPOINT = "https://webapi.moomoo.com/api/v1.0/news/search"
LEDGER = PRIVATE / "request_ledger_private.jsonl"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def count_requests() -> int:
    if not LEDGER.exists():
        return 0
    return sum(1 for _ in LEDGER.open(encoding="utf-8"))


def request_once(keyword: str | None = None, *, accept_language_en: bool = False) -> dict:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    frozen = json.loads((PUBLIC / "preflight_redacted.json").read_text(encoding="utf-8"))
    if sha(CONFIG) != frozen["config_sha256"]:
        raise ValueError("CONFIG_DRIFT")
    if count_requests() >= min(config["request_budget"]["total_max"],
                               config["request_budget"]["moomoo_webapi_max"]):
        raise ValueError("PROBE_BUDGET_EXHAUSTED")
    if LEDGER.exists() and any(json.loads(line).get("http_status") in config["request_budget"]["stop_provider_on_http"]
                               for line in LEDGER.read_text(encoding="utf-8").splitlines() if line):
        raise RuntimeError("PROVIDER_STOPPED_AFTER_AUTH_OR_RATE_LIMIT")
    if keyword is not None and keyword not in config["symbols"]:
        raise ValueError("KEYWORD_OUTSIDE_FROZEN_SYMBOLS")
    token = LocalTokenStore(ROOT / "artifacts/market_observer_live/moomoo_webapi_oauth.dpapi").access_token()
    index = count_requests() + 1
    raw_path = PRIVATE / f"moomoo_news_probe_{index:02}.json"
    if raw_path.exists():
        raise FileExistsError("PROBE_RAW_EXISTS")
    params = {"keyword": keyword} if keyword else {}
    url = ENDPOINT + ("?" + urlencode(params) if params else "")
    row = {"provider": "moomoo_webapi", "method": "GET", "endpoint": ENDPOINT,
           "parameters": params, "started_at_utc": now(), "attempt": 1, "retry": 0,
           "accept_language": "en-US" if accept_language_en else "NOT_SET",
           "raw_path": str(raw_path), "raw_sha256": None}
    body = None
    try:
        headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
        if accept_language_en:
            headers["Accept-Language"] = "en-US"
        request = Request(url, headers=headers, method="GET")
        with urlopen(request, timeout=15) as response:
            row["http_status"] = response.status
            body = response.read(config["request_budget"]["max_response_bytes"] + 1)
    except HTTPError as exc:
        row["http_status"] = exc.code
        body = exc.read(config["request_budget"]["max_response_bytes"] + 1)
        row["error_class"] = "HTTPError"
    except (URLError, TimeoutError, OAuthError) as exc:
        row["error_class"] = type(exc).__name__
    if body is not None:
        if len(body) > config["request_budget"]["max_response_bytes"]:
            row["error_class"] = "RESPONSE_BYTE_CAP"
        else:
            raw_path.write_bytes(body)
            row["raw_sha256"] = hashlib.sha256(body).hexdigest()
            row["bytes"] = len(body)
            try:
                obj = json.loads(body)
                row["top_keys"] = sorted(obj) if isinstance(obj, dict) else None
                if isinstance(obj, dict):
                    row["ret_code"] = obj.get("ret_code")
                    data = obj.get("data")
                    row["data_type"] = type(data).__name__
                    row["data_keys"] = sorted(data) if isinstance(data, dict) else None
                    if isinstance(data, dict):
                        for key in ("items", "list", "news_list"):
                            if isinstance(data.get(key), list):
                                row["item_array_key"] = key
                                row["item_count"] = len(data[key])
                                row["first_item_keys"] = sorted(data[key][0]) if data[key] and isinstance(data[key][0], dict) else None
                                break
            except (json.JSONDecodeError, UnicodeDecodeError):
                row["error_class"] = "NON_JSON_RESPONSE"
    row["completed_at_utc"] = now()
    with LEDGER.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    safe = {key: row.get(key) for key in ("provider", "method", "endpoint", "parameters",
        "started_at_utc", "completed_at_utc", "attempt", "retry", "accept_language", "http_status",
        "error_class", "raw_sha256", "bytes", "top_keys", "ret_code", "data_type",
        "data_keys", "item_array_key", "item_count", "first_item_keys")}
    safe["provider_requests_used"] = count_requests()
    safe["historical_date_parameters_verified"] = False
    save(PUBLIC / f"moomoo_probe_{index:02}_redacted.json", safe)
    return safe


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--keyword", choices=("NVDA", "MU", "MSFT"))
    parser.add_argument("--accept-language-en", action="store_true")
    args = parser.parse_args()
    print(json.dumps(request_once(args.keyword, accept_language_en=args.accept_language_en), ensure_ascii=False))
