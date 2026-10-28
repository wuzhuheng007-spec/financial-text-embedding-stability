"""Read-only M7 arithmetic and provenance self-check from saved real-run files.

This uses separate arithmetic for cosine, peer ranks, BM25 normalization, and
10-Q aggregation. It is a same-team self-check, not an independent data audit.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def receipt_files(folder: Path) -> int:
    receipt = json.loads((folder / "receipt.json").read_text(encoding="utf-8"))
    entries = receipt.get("outputs", receipt.get("output_files"))
    require(isinstance(entries, dict), f"No output map: {folder}")
    for name, spec in entries.items():
        path = folder / name
        require(path.is_file() and path.stat().st_size == spec["bytes"] and
                digest(path) == spec["sha256"], f"Output hash mismatch: {path}")
    return len(entries)


def cosine(vectors: np.ndarray) -> np.ndarray:
    require(vectors.ndim == 2 and np.isfinite(vectors).all(), "Invalid vector array")
    unit = vectors / np.linalg.norm(vectors, axis=1)[:, None]
    return unit @ unit.T


def csv_matrix(path: Path) -> np.ndarray:
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.reader(stream))
    labels = rows[0][1:]
    require([row[0] for row in rows[1:]] == labels, f"Matrix rows differ: {path}")
    return np.asarray([[float(x) for x in row[1:]] for row in rows[1:]])


def check_tail(folder: Path) -> dict:
    meta = json.loads((folder / "tail_weight_diagnostics.json").read_text(encoding="utf-8"))
    names = meta["company_order"]
    maximum = 0.0
    count = 0
    for size in (128, 192):
        baseline = csv_matrix(folder / f"cosine_{size}_equal_chunks.csv")
        for method in ("equal_chunks", "body_token_weighted", "tail_only_ratio",
                       "drop_last_chunk_equal"):
            vectors = np.load(folder / f"vectors_{size}_{method}.npy", allow_pickle=False)
            saved = csv_matrix(folder / f"cosine_{size}_{method}.csv")
            recomputed = cosine(vectors)
            error = float(np.max(np.abs(saved - recomputed)))
            require(error < 1e-12, f"Tail cosine mismatch: {size}, {method}")
            maximum = max(maximum, error)
            peers = meta["matrix_results"][str(size)][method]["top2_and_margin"]
            for i, ticker in enumerate(names):
                order = sorted((j for j in range(len(names)) if j != i),
                               key=lambda j: (-float(recomputed[i, j]), names[j]))
                expected = [names[j] for j in order[:2]]
                margin = float(recomputed[i, order[1]] - recomputed[i, order[2]])
                require(peers[ticker]["top2"] == expected and
                        abs(peers[ticker]["rank2_minus_rank3"] - margin) < 1e-12,
                        f"Tail peers/margin mismatch: {size}, {method}, {ticker}")
                count += 1
            delta = float(np.max(np.abs((recomputed - baseline)[np.triu_indices(len(names), 1)])))
            require(abs(delta - meta["matrix_results"][str(size)][method]
                        ["max_abs_offdiag_vs_equal"]) < 1e-12,
                    f"Tail matrix delta mismatch: {size}, {method}")
        a = np.load(folder / f"vectors_{size}_body_token_weighted.npy", allow_pickle=False)
        b = np.load(folder / f"vectors_{size}_tail_only_ratio.npy", allow_pickle=False)
        require(np.max(np.abs(a - b)) < 1e-12, f"Tail algebraic check: {size}")
    return {"status": "PASS", "receipt_outputs": receipt_files(folder),
            "peer_rows_checked": count, "max_cosine_error": maximum}


def check_bm25(folder: Path) -> dict:
    meta = json.loads((folder / "bm25_normalization_diagnostics.json").read_text(encoding="utf-8"))
    rows = {(row["ticker"], float(row["b"])): row for row in meta["normalization_rows"]}
    rank_rows = list(csv.DictReader((folder / "bm25_peer_rankings.csv").open(
        newline="", encoding="utf-8")))
    names = meta["company_order"]
    maximum = 0.0
    checked = 0
    with np.load(folder / "bm25_termweights_and_matrices.npz", allow_pickle=False) as data:
        for setting in (0.0, 0.75, 1.0):
            key = f"{setting:g}"
            raw = data[f"raw_weights_b{key}"]
            units = raw / np.linalg.norm(raw, axis=1)[:, None]
            saved_units = data[f"unit_weights_b{key}"]
            saved_cos = data[f"cosine_b{key}"]
            maximum = max(maximum, float(np.max(np.abs(units - saved_units))),
                          float(np.max(np.abs(units @ units.T - saved_cos))))
            require(maximum < 1e-12, f"BM25 L2/cosine mismatch b={setting}")
            reference = data["raw_weights_b0"]
            for i, ticker in enumerate(names):
                row = rows[(ticker, setting)]
                x, y = reference[i], raw[i]
                require(np.array_equal(x != 0, y != 0), "BM25 term support changed")
                scale = float((x @ y) / (x @ x))
                residual = float(np.linalg.norm(y - scale * x) / np.linalg.norm(y))
                direction = float((reference[i] / np.linalg.norm(reference[i])) @ units[i])
                require(abs(row["pre_l2_norm"] - np.linalg.norm(y)) < 1e-10 and
                        abs(row["fitted_scale_alpha"] - scale) < 1e-10 and
                        abs(row["relative_residual"] - residual) < 1e-10 and
                        abs(row["direction_cosine_vs_b0"] - direction) < 1e-10,
                        f"BM25 normalization row mismatch: {ticker}, {setting}")
                checked += 1
            for kind, matrix in (("termweight_cosine", saved_cos),
                                 ("directed_query_to_document", data[f"directed_b{key}"])):
                selected = [r for r in rank_rows if float(r["b"]) == setting and
                            r["ranking_type"] == kind]
                require(len(selected) == len(names) * (len(names) - 1),
                        f"BM25 ranking row count: {setting}, {kind}")
                for i, ticker in enumerate(names):
                    got = sorted((r for r in selected if r["query_ticker"] == ticker),
                                 key=lambda r: int(r["rank"]))
                    order = sorted((j for j in range(len(names)) if j != i),
                                   key=lambda j: (-float(matrix[i, j]), names[j]))
                    require([r["target_ticker"] for r in got] == [names[j] for j in order],
                            f"BM25 peer rank mismatch: {ticker}, {setting}, {kind}")
                    require(all(abs(float(g["score"]) - float(matrix[i, j])) < 1e-10
                                for g, j in zip(got, order)), "BM25 peer score mismatch")
    return {"status": "PASS", "receipt_outputs": receipt_files(folder),
            "normalization_rows_checked": checked, "rank_rows_checked": len(rank_rows),
            "max_unit_or_cosine_error": maximum}


def check_tenq(folder: Path) -> dict:
    source, rep = folder / "pilot_v3", folder / "representation_v2"
    boundary = json.loads((source / "boundary_evidence.json").read_text(encoding="utf-8"))
    identity = json.loads((rep / "receipt.json").read_text(encoding="utf-8"))["source_identity"]
    text = (source / "primary_text.txt").read_text(encoding="utf-8")
    section = text[identity["start_char"]:identity["end_char_exclusive"]]
    require(identity["human_review"] == "PENDING" and
            hashlib.sha256(section.encode()).hexdigest() == identity["section_sha256"] and
            section.encode() == (source / "mda_section_program_candidate.txt").read_bytes() and
            boundary["selected_candidate_index"] == 1, "10-Q source section mismatch")
    summary = json.loads((rep / "representation_checks.json").read_text(encoding="utf-8"))
    vector_error = 0.0
    for row in summary["lengths"]:
        size = row["body_chunk_length"]
        chunks = [json.loads(line) for line in (rep / f"chunk_map_{size}.jsonl")
                  .read_text(encoding="utf-8").splitlines()]
        vectors = np.load(rep / f"chunk_vectors_{size}.npy", allow_pickle=False)
        require(len(chunks) == len(vectors) == row["chunk_count"], "10-Q chunk count mismatch")
        cursor = 0
        for chunk in chunks:
            require(chunk["body_token_start"] == cursor and
                    chunk["body_token_end_exclusive"] == cursor + chunk["body_token_count"] and
                    chunk["model_input_tokens_including_specials"] <= row["model_input_limit"] and
                    chunk["source_char_start_in_primary"] == identity["start_char"] +
                    chunk["source_char_start_in_section"], "10-Q chunk mapping mismatch")
            span = section[chunk["source_char_start_in_section"]:
                           chunk["source_char_end_exclusive_in_section"]]
            require(hashlib.sha256(span.encode()).hexdigest() == chunk["source_span_sha256"],
                    "10-Q chunk source hash mismatch")
            cursor = chunk["body_token_end_exclusive"]
        require(cursor == row["body_tokens"], "10-Q token coverage mismatch")
        units = vectors / np.linalg.norm(vectors, axis=1)[:, None]
        for method, weights in (("equal_chunks", np.ones(len(chunks))),
                                ("body_token_weighted", np.asarray(
                                    [c["body_token_count"] for c in chunks], dtype=float))):
            combined = np.average(units, axis=0, weights=weights)
            combined /= np.linalg.norm(combined)
            saved = np.load(rep / f"document_vector_{size}_{method}.npy", allow_pickle=False)
            vector_error = max(vector_error, float(np.max(np.abs(saved - combined))))
        require(vector_error < 1e-12, f"10-Q aggregate mismatch: {size}")
    return {"status": "PASS", "pilot_receipt_outputs": receipt_files(source),
            "representation_receipt_outputs": receipt_files(rep),
            "max_document_vector_error": vector_error,
            "human_review": "PENDING"}


def check_curated(run: Path, curated: Path) -> dict:
    copies = {"tail_weight_diagnostics.csv": "tail_v2/tail_weight_diagnostics.csv",
              "tail_weight_effects.svg": "tail_v2/tail_weight_effects.svg",
              "bm25_normalization_diagnostics.csv": "bm25/bm25_normalization_diagnostics.csv",
              "bm25_peer_rankings.csv": "bm25/bm25_peer_rankings.csv",
              "bm25_scaling_residuals.svg": "bm25/bm25_scaling_residuals.svg"}
    for name, original in copies.items():
        require((curated / name).read_bytes() == (run / original).read_bytes(),
                f"Curated copy differs: {name}")
        if name.endswith(".svg"):
            ET.parse(curated / name)
    rows = list(csv.DictReader((curated / "tenq_pilot_coverage.csv").open(
        newline="", encoding="utf-8")))
    reference = json.loads((run / "tenq/representation_v2/representation_checks.json")
                           .read_text(encoding="utf-8"))["lengths"]
    require(len(rows) == len(reference) == 2, "10-Q curated length count mismatch")
    for saved, source in zip(rows, reference):
        for key in ("body_chunk_length", "body_tokens", "chunk_count", "tail_body_tokens",
                    "max_actual_model_input_tokens", "model_input_limit"):
            require(int(saved[key]) == source[key], f"10-Q curated {key} differs")
        require(abs(float(saved["same_document_equal_vs_weighted_cosine"]) -
                    source["same_document_equal_vs_weighted_cosine"]) < 1e-12,
                "10-Q curated cosine differs")
    return {"status": "PASS", "byte_identical_copies": len(copies),
            "parseable_svg": 2, "tenq_coverage_rows": len(rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--curated", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), "Verification output already exists")
    result = {"kind": "M7_SAME_TEAM_RECOMPUTATION", "status": "PASS",
              "review_label": "same-team self-check; not independent audit",
              "tail": check_tail(args.run / "tail_v2"),
              "bm25": check_bm25(args.run / "bm25"),
              "tenq": check_tenq(args.run / "tenq"),
              "curated": check_curated(args.run, args.curated)}
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
