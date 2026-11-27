"""Rehearsal of pages_per_query 1 -> 3 for the keyword increment, replayed on the real ledger (11-25..11-26).
No request is sent. Money as in ledger.py: 15 credits per returned post, minimum 15 per request, a reservation of
300 credits (one full page) before each request, 100,000 credits per USD. A next page is asked only when the page
before it came back full (20) and the provider gave a cursor; the caps are checked before each page exactly as the
ledger does (settled spend plus the reservation must fit)."""
import json, sys, collections
R = json.load(open(sys.argv[1]))
CAP = {"kw_posts": 2400, "kw_credits": 36000, "recent_posts": 3000, "recent_credits": 45000, "all_posts": 5000, "all_credits": 75000}
cr = lambda n: 15 * max(n, 1)
# empirical fill of page k+1 after a full page k, from multi-page keyword runs that already exist in the ledger
runs = collections.defaultdict(list)
for x in R:
    if x["status"] == "SETTLED" and x["run_id"]: runs[x["run_id"]].append(x)
nxt = collections.defaultdict(list)
for rid, xs in runs.items():
    xs.sort(key=lambda x: x["created_at_utc"])
    if not xs[0]["job_type"] in ("recent",): continue
    for a, b in zip(xs, xs[1:]):
        if a["returned"] == 20: nxt[xs[0]["vocab_version"][:24]].append(b["returned"])
emp = {k: (len(v), round(sum(v) / len(v), 2), sum(1 for t in v if t == 20)) for k, v in nxt.items()}
inc = [x for x in R if x["job_type"] == "recent" and (x["vocab_version"] or "").startswith("incremental")]
def day_use(day, pred):
    xs = [x for x in R if x["budget_day"] == day and pred(x) and x["status"] in ("SETTLED", "UNKNOWN_HELD")]
    return sum(x["returned"] or 0 for x in xs), sum(x["settled_credits"] if x["settled_credits"] is not None else 300 for x in xs)
res = {"empirical_next_page_after_full": emp, "days": {}}
for day in sorted({x["budget_day"] for x in inc}):
    kw = (lambda x: x["job_type"] == "recent"); rec = (lambda x: x["pool"] == "recent"); al = (lambda x: True)
    used = {"kw": list(day_use(day, kw)), "recent": list(day_use(day, rec)), "all": list(day_use(day, al))}
    dx = [x for x in inc if x["budget_day"] == day]
    full = [x for x in dx if x["returned"] == 20 and x["run_cursor"]]
    out = {"incremental_requests": len(dx), "full_first_pages_with_cursor": len(full), "actual": {"kw_posts": used["kw"][0], "kw_credits": used["kw"][1], "all_credits": used["all"][1]}}
    for name, fill in (("upper_every_extra_page_full", lambda k: 20), ("empirical", lambda k: None), ("lower_every_extra_page_empty", lambda k: 0)):
        p = {"kw": used["kw"][:], "recent": used["recent"][:], "all": used["all"][:]}; asked = blocked = 0
        e = emp.get("vocab-20261122-a", (1, 20, 1)); avg, q_full = e[1], e[2] / e[0]
        for x in sorted(full, key=lambda x: x["created_at_utc"]):
            for k in (2, 3):
                if p["kw"][1] + 300 > CAP["kw_credits"] or p["kw"][0] + 20 > CAP["kw_posts"] or p["recent"][1] + 300 > CAP["recent_credits"] or p["all"][1] + 300 > CAP["all_credits"]:
                    blocked += 1; break
                if fill(k) is None:                      # expectation: page 2 always, page 3 with the observed chance that page 2 came back full
                    w = 1.0 if k == 2 else q_full; n = avg
                    asked += w
                    for key in p: p[key][0] += w * n; p[key][1] += w * cr(round(n))
                    continue
                n = fill(k); asked += 1
                for key in p: p[key][0] += n; p[key][1] += cr(n)
                if n < 20: break
        out[name] = {"extra_pages_asked": round(asked, 1), "pages_blocked_by_cap": blocked, "extra_posts": round(p["kw"][0] - used["kw"][0]), "extra_credits": p["kw"][1] - used["kw"][1],
                     "extra_usd": round((p["kw"][1] - used["kw"][1]) / 100000, 4), "kw_after": {"posts": round(p["kw"][0]), "credits": p["kw"][1], "share_of_kw_credit_cap": round(p["kw"][1] / CAP["kw_credits"], 3)},
                     "all_jobs_usd_after": round(p["all"][1] / 100000, 4)}
    rounds = len({x["created_at_utc"][:13] for x in dx})
    out["rounds_seen"] = rounds
    f = 12 / rounds if rounds else 1                      # a full Shanghai day has 12 two-hour rounds
    full_day = {}
    for name in ("upper_every_extra_page_full", "empirical", "lower_every_extra_page_empty"):
        base_c = used["kw"][1] * f; extra_c = out[name]["extra_credits"] * f
        allowed = max(0, min(extra_c, CAP["kw_credits"] - base_c))
        full_day[name] = {"kw_credits_without": round(base_c), "extra_credits": round(allowed), "extra_usd": round(allowed / 100000, 4), "kw_share_of_cap": round((base_c + allowed) / CAP["kw_credits"], 3),
                          "cap_binds": extra_c > CAP["kw_credits"] - base_c, "extra_posts_approx": round(allowed / 15)}
    out["scaled_to_12_rounds"] = full_day
    res["days"][day] = out
res["caps"] = CAP; res["note"] = "keyword sub-pool = job_type 'recent' (incremental and other keyword runs); watched authors have their own 600 / USD 0.09 share and are not touched"
print(json.dumps(res, ensure_ascii=False, indent=1))
