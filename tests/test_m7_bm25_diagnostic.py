"""Synthetic software controls only; no real filing or company finding."""

import numpy as np
import pytest

from scripts.m7_bm25_diagnostic import (
    fitted_scalar, fitted_terms, term_weights, synthetic_controls,
)


def test_positive_document_scalar_cancels_under_l2():
    v = np.array([1.0, 2.0, 3.0])
    assert np.allclose(v / np.linalg.norm(v), 7.3 * v / np.linalg.norm(7.3 * v),
                       rtol=0, atol=1e-15)
    assert synthetic_controls()["data_class"] == "SYNTHETIC_SOFTWARE_TEST_ONLY"


def test_all_frequency_one_terms_are_an_exact_document_scalar():
    counts, vocabulary, idf, avg = fitted_terms([["a", "b", "c"], ["a"]])
    w0 = term_weights(counts, vocabulary, idf, avg, k1=1.2, b=0.0)[0]
    w1 = term_weights(counts, vocabulary, idf, avg, k1=1.2, b=1.0)[0]
    fit = fitted_scalar(w0, w1)
    expected = (1 + 1.2) / (1 + 1.2 * 3 / 2)
    assert fit["alpha"] == pytest.approx(expected, abs=1e-15)
    assert fit["relative_residual_to_target_l2"] < 1e-12


def test_mixed_frequencies_change_direction_when_length_differs_from_average():
    counts, vocabulary, idf, avg = fitted_terms(
        [["x", "x", "y", "z"], ["x", "z"], ["y", "u", "u"]])
    assert avg == 3
    w0 = term_weights(counts, vocabulary, idf, avg, k1=1.2, b=0.0)[0]
    w1 = term_weights(counts, vocabulary, idf, avg, k1=1.2, b=1.0)[0]
    assert fitted_scalar(w0, w1)["relative_residual_to_target_l2"] > 1e-6
    ix, iy = vocabulary.index("x"), vocabulary.index("y")
    assert w1[ix] / w0[ix] != pytest.approx(w1[iy] / w0[iy], abs=1e-8)


def test_invalid_b_is_rejected_without_imputation():
    counts, vocabulary, idf, avg = fitted_terms([["a", "b"], ["b"]])
    with pytest.raises(ValueError, match="Invalid BM25"):
        term_weights(counts, vocabulary, idf, avg, k1=1.2, b=1.1)
