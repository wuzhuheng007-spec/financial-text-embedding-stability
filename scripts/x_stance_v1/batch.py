"""The metered jobs: preflight, the daily recent sample and the historical queue. All three draw on the one ledger.

This volume tier is a PLANNED SAMPLE. Before a job reads anything, its time slices, query order and page depth
are frozen and stored with a hash; the job then walks that plan breadth first (page 1 of every group, then page 2,
...), so a cap that stops it early leaves every group sampled to a similar depth instead of one group read deep.
Within a slice the provider returns newest first, so a shallow read sees the END of the slice; slices spread the
sample over the day but it is not a random sample of the day, of the topic or of X.

  recent      the last completed New York calendar date, 4 slices x every topic/language query
  historical  a 90-date range frozen at first run (dates before the first recent date), 2 slices x every query,
              ordered depth -> slice -> query -> date so every date gets a first page before any gets a second
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from . import config as cfgmod, provider, store
from .ledger import JobBudget, Ledger, get_meta, set_meta
from .sessions import NY, calendar_window

SCHEMA = """
CREATE TABLE IF NOT EXISTS x_sample_plans(plan_id TEXT PRIMARY KEY, job TEXT NOT NULL, plan_key TEXT NOT NULL, frozen_at_utc TEXT NOT NULL,
  sha256 TEXT NOT NULL, plan_json TEXT NOT NULL, UNIQUE(job, plan_key));
"""


def day_slices(d: date, n: int) -> list[tuple[int, int]]:
    """n equal parts of one New York calendar date, as Unix seconds [start, end). DST days are 23 or 25 hours."""
    a, b = (int(x.timestamp()) for x in calendar_window(d))
    edges = [a + (b - a) * i // n for i in range(n + 1)]
    return list(zip(edges[:-1], edges[1:]))


def last_completed_ny_date(now: datetime) -> date:
    return now.astimezone(NY).date() - timedelta(days=1)


def _freeze(db, job: str, key: str, plan: dict, now: datetime) -> dict:
    """Stores the plan once. A plan that already exists for this job and key is returned unchanged."""
    db.executescript(SCHEMA)
    row = db.execute("SELECT plan_json, sha256, frozen_at_utc FROM x_sample_plans WHERE job=? AND plan_key=?", (job, key)).fetchone()
    if row:
        return {**json.loads(row[0]), "sha256": row[1], "frozen_at_utc": row[2], "reused": True}
    body = json.dumps(plan, sort_keys=True, ensure_ascii=False)
    sha = store.sha(body)
    db.execute("INSERT INTO x_sample_plans VALUES(?,?,?,?,?,?)", (sha[:24], job, key, store.iso(now), sha, body))
    db.commit()
    return {**plan, "sha256": sha, "frozen_at_utc": store.iso(now), "reused": False}


def plan_recent(db, cfg: dict, d: date, now: datetime) -> dict:
    queries = provider.register_queries(db, cfg)
    n, page = cfg["acquisition"]["proposed_recent_slices_per_day"], cfg["quote"]["documented_max_posts_per_page"]
    units = [{"query_hash": q["query_hash"], "topic_id": q["topic_id"], "lang": q["lang"], "slice": i, "start": s, "end": e}
             for i, (s, e) in enumerate(day_slices(d, n)) for q in queries]
    depth = max(1, cfg["allocation"]["recent"]["max_returned_posts_per_day"] // page // max(1, len(units)))
    return _freeze(db, "recent", d.isoformat(), {"job": "recent", "ny_date": d.isoformat(), "vocab_version": cfg["topic_vocabulary_version"], "slices": n,
                                                 "pages_per_unit": depth, "order": "depth, slice, query", "units": units,
                                                 "sampling_mode": "PLANNED_SAMPLE", "max_posts_if_every_page_is_full": depth * len(units) * page}, now)


def history_range(db, cfg: dict, now: datetime) -> dict:
    """Frozen at the first call and never moved: the dates before the first recent date."""
    cur = get_meta(db, "history_range")
    if cur:
        return json.loads(cur)
    anchor = last_completed_ny_date(now)
    n = cfg["acquisition"]["initial_history_lookback_calendar_days"]
    first, last = anchor - timedelta(days=n), anchor - timedelta(days=1)
    r = {"anchor_ny_date": anchor.isoformat(), "first_ny_date": first.isoformat(), "last_ny_date": last.isoformat(), "days": n,
         "start_utc": store.iso(calendar_window(first)[0]), "end_utc_exclusive": store.iso(calendar_window(last)[1]), "frozen_at_utc": store.iso(now),
         "note": "first engineering range; not a sample size that could establish a market regularity"}
    set_meta(db, "history_range", json.dumps(r, sort_keys=True)); db.commit()
    return r


def plan_history(db, cfg: dict, now: datetime) -> dict:
    r, queries = history_range(db, cfg, now), provider.register_queries(db, cfg)
    return _freeze(db, "historical", f"{r['first_ny_date']}..{r['last_ny_date']}",
                   {"job": "historical", "range": r, "vocab_version": cfg["topic_vocabulary_version"], "slices": cfg["acquisition"]["proposed_history_slices_per_day"],
                    "max_depth": cfg["acquisition"].get("proposed_history_max_depth", 3), "order": "depth, slice, query, date (oldest first)",
                    "queries": [{k: q[k] for k in ("query_hash", "topic_id", "lang")} for q in queries], "sampling_mode": "PLANNED_SAMPLE"}, now)


def _history_units(plan: dict):
    first, last = date.fromisoformat(plan["range"]["first_ny_date"]), date.fromisoformat(plan["range"]["last_ny_date"])
    dates = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    for depth in range(1, plan["max_depth"] + 1):
        for i in range(plan["slices"]):
            for q in plan["queries"]:
                for d in dates:
                    s, e = day_slices(d, plan["slices"])[i]
                    yield depth, {"query_hash": q["query_hash"], "topic_id": q["topic_id"], "lang": q["lang"], "start": s, "end": e, "ny_date": d.isoformat()}


def _query(db, query_hash: str) -> dict:
    r = db.execute("SELECT query_hash, topic_id, lang, query_text FROM x_queries WHERE query_hash=?", (query_hash,)).fetchone()
    return {"query_hash": r[0], "topic_id": r[1], "lang": r[2], "query": r[3]}


def _walk(col: provider.Collector, db, steps) -> dict:
    """Runs (depth, unit) steps in order until the plan ends or the job must stop. A unit that already ended
    (COMPLETE) or failed (PARTIAL) is not asked for more pages."""
    out = {"steps": 0, "requests_before": col.budget.requests, "stopped": None, "units_touched": 0}
    done = {}
    try:
        for depth, u in steps:
            rid = col._run_id(u["query_hash"], u["start"], u["end"])
            if rid not in done:
                r = db.execute("SELECT status, pages FROM x_query_runs WHERE run_id=?", (rid,)).fetchone()
                done[rid] = (r[0], r[1]) if r else (None, 0)
            status, pages = done[rid]
            if status in ("COMPLETE", "PARTIAL") or pages >= depth:
                continue
            if col.free_gib() < col.min_free_gib:
                raise provider.StopAll("DISK_LOW_COLLECTION_PAUSED")
            res = col.collect_pages(_query(db, u["query_hash"]), u["start"], u["end"], depth)
            done[rid] = (res["status"], res.get("pages", depth)); out["steps"] += 1; out["units_touched"] += 1
    except provider.StopAll as stop:
        out["stopped"] = stop.reason
    out["requests"] = col.budget.requests - out.pop("requests_before")
    return out


def run_recent(db, cfg: dict, transport, now: datetime, *, ledger: Ledger | None = None) -> dict:
    ledger = ledger or Ledger(db, cfg, clock=lambda: now)
    d = last_completed_ny_date(now)
    plan = plan_recent(db, cfg, d, now)
    history_range(db, cfg, now)                                # freezes the historical range the first time anything runs
    col = provider.Collector(db, cfg, budget=JobBudget(ledger, pool="recent"), transport=transport, mode=provider.BATCH, clock=lambda: now)
    steps = ((depth, u) for depth in range(1, plan["pages_per_unit"] + 1) for u in plan["units"])
    return {"job": "recent", "ny_date": d.isoformat(), "plan_sha256": plan["sha256"], "plan_reused": plan["reused"], **_walk(col, db, steps)}


def run_history(db, cfg: dict, transport, now: datetime, *, ledger: Ledger | None = None) -> dict:
    ledger = ledger or Ledger(db, cfg, clock=lambda: now)
    plan = plan_history(db, cfg, now)
    col = provider.Collector(db, cfg, budget=JobBudget(ledger, pool="historical"), transport=transport, mode=provider.RETRO, clock=lambda: now)
    return {"job": "historical", "range": plan["range"], "plan_sha256": plan["sha256"], **_walk(col, db, _history_units(plan)), "progress": history_progress(db, cfg)}


def history_progress(db, cfg: dict) -> dict:
    cur = get_meta(db, "history_range")
    if not cur:
        return {"state": "NOT_STARTED", "range": None}
    r = json.loads(cur)
    row = db.execute("SELECT COUNT(DISTINCT substr(window_start_utc,1,10)), COUNT(*), COALESCE(SUM(returned),0), COALESCE(SUM(new_posts),0) FROM x_query_runs "
                     "WHERE window_start_utc>=? AND window_end_utc<=? AND pages>0", (r["start_utc"], r["end_utc_exclusive"])).fetchone()
    units = r["days"] * cfg["acquisition"]["proposed_history_slices_per_day"] * db.execute("SELECT COUNT(*) FROM x_queries WHERE vocab_version=? AND provider='twitterapi_io'", (cfg["topic_vocabulary_version"],)).fetchone()[0]
    return {"state": "IN_PROGRESS" if row[1] else "QUEUED", "range": r, "utc_dates_with_any_page": row[0], "units_with_any_page": row[1], "units_planned": units,
            "returned": row[2], "new_posts": row[3], "note": "coverage of the planned sample, not of what was posted on those dates"}


def run_preflight(db, cfg: dict, transport, now: datetime, *, ledger: Ledger | None = None) -> dict:
    """At most 200 returned posts / USD 0.03 over the whole life of the store, charged to the normal pools. One page
    for every topic/language query in the newest slice of the last completed New York date, and one page for the
    QQQ English query on an older date. The windows are the same ones the scheduled jobs use, so nothing read
    here is bought again later."""
    ledger = ledger or Ledger(db, cfg, clock=lambda: now)
    d = last_completed_ny_date(now)
    recent, hist = plan_recent(db, cfg, d, now), plan_history(db, cfg, now)
    last_slice = recent["slices"] - 1
    receipt = {"started_at_utc": store.iso(now), "provider": "twitterapi_io", "endpoint": provider.ENDPOINT, "query_type": "Latest", "requests": [], "stopped": None}
    plans = [("recent", provider.BATCH, u) for u in recent["units"] if u["slice"] == last_slice]
    back = date.fromisoformat(hist["range"]["anchor_ny_date"]) - timedelta(days=cfg["preflight"]["proposed_older_window_days_back"])
    q = next(x for x in hist["queries"] if x["topic_id"] == "qqq" and x["lang"] == "en")
    s, e = day_slices(back, hist["slices"])[hist["slices"] - 1]
    plans.append(("historical", provider.RETRO, {**q, "start": s, "end": e, "ny_date": back.isoformat()}))
    parse_failures = 0
    for pool, mode, u in plans:
        col = provider.Collector(db, cfg, budget=JobBudget(ledger, pool=pool, is_preflight=True, job_type="preflight"), transport=transport, mode=mode, clock=lambda: now)
        try:
            r = col.collect_pages(_query(db, u["query_hash"]), u["start"], u["end"], 1)
        except provider.StopAll as stop:
            receipt["stopped"] = stop.reason
            break
        parse_failures += col.parse_failures
        receipt["requests"].append({"topic_id": u["topic_id"], "lang": u["lang"], "pool": pool, "window_start_utc": store.iso(datetime.fromtimestamp(u["start"], timezone.utc)),
                                    "window_end_utc": store.iso(datetime.fromtimestamp(u["end"], timezone.utc)), **{k: r.get(k) for k in ("run_id", "status", "pages", "returned", "in_window", "out_of_window", "duplicates", "new_posts", "cost_estimate_usd")}})
    returned = sum(r["returned"] or 0 for r in receipt["requests"])
    ok = receipt["stopped"] is None and len(receipt["requests"]) == len(plans) and parse_failures == 0 and returned > 0
    receipt.update({"returned_total": returned, "in_window_total": sum(r["in_window"] or 0 for r in receipt["requests"]), "unparseable_posts": parse_failures,
                    "usage_after": ledger.usage(now), "verdict": "PASS" if ok else "FAIL",
                    "verdict_basis": "every planned request answered in the documented shape, every returned post parsed (id, author id, timestamp), at least one post returned",
                    "not_checked": "coverage of X, label quality, provider billing statement"})
    receipt["sha256"] = store.sha(json.dumps(receipt, sort_keys=True, ensure_ascii=False))
    set_meta(db, "preflight_last", json.dumps(receipt, sort_keys=True, ensure_ascii=False))
    if ok:
        set_meta(db, "preflight_passed", json.dumps({"at_utc": store.iso(now), "receipt_sha256": receipt["sha256"]}))
    db.commit()
    return receipt


def preflight_passed(db) -> bool:
    try:
        return bool(get_meta(db, "preflight_passed"))
    except Exception:  # noqa: BLE001 - a store without the table has no preflight
        return False


def run_daily(db, cfg: dict, env: dict, transport_factory, now: datetime) -> dict:
    """The scheduled entry point. Refuses unless the key is present and a preflight has passed."""
    ledger = Ledger(db, cfg, clock=lambda: now)
    gate = cfgmod.acquisition_gate(cfg, env, preflight_passed=preflight_passed(db))
    if not gate["allowed"]:
        return {"status": "REFUSED_FAIL_CLOSED", "requests_sent": 0, "gate": gate}
    held = ledger.recover_open()
    transport = transport_factory()
    out = {"status": "RAN", "recovered_open_reservations": held, "recent": run_recent(db, cfg, transport, now, ledger=ledger),
           "historical": run_history(db, cfg, transport, now, ledger=ledger)}
    out["usage"] = ledger.usage(now)
    return out
