"""SYNTHETIC_TEST_ONLY report-software tests; no model or research outputs."""
from __future__ import annotations

import math

import numpy as np
import pytest

from finembed.boundary_shift_metrics import compute_boundary_shift_metrics
from scripts import report_boundary_shift_round6 as report


def synthetic_tables():
    """Construct in-memory fake vectors and metadata for renderer checks."""
    ids = list(report.COMPANIES)
    base_angles = [0.0, 0.18, 0.48, 1.28, 2.2]
    vectors = {}
    for delta in report.SHIFTS:
        for pool in report.POOLING:
            angles = base_angles.copy()
            angles[1] += delta * (0.008 if pool == "equal_chunks" else 0.011)
            if delta == 10:
                angles[1] = 1.9 if pool == "equal_chunks" else 1.82
            if pool == "body_token_weighted":
                angles[3] += 0.035
                if delta == 5:
                    angles[2] = 1.9
            vectors[(delta, pool)] = np.array(
                [[math.cos(angle), math.sin(angle)] for angle in angles],
                dtype=np.float64)
    metrics = compute_boundary_shift_metrics(ids, vectors)
    metrics["inference_provenance"] = "REAL_MODEL"
    versions = [{"ticker": company, "accession": f"{index + 1:010d}-25-000001",
                 "item1_sha256": f"{index + 1:064x}"}
                for index, company in enumerate(ids)]
    jobs = [{"ticker": company, "boundary_shift": delta, "status": "COMPLETE",
             "chunk_count": 2} for delta in report.SHIFTS for company in ids]
    manifest = {
        "fixture_kind": "SYNTHETIC_TEST_ONLY",
        "kind": "BOUNDARY_SHIFT_PROBE_RUN", "status": "COMPLETE",
        "evidence_class": "REAL_MODEL", "axis": "boundary_shift",
        "reference_body_tokens": 128, "reference_boundary_shift": 0,
        "boundary_shifts": list(report.SHIFTS), "pooling": list(report.POOLING),
        "decision_validation": {"status": "ACCEPTED"}, "failures": [],
        "input_versions": versions, "jobs": jobs,
        "logical_job_count_planned": 25, "logical_jobs_completed": 25,
        "chunk_vector_count_planned": 50, "chunk_vectors_completed": 50,
        "company_vector_count_planned": 50, "company_vectors_completed": 50,
        "real_encoder_sessions_created": 1, "actual_batch_call_attempts": 25,
        "process_wall_seconds_from_output_creation": 1.25,
        "candidate_sha256": "a" * 64, "metric_contract_sha256": "b" * 64,
        "decision_sha256": "c" * 64, "input_manifest_sha256": "d" * 64,
        "git_at_run": {"head": "e" * 40},
        "model_identity": {"revision": "f" * 40, "provider": "CPUExecutionProvider"},
        "batch_size": 8,
    }
    receipt = {"fixture_kind": "SYNTHETIC_TEST_ONLY",
               "status": "PASS_TOKENIZATION_ONLY", "planned_counts": {"chunk_vectors": 50},
               "documents": [{"ticker": company, "body_tokens": 250,
                              "shifts": {str(delta): {"first_tokens": 128 + delta,
                                                       "tail_tokens": 122 - delta,
                                                       "chunk_count": 2}
                                         for delta in report.SHIFTS}}
                             for company in ids]}
    return manifest, metrics, receipt


def _section_rows(markdown: str, heading: str, next_heading: str) -> list[str]:
    section = markdown.split(heading, 1)[1].split(next_heading, 1)[0]
    return [line for line in section.splitlines()
            if line.startswith("| ") and not line.startswith("| ---")][1:]


def test_synthetic_fixture_renders_all_declared_tables_without_encoder(monkeypatch):
    monkeypatch.setattr("finembed.model.Encoder.__init__",
                        lambda *_: pytest.fail("Real Encoder constructed by S3 report"))
    manifest, metrics, receipt = synthetic_tables()
    text = report._render_markdown(
        manifest, metrics, receipt, run_name="SYNTHETIC_TEST_ONLY",
        manifest_sha256="1" * 64, metrics_sha256="2" * 64,
        receipt_sha256="3" * 64)
    assert "The experimental axis is an internal boundary shift" in text
    assert "not a change in nominal chunk length" in text
    assert "SYNTHETIC_TEST_ONLY" in text
    assert len(_section_rows(text, "## All ten shift-by-pooling arms",
                             "## Nonzero shifts")) == 10
    assert len(_section_rows(text, "## Nonzero shifts against delta zero",
                             "## Pooling comparison")) == 8
    assert len(_section_rows(text, "## Pooling comparison at every fixed shift",
                             "## Signed shift-by-pooling")) == 5
    interactions = text.split("All declared signed company-pair interactions:", 1)[1]
    interactions = interactions.split("## Every Top-2", 1)[0]
    assert sum(line.startswith("| ") and not line.startswith("| ---")
               for line in interactions.splitlines()) == 41  # Header plus 40 pairs.
    expected_changes = sum(
        set(metrics["arms"]["0"][pool]["top2"][company]) !=
        set(metrics["arms"][str(delta)][pool]["top2"][company])
        for delta in report.SHIFTS if delta != 0
        for pool in report.POOLING for company in report.COMPANIES)
    assert expected_changes > 0
    change_rows = _section_rows(text, "## Every Top-2 peer-set change",
                                "## Every same-delta pooling Top-2 peer-set change")
    assert len(change_rows) == expected_changes
    expected_pooling_changes = sum(
        set(metrics["arms"][str(delta)]["equal_chunks"]["top2"][company]) !=
        set(metrics["arms"][str(delta)]["body_token_weighted"]["top2"][company])
        for delta in report.SHIFTS for company in report.COMPANIES)
    assert expected_pooling_changes > 0
    pooling_rows = _section_rows(text,
        "## Every same-delta pooling Top-2 peer-set change",
        "## First and tail body lengths")
    assert len(pooling_rows) == expected_pooling_changes
    assert "Equal rank 2 / rank 3 (gap)" in text
    assert "Weighted rank 2 / rank 3 (gap)" in text
    assert "Failures: **0**. Excluded firms/arms: **0**." in text
    assert "peak memory: UNKNOWN" in text


def test_incomplete_or_mislabeled_fixture_is_rejected():
    manifest, metrics, receipt = synthetic_tables()
    del metrics["arms"]["-10"]
    with pytest.raises(ValueError, match="grid is incomplete"):
        report._render_markdown(manifest, metrics, receipt, run_name="synthetic",
                                manifest_sha256="1", metrics_sha256="2",
                                receipt_sha256="3")
    _, metrics, _ = synthetic_tables()
    manifest["evidence_class"] = "SYNTHETIC_TEST_ONLY"
    with pytest.raises(ValueError, match="real S3 grid"):
        report._render_markdown(manifest, metrics, receipt, run_name="synthetic",
                                manifest_sha256="1", metrics_sha256="2",
                                receipt_sha256="3")


def test_cli_rejects_synthetic_saved_output_before_writing(monkeypatch, tmp_path):
    run = tmp_path / "SYNTHETIC_TEST_ONLY"
    run.mkdir()
    out = tmp_path / "report.md"
    monkeypatch.setattr(report, "verify_saved_output", lambda _: {
        "status": "PASS_SAVED_OUTPUT_RECOMPUTED", "run_status": "COMPLETE",
        "evidence_class": "SYNTHETIC_TEST_ONLY", "jobs_checked": 25,
        "metrics_recomputed": True})
    monkeypatch.setattr("finembed.model.Encoder.__init__",
                        lambda *_: pytest.fail("Real Encoder constructed by S3 report"))
    assert report.main(["--run", str(run), "--output", str(out)]) == 2
    assert not out.exists()
