"""SYNTHETIC_TEST_ONLY numerical checks; no Encoder or research vectors."""

import json

import numpy as np
import pytest

from finembed.boundary_shift_metrics import compute_boundary_shift_metrics


IDS = ["A", "B", "C", "D", "E"]
SHIFTS = (-10, -5, 0, 5, 10)
POOLING = ("equal_chunks", "body_token_weighted")


def synthetic_grid():
    base = np.array([[1., 0.], [0., 1.], [1., 1.], [1., -1.], [-1., 0.]])
    grid = {}
    for shift in SHIFTS:
        equal, weighted = base.copy(), base.copy()
        equal[1] = [(shift + 10) / 100, 1.]
        weighted[1] = [0.1 + (shift + 10) / 50, 1.]
        grid[(shift, "equal_chunks")] = equal
        grid[(shift, "body_token_weighted")] = weighted
    return grid


def test_full_grid_uses_boundary_shift_labels_and_all_declared_comparisons():
    result = compute_boundary_shift_metrics(IDS, synthetic_grid())
    json.dumps(result, allow_nan=False)
    assert result["kind"] == "BOUNDARY_SHIFT_DESCRIPTIVE_METRICS"
    assert result["axis"] == "boundary_shift"
    assert result["base_step_body_tokens"] == 128
    assert result["reference_boundary_shift"] == 0
    assert result["candidate_boundary_shifts"] == list(SHIFTS)
    assert set(result["arms"]) == {str(x) for x in SHIFTS}
    assert set(result["within_pooling_shift_comparisons"]) == {"-10", "-5", "5", "10"}
    assert set(result["same_shift_pooling_comparisons"]) == {str(x) for x in SHIFTS}
    assert set(result["signed_shift_by_pooling_interactions"]) == {"-10", "-5", "5", "10"}
    assert result["denominators"] == {"companies": 5, "upper_triangle_pairs": 10,
                                      "tracked_reference_rank2_rank3_peers": 10}
    comparison = result["within_pooling_shift_comparisons"]["-10"]["equal_chunks"]
    assert comparison["reference_arm"] == {"boundary_shift": 0, "pooling": "equal_chunks"}
    assert comparison["candidate_arm"] == {"boundary_shift": -10, "pooling": "equal_chunks"}
    assert comparison["company_vector_cosine_distance"]["denominator"] == 5
    assert comparison["similarity_change"]["pair_denominator"] == 10
    assert comparison["top2_overlap"]["company_denominator"] == 5
    assert comparison["reference_rank2_rank3_peer_movement"]["tracked_peer_denominator"] == 10
    assert result["same_shift_pooling_comparisons"]["0"]["reference_arm"] == {
        "boundary_shift": 0, "pooling": "equal_chunks"}
    assert result["same_shift_pooling_comparisons"]["0"]["candidate_arm"] == {
        "boundary_shift": 0, "pooling": "body_token_weighted"}
    assert '"body_tokens"' not in json.dumps(result)


def test_known_cosine_and_signed_interaction_from_saved_arm_matrices():
    result = compute_boundary_shift_metrics(IDS, synthetic_grid())
    reference_arm = result["arms"]["0"]["equal_chunks"]
    assert reference_arm["cosine_matrix"][0][1] == pytest.approx(.1 / np.sqrt(1.01))
    assert reference_arm["neighbor_order"]["A"] == ["C", "D", "B", "E"]
    assert reference_arm["rank2_rank3"]["A"]["rank2"] == "D"
    assert reference_arm["rank2_rank3"]["A"]["rank3"] == "B"
    distance = result["within_pooling_shift_comparisons"]["10"]["equal_chunks"][
        "company_vector_cosine_distance"]["by_company"]["B"]
    ref, cand = np.array([.1, 1.]), np.array([.2, 1.])
    expected_distance = 1 - float(ref @ cand / (np.linalg.norm(ref) * np.linalg.norm(cand)))
    assert distance == pytest.approx(expected_distance)

    arms = result["arms"]
    row = result["signed_shift_by_pooling_interactions"]["10"]["upper_triangle_pairs"][0]
    assert row["companies"] == ["A", "B"]
    weighted_delta = (arms["10"]["body_token_weighted"]["cosine_matrix"][0][1] -
                      arms["0"]["body_token_weighted"]["cosine_matrix"][0][1])
    equal_delta = (arms["10"]["equal_chunks"]["cosine_matrix"][0][1] -
                   arms["0"]["equal_chunks"]["cosine_matrix"][0][1])
    assert row["weighted_shift_effect"] == pytest.approx(weighted_delta)
    assert row["equal_shift_effect"] == pytest.approx(equal_delta)
    assert row["weighted_minus_equal_interaction"] == pytest.approx(
        weighted_delta - equal_delta)
    cosine_x = lambda x: x / np.sqrt(1 + x * x)
    hand_calculated = ((cosine_x(.5) - cosine_x(.3)) -
                       (cosine_x(.2) - cosine_x(.1)))
    assert row["weighted_minus_equal_interaction"] == pytest.approx(hand_calculated)


def test_exact_tie_and_constant_spearman_are_preserved():
    grid = {(shift, pool): np.eye(5) for shift in SHIFTS for pool in POOLING}
    result = compute_boundary_shift_metrics(IDS, grid)
    arm = result["arms"]["0"]["equal_chunks"]
    assert arm["neighbor_order"]["A"] == ["B", "C", "D", "E"]
    assert arm["rank2_rank3"]["A"] == {"rank2": "C", "rank3": "D",
                                          "rank2_minus_rank3": 0.0,
                                          "exact_cutoff_tie": True}
    rho = result["within_pooling_shift_comparisons"]["5"]["equal_chunks"][
        "upper_triangle_spearman"]
    assert rho["value"] is None
    assert "constant ranks" in rho["reason"]
    assert rho["pair_count"] == 10
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("change,reason", [
    (lambda grid: grid.pop((10, "equal_chunks")), "Incomplete"),
    (lambda grid: grid.__setitem__((10, "equal_chunks"), np.ones((4, 2))), "shape"),
    (lambda grid: grid[(10, "equal_chunks")].__setitem__((0, 0), np.nan), "Nonfinite"),
    (lambda grid: grid[(10, "equal_chunks")].__setitem__(0, [0., 0.]), "Zero"),
])
def test_invalid_or_incomplete_grid_fails_closed(change, reason):
    grid = synthetic_grid()
    change(grid)
    with pytest.raises(ValueError, match=reason):
        compute_boundary_shift_metrics(IDS, grid)


def test_duplicate_company_ids_and_extra_arm_rejected():
    with pytest.raises(ValueError, match="unique"):
        compute_boundary_shift_metrics(["A", "A", "C", "D", "E"], synthetic_grid())
    grid = synthetic_grid()
    grid[(15, "equal_chunks")] = np.eye(5, 2)
    with pytest.raises(ValueError, match="unexpected"):
        compute_boundary_shift_metrics(IDS, grid)


@pytest.mark.parametrize("shifts,reference,pooling", [
    ((-10, -10, 0), 0, POOLING),
    ((-10, 5), 0, POOLING),
    ((-10, 0, 5), -10, POOLING),
    ((-130, 0, 5), 0, POOLING),
    (SHIFTS, 0, tuple(reversed(POOLING))),
])
def test_invalid_axis_or_pooling_rejected(shifts, reference, pooling):
    with pytest.raises(ValueError):
        compute_boundary_shift_metrics(IDS, synthetic_grid(), shifts,
                                       reference_shift=reference, pooling=pooling)
