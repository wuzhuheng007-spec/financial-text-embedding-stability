"""Write a value-free receipt from the private local feed cache."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .cli import load_config
from .collector import ROOT, owner_id, under_root


def _query(db: sqlite3.Connection, sql: str, params: tuple = ()) -> list[tuple]:
    return db.execute(sql, params).fetchall()


def _table_digest(db: sqlite3.Connection, table: str) -> str:
    if table not in {"auth_user", "core_watchlistitem", "core_userpreferences", "core_publicfactcard"}:
        raise ValueError("Unexpected account comparison table")
    rows = _query(db, f"SELECT * FROM {table} ORDER BY id")
    return hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()


def build_receipt(config_path: Path, baseline_backup: Path | None = None) -> dict:
    config = load_config(config_path)
    user_id = owner_id(config)
    cache_path = under_root(config["cache_db"])
    now = datetime.now(timezone.utc)
    result = {
        "schema": "quant_portal_personal_feeds_redacted_receipt_v1",
        "observed_at_utc": now.isoformat().replace("+00:00", "Z"),
        "observed_at_asia_shanghai": now.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "loopback_only": True,
        "personal_mode": True,
        "public_rebroadcast_enabled": False,
        "trade_calls": 0,
        "provider_account_quota": "UNKNOWN",
        "jin10_vendor_polling_limit": "NOT_CONFIRMED",
        "jin10_local_poll_interval_seconds": config["jin10"]["poll_interval_seconds"],
        "jin10_local_max_business_calls_per_utc_day": config["jin10"]["max_business_calls_per_utc_day"],
        "portal_cache_poll_interval_seconds": 5,
        "browser_visual_acceptance": "NOT_RUN_CUA_POLICY_STOP",
    }
    if cache_path.is_file():
        with closing(sqlite3.connect(cache_path)) as db:
            db.row_factory = sqlite3.Row
            providers = []
            for row in db.execute("SELECT * FROM provider_state ORDER BY provider"):
                item = {key: row[key] for key in (
                    "provider", "state", "heartbeat_at", "last_success_at",
                    "last_received_at", "last_source_at", "last_error_class")}
                if row["provider"] == "jin10":
                    item["mcp_http_requests_total_since_cache_creation"] = row["requests_total"]
                    item["business_calls_today_utc"] = row["business_calls_today"]
                else:
                    item["request_count_status"] = "NOT_INSTRUMENTED_WEBSOCKET_PUSH"
                providers.append(item)
            result["providers"] = providers
            result["quote_symbols"] = [dict(row) for row in db.execute("""
                SELECT symbol, MIN(provider_time) AS earliest_source_time,
                  MAX(provider_time) AS latest_source_time,
                  MAX(received_at) AS latest_received_at, COUNT(*) AS saved_rows,
                  COUNT(DISTINCT price_text) AS distinct_display_prices,
                  COUNT(DISTINCT provider_time) AS distinct_source_times
                FROM quotes WHERE owner_id=? GROUP BY symbol ORDER BY symbol
            """, (user_id,))]
            result["symbol_status"] = [dict(row) for row in db.execute(
                "SELECT symbol,state,reason FROM symbol_state WHERE owner_id=? ORDER BY symbol", (user_id,))]
            result["jin10_news_items"] = db.execute(
                "SELECT COUNT(*) FROM news_items WHERE owner_id=?", (user_id,)).fetchone()[0]
            result["jin10_news_versions"] = db.execute(
                "SELECT COUNT(*) FROM news_versions WHERE owner_id=?", (user_id,)).fetchone()[0]
            result["jin10_key_kinds"] = [dict(row) for row in db.execute(
                "SELECT source_key_kind,COUNT(*) AS items FROM news_items WHERE owner_id=? GROUP BY source_key_kind",
                (user_id,))]
    else:
        result["cache_status"] = "MISSING"
    if baseline_backup is not None:
        backup = baseline_backup.resolve()
        if not backup.is_file() or not backup.is_relative_to(ROOT / "apps/quant_portal/backups"):
            raise ValueError("Baseline must be an existing private portal backup")
        tables = ("auth_user", "core_watchlistitem", "core_userpreferences", "core_publicfactcard")
        with closing(sqlite3.connect(under_root(config["portal_db"]))) as current, closing(sqlite3.connect(backup)) as original:
            result["owner_data_preservation"] = {table: {
                "same_rows_as_prechange_backup": _table_digest(current, table) == _table_digest(original, table),
                "current_row_count": current.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],
            } for table in tables}
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/quant_portal_personal_feeds_v1.json")
    parser.add_argument("--baseline-backup", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt = build_receipt(args.config, args.baseline_backup)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes((json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(f"REDACTED_RECEIPT_WRITTEN provider_count={len(receipt.get('providers', []))}")


if __name__ == "__main__":
    main()
