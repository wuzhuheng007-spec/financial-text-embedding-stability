"""Exploratory BM25 length-correction diagnostics on frozen real M1 Item 1.

Real-company outputs and synthetic software controls are strictly separate.
This independently recomputes the M6 lexical formula from saved Item 1 bytes;
it does not select a winning b, fetch data, or run a financial experiment.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import sys
from xml.sax.saxutils import escape

import numpy as np


TOKEN_PATTERN = r"[a-z0-9]+"
B_VALUES = (0.0, 0.75, 1.0)
K1 = 1.2


def digest(path: Path) -> str:
    h = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def tokenize(text: str) -> list[str]:
    if not isinstance(text, str):
        raise ValueError("Text must be a string")
    return re.findall(TOKEN_PATTERN, text.lower())


def fitted_terms(token_lists: list[list[str]]) -> tuple[list[Counter], list[str], np.ndarray, float]:
    require(bool(token_lists) and all(token_lists), "Empty corpus or token-empty document")
    counts = [Counter(row) for row in token_lists]
    vocabulary = sorted(set().union(*(set(c) for c in counts)))
    n = len(counts)
    df = Counter(t for c in counts for t in c)
    idf = np.asarray([math.log1p((n - df[t] + .5) / (df[t] + .5))
                      for t in vocabulary], dtype=np.float64)
    avg = math.fsum(sum(c.values()) for c in counts) / n
    require(math.isfinite(avg) and avg > 0, "Invalid average document length")
    return counts, vocabulary, idf, avg


def term_weights(counts: list[Counter], vocabulary: list[str], idf: np.ndarray,
                 average_length: float, *, k1: float, b: float) -> np.ndarray:
    """Separate implementation of the M6 exact-length positive-IDF formula."""
    require(math.isfinite(k1) and k1 > 0 and math.isfinite(b) and 0 <= b <= 1,
            "Invalid BM25 k1 or b")
    require(len(vocabulary) == len(idf) and average_length > 0,
            "Malformed lexical fit")
    term_index = {term: j for j, term in enumerate(vocabulary)}
    output = np.zeros((len(counts), len(vocabulary)), dtype=np.float64)
    for row, counter in enumerate(counts):
        length = sum(counter.values())
        require(length > 0, "Token-empty document")
        for term, frequency in counter.items():
            j = term_index[term]
            denominator = frequency + k1 * (1 - b + b * length / average_length)
            output[row, j] = float(idf[j]) * frequency * (k1 + 1) / denominator
    require(np.isfinite(output).all() and (output >= 0).all(), "Nonfinite BM25 weights")
    return output


def cosine_from_weights(weights: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    norms = np.linalg.norm(weights, axis=1)
    require(np.isfinite(norms).all() and (norms > 0).all(), "Zero/nonfinite lexical vector")
    unit = weights / norms[:, None]
    return norms, unit, unit @ unit.T


def directed_scores(weights: np.ndarray, token_lists: list[list[str]],
                    vocabulary: list[str]) -> np.ndarray:
    term_index = {term: i for i, term in enumerate(vocabulary)}
    result = np.zeros((len(token_lists), len(token_lists)), dtype=np.float64)
    for query, tokens in enumerate(token_lists):
        terms = sorted(set(tokens))
        for document in range(len(token_lists)):
            result[query, document] = math.fsum(float(weights[document, term_index[t]])
                                                 for t in terms)
    return result


def fitted_scalar(reference: np.ndarray, target: np.ndarray) -> dict:
    """Fit alpha=dot(ref,target)/dot(ref,ref) on ref's nonzero support."""
    support = reference != 0
    require(bool(np.any(support)) and np.array_equal(support, target != 0),
            "BM25 term supports differ")
    x, y = reference[support], target[support]
    alpha = float((x @ y) / (x @ x))
    residual = float(np.linalg.norm(y - alpha * x))
    denominator = float(np.linalg.norm(y))
    return {"alpha": alpha, "residual_l2": residual,
            "relative_residual_to_target_l2": residual / denominator,
            "same_nonzero_support": True, "support_terms": int(support.sum()),
            "formula": "alpha=(w_b0 dot w_b)/(w_b0 dot w_b0); residual=||w_b-alpha*w_b0||_2/||w_b||_2 on identical nonzero term support"}


def ordered_peers(matrix: np.ndarray, tickers: list[str]) -> dict[str, list[dict]]:
    require(matrix.shape == (len(tickers), len(tickers)) and np.isfinite(matrix).all(),
            "Invalid ranking matrix")
    result = {}
    for i, ticker in enumerate(tickers):
        peers = sorted((j for j in range(len(tickers)) if j != i),
                       key=lambda j: (-float(matrix[i, j]), tickers[j]))
        result[ticker] = [{"ticker": tickers[j], "score": float(matrix[i, j]),
                           "rank": rank} for rank, j in enumerate(peers, 1)]
    return result


def average_ranks(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="stable")
    ranks = np.empty(len(values), dtype=float)
    i = 0
    while i < len(values):
        j = i + 1
        while j < len(values) and values[order[j]] == values[order[i]]:
            j += 1
        ranks[order[i:j]] = (i + j - 1) / 2 + 1
        i = j
    return ranks


def upper_spearman(reference: np.ndarray, target: np.ndarray) -> dict:
    require(reference.shape == target.shape and reference.ndim == 2 and
            reference.shape[0] == reference.shape[1], "Matrix shape mismatch")
    indices = np.triu_indices(len(reference), 1)
    x, y = reference[indices], target[indices]
    result = {"value": None, "reason": None, "pair_count": int(len(x)),
              "tie_rule": "exact saved float values receive average ranks"}
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        result["reason"] = "nonfinite_values"
    elif len(x) < 2:
        result["reason"] = "fewer_than_two_pairs"
    else:
        xr, yr = average_ranks(x), average_ranks(y)
        dx, dy = xr - xr.mean(), yr - yr.mean()
        den = math.sqrt(float(dx @ dx) * float(dy @ dy))
        if den == 0:
            result["reason"] = "constant_ranks"
        else:
            result["value"] = float((dx @ dy) / den)
    return result


def synthetic_controls() -> dict:
    """Software-only controls: no company filing contributes to these numbers."""
    vector = np.asarray([1., 2., 3.])
    unit = vector / np.linalg.norm(vector)
    scaled = 7.3 * vector
    scalar_error = float(np.max(np.abs(unit - scaled / np.linalg.norm(scaled))))

    unique_tokens = [["a", "b", "c"], ["a"]]
    unique_counts, vocab, idf, avg = fitted_terms(unique_tokens)
    unique0 = term_weights(unique_counts, vocab, idf, avg, k1=K1, b=0.)[0]
    unique1 = term_weights(unique_counts, vocab, idf, avg, k1=K1, b=1.)[0]
    unique_fit = fitted_scalar(unique0, unique1)

    mixed_tokens = [["x", "x", "y", "z"], ["x", "z"], ["y", "u", "u"]]
    mixed_counts, vocab, idf, avg = fitted_terms(mixed_tokens)
    mixed0 = term_weights(mixed_counts, vocab, idf, avg, k1=K1, b=0.)[0]
    mixed1 = term_weights(mixed_counts, vocab, idf, avg, k1=K1, b=1.)[0]
    mixed_fit = fitted_scalar(mixed0, mixed1)
    require(scalar_error <= 1e-15 and unique_fit["relative_residual_to_target_l2"] < 1e-12
            and mixed_fit["relative_residual_to_target_l2"] > 1e-6,
            "Synthetic software positive controls failed")
    return {"data_class": "SYNTHETIC_SOFTWARE_TEST_ONLY", "real_company_rows": 0,
            "positive_scalar_normalized_max_abs_error": scalar_error,
            "all_frequency_one_document": unique_fit,
            "mixed_frequency_document": mixed_fit,
            "interpretation": "All-frequency-one terms receive a document-level scalar when b changes; unequal term frequencies can change direction. Neither synthetic corpus is a company result."}


def read_saved_matrix(path: Path, ids: list[str]) -> np.ndarray:
    with path.open(newline="", encoding="utf-8") as stream:
        data = list(csv.reader(stream))
    require(data[0] == ["document_id", *ids], f"Saved matrix column order differs: {path}")
    require([row[0] for row in data[1:]] == ids and len(data) == len(ids) + 1,
            f"Saved matrix row order differs: {path}")
    matrix = np.asarray([[float(x) for x in row[1:]] for row in data[1:]], dtype=np.float64)
    require(matrix.shape == (len(ids), len(ids)) and np.isfinite(matrix).all(),
            f"Invalid saved matrix: {path}")
    return matrix


def save_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def save_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False,
                               ensure_ascii=False) + "\n", encoding="utf-8")


def plot_residuals(path: Path, rows: list[dict], run_id: str, corpus_hash: str) -> None:
    companies = list(dict.fromkeys(row["ticker"] for row in rows))
    active = [r for r in rows if r["b"] != 0]
    maximum = max((r["relative_residual"] for r in active), default=0)
    scale = 480 / maximum if maximum > 0 else 0
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="990" height="440" viewBox="0 0 990 440">',
           '<rect width="100%" height="100%" fill="#f8fafc"/>',
           '<text x="25" y="35" font-family="Arial" font-size="22">BM25 term-weight departure from document scaling</text>',
           f'<text x="25" y="58" font-family="Arial" font-size="12">Run {escape(run_id)} | M1 {corpus_hash[:12]} | n=5 | exploratory; relative residual after scalar fit</text>']
    for i, ticker in enumerate(companies):
        y = 105 + 60 * i
        svg.append(f'<text x="30" y="{y+13}" font-family="Arial" font-size="15">{ticker}</text>')
        for offset, b, color in ((0, 0.75, "#2563eb"), (21, 1.0, "#dc2626")):
            value = next(r["relative_residual"] for r in active if r["ticker"] == ticker and r["b"] == b)
            width = value * scale
            svg.append(f'<rect x="115" y="{y+offset-5}" width="{width:.2f}" height="15" fill="{color}"/>')
            svg.append(f'<text x="{123+width:.2f}" y="{y+offset+8}" font-family="Arial" font-size="12">b={b:g}: {value:.6g}</text>')
    svg.append('<text x="25" y="420" font-family="Arial" font-size="12">Same-support fit: alpha=(w0 dot wb)/(w0 dot w0); residual ||wb-alpha*w0||/||wb||. This is not a quality metric.</text>')
    svg.append('</svg>')
    path.write_text("\n".join(svg) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> dict:
    from finembed.analysis import validate_corpus
    from finembed.config import load_config

    config = load_config(args.config)
    valid, source_audit = validate_corpus(config, args.corpus_index)
    require(source_audit["planned_count"] == source_audit["valid_count"] == 5 and
            [x["status"] for x in source_audit["entries"]] == ["VALID"] * 5,
            "Original M1 corpus is not five valid files")
    require(args.m6_lexical_run.is_dir(), "Original M6 lexical output missing")
    receipt_path = args.m6_lexical_run / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    require(receipt.get("status") == "DONE" and receipt.get("dense_status") == "NOT_REQUESTED",
            "Original M6 lexical run not DONE")
    require(receipt.get("config_sha256") == config.source_sha256 and
            Path(receipt["arguments"]["corpus"]).resolve() == args.corpus_index.resolve(),
            "Original M6 config or corpus differs")
    require(float(receipt["arguments"]["k1"]) == K1 and
            [float(x) for x in receipt["arguments"]["b_values"]] == list(B_VALUES),
            "Original M6 BM25 settings differ")
    for name, listed in receipt["outputs"].items():
        path = args.m6_lexical_run / name
        require(path.is_file() and path.stat().st_size == listed["bytes"] and
                digest(path) == listed["sha256"], f"Original M6 output hash mismatch: {path}")
    require(set(receipt["outputs"]) == {p.name for p in args.m6_lexical_run.iterdir()
                                       if p.is_file() and p.name != "receipt.json"},
            "Original M6 receipt does not cover every output")

    ids = [f"{r['ticker']}|{r['accession']}" for r in valid]
    tickers = [r["ticker"] for r in valid]
    require(receipt.get("row_text_hashes") == [r["item1_sha256"] for r in valid],
            "Original M6 source text hashes differ")
    saved_rows = json.loads((args.m6_lexical_run / "rows.json").read_text(encoding="utf-8"))["rows"]
    require([r["id"] for r in saved_rows] == ids and
            [r["cik"] for r in saved_rows] == [r["cik"] for r in valid],
            "Original M6 row identity/order mismatch")

    token_lists = [tokenize(r["item1_text"]) for r in valid]
    counts, vocabulary, idf, average_length = fitted_terms(token_lists)
    fit = json.loads((args.m6_lexical_run / "lexical_fit_stats.json").read_text(encoding="utf-8"))
    require(fit["source_index_sha256"] == digest(args.corpus_index) and fit["N"] == 5 and
            fit["k1"] == K1 and fit["b_values"] == list(B_VALUES) and
            fit["tokenizer"] == "ascii_alphanumeric_lower_v1" and
            fit["average_document_length_tokens"] == average_length and
            len(fit["term_statistics"]) == len(vocabulary),
            "Original M6 lexical fit boundary/statistics differ")
    require([r["length_tokens"] for r in fit["documents"]] == [len(t) for t in token_lists],
            "Original M6 lexical lengths differ")
    for row, tokens in zip(fit["documents"], token_lists):
        encoded = json.dumps(tokens, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        require(row["token_sequence_sha256"] == sha256(encoded).hexdigest(),
                f"Original M6 token sequence hash differs: {row['ticker']}")
    df = Counter(t for c in counts for t in c)
    for term, computed in zip(vocabulary, idf):
        prior = fit["term_statistics"].get(term)
        require(prior is not None and prior["df"] == df[term] and
                abs(prior["idf"] - float(computed)) <= 1e-15,
                f"Original M6 IDF/df differs: {term}")

    weights, normalized, cosines, directed = {}, {}, {}, {}
    baselines = {}
    for b in B_VALUES:
        w = term_weights(counts, vocabulary, idf, average_length, k1=K1, b=b)
        norms, unit, cosine = cosine_from_weights(w)
        scores = directed_scores(w, token_lists, vocabulary)
        suffix = f"b{b:g}"
        saved_cosine = read_saved_matrix(args.m6_lexical_run /
                                          f"bm25_termweight_cosine_{suffix}.csv", ids)
        saved_scores = read_saved_matrix(args.m6_lexical_run /
                                          f"bm25_directed_scores_{suffix}.csv", ids)
        require(np.max(np.abs(saved_cosine - cosine)) <= 1e-12 and
                np.max(np.abs(saved_scores - scores)) <= 1e-9,
                f"Independent BM25 recomputation differs from M6: {suffix}")
        weights[b], normalized[b], cosines[b], directed[b] = w, unit, cosine, scores
        baselines[b] = {"raw_norms": norms.tolist(),
                        "cosine_recomputed_max_abs_vs_M6": float(np.max(np.abs(saved_cosine - cosine))),
                        "directed_recomputed_max_abs_vs_M6": float(np.max(np.abs(saved_scores - scores)))}

    output_rows = []
    company_stats = []
    for i, (r, counter) in enumerate(zip(valid, counts)):
        lexical_length = sum(counter.values())
        histogram = dict(sorted(Counter(counter.values()).items()))
        hapax_count = histogram.get(1, 0)
        company_stats.append({"ticker": r["ticker"], "cik": r["cik"],
                              "accession": r["accession"], "item1_sha256": r["item1_sha256"],
                              "lexical_token_count": lexical_length,
                              "unique_term_count": len(counter),
                              "frequency_one_term_count": hapax_count,
                              "frequency_one_fraction_of_distinct_terms": hapax_count / len(counter),
                              "frequency_histogram_term_count_by_within_document_frequency":
                                  {str(k): v for k, v in histogram.items()}})
        reference = weights[0.0][i]
        for b in B_VALUES:
            target = weights[b][i]
            fitted = fitted_scalar(reference, target)
            difference = target - reference
            dot = float(np.clip(normalized[0.0][i] @ normalized[b][i], -1, 1))
            output_rows.append({"ticker": r["ticker"], "accession": r["accession"],
                                "item1_sha256": r["item1_sha256"], "b": b,
                                "lexical_tokens": lexical_length,
                                "unique_terms": len(counter),
                                "frequency_one_fraction": hapax_count / len(counter),
                                "pre_l2_norm": float(np.linalg.norm(target)),
                                "raw_weight_l2_change_vs_b0": float(np.linalg.norm(difference)),
                                "raw_weight_max_abs_change_vs_b0": float(np.max(np.abs(difference))),
                                "raw_weight_mean_abs_change_on_support_vs_b0":
                                    float(np.mean(np.abs(difference[reference != 0]))),
                                "direction_cosine_vs_b0": dot,
                                "direction_l2_change_vs_b0": float(np.linalg.norm(
                                    normalized[b][i] - normalized[0.0][i])),
                                "fitted_scale_alpha": fitted["alpha"],
                                "relative_residual": fitted["relative_residual_to_target_l2"],
                                "same_nonzero_support": fitted["same_nonzero_support"]})

    comparisons = []
    ranking_rows = []
    for b in B_VALUES:
        c = cosines[b]
        s = directed[b]
        cosine_peers = ordered_peers(c, tickers)
        directed_peers = ordered_peers(s, tickers)
        base_cosine = ordered_peers(cosines[0.0], tickers)
        base_directed = ordered_peers(directed[0.0], tickers)
        off = np.triu_indices(5, 1)
        directed_off = np.ones((5, 5), dtype=bool)
        np.fill_diagonal(directed_off, False)
        c_delta = np.abs(c[off] - cosines[0.0][off])
        s_delta = np.abs(s[directed_off] - directed[0.0][directed_off])
        by_company = {}
        for ticker in tickers:
            c0, cb = base_cosine[ticker], cosine_peers[ticker]
            s0, sb = base_directed[ticker], directed_peers[ticker]
            by_company[ticker] = {
                "cosine_top2": [p["ticker"] for p in cb[:2]],
                "cosine_top2_overlap_vs_b0": len({p["ticker"] for p in c0[:2]} &
                                                {p["ticker"] for p in cb[:2]}) / 2,
                "cosine_rank_order_changed_vs_b0": [p["ticker"] for p in c0] !=
                                                     [p["ticker"] for p in cb],
                "directed_top2": [p["ticker"] for p in sb[:2]],
                "directed_top2_overlap_vs_b0": len({p["ticker"] for p in s0[:2]} &
                                                  {p["ticker"] for p in sb[:2]}) / 2,
                "directed_rank_order_changed_vs_b0": [p["ticker"] for p in s0] !=
                                                       [p["ticker"] for p in sb],
                "cosine_rank2_rank3_gap": cb[1]["score"] - cb[2]["score"],
                "directed_rank2_rank3_gap": sb[1]["score"] - sb[2]["score"]}
            for ranking_type, peers in (("termweight_cosine", cb), ("directed_query_to_document", sb)):
                for peer in peers:
                    ranking_rows.append({"b": b, "ranking_type": ranking_type,
                                         "query_ticker": ticker, "target_ticker": peer["ticker"],
                                         "rank": peer["rank"], "score": peer["score"]})
        comparisons.append({"b": b, "termweight_cosine": {
            "upper_triangle_spearman_vs_b0": upper_spearman(cosines[0.0], c),
            "max_abs_offdiagonal_change_vs_b0": float(np.max(c_delta)),
            "mean_abs_offdiagonal_change_vs_b0": float(np.mean(c_delta)),
            "changed_upper_pairs": int(np.count_nonzero(c_delta))},
            "directed_query_document": {"max_abs_offdiagonal_score_change_vs_b0":
                                          float(np.max(s_delta)),
                                          "mean_abs_offdiagonal_score_change_vs_b0":
                                          float(np.mean(s_delta)),
                                          "asymmetry_max_abs": float(np.max(np.abs(s - s.T))),
                                          "ranking_is_directed": True},
            "per_company": by_company})

    source_inputs = [{"ticker": r["ticker"], "cik": r["cik"],
                      "accession": r["accession"], "item1_sha256": r["item1_sha256"],
                      "raw_sha256": r["files"]["original_submission.txt"]["sha256"]}
                     for r in valid]
    diagnostics = {"kind": "M7_EXPLORATORY_REAL_FIVE_COMPANY_BM25_LENGTH_NORMALIZATION",
                   "scientific_protocol": "M5_NOT_FROZEN", "financial_experiment": "NOT_RUN",
                   "not_model_selection": True, "source_index_sha256": digest(args.corpus_index),
                   "config_sha256": config.source_sha256,
                   "M6_lexical_receipt_sha256": digest(receipt_path),
                   "M6_lexical_fit_stats_sha256": digest(args.m6_lexical_run / "lexical_fit_stats.json"),
                   "company_order": tickers, "source_inputs": source_inputs,
                   "fit_boundary": "exact five M1 Item 1 documents, no other text",
                   "tokenizer": "ascii_alphanumeric_lower_v1", "token_pattern": TOKEN_PATTERN,
                   "N": 5, "vocabulary_size": len(vocabulary), "average_document_length_tokens": average_length,
                   "k1": K1, "b_values": list(B_VALUES),
                   "idf_formula": "log1p((N-df+0.5)/(df+0.5))",
                   "term_weight_formula": "idf*f*(k1+1)/(f+k1*(1-b+b*L/avgL))",
                   "query_rule": "all unique source-document lexical terms, self excluded for peer ranks",
                   "same_support_scale_formula": "alpha=(w_b0 dot w_b)/(w_b0 dot w_b0); relative residual=||w_b-alpha*w_b0||_2/||w_b||_2",
                   "company_statistics": company_stats, "normalization_rows": output_rows,
                   "comparisons": comparisons, "M6_matrix_recomputation_checks": baselines,
                   "tie_rule": "exact equal scores sort by ticker ascending",
                   "inference_limit": "Five related company texts, no relevance labels, no independent company-pair significance, no financial or causal claim"}
    args.output.mkdir(parents=True, exist_ok=False)
    save_json(args.output / "bm25_normalization_diagnostics.json", diagnostics)
    save_csv(args.output / "bm25_normalization_diagnostics.csv", list(output_rows[0]), output_rows)
    save_csv(args.output / "bm25_peer_rankings.csv", list(ranking_rows[0]), ranking_rows)
    save_json(args.output / "synthetic_software_controls.json", synthetic_controls())
    save_json(args.output / "input_audit.json", source_audit)
    save_json(args.output / "vocabulary.json", {"terms": vocabulary, "idf": idf.tolist(),
                                                "fit_corpus_sha256": digest(args.corpus_index)})
    np.savez(args.output / "bm25_termweights_and_matrices.npz", **{
        f"raw_weights_b{b:g}": weights[b] for b in B_VALUES}, **{
        f"unit_weights_b{b:g}": normalized[b] for b in B_VALUES}, **{
        f"cosine_b{b:g}": cosines[b] for b in B_VALUES}, **{
        f"directed_b{b:g}": directed[b] for b in B_VALUES})
    plot_residuals(args.output / "bm25_scaling_residuals.svg", output_rows,
                   args.output.parent.name, digest(args.corpus_index))
    receipt_out = {"status": "DONE", "kind": diagnostics["kind"],
                   "recorded_utc": datetime.now(timezone.utc).isoformat(),
                   "command_arguments": {k: str(v) for k, v in vars(args).items()},
                   "source_script_sha256": digest(Path(__file__)),
                   "input_files": {"corpus_index": digest(args.corpus_index),
                                   "config": digest(args.config),
                                   "M6_receipt": digest(receipt_path)},
                   "output_files": {p.name: {"sha256": digest(p), "bytes": p.stat().st_size}
                                    for p in sorted(args.output.iterdir()) if p.is_file()},
                   "audit_relationship": "same-agent self-check, independently coded arithmetic",
                   "synthetic_controls_are_company_results": False}
    save_json(args.output / "receipt.json", receipt_out)
    return {"status": "DONE", "output": str(args.output),
            "max_original_matrix_difference": max(
                max(row["cosine_recomputed_max_abs_vs_M6"],
                    row["directed_recomputed_max_abs_vs_M6"])
                for row in baselines.values())}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--corpus-index", type=Path, required=True)
    parser.add_argument("--m6-lexical-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    for name in ("config", "corpus_index", "m6_lexical_run"):
        setattr(args, name, getattr(args, name).resolve())
    args.output = args.output.resolve()
    if args.output.exists():
        print(json.dumps({"status": "BLOCKED", "reason": "Output directory already exists; prior evidence is immutable",
                          "output": str(args.output)}))
        return 2
    try:
        result = run(args)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        if not args.output.exists():
            args.output.mkdir(parents=True)
        save_json(args.output / "failure.json", {"status": "FAILED", "reason":
                   f"{type(exc).__name__}: {exc}", "financial_experiment": "NOT_RUN"})
        print(json.dumps({"status": "FAILED", "reason": str(exc), "output": str(args.output)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
