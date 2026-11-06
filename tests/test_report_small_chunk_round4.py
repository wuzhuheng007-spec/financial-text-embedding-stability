"""Synthetic report-contract tests only; these are not model results."""

import csv
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from finembed.small_chunk_metrics import compute_small_chunk_metrics  # noqa: E402
from report_small_chunk_round4 import compare_m6_reference, file_sha256, render_markdown  # noqa: E402


IDS = ["AAPL", "MSFT", "AMZN", "GOOGL", "META"]
LENGTHS = [118, 123, 128, 133, 138]
POOLING = ["equal_chunks", "body_token_weighted"]


def synthetic_complete_input():
    base = np.array([[1., 0., 0.1], [0.1, 1., 0.2], [-0.6, 1., 0.4],
                     [-1., -0.1, 0.2], [0.4, -1., 0.6]])
    grid = {(length, pool): base + (length - 128) * np.array([0.002, 0.001, -0.001])
            + (0.01 if pool == "body_token_weighted" else 0.) *
            np.array([0.4, -0.5, 0.6]) for length in LENGTHS for pool in POOLING}
    metrics = compute_small_chunk_metrics(IDS, grid, LENGTHS)
    metrics["inference_provenance"] = "REAL_MODEL"  # Synthetic fixture for renderer only.
    metrics["fixture_origin"] = "SYNTHETIC_TEST_ONLY"
    versions = [{"ticker": ticker, "cik": f"synthetic-cik-{index}",
                 "accession": f"synthetic-{index}", "form": "10-K",
                 "section": "ITEM_1_BUSINESS",
                 "item1_sha256": f"synthetic-hash-{index}"}
                for index, ticker in enumerate(IDS)]
    manifest = {"status": "COMPLETE", "evidence_class": "REAL_MODEL",
                "fixture_origin": "SYNTHETIC_TEST_ONLY",
                "decision_validation": {"status": "ACCEPTED"},
                "input_versions": versions, "model_identity": {"revision": "synthetic-revision"},
                "candidate_sha256": "synthetic-candidate", "decision_sha256": "synthetic-decision",
                "input_manifest_sha256": "synthetic-input", "metric_contract_sha256": "synthetic-contract",
                "logical_job_count_planned": 25, "logical_jobs_completed": 25,
                "chunk_vector_count_planned": 50, "chunk_vectors_completed": 50,
                "company_vector_count_planned": 50, "company_vectors_completed": 50,
                "actual_batch_call_attempts": 25,
                "jobs": [{"ticker": ticker, "body_tokens_per_chunk": length,
                          "chunk_count": 2, "status": "COMPLETE"}
                         for length in LENGTHS for ticker in IDS]}
    receipt_docs = [{**version, "body_tokens": 200 + index,
                     "lengths": {str(length): {"body_tokens": 200 + index,
                                               "chunk_count": 2,
                                               "tail_tokens": 200 + index - length}
                                 for length in LENGTHS}}
                    for index, version in enumerate(versions)]
    receipt = {"status": "PASS_TOKENIZATION_ONLY", "documents": receipt_docs,
               "planned_counts": {"chunk_vectors": 50}}
    return manifest, metrics, receipt


def test_report_covers_all_ten_arms_and_all_declared_metric_families():
    manifest, metrics, receipt = synthetic_complete_input()
    assert manifest["fixture_origin"] == metrics["fixture_origin"] == "SYNTHETIC_TEST_ONLY"
    report = render_markdown(manifest, metrics, receipt, run_name="synthetic-only",
                             manifest_sha256="synthetic-manifest",
                             metrics_sha256="synthetic-metrics",
                             receipt_sha256="synthetic-receipt")
    arm_section = report.split("## Complete arm inventory and length contrasts")[1].split(
        "## Top-2 set changes against same-pooling 128")[0]
    for length in LENGTHS:
        for pool in POOLING:
            assert f"| {length} | `{pool}` | 5 | 10 | 5 |" in arm_section
    assert arm_section.count("| `equal_chunks` |") == 5
    assert arm_section.count("| `body_token_weighted` |") == 5
    for name in ("Mean vector distance", "Mean / max abs pair change",
                 "Pair-rank Spearman", "Mean Top-2 overlap",
                 "Mean abs tracked-rank shift", "Mean rank-2/3 gap change",
                 "Exact cutoff ties", "Signed length-by-pooling interaction"):
        assert name in report
    assert "statistical significance" in report
    assert "M5 remains unfrozen" in report
    assert report.count("Dependent pair count") == 1
    changed_section = report.split("## Top-2 set changes against same-pooling 128")[1].split(
        "## Tail-token preflight matrix")[0]
    assert "| 138 | `equal_chunks` | GOOGL |" in changed_section
    assert "Reference rank 2 / rank 3 (gap)" in changed_section
    assert "Observed set-change rows by firm: AAPL 0, MSFT 0, AMZN 0, GOOGL 1, META 0; total 1" in changed_section
    tail_section = report.split("## Tail-token preflight matrix")[1].split(
        "## Same-length pooling contrasts")[0]
    assert "| AAPL | 200 | 82 (41%) | 77 (38.5%) | 72 (36%) | 67 (33.5%) | 62 (31%) |" in tail_section
    assert "| META | 204 |" in tail_section


def test_incomplete_grid_cannot_be_rendered_as_complete():
    manifest, metrics, receipt = synthetic_complete_input()
    del metrics["arms"]["138"]["body_token_weighted"]
    with pytest.raises(ValueError, match="grid is incomplete"):
        render_markdown(manifest, metrics, receipt, run_name="synthetic-only",
                        manifest_sha256="x", metrics_sha256="y", receipt_sha256="z")


@pytest.mark.parametrize("change", [
    lambda m, x, r: m.update(status="PARTIAL_FAILURE"),
    lambda m, x, r: m.update(evidence_class="SYNTHETIC_TEST_ONLY"),
    lambda m, x, r: m["decision_validation"].update(status="BLOCKED"),
    lambda m, x, r: r.update(status="BLOCKED"),
])
def test_report_rejects_nonreal_incomplete_or_unaccepted_run(change):
    manifest, metrics, receipt = synthetic_complete_input()
    change(manifest, metrics, receipt)
    with pytest.raises(ValueError, match="decision-bound real-model run"):
        render_markdown(manifest, metrics, receipt, run_name="synthetic-only",
                        manifest_sha256="x", metrics_sha256="y", receipt_sha256="z")


def test_historical_128_readback_requires_matching_hashes_and_accessions(tmp_path):
    manifest, metrics, _ = synthetic_complete_input()
    run = tmp_path / "new"
    dense = tmp_path / "old"
    dense.mkdir()
    manifest["model_identity"] = {
        "revision": "synthetic-revision", "tokenizer_revision": "synthetic-revision",
        "reference_config_sha256": "synthetic-config", "model_file_sha256": {"model": "synthetic"}}
    old = {"status": "DONE", "dense_status": "DONE", "config_sha256": "synthetic-config",
           "model_identity": {"revision": "synthetic-revision",
                              "tokenizer_revision": "synthetic-revision",
                              "files": {"model": {"sha256": "synthetic"}}},
           "row_text_hashes": [row["item1_sha256"] for row in manifest["input_versions"]],
           "outputs": {}}
    labels = [f"{row['ticker']}|{row['accession']}" for row in manifest["input_versions"]]
    base = np.array([[1., 0., 0.1], [0.1, 1., 0.2], [-0.6, 1., 0.4],
                     [-1., -0.1, 0.2], [0.4, -1., 0.6]])
    manifest["jobs"] = []
    for ticker in IDS:
        job = {"ticker": ticker, "body_tokens_per_chunk": 128, "files": {}}
        for pool in POOLING:
            relative = f"vectors/128/{ticker}_{pool}.npy"
            path = run / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            vector = base[IDS.index(ticker)] + (0.01 if pool == "body_token_weighted"
                                                 else 0.) * np.array([0.4, -0.5, 0.6])
            np.save(path, vector / np.linalg.norm(vector), allow_pickle=False)
            job["files"][pool] = {"path": relative}
        manifest["jobs"].append(job)
    for pool in POOLING:
        vectors = np.vstack([np.load(run / job["files"][pool]["path"], allow_pickle=False)
                             for job in manifest["jobs"]])
        vector_name = f"dense_vectors_128_{pool}.npy"
        np.save(dense / vector_name, vectors, allow_pickle=False)
        old["outputs"][vector_name] = {"sha256": file_sha256(dense / vector_name)}
        csv_name = f"dense_cosine_128_{pool}.csv"
        with (dense / csv_name).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["document_id", *labels])
            writer.writerows([[label, *row] for label, row in zip(
                labels, metrics["arms"]["128"][pool]["cosine_matrix"])])
        old["outputs"][csv_name] = {"sha256": file_sha256(dense / csv_name)}
    (dense / "receipt.json").write_text(json.dumps(old), encoding="utf-8")
    comparison = compare_m6_reference(run, manifest, metrics, dense)
    assert comparison["pooling"]["equal_chunks"]["vector_max_abs"] == 0
    assert comparison["pooling"]["body_token_weighted"]["cosine_max_abs"] == 0
    (dense / "dense_vectors_128_equal_chunks.npy").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash changed"):
        compare_m6_reference(run, manifest, metrics, dense)
