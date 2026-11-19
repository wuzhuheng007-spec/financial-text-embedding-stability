"""Deterministic, artificial-array counterexamples; never a financial backtest."""
from pathlib import Path
import datetime
import hashlib
import json
import platform
import warnings
import numpy as np

ROOT = Path(__file__).resolve().parent

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def describe(x, ddof):
    x = np.asarray(x, dtype=np.float64)
    mean = float(np.nanmean(x))
    std = float(np.nanstd(x, ddof=ddof))
    return {"valid_n": int(np.isfinite(x).sum()), "mean": mean,
            "std": std, "product": mean * std}

def main():
    cfg = json.loads((ROOT / "toy_config.json").read_text())
    start = datetime.datetime.now(datetime.timezone.utc).isoformat()
    before = {p: digest(ROOT / p) for p in
              ["PLAN_zh.md", "toy_config.json", "formula_audit.py"]}
    d = cfg["std_ddof"]
    x = np.array(cfg["chronological_series"], dtype=np.float64)
    fwd, rev = describe(x, d), describe(x[::-1], d)
    assert np.allclose([fwd[k] for k in ["mean", "std", "product"]],
                       [rev[k] for k in ["mean", "std", "product"]])
    assert (x[-1] - x[0]) * (x[0] - x[-1]) < 0
    a, b = [np.array(cfg[k], dtype=np.float64) for k in
            ["calibration_stock_a", "calibration_stock_b"]]
    scale, shift = cfg["score_transform"].values()
    a0, b0 = describe(a, d), describe(b, d)
    a1, b1 = describe(scale * a + shift, d), describe(scale * b + shift, d)
    assert a0["product"] < b0["product"] and a1["product"] > b1["product"]
    assert np.isclose(a1["product"], scale**2 * a0["product"] +
                      scale * shift * a0["std"])
    sparse = describe([np.nan if q is None else q for q in cfg["sparse_series"]], d)
    assert sparse["valid_n"] == 1 and sparse["std"] == 0
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always", RuntimeWarning)
        missing_mean = np.nanmean([np.nan, np.nan])
        missing_std = np.nanstd([np.nan, np.nan], ddof=d)
    assert np.isnan(missing_mean) and np.isnan(missing_std) and len(seen) > 0
    articles = np.array(cfg["daily_article_scores"], dtype=np.float64)
    daily_means = articles.mean(axis=1)
    within = articles.std(axis=1, ddof=d)
    assert daily_means.std(ddof=d) == 0 and np.all(within == 1)
    cases = {
        "order_invariance": {"forward": fwd, "reverse": rev,
            "endpoint_change_forward": float(x[-1] - x[0]),
            "endpoint_change_reverse": float(x[0] - x[-1]),
            "meaning": "Same mean/std/product despite opposite temporal direction"},
        "origin_sensitive_rank": {"original_a": a0, "original_b": b0,
            "transformed_a": a1, "transformed_b": b1,
            "meaning": "Uncentered mean*std ranking changes after neutral-origin shift"},
        "missing_day_false_calm": {"computed": sparse,
            "meaning": "One observed day produces zero std; cannot infer calmness"},
        "all_missing": {"mean_is_nan": bool(np.isnan(missing_mean)),
            "std_is_nan": bool(np.isnan(missing_std)),
            "runtime_warning_observed": bool(seen)},
        "cross_article_vs_time": {"daily_means": daily_means.tolist(),
            "temporal_std_of_daily_mean": float(daily_means.std(ddof=d)),
            "within_day_article_std": within.tolist(),
            "meaning": "Daily means hide simultaneous article-level disagreement"}
    }
    result = {"status": "PASS_SAME_AUTHOR_MATH_CHECK_ONLY", "cases": cases,
              "data_origin": cfg["purpose"], "financial_claims_authorized": False}
    out = ROOT / "formula_results.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    assert all(digest(ROOT / p) == h for p, h in before.items())
    manifest = {"started_at_utc": start,
        "finished_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "command": "python formula_audit.py", "inputs_sha256": before,
        "output_sha256": {out.name: digest(out)},
        "python": platform.python_version(), "numpy": np.__version__,
        "model": None, "seed": None, "randomness": "None", "dtype": "float64",
        "independent_validation": False, "financial_fit_count": 0,
        "warning_handling": "All-NaN warning intentionally captured and asserted"}
    (ROOT / "formula_run_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "math_cases": list(cases),
                      "financial_fit_count": 0, "output_sha256": digest(out)}))

if __name__ == "__main__":
    main()
