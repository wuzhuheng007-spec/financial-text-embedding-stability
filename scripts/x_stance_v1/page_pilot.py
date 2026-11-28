"""QQQ-only page pilot (owner instruction of 2026-11-28): pages 2 and 3 of the regular two-hourly keyword windows.

What it asks. The regular increment reads one page per query and window (`pages_per_query: 1`); that page stays
exactly as it is and is the same-window control. Only when the regular first page of a pilot topic (QQQ) came back
as a planned sample with a cursor left, the pilot continues THAT window from THAT cursor up to page 3, in the same
round, with the same query, window and order. It never asks page 1 again and never asks a window twice.

Isolation (the lesson of the like-floor control, 2026-11-26). Pages 2 and 3, their posts and runs are written to the
pilot's own store (`x_stance_page_pilot.sqlite3` next to the production store). To continue the window there, the
production run's checkpoint, its first page and its posts are copied in first; production rows are never changed.
Production gets ledger rows only (job type `pilot_pages`, recent pool), so its sample, classification queue,
aggregates and freshness never see a pilot post.

Budget. The pilot has no allocation of its own in the config's `budget` / `allocation` blocks (those are bound by
the preflight receipt; changing them would close the collection gate). It is paid from the recent pool and its own
caps live under `incremental.page_pilot.caps`, counted from the ledger. In front of every pilot request, inside the
ledger's transaction, a guard checks that after this page the recent pool, the all-jobs day and the rolling window
still have room for every regular request of the rest of the Beijing budget day: for each remaining round, every
regular keyword query and every watched-author query at a full (tolerated) page, but never more than what is left
of their own shares; and the whole remaining historical pool. If not, the pilot stops for the day. The regular
first pages therefore keep their whole allowance.

Activation. Nothing is sent unless `approved` names the owner's approval and the run is inside
[active_from_utc, active_until_utc). The cut-off is checked in front of every network call (retries included).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import control_pair, provider, store
from .ledger import JobBudget, Ledger

AUDIT_FILE = "x_stance_page_pilot.sqlite3"
JOB = "pilot_pages"
SCHEMA = [
    """CREATE TABLE IF NOT EXISTS x_pilot_units(unit_id TEXT PRIMARY KEY, pilot TEXT NOT NULL, run_id TEXT NOT NULL, query_hash TEXT NOT NULL,
       topic_id TEXT NOT NULL, lang TEXT NOT NULL, window_start_utc TEXT NOT NULL, window_end_utc TEXT NOT NULL, round_at_utc TEXT NOT NULL,
       page1_returned INTEGER, page1_new_posts INTEGER, status TEXT NOT NULL, stop_reason TEXT, pages_after INTEGER, requests INTEGER,
       created_at_utc TEXT NOT NULL, updated_at_utc TEXT NOT NULL)""",
]
COUNT = Ledger._COUNT


def pilot_cfg(cfg: dict) -> dict | None:
    return (cfg.get("incremental") or {}).get("page_pilot")


def active(cfg: dict, now: datetime) -> tuple[bool, str]:
    p = pilot_cfg(cfg)
    if not p:
        return False, "NOT_CONFIGURED"
    if not (p.get("approved") or "").strip():
        return False, "NOT_APPROVED"
    if not p.get("active_from_utc") or not p.get("active_until_utc"):
        return False, "NO_ACTIVE_WINDOW"
    if now < store.parse(p["active_from_utc"]):
        return False, "BEFORE_START"
    if now >= store.parse(p["active_until_utc"]):
        return False, "ENDED"
    return True, "ACTIVE"


def audit_path(prod_path: str | Path) -> Path:
    return Path(prod_path).with_name(AUDIT_FILE)


def connect_audit(path: str | Path):
    db = store.connect(path)
    for s in SCHEMA:
        db.execute(s)
    db.commit()
    return db


def _sum(ledger: Ledger, where: str, args: tuple) -> tuple[int, int]:
    return ledger._sum(where, args)


def _rounds_left(ledger: Ledger, now: datetime, interval_hours: float) -> int:
    """Regular rounds still to come in this Beijing budget day after the current one."""
    local = now.astimezone(ledger.tz)
    midnight = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    n, t = 0, local + timedelta(hours=interval_hours)
    while t < midnight:
        n, t = n + 1, t + timedelta(hours=interval_hours)
    return n


def headroom(ledger: Ledger, cfg: dict, now: datetime, *, keyword_queries: int, watched_queries: int) -> dict:
    """The numbers the guard decides on, in credits and posts. `ok` is whether one more pilot page fits."""
    p, inc = pilot_cfg(cfg) or {}, cfg.get("incremental") or {}
    caps = p.get("caps") or {}
    d, since = ledger.day(now), store.iso(now - timedelta(hours=ledger.roll_hours))
    cc, cp = ledger.check_credits, ledger.check_posts
    rounds = _rounds_left(ledger, now, float(inc.get("interval_hours", 2)))

    def share(job, n_queries):                                   # what the regular job may still need today, bounded by its share
        _, posts_cap, cred_cap = ledger.sub[job]
        c_, p_ = _sum(ledger, "budget_day=? AND job_type=? AND is_preflight=0", (d, job))
        return min(max(0, cred_cap - c_), rounds * n_queries * cc), min(max(0, posts_cap - p_), rounds * n_queries * cp)

    kw_c, kw_p = share("recent", keyword_queries)
    wl_c, wl_p = share("watchlist", watched_queries)
    pool_c, pool_p = _sum(ledger, "budget_day=? AND pool='recent'", (d,))
    hist_c, hist_p = _sum(ledger, "budget_day=? AND pool='historical'", (d,))
    day_c, day_p = _sum(ledger, "budget_day=?", (d,))
    roll_c, _ = _sum(ledger, "created_at_utc>?", (since,))
    hist_left_c, hist_left_p = max(0, ledger.pool["historical"][1] - hist_c), max(0, ledger.pool["historical"][0] - hist_p)
    pil_day_c, _ = _sum(ledger, "budget_day=? AND job_type=?", (d, JOB))
    pil_all_c, _ = _sum(ledger, "job_type=?", (JOB,))
    pil_day_n = ledger.db.execute("SELECT COUNT(*) FROM x_cost_ledger WHERE budget_day=? AND job_type=? AND status<>'RELEASED'", (d, JOB)).fetchone()[0]
    pil_all_n = ledger.db.execute("SELECT COUNT(*) FROM x_cost_ledger WHERE job_type=? AND status<>'RELEASED'", (JOB,)).fetchone()[0]
    per_usd = ledger.per_usd
    c = lambda usd: int(round(float(usd) * per_usd))
    checks = [
        ("PILOT_DAY_REQUESTS", pil_day_n + 1 > int(caps.get("max_extra_requests_per_day", 0))),
        ("PILOT_DAY_USD", pil_day_c + cc > c(caps.get("max_extra_usd_per_day", 0))),
        ("PILOT_TOTAL_REQUESTS", pil_all_n + 1 > int(caps.get("max_extra_requests_total", 0))),
        ("PILOT_TOTAL_USD", pil_all_c + cc > c(caps.get("max_extra_usd_total", 0))),
        ("PILOT_KEEPS_REGULAR:POOL_RECENT_USD", pool_c + cc + kw_c + wl_c > ledger.pool["recent"][1]),
        ("PILOT_KEEPS_REGULAR:POOL_RECENT_POSTS", pool_p + cp + kw_p + wl_p > ledger.pool["recent"][0]),
        ("PILOT_KEEPS_REGULAR:DAILY_USD", day_c + cc + kw_c + wl_c + hist_left_c > ledger.day_credits),
        ("PILOT_KEEPS_REGULAR:DAILY_POSTS", day_p + cp + kw_p + wl_p + hist_left_p > ledger.day_posts),
        ("PILOT_KEEPS_REGULAR:ROLLING_USD", roll_c + cc + kw_c + wl_c + hist_left_c > ledger.roll_credits),
    ]
    hit = next((k for k, over in checks if over), None)
    return {"budget_day": d, "rounds_left_today": rounds, "page_check": {"credits": cc, "posts": cp},
            "kept_for_regular": {"keyword": {"credits": kw_c, "posts": kw_p}, "watched_authors": {"credits": wl_c, "posts": wl_p}, "historical_pool_left": {"credits": hist_left_c, "posts": hist_left_p}},
            "used": {"recent_pool": {"credits": pool_c, "posts": pool_p}, "all_jobs_today": {"credits": day_c, "posts": day_p}, "rolling_credits": roll_c,
                     "pilot_today": {"requests": pil_day_n, "credits": pil_day_c}, "pilot_total": {"requests": pil_all_n, "credits": pil_all_c}},
            "caps": caps, "ok": hit is None, "limit": hit}


def guard(cfg: dict, *, keyword_queries: int, watched_queries: int):
    def g(ledger: Ledger, now: datetime):
        return headroom(ledger, cfg, now, keyword_queries=keyword_queries, watched_queries=watched_queries)["limit"]
    return g


def _seed(prod_db, adb, run_id: str) -> dict | None:
    """Copies the production run's checkpoint, query, pages and posts, so the pilot store resumes from its cursor."""
    r = prod_db.execute("SELECT * FROM x_query_runs WHERE run_id=?", (run_id,)).fetchone()
    if r is None:
        return None
    q = prod_db.execute("SELECT * FROM x_queries WHERE query_hash=?", (r["query_hash"],)).fetchone()
    if q is not None:
        adb.execute(f"INSERT OR IGNORE INTO x_queries VALUES({','.join('?' * len(q.keys()))})", tuple(q))
    if adb.execute("SELECT 1 FROM x_query_runs WHERE run_id=?", (run_id,)).fetchone() is None:
        adb.execute(f"INSERT INTO x_query_runs VALUES({','.join('?' * len(r.keys()))})", tuple(r))
    for pc in prod_db.execute("SELECT * FROM x_page_cache WHERE provider=? AND query_hash=? AND window_start_utc=? AND window_end_utc=?",
                              (r["provider"], r["query_hash"], r["window_start_utc"], r["window_end_utc"])):
        adb.execute("INSERT OR IGNORE INTO x_page_cache VALUES(?,?,?,?,?,?,?,?)", tuple(pc))
    for rp in prod_db.execute("SELECT * FROM x_run_posts WHERE run_id=?", (run_id,)):
        adb.execute("INSERT OR IGNORE INTO x_run_posts VALUES(?,?,?)", tuple(rp))
        post = prod_db.execute("SELECT * FROM x_posts WHERE provider=? AND post_id=?", (rp["provider"], rp["post_id"])).fetchone()
        if post is not None:
            adb.execute(f"INSERT OR IGNORE INTO x_posts VALUES({','.join('?' * len(post.keys()))})", tuple(post))
    adb.commit()
    return dict(r)


def candidates(prod_db, cfg: dict, asked: list[tuple[dict, dict]]) -> list[tuple[dict, dict]]:
    """Regular units of this round whose first page was a planned sample of a pilot topic with a cursor left."""
    p = pilot_cfg(cfg) or {}
    topics, langs = set(p.get("topic_ids") or []), set(p.get("langs") or []) or None
    out = []
    for u, res in asked:
        if u.get("job_type") != "recent" or u.get("control") or u["topic_id"] not in topics or (langs and u["lang"] not in langs):
            continue
        if res.get("status") != "PLANNED_SAMPLE" or not res.get("run_id"):
            continue
        r = prod_db.execute("SELECT cursor, pages FROM x_query_runs WHERE run_id=?", (res["run_id"],)).fetchone()
        if r and r["cursor"] and int(r["pages"]) < int(p.get("pages_per_query", 3)):
            out.append((u, res))
    return out


def run(prod_db, adb, cfg: dict, transport, now: datetime, *, ledger: Ledger, asked: list[tuple[dict, dict]], keyword_queries: int, watched_queries: int, clock=None) -> dict:
    from .batch import utc_now
    clock = clock or utc_now
    p = pilot_cfg(cfg)
    ok, state = active(cfg, now)
    out = {"job": "page_pilot", "state": state, "units": [], "requests": 0, "refused_after_cutoff": 0, "stopped": None}
    if not ok:
        return out
    until = store.parse(p["active_until_utc"])
    gate = control_pair.CutoffTransport(transport, until, clock)
    budget = JobBudget(ledger, pool="recent", job_type=JOB, guard=guard(cfg, keyword_queries=keyword_queries, watched_queries=watched_queries))
    col = provider.Collector(adb, cfg, budget=budget, transport=gate, mode=provider.LIVE, clock=clock)
    for u, res in candidates(prod_db, cfg, asked):
        uid = store.sha(f"{p['name']}|{res['run_id']}")[:24]
        if adb.execute("SELECT 1 FROM x_pilot_units WHERE unit_id=?", (uid,)).fetchone():
            continue                                             # a window is continued once
        seeded = _seed(prod_db, adb, res["run_id"])
        if seeded is None:
            continue
        t0 = store.iso(clock())
        if out["stopped"]:                                       # after a stop the rest of this round's windows are recorded, not asked
            adb.execute("INSERT INTO x_pilot_units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (uid, p["name"], res["run_id"], u["query_hash"], u["topic_id"], u["lang"], seeded["window_start_utc"], seeded["window_end_utc"], store.iso(now),
                         seeded["returned"], seeded["new_posts"], "SKIPPED", out["stopped"], 0, 0, t0, t0))
            adb.commit()
            out["units"].append({"unit_id": uid, "topic_id": u["topic_id"], "lang": u["lang"], "status": "SKIPPED", "stop_reason": out["stopped"], "requests": 0})
            continue
        q = {k: u[k] for k in ("query_hash", "topic_id", "lang", "query")}
        before = budget.requests
        try:
            r2 = col.collect_pages(q, u["start"], u["end"], int(p.get("pages_per_query", 3)))
            status, reason = r2.get("status"), r2.get("stop_reason")
        except provider.StopAll as stop:
            status, reason = "STOPPED", stop.reason
        run_row = adb.execute("SELECT pages FROM x_query_runs WHERE run_id=?", (res["run_id"],)).fetchone()
        adb.execute("INSERT INTO x_pilot_units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (uid, p["name"], res["run_id"], u["query_hash"], u["topic_id"], u["lang"], seeded["window_start_utc"], seeded["window_end_utc"], store.iso(now),
                     seeded["returned"], seeded["new_posts"], status, reason, (run_row["pages"] if run_row else 1) - int(seeded["pages"]), budget.requests - before, t0, store.iso(clock())))
        adb.commit()
        out["units"].append({"unit_id": uid, "topic_id": u["topic_id"], "lang": u["lang"], "status": status, "stop_reason": reason, "requests": budget.requests - before})
        if status == "STOPPED":
            out["stopped"] = reason
    out["requests"] = budget.requests
    out["refused_after_cutoff"] = gate.refused
    out["transient_errors"] = col.transient_errors
    return out


def pages_of(adb, provider_name: str, query_hash: str, ws: str, we: str) -> list[dict]:
    """The window's pages in order, rebuilt from the stored responses: page 1 is the empty cursor, each next page the
    cursor the previous one returned."""
    cache = {r["cursor"]: r for r in adb.execute("SELECT cursor, response_json, fetched_at_utc FROM x_page_cache WHERE provider=? AND query_hash=? AND window_start_utc=? AND window_end_utc=?",
                                                  (provider_name, query_hash, ws, we))}
    out, cur, seen = [], "", set()
    while cur in cache and cur not in seen:
        seen.add(cur)
        body = json.loads(cache[cur]["response_json"])
        tweets = body.get("tweets") or []
        out.append({"page": len(out) + 1, "fetched_at_utc": cache[cur]["fetched_at_utc"], "ids": [str(t.get("id")) for t in tweets if isinstance(t, dict) and t.get("id")],
                    "authors": [str((t.get("author") or {}).get("id")) for t in tweets if isinstance(t, dict)]})
        if not body.get("has_next_page"):
            break
        cur = body.get("next_cursor") or ""
    return out


def report(prod_db, adb, cfg: dict) -> dict:
    """Counts only (no ids, texts or authors leave this function): per page number, what came back, what is new
    against production and the earlier pages of the same window, distinct authors, and what the ledger booked."""
    p = pilot_cfg(cfg) or {}
    blank = lambda: {"windows": 0, "returned": 0, "new_vs_production_and_earlier_pages": 0, "authors": set(), "new_authors_vs_page1": set()}
    per, per_lang = {k: blank() for k in (1, 2, 3)}, {}
    units = [dict(r) for r in adb.execute("SELECT * FROM x_pilot_units WHERE pilot=? ORDER BY window_start_utc", (p.get("name"),))]
    for u in units:
        pg = pages_of(adb, "twitterapi_io", u["query_hash"], u["window_start_utc"], u["window_end_utc"])
        seen, a1 = set(), set()
        for x in pg[:3]:
            k = x["page"]
            fresh = [i for i in x["ids"] if i not in seen and (k == 1 or prod_db.execute("SELECT 1 FROM x_posts WHERE post_id=?", (i,)).fetchone() is None)]
            for bucket in (per[k], per_lang.setdefault(u["lang"], {kk: blank() for kk in (1, 2, 3)})[k]):
                bucket["windows"] += 1; bucket["returned"] += len(x["ids"])
                bucket["new_vs_production_and_earlier_pages"] += len(fresh) if k > 1 else 0
                bucket["authors"] |= set(x["authors"])
                if k > 1:
                    bucket["new_authors_vs_page1"] |= set(x["authors"]) - a1
            seen |= set(x["ids"])
            if k == 1:
                a1 = set(x["authors"])
    led = prod_db.execute(f"SELECT COUNT(*), {COUNT} FROM x_cost_ledger WHERE job_type=?", (JOB,)).fetchone()
    by_status = dict(prod_db.execute("SELECT status, COUNT(*) FROM x_cost_ledger WHERE job_type=? GROUP BY 1", (JOB,)).fetchall())
    show = lambda d: {k: {"windows": v["windows"], "returned": v["returned"], "new_posts": v["new_vs_production_and_earlier_pages"] if k > 1 else None,
                          "distinct_authors": len(v["authors"]), "authors_not_on_page1_of_the_same_window": len(v["new_authors_vs_page1"]) if k > 1 else None} for k, v in d.items()}
    return {"pilot": p.get("name"), "units": len(units), "by_status": {s: sum(1 for u in units if u["status"] == s) for s in {u["status"] for u in units}},
            "stop_reasons": {r: sum(1 for u in units if u["stop_reason"] == r) for r in {u["stop_reason"] for u in units if u["status"] in ("STOPPED", "SKIPPED")}},
            "per_page": show(per), "per_lang": {lg: show(d) for lg, d in sorted(per_lang.items())},
            "ledger": {"requests": led[0], "credits": int(led[1]), "usd": str(int(led[1]) / 100000), "posts_counted": int(led[2]), "by_status": by_status},
            "note": "page 1 is the regular production page (the same-window control); pages 2 and 3 live only in the pilot store"}


def preview(prod_db, cfg: dict, now: datetime, ledger: Ledger, *, keyword_queries: int, watched_queries: int) -> dict:
    ok, state = active(cfg, now)
    p = pilot_cfg(cfg)
    if not p:
        return {"state": state}
    return {"state": state, "name": p.get("name"), "topic_ids": p.get("topic_ids"), "langs": p.get("langs"), "pages_per_query": p.get("pages_per_query"),
            "active_from_utc": p.get("active_from_utc"), "active_until_utc": p.get("active_until_utc"), "approved": bool((p.get("approved") or "").strip()),
            "headroom_now": headroom(ledger, cfg, now, keyword_queries=keyword_queries, watched_queries=watched_queries)}


def main(argv=None) -> int:
    """python -m scripts.x_stance_v1.page_pilot report|preview --db PROD [--audit PILOT] [--config CFG]: read-only."""
    import argparse
    from . import config as cfgmod
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["report", "preview"]); ap.add_argument("--db", required=True); ap.add_argument("--audit"); ap.add_argument("--config")
    ap.add_argument("--keyword-queries", type=int, default=8); ap.add_argument("--watched-queries", type=int, default=24)
    a = ap.parse_args(argv)
    cfg = cfgmod.load(a.config)
    prod = store.connect(a.db, readonly=True)
    now = datetime.now(timezone.utc)
    if a.action == "preview":
        out = preview(prod, cfg, now, Ledger(prod, cfg, readonly=True), keyword_queries=a.keyword_queries, watched_queries=a.watched_queries)
    else:
        adb = store.connect(a.audit or audit_path(a.db), readonly=True)
        out = report(prod, adb, cfg)
    print(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
