"""Parse moomoo's separate US quote-session fields without inventing child times."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

SESSIONS = ("PREMARKET", "REGULAR", "AFTERHOURS", "OVERNIGHT")
CHILDREN = (("PREMARKET", "pre_market"), ("AFTERHOURS", "after_market"),
            ("OVERNIGHT", "overnight"))
NY = ZoneInfo("America/New_York")


def _price(value: object) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return str(number) if number.is_finite() and number > 0 else None


def _utc_millis(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError, OSError):
        return None


def _trade_date(source_at: str | None, parent_date: object) -> tuple[str | None, str]:
    if source_at is None:
        return None, "CHILD_TIME_UNKNOWN"
    actual = datetime.fromisoformat(source_at.replace("Z", "+00:00")).astimezone(NY).date().isoformat()
    if isinstance(parent_date, str) and parent_date:
        if parent_date == actual:
            return parent_date, "PARENT_DATE_MATCHES_SOURCE_TIME_NY"
        return None, "PARENT_DATE_CONFLICT"
    # A Sunday-night session can belong to Monday's trading day. A local
    # calendar date is not an official trade-date identifier.
    return None, "SOURCE_TIME_NEW_YORK_DATE_ONLY"


def parse_stock_quote_row(row: dict, *, received_at: str, raw_sha256: str) -> tuple[list[dict], dict[str, str]]:
    """REST parent last_price has data_time; child prices have no documented time."""
    if not isinstance(row, dict) or not isinstance(row.get("code"), str):
        raise ValueError("Missing quote code")
    code = row["code"]
    symbol = code.split(".", 1)[1] if code.startswith("US.") else code
    parent_date = row.get("data_date") if isinstance(row.get("data_date"), str) else None
    parsed = []
    states = {}
    regular = _price(row.get("last_price"))
    source = _utc_millis(row.get("data_time"))
    if regular is not None and source is not None:
        date, date_basis = _trade_date(source, parent_date)
        parsed.append({"symbol": symbol, "provider_code": code, "session": "REGULAR",
                       "price_text": regular, "currency": None, "source_at": source,
                       "received_at": received_at, "provider_field_path": "data.quote_list[].last_price",
                       "source_time_field_path": "data.quote_list[].data_time",
                       "trade_date": date, "date_basis": date_basis,
                       "parent_date_hint": parent_date, "parent_source_at_hint": source,
                       "delay_status": "UNKNOWN",
                       "source_channel": "REST_STOCK_QUOTE", "raw_sha256": raw_sha256})
        states["REGULAR"] = "DATE_CONFLICT" if date_basis == "PARENT_DATE_CONFLICT" else "HAS_SOURCE_TIME"
    else:
        states["REGULAR"] = "NO_OBSERVED_VALUE_OR_TIME"
    for session, field in CHILDREN:
        child = row.get(field)
        price = _price(child.get("price")) if isinstance(child, dict) else None
        if price is None:
            states[session] = "NO_OBSERVED_VALUE"
            continue
        # The published stock-quote child schema contains no time field. Only
        # an actual explicit child timestamp may fill source_at; the parent's
        # data_time and this fetch's received_at are not substitutes.
        child_time_field = ("data_time_ms" if isinstance(child.get("data_time_ms"), (int,float))
                            and not isinstance(child.get("data_time_ms"),bool) else None)
        source = _utc_millis(child.get(child_time_field)) if child_time_field else None
        date, date_basis = _trade_date(source, None)
        parsed.append({"symbol": symbol, "provider_code": code, "session": session,
                       "price_text": price, "currency": None, "source_at": source,
                       "received_at": received_at,
                       "provider_field_path": f"data.quote_list[].{field}.price",
                       "source_time_field_path":
                           f"data.quote_list[].{field}.{child_time_field}" if child_time_field else None,
                       "trade_date": date, "date_basis": date_basis,
                       "parent_date_hint": parent_date,
                       "parent_source_at_hint": _utc_millis(row.get("data_time")),
                       "delay_status": "UNKNOWN",
                       "source_channel": "REST_STOCK_QUOTE", "raw_sha256": raw_sha256})
        states[session] = "HAS_SOURCE_TIME" if source else "SNAPSHOT_TIME_UNKNOWN"
    return parsed, states


def parse_push_children(quote: dict, *, raw_sha256: str) -> list[dict]:
    """Reuse the existing QUOTE subscription; don't label its parent as regular."""
    data = quote.get("payload", {}).get("provider_data")
    if not isinstance(data, dict):
        return []
    code = quote.get("instrument_id")
    if not isinstance(code, str) or not code.startswith("US."):
        return []
    symbol = code.split(".", 1)[1]
    result = []
    for session, field in CHILDREN:
        child = data.get(field)
        price = _price(child.get("price")) if isinstance(child, dict) else None
        if price is None:
            continue
        child_time_field = ("data_time_ms" if isinstance(child.get("data_time_ms"), (int,float))
                            and not isinstance(child.get("data_time_ms"),bool) else None)
        source = _utc_millis(child.get(child_time_field)) if child_time_field else None
        date, basis = _trade_date(source, None)
        result.append({"symbol": symbol,"provider_code": code,"session": session,
                       "price_text": price,"currency": None,"source_at": source,
                       "received_at": quote["received_at"],
                       "provider_field_path": f"data.{field}.price",
                       "source_time_field_path":
                           f"data.{field}.{child_time_field}" if child_time_field else None,
                       "trade_date": date,"date_basis": basis,"parent_date_hint": None,
                       "parent_source_at_hint": quote.get("provider_time"),
                       "delay_status": "UNKNOWN","source_channel": "WS_QUOTE",
                       "raw_sha256": raw_sha256})
    return result
