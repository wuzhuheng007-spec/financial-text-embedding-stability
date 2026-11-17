"""Pooled, paired, training-window-only date-proxy development comparison.

No random split, search, post-March label, cross-sectional IC, or signal
promotion. All individual returns and predictions stay in ignored artifacts.
"""
from __future__ import annotations

import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from .align import ALIGNMENT
from .source import CONFIG, PRIVATE, PUBLIC, digest, now, write_json

PREDICTIONS = PRIVATE / "fold_predictions_private.jsonl"
DIAGNOSTICS = PRIVATE / "old_eight_influence_private.json"
SUMMARY = PUBLIC / "development_evaluation_redacted.json"


def pearson(x, y):
    if len(x) < 3 or len(set(x)) < 2 or len(set(y)) < 2:
        return None
    return float(np.corrcoef(np.asarray(x, dtype=float), np.asarray(y, dtype=float))[0, 1])


def rank_corr(x, y):
    if len(x) < 3 or len(set(x)) < 2 or len(set(y)) < 2:
        return None
    return float(spearmanr(x, y).statistic)


def block_interval(rows, statistic, horizon, seed):
    # Calendar-date clusters preserve all same-day issuer events. Consecutive
    # blocks of horizon+1 event-date groups are a conservative overlap guard.
    groups = defaultdict(list)
    for row in rows:
        groups[row["acceptance_et_date"]].append(row)
    dates = sorted(groups)
    block = horizon + 1
    effective = len(dates) // block
    if effective < 8:
        return {"status": "TOO_FEW_NONOVERLAPPING_DATE_BLOCKS", "distinct_dates": len(dates),
                "block_length_event_dates": block, "effective_blocks_floor": effective,
                "interval": None}
    rng = random.Random(seed)
    draws = []
    for _ in range(500):
        sampled = []
        while len(sampled) < len(dates):
            start = rng.randrange(len(dates))
            sampled.extend(dates[(start + j) % len(dates)] for j in range(block))
        replicate = [row for d in sampled[:len(dates)] for row in groups[d]]
        number = statistic(replicate)
        if number is not None and math.isfinite(number):
            draws.append(number)
    draws.sort()
    interval = [draws[int(0.025 * (len(draws) - 1))], draws[int(0.975 * (len(draws) - 1))]] if len(draws) >= 400 else None
    return {"status": "DATE_CLUSTER_MOVING_BLOCK_DESCRIPTIVE" if interval else "INSUFFICIENT_VALID_DRAWS",
            "distinct_dates": len(dates), "block_length_event_dates": block,
            "effective_blocks_floor": effective, "valid_draws": len(draws), "interval": interval}


def fold_specs(eligible):
    dates = sorted({x["acceptance_et_date"] for x in eligible})
    indexes = [int(len(dates) * x) for x in (0.5, 0.65, 0.8)]
    if len(set(indexes)) != 3 or indexes[0] <= 0 or indexes[-1] >= len(dates):
        return []
    specs = []
    for i, left in enumerate(indexes):
        right = indexes[i + 1] if i + 1 < len(indexes) else len(dates)
        train = [x for x in eligible if x["acceptance_et_date"] < dates[left]]
        test = [x for x in eligible if dates[left] <= x["acceptance_et_date"] < (dates[right] if right < len(dates) else "9999-12-31")]
        if not test:
            specs.append({"fold": i + 1, "train": [], "test": [], "purged": len(train), "cut_date": dates[left]})
            continue
        first_test_entry = min(x["entry_date"] for x in test)
        horizon = eligible[0]["_horizon"]
        clean = [x for x in train if x[f"exit_{horizon}d"] < first_test_entry]
        specs.append({"fold": i + 1, "train": clean, "test": test,
                      "purged": len(train) - len(clean), "cut_date": dates[left],
                      "first_test_entry": first_test_entry})
    return specs


def candidate_summary(rows, factor, horizon):
    x = [r[factor] for r in rows]
    y = [r[f"return_{horizon}d"] for r in rows]
    p = pearson(x, y)
    s = rank_corr(x, y)
    interval = block_interval(rows, lambda sample: pearson([r[factor] for r in sample],
                                                           [r[f"return_{horizon}d"] for r in sample]),
                              horizon, 1729)
    return {"events": len(rows), "distinct_acceptance_dates": len({r["acceptance_et_date"] for r in rows}),
            "pearson": p, "spearman": s, "pearson_block_interval": interval,
            "undefined_reason": "CONSTANT_OR_TOO_FEW" if p is None or s is None else None}


def fit_candidate(rows, factor, horizon, gate):
    ready = len(rows) >= gate["minimum_pooled_events_for_fit"] and len({r["acceptance_et_date"] for r in rows}) >= gate["minimum_distinct_event_dates_for_fit"]
    if not ready:
        return {"status": "OVERALL_FIT_GATE_NOT_MET", "fit_calls": 0, "folds": []}, []
    folds = fold_specs(rows)
    output = []
    fold_receipts = []
    fits = 0
    for spec in folds:
        train, test = spec["train"], spec["test"]
        receipt = {k: spec.get(k) for k in ("fold", "cut_date", "first_test_entry", "purged")}
        receipt.update({"train_events": len(train), "test_events": len(test),
                        "train_dates": len({x["acceptance_et_date"] for x in train}),
                        "test_dates": len({x["acceptance_et_date"] for x in test})})
        if len(train) < gate["minimum_train_events_per_fold"] or len(test) < gate["minimum_test_events_per_fold"]:
            receipt["status"] = "FOLD_FIT_GATE_NOT_MET"
            fold_receipts.append(receipt)
            continue
        y_train = np.asarray([x[f"return_{horizon}d"] for x in train], dtype=float)
        matrices = {}
        for name, fields in (("A_PRICE", ["prior_session_open_to_close_return", "prior_session_log_volume"]),
                             ("C_PRICE_TEXT", ["prior_session_open_to_close_return", "prior_session_log_volume", factor])):
            x_train = np.asarray([[r[f] for f in fields] for r in train], dtype=float)
            x_test = np.asarray([[r[f] for f in fields] for r in test], dtype=float)
            scaler = StandardScaler().fit(x_train)
            model = Ridge(alpha=10.0, fit_intercept=True).fit(scaler.transform(x_train), y_train)
            prediction = model.predict(scaler.transform(x_test))
            matrices[name] = [float(v) for v in prediction]
            fits += 1
        for i, row in enumerate(test):
            output.append({"event_id": row["event_id"], "symbol": row["symbol"],
                           "acceptance_et_date": row["acceptance_et_date"],
                           "entry_date": row["entry_date"], "exit_date": row[f"exit_{horizon}d"],
                           "horizon": horizon, "factor": factor, "fold": spec["fold"],
                           "actual_return": row[f"return_{horizon}d"],
                           "A_price_prediction": matrices["A_PRICE"][i],
                           "C_price_text_prediction": matrices["C_PRICE_TEXT"][i]})
        receipt["status"] = "FITTED_PAIRED"
        receipt["fit_calls"] = 2
        fold_receipts.append(receipt)
    if not output:
        return {"status": "NO_FOLD_PASSED_GATE", "fit_calls": fits, "folds": fold_receipts}, []
    actual = np.asarray([x["actual_return"] for x in output], dtype=float)
    base = np.asarray([x["A_price_prediction"] for x in output], dtype=float)
    with_text = np.asarray([x["C_price_text_prediction"] for x in output], dtype=float)
    by_company = {}
    for symbol in sorted({x["symbol"] for x in output}):
        part = [x for x in output if x["symbol"] == symbol]
        by_company[symbol] = {"events": len(part), "mse_A": sum((x["A_price_prediction"] - x["actual_return"]) ** 2 for x in part) / len(part),
                              "mse_C": sum((x["C_price_text_prediction"] - x["actual_return"]) ** 2 for x in part) / len(part)}
    diffs = [{"acceptance_et_date": r["acceptance_et_date"], "delta_sq_error_C_minus_A":
              (r["C_price_text_prediction"] - r["actual_return"]) ** 2 -
              (r["A_price_prediction"] - r["actual_return"]) ** 2} for r in output]
    interval = block_interval(diffs, lambda sample: sum(x["delta_sq_error_C_minus_A"] for x in sample) / len(sample), horizon, 1729)
    receipt = {"status": "DEVELOPMENT_FIT_COMPLETED", "fit_calls": fits, "folds": fold_receipts,
               "paired_predictions": len(output), "independent_event_ids": len({x["event_id"] for x in output}),
               "distinct_acceptance_dates": len({x["acceptance_et_date"] for x in output}),
               "mse_A": float(np.mean((base - actual) ** 2)), "mse_C": float(np.mean((with_text - actual) ** 2)),
               "mse_delta_C_minus_A": float(np.mean((with_text - actual) ** 2 - (base - actual) ** 2)),
               "mae_A": float(np.mean(np.abs(base - actual))), "mae_C": float(np.mean(np.abs(with_text - actual))),
               "paired_mse_delta_block_interval": interval, "company_results": by_company,
               "B_equals_A": True, "D_equals_C": True,
               "no_general_effect_claim": True}
    return receipt, output


def old_eight_diagnostic():
    old_path = Path(__file__).resolve().parents[2] / "artifacts/news_sentiment_factor_v2_20261117/event_alignment_private.jsonl"
    old = [json.loads(line) for line in old_path.read_text(encoding="utf-8").splitlines()]
    records = []
    for factor in ("lm_F1", "finbert_F1"):
        for horizon in (1, 5):
            subset = [x for x in old if x.get(f"return_{horizon}d") is not None and x.get(factor) is not None]
            original = pearson([x[factor] for x in subset], [x[f"return_{horizon}d"] for x in subset])
            for drop in subset:
                others = [x for x in subset if x["event_group_id"] != drop["event_group_id"]]
                revised = pearson([x[factor] for x in others], [x[f"return_{horizon}d"] for x in others])
                records.append({"factor": factor, "horizon": horizon,
                                "omitted_event_id": drop["event_group_id"], "original_pearson": original,
                                "leave_one_out_pearson": revised,
                                "difference": revised - original if revised is not None and original is not None else None})
    write_json(DIAGNOSTICS, {"schema": "sentiment_v3_old_eight_influence_private_1",
                             "old_alignment_sha256": digest(old_path), "records": records,
                             "purpose": "sensitivity only; no event deletion"})
    finite = [abs(x["difference"]) for x in records if x["difference"] is not None]
    return {"old_events": len(old), "diagnostic_cases": len(records),
            "maximum_abs_pearson_change_after_omission": max(finite) if finite else None,
            "private_diagnostic_sha256": digest(DIAGNOSTICS)}


def evaluate():
    if SUMMARY.exists() or PREDICTIONS.exists() or DIAGNOSTICS.exists():
        raise FileExistsError("EVALUATION_EXISTS_NO_SILENT_RETRY")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    aligned = [json.loads(line) for line in ALIGNMENT.read_text(encoding="utf-8").splitlines()]
    alignment_receipt = json.loads((PUBLIC / "alignment_redacted.json").read_text(encoding="utf-8"))
    if alignment_receipt["event_alignment_sha256"] != digest(ALIGNMENT) or alignment_receipt["config_sha256"] != digest(CONFIG):
        raise ValueError("ALIGNMENT_IDENTITY_DRIFT")
    gate = config["evaluation"]
    candidate_results = {}
    predictions = []
    fit_calls = 0
    for horizon in (1, 5):
        for family in ("F1", "F2"):
            for model in ("lm", "finbert"):
                factor = f"{model}_{family}"
                subset = [dict(x, _horizon=horizon) for x in aligned
                          if x.get(factor) is not None and x.get(f"return_{horizon}d") is not None]
                key = f"{factor}_{horizon}d"
                description = candidate_summary(subset, factor, horizon) if subset else {
                    "events": 0, "distinct_acceptance_dates": 0, "pearson": None, "spearman": None,
                    "pearson_block_interval": None, "undefined_reason": "NO_COMMON_SAMPLE"}
                fit, output = fit_candidate(subset, factor, horizon, gate)
                fit_calls += fit["fit_calls"]
                candidate_results[key] = {"descriptive": description, "paired_fit": fit}
                predictions.extend(output)
    PREDICTIONS.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in predictions), encoding="utf-8")
    influence = old_eight_diagnostic()
    receipt = {"schema": "sentiment_v3_development_evaluation_redacted_1", "completed_at_utc": now(),
               "config_sha256": digest(CONFIG), "evaluation_code_sha256": digest(Path(__file__)),
               "alignment_sha256": digest(ALIGNMENT), "predictions_sha256": digest(PREDICTIONS),
               "fit_calls_actual": fit_calls, "candidate_results": candidate_results,
               "count_feature": "one SEC accession per event; constant 1; B=A and D=C without redundant fits",
               "old_eight_influence": influence,
               "strict_historical_executability": "NOT_PROVEN_SEC_ACCEPTANCE_ANCHOR_PROXY",
               "population": "purposeful_watchlist_official_earnings_development_only",
               "test_April_to_July_2026_labels_opened": False,
               "not_a_live_signal_or_alpha_claim": True}
    write_json(SUMMARY, receipt)
    return {"fit_calls_actual": fit_calls, "predictions_private": len(predictions),
            "candidates": {k: {"n": v["descriptive"]["events"], "status": v["paired_fit"]["status"]} for k, v in candidate_results.items()}}


if __name__ == "__main__":
    print(json.dumps(evaluate()))
