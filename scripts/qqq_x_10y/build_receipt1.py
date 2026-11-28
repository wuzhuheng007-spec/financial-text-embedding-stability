"""Receipt 1, posts part: month coverage matrix, signal-day coverage, private case example index.

python -m scripts.qqq_x_10y.build_receipt1 --export <private store_export dir> --regimes <private regime_daily_private.csv>
       --private <dir> --public <dir>

Example rule (frozen, protocol qqq-x-10y-sample-v1): for each case window and each class (bullish, bearish, neutral, and
irrelevant / failure for the classifier check), main-sample posts only (bullish and bearish only when the label's evidence
quote is found verbatim in the text; a neutral label carries no directional quote), one post per author, ranked by
sha256("qqq-x-10y-examples-v1|" + post_id) ascending; the first K=4 are taken. Fewer than K is reported as a shortfall.
Public output carries counts only; the private index carries links, times, evidence and text.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

from . import coverage as C
from .build_regimes import CASES, STUDY_END, STUDY_START, month_bounds

SEED = "qqq-x-10y-examples-v1|"
K = 4
CLASSES = ("bullish", "bearish", "neutral", "irrelevant", "failure")


def rank(pid: str) -> str:
    return hashlib.sha256((SEED + pid).encode()).hexdigest()


def pick(posts, k=K):
    out, seen = [], set()
    for p in sorted(posts, key=lambda p: rank(p["post_id"])):
        if p["author_id"] in seen:
            continue
        seen.add(p["author_id"]); out.append(p)
        if len(out) == k:
            break
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    for a in ("--export", "--regimes", "--private", "--public"):
        ap.add_argument(a, required=True)
    a = ap.parse_args(argv)
    priv, pub = Path(a.private), Path(a.public); priv.mkdir(parents=True, exist_ok=True); pub.mkdir(parents=True, exist_ok=True)
    sd, posts, cover, states, X = C.build(Path(a.export), STUDY_START, STUDY_END)
    rows = C.month_table(sd, posts, cover, states, STUDY_START, STUDY_END)
    with open(pub / "coverage_by_month.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, list(rows[0].keys())); w.writeheader(); [w.writerow(r) for r in rows]
    phase = {}
    with open(a.regimes, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            phase[r["date"]] = r["phase"]
    # signal-day table (counts only)
    by_day = defaultdict(list)
    for p in posts:
        if p["main"] and p["signal_day"]:
            by_day[p["signal_day"]].append(p)
    with open(pub / "coverage_by_signal_day.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["signal_day", "phase", "searched_layers", "window_states", "main_posts", "authors", "authors_regular_hours_only",
                    "bullish_posts", "bearish_posts", "neutral_posts", "failure_posts", "unlabelled_posts"])
        for d in sd.days:
            cv = cover.get(d)
            if not cv:
                continue
            pp = by_day.get(d.isoformat(), [])
            lc = Counter(p["label_class"] for p in pp)
            w.writerow([d, phase.get(d.isoformat(), "AFTER_PRICE_DATA_END"), ";".join(f"{k}:{v:.2f}" for k, v in sorted(cv.items())),
                        ";".join(f"{k}:{v}" for k, v in sorted(states[d].items())), len(pp), len({p["author_id"] for p in pp}),
                        len({p["author_id"] for p in pp if p["regular_hours"]}), lc["bullish"], lc["bearish"], lc["neutral"], lc["failure"], lc["unlabelled"]])
    # case examples
    windows = [(cid, kind, *month_bounds(m0, m1)) for cid, kind, m0, m1 in CASES]
    windows.append(("REF-2026-09", "ordinary_reference", date(2026, 9, 1), date(2026, 9, 30)))
    ex_rows, ex_counts = [], []
    for cid, kind, d0, d1 in windows:
        pool = [p for p in posts if p["main"] and p["signal_day"] and d0.isoformat() <= p["signal_day"] <= d1.isoformat()]
        for cls in CLASSES:
            cand = [p for p in pool if p["label_class"] == cls and (cls not in ("bullish", "bearish") or p["evidence_in_text"])]
            got = pick(cand)
            ex_counts.append({"case_id": cid, "owner_label": kind, "start": d0, "end": d1, "class": cls, "available_main_posts": len(cand),
                              "available_authors": len({p["author_id"] for p in cand}), "taken": len(got), "shortfall": K - len(got)})
            for p in got:
                ex_rows.append({"case_id": cid, "class": cls, "signal_day": p["signal_day"], "phase": phase.get(p["signal_day"], "AFTER_PRICE_DATA_END"),
                                "created_at_utc": p["created_at_utc"], "url": p["url"], "post_id": p["post_id"], "author_id": p["author_id"],
                                "kind": p["kind"], "layers": ";".join(p["layers"]), "ingest_mode": p["ingest_mode"], "label": p["label"],
                                "quality_status": p["quality_status"], "evidence_text": p["evidence_text"], "evidence_in_text": p["evidence_in_text"],
                                "raw_page_sha256": ";".join(p["raw_page_sha256"][:3]), "rank_key": rank(p["post_id"])[:16], "text": p["text"]})
    with open(priv / "case_examples_private.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, list(ex_rows[0].keys())); w.writeheader(); [w.writerow(r) for r in ex_rows]
    with open(pub / "case_examples_counts.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, list(ex_counts[0].keys())); w.writeheader(); [w.writerow(r) for r in ex_counts]
    with open(priv / "posts_private.jsonl", "w", encoding="utf-8") as f:
        for p in posts:
            f.write(json.dumps(p, ensure_ascii=False, sort_keys=True) + "\n")
    summ = {"protocol": C.PROTOCOL, "posts_qqq_topic": len(posts), "reasons": Counter(p["reason"] for p in posts),
            "main_by_label": Counter(p["label_class"] for p in posts if p["main"]),
            "main_by_layer": Counter(l for p in posts if p["main"] for l in p["layers"]),
            "main_by_source": Counter(p["source"] for p in posts if p["main"]),
            "main_by_ingest": Counter(p["ingest_mode"] for p in posts if p["main"]),
            "main_cashtag_vs_bare": Counter(("cashtag" if C.CASHTAG_QQQ.search(p["text"] or "") else "bare_only") for p in posts if p["main"]),
            "main_evidence_in_text": Counter(str(p["evidence_in_text"]) for p in posts if p["main"] and p["label_class"] in ("bullish", "bearish", "neutral")),
            "main_dup_text_posts": sum(1 for p in posts if p["main"] and p["dup_text_count"] > 1),
            "main_edited": Counter(p["edit_status"] for p in posts if p["main"]),
            "first_post_utc": min(p["created_at_utc"] for p in posts), "last_post_utc": max(p["created_at_utc"] for p in posts),
            "months_total": len(rows), "months_not_searched": sum(r["coverage_status"] == "NOT_SEARCHED" for r in rows),
            "export_meta": json.load(open(Path(a.export) / "export_meta.json", encoding="utf-8"))}
    (pub / "post_inventory_summary.json").write_text(json.dumps(summ, indent=1, default=str, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: summ[k] for k in ("main_by_label", "main_by_source", "main_by_ingest", "main_cashtag_vs_bare", "main_dup_text_posts", "main_edited", "months_not_searched")}, default=str))


if __name__ == "__main__":
    main()
