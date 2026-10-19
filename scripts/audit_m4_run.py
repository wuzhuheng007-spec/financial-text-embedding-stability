"""Read-only, separately coded self-audit of an M4 run; emits a receipt."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import argparse
import hashlib
import json

import numpy as np


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def audit(run: Path, corpus: Path) -> dict:
    run, corpus = run.resolve(strict=True), corpus.resolve(strict=True)
    manifest = read(run / "manifest.json")
    assert manifest["kind"] == "m4" and manifest["status"] == "DONE"
    assert manifest["as_of"] == "2025-12-31"
    assert digest(corpus) in [x["sha256"] for x in manifest["inputs"]]
    for kind in ("inputs", "outputs"):
        for entry in manifest[kind]:
            if "path" in entry:
                path = Path(entry["path"]).resolve(strict=True)
                assert digest(path) == entry["sha256"], (kind, path)
    root = Path(__file__).resolve().parents[1]
    for relative, expected in manifest["source_files_sha256"].items():
        assert digest(root / relative) == expected, relative
    fixed = [x["ticker"] for x in read(corpus)["entries"]]
    assert fixed == ["AAPL", "MSFT", "AMZN", "GOOGL", "META"]
    sec_cik = {x["ticker"]: x["cik"] for x in read(corpus)["entries"]}
    access = read(run / "market_access.json")
    assert access["key_present"] is True
    assert len(access["endpoint_results"]) == 5
    assert all(x["status"] == "INCLUDED" for x in access["endpoint_results"])

    closes = {}
    page_count = 0
    receipt_count = 0
    for ticker in fixed:
        identity = read(run / "market" / f"identity_{ticker}.json")
        assert identity["sec_cik"] == sec_cik[ticker]
        assert identity["massive"]["ticker"] == ticker
        assert str(identity["massive"]["cik"]).zfill(10) == sec_cik[ticker]
        assert identity["as_of"] == "2025-12-31"
        bars = read(run / "market" / f"bars_{ticker}.json")
        assert bars["adjusted"] is True and bars["price_field"] == "c"
        assert bars["adjustment_scope"] == "splits only; excludes dividend total return"
        raw_rows = []
        for label in (f"reference_{ticker}", f"bars_{ticker}"):
            pages = sorted((run / "market" / "raw" / label).glob("page_*_attempt_*.json"))
            pages = [p for p in pages if not p.name.endswith(".receipt.json")]
            assert pages
            for path in pages:
                receipt = read(path.with_suffix(".receipt.json"))
                assert receipt["raw_path"] == str(path)
                assert receipt["http_status"] == 200 and receipt["sha256"] == digest(path)
                assert receipt["provider"] == "Massive REST"
                assert "apiKey" not in receipt["url"]
                body = read(path)
                assert body["status"] == "OK"
                if label.startswith("bars_"):
                    assert body["ticker"] == ticker and body["adjusted"] is True
                    raw_rows.extend(body["results"])
                page_count += 1
                receipt_count += 1
        assert bars["response_request_ids"]
        assert len(raw_rows) == len(bars["bars"])
        observed = {}
        for source, saved in zip(sorted(raw_rows, key=lambda x: x["t"]), bars["bars"]):
            day = datetime.fromtimestamp(source["t"] / 1000, ZoneInfo("America/New_York")).date().isoformat()
            assert "2025-01-01" <= day <= "2025-12-31"
            assert saved == {"date_et": day, **source}
            assert day not in observed and source["c"] > 0
            observed[day] = float(source["c"])
        closes[ticker] = observed

    dates = sorted({day for series in closes.values() for day in series})
    matrix = np.full((len(dates), len(fixed)), np.nan)
    for j, ticker in enumerate(fixed):
        for i in range(1, len(dates)):
            if dates[i] in closes[ticker] and dates[i - 1] in closes[ticker]:
                matrix[i, j] = closes[ticker][dates[i]] / closes[ticker][dates[i - 1]] - 1
    saved_returns = np.load(run / "returns_TxN.npy")
    assert saved_returns.shape == matrix.shape
    assert np.array_equal(np.isnan(saved_returns), np.isnan(matrix))
    assert np.allclose(saved_returns, matrix, equal_nan=True, rtol=0, atol=1e-14)
    common = read(run / "common_masks.json")
    complete = np.isfinite(matrix).all(axis=1)
    assert common["ticker_order"] == fixed and common["date_order"] == dates
    assert common["observed_return_mask_TxN"] == np.isfinite(matrix).tolist()
    assert common["complete_date_mask"] == complete.tolist()
    assert common["complete_dates"] == [d for d, valid in zip(dates, complete) if valid]
    exclusions = read(run / "missing_exclusions.json")
    assert exclusions["complete_date_count"] == int(complete.sum())
    assert exclusions["total_union_dates"] == len(dates)
    assert exclusions["missing_return_cells_by_ticker"] == {
        ticker: int(np.isnan(matrix[:, j]).sum()) for j, ticker in enumerate(fixed)}

    pca = read(run / "pca_metadata.json")
    loadings = np.load(run / "pca_stock_loadings_NxK.npy")
    scores = np.load(run / "pca_date_scores_TxK.npy")
    x = matrix[complete]
    assert pca["ticker_order"] == fixed and pca["date_order"] == common["complete_dates"]
    assert pca["center"] is True and pca["scale"] is False
    assert pca["input_shape_TxN"] == list(x.shape)
    assert loadings.shape == tuple(pca["loadings_shape_NxK"]) == (len(fixed), pca["requested_and_used_k"])
    assert scores.shape == tuple(pca["scores_shape_TxK"]) == (int(complete.sum()), pca["requested_and_used_k"])
    mean = x.mean(axis=0)
    centered = x - mean
    assert np.allclose(mean, pca["mean_by_security"], rtol=0, atol=1e-14)
    assert np.allclose(pca["scale_by_security"], np.ones(len(fixed)))
    assert pca["effective_rank"] == int(np.linalg.matrix_rank(centered))
    assert np.allclose(np.linalg.svd(centered, compute_uv=False), pca["singular_values"], rtol=1e-12)
    assert np.allclose(loadings.T @ loadings, np.eye(loadings.shape[1]), atol=1e-12)
    assert np.allclose(centered @ loadings, scores, atol=1e-12)
    assert np.isfinite(loadings).all() and np.isfinite(scores).all()

    return {"status": "PASS", "audit_kind": "same-agent read-only self-audit, separately coded",
            "audit_script_sha256": digest(Path(__file__).resolve()),
            "run": str(run), "run_manifest_sha256": digest(run / "manifest.json"),
            "corpus_index_sha256": digest(corpus), "ticker_order": fixed,
            "bars_per_security": {t: len(closes[t]) for t in fixed},
            "raw_page_count": page_count, "raw_receipt_count": receipt_count,
            "union_dates": len(dates), "complete_return_dates": int(complete.sum()),
            "returns_shape_TxN": list(matrix.shape), "return_missing_cells": int(np.isnan(matrix).sum()),
            "pca_loadings_shape_NxK": list(loadings.shape), "pca_scores_shape_TxK": list(scores.shape),
            "pca_effective_rank": pca["effective_rank"],
            "manifest_inputs_checked": len([x for x in manifest["inputs"] if "path" in x]),
            "manifest_outputs_checked": len([x for x in manifest["outputs"] if "path" in x]),
            "source_files_checked": len(manifest["source_files_sha256"])}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt = audit(args.run, args.corpus)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": receipt["status"], "receipt": str(args.output),
                      "complete_dates": receipt["complete_return_dates"]}))


if __name__ == "__main__":
    main()
