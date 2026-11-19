"""Display references for the v20 UI: explicit instrument currency and change bases.

Currency comes only from an explicit per-instrument metadata file (owner-confirmed list keyed
by provider code). A market prefix is never treated as a currency.

Change is computed only against a base that belongs to the same supplier message as the shown
price, so a session's price is never compared with an unrelated or stale reference:
  * extended-hours quote (AFTERHOURS / PREMARKET / OVERNIGHT): base = data.last_price of the
    same raw message (the regular-session price carried alongside it);
  * regular / legacy quote: base = data.prev_close_price of the raw message that produced the
    quote, identified by symbol link + data.data_time_ms equal to the quote's provider time (ms)
    + data.last_price equal to the shown price. Price equality alone is never enough (DATA-01).
Otherwise no change is returned. Read-only; never calls a provider.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
METADATA_PATH = ROOT / "configs/quant_portal_instruments_v20_20261119.json"
EXTENDED = {"AFTERHOURS", "PREMARKET", "OVERNIGHT"}


def instrument_metadata(path: Path = METADATA_PATH) -> dict:
    """Return {symbol: {...}} from the explicit metadata file; empty when absent or invalid."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if doc.get("schema") != "quant_portal_instruments_v20":
        return {}
    out = {}
    for code, row in (doc.get("instruments") or {}).items():
        symbol = row.get("symbol")
        currency = row.get("currency")
        if symbol and isinstance(currency, str) and len(currency) == 3 and currency.isupper():
            out[symbol] = {"provider_code": code, "currency": currency, "short_name": row.get("short_name"),
                           "currency_basis": row.get("currency_basis"), "confirmed_at": row.get("confirmed_at")}
    return out


def _dec(value) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() and d > 0 else None


def _q2(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _raw(db: sqlite3.Connection, sha: str | None) -> dict | None:
    if not sha:
        return None
    row = db.execute("SELECT raw_json, received_at FROM session_raw_responses WHERE sha256=?", (sha,)).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row[0])
    except ValueError:
        return None
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        data = data["data"]
    return {"data": data, "received_at": row[1]} if isinstance(data, dict) else None


def change_for(price_text: str, base_text, base_kind: str, base_field: str, raw_received_at) -> dict | None:
    price, base = _dec(price_text), _dec(base_text)
    if price is None or base is None:
        return None
    diff = price - base
    return {"base_text": str(base_text), "base_kind": base_kind, "base_field": base_field,
            "raw_received_at": raw_received_at, "change_text": _q2(diff),
            "change_pct_text": _q2(diff / base * 100)}


def quote_changes(db_path: Path, owner_id: int, latest_quotes: dict) -> dict:
    """Return {symbol: change dict} for the shown (latest) quote of each symbol, where attributable."""
    if not Path(db_path).is_file():
        return {}
    out = {}
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"session_quotes", "session_raw_responses"} <= tables:
            return {}
        for symbol, quote in (latest_quotes or {}).items():
            if not quote or not quote.get("price_text"):
                continue
            session = quote.get("session")
            if session in EXTENDED:
                row = db.execute("""SELECT raw_sha256 FROM session_quotes WHERE owner_id=? AND symbol=? AND session=?
                    AND source_at IS ? AND price_text=? ORDER BY id DESC LIMIT 1""",
                    (owner_id, symbol, session, quote.get("source_at"), quote["price_text"])).fetchone()
                raw = _raw(db, row[0] if row else None)
                if raw:
                    change = change_for(quote["price_text"], raw["data"].get("last_price"),
                                        "REGULAR_LAST_SAME_MESSAGE", "data.last_price", raw["received_at"])
                    if change:
                        out[symbol] = change
            else:
                raw = _same_message_raw(db, owner_id, symbol, quote)
                if raw:
                    change = change_for(quote["price_text"], raw["data"].get("prev_close_price"),
                                        "PREV_CLOSE_SAME_MESSAGE", "data.prev_close_price", raw["received_at"])
                    if change:
                        out[symbol] = change
    return out


def _epoch_ms(iso: str | None) -> int | None:
    from datetime import datetime
    if not iso:
        return None
    try:
        parsed = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return round(parsed.timestamp() * 1000)


def _same_message_raw(db: sqlite3.Connection, owner_id: int, symbol: str, quote: dict, scan: int = 400) -> dict | None:
    """Find the raw message that produced a regular/legacy quote, by identity, not by price alone.

    A raw message qualifies only if it is linked to this symbol (through session_quotes), its own
    data.data_time_ms equals the quote's provider time to the millisecond, and its data.last_price
    equals the shown price. Equal prices on another message or trading day never qualify.
    Only the most recent `scan` linked rows are examined; an older quote gets no change.
    """
    target_ms = _epoch_ms(quote.get("source_at") or quote.get("provider_time"))
    price = _dec(quote.get("price_text"))
    if target_ms is None or price is None:
        return None
    seen = set()
    for (sha,) in db.execute("""SELECT raw_sha256 FROM session_quotes WHERE owner_id=? AND symbol=?
                                ORDER BY id DESC LIMIT ?""", (owner_id, symbol, scan)):
        if sha in seen:
            continue
        seen.add(sha)
        raw = _raw(db, sha)
        if not raw:
            continue
        data = raw["data"]
        try:
            msg_ms = int(data.get("data_time_ms"))
        except (TypeError, ValueError):
            continue
        if msg_ms == target_ms and _dec(data.get("last_price")) == price:
            return raw
    return None
