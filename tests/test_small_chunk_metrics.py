"""Synthetic numerical contracts only; no encoder run or research result."""

import json

import numpy as np
import pytest

from finembed.small_chunk_metrics import compute_small_chunk_metrics


IDS = ["A", "B", "C", "D", "E"]


def sample_grid():
    base = np.array([[1., 0.], [0., 1.], [1., 1.], [1., -1.], [-1., 0.]])
    weighted = base.copy()
    weighted[1] = [.1, 1.]
    candidate_equal = base.copy()
    candidate_equal[1] = [.2, 1.]
    candidate_weighted = base.copy()
    candidate_weighted[1] = [.4, 1.]
    return {(128, "equal_chunks"): base,
            (128, "body_token_weighted"): weighted,
            (133, "equal_chunks"): candidate_equal,
            (133, "body_token_weighted"): candidate_weighted}


def test_complete_grid_returns_serializable_metrics_and_signed_interaction():
    result = compute_small_chunk_metrics(IDS, sample_grid(), [128, 133])
    json.dumps(result, allow_nan=False)
    assert result["status"] == "COMPLETE_NUMERICAL_ONLY"
    assert result["denominators"] == {"companies": 5, "upper_triangle_pairs": 10,
                                       "tracked_reference_rank2_rank3_peers": 10}
    comparison = result["within_pooling_length_comparisons"]["133"]["equal_chunks"]
    assert comparison["company_vector_cosine_distance"]["denominator"] == 5
    assert comparison["similarity_change"]["pair_denominator"] == 10
    assert comparison["top2_overlap"]["company_denominator"] == 5
    assert comparison["reference_rank2_rank3_peer_movement"]["tracked_peer_denominator"] == 10
    assert set(result["same_length_pooling_comparisons"]) == {"128", "133"}

    # Check the signed difference of length effects independently of the output formula.
    arms = result["arms"]
    pair = result["signed_length_by_pooling_interactions"]["133"]["upper_triangle_pairs"][0]
    assert pair["companies"] == ["A", "B"]
    expected = ((arms["133"]["body_token_weighted"]["cosine_matrix"][0][1]
                 - arms["128"]["body_token_weighted"]["cosine_matrix"][0][1])
                - (arms["133"]["equal_chunks"]["cosine_matrix"][0][1]
                   - arms["128"]["equal_chunks"]["cosine_matrix"][0][1]))
    assert pair["weighted_minus_equal_interaction"] == pytest.approx(expected)


def test_exact_cutoff_tie_keeps_zero_gap_and_constant_spearman_is_null():
    identity = np.eye(5)
    grid = {(length, pool): identity for length in (128, 133)
            for pool in ("equal_chunks", "body_token_weighted")}
    result = compute_small_chunk_metrics(IDS, grid, [128, 133])
    arm = result["arms"]["128"]["equal_chunks"]
    assert arm["neighbor_order"]["A"] == ["B", "C", "D", "E"]
    assert arm["rank2_rank3"]["A"] == {"rank2": "C", "rank3": "D",
                                         "rank2_minus_rank3": 0.0, "exact_cutoff_tie": True}
    rho = result["within_pooling_length_comparisons"]["133"]["equal_chunks"]["upper_triangle_spearman"]
    assert rho["value"] is None
    assert "constant ranks" in rho["reason"]
    assert result["within_pooling_length_comparisons"]["133"]["equal_chunks"]["top2_overlap"]["mean"] == 1
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("change,reason", [
    (lambda g: g.pop((133, "equal_chunks")), "Incomplete"),
    (lambda g: g[(133, "equal_chunks")].__setitem__((0, 0), np.nan), "Nonfinite"),
    (lambda g: g[(133, "equal_chunks")].__setitem__(0, [0., 0.]), "Zero"),
    (lambda g: g.__setitem__((133, "equal_chunks"), np.ones((4, 2))), "shape"),
])
def test_invalid_or_incomplete_grid_fails_closed(change, reason):
    grid = sample_grid()
    change(grid)
    with pytest.raises(ValueError, match=reason):
        compute_small_chunk_metrics(IDS, grid, [128, 133])


def test_duplicate_company_ids_and_unlisted_arm_rejected():
    with pytest.raises(ValueError, match="unique"):
        compute_small_chunk_metrics(["A", "A", "C", "D", "E"], sample_grid(), [128, 133])
    grid = sample_grid()
    grid[(138, "equal_chunks")] = np.eye(5, 2)
    with pytest.raises(ValueError, match="unexpected"):
        compute_small_chunk_metrics(IDS, grid, [128, 133])
