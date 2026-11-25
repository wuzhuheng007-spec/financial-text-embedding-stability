"""A focused planned sample for ONE target, by trading session.

The daily and historical jobs spread their pages over every topic, so a single target grows slowly. A focus plan
asks one fixed query for every completed session since `first_session`, in two slices per session (regular hours,
and the time from the previous close to the open), one page each, thinnest sessions first.

  preview   what would be asked, per session, with what is already in the store. Sends nothing, writes nothing.
  run       freezes the plan once, then walks it through the shared ledger (same pools, same caps, same request
            interval, same 503 handling as every other job). A unit that was read is never read again.
  progress  what the plan's units returned and cost so far, from the ledger.

Budget: a unit that ends inside the frozen historical range is charged to the historical pool; any later unit to
the keyword share of the recent pool. Nothing is moved between pools.

Every post is fetched after the fact. A unit that ends more than 24 hours before the run is stored as
RETROSPECTIVE_VISIBLE_NOW, a newer one as BATCH_AFTER_CLOSE; neither is a signal that existed during the session.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from . import batch, config as cfgmod, provider, sample, stance, store
from .ledger import JobBudget, Ledger, get_meta
from .sessions import Calendar

DONE = ("COMPLETE", "PLANNED_SAMPLE")
FRESH_HOURS = 24


class FocusError(ValueError):
    pass


def _read(r, pages: int) -> bool:
    """A unit is read when the provider said it ended, or the plan's number of pages was read."""
    return bool(r) and (r["status"] == "COMPLETE" or (r["status"] in DONE and (r["pages"] or 0) >= pages))


def spec(cfg: dict, name: str) -> dict:
    f = (cfg.get("focus") or {}).get(name)
    if not f:
        raise FocusError("unknown focus plan")
    return f


def _cal(cfg: dict) -> Calendar:
    c = cfg["time"]["exchange_calendar"]
    return Calendar.load(store.REPO_ROOT / c["path"], c["sha256"])


def query_hash(cfg: dict, name: str) -> str:
    f = spec(cfg, name)
    base = f.get("reuse_query_of") or name
    if base != name and spec(cfg, base)["query"] != f["query"]:
        raise FocusError("reuse_query_of names a plan with a different query")
    return store.sha(f"{cfg['provider_candidate']}|focus:{base}|{f['query']}")


def run_id(cfg: dict, qh: str, s: int, e: int) -> str:
    return store.sha(f"{cfg['provider_candidate']}|{qh}|{s}|{e}")[:24]              # the collector's own run id


def in_store(db, cfg: dict, name: str, cal: Calendar) -> dict:
    """Per session: posts already stored whose own text names the target, how many meet the like floor, and how
    many of those carry a valid label of the classification version in force."""
    f = spec(cfg, name)
    pat = ((sample.rules(cfg) or {}).get("target_must_be_named") or {}).get(f["target_id"])
    rx = re.compile(pat, re.I) if pat else None
    floor = (sample.rules(cfg) or {}).get("min_likes") or 0
    ident = stance.identity_hash(cfg)
    ok = {r[0] for r in db.execute("SELECT post_id FROM x_stance_labels WHERE topic_id=? AND target_id=? AND inference_identity=? AND quality_status='OK' AND label IN ('bullish','bearish','neutral')",
                                   (f["topic_id"], f["target_id"], ident))}
    out: dict = {}
    for r in db.execute("SELECT post_id, author_id, created_at_utc, author_text, engagement_snapshot_json FROM x_posts"):
        if rx and not rx.search(r["author_text"] or ""):
            continue
        i = cal.assign(store.parse(r["created_at_utc"]))
        if i is None:
            continue
        c = out.setdefault(cal.dates[i], {"named": 0, "at_floor": 0, "valid": 0, "authors": set()})
        c["named"] += 1
        likes = sample.engagement(r).get("likeCount")
        if isinstance(likes, int) and likes >= floor:
            c["at_floor"] += 1
            if r["post_id"] in ok:
                c["valid"] += 1; c["authors"].add(r["author_id"])
    return out


def build_plan(db, cfg: dict, name: str, now: datetime) -> dict:
    f, cal = spec(cfg, name), _cal(cfg)
    have = in_store(db, cfg, name, cal)
    hr = get_meta(db, "history_range")
    hist_end = store.parse(json.loads(hr)["end_utc_exclusive"]) if hr else None
    qh = query_hash(cfg, name)
    first = cal.index(f["first_session"])
    if first is None or first <= 0:
        raise FocusError("first_session is not a session with a known previous close")
    sessions = [i for i in range(first, len(cal.dates)) if cal.closes[i] <= now]
    by_slice = {"regular": lambda i: (cal.opens[i], cal.closes[i]), "pre_open": lambda i: (cal.closes[i - 1], cal.opens[i])}
    units, rank = [], sorted(sessions, key=lambda i: ((have.get(cal.dates[i]) or {}).get("valid", 0), cal.dates[i]))
    for rnd, sl in enumerate(f["slices"], 1):
        for i in rank:
            a, b = by_slice[sl](i)
            s, e = int(a.timestamp()), int(b.timestamp())
            units.append({"round": rnd, "session": cal.dates[i], "slice": sl, "start": s, "end": e, "run_id": run_id(cfg, qh, s, e),
                          "pool": "historical" if hist_end is not None and b <= hist_end else "recent",
                          "ingest_mode": provider.RETRO if now - b > timedelta(hours=FRESH_HOURS) else provider.BATCH})
    return {"job": "focus", "name": name, "topic_id": f["topic_id"], "target_id": f["target_id"], "lang": f["lang"], "query": f["query"], "query_hash": qh,
            "min_likes_in_query": f.get("min_likes_in_query"), "pages_per_unit": int(f.get("pages_per_unit", 1)), "max_new_posts": int(f["max_new_posts"]),
            "order": f["order"], "sessions": [cal.dates[i] for i in sessions], "valid_in_store_when_planned": {cal.dates[i]: (have.get(cal.dates[i]) or {}).get("valid", 0) for i in sessions},
            "units": units, "sampling_mode": "PLANNED_SAMPLE", "sample_rules_version": sample.rules_version(cfg)}


def _frozen(db, name: str):
    row = db.execute("SELECT plan_json, sha256, frozen_at_utc FROM x_sample_plans WHERE job='focus' AND plan_key=?", (name,)).fetchone()
    return {**json.loads(row[0]), "sha256": row[1], "frozen_at_utc": row[2], "reused": True} if row else None


def preview(db, cfg: dict, name: str, now: datetime, ledger: Ledger) -> dict:
    """Nothing is sent and nothing is written. If the plan is already frozen, that plan is shown."""
    plan = _frozen(db, name) or {**build_plan(db, cfg, name, now), "sha256": None, "frozen_at_utc": None, "reused": False}
    cal, page = _cal(cfg), cfg["quote"]["documented_max_posts_per_page"]
    have, per_post = in_store(db, cfg, name, cal), float(cfg["quote"]["per_post"])
    runs = {r["run_id"]: r for r in db.execute("SELECT * FROM x_query_runs WHERE query_hash=?", (plan["query_hash"],))}
    usage = ledger.usage(now)
    left = {p: usage["pools"][p]["cap_posts"] - usage["pools"][p]["returned_posts"] for p in ("recent", "historical")}
    left["recent"] = min(left["recent"], usage["sub_pools"]["recent"]["cap_posts"] - usage["sub_pools"]["recent"]["returned_posts"]) if "recent" in usage.get("sub_pools", {}) else left["recent"]
    left_day = usage["today"]["cap_posts"] - usage["today"]["returned_posts"]
    open_units = [u for u in plan["units"] if not _read(runs.get(u["run_id"]), plan["pages_per_unit"])]
    left_pages = {u["run_id"]: plan["pages_per_unit"] - ((runs.get(u["run_id"]) or {"pages": 0})["pages"] or 0) for u in open_units}   # a unit read to page 1 has one page to go
    pages_left = sum(left_pages.values())
    fit, room, day_room, budgeted_new = Counter(), dict(left), left_day, 0
    already_new = sum(r["new_posts"] or 0 for r in runs.values())
    for u in open_units:                                       # same order and the same stop rule as the run; full pages assumed
        if already_new + budgeted_new >= plan["max_new_posts"]:
            break
        need = page * left_pages[u["run_id"]]
        if room[u["pool"]] >= need and day_room >= need:
            room[u["pool"]] -= need; day_room -= need; fit[u["pool"]] += 1; budgeted_new += need
    table = []
    for d in plan["sessions"]:
        h = have.get(d) or {"named": 0, "at_floor": 0, "valid": 0, "authors": set()}
        us = [u for u in plan["units"] if u["session"] == d]
        table.append({"session": d, "named_in_store": h["named"], "at_like_floor_in_store": h["at_floor"], "valid_in_store": h["valid"], "valid_authors_in_store": len(h["authors"]),
                      "pool": us[0]["pool"], "stored_as": us[0]["ingest_mode"], "units": len(us), "units_read": sum(1 for u in us if _read(runs.get(u["run_id"]), plan["pages_per_unit"])),
                      "max_posts_planned": len(us) * page * plan["pages_per_unit"], "possible_repeat_of_stored_posts": h["at_floor"]})
    max_posts = pages_left * page
    tot_valid, tot_floor = sum(t["valid_in_store"] for t in table), sum(t["at_like_floor_in_store"] for t in table)
    return {"status": "PREVIEW", "requests_sent": 0, "name": name, "frozen": bool(plan["reused"]), "plan_sha256": plan["sha256"], "query": plan["query"], "order": plan["order"],
            "sessions": len(plan["sessions"]), "first_session": plan["sessions"][0] if plan["sessions"] else None, "last_session": plan["sessions"][-1] if plan["sessions"] else None,
            "units_total": len(plan["units"]), "units_open": len(open_units), "units_by_pool": dict(Counter(u["pool"] for u in open_units)), "units_by_round": dict(Counter(u["round"] for u in open_units)),
            "stop_at_new_posts": plan["max_new_posts"], "new_posts_so_far": already_new,
            "if_every_page_is_full": {"posts": min(max_posts, max(0, plan["max_new_posts"] - already_new) + page), "all_open_units_posts": max_posts,
                                      "usd_at_quoted_rate_all_open_units": round(max_posts * per_post, 4), "requests_all_open_units": pages_left,
                                      "minutes_at_request_interval": round(pages_left * cfg["acquisition"]["min_request_interval_seconds"] / 60, 1)},
            "today": {"budget_day": usage["budget_day"], "posts_left": {"day": left_day, **left}, "units_that_fit_today": dict(fit), "posts_budgeted_today_if_full": budgeted_new,
                      "usd_today_if_full": round(budgeted_new * per_post, 4), "units_left_for_a_later_budget_day": len(open_units) - sum(fit.values())},
            "in_store_now": {"valid_posts": tot_valid, "valid_authors": len({a for d in plan["sessions"] for a in (have.get(d) or {"authors": ()})["authors"]}),
                             "sessions_with_valid": sum(1 for t in table if t["valid_in_store"]), "sessions_without_valid": sum(1 for t in table if not t["valid_in_store"]),
                             "valid_per_post_at_like_floor": round(tot_valid / tot_floor, 3) if tot_floor else None},
            "per_session": table,
            "note": "counts in store are posts whose own text names the target; valid = a bullish/bearish/neutral label of the classification version in force. "
                    "Planned posts assume full pages; the provider may return fewer. Nothing here is a promise about what was posted on a date."}


def progress(db, cfg: dict, name: str) -> dict:
    plan = _frozen(db, name)
    if not plan:
        return {"status": "NOT_FROZEN", "name": name}
    ids = [u["run_id"] for u in plan["units"]]
    runs = {r["run_id"]: r for r in db.execute("SELECT * FROM x_query_runs WHERE query_hash=?", (plan["query_hash"],))}
    led = db.execute(f"SELECT status, COUNT(*), COALESCE(SUM(settled_credits),0), COALESCE(SUM(returned),0) FROM x_cost_ledger WHERE run_id IN ({','.join('?' * len(ids))}) GROUP BY status", ids).fetchall() if ids else []
    per_usd = cfg["quote"]["credits_per_usd"]
    st = Counter((runs[i]["status"], runs[i]["stop_reason"]) if i in runs else ("NOT_ASKED", None) for i in ids)
    by_session = {}
    for u in plan["units"]:
        r = runs.get(u["run_id"])
        c = by_session.setdefault(u["session"], {"session": u["session"], "units": 0, "units_read": 0, "returned": 0, "new_posts": 0, "duplicates": 0, "open": []})
        c["units"] += 1
        if r:
            c["returned"] += r["returned"] or 0; c["new_posts"] += r["new_posts"] or 0; c["duplicates"] += r["duplicates"] or 0
        if _read(r, plan["pages_per_unit"]):
            c["units_read"] += 1
        else:
            c["open"].append({"slice": u["slice"], "status": r["status"] if r else "NOT_ASKED", "stop_reason": r["stop_reason"] if r else None})
    return {"status": "FROZEN", "name": name, "plan_sha256": plan["sha256"], "frozen_at_utc": plan["frozen_at_utc"], "query": plan["query"], "units": len(ids),
            "units_by_status": [{"status": k[0], "stop_reason": k[1], "units": v} for k, v in sorted(st.items(), key=lambda x: str(x[0]))],
            "returned": sum(r["returned"] or 0 for r in runs.values()), "new_posts": sum(r["new_posts"] or 0 for r in runs.values()), "duplicates": sum(r["duplicates"] or 0 for r in runs.values()),
            "requests": sum(r["requests"] or 0 for r in runs.values()),
            "ledger": [{"status": r[0], "requests": r[1], "credits": r[2], "usd": str(round(r[2] / per_usd, 5)), "returned": r[3]} for r in led],
            "ledger_basis": "estimate from the quoted rate; not the provider's bill", "per_session": [by_session[d] for d in plan["sessions"]]}


def _page_min_likes(db, plan: dict, u: dict):
    ws, we = (store.iso(datetime.fromtimestamp(x, timezone.utc)) for x in (u["start"], u["end"]))
    page = db.execute("SELECT response_json FROM x_page_cache WHERE query_hash=? AND window_start_utc=? AND window_end_utc=? AND cursor=''", (plan["query_hash"], ws, we)).fetchone()
    likes = [t.get("likeCount") for t in (json.loads(page[0]).get("tweets") or [])] if page else []
    known = [x for x in likes if isinstance(x, int)]
    return (min(known) if known else None), len(likes), len(known)


def run(db, cfg: dict, transport, name: str, anchor: datetime, *, clock=None, ledger: Ledger | None = None) -> dict:
    clock = clock or batch.utc_now
    ledger = ledger or Ledger(db, cfg, clock=clock)
    plan = batch._freeze(db, "focus", name, build_plan(db, cfg, name, anchor), anchor)
    db.execute("INSERT OR IGNORE INTO x_queries VALUES(?,?,?,?,?,?)", (plan["query_hash"], cfg["provider_candidate"], plan["topic_id"], plan["lang"], f"focus:{spec(cfg, name).get('reuse_query_of') or name}", plan["query"]))
    db.commit()
    q = {"query_hash": plan["query_hash"], "topic_id": plan["topic_id"], "lang": plan["lang"], "query": plan["query"]}
    cols = {}

    def col(u):
        key = (u["pool"], u["ingest_mode"])
        if key not in cols:                                    # the recent pool is entered through its keyword share
            cols[key] = provider.Collector(db, cfg, budget=JobBudget(ledger, pool=u["pool"], job_type="recent" if u["pool"] == "recent" else "focus"),
                                           transport=transport, mode=u["ingest_mode"], clock=clock)
        return cols[key]
    out = {"job": "focus", "name": name, "plan_sha256": plan["sha256"], "plan_reused": plan["reused"], "started_at_utc": store.iso(clock()), "stopped": None,
           "units_asked": 0, "units_skipped_already_read": 0, "pools_exhausted": [], "like_floor_check": None, "interruptions": []}
    new_total = sum(r[0] or 0 for r in db.execute("SELECT new_posts FROM x_query_runs WHERE query_hash=?", (plan["query_hash"],)))
    closed, floor = set(), plan.get("min_likes_in_query")
    for u in plan["units"]:
        if new_total >= plan["max_new_posts"]:
            out["stopped"] = "TARGET_NEW_POSTS_REACHED"
            break
        if u["pool"] in closed:
            continue
        r = db.execute("SELECT * FROM x_query_runs WHERE run_id=?", (u["run_id"],)).fetchone()
        if r and (r["status"] in DONE and r["pages"] >= plan["pages_per_unit"] or r["status"] == "COMPLETE" or (r["status"] == "PARTIAL" and not batch.retriable(db, r, clock()))):
            out["units_skipped_already_read"] += 1
            continue
        before = (r["new_posts"] or 0) if r else 0
        c = col(u)
        try:
            res = c.collect_pages(q, u["start"], u["end"], plan["pages_per_unit"])
        except provider.StopAll as stop:
            if stop.reason.startswith(("LIMIT_REACHED:POOL_", "LIMIT_REACHED:SUBPOOL_")):   # this pool is used up for the budget day; the other one may go on
                closed.add(u["pool"]); out["pools_exhausted"].append({"pool": u["pool"], "reason": stop.reason})
                continue
            out["stopped"] = stop.reason
            break
        out["units_asked"] += 1
        new_total += max(0, (res.get("new_posts") or 0) - before)
        if res.get("status") == "PARTIAL":
            out["interruptions"].append({"session": u["session"], "slice": u["slice"], "reason": res.get("stop_reason")})
        if floor is not None and out["like_floor_check"] is None and (res.get("returned") or 0) > 0:
            lo, n, known = _page_min_likes(db, plan, u)
            out["like_floor_check"] = {"first_page_posts": n, "with_like_count": known, "lowest_like_count": lo, "floor": floor, "honoured": lo is not None and lo >= floor}
            if not out["like_floor_check"]["honoured"]:           # the search did not apply the like operator: stop before paying for posts the rules drop
                out["stopped"] = "LIKE_OPERATOR_NOT_HONOURED"
                break
    out["transient_errors"] = sum(c.transient_errors for c in cols.values())
    out["deferred_units"] = sum(c.deferred_units for c in cols.values())
    out["requests"] = sum(c.budget.requests for c in cols.values())
    out["finished_at_utc"] = store.iso(clock())
    out["progress"] = progress(db, cfg, name)
    out["usage"] = ledger.usage(clock())
    return out
