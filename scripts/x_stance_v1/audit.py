"""Read-only audit of one X store: the request ledger row by row, a cross-check of its totals against the stored
pages, every unit of every frozen plan that is not finished (with the reason), and how model calls map to the
pairs the page counts as processed.

Nothing here writes to the store and nothing reaches the provider. The output carries no post text, no query text,
no user name and no key: requests are shown by a short id, authors by their watch id.

What the ledger columns mean (the cap checks use budget_consumed, and showing the parts changes none of it):
  confirmed_returned  posts in pages that really came back (status SETTLED)
  reserved            a request still open (status RESERVED): one full page is held until it is settled
  unknown_held        a request whose outcome is not known (timeout, 429 ...): one full page stays counted
  budget_consumed     confirmed_returned + reserved + unknown_held  - what every cap is checked against
  settled_estimated_cost   15 credits x max(returned, 1) for settled requests, from the quoted rate; not a bill
  held_cost           credits kept for reserved and unknown requests
  released            a reservation given back under a documented no-charge rule (HTTP 503); counts for nothing
"""
from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
PER_USD = 100000


def _usd(credits: int) -> str:
    return f"{credits / PER_USD:.5f}".rstrip("0").rstrip(".") if credits else "0"


def _run_id(provider: str, qh: str, s: int, e: int) -> str:
    return hashlib.sha256(f"{provider}|{qh}|{s}|{e}".encode("utf-8")).hexdigest()[:24]


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ny_date(iso: str | None) -> str | None:
    if not iso:
        return None
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(NY).date().isoformat()


def _has(db, table: str) -> bool:
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


# ---------- 1. ledger ----------
def ledger_rows(db) -> list[dict]:
    out = []
    q = ("SELECT l.*, r.topic_id AS run_topic, r.lang AS run_lang, r.status AS run_status FROM x_cost_ledger l LEFT JOIN x_query_runs r ON r.run_id = l.run_id "
         "ORDER BY l.created_at_utc, l.rowid")
    for i, r in enumerate(db.execute(q), 1):
        st = r["status"]
        settled = st == "SETTLED"
        open_ = st == "RESERVED"
        held = st == "UNKNOWN_HELD"
        topic = r["run_topic"] or ""
        out.append({
            "seq": i, "request": r["request_id"][:8], "created_at_utc": r["created_at_utc"], "settled_at_utc": r["settled_at_utc"], "budget_day_shanghai": r["budget_day"],
            "pool": r["pool"], "job_type": "preflight" if r["is_preflight"] else r["job_type"], "unit_kind": "author" if topic.startswith("watch") else "keyword" if topic else "unknown",
            "unit": topic, "lang": r["run_lang"], "window_start_utc": r["window_start_utc"], "window_end_utc": r["window_end_utc"], "sample_date_ny": _ny_date(r["window_start_utc"]),
            "page": "continuation" if (r["cursor"] or "") else "first", "attempt": r["attempt"], "status": st, "note": r["note"],
            "confirmed_returned": (r["returned"] or 0) if settled else 0, "unique_ids": r["unique_ids"] if settled else None,
            "reserved": r["reserved_posts"] if open_ else 0, "unknown_held": r["reserved_posts"] if held else 0,
            "budget_consumed": ((r["returned"] or 0) if settled else r["reserved_posts"] if (open_ or held) else 0),
            "settled_estimated_credits": (r["settled_credits"] or 0) if settled else 0, "held_credits": r["reserved_credits"] if (open_ or held) else 0,
            "released_credits_reserved": r["reserved_credits"] if st == "RELEASED" else 0, "cost_source": r["cost_source"],
        })
    return out


def _total(rows) -> dict:
    s = lambda k: sum(r[k] for r in rows)
    return {"requests": len(rows), "by_status": dict(Counter(r["status"] for r in rows)), "confirmed_returned": s("confirmed_returned"), "reserved": s("reserved"),
            "unknown_held": s("unknown_held"), "budget_consumed": s("budget_consumed"), "settled_estimated_cost_usd": _usd(s("settled_estimated_credits")),
            "held_cost_usd": _usd(s("held_credits")), "budget_consumed_cost_usd": _usd(s("settled_estimated_credits") + s("held_credits")),
            "released_requests": sum(1 for r in rows if r["status"] == "RELEASED")}


def reconcile(db, rows: list[dict]) -> dict:
    """Three independent counts of the same thing: the ledger's settled rows, the stored pages, and the run table."""
    pages = defaultdict(int)
    n_pages, page_posts, sizes = 0, 0, Counter()
    for r in db.execute("SELECT response_json FROM x_page_cache"):
        tw = json.loads(r[0]).get("tweets")
        n = len(tw) if isinstance(tw, list) else 0
        n_pages += 1; page_posts += n; sizes[n] += 1
    runs = db.execute("SELECT COALESCE(SUM(returned),0), COALESCE(SUM(requests),0), COUNT(*) FROM x_query_runs").fetchone()
    settled = [r for r in rows if r["status"] == "SETTLED"]
    credit_rule = sum(15 * max(r["confirmed_returned"], 1) for r in settled)
    t = _total(rows)
    by_day = {d: _total([r for r in rows if r["budget_day_shanghai"] == d]) for d in sorted({r["budget_day_shanghai"] for r in rows})}
    for d in by_day.values():
        d.pop("by_status")
    groups = defaultdict(list)
    for r in rows:
        groups[(r["budget_day_shanghai"], r["pool"], r["job_type"])].append(r)
    checks = {
        "settled_requests_equal_stored_pages": len(settled) == n_pages,
        "confirmed_returned_equals_posts_in_stored_pages": t["confirmed_returned"] == page_posts,
        "settled_credits_equal_15_x_max_returned_1": sum(r["settled_estimated_credits"] for r in settled) == credit_rule,
        "no_reservation_left_open": t["reserved"] == 0,
    }
    return {"totals": t, "by_budget_day_shanghai": by_day,
            "by_day_pool_job": [{"budget_day_shanghai": k[0], "pool": k[1], "job_type": k[2], **{kk: vv for kk, vv in _total(v).items() if kk != "by_status"}} for k, v in sorted(groups.items())],
            "stored_pages": {"pages": n_pages, "posts_in_pages": page_posts, "page_size_distribution": {str(k): v for k, v in sorted(sizes.items())}},
            "run_table": {"runs": runs[2], "returned": runs[0], "requests": runs[1]},
            "unique_posts_stored": db.execute("SELECT COUNT(*) FROM x_posts").fetchone()[0],
            "checks": checks, "all_checks_pass": all(checks.values()),
            "billing_note": "costs are estimates from the quoted rate; this was not reconciled with the provider's bill"}


# ---------- 2. unfinished units of every frozen plan ----------
def _unit_state(run, planned_pages: int) -> tuple[str, str]:
    """(state, reason). Finished means the frozen plan for this unit is finished - not that X was read in full."""
    if run is None:
        return "NEVER_RUN", "NO_REQUEST_SENT"
    st, reason, pages = run["status"], run["stop_reason"] or "", run["pages"]
    if st == "COMPLETE":
        return "DONE", reason
    if st == "PARTIAL":
        return "PARTIAL", reason
    if pages >= planned_pages:
        return "DONE", "PLANNED_PAGE_LIMIT"
    if reason.startswith("LIMIT_REACHED") or reason.startswith("BUDGET") or reason.startswith("PAUSED"):
        return "BUDGET_CUT", reason
    return "BELOW_PLANNED_DEPTH", reason or "JOB_ENDED_BEFORE_THIS_DEPTH"


def plan_units(db, plan_row) -> list[dict]:
    """Every (unit, planned pages) of one frozen plan with its run. Recent plans only; see history_summary."""
    plan = json.loads(plan_row["plan_json"])
    prov = db.execute("SELECT provider FROM x_queries LIMIT 1").fetchone()
    prov = prov[0] if prov else "twitterapi_io"
    out = []

    def add(kind, u, planned, label):
        rid = _run_id(prov, u["query_hash"], u["start"], u["end"])
        run = db.execute("SELECT * FROM x_query_runs WHERE run_id=?", (rid,)).fetchone()
        state, reason = _unit_state(run, planned)
        out.append({"plan_id": plan_row["plan_id"], "sample_date_ny": plan_row["plan_key"], "kind": kind, "unit": label, "lang": u.get("lang"), "slice": u.get("slice"),
                    "window_start_utc": _iso(u["start"]), "window_end_utc": _iso(u["end"]), "run_id": rid, "query_hash": u["query_hash"], "planned_pages": planned,
                    "pages_read": run["pages"] if run else 0, "returned": run["returned"] if run else 0, "state": state, "reason": reason,
                    "has_cursor": bool(run and run["cursor"]), "query_registered": bool(db.execute("SELECT 1 FROM x_queries WHERE query_hash=?", (u["query_hash"],)).fetchone())})

    for u in (plan.get("watch") or {}).get("units", []):
        add("author", u, plan["watch"]["max_pages_per_author"], u["watch_id"])
    for u in plan.get("units", []):
        add("keyword", u, plan["pages_per_unit"], u["topic_id"])
    return out


def gaps(db) -> dict:
    plans = [r for r in db.execute("SELECT * FROM x_sample_plans WHERE job='recent' ORDER BY plan_key")] if _has(db, "x_sample_plans") else []
    units, per_plan = [], []
    for p in plans:
        us = plan_units(db, p)
        plan = json.loads(p["plan_json"])
        open_ = [u for u in us if u["state"] != "DONE"]
        units += open_
        per_plan.append({"plan_id": p["plan_id"], "sample_date_ny": p["plan_key"], "frozen_at_utc": p["frozen_at_utc"], "vocab_version": plan.get("vocab_version"),
                         "watchlist_version_tag": (plan.get("watch") or {}).get("watchlist_version_tag"), "authors_not_asked_blocked": (plan.get("watch") or {}).get("not_asked", []),
                         "units_planned": len(us), "units_done": len(us) - len(open_),
                         "pages_planned": sum(u["planned_pages"] for u in us), "pages_read": sum(min(u["pages_read"], u["planned_pages"]) for u in us),
                         "pages_missing_upper_bound": sum(u["planned_pages"] - u["pages_read"] for u in open_ if u["state"] != "PARTIAL" or True),
                         "open_by_kind_state_reason": [{"kind": k[0], "state": k[1], "reason": k[2], "units": v} for k, v in sorted(Counter((u["kind"], u["state"], u["reason"]) for u in open_).items())]})
    partial = [dict(r) for r in db.execute("SELECT run_id, topic_id, lang, window_start_utc, window_end_utc, stop_reason, pages, returned FROM x_query_runs WHERE status='PARTIAL' ORDER BY window_start_utc, topic_id")]
    recent_ids = {u["run_id"]: u for u in units}
    for r in partial:
        u = recent_ids.get(r["run_id"])
        r["belongs_to"] = f"recent plan {u['sample_date_ny']}" if u else "historical plan or preflight"
        r["sample_date_ny"] = _ny_date(r["window_start_utc"])
    return {"recent_plans": per_plan, "open_units": units, "partial_runs_all_jobs": partial,
            "partial_runs_by_sample_date": dict(Counter(r["sample_date_ny"] for r in partial)), "history": history_summary(db),
            "note": "an open unit is a unit of a frozen sampling plan that did not reach its planned pages; closing all of them finishes the plan, it does not mean X was read in full"}


def history_summary(db) -> dict:
    row = db.execute("SELECT * FROM x_sample_plans WHERE job='historical' ORDER BY frozen_at_utc LIMIT 1").fetchone() if _has(db, "x_sample_plans") else None
    if not row:
        return {"state": "NO_PLAN"}
    plan = json.loads(row["plan_json"])
    r = plan["range"]
    days = r["days"]
    n_units = days * plan["slices"] * len(plan["queries"])
    q = ("SELECT status, stop_reason, pages FROM x_query_runs WHERE window_start_utc>=? AND window_end_utc<=? AND topic_id NOT LIKE 'watch%'")
    runs = db.execute(q, (r["start_utc"], r["end_utc_exclusive"])).fetchall()
    touched = [x for x in runs if x["pages"] > 0]
    return {"plan_id": row["plan_id"], "range": [r["first_ny_date"], r["last_ny_date"]], "max_depth": plan["max_depth"], "units_planned": n_units, "pages_planned": n_units * plan["max_depth"],
            "units_with_at_least_one_page": len(touched), "pages_read": sum(x["pages"] for x in touched), "units_never_run": n_units - len(runs),
            "runs_by_status_reason": [{"status": k[0], "reason": k[1], "runs": v} for k, v in sorted(Counter((x["status"], x["stop_reason"]) for x in runs).items(), key=str)],
            "note": "walked date-first within the daily historical pool; most of this plan has not been asked yet"}


# ---------- 3. model calls against processed pairs ----------
def label_mapping(db) -> dict:
    if not _has(db, "x_stance_labels"):
        return {"labels": 0}
    cols = {r[1] for r in db.execute("PRAGMA table_info(x_stance_labels)")}
    labels = [dict(r) for r in db.execute("SELECT * FROM x_stance_labels")]
    by_version = defaultdict(lambda: Counter())
    for r in db.execute("SELECT topic_version, quality, COUNT(*) FROM x_post_topics GROUP BY 1, 2"):
        by_version[r[0]][r[1]] = r[2]
    lab_keys = {(l["provider"], l["post_id"], l["topic_id"], l["target_id"]) for l in labels}
    rows = db.execute("SELECT topic_version, provider, post_id, topic_id, target_id, quality FROM x_post_topics").fetchall()
    per_version = {}
    for ver in sorted(by_version):
        mine = [r for r in rows if r["topic_version"] == ver]
        counted = [r for r in mine if not str(r["quality"]).startswith("FILTERED_")]
        with_label = [r for r in counted if (r["provider"], r["post_id"], r["topic_id"], r["target_id"]) in lab_keys]
        not_pending_no_label = [r for r in counted if r["quality"] != "PENDING" and (r["provider"], r["post_id"], r["topic_id"], r["target_id"]) not in lab_keys]
        per_version[ver] = {"pairs_counted": len(counted), "pairs_with_a_stored_label": len(with_label), "pairs_settled_without_a_model_call": len(not_pending_no_label),
                            "settled_without_a_model_call_by_quality": dict(Counter(r["quality"] for r in not_pending_no_label)),
                            "processed_as_the_page_counts_it": len(with_label) + len(not_pending_no_label), "by_quality": dict(by_version[ver])}
    return {"stored_labels": len(labels), "distinct_post_topic_pairs_with_a_label": len(lab_keys), "labels_by_quality": dict(Counter(l["quality_status"] for l in labels)),
            "label_identities": dict(Counter((l.get("inference_identity") or "none")[:12] for l in labels)) if "inference_identity" in cols else None,
            "per_sample_version": per_version,
            "reading": "the page's 'processed' count is per sample version: pairs that have a stored label plus pairs settled by a rule without a model call"}


# ---------- output ----------
def write(db, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = ledger_rows(db)
    with open(out_dir / "ledger_rows_redacted.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["seq"])
        w.writeheader(); w.writerows(rows)
    rec, g, lm = reconcile(db, rows), gaps(db), label_mapping(db)
    with open(out_dir / "open_units.csv", "w", newline="", encoding="utf-8") as f:
        keys = ["plan_id", "sample_date_ny", "kind", "unit", "lang", "slice", "window_start_utc", "window_end_utc", "planned_pages", "pages_read", "returned", "state", "reason", "has_cursor", "query_registered"]
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader(); w.writerows(g["open_units"])
    summary = {"generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "ledger": rec,
               "gaps": {k: v for k, v in g.items() if k != "open_units"}, "open_units": len(g["open_units"]), "labels": lm}
    (out_dir / "audit_summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return summary


if __name__ == "__main__":
    import sys
    con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    s = write(con, Path(sys.argv[2]))
    print(json.dumps({"ledger_totals": s["ledger"]["totals"], "checks": s["ledger"]["checks"], "stored_pages": s["ledger"]["stored_pages"], "open_units": s["open_units"],
                      "partial_by_sample_date": s["gaps"]["partial_runs_by_sample_date"]}, ensure_ascii=False, indent=1))
