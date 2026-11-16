"""One source-time selection rule for the owner's watchlist, cards and chart."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

SESSION_RANK = {"REGULAR": 4, "OVERNIGHT": 3, "AFTERHOURS": 2, "PREMARKET": 1}


def _time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def choose_latest(symbol: str, session_rows: list[dict], legacy: dict | None = None) -> dict | None:
    """Ignore receipt time when choosing market recency; retain original precision."""
    candidates = []
    for row in session_rows:
        if row.get("symbol") != symbol or not row.get("price_text"):
            continue
        source = _time(row.get("source_at"))
        if source:
            candidates.append((source, SESSION_RANK.get(row.get("session"), 0),
                               _time(row.get("received_at")) or datetime.min.replace(tzinfo=timezone.utc),
                               row))
    if legacy and legacy.get("symbol") == symbol and legacy.get("price_text"):
        source = _time(legacy.get("provider_time"))
        if source:
            row = {"symbol": symbol, "provider_code": legacy.get("provider_code"),
                   "session": "UNKNOWN", "price_text": legacy["price_text"],
                   "currency": legacy.get("currency"), "source_at": legacy["provider_time"],
                   "received_at": legacy.get("received_at"), "trade_date": None,
                   "date_basis": None, "delay_status": legacy.get("delay_status"),
                   "source_channel": "LEGACY_QUOTE", "market_state": legacy.get("market_state")}
            candidates.append((source, 0, _time(row["received_at"]) or
                               datetime.min.replace(tzinfo=timezone.utc), row))
    if not candidates:
        return None
    return dict(max(candidates, key=lambda item: item[:3])[3])


def read_latest(db: sqlite3.Connection, owner_id: int, symbol: str) -> dict | None:
    db.row_factory = sqlite3.Row
    rows = [dict(row) for row in db.execute("""SELECT symbol,provider_code,session,price_text,currency,
      source_at,received_at,provider_field_path,source_time_field_path,trade_date,date_basis,
      parent_date_hint,parent_source_at_hint,
      delay_status,source_channel FROM session_quotes WHERE owner_id=? AND symbol=?
      ORDER BY julianday(source_at) DESC,id DESC LIMIT 20""", (owner_id, symbol))]
    legacy_row = db.execute("""SELECT symbol,provider_code,price_text,currency,provider_time,
      received_at,market_state,delay_status FROM quotes WHERE owner_id=? AND symbol=?
      ORDER BY id DESC LIMIT 1""", (owner_id, symbol)).fetchone()
    return choose_latest(symbol, rows, dict(legacy_row) if legacy_row else None)
