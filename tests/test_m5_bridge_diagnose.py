"""Geometry and data-integrity checks for saved-vector network diagnostics."""

import numpy as np
import pytest

from m5_bridge.diagnose import compare_vectors


def _example():
    return {
        "D": np.array([0.1, 0.5, 0.9]),
        "B": np.array([0.8, 0.3, 0.1]),
        "A": np.array([1.0, 0.0, 0.1]),
        "C": np.array([0.4, 0.9, 0.0]),
    }


def test_identical_vectors_have_zero_network_change():
    vectors = _example()
    summary, pairs, changes, _ = compare_vectors(vectors, vectors)
    assert summary["mean_abs_cosine_difference"] == 0
    assert summary["directed_edge_change_rate"] == 0
    assert all(row["changed"] == 0 and row["jaccard"] == 1 for row in changes)
    assert all(row["absolute_difference"] == 0 for row in pairs)


def test_common_orthogonal_rotation_keeps_relations():
    vectors = _example()
    rotation = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]], dtype=float)
    rotated = {key: vector @ rotation for key, vector in vectors.items()}
    summary, _, changes, _ = compare_vectors(vectors, rotated)
    assert summary["max_abs_cosine_difference"] < 1e-12
    assert summary["directed_edge_change_rate"] == 0
    assert all(row["changed"] == 0 for row in changes)


def test_reordering_rows_with_ids_is_invariant_and_missing_id_rejected():
    vectors = _example()
    reordered = dict(reversed(list(vectors.items())))
    first, pairs1, changes1, _ = compare_vectors(vectors, vectors)
    second, pairs2, changes2, _ = compare_vectors(reordered, vectors)
    assert first == second
    assert pairs1 == pairs2
    assert changes1 == changes2
    with pytest.raises(ValueError, match="universes differ"):
        compare_vectors(vectors, {key: value for key, value in vectors.items() if key != "D"})


@pytest.mark.parametrize("bad", [np.zeros(3), np.array([float("nan"), 0, 1])])
def test_invalid_company_vector_rejected(bad):
    vectors = _example()
    vectors["A"] = bad
    with pytest.raises(ValueError, match="vector"):
        compare_vectors(vectors, vectors)


def test_cutoff_tie_uses_company_id_and_is_flagged():
    vectors = {
        "D": np.array([0.6, -0.8, 0.0]),
        "C": np.array([0.6, 0.8, 0.0]),
        "B": np.array([0.8, 0.0, 0.6]),
        "A": np.array([1.0, 0.0, 0.0]),
    }
    _, _, changes, gaps = compare_vectors(vectors, vectors, k=2)
    assert next(row for row in changes if row["company_id"] == "A")["reference_neighbors"] == "B|C"
    a_gap = next(row for row in gaps if row["company_id"] == "A" and row["role"] == "reference")
    assert a_gap["rank_k_neighbor"] == "C"
    assert a_gap["rank_k1_neighbor"] == "D"
    assert a_gap["gap"] == 0
    assert a_gap["exact_cutoff_tie"] == 1


def test_gap_unavailable_with_only_k_neighbors():
    vectors = {key: value for key, value in _example().items() if key != "D"}
    _, _, _, gaps = compare_vectors(vectors, vectors, k=2)
    assert all(row["gap"] is None and row["rank_k1_neighbor"] is None for row in gaps)
