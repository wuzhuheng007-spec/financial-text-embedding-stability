"""Paired count/text ablation on reproduced, frozen SEC development rows.

All labels and predictions stay under artifacts/. This module never opens a
June/July price row, downloads a source, or modifies the earlier A/B/C run.
It is deliberately gated on an independently saved P1 reproduction receipt.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import subprocess
from typing import Any

import numpy as np

from scripts.market_observer_historical_v1.direction_baseline import (
    L2_GRID, TRAIN_END, VALIDATION_END, choose_regularization,
    classification_metrics, fit_logistic, predict_up,
)
from scripts.market_observer_historical_v1.price_baseline import sha256_file
from scripts.market_observer_official_v1.text_comparison import (
    SYMBOLS, canonical_hash, fit_tfidf, load_config, transform_tfidf,
)


SCHEMA = "market_observer_official_incremental_v1"
BRANCHES = ("B", "B_count", "B_text", "B_both")
PAIRINGS = (("B_text", "B"), ("B_both", "B_count"), ("B_count", "B"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def read_gate(path: Path) -> dict[str, Any]:
    gate = json.loads(path.read_text(encoding="utf-8"))
    if gate.get("gate_status") != "PASS":
        raise ValueError("P1 reproduction gate has not passed")
    return gate


def read_qualified_rows(path: Path, symbol: str) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    days: list[date] = []
    for row in rows:
        if row.get("symbol") != symbol:
            raise ValueError("qualified row belongs to another symbol")
        day = date.fromisoformat(row["label_day"])
        if day > VALIDATION_END:
            raise ValueError("sealed June/July label row was supplied")
        if row.get("label_up") not in (0, 1):
            raise ValueError("nonbinary label")
        if len(row.get("price_features", [])) != 6:
            raise ValueError("old six-feature price contract changed")
        if row.get("sec_source_coverage_complete") is not True:
            raise ValueError("incomplete SEC source was coded as covered")
        days.append(day)
    if not rows or days != sorted(days) or len(days) != len(set(days)):
        raise ValueError("qualified rows are empty, duplicated or unsorted")
    if sum(d <= TRAIN_END for d in days) < 30 or sum(d > TRAIN_END for d in days) < 1:
        raise ValueError("insufficient frozen common sample")
    return rows


def brier_score(actual: np.ndarray, probability: np.ndarray) -> float:
    if not len(actual) or len(actual) != len(probability):
        raise ValueError("Brier inputs have incompatible lengths")
    if not np.all(np.isfinite(probability)) or np.any(probability < 0) or np.any(probability > 1):
        raise ValueError("invalid predicted probability")
    return float(np.mean((probability - actual) ** 2))


def augmented_metrics(actual: np.ndarray, predicted: np.ndarray,
                      probability: np.ndarray) -> dict[str, Any]:
    result = classification_metrics(actual, predicted)
    result["confusion"] = {
        "actual_down_predicted_down": int(np.count_nonzero((actual == 0) & (predicted == 0))),
        "actual_down_predicted_up": int(np.count_nonzero((actual == 0) & (predicted == 1))),
        "actual_up_predicted_down": int(np.count_nonzero((actual == 1) & (predicted == 0))),
        "actual_up_predicted_up": int(np.count_nonzero((actual == 1) & (predicted == 1))),
    }
    result["predicted_up_count"] = int(np.count_nonzero(predicted == 1))
    result["predicted_up_fraction"] = float(np.mean(predicted == 1))
    result["brier_score"] = brier_score(actual, probability)
    result["probability_mean"] = float(np.mean(probability))
    result["probability_min"] = float(np.min(probability))
    result["probability_max"] = float(np.max(probability))
    return result


def paired_counts(actual: np.ndarray, first: np.ndarray,
                  second: np.ndarray) -> dict[str, int]:
    if not len(actual) or len(actual) != len(first) or len(actual) != len(second):
        raise ValueError("paired comparison requires identical rows")
    a = first == actual
    b = second == actual
    return {
        "both_correct": int(np.count_nonzero(a & b)),
        "both_wrong": int(np.count_nonzero(~a & ~b)),
        "first_correct_second_wrong": int(np.count_nonzero(a & ~b)),
        "first_wrong_second_correct": int(np.count_nonzero(~a & b)),
    }


def event_reuse_summary(rows: list[dict[str, Any]], indices: np.ndarray) -> dict[str, Any]:
    usage: Counter[str] = Counter(event for i in indices for event in rows[int(i)]["event_ids"])
    with_event = sum(bool(rows[int(i)]["event_ids"]) for i in indices)
    return {
        "unique_event_groups": len(usage),
        "days_with_announcement": int(with_event),
        "days_without_announcement_verified_sec": int(len(indices) - with_event),
        "event_groups_reused_on_multiple_label_days": sum(n > 1 for n in usage.values()),
        "event_group_reuse_day_count_min": min(usage.values()) if usage else None,
        "event_group_reuse_day_count_max": max(usage.values()) if usage else None,
        "total_event_group_day_uses": int(sum(usage.values())),
    }


def _branch_matrices(rows: list[dict[str, Any]], train: np.ndarray, config: dict[str, Any]
                     ) -> tuple[dict[str, np.ndarray], list[str], np.ndarray]:
    tf = config["tfidf"]
    texts = [row["announcement_text"] for row in rows]
    vocab, idf = fit_tfidf([texts[int(i)] for i in train],
                           max_features=tf["max_features"],
                           min_df=tf["min_document_frequency"])
    if not vocab:
        raise ValueError("frozen training-only vocabulary is empty")
    text_x = transform_tfidf(texts, vocab, idf)
    price_x = np.asarray([row["price_features"] for row in rows], dtype=float)
    count_x = np.asarray([[row["announcement_count"],
                           int(row["sec_source_coverage_complete"])] for row in rows], dtype=float)
    if not np.all(np.isfinite(price_x)) or not np.all(np.isfinite(count_x)):
        raise ValueError("nonfinite feature")
    return {
        "B": price_x,
        "B_count": np.column_stack((price_x, count_x)),
        "B_text": np.column_stack((price_x, text_x)),
        "B_both": np.column_stack((price_x, text_x, count_x)),
    }, vocab, idf


def compare_one_symbol(rows: list[dict[str, Any]], config: dict[str, Any]
                       ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    train = np.asarray([i for i, row in enumerate(rows)
                        if date.fromisoformat(row["label_day"]) <= TRAIN_END], dtype=int)
    val = np.asarray([i for i, row in enumerate(rows)
                      if TRAIN_END < date.fromisoformat(row["label_day"]) <= VALIDATION_END], dtype=int)
    y = np.asarray([row["label_up"] for row in rows], dtype=int)
    if set(y[train]) != {0, 1} or set(y[val]) != {0, 1}:
        raise ValueError("common train or validation labels lack both classes")
    matrices, vocab, idf = _branch_matrices(rows, train, config)
    majority = int(np.count_nonzero(y[train]) > len(train) / 2)
    a_pred = np.full(len(val), majority, dtype=int)
    a_probability = np.full(len(val), float(np.mean(y[train])), dtype=float)
    branch_public: dict[str, Any] = {}
    candidates: dict[str, dict[float, dict[str, Any]]] = {}
    fitted_count = 0
    for name in BRANCHES:
        x = matrices[name]
        candidate_scores: dict[float, dict[str, Any]] = {}
        candidates[name] = {}
        for l2 in L2_GRID:
            fitted = fit_logistic(x[train], y[train], l2=l2)
            fitted_count += 1
            predicted, probability = predict_up(x[val], *fitted)
            candidate_scores[l2] = augmented_metrics(y[val], predicted, probability)
            candidates[name][l2] = {"pred": predicted, "prob": probability}
        selected = choose_regularization(candidate_scores)
        branch_public[name] = {
            "selected_l2": selected,
            "selected_validation": candidate_scores[selected],
            "all_l2_candidates": {str(l2): candidate_scores[l2] for l2 in L2_GRID},
            "feature_count": int(x.shape[1]),
            "train_matrix_sha256": hashlib.sha256(np.ascontiguousarray(x[train]).tobytes()).hexdigest(),
            "validation_matrix_sha256": hashlib.sha256(np.ascontiguousarray(x[val]).tobytes()).hexdigest(),
        }
    pair_public: dict[str, Any] = {}
    for first, second in PAIRINGS:
        per_l2: dict[str, Any] = {}
        for l2 in L2_GRID:
            f = candidates[first][l2]
            s = candidates[second][l2]
            per_l2[str(l2)] = {
                "paired_correctness": paired_counts(y[val], f["pred"], s["pred"]),
                "accuracy_difference_first_minus_second": same_l2_metric_difference(
                    branch_public, first, second, l2, "accuracy"),
                "balanced_accuracy_difference_first_minus_second": same_l2_metric_difference(
                    branch_public, first, second, l2, "balanced_accuracy"),
                "brier_difference_first_minus_second": same_l2_metric_difference(
                    branch_public, first, second, l2, "brier_score"),
                "probability_identical_within_1e_12": bool(np.allclose(
                    f["prob"], s["prob"], rtol=0, atol=1e-12)),
                "max_absolute_probability_difference": float(np.max(np.abs(f["prob"] - s["prob"]))),
            }
        fs = branch_public[first]["selected_l2"]
        ss = branch_public[second]["selected_l2"]
        pair_public[f"{first}_vs_{second}"] = {
            "same_l2": per_l2,
            "selected_l2_first": fs,
            "selected_l2_second": ss,
            "selected_pair_correctness": paired_counts(y[val],
                                                         candidates[first][fs]["pred"],
                                                         candidates[second][ss]["pred"]),
            "selected_brier_difference_first_minus_second": float(
                branch_public[first]["selected_validation"]["brier_score"]
                - branch_public[second]["selected_validation"]["brier_score"]),
            "selected_accuracy_difference_first_minus_second": float(
                branch_public[first]["selected_validation"]["accuracy"]
                - branch_public[second]["selected_validation"]["accuracy"]),
        }
    private_rows: list[dict[str, Any]] = []
    for j, i in enumerate(val):
        row = rows[int(i)]
        private = {"symbol": row["symbol"], "label_day": row["label_day"],
                   "actual_up": int(y[int(i)]), "input_text_sha256": row["input_text_sha256"],
                   "event_ids": row["event_ids"], "announcement_count": row["announcement_count"],
                   "A_predicted_up": int(a_pred[j]), "A_probability_up": float(a_probability[j]),
                   "candidates": {}}
        for name in BRANCHES:
            private["candidates"][name] = {
                str(l2): {"predicted_up": int(candidates[name][l2]["pred"][j]),
                          "probability_up": float(candidates[name][l2]["prob"][j])}
                for l2 in L2_GRID}
        private["paired_selected"] = {}
        private["paired_same_l2"] = {}
        for first, second in PAIRINGS:
            fs = branch_public[first]["selected_l2"]
            ss = branch_public[second]["selected_l2"]
            key = f"{first}_vs_{second}"
            private["paired_selected"][key] = {
                "first_correct": bool(candidates[first][fs]["pred"][j] == y[int(i)]),
                "second_correct": bool(candidates[second][ss]["pred"][j] == y[int(i)]),
                "first_probability_up": float(candidates[first][fs]["prob"][j]),
                "second_probability_up": float(candidates[second][ss]["prob"][j]),
            }
            private["paired_same_l2"][key] = {
                str(l2): {
                    "first_correct": bool(candidates[first][l2]["pred"][j] == y[int(i)]),
                    "second_correct": bool(candidates[second][l2]["pred"][j] == y[int(i)]),
                    "first_probability_up": float(candidates[first][l2]["prob"][j]),
                    "second_probability_up": float(candidates[second][l2]["prob"][j]),
                } for l2 in L2_GRID}
        private_rows.append(private)
    public = {
        "train_n": len(train), "validation_n": len(val),
        "train_class_distribution": {"down": int(np.count_nonzero(y[train] == 0)),
                                     "up": int(np.count_nonzero(y[train] == 1))},
        "validation_class_distribution": {"down": int(np.count_nonzero(y[val] == 0)),
                                          "up": int(np.count_nonzero(y[val] == 1))},
        "A": {"train_majority_up": majority,
              "validation": augmented_metrics(y[val], a_pred, a_probability)},
        "branches": branch_public,
        "pairings": pair_public,
        "tfidf_vocabulary_size": len(vocab),
        "tfidf_vocabulary_sha256": canonical_hash(vocab),
        "tfidf_idf_sha256": hashlib.sha256(idf.tobytes()).hexdigest(),
        "coverage_flag_constant": len(set(row["sec_source_coverage_complete"] for row in rows)) == 1,
        "train_announcement_reuse": event_reuse_summary(rows, train),
        "validation_announcement_reuse": event_reuse_summary(rows, val),
        "estimator_fit_count": fitted_count,
    }
    private_aux = {"vocabulary": vocab, "idf": [float(v) for v in idf],
                   "train_label_dates": [rows[int(i)]["label_day"] for i in train],
                   "validation_label_dates": [rows[int(i)]["label_day"] for i in val]}
    return public, private_rows, private_aux


def same_l2_metric_difference(branch_public: dict[str, Any], first: str,
                              second: str, l2: float, field: str) -> float:
    return float(branch_public[first]["all_l2_candidates"][str(l2)][field]
                 - branch_public[second]["all_l2_candidates"][str(l2)][field])


def pre_run_receipt(*, gate_path: Path, old_config_path: Path,
                    frozen_plan_path: Path, input_manifest_path: Path,
                    private_rows_dir: Path, private_output_dir: Path) -> dict[str, Any]:
    """Persist actual run provenance before any estimator is fitted."""
    repo = Path(__file__).resolve().parents[2]

    def git(*parts: str) -> bytes:
        return subprocess.check_output(["git", *parts], cwd=repo)

    code_paths = [Path(__file__).resolve(),
                  repo / "scripts/market_observer_official_v1/text_comparison.py",
                  repo / "scripts/market_observer_historical_v1/direction_baseline.py",
                  repo / "scripts/market_observer_historical_v1/price_baseline.py"]
    rows = {s: private_rows_dir / f"{s.replace('.', '_')}_qualified_rows.private.jsonl"
            for s in SYMBOLS}
    receipt = {
        "schema": "market_observer_official_incremental_pre_run_v1",
        "run_id": "incremental_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"),
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git("rev-parse", "HEAD").decode("ascii").strip(),
        "actual_code_sha256": {str(p.relative_to(repo)): sha256_file(p) for p in code_paths},
        "tracked_workspace_diff_sha256": hashlib.sha256(git("diff", "--binary", "HEAD", "--")).hexdigest(),
        "workspace_status_sha256": hashlib.sha256(git("status", "--porcelain=v1", "-uall")).hexdigest(),
        "untracked_files_not_in_tracked_diff": True,
        "old_frozen_config_sha256": sha256_file(old_config_path),
        "new_frozen_plan_sha256": sha256_file(frozen_plan_path),
        "input_corpus_manifest_sha256": sha256_file(input_manifest_path),
        "p1_gate_sha256": sha256_file(gate_path),
        "qualified_rows_sha256": {s: sha256_file(path) for s, path in rows.items()},
        "dependencies": {"python": platform.python_version(), "numpy": np.__version__},
        "random_seed": None,
        "random_seed_reason": "No random generator is used by the frozen deterministic IRLS or TF-IDF implementation.",
        "sealed_test": "June-July labels are not opened by this runner",
    }
    _write_json(private_output_dir / "incremental_pre_run.private.json", receipt)
    return receipt


def run(*, gate_path: Path, old_config_path: Path, frozen_plan_path: Path,
        input_manifest_path: Path, private_rows_dir: Path,
        private_output_dir: Path, public_path: Path) -> dict[str, Any]:
    read_gate(gate_path)
    config = load_config(old_config_path)
    frozen_plan = json.loads(frozen_plan_path.read_text(encoding="utf-8"))
    if not frozen_plan:
        raise ValueError("missing pre-run frozen plan")
    pre_run = pre_run_receipt(gate_path=gate_path, old_config_path=old_config_path,
                              frozen_plan_path=frozen_plan_path,
                              input_manifest_path=input_manifest_path,
                              private_rows_dir=private_rows_dir,
                              private_output_dir=private_output_dir)
    output: dict[str, Any] = {
        "schema": SCHEMA,
        "run_id": pre_run["run_id"],
        "run_started_at_utc": pre_run["recorded_at_utc"],
        "pre_run_receipt_sha256": sha256_file(private_output_dir / "incremental_pre_run.private.json"),
        "role": "REPEATED_DEVELOPMENT_VALIDATION_NOT_INDEPENDENT_TEST",
        "strict_historical_availability": "UNPROVEN_UNCHANGED",
        "test": "NO_JUNE_JULY_LABEL_READ_FIT_OR_SCORE",
        "p1_gate_sha256": sha256_file(gate_path),
        "old_frozen_config_sha256": sha256_file(old_config_path),
        "new_frozen_plan_sha256": sha256_file(frozen_plan_path),
        "l2_interpretation": "positive coefficient on squared non-intercept coefficients in IRLS; larger l2 means stronger penalty",
        "l2_grid": list(L2_GRID),
        "selection": "validation balanced accuracy, tie to larger l2",
        "symbols": {},
        "full_incremental_pipeline_runs": 1,
        "estimator_fit_count": 0,
    }
    for symbol in SYMBOLS:
        path = private_rows_dir / f"{symbol.replace('.', '_')}_qualified_rows.private.jsonl"
        rows = read_qualified_rows(path, symbol)
        public, private_predictions, auxiliary = compare_one_symbol(rows, config)
        private_path = private_output_dir / f"{symbol.replace('.', '_')}_incremental_predictions.private.jsonl"
        private_path.parent.mkdir(parents=True, exist_ok=True)
        private_path.write_text("".join(json.dumps(row, sort_keys=True) + "\n"
                                        for row in private_predictions), encoding="utf-8")
        _write_json(private_output_dir / f"{symbol.replace('.', '_')}_incremental_aux.private.json", auxiliary)
        public["qualified_rows_sha256"] = sha256_file(path)
        public["private_predictions_sha256"] = sha256_file(private_path)
        output["symbols"][symbol] = public
        output["estimator_fit_count"] += public["estimator_fit_count"]
    _write_json(private_output_dir / "incremental_receipt.private.json", output)
    _write_json(public_path, output)
    return output


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--gate-path", type=Path, required=True)
    p.add_argument("--old-config-path", type=Path, required=True)
    p.add_argument("--frozen-plan-path", type=Path, required=True)
    p.add_argument("--input-manifest-path", type=Path, required=True)
    p.add_argument("--private-rows-dir", type=Path, required=True)
    p.add_argument("--private-output-dir", type=Path, required=True)
    p.add_argument("--public-path", type=Path, required=True)
    args = p.parse_args()
    receipt = run(gate_path=args.gate_path, old_config_path=args.old_config_path,
                  frozen_plan_path=args.frozen_plan_path,
                  input_manifest_path=args.input_manifest_path,
                  private_rows_dir=args.private_rows_dir,
                  private_output_dir=args.private_output_dir, public_path=args.public_path)
    print(json.dumps({"schema": SCHEMA, "symbols": {
        s: {"train": v["train_n"], "validation": v["validation_n"],
            "fit_count": v["estimator_fit_count"]} for s, v in receipt["symbols"].items()},
        "estimator_fit_count": receipt["estimator_fit_count"]}, sort_keys=True))


if __name__ == "__main__":
    main()
