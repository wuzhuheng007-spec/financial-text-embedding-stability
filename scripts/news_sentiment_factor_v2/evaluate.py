"""Training-period-only retrospective date-proxy descriptive alignment.

The price reader consumes exactly the known number of training calendar bars
from each saved compact JSON file. It never parses April-July 2026 bars.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics
from bisect import bisect_right
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .corpus import CONFIG, PRIVATE, PUBLIC, ROOT, file_sha, write_json

PRICE = ROOT / "artifacts/market_observer_historical_20261115"
CALENDAR = PRICE / "US_trading_days.raw.json"
ACTIONS = PRICE / "action_alignment_audit.json"
RECEIPT = PRICE / "receipt_unadjusted.json"
FACTOR = PRIVATE / "event_factors_private.jsonl"


def _first_json_array_objects(path: Path, count: int) -> list[dict]:
    """Read only the first count objects from the kline_list array."""
    marker = b'"kline_list":['
    output: list[dict] = []
    with path.open("rb") as handle:
        prefix = bytearray()
        while not prefix.endswith(marker):
            byte = handle.read(1)
            if not byte or len(prefix) > 10000:
                raise ValueError("KLINE_LIST_MARKER_NOT_FOUND")
            prefix.extend(byte)
        for _ in range(count):
            while True:
                byte = handle.read(1)
                if not byte:
                    raise ValueError("KLINE_LIST_ENDED_EARLY")
                if byte == b"{":
                    break
                if byte not in (b",", b" ", b"\t", b"\n", b"\r"):
                    raise ValueError("BAD_KLINE_ARRAY_SEPARATOR")
            raw = bytearray(b"{")
            depth = 1
            quoted = False
            escaped = False
            while depth:
                byte = handle.read(1)
                if not byte:
                    raise ValueError("KLINE_OBJECT_ENDED_EARLY")
                raw.extend(byte)
                b = byte[0]
                if quoted:
                    if escaped:
                        escaped = False
                    elif b == 92:
                        escaped = True
                    elif b == 34:
                        quoted = False
                elif b == 34:
                    quoted = True
                elif b == 123:
                    depth += 1
                elif b == 125:
                    depth -= 1
            output.append(json.loads(raw))
    return output


def _pearson(x: list[float], y: list[float]) -> float | None:
    if len(x) < 2 or len(x) != len(y):
        return None
    mx, my = statistics.mean(x), statistics.mean(y)
    sx = sum((v - mx) ** 2 for v in x)
    sy = sum((v - my) ** 2 for v in y)
    if sx <= 0 or sy <= 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / math.sqrt(sx * sy)


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i + 1
        while j < len(order) and values[order[j]] == values[order[i]]:
            j += 1
        rank = (i + 1 + j) / 2
        for index in order[i:j]:
            ranks[index] = rank
        i = j
    return ranks


def correlations(rows: list[dict], factor: str, horizon: int, min_n: int,
                 block_size: int, reps: int, seed: int) -> dict:
    eligible = sorted((r for r in rows if r.get(factor) is not None and r.get(f"return_{horizon}d") is not None),
                      key=lambda r: (r["entry_date"], r["symbol"]))
    n = len(eligible)
    if n < min_n:
        return {"n": n, "pearson": None, "spearman": None,
                "block_bootstrap_95pct": None, "status": "MINIMUM_EVENT_COUNT_NOT_MET"}
    x = [r[factor] for r in eligible]
    y = [r[f"return_{horizon}d"] for r in eligible]
    p = _pearson(x, y)
    s = _pearson(_ranks(x), _ranks(y))
    if p is None or s is None:
        return {"n": n, "pearson": p, "spearman": s,
                "block_bootstrap_95pct": None, "status": "CONSTANT_OR_UNDEFINED"}
    blocks = [eligible[i:i + block_size] for i in range(0, len(eligible), block_size)]
    rng = random.Random(seed)
    draws = []
    for _ in range(reps):
        sample = [row for _j in range(len(blocks)) for row in rng.choice(blocks)]
        result = _pearson([r[factor] for r in sample], [r[f"return_{horizon}d"] for r in sample])
        if result is not None:
            draws.append(result)
    draws.sort()
    interval = [draws[int(0.025 * (len(draws) - 1))], draws[int(0.975 * (len(draws) - 1))]] if draws else None
    return {"n": n, "pearson": p, "spearman": s,
            "block_bootstrap_95pct": interval, "valid_bootstrap_draws": len(draws),
            "status": "RETROSPECTIVE_DATE_PROXY_DESCRIPTIVE_ONLY"}


def preflight() -> dict:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    factors = [json.loads(line) for line in FACTOR.read_text(encoding="utf-8").splitlines()]
    if any(r["announcement_date"] > config["this_run_end"] for r in factors):
        raise ValueError("LATER_EVENT_IN_FACTOR_INPUT")
    calendar_sha = file_sha(CALENDAR)
    if calendar_sha != config["price_proxy"]["calendar_sha256"]:
        raise ValueError("TRADING_CALENDAR_HASH_MISMATCH")
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    price_hashes = {x["symbol"]: x["response_sha256"] for x in receipt["symbols"]}
    for sym in config["issuer_symbols"]:
        if file_sha(PRICE / f"US_{sym}_unadjusted_page1.raw.json") != price_hashes[f"US.{sym}"]:
            raise ValueError("UNADJUSTED_PRICE_FILE_HASH_MISMATCH")
    gate = config["development_evaluation"]
    count = Counter(r["symbol"] for r in factors)
    fit_ready = len(factors) >= gate["fit_gate_min_independent_events"] and \
        all(count[s] >= gate["fit_gate_min_events_per_issuer"] for s in config["issuer_symbols"])
    result = {"schema": "sentiment_v2_evaluation_preflight_redacted_1",
              "checked_at_utc": datetime.now(timezone.utc).isoformat(),
              "config_sha256": file_sha(CONFIG), "factor_file_sha256": file_sha(FACTOR),
              "evaluation_code_sha256": file_sha(Path(__file__)),
              "calendar_sha256": calendar_sha, "raw_price_sha256": price_hashes,
              "company_actions_audit_sha256": file_sha(ACTIONS),
              "independent_issuer_events": len(factors), "events_by_symbol": dict(count),
              "four_group_fit_ready": fit_ready, "fit_calls_before_labels": 0,
              "post_train_price_bars_parsed": 0, "strict_historical_availability": False}
    write_json(PUBLIC / "evaluation_preflight_redacted.json", result)
    return {"events": len(factors), "four_group_fit_ready": fit_ready}


def evaluate() -> dict:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    pre = json.loads((PUBLIC / "evaluation_preflight_redacted.json").read_text(encoding="utf-8"))
    if pre["evaluation_code_sha256"] != file_sha(Path(__file__)) or \
       pre["factor_file_sha256"] != file_sha(FACTOR) or \
       pre["config_sha256"] != file_sha(CONFIG):
        raise ValueError("EVALUATION_PREFLIGHT_IDENTITY_CHANGED")
    if (PRIVATE / "event_alignment_private.jsonl").exists():
        raise FileExistsError("EVALUATION_OUTPUT_ALREADY_EXISTS")
    calendar_data = json.loads(CALENDAR.read_text(encoding="utf-8"))["data"]["trading_days"]
    all_days = [r["time"] for r in calendar_data if
                config["study_window"][0] <= r["time"] <= config["this_run_end"]]
    days = [r["time"] for r in calendar_data if r["trade_date_type"] == "WHOLE" and
            config["study_window"][0] <= r["time"] <= config["this_run_end"]]
    if all_days != sorted(set(all_days)) or days != sorted(set(days)) or not days:
        raise ValueError("TRAINING_CALENDAR_UNUSABLE")
    bars = {}
    for sym in config["issuer_symbols"]:
        first = _first_json_array_objects(PRICE / f"US_{sym}_unadjusted_page1.raw.json", len(all_days))
        pairs = {str(r["date"]): r for r in first}
        bars[sym] = {d: pairs[d.replace("-", "")] for d in all_days if d.replace("-", "") in pairs}
        if len(bars[sym]) != len(all_days):
            raise ValueError("TRAINING_PRICE_COVERAGE_GAP:" + sym)
        if any(str(r["date"]) > config["this_run_end"].replace("-", "") for r in first):
            raise ValueError("LATER_PRICE_BAR_PARSED")
    actions = json.loads(ACTIONS.read_text(encoding="utf-8"))["symbols"]
    action_days: dict[str, set[str]] = {}
    for item in actions:
        sym = item["symbol"].removeprefix("US.")
        if sym not in config["issuer_symbols"]:
            continue
        action_days[sym] = {d for d in item["dividends_endpoint_dates"] if d <= config["this_run_end"]} | \
                           {x["date"] for x in item["split_records"] if x["date"] <= config["this_run_end"]}
    factors = [json.loads(line) for line in FACTOR.read_text(encoding="utf-8").splitlines()]
    out = []
    exclusions: Counter = Counter()
    for factor in factors:
        row = dict(factor)
        sym = row["symbol"]
        complete_after = bisect_right(days, row["announcement_date"])
        entry_idx = complete_after + 1
        row["entry_date"] = days[entry_idx] if entry_idx < len(days) else None
        row["entry_rule"] = config["price_proxy"]["entry_rule"]
        for horizon in config["price_proxy"]["horizons_trading_days"]:
            reason = None
            end_idx = entry_idx + horizon
            if row["entry_date"] is None or end_idx >= len(days):
                reason = "CROSSES_TRAIN_BOUNDARY"
            else:
                entry, exit = days[entry_idx], days[end_idx]
                row[f"exit_date_{horizon}d"] = exit
                if any(entry <= d <= exit for d in action_days[sym]):
                    reason = "COMPANY_ACTION_IN_WINDOW"
                else:
                    price_a = float(bars[sym][entry]["open"])
                    price_b = float(bars[sym][exit]["open"])
                    if not (math.isfinite(price_a) and math.isfinite(price_b) and price_a > 0 and price_b > 0):
                        reason = "INVALID_UNADJUSTED_OPEN"
                    else:
                        row[f"entry_open_{horizon}d"] = price_a
                        row[f"exit_open_{horizon}d"] = price_b
                        row[f"return_{horizon}d"] = price_b / price_a - 1
            row[f"label_status_{horizon}d"] = reason or "DATE_PROXY_DESCRIPTIVE"
            if reason:
                row[f"return_{horizon}d"] = None
                exclusions[reason] += 1
        row["price_source_kind"] = "moomoo_saved_unadjusted_kline_retrospective"
        row["price_file_sha256"] = pre["raw_price_sha256"][f"US.{sym}"]
        out.append(row)
    target = PRIVATE / "event_alignment_private.jsonl"
    target.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out), encoding="utf-8")
    gate = config["development_evaluation"]
    tests = {}
    for name in ("lm_F1", "finbert_F1", "lm_F2", "finbert_F2"):
        tests[name] = {str(h): correlations(out, name, h, gate["descriptive_correlation_min_pooled_events"],
                                           gate["date_block_size_events"], gate["bootstrap_repetitions"],
                                           gate["bootstrap_seed"]) for h in config["price_proxy"]["horizons_trading_days"]}
    result = {"schema": "sentiment_v2_date_proxy_development_redacted_1",
              "completed_at_utc": datetime.now(timezone.utc).isoformat(),
              "config_sha256": file_sha(CONFIG), "factor_file_sha256": file_sha(FACTOR),
              "event_alignment_private_sha256": file_sha(target),
              "events": len(out), "issuer_counts": dict(Counter(r["symbol"] for r in out)),
              "label_eligible_counts": {str(h): sum(r.get(f"return_{h}d") is not None for r in out)
                                        for h in config["price_proxy"]["horizons_trading_days"]},
              "exclusions": dict(exclusions), "descriptive_correlations": tests,
              "four_group_fit_calls": 0, "fit_gate": "INSUFFICIENT_INDEPENDENT_EVENTS" if not pre["four_group_fit_ready"] else "NOT_EXECUTED",
              "later_price_bars_parsed": 0, "later_labels_scored": 0,
              "strict_historical_tradeability": "NOT_ESTABLISHED",
              "warning": "8 reused training events; weak date-block intervals are descriptive and cannot establish prediction or text increment"}
    write_json(PUBLIC / "development_evaluation_redacted.json", result)
    return {"events": len(out), "eligible_1d": result["label_eligible_counts"]["1"],
            "eligible_5d": result["label_eligible_counts"]["5"], "fit_calls": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("preflight", "evaluate"))
    args = parser.parse_args()
    print(json.dumps(preflight() if args.mode == "preflight" else evaluate()))
