"""Publish counts and provenance hashes for the private two-symbol price pilot."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .labels import DB
from .preflight import PRIVATE, PUBLIC, ROOT, sha


def read() -> dict:
    basic_file = next((PRIVATE / "price_raw").glob("BATCH_basicinfo_*.json"))
    basic = json.loads(basic_file.read_text(encoding="utf-8"))
    identities = {row["code"]: row for row in basic["data"]["basic_list"]}
    with closing(sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        securities = {}
        for symbol in ("NVDA", "MU"):
            sid = f"SEC:{symbol}:US"
            dates = [dict(x) for x in con.execute("""SELECT trade_date,
                COUNT(*) AS field_rows,
                SUM(field='OPEN') AS open_rows,
                SUM(field='CLOSE') AS close_rows,
                MIN(currency) AS currency,
                MIN(price_semantics) AS price_semantics,
                MIN(adjustment) AS adjustment,
                MIN(raw_sha256) AS response_sha256
                FROM price_evidence WHERE security_id=? GROUP BY trade_date ORDER BY trade_date""", (sid,))]
            action = [dict(x) for x in con.execute("""SELECT observed_from_date,observed_through_date,
                split_events AS rehab_factor_rows,dividend_events,evidence_status,rehab_sha256,dividends_sha256
                FROM action_evidence WHERE security_id=?""", (sid,))]
            securities[symbol] = {
                "provider_identity": {key: identities[f"US.{symbol}"].get(key)
                                      for key in ("code", "stock_type", "exchange")},
                "identity_response_sha256": sha(basic_file),
                "regular_daily_bars": len(dates), "dates_and_hashes": dates,
                "action_interval_count": len(action), "action_intervals": action,
                "v43_verified_auction_open_bars": 0,
                "v44_currency_qualified_daily_bars": sum(x["currency"] != "UNKNOWN" for x in dates),
                "blocking_reasons": ["DAILY_BAR_OPEN_NOT_VERIFIED_OFFICIAL_AUCTION",
                                     "QUOTE_CURRENCY_NOT_IN_CHECKED_WEBAPI_RESPONSE"],
            }
        labels = con.execute("SELECT COUNT(*) FROM label_versions").fetchone()[0]
    now = datetime.now(timezone.utc)
    result = {
        "schema": "news_sentiment_v44_price_qualification_redacted_1",
        "checked_at_utc": now.isoformat(),
        "checked_at_asia_shanghai": now.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
        "scope": "2026-11-13 and 2026-11-16 completed regular sessions only; no forward signal yet",
        "supplier_request_count": 7,
        "supplier_request_budget": 12,
        "securities": securities,
        "real_label_versions": labels,
        "price_contracts": {
            "v43": "VERIFIED_REGULAR_OPEN_AUCTION_AND_CLOSE_NOT_MET",
            "v44": "SEPARATE_UNADJUSTED_DAILY_BAR_OPEN_TO_CLOSE_PRICE_RETURN_PENDING_CURRENCY_AND_FUTURE_SIGNAL",
        },
        "source_docs": [
            "https://open.moomoo.com/zh-cn/api/quote/basic-data/history-kline",
            "https://open.moomoo.com/api/overview/getting-started",
            "https://www.nyse.com/trade/hours-calendars",
        ],
        "config_sha256": sha(ROOT / "configs/news_sentiment_factor_v44_20261117.json"),
        "parser_sha256": sha(ROOT / "scripts/news_sentiment_factor_v44/labels.py"),
        "pilot_receipt_sha256": sha(PUBLIC / "price_pilot_redacted.json"),
        "supplement_receipt_sha256": sha(PUBLIC / "price_supplement_redacted.json"),
    }
    path = PUBLIC / "price_qualification_redacted.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(read(), ensure_ascii=False))
