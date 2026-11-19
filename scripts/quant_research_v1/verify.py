"""Independent re-computation from a saved ledger (separate code path from engine.py).

It re-derives every daily return from the ledger's prices, positions and cost rate (not from the
engine's return columns), checks the execution timing rule, recomputes NAV and the metrics, and
compares them with metrics.json. Usage: python -m scripts.quant_research_v1.verify RUN_DIR
"""
from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

TOL = 1e-9


def _f(v):
    return None if v == "" else float(v)


def recompute(rows: list[dict], cost_bps: float, segments: list[dict], ann: int, min_days: int) -> dict:
    c = cost_bps / 10000.0
    problems, nav = [], 1.0
    daily, bdaily = [], []
    prev_after = 0.0
    bnav, b_prev = 1.0, 0.0
    for k, r in enumerate(rows):
        f_prev = r["factor_prev_close"]
        want = 1.0 if (f_prev is not None and f_prev > 0) else 0.0
        if r["position_after_open"] != want:
            problems.append(f"{r['date']}: position after open does not follow the previous close's signal")
        if r["position_overnight"] != prev_after:
            problems.append(f"{r['date']}: overnight position is not yesterday's post-open position")
        if r["exec_price"] != r["open"]:
            problems.append(f"{r['date']}: execution price is not the open")
        if k and rows[k - 1]["close"] != r["prev_close"]:
            problems.append(f"{r['date']}: prev_close does not chain to the previous row")
        if k and rows[k - 1]["target_next_open"] != r["position_after_open"]:
            problems.append(f"{r['date']}: yesterday's target is not today's executed position")
        turn = abs(r["position_after_open"] - r["position_overnight"])
        on = r["open"] / r["prev_close"] - 1
        intra = r["close"] / r["open"] - 1
        net = (1 + r["position_overnight"] * on) * (1 - turn * c) * (1 + r["position_after_open"] * intra) - 1
        bturn = abs(1.0 - b_prev)
        bnet = (1 + b_prev * on) * (1 - bturn * c) * (1 + intra) - 1
        nav *= 1 + net
        bnav *= 1 + bnet
        for name, mine, theirs in (("net_return", net, r["net_return"]), ("nav", nav, r["nav"]),
                                   ("turnover", turn, r["turnover"]), ("benchmark_nav", bnav, r["benchmark_nav"])):
            if abs(mine - theirs) > TOL * max(1.0, abs(theirs)):
                problems.append(f"{r['date']}: {name} recomputed {mine!r} != ledger {theirs!r}")
        daily.append((r["date"], net, r["position_after_open"], turn))
        bdaily.append((r["date"], bnet, 1.0, bturn))
        prev_after, b_prev = r["position_after_open"], 1.0

    def stats(series):
        n = len(series)
        out = {"days": n, "trades": sum(1 for x in series if x[3] > 0), "turnover_sum": sum(x[3] for x in series)}
        if n < min_days:
            return {**out, "total_return": None, "max_drawdown": None, "sharpe_rf0": None, "cagr": None, "ann_vol": None}
        v, peak, mdd = 1.0, 1.0, 0.0
        for x in series:
            v *= 1 + x[1]; peak = max(peak, v); mdd = min(mdd, v / peak - 1)
        m = sum(x[1] for x in series) / n
        sd = (sum((x[1] - m) ** 2 for x in series) / (n - 1)) ** 0.5
        return {**out, "total_return": v - 1, "max_drawdown": mdd, "sharpe_rf0": m / sd * math.sqrt(ann) if sd else None,
                "cagr": v ** (ann / n) - 1, "ann_vol": sd * math.sqrt(ann)}

    segs = {}
    for s in segments:
        segs[s["name"]] = {"strategy": stats([x for x in daily if s["start"] <= x[0] <= s["end"]]),
                           "benchmark": stats([x for x in bdaily if s["start"] <= x[0] <= s["end"]])}
    return {"problems": problems, "segments": segs, "full": {"strategy": stats(daily), "benchmark": stats(bdaily)}}


def verify_run(run_dir: Path) -> dict:
    run_dir = Path(run_dir)
    metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
    cfg = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    report = {"run_id": metrics["run_id"], "scenarios": {}, "ok": True}
    for scen in metrics["scenarios"]:
        bps = scen["cost_bps"]
        with open(run_dir / f"ledger_cost{int(bps)}bps.csv", encoding="utf-8", newline="") as fh:
            rows = [{k: (v if k == "date" else _f(v)) for k, v in r.items()} for r in csv.DictReader(fh)]
        re = recompute(rows, bps, cfg["segments"], cfg["metrics"]["annualization_days"], cfg["metrics"]["min_days_for_metrics"])
        mism = list(re["problems"][:20])
        for seg in scen["segments"]:
            for side in ("strategy", "benchmark"):
                for key in ("total_return", "max_drawdown", "sharpe_rf0", "cagr", "ann_vol", "trades", "days"):
                    a, b = seg[side][key], re["segments"][seg["name"]][side][key]
                    if (a is None) != (b is None) or (a is not None and abs(a - b) > 1e-9 * max(1.0, abs(a))):
                        mism.append(f"{seg['name']}.{side}.{key}: metrics {a!r} vs recomputed {b!r}")
        report["scenarios"][str(bps)] = {"rows": len(rows), "mismatches": mism, "ok": not mism,
                                         "recomputed_full_strategy_total_return": re["full"]["strategy"]["total_return"]}
        report["ok"] &= not mism
    return report


if __name__ == "__main__":
    out = verify_run(Path(sys.argv[1]))
    print(json.dumps(out, indent=1, ensure_ascii=False))
    sys.exit(0 if out["ok"] else 1)
