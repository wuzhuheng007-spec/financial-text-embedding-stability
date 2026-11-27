"""Steady-day projection from the incremental keyword requests only (other keyword runs were one-off planned
samples). Per-round averages come from the real rounds of 11-26 (Shanghai day) before collection stopped."""
import json, sys, collections
R = json.load(open(sys.argv[1])); emp = json.load(open(sys.argv[2]))["empirical_next_page_after_full"]["vocab-20261122-a"]
n_after, avg_next, full_next = emp; q = full_next / n_after
inc = [x for x in R if x["job_type"] == "recent" and (x["vocab_version"] or "").startswith("incremental") and x["budget_day"] == "2026-11-26"]
rounds = collections.defaultdict(list)
for x in inc: rounds[x["created_at_utc"][:13]].append(x)
k = len(rounds); per = lambda f: sum(f(x) for x in inc) / k
base_c = per(lambda x: x["settled_credits"] or 0); base_p = per(lambda x: x["returned"] or 0); full = per(lambda x: x["returned"] == 20 and x["run_cursor"])
by_topic = collections.Counter(x["topic_id"] for x in inc if x["returned"] == 20 and x["run_cursor"])
CAP = 36000; day = 12
out = {"rounds_used": k, "first_pages_per_round": round(len(inc) / k, 2), "full_first_pages_per_round": round(full, 2), "full_first_pages_by_topic_11_26": dict(by_topic),
       "next_page_after_full_observed": {"pairs": n_after, "mean_returned": avg_next, "share_full": round(q, 3)},
       "now_pages_1": {"posts_per_day": round(base_p * day), "credits_per_day": round(base_c * day), "usd_per_day": round(base_c * day / 1e5, 4), "share_of_keyword_cap": round(base_c * day / CAP, 3)}}
for name, extra_c, extra_p in (("pages_3_upper_all_full", full * 2 * 300, full * 2 * 20),
                               ("pages_3_expected", full * (avg_next * 15 + q * avg_next * 15), full * (avg_next + q * avg_next)),
                               ("pages_3_lower_all_empty", full * 15, 0)):
    want = (base_c + extra_c) * day; got = min(want, CAP)
    out[name] = {"credits_per_day_wanted": round(want), "credits_per_day_within_cap": round(got), "usd_per_day": round(got / 1e5, 4), "share_of_keyword_cap": round(got / CAP, 3), "cap_binds": want > CAP,
                 "posts_per_day_approx": round((base_p + extra_p) * day * (got / want if want else 1)), "extra_usd_per_day_vs_now": round((got - base_c * day) / 1e5, 4),
                 "rounds_before_cap": round(CAP / (base_c + extra_c), 1) if want > CAP else day}
print(json.dumps(out, ensure_ascii=False, indent=1))
