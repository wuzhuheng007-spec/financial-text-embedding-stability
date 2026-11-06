"""Pure descriptive metrics for an approved small-chunk vector grid.

This module accepts already-computed company vectors. It never tokenizes text,
loads an encoder, or infers whether the vectors came from a real model run.
"""

from __future__ import annotations

from itertools import combinations, product
from typing import Mapping, Sequence

import numpy as np

from .analysis import _cosine, topk, upper_triangle_spearman


POOLING_METHODS = ("equal_chunks", "body_token_weighted")
TIE_RULE = "descending exact float64 cosine; ascending company ID on exact ties"


def _validate_ids(company_ids: Sequence[str], top_k: int) -> list[str]:
    ids = list(company_ids)
    if (len(ids) < 4 or any(not isinstance(x, str) or not x or x.strip() != x for x in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError("At least four unique, nonempty ordered company IDs are required")
    if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k != 2 or top_k >= len(ids) - 1:
        raise ValueError("This metric contract requires Top-2 and at least one peer outside Top-2")
    return ids


def _validate_grid(vectors_by_arm: Mapping, lengths: Sequence[int], reference_length: int,
                   pooling_methods: Sequence[str], row_count: int) -> dict:
    lengths = list(lengths)
    methods = list(pooling_methods)
    if (not lengths or len(set(lengths)) != len(lengths)
            or any(not isinstance(x, int) or isinstance(x, bool) or x <= 0 for x in lengths)
            or reference_length not in lengths):
        raise ValueError("Lengths must be distinct positive integers and include the reference")
    if methods != list(POOLING_METHODS):
        raise ValueError("Both pooling methods must use the declared contract order")
    expected = set(product(lengths, methods))
    actual = set(vectors_by_arm)
    if actual != expected:
        raise ValueError(f"Incomplete or unexpected vector grid: missing={sorted(expected - actual)}, "
                         f"extra={sorted(actual - expected)}")
    arrays = {}
    dimension = None
    for key in product(lengths, methods):
        try:
            matrix = np.asarray(vectors_by_arm[key], dtype=np.float64)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid vector matrix for {key}") from exc
        if matrix.ndim != 2 or matrix.shape[0] != row_count or matrix.shape[1] == 0:
            raise ValueError(f"Vector shape differs from declared companies for {key}")
        if dimension is None:
            dimension = matrix.shape[1]
        if matrix.shape[1] != dimension or not np.isfinite(matrix).all():
            raise ValueError(f"Nonfinite or incompatible vectors for {key}")
        norms = np.linalg.norm(matrix, axis=1)
        if not np.isfinite(norms).all() or (norms <= 0).any():
            raise ValueError(f"Zero or nonfinite vector norm for {key}")
        arrays[key] = matrix / norms[:, None]
    return arrays


def _ranking(cosine: np.ndarray, ids: list[str]) -> dict:
    # topk shares the historical exact-score/ticker tie rule. Request all peers
    # so that rank-2/3 margins and tracked-peer movement use the same ordering.
    all_peers = topk(cosine, ids, len(ids) - 1)
    return {company: [peer["ticker"] for peer in all_peers[company]] for company in ids}


def _arm_description(cosine: np.ndarray, ids: list[str], order: dict) -> dict:
    margins = {}
    for i, company in enumerate(ids):
        second, third = order[company][1:3]
        gap = float(cosine[i, ids.index(second)] - cosine[i, ids.index(third)])
        margins[company] = {"rank2": second, "rank3": third,
                            "rank2_minus_rank3": gap, "exact_cutoff_tie": gap == 0.0}
    return {"cosine_matrix": cosine.tolist(), "neighbor_order": order,
            "top2": {company: order[company][:2] for company in ids},
            "rank2_rank3": margins, "tie_rule": TIE_RULE}


def _comparison(reference: tuple[int, str], candidate: tuple[int, str],
                ids: list[str], unit: dict, cosine: dict, order: dict) -> dict:
    ref_u, cand_u = unit[reference], unit[candidate]
    ref_s, cand_s = cosine[reference], cosine[candidate]
    distances = {}
    overlap = {}
    margin = {}
    tracked = {}
    rank_shift_total = 0
    for i, company in enumerate(ids):
        cross_cosine = float(ref_u[i] @ cand_u[i])
        # Roundoff can produce a dot product marginally above one.
        distance = float(1.0 - np.clip(cross_cosine, -1.0, 1.0))
        distances[company] = distance
        overlap[company] = len(set(order[reference][company][:2]) &
                               set(order[candidate][company][:2])) / 2
        ref_peers, cand_peers = order[reference][company], order[candidate][company]
        ref_gap = float(ref_s[i, ids.index(ref_peers[1])] - ref_s[i, ids.index(ref_peers[2])])
        cand_gap = float(cand_s[i, ids.index(cand_peers[1])] - cand_s[i, ids.index(cand_peers[2])])
        margin[company] = {"reference": ref_gap, "candidate": cand_gap,
                           "candidate_minus_reference": cand_gap - ref_gap,
                           "reference_exact_cutoff_tie": ref_gap == 0.0,
                           "candidate_exact_cutoff_tie": cand_gap == 0.0}
        rank_rows = []
        for peer in ref_peers[1:3]:
            before, after = ref_peers.index(peer) + 1, cand_peers.index(peer) + 1
            rank_shift_total += abs(after - before)
            rank_rows.append({"peer": peer, "reference_rank": before,
                              "candidate_rank": after, "rank_change": after - before})
        tracked[company] = rank_rows
    pair_rows = []
    for i, j in combinations(range(len(ids)), 2):
        delta = float(cand_s[i, j] - ref_s[i, j])
        pair_rows.append({"companies": [ids[i], ids[j]], "signed_cosine_change": delta,
                          "absolute_cosine_change": abs(delta)})
    rho = upper_triangle_spearman(ref_s, cand_s)
    rho = {"value": rho["value"], "reason": rho.get("reason"),
           "pair_count": len(pair_rows),
           "tie_rule": "exact float64 values receive average ranks; no tolerance grouping"}
    return {"reference_arm": {"body_tokens": reference[0], "pooling": reference[1]},
            "candidate_arm": {"body_tokens": candidate[0], "pooling": candidate[1]},
            "company_vector_cosine_distance": {"by_company": distances,
                "mean": float(sum(distances.values()) / len(ids)), "denominator": len(ids)},
            "similarity_change": {"upper_triangle_pairs": pair_rows,
                "mean_absolute": float(sum(x["absolute_cosine_change"] for x in pair_rows) / len(pair_rows)),
                "maximum_absolute": max(x["absolute_cosine_change"] for x in pair_rows),
                "pair_denominator": len(pair_rows), "pairs_independent": False},
            "upper_triangle_spearman": rho,
            "top2_overlap": {"by_company": overlap,
                "mean": float(sum(overlap.values()) / len(ids)), "company_denominator": len(ids),
                "set_denominator_per_company": 2},
            "rank2_rank3_margin": margin,
            "reference_rank2_rank3_peer_movement": {"by_company": tracked,
                "mean_absolute_rank_shift": float(rank_shift_total / (2 * len(ids))),
                "tracked_peer_denominator": 2 * len(ids)}}


def compute_small_chunk_metrics(company_ids: Sequence[str], vectors_by_arm: Mapping,
                                lengths: Sequence[int], *, reference_length: int = 128,
                                pooling_methods: Sequence[str] = POOLING_METHODS,
                                top_k: int = 2) -> dict:
    """Describe a complete fixed-company grid of final document vectors.

    `vectors_by_arm[(body_length, pooling_name)]` is a matrix whose rows follow
    `company_ids`. The caller must authenticate vector provenance and approvals;
    this function only enforces numerical and grid completeness contracts.
    """
    ids = _validate_ids(company_ids, top_k)
    unit = _validate_grid(vectors_by_arm, lengths, reference_length, pooling_methods, len(ids))
    lengths = list(lengths)
    cosine = {key: _cosine(value) for key, value in unit.items()}
    order = {key: _ranking(matrix, ids) for key, matrix in cosine.items()}
    arms = {str(length): {pool: _arm_description(cosine[(length, pool)], ids,
                       order[(length, pool)]) for pool in pooling_methods} for length in lengths}
    length_comparisons = {str(length): {pool: _comparison((reference_length, pool), (length, pool),
                          ids, unit, cosine, order) for pool in pooling_methods}
                          for length in lengths if length != reference_length}
    pooling_comparisons = {str(length): _comparison((length, "equal_chunks"),
                           (length, "body_token_weighted"), ids, unit, cosine, order)
                           for length in lengths}
    interactions = {}
    equal, weighted = POOLING_METHODS
    for length in lengths:
        if length == reference_length:
            continue
        rows = []
        for i, j in combinations(range(len(ids)), 2):
            equal_delta = float(cosine[(length, equal)][i, j] -
                                cosine[(reference_length, equal)][i, j])
            weighted_delta = float(cosine[(length, weighted)][i, j] -
                                   cosine[(reference_length, weighted)][i, j])
            rows.append({"companies": [ids[i], ids[j]], "equal_length_effect": equal_delta,
                         "weighted_length_effect": weighted_delta,
                         "weighted_minus_equal_interaction": weighted_delta - equal_delta})
        interactions[str(length)] = {"definition": "(S_L_weighted - S_ref_weighted) - "
            "(S_L_equal - S_ref_equal); signed company-pair cosine effects",
            "reference_body_tokens": reference_length, "upper_triangle_pairs": rows,
            "pair_denominator": len(rows), "pairs_independent": False,
            "mean_signed": float(sum(x["weighted_minus_equal_interaction"] for x in rows) / len(rows))}
    return {"schema_version": 1, "kind": "SMALL_CHUNK_DESCRIPTIVE_METRICS",
            "status": "COMPLETE_NUMERICAL_ONLY", "company_order": ids,
            "reference_body_tokens": reference_length, "candidate_body_tokens": lengths,
            "pooling_methods": list(pooling_methods), "top_k": top_k,
            "denominators": {"companies": len(ids), "upper_triangle_pairs": len(ids) * (len(ids) - 1) // 2,
                             "tracked_reference_rank2_rank3_peers": 2 * len(ids)},
            "arms": arms, "within_pooling_length_comparisons": length_comparisons,
            "same_length_pooling_comparisons": pooling_comparisons,
            "signed_length_by_pooling_interactions": interactions,
            "uncertainty": "none; company pairs share firms and are not independent observations",
            "inference_provenance": "UNKNOWN_TO_THIS_PURE_FUNCTION"}
