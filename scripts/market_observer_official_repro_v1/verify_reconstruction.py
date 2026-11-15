"""Read-only matrix and probability checks for a completed P1 reproduction.

This does not fit an estimator. Old matrices and probabilities were not saved;
matrix comparisons reconstruct old inputs from saved old rows and frozen code.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from scripts.market_observer_official_v1 import text_comparison as tc


ROOT = Path(__file__).resolve().parents[2]
OLD = ROOT / "artifacts/market_observer_official_20261115/text_comparison_v1"
NEW = ROOT / "artifacts/market_observer_official_repro_20261115"
PUBLIC = ROOT / "results/market_observer_official_repro_20261115"
CONFIG = ROOT / "configs/market_observer_official_text_v1.json"


def load_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def matrix_hash(x: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(x).tobytes()).hexdigest()


def main() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    instrumentation = json.loads((NEW / "instrumentation.private.json").read_text(encoding="utf-8"))
    gate = json.loads((NEW / "gate.private.json").read_text(encoding="utf-8"))
    if gate["gate_status"] != "PASS":
        raise ValueError("P1 gate is not PASS")
    report = {"schema": "market_observer_official_repro_matrix_audit_v1",
              "run_id": gate["run_id"], "source": "old saved qualified rows; current actual fit-time matrix hashes",
              "old_fit_time_matrix_comparison": "UNAVAILABLE_NOT_SAVED",
              "old_fit_time_probability_comparison": "UNAVAILABLE_NOT_SAVED",
              "new_estimator_fit_calls": 0, "symbols": {}}
    for symbol in tc.SYMBOLS:
        stem = symbol.replace(".", "_")
        old_rows = load_lines(OLD / f"{stem}_qualified_rows.private.jsonl")
        new_rows = load_lines(NEW / f"{stem}_qualified_rows.private.jsonl")
        assert old_rows == new_rows
        train, val = tc._split_indices(old_rows)
        tf = config["tfidf"]
        vocab, idf = tc.fit_tfidf([old_rows[int(i)]["announcement_text"] for i in train],
                                  max_features=tf["max_features"],
                                  min_df=tf["min_document_frequency"])
        text_x = tc.transform_tfidf([r["announcement_text"] for r in old_rows], vocab, idf)
        price_x = np.asarray([r["price_features"] for r in old_rows], dtype=float)
        extra_x = np.asarray([[r["announcement_count"], int(r["sec_source_coverage_complete"])]
                              for r in old_rows], dtype=float)
        c_x = np.column_stack((price_x, text_x, extra_x))
        instr = instrumentation["symbols"][symbol]
        old_predictions = load_lines(OLD / f"{stem}_validation_predictions.private.jsonl")
        new_predictions = load_lines(NEW / f"{stem}_validation_predictions.private.jsonl")
        checks = {
            "old_vs_new_rows_exact": old_rows == new_rows,
            "reconstructed_vocabulary_order_exact": vocab == instr["tfidf"]["vocabulary"],
            "reconstructed_idf_within_frozen_tolerance": bool(np.allclose(
                idf, np.asarray(instr["tfidf"]["idf"]), atol=1e-10, rtol=0)),
            "reconstructed_old_text_matrix_matches_current_hash": matrix_hash(text_x) == instr["transformed"]["matrix_sha256"],
            "reconstructed_old_B_train_matrix_matches_current_hash": matrix_hash(price_x[train]) == instr["B"]["train_x_sha256"],
            "reconstructed_old_B_validation_matrix_matches_current_hash": matrix_hash(price_x[val]) == instr["B"]["validation_x_sha256"],
            "reconstructed_old_C_train_matrix_matches_current_hash": matrix_hash(c_x[train]) == instr["C"]["train_x_sha256"],
            "reconstructed_old_C_validation_matrix_matches_current_hash": matrix_hash(c_x[val]) == instr["C"]["validation_x_sha256"],
            "old_vs_new_saved_classes_exact": old_predictions == new_predictions,
        }
        for method in ("B", "C"):
            probs = np.asarray(instr[method]["probabilities"], dtype=float)
            classes = np.asarray(instr[method]["classes"], dtype=int)
            checks[f"current_{method}_probability_threshold_matches_classes"] = bool(
                len(probs) == len(new_predictions) and
                np.array_equal((probs >= 0.5).astype(int), classes) and
                np.array_equal(classes, [row[f"{method}_predicted_up"] for row in new_predictions]))
        report["symbols"][symbol] = {"checks": checks, "validation_probability_count": len(new_predictions)}
    report["status"] = "PASS" if all(all(v["checks"].values()) for v in report["symbols"].values()) else "FAIL"
    (NEW / "matrix_audit.private.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (PUBLIC / "matrix_audit_redacted.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "new_estimator_fit_calls": 0,
                      "symbols": {k: v["validation_probability_count"] for k, v in report["symbols"].items()}}, sort_keys=True))


if __name__ == "__main__":
    main()
