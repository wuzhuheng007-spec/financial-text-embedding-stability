"""Frozen A/B daily-direction development baseline on real historical bars.

The target is the sign of the label session's own open-to-close price move.
For label session t, every price and volume input ends at trading session t-2.
This deliberately leaves the entire preceding session t-1 unused in A/B.
Historical provider data downloaded later is a retrospective development input,
not a proof that a prediction was made then. No trade is placed.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from .price_baseline import (END, FEATURE_NAMES, START, SYMBOLS, canonical_hash,
                             load_raw_bars, sha256_file, source_config)


SCHEMA = "market_observer_historical_direction_baseline_v3"
TRAIN_END = date(2026, 3, 31)
VALIDATION_END = date(2026, 5, 31)
L2_GRID = (0.1, 1.0, 10.0)
FEATURES = ("price_return_1d", "price_return_5d", "price_return_20d",
            "price_volatility_20d", "volume_log_change_1d",
            "volume_log_change_5d")


@dataclass(frozen=True)
class Ohlcv:
    day: date
    open: float
    close: float
    volume: float


@dataclass(frozen=True)
class DirectionExample:
    symbol: str
    label_day: date
    last_feature_day: date
    skipped_prior_day: date
    features: tuple[float, ...]
    label_up: int
    intraday_zero: bool


def load_ohlcv(input_dir: Path, symbol: str) -> tuple[list[Ohlcv], dict[str, Any]]:
    validated, receipt = load_raw_bars(input_dir, symbol)
    path = input_dir / f"{symbol.replace('.', '_')}_page1.raw.json"
    raw = json.loads(path.read_text(encoding="utf-8"))["data"]["kline_list"]
    by_day: dict[date, Ohlcv] = {}
    for row in raw:
        day = date(int(str(row["date"])[:4]), int(str(row["date"])[4:6]),
                   int(str(row["date"])[6:8]))
        opening = float(row["open"])
        closing = float(row["close"])
        volume = float(row["volume"])
        if not all(math.isfinite(v) and v > 0 for v in (opening, closing, volume)):
            raise ValueError(f"{symbol}: nonpositive or nonfinite OHLCV input")
        by_day[day] = Ohlcv(day, opening, closing, volume)
    bars = [by_day[b.day] for b in validated]
    if any(abs(a.close - b.close) > 1e-10 for a, b in zip(bars, validated)):
        raise ValueError(f"{symbol}: close changed between raw and validated views")
    return bars, receipt


def make_direction_examples(symbol: str, bars: list[Ohlcv]) -> list[DirectionExample]:
    if len(bars) < 24:
        raise ValueError(f"{symbol}: insufficient bars for twenty-session lag")
    closes = np.asarray([b.close for b in bars], dtype=float)
    volumes = np.asarray([b.volume for b in bars], dtype=float)
    if not np.isfinite(closes).all() or not np.isfinite(volumes).all() or \
            np.any(closes <= 0) or np.any(volumes <= 0):
        raise ValueError("OHLCV contains invalid values")
    log_close = np.log(closes)
    log_volume = np.log(volumes)
    examples: list[DirectionExample] = []
    for label_index in range(22, len(bars)):
        last = label_index - 2
        twenty = np.diff(log_close[last - 20:last + 1])
        features = (float(twenty[-1]), float(sum(twenty[-5:])),
                    float(sum(twenty)), float(np.std(twenty, ddof=0)),
                    float(log_volume[last] - log_volume[last - 1]),
                    float(log_volume[last] - log_volume[last - 5]))
        current = bars[label_index]
        examples.append(DirectionExample(
            symbol=symbol, label_day=current.day, last_feature_day=bars[last].day,
            skipped_prior_day=bars[label_index - 1].day, features=features,
            label_up=int(current.close > current.open),
            intraday_zero=current.close == current.open))
    return examples


def split_name(label_day: date) -> str:
    if label_day <= TRAIN_END:
        return "train"
    if label_day <= VALIDATION_END:
        return "validation"
    if label_day <= END:
        return "test"
    raise ValueError("label outside frozen target window")


def _sigmoid(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(values, -40.0, 40.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def fit_logistic(train_x: np.ndarray, train_y: np.ndarray, *, l2: float
                 ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Newton IRLS, with intercept unpenalized and scaling fit only to train."""
    if len(train_x) < 30 or len(train_x) != len(train_y) or l2 <= 0:
        raise ValueError("invalid training set or regularization")
    if set(np.unique(train_y)) != {0, 1}:
        raise ValueError("training set needs both directions")
    mean = train_x.mean(axis=0)
    std = train_x.std(axis=0)
    std = np.where(std > 1e-12, std, 1.0)
    design = np.column_stack((np.ones(len(train_x)), (train_x - mean) / std))
    beta = np.zeros(design.shape[1])
    penalty = np.eye(design.shape[1]) * l2
    penalty[0, 0] = 0.0
    for _ in range(100):
        p = _sigmoid(design @ beta)
        weight = np.clip(p * (1 - p), 1e-8, None)
        gradient = design.T @ (p - train_y) + penalty @ beta
        hessian = design.T @ (design * weight[:, None]) + penalty
        step = np.linalg.solve(hessian, gradient)
        beta -= step
        if float(np.max(np.abs(step))) < 1e-9:
            break
    return beta, mean, std


def predict_up(x: np.ndarray, beta: np.ndarray, mean: np.ndarray,
               std: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    design = np.column_stack((np.ones(len(x)), (x - mean) / std))
    probability = _sigmoid(design @ beta)
    return (probability >= 0.5).astype(int), probability


def classification_metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, Any]:
    if not len(actual) or len(actual) != len(predicted):
        raise ValueError("empty or mismatched classification vectors")
    if not set(np.unique(actual)).issubset({0, 1}) or \
            not set(np.unique(predicted)).issubset({0, 1}):
        raise ValueError("direction labels must be binary")
    support_down = int(np.count_nonzero(actual == 0))
    support_up = int(np.count_nonzero(actual == 1))
    correct_down = int(np.count_nonzero((actual == 0) & (predicted == 0)))
    correct_up = int(np.count_nonzero((actual == 1) & (predicted == 1)))
    accuracy = float(np.mean(actual == predicted))
    balanced = ((correct_down / support_down + correct_up / support_up) / 2
                if support_down and support_up else None)
    return {"n": int(len(actual)), "support_down": support_down,
            "support_up": support_up, "correct_down": correct_down,
            "correct_up": correct_up, "accuracy": accuracy,
            "balanced_accuracy": balanced,
            "balanced_accuracy_status": "DEFINED" if balanced is not None else "UNDEFINED_ONE_CLASS"}


def choose_regularization(validation: dict[float, dict[str, Any]]) -> float:
    """Highest validation balanced accuracy; ties use stronger regularization."""
    if any(item["balanced_accuracy"] is None for item in validation.values()):
        raise ValueError("validation class coverage cannot support selection")
    return max(L2_GRID, key=lambda value: (validation[value]["balanced_accuracy"], value))


def fit_and_select(train_x: np.ndarray, train_y: np.ndarray,
                   validation_x: np.ndarray, validation_y: np.ndarray
                   ) -> tuple[float, tuple[np.ndarray, np.ndarray, np.ndarray],
                              dict[float, dict[str, Any]]]:
    """Grid candidates see train and validation rows only, never test rows."""
    candidates: dict[float, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    validation: dict[float, dict[str, Any]] = {}
    for l2 in L2_GRID:
        fitted = fit_logistic(train_x, train_y, l2=l2)
        predictions, _ = predict_up(validation_x, *fitted)
        candidates[l2] = fitted
        validation[l2] = classification_metrics(validation_y, predictions)
    selected = choose_regularization(validation)
    return selected, candidates[selected], validation


def predict_selected_once_per_split(
        x: np.ndarray, groups: dict[str, np.ndarray],
        fitted: tuple[np.ndarray, np.ndarray, np.ndarray]
        ) -> tuple[np.ndarray, np.ndarray]:
    """Only the selected model sees the test features, in one prediction call."""
    predicted = np.empty(len(x), dtype=int)
    probabilities = np.empty(len(x), dtype=float)
    for name in ("train", "validation", "test"):
        indices = groups[name]
        split_predicted, split_probabilities = predict_up(x[indices], *fitted)
        predicted[indices] = split_predicted
        probabilities[indices] = split_probabilities
    return predicted, probabilities


def run(input_dir: Path, private_output_dir: Path, public_summary_path: Path) -> dict[str, Any]:
    source, source_hash = source_config(input_dir)
    config: dict[str, Any] = {
        "schema": SCHEMA, "source_request_sha256": source_hash,
        "symbols": list(SYMBOLS), "target_window": [START.isoformat(), END.isoformat()],
        "source_adjustment": "autype=1 forward-adjusted; retrospective provider series",
        "target": "label-session open-to-close UP if close>open, otherwise DOWN including exact ties",
        "features": list(FEATURES),
        "feature_rule": "for label t use only adjusted daily close and volume through trading session t-2; t-1 is excluded",
        "lookback_sessions": 20,
        "split_by_label_date": {"train_end": TRAIN_END.isoformat(),
                                "validation_end": VALIDATION_END.isoformat(),
                                "test_end": END.isoformat()},
        "A": "per-symbol train-majority class; tie chooses DOWN",
        "B": {"model": "per-symbol L2 logistic IRLS", "grid": list(L2_GRID),
              "train_standardization": True,
              "selection_metric": "validation balanced accuracy",
              "tie_break": "strongest regularization", "threshold": 0.5,
              "test_tuning": False},
        "C": "NOT_RUN without complete historically available news coverage",
        "retrospective_limit": "provider daily bar has market date but no verified historical feature-availability timestamp",
        "test_independence": "CONTAMINATED_BY_PRIOR_EXPLORATORY_JUNE_JULY_LABEL_REVIEW_AND_V2_GRID_TEST_FEATURE_EVALUATION",
        "supersedes": "market_observer_historical_direction_baseline_v2; old outputs retained",
    }
    private_output_dir.mkdir(parents=True, exist_ok=True)
    private_config_path = private_output_dir / "run_config.json"
    private_config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    config_hash = sha256_file(private_config_path)
    all_bars: dict[str, list[Ohlcv]] = {}
    receipts: dict[str, dict[str, Any]] = {}
    for symbol in SYMBOLS:
        all_bars[symbol], receipts[symbol] = load_ohlcv(input_dir, symbol)
    dates = {symbol: {bar.day for bar in bars} for symbol, bars in all_bars.items()}
    if any(value != dates[SYMBOLS[0]] for value in dates.values()):
        raise ValueError("pilot symbols do not share a complete same-market date set")
    summary: dict[str, Any] = {}
    for symbol in SYMBOLS:
        examples = make_direction_examples(symbol, all_bars[symbol])
        groups = {name: np.asarray([i for i, e in enumerate(examples)
                                    if split_name(e.label_day) == name], dtype=int)
                  for name in ("train", "validation", "test")}
        if any(not len(indices) for indices in groups.values()):
            raise ValueError(f"{symbol}: empty chronological split")
        x = np.asarray([e.features for e in examples])
        y = np.asarray([e.label_up for e in examples])
        majority = int(np.count_nonzero(y[groups["train"]]) > len(groups["train"]) / 2)
        a_predictions = np.full(len(y), majority, dtype=int)
        selected, fitted, validation = fit_and_select(
            x[groups["train"]], y[groups["train"]],
            x[groups["validation"]], y[groups["validation"]])
        b_predictions, probabilities = predict_selected_once_per_split(x, groups, fitted)
        split_results: dict[str, Any] = {}
        for name, indices in groups.items():
            split_results[name] = {
                "n": int(len(indices)),
                "label_first": examples[int(indices[0])].label_day.isoformat(),
                "label_last": examples[int(indices[-1])].label_day.isoformat(),
                "A_train_majority": classification_metrics(y[indices], a_predictions[indices]),
                "B_price_volume": classification_metrics(y[indices], b_predictions[indices]),
                "intraday_zero_count": int(sum(examples[int(i)].intraday_zero for i in indices)),
            }
        row_path = private_output_dir / f"{symbol.replace('.', '_')}_predictions.private.jsonl"
        rows = [{"symbol": symbol, "label_day": e.label_day.isoformat(),
                 "last_feature_day": e.last_feature_day.isoformat(),
                 "skipped_prior_day": e.skipped_prior_day.isoformat(),
                 "split": split_name(e.label_day),
                 "features": dict(zip(FEATURES, e.features)), "actual_up": e.label_up,
                 "intraday_zero": e.intraday_zero, "A_predicted_up": int(a_predictions[i]),
                 "B_predicted_up": int(b_predictions[i]),
                 "B_uncalibrated_probability": float(probabilities[i])}
                for i, e in enumerate(examples)]
        row_path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
                            encoding="utf-8")
        summary[symbol] = {"source": receipts[symbol], "examples": len(examples),
                           "train_majority_up": majority, "selected_l2": selected,
                           "validation_grid": {str(k): v for k, v in validation.items()},
                           "splits": split_results,
                           "private_predictions_sha256": sha256_file(row_path)}
    receipt = {"schema": SCHEMA,
               "status": "REAL_RETROSPECTIVE_PRICE_VOLUME_DEVELOPMENT",
               "config": config, "config_sha256": config_hash,
               "source_request": source, "symbols": summary,
               "C_news_incremental": "NOT_RUN_NO_QUALIFIED_COMPLETE_POINT_IN_TIME_NEWS",
               "test_independence": config["test_independence"],
               "financial_validity": "NOT_ESTABLISHED",
               "limitations": [
                   "Daily bars were retrieved after the historical window; original as-known-at-time availability is unverified.",
                   "Forward-adjustment and action inventory are not fully independently reconciled; within-bar sign is scale-invariant.",
                   "The test interval had been inspected in a prior exploratory baseline, so this is not an untouched test.",
                   "Three correlated pilot securities do not establish general predictive performance."]}
    (private_output_dir / "run_receipt.private.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    public = {k: v for k, v in receipt.items() if k not in {"source_request", "limitations"}}
    public["limitations"] = receipt["limitations"]
    public_summary_path.parent.mkdir(parents=True, exist_ok=True)
    public_summary_path.write_text(json.dumps(public, indent=2) + "\n", encoding="utf-8")
    return public


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--private-output-dir", type=Path, required=True)
    parser.add_argument("--public-summary", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.input_dir, args.private_output_dir, args.public_summary)
    print(json.dumps({"status": result["status"],
                      "symbols": {s: {"bars": data["source"]["bar_count"],
                                      "train": data["splits"]["train"]["n"],
                                      "validation": data["splits"]["validation"]["n"],
                                      "test": data["splits"]["test"]["n"],
                                      "selected_l2": data["selected_l2"]}
                                  for s, data in result["symbols"].items()},
                      "C_news_incremental": result["C_news_incremental"]}))


if __name__ == "__main__":
    main()
