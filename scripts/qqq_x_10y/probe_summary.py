"""Summarise the probes from their private output (raw gzip pages + ledger), without any new request. Public output:
counts only. (protocol qqq-x-10y-probe-summary-v2)

python -m scripts.qqq_x_10y.probe_summary --plan <probe_plan.json> [--cal-plan <cal_plan.json>] --out <probe out dir>
       --public <dir> [--private <dir>]

Three ID levels per unit: ALL = every id returned; HOUR = ids whose created time is inside the unit's hour; MAIN = HOUR
and in the main sample by the frozen rules (English, original or quote, names QQQ, not a reply, retweet or ticker string).
The main report uses MAIN. For each month and level: ids found only by $QQQ, only by bare QQQ, by both, their union, and
the bare-only share of the union (NA when the union is 0).
Pairing: a month enters the main paired comparison only when both units are COMPLETE. If both ran and either is
TRUNCATED, the month is a PAIRED_LOWER_BOUND row (counts are observed minimums). If a unit is missing, failed or
uncertain, the month is HALF_MISSING and every comparison field is NA.
Authors: distinct authors count only non-empty author ids; records without one are counted apart.
Earliest result: reported as "the earliest month observed in this sample", not as the provider's start.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from . import coverage as C

LEVELS = ("ALL", "HOUR", "MAIN")
ERAS = (("2016-2019", "2016", "2019"), ("2020-2022", "2020", "2022"), ("2023-2026", "2023", "2026"))
BLIND_SEED = "qqq-x-10y-bare-blind-v1|"
BLIND_PER_ERA = 20


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


def author_id(t):
    a = t.get("author") or {}
    v = a.get("id") if isinstance(a, dict) else None
    return str(v) if v not in (None, "") else None


def read_pages(out: Path, unit_id: str) -> list[dict]:
    tw = []
    for f in sorted((out / "pages").glob(unit_id.replace(":", "_") + "_*.json.gz")):
        with gzip.open(f, "rt", encoding="utf-8") as g:
            tw.extend((json.load(g) or {}).get("tweets") or [])
    return tw


def unit_sets(tweets: list[dict], since: int, until: int) -> dict:
    """{level: {id: tweet}} for one unit; ids without a value are dropped and counted."""
    s, e = datetime.fromtimestamp(since, timezone.utc), datetime.fromtimestamp(until, timezone.utc)
    out = {k: {} for k in LEVELS}; no_id = 0
    for t in tweets:
        i = t.get("id")
        if i in (None, ""):
            no_id += 1; continue
        i = str(i); out["ALL"][i] = t
        c = created(t)
        if c and s <= c < e:
            out["HOUR"][i] = t
            if C.eligibility({"lang": t.get("lang"), "kind": kind_of(t), "text": t.get("text")})[0]:
                out["MAIN"][i] = t
    out["_no_id"] = no_id
    return out


def authors(d: dict) -> tuple[int, int]:
    ids = [author_id(t) for t in d.values()]
    return len({a for a in ids if a}), sum(1 for a in ids if not a)


def compare(cash: dict | None, bare: dict | None, cash_status: str | None, bare_status: str | None) -> dict:
    """One month, one level. cash/bare: {id: tweet} or None when the unit has no usable result."""
    ok = {"COMPLETE", "TRUNCATED"}
    if cash_status not in ok or bare_status not in ok or cash is None or bare is None:
        return {"pairing": "HALF_MISSING", "cash_only": None, "bare_only": None, "both": None, "union": None, "bare_new_share": None,
                "union_authors": None, "union_missing_author": None}
    pairing = "PAIRED_COMPLETE" if cash_status == bare_status == "COMPLETE" else "PAIRED_LOWER_BOUND"
    a, b = set(cash), set(bare)
    u = a | b
    merged = {**bare, **cash}
    na, nm = authors({i: merged[i] for i in u})
    return {"pairing": pairing, "cash_only": len(a - b), "bare_only": len(b - a), "both": len(a & b), "union": len(u),
            "bare_new_share": (len(b - a) / len(u)) if u else None, "union_authors": na, "union_missing_author": nm}


def na(x, nd=4):
    return "NA" if x is None else round(x, nd) if isinstance(x, float) else x


def summarise(units: list[dict], out: Path, ust: dict) -> tuple[list[dict], dict, dict]:
    by_month = defaultdict(dict)
    sets = {}
    for u in units:
        st = ust.get(u["unit_id"], ("NOT_RUN", 0, None))[0]
        sets[u["unit_id"]] = unit_sets(read_pages(out, u["unit_id"]), u["since_time"], u["until_time"]) if st in ("COMPLETE", "TRUNCATED") else None
        by_month[u["month"]][u["query_id"]] = (u, st)
    rows = []
    for m in sorted(by_month):
        (cu, cs), (bu, bs) = by_month[m].get("Q_CASH", (None, None)), by_month[m].get("Q_BARE", (None, None))
        row = {"month": m, "day": (cu or bu)["day"], "cash_status": cs, "bare_status": bs}
        for lev in LEVELS:
            c = sets.get(cu["unit_id"]) if cu else None; b = sets.get(bu["unit_id"]) if bu else None
            r = compare(c and c[lev], b and b[lev], cs, bs)
            row["pairing"] = r["pairing"]
            for k in ("cash_only", "bare_only", "both", "union", "bare_new_share", "union_authors", "union_missing_author"):
                row[f"{lev.lower()}_{k}"] = r[k]
        # bare-only MAIN posts split by what the text holds: the cashtag (the $QQQ query missed a post it should match:
        # provider recall) or only the bare word (a real spelling gain); the same split the other way for cash-only
        if row["pairing"] != "HALF_MISSING":
            c, b = sets[cu["unit_id"]]["MAIN"], sets[bu["unit_id"]]["MAIN"]
            bo = [b[i] for i in set(b) - set(c)]; co = [c[i] for i in set(c) - set(b)]
            row["main_bare_only_text_has_cashtag"] = sum(1 for t in bo if C.CASHTAG_QQQ.search(t.get("text") or ""))
            row["main_bare_only_bare_word_only"] = len(bo) - row["main_bare_only_text_has_cashtag"]
            row["main_cash_only_text_has_bare_word"] = sum(1 for t in co if C.BARE_QQQ.search(t.get("text") or ""))
            u = row["main_union"]
            row["main_bare_word_gain_share"] = row["main_bare_only_bare_word_only"] / u if u else None
        else:
            for k in ("main_bare_only_text_has_cashtag", "main_bare_only_bare_word_only", "main_cash_only_text_has_bare_word", "main_bare_word_gain_share"):
                row[k] = None
        row["records_without_id"] = sum((sets.get(x["unit_id"]) or {}).get("_no_id", 0) for x in (cu, bu) if x)
        rows.append(row)
    years = defaultdict(list)
    for r in rows:
        years[r["month"][:4]].append(r)
    by_year = {}
    for y, rr in sorted(years.items()):
        comp = [r for r in rr if r["pairing"] == "PAIRED_COMPLETE"]
        un = sum(r["main_union"] for r in comp); bo = sum(r["main_bare_only"] for r in comp)
        by_year[y] = {"planned_months": len(rr), "paired_complete": len(comp), "paired_lower_bound": sum(r["pairing"] == "PAIRED_LOWER_BOUND" for r in rr),
                      "half_missing": sum(r["pairing"] == "HALF_MISSING" for r in rr),
                      "main_union_mean_per_hour": round(un / len(comp), 1) if comp else None,
                      "main_cash_mean_per_hour": round(sum(r["main_cash_only"] + r["main_both"] for r in comp) / len(comp), 1) if comp else None,
                      "main_bare_only_share": round(bo / un, 3) if un else None,
                      "main_bare_word_gain_share": round(sum(r["main_bare_only_bare_word_only"] for r in comp) / un, 3) if un else None,
                      "main_cash_query_recall_gap_share": round(sum(r["main_bare_only_text_has_cashtag"] for r in comp) / un, 3) if un else None,
                      "main_union_authors_mean": round(sum(r["main_union_authors"] for r in comp) / len(comp), 1) if comp else None}
    return rows, by_year, sets


def blind_sheet(units, sets, ust):
    """Bare-only MAIN posts by era, mixed with the same number of posts found by both queries as controls, shuffled by a
    fixed hash; the sheet shows only an item number and the text."""
    pool = defaultdict(lambda: {"bare_only": [], "both": []})
    by_m = defaultdict(dict)
    for u in units:
        by_m[u["month"]][u["query_id"]] = u
    for m, q in by_m.items():
        if not all(ust.get(q[k]["unit_id"], ("",))[0] == "COMPLETE" for k in ("Q_CASH", "Q_BARE") if k in q) or len(q) < 2:
            continue
        c, b = sets[q["Q_CASH"]["unit_id"]]["MAIN"], sets[q["Q_BARE"]["unit_id"]]["MAIN"]
        era = next(e for e, a, z in ERAS if a <= m[:4] <= z)
        pool[era]["bare_only"] += [(i, b[i]) for i in set(b) - set(c)]
        pool[era]["both"] += [(i, c[i]) for i in set(b) & set(c)]
    items = []
    rank = lambda i: hashlib.sha256((BLIND_SEED + i).encode()).hexdigest()
    for era, _, _ in ERAS:
        bo = sorted(pool[era]["bare_only"], key=lambda x: rank(x[0]))[:BLIND_PER_ERA]
        bt = sorted(pool[era]["both"], key=lambda x: rank("ctl|" + x[0]))[:len(bo)]
        items += [(era, "bare_only", i, t) for i, t in bo] + [(era, "both", i, t) for i, t in bt]
    items.sort(key=lambda x: rank("order|" + x[2]))
    sheet = [{"item": k + 1, "text": (t.get("text") or "").replace("\r", " ")} for k, (_, _, _, t) in enumerate(items)]
    key = [{"item": k + 1, "era": e, "source": s, "post_id": i, "created": t.get("createdAt"), "url": t.get("url") or t.get("twitterUrl")}
           for k, (e, s, i, t) in enumerate(items)]
    avail = {era: {k: len(v) for k, v in pool[era].items()} for era, _, _ in ERAS}
    return sheet, key, avail


def cal_profile(cal_units, sets, ust):
    """Per calibration day: union MAIN posts per ET hour, the day total and the 11:00 hour; observed only."""
    by = defaultdict(lambda: defaultdict(dict))
    for u in cal_units:
        by[u["day"]][u["hour_et"]][u["query_id"]] = u
    out = {}
    for d, hours in sorted(by.items()):
        row, total, complete = {}, 0, True
        for h in range(24):
            q = hours.get(h, {})
            sts = [ust.get(q[k]["unit_id"], ("NOT_RUN",))[0] for k in ("Q_CASH", "Q_BARE") if k in q]
            if len(sts) < 2 or any(s not in ("COMPLETE", "TRUNCATED") for s in sts):
                row[h] = None; complete = False; continue
            if any(s == "TRUNCATED" for s in sts):
                complete = False
            u = set(sets[q["Q_CASH"]["unit_id"]]["MAIN"]) | set(sets[q["Q_BARE"]["unit_id"]]["MAIN"])
            row[h] = len(u); total += len(u)
        out[d] = {"hours_complete": sum(1 for v in row.values() if v is not None), "all_24_complete": complete,
                  "main_union_day_observed": total, "main_union_11h": row.get(11),
                  "ratio_day_to_11h": round(total / row[11], 2) if complete and row.get(11) else None, "by_hour": row}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    for a in ("--plan", "--out", "--public"):
        ap.add_argument(a, required=True)
    ap.add_argument("--cal-plan"); ap.add_argument("--private")
    a = ap.parse_args(argv)
    out, pub = Path(a.out), Path(a.public); pub.mkdir(parents=True, exist_ok=True)
    units = json.loads(Path(a.plan).read_text(encoding="utf-8"))
    db = sqlite3.connect(f"file:{(out / 'probe_ledger.sqlite3').as_posix()}?mode=ro", uri=True)
    ust = {r[0]: r[1:] for r in db.execute("SELECT unit_id, status, pages, stop_reason FROM probe_units")}
    cols = {r[1] for r in db.execute("PRAGMA table_info(probe_requests)")}
    pl = "plan" if "plan" in cols else "'main'"
    led = {p: {"requests": n, "credits": c, "tweets": t} for p, n, c, t in
           db.execute(f"SELECT {pl}, COUNT(*), COALESCE(SUM(credits),0), COALESCE(SUM(tweets),0) FROM probe_requests GROUP BY 1")}
    errs = db.execute("SELECT status, http, COUNT(*) FROM probe_requests WHERE status != 'OK' GROUP BY 1, 2").fetchall()
    rows, by_year, sets = summarise(units, out, ust)
    with open(pub / "probe_by_month.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, list(rows[0].keys())); w.writeheader(); [w.writerow({k: na(v) for k, v in r.items()}) for r in rows]
    with open(pub / "probe_by_year.csv", "w", newline="", encoding="utf-8") as f:
        k0 = list(next(iter(by_year.values())).keys())
        w = csv.DictWriter(f, ["year"] + k0); w.writeheader(); [w.writerow({"year": y, **{k: na(v) for k, v in r.items()}}) for y, r in by_year.items()]
    first = next((r["month"] for r in rows if r["pairing"] != "HALF_MISSING" and (r["main_union"] or 0) > 0), None)
    summ = {"protocol": "qqq-x-10y-probe-summary-v2", "ledger_by_plan": led, "requests_total": sum(v["requests"] for v in led.values()),
            "credits_total": sum(v["credits"] for v in led.values()), "usd_total": round(sum(v["credits"] for v in led.values()) / 100000, 4),
            "non_ok_requests": errs,
            "main_units": {k: sum(1 for u in units if ust.get(u["unit_id"], ("NOT_RUN",))[0] == k) for k in ("COMPLETE", "TRUNCATED", "FAILED", "UNCERTAIN", "OPEN", "NOT_RUN")},
            "months": {k: sum(1 for r in rows if r["pairing"] == k) for k in ("PAIRED_COMPLETE", "PAIRED_LOWER_BOUND", "HALF_MISSING")},
            "earliest_month_observed_in_this_sample": first, "records_without_id": sum(r["records_without_id"] for r in rows),
            "main_missing_author_ids": sum(r["main_union_missing_author"] or 0 for r in rows)}
    if a.cal_plan:
        cu = json.loads(Path(a.cal_plan).read_text(encoding="utf-8"))
        cs = {u["unit_id"]: unit_sets(read_pages(out, u["unit_id"]), u["since_time"], u["until_time"]) if ust.get(u["unit_id"], ("",))[0] in ("COMPLETE", "TRUNCATED") else None for u in cu}
        prof = cal_profile(cu, cs, ust)
        summ["calibration"] = {d: {k: v for k, v in p.items() if k != "by_hour"} for d, p in prof.items()}
        summ["calibration_units"] = {k: sum(1 for u in cu if ust.get(u["unit_id"], ("NOT_RUN",))[0] == k) for k in ("COMPLETE", "TRUNCATED", "FAILED", "UNCERTAIN", "OPEN", "NOT_RUN")}
        with open(pub / "calibration_by_hour.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f); w.writerow(["day"] + [f"h{h:02d}" for h in range(24)] + ["day_total_observed", "all_24_complete"])
            for d, p in prof.items():
                w.writerow([d] + [na(p["by_hour"][h]) for h in range(24)] + [p["main_union_day_observed"], int(p["all_24_complete"])])
    summ["by_year"] = by_year
    if a.private:
        sheet, key, avail = blind_sheet(units, sets, ust)
        pv = Path(a.private); pv.mkdir(parents=True, exist_ok=True)
        with open(pv / "bare_blind_sheet.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, ["item", "text"]); w.writeheader(); [w.writerow(r) for r in sheet]
        with open(pv / "bare_blind_key.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, list(key[0].keys())); w.writeheader(); [w.writerow(r) for r in key]
        summ["blind_pool_by_era"] = avail; summ["blind_items"] = len(sheet)
    (pub / "probe_summary.json").write_text(json.dumps(summ, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in summ.items() if k not in ("by_year",)}, indent=1)[:4000])


if __name__ == "__main__":
    main()
