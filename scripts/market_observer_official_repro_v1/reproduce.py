"""One bounded reproduction of the saved SEC date-proxy A/B/C development run.

The old runner and cached inputs are unchanged. This wrapper records execution
provenance before fitting, observes the original estimator calls, and compares
only train/validation artifacts. Private rows and model probabilities stay local.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import random
import subprocess
import sys
import uuid

import numpy as np

from scripts.market_observer_historical_v1 import direction_baseline as db
from scripts.market_observer_official_v1 import text_comparison as tc


ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "artifacts/market_observer_official_repro_20261115"
PUBLIC = ROOT / "results/market_observer_official_repro_20261115"
OLD_PRIVATE = ROOT / "artifacts/market_observer_official_20261115/text_comparison_v1"
OLD_PUBLIC = ROOT / "results/market_observer_official_20261115/text_comparison_redacted.json"
PRICE = ROOT / "artifacts/market_observer_historical_20261115"
CALENDAR = PRICE / "US_trading_days.raw.json"
MANIFEST = ROOT / "artifacts/market_observer_official_20261115/corpus_manifest.json"
CONFIG = ROOT / "configs/market_observer_official_text_v1.json"
PLAN = ROOT / "configs/market_observer_official_repro_v1.json"
CODE = [Path(__file__), Path(tc.__file__), Path(db.__file__),
        ROOT / "scripts/market_observer_historical_v1/price_baseline.py"]
SEED = 1729


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT, stderr=subprocess.STDOUT)


def write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def load_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def first_difference(old: object, new: object, path: str = "", *, atol: float = 0.0):
    """First deterministic mismatch; numerical comparisons have zero relative tolerance."""
    if isinstance(old, dict) and isinstance(new, dict):
        if old.keys() != new.keys():
            return {"path": path or "<root>", "reason": "KEY_SET", "old": sorted(old), "new": sorted(new)}
        for key in old:
            issue = first_difference(old[key], new[key], f"{path}.{key}" if path else key, atol=atol)
            if issue:
                return issue
        return None
    if isinstance(old, list) and isinstance(new, list):
        if len(old) != len(new):
            return {"path": path, "reason": "LENGTH", "old": len(old), "new": len(new)}
        for i, (a, b) in enumerate(zip(old, new)):
            issue = first_difference(a, b, f"{path}[{i}]", atol=atol)
            if issue:
                return issue
        return None
    if isinstance(old, (int, float)) and isinstance(new, (int, float)) and not isinstance(old, bool) and not isinstance(new, bool):
        if abs(old - new) <= atol:
            return None
    elif old == new:
        return None
    return {"path": path, "reason": "VALUE", "old": old, "new": new}


def preflight() -> dict:
    if PRIVATE.exists() and any(PRIVATE.iterdir()):
        raise FileExistsError("reproduction directory already contains files; refuse to overwrite")
    if not all(p.exists() for p in [*CODE, OLD_PUBLIC, CALENDAR, MANIFEST, CONFIG, PLAN]):
        raise FileNotFoundError("a frozen input or code file is missing")
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    assert plan["float_comparison"] == {
        "relative_tolerance": 0.0, "idf_absolute_tolerance": 1e-10,
        "feature_matrix_absolute_tolerance": 1e-10,
        "probability_absolute_tolerance": 1e-10,
        "metric_absolute_tolerance": 1e-12}
    assert plan["exact_comparison"] == ["sample_dates", "excluded_dates_and_reasons", "vocabulary_order", "selected_l2", "per_row_classification"]
    status = git("status", "--porcelain=v1", "-uall")
    diff = git("diff", "--binary")
    staged = git("diff", "--cached", "--binary")
    inputs = [CONFIG, PLAN, MANIFEST, CALENDAR, OLD_PUBLIC,
              PRICE / "US_NVDA_page1.raw.json", PRICE / "US_MU_page1.raw.json"]
    evidence = {
        "schema": "market_observer_official_repro_preflight_v1",
        "run_id": str(uuid.uuid4()),
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_head": git("rev-parse", "HEAD").decode().strip(),
        "branch": git("branch", "--show-current").decode().strip(),
        "actual_code_sha256": {str(p.relative_to(ROOT)).replace("\\", "/"): digest(p) for p in CODE},
        "input_sha256": {str(p.relative_to(ROOT)).replace("\\", "/"): digest(p) for p in inputs},
        "workspace": {
            "status_sha256": hashlib.sha256(status).hexdigest(),
            "status_entry_count": len(status.splitlines()),
            "tracked_diff_sha256": hashlib.sha256(diff).hexdigest(),
            "staged_diff_sha256": hashlib.sha256(staged).hexdigest(),
        },
        "runtime": {"python": sys.version, "python_executable": sys.executable,
                    "platform": platform.platform(), "numpy": np.__version__},
        "seed": SEED,
        "seed_role": "frozen but fitting algorithm is deterministic and does not sample",
        "comparison_contract": plan["float_comparison"],
        "exact_comparison": plan["exact_comparison"],
        "old_fit_time_code_hash_recorded": False,
        "sealed_test_period": plan["sealed_test"],
    }
    write_json(PRIVATE / "preflight.private.json", evidence)
    write_json(PUBLIC / "preflight_redacted.json", {
        k: v for k, v in evidence.items() if k not in {"runtime"}} | {
        "runtime": {"python_version": platform.python_version(), "numpy": np.__version__}})
    return evidence


def run_once(pre: dict) -> dict:
    random.seed(SEED)
    np.random.seed(SEED)
    fit_calls: list[dict] = []
    selected: list[dict] = []
    vocabularies: list[dict] = []
    transformed: list[dict] = []
    original_fit = db.fit_logistic
    original_select = tc.fit_and_select
    original_vocab = tc.fit_tfidf
    original_transform = tc.transform_tfidf

    def observed_fit(x, y, *, l2):
        fit_calls.append({"ordinal": len(fit_calls) + 1, "l2": l2,
                          "train_rows": len(y), "features": int(x.shape[1])})
        return original_fit(x, y, l2=l2)

    def observed_select(train_x, train_y, val_x, val_y):
        chosen_l2, fitted, grid = original_select(train_x, train_y, val_x, val_y)
        classes, probabilities = db.predict_up(val_x, *fitted)
        selected.append({"selected_l2": chosen_l2, "classes": classes.tolist(),
                         "probabilities": probabilities.tolist(),
                         "train_x_sha256": hashlib.sha256(np.ascontiguousarray(train_x).tobytes()).hexdigest(),
                         "validation_x_sha256": hashlib.sha256(np.ascontiguousarray(val_x).tobytes()).hexdigest(),
                         "feature_count": int(train_x.shape[1])})
        return chosen_l2, fitted, grid

    def observed_vocab(train_texts, *, max_features, min_df):
        vocab, idf = original_vocab(train_texts, max_features=max_features, min_df=min_df)
        vocabularies.append({"vocabulary": vocab, "idf": idf.tolist()})
        return vocab, idf

    def observed_transform(texts, vocab, idf):
        x = original_transform(texts, vocab, idf)
        transformed.append({"shape": list(x.shape), "matrix_sha256": hashlib.sha256(x.tobytes()).hexdigest()})
        return x

    db.fit_logistic = observed_fit
    tc.fit_and_select = observed_select
    tc.fit_tfidf = observed_vocab
    tc.transform_tfidf = observed_transform
    try:
        result = tc.run(price_dir=PRICE, calendar_path=CALENDAR, manifest_path=MANIFEST,
                        config_path=CONFIG, private_dir=PRIVATE,
                        public_path=PUBLIC / "comparison_redacted.json", fit=True)
    finally:
        db.fit_logistic = original_fit
        tc.fit_and_select = original_select
        tc.fit_tfidf = original_vocab
        tc.transform_tfidf = original_transform
    if len(fit_calls) != 12 or len(selected) != 4 or len(vocabularies) != 2:
        raise AssertionError("unexpected fit/instrumentation count")
    symbols = list(tc.SYMBOLS)
    observations = {symbol: {
        "B": selected[2 * i], "C": selected[2 * i + 1],
        "tfidf": vocabularies[i], "transformed": transformed[i]}
        for i, symbol in enumerate(symbols)}
    write_json(PRIVATE / "instrumentation.private.json", {
        "run_id": pre["run_id"], "full_pipeline_executions": 1,
        "estimator_fit_calls": fit_calls, "symbols": observations})
    return result


def compare(pre: dict, new: dict) -> dict:
    old = json.loads(OLD_PUBLIC.read_text(encoding="utf-8"))
    instr = json.loads((PRIVATE / "instrumentation.private.json").read_text(encoding="utf-8"))
    tol = pre["comparison_contract"]
    result = {"schema": "market_observer_official_repro_gate_v1",
              "run_id": pre["run_id"], "gate_status": "PASS",
              "gate_rule": "all directly comparable old outputs must match; old unrecorded fields remain unavailable",
              "old_original_fit_time_code_hash": "NOT_RECORDED",
              "old_original_row_probabilities": "NOT_RECORDED_DIRECT_COMPARISON_UNAVAILABLE",
              "old_original_feature_matrices": "NOT_RECORDED_DIRECT_COMPARISON_UNAVAILABLE",
              "full_pipeline_executions": 1,
              "estimator_fit_calls": len(instr["estimator_fit_calls"]),
              "symbols": {}, "first_mismatch": None,
              "contaminated_test": "NOT_READ_LABELED_OR_SCORED"}
    receipt_issue = first_difference(old, new, atol=tol["metric_absolute_tolerance"])
    if receipt_issue:
        result["first_mismatch"] = {"stage": "aggregate_public_receipt", **receipt_issue}
    for symbol in tc.SYMBOLS:
        stem = symbol.replace(".", "_")
        detail = {"old_qualified_rows_sha256": digest(OLD_PRIVATE / f"{stem}_qualified_rows.private.jsonl"),
                  "new_qualified_rows_sha256": digest(PRIVATE / f"{stem}_qualified_rows.private.jsonl"),
                  "old_excluded_rows_sha256": digest(OLD_PRIVATE / f"{stem}_excluded_rows.private.jsonl"),
                  "new_excluded_rows_sha256": digest(PRIVATE / f"{stem}_excluded_rows.private.jsonl"),
                  "old_predictions_sha256": digest(OLD_PRIVATE / f"{stem}_validation_predictions.private.jsonl"),
                  "new_predictions_sha256": digest(PRIVATE / f"{stem}_validation_predictions.private.jsonl")}
        old_rows = load_lines(OLD_PRIVATE / f"{stem}_qualified_rows.private.jsonl")
        new_rows = load_lines(PRIVATE / f"{stem}_qualified_rows.private.jsonl")
        old_ex = load_lines(OLD_PRIVATE / f"{stem}_excluded_rows.private.jsonl")
        new_ex = load_lines(PRIVATE / f"{stem}_excluded_rows.private.jsonl")
        old_pred = load_lines(OLD_PRIVATE / f"{stem}_validation_predictions.private.jsonl")
        new_pred = load_lines(PRIVATE / f"{stem}_validation_predictions.private.jsonl")
        checks = {
            "sample_dates_exact": [r["label_day"] for r in old_rows] == [r["label_day"] for r in new_rows],
            "excluded_dates_reasons_exact": old_ex == new_ex,
            "price_and_text_rows_exact": old_rows == new_rows,
            "validation_classes_exact": old_pred == new_pred,
            "selected_l2_exact": all(old["symbols"][symbol]["comparison"][method]["selected_l2"] ==
                                     new["symbols"][symbol]["comparison"][method]["selected_l2"]
                                     for method in ("B", "C")),
            "vocabulary_hash_exact": old["symbols"][symbol]["comparison"]["tfidf_vocabulary_sha256"] ==
                                     new["symbols"][symbol]["comparison"]["tfidf_vocabulary_sha256"],
            "idf_hash_exact": old["symbols"][symbol]["comparison"]["tfidf_idf_sha256"] ==
                              new["symbols"][symbol]["comparison"]["tfidf_idf_sha256"],
            "all_grid_and_aggregate_metrics_within_tolerance": first_difference(
                old["symbols"][symbol]["comparison"], new["symbols"][symbol]["comparison"],
                atol=tol["metric_absolute_tolerance"]) is None,
        }
        # Rebuild old feature arrays from saved old rows: this is NOT a direct
        # comparison to old fit-time matrices, which were never persisted.
        train, val = tc._split_indices(old_rows)
        tf = json.loads(CONFIG.read_text(encoding="utf-8"))["tfidf"]
        old_vocab, old_idf = tc.fit_tfidf([old_rows[int(i)]["announcement_text"] for i in train],
                                          max_features=tf["max_features"],
                                          min_df=tf["min_document_frequency"])
        new_vocab = instr["symbols"][symbol]["tfidf"]["vocabulary"]
        new_idf = np.asarray(instr["symbols"][symbol]["tfidf"]["idf"])
        checks["reconstructed_old_vocabulary_order_exact"] = old_vocab == new_vocab
        checks["reconstructed_old_idf_within_tolerance"] = bool(np.allclose(old_idf, new_idf,
            atol=tol["idf_absolute_tolerance"], rtol=0))
        old_text_x = tc.transform_tfidf([r["announcement_text"] for r in old_rows], old_vocab, old_idf)
        new_text_x = tc.transform_tfidf([r["announcement_text"] for r in new_rows], new_vocab, new_idf)
        checks["reconstructed_old_text_matrix_within_tolerance"] = bool(np.allclose(
            old_text_x, new_text_x, atol=tol["feature_matrix_absolute_tolerance"], rtol=0))
        detail["checks"] = checks
        detail["qualified_train_n"] = len(train)
        detail["qualified_validation_n"] = len(val)
        detail["reconstructed_old_text_matrix_sha256"] = hashlib.sha256(old_text_x.tobytes()).hexdigest()
        detail["current_actual_text_matrix_sha256"] = instr["symbols"][symbol]["transformed"]["matrix_sha256"]
        detail["current_probabilities_sha256"] = {method: hashlib.sha256(np.asarray(
            instr["symbols"][symbol][method]["probabilities"], dtype=float).tobytes()).hexdigest()
            for method in ("B", "C")}
        result["symbols"][symbol] = detail
        if not all(checks.values()) and result["first_mismatch"] is None:
            failed = next(k for k, v in checks.items() if not v)
            result["first_mismatch"] = {"stage": symbol, "reason": failed}
    if result["first_mismatch"]:
        result["gate_status"] = "FAIL"
    write_json(PRIVATE / "gate.private.json", result)
    public = {k: v for k, v in result.items() if k != "symbols"} | {
        "symbols": {s: {k: v for k, v in d.items() if k not in {
            "old_qualified_rows_sha256", "new_qualified_rows_sha256", "old_excluded_rows_sha256",
            "new_excluded_rows_sha256", "old_predictions_sha256", "new_predictions_sha256",
            "reconstructed_old_text_matrix_sha256", "current_actual_text_matrix_sha256",
            "current_probabilities_sha256"}} for s, d in result["symbols"].items()}}
    write_json(PUBLIC / "gate_redacted.json", public)
    return result


def main() -> None:
    pre = preflight()
    new = run_once(pre)
    gate = compare(pre, new)
    print(json.dumps({"run_id": pre["run_id"], "gate_status": gate["gate_status"],
                      "first_mismatch": gate["first_mismatch"],
                      "full_pipeline_executions": gate["full_pipeline_executions"],
                      "estimator_fit_calls": gate["estimator_fit_calls"]}, sort_keys=True))


if __name__ == "__main__":
    main()
