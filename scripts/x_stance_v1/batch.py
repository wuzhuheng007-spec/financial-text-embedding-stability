"""The metered jobs: preflight, the daily recent sample and the historical queue. All three draw on the one ledger.

Two different notions of time are kept apart:
  anchor  fixed when a job starts; it only decides WHICH New York date is sampled and which plan is frozen
  clock   read again for every reservation, response and settlement; budget days, rolling windows, fetched_at and
          first_seen_at all come from it. A job that runs past midnight in Shanghai books its later requests on the
          new budget day.

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

import hashlib
import json
from datetime import date, datetime, timedelta, timezone

from . import config as cfgmod, provider, store, vocab
from .ledger import JobBudget, Ledger, get_meta, set_meta
from .sessions import NY, calendar_window

RECEIPT_KEY = "preflight_receipt"                              # the latest receipt only; older ones stay in x_preflight_receipts


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def day_slices(d: date, n: int) -> list[tuple[int, int]]:
    """n equal parts of one New York calendar date, as Unix seconds [start, end). DST days are 23 or 25 hours."""
    a, b = (int(x.timestamp()) for x in calendar_window(d))
    edges = [a + (b - a) * i // n for i in range(n + 1)]
    return list(zip(edges[:-1], edges[1:]))


def last_completed_ny_date(now: datetime) -> date:
    return now.astimezone(NY).date() - timedelta(days=1)


def _freeze(db, job: str, key: str, plan: dict, now: datetime) -> dict:
    """Stores the plan once. A plan that already exists for this job and key is returned unchanged."""
    row = db.execute("SELECT plan_json, sha256, frozen_at_utc FROM x_sample_plans WHERE job=? AND plan_key=?", (job, key)).fetchone()
    if row:
        return {**json.loads(row[0]), "sha256": row[1], "frozen_at_utc": row[2], "reused": True}
    body = json.dumps(plan, sort_keys=True, ensure_ascii=False)
    sha = store.sha(body)
    db.execute("INSERT INTO x_sample_plans VALUES(?,?,?,?,?,?)", (sha[:24], job, key, store.iso(now), sha, body))
    db.commit()
    return {**plan, "sha256": sha, "frozen_at_utc": store.iso(now), "reused": False}


def plan_recent(db, cfg: dict, d: date, now: datetime) -> dict:
    """The vocabulary in force for date d is fixed into the plan. A plan that already exists is never re-cut by a
    later vocabulary change: the change takes effect from the next date that has no plan yet."""
    existing = db.execute("SELECT plan_json, sha256, frozen_at_utc FROM x_sample_plans WHERE job='recent' AND plan_key=?", (d.isoformat(),)).fetchone()
    if existing:
        return {**json.loads(existing[0]), "sha256": existing[1], "frozen_at_utc": existing[2], "reused": True}
    cfg = vocab.apply(db, cfg, d, now)
    queries = provider.register_queries(db, cfg)
    n, page = cfg["acquisition"]["proposed_recent_slices_per_day"], cfg["quote"]["documented_max_posts_per_page"]
    units = [{"query_hash": q["query_hash"], "topic_id": q["topic_id"], "lang": q["lang"], "slice": i, "start": s, "end": e}
             for i, (s, e) in enumerate(day_slices(d, n)) for q in queries]
    depth = max(1, cfg["allocation"]["recent"]["max_returned_posts_per_day"] // page // max(1, len(units)))
    return _freeze(db, "recent", d.isoformat(), {"job": "recent", "ny_date": d.isoformat(), "vocab_version": cfg["topic_vocabulary_version"], "slices": n,
                                                 "pages_per_unit": depth, "order": "depth, slice, query", "units": units,
                                                 "sampling_mode": "PLANNED_SAMPLE", "max_posts_if_every_page_is_full": depth * len(units) * page}, now)


def history_range(db, cfg: dict, anchor: datetime) -> dict:
    """Frozen at the first call and never moved: the dates before the first recent date."""
    cur = get_meta(db, "history_range")
    if cur:
        return json.loads(cur)
    a = last_completed_ny_date(anchor)
    n = cfg["acquisition"]["initial_history_lookback_calendar_days"]
    first, last = a - timedelta(days=n), a - timedelta(days=1)
    r = {"anchor_ny_date": a.isoformat(), "first_ny_date": first.isoformat(), "last_ny_date": last.isoformat(), "days": n,
         "start_utc": store.iso(calendar_window(first)[0]), "end_utc_exclusive": store.iso(calendar_window(last)[1]), "frozen_at_utc": store.iso(anchor),
         "note": "first engineering range; not a sample size that could establish a market regularity"}
    set_meta(db, "history_range", json.dumps(r, sort_keys=True)); db.commit()
    return r


def plan_history(db, cfg: dict, anchor: datetime) -> dict:
    """Frozen once with the vocabulary of that moment. A later vocabulary change does not replace this plan; a new
    historical task would need its own explicit plan (and would still draw on the same ledger)."""
    r = history_range(db, cfg, anchor)
    key = f"{r['first_ny_date']}..{r['last_ny_date']}"
    existing = db.execute("SELECT plan_json, sha256, frozen_at_utc FROM x_sample_plans WHERE job='historical' AND plan_key=?", (key,)).fetchone()
    if existing:
        return {**json.loads(existing[0]), "sha256": existing[1], "frozen_at_utc": existing[2], "reused": True}
    cfg = vocab.apply(db, cfg, date.fromisoformat(r["anchor_ny_date"]), anchor)
    queries = provider.register_queries(db, cfg)
    return _freeze(db, "historical", key,
                   {"job": "historical", "range": r, "vocab_version": cfg["topic_vocabulary_version"], "slices": cfg["acquisition"]["proposed_history_slices_per_day"],
                    "max_depth": cfg["acquisition"].get("proposed_history_max_depth", 3), "order": "depth, slice, query, date (oldest first)",
                    "queries": [{k: q[k] for k in ("query_hash", "topic_id", "lang")} for q in queries], "sampling_mode": "PLANNED_SAMPLE"}, anchor)


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
    out = {"steps": 0, "requests_before": col.budget.requests, "stopped": None, "units_touched": 0, "protocol_anomalies": 0}
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
    out["protocol_anomalies"] = col.protocol_anomalies
    out["requests"] = col.budget.requests - out.pop("requests_before")
    return out


def run_recent(db, cfg: dict, transport, anchor: datetime, *, clock=None, ledger: Ledger | None = None) -> dict:
    clock = clock or utc_now
    ledger = ledger or Ledger(db, cfg, clock=clock)
    d = last_completed_ny_date(anchor)
    plan = plan_recent(db, cfg, d, anchor)
    history_range(db, cfg, anchor)                             # freezes the historical range the first time anything runs
    col = provider.Collector(db, cfg, budget=JobBudget(ledger, pool="recent"), transport=transport, mode=provider.BATCH, clock=clock)
    steps = ((depth, u) for depth in range(1, plan["pages_per_unit"] + 1) for u in plan["units"])
    return {"job": "recent", "ny_date": d.isoformat(), "vocab_version": plan["vocab_version"], "plan_sha256": plan["sha256"], "plan_reused": plan["reused"], **_walk(col, db, steps)}


def run_history(db, cfg: dict, transport, anchor: datetime, *, clock=None, ledger: Ledger | None = None) -> dict:
    clock = clock or utc_now
    ledger = ledger or Ledger(db, cfg, clock=clock)
    plan = plan_history(db, cfg, anchor)
    col = provider.Collector(db, cfg, budget=JobBudget(ledger, pool="historical"), transport=transport, mode=provider.RETRO, clock=clock)
    return {"job": "historical", "range": plan["range"], "vocab_version": plan["vocab_version"], "plan_sha256": plan["sha256"], **_walk(col, db, _history_units(plan)),
            "progress": history_progress(db, cfg)}


def history_progress(db, cfg: dict) -> dict:
    cur = get_meta(db, "history_range")
    if not cur:
        return {"state": "NOT_STARTED", "range": None}
    r = json.loads(cur)
    row = db.execute("SELECT COUNT(DISTINCT substr(window_start_utc,1,10)), COUNT(*), COALESCE(SUM(returned),0), COALESCE(SUM(new_posts),0) FROM x_query_runs "
                     "WHERE window_start_utc>=? AND window_end_utc<=? AND pages>0", (r["start_utc"], r["end_utc_exclusive"])).fetchone()
    plan = db.execute("SELECT plan_json FROM x_sample_plans WHERE job='historical' ORDER BY frozen_at_utc LIMIT 1").fetchone()
    nq = len(json.loads(plan[0])["queries"]) if plan else None
    return {"state": "IN_PROGRESS" if row[1] else "QUEUED", "range": r, "utc_dates_with_any_page": row[0], "units_with_any_page": row[1],
            "units_planned": (r["days"] * cfg["acquisition"]["proposed_history_slices_per_day"] * nq) if nq else None,
            "returned": row[2], "new_posts": row[3], "note": "coverage of the planned sample, not of what was posted on those dates"}


# ---------- preflight: receipt, binding, gate ----------
def key_fingerprint(env: dict, cfg: dict) -> str | None:
    key = (env.get(cfgmod.KEY_ENV[cfg["provider_candidate"]]) or "").strip()
    return hashlib.sha256(("x-stance-key-fingerprint|" + key).encode("utf-8")).hexdigest()[:16] if key else None


def binding(db, cfg: dict, env: dict) -> dict:
    """What a receipt is valid for. If any of this changes, the receipt no longer opens the gate."""
    relevant = {k: cfg[k] for k in ("quote", "budget", "allocation", "preflight")}
    relevant["acquisition"] = {k: v for k, v in cfg["acquisition"].items() if k in ("endpoint", "sort", "collection_mode", "proposed_recent_slices_per_day", "proposed_history_slices_per_day")}
    return {"provider": cfg["provider_candidate"], "endpoint": provider.ENDPOINT, "validator": provider.PROTOCOL_VALIDATOR,
            "config_sha256": store.sha(json.dumps(relevant, sort_keys=True)), "base_vocab_version": cfg["topic_vocabulary_version"],
            "credential_fingerprint": key_fingerprint(env, cfg)}


def preflight_state(db, cfg: dict, env: dict) -> dict:
    """Derived from the LATEST stored receipt and the current binding; never from a bare flag. A receipt written by
    the earlier, lenient preflight has no validator in it and is STALE. A newer failed receipt replaces an older pass."""
    out = {"state": "NOT_RUN", "recent_unlocked": False, "history_unlocked": False, "reasons": [], "receipt_sha256": None, "at_utc": None}
    raw = get_meta(db, RECEIPT_KEY)
    if not raw:
        if get_meta(db, "preflight_passed") or get_meta(db, "preflight_last"):
            out.update(state="STALE", reasons=["RECEIPT_FROM_LENIENT_VALIDATOR"])
        return out
    rec = json.loads(raw)
    out.update(receipt_sha256=rec.get("sha256"), at_utc=rec.get("finished_at_utc"), verdict=rec.get("verdict"))
    now_b, then_b = binding(db, cfg, env), rec.get("binding") or {}
    diff = [k for k in now_b if now_b[k] != then_b.get(k)]
    if rec.get("verdict") != "PASS":
        out.update(state="FAILED", reasons=list(rec.get("fail_reasons") or ["NOT_PASSED"]))
    elif diff:
        out.update(state="STALE", reasons=[f"BINDING_CHANGED:{k}" for k in diff])
    else:
        hist = bool((rec.get("checks") or {}).get("history_search_verified"))
        out.update(state="PASSED", recent_unlocked=True, history_unlocked=hist, reasons=[] if hist else ["HISTORY_SEARCH_NOT_VERIFIED"])
    return out


def preflight_passed(db, cfg: dict | None = None, env: dict | None = None) -> bool:
    if cfg is None:
        return False                                             # a bare marker proves nothing
    return preflight_state(db, cfg, env or {})["recent_unlocked"]


def _store_receipt(db, receipt: dict) -> dict:
    db.execute("CREATE TABLE IF NOT EXISTS x_preflight_receipts(sha256 TEXT PRIMARY KEY, finished_at_utc TEXT NOT NULL, verdict TEXT NOT NULL, receipt_json TEXT NOT NULL)")
    receipt["sha256"] = store.sha(json.dumps({k: v for k, v in receipt.items() if k != "sha256"}, sort_keys=True, ensure_ascii=False))
    body = json.dumps(receipt, sort_keys=True, ensure_ascii=False)
    db.execute("INSERT OR REPLACE INTO x_preflight_receipts VALUES(?,?,?,?)", (receipt["sha256"], receipt["finished_at_utc"], receipt["verdict"], body))
    set_meta(db, RECEIPT_KEY, body)                              # latest wins: a failure after a pass closes the gate again
    db.commit()
    return receipt


def run_preflight(db, cfg: dict, transport, anchor: datetime, *, env: dict | None = None, clock=None, ledger: Ledger | None = None) -> dict:
    """At most 200 returned posts / USD 0.03 over the whole life of the store, charged to the normal pools: one page
    for every topic/language query in the newest slice of the last completed New York date, one page for the QQQ
    English query on an older date, and - only if a first page really says more exists - one continuation page.
    The windows are the ones the scheduled jobs use, so nothing read here is bought again.

    PASS needs: every planned request answered in the documented shape; at least one post inside its requested
    window; no post that could not be parsed. All pages empty is NOT a pass (nothing was verified). The historical
    job is unlocked only if the older window also returned a post inside its window."""
    clock, env = clock or utc_now, env or {}
    ledger = ledger or Ledger(db, cfg, clock=clock)
    provider.migrate_checkpoints(db)
    d = last_completed_ny_date(anchor)
    recent, hist = plan_recent(db, cfg, d, anchor), plan_history(db, cfg, anchor)
    last_slice = recent["slices"] - 1
    receipt = {"started_at_utc": store.iso(clock()), "anchor_utc": store.iso(anchor), "provider": "twitterapi_io", "endpoint": provider.ENDPOINT, "query_type": "Latest",
               "binding": binding(db, cfg, env), "requests": [], "stopped": None, "continuation": {"state": "NOT_ATTEMPTED"}}
    plans = [("recent", provider.BATCH, u) for u in recent["units"] if u["slice"] == last_slice]
    back = date.fromisoformat(hist["range"]["anchor_ny_date"]) - timedelta(days=cfg["preflight"]["proposed_older_window_days_back"])
    q = next((x for x in hist["queries"] if x["topic_id"] == "qqq" and x["lang"] == "en"), hist["queries"][0])
    s, e = day_slices(back, hist["slices"])[hist["slices"] - 1]
    plans.append(("historical", provider.RETRO, {**q, "start": s, "end": e, "ny_date": back.isoformat()}))

    def one(pool, mode, u, pages, kind):
        col = provider.Collector(db, cfg, budget=JobBudget(ledger, pool=pool, is_preflight=True, job_type="preflight"), transport=transport, mode=mode, clock=clock)
        try:
            r = col.collect_pages(_query(db, u["query_hash"]), u["start"], u["end"], pages)
            stop = None
        except provider.StopAll as ex:
            r, stop = col.last or {}, ex.reason
        row = {"kind": kind, "topic_id": u["topic_id"], "lang": u["lang"], "pool": pool, "pages_requested": pages,
               "window_start_utc": store.iso(datetime.fromtimestamp(u["start"], timezone.utc)), "window_end_utc": store.iso(datetime.fromtimestamp(u["end"], timezone.utc)),
               **{k: r.get(k) for k in ("run_id", "status", "stop_reason", "pages", "returned", "in_window", "out_of_window", "duplicates", "new_posts", "parse_failures",
                                        "network_requests", "cache_hits", "cost_estimate_usd")}}
        row["schema_valid"] = row["status"] in ("COMPLETE", "PLANNED_SAMPLE") and not str(row["stop_reason"] or "").startswith("PROTOCOL")
        row["empty"] = row["schema_valid"] and not row["returned"]
        receipt["requests"].append(row)
        return row, stop

    for pool, mode, u in plans:
        row, stop = one(pool, mode, u, 1, "first_page")
        if stop:
            receipt["stopped"] = stop
            break
        if sum(1 for r in receipt["requests"] if not r["schema_valid"]) >= 3:      # the same malformed answer three times: stop paying for more
            receipt["stopped"] = "PROTOCOL_ANOMALY_REPEATED"
            break
    rows = receipt["requests"]
    first = [r for r in rows if r["kind"] == "first_page"]
    if receipt["stopped"] is None and len(first) == len(plans) and all(r["schema_valid"] for r in first):
        more = next((i for i, r in enumerate(first) if r["pool"] == "recent" and r["status"] == "PLANNED_SAMPLE" and r["stop_reason"] == "PLANNED_PAGE_LIMIT"), None)
        if more is None:
            receipt["continuation"] = {"state": "NOT_OBSERVED", "reason": "no first page reported a next page"}
        else:
            pool, mode, u = plans[more]
            row, stop = one(pool, mode, u, 2, "continuation")
            ok = stop is None and row["schema_valid"] and (row["pages"] or 0) >= 2
            receipt["continuation"] = {"state": "VERIFIED" if ok else "FAILED" if (row["network_requests"] or row["cache_hits"]) else "NOT_OBSERVED",
                                       "topic_id": u["topic_id"], "lang": u["lang"], "stop": stop or row["stop_reason"]}
            if stop and stop.startswith("LIMIT_REACHED"):
                receipt["continuation"] = {"state": "NOT_OBSERVED", "reason": stop}
    units = {r["run_id"] or id(r): r for r in rows}.values()           # a unit read twice (first page, then continuation) counts once, at its final totals
    returned = sum(r["returned"] or 0 for r in units)
    in_recent = sum(r["in_window"] or 0 for r in units if r["pool"] == "recent")
    in_hist = sum(r["in_window"] or 0 for r in units if r["pool"] == "historical")
    fails = sum(r["parse_failures"] or 0 for r in rows)
    checks = {"all_planned_requests_answered": receipt["stopped"] is None and len(first) == len(plans),
              "schema_valid_on_every_page": bool(first) and all(r["schema_valid"] for r in rows),
              "every_returned_post_parsed": fails == 0,
              "recent_content_verified": in_recent > 0, "history_search_verified": in_hist > 0,
              "continuation_verified": receipt["continuation"]["state"] == "VERIFIED"}
    reasons = []
    if not checks["all_planned_requests_answered"]:
        reasons.append(f"STOPPED:{receipt['stopped']}" if receipt["stopped"] else "NOT_ALL_REQUESTS_ANSWERED")
    if not checks["schema_valid_on_every_page"]:
        reasons += sorted({str(r["stop_reason"]) for r in rows if not r["schema_valid"] and r["stop_reason"]}) or ["SCHEMA_INVALID"]
    if fails:
        reasons.append("UNPARSEABLE_POSTS")
    if returned and not (in_recent + in_hist):
        reasons.append("ALL_RETURNED_POSTS_OUTSIDE_WINDOW_OR_UNPARSEABLE")
    elif not returned and not reasons:
        reasons.append("ALL_PAGES_EMPTY_NOTHING_VERIFIED")
    elif not in_recent and not reasons:
        reasons.append("NO_RECENT_POST_INSIDE_WINDOW")
    if receipt["continuation"]["state"] == "FAILED":
        reasons.append("CONTINUATION_FAILED")
    ok = not reasons
    receipt.update({"finished_at_utc": store.iso(clock()), "returned_total": returned, "in_window_total": in_recent + in_hist, "in_window_recent": in_recent,
                    "in_window_historical": in_hist, "unparseable_posts": fails, "network_requests_total": sum(r["network_requests"] or 0 for r in rows),
                    "cache_hits_total": sum(r["cache_hits"] or 0 for r in rows), "checks": checks, "fail_reasons": reasons,
                    "usage_after": ledger.usage(clock()), "verdict": "PASS" if ok else "FAIL",
                    "unlocks": {"recent_job": ok, "historical_job": ok and checks["history_search_verified"]},
                    "not_checked": "coverage of X, label quality, provider billing statement"})
    return _store_receipt(db, receipt)


def run_daily(db, cfg: dict, env: dict, transport_factory, anchor: datetime, *, clock=None) -> dict:
    """The scheduled entry point. Refuses unless the key is present and a current preflight receipt opens the gate.
    The historical queue runs only if that receipt also verified a historical window."""
    clock = clock or utc_now
    ledger = Ledger(db, cfg, clock=clock)
    state = preflight_state(db, cfg, env)
    gate = cfgmod.acquisition_gate(cfg, env, preflight_passed=state["recent_unlocked"])
    gate["preflight"] = {k: state[k] for k in ("state", "reasons", "history_unlocked")}
    if not gate["allowed"]:
        return {"status": "REFUSED_FAIL_CLOSED", "requests_sent": 0, "gate": gate}
    provider.migrate_checkpoints(db)
    held = ledger.recover_open()
    transport = transport_factory()
    out = {"status": "RAN", "anchor_utc": store.iso(anchor), "started_at_utc": store.iso(clock()), "recovered_open_reservations": held,
           "recent": run_recent(db, cfg, transport, anchor, clock=clock, ledger=ledger)}
    out["historical"] = run_history(db, cfg, transport, anchor, clock=clock, ledger=ledger) if state["history_unlocked"] else {"job": "historical", "status": "SKIPPED_HISTORY_SEARCH_NOT_VERIFIED", "requests": 0}
    out["finished_at_utc"] = store.iso(clock())
    out["usage"] = ledger.usage(clock())
    return out
