"""Focused contracts for the gated official-notice count/text split."""

from datetime import date, timedelta
import json

import numpy as np
import pytest

from scripts.market_observer_official_repro_v1.incremental_comparison import (
    _branch_matrices, augmented_metrics, brier_score, paired_counts,
    read_gate, read_qualified_rows,
)


def test_gate_rejects_failed_or_missing_reproduction(tmp_path):
    path = tmp_path / "gate.json"
    path.write_text(json.dumps({"gate_status": "FAIL"}), encoding="utf-8")
    with pytest.raises(ValueError, match="has not passed"):
        read_gate(path)
    path.write_text(json.dumps({"gate_status": "PASS"}), encoding="utf-8")
    assert read_gate(path)["gate_status"] == "PASS"


def test_paired_counts_and_brier_use_same_actual_rows():
    actual = np.array([1, 0, 1, 0])
    first = np.array([1, 0, 0, 1])
    second = np.array([1, 1, 1, 1])
    counts = paired_counts(actual, first, second)
    assert counts == {"both_correct": 1, "both_wrong": 1,
                      "first_correct_second_wrong": 1,
                      "first_wrong_second_correct": 1}
    assert sum(counts.values()) == len(actual)
    assert brier_score(actual, np.array([0.8, 0.2, 0.4, 0.7])) == pytest.approx(0.2325)
    metrics = augmented_metrics(actual, first, np.array([0.8, 0.2, 0.4, 0.7]))
    assert metrics["confusion"] == {"actual_down_predicted_down": 1,
                                    "actual_down_predicted_up": 1,
                                    "actual_up_predicted_down": 1,
                                    "actual_up_predicted_up": 1}
    assert metrics["predicted_up_fraction"] == 0.5


def test_text_vocabulary_train_only_and_four_matrices_same_row_count():
    rows = [
        {"announcement_text": "revenue was high", "announcement_count": 1,
         "sec_source_coverage_complete": True, "price_features": [1.0] * 6},
        {"announcement_text": "revenue revenue", "announcement_count": 1,
         "sec_source_coverage_complete": True, "price_features": [2.0] * 6},
        {"announcement_text": "validationexclusive", "announcement_count": 0,
         "sec_source_coverage_complete": True, "price_features": [3.0] * 6},
    ]
    matrices, vocab, _ = _branch_matrices(rows, np.array([0, 1]),
                                         {"tfidf": {"max_features": 8,
                                                    "min_document_frequency": 1}})
    assert "validationexclusive" not in vocab
    assert {name: m.shape[0] for name, m in matrices.items()} == {
        "B": 3, "B_count": 3, "B_text": 3, "B_both": 3}
    assert np.all(matrices["B_text"][2, 6:] == 0)
    assert matrices["B_count"][2, -2:].tolist() == [0.0, 1.0]


def test_qualified_reader_rejects_sealed_test_and_duplicate_dates(tmp_path):
    rows = []
    for i in range(30):
        rows.append({"symbol": "US.NVDA", "label_day": (date(2025, 4, 1) + timedelta(days=i)).isoformat(),
                     "label_up": i % 2, "price_features": [float(i)] * 6,
                     "sec_source_coverage_complete": True})
    rows.append({"symbol": "US.NVDA", "label_day": "2026-04-01", "label_up": 1,
                 "price_features": [1.0] * 6, "sec_source_coverage_complete": True})
    path = tmp_path / "rows.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert len(read_qualified_rows(path, "US.NVDA")) == 31
    rows[-1]["label_day"] = "2026-06-01"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    with pytest.raises(ValueError, match="sealed June/July"):
        read_qualified_rows(path, "US.NVDA")
    rows[-1]["label_day"] = rows[-2]["label_day"]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicated"):
        read_qualified_rows(path, "US.NVDA")
