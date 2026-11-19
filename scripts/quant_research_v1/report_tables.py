"""Generate report tables and comparison statements directly from a run's metrics.json.

No number in a report should be typed by hand: this prints a Markdown table of every cost scenario
x segment (and full_period) plus a machine-derived count of where the strategy beat the benchmark.
Usage: python -m scripts.quant_research_v1.report_tables METRICS_JSON [--json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SEG = {"development": "开发段", "validation": "验证段", "holdout": "后段（holdout，已查看过的历史区间）", "full_period": "全期"}


def pct(v):
    return "—" if v is None else f"{v * 100:+.2f}%"


def num(v):
    return "—" if v is None else f"{v:.2f}"


def rows(metrics: dict):
    for s in metrics["scenarios"]:
        for g in s["segments"] + [{"name": "full_period", "start": s["first_trade_date"], "end": s["last_date"], **s["full_period"]}]:
            yield s["cost_bps"], g


def comparison(metrics: dict) -> dict:
    beat, lost, tie, cells = [], [], [], []
    for bps, g in rows(metrics):
        if g["name"] == "full_period":
            continue
        a, b = g["strategy"]["total_return"], g["benchmark"]["total_return"]
        key = f"{bps}bps/{g['name']}"
        (beat if a > b else lost if a < b else tie).append(key)
        cells.append({"cell": key, "strategy_total_return": a, "benchmark_total_return": b})
    return {"strategy_above_benchmark": beat, "strategy_below_benchmark": lost, "equal": tie, "cells": cells}


def markdown(metrics: dict) -> str:
    out = [f"run `{metrics['run_id']}` · results_digest `{metrics['results_digest']}` · {metrics['return_label']}", "",
           "| 成本 | 区间 | 起止 | 策略总收益 | 基准总收益 | 策略 Sharpe(rf=0) | 基准 Sharpe | 策略最大回撤 | 基准最大回撤 | 交易次数 | 天数 |",
           "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for bps, g in rows(metrics):
        s, b = g["strategy"], g["benchmark"]
        out.append(f"| {bps} bps | {SEG.get(g['name'], g['name'])} | {g['start']}—{g['end']} | {pct(s['total_return'])} | {pct(b['total_return'])} | "
                   f"{num(s['sharpe_rf0'])} | {num(b['sharpe_rf0'])} | {pct(s['max_drawdown'])} | {pct(b['max_drawdown'])} | {s['trades']} | {s['days']} |")
    c = comparison(metrics)
    out += ["", f"分段比较（由结果文件生成，不含全期）：策略总收益高于基准 {len(c['strategy_above_benchmark'])} 格："
                f"{', '.join(c['strategy_above_benchmark']) or '无'}；低于基准 {len(c['strategy_below_benchmark'])} 格；相等 {len(c['equal'])} 格。"]
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    m = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(json.dumps(comparison(m), indent=1) if "--json" in sys.argv else markdown(m))
