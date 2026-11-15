"""Auditable daily price baseline, with an optional point-in-time event comparison.

The historical moomoo price response is private. This module writes row-level
features and forecasts only to a private output directory and a separate
aggregate receipt suitable for review before publication. It does not infer
historical availability of a present-day download.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np


SCHEMA = "market_observer_historical_price_baseline_v1"
SYMBOLS = ("US.TQQQ", "US.NVDA", "US.MU")
START = date(2025, 4, 1)
END = date(2026, 7, 31)
TRAIN_END = date(2025, 12, 31)
VALIDATION_END = date(2026, 3, 31)
ALPHA = 1.0  # fixed before inspecting labels; no test-set selection
FEATURE_NAMES = ("return_1d", "return_5d", "return_20d", "volatility_20d")
NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class DailyBar:
    day: date
    close: float


@dataclass(frozen=True)
class Example:
    symbol: str
    feature_day: date
    label_day: date
    features: tuple[float, ...]
    label_bps: float
    momentum_bps: float


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def source_config(input_dir: Path) -> tuple[dict[str, Any], str]:
    path = input_dir / "run_config.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    required = {"start": START.isoformat(), "end": END.isoformat(),
                "ktype": 2, "autype": 1, "extended_time": 0}
    if any(config.get(key) != value for key, value in required.items()):
        raise ValueError("source request is not the frozen daily forward-adjusted regular-session window")
    if set(config.get("symbols", [])) != set(SYMBOLS):
        raise ValueError("source request symbols differ from frozen pilot")
    return config, sha256_file(path)


def load_raw_bars(input_dir: Path, symbol: str) -> tuple[list[DailyBar], dict[str, Any]]:
    if symbol not in SYMBOLS:
        raise ValueError("symbol is outside frozen pilot")
    path = input_dir / f"{symbol.replace('.', '_')}_page1.raw.json"
    response = json.loads(path.read_text(encoding="utf-8"))
    if response.get("ret_code") != 0:
        raise ValueError(f"{symbol}: source response is not successful")
    if response.get("pagination", {}).get("has_more") is not False:
        raise ValueError(f"{symbol}: source pagination incomplete")
    raw_list = response.get("data", {}).get("kline_list")
    if not isinstance(raw_list, list) or not raw_list:
        raise ValueError(f"{symbol}: missing daily bars")
    bars: list[DailyBar] = []
    seen: set[date] = set()
    for row in raw_list:
        day = datetime.strptime(str(row["date"]), "%Y%m%d").date()
        if not START <= day <= END:
            raise ValueError(f"{symbol}: date outside requested window")
        if day in seen:
            raise ValueError(f"{symbol}: duplicate daily date")
        seen.add(day)
        instant = datetime.fromtimestamp(int(row["time_key"]) / 1000, tz=timezone.utc)
        if instant.astimezone(NEW_YORK).date() != day:
            raise ValueError(f"{symbol}: provider timestamp and market date disagree")
        close = float(row["close"])
        if not math.isfinite(close) or close <= 0:
            raise ValueError(f"{symbol}: invalid close")
        bars.append(DailyBar(day, close))
    bars.sort(key=lambda item: item.day)
    if bars[0].day != START or bars[-1].day != END:
        raise ValueError(f"{symbol}: requested endpoint coverage incomplete")
    receipt = {"symbol": symbol, "source_file": path.name,
               "source_sha256": sha256_file(path), "bar_count": len(bars),
               "first_date": bars[0].day.isoformat(), "last_date": bars[-1].day.isoformat(),
               "pagination_complete": True}
    return bars, receipt


def make_examples(symbol: str, bars: list[DailyBar]) -> list[Example]:
    """Each feature uses closes through t; each label uses only close t+1."""
    if len(bars) < 22:
        raise ValueError(f"{symbol}: insufficient bars for twenty-day features")
    closes = np.asarray([bar.close for bar in bars], dtype=float)
    returns = np.diff(np.log(closes))
    examples: list[Example] = []
    for i in range(20, len(bars) - 1):
        twenty = returns[i - 20:i]
        features = (float(twenty[-1]), float(sum(twenty[-5:])),
                    float(sum(twenty)), float(np.std(twenty, ddof=0)))
        examples.append(Example(symbol=symbol, feature_day=bars[i].day,
                                label_day=bars[i + 1].day, features=features,
                                label_bps=float(returns[i] * 10_000),
                                momentum_bps=float(twenty[-1] * 10_000)))
    return examples


def split_name(label_day: date) -> str:
    if label_day <= TRAIN_END:
        return "train"
    if label_day <= VALIDATION_END:
        return "validation"
    if label_day <= END:
        return "test"
    raise ValueError("label outside frozen window")


def fit_ridge(train_x: np.ndarray, train_y: np.ndarray, *, alpha: float = ALPHA
              ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if len(train_x) < 30 or len(train_x) != len(train_y):
        raise ValueError("insufficient or inconsistent training rows")
    mean = train_x.mean(axis=0)
    std = train_x.std(axis=0)
    std = np.where(std > 1e-12, std, 1.0)
    z = (train_x - mean) / std
    design = np.column_stack((np.ones(len(z)), z))
    penalty = np.eye(design.shape[1]) * alpha
    penalty[0, 0] = 0.0  # intercept is not penalized
    coefficients = np.linalg.solve(design.T @ design + penalty, design.T @ train_y)
    return coefficients, mean, std


def predict(x: np.ndarray, coefficients: np.ndarray, mean: np.ndarray,
            std: np.ndarray) -> np.ndarray:
    z = (x - mean) / std
    return np.column_stack((np.ones(len(z)), z)) @ coefficients


def metric_set(actual: np.ndarray, forecast: np.ndarray) -> dict[str, Any]:
    if len(actual) != len(forecast) or not len(actual):
        raise ValueError("metric vectors are empty or mismatched")
    if not np.isfinite(actual).all() or not np.isfinite(forecast).all():
        raise ValueError("non-finite model result")
    error = forecast - actual
    eligible = forecast != 0
    return {"n": int(len(actual)), "mae_bps": float(np.mean(np.abs(error))),
            "rmse_bps": float(np.sqrt(np.mean(error ** 2))),
            "direction_n": int(np.count_nonzero(eligible)),
            "direction_hits": int(np.count_nonzero((np.sign(forecast) == np.sign(actual)) & eligible)),
            "direction_hit_rate": (float(np.mean(np.sign(forecast[eligible]) ==
                                                   np.sign(actual[eligible]))) if np.any(eligible) else None)}


def load_pit_events(path: Path, *, symbol: str, examples: list[Example]) -> dict[date, float]:
    """Optional candidate event count; withhold unless full point-in-time coverage is attested.

    This does not certify news truth or complete historical coverage. The caller
    must supply a separately reviewed manifest before running a comparison.
    """
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "historical_event_availability_v1":
        raise ValueError("event availability contract is unknown")
    coverage = manifest.get("coverage_by_symbol", {}).get(symbol, {})
    if coverage.get("complete") is not True:
        raise ValueError(f"{symbol}: event coverage is not marked complete")
    first_cutoff = datetime.combine(min(e.feature_day for e in examples), time(16), NEW_YORK)
    last_cutoff = datetime.combine(max(e.feature_day for e in examples), time(16), NEW_YORK)
    start = datetime.fromisoformat(coverage["start"])
    end = datetime.fromisoformat(coverage["end"])
    if start.tzinfo is None or end.tzinfo is None or start > first_cutoff or end < last_cutoff:
        raise ValueError(f"{symbol}: event coverage does not span feature cutoffs")
    events: list[tuple[str, datetime]] = []
    for item in manifest.get("events", []):
        if symbol not in item.get("symbols", []):
            continue
        if item.get("availability_basis") not in {"SOURCE_PUBLISHED", "ARCHIVED_SNAPSHOT"}:
            raise ValueError("event has no acceptable historical availability basis")
        available = datetime.fromisoformat(item["available_at"])
        published = datetime.fromisoformat(item["source_published_at"])
        if available.tzinfo is None or published.tzinfo is None or available < published:
            raise ValueError("event timestamp is naive or predates source publication")
        events.append((str(item["event_id"]), available))
    if len({item[0] for item in events}) != len(events):
        raise ValueError("duplicate event IDs must be clustered before counting")
    counts: dict[date, float] = {}
    for example in examples:
        cutoff = datetime.combine(example.feature_day, time(16), NEW_YORK)
        window_start = cutoff - timedelta(days=7)
        counts[example.feature_day] = float(sum(window_start < instant <= cutoff
                                                 for _, instant in events))
    return counts


def run(input_dir: Path, private_output_dir: Path, public_summary_path: Path,
        *, event_manifest: Path | None = None) -> dict[str, Any]:
    config, source_config_hash = source_config(input_dir)
    frozen_config: dict[str, Any] = {
        "schema": SCHEMA, "source_config_sha256": source_config_hash,
        "symbols": list(SYMBOLS), "window": [START.isoformat(), END.isoformat()],
        "source_basis": "moomoo autype=1 forward-adjusted daily close; price return, not total return",
        "feature_names": list(FEATURE_NAMES), "feature_cutoff": "after feature-day close; 16:00 America/New_York is a retrospective proxy, not a captured availability timestamp",
        "label": "next observed trading-day close-to-close log return, basis points",
        "split_by_label_date": {"train_end": TRAIN_END.isoformat(),
                                "validation_end": VALIDATION_END.isoformat(),
                                "test_end": END.isoformat()},
        "model": {"type": "per-symbol ridge regression", "alpha": ALPHA,
                  "intercept_penalized": False, "standardization": "train only",
                  "hyperparameter_selection": "none"},
        "comparators": ["zero_return", "lag1_momentum"],
        "event_feature": "7-calendar-day available event count, only with complete reviewed manifest",
    }
    private_output_dir.mkdir(parents=True, exist_ok=True)
    (private_output_dir / "run_config.json").write_text(
        json.dumps(frozen_config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    config_sha = sha256_file(private_output_dir / "run_config.json")
    all_bars: dict[str, list[DailyBar]] = {}
    receipts: dict[str, dict[str, Any]] = {}
    for symbol in SYMBOLS:
        all_bars[symbol], receipts[symbol] = load_raw_bars(input_dir, symbol)
    union = set.union(*(set(b.day for b in bars) for bars in all_bars.values()))
    for symbol in SYMBOLS:
        missing = union - {b.day for b in all_bars[symbol]}
        receipts[symbol]["missing_vs_three_symbol_union"] = len(missing)
        if missing:
            raise ValueError(f"{symbol}: missing days versus same-market pilot union")
    results: dict[str, Any] = {}
    for symbol in SYMBOLS:
        examples = make_examples(symbol, all_bars[symbol])
        indices = {split: [i for i, e in enumerate(examples) if split_name(e.label_day) == split]
                   for split in ("train", "validation", "test")}
        if any(not rows for rows in indices.values()):
            raise ValueError(f"{symbol}: at least one split is empty")
        x = np.asarray([e.features for e in examples])
        y = np.asarray([e.label_bps for e in examples])
        train = indices["train"]
        coefficients, mean, std = fit_ridge(x[train], y[train])
        ridge_forecast = predict(x, coefficients, mean, std)
        zero_forecast = np.zeros(len(y))
        momentum_forecast = np.asarray([e.momentum_bps for e in examples])
        forecasts = {"ridge": ridge_forecast, "zero_return": zero_forecast,
                     "lag1_momentum": momentum_forecast}
        event_status: dict[str, Any] = {"status": "NOT_RUN", "reason": "no reviewed complete point-in-time event manifest"}
        if event_manifest is not None:
            try:
                counts = load_pit_events(event_manifest, symbol=symbol, examples=examples)
                event_x = np.column_stack((x, [counts[e.feature_day] for e in examples]))
                event_coef, event_mean, event_std = fit_ridge(event_x[train], y[train])
                forecasts["ridge_plus_event_count"] = predict(event_x, event_coef, event_mean, event_std)
                event_status = {"status": "RUN", "manifest_sha256": sha256_file(event_manifest),
                                "candidate_event_count": int(sum(counts.values()))}
            except (ValueError, KeyError, TypeError) as exc:
                event_status = {"status": "WITHHELD", "reason": str(exc)}
        split_results: dict[str, Any] = {}
        for split, rows in indices.items():
            subset = np.asarray(rows, dtype=int)
            split_results[split] = {
                "n": len(rows), "label_first": examples[rows[0]].label_day.isoformat(),
                "label_last": examples[rows[-1]].label_day.isoformat(),
                "metrics": {name: metric_set(y[subset], values[subset])
                            for name, values in forecasts.items()},
            }
        private_rows = [{"symbol": e.symbol, "feature_day": e.feature_day.isoformat(),
                         "label_day": e.label_day.isoformat(), "split": split_name(e.label_day),
                         "features": dict(zip(FEATURE_NAMES, e.features)), "label_bps": e.label_bps,
                         "forecasts_bps": {name: float(values[i]) for name, values in forecasts.items()}}
                        for i, e in enumerate(examples)]
        private_path = private_output_dir / f"{symbol.replace('.', '_')}_predictions.private.jsonl"
        private_path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in private_rows),
                                encoding="utf-8")
        results[symbol] = {"source": receipts[symbol], "example_count": len(examples),
                           "splits": split_results, "event_comparison": event_status,
                           "private_predictions_sha256": sha256_file(private_path)}
    private_receipt = {"schema": SCHEMA, "config": frozen_config,
                       "source_request": config, "config_sha256": config_sha,
                       "symbols": results, "limitations": [
                           "Retrospective downloaded forward-adjusted prices; historical first availability not established.",
                           "Price return excludes dividends and costs; no causal or live-prediction conclusion.",
                           "Three correlated pilot instruments and one fixed split are development evidence only."]}
    (private_output_dir / "run_receipt.private.json").write_text(
        json.dumps(private_receipt, indent=2) + "\n", encoding="utf-8")
    public = {"schema": SCHEMA, "status": "REAL_PRICE_BASELINE_RETROSPECTIVE_DEVELOPMENT",
              "source_request_sha256": source_config_hash, "baseline_config_sha256": config_sha,
              "config": frozen_config, "symbols": results,
              "time_alignment": "feature closes through t; label next observed trading day t+1; split by label date",
              "price_basis_limit": "post-hoc forward-adjusted daily close, not point-in-time total-return series",
              "news_incremental_status": ("RUN_OR_WITHHELD_PER_SYMBOL" if event_manifest else "NOT_RUN_NO_QUALIFIED_EVENT_COVERAGE"),
              "formal_financial_validation": "NOT_RUN"}
    public_summary_path.parent.mkdir(parents=True, exist_ok=True)
    public_summary_path.write_text(json.dumps(public, indent=2) + "\n", encoding="utf-8")
    return public


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--private-output-dir", type=Path, required=True)
    parser.add_argument("--public-summary", type=Path, required=True)
    parser.add_argument("--event-manifest", type=Path)
    args = parser.parse_args()
    summary = run(args.input_dir, args.private_output_dir, args.public_summary,
                  event_manifest=args.event_manifest)
    print(json.dumps({"status": summary["status"],
                      "symbols": {s: {"bars": d["source"]["bar_count"],
                                      "train": d["splits"]["train"]["n"],
                                      "validation": d["splits"]["validation"]["n"],
                                      "test": d["splits"]["test"]["n"]}
                                  for s, d in summary["symbols"].items()},
                      "news_incremental_status": summary["news_incremental_status"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
