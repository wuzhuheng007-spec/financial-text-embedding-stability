"""Author-level daily sentiment (protocol qqq-x-10y-sentiment-v1, frozen 2026-11-28 before any return was joined).

One vote per author per signal window. The author's direction is the majority of their bullish / bearish / neutral posts in
the window; a tie between the top classes is neutral. Irrelevant, cannot-tell, failure and unlabelled posts do not vote;
an author with only such posts is not counted, but their posts enter the failure rate.
B, S, N = bullish, bearish, neutral authors. p = B / (B + S); S = 0 with B > 0 is flagged as the boundary case; B + S = 0
means p is missing. Fixed p buckets: P1 [0, 1/3), P2 [1/3, 1/2), P3 [1/2, 2/3), P4 [2/3, 1] (2:1 falls in P4).
Also reported: B:S, B / (B + S + N), neutral share, author count, failure rate, the same by posts, and the deviation of p
from the mean p of earlier NARROW-phase signal days (lookback 252 trading days, at least 20 such days, else missing).
A day enters forecasting only with at least MIN_DIRECTIONAL_AUTHORS = 10 directional (B + S) authors; the value was set
from the coverage of the data held (every covered day has 10 or more) and the standard error of p at n = 10 (about 0.16,
below the widest bucket), before any return was looked at.
"""
from __future__ import annotations

from collections import Counter, defaultdict

PROTOCOL = "qqq-x-10y-sentiment-v1"
MIN_DIRECTIONAL_AUTHORS = 10
BUCKETS = (("P1", 0.0, 1 / 3), ("P2", 1 / 3, 1 / 2), ("P3", 1 / 2, 2 / 3), ("P4", 2 / 3, 1.0000001))
DIRS = ("bullish", "bearish", "neutral")
BASE_LOOKBACK, BASE_MIN = 252, 20


def author_vote(classes: list[str]) -> str | None:
    c = Counter(x for x in classes if x in DIRS)
    if not c:
        return None
    top = max(c.values())
    lead = [k for k in DIRS if c[k] == top]
    return lead[0] if len(lead) == 1 else "neutral"


def bucket(p: float | None) -> str | None:
    if p is None:
        return None
    for name, lo, hi in BUCKETS:
        if lo <= p < hi:
            return name
    raise ValueError(p)


def daily(posts: list[dict]) -> dict:
    """posts: dicts with signal_day, author_id, label_class. Returns {signal_day: row}."""
    by = defaultdict(lambda: defaultdict(list))
    for p in posts:
        by[p["signal_day"]][p["author_id"]].append(p["label_class"])
    out = {}
    for d, authors in by.items():
        votes = Counter(v for v in (author_vote(c) for c in authors.values()) if v)
        B, S, N = votes["bullish"], votes["bearish"], votes["neutral"]
        posts_c = Counter(x for c in authors.values() for x in c)
        attempted = sum(v for k, v in posts_c.items() if k != "unlabelled")
        pb, ps = posts_c["bullish"], posts_c["bearish"]
        p = B / (B + S) if B + S else None
        out[d] = {"signal_day": d, "B": B, "S": S, "N": N, "authors_voting": B + S + N, "authors_any": len(authors),
                  "p": p, "bucket": bucket(p), "boundary_S0": int(S == 0 and B > 0), "missing_p": int(B + S == 0),
                  "B_to_S": f"{B}:{S}", "share_bull_of_all": B / (B + S + N) if B + S + N else None,
                  "neutral_share": N / (B + S + N) if B + S + N else None,
                  "failure_rate": posts_c["failure"] / attempted if attempted else None,
                  "posts_bullish": pb, "posts_bearish": ps, "posts_neutral": posts_c["neutral"],
                  "p_posts": pb / (pb + ps) if pb + ps else None,
                  "eligible": int(B + S >= MIN_DIRECTIONAL_AUTHORS)}
    return out


def add_baseline(rows: list[dict], phase_of: dict, trading_days: list) -> None:
    """In place: p_dev_vs_narrow = p - mean p of earlier NARROW days within BASE_LOOKBACK trading days."""
    pos = {d: i for i, d in enumerate(trading_days)}
    hist = []  # (index, p) of eligible NARROW days
    for r in sorted(rows, key=lambda r: r["signal_day"]):
        i = pos.get(r["signal_day"])
        prior = [p for j, p in hist if i is not None and i - BASE_LOOKBACK <= j < i]
        r["narrow_baseline_n"] = len(prior)
        r["narrow_baseline_p"] = sum(prior) / len(prior) if len(prior) >= BASE_MIN else None
        r["p_dev_vs_narrow"] = (r["p"] - r["narrow_baseline_p"]) if r["p"] is not None and r["narrow_baseline_p"] is not None else None
        if i is not None and r["eligible"] and r["p"] is not None and phase_of.get(r["signal_day"]) == "NARROW":
            hist.append((i, r["p"]))
