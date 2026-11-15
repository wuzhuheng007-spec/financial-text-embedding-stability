"""No-fit replay of incremental candidate and paired validation receipts."""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
from typing import Any

import numpy as np

from scripts.market_observer_historical_v1.direction_baseline import L2_GRID
from scripts.market_observer_historical_v1.price_baseline import sha256_file
from scripts.market_observer_official_repro_v1.incremental_comparison import (
    BRANCHES, PAIRINGS, augmented_metrics, paired_counts,
)


def _close(a: float, b: float, *, atol: float = 1e-12) -> bool:
    return abs(float(a) - float(b)) <= atol


def validate(public_path: Path, private_dir: Path, old_result_path: Path) -> dict[str, Any]:
    public = json.loads(public_path.read_text(encoding="utf-8"))
    old = json.loads(old_result_path.read_text(encoding="utf-8"))
    if public.get("schema") != "market_observer_official_incremental_v1":
        raise ValueError("unexpected incremental schema")
    if public.get("test") != "NO_JUNE_JULY_LABEL_READ_FIT_OR_SCORE":
        raise ValueError("sealed test status missing")
    checked = 0
    by_symbol: dict[str, Any] = {}
    for symbol, summary in public["symbols"].items():
        file = private_dir / f"{symbol.replace('.', '_')}_incremental_predictions.private.jsonl"
        if sha256_file(file) != summary["private_predictions_sha256"]:
            raise ValueError("private row receipt hash differs")
        rows = [json.loads(line) for line in file.read_text(encoding="utf-8").splitlines()]
        if len(rows) != summary["validation_n"]:
            raise ValueError("validation row count differs")
        days = [date.fromisoformat(row["label_day"]) for row in rows]
        if len(set(days)) != len(days) or days != sorted(days) or any(d >= date(2026, 6, 1) for d in days):
            raise ValueError("validation dates duplicated, unordered or include sealed test")
        y = np.asarray([row["actual_up"] for row in rows], dtype=int)
        for name in BRANCHES:
            branch = summary["branches"][name]
            for l2 in L2_GRID:
                key = str(l2)
                pred = np.asarray([row["candidates"][name][key]["predicted_up"] for row in rows], dtype=int)
                prob = np.asarray([row["candidates"][name][key]["probability_up"] for row in rows], dtype=float)
                recomputed = augmented_metrics(y, pred, prob)
                saved = branch["all_l2_candidates"][key]
                for field in ("accuracy", "balanced_accuracy", "brier_score",
                              "predicted_up_fraction", "probability_mean"):
                    if not _close(recomputed[field], saved[field]):
                        raise ValueError(f"{symbol}/{name}/{key}/{field} differs")
                if recomputed["confusion"] != saved["confusion"]:
                    raise ValueError("confusion table differs")
                checked += 1
            selected = branch["selected_l2"]
            if branch["selected_validation"] != branch["all_l2_candidates"][str(selected)]:
                raise ValueError("selected metrics differ from candidate grid")
        for first, second in PAIRINGS:
            pair = summary["pairings"][f"{first}_vs_{second}"]
            for l2 in L2_GRID:
                key = str(l2)
                first_pred = np.asarray([row["candidates"][first][key]["predicted_up"] for row in rows], dtype=int)
                second_pred = np.asarray([row["candidates"][second][key]["predicted_up"] for row in rows], dtype=int)
                if paired_counts(y, first_pred, second_pred) != pair["same_l2"][key]["paired_correctness"]:
                    raise ValueError("same-L2 paired correctness differs")
            fs = str(pair["selected_l2_first"])
            ss = str(pair["selected_l2_second"])
            fp = np.asarray([row["candidates"][first][fs]["predicted_up"] for row in rows], dtype=int)
            sp = np.asarray([row["candidates"][second][ss]["predicted_up"] for row in rows], dtype=int)
            if paired_counts(y, fp, sp) != pair["selected_pair_correctness"]:
                raise ValueError("selected paired correctness differs")
        prior = old["symbols"][symbol]["comparison"]
        if summary["tfidf_vocabulary_sha256"] != prior["tfidf_vocabulary_sha256"] or \
                summary["tfidf_idf_sha256"] != prior["tfidf_idf_sha256"]:
            raise ValueError("training-only vocabulary or IDF changed")
        for name, old_name in (("B", "B"), ("B_both", "C")):
            branch = summary["branches"][name]
            previous = prior[old_name]
            if branch["selected_l2"] != previous["selected_l2"]:
                raise ValueError("old B/C selected L2 changed")
            for l2 in L2_GRID:
                now = branch["all_l2_candidates"][str(l2)]
                before = previous["all_validation_candidates"][str(l2)]
                for field in ("accuracy", "balanced_accuracy"):
                    if not _close(now[field], before[field]):
                        raise ValueError("old B/C candidate metric changed")
        by_symbol[symbol] = {"validation_rows": len(rows),
                             "candidate_metric_rows_checked": len(BRANCHES) * len(L2_GRID),
                             "paired_comparisons_checked": len(PAIRINGS),
                             "old_B_and_C_consistent": True,
                             "vocabulary_and_idf_consistent": True}
    return {"schema": "market_observer_official_incremental_validation_v1",
            "status": "PASS", "public_result_sha256": sha256_file(public_path),
            "old_result_sha256": sha256_file(old_result_path),
            "candidate_metric_rows_checked": checked,
            "semantic_financial_validity": "NOT_ESTABLISHED_BY_THIS_SOFTWARE_REPLAY",
            "symbols": by_symbol}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--public-path", type=Path, required=True)
    p.add_argument("--private-dir", type=Path, required=True)
    p.add_argument("--old-result-path", type=Path, required=True)
    p.add_argument("--output-path", type=Path, required=True)
    args = p.parse_args()
    result = validate(args.public_path, args.private_dir, args.old_result_path)
    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    args.output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"],
                      "candidate_metric_rows_checked": result["candidate_metric_rows_checked"]}))


if __name__ == "__main__":
    main()
