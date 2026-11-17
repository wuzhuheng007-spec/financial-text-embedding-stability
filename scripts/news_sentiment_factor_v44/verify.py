"""Isolated calendar and label-closure checks; no real return labels are read."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from scripts.news_sentiment_factor_v43.daily import _label_rows, regular_session_close, regular_session_open
from .labels import CONTRACT, advance, digest, init


def run() -> dict:
    checks = {}
    checks["normal_close_est"] = regular_session_close(date(2026, 11, 18)).isoformat() == "2026-11-18T21:00:00+00:00"
    checks["early_close_est"] = regular_session_close(date(2026, 11, 27)).isoformat() == "2026-11-27T18:00:00+00:00"
    checks["early_close_december"] = regular_session_close(date(2026, 12, 24)).isoformat() == "2026-12-24T18:00:00+00:00"
    checks["dst_open"] = (regular_session_open(date(2026, 3, 6)).hour == 14 and
                          regular_session_open(date(2026, 3, 9)).hour == 13)
    mapping = {"NVDA": {"security_id": "SEC:NVDA:US", "verification": "CURRENT_US_PROVIDER_CODE_VERIFIED"}}
    rows = _label_rows(["NVDA"], datetime(2026, 11, 25, 21, 15, tzinfo=timezone.utc),
                       datetime(2026, 11, 25, 21, 16, tzinfo=timezone.utc), mapping,
                       observed_at=datetime(2026, 11, 25, 21, 16, tzinfo=timezone.utc))
    checks["thanksgiving_next_entry"] = all(r["entry_session"] == "2026-11-27" for r in rows)
    checks["five_sessions_cross_holiday"] = next(r for r in rows if r["horizon"] == 5)["exit_session"] == "2026-12-03"
    checks["early_close_exit"] = next(r for r in rows if r["horizon"] == 1)["exit_at"] == "2026-11-27T18:00:00+00:00"
    boundary = _label_rows(["NVDA"], datetime(2026, 12, 30, 21, 15, tzinfo=timezone.utc),
                           datetime(2026, 12, 30, 21, 16, tzinfo=timezone.utc), mapping)
    checks["calendar_out_of_range_blocks"] = next(r for r in boundary if r["horizon"] == 5)["status"] == "BLOCKED_CALENDAR_RANGE"
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        db, queue = root / "labels.sqlite3", root / "queue.sqlite3"
        init(db)
        with closing(sqlite3.connect(queue)) as c, c:
            c.executescript("""
              CREATE TABLE daily_label_queue(snapshot_id TEXT,entity_id TEXT,horizon_sessions INTEGER,status TEXT,
                reason TEXT,security_id TEXT,entry_session_ny TEXT,entry_at_utc TEXT,exit_session_ny TEXT,
                exit_at_utc TEXT,maturity_at_utc TEXT,price_requirements_json TEXT);
              CREATE TABLE daily_snapshot_company(snapshot_id TEXT,entity_id TEXT,n1 REAL);
              CREATE TABLE daily_snapshot_releases(snapshot_id TEXT,signal_available_at_utc TEXT);
              CREATE TABLE daily_snapshot_publication_witness(snapshot_id TEXT,release_commit_observed_at_utc TEXT);
            """)
            c.execute("INSERT INTO daily_label_queue VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      ("synthetic", "NVDA", 1, "WAIT_QUALIFIED_PRICE", "", "SEC:NVDA:US", "2026-01-05",
                       "2026-01-05T14:30:00+00:00", "2026-01-05", "2026-01-05T21:00:00+00:00",
                       "2026-01-05T21:00:00+00:00", "[]"))
            c.execute("INSERT INTO daily_snapshot_company VALUES(?,?,?)", ("synthetic", "NVDA", 0.1))
            c.execute("INSERT INTO daily_snapshot_releases VALUES(?,?)", ("synthetic", "2026-01-02T21:16:00Z"))
            c.execute("INSERT INTO daily_snapshot_publication_witness VALUES(?,?)", ("synthetic", "2026-01-02T21:15:30+00:00"))
        with closing(sqlite3.connect(db)) as c, c:
            for field, price in (("OPEN", "100"), ("CLOSE", "102")):
                c.execute("INSERT INTO price_evidence VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (field.lower(), "SEC:NVDA:US", "US.NVDA", "2026-01-05", field,
                           price, "USD", "SYNTHETIC", "REGULAR",
                           "UNADJUSTED_DAILY_BAR_NOT_OFFICIAL_AUCTION", "UNADJUSTED_AUTYPE_0",
                           "SYNTHETIC", "2026-01-06T00:00:00Z", "synthetic-sha", "synthetic-parser"))
            c.execute("INSERT INTO action_evidence VALUES(?,?,?,?,?,?,?,?,?,?)",
                      ("action", "SEC:NVDA:US", "2026-01-05", "2026-01-05", 0, 0,
                       "PROVIDER_LISTS_NO_ACTION_IN_PILOT_INTERVAL", "synthetic-rehab", "synthetic-dividend",
                       "2026-01-06T00:00:00+00:00"))
        when = datetime(2026, 11, 17, 9, 0, tzinfo=timezone.utc)
        first = advance(db, queue, when)
        again = advance(db, queue, when + timedelta(minutes=1))
        with closing(sqlite3.connect(db)) as c, c:
            value = c.execute("SELECT price_return_text,contract FROM label_versions").fetchone()
            attempt_count = c.execute("SELECT COUNT(*) FROM label_attempts").fetchone()[0]
        checks["synthetic_ready_calculation"] = first["new_label_versions"] == 1 and value == ("0.02", CONTRACT)
        checks["synthetic_idempotent"] = again["new_label_versions"] == 0
        checks["mixed_utc_text_and_no_repeat_attempt"] = attempt_count == 1
        with closing(sqlite3.connect(db)) as c, c:
            c.execute("INSERT INTO price_evidence VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      ("close-revised", "SEC:NVDA:US", "US.NVDA", "2026-01-05", "CLOSE",
                       "103", "USD", "SYNTHETIC", "REGULAR",
                       "UNADJUSTED_DAILY_BAR_NOT_OFFICIAL_AUCTION", "UNADJUSTED_AUTYPE_0",
                       "SYNTHETIC", "2026-01-07T00:00:00+00:00", "synthetic-sha-2", "synthetic-parser"))
        revised = advance(db, queue, when)
        checks["synthetic_revision_appends"] = revised["new_label_versions"] == 1
    if not all(checks.values()):
        raise AssertionError({k: v for k, v in checks.items() if not v})
    return {"checks": checks, "passed": len(checks), "real_labels": 0,
            "synthetic_only": True}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
