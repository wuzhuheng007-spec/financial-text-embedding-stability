"""Pure descriptive metrics for the S3 boundary-shift engineering grid.

The arithmetic is delegated to the existing small-chunk metric implementation.
This module changes only the experimental-axis labels and reference mapping;
it never loads an encoder, reads text, or authenticates vector provenance.
"""

from __future__ import annotations

from copy import deepcopy
from itertools import product
from typing import Mapping, Sequence

from .small_chunk_metrics import POOLING_METHODS, compute_small_chunk_metrics


BOUNDARY_SHIFTS = (-10, -5, 0, 5, 10)
BASE_STEP_BODY_TOKENS = 128


def _comparison_with_shift_labels(value: dict, reference_shift: int,
                                  candidate_shift: int) -> dict:
    result = deepcopy(value)
    result["reference_arm"] = {"boundary_shift": reference_shift,
                               "pooling": value["reference_arm"]["pooling"]}
    result["candidate_arm"] = {"boundary_shift": candidate_shift,
                               "pooling": value["candidate_arm"]["pooling"]}
    return result


def compute_boundary_shift_metrics(
        company_ids: Sequence[str], vectors_by_arm: Mapping,
        shifts: Sequence[int] = BOUNDARY_SHIFTS, *, reference_shift: int = 0,
        pooling: Sequence[str] = POOLING_METHODS) -> dict:
    """Describe every company and pair in a complete S3 vector grid.

    Keys are ``(boundary_shift, pooling_name)`` and rows follow
    ``company_ids``. The fixed 128-token base step is a bijection used only to
    call the established pure numerical engine; the returned axis and arm
    identities are boundary shifts, not body chunk lengths.
    """
    shifts = list(shifts)
    pooling = list(pooling)
    if (not shifts or len(set(shifts)) != len(shifts) or
            any(type(shift) is not int or BASE_STEP_BODY_TOKENS + shift <= 0
                for shift in shifts) or type(reference_shift) is not int or
            reference_shift != 0 or 0 not in shifts):
        raise ValueError("Boundary shifts must be distinct valid integers and include reference 0")
    if pooling != list(POOLING_METHODS):
        raise ValueError("Both pooling methods must use the declared contract order")
    if not isinstance(vectors_by_arm, Mapping):
        raise ValueError("Boundary-shift vectors must be a mapping")
    expected = set(product(shifts, pooling))
    if set(vectors_by_arm) != expected:
        raise ValueError("Incomplete or unexpected boundary-shift vector grid")

    mapped_shifts = [BASE_STEP_BODY_TOKENS + shift for shift in shifts]
    mapped = {(BASE_STEP_BODY_TOKENS + shift, method): vectors_by_arm[(shift, method)]
              for shift, method in product(shifts, pooling)}
    base = compute_small_chunk_metrics(
        company_ids, mapped, mapped_shifts,
        reference_length=BASE_STEP_BODY_TOKENS, pooling_methods=pooling)

    arms = {str(shift): deepcopy(base["arms"][str(BASE_STEP_BODY_TOKENS + shift)])
            for shift in shifts}
    within = {
        str(shift): {
            method: _comparison_with_shift_labels(
                base["within_pooling_length_comparisons"]
                    [str(BASE_STEP_BODY_TOKENS + shift)][method], 0, shift)
            for method in pooling}
        for shift in shifts if shift != 0}
    same = {
        str(shift): _comparison_with_shift_labels(
            base["same_length_pooling_comparisons"]
                [str(BASE_STEP_BODY_TOKENS + shift)], shift, shift)
        for shift in shifts}
    interactions = {}
    for shift in shifts:
        if shift == 0:
            continue
        old = base["signed_length_by_pooling_interactions"][
            str(BASE_STEP_BODY_TOKENS + shift)]
        rows = [{"companies": row["companies"],
                 "equal_shift_effect": row["equal_length_effect"],
                 "weighted_shift_effect": row["weighted_length_effect"],
                 "weighted_minus_equal_interaction":
                     row["weighted_minus_equal_interaction"]}
                for row in old["upper_triangle_pairs"]]
        interactions[str(shift)] = {
            "definition": "[S(delta,weighted)-S(0,weighted)] - "
                          "[S(delta,equal)-S(0,equal)]; signed pairwise cosine changes",
            "reference_boundary_shift": 0,
            "upper_triangle_pairs": rows,
            "pair_denominator": old["pair_denominator"],
            "pairs_independent": False,
            "mean_signed": old["mean_signed"]}
    return {
        "schema_version": 1,
        "kind": "BOUNDARY_SHIFT_DESCRIPTIVE_METRICS",
        "status": base["status"],
        "axis": "boundary_shift",
        "base_step_body_tokens": BASE_STEP_BODY_TOKENS,
        "reference_boundary_shift": 0,
        "candidate_boundary_shifts": shifts,
        "company_order": base["company_order"],
        "pooling_methods": pooling,
        "top_k": base["top_k"],
        "denominators": base["denominators"],
        "arms": arms,
        "within_pooling_shift_comparisons": within,
        "same_shift_pooling_comparisons": same,
        "signed_shift_by_pooling_interactions": interactions,
        "uncertainty": base["uncertainty"],
        "inference_provenance": base["inference_provenance"]}
