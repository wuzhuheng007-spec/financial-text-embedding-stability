"""Read-only integrity check of the single SEC-text development comparison."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path
from typing import Any

import numpy as np

from scripts.market_observer_historical_v1.direction_baseline import (
    L2_GRID, TRAIN_END, VALIDATION_END, classification_metrics,
)
from scripts.market_observer_historical_v1.price_baseline import sha256_file


def _read_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def validate(*, public_path: Path, private_dir: Path, config_path: Path,
             manifest_path: Path, calendar_path: Path,
             runner_path: Path, output_path: Path) -> dict[str, Any]:
    public = json.loads(public_path.read_text(encoding="utf-8"))
    if public["config_sha256"] != sha256_file(config_path) or \
            public["corpus_manifest_sha256"] != sha256_file(manifest_path) or \
            public["calendar_sha256"] != sha256_file(calendar_path):
        raise ValueError("comparison source/config hashes do not match current frozen files")
    if public["test"] != "NO_JUNE_JULY_OHLCV_EXTRACTED_LABELED_FIT_OR_SCORED_CONTAMINATED":
        raise ValueError("test isolation state changed")
    checks: dict[str, Any] = {}
    for symbol, summary in public["symbols"].items():
        stem = symbol.replace(".", "_")
        row_path = private_dir / f"{stem}_qualified_rows.private.jsonl"
        ex_path = private_dir / f"{stem}_excluded_rows.private.jsonl"
        prediction_path = private_dir / f"{stem}_validation_predictions.private.jsonl"
        if (summary["qualified_rows_sha256"] != sha256_file(row_path) or
                summary["excluded_rows_sha256"] != sha256_file(ex_path) or
                summary["private_predictions_sha256"] != sha256_file(prediction_path)):
            raise ValueError(f"{symbol}: private result hash mismatch")
        rows, excluded, predictions = map(_read_lines, (row_path, ex_path, prediction_path))
        train = [row for row in rows if date.fromisoformat(row["label_day"]) <= TRAIN_END]
        val = [row for row in rows if TRAIN_END < date.fromisoformat(row["label_day"]) <= VALIDATION_END]
        if len(train) != summary["qualified_train_n"] or len(val) != summary["qualified_validation_n"]:
            raise ValueError(f"{symbol}: qualified split count mismatch")
        if any(date.fromisoformat(row["label_day"]) > VALIDATION_END for row in rows + excluded + predictions):
            raise ValueError(f"{symbol}: test-window result present")
        if len(predictions) != len(val) or \
                [row["label_day"] for row in predictions] != [row["label_day"] for row in val]:
            raise ValueError(f"{symbol}: validation prediction alignment mismatch")
        if {event for row in train for event in row["event_ids"]} & \
                {event for row in val for event in row["event_ids"]}:
            raise ValueError(f"{symbol}: announcement event leaks across chronological split")
        observed_ex = Counter(row["reason"] for row in excluded)
        if dict(observed_ex) != summary["excluded_by_reason"]:
            raise ValueError(f"{symbol}: exclusion reason mismatch")
        public_dates = summary["excluded_label_dates_by_reason"]
        for split in ("train", "validation"):
            for reason, dates in public_dates[split].items():
                target = sorted(row["label_day"] for row in excluded
                                if row["reason"] == reason and
                                ("train" if date.fromisoformat(row["label_day"]) <= TRAIN_END
                                 else "validation") == split)
                if dates != target:
                    raise ValueError(f"{symbol}: excluded label dates mismatch")
        actual = np.asarray([row["actual_up"] for row in predictions], dtype=int)
        comparison = summary["comparison"]
        for method in ("A", "B", "C"):
            predicted = np.asarray([row[f"{method}_predicted_up"] for row in predictions], dtype=int)
            computed = classification_metrics(actual, predicted)
            if computed != comparison[method]["validation"]:
                raise ValueError(f"{symbol}: {method} metrics mismatch private predictions")
        for method in ("B", "C"):
            grid = comparison[method]["all_validation_candidates"]
            if set(grid) != {str(value) for value in L2_GRID}:
                raise ValueError(f"{symbol}: missing frozen L2 candidate")
            if not all(item["n"] == len(val) for item in grid.values()):
                raise ValueError(f"{symbol}: L2 candidate uses different validation sample")
        if sum(row["announcement_count"] > 0 for row in val) != comparison["validation_with_announcement"]:
            raise ValueError(f"{symbol}: event-present validation count mismatch")
        checks[symbol] = {"validation_predictions_checked": len(predictions),
                          "common_train_checked": len(train),
                          "common_validation_checked": len(val),
                          "excluded_dates_checked": len(excluded),
                          "all_candidate_metrics_present": True,
                          "A_B_C_metrics_recomputed_from_private_predictions": True,
                          "no_cross_split_event_id": True,
                          "no_test_window_rows": True}
    result = {"schema": "market_observer_official_text_comparison_validation_v1",
              "status": "PASS", "comparison_receipt_sha256": sha256_file(public_path),
              "config_sha256": sha256_file(config_path),
              "corpus_manifest_sha256": sha256_file(manifest_path),
              "calendar_sha256": sha256_file(calendar_path),
              "released_runner_sha256": sha256_file(runner_path),
              "execution_runner_sha256_recorded_at_fit": False,
              "runner_provenance_note": "Post-fit receipt-only source edits were reverted; this hash is the reconstructed execution source, without a contemporaneous runtime code hash.",
              "real_model_fit_count": 1,
              "independent_replication": False,
              "checks": checks}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("public-path", "private-dir", "config-path", "manifest-path",
                 "calendar-path", "runner-path", "output-path"):
        parser.add_argument("--" + flag, type=Path, required=True)
    args = parser.parse_args()
    receipt = validate(**vars(args))
    print(json.dumps({"status": receipt["status"], "symbols": receipt["checks"]}))


if __name__ == "__main__":
    main()
