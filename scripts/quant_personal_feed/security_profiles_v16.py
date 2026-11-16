"""One bounded official security profile refresh for the owner-only portal.

The response is private. The public receipt contains only field coverage and
hashes, never the OAuth credential or licensed market data.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from scripts.market_observer_v1.moomoo_webapi_oauth import LocalTokenStore
from scripts.quant_personal_feed.collector import ROOT, TOKEN_FILE, verified_current_symbols

CONFIG = ROOT / "configs/quant_portal_personal_feeds_v11_20261116.json"
PRIVATE = ROOT / "apps/quant_portal/var/security_profiles_v16.json"
RECEIPT = ROOT / "results/quant_portal_personal_v16_20261116/security_profiles_redacted.json"
URL = "https://webapi.moomoo.com/api/v1.0/quote/stock-basicinfo"
SYMBOLS = ("TQQQ", "QQQ", "SOXX", "SPY", "NVDA", "MU")


def refresh_once() -> dict:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    mapped = verified_current_symbols(cfg)
    codes = [mapped[symbol] for symbol in SYMBOLS]
    token = LocalTokenStore(TOKEN_FILE).access_token()
    request = Request(URL, data=json.dumps({"code_list": codes}).encode(), method="POST",
                      headers={"Authorization": "Bearer " + token,
                               "Content-Type": "application/json"})
    with urlopen(request, timeout=15) as response:
        body = response.read(2_000_001)
    if len(body) > 2_000_000:
        raise ValueError("SECURITY_PROFILE_RESPONSE_TOO_LARGE")
    payload = json.loads(body)
    if payload.get("ret_code") != 0 or not isinstance(payload.get("data", {}).get("basic_list"), list):
        raise ValueError(f"SECURITY_PROFILE_BUSINESS_{payload.get('ret_code')}")
    rows = payload["data"]["basic_list"]
    by_code = {row.get("code"): row for row in rows if isinstance(row, dict)}
    if len(by_code) != len(rows):
        raise ValueError("SECURITY_PROFILE_DUPLICATE_CODE")
    if not set(by_code).issubset(set(codes)):
        raise ValueError("SECURITY_PROFILE_UNREQUESTED_CODE")
    received_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    digest = hashlib.sha256(body).hexdigest()
    private = {"schema": "security_profiles_v16", "endpoint": URL,
               "received_at_utc": received_at, "response_sha256": digest,
               "requested": dict(zip(SYMBOLS, codes)), "response": payload}
    PRIVATE.parent.mkdir(parents=True, exist_ok=True)
    PRIVATE.write_text(json.dumps(private, ensure_ascii=False, indent=2), encoding="utf-8")
    receipt = {"schema": "security_profiles_v16_receipt", "endpoint": URL,
               "received_at_utc": received_at, "response_sha256": digest,
               "requested_symbols": len(codes), "returned_symbols": len(rows),
               "fields_present": {symbol: [field for field in ("name", "stock_type", "exchange", "currency")
                   if by_code.get(code, {}).get(field) not in (None, "")]
                   for symbol, code in zip(SYMBOLS, codes)}}
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(receipt, ensure_ascii=False, indent=2),
                       encoding="utf-8", newline="\n")
    return receipt


def read_profiles() -> dict[str, dict]:
    if not PRIVATE.is_file():
        return {}
    record = json.loads(PRIVATE.read_text(encoding="utf-8"))
    if record.get("schema") != "security_profiles_v16":
        return {}
    by_code = {row.get("code"): row for row in record["response"]["data"]["basic_list"]}
    return {symbol: {"symbol": symbol, "provider_code": code,
                     "name": by_code[code].get("name"), "asset_type": by_code[code].get("stock_type"),
                     "exchange": by_code[code].get("exchange"), "currency": by_code[code].get("currency"),
                     "source_endpoint": record["endpoint"], "response_sha256": record["response_sha256"],
                     "checked_at_utc": record["received_at_utc"]}
            for symbol, code in record["requested"].items() if code in by_code}


def fill_system_watchlist_placeholders() -> dict:
    """Fill only blank/system names and unknown types for the configured owner."""
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    path = ROOT / cfg["portal_db"]
    profiles = read_profiles()
    updated_names = updated_types = 0
    with sqlite3.connect(path, timeout=10) as db:
        owner = db.execute("SELECT id FROM auth_user WHERE username=?",
                           (cfg["owner_username"],)).fetchone()
        if owner is None:
            raise ValueError("OWNER_NOT_FOUND")
        for symbol, name, kind in db.execute(
            "SELECT symbol,display_name,asset_type FROM core_watchlistitem WHERE user_id=?", (owner[0],)
        ).fetchall():
            profile = profiles.get(symbol)
            if not profile:
                continue
            if name in (None, "", symbol, "名称待核实") and profile["name"]:
                db.execute("UPDATE core_watchlistitem SET display_name=? WHERE user_id=? AND symbol=?",
                           (profile["name"], owner[0], symbol))
                updated_names += 1
            mapped_kind = {"STOCK": "EQUITY", "ETF": "ETF"}.get(profile["asset_type"])
            if kind == "UNKNOWN" and mapped_kind:
                db.execute("UPDATE core_watchlistitem SET asset_type=? WHERE user_id=? AND symbol=?",
                           (mapped_kind, owner[0], symbol))
                updated_types += 1
    return {"updated_system_names": updated_names, "updated_unknown_types": updated_types}


if __name__ == "__main__":
    receipt = refresh_once()
    receipt["watchlist_fill"] = fill_system_watchlist_placeholders()
    print(json.dumps(receipt, ensure_ascii=False))
