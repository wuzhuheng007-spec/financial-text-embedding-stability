"""Six horizons, in exchange sessions, against one fixed price snapshot.

  R_desc(t, h)      = close[t+h] / close[t] - 1          t = session whose signal window ended; descriptive only
  R_next_open(t, h) = close[k+h-1] / open[k] - 1         k = first session whose regular open is after the signal
                                                         was committed; h = 1 is that open to that close

Both use the same adjusted series, so splits are treated the same way on both ends. Whether the vendor's adjusted
price also folds in dividends is not established here, so these are price returns of the adjusted series and are
not called total returns. Missing is None with a reason, never 0. QQQ's own return is a description of the market
state in this branch; it is not a factor-residual return.
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path

from .config import HORIZONS
from .sessions import Calendar


class Prices:
    def __init__(self, bars: dict[str, tuple[float | None, float | None]], *, series_id: str, input_sha256: str | None):
        self.bars, self.series_id, self.input_sha256 = bars, series_id, input_sha256
        self.first, self.last = (min(bars), max(bars)) if bars else (None, None)

    @classmethod
    def from_snapshot(cls, path: str | Path, series_id: str, expected_sha256: str | None = None) -> "Prices":
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        sha = h.hexdigest()
        if expected_sha256 and sha != expected_sha256:
            raise ValueError("price snapshot does not match the registered hash")
        db = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
        try:
            rows = db.execute("SELECT local_date, open, close FROM observations WHERE series_id=? AND research_eligible=1", (series_id,)).fetchall()
        finally:
            db.close()
        return cls({d: (o, c) for d, o, c in rows}, series_id=series_id, input_sha256=sha)

    def close(self, d: str):
        v = self.bars.get(d, (None, None))[1]
        return v if v is not None and v > 0 else None

    def open(self, d: str):
        v = self.bars.get(d, (None, None))[0]
        return v if v is not None and v > 0 else None


def alignment(cal: Calendar, prices: Prices) -> dict:
    """One-to-one check of exchange sessions against price dates over the range both cover."""
    if not prices.bars or not cal.dates:
        return {"status": "NO_OVERLAP", "sessions_without_price": [], "prices_without_session": []}
    a, b = max(cal.dates[0], prices.first), min(cal.dates[-1], prices.last)
    sess = {d for d in cal.dates if a <= d <= b}; px = {d for d in prices.bars if a <= d <= b}
    return {"status": "ALIGNED" if sess == px else "MISMATCH", "overlap_start": a, "overlap_end": b, "sessions": len(sess),
            "sessions_without_price": sorted(sess - px), "prices_without_session": sorted(px - sess),
            "calendar_last_session": cal.dates[-1], "price_last_date": prices.last,
            "price_dates_after_calendar_end": sum(1 for d in prices.bars if d > cal.dates[-1])}


def _missing_at(prices: Prices, d: str, where: str) -> str:
    return f"INSUFFICIENT_FUTURE_PRICES_AT_{where}" if prices.last and d > prices.last else f"MISSING_PRICE_AT_{where}"


def pair(cal: Calendar, prices: Prices | None, *, session_date: str, h: int, available_at: datetime | None, availability: str) -> dict:
    out = {"session_date": session_date, "h": h, "anchor_date": session_date, "anchor_close": None, "horizon_date": None, "desc_return": None,
           "desc_missing_reason": None, "entry_date": None, "entry_open": None, "exit_date": None, "next_open_return": None,
           "next_open_missing_reason": None, "entry_availability": availability, "series_id": prices.series_id if prices else None,
           "price_input_sha256": prices.input_sha256 if prices else None, "return_kind": "PRICE_RETURN_OF_ADJUSTED_SERIES_DIVIDEND_TREATMENT_UNVERIFIED",
           "residual_method": None, "desc_is_tradable": False}
    if h not in HORIZONS:
        raise ValueError("horizon outside the frozen set")
    if prices is None:
        out["desc_missing_reason"] = out["next_open_missing_reason"] = "NO_COMPARISON_ASSET_OR_PRICE_INPUT"
        return out
    i = cal.index(session_date)
    if i is None:
        out["desc_missing_reason"] = out["next_open_missing_reason"] = "CALENDAR_NOT_COVERED"
        return out
    # descriptive, close to close
    j = cal.shift(i, h)
    if j is None:
        out["desc_missing_reason"] = "INSUFFICIENT_FUTURE_SESSIONS_IN_CALENDAR"
    else:
        out["horizon_date"] = cal.dates[j]
        c0, c1 = prices.close(cal.dates[i]), prices.close(cal.dates[j])
        out["anchor_close"] = c0
        if c0 is None:
            out["desc_missing_reason"] = _missing_at(prices, cal.dates[i], "ANCHOR")
        elif c1 is None:
            out["desc_missing_reason"] = _missing_at(prices, cal.dates[j], "HORIZON")
        else:
            out["desc_return"] = c1 / c0 - 1.0
    # first open after the signal was available
    if available_at is None:
        out["next_open_missing_reason"] = "SIGNAL_NOT_COMMITTED"
        return out
    k = cal.first_open_after(available_at)
    if k is None:
        out["next_open_missing_reason"] = "NO_OPEN_AFTER_COMMIT_IN_CALENDAR"
        return out
    out["entry_date"] = cal.dates[k]
    x = cal.shift(k, h - 1)
    if x is None:
        out["next_open_missing_reason"] = "INSUFFICIENT_FUTURE_SESSIONS_IN_CALENDAR"
        return out
    out["exit_date"] = cal.dates[x]
    o, c = prices.open(cal.dates[k]), prices.close(cal.dates[x])
    out["entry_open"] = o
    if o is None:
        out["next_open_missing_reason"] = _missing_at(prices, cal.dates[k], "ENTRY")
    elif c is None:
        out["next_open_missing_reason"] = _missing_at(prices, cal.dates[x], "EXIT")
    else:
        out["next_open_return"] = c / o - 1.0
    return out


def controls(cal: Calendar, prices: Prices, session_date: str) -> dict:
    """Past 1/5/20-session returns and 20-session realised volatility, from closes up to and including the day."""
    i = cal.index(session_date)
    out = {"ret_1": None, "ret_5": None, "ret_20": None, "rv_20": None}
    if i is None:
        return out
    c = [prices.close(cal.dates[j]) if j >= 0 else None for j in range(i - 20, i + 1)]
    for n in (1, 5, 20):
        if c[-1] is not None and c[-1 - n] is not None:
            out[f"ret_{n}"] = c[-1] / c[-1 - n] - 1.0
    if all(v is not None for v in c):
        r = [c[j] / c[j - 1] - 1.0 for j in range(1, 21)]
        m = sum(r) / 20
        out["rv_20"] = (sum((x - m) ** 2 for x in r) / 19) ** 0.5
    return out
