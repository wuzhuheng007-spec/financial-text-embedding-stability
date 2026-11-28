"""Forward outcomes and probability tables (protocol qqq-x-10y-forecast-v1, frozen 2026-11-28 before returns were joined).

Signal at trading day t (cut-off: t's regular close). Entry at the regular open of t+1, exit at the close of the h-th
trading day after t: r(t, h) = close[t+h] / open[t+1] - 1, h in 1, 3, 5, 10, 20, 30; main h = 10. Price return on the
split-comparable series; the dividend-inclusive variant uses the adjusted series' close/open ratio.
Up: r > +0.1%; down: r < -0.1%; flat otherwise. An outcome exists only when t+h is within the data (no immature labels).
Splits by signal date: DEV 2016-11-28..2021-12-31, VAL 2022-01-01..2023-12-31, TEST 2024-01-01..end. A label whose holding
days [t+1, t+h] reach into the next split is dropped (purge), so no outcome crosses a boundary.
Per cell (split, state, bucket, h): signal days n, up/down/flat, P(up), mean and median r, base rate = P(up) over ALL days of
the same split and state with a mature outcome (sentiment or not), difference in percentage points, 95% interval of the
difference by a moving-block bootstrap over calendar-ordered days (block = max(h, 20) trading days, 2000 draws, seed 20261128),
distinct event rounds, and a non-overlapping variant that keeps every h-th signal day.
"""
from __future__ import annotations

import random
from datetime import date
from statistics import mean, median

PROTOCOL = "qqq-x-10y-forecast-v1"
HORIZONS = (1, 3, 5, 10, 20, 30)
MAIN_H = 10
FLAT = 0.001
SPLITS = (("DEV", date(2016, 11, 28), date(2021, 12, 31)), ("VAL", date(2022, 1, 1), date(2023, 12, 31)), ("TEST", date(2024, 1, 1), date(2100, 1, 1)))
SEED = 20261128
DRAWS = 2000


def split_of(d: date) -> str | None:
    for n, a, b in SPLITS:
        if a <= d <= b:
            return n
    return None


def outcomes(dates, opn, close, h):
    """{t_index: r} for mature, non-crossing outcomes."""
    out = {}
    for t in range(len(dates) - 1):
        if t + h >= len(dates):
            break
        s0, s1 = split_of(dates[t]), split_of(dates[t + h])
        if s0 is None or s0 != s1:
            continue
        out[t] = close[t + h] / opn[t + 1] - 1
    return out


def direction(r: float) -> str:
    return "up" if r > FLAT else "down" if r < -FLAT else "flat"


def block_bootstrap_diff(cell_idx, base_idx, up_of, block, draws=DRAWS, seed=SEED):
    """95% interval of P(up | cell) - P(up | base) resampling blocks of consecutive base days (cell days are a subset)."""
    if not cell_idx or not base_idx:
        return None, None
    base_sorted = sorted(base_idx)
    cell = set(cell_idx)
    nb = len(base_sorted)
    rng = random.Random(seed)
    diffs = []
    k = max(1, nb // block + (nb % block > 0))
    for _ in range(draws):
        pick = []
        for _ in range(k):
            s = rng.randrange(nb)
            pick.extend(base_sorted[(s + j) % nb] for j in range(block))
        pick = pick[:nb]
        c = [i for i in pick if i in cell]
        if not c:
            continue
        diffs.append(sum(up_of[i] for i in c) / len(c) - sum(up_of[i] for i in pick) / len(pick))
    if len(diffs) < draws * 0.5:
        return None, None
    diffs.sort()
    return diffs[int(0.025 * len(diffs))], diffs[int(0.975 * len(diffs)) - 1]


def table(dates, opn, close, state_of, bucket_of, event_of, eligible, horizons=HORIZONS):
    """state_of/bucket_of/event_of: {index: value}; eligible: set of indices with an eligible sentiment row."""
    rows = []
    for h in horizons:
        out = outcomes(dates, opn, close, h)
        up_of = {i: int(direction(r) == "up") for i, r in out.items()}
        for sp, _, _ in SPLITS:
            for st in sorted({state_of.get(i) for i in out if split_of(dates[i]) == sp} - {None}) + ["ALL"]:
                base = [i for i in out if split_of(dates[i]) == sp and (st == "ALL" or state_of.get(i) == st)]
                base_rate = sum(up_of[i] for i in base) / len(base) if base else None
                for bk in ("P1", "P2", "P3", "P4"):
                    cell = [i for i in base if i in eligible and bucket_of.get(i) == bk]
                    n = len(cell)
                    rs = [out[i] for i in cell]
                    d = [direction(r) for r in rs]
                    pu = d.count("up") / n if n else None
                    lo, hi = block_bootstrap_diff(cell, base, up_of, max(h, 20)) if n >= 2 else (None, None)
                    nonov, last = [], None
                    for i in sorted(cell):
                        if last is None or i - last >= h:
                            nonov.append(i); last = i
                    rows.append({"h": h, "split": sp, "state": st, "bucket": bk, "signal_days": n, "up": d.count("up"), "down": d.count("down"),
                                 "flat": d.count("flat"), "p_up": pu, "mean_r": mean(rs) if rs else None, "median_r": median(rs) if rs else None,
                                 "base_days": len(base), "base_up": sum(up_of[i] for i in base), "base_rate": base_rate,
                                 "diff_pp": (100 * (pu - base_rate)) if pu is not None and base_rate is not None else None,
                                 "ci95_lo_pp": None if lo is None else 100 * lo, "ci95_hi_pp": None if hi is None else 100 * hi,
                                 "event_rounds": len({event_of[i] for i in cell if event_of.get(i)}),
                                 "nonoverlap_days": len(nonov), "nonoverlap_up": sum(up_of[i] for i in nonov),
                                 "evidence": "INSUFFICIENT" if n < 30 or len({(dates[i].year, dates[i].month) for i in cell}) < 6 else "OK"})
    return rows


def brier(probs, ys):
    return sum((p - y) ** 2 for p, y in zip(probs, ys)) / len(ys) if ys else None
