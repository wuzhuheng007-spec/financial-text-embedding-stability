"""Offline import of one verified private moomoo MCP probe projection.

No MCP call is made here. Quote receipt time is an interval, so the quote is
quarantined separately from timestamp-qualified quotes_or_bars.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .store import Store, digest, utc_now


def import_private_projection(projection_path: Path, db_path: Path,
                              requested_symbols: list[str]) -> dict[str, Any]:
    projection_path = Path(projection_path)
    projection = json.loads(projection_path.read_text(encoding="utf-8"))
    if projection.get("schema") != "moomoo_private_probe_projection_v1":
        raise ValueError("unknown moomoo private projection schema")
    raw_path = Path(projection["raw_jsonl_path"])
    if not raw_path.is_file():
        raise FileNotFoundError("private raw MCP response file is unavailable for hash verification")
    raw_hash = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    if raw_hash != projection["raw_jsonl_sha256"]:
        raise ValueError("private raw moomoo MCP response hash mismatch")
    basic = projection["basicinfo"]
    if basic["tool"] != "quote_stock_basicinfo" or basic["ret_code"] != 0:
        raise ValueError("basicinfo probe did not return a successful official tool result")
    rows = basic["rows"]
    if len(rows) != len(requested_symbols):
        raise ValueError("basicinfo response row count differs from frozen observation pool")
    by_symbol: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = row.get("requested_code")
        if not isinstance(code, str) or "." not in code:
            raise ValueError("unparseable requested provider code")
        symbol = code.split(".", 1)[1]
        if symbol in by_symbol:
            raise ValueError("duplicate requested symbol in provider basicinfo")
        if symbol not in requested_symbols or row.get("returned_code") != code or \
                row.get("exchange") != "US" or \
                row.get("verification_status") != "VERIFIED_EXACT_CODE_EXCHANGE":
            raise ValueError("provider basicinfo row failed exact-code/exchange verification")
        by_symbol[symbol] = row
    if set(by_symbol) != set(requested_symbols):
        raise ValueError("provider basicinfo response did not cover exact frozen pool")
    verified_now = utc_now()
    inserted_resolutions = 0
    with Store(db_path) as store:
        store.seed_candidate_instruments(requested_symbols)
        for symbol in requested_symbols:
            row = by_symbol[symbol]
            before = store.connection.execute("""SELECT COUNT(*) FROM instrument_resolutions
              WHERE candidate_id=? AND verified_id=? AND evidence_hash=?""",
              (f"CANDIDATE:{symbol}",row["returned_code"],raw_hash)).fetchone()[0]
            store.record_instrument_resolution(
                requested_symbol=symbol, verified_id=row["returned_code"],
                provider_code=row["returned_code"], market=row["exchange"],
                verified_at=verified_now, evidence_hash=raw_hash,
                source_tool=basic["tool"],
                metadata={"stock_type": row.get("stock_type"), "state": row.get("state"),
                          "suspension": row.get("suspension"),
                          "source_session_lower_bound_utc": projection["session_time_lower_bound_utc"],
                          "source_session_upper_bound_utc": projection["session_time_upper_bound_utc"],
                          "verification_time_semantics": "LOCAL_OFFLINE_IMPORT_TIME"})
            inserted_resolutions += int(before == 0)
        quote = projection["quote"]
        if quote.get("tool") != "quote_stock_quote" or quote.get("ret_code") != 0:
            raise ValueError("quote probe result was not successful")
        qrow = quote["row"]
        if qrow.get("code") not in {row["returned_code"] for row in rows}:
            raise ValueError("quote code was not verified by basicinfo")
        if projection.get("per_call_received_at_utc") is not None:
            raise ValueError("unexpected exact receipt timestamp; review new projection contract")
        probe_created = store.insert_probe_snapshot(
            probe_id=digest({"source_hash": raw_hash, "tool": quote["tool"], "row": qrow}),
            provider="moomoo_mcp", tool_name=quote["tool"], instrument_id=qrow["code"],
            provider_time=quote["provider_data_time_utc"],
            receipt_lower_bound=projection["session_time_lower_bound_utc"],
            receipt_upper_bound=projection["session_time_upper_bound_utc"],
            delay_status=quote["delay_status"], market_session=quote["market_session"],
            payload=qrow, source_hash=raw_hash)
    return {"mode": "OFFLINE_PROBE_IMPORT", "provider_calls": 0,
            "source_raw_sha256": raw_hash,
            "requested_symbols": len(requested_symbols),
            "verified_resolutions_inserted": inserted_resolutions,
            "probe_snapshots_inserted": int(probe_created),
            "qualified_quote_or_bar_rows_inserted": 0,
            "quote_delay_status": quote["delay_status"],
            "quote_market_session": quote["market_session"],
            "quote_receipt_time": "SESSION_INTERVAL_ONLY",
            "interpretation": "Provider quote is a historical snapshot with unknown delay/session and no exact local receipt timestamp"}
