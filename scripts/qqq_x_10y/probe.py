"""History-depth and volume probe (protocol qqq-x-10y-probe-v1, owner approval 2026-11-28: at most 2,400 requests, USD 7.20).

Plan (built by `plan`, frozen by its sha256 before any request): months 2016-12 .. 2026-11 (120). In each month one trading
day, the one with the smallest sha256("qqq-x-10y-probe-v1|<month>|<day>"); slice 11:00-12:00 America/New_York of that day.
Two queries per slice: Q_CASH `$QQQ lang:en -filter:replies` and Q_BARE `QQQ lang:en -filter:replies`, queryType Latest,
read to the end or 10 pages, whichever comes first. Unit order: months ranked by sha256 of the month (so a stop part-way
still spreads over the years), both queries of a month together.

Run (inside the web image, provider key from the existing private env file only): own sqlite ledger in the output
directory; nothing is written to the production store or its ledger. Before every request: the wall-clock cut-off, the
request cap and the credit cap (spent + one worst-case page of 25 posts x 15 credits <= 720,000 credits = USD 7.20) are
checked. A 429 or a 402 stops the run; a 503 (documented as not charged) or a network error ends that unit only, the
network error is counted as a worst-case page; three failed units in a row stop the run. No retries. Resumable: units
already finished are skipped, an unfinished unit continues from its stored cursor. Raw pages are kept gzip-compressed in
the output directory (private). Printed output is counts only.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sqlite3
import sys
import time
from datetime import date, datetime, time as dtime, timezone
from pathlib import Path

PROTOCOL = "qqq-x-10y-probe-v1"
QUERIES = {"Q_CASH": "$QQQ lang:en -filter:replies", "Q_BARE": "QQQ lang:en -filter:replies"}
MAX_PAGES = 10
MAX_REQUESTS = 2400
MAX_CREDITS = 720_000                     # USD 7.20 at 100,000 credits per USD
CREDITS_PER_POST, MIN_CREDITS, PAGE_WORST_POSTS = 15, 15, 25
MIN_INTERVAL = 1.1


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def plan(first="2016-12", last="2026-11", data_end=date(2026, 11, 27)) -> list[dict]:
    from zoneinfo import ZoneInfo
    from . import calendar_us as cal
    et = ZoneInfo("America/New_York")
    y, m = map(int, first.split("-")); y1, m1 = map(int, last.split("-"))
    months = []
    while (y, m) <= (y1, m1):
        months.append(f"{y:04d}-{m:02d}"); y, m = (y + (m == 12), m % 12 + 1)
    units = []
    for mo in sorted(months, key=lambda x: _h(f"{PROTOCOL}|month|{x}")):
        yy, mm = map(int, mo.split("-"))
        days = [d for d in cal.trading_days(date(yy, mm, 1), date(yy + (mm == 12), mm % 12 + 1, 1)) if d.month == mm and d <= data_end]
        day = min(days, key=lambda d: _h(f"{PROTOCOL}|{mo}|{d.isoformat()}"))
        s = int(datetime.combine(day, dtime(11, 0), et).timestamp()); e = int(datetime.combine(day, dtime(12, 0), et).timestamp())
        for qid, q in QUERIES.items():
            units.append({"unit_id": f"{mo}:{qid}", "month": mo, "day": day.isoformat(), "query_id": qid,
                          "since_time": s, "until_time": e, "query": f"{q} since_time:{s} until_time:{e}"})
    return units


SCHEMA = [
    "CREATE TABLE IF NOT EXISTS probe_units(unit_id TEXT PRIMARY KEY, status TEXT NOT NULL, pages INTEGER NOT NULL, cursor TEXT, stop_reason TEXT, updated_utc TEXT)",
    "CREATE TABLE IF NOT EXISTS probe_requests(n INTEGER PRIMARY KEY AUTOINCREMENT, unit_id TEXT NOT NULL, page INTEGER NOT NULL, status TEXT NOT NULL,"
    " http INTEGER, tweets INTEGER, credits INTEGER NOT NULL, has_next INTEGER, at_utc TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS probe_meta(k TEXT PRIMARY KEY, v TEXT NOT NULL)",
]


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(units, out: Path, transport, cutoff_utc: datetime, clock=lambda: datetime.now(timezone.utc),
        max_requests=MAX_REQUESTS, max_credits=MAX_CREDITS, schema_error=None, http_error=Exception) -> dict:
    out.mkdir(parents=True, exist_ok=True); (out / "pages").mkdir(exist_ok=True)
    db = sqlite3.connect(out / "probe_ledger.sqlite3")
    for s in SCHEMA:
        db.execute(s)
    ph = _h(json.dumps(units, sort_keys=True))
    old = db.execute("SELECT v FROM probe_meta WHERE k='plan_sha256'").fetchone()
    if old and old[0] != ph:
        raise SystemExit("plan changed since the first run; refusing to mix plans")
    db.execute("INSERT OR IGNORE INTO probe_meta VALUES('plan_sha256', ?)", (ph,)); db.commit()
    spent = lambda: db.execute("SELECT COALESCE(SUM(credits),0), COUNT(*) FROM probe_requests").fetchone()
    worst = PAGE_WORST_POSTS * CREDITS_PER_POST
    stop, fails_in_row = None, 0
    for u in units:
        row = db.execute("SELECT status, pages, cursor FROM probe_units WHERE unit_id=?", (u["unit_id"],)).fetchone()
        if row and row[0] != "OPEN":
            continue
        pages, cursor = (row[1], row[2]) if row else (0, "")
        if not row:
            db.execute("INSERT INTO probe_units VALUES(?, 'OPEN', 0, '', NULL, ?)", (u["unit_id"], now_iso())); db.commit()
        status, reason = "OPEN", None
        while pages < MAX_PAGES:
            credits, nreq = spent()
            if clock() >= cutoff_utc:
                stop = "CUTOFF"; break
            if nreq >= max_requests:
                stop = "REQUEST_CAP"; break
            if credits + worst > max_credits:
                stop = "CREDIT_CAP"; break
            try:
                page = transport.get({"query": u["query"], "queryType": "Latest", "cursor": cursor})
            except http_error as e:          # ProviderHTTPError in the image
                code, cls = getattr(e, "status", None), getattr(e, "kind", None) or str(e)
                charged = 0 if code in (429, 402, 503) else worst
                db.execute("INSERT INTO probe_requests(unit_id, page, status, http, tweets, credits, has_next, at_utc) VALUES(?,?,?,?,?,?,?,?)",
                           (u["unit_id"], pages + 1, cls, code, None, charged, None, now_iso())); db.commit()
                if code == 429:
                    stop = "STOPPED_429"
                elif code == 402:
                    stop = "STOPPED_402_CREDITS"
                status, reason = "FAILED", cls
                break
            err = schema_error(page) if schema_error else None
            tw = page.get("tweets") if isinstance(page, dict) else None
            n = len(tw) if isinstance(tw, list) else 0
            pages += 1
            with gzip.open(out / "pages" / f"{u['unit_id'].replace(':', '_')}_{pages:02d}.json.gz", "wt", encoding="utf-8") as f:
                json.dump(page, f, ensure_ascii=False)
            hn = bool(page.get("has_next_page")) if isinstance(page, dict) else False
            db.execute("INSERT INTO probe_requests(unit_id, page, status, http, tweets, credits, has_next, at_utc) VALUES(?,?,?,?,?,?,?,?)",
                       (u["unit_id"], pages, err or "OK", 200, n, max(MIN_CREDITS, CREDITS_PER_POST * n), int(hn), now_iso()))
            cursor = (page.get("next_cursor") or "") if isinstance(page, dict) else ""
            db.execute("UPDATE probe_units SET pages=?, cursor=?, updated_utc=? WHERE unit_id=?", (pages, cursor, now_iso(), u["unit_id"])); db.commit()
            if err:
                status, reason = "FAILED", err; break
            if not hn:
                status, reason = "COMPLETE", "END_OF_RESULTS" if n or pages > 1 else "END_OF_RESULTS_EMPTY"; break
        else:
            status, reason = "TRUNCATED", "PAGE_LIMIT_10"
        if status != "OPEN":
            db.execute("UPDATE probe_units SET status=?, stop_reason=?, updated_utc=? WHERE unit_id=?", (status, reason, now_iso(), u["unit_id"])); db.commit()
        fails_in_row = fails_in_row + 1 if status == "FAILED" else 0
        if fails_in_row >= 3 and not stop:
            stop = "THREE_FAILED_UNITS"
        if stop:
            break
    credits, nreq = spent()
    st = dict(db.execute("SELECT status, COUNT(*) FROM probe_units GROUP BY status").fetchall())
    res = {"protocol": PROTOCOL, "plan_sha256": ph, "stopped": stop or "PLAN_DONE", "requests": nreq, "credits": credits,
           "usd": round(credits / 100000, 4), "units": st, "units_planned": len(units),
           "tweets_returned": db.execute("SELECT COALESCE(SUM(tweets),0) FROM probe_requests").fetchone()[0], "at_utc": now_iso()}
    db.close()
    return res


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan"); p.add_argument("out")
    r = sub.add_parser("run"); r.add_argument("--plan", required=True); r.add_argument("--out", required=True); r.add_argument("--cutoff-utc", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "plan":
        u = plan(); Path(a.out).write_text(json.dumps(u, indent=1), encoding="utf-8")
        print(json.dumps({"units": len(u), "sha256": _h(json.dumps(u, sort_keys=True))})); return
    import os
    from scripts.x_stance_v1 import provider as P               # the production client, as shipped in the web image
    key = os.environ.get("TWITTERAPI_IO_API_KEY") or ""
    if not key:
        print(json.dumps({"stopped": "NO_CREDENTIAL"})); sys.exit(2)
    t = P.UrllibTransport(key, min_interval=MIN_INTERVAL)
    units = json.loads(Path(a.plan).read_text(encoding="utf-8"))
    cut = datetime.fromisoformat(a.cutoff_utc.replace("Z", "+00:00"))
    print(json.dumps(run(units, Path(a.out), t, cut, schema_error=P.page_schema_error, http_error=P.ProviderHTTPError)))


if __name__ == "__main__":
    main()
