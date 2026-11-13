"""Read-only neighbor diagnostics for the saved five-firm S2/S3 vectors.

This module never constructs an encoder or a financial target.  A source manifest
binds every vector to its text, model revision, settings, and file hash.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping
from xml.sax.saxutils import escape

import numpy as np


POOLS = ("equal_chunks", "body_token_weighted")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _rank(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    result = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        average_rank = (start + 1 + end) / 2.0
        for index in order[start:end]:
            result[index] = average_rank
        start = end
    return result


def spearman(x: list[float], y: list[float]) -> float | None:
    if len(x) != len(y) or len(x) < 2:
        return None
    a, b = _rank(x), _rank(y)
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    cross = sum((u - ma) * (v - mb) for u, v in zip(a, b))
    va = sum((u - ma) ** 2 for u in a)
    vb = sum((v - mb) ** 2 for v in b)
    if va == 0 or vb == 0:
        return None
    return cross / math.sqrt(va * vb)


def cosine_matrix(vectors: Mapping[str, np.ndarray]) -> tuple[list[str], np.ndarray]:
    ids = sorted(vectors)
    if len(ids) < 2:
        raise ValueError("At least two company IDs are required")
    shapes = {np.asarray(vectors[name]).shape for name in ids}
    if len(shapes) != 1 or len(next(iter(shapes))) != 1:
        raise ValueError("All company vectors must have one common 1-D shape")
    matrix = np.vstack([np.asarray(vectors[name], dtype=np.float64) for name in ids])
    if not np.isfinite(matrix).all():
        raise ValueError("Nonfinite company vector")
    norms = np.linalg.norm(matrix, axis=1)
    if not np.isfinite(norms).all() or (norms == 0).any():
        raise ValueError("Zero or invalid company vector norm")
    unit = matrix / norms[:, None]
    return ids, unit @ unit.T


def _neighbors(ids: list[str], similarities: np.ndarray, k: int) -> dict[str, dict[str, Any]]:
    if k <= 0 or len(ids) <= k:
        raise ValueError("Require 0 < k < number of companies")
    answer: dict[str, dict[str, Any]] = {}
    for i, company in enumerate(ids):
        order = sorted((j for j in range(len(ids)) if j != i), key=lambda j: (-float(similarities[i, j]), ids[j]))
        kth = float(similarities[i, order[k - 1]])
        next_value = float(similarities[i, order[k]]) if len(order) > k else None
        gap = kth - next_value if next_value is not None else None
        answer[company] = {
            "top_k": [ids[j] for j in order[:k]],
            "rank_k": ids[order[k - 1]],
            "rank_k1": ids[order[k]] if len(order) > k else None,
            "gap": gap,
            "exact_cutoff_tie": next_value is not None and kth == next_value,
            "near_cutoff_tie_1e_12": next_value is not None and gap <= 1e-12,
        }
    return answer


def compare_vectors(
    reference: Mapping[str, np.ndarray], arm: Mapping[str, np.ndarray], k: int = 2
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if set(reference) != set(arm):
        raise ValueError("Company ID universes differ; missing companies cannot be filled")
    ids, ref_matrix = cosine_matrix(reference)
    arm_ids, arm_matrix = cosine_matrix(arm)
    assert ids == arm_ids
    ref_neighbors = _neighbors(ids, ref_matrix, k)
    arm_neighbors = _neighbors(ids, arm_matrix, k)
    pairs: list[dict[str, Any]] = []
    for i, first in enumerate(ids):
        for j in range(i + 1, len(ids)):
            ref_value = float(ref_matrix[i, j])
            arm_value = float(arm_matrix[i, j])
            pairs.append({"company_a": first, "company_b": ids[j], "reference_similarity": ref_value,
                          "arm_similarity": arm_value, "absolute_difference": abs(ref_value - arm_value)})
    changes: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    ref_edges, arm_edges = set(), set()
    for company in ids:
        ref_set = set(ref_neighbors[company]["top_k"])
        arm_set = set(arm_neighbors[company]["top_k"])
        ref_edges.update((company, neighbor) for neighbor in ref_set)
        arm_edges.update((company, neighbor) for neighbor in arm_set)
        common, union = len(ref_set & arm_set), len(ref_set | arm_set)
        changes.append({
            "company_id": company,
            "reference_neighbors": "|".join(ref_neighbors[company]["top_k"]),
            "arm_neighbors": "|".join(arm_neighbors[company]["top_k"]),
            "overlap_count": common, "overlap_fraction": common / k,
            "jaccard": common / union, "changed": int(ref_set != arm_set),
            "reference_gap": ref_neighbors[company]["gap"],
            "reference_exact_cutoff_tie": int(ref_neighbors[company]["exact_cutoff_tie"]),
            "arm_exact_cutoff_tie": int(arm_neighbors[company]["exact_cutoff_tie"]),
        })
        for label, info in (("reference", ref_neighbors[company]), ("arm", arm_neighbors[company])):
            gaps.append({"role": label, "company_id": company, "rank_k_neighbor": info["rank_k"],
                         "rank_k1_neighbor": info["rank_k1"], "gap": info["gap"],
                         "exact_cutoff_tie": int(info["exact_cutoff_tie"]),
                         "near_cutoff_tie_1e_12": int(info["near_cutoff_tie_1e_12"])})
    difference = [row["absolute_difference"] for row in pairs]
    summary = {
        "company_count": len(ids), "pair_count": len(pairs), "k": k,
        "mean_abs_cosine_difference": sum(difference) / len(difference),
        "max_abs_cosine_difference": max(difference),
        "upper_triangle_spearman": spearman(
            [row["reference_similarity"] for row in pairs],
            [row["arm_similarity"] for row in pairs]),
        "directed_edge_change_rate": len(ref_edges ^ arm_edges) / len(ref_edges | arm_edges),
        "changed_company_count": sum(row["changed"] for row in changes),
        "mean_overlap_fraction": sum(row["overlap_fraction"] for row in changes) / len(changes),
        "mean_jaccard": sum(row["jaccard"] for row in changes) / len(changes),
        "reference_cutoff_ties": sum(row["reference_exact_cutoff_tie"] for row in changes),
        "arm_cutoff_ties": sum(row["arm_exact_cutoff_tie"] for row in changes),
    }
    return summary, pairs, changes, gaps


def load_saved_manifest(path: Path, axis: str) -> tuple[dict[str, Any], dict[str, dict[str, np.ndarray]]]:
    source = json.loads(path.read_text(encoding="utf-8"))
    if source.get("status") != "COMPLETE" or source.get("evidence_class") != "REAL_MODEL":
        raise ValueError(f"Saved source is not a completed real-model run: {path}")
    metric_file = source["metric_file"]
    metrics_path = path.parent / metric_file["path"]
    if sha256_file(metrics_path) != metric_file["sha256"]:
        raise ValueError(f"Historical metrics hash mismatch: {metrics_path}")
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    if metrics.get("status") != "COMPLETE_NUMERICAL_ONLY":
        raise ValueError("Historical metrics are incomplete")
    company_order = metrics["company_order"]
    jobs = source["jobs"]
    if len({job["ticker"] for job in jobs}) != len(company_order):
        raise ValueError("Manifest and historical metric company universes disagree")
    arms: dict[str, dict[str, np.ndarray]] = {}
    seen = set()
    for job in jobs:
        if job.get("status") != "COMPLETE":
            raise ValueError("Incomplete saved vector job")
        parameter = job["body_tokens_per_chunk"] if axis == "length" else job["boundary_shift"]
        for pooling in POOLS:
            label = f"{axis}:{parameter:+d}:{pooling}" if axis == "shift" else f"{axis}:{parameter}:{pooling}"
            key = (label, job["ticker"])
            if key in seen:
                raise ValueError(f"Duplicate saved company vector: {key}")
            seen.add(key)
            entry = job["files"][pooling]
            vector_path = (path.parent / entry["path"]).resolve()
            if not vector_path.is_relative_to(path.parent.resolve()):
                raise ValueError("Vector path escapes saved source directory")
            if sha256_file(vector_path) != entry["sha256"]:
                raise ValueError(f"Vector hash mismatch: {vector_path}")
            vector = np.load(vector_path, allow_pickle=False)
            if list(vector.shape) != entry["shape"] or str(vector.dtype) != entry["dtype"]:
                raise ValueError(f"Vector shape/dtype mismatch: {vector_path}")
            if vector.ndim != 1 or not np.isfinite(vector).all() or np.linalg.norm(vector) == 0:
                raise ValueError(f"Invalid vector values: {vector_path}")
            arms.setdefault(label, {})[job["ticker"]] = vector
    if set(company_order) != {job["ticker"] for job in jobs}:
        raise ValueError("Historical company order and manifest IDs disagree")
    for label, vectors in arms.items():
        if set(vectors) != set(company_order):
            raise ValueError(f"Incomplete company universe in {label}")
        _, actual_matrix = cosine_matrix(vectors)
        ordered_ids = sorted(vectors)
        param = label.split(":")[1]
        pool = label.split(":")[2]
        saved_key = str(int(param))
        historical = np.asarray(metrics["arms"][saved_key][pool]["cosine_matrix"], dtype=np.float64)
        positions = [company_order.index(company) for company in ordered_ids]
        if historical.shape != (len(company_order), len(company_order)) or not np.allclose(
            actual_matrix, historical[np.ix_(positions, positions)], rtol=0, atol=1e-12
        ):
            raise ValueError(f"RESULT_MISMATCH: historical cosine matrix for {label}")
    return source, arms


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"No detail rows for {path.name}")
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _heatmap(path: Path, changes: list[dict[str, Any]], manifest_hashes: dict[str, str]) -> None:
    labels = list(dict.fromkeys(row["comparison_id"] for row in changes))
    companies = sorted({row["company_id"] for row in changes})
    values = {(row["comparison_id"], row["company_id"]): int(row["changed"]) for row in changes}
    left, top, cell, row_height = 355, 110, 35, 25
    width, height = left + cell * len(companies) + 30, top + row_height * len(labels) + 45
    lines = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
             '<rect width="100%" height="100%" fill="white"/>',
             '<text x="12" y="24" font-family="Arial" font-size="16">Saved-vector top-k neighbor changes (five 2025 filings)</text>',
             f'<text x="12" y="45" font-family="Arial" font-size="10">round4 {manifest_hashes["round4"][:12]} | round6 {manifest_hashes["round6"][:12]} | red=changed, blue=unchanged</text>']
    for column, company in enumerate(companies):
        lines.append(f'<text x="{left + column * cell + 2}" y="{top - 12}" font-family="Arial" font-size="11">{escape(company)}</text>')
    for row_number, label in enumerate(labels):
        y = top + row_number * row_height
        lines.append(f'<text x="12" y="{y + 15}" font-family="Arial" font-size="10">{escape(label)}</text>')
        for column, company in enumerate(companies):
            value = values[(label, company)]
            color = "#c74b40" if value else "#337aa8"
            x = left + column * cell
            lines.append(f'<rect x="{x}" y="{y}" width="{cell - 3}" height="{row_height - 3}" fill="{color}"><title>{escape(label)} {escape(company)} changed={value}</title></rect>')
    lines.append("</svg>")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_diagnosis(round4_path: Path, round6_path: Path, output: Path, k: int = 2) -> dict[str, Any]:
    round4, length_arms = load_saved_manifest(round4_path, "length")
    round6, shift_arms = load_saved_manifest(round6_path, "shift")
    if round4["model_identity"] != round6["model_identity"]:
        raise ValueError("Model identity/revision differs between saved sources")
    if round4["input_manifest_sha256"] != round6["input_manifest_sha256"]:
        raise ValueError("Input manifest differs between saved sources")
    if sorted(round4["input_versions"], key=lambda row: row["ticker"]) != sorted(
        round6["input_versions"], key=lambda row: row["ticker"]
    ):
        raise ValueError("Saved text versions differ between sources")
    reference_length = round6["reference_body_tokens"]
    for pooling in POOLS:
        length_ref = length_arms[f"length:{reference_length}:{pooling}"]
        shift_ref = shift_arms[f"shift:+0:{pooling}"]
        if set(length_ref) != set(shift_ref) or any(
            not np.array_equal(length_ref[name], shift_ref[name]) for name in length_ref
        ):
            raise ValueError("RESULT_MISMATCH: round4 reference and round6 zero shift")
    comparisons: list[tuple[str, str, str, Mapping[str, np.ndarray], Mapping[str, np.ndarray]]] = []
    for pooling in POOLS:
        ref_name = f"length:{reference_length}:{pooling}"
        for arm_name in sorted((name for name in length_arms if name.endswith(f":{pooling}")), key=lambda name: int(name.split(":")[1])):
            comparisons.append(("chunk_length", ref_name, arm_name, length_arms[ref_name], length_arms[arm_name]))
        ref_name = f"shift:+0:{pooling}"
        for arm_name in sorted((name for name in shift_arms if name.endswith(f":{pooling}")), key=lambda name: int(name.split(":")[1])):
            comparisons.append(("boundary_shift", ref_name, arm_name, shift_arms[ref_name], shift_arms[arm_name]))
    ref_name = f"length:{reference_length}:equal_chunks"
    arm_name = f"length:{reference_length}:body_token_weighted"
    comparisons.append(("pooling_at_fixed_length", ref_name, arm_name, length_arms[ref_name], length_arms[arm_name]))
    pair_rows: list[dict[str, Any]] = []
    change_rows: list[dict[str, Any]] = []
    gap_rows: list[dict[str, Any]] = []
    comparison_summaries: list[dict[str, Any]] = []
    for axis, reference_name, arm_name, reference, arm in comparisons:
        comparison_id = f"{axis}|{reference_name}|{arm_name}"
        result, pairs, changes, gaps = compare_vectors(reference, arm, k=k)
        metadata = {"comparison_id": comparison_id, "axis": axis, "reference_arm": reference_name, "arm": arm_name}
        comparison_summaries.append({**metadata, "identity_control": reference_name == arm_name, **result})
        pair_rows.extend({**metadata, **row} for row in pairs)
        change_rows.extend({**metadata, **row} for row in changes)
        gap_rows.extend({**metadata, **row} for row in gaps)
    output.mkdir(parents=True, exist_ok=True)
    _write_csv(output / "similarity_pairs.csv", pair_rows)
    _write_csv(output / "neighbor_changes.csv", change_rows)
    _write_csv(output / "boundary_gaps.csv", gap_rows)
    manifest_hashes = {"round4": sha256_file(round4_path), "round6": sha256_file(round6_path)}
    _heatmap(output / "neighbor_change_heatmap.svg", change_rows, manifest_hashes)
    company_counts = {axis: {company: sum(int(row["changed"]) for row in change_rows
                                              if row["axis"] == axis and row["company_id"] == company
                                              and row["reference_arm"] != row["arm"])
                             for company in sorted(length_arms[f"length:{reference_length}:equal_chunks"])}
                      for axis in ("chunk_length", "boundary_shift", "pooling_at_fixed_length")}
    summary = {
        "schema": "m5_bridge_saved_vector_diagnostic_v1", "run_id": output.parent.name,
        "diagnostic_code_sha256": sha256_file(Path(__file__)),
        "evidence_class": "REAL_SAVED_VECTORS_DESCRIPTIVE",
        "sample_role": "historical_development_five_2025_filings", "formal_m5_financial_experiment": "NOT_RUN",
        "encoder_forward_calls_this_run": 0, "company_count": len(round4["input_versions"]), "k": k,
        "reference_body_tokens": reference_length, "model_identity": round4["model_identity"],
        "input_manifest_sha256": round4["input_manifest_sha256"], "input_versions": round4["input_versions"],
        "source_manifest_sha256": manifest_hashes, "comparison_summaries": comparison_summaries,
        "company_change_counts_excluding_identity_controls": company_counts,
        "limitations": ["Five selected 2025 Item 1 filings; no independent confirmation sample",
                        "Correlated companies and configurations are descriptive, not independent replicates",
                        "Length perturbations also alter chunk boundaries and tail weights",
                        "No prediction signal, price target, or financial result was computed"],
    }
    (output / "diagnostic_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnose saved company-vector neighbor stability")
    parser.add_argument("--manifest", required=True, type=Path, help="Saved round4 manifest.json")
    parser.add_argument("--secondary-manifest", required=True, type=Path, help="Saved round6 manifest.json")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--k", type=int, default=2)
    args = parser.parse_args(argv)
    summary = run_diagnosis(args.manifest, args.secondary_manifest, args.output, args.k)
    print(json.dumps({"status": "COMPLETE", "output": str(args.output), "comparisons": len(summary["comparison_summaries"]),
                      "encoder_forward_calls_this_run": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
