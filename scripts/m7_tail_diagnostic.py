"""Exploratory short-tail pooling diagnostic on the fixed real M1 Item 1 corpus.

The prior M6 run saved only document vectors, so this command re-embeds the
identical body chunks and gates all further comparisons on reproducing the
saved M6 document vectors and cosines. It never downloads or edits input text.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np

from finembed.analysis import validate_corpus, upper_triangle_spearman
from finembed.config import load_config
from finembed.model import Encoder, ModelError, aggregate


METHODS = ("equal_chunks", "body_token_weighted", "tail_only_ratio", "drop_last_chunk_equal")


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True,
                               allow_nan=False) + "\n", encoding="utf-8")


def read_matrix(path: Path, ids: list[str]) -> np.ndarray:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if rows[0] != ["document_id", *ids] or [row[0] for row in rows[1:]] != ids:
        raise ValueError(f"Saved cosine company order differs: {path}")
    matrix = np.asarray([[float(x) for x in row[1:]] for row in rows[1:]], dtype=np.float64)
    if matrix.shape != (len(ids), len(ids)) or not np.isfinite(matrix).all():
        raise ValueError(f"Saved cosine malformed: {path}")
    return matrix


def save_matrix(path: Path, ids: list[str], matrix: np.ndarray) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["document_id", *ids])
        writer.writerows([[ids[i], *matrix[i].tolist()] for i in range(len(ids))])


def weights_for_chunks(counts: list[int], body_length: int) -> dict[str, list[float] | None]:
    """Return unnormalized weights; whole-body and tail-only are algebraically equal.

    The equality requires every non-tail chunk to contain exactly body_length
    tokens, and requires both methods to use the *same* chunk vectors and L2
    normalization order. A one-chunk drop-tail ablation is undefined.
    """
    if not counts or not isinstance(body_length, int) or body_length <= 0:
        raise ValueError("Require a positive chunk length and nonempty chunk list")
    if any(type(n) is not int or n <= 0 or n > body_length for n in counts):
        raise ValueError("Invalid body-token count in chunks")
    if any(n != body_length for n in counts[:-1]):
        raise ValueError("Non-tail chunks are not full; tail-ratio formula is inapplicable")
    return {
        "equal_chunks": [1.0] * len(counts),
        "body_token_weighted": [float(n) for n in counts],
        "tail_only_ratio": [1.0] * (len(counts) - 1) + [counts[-1] / body_length],
        "drop_last_chunk_equal": [1.0] * (len(counts) - 1) if len(counts) > 1 else None,
    }


def normalized_weights(values: list[float] | None) -> list[float] | None:
    if values is None:
        return None
    total = math.fsum(values)
    if not math.isfinite(total) or total <= 0:
        raise ValueError("Invalid weight total")
    return [x / total for x in values]


def cosine(vectors: np.ndarray) -> np.ndarray:
    x = np.asarray(vectors, dtype=np.float64)
    if x.ndim != 2 or not np.isfinite(x).all():
        raise ValueError("Invalid document vector matrix")
    norms = np.linalg.norm(x, axis=1)
    if np.any(norms <= 0) or not np.isfinite(norms).all():
        raise ValueError("Zero or nonfinite document vector")
    y = x / norms[:, None]
    return y @ y.T


def peers_and_margin(matrix: np.ndarray, tickers: list[str]) -> dict[str, dict]:
    if matrix.shape != (len(tickers), len(tickers)) or len(tickers) < 4:
        raise ValueError("Top-2 with rank-3 margin needs at least four companies")
    rows = {}
    for i, ticker in enumerate(tickers):
        ranking = sorted((j for j in range(len(tickers)) if j != i),
                         key=lambda j: (-float(matrix[i, j]), tickers[j]))
        second, third = ranking[1:3]
        rows[ticker] = {
            "top2": [tickers[j] for j in ranking[:2]],
            "rank2_score": float(matrix[i, second]),
            "rank3_ticker": tickers[third],
            "rank3_score": float(matrix[i, third]),
            "rank2_minus_rank3": float(matrix[i, second] - matrix[i, third]),
            "exact_cutoff_tie": bool(matrix[i, second] == matrix[i, third]),
        }
    return rows


def verify_prior(prior: Path, config_hash: str, corpus_hash: str) -> dict:
    receipt = json.loads((prior / "receipt.json").read_text(encoding="utf-8"))
    if receipt.get("status") != "DONE" or receipt.get("dense_status") != "DONE":
        raise ValueError("Prior M6 dense receipt is not DONE")
    if receipt.get("config_sha256") != config_hash:
        raise ValueError("Prior M6 config hash differs")
    prior_index = Path(receipt["arguments"]["corpus"])
    if file_hash(prior_index) != corpus_hash:
        raise ValueError("Prior M6 corpus index hash differs")
    for name, saved in receipt["outputs"].items():
        path = prior / name
        if not path.is_file() or path.stat().st_size != saved["bytes"] or file_hash(path) != saved["sha256"]:
            raise ValueError(f"Prior M6 output changed: {path}")
    return receipt


def svg_plot(path: Path, rows: list[dict], run_id: str, corpus_hash: str) -> None:
    # A deliberately simple static scientific diagnostic: unit-vector L2 shifts.
    by_length = {length: [r for r in rows if r["body_length"] == length and
                  r["method"] in ("body_token_weighted", "drop_last_chunk_equal")]
                 for length in (128, 192)}
    maximum = max((r["vector_l2_from_equal"] for group in by_length.values() for r in group), default=0)
    scale = 500 / maximum if maximum > 0 else 0
    lines = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1150" height="650" viewBox="0 0 1150 650">',
             '<rect width="1150" height="650" fill="#fff"/>',
             '<text x="30" y="40" font-family="Arial" font-size="23">Tail pooling sensitivity: five fixed 10-K Item 1 documents</text>',
             f'<text x="30" y="66" font-family="Arial" font-size="13">Run {run_id} | M1 index SHA-256 {corpus_hash[:12]} | L2 distance from equal pooling | exploratory, not quality</text>']
    for row_index, length in enumerate((128, 192)):
        y0 = 100 + row_index * 270
        lines.append(f'<text x="35" y="{y0}" font-family="Arial" font-size="18">{length} body tokens</text>')
        for i, ticker in enumerate(("AAPL", "MSFT", "AMZN", "GOOGL", "META")):
            y = y0 + 37 + i * 38
            lines.append(f'<text x="40" y="{y+16}" font-family="Arial" font-size="13">{ticker}</text>')
            for j, method in enumerate(("body_token_weighted", "drop_last_chunk_equal")):
                entry = next(r for r in by_length[length] if r["ticker"] == ticker and r["method"] == method)
                width = entry["vector_l2_from_equal"] * scale
                color = "#2563eb" if j == 0 else "#d97706"
                lines.append(f'<rect x="{105 + j * 500}" y="{y + j*14}" width="{width:.2f}" height="12" fill="{color}"/>')
                lines.append(f'<text x="{112 + j * 500 + width:.2f}" y="{y + j*14 + 11}" font-family="Arial" font-size="10">{entry["vector_l2_from_equal"]:.4f}</text>')
    lines += ['<text x="105" y="625" font-family="Arial" font-size="12" fill="#2563eb">Blue: body-token weighted</text>',
              '<text x="605" y="625" font-family="Arial" font-size="12" fill="#d97706">Orange: delete last chunk (diagnostic removal)</text>', '</svg>']
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> None:
    for required in (args.config, args.corpus, args.prior_dense / "receipt.json"):
        if not required.is_file():
            raise FileNotFoundError(f"Required frozen input is missing: {required}")
    config = load_config(args.config)
    if (config.body_tokens, config.alternate_body_tokens, config.overlap_tokens) != (128, 192, 0):
        raise ValueError("Original 128/192 no-overlap chunk setup required")
    if not config.normalize_chunks or not config.normalize_documents:
        raise ValueError("Original chunk/document L2 order required")
    valid, audit = validate_corpus(config, args.corpus)
    write_json(args.output / "corpus_validation.json", audit)
    if len(valid) != 5 or audit["valid_count"] != audit["planned_count"]:
        raise ValueError("Original five-company M1 corpus is not fully valid")
    ids = [r["ticker"] + "|" + r["accession"] for r in valid]
    tickers = [r["ticker"] for r in valid]
    prior_receipt = verify_prior(args.prior_dense, config.source_sha256, file_hash(args.corpus))
    if prior_receipt.get("row_text_hashes") != [r["item1_sha256"] for r in valid]:
        raise ValueError("Prior M6 text hashes or row order differ")
    encoder = Encoder(config)
    if prior_receipt.get("model_identity") != encoder.identity:
        raise ValueError("Pinned model identity differs from prior M6 receipt")
    actual = {}
    gate = []
    for length in (128, 192):
        old_map = [json.loads(line) for line in
                   (args.prior_dense / f"dense_chunk_map_{length}.jsonl").read_text(encoding="utf-8").splitlines()]
        old_stats = json.loads((args.prior_dense / f"dense_chunk_stats_{length}.json").read_text(encoding="utf-8"))["rows"]
        doc_records = []
        flat_vectors = []
        chunk_rows = []
        for index, doc in enumerate(valid):
            chunks = encoder.chunks(doc["item1_text"], length)
            vectors = encoder.embed_chunks(chunks)
            if vectors.shape[0] != len(chunks) or not np.isfinite(vectors).all():
                raise ValueError(f"Invalid re-embedded chunks: {doc['ticker']} {length}")
            old = [r for r in old_map if r["id"] == ids[index]]
            if len(old) != len(chunks) or old_stats[index]["body_tokens"] != sum(len(c.token_ids) for c in chunks):
                raise ValueError(f"Prior chunk count/token total differs: {doc['ticker']} {length}")
            for local_index, (chunk, reference) in enumerate(zip(chunks, old)):
                subset = {"id": ids[index], "ticker": doc["ticker"], "text_sha256": doc["item1_sha256"],
                          "chunk_index": local_index, "token_start": chunk.token_start,
                          "token_end_exclusive": chunk.token_end_exclusive, "char_start": chunk.char_start,
                          "char_end_exclusive": chunk.char_end_exclusive,
                          "source_span_sha256": chunk.source_span_sha256,
                          "body_token_count": len(chunk.token_ids),
                          "model_input_tokens_including_specials": len(chunk.token_ids) + encoder.special_count,
                          "model_input_limit": encoder.identity["max_input_tokens"]}
                if subset != reference:
                    raise ValueError(f"Prior chunk mapping differs: {doc['ticker']} {length} chunk {local_index}")
                chunk_rows.append({**subset, "global_chunk_row": len(flat_vectors) + local_index})
            flat_vectors.extend(vectors)
            counts = [len(c.token_ids) for c in chunks]
            weights = weights_for_chunks(counts, length)
            equal = aggregate(vectors, True, True)
            weighted = aggregate(vectors, True, True, weights=weights["body_token_weighted"])
            doc_records.append({"doc": doc, "id": ids[index], "chunks": chunks,
                                "raw_vectors": vectors, "counts": counts, "weights": weights,
                                "baseline_equal": equal, "baseline_body": weighted})
        np.save(args.output / f"chunk_vectors_{length}.npy", np.asarray(flat_vectors), allow_pickle=False)
        with (args.output / f"chunk_index_{length}.jsonl").open("w", encoding="utf-8") as stream:
            for row in chunk_rows:
                stream.write(json.dumps(row, sort_keys=True) + "\n")
        for method, key in (("equal_chunks", "baseline_equal"),
                            ("body_token_weighted", "baseline_body")):
            new = np.stack([r[key] for r in doc_records])
            saved = np.load(args.prior_dense / f"dense_vectors_{length}_{method}.npy", allow_pickle=False)
            new_cosine = cosine(new)
            saved_cosine = read_matrix(args.prior_dense / f"dense_cosine_{length}_{method}.csv", ids)
            vector_error = float(np.max(np.abs(new - saved)))
            cosine_error = float(np.max(np.abs(new_cosine - saved_cosine)))
            gate.append({"body_length": length, "method": method,
                         "max_abs_vector": vector_error, "max_abs_cosine": cosine_error,
                         "tolerance": config.repeat_tolerance,
                         "pass": vector_error <= config.repeat_tolerance and
                                 cosine_error <= config.repeat_tolerance})
        actual[length] = doc_records
    write_json(args.output / "reconstruction_gate.json", {"checks": gate,
               "all_pass": all(x["pass"] for x in gate),
               "note": "New fixed-weight inference; not an independent training seed"})
    if not all(x["pass"] for x in gate):
        raise ValueError("Prior M6 equal/token-weighted representations were not reproduced")
    # Only now evaluate new tail interventions.
    all_rows = []
    matrix_results = {}
    company_results = []
    for length in (128, 192):
        records = actual[length]
        arms: dict[str, np.ndarray] = {}
        for method in METHODS:
            if method == "drop_last_chunk_equal" and any(len(r["chunks"]) == 1 for r in records):
                # Do not manufacture an empty vector for a singleton document.
                arms[method] = None
                continue
            vectors = []
            for r in records:
                weights = r["weights"][method]
                source = r["raw_vectors"][:-1] if method == "drop_last_chunk_equal" else r["raw_vectors"]
                vectors.append(aggregate(source, True, True,
                                         weights=None if method in ("equal_chunks", "drop_last_chunk_equal") else weights))
            arms[method] = np.stack(vectors)
            np.save(args.output / f"vectors_{length}_{method}.npy", arms[method], allow_pickle=False)
            m = cosine(arms[method])
            save_matrix(args.output / f"cosine_{length}_{method}.csv", ids, m)
        baseline = arms["equal_chunks"]
        base_matrix = cosine(baseline)
        base_peers = peers_and_margin(base_matrix, tickers)
        matrix_results[str(length)] = {}
        for method, matrix_vectors in arms.items():
            if matrix_vectors is None:
                matrix_results[str(length)][method] = {"status": "UNDEFINED",
                    "reason": "At least one document has only one chunk; deleting it creates no vector"}
                continue
            matrix = cosine(matrix_vectors)
            peers = peers_and_margin(matrix, tickers)
            upper = np.triu_indices(len(tickers), 1)
            differences = np.abs((matrix - base_matrix)[upper])
            matrix_results[str(length)][method] = {
                "status": "DONE", "cosine_csv": f"cosine_{length}_{method}.csv",
                "vectors_npy": f"vectors_{length}_{method}.npy",
                "max_abs_offdiag_vs_equal": float(differences.max()),
                "mean_abs_offdiag_vs_equal": float(differences.mean()),
                "upper_triangle_spearman_vs_equal": upper_triangle_spearman(base_matrix, matrix),
                "top2_overlap_by_company": {ticker: len(set(base_peers[ticker]["top2"]) &
                    set(peers[ticker]["top2"])) / 2 for ticker in tickers},
                "top2_and_margin": peers,
            }
        for i, r in enumerate(records):
            counts = r["counts"]
            last = r["chunks"][-1]
            text = r["doc"]["item1_text"]
            dropped = {"token_start": last.token_start, "token_end_exclusive": last.token_end_exclusive,
                       "char_start": last.char_start, "char_end_exclusive": last.char_end_exclusive,
                       "source_span_sha256": last.source_span_sha256,
                       "source_span_rehash_pass": sha_bytes(text[last.char_start:last.char_end_exclusive].encode("utf-8")) == last.source_span_sha256,
                       "omitted_body_token_count": counts[-1],
                       "meaning": "Exact last token span of saved Item 1 text; excluded only in diagnostic ablation"}
            if not dropped["source_span_rehash_pass"]:
                raise ValueError(f"Removed source span hash changed: {r['doc']['ticker']} {length}")
            for method in METHODS:
                weights = r["weights"][method]
                if arms[method] is None:
                    row = {"ticker": r["doc"]["ticker"], "id": r["id"], "body_length": length,
                           "method": method, "status": "UNDEFINED", "reason": "single_chunk_document",
                           "body_total": sum(counts), "chunk_count": len(counts), "tail_body_tokens": counts[-1],
                           "weights_unnormalized": None, "weights_normalized": None,
                           "vector_l2_from_equal": None, "vector_max_abs_from_equal": None,
                           "cosine_to_equal": None, "top2": None, "rank2_minus_rank3": None,
                           "dropped_source": dropped}
                else:
                    vec = arms[method][i]
                    peer = matrix_results[str(length)][method]["top2_and_margin"][r["doc"]["ticker"]]
                    row = {"ticker": r["doc"]["ticker"], "id": r["id"], "body_length": length,
                           "method": method, "status": "DONE", "reason": None,
                           "body_total": sum(counts), "chunk_count": len(counts), "tail_body_tokens": counts[-1],
                           "weights_unnormalized": weights, "weights_normalized": normalized_weights(weights),
                           "vector_l2_from_equal": float(np.linalg.norm(vec - baseline[i])),
                           "vector_max_abs_from_equal": float(np.max(np.abs(vec - baseline[i]))),
                           "cosine_to_equal": float(np.dot(vec, baseline[i])),
                           "top2": peer["top2"], "rank2_minus_rank3": peer["rank2_minus_rank3"],
                           "dropped_source": dropped if method == "drop_last_chunk_equal" else None}
                all_rows.append(row)
                company_results.append({k: row[k] for k in ("ticker", "body_length", "method", "status",
                    "body_total", "chunk_count", "tail_body_tokens", "vector_l2_from_equal",
                    "cosine_to_equal", "top2", "rank2_minus_rank3")})
        # Under complete equal non-tail chunks, normalized body and tail-only
        # weights coincide. Record this as an algebraic check, not a discovery.
        max_delta = float(np.max(np.abs(arms["body_token_weighted"] - arms["tail_only_ratio"])))
        if max_delta > 1e-12:
            raise ValueError(f"Tail-only ratio and body weighting are not algebraically equal at {length}")
        matrix_results[str(length)]["algebraic_equivalence"] = {
            "condition": "every non-tail chunk has exactly body_length tokens; same vectors and normalize order",
            "body_weighted_vs_tail_ratio_max_abs_vector": max_delta,
            "not_an_independent_experiment": True}
    write_json(args.output / "tail_weight_diagnostics.json", {
        "schema_version": 1, "kind": "EXPLORATORY_M6_TAIL_WEIGHT_MECHANISM",
        "company_order": tickers, "input_index_sha256": file_hash(args.corpus),
        "config_sha256": config.source_sha256, "prior_m6_dense_receipt_sha256": file_hash(args.prior_dense / "receipt.json"),
        "model_identity": encoder.identity, "conditions": {
            "equal_chunks": "all chunk weights 1",
            "body_token_weighted": "each chunk weight is actual body token count; specials and padding excluded",
            "tail_only_ratio": "full chunks weight 1; final chunk weight tail_body_tokens/body_length",
            "drop_last_chunk_equal": "remove exact last body-token span; then equal-pool remaining chunks; diagnostic only"},
        "company_conditions": all_rows, "matrix_results": matrix_results,
        "interpretation_limit": "Exploratory fixed-text sensitivity, not causality, semantic improvement, financial value, or independent audit"})
    csv_fields = ["ticker", "id", "body_length", "method", "status", "body_total", "chunk_count",
                  "tail_body_tokens", "weights_unnormalized", "weights_normalized", "vector_l2_from_equal",
                  "vector_max_abs_from_equal", "cosine_to_equal", "top2", "rank2_minus_rank3",
                  "dropped_char_start", "dropped_char_end_exclusive", "dropped_source_span_sha256", "reason"]
    with (args.output / "tail_weight_diagnostics.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=csv_fields, extrasaction="ignore")
        writer.writeheader()
        for r in all_rows:
            writer.writerow({**r, "weights_unnormalized": json.dumps(r["weights_unnormalized"]),
                             "weights_normalized": json.dumps(r["weights_normalized"]),
                             "top2": json.dumps(r["top2"]),
                             "dropped_char_start": r["dropped_source"]["char_start"] if r["dropped_source"] else None,
                             "dropped_char_end_exclusive": r["dropped_source"]["char_end_exclusive"] if r["dropped_source"] else None,
                             "dropped_source_span_sha256": r["dropped_source"]["source_span_sha256"] if r["dropped_source"] else None})
    svg_plot(args.output / "tail_weight_effects.svg", all_rows, args.output.parent.name, file_hash(args.corpus))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--prior-dense", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=False)
    receipt = {"schema_version": 1, "status": "RUNNING", "kind": "EXPLORATORY_TAIL_WEIGHT_DIAGNOSTIC",
               "started_utc": datetime.now(timezone.utc).isoformat(),
               "arguments": {k: str(v.resolve()) for k, v in vars(args).items()},
               "source_script_sha256": file_hash(Path(__file__)), "financial_experiment": "NOT_RUN",
               "review": "same-agent self-check; no human boundary review performed by this task"}
    write_json(args.output / "receipt.json", receipt)
    try:
        run(args)
        receipt["status"] = "DONE"
        code = 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, IndexError) as exc:
        receipt["status"] = "BLOCKED" if isinstance(exc, (FileNotFoundError, ModelError)) else "FAILED"
        receipt["reason"] = f"{type(exc).__name__}: {exc}"
        code = 2 if receipt["status"] == "BLOCKED" else 1
    receipt["finished_utc"] = datetime.now(timezone.utc).isoformat()
    receipt["outputs"] = {p.name: {"sha256": file_hash(p), "bytes": p.stat().st_size}
                          for p in sorted(args.output.iterdir()) if p.is_file() and p.name != "receipt.json"}
    write_json(args.output / "receipt.json", receipt)
    print(json.dumps({"status": receipt["status"], "reason": receipt.get("reason"), "output": str(args.output)}))
    return code


if __name__ == "__main__":
    sys.exit(main())
