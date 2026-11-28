"""Summarise the probe from its private output (raw pages + ledger). Public output: counts only.

python -m scripts.qqq_x_10y.probe_summary --plan <probe_plan.json> --out <probe out dir> --public <dir>
Per unit: pages, posts returned, unique ids, posts whose created time is inside the hour, whether the 10-page limit was
hit. Per month: $QQQ vs bare-QQQ counts, ids found by both, by one only; main-sample share (English original or quote
naming QQQ, by the frozen rules). Per year: mean posts in the 11:00-12:00 ET hour and its ratio to 2026.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from . import coverage as C


def created(t):
    v = t.get("createdAt") or t.get("created_at") or ""
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            d = datetime.strptime(v, fmt)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def kind_of(t):
    if t.get("retweeted_tweet"):
        return "retweet"
    if t.get("isReply") or t.get("inReplyToId"):
        return "reply"
    if t.get("quoted_tweet"):
        return "quote"
    return "original"


def main(argv=None):
    ap = argparse.ArgumentParser()
    for a in ("--plan", "--out", "--public"):
        ap.add_argument(a, required=True)
    a = ap.parse_args(argv)
    out, pub = Path(a.out), Path(a.public); pub.mkdir(parents=True, exist_ok=True)
    units = {u["unit_id"]: u for u in json.loads(Path(a.plan).read_text(encoding="utf-8"))}
    db = sqlite3.connect(f"file:{(out / 'probe_ledger.sqlite3').as_posix()}?mode=ro", uri=True)
    ust = {r[0]: r[1:] for r in db.execute("SELECT unit_id, status, pages, stop_reason FROM probe_units")}
    led = db.execute("SELECT COUNT(*), COALESCE(SUM(credits),0), COALESCE(SUM(tweets),0) FROM probe_requests").fetchone()
    errs = db.execute("SELECT status, http, COUNT(*) FROM probe_requests WHERE status != 'OK' GROUP BY 1, 2").fetchall()
    ids = defaultdict(dict)
    per_unit = {}
    for uid, u in units.items():
        files = sorted((out / "pages").glob(uid.replace(":", "_") + "_*.json.gz"))
        tw = []
        for f in files:
            with gzip.open(f, "rt", encoding="utf-8") as g:
                tw.extend((json.load(g) or {}).get("tweets") or [])
        s, e = datetime.fromtimestamp(u["since_time"], timezone.utc), datetime.fromtimestamp(u["until_time"], timezone.utc)
        uniq = {}
        for t in tw:
            if t.get("id"):
                uniq[str(t["id"])] = t
        inside = [t for t in uniq.values() if (c := created(t)) and s <= c < e]
        main = [t for t in inside if C.eligibility({"lang": t.get("lang"), "kind": kind_of(t), "text": t.get("text")})[0]]
        st = ust.get(uid, ("NOT_RUN", 0, None))
        per_unit[uid] = {"month": u["month"], "day": u["day"], "query_id": u["query_id"], "status": st[0], "pages": st[1], "stop_reason": st[2],
                         "returned": len(tw), "unique": len(uniq), "inside_hour": len(inside), "main_sample": len(main),
                         "authors_inside": len({(t.get("author") or {}).get("id") for t in inside})}
        for i in uniq:
            ids[u["month"]].setdefault(i, set()).add(u["query_id"])
    months = sorted({u["month"] for u in units.values()})
    rows = []
    for m in months:
        c, b = per_unit.get(f"{m}:Q_CASH", {}), per_unit.get(f"{m}:Q_BARE", {})
        both = sum(1 for v in ids[m].values() if v == {"Q_CASH", "Q_BARE"})
        rows.append({"month": m, "day": c.get("day"), "cash_status": c.get("status"), "cash_pages": c.get("pages"), "cash_inside_hour": c.get("inside_hour"),
                     "cash_main_sample": c.get("main_sample"), "cash_authors": c.get("authors_inside"),
                     "bare_status": b.get("status"), "bare_pages": b.get("pages"), "bare_inside_hour": b.get("inside_hour"),
                     "bare_main_sample": b.get("main_sample"), "bare_authors": b.get("authors_inside"),
                     "ids_both": both, "ids_cash_only": sum(1 for v in ids[m].values() if v == {"Q_CASH"}),
                     "ids_bare_only": sum(1 for v in ids[m].values() if v == {"Q_BARE"})})
    with open(pub / "probe_by_month.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, list(rows[0].keys())); w.writeheader(); [w.writerow(r) for r in rows]
    years = defaultdict(list)
    for r in rows:
        if r["cash_status"] in ("COMPLETE", "TRUNCATED"):
            years[r["month"][:4]].append(r)
    by_year = {}
    for y, rr in sorted(years.items()):
        cash = [r["cash_inside_hour"] for r in rr]; bare = [r["bare_inside_hour"] or 0 for r in rr if r["bare_status"] in ("COMPLETE", "TRUNCATED")]
        by_year[y] = {"months_run": len(rr), "cash_mean_per_hour": round(sum(cash) / len(cash), 1), "cash_months_truncated": sum(r["cash_status"] == "TRUNCATED" for r in rr),
                      "bare_mean_per_hour": round(sum(bare) / len(bare), 1) if bare else None,
                      "union_mean_per_hour": round(sum(r["ids_both"] + r["ids_cash_only"] + r["ids_bare_only"] for r in rr) / len(rr), 1),
                      "bare_only_share_of_union": round(sum(r["ids_bare_only"] for r in rr) / max(1, sum(r["ids_both"] + r["ids_cash_only"] + r["ids_bare_only"] for r in rr)), 3),
                      "cash_main_share": round(sum(r["cash_main_sample"] or 0 for r in rr) / max(1, sum(cash)), 3)}
    first_nonzero = next((r["month"] for r in rows if (r["cash_inside_hour"] or 0) > 0), None)
    summ = {"protocol": "qqq-x-10y-probe-v1", "requests": led[0], "credits": led[1], "usd": round(led[1] / 100000, 4), "tweets_returned": led[2],
            "errors": errs, "units": {k: sum(1 for v in per_unit.values() if v["status"] == k) for k in ("COMPLETE", "TRUNCATED", "FAILED", "OPEN", "NOT_RUN")},
            "first_month_with_cash_results": first_nonzero, "by_year": by_year}
    (pub / "probe_summary.json").write_text(json.dumps(summ, indent=1), encoding="utf-8")
    print(json.dumps(summ, indent=1))


if __name__ == "__main__":
    main()
