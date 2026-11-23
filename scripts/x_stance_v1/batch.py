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
from decimal import Decimal
from datetime import date, datetime, timedelta, timezone

from . import audit, config as cfgmod, provider, store, vocab, watchlist
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
    sub_pools = cfg["allocation"]["recent"].get("sub_pools") or {}
    kw_posts = (sub_pools.get("recent") or {}).get("max_returned_posts_per_day") or cfg["allocation"]["recent"]["max_returned_posts_per_day"]
    depth = max(1, kw_posts // page // max(1, len(units)))
    plan = {"job": "recent", "ny_date": d.isoformat(), "vocab_version": cfg["topic_vocabulary_version"], "slices": n,
            "pages_per_unit": depth, "order": "depth, slice, query", "units": units,
            "sampling_mode": "PLANNED_SAMPLE", "max_posts_if_every_page_is_full": depth * len(units) * page}
    w = watch_plan(db, cfg, d)
    if w:                                                         # frozen with the same plan: who is asked, in what order, how deep
        plan["watch"] = w
        plan["keyword_posts_budget"] = kw_posts
    return _freeze(db, "recent", d.isoformat(), plan, now)


def watch_plan(db, cfg: dict, d: date) -> dict | None:
    """Author queries of one New York date: one whole-day window per author that may be asked, in scheduling order
    (priority, then watch id). The walk is breadth first - every author gets a first page before anyone gets a
    second - and the watched-author share of the recent pool stops it. Not a promise that everything an author
    wrote that day is read."""
    wl = (cfg.get("watchlist") or {})
    if not wl.get("enabled", True) or not watchlist.exists(db):
        return None
    queries = watchlist.register_queries(db, cfg)
    if not queries:
        return None
    a, b = (int(x.timestamp()) for x in calendar_window(d))
    page = cfg["quote"]["documented_max_posts_per_page"]
    share = ((cfg["allocation"]["recent"].get("sub_pools") or {}).get("watchlist") or {}).get("max_returned_posts_per_day")
    blocked = [x["watch_id"] for x in watchlist.authors(db) if x["enabled"] and not watchlist.ready(x)]
    return {"job_type": "watchlist", "watchlist_version_tag": watchlist.version_tag(db), "window": [a, b], "max_pages_per_author": int(wl.get("max_pages_per_author", 3)),
            "order": "depth, priority, watch id", "posts_budget": share, "first_round_max_posts": len(queries) * page,
            "units": [{"query_hash": q["query_hash"], "topic_id": q["topic_id"], "lang": "any", "watch_id": q["watch_id"], "handle": q["handle"],
                       "scheduling_priority": q["scheduling_priority"], "start": a, "end": b, "ny_date": d.isoformat()} for q in queries],
            "not_asked": blocked, "sampling_mode": "PLANNED_SAMPLE", "keyword_independent": True}


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


def stored_page_ok(db, run) -> bool:
    page = db.execute("SELECT response_json FROM x_page_cache WHERE provider=? AND query_hash=? AND window_start_utc=? AND window_end_utc=? AND cursor=?",
                      (run["provider"], run["query_hash"], run["window_start_utc"], run["window_end_utc"], run["cursor"] or "")).fetchone()
    return bool(page) and provider.page_schema_error(json.loads(page[0])) is None


def retriable(db, run, now: datetime | None = None) -> bool:
    """A PARTIAL unit a LATER run may resume from its checkpoint: it ended on an uncharged HTTP 503 and its
    deferral time has passed, or on a page the validator of the time rejected and the current one accepts (the
    stored page is read again, at no cost). Any other failure stays as it is until someone looks."""
    if run["status"] != "PARTIAL":
        return False
    reason = run["stop_reason"] or ""
    if reason == "ERROR:HTTP_503_NOT_CHARGED":
        rs = provider.retry_state(db, run["run_id"])
        due = rs["next_retry_at_utc"] if rs else None
        return not (due and now is not None and store.parse(due) > now)
    if reason.startswith("PROTOCOL:"):
        page = db.execute("SELECT response_json FROM x_page_cache WHERE provider=? AND query_hash=? AND window_start_utc=? AND window_end_utc=? AND cursor=?",
                          (run["provider"], run["query_hash"], run["window_start_utc"], run["window_end_utc"], run["cursor"] or "")).fetchone()
        return bool(page) and provider.page_schema_error(json.loads(page[0])) is None
    return False


def _walk(col: provider.Collector, db, steps) -> dict:
    """Runs (depth, unit) steps in order until the plan ends or the job must stop. A unit that already ended
    (COMPLETE) or failed (PARTIAL) is not asked for more pages - except a failed unit that `retriable` allows,
    once per run: a unit that fails in this run is not asked again in this run."""
    out = {"steps": 0, "requests_before": col.budget.requests, "stopped": None, "units_touched": 0, "protocol_anomalies": 0, "resumed_units": 0}
    done = {}
    try:
        for depth, u in steps:
            rid = col._run_id(u["query_hash"], u["start"], u["end"])
            if rid not in done:
                r = db.execute("SELECT * FROM x_query_runs WHERE run_id=?", (rid,)).fetchone()
                if r and retriable(db, r, col.clock()):
                    done[rid] = ("RESUMABLE", r["pages"]); out["resumed_units"] += 1
                else:
                    done[rid] = (r["status"], r["pages"]) if r else (None, 0)
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
    out["transient_errors"] = col.transient_errors
    out["deferred_units"] = col.deferred_units
    out["requests"] = col.budget.requests - out.pop("requests_before")
    return out


def run_recent(db, cfg: dict, transport, anchor: datetime, *, clock=None, ledger: Ledger | None = None) -> dict:
    clock = clock or utc_now
    ledger = ledger or Ledger(db, cfg, clock=clock)
    d = last_completed_ny_date(anchor)
    plan = plan_recent(db, cfg, d, anchor)
    history_range(db, cfg, anchor)                             # freezes the historical range the first time anything runs
    out = {"job": "recent", "ny_date": d.isoformat(), "vocab_version": plan["vocab_version"], "plan_sha256": plan["sha256"], "plan_reused": plan["reused"]}
    w = plan.get("watch")
    if w:                                                         # the watched authors first: a small, bounded share of the same pool
        wcol = provider.Collector(db, cfg, budget=JobBudget(ledger, pool="recent", job_type="watchlist"), transport=transport, mode=provider.BATCH, clock=clock)
        wsteps = ((depth, u) for depth in range(1, w["max_pages_per_author"] + 1) for u in w["units"])
        res = _walk(wcol, db, wsteps)
        units = [{**u, "run_id": wcol._run_id(u["query_hash"], u["start"], u["end"])} for u in w["units"]]
        out["watch"] = {**res, "authors_asked": len(units), "not_asked": w["not_asked"], "identity": watchlist.resolve(db, units, clock())}
    col = provider.Collector(db, cfg, budget=JobBudget(ledger, pool="recent"), transport=transport, mode=provider.BATCH, clock=clock)
    steps = ((depth, u) for depth in range(1, plan["pages_per_unit"] + 1) for u in plan["units"])
    return {**out, **_walk(col, db, steps)}


def verify_candidates(db, cfg: dict, transport, watch_id: str, anchor: datetime, *, clock=None, ledger: Ledger | None = None, days: int = 7) -> dict:
    """For an author whose handle is ambiguous: ONE page per recorded spelling over the last `days` completed New
    York dates, charged to the watched-author share like any other author query. What each spelling returned
    (author id, user name, display name, profile text) is stored as evidence. Nothing is decided here: the handle
    stays blocked until the owner confirms one spelling. A similar display name alone settles nothing."""
    clock = clock or utc_now
    ledger = ledger or Ledger(db, cfg, clock=clock)
    a = watchlist.get(db, watch_id)
    if a["identity_status"] not in watchlist.BLOCKED:
        raise watchlist.WatchError("NOT_AMBIGUOUS")
    last = last_completed_ny_date(anchor)
    s, e = int(calendar_window(last - timedelta(days=days - 1))[0].timestamp()), int(calendar_window(last)[1].timestamp())
    spellings = [c["handle"] for c in a["handle_candidates"]] + ([a["provenance"]["handle_text_in_image"]] if (a["provenance"] or {}).get("handle_text_in_image") else [])
    col = provider.Collector(db, cfg, budget=JobBudget(ledger, pool="recent", job_type="watchlist"), transport=transport, mode=provider.BATCH, clock=clock)
    evidence, requests = [], 0
    for h in dict.fromkeys(spellings):
        qh = store.sha(f"{cfg['provider_candidate']}|watchcheck|{watch_id}|from:{h}")
        db.execute("INSERT OR IGNORE INTO x_queries VALUES(?,?,?,?,?,?)", (qh, cfg["provider_candidate"], "watchcheck:" + watch_id, "any", "watchcheck", f"from:{h}"))
        item = {"spelling": h, "status": None, "returned": 0, "authors": []}
        try:
            res = col.collect_pages({"query_hash": qh, "topic_id": "watchcheck:" + watch_id, "lang": "any", "query": f"from:{h}"}, s, e, 1)
            item["status"], item["returned"] = res["status"], res["returned"]
        except provider.StopAll as stop:
            item["status"] = "STOPPED:" + stop.reason
            evidence.append(item); break
        ws, we = store.iso(datetime.fromtimestamp(s, timezone.utc)), store.iso(datetime.fromtimestamp(e, timezone.utc))
        page = db.execute("SELECT response_json FROM x_page_cache WHERE query_hash=? AND window_start_utc=? AND window_end_utc=? AND cursor=''", (qh, ws, we)).fetchone()
        seen = {}
        for t in (json.loads(page[0]).get("tweets") or []) if page else []:
            au = t.get("author") or {}
            if au.get("id") and str(au["id"]) not in seen:
                seen[str(au["id"])] = {"author_id": str(au["id"]), "user_name": au.get("userName"), "display_name": au.get("name"), "profile_text": (au.get("description") or "")[:300],
                                       "user_name_equals_spelling": watchlist.norm(au.get("userName")) == watchlist.norm(h)}
        item["authors"] = list(seen.values())
        evidence.append(item)
    requests = col.budget.requests
    db.execute("UPDATE x_watch_authors SET candidate_evidence_json=?, updated_at_utc=? WHERE watch_id=?",
               (json.dumps({"checked_at_utc": store.iso(clock()), "window_days": days, "spellings": evidence}, ensure_ascii=False), store.iso(clock()), watch_id))
    db.commit()
    return {"status": "EVIDENCE_STORED_HANDLE_STILL_BLOCKED", "watch_id": watch_id, "requests": requests, "spellings": evidence, "identity_status": watchlist.get(db, watch_id)["identity_status"],
            "next": "the owner confirms one spelling with: watch confirm-handle ID HANDLE", "usage": ledger.usage(clock())}


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


def preflight_state(db, cfg: dict, env: dict, *, check_credential: bool = True) -> dict:
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
    if not check_credential:                                     # a reader without the key (the web page) cannot compare fingerprints: it
        diff = [k for k in diff if k != "credential_fingerprint"]   # reports the receipt as it stands and opens nothing
        out["credential_checked"] = False
    if rec.get("verdict") != "PASS":
        out.update(state="FAILED", reasons=list(rec.get("fail_reasons") or ["NOT_PASSED"]))
    elif diff:
        out.update(state="STALE", reasons=[f"BINDING_CHANGED:{k}" for k in diff])
    else:
        hist = bool((rec.get("checks") or {}).get("history_search_verified"))
        out.update(state="PASSED", recent_unlocked=True, history_unlocked=hist, reasons=[] if hist else ["HISTORY_SEARCH_NOT_VERIFIED"])
    if not check_credential:
        out["recent_unlocked"] = False                           # only a process that holds the key can open the gate
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
    out = {"status": "RAN", "anchor_utc": store.iso(anchor), "started_at_utc": store.iso(clock()), "recovered_open_reservations": held}
    # Earlier sample dates with open units come first, oldest first, inside the same recent pool and its shares.
    # What does not fit stays on the to-do list for the next budget day.
    out["recovery"] = [run_recover(db, cfg, transport, p["sample_date_ny"], p["plan_id"], anchor, clock=clock, ledger=ledger)
                       for p in recovery_pending(db, cfg, last_completed_ny_date(anchor), clock())]
    out["recent"] = run_recent(db, cfg, transport, anchor, clock=clock, ledger=ledger)
    out["recovery_todo"] = save_recovery_todo(db, cfg, last_completed_ny_date(anchor), clock())
    out["historical"] = run_history(db, cfg, transport, anchor, clock=clock, ledger=ledger) if state["history_unlocked"] else {"job": "historical", "status": "SKIPPED_HISTORY_SEARCH_NOT_VERIFIED", "requests": 0}
    out["finished_at_utc"] = store.iso(clock())
    out["usage"] = ledger.usage(clock())
    return out


# ---------- recovery of one frozen sample date ----------
RECOVERY_SCHEMA = """
CREATE TABLE IF NOT EXISTS x_recovery_runs(recovery_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, sample_date_ny TEXT NOT NULL, started_at_utc TEXT NOT NULL,
  finished_at_utc TEXT, result_json TEXT);
CREATE TABLE IF NOT EXISTS x_recovery_attempts(attempt_id TEXT PRIMARY KEY, parent_plan_id TEXT NOT NULL, sample_date_ny TEXT NOT NULL, parent_run_id TEXT NOT NULL,
  parent_query_hash TEXT NOT NULL, new_query_hash TEXT NOT NULL, new_run_id TEXT NOT NULL, reason TEXT NOT NULL, created_at_utc TEXT NOT NULL,
  status TEXT, returned INTEGER, new_posts INTEGER, duplicate_returned INTEGER, duplicate_cost_credits INTEGER);
"""


def _plan_row(db, sample_date: str, plan_id: str | None):
    """(row, error). The plan is found by its sample date; a plan id, if given, must be that plan's."""
    row = db.execute("SELECT * FROM x_sample_plans WHERE job='recent' AND plan_key=?", (sample_date,)).fetchone()
    if not row:
        return None, "PLAN_NOT_FOUND"
    if plan_id and row["plan_id"] != plan_id and not row["plan_id"].startswith(plan_id):
        return None, "PLAN_ID_MISMATCH"
    return row, None


def _rebuilt(db, parent_run_id: str):
    try:
        return db.execute("SELECT * FROM x_recovery_attempts WHERE parent_run_id=? ORDER BY created_at_utc DESC LIMIT 1", (parent_run_id,)).fetchone()
    except Exception:  # noqa: BLE001 - the table appears with the first rebuild
        return None


def recovery_units(db, row, now: datetime) -> list[dict]:
    """Every unit of the frozen plan that has not reached its planned pages, with what a recovery would do.
    The plan is not re-cut: query, vocabulary, list version, window and page depth are the frozen ones."""
    out = []
    for u in audit.plan_units(db, row):
        if u["state"] == "DONE":
            continue
        run = db.execute("SELECT * FROM x_query_runs WHERE run_id=?", (u["run_id"],)).fetchone()
        rs = provider.retry_state(db, u["run_id"])
        att = _rebuilt(db, u["run_id"])
        if not u["query_registered"]:
            action = "BLOCKED:QUERY_NOT_IN_STORE"                # the frozen query text is gone: nothing is guessed
        elif att is not None and att["status"] in ("COMPLETE", "PLANNED_SAMPLE"):
            action = "DONE_BY_REBUILD"
        elif u["state"] == "PARTIAL" and u["reason"].startswith("PROTOCOL:"):
            action = "REREAD_STORED_PAGE" if stored_page_ok(db, run) else "BLOCKED:STORED_PAGE_STILL_REJECTED"
        elif u["state"] == "PARTIAL" and u["reason"] == "ERROR:HTTP_503_NOT_CHARGED":
            due = rs["next_retry_at_utc"] if rs else None
            action = f"WAIT_UNTIL:{due}" if due and store.parse(due) > now else ("RESUME_FROM_CURSOR" if u["has_cursor"] else "FIRST_PAGE")
        elif u["state"] == "PARTIAL":                            # the provider refused the request (e.g. the stored cursor)
            action = "NEEDS_REBUILD:" + u["reason"] if u["has_cursor"] else "BLOCKED:" + u["reason"]
        else:
            action = "RESUME_FROM_CURSOR" if u["has_cursor"] else "FIRST_PAGE"
        out.append({**u, "action": action, "attempts_so_far": rs["attempts_total"] if rs else 0, "next_retry_at_utc": rs["next_retry_at_utc"] if rs else None,
                    "pages_to_read": 0 if action.startswith(("BLOCKED", "DONE", "REREAD")) else u["planned_pages"] - u["pages_read"]})
    return [u for u in out if u["action"] != "DONE_BY_REBUILD"]


def recovery_preview(db, cfg: dict, sample_date: str, plan_id: str | None, now: datetime, ledger: Ledger | None = None) -> dict:
    row, err = _plan_row(db, sample_date, plan_id)
    if err:
        return {"status": err, "sample_date_ny": sample_date, "plan_id": plan_id, "requests_sent": 0,
                "plans_in_store": [{"plan_id": r[0], "sample_date_ny": r[1]} for r in db.execute("SELECT plan_id, plan_key FROM x_sample_plans WHERE job='recent' ORDER BY plan_key")]}
    plan, units = json.loads(row["plan_json"]), recovery_units(db, row, now)
    page = cfg["quote"]["documented_max_posts_per_page"]
    go = [u for u in units if u["pages_to_read"] > 0 and not u["action"].startswith(("WAIT", "NEEDS_REBUILD"))]
    pages = {k: sum(u["pages_to_read"] for u in go if u["kind"] == k) for k in ("author", "keyword")}
    out = {"status": "PREVIEW", "sample_date_ny": sample_date, "plan_id": row["plan_id"], "plan_sha256": row["sha256"], "frozen_at_utc": row["frozen_at_utc"],
           "vocab_version": plan.get("vocab_version"), "watchlist_version_tag": (plan.get("watch") or {}).get("watchlist_version_tag"),
           "open_units": len(units), "by_action": {}, "free_rereads_of_stored_pages": sum(1 for u in units if u["action"] == "REREAD_STORED_PAGE"),
           "requests_upper_bound": pages, "posts_upper_bound": {k: v * page for k, v in pages.items()},
           "cost_upper_bound_usd": str((Decimal(sum(pages.values()) * page * cfg["quote"]["credits_per_post"]) / Decimal(cfg["quote"]["credits_per_usd"])).normalize()),
           "charged_to": "the recent pool and its author / keyword shares of the budget day in which the requests are actually sent",
           "ingest_mode_of_new_posts": provider.RETRO, "requests_sent": 0, "units": units,
           "note": "finishing these units finishes the frozen sampling plan of that date; it does not mean everything posted on X that day was read"}
    for u in units:
        k = u["kind"] + ":" + u["action"].split(":")[0]
        out["by_action"][k] = out["by_action"].get(k, 0) + 1
    if ledger is not None:
        us = ledger.usage(now)
        out["budget_now"] = {"budget_day": us["budget_day"], "recent_pool_left_posts": us["pools"]["recent"]["cap_posts"] - us["pools"]["recent"]["returned_posts"],
                             "shares_left_posts": {k: v["cap_posts"] - v["returned_posts"] for k, v in us["sub_pools"].items()},
                             "day_left_posts": us["today"]["cap_posts"] - us["today"]["returned_posts"], "paused": us["paused"]}
    return out


def _rebuild_unit(db, cfg, col: provider.Collector, row, u: dict, now: datetime) -> dict:
    """A unit whose stored cursor the provider no longer accepts: asked again from its first page under a NEW query
    record that points at the parent plan and run. Posts already stored are paid for a second time; that is
    counted and kept."""
    db.executescript(RECOVERY_SCHEMA)
    q = _query(db, u["query_hash"])
    n = db.execute("SELECT COUNT(*) FROM x_recovery_attempts WHERE parent_run_id=?", (u["run_id"],)).fetchone()[0] + 1
    qh = store.sha(f"{u['query_hash']}|recovery|{row['plan_id']}|{n}")
    vv = db.execute("SELECT provider, topic_id, lang, vocab_version, query_text FROM x_queries WHERE query_hash=?", (u["query_hash"],)).fetchone()
    db.execute("INSERT OR IGNORE INTO x_queries VALUES(?,?,?,?,?,?)", (qh, vv[0], vv[1], vv[2], vv[3], vv[4]))
    s, e = int(store.parse(u["window_start_utc"]).timestamp()), int(store.parse(u["window_end_utc"]).timestamp())
    rid = col._run_id(qh, s, e)
    aid = store.sha(f"{u['run_id']}|{n}")[:24]
    db.execute("INSERT INTO x_recovery_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (aid, row["plan_id"], row["plan_key"], u["run_id"], u["query_hash"], qh, rid, u["reason"], store.iso(now), None, None, None, None, None))
    db.commit()
    res = col.collect_pages({**q, "query_hash": qh}, s, e, u["planned_pages"])
    dup = res["returned"] - res["new_posts"]
    db.execute("UPDATE x_recovery_attempts SET status=?, returned=?, new_posts=?, duplicate_returned=?, duplicate_cost_credits=? WHERE attempt_id=?",
               (res["status"], res["returned"], res["new_posts"], dup, dup * cfg["quote"]["credits_per_post"], aid))
    db.commit()
    return {"attempt_id": aid, "parent_run_id": u["run_id"], "new_run_id": rid, "status": res["status"], "returned": res["returned"], "new_posts": res["new_posts"], "duplicate_returned": dup}


def run_recover(db, cfg: dict, transport, sample_date: str, plan_id: str | None, anchor: datetime, *, clock=None, ledger: Ledger | None = None, rebuild: bool = False) -> dict:
    """Finishes the frozen plan of one earlier sample date as far as today's budget allows: stored pages that an
    older validator rejected are read again first (no request), then units resume from their stored cursor and
    page count. Requests are charged at the time they are sent, to the recent pool and its shares - never to the
    historical pool and never to the sample date. New posts are RETROSPECTIVE_VISIBLE_NOW: obtained later, with the
    real receive time; nothing already stored (receive time, labels, first signal time) is touched."""
    clock = clock or utc_now
    ledger = ledger or Ledger(db, cfg, clock=clock)
    row, err = _plan_row(db, sample_date, plan_id)
    if err:
        return recovery_preview(db, cfg, sample_date, plan_id, clock(), ledger)
    db.executescript(RECOVERY_SCHEMA)
    before = recovery_preview(db, cfg, sample_date, plan_id, clock(), ledger)
    rid = store.sha(f"{row['plan_id']}|{store.iso(clock())}|recover")[:24]
    db.execute("INSERT OR REPLACE INTO x_recovery_runs VALUES(?,?,?,?,?,?)", (rid, row["plan_id"], sample_date, store.iso(clock()), None, None)); db.commit()
    units = before["units"]
    out = {"status": "RAN", "recovery_id": rid, "sample_date_ny": sample_date, "plan_id": row["plan_id"], "open_units_before": len(units), "by_action_before": before["by_action"], "parts": {}, "rebuilt": []}
    free = [u for u in units if u["action"] == "REREAD_STORED_PAGE"]
    for kind, job in (("author", "watchlist"), ("keyword", None)):
        mine = [u for u in units if u["kind"] == kind and not u["action"].startswith(("BLOCKED", "WAIT", "NEEDS_REBUILD"))]
        redo = [x for x in units if x["kind"] == kind and x["action"].startswith("NEEDS_REBUILD")] if rebuild else []
        if not mine and not redo:
            continue
        col = provider.Collector(db, cfg, budget=JobBudget(ledger, pool="recent", job_type=job), transport=transport, mode=provider.RETRO, clock=clock)
        as_step = lambda u: {"query_hash": u["query_hash"], "start": int(store.parse(u["window_start_utc"]).timestamp()), "end": int(store.parse(u["window_end_utc"]).timestamp())}
        depth = max([u["planned_pages"] for u in mine] or [0])
        first = [(u["planned_pages"], as_step(u)) for u in mine if u in free]                     # stored pages first: they cost nothing
        steps = first + [(d, as_step(u)) for d in range(1, depth + 1) for u in mine if d <= u["planned_pages"]]
        out["parts"][kind] = _walk(col, db, iter(steps))
        if rebuild and not out["parts"][kind]["stopped"]:
            for u in redo:
                try:
                    out["rebuilt"].append(_rebuild_unit(db, cfg, col, row, u, clock()))
                except provider.StopAll as stop:
                    out["parts"][kind]["stopped"] = stop.reason
                    break
    plan = json.loads(row["plan_json"])
    wu = [{**u, "run_id": provider.Collector(db, cfg, budget=None)._run_id(u["query_hash"], u["start"], u["end"])} for u in (plan.get("watch") or {}).get("units", [])]
    unbound = [u for u in wu if not (watchlist.get(db, u["watch_id"]) or {}).get("stable_author_id")]
    if unbound and "author" in out["parts"]:                     # only authors with no id yet: a later date's state is not overwritten by an older one
        out["identity"] = {k: len(v) for k, v in watchlist.resolve(db, unbound, clock()).items()}
    after = recovery_preview(db, cfg, sample_date, plan_id, clock(), ledger)
    out.update(open_units_after=after["open_units"], by_action_after=after["by_action"], finished_at_utc=store.iso(clock()),
               plan_complete=after["open_units"] == 0, usage=ledger.usage(clock()),
               note="complete means the frozen sampling plan of that date is finished; it does not mean X was read in full")
    db.execute("UPDATE x_recovery_runs SET finished_at_utc=?, result_json=? WHERE recovery_id=?", (out["finished_at_utc"], json.dumps({k: v for k, v in out.items() if k != "usage"}, ensure_ascii=False), rid))
    db.commit()
    return out


def recovery_pending(db, cfg: dict, current: date, now: datetime) -> list[dict]:
    """Earlier sample dates whose frozen plan still has units a recovery can work on, oldest first."""
    out = []
    for row in db.execute("SELECT * FROM x_sample_plans WHERE job='recent' AND plan_key<? ORDER BY plan_key", (current.isoformat(),)).fetchall():
        units = recovery_units(db, row, now)
        work = [u for u in units if not u["action"].startswith(("BLOCKED", "NEEDS_REBUILD"))]
        if work:
            out.append({"plan_id": row["plan_id"], "sample_date_ny": row["plan_key"], "open_units": len(units), "workable_units": len(work),
                        "pages_to_read": sum(u["pages_to_read"] for u in work), "needs_a_person": len(units) - len(work)})
    return out


def save_recovery_todo(db, cfg: dict, current: date, now: datetime) -> dict:
    """What is still open after this run, kept in the store so the next budget day (and the page) can see it."""
    todo = {"saved_at_utc": store.iso(now), "before_sample_date_ny": current.isoformat(), "plans": recovery_pending(db, cfg, current, now)}
    set_meta(db, "recovery_todo", json.dumps(todo, ensure_ascii=False)); db.commit()
    return todo


# ---------- what the collection worker tells the web page about itself ----------
HEALTH_KEY = "worker_health"


def write_health(db, cfg: dict, env: dict, now: datetime, *, source: str) -> dict:
    """Written by a process that holds the key. The page has no key and reads this instead. It carries no key and
    no fingerprint: only whether a credential is present and whether it is the one the preflight receipt was made with."""
    state = preflight_state(db, cfg, env)
    gate = cfgmod.acquisition_gate(cfg, env, preflight_passed=state["recent_unlocked"])
    present = bool(key_fingerprint(env, cfg))
    h = {"checked_at_utc": store.iso(now), "source": source, "credential_available": present,
         "credential_matches_preflight_receipt": present and "BINDING_CHANGED:credential_fingerprint" not in state["reasons"] and state["state"] in ("PASSED", "STALE"),
         "preflight": {k: state.get(k) for k in ("state", "reasons", "history_unlocked", "at_utc")}, "gate": {"allowed": gate["allowed"], "reasons": gate["reasons"]},
         "ledger_paused": get_meta(db, "paused") or None}
    set_meta(db, HEALTH_KEY, json.dumps(h, ensure_ascii=False)); db.commit()
    return h


def read_health(db, cfg: dict, now: datetime) -> dict:
    raw = get_meta(db, HEALTH_KEY)
    limit = float((cfg["acquisition"].get("worker_health_max_age_hours")) or 36)
    if not raw:
        return {"state": "NOT_REPORTED", "max_age_hours": limit}
    h = json.loads(raw)
    age = (now - store.parse(h["checked_at_utc"])).total_seconds() / 3600
    return {**h, "age_hours": round(age, 2), "max_age_hours": limit, "state": "FRESH" if age <= limit else "STALE_RECHECK_NEEDED"}
