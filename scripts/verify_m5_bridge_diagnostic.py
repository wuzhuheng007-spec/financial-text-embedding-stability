"""Independently recalculate saved-vector diagnostic summaries from detail CSVs."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    start = 0
    while start < len(values):
        end = start + 1
        while end < len(values) and values[order[end]] == values[order[start]]:
            end += 1
        mean = (start + 1 + end) / 2
        for j in order[start:end]:
            out[j] = mean
        start = end
    return out


def correlation(x: list[float], y: list[float]) -> float | None:
    a, b = ranks(x), ranks(y)
    if len(a) < 2:
        return None
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    numerator = sum((u - ma) * (v - mb) for u, v in zip(a, b))
    denominator = math.sqrt(sum((u - ma) ** 2 for u in a) * sum((v - mb) ** 2 for v in b))
    return numerator / denominator if denominator else None


def equal(actual: float | None, expected: float | None, label: str) -> None:
    if actual is None or expected is None:
        assert actual is expected, label
    else:
        assert math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12), (label, actual, expected)


def verify(directory: Path) -> dict[str, int]:
    summary = json.loads((directory / "diagnostic_summary.json").read_text(encoding="utf-8"))
    pairs = rows(directory / "similarity_pairs.csv")
    changes = rows(directory / "neighbor_changes.csv")
    gaps = rows(directory / "boundary_gaps.csv")
    by_pair: dict[str, list[dict[str, str]]] = defaultdict(list)
    by_change: dict[str, list[dict[str, str]]] = defaultdict(list)
    by_gap: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in pairs:
        by_pair[row["comparison_id"]].append(row)
    for row in changes:
        by_change[row["comparison_id"]].append(row)
    for row in gaps:
        by_gap[row["comparison_id"]].append(row)
    assert set(by_pair) == set(by_change) == set(by_gap)
    assert set(by_pair) == {item["comparison_id"] for item in summary["comparison_summaries"]}
    k = summary["k"]
    for item in summary["comparison_summaries"]:
        ident = item["comparison_id"]
        pair_data, change_data, gap_data = by_pair[ident], by_change[ident], by_gap[ident]
        assert len(pair_data) == item["pair_count"] == item["company_count"] * (item["company_count"] - 1) // 2
        assert len(change_data) == item["company_count"]
        assert len(gap_data) == 2 * item["company_count"]
        absolute = [abs(float(row["reference_similarity"]) - float(row["arm_similarity"])) for row in pair_data]
        for row, value in zip(pair_data, absolute):
            equal(float(row["absolute_difference"]), value, ident + " pair diff")
        equal(item["mean_abs_cosine_difference"], sum(absolute) / len(absolute), ident + " mean")
        equal(item["max_abs_cosine_difference"], max(absolute), ident + " max")
        equal(item["upper_triangle_spearman"], correlation(
            [float(row["reference_similarity"]) for row in pair_data],
            [float(row["arm_similarity"]) for row in pair_data]), ident + " Spearman")
        reference_edges, arm_edges = set(), set()
        for row in change_data:
            ref_set = set(row["reference_neighbors"].split("|"))
            arm_set = set(row["arm_neighbors"].split("|"))
            assert len(ref_set) == len(arm_set) == k
            common = len(ref_set & arm_set)
            assert int(row["overlap_count"]) == common
            equal(float(row["overlap_fraction"]), common / k, ident + " overlap")
            equal(float(row["jaccard"]), common / len(ref_set | arm_set), ident + " Jaccard")
            assert int(row["changed"]) == int(ref_set != arm_set)
            reference_edges.update((row["company_id"], target) for target in ref_set)
            arm_edges.update((row["company_id"], target) for target in arm_set)
            gap_row = next(gap for gap in gap_data if gap["company_id"] == row["company_id"] and gap["role"] == "reference")
            assert row["reference_gap"] == gap_row["gap"]
        equal(item["directed_edge_change_rate"], len(reference_edges ^ arm_edges) / len(reference_edges | arm_edges), ident + " edges")
        assert item["changed_company_count"] == sum(int(row["changed"]) for row in change_data)
        equal(item["mean_overlap_fraction"], sum(float(row["overlap_fraction"]) for row in change_data) / len(change_data), ident + " mean overlap")
        equal(item["mean_jaccard"], sum(float(row["jaccard"]) for row in change_data) / len(change_data), ident + " mean Jaccard")
        assert item["reference_cutoff_ties"] == sum(int(row["reference_exact_cutoff_tie"]) for row in change_data)
        assert item["arm_cutoff_ties"] == sum(int(row["arm_exact_cutoff_tie"]) for row in change_data)
    for axis, counts in summary["company_change_counts_excluding_identity_controls"].items():
        for company, total in counts.items():
            assert total == sum(int(row["changed"]) for row in changes if row["axis"] == axis
                                and row["company_id"] == company and row["reference_arm"] != row["arm"])
    svg = ElementTree.parse(directory / "neighbor_change_heatmap.svg")
    titles = [node.text for node in svg.iter() if node.tag.endswith("title")]
    assert len(titles) == len(changes)
    for row in changes:
        assert f'{row["comparison_id"]} {row["company_id"]} changed={row["changed"]}' in titles
    return {"comparisons": len(by_pair), "pair_rows": len(pairs), "neighbor_rows": len(changes), "gap_rows": len(gaps), "figure_cells": len(titles)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps({"status": "PASS", **verify(arguments.run_dir)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
