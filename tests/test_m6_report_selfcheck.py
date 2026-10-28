"""Synthetic software tests only; none of these rows is a research result."""
from __future__ import annotations

import csv
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import numpy as np
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "report_m6_real_run.py"
SPEC = spec_from_file_location("m6_saved_output_report", SCRIPT)
report = module_from_spec(SPEC)
SPEC.loader.exec_module(report)


def test_average_ties_and_null_constant_matrix():
    np.testing.assert_array_equal(report.average_ranks(np.array([2., 1., 1.])),
                                  np.array([3., 1.5, 1.5]))
    constant = np.eye(4)
    varied = np.array([[1., .2, .3, .4], [.2, 1., .5, .6],
                       [.3, .5, 1., .7], [.4, .6, .7, 1.]])
    result = report.upper_spearman(constant, varied)
    assert result["value"] is None
    assert result["reason"] == "constant_ranks_in_at_least_one_matrix"
    assert report.upper_spearman(varied, varied)["value"] == pytest.approx(1.)


def test_top_two_tie_rule_and_directed_matrix(tmp_path):
    ids = ["A|1", "C|2", "B|3", "D|4"]
    tickers = ["A", "C", "B", "D"]
    directed = np.array([[1., .5, .5, .1], [.2, 1., .4, .3],
                         [.8, .7, 1., .2], [.6, .5, .4, 1.]])
    path = tmp_path / "scores.csv"
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["document_id", *ids])
        writer.writerows([[key, *row] for key, row in zip(ids, directed)])
    np.testing.assert_array_equal(report.read_matrix(path, ids, False), directed)
    assert [p["ticker"] for p in report.top2(directed, ids, tickers)["A|1"]] == ["B", "C"]
    with pytest.raises(ValueError, match="Symmetric"):
        report.read_matrix(path, ids, True)


def test_same_tokenizer_refit_keeps_numeric_tokens(tmp_path):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("Item 1 10K Alpha", encoding="utf-8")
    b.write_text("Item 2 10Q Beta", encoding="utf-8")
    rows = [{"item1_path": str(p), "lexical_tokens": 4} for p in (a, b)]
    vectors, cosine, vocabulary, idf = report.refit_tfidf_same_tokenizer(rows)
    assert {"1", "2", "10k", "10q"} <= set(vocabulary)
    assert vectors.shape == (2, len(vocabulary))
    assert idf.shape == (len(vocabulary),)
    assert cosine[0, 1] == pytest.approx(float(vectors[0] @ vectors[1]))
