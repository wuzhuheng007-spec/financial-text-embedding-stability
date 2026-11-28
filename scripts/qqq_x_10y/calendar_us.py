"""NYSE trading calendar by rule (2010-2030): full-day holidays, one-off closures and 13:00 ET early closes.

Rules (NYSE Rule 7.2 and its published holiday lists):
- New Year's Day, MLK Day (3rd Mon Jan), Presidents' Day (3rd Mon Feb), Good Friday, Memorial Day (last Mon May),
  Juneteenth (from 2022), Independence Day, Labor Day (1st Mon Sep), Thanksgiving (4th Thu Nov), Christmas.
- A holiday on Saturday is observed the Friday before, on Sunday the Monday after; New Year's Day on a Saturday is NOT
  moved to the preceding Friday (the year-end close stays open).
- One-off closures: 2012-10-29/30 (storm), 2018-12-05 (President Bush), 2025-01-09 (President Carter).
- Early close 13:00 ET: the day after Thanksgiving; July 3 when it is a trading day and July 4 is a weekday; Dec 24 when it
  is a trading day.
The rules are cross-checked against the vendor's trading dates in prices.check_calendar (every rule trading day must have a
bar and no bar may fall on a rule holiday); early closes cannot be checked from daily bars and are listed as rule-derived.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
SPECIAL_CLOSURES = {date(2012, 10, 29), date(2012, 10, 30), date(2018, 12, 5), date(2025, 1, 9)}


def _nth_weekday(y, m, wd, n):
    d = date(y, m, 1)
    d += timedelta(days=(wd - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(y, m, wd):
    d = date(y, m + 1, 1) - timedelta(days=1) if m < 12 else date(y, 12, 31)
    return d - timedelta(days=(d.weekday() - wd) % 7)


def _easter(y):
    a = y % 19; b = y // 100; c = y % 100; d = b // 4; e = b % 4; f = (b + 8) // 25; g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30; i = c // 4; k = c % 4; l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451; month = (h + l - 7 * m + 114) // 31; day = ((h + l - 7 * m + 114) % 31) + 1
    return date(y, month, day)


def _observed(d, new_year=False):
    if d.weekday() == 5:
        return None if new_year else d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=None)
def holidays(y: int) -> frozenset:
    out = {_observed(date(y, 1, 1), new_year=True), _nth_weekday(y, 1, 0, 3), _nth_weekday(y, 2, 0, 3),
           _easter(y) - timedelta(days=2), _last_weekday(y, 5, 0), _observed(date(y, 7, 4)), _nth_weekday(y, 9, 0, 1),
           _nth_weekday(y, 11, 3, 4), _observed(date(y, 12, 25))}
    if y >= 2022:
        out.add(_observed(date(y, 6, 19)))
    out |= {d for d in SPECIAL_CLOSURES if d.year == y}
    return frozenset(x for x in out if x)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in holidays(d.year)


@lru_cache(maxsize=None)
def early_closes(y: int) -> frozenset:
    out = set()
    thx = _nth_weekday(y, 11, 3, 4) + timedelta(days=1)
    if is_trading_day(thx):
        out.add(thx)
    j3, j4 = date(y, 7, 3), date(y, 7, 4)
    if is_trading_day(j3) and j4.weekday() < 5:
        out.add(j3)
    d24 = date(y, 12, 24)
    if is_trading_day(d24):
        out.add(d24)
    return frozenset(out)


def close_time_et(d: date) -> time:
    return time(13, 0) if d in early_closes(d.year) else time(16, 0)


def close_utc(d: date) -> datetime:
    """The regular-session close of trading day d as an aware UTC datetime (DST handled by zoneinfo)."""
    return datetime.combine(d, close_time_et(d), ET).astimezone(UTC)


def open_utc(d: date) -> datetime:
    return datetime.combine(d, time(9, 30), ET).astimezone(UTC)


def trading_days(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def last_closed_trading_day(now_utc: datetime) -> date:
    d = now_utc.astimezone(ET).date()
    while True:
        if is_trading_day(d) and close_utc(d) <= now_utc:
            return d
        d -= timedelta(days=1)
