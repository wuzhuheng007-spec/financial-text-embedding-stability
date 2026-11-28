"""History-depth and volume probes (owner approvals of 2026-11-28; all plans share one ledger and one total cap of
2,400 requests and USD 7.20).

Plan "main" (qqq-x-10y-probe-v1, sha256 3e60517a..., ran 2026-11-28 09:16-09:45 UTC): months 2016-12 .. 2026-11 (120). In
each month one trading day, the one with the smallest sha256("qqq-x-10y-probe-v1|<month>|<day>"); slice 11:00-12:00
America/New_York. Two queries per slice: Q_CASH `$QQQ lang:en -filter:replies` and Q_BARE `QQQ lang:en -filter:replies`,
queryType Latest, read to the end or 10 pages. Months ranked by sha256 of the month, both queries of a month together.

Plan "cal" (qqq-x-10y-calib-v1, owner approval "about 350 requests, hard cap USD 2"): six of the main plan's days, one
each in 2017, 2019, 2021, 2023, 2025 and 2026 (the year's month with the smallest sha256("qqq-x-10y-calib-v1|<year>|<month>")),
the whole ET calendar day in 24 one-hour slices, both queries, read to the end or 10 pages. Plan caps: 400 requests and
200,000 credits; the shared total cap still applies.

Accounting (both plans): before every request one request and its worst-case page (25 posts x 15 credits) are reserved in
the same transaction as the cap checks (whole ledger; and this plan's own caps). The reservation is settled from the
answer: posts x 15 credits, at least 15; 429, 402 and 503 settle at 0 (not charged), a network error at the worst case. A
reservation still open at the next start (the process died after sending) stays at the worst case, is marked
UNCERTAIN_INTERRUPTED and its unit is closed UNCERTAIN: the request is never sent again automatically. One instance per
ledger (exclusive lock file; a stale lock is removed only by hand). A 429 or 402 stops the run; a 503 or a network error
ends that unit; three failed units in a row stop the run. No retries. A unit that ended on a 503 can be reopened by an
explicit owner-directed `reopen` (the outcome of a 503 is certain). Wall-clock cut-off checked before every request.
Raw pages are kept gzip-compressed in the output directory (private). Printed output is counts only.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sqlite3
import sys
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path

PROTOCOL = "qqq-x-10y-probe-v1"
CAL_PROTOCOL = "qqq-x-10y-calib-v1"
QUERIES = {"Q_CASH": "$QQQ lang:en -filter:replies", "Q_BARE": "QQQ lang:en -filter:replies"}
MAX_PAGES = 10
MAX_REQUESTS = 2400
MAX_CREDITS = 720_000                     # USD 7.20 at 100,000 credits per USD
CAL_MAX_REQUESTS, CAL_MAX_CREDITS = 400, 200_000
CREDITS_PER_POST, MIN_CREDITS, PAGE_WORST_POSTS = 15, 15, 25
MIN_INTERVAL = 1.1
LOCK = "probe.lock"


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


def plan_cal(main_units=None, years=(2017, 2019, 2021, 2023, 2025, 2026)) -> list[dict]:
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    main_units = main_units or plan()
    day_of = {u["month"]: u["day"] for u in main_units}
    units = []
    for y in years:
        months = sorted(m for m in day_of if m.startswith(f"{y}-"))
        mo = min(months, key=lambda m: _h(f"{CAL_PROTOCOL}|{y}|{m}"))
        d = date.fromisoformat(day_of[mo])
        for hr in range(24):
            a = datetime.combine(d, dtime(0, 0), et) + timedelta(hours=hr)       # wall-clock hours of the ET day
            s = int(a.timestamp()); e = int((a + timedelta(hours=1)).timestamp())
            for qid, q in QUERIES.items():
                units.append({"unit_id": f"cal:{d.isoformat()}:{hr:02d}:{qid}", "month": mo, "day": d.isoformat(), "hour_et": hr,
                              "query_id": qid, "since_time": s, "until_time": e, "query": f"{q} since_time:{s} until_time:{e}"})
    return units


S1_PROTOCOL = "qqq-x-10y-s1-v1"
S1_MAX_REQUESTS, S1_MAX_CREDITS = 25_000, 3_000_000      # owner approval 2026-11-29 "Approve S1": hard cap USD 30
S1_DAYS, S1_REGULAR, S1_OFF = 4, 4, 2


def plan_s1(first="2016-12", last="2026-11", data_end=date(2026, 11, 27)) -> list[dict]:
    """Plan S1 (separate ledger, its own cap). Per month the 4 trading days with the smallest sha256(S1|day|<month>|<day>).
    Per day the ET wall-clock hours are split into regular hours (those overlapping 09:30 to the close: 09..15, or 09..12
    on a 13:00 early close) and off hours (the rest). 4 regular and 2 off hours are drawn without replacement by smallest
    sha256(S1|hour|<day>|<hh>); the inclusion probability is stored per slice. Both queries per slice, read to the end
    or 10 pages. Order: pass 1 every Q_BARE unit, pass 2 every Q_CASH unit, each pass in sha256(S1|order|<slice>) order,
    so a stop at the cap leaves an unbiased subset with the main query first."""
    from zoneinfo import ZoneInfo
    from . import calendar_us as cal
    et = ZoneInfo("America/New_York")
    y, m = map(int, first.split("-")); y1, m1 = map(int, last.split("-"))
    slices = []
    while (y, m) <= (y1, m1):
        mo = f"{y:04d}-{m:02d}"
        days = [d for d in cal.trading_days(date(y, m, 1), date(y + (m == 12), m % 12 + 1, 1)) if d.month == m and d <= data_end]
        for d in sorted(sorted(days, key=lambda d: _h(f"{S1_PROTOCOL}|day|{mo}|{d.isoformat()}"))[:S1_DAYS]):
            close = cal.close_time_et(d).hour
            reg = list(range(9, close)); off = [h for h in range(24) if h not in reg]
            key = lambda h: _h(f"{S1_PROTOCOL}|hour|{d.isoformat()}|{h:02d}")
            pick = [(h, "REGULAR", min(S1_REGULAR, len(reg)) / len(reg)) for h in sorted(reg, key=key)[:S1_REGULAR]]
            pick += [(h, "OFF", S1_OFF / len(off)) for h in sorted(off, key=key)[:S1_OFF]]
            for h, kind, p in sorted(pick):
                a = datetime.combine(d, dtime(0, 0), et) + timedelta(hours=h)
                slices.append({"slice_id": f"{d.isoformat()}:{h:02d}", "month": mo, "day": d.isoformat(), "hour_et": h,
                               "session": kind, "incl_prob_hour": round(p, 6), "incl_prob_day": round(min(S1_DAYS, len(days)) / len(days), 6),
                               "early_close": close == 13, "since_time": int(a.timestamp()), "until_time": int((a + timedelta(hours=1)).timestamp())})
        y, m = (y + (m == 12), m % 12 + 1)
    slices.sort(key=lambda s: _h(f"{S1_PROTOCOL}|order|{s['slice_id']}"))
    units = []
    for qid in ("Q_BARE", "Q_CASH"):
        for s in slices:
            units.append({**s, "unit_id": f"s1:{s['slice_id']}:{qid}", "query_id": qid,
                          "query": f"{QUERIES[qid]} since_time:{s['since_time']} until_time:{s['until_time']}"})
    return units


SCHEMA = [
    "CREATE TABLE IF NOT EXISTS probe_units(unit_id TEXT PRIMARY KEY, status TEXT NOT NULL, pages INTEGER NOT NULL, cursor TEXT, stop_reason TEXT, updated_utc TEXT)",
    "CREATE TABLE IF NOT EXISTS probe_requests(n INTEGER PRIMARY KEY AUTOINCREMENT, unit_id TEXT NOT NULL, page INTEGER NOT NULL, status TEXT NOT NULL,"
    " http INTEGER, tweets INTEGER, credits INTEGER NOT NULL, has_next INTEGER, at_utc TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS probe_meta(k TEXT PRIMARY KEY, v TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS probe_events(n INTEGER PRIMARY KEY AUTOINCREMENT, at_utc TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL)",
]


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Lock:
    """One probe instance per ledger: an exclusive lock file (O_EXCL). A lock left by a killed process is never broken
    automatically; it is removed by hand after checking that no probe process is running."""
    def __init__(self, out: Path):
        self.p = Path(out) / LOCK

    def __enter__(self):
        try:
            fd = os.open(self.p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise SystemExit("ANOTHER_INSTANCE_OR_STALE_LOCK") from None
        os.write(fd, f"{os.getpid()} {now_iso()}".encode()); os.close(fd)
        return self

    def __exit__(self, *exc):
        try:
            self.p.unlink()
        except FileNotFoundError:
            pass


def _migrate(db):
    for s in SCHEMA:
        db.execute(s)
    if "plan" not in {r[1] for r in db.execute("PRAGMA table_info(probe_requests)")}:
        db.execute("ALTER TABLE probe_requests ADD COLUMN plan TEXT NOT NULL DEFAULT 'main'")


def recover_reservations(db) -> int:
    rows = db.execute("SELECT n, unit_id FROM probe_requests WHERE status='RESERVED'").fetchall()
    for n, uid in rows:
        db.execute("UPDATE probe_requests SET status='UNCERTAIN_INTERRUPTED' WHERE n=?", (n,))
        db.execute("UPDATE probe_units SET status='UNCERTAIN', stop_reason='INTERRUPTED_AFTER_SEND', updated_utc=? WHERE unit_id=?", (now_iso(), uid))
        db.execute("INSERT INTO probe_events(at_utc, kind, detail) VALUES(?,?,?)", (now_iso(), "RESERVATION_RECOVERED", json.dumps({"n": n, "unit_id": uid})))
    return len(rows)


def _open(out: Path):
    db = sqlite3.connect(Path(out) / "probe_ledger.sqlite3", isolation_level=None)
    _migrate(db)
    return db


def reopen_503(out: Path, unit_id: str, reason: str) -> dict:
    with Lock(out):
        db = _open(out)
        r = db.execute("SELECT status, stop_reason FROM probe_units WHERE unit_id=?", (unit_id,)).fetchone()
        if not r or r[0] != "FAILED" or r[1] != "HTTP_503_NOT_CHARGED":
            raise SystemExit(f"refused: {unit_id} is {r}")
        db.execute("UPDATE probe_units SET status='OPEN', stop_reason=NULL, updated_utc=? WHERE unit_id=?", (now_iso(), unit_id))
        db.execute("INSERT INTO probe_events(at_utc, kind, detail) VALUES(?,?,?)", (now_iso(), "REOPEN_503", json.dumps({"unit_id": unit_id, "reason": reason})))
        db.close()
    return {"reopened": unit_id}


def run(units, out: Path, transport, cutoff_utc: datetime, clock=lambda: datetime.now(timezone.utc),
        max_requests=MAX_REQUESTS, max_credits=MAX_CREDITS, schema_error=None, http_error=Exception,
        plan_name="main", plan_max_requests=None, plan_max_credits=None) -> dict:
    """max_requests / max_credits bound the whole ledger (all plans together); plan_max_* bound this plan alone."""
    out = Path(out); out.mkdir(parents=True, exist_ok=True); (out / "pages").mkdir(exist_ok=True)
    with Lock(out):
        db = _open(out)
        try:
            return _run(db, units, out, transport, cutoff_utc, clock, max_requests, max_credits, schema_error, http_error,
                        plan_name, plan_max_requests, plan_max_credits)
        finally:
            db.close()


def _run(db, units, out, transport, cutoff_utc, clock, max_requests, max_credits, schema_error, http_error, plan_name, pmr, pmc):
    recovered = recover_reservations(db)
    ph = _h(json.dumps(units, sort_keys=True))
    key = "plan_sha256" if plan_name == "main" else f"plan_sha256:{plan_name}"
    old = db.execute("SELECT v FROM probe_meta WHERE k=?", (key,)).fetchone()
    if old and old[0] != ph:
        raise SystemExit("plan changed since the first run; refusing to mix plans")
    db.execute("INSERT OR IGNORE INTO probe_meta VALUES(?, ?)", (key, ph))
    worst = PAGE_WORST_POSTS * CREDITS_PER_POST
    tot = lambda: db.execute("SELECT COALESCE(SUM(credits),0), COUNT(*) FROM probe_requests").fetchone()
    mine = lambda: db.execute("SELECT COALESCE(SUM(credits),0), COUNT(*) FROM probe_requests WHERE plan=?", (plan_name,)).fetchone()
    stop, fails_in_row = None, 0
    for u in units:
        row = db.execute("SELECT status, pages, cursor FROM probe_units WHERE unit_id=?", (u["unit_id"],)).fetchone()
        if row and row[0] != "OPEN":
            continue
        pages, cursor = (row[1], row[2]) if row else (0, "")
        if not row:
            db.execute("INSERT INTO probe_units VALUES(?, 'OPEN', 0, '', NULL, ?)", (u["unit_id"], now_iso()))
        status, reason = "OPEN", None
        while pages < MAX_PAGES:
            if clock() >= cutoff_utc:
                stop = "CUTOFF"; break
            db.execute("BEGIN IMMEDIATE")                 # cap check and reservation in one transaction
            (c_all, n_all), (c_me, n_me) = tot(), mine()
            why = ("REQUEST_CAP" if n_all >= max_requests else "CREDIT_CAP" if c_all + worst > max_credits else
                   "PLAN_REQUEST_CAP" if pmr is not None and n_me >= pmr else
                   "PLAN_CREDIT_CAP" if pmc is not None and c_me + worst > pmc else None)
            if why:
                db.execute("COMMIT"); stop = why; break
            rid = db.execute("INSERT INTO probe_requests(unit_id, page, status, http, tweets, credits, has_next, at_utc, plan) VALUES(?,?,?,?,?,?,?,?,?)",
                             (u["unit_id"], pages + 1, "RESERVED", None, None, worst, None, now_iso(), plan_name)).lastrowid
            db.execute("COMMIT")
            try:
                page = transport.get({"query": u["query"], "queryType": "Latest", "cursor": cursor})
            except http_error as e:                       # ProviderHTTPError in the image
                code, cls = getattr(e, "status", None), getattr(e, "kind", None) or str(e)
                db.execute("UPDATE probe_requests SET status=?, http=?, credits=?, at_utc=? WHERE n=?",
                           (cls, code, 0 if code in (429, 402, 503) else worst, now_iso(), rid))
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
            cursor = (page.get("next_cursor") or "") if isinstance(page, dict) else ""
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE probe_requests SET status=?, http=200, tweets=?, credits=?, has_next=?, at_utc=? WHERE n=?",
                       (err or "OK", n, max(MIN_CREDITS, CREDITS_PER_POST * n), int(hn), now_iso(), rid))
            db.execute("UPDATE probe_units SET pages=?, cursor=?, updated_utc=? WHERE unit_id=?", (pages, cursor, now_iso(), u["unit_id"]))
            db.execute("COMMIT")
            if err:
                status, reason = "FAILED", err; break
            if not hn:
                status, reason = "COMPLETE", "END_OF_RESULTS" if n or pages > 1 else "END_OF_RESULTS_EMPTY"; break
        else:
            status, reason = "TRUNCATED", "PAGE_LIMIT_10"
        if status != "OPEN":
            db.execute("UPDATE probe_units SET status=?, stop_reason=?, updated_utc=? WHERE unit_id=?", (status, reason, now_iso(), u["unit_id"]))
        fails_in_row = fails_in_row + 1 if status == "FAILED" else 0
        if fails_in_row >= 3 and not stop:
            stop = "THREE_FAILED_UNITS"
        if stop:
            break
    (c_all, n_all), (c_me, n_me) = tot(), mine()
    st = {}
    for u in units:
        r = db.execute("SELECT status FROM probe_units WHERE unit_id=?", (u["unit_id"],)).fetchone()
        k = r[0] if r else "NOT_RUN"; st[k] = st.get(k, 0) + 1
    return {"plan": plan_name, "plan_sha256": ph, "stopped": stop or "PLAN_DONE", "recovered_reservations": recovered,
            "requests": n_all, "credits": c_all, "usd": round(c_all / 100000, 4), "plan_requests": n_me, "plan_credits": c_me,
            "units": st, "units_planned": len(units),
            "tweets_returned": db.execute("SELECT COALESCE(SUM(tweets),0) FROM probe_requests").fetchone()[0], "at_utc": now_iso()}


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan"); p.add_argument("out")
    pc = sub.add_parser("plan-cal"); pc.add_argument("out")
    ps = sub.add_parser("plan-s1"); ps.add_argument("out")
    ro = sub.add_parser("reopen"); ro.add_argument("--out", required=True); ro.add_argument("--unit", required=True); ro.add_argument("--reason", required=True)
    r = sub.add_parser("run"); r.add_argument("--plan", required=True); r.add_argument("--out", required=True); r.add_argument("--cutoff-utc", required=True)
    r.add_argument("--plan-name", default="main", choices=["main", "cal", "s1"])
    a = ap.parse_args(argv)
    if a.cmd in ("plan", "plan-cal", "plan-s1"):
        u = {"plan": plan, "plan-cal": plan_cal, "plan-s1": plan_s1}[a.cmd](); Path(a.out).write_text(json.dumps(u, indent=1), encoding="utf-8")
        print(json.dumps({"units": len(u), "sha256": _h(json.dumps(u, sort_keys=True))})); return
    if a.cmd == "reopen":
        print(json.dumps(reopen_503(Path(a.out), a.unit, a.reason))); return
    from scripts.x_stance_v1 import provider as P               # the production client, as shipped in the web image
    key = os.environ.get("TWITTERAPI_IO_API_KEY") or ""
    if not key:
        print(json.dumps({"stopped": "NO_CREDENTIAL"})); sys.exit(2)
    t = P.UrllibTransport(key, min_interval=MIN_INTERVAL)
    units = json.loads(Path(a.plan).read_text(encoding="utf-8"))
    cut = datetime.fromisoformat(a.cutoff_utc.replace("Z", "+00:00"))
    if a.plan_name == "s1":          # S1 has its own ledger: refuse to run it in the probe ledger
        if (Path(a.out) / "probe_ledger.sqlite3").exists() and _open(Path(a.out)).execute(
                "SELECT COUNT(*) FROM probe_requests WHERE plan!='s1'").fetchone()[0]:
            raise SystemExit("refused: S1 must use its own ledger")
        caps = {"max_requests": S1_MAX_REQUESTS, "max_credits": S1_MAX_CREDITS}
    else:
        caps = {"plan_max_requests": CAL_MAX_REQUESTS, "plan_max_credits": CAL_MAX_CREDITS} if a.plan_name == "cal" else {}
    print(json.dumps(run(units, Path(a.out), t, cut, schema_error=P.page_schema_error, http_error=P.ProviderHTTPError,
                         plan_name=a.plan_name, **caps)))


if __name__ == "__main__":
    main()
