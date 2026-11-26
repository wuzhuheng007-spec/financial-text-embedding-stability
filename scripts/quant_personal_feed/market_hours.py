"""When the moomoo US quote push is expected, from the registered XNYS session file (holidays and early closes).

The feed health check used to call the push stale whenever the last push was older than 300 s, so every night,
weekend and holiday (2026-11-26, Thanksgiving) read as a failure. A missing push is only a fault while a push is
expected: regular hours, plus the extended sessions the subscription covers (pre-market from 04:00 ET, after-hours
until 4 h after the close, i.e. 20:00 ET, or 17:00 ET on an early-close day). Overnight trading is off unless set.

Outside the calendar's range the answer is UNKNOWN, never "closed": the caller keeps the old, strict rule."""
from __future__ import annotations

import csv
import hashlib
from datetime import datetime, timedelta
from pathlib import Path

CALENDAR = Path("research/x_stance_v1/calendar/sessions_xnys_20191101_20261231.csv")
CALENDAR_SHA256 = "b14e9f02fc19a50b9232f7340e1cc3ed074a17f91b5e645e5be0242176f1c0c0"
PRE_OPEN = timedelta(hours=5, minutes=30)          # 04:00 ET before a 09:30 ET open
AFTER_CLOSE = timedelta(hours=4)                   # 20:00 ET after 16:00; 17:00 ET after a 13:00 early close


def load(root: Path) -> dict:
    p = root / CALENDAR
    raw = p.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    rows = [(r["session_date"], datetime.fromisoformat(r["open_utc"]), datetime.fromisoformat(r["close_utc"]), r.get("session_type"))
            for r in csv.DictReader(raw.decode("utf-8").splitlines())]
    return {"rows": sorted(rows), "sha256": sha, "registered": sha == CALENDAR_SHA256, "path": str(CALENDAR)}


def phase(cal: dict, now: datetime, *, extended: bool = True, overnight: bool = False) -> dict:
    """{"phase": REGULAR | PRE_MARKET | AFTER_HOURS | CLOSED | UNKNOWN, "push_expected": bool | None, "session": ...}"""
    rows = cal["rows"]
    if not rows or not cal.get("registered") or now < rows[0][1] - PRE_OPEN or now > rows[-1][2] + AFTER_CLOSE:
        return {"phase": "UNKNOWN", "push_expected": None, "session": None, "calendar_sha256": cal.get("sha256")}
    for d, o, c, kind in rows:
        if o - PRE_OPEN <= now < c + AFTER_CLOSE:
            ph = "REGULAR" if o <= now < c else ("PRE_MARKET" if now < o else "AFTER_HOURS")
            exp = ph == "REGULAR" or extended
            return {"phase": ph, "push_expected": exp, "session": d, "session_type": kind, "open_utc": o.isoformat(), "close_utc": c.isoformat(), "calendar_sha256": cal["sha256"]}
    nxt = next((r for r in rows if r[1] > now), None)
    return {"phase": "CLOSED", "push_expected": bool(overnight), "session": None, "next_session": nxt[0] if nxt else None,
            "next_open_utc": nxt[1].isoformat() if nxt else None, "calendar_sha256": cal["sha256"]}


def judge(cal: dict | None, now: datetime, push_age: float | None, limit: float, **kw) -> dict:
    """The business verdict for the moomoo quote push."""
    ph = phase(cal, now, **kw) if cal else {"phase": "UNKNOWN", "push_expected": None}
    fresh = push_age is not None and push_age <= limit
    if fresh:
        verdict = "OK"
    elif ph["push_expected"] is False:
        verdict = "MARKET_CLOSED"                   # healthy: nothing is due; the last push time stays visible
    elif ph["push_expected"] is None:
        verdict = "STALE_OR_NONE"                   # calendar unknown: the old, strict rule
    else:
        verdict = "STALE_IN_SESSION" if push_age is not None else "NO_PUSH_IN_SESSION"
    return {"business": verdict, "market": ph, "ok": verdict in ("OK", "MARKET_CLOSED")}
