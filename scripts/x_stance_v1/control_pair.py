"""The paired like-floor control, isolated from production (owner instruction of 2026-11-26, after the 10:20 round
showed the first version was not isolated).

A pair is one fixed query asked twice over one fixed window [start, end), once without a like operator and once
with the sample's floor. Each pair has its own `pair_id`, decided once when the pair is created: window, query
template and observation rule never change afterwards. A side that failed is retried on the same window, the side
that succeeded is never asked again, and a new pair is created only when no pair is open.

Isolation: every page, post and run of a pair is written to its own audit store (`x_stance_control_audit.sqlite3`
next to the production store). Production gets only the ledger rows (the requests are paid from the same budget),
so its sample, classification queue, aggregates and freshness never see a control post.

Cut-off: the pair's `active_until_utc` is checked in front of every network call, retries included
(`CutoffTransport`); after it, a pair can only be read back from the audit store."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import provider, store
from .ledger import JobBudget

DONE = {"COMPLETE", "PLANNED_SAMPLE"}
AUDIT_FILE = "x_stance_control_audit.sqlite3"
SCHEMA = [
    """CREATE TABLE IF NOT EXISTS x_control_pairs(pair_id TEXT PRIMARY KEY, name TEXT NOT NULL, template_sha256 TEXT NOT NULL,
       window_start_utc TEXT NOT NULL, window_end_utc TEXT NOT NULL, rule_json TEXT NOT NULL, created_at_utc TEXT NOT NULL,
       state TEXT NOT NULL, updated_at_utc TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS x_control_sides(pair_id TEXT NOT NULL, variant TEXT NOT NULL, query_hash TEXT NOT NULL, query_text TEXT NOT NULL,
       status TEXT NOT NULL, stop_reason TEXT, attempts INTEGER NOT NULL DEFAULT 0, first_request_utc TEXT, last_response_utc TEXT,
       returned INTEGER, new_posts INTEGER, pages INTEGER, cursor_left INTEGER, earliest_utc TEXT, latest_utc TEXT, run_id TEXT,
       PRIMARY KEY(pair_id, variant))""",
]
VARIANTS = ("no_threshold", "floor")


class CutoffReached(provider.StopAll):
    def __init__(self, until: str):
        super().__init__(f"CONTROL_CUTOFF:{until}")


class CutoffTransport:
    """Refuses every call at or after the cut-off. Sits between the collector and the real transport, so retries and
    back-off that cross the cut-off are refused too."""

    def __init__(self, inner, until: datetime, clock):
        self.inner, self.until, self.clock, self.calls, self.refused = inner, until, clock, 0, 0
        for k in ("min_interval",):
            if hasattr(inner, k):
                setattr(self, k, getattr(inner, k))

    def get(self, params):
        if self.clock() >= self.until:
            self.refused += 1
            raise CutoffReached(store.iso(self.until))
        self.calls += 1
        return self.inner.get(params)


def audit_path(prod_path: str | Path) -> Path:
    return Path(prod_path).with_name(AUDIT_FILE)


def connect_audit(path: str | Path):
    db = store.connect(path)
    for s in SCHEMA:
        db.execute(s)
    db.commit()
    return db


def pairs_cfg(cfg: dict) -> list[dict]:
    return list(((cfg.get("incremental") or {}).get("control_pairs")) or [])


def texts(p: dict) -> dict:
    return {"no_threshold": p["query"], "floor": p["query"] + p["floor_suffix"]}


def query_hash(cfg: dict, p: dict, variant: str) -> str:
    return store.sha(f"{cfg['provider_candidate']}|control|{p['name']}|{variant}|{texts(p)[variant]}")     # same hash as the first, unisolated version


def template_sha(p: dict) -> str:
    return store.sha(json.dumps({k: p.get(k) for k in ("name", "topic_id", "lang", "query", "floor_suffix")}, sort_keys=True))


def pair_id(p: dict, ws: int, we: int) -> str:
    return store.sha(f"{p['name']}|{template_sha(p)}|{ws}|{we}")[:16]


def _iso(t: int) -> str:
    return store.iso(datetime.fromtimestamp(t, timezone.utc))


def _next_window(adb, cfg: dict, p: dict, now: datetime) -> tuple[int, int, int]:
    inc = cfg.get("incremental") or {}
    end = int(now.replace(second=0, microsecond=0).timestamp()) - int(float(inc.get("lag_hours", 0)) * 3600)
    width = int(float(inc.get("interval_hours", 2)) * 3600)
    last = adb.execute("SELECT max(window_end_utc) FROM x_control_pairs WHERE name=?", (p["name"],)).fetchone()[0]
    start = end - width if last is None else int(store.parse(last).timestamp())
    gap, behind = 0, int(float(inc.get("max_window_hours", 6)) * 3600)
    if end - start > behind:                                    # a long outage: the pair keeps its width, the gap is recorded, not filled
        gap, start = end - width - start, end - width           # otherwise windows catch up contiguously, one pair per round
    return start, min(end, start + width), gap


def open_pairs(adb, name: str) -> list:
    return adb.execute("SELECT * FROM x_control_pairs WHERE name=? AND state IN ('OPEN','PARTIAL') ORDER BY window_start_utc", (name,)).fetchall()


def create_pair(adb, cfg: dict, p: dict, now: datetime) -> dict | None:
    ws, we, gap = _next_window(adb, cfg, p, now)
    if we - ws < 60:
        return None
    pid = pair_id(p, ws, we)
    rule = {"pages_per_side": int((cfg.get("incremental") or {}).get("pages_per_query", 1)), "lag_hours": (cfg.get("incremental") or {}).get("lag_hours"),
            "query_type": "Latest", "gap_before_seconds": gap, "observation": "each side reads its first pages of the same [start,end); a side with a cursor left is TRUNCATED"}
    t = store.iso(now)
    adb.execute("INSERT OR IGNORE INTO x_control_pairs VALUES(?,?,?,?,?,?,?,?,?)", (pid, p["name"], template_sha(p), _iso(ws), _iso(we), json.dumps(rule, sort_keys=True), t, "OPEN", t))
    for v in VARIANTS:
        qh = query_hash(cfg, p, v)
        adb.execute("INSERT OR IGNORE INTO x_queries VALUES(?,?,?,?,?,?)", (qh, cfg["provider_candidate"], p["topic_id"], p["lang"], f"control:{p['name']}:{v}", texts(p)[v]))
        adb.execute("INSERT OR IGNORE INTO x_control_sides(pair_id, variant, query_hash, query_text, status) VALUES(?,?,?,?,?)", (pid, v, qh, texts(p)[v], "NOT_ASKED"))
    adb.commit()
    return dict(adb.execute("SELECT * FROM x_control_pairs WHERE pair_id=?", (pid,)).fetchone())


def _side_facts(adb, cfg: dict, qh: str, ws: str, we: str) -> dict:
    r = adb.execute("SELECT run_id, status, stop_reason, pages, returned, new_posts, cursor, earliest_utc, latest_utc FROM x_query_runs WHERE query_hash=? AND window_start_utc=? AND window_end_utc=? ORDER BY updated_at_utc DESC LIMIT 1",
                    (qh, ws, we)).fetchone()
    return dict(r) if r else {}


def _settle(adb, pid: str) -> str:
    st = [r[0] for r in adb.execute("SELECT status FROM x_control_sides WHERE pair_id=?", (pid,))]
    state = "COMPLETE" if all(s in DONE for s in st) else ("PARTIAL" if any(s in DONE for s in st) else "OPEN")
    adb.execute("UPDATE x_control_pairs SET state=?, updated_at_utc=? WHERE pair_id=?", (state, store.utcnow(), pid))
    return state


def run(prod_db, adb, cfg: dict, transport, now: datetime, *, ledger, clock=None) -> dict:
    """One round of every configured pair. Production gets ledger rows only."""
    from .batch import utc_now
    clock = clock or utc_now
    out = {"job": "control_pair", "pairs": [], "requests": 0, "refused_after_cutoff": 0}
    for p in pairs_cfg(cfg):
        until = store.parse(p["active_until_utc"]) if p.get("active_until_utc") else None
        rec = {"name": p["name"], "active_until_utc": p.get("active_until_utc"), "sides_asked": [], "pair_id": None, "state": None}
        if until is not None and clock() >= until:
            for r in open_pairs(adb, p["name"]):
                adb.execute("UPDATE x_control_pairs SET state='CUTOFF', updated_at_utc=? WHERE pair_id=?", (store.iso(clock()), r["pair_id"]))
            adb.commit()
            rec["state"] = "CUTOFF"; out["pairs"].append(rec)
            continue
        op = open_pairs(adb, p["name"])
        pr = dict(op[0]) if op else create_pair(adb, cfg, p, now)
        if pr is None:
            rec["state"] = "NO_WINDOW"; out["pairs"].append(rec); continue
        rec["pair_id"], rule = pr["pair_id"], json.loads(pr["rule_json"])
        ws, we = int(store.parse(pr["window_start_utc"]).timestamp()), int(store.parse(pr["window_end_utc"]).timestamp())
        guard = CutoffTransport(transport, until, clock) if until is not None else transport
        col = provider.Collector(adb, cfg, budget=JobBudget(ledger, pool="recent", job_type="recent"), transport=guard, mode=provider.LIVE, clock=clock)
        for side in adb.execute("SELECT * FROM x_control_sides WHERE pair_id=? ORDER BY variant", (pr["pair_id"],)).fetchall():
            if side["status"] in DONE:
                continue                                         # the side that succeeded is never asked again
            q = {"query_hash": side["query_hash"], "topic_id": p["topic_id"], "lang": p["lang"], "query": side["query_text"]}
            t0 = store.iso(clock())
            try:
                res = col.collect_pages(q, ws, we, rule["pages_per_side"])
                status, reason = res.get("status"), res.get("stop_reason")
            except provider.StopAll as stop:                     # budget, credits or cut-off: this side stays open on this window
                status, reason = "STOPPED", stop.reason
            f = _side_facts(adb, cfg, side["query_hash"], pr["window_start_utc"], pr["window_end_utc"])
            adb.execute("""UPDATE x_control_sides SET status=?, stop_reason=?, attempts=attempts+1, first_request_utc=coalesce(first_request_utc, ?), last_response_utc=?,
                           returned=?, new_posts=?, pages=?, cursor_left=?, earliest_utc=?, latest_utc=?, run_id=? WHERE pair_id=? AND variant=?""",
                        (status, reason, t0, store.iso(clock()), f.get("returned"), f.get("new_posts"), f.get("pages"), int(bool(f.get("cursor"))), f.get("earliest_utc"),
                         f.get("latest_utc"), f.get("run_id"), pr["pair_id"], side["variant"]))
            adb.commit()
            rec["sides_asked"].append({"variant": side["variant"], "status": status, "reason": reason})
            if status == "STOPPED":
                break
        rec["state"] = _settle(adb, pr["pair_id"]); adb.commit()
        out["requests"] += col.budget.requests
        out["refused_after_cutoff"] += getattr(guard, "refused", 0)
        out["pairs"].append(rec)
    return out


def migrate_from_production(prod_db, adb, cfg: dict) -> dict:
    """Moves the pages the first, unisolated version stored in production into the audit store (copies; production
    rows are left in place and marked by the sample's control-only rule). Pairs are rebuilt from the stored windows."""
    out = {"pages": 0, "runs": 0, "pairs": 0}
    for p in pairs_cfg(cfg):
        hs = {v: query_hash(cfg, p, v) for v in VARIANTS}
        for v, qh in hs.items():
            adb.execute("INSERT OR IGNORE INTO x_queries VALUES(?,?,?,?,?,?)", (qh, cfg["provider_candidate"], p["topic_id"], p["lang"], f"control:{p['name']}:{v}", texts(p)[v]))
            for r in prod_db.execute("SELECT * FROM x_page_cache WHERE query_hash=?", (qh,)):
                adb.execute("INSERT OR IGNORE INTO x_page_cache VALUES(?,?,?,?,?,?,?,?)", tuple(r)); out["pages"] += 1
            for r in prod_db.execute("SELECT * FROM x_query_runs WHERE query_hash=?", (qh,)):
                adb.execute(f"INSERT OR IGNORE INTO x_query_runs VALUES({','.join('?' * len(r.keys()))})", tuple(r)); out["runs"] += 1
                for rp in prod_db.execute("SELECT * FROM x_run_posts WHERE run_id=?", (r["run_id"],)):
                    adb.execute("INSERT OR IGNORE INTO x_run_posts VALUES(?,?,?)", tuple(rp))
                    post = prod_db.execute("SELECT * FROM x_posts WHERE provider=? AND post_id=?", (rp["provider"], rp["post_id"])).fetchone()
                    if post:
                        adb.execute(f"INSERT OR IGNORE INTO x_posts VALUES({','.join('?' * len(post.keys()))})", tuple(post))
        wins = {}
        for r in adb.execute("SELECT query_hash, window_start_utc, window_end_utc, status, stop_reason, pages, returned, new_posts, cursor, earliest_utc, latest_utc, run_id, started_at_utc, updated_at_utc FROM x_query_runs WHERE query_hash IN (?,?)", tuple(hs.values())):
            wins.setdefault((r["window_start_utc"], r["window_end_utc"]), {})[r["query_hash"]] = dict(r)
        for (ws, we), sides in wins.items():
            s_, e_ = int(store.parse(ws).timestamp()), int(store.parse(we).timestamp())
            pid = pair_id(p, s_, e_)
            rule = {"pages_per_side": 1, "migrated_from": "production (first unisolated version)", "query_type": "Latest"}
            adb.execute("INSERT OR IGNORE INTO x_control_pairs VALUES(?,?,?,?,?,?,?,?,?)", (pid, p["name"], template_sha(p), ws, we, json.dumps(rule, sort_keys=True), store.utcnow(), "OPEN", store.utcnow()))
            for v, qh in hs.items():
                f = sides.get(qh) or {}
                adb.execute("INSERT OR REPLACE INTO x_control_sides VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (pid, v, qh, texts(p)[v], f.get("status", "NOT_ASKED"), f.get("stop_reason"), 1 if f else 0, f.get("started_at_utc"), f.get("updated_at_utc"),
                             f.get("returned"), f.get("new_posts"), f.get("pages"), int(bool(f.get("cursor"))), f.get("earliest_utc"), f.get("latest_utc"), f.get("run_id")))
            _settle(adb, pid); out["pairs"] += 1
    adb.commit()
    return out


def control_only_posts(db) -> set:
    """Posts in a store that only control queries returned (the first, unisolated version wrote them to production)."""
    rows = db.execute("""SELECT rp.provider, rp.post_id, max(CASE WHEN q.vocab_version LIKE 'control:%' THEN 0 ELSE 1 END) AS other
                         FROM x_run_posts rp JOIN x_query_runs r ON r.run_id=rp.run_id LEFT JOIN x_queries q ON q.query_hash=r.query_hash
                         GROUP BY rp.provider, rp.post_id""").fetchall()
    ctl = {h for (h,) in db.execute("SELECT query_hash FROM x_queries WHERE vocab_version LIKE 'control:%'")}
    if not ctl:
        return set()
    return {(r[0], r[1]) for r in rows if not r[2]}
