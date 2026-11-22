"""Exchange sessions and the two window kinds.

Sessions come from the registered XNYS file (actual opens and closes in UTC, early closes included). A timestamp
outside the file's range is not covered: it gets no session, and no weekday rule stands in for the file.

  session window  [previous actual close, this actual close)   -> the research signal of that session
  calendar window [New York midnight, next New York midnight)  -> the reading summary of that date
"""
from __future__ import annotations

import bisect
import csv
import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class Session:
    date: str
    open_utc: datetime
    close_utc: datetime
    session_type: str


class Calendar:
    def __init__(self, sessions: list[Session], sha256: str | None = None):
        self.sessions = sorted(sessions, key=lambda s: s.date)
        self.dates = [s.date for s in self.sessions]
        self.closes = [s.close_utc for s in self.sessions]
        self.opens = [s.open_utc for s in self.sessions]
        self.sha256 = sha256
        if len(set(self.dates)) != len(self.dates):
            raise ValueError("duplicate session dates")

    @classmethod
    def load(cls, path: str | Path, expected_sha256: str | None = None) -> "Calendar":
        raw = Path(path).read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        if expected_sha256 and sha != expected_sha256:
            raise ValueError("calendar file does not match the registered hash")
        rows = csv.DictReader(raw.decode("utf-8").splitlines())
        return cls([Session(r["session_date"], datetime.fromisoformat(r["open_utc"]), datetime.fromisoformat(r["close_utc"]), r["session_type"]) for r in rows], sha)

    def index(self, session_date: str) -> int | None:
        i = bisect.bisect_left(self.dates, session_date)
        return i if i < len(self.dates) and self.dates[i] == session_date else None

    def session_window(self, i: int) -> tuple[datetime, datetime] | None:
        """None for the first session of the file: its previous close is unknown."""
        return None if i <= 0 else (self.closes[i - 1], self.closes[i])

    def assign(self, ts: datetime) -> int | None:
        """Index of the one session whose window contains ts. A post at the exact close belongs to the NEXT session.
        Weekend and holiday posts fall into the following session, once."""
        i = bisect.bisect_right(self.closes, ts)
        return i if 0 < i < len(self.closes) else None

    def first_open_after(self, ts: datetime) -> int | None:
        """First session whose regular open is strictly after ts (a signal committed at the open cannot trade it)."""
        i = bisect.bisect_right(self.opens, ts)
        return i if i < len(self.opens) else None

    def shift(self, i: int, h: int) -> int | None:
        j = i + h
        return j if 0 <= j < len(self.sessions) else None

    def covers(self, d: str) -> bool:
        return bool(self.dates) and self.dates[0] <= d <= self.dates[-1]


def calendar_window(d: str | date) -> tuple[datetime, datetime]:
    """New York local midnight to the next local midnight, in UTC (23, 24 or 25 hours long)."""
    d = date.fromisoformat(d) if isinstance(d, str) else d
    a = datetime.combine(d, time(0), NY).astimezone(timezone.utc)
    b = datetime.combine(d + timedelta(days=1), time(0), NY).astimezone(timezone.utc)
    return a, b
