"""Intraday increments and the update receipt the page reads.

The daily job plans whole New York dates after they have ended (fixed slices of the day). It cannot be run every two
hours without asking for the same slices again. An increment instead asks every query only for the time since the
last window it read successfully:

  window = [end of the last successful window of that query, now)      first run: the last `first_window_hours`
  one page per query per run (planned sample), through the shared ledger: keyword queries in the keyword share of the
  recent pool, watched authors in theirs. A window that was read is never asked again; posts already in the store are
  recognised by id as before. A gap longer than `max_window_hours` is not re-read here: it is left to the daily job.

Provider credit: when the provider answers HTTP 402 (credits used up), no new request is sent by this job until the
owner says the account was topped up (`credit-resume REASON`) - sending more would only repeat the refusal. Posts
already stored keep being classified, imported and built.

Like floor. The sample rules keep the like count of a post's FIRST fetch for good. Asked without a filter, most posts
of the last two hours were stored with a handful of likes and could never enter the sample (measured 2026-11-25: 166
new posts, 2 admitted). So every increment query carries `query_suffix` (the sample's own like floor, `min_faves:20`)
and reads a window that ends `lag_hours` before the run: a post is first fetched only once it already meets the floor,
and is not paid for earlier at a count that would exclude it. The queries with the suffix are registered as queries
of their own; the plain queries of the daily job are not changed.

Posts are stored as LIVE; the window they were read in ends `lag_hours` before they were received.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from . import control_pair, batch, page_pilot, provider, stance, store, vocab, watchlist
from .ledger import JobBudget, Ledger, get_meta, set_meta

STATE_KEY, RECEIPT_KEY, RESUME_KEY = "incremental_state", "update_receipt", "credit_hold_cleared"
DONE = ("COMPLETE", "PLANNED_SAMPLE")
DEFAULTS = {"interval_hours": 2, "first_window_hours": 2, "max_window_hours": 6, "pages_per_query": 1, "include_watchlist": True, "query_suffix": "", "lag_hours": 0}
LOG_SCHEMA = "CREATE TABLE IF NOT EXISTS x_update_log(id INTEGER PRIMARY KEY AUTOINCREMENT, finished_at_utc TEXT NOT NULL, receipt_json TEXT NOT NULL)"


def settings(cfg: dict) -> dict:
    return {**DEFAULTS, **(cfg.get("incremental") or {})}


def credit_hold(db) -> dict | None:
    """Derived from the ledger, not from a flag: the newest request the provider refused with HTTP 402, unless a
    later request was settled or the owner cleared the hold afterwards."""
    try:
        r = db.execute("SELECT created_at_utc FROM x_cost_ledger WHERE note LIKE 'HTTP_402%' ORDER BY created_at_utc DESC LIMIT 1").fetchone()
        if not r:
            return None
        later = db.execute("SELECT 1 FROM x_cost_ledger WHERE status='SETTLED' AND created_at_utc>? LIMIT 1", (r[0],)).fetchone()
    except Exception:  # noqa: BLE001 - a store without the ledger has nothing on hold
        return None
    cleared = json.loads(get_meta(db, RESUME_KEY) or "null")
    if later or (cleared and cleared["at_utc"] > r[0]):
        return None
    return {"reason": "PROVIDER_CREDITS_EXHAUSTED", "http_status": 402, "since_utc": r[0], "lifted_by": "credit-resume REASON, after the owner has topped up the provider account"}


def credit_resume(db, reason: str, now: datetime) -> dict:
    entry = {"at_utc": store.iso(now), "reason": reason.strip()[:300]}
    set_meta(db, RESUME_KEY, json.dumps(entry, ensure_ascii=False)); db.commit()
    return {"status": "HOLD_CLEARED", **entry, "hold_now": credit_hold(db)}


def _state(db) -> dict:
    return json.loads(get_meta(db, STATE_KEY) or "{}")


def _suffixed(db, cfg: dict, q: dict, suffix: str) -> dict:
    """The same query with the suffix, registered under its own hash (same topic id), so its runs, pages and
    position never mix with those of the plain query."""
    if not suffix:
        return q
    text = q["query"] + suffix
    qh = store.sha(f"{cfg['provider_candidate']}|incremental|{q['query_hash']}|{suffix}")
    db.execute("INSERT OR IGNORE INTO x_queries VALUES(?,?,?,?,?,?)", (qh, cfg["provider_candidate"], q["topic_id"], q["lang"], "incremental:" + suffix.strip(), text))
    return {**q, "query_hash": qh, "query": text, "plain_query_hash": q["query_hash"]}


def control_queries(db, cfg: dict, now: datetime | None = None) -> list[dict]:
    """A like-threshold control (owner instruction of 2026-11-26): each pair is one fixed query asked twice per run, once
    without a like operator and once with the sample's floor, over the same window (same lag, same page depth). Both
    are queries of their own (own hash, own position), registered as `control:<name>:<variant>`.

    Their posts enter the store like any other: those at or above the floor that name the target can enter the main
    sample as they would through the other keyword queries; those below stay out of it (the sample rules decide) and
    are only classified for the audit, outside the production queue."""
    out = []
    for p in (settings(cfg).get("control_pairs") or []):
        until = p.get("active_until_utc")
        if until and now is not None and now >= store.parse(until):    # paid: the pair ends on its own at the planned time
            continue
        for variant, text in (("no_threshold", p["query"]), ("floor", p["query"] + p["floor_suffix"])):
            qh = store.sha(f"{cfg['provider_candidate']}|control|{p['name']}|{variant}|{text}")
            db.execute("INSERT OR IGNORE INTO x_queries VALUES(?,?,?,?,?,?)", (qh, cfg["provider_candidate"], p["topic_id"], p["lang"], f"control:{p['name']}:{variant}", text))
            out.append({"query_hash": qh, "topic_id": p["topic_id"], "lang": p["lang"], "query": text, "job_type": "recent", "control": f"{p['name']}:{variant}"})
    return out


def units(db, cfg: dict, now: datetime) -> list[dict]:
    """What this run would ask: one window per query, from where that query last succeeded, ending lag_hours ago."""
    s_, st = settings(cfg), _state(db)
    end = int(now.replace(second=0, microsecond=0).timestamp()) - int(float(s_["lag_hours"]) * 3600)
    c = vocab.with_topics(cfg, vocab.active(db, cfg))
    qs = [{**q, "job_type": "recent"} for q in provider.register_queries(db, c)]
    if s_["include_watchlist"] and (cfg.get("watchlist") or {}).get("enabled", True) and watchlist.exists(db):
        qs += [{**q, "job_type": "watchlist"} for q in watchlist.register_queries(db, cfg)]
    qs = [_suffixed(db, cfg, q, s_["query_suffix"]) for q in qs]
    db.commit()
    out = []
    for q in qs:
        last = (st.get(q["query_hash"]) or {}).get("until")
        start = last if last is not None else end - int(s_["first_window_hours"] * 3600)
        floor = end - int(s_["max_window_hours"] * 3600)
        out.append({"query_hash": q["query_hash"], "topic_id": q["topic_id"], "lang": q["lang"], "query": q["query"], "job_type": q["job_type"],
                    "start": max(start, floor), "end": end, "resumes_from_last_success": last is not None, "gap_left_to_daily_job_seconds": max(0, floor - start), "control": q.get("control")})
    return [u for u in out if u["end"] - u["start"] >= 60]


def preview(db, cfg: dict, now: datetime, ledger: Ledger) -> dict:
    us, page = units(db, cfg, now), cfg["quote"]["documented_max_posts_per_page"]
    s_ = settings(cfg)
    kw, wa = [u for u in us if u["job_type"] == "recent"], [u for u in us if u["job_type"] == "watchlist"]
    per_day = 24 / s_["interval_hours"]
    ctl = [q for q in control_pair.pairs_cfg(cfg) if not q.get("active_until_utc") or now < store.parse(q["active_until_utc"])]
    ctl_req = 2 * len(ctl) * s_["pages_per_query"]                # isolated pairs, paid from the same keyword share
    return {"status": "PREVIEW", "requests_sent": 0, "settings": s_, "credit_hold": credit_hold(db), "queries": {"keyword": len(kw), "watched_authors": len(wa), "control_pair_sides": 2 * len(ctl)},
            "windows": {"earliest_start_utc": store.iso(datetime.fromtimestamp(min(u["start"] for u in us), timezone.utc)) if us else None,
                        "end_utc": store.iso(datetime.fromtimestamp(us[0]["end"], timezone.utc)) if us else None,
                        "resuming_from_last_success": sum(1 for u in us if u["resumes_from_last_success"]), "gaps_left_to_daily_job": sum(1 for u in us if u["gap_left_to_daily_job_seconds"])},
            "this_run_if_every_page_is_full": {"requests": len(us) * s_["pages_per_query"], "keyword_posts": len(kw) * page * s_["pages_per_query"]},
            "per_day_if_every_page_is_full": {"runs": per_day, "keyword_posts": int((len(kw) * s_["pages_per_query"] + ctl_req) * page * per_day), "control_posts": int(ctl_req * page * per_day),
                                              "keyword_share_cap_posts": ((cfg["allocation"]["recent"].get("sub_pools") or {}).get("recent") or {}).get("max_returned_posts_per_day"),
                                              "watched_author_requests": int(len(wa) * s_["pages_per_query"] * per_day)},
            "usage": ledger.usage(now),
            "page_pilot": page_pilot.preview(db, cfg, now, ledger, keyword_queries=len(kw), watched_queries=len(wa)) if page_pilot.pilot_cfg(cfg) else None}


def run(db, cfg: dict, transport, now: datetime, *, clock=None, ledger: Ledger | None = None, audit_db=None, audit_db_pilot=None) -> dict:
    clock = clock or batch.utc_now
    hold = credit_hold(db)
    out = {"job": "incremental", "started_at_utc": store.iso(clock()), "requests": 0, "units_asked": 0, "returned": 0, "new_posts": 0, "duplicates": 0, "stopped": None,
           "credit_hold": hold, "interruptions": [], "advanced": 0}
    if hold:                                                      # nothing is sent while the provider refuses for lack of credit
        out.update(status="PAUSED_PROVIDER_CREDITS", finished_at_utc=store.iso(clock()))
        return out
    ledger = ledger or Ledger(db, cfg, clock=clock)
    s_, st, cols, closed = settings(cfg), _state(db), {}, set()
    us, asked = units(db, cfg, now), []
    for u in us:
        if u["job_type"] in closed:
            continue
        if u["job_type"] not in cols:
            cols[u["job_type"]] = provider.Collector(db, cfg, budget=JobBudget(ledger, pool="recent", job_type=u["job_type"]), transport=transport, mode=provider.LIVE, clock=clock)
        col = cols[u["job_type"]]
        try:
            res = col.collect_pages({k: u[k] for k in ("query_hash", "topic_id", "lang", "query")}, u["start"], u["end"], s_["pages_per_query"])
        except provider.StopAll as stop:
            if stop.reason.startswith(("LIMIT_REACHED:SUBPOOL_", "LIMIT_REACHED:POOL_")):   # this share is used up for the budget day
                closed.add(u["job_type"]); out["interruptions"].append({"job_type": u["job_type"], "reason": stop.reason})
                continue
            out["stopped"] = stop.reason
            break
        out["units_asked"] += 1
        asked.append((u, res))
        for k in ("returned", "new_posts", "duplicates"):
            out[k] += res.get(k) or 0
        if res.get("status") in DONE:                             # only a window that was read moves the query forward
            st[u["query_hash"]] = {"until": u["end"], "at_utc": store.iso(clock()), "status": res["status"]}
            out["advanced"] += 1
        else:
            out["interruptions"].append({"topic_id": u["topic_id"], "reason": res.get("stop_reason")})
    set_meta(db, STATE_KEY, json.dumps(st, sort_keys=True)); db.commit()
    out["requests"] = sum(c.budget.requests for c in cols.values())
    if page_pilot.pilot_cfg(cfg) and not out["stopped"]:        # pages 2-3 of this round's pilot windows: own store, own caps, regular share kept
        ok, why = page_pilot.active(cfg, now)
        if ok:
            pdb = page_pilot.connect_audit(audit_db_pilot or page_pilot.audit_path(db.execute("PRAGMA database_list").fetchone()[2]))
            try:
                out["page_pilot"] = page_pilot.run(db, pdb, cfg, transport, now, ledger=ledger, asked=asked, clock=clock,
                                                   keyword_queries=sum(1 for u in us if u["job_type"] == "recent"), watched_queries=sum(1 for u in us if u["job_type"] == "watchlist"))
            finally:
                pdb.close()
            out["requests_pilot"] = out["page_pilot"]["requests"]
        else:
            out["page_pilot"] = {"job": "page_pilot", "state": why, "requests": 0}
    if control_pair.pairs_cfg(cfg) and not out["stopped"]:       # isolated: its pages and posts go to the audit store, production gets ledger rows only
        adb = control_pair.connect_audit(audit_db or control_pair.audit_path(db.execute("PRAGMA database_list").fetchone()[2]))
        try:
            out["control"] = control_pair.run(db, adb, cfg, transport, now, ledger=ledger, clock=clock)
        finally:
            adb.close()
        out["requests_control"] = out["control"]["requests"]
    out["transient_errors"] = sum(c.transient_errors for c in cols.values())
    out["credit_hold"] = credit_hold(db)
    out["status"] = "PAUSED_PROVIDER_CREDITS" if out["credit_hold"] else "RAN"
    out["finished_at_utc"] = store.iso(clock())
    out["usage"] = ledger.usage(clock())
    return out


def _one(db, sql, args=()):
    try:
        r = db.execute(sql, args).fetchone()
        return r[0] if r else None
    except Exception:  # noqa: BLE001
        return None


def facts(db, cfg: dict, now: datetime, versions=None) -> dict:
    """What the store itself says about freshness; nothing here is taken from a caller."""
    ident = stance.identity_hash(cfg)
    q = stance.queue_status(db, cfg, now=store.iso(now), versions=versions)
    return {"credit_hold": credit_hold(db),
            "last_successful_collection_at_utc": _one(db, "SELECT MAX(settled_at_utc) FROM x_cost_ledger WHERE status='SETTLED' AND returned>0"),
            "last_post_received_at_utc": _one(db, "SELECT MAX(first_seen_at_utc) FROM x_posts"),
            "last_label_written_at_utc": _one(db, "SELECT MAX(label_written_at_utc) FROM x_stance_labels WHERE inference_identity=?", (ident,)),
            "last_summary_committed_at_utc": _one(db, "SELECT MAX(signal_committed_at_utc) FROM x_daily_sentiment"),
            "pending": {"new_posts_queue": q["recent"]["pending"], "history_backlog": q["history"]["pending"],
                        "new_posts_oldest_pending_age_hours": q["recent"]["oldest_pending_age_hours"], "history_oldest_pending_age_hours": q["history"]["oldest_pending_age_hours"]},
            "pairs": {"new_posts_queue": q["recent"]["pairs"], "history_backlog": q["history"]["pairs"]},
            "inferences_last_7_days": q["rates_last_7_days"]["model_inferences_completed"]}


def write_receipt(db, cfg: dict, now: datetime, stages: dict, versions=None) -> dict:
    """One update round, as the host script ran it: its own stage timings plus the store's facts. The page reads the
    newest one. A round is a successful update when the build stage ended well; collection may be paused."""
    f = facts(db, cfg, now, versions)
    ok = (stages.get("build") or {}).get("exit") == 0
    prev = json.loads(get_meta(db, RECEIPT_KEY) or "null")
    rec = {"round_started_at_utc": stages.get("started_at_utc"), "round_finished_at_utc": store.iso(now), "ok": ok, "stages": stages, **f,
           "last_successful_update_at_utc": store.iso(now) if ok else (prev or {}).get("last_successful_update_at_utc"),
           "interval_hours": settings(cfg)["interval_hours"]}
    db.execute(LOG_SCHEMA)
    db.execute("INSERT INTO x_update_log(finished_at_utc, receipt_json) VALUES(?,?)", (rec["round_finished_at_utc"], json.dumps(rec, ensure_ascii=False, sort_keys=True)))
    set_meta(db, RECEIPT_KEY, json.dumps(rec, ensure_ascii=False, sort_keys=True)); db.commit()
    return rec


def read_receipt(db, now: datetime) -> dict:
    """For the page: the newest receipt, the hold as it stands now, and whether the receipt is older than two intervals."""
    rec = json.loads(get_meta(db, RECEIPT_KEY) or "null")
    hold = credit_hold(db)
    if not rec:
        return {"state": "NOT_REPORTED", "credit_hold": hold}
    age = (now - store.parse(rec["round_finished_at_utc"])).total_seconds() / 3600
    keep = ("round_started_at_utc", "round_finished_at_utc", "ok", "last_successful_update_at_utc", "last_successful_collection_at_utc", "last_label_written_at_utc",
            "last_summary_committed_at_utc", "pending", "interval_hours")
    col = (rec.get("stages") or {}).get("collect") or {}
    return {"state": "STALE" if age > 2 * rec.get("interval_hours", 2) + 0.5 else "FRESH", "age_hours": round(age, 2), **{k: rec.get(k) for k in keep},
            "collection": {k: col.get(k) for k in ("status", "requests", "new_posts", "returned")}, "credit_hold": hold,
            "last_post_received_at_utc": rec.get("last_post_received_at_utc"),
            # each stage's exit code as the host script reported it, so the page can tell a partial round from a failed one
            "stage_exits": {k: v.get("exit") for k, v in sorted((rec.get("stages") or {}).items()) if isinstance(v, dict) and "exit" in v}}
