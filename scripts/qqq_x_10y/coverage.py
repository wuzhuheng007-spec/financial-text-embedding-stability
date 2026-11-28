"""Inventory of the X posts already held, by signal day and month (protocol qqq-x-10y-sample-v1).

Signal day: trading day t takes the posts created in [close(t-1), close(t)), closes at 16:00 ET (13:00 on early-close
days); posts after a close belong to the next trading day, weekend and holiday posts to the next trading day.

Main sample (frozen): English (lang 'en'); kind 'original' (main) or 'quote' (kept, reported as its own source, the quoting
post's own text is what is judged); the text names QQQ itself: the cashtag $QQQ or the bare upper-case word QQQ.
Excluded, each with a reason: retweet, reply, other language, only TQQQ/SQQQ/QQQM or only the index ("Nasdaq 100") named,
ticker string only (5 or more cashtags and at most 3 other words).
Layers (a post can carry several): history_no_floor (90-day backfill query without a like floor), floor20_increment,
floor20_focus, control_no_threshold, control_floor20, watchlist, other_topic_query. The like floor is a query condition
seen at fetch time (min_faves:20) and is a stratum, never a filter of the main historical sample.
Ingest modes kept as stored: LIVE, BATCH_AFTER_CLOSE, RETROSPECTIVE_VISIBLE_NOW (backfill read later: "rebuilt from the text
visible at backfill time", not a same-time archive).
"""
from __future__ import annotations

import bisect
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from . import calendar_us as cal

PROTOCOL = "qqq-x-10y-sample-v1"
CASHTAG_QQQ = re.compile(r"\$QQQ(?![A-Za-z])")
BARE_QQQ = re.compile(r"(?<![A-Za-z$])QQQ(?![A-Za-z])")
OTHER_Q = re.compile(r"\$?(TQQQ|SQQQ|QQQM)(?![A-Za-z])")
INDEX = re.compile(r"nasdaq[\s-]?100|ndx", re.I)
CASHTAG = re.compile(r"\$[A-Za-z]{1,6}(?![A-Za-z])")
WORD = re.compile(r"[A-Za-z一-鿿]{2,}")
URL = re.compile(r"https?://\S+")


def parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


class SignalDays:
    def __init__(self, start: date, end: date):
        self.days = cal.trading_days(start, end)
        self.closes = [cal.close_utc(d) for d in self.days]

    def of(self, ts: datetime):
        """Trading day whose window [close(t-1), close(t)) contains ts; None past the last day."""
        i = bisect.bisect_right(self.closes, ts)
        return self.days[i] if i < len(self.days) else None

    def window(self, d: date):
        i = self.days.index(d)
        return (self.closes[i - 1] if i else None), self.closes[i]


def eligibility(post: dict) -> tuple[bool, str, str]:
    """(in main sample, reason, source) for one post."""
    t = post.get("text") or ""
    if post.get("lang") != "en":
        return False, "not_english", ""
    k = post.get("kind")
    if k == "retweet":
        return False, "retweet", ""
    if k == "reply":
        return False, "reply", ""
    names = bool(CASHTAG_QQQ.search(t) or BARE_QQQ.search(t))
    if not names:
        if OTHER_Q.search(t):
            return False, "only_tqqq_sqqq_qqqm", ""
        if INDEX.search(t):
            return False, "index_only_not_qqq", ""
        return False, "qqq_not_named", ""
    tags = CASHTAG.findall(t)
    words = [w for w in WORD.findall(URL.sub(" ", CASHTAG.sub(" ", t))) if not w.startswith("#")]
    if len(tags) >= 5 and len(words) <= 3:
        return False, "ticker_string_only", ""
    return True, "main", "quote_own_text" if k == "quote" else "original"


def layer_of(query: dict | None, run: dict) -> str:
    if not query:
        return "unknown_query"
    v, t, topic = query.get("vocab_version", ""), query.get("query_text", ""), run.get("topic_id") or ""
    if topic.startswith("watch:"):
        return "watchlist"
    if topic != "qqq":
        return "other_topic_query"
    if v.startswith("control:"):
        return "control_floor20" if v.endswith(":floor") else "control_no_threshold"
    if v.startswith("focus:"):
        return "floor20_focus"
    if v.startswith("incremental:"):
        return "floor20_increment"
    return "history_no_floor" if "min_faves" not in t else "floor20_other"


def run_state(run: dict) -> str:
    st, sr = run.get("status"), run.get("stop_reason") or ""
    if st == "COMPLETE" and sr == "END_OF_RESULTS_EMPTY":
        return "zero_results"
    if st == "COMPLETE":
        return "complete"
    if st == "PLANNED_SAMPLE" and run.get("cursor_left"):
        return "truncated_page_limit"
    if st == "PLANNED_SAMPLE":
        return "complete"
    if sr.startswith("ERROR") or sr.startswith("CIRCUIT"):
        return "api_failure"
    if sr == "ORDER_ANOMALY":
        return "order_anomaly_partial"
    return "other_partial"


def load(export_dir: Path):
    L = lambda f: [json.loads(x) for x in open(export_dir / f, encoding="utf-8") if x.strip()]
    return {k: L(f"{k}.jsonl") for k in ("posts_qqq", "topics_qqq", "labels_qqq", "queries", "runs", "run_posts", "pages", "ledger", "plans")}


def pick_labels(labels):
    """One label per post: the latest written row of the production inference identity (20cf954b...)."""
    best = {}
    for r in labels:
        k = r["post_id"]
        key = (r["inference_identity"] or "", r["label_written_at_utc"] or r["inferred_at_utc"])
        if k not in best or key > best[k][0]:
            best[k] = (key, r)
    return {k: v[1] for k, v in best.items()}


def label_class(r: dict | None) -> str:
    if r is None:
        return "unlabelled"
    if r["quality_status"] == "OK" and r["label"] in ("bullish", "bearish", "neutral"):
        return r["label"]
    if r["quality_status"] == "IRRELEVANT":
        return "irrelevant"
    if r["quality_status"] in ("UNDETERMINED", "CANNOT_TELL"):
        return "cannot_tell"
    return "failure"


def build(export_dir: Path, start: date, end: date, min_authors=(3, 5, 10)):
    X = load(export_dir)
    sd = SignalDays(start, end)
    q = {r["query_hash"]: r for r in X["queries"]}
    runs = {r["run_id"]: r for r in X["runs"]}
    post_layers = defaultdict(set); post_runs = defaultdict(set)
    for rp in X["run_posts"]:
        r = runs.get(rp["run_id"])
        if r:
            post_layers[rp["post_id"]].add(layer_of(q.get(r["query_hash"]), r)); post_runs[rp["post_id"]].add(rp["run_id"])
    pages_by = defaultdict(list)
    for p in X["pages"]:
        pages_by[(p["query_hash"], p["window_start_utc"], p["window_end_utc"])].append(p["response_sha256"])
    lab = pick_labels(X["labels_qqq"])
    posts = []
    for p in X["posts_qqq"]:
        ts = parse(p["created_at_utc"]); day = sd.of(ts)
        ok, why, src = eligibility(p)
        lr = lab.get(p["post_id"])
        page_refs = sorted({h for rid in post_runs[p["post_id"]] for h in pages_by.get((runs[rid]["query_hash"], runs[rid]["window_start_utc"], runs[rid]["window_end_utc"]), [])})
        posts.append({"post_id": p["post_id"], "author_id": p["author_id"], "created_at_utc": p["created_at_utc"], "signal_day": day.isoformat() if day else None,
                      "regular_hours": bool(day and cal.open_utc(day) <= ts < cal.close_utc(day)),
                      "lang": p["lang"], "kind": p["kind"], "ingest_mode": p["ingest_mode"], "edit_status": p["edit_status"], "text_hash": p["text_hash"],
                      "main": ok, "reason": why, "source": src, "layers": sorted(post_layers[p["post_id"]]) or ["no_run_link"],
                      "label_class": label_class(lr), "label": lr and lr["label"], "quality_status": lr and lr["quality_status"],
                      "evidence_text": lr and lr["evidence_text"], "evidence_in_text": bool(lr and lr["evidence_text"] and lr["evidence_text"] in (p["text"] or "")),
                      "inference_identity": lr and lr["inference_identity"], "url": p["source_url"], "text": p["text"],
                      "raw_page_sha256": page_refs, "runs": sorted(post_runs[p["post_id"]])})
    # duplicate texts (same text hash, different id) are kept but counted
    th = Counter(x["text_hash"] for x in posts if x["main"])
    for x in posts:
        x["dup_text_count"] = th[x["text_hash"]] if x["main"] else 0
    # search coverage per signal day, QQQ keyword queries in English only
    cover = defaultdict(lambda: defaultdict(float)); states = defaultdict(Counter)
    for r in X["runs"]:
        if r["topic_id"] != "qqq" or r.get("lang") != "en":
            continue
        lay = layer_of(q.get(r["query_hash"]), r); st = run_state(r)
        a, b = parse(r["window_start_utc"]), parse(r["window_end_utc"])
        i0 = bisect.bisect_right(sd.closes, a)
        for i in range(max(i0, 0), len(sd.days)):
            lo = sd.closes[i - 1] if i else None
            hi = sd.closes[i]
            if lo is not None and lo >= b:
                break
            s0 = max(a, lo) if lo else a; s1 = min(b, hi)
            if s1 > s0:
                span = (hi - lo).total_seconds() if lo else 86400.0
                cover[sd.days[i]][lay] += (s1 - s0).total_seconds() / span
                states[sd.days[i]][st] += 1
    return sd, posts, cover, states, X


def month_table(sd, posts, cover, states, start, end, min_authors=(3, 5, 10)):
    by_day = defaultdict(list)
    for p in posts:
        if p["main"] and p["signal_day"]:
            by_day[p["signal_day"]].append(p)
    months = sorted({d.strftime("%Y-%m") for d in sd.days})
    out = []
    for m in months:
        ds = [d for d in sd.days if d.strftime("%Y-%m") == m]
        pp = [p for d in ds for p in by_day.get(d.isoformat(), [])]
        authors_day = {d: len({p["author_id"] for p in by_day.get(d.isoformat(), [])}) for d in ds}
        dir_auth_day = {d: len({p["author_id"] for p in by_day.get(d.isoformat(), []) if p["label_class"] in ("bullish", "bearish")}) for d in ds}
        searched = [d for d in ds if cover.get(d)]
        st = Counter()
        for d in ds:
            st.update({k: 1 for k in states.get(d, {})})
        lay = Counter(l for p in pp for l in p["layers"])
        lc = Counter(p["label_class"] for p in pp)
        row = {"month": m, "trading_days": len(ds), "days_searched_any_qqq_en_query": len(searched),
               "days_searched_no_floor": sum(1 for d in ds if cover.get(d, {}).get("history_no_floor")),
               "days_searched_floor20": sum(1 for d in ds if any(cover.get(d, {}).get(k) for k in ("floor20_increment", "floor20_focus", "control_floor20"))),
               "days_not_searched": len(ds) - len(searched),
               "days_with_zero_result_window": st["zero_results"], "days_with_truncated_window": st["truncated_page_limit"],
               "days_with_api_failure_window": st["api_failure"], "days_with_partial_order_anomaly": st["order_anomaly_partial"],
               "days_with_main_posts": sum(1 for d in ds if authors_day[d] > 0),
               **{f"days_ge{k}_authors": sum(1 for d in ds if authors_day[d] >= k) for k in min_authors},
               **{f"days_ge{k}_directional_authors": sum(1 for d in ds if dir_auth_day[d] >= k) for k in min_authors},
               "main_posts_dedup": len(pp), "distinct_authors": len({p["author_id"] for p in pp}),
               "source_original": sum(p["source"] == "original" for p in pp), "source_quote_own_text": sum(p["source"] == "quote_own_text" for p in pp),
               "ingest_live": sum(p["ingest_mode"] == "LIVE" for p in pp), "ingest_batch_after_close": sum(p["ingest_mode"] == "BATCH_AFTER_CLOSE" for p in pp),
               "ingest_retrospective": sum(p["ingest_mode"] == "RETROSPECTIVE_VISIBLE_NOW" for p in pp),
               **{f"layer_{k}": lay[k] for k in ("history_no_floor", "floor20_increment", "floor20_focus", "control_no_threshold", "control_floor20", "watchlist", "other_topic_query", "no_run_link")},
               **{f"label_{k}": lc[k] for k in ("bullish", "bearish", "neutral", "irrelevant", "cannot_tell", "failure", "unlabelled")},
               "coverage_status": ("NOT_SEARCHED" if not searched else "PARTIAL_DAYS" if len(searched) < len(ds) else "ALL_DAYS_SEARCHED")}
        out.append(row)
    return out
