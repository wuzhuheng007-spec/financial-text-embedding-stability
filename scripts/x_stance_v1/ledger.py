"""One persistent spending ledger for every X job (preflight, recent, historical, retries; any process that opens
the same store). Money is kept in provider credits as integers: 15 credits per returned post, 15 minimum per
request, 100,000 credits per USD.

Before a request: a reservation of one full page (300 credits, 20 posts) is written inside BEGIN IMMEDIATE, after
checking every cap against settled spend PLUS reservations still open. After the response: the reservation is
settled at 15 x max(returned, 1). If the outcome is not known (timeout, dropped connection, unreadable body) the
reservation is HELD at the full page and keeps counting; it is released only on explicit evidence that the provider
did not charge. Cleaning or de-duplicating posts never gives budget back.

Caps (all must hold): all jobs per Asia/Shanghai day (posts and credits), all jobs over the rolling window, the
job's pool per day (recent / historical), and the cumulative preflight allowance. Pools do not borrow from each
other. This limits THIS program's requests; it cannot see other use of the same provider account.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from . import store

SCHEMA = """
CREATE TABLE IF NOT EXISTS x_cost_ledger(
  request_id TEXT PRIMARY KEY, job_type TEXT NOT NULL, pool TEXT NOT NULL, is_preflight INTEGER NOT NULL,
  run_id TEXT, window_start_utc TEXT, window_end_utc TEXT, cursor TEXT, attempt INTEGER NOT NULL,
  reserved_credits INTEGER NOT NULL, reserved_posts INTEGER NOT NULL, settled_credits INTEGER, returned INTEGER, unique_ids INTEGER,
  status TEXT NOT NULL, cost_source TEXT NOT NULL, note TEXT, budget_day TEXT NOT NULL, created_at_utc TEXT NOT NULL, settled_at_utc TEXT);
CREATE INDEX IF NOT EXISTS x_cost_ledger_day ON x_cost_ledger(budget_day);
CREATE INDEX IF NOT EXISTS x_cost_ledger_created ON x_cost_ledger(created_at_utc);
CREATE TABLE IF NOT EXISTS x_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at_utc TEXT NOT NULL);
"""
POOLS = ("recent", "historical")


class LimitReached(RuntimeError):
    def __init__(self, limit_kind: str):
        super().__init__(limit_kind)
        self.limit_kind = limit_kind


class Paused(RuntimeError):
    pass


def usd(credits: int, per_usd: int = 100000) -> str:
    return str((Decimal(credits) / Decimal(per_usd)).normalize()) if credits else "0"


def get_meta(db, key: str):
    r = db.execute("SELECT value FROM x_meta WHERE key=?", (key,)).fetchone()
    return r[0] if r else None


def set_meta(db, key: str, value: str) -> None:
    db.execute("INSERT INTO x_meta VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at_utc=excluded.updated_at_utc", (key, value, store.utcnow()))


class Ledger:
    def __init__(self, db: sqlite3.Connection, cfg: dict, clock=None, readonly: bool = False):
        self.db, self.cfg = db, cfg
        if not readonly:
            db.executescript(SCHEMA)
        q, b, a, p = cfg["quote"], cfg["budget"], cfg["allocation"], cfg["preflight"]
        self.per_post, self.per_usd, self.page_posts = int(q["credits_per_post"]), int(q["credits_per_usd"]), int(q["documented_max_posts_per_page"])
        self.page_credits = int(q["reserve_page_credits"])
        if self.page_credits != self.per_post * self.page_posts:
            raise ValueError("reserve_page_credits must equal credits_per_post x documented_max_posts_per_page")
        # A page can come back slightly larger than documented (22 against 20 was observed on 2026-11-23). Up to
        # `tolerated` it is charged at the real count without a pause, and every cap check keeps headroom for a page
        # of that size, so a cap is still never exceeded. Anything larger pauses all requests as before.
        self.tolerated = int(cfg["acquisition"].get("tolerated_max_posts_per_page") or self.page_posts)
        if self.tolerated < self.page_posts:
            raise ValueError("tolerated_max_posts_per_page is below the documented page size")
        self.check_posts, self.check_credits = self.tolerated, self.per_post * self.tolerated
        c = lambda s: int((Decimal(str(s)) * self.per_usd).to_integral_exact())
        self.tz = ZoneInfo(b["timezone"])
        self.day_posts, self.day_credits = int(b["daily_all_jobs_returned_post_cap"]), c(b["daily_all_jobs_cost_cap_usd"])
        self.roll_hours, self.roll_credits = int(b["rolling_window_hours"]), c(b["rolling_all_jobs_cost_cap_usd"])
        self.pool = {k: (int(a[k]["max_returned_posts_per_day"]), c(a[k]["cost_cap_usd_per_day"])) for k in POOLS}
        self.pre_posts, self.pre_credits = int(p["max_returned_posts"]), c(p["cost_cap_usd"])
        # Shares of ONE pool by job type (e.g. recent = watched authors + keyword search). They split the pool; they
        # add no allowance, and the pool cap still binds everything in it, the preflight included.
        self.sub = {}
        for pool in POOLS:
            parts = a[pool].get("sub_pools") or {}
            for job, v in parts.items():
                self.sub[job] = (pool, int(v["max_returned_posts_per_day"]), c(v["cost_cap_usd_per_day"]))
            if parts and (sum(int(v["max_returned_posts_per_day"]) for v in parts.values()) > self.pool[pool][0] or sum(c(v["cost_cap_usd_per_day"]) for v in parts.values()) > self.pool[pool][1]):
                raise ValueError("sub-allocations exceed their pool")
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def day(self, now: datetime) -> str:
        return now.astimezone(self.tz).strftime("%Y-%m-%d")

    # counted = what is settled, or the full reservation while a request is open or its outcome unknown
    _COUNT = "COALESCE(SUM(CASE WHEN status='SETTLED' THEN settled_credits WHEN status IN ('RESERVED','UNKNOWN_HELD') THEN reserved_credits ELSE 0 END),0), " \
             "COALESCE(SUM(CASE WHEN status='SETTLED' THEN returned WHEN status IN ('RESERVED','UNKNOWN_HELD') THEN reserved_posts ELSE 0 END),0)"

    def _sum(self, where: str, args: tuple) -> tuple[int, int]:
        r = self.db.execute(f"SELECT {self._COUNT} FROM x_cost_ledger WHERE {where}", args).fetchone()
        return int(r[0]), int(r[1])

    def usage(self, now: datetime | None = None) -> dict:
        now = now or self.clock()
        d, since = self.day(now), store.iso(now - timedelta(hours=self.roll_hours))
        dc, dp = self._sum("budget_day=?", (d,))
        rc, _ = self._sum("created_at_utc>?", (since,))
        pc, pp = self._sum("is_preflight=1", ())
        out = {"budget_day": d, "timezone": str(self.tz), "today": {"credits": dc, "usd": usd(dc), "returned_posts": dp, "cap_usd": usd(self.day_credits), "cap_posts": self.day_posts},
               "rolling": {"hours": self.roll_hours, "credits": rc, "usd": usd(rc), "cap_usd": usd(self.roll_credits)},
               "preflight_cumulative": {"credits": pc, "usd": usd(pc), "returned_posts": pp, "cap_usd": usd(self.pre_credits), "cap_posts": self.pre_posts}, "pools": {}}
        for k in POOLS:
            c_, p_ = self._sum("budget_day=? AND pool=?", (d, k))
            out["pools"][k] = {"credits": c_, "usd": usd(c_), "returned_posts": p_, "cap_usd": usd(self.pool[k][1]), "cap_posts": self.pool[k][0]}
        out["sub_pools"] = {}
        for job, (pool, posts, credits) in self.sub.items():
            c_, p_ = self._sum("budget_day=? AND job_type=? AND is_preflight=0", (d, job))
            out["sub_pools"][job] = {"pool": pool, "credits": c_, "usd": usd(c_), "returned_posts": p_, "cap_usd": usd(credits), "cap_posts": posts}
        held = self.db.execute("SELECT COUNT(*), COALESCE(SUM(reserved_credits),0) FROM x_cost_ledger WHERE status='UNKNOWN_HELD'").fetchone()
        out["unknown_held"] = {"requests": held[0], "usd": usd(held[1])}
        out["billing_source"] = "ESTIMATE_FROM_QUOTED_RATE (15 credits x max(returned, 1)); not the provider's bill"
        out["paused"] = get_meta(self.db, "paused") or None
        out["tolerated_max_posts_per_page"] = self.tolerated
        return out

    def reserve(self, *, pool: str, is_preflight: bool = False, job_type: str | None = None, run_id=None, window=(None, None), cursor=None, attempt: int = 1, guard=None) -> str:
        """Atomic: check every cap including the full page about to be requested, then write the reservation.
        `guard(ledger, now)` is an extra check of the caller's own, run inside the same transaction; it returns the
        name of a limit it hits, or None."""
        if pool not in POOLS:
            raise ValueError("unknown pool")
        now = self.clock()
        d, since = self.day(now), store.iso(now - timedelta(hours=self.roll_hours))
        self.db.commit()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if get_meta(self.db, "paused"):
                raise Paused(get_meta(self.db, "paused"))
            dc, dp = self._sum("budget_day=?", (d,))
            rc, _ = self._sum("created_at_utc>?", (since,))
            pc, pp = self._sum("budget_day=? AND pool=?", (d, pool))
            checks = [("DAILY_POSTS", dp + self.check_posts > self.day_posts), ("DAILY_USD", dc + self.check_credits > self.day_credits),
                      ("ROLLING_USD", rc + self.check_credits > self.roll_credits),
                      (f"POOL_{pool.upper()}_POSTS", pp + self.check_posts > self.pool[pool][0]), (f"POOL_{pool.upper()}_USD", pc + self.check_credits > self.pool[pool][1])]
            if not is_preflight and (job_type or pool) in self.sub:
                job = job_type or pool
                sc, sp = self._sum("budget_day=? AND job_type=? AND is_preflight=0", (d, job))
                checks += [(f"SUBPOOL_{job.upper()}_POSTS", sp + self.check_posts > self.sub[job][1]), (f"SUBPOOL_{job.upper()}_USD", sc + self.check_credits > self.sub[job][2])]
            if is_preflight:
                fc, fp = self._sum("is_preflight=1", ())
                checks += [("PREFLIGHT_POSTS", fp + self.check_posts > self.pre_posts), ("PREFLIGHT_USD", fc + self.check_credits > self.pre_credits)]
            for kind, over in checks:
                if over:
                    raise LimitReached(kind)
            if guard is not None:
                kind = guard(self, now)
                if kind:
                    raise LimitReached(kind)
            rid = uuid.uuid4().hex
            self.db.execute("INSERT INTO x_cost_ledger VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (rid, job_type or pool, pool, int(is_preflight), run_id, window[0], window[1], cursor, attempt, self.page_credits, self.page_posts,
                             None, None, None, "RESERVED", "ESTIMATE", None, d, store.iso(now), None))
            self.db.execute("COMMIT")
            return rid
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def settle(self, request_id: str, *, returned: int, unique_ids: int) -> int:
        """Books what came back. More posts than the documented page size is a provider change: it is charged at the
        real count and all further requests are paused until someone looks."""
        credits = self.per_post * max(int(returned), 1)
        self.db.execute("UPDATE x_cost_ledger SET status='SETTLED', settled_credits=?, returned=?, unique_ids=?, settled_at_utc=? WHERE request_id=? AND status IN ('RESERVED','UNKNOWN_HELD')",
                        (credits, int(returned), int(unique_ids), store.iso(self.clock()), request_id))
        if returned > self.tolerated:
            set_meta(self.db, "paused", f"PAGE_OVER_DOCUMENTED_MAX:{returned}")
        self.db.commit()
        return credits

    def resume(self, reason: str) -> dict:
        """Lifts a pause after someone looked at it. The reason is required and kept, with what was lifted and when."""
        if not (reason or "").strip():
            raise ValueError("a resume needs its reason")
        was = get_meta(self.db, "paused")
        if not was:
            return {"status": "NOT_PAUSED", "paused": None}
        log = json.loads(get_meta(self.db, "pause_log") or "[]")
        log.append({"paused": was, "resumed_at_utc": store.iso(self.clock()), "reason": reason.strip()[:300]})
        set_meta(self.db, "pause_log", json.dumps(log, ensure_ascii=False))
        set_meta(self.db, "paused", "")
        self.db.commit()
        return {"status": "RESUMED", "was": was, "tolerated_max_posts_per_page": self.tolerated, "log_entries": len(log)}

    def hold_unknown(self, request_id: str, note: str) -> None:
        self.db.execute("UPDATE x_cost_ledger SET status='UNKNOWN_HELD', note=? WHERE request_id=? AND status='RESERVED'", (note[:120], request_id))
        self.db.commit()

    def release(self, request_id: str, evidence: str) -> None:
        """Only with explicit evidence of no charge (the provider documents HTTP 503 as not charged)."""
        if not evidence:
            raise ValueError("a release needs its evidence")
        self.db.execute("UPDATE x_cost_ledger SET status='RELEASED', settled_credits=0, returned=0, unique_ids=0, note=?, settled_at_utc=? WHERE request_id=? AND status IN ('RESERVED','UNKNOWN_HELD')",
                        (evidence[:400], store.iso(self.clock()), request_id))
        self.db.commit()

    def recover_open(self) -> int:
        """After a crash: a reservation left open by a dead process has an unknown outcome, so it is held."""
        n = self.db.execute("UPDATE x_cost_ledger SET status='UNKNOWN_HELD', note='PROCESS_ENDED_BEFORE_SETTLEMENT' WHERE status='RESERVED'").rowcount
        self.db.commit()
        return n

    def summary(self) -> dict:
        rows = self.db.execute("SELECT job_type, pool, is_preflight, status, COUNT(*) n, COALESCE(SUM(returned),0) r, COALESCE(SUM(unique_ids),0) u, "
                               "COALESCE(SUM(settled_credits),0) s, COALESCE(SUM(CASE WHEN status IN ('RESERVED','UNKNOWN_HELD') THEN reserved_credits ELSE 0 END),0) h, SUM(attempt>1) retries "
                               "FROM x_cost_ledger GROUP BY job_type, pool, is_preflight, status ORDER BY 1,2,3,4").fetchall()
        return {"rows": [{"job_type": r[0], "pool": r[1], "is_preflight": bool(r[2]), "status": r[3], "requests": r[4], "returned": r[5], "unique_ids": r[6],
                          "settled_usd": usd(r[7]), "held_or_reserved_usd": usd(r[8]), "retries": r[9] or 0} for r in rows],
                "requests_total": sum(r[4] for r in rows), "returned_total": sum(r[5] for r in rows),
                "settled_usd_total": usd(sum(r[7] for r in rows)), "held_or_reserved_usd_total": usd(sum(r[8] for r in rows)),
                "billing_source": "ESTIMATE_FROM_QUOTED_RATE; provider bill not read"}


class JobBudget:
    """The collector's view of the ledger for one job. Same three calls the collector makes around every request."""

    def __init__(self, ledger: Ledger, *, pool: str, is_preflight: bool = False, job_type: str | None = None, guard=None):
        self.ledger, self.pool, self.is_preflight, self.job_type, self.guard = ledger, pool, is_preflight, job_type or pool, guard
        self.current, self.requests, self.context = None, 0, {}

    def reserve_next_page(self, now=None) -> None:
        c = self.context
        self.current = self.ledger.reserve(pool=self.pool, is_preflight=self.is_preflight, job_type=self.job_type, run_id=c.get("run_id"),
                                           window=(c.get("ws"), c.get("we")), cursor=c.get("cursor"), attempt=c.get("attempt", 1), guard=self.guard)

    def charge(self, returned: int, now=None, unique_ids: int | None = None) -> Decimal:
        credits = self.ledger.settle(self.current, returned=returned, unique_ids=returned if unique_ids is None else unique_ids)
        self.current, self.requests = None, self.requests + 1
        return Decimal(credits) / Decimal(self.ledger.per_usd)

    def unknown(self, note: str) -> None:
        if self.current:
            self.ledger.hold_unknown(self.current, note); self.current = None; self.requests += 1

    def not_charged(self, evidence: str) -> None:
        if self.current:
            self.ledger.release(self.current, evidence); self.current = None; self.requests += 1
