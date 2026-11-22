"""Extends the registered XNYS session file to the end of 2026 for the X stance study.

The registered file (results/calendar_round9/sessions.csv, bound to archived official announcements) ends on
2026-02-27. The rows after it are generated here from the 2026 closures and early closes shown on the NYSE hours
page, read on 2026-11-22. That page was read, not archived, so the extension is a lower grade of evidence than the
registered part and is marked as such in every added row and in the manifest. The registered rows are copied
byte for byte.

  python -m scripts.x_stance_v1.calendar_ext            writes research/x_stance_v1/calendar/
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
BASE = REPO / "results" / "calendar_round9" / "sessions.csv"
BASE_SHA256 = "2072e81065f8749fb41f531f8c62338d0ff478a2965e00ced18ee324d995489c"
OUT_DIR = REPO / "research" / "x_stance_v1" / "calendar"
OUT_NAME = "sessions_xnys_20191101_20261231.csv"
NY = ZoneInfo("America/New_York")
SOURCE_ID = "nyse_hours_page_read_20261122"
END = date(2026, 12, 31)
# Full-day closures after the registered range, and 13:00 closes, as listed for 2026.
CLOSURES_2026 = {"2026-04-03": "Good Friday", "2026-05-25": "Memorial Day", "2026-06-19": "Juneteenth National Independence Day",
                 "2026-07-03": "Independence Day (observed)", "2026-09-07": "Labor Day", "2026-11-26": "Thanksgiving Day", "2026-12-25": "Christmas Day"}
EARLY_2026 = {"2026-11-27": "day after Thanksgiving", "2026-12-24": "Christmas Eve"}


def build() -> tuple[str, dict]:
    raw = BASE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != BASE_SHA256:
        raise ValueError("registered calendar file changed")
    text = raw.decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(text)))
    fields, last = list(rows[0].keys()), date.fromisoformat(rows[-1]["session_date"])
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
    added, d = 0, last + timedelta(days=1)
    while d <= END:
        iso = d.isoformat()
        if d.weekday() < 5 and iso not in CLOSURES_2026:
            early = iso in EARLY_2026
            o = datetime.combine(d, time(9, 30), NY); c = datetime.combine(d, time(13, 0) if early else time(16, 0), NY)
            w.writerow({"mic": "XNYS", "market_scope": "NYSE_EQUITIES_CORE", "session_date": iso, "timezone": "America/New_York",
                        "open_et": o.isoformat(), "close_et": c.isoformat(), "open_utc": o.astimezone(timezone.utc).isoformat(),
                        "close_utc": c.astimezone(timezone.utc).isoformat(), "session_type": "EARLY_CLOSE" if early else "NORMAL",
                        "hours_source_id": SOURCE_ID, "annual_source_id": SOURCE_ID, "exception_source_id": SOURCE_ID if early else ""})
            added += 1
        d += timedelta(days=1)
    out = text if text.endswith("\n") else text + "\n"
    out = out.replace("\r\n", "\n") + buf.getvalue()
    manifest = {"file": OUT_NAME, "sha256": hashlib.sha256(out.encode("utf-8")).hexdigest(), "sessions": len(rows) + added,
                "registered_part": {"source": "results/calendar_round9/sessions.csv", "sha256": BASE_SHA256, "sessions": len(rows), "last_session": last.isoformat(),
                                    "evidence": "archived official announcements (round9 builder)"},
                "extension_part": {"first_session": (last + timedelta(days=3)).isoformat() if last.weekday() == 4 else None, "last_session": END.isoformat(), "sessions": added,
                                   "closures": CLOSURES_2026, "early_closes_13_00": EARLY_2026, "source_id": SOURCE_ID,
                                   "source": "https://www.nyse.com/markets/hours-calendars read on 2026-11-22",
                                   "evidence": "page read, not archived; the official announcement PDF could not be text-extracted in this run",
                                   "unscheduled_closures": "none can be known in advance; a later unscheduled closure would need a new version"}}
    return out, manifest


def main() -> int:
    out, manifest = build()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / OUT_NAME).write_text(out, encoding="utf-8", newline="\n")
    (OUT_DIR / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: manifest[k] for k in ("file", "sha256", "sessions")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
