"""Frozen time and coverage gates for the future factor experiment."""
from __future__ import annotations

from bisect import bisect_right


def proxy_eligible_from(announcement_date: str, trading_days: list[str]) -> str | None:
    """After the next *complete* trading session, use no earlier than the one after it."""
    complete_session = bisect_right(trading_days, announcement_date)
    usable = complete_session + 1
    return trading_days[usable] if usable < len(trading_days) else None


def split_of(day: str) -> str | None:
    if "2025-04-01" <= day <= "2026-03-31":
        return "TRAIN"
    if "2026-04-01" <= day <= "2026-05-31":
        return "VALIDATION_REUSED"
    if "2026-06-01" <= day <= "2026-07-31":
        return "CONTAMINATED_HOLDOUT"
    return None


def label_window_status(signal_day: str, horizon: int, trading_days: list[str]) -> str:
    if horizon <= 0:
        raise ValueError("HORIZON_MUST_BE_POSITIVE")
    try:
        pos = trading_days.index(signal_day)
    except ValueError:
        return "SIGNAL_DAY_NOT_IN_CALENDAR"
    end = pos + horizon
    if end >= len(trading_days):
        return "NOT_MATURED_IN_WINDOW"
    return "ELIGIBLE" if split_of(signal_day) == split_of(trading_days[end]) else "CROSSES_SPLIT_BOUNDARY"


def news_count_or_missing(event_ids: list[str], *, coverage_complete: bool) -> int | None:
    """A zero is admissible only for a source whose capture coverage was checked complete."""
    return len(set(event_ids)) if coverage_complete else None
