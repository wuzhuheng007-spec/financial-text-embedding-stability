"""Daily author sentiment, probability tables and the classifier audit table from the frozen private inputs.

python -m scripts.qqq_x_10y.build_signals --snapshot <market_research.sqlite> --private <dir> --public <dir>
Inputs: <private>/posts_private.jsonl (from build_receipt1), the price snapshot. Outputs (public, counts and rates only):
sentiment_daily.csv, out_of_sample_probabilities.csv, classifier_audit.csv, fig_qqq_sentiment_timeline.svg,
fig_up_probability_by_state.svg, signals_summary.json.
The posts held are the min_faves:20 query stratum of 2026-08-24..2026-11-27; they are reported as that stratum, not as the
main historical sample.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import date
from pathlib import Path

from . import forecast as F, prices, regimes as R, sentiment as S
from .build_regimes import STUDY_START


def fmt(x, nd=4):
    return "" if x is None else round(x, nd) if isinstance(x, float) else x


def svg_timeline(path, dates, close, srows, events):
    W, H, L, T, Bm = 900, 360, 60, 20, 40
    d0 = dates[0]; d1 = dates[-1]
    span = (d1 - d0).days or 1
    x = lambda d: L + (W - L - 20) * (d - d0).days / span
    lo, hi = min(close), max(close)
    yp = lambda v: T + (H / 2 - T - 10) * (1 - (v - lo) / (hi - lo or 1))
    ys = lambda p: H / 2 + 10 + (H / 2 - Bm - 10) * (1 - p)
    pts = " ".join(f"{x(d):.1f},{yp(c):.1f}" for d, c in zip(dates, close))
    base = close[0]
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="sans-serif" font-size="11">',
         '<style>.ax{stroke:#888;stroke-width:1}.px{fill:none;stroke:#2a6fdb;stroke-width:1.2}.dd{fill:#d9534f;opacity:.12}.pt{fill:#e08a00}.lb{fill:#444}</style>',
         f'<text class="lb" x="{L}" y="14">US.QQQ close, indexed to 100 at {d0} (moomoo a0) and author bull share p = B/(B+S) by signal day</text>']
    for e in events:
        a = max(d0, e[0]); b = min(d1, e[1] or d1)
        if b >= a:
            s.append(f'<rect class="dd" x="{x(a):.1f}" y="{T}" width="{max(1, x(b) - x(a)):.1f}" height="{H - T - Bm}"/>')
    s.append(f'<polyline class="px" points="{pts}"/>')
    s.append(f'<text class="lb" x="4" y="{yp(hi):.0f}">{100 * hi / base:.0f}</text><text class="lb" x="4" y="{yp(lo):.0f}">{100 * lo / base:.0f}</text>')
    s.append(f'<line class="ax" x1="{L}" x2="{W - 20}" y1="{ys(0.5):.1f}" y2="{ys(0.5):.1f}" stroke-dasharray="3 3"/>')
    for k, lab in ((0, "0"), (0.5, "0.5"), (1, "1")):
        s.append(f'<text class="lb" x="30" y="{ys(k) + 4:.0f}">{lab}</text>')
    for r in srows:
        if r["p"] is not None:
            d = date.fromisoformat(r["signal_day"])
            if d0 <= d <= d1:
                s.append(f'<circle class="pt" cx="{x(d):.1f}" cy="{ys(r["p"]):.1f}" r="{2.5 if r["eligible"] else 1.2}"/>')
    for yr in range(d0.year + 1, d1.year + 1):
        xx = x(date(yr, 1, 1)); s.append(f'<line class="ax" x1="{xx:.1f}" x2="{xx:.1f}" y1="{H - Bm}" y2="{H - Bm + 5}"/><text class="lb" x="{xx - 12:.1f}" y="{H - Bm + 18}">{yr}</text>')
    s.append(f'<text class="lb" x="{L}" y="{H - 6}">Shaded: drawdown events (trigger to rebound). Dots: p on signal days with posts (large = 10+ directional authors). No posts before 2026-08-24.</text></svg>')
    Path(path).write_text("\n".join(s), encoding="utf-8")


def svg_prob(path, rows):
    sel = [r for r in rows if r["h"] == F.MAIN_H and r["bucket"] == "ALL_DAYS"]
    W, H = 900, 300
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="sans-serif" font-size="11">',
         '<style>.b{fill:#2a6fdb}.e{stroke:#222}.lb{fill:#444}</style>',
         f'<text class="lb" x="10" y="14">Share of signal days followed by a {F.MAIN_H}-day rise (entry next open), by split and phase, all days (price only), 95% block-bootstrap interval</text>']
    bw = (W - 80) / max(1, len(sel)); y0 = H - 50
    for k, r in enumerate(sel):
        if r["p_up"] is None:
            continue
        xx = 60 + k * bw; hh = (y0 - 30) * r["p_up"]
        s.append(f'<rect class="b" x="{xx + 4:.1f}" y="{y0 - hh:.1f}" width="{bw - 8:.1f}" height="{hh:.1f}"/>')
        if r.get("ci_lo") is not None:
            a, b = y0 - (y0 - 30) * r["ci_lo"], y0 - (y0 - 30) * r["ci_hi"]
            s.append(f'<line class="e" x1="{xx + bw / 2:.1f}" x2="{xx + bw / 2:.1f}" y1="{a:.1f}" y2="{b:.1f}"/>')
        s.append(f'<text class="lb" x="{xx + 4:.1f}" y="{y0 + 14}">{r["split"]}</text><text class="lb" x="{xx + 4:.1f}" y="{y0 + 27}">{r["state"]}</text>'
                 f'<text class="lb" x="{xx + 4:.1f}" y="{y0 - hh - 4:.1f}">{r["up"]}/{r["signal_days"]}</text>')
    s.append(f'<text class="lb" x="10" y="{H - 6}">Sentiment cells are not drawn: no split has enough eligible sentiment days (all INSUFFICIENT).</text></svg>')
    Path(path).write_text("\n".join(s), encoding="utf-8")


def price_only_table(dates, opn, close, state_of, h):
    out = F.outcomes(dates, opn, close, h)
    rows = []
    for sp, _, _ in F.SPLITS:
        for st in ["REBOUND", "DRAWDOWN", "NARROW", "OTHER", "ALL"]:
            base = [i for i in out if F.split_of(dates[i]) == sp and (st == "ALL" or state_of.get(i) == st)]
            if not base:
                continue
            up = sum(F.direction(out[i]) == "up" for i in base)
            # interval of the share itself by moving blocks
            import random
            rng = random.Random(F.SEED); b = max(h, 20); nb = len(base); bs = sorted(base); vals = []
            for _ in range(F.DRAWS):
                pick = []
                while len(pick) < nb:
                    s0 = rng.randrange(nb); pick.extend(bs[(s0 + j) % nb] for j in range(b))
                pick = pick[:nb]; vals.append(sum(F.direction(out[i]) == "up" for i in pick) / nb)
            vals.sort()
            rows.append({"h": h, "split": sp, "state": st, "bucket": "ALL_DAYS", "signal_days": nb, "up": up, "p_up": up / nb,
                         "ci_lo": vals[int(0.025 * len(vals))], "ci_hi": vals[int(0.975 * len(vals)) - 1]})
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    for a in ("--snapshot", "--private", "--public"):
        ap.add_argument(a, required=True)
    a = ap.parse_args(argv)
    priv, pub = Path(a.private), Path(a.public)
    b = prices.load(a.snapshot)
    D = b.dates
    s = R.scan(D, b.close); lab = R.label_days(s, R.rolling_range(b.close, b.high, b.low, 63))
    pos = {d.isoformat(): i for i, d in enumerate(D)}
    posts = [json.loads(x) for x in open(priv / "posts_private.jsonl", encoding="utf-8")]
    main_posts = [p for p in posts if p["main"] and p["signal_day"]]
    daily = S.daily(main_posts)
    rows = sorted(daily.values(), key=lambda r: r["signal_day"])
    phase_of = {d.isoformat(): lab[i]["phase"] for i, d in enumerate(D)}
    S.add_baseline(rows, phase_of, [d.isoformat() for d in D])
    keys = ["signal_day", "phase", "B", "S", "N", "authors_voting", "authors_any", "p", "bucket", "boundary_S0", "missing_p", "B_to_S",
            "share_bull_of_all", "neutral_share", "failure_rate", "posts_bullish", "posts_bearish", "posts_neutral", "p_posts", "eligible",
            "narrow_baseline_n", "narrow_baseline_p", "p_dev_vs_narrow", "stratum"]
    with open(pub / "sentiment_daily.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, keys); w.writeheader()
        for r in rows:
            w.writerow({**{k: fmt(r.get(k)) for k in keys}, "phase": phase_of.get(r["signal_day"], "AFTER_PRICE_DATA_END"),
                        "stratum": "min_faves20_query_layer_2026-08..11 (not the main historical sample)"})
    # probability tables
    state_of = {i: lab[i]["phase"] for i in range(len(D))}
    bucket_of, eligible = {}, set()
    for r in rows:
        i = pos.get(r["signal_day"])
        if i is not None and r["bucket"]:
            bucket_of[i] = r["bucket"]
            if r["eligible"]:
                eligible.add(i)
    event_of = {i: s.flags[i]["event_id"] for i in range(len(D)) if s.flags[i]["event_id"]}
    i0 = pos[min(d.isoformat() for d in D if d >= STUDY_START)]
    sl = slice(i0, None)
    Dd, Do, Dc = D[sl], b.open[sl], b.close[sl]
    shift = lambda m: {i - i0: v for i, v in m.items() if i >= i0}
    tab = F.table(Dd, Do, Dc, shift(state_of), shift(bucket_of), shift(event_of), {i - i0 for i in eligible if i >= i0})
    po = [r for h in F.HORIZONS for r in price_only_table(Dd, Do, Dc, shift(state_of), h)]
    ok = [r for r in tab if r["signal_days"]]
    with open(pub / "out_of_sample_probabilities.csv", "w", newline="", encoding="utf-8") as f:
        k2 = list(tab[0].keys())
        w = csv.DictWriter(f, k2 + ["ci_lo", "ci_hi", "table"]); w.writeheader()
        for r in po:
            w.writerow({**{k: fmt(r.get(k)) for k in k2}, "ci_lo": fmt(r["ci_lo"]), "ci_hi": fmt(r["ci_hi"]), "table": "price_only_base_rate"})
        for r in tab:
            if r["signal_days"] or r["bucket"] == "P1":
                w.writerow({**{k: fmt(r.get(k)) for k in k2}, "table": "sentiment_bucket"})
    # classifier audit (what can be said without human labels)
    lc = Counter(p["label_class"] for p in main_posts)
    dirn = [p for p in main_posts if p["label_class"] in ("bullish", "bearish", "neutral")]
    audit = [{"item": "main-sample posts", "value": len(main_posts)},
             *[{"item": f"model label {k}", "value": v} for k, v in sorted(lc.items())],
             {"item": "model failure rate (failure / labelled)", "value": fmt(lc["failure"] / max(1, len(main_posts) - lc["unlabelled"]))},
             {"item": "directional labels whose evidence quote is verbatim in the text", "value": sum(p["evidence_in_text"] for p in dirn if p["label_class"] != "neutral")},
             {"item": "directional labels with a non-matching or empty quote", "value": sum(not p["evidence_in_text"] for p in dirn if p["label_class"] != "neutral")},
             {"item": "inference identities", "value": ";".join(f"{k[:12]}={v}" for k, v in Counter(p["inference_identity"] or "none" for p in main_posts).items())},
             {"item": "human blind review (double, adjudicated)", "value": "NOT DONE - needs two reviewers; precision/recall/confusion matrix pending"},
             {"item": "independent model re-label of key cells", "value": "NOT DONE - no key cell has enough days"},
             {"item": "post-training-cutoff subsample", "value": "all held posts are 2026-08..11; model qwen2.5-3b (2024 release) predates them"}]
    with open(pub / "classifier_audit.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, ["item", "value"]); w.writeheader(); [w.writerow(r) for r in audit]
    ev = [(D[e.trigger_i], D[e.rebound_i] if e.rebound_i is not None else None) for e in s.events if D[e.trigger_i] >= STUDY_START]
    svg_timeline(pub / "fig_qqq_sentiment_timeline.svg", Dd, Dc, rows, ev)
    svg_prob(pub / "fig_up_probability_by_state.svg", po)
    summ = {"signal_days_with_posts": len(rows), "eligible_days": sum(r["eligible"] for r in rows),
            "eligible_days_with_mature_10d_outcome": sum(1 for r in rows if r["eligible"] and pos.get(r["signal_day"]) is not None and pos[r["signal_day"]] + 10 < len(D)),
            "phase_of_eligible_days": Counter(phase_of.get(r["signal_day"], "AFTER_PRICE_DATA_END") for r in rows if r["eligible"]),
            "bucket_of_eligible_days": Counter(r["bucket"] for r in rows if r["eligible"]),
            "sentiment_cells_with_days": len(ok), "sentiment_cells_OK": sum(r["evidence"] == "OK" for r in ok),
            "price_only_main_h": [{k: fmt(r[k]) for k in ("split", "state", "signal_days", "up", "p_up", "ci_lo", "ci_hi")} for r in po if r["h"] == F.MAIN_H]}
    (pub / "signals_summary.json").write_text(json.dumps(summ, indent=1, default=str), encoding="utf-8")
    print(json.dumps(summ, default=str)[:3000])


if __name__ == "__main__":
    main()
