"""US.QQQ daily bars from the frozen moomoo research snapshot, read-only and hash-checked.

Series: US.QQQ|DAY|a0 (autype 0, unadjusted) is the main price series; QQQ had no split in the study window (the last one
was 2000-03), which check_splits verifies, so a0 is split-comparable. US.QQQ|DAY|a1 (autype 1, moomoo forward adjustment
for cash dividends) is used only for the dividend-inclusive return attachment; the a1/a0 ratio steps mark ex-dividend dates.
Bars: regular session, exchange local date (time_key is 16:00 America/New_York, time_zone -5 in the vendor field).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import calendar_us as cal

SNAPSHOT_SHA256 = "c5b5ec22b1d3773f6a0345004080d4d2ff10e4cc7d48be21fe13a99cbc6ebe7e"
MAIN, ADJ = "US.QQQ|DAY|a0", "US.QQQ|DAY|a1"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


@dataclass
class Bars:
    dates: list = field(default_factory=list)
    open: list = field(default_factory=list)
    high: list = field(default_factory=list)
    low: list = field(default_factory=list)
    close: list = field(default_factory=list)
    adj_close: list = field(default_factory=list)
    eligible: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def index(self) -> dict:
        return {d: i for i, d in enumerate(self.dates)}


def load(path: str | Path, expect_sha: str = SNAPSHOT_SHA256, start: str | None = None) -> Bars:
    path = Path(path)
    got = sha256(path)
    if expect_sha and got != expect_sha:
        raise ValueError(f"snapshot sha256 {got[:12]} != expected {expect_sha[:12]}")
    db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    q = ("select a.local_date,a.open,a.high,a.low,a.close,a.research_eligible,b.close,a.time_zone_raw,a.flags_json "
         "from observations a left join observations b on b.series_id=? and b.local_date=a.local_date "
         "where a.series_id=? " + ("and a.local_date>=? " if start else "") + "order by a.local_date")
    rows = db.execute(q, (ADJ, MAIN, start) if start else (ADJ, MAIN)).fetchall()
    archives = db.execute("select name, sha256, manifest_files from input_archives order by name").fetchall()
    db.close()
    b = Bars()
    for d, o, h, l, c, e, ac, tz, fl in rows:
        b.dates.append(date.fromisoformat(d)); b.open.append(o); b.high.append(h); b.low.append(l); b.close.append(c)
        b.adj_close.append(ac); b.eligible.append(bool(e))
    b.meta = {"vendor": "moomoo (Futu OpenAPI history K-line, read-only export)", "security": "US.QQQ Invesco QQQ Trust (ETF)",
              "snapshot_sha256": got, "snapshot_file": path.name, "series_main": MAIN, "series_dividend_adjusted": ADJ,
              "adjustment_main": "none (autype 0); no split in the window, see check_splits",
              "adjustment_dividend": "moomoo forward adjustment autype 1 (cash dividends)",
              "session": "regular session daily bars, exchange local date, close 16:00 ET (13:00 on early-close days)",
              "time_zone": "America/New_York (vendor time_zone field -5, time_key at the session close)",
              "source_archives": [{"name": n, "sha256": s, "files": m} for n, s, m in archives],
              "first_date": b.dates[0].isoformat() if b.dates else None, "last_date": b.dates[-1].isoformat() if b.dates else None}
    return b


def check_calendar(b: Bars, start: date, end: date) -> dict:
    have = {d for d in b.dates if start <= d <= end}
    rule = set(cal.trading_days(start, min(end, b.dates[-1])))
    missing = sorted(rule - have)
    extra = sorted(have - rule)
    ineligible = [d.isoformat() for d, e in zip(b.dates, b.eligible) if start <= d <= end and not e]
    return {"window": [start.isoformat(), end.isoformat()], "rule_trading_days_to_data_end": len(rule), "bars": len(have),
            "missing_bars": [d.isoformat() for d in missing], "bars_on_rule_holidays": [d.isoformat() for d in extra],
            "not_research_eligible": ineligible,
            "tail_gap_after_data_end": [d.isoformat() for d in cal.trading_days(b.dates[-1], end) if d > b.dates[-1]],
            "early_closes_in_window": sorted(d.isoformat() for y in range(start.year, end.year + 1) for d in cal.early_closes(y) if start <= d <= end)}


def check_splits(b: Bars, start: date) -> dict:
    """A split shows as a one-day close ratio far outside the market's range together with a step in the a1/a0 ratio
    that is not a dividend-sized step. Report day moves beyond 20% and the a1/a0 steps (ex-dividend dates)."""
    big, steps = [], []
    for i in range(1, len(b.dates)):
        if b.dates[i] < start:
            continue
        r = b.close[i] / b.close[i - 1] - 1
        if abs(r) > 0.20:
            big.append({"date": b.dates[i].isoformat(), "close_change": round(r, 4)})
        if b.adj_close[i] and b.adj_close[i - 1]:
            k0, k1 = b.adj_close[i - 1] / b.close[i - 1], b.adj_close[i] / b.close[i]
            s = k1 / k0 - 1
            if abs(s) > 1e-6:
                steps.append({"date": b.dates[i - 1].isoformat(), "factor_step": round(s, 6)})
    divs = [x for x in steps if x["factor_step"] > 5e-4]
    return {"day_moves_over_20pct": big, "split_detected": any(abs(x["factor_step"]) > 0.05 for x in steps),
            "note": "moomoo's forward adjustment is not a pure multiplicative factor, so the a1/a0 ratio drifts slightly every "
                    "day; steps above 0.05% are counted as ex-dividend dates",
            "ex_dividend_like_steps": len(divs), "ex_dividend_like_dates": [x["date"] for x in divs]}
