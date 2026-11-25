"""The page's price line: every session's close reaches the page, however many return rows other topics have.

Regression (2026-11-26): the closes came from the first 1,200 of 2,400 return rows ordered by event id; with four
topics and six horizons the QQQ rows fell outside (1 of 393 reached the page) and the price strip stayed empty."""
import json, sqlite3
from pathlib import Path

from scripts.x_stance_v1 import config as C, portal_read, store


def _fill(db, topics, dates):
    for t in topics:
        for i, d in enumerate(dates):
            for h in (1, 3, 5, 10, 20, 30):
                p = {"session_date": d, "h": h, "anchor_close": 600.0 + i, "series_id": "US.QQQ|DAY|a1", "state": "UNKNOWN"}
                db.execute("INSERT INTO x_event_returns VALUES(?,?,?,?,?,?)", (f"{t}:{d}", "daily", t, "QQQ" if t == "qqq" else f"industry:{t}", h, json.dumps(p)))
    db.commit()


def test_every_session_close_reaches_the_page(tmp_path, monkeypatch):
    p = tmp_path / "x.sqlite3"; db = store.connect(p)
    dates = [f"2026-{m:02d}-{d:02d}" for m in (8, 9, 10, 11) for d in range(1, 29) if True][:100]
    _fill(db, ["technology", "qqq", "market", "memory"], dates); db.close()
    monkeypatch.setenv("MOOMOO_DISPLAY_HISTORY_DB", str(tmp_path / "missing.sqlite"))
    out = portal_read.read_for_portal(C.load(), p, env={})
    assert len(out["closes"]["qqq"]) == 100 and out["closes"]["qqq"][dates[-1]] == 699.0
    assert out["closes_meta"]["frozen_input_last_date"] == dates[-1]
    assert out["closes_meta"]["display_fill"]["dates_added"] == 0                     # no display history: nothing filled, nothing invented


def test_dates_after_the_frozen_input_are_filled_from_the_display_history_and_marked(tmp_path, monkeypatch):
    p = tmp_path / "x.sqlite3"; db = store.connect(p)
    _fill(db, ["qqq"], ["2026-11-16", "2026-11-17"]); db.close()
    disp = tmp_path / "display.sqlite"; d = sqlite3.connect(disp)
    d.executescript("CREATE TABLE series(series_id TEXT, symbol TEXT, frequency TEXT, autype INTEGER); CREATE TABLE observations(series_id TEXT, local_date TEXT, close REAL, research_eligible INTEGER);")
    d.execute("INSERT INTO series VALUES('s','US.QQQ','DAY',0)")
    for day, c in (("2026-11-17", 656.95), ("2026-11-18", 659.51), ("2026-11-19", 645.04)):
        d.execute("INSERT INTO observations VALUES('s',?,?,1)", (day, c))
    d.commit(); d.close()
    monkeypatch.setenv("MOOMOO_DISPLAY_HISTORY_DB", str(disp))
    out = portal_read.read_for_portal(C.load(), p, env={})
    c = out["closes"]["qqq"]
    assert c["2026-11-17"] == 601.0                                                     # the frozen input wins where it has the date
    assert c["2026-11-18"] == 659.51 and c["2026-11-19"] == 645.04
    f = out["closes_meta"]["display_fill"]
    assert f["after"] == "2026-11-17" and f["dates_added"] == 2 and "unadjusted" in f["source"]
