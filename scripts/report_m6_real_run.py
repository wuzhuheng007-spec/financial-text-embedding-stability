"""Audit saved five-company M6 outputs and create descriptive, shareable figures.

This program reads already-produced real runs. It does not run inference, fetch data,
or export SEC text. Every input is checked before any comparison is described.
The arithmetic here is separately coded but remains a same-agent self-check.
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
import yaml


TOKEN_RE = re.compile(r"[a-z0-9]+")
METHODS = ("equal_chunks", "body_token_weighted")


def digest(path: Path) -> str:
    h = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def need(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True,
                               allow_nan=False) + "\n", encoding="utf-8")


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def average_ranks(values: np.ndarray) -> np.ndarray:
    """Average exact ties, independent of finembed.analysis implementation."""
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=float)
    start = 0
    while start < len(values):
        stop = start + 1
        while stop < len(values) and values[order[stop]] == values[order[start]]:
            stop += 1
        ranks[order[start:stop]] = (start + stop - 1) / 2 + 1
        start = stop
    return ranks


def upper_spearman(a: np.ndarray, b: np.ndarray) -> dict:
    need(a.shape == b.shape and a.ndim == 2 and a.shape[0] == a.shape[1],
         "Spearman matrix shape mismatch")
    x, y = a[np.triu_indices(len(a), 1)], b[np.triu_indices(len(b), 1)]
    result = {"value": None, "reason": None, "pair_count": int(len(x)),
              "tie_rule": "average ranks for exact equal saved values"}
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        result["reason"] = "non_finite_upper_triangle"
    elif len(x) < 2:
        result["reason"] = "fewer_than_two_company_pairs"
    else:
        xr, yr = average_ranks(x), average_ranks(y)
        xc, yc = xr - xr.mean(), yr - yr.mean()
        denominator = math.sqrt(float(xc @ xc) * float(yc @ yc))
        if denominator == 0:
            result["reason"] = "constant_ranks_in_at_least_one_matrix"
        else:
            result["value"] = float((xc @ yc) / denominator)
    return result


def difference(a: np.ndarray, b: np.ndarray) -> dict:
    delta = a - b
    off = np.abs(delta[np.triu_indices(len(a), 1)])
    return {"mean_absolute_off_diagonal": float(off.mean()),
            "max_absolute_off_diagonal": float(off.max()),
            "frobenius_full_matrix": float(np.linalg.norm(delta)),
            "different_off_diagonal_pairs": int(np.count_nonzero(off))}


def top2(matrix: np.ndarray, ids: list[str], tickers: list[str]) -> dict[str, list[dict]]:
    out = {}
    for i, document_id in enumerate(ids):
        peers = sorted((j for j in range(len(ids)) if j != i),
                       key=lambda j: (-float(matrix[i, j]), tickers[j]))[:2]
        out[document_id] = [{"ticker": tickers[j], "id": ids[j],
                             "score": float(matrix[i, j])} for j in peers]
    return out


def read_matrix(path: Path, ids: list[str], symmetric: bool) -> np.ndarray:
    with path.open("r", encoding="utf-8", newline="") as stream:
        data = list(csv.reader(stream))
    need(len(data) == len(ids) + 1, f"Wrong row count: {path}")
    need(data[0] == ["document_id", *ids], f"Wrong column order: {path}")
    need([row[0] for row in data[1:]] == ids, f"Wrong row order: {path}")
    need(all(len(row) == len(ids) + 1 for row in data[1:]), f"Wrong width: {path}")
    matrix = np.array([[float(x) for x in row[1:]] for row in data[1:]], dtype=float)
    need(np.isfinite(matrix).all(), f"Non-finite values: {path}")
    if symmetric:
        need(np.allclose(matrix, matrix.T, rtol=0, atol=1e-10),
             f"Symmetric cosine mismatch: {path}")
        need(np.allclose(np.diag(matrix), 1, rtol=0, atol=1e-8),
             f"Cosine diagonal mismatch: {path}")
        need(np.max(np.abs(matrix)) <= 1 + 1e-8, f"Invalid cosine range: {path}")
    return matrix


def verify_receipt(run: Path, config_hash: str, corpus: Path, corpus_hash: str,
                   expected_hashes: list[str], dense: bool) -> dict:
    receipt = read_json(run / "receipt.json")
    need(receipt.get("status") == "DONE", f"Run not DONE: {run}")
    need(receipt.get("dense_status") == ("DONE" if dense else "NOT_REQUESTED"),
         f"Dense status wrong: {run}")
    need(receipt.get("config_sha256") == config_hash, f"Config hash mismatch: {run}")
    args = receipt.get("arguments", {})
    need(args.get("corpus") and Path(args["corpus"]).resolve() == corpus.resolve(),
         f"Not the requested original M1 corpus: {run}")
    need(not args.get("section_index"), f"Unexpected section index: {run}")
    need(receipt.get("row_text_hashes") == expected_hashes,
         f"Document text hashes mismatch: {run}")
    need(float(args.get("k1")) == 1.2 and
         sorted(float(x) for x in args.get("b_values", [])) == [0, 0.75, 1] and
         int(args.get("top_k")) == 2, f"Engineering settings mismatch: {run}")
    need(bool(args.get("dense")) == dense, f"Dense argument mismatch: {run}")
    outputs = receipt.get("outputs", {})
    need(isinstance(outputs, dict) and outputs, f"Missing receipt output listing: {run}")
    actual = {p.name for p in run.iterdir() if p.is_file() and p.name != "receipt.json"}
    need(actual == set(outputs), f"Receipt does not enumerate exactly the saved outputs: {run}")
    for filename, evidence in outputs.items():
        path = run / filename
        need(path.is_file() and path.stat().st_size == evidence["bytes"] and
             digest(path) == evidence["sha256"], f"Saved output hash mismatch: {path}")
    input_audit = read_json(run / "input_audit.json")
    need(input_audit.get("valid_count") == 5 and input_audit.get("planned_count") == 5 and
         all(item.get("status") == "VALID" for item in input_audit.get("entries", [])),
         f"M1 validation did not accept all five sources: {run}")
    need(input_audit.get("source") in ("legacy_m1", "legacy-corpus", "M1_CORPUS_INDEX") or
         input_audit.get("index_sha256") == corpus_hash or
         input_audit.get("corpus_index_sha256") == corpus_hash,
         f"Input audit does not identify original corpus: {run}")
    return receipt


def verify_rows(run: Path, corpus_rows: list[dict]) -> None:
    rows = read_json(run / "rows.json")["rows"]
    need(len(rows) == len(corpus_rows), f"Run row count differs: {run}")
    for saved, source in zip(rows, corpus_rows):
        for key in ("ticker", "cik", "accession", "raw_sha256"):
            need(saved.get(key) == source[key], f"{key} mismatch for {source['ticker']} in {run}")
        need(saved.get("id") == source["id"] and
             saved.get("text_sha256") == source["item1_sha256"] and
             saved.get("form") == "10-K" and saved.get("section") == "ITEM_1_BUSINESS" and
             saved.get("lexical_token_count") == source["lexical_tokens"],
             f"Row identity/section/text/token count mismatch: {run}, {source['ticker']}")


def verify_corpus(path: Path) -> tuple[list[dict], str]:
    index = read_json(path)
    rows = index.get("entries", [])
    need(len(rows) == 5 and index.get("included_count") == 5 and
         index.get("failed_count") == 0, "M1 index is not the complete five-company corpus")
    out = []
    for row in rows:
        need(row.get("status") == "INCLUDED", f"M1 row not included: {row.get('ticker')}")
        item = Path(row["item1_path"])
        raw = Path(row["filing_dir"]) / "original_submission.txt"
        need(item.is_file() and raw.is_file(), f"M1 source missing: {row['ticker']}")
        need(digest(item) == row["item1_sha256"] and digest(raw) == row["raw_sha256"],
             f"M1 source SHA-256 mismatch: {row['ticker']}")
        data = item.read_text(encoding="utf-8")
        out.append({"id": row["ticker"] + "|" + row["accession"],
                    "ticker": row["ticker"], "cik": row["cik"],
                    "accession": row["accession"], "item1_sha256": row["item1_sha256"],
                    "raw_sha256": row["raw_sha256"], "characters": len(data),
                    "item1_path": str(item),
                    "lexical_tokens": len(TOKEN_RE.findall(data.lower()))})
    need(len({r["ticker"] for r in out}) == 5 and len({r["cik"] for r in out}) == 5,
         "Duplicate company in corpus")
    return out, digest(path)


def verify_tfidf(directory: Path, corpus_hash: str, rows: list[dict]) -> tuple[np.ndarray, dict]:
    parent = directory.parent
    manifest = read_json(parent / "manifest.json")
    need(manifest.get("status") == "DONE", "Accepted M4 manifest not DONE")
    listed = {Path(item["path"]).name: item for item in manifest.get("outputs", [])
              if Path(item["path"]).parent.resolve() == directory.resolve()}
    required = {"fit_boundary.json", "cosine.npy", "vectors.npy", "vocabulary.json"}
    need(required <= listed.keys(), "M4 manifest omits required TF-IDF output")
    for filename in required:
        p = directory / filename
        need(p.stat().st_size == listed[filename]["bytes"] and
             digest(p) == listed[filename]["sha256"], f"M4 TF-IDF hash mismatch: {p}")
    fit = read_json(directory / "fit_boundary.json")
    need(fit.get("corpus_index_sha256") == corpus_hash and
         fit.get("ticker_order") == [r["ticker"] for r in rows],
         "M4 TF-IDF fit corpus/order mismatch")
    for prior, source in zip(fit["item1_inputs"], rows):
        need(all(prior.get(key) == source[key] for key in
                 ("ticker", "cik", "accession", "item1_sha256")),
             "M4 TF-IDF source text mismatch")
    matrix = np.load(directory / "cosine.npy", allow_pickle=False)
    vectors = np.load(directory / "vectors.npy", allow_pickle=False)
    need(matrix.shape == (5, 5) and vectors.shape[0] == 5 and
         vectors.shape[1] == fit["vocabulary_size"], "M4 TF-IDF shape mismatch")
    need(np.isfinite(vectors).all() and np.isfinite(matrix).all(),
         "M4 TF-IDF non-finite value")
    norms = np.linalg.norm(vectors, axis=1)
    need(np.allclose(norms, 1, rtol=0, atol=1e-8), "M4 TF-IDF vectors not L2 normalized")
    need(np.allclose(matrix, vectors @ vectors.T, rtol=0, atol=1e-8),
         "M4 TF-IDF cosine not reproduced by saved vectors")
    return matrix, {"manifest_sha256": digest(parent / "manifest.json"),
                    "fit_boundary_sha256": digest(directory / "fit_boundary.json"),
                    "vocabulary_size": fit["vocabulary_size"], "fit_scope": fit["fit_scope"],
                    "token_pattern": fit["token_pattern"]}


def refit_tfidf_same_tokenizer(rows: list[dict]) -> tuple[np.ndarray, np.ndarray,
                                                              list[str], np.ndarray]:
    """M4's TF-IDF formula refit on exact M6 BM25 regex and source documents."""
    counts = [Counter(TOKEN_RE.findall(Path(row["item1_path"]).read_text(
              encoding="utf-8").lower())) for row in rows]
    need(all(sum(counter.values()) == row["lexical_tokens"] and counter for
             counter, row in zip(counts, rows)), "Refit TF-IDF tokens differ from BM25 inputs")
    vocabulary = sorted(set().union(*(set(counter) for counter in counts)))
    df = Counter(term for counter in counts for term in counter)
    size = len(rows)
    idf = np.asarray([math.log((1 + size) / (1 + df[term])) + 1
                      for term in vocabulary], dtype=np.float64)
    vectors = np.asarray([[counter.get(term, 0) for term in vocabulary]
                          for counter in counts], dtype=np.float64) * idf
    norms = np.linalg.norm(vectors, axis=1)
    need(np.isfinite(vectors).all() and np.all(norms > 0), "Invalid refit TF-IDF vectors")
    vectors /= norms[:, None]
    matrix = vectors @ vectors.T
    need(np.isfinite(matrix).all(), "Invalid refit TF-IDF cosine")
    return vectors, matrix, vocabulary, idf


def verify_chunk_map(run: Path, length: int, rows: list[dict]) -> list[dict]:
    stats = read_json(run / f"dense_chunk_stats_{length}.json")["rows"]
    maps = [json.loads(line) for line in
            (run / f"dense_chunk_map_{length}.jsonl").read_text(encoding="utf-8").splitlines()]
    need(len(stats) == len(rows), f"Chunk stat row count mismatch: {run}")
    result = []
    for source, saved in zip(rows, stats):
        doc_id = source["id"]
        need(saved.get("id") == doc_id and saved.get("coverage_check") == "PASS",
             f"Chunk coverage status not PASS: {doc_id}, {run}")
        spans = [item for item in maps if item["id"] == doc_id]
        need(len(spans) == saved["chunk_count"] and spans,
             f"Missing or duplicate chunk spans: {doc_id}, {run}")
        need([item["chunk_index"] for item in spans] == list(range(len(spans))),
             f"Chunk order mismatch: {doc_id}, {run}")
        # Reopen the hash-checked M1 text only in memory; no source text is exported.
        text = Path(source["item1_path"]).read_text(encoding="utf-8")
        cursor = 0
        for item in spans:
            count = item["body_token_count"]
            need(item["text_sha256"] == source["item1_sha256"] and
                 item["token_start"] == cursor and
                 item["token_end_exclusive"] == cursor + count and
                 0 < count <= length and
                 count + (item["model_input_tokens_including_specials"] - count) <=
                 item["model_input_limit"], f"Token gap/overlap/input limit: {doc_id}, {run}")
            a, b = item["char_start"], item["char_end_exclusive"]
            need(0 <= a < b <= len(text) and
                 sha256(text[a:b].encode("utf-8")).hexdigest() == item["source_span_sha256"],
                 f"Source span mismatch: {doc_id}, {run}")
            cursor += count
        need(cursor == saved["body_tokens"] == saved["covered_token_end"] and
             len(spans) == math.ceil(cursor / length) and
             spans[-1]["body_token_count"] == saved["tail_tokens"] and
             all(item["body_token_count"] == length for item in spans[:-1]),
             f"Truncation, omission, or bad tail: {doc_id}, {run}")
        result.append(saved)
    need(len(maps) == sum(item["chunk_count"] for item in stats),
         f"Unexpected chunk map rows: {run}")
    return result


def verify_dense_vectors(run: Path, length: int, method: str,
                         ids: list[str]) -> tuple[np.ndarray, np.ndarray]:
    matrix = read_matrix(run / f"dense_cosine_{length}_{method}.csv", ids, True)
    vectors = np.load(run / f"dense_vectors_{length}_{method}.npy", allow_pickle=False)
    need(vectors.shape[0] == len(ids) and vectors.ndim == 2 and vectors.shape[1] > 0 and
         np.isfinite(vectors).all(), f"Dense vector shape/value invalid: {run}, {length}, {method}")
    need(np.allclose(np.linalg.norm(vectors, axis=1), 1, rtol=0, atol=1e-6),
         f"Dense document vectors not L2 normalized: {run}, {length}, {method}")
    need(np.allclose(matrix, vectors @ vectors.T, rtol=0, atol=1e-6),
         f"Dense cosine not reproduced from vectors: {run}, {length}, {method}")
    return vectors, matrix


def svg_start(width: int, height: int, title: str, subtitle: str) -> list[str]:
    return [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
            f'width="{width}" height="{height}" role="img" aria-label="{escape(title)}">',
            '<rect width="100%" height="100%" fill="#f8fafc"/>',
            f'<text x="24" y="34" font-family="Arial" font-size="24" fill="#0f172a">{escape(title)}</text>',
            f'<text x="24" y="58" font-family="Arial" font-size="13" fill="#475569">{escape(subtitle)}</text>']


def heat_color(value: float) -> str:
    if value < 0:
        alpha = min(1, -value)
        return f'rgb(255,{int(255-110*alpha)},{int(255-110*alpha)})'
    alpha = min(1, value)
    return f'rgb({int(255-205*alpha)},{int(255-135*alpha)},255)'


def save_heatmap(path: Path, matrices: dict[str, np.ndarray], tickers: list[str],
                 run_id: str, input_version: str) -> None:
    names = list(matrices)
    width, height = 1420, 100 + math.ceil(len(names) / 4) * 320
    svg = svg_start(width, height, "Five-company similarity heatmaps",
                    f"Run {run_id} | M1 index SHA-256 {input_version} | n=5 | self-check | cosine scale -1 to +1")
    for index, name in enumerate(names):
        col, row = index % 4, index // 4
        x0, y0 = 55 + col * 350, 95 + row * 320
        svg.append(f'<text x="{x0}" y="{y0}" font-family="Arial" font-size="15" font-weight="bold">{escape(name)}</text>')
        matrix = matrices[name]
        for i in range(5):
            svg.append(f'<text x="{x0 + 32}" y="{y0 + 31 + i*43}" text-anchor="end" font-family="Arial" font-size="11">{tickers[i]}</text>')
            svg.append(f'<text x="{x0 + 55 + i*43}" y="{y0 + 255}" text-anchor="middle" font-family="Arial" font-size="11">{tickers[i]}</text>')
            for j in range(5):
                v = float(matrix[i, j])
                x, y = x0 + 35 + j*43, y0 + 8 + i*43
                svg.append(f'<rect x="{x}" y="{y}" width="42" height="42" fill="{heat_color(v)}" stroke="#cbd5e1"/>')
                svg.append(f'<text x="{x+21}" y="{y+26}" text-anchor="middle" font-family="Arial" font-size="10" fill="#0f172a">{v:.2f}</text>')
    svg.append('</svg>')
    path.write_text("\n".join(svg) + "\n", encoding="utf-8")


def save_neighbors_svg(path: Path, neighbor_rows: list[dict], tickers: list[str],
                       run_id: str, input_version: str) -> None:
    names = list(dict.fromkeys(row["method"] for row in neighbor_rows))
    width, height = 1430, 150 + len(names)*57
    svg = svg_start(width, height, "Top-2 neighbor changes",
                    f"Run {run_id} | M1 index SHA-256 {input_version} | n=5 | exact-score ticker ties | self-check")
    for j, ticker in enumerate(tickers):
        svg.append(f'<text x="{270+j*225}" y="100" font-family="Arial" font-size="14" font-weight="bold">{ticker}</text>')
    for i, name in enumerate(names):
        y = 140 + i*57
        shade = "#e2e8f0" if i % 2 else "#fff"
        svg.append(f'<rect x="16" y="{y-28}" width="1395" height="50" fill="{shade}"/>')
        svg.append(f'<text x="26" y="{y}" font-family="Arial" font-size="13">{escape(name)}</text>')
        for j, ticker in enumerate(tickers):
            peer = next(row for row in neighbor_rows if row["method"] == name and row["ticker"] == ticker)
            label = peer["rank1"] + ", " + peer["rank2"]
            svg.append(f'<text x="{270+j*225}" y="{y}" font-family="Arial" font-size="13">{escape(label)}</text>')
    svg.append('</svg>')
    path.write_text("\n".join(svg) + "\n", encoding="utf-8")


def save_lengths_svg(path: Path, lengths: list[dict], run_id: str, input_version: str) -> None:
    width, height = 1000, 390
    svg = svg_start(width, height, "Item 1 length and chunk coverage",
                    f"Run {run_id} | M1 index SHA-256 {input_version} | n=5 | body tokens exclude specials/padding | self-check")
    maximum = max(row["model_body_tokens_128"] for row in lengths)
    for i, row in enumerate(lengths):
        y = 98 + i*54
        width_bar = 560 * row["model_body_tokens_128"] / maximum
        svg.append(f'<text x="27" y="{y+15}" font-family="Arial" font-size="14">{row["ticker"]}</text>')
        svg.append(f'<rect x="108" y="{y}" width="{width_bar:.1f}" height="22" fill="#2563eb"/>')
        svg.append(f'<text x="{118+width_bar:.1f}" y="{y+16}" font-family="Arial" font-size="12">{row["model_body_tokens_128"]} tokens</text>')
        svg.append(f'<text x="720" y="{y+16}" font-family="Arial" font-size="12">128: {row["chunks_128"]} / tail {row["tail_128"]}; 192: {row["chunks_192"]} / tail {row["tail_192"]}</text>')
    svg.append('</svg>')
    path.write_text("\n".join(svg) + "\n", encoding="utf-8")


def build(args: argparse.Namespace) -> dict:
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    embedding = config["embedding"]
    lengths = [int(embedding["body_tokens"]), int(embedding["alternate_body_tokens"])]
    need(lengths == [128, 192] and embedding["overlap_tokens"] == 0 and
         embedding["normalize_chunks"] is True and embedding["normalize_documents"] is True,
         "Configuration differs from the specified M6 dense engineering arms")
    tolerance = float(embedding["repeat_tolerance"])
    need(math.isfinite(tolerance) and tolerance > 0, "Invalid repeat tolerance")
    corpus_rows, corpus_hash = verify_corpus(args.corpus_index)
    ids = [r["id"] for r in corpus_rows]
    tickers = [r["ticker"] for r in corpus_rows]
    text_hashes = [r["item1_sha256"] for r in corpus_rows]
    config_hash = digest(args.config)
    tfidf, tfidf_evidence = verify_tfidf(args.tfidf_dir, corpus_hash, corpus_rows)
    runs = {"lexical": args.lexical_run, "dense": args.dense_run,
            "repeat": args.repeat_run}
    receipts = {name: verify_receipt(path, config_hash, args.corpus_index, corpus_hash,
                                     text_hashes, name != "lexical")
                for name, path in runs.items()}
    for path in runs.values():
        verify_rows(path, corpus_rows)
    need(receipts["dense"].get("model_identity") == receipts["repeat"].get("model_identity"),
         "Dense/repeat model identity mismatch")
    for name in ("lexical", "repeat"):
        need(digest(runs[name] / "rows.json") == digest(args.dense_run / "rows.json"),
             f"{name} row evidence differs from dense run")

    new_tfidf_vectors, new_tfidf, vocabulary, idf = refit_tfidf_same_tokenizer(corpus_rows)
    matrices = {"TF-IDF M6 same-tokenizer": new_tfidf,
                "TF-IDF M4 historical": tfidf}
    directed = {}
    for b in (0, 0.75, 1):
        suffix = f"b{b:g}"
        label = f"BM25 term-weight cosine {suffix}"
        matrices[label] = read_matrix(args.lexical_run /
                                      f"bm25_termweight_cosine_{suffix}.csv", ids, True)
        directed[suffix] = read_matrix(args.lexical_run /
                                       f"bm25_directed_scores_{suffix}.csv", ids, False)
        # Dense runs also produce lexical outputs; they must use the identical fit corpus.
        for run in (args.dense_run, args.repeat_run):
            need(np.allclose(matrices[label], read_matrix(run /
                 f"bm25_termweight_cosine_{suffix}.csv", ids, True), rtol=0, atol=1e-12),
                 f"Lexical matrix differs across runs: {suffix}")
    lexical_metrics = read_json(args.lexical_run / "lexical_metrics.json")
    need(lexical_metrics.get("k1") == 1.2 and
         "unique" in lexical_metrics.get("query_rule", "") and
         lexical_metrics.get("fit_corpus", "").startswith("exact included rows"),
         "BM25 scoring/fit boundary not recorded as expected")
    need(sorted(lexical_metrics.get("arms", {})) == ["b0", "b0.75", "b1"],
         "Wrong BM25 arms")

    length_rows = [{"ticker": r["ticker"], "cik": r["cik"],
                    "accession": r["accession"], "document_id": r["id"],
                    "section": "10-K Item 1 Business", "characters": r["characters"],
                    "lexical_tokens": r["lexical_tokens"]} for r in corpus_rows]
    repeat_checks = {}
    for length in lengths:
        main_stats = verify_chunk_map(args.dense_run, length, corpus_rows)
        repeat_stats = verify_chunk_map(args.repeat_run, length, corpus_rows)
        need(main_stats == repeat_stats, f"Dense/repeat chunk stats differ: {length}")
        need(digest(args.dense_run / f"dense_chunk_map_{length}.jsonl") ==
             digest(args.repeat_run / f"dense_chunk_map_{length}.jsonl"),
             f"Dense/repeat source chunk maps differ: {length}")
        for row, stat in zip(length_rows, main_stats):
            row[f"model_body_tokens_{length}"] = stat["body_tokens"]
            row[f"chunks_{length}"] = stat["chunk_count"]
            row[f"tail_{length}"] = stat["tail_tokens"]
            row[f"coverage_{length}"] = stat["coverage_check"]
        for method in METHODS:
            v, cosine = verify_dense_vectors(args.dense_run, length, method, ids)
            rv, rc = verify_dense_vectors(args.repeat_run, length, method, ids)
            v_error = float(np.max(np.abs(v - rv)))
            c_error = float(np.max(np.abs(cosine - rc)))
            need(v_error <= tolerance and c_error <= tolerance,
                 f"Exact repeated inference exceeded tolerance for {length}/{method}: "
                 f"vector={v_error}, cosine={c_error}, tolerance={tolerance}")
            label = f"Dense {length} {method}"
            matrices[label] = cosine
            repeat_checks[label] = {"max_abs_vector": v_error,
                                    "max_abs_cosine": c_error, "tolerance": tolerance,
                                    "status": "PASS"}
    for row in length_rows:
        need(row["model_body_tokens_128"] == row["model_body_tokens_192"],
             f"Same text has different token count across chunk settings: {row['ticker']}")

    run_id = args.dense_run.parent.name
    method_rows = [{"method": "TF-IDF M6 same-tokenizer", "input": "same five saved 10-K Item 1 texts",
                    "output": "symmetric document cosine", "fit_boundary": "five M1 documents",
                    "reference": "descriptive lexical baseline for M6",
                    "parameters": "regex [a-z0-9]+; raw TF; smooth IDF; L2"},
                   {"method": "TF-IDF M4 historical", "input": "same five saved 10-K Item 1 texts",
                    "output": "symmetric document cosine", "fit_boundary": "five M1 documents",
                    "reference": "historical only; tokenizer differs from M6",
                    "parameters": "regex [a-z][a-z0-9]+; M4 fit_boundary.json"}]
    method_rows.extend({"method": f"BM25 term-weight cosine b{b:g}",
                        "input": "same five saved 10-K Item 1 texts",
                        "output": "symmetric term-weight cosine", "fit_boundary": "five M1 documents",
                        "reference": "BM25 b0", "parameters": f"k1=1.2; b={b:g}; unique query terms"}
                       for b in (0, 0.75, 1))
    method_rows.extend({"method": f"Dense {length} {method}",
                        "input": "same five saved 10-K Item 1 texts",
                        "output": "symmetric document cosine", "fit_boundary": "fixed pretrained model",
                        "reference": f"Dense {length} equal_chunks" if method != "equal_chunks" else
                                     "Dense 128 equal_chunks",
                        "parameters": f"{length} body tokens; no overlap; chunk L2; {method}; document L2"}
                       for length in lengths for method in METHODS)
    method_rows.extend({"method": f"BM25 directed query-document b{b:g}",
                        "input": "unique terms of source document as query; target document as candidate",
                        "output": "directed score, not symmetric similarity",
                        "fit_boundary": "five M1 documents", "reference": "BM25 directed b0",
                        "parameters": f"k1=1.2; b={b:g}; positive log1p IDF"}
                       for b in (0, 0.75, 1))
    comparisons = [("TF-IDF M6 same-tokenizer", "TF-IDF M4 historical")]
    comparisons += [("TF-IDF M6 same-tokenizer", f"BM25 term-weight cosine b{b:g}")
                    for b in (0, 0.75, 1)]
    comparisons += [("BM25 term-weight cosine b0", f"BM25 term-weight cosine b{b:g}")
                    for b in (0.75, 1)]
    comparisons += [(f"Dense {length} equal_chunks", f"Dense {length} body_token_weighted")
                    for length in lengths]
    comparisons += [(f"Dense 128 {method}", f"Dense 192 {method}") for method in METHODS]
    metrics = []
    overlap_rows = []
    for reference, compared in comparisons:
        a, b = matrices[reference], matrices[compared]
        peer_a, peer_b = top2(a, ids, tickers), top2(b, ids, tickers)
        overlap = {}
        for document_id in ids:
            ticker = document_id.split("|", 1)[0]
            value = len({r["ticker"] for r in peer_a[document_id]} &
                        {r["ticker"] for r in peer_b[document_id]}) / 2
            overlap[ticker] = value
            overlap_rows.append({"reference": reference, "compared": compared,
                                 "ticker": ticker, "top2_overlap": value,
                                 "reference_top2": ",".join(r["ticker"] for r in peer_a[document_id]),
                                 "compared_top2": ",".join(r["ticker"] for r in peer_b[document_id])})
        metrics.append({"reference": reference, "compared": compared,
                        "upper_triangle_spearman": upper_spearman(a, b),
                        "top2_overlap_by_ticker": overlap,
                        "mean_top2_overlap": float(np.mean(list(overlap.values()))),
                        "matrix_difference": difference(a, b),
                        "common_company_count": len(ids),
                        "company_pair_count": len(ids)*(len(ids)-1)//2})
    neighbor_rows = []
    for name, matrix in matrices.items():
        peers = top2(matrix, ids, tickers)
        for source in corpus_rows:
            p = peers[source["id"]]
            neighbor_rows.append({"method": name, "ticker": source["ticker"],
                                  "rank1": p[0]["ticker"], "score1": p[0]["score"],
                                  "rank2": p[1]["ticker"], "score2": p[1]["score"]})
    # Directed BM25 is exported separately; never insert it into symmetric-matrix comparisons.
    directed_rows = []
    for suffix, matrix in directed.items():
        peers = top2(matrix, ids, tickers)
        for source in corpus_rows:
            p = peers[source["id"]]
            directed_rows.append({"arm": suffix, "query_ticker": source["ticker"],
                                  "target_rank1": p[0]["ticker"], "score1": p[0]["score"],
                                  "target_rank2": p[1]["ticker"], "score2": p[1]["score"]})

    np.save(args.output / "tfidf_same_tokenizer_vectors.npy", new_tfidf_vectors, allow_pickle=False)
    write_csv(args.output / "tfidf_same_tokenizer_cosine.csv", ["document_id", *ids],
              [{"document_id": ids[i], **{ids[j]: float(new_tfidf[i, j])
                                           for j in range(len(ids))}} for i in range(len(ids))])
    write_json(args.output / "tfidf_same_tokenizer_vocabulary.json",
               {"terms": vocabulary, "idf": idf.tolist()})
    vocabulary_hash = digest(args.output / "tfidf_same_tokenizer_vocabulary.json")
    write_json(args.output / "tfidf_same_tokenizer_fit.json", {
               "fit_scope": "exact five hash-verified M1 10-K Item 1 Business documents only",
               "corpus_index_sha256": corpus_hash,
               "company_order": tickers,
               "item1_inputs": [{key: row[key] for key in
                                ("ticker", "cik", "accession", "item1_sha256")}
                               for row in corpus_rows],
               "tokenizer": "ascii_alphanumeric_lower_v1", "token_pattern": "[a-z0-9]+",
               "term_frequency": "raw count", "idf": "log((1+n_documents)/(1+document_frequency))+1",
               "l2_normalize": True, "vocabulary_size": len(vocabulary),
               "vocabulary_file_sha256": vocabulary_hash,
               "historical_m4_fit_boundary_sha256": tfidf_evidence["fit_boundary_sha256"],
               "historical_difference": "M4 uses [a-z][a-z0-9]+; not the same tokenizer as M6 BM25"})
    write_csv(args.output / "length_table.csv", list(length_rows[0]), length_rows)
    write_csv(args.output / "method_table.csv", list(method_rows[0]), method_rows)
    write_csv(args.output / "neighbors.csv", list(neighbor_rows[0]), neighbor_rows)
    write_csv(args.output / "top2_overlap.csv", list(overlap_rows[0]), overlap_rows)
    write_csv(args.output / "directed_bm25_neighbors.csv", list(directed_rows[0]), directed_rows)
    write_json(args.output / "comparison_metrics.json", {"run_id": run_id,
               "scope": "five-company same-corpus descriptive engineering demonstration",
               "tie_rule": "exact-score ties by ticker ascending; Spearman average ranks",
               "undefined_values": "null with reason; never silently replaced by zero",
               "comparisons": metrics, "directed_bm25_excluded_from_symmetric_comparisons": True})
    save_heatmap(args.output / "similarity_heatmaps.svg", matrices, tickers, run_id,
                 corpus_hash[:12])
    save_neighbors_svg(args.output / "neighbor_changes.svg", neighbor_rows, tickers, run_id,
                       corpus_hash[:12])
    save_lengths_svg(args.output / "length_chunks.svg", length_rows, run_id,
                     corpus_hash[:12])
    evidence = {"status": "PASS", "kind": "M6_SAVED_OUTPUT_SELF_CHECK",
                "checked_utc": datetime.now(timezone.utc).isoformat(),
                "audit_relationship": "same-agent self-check; separately coded arithmetic, not independent audit",
                "financial_experiment": "NOT_RUN", "protocol": "M5_NOT_FROZEN",
                "no_historical_availability_claim": True,
                "corpus_index_sha256": corpus_hash, "config_sha256": config_hash,
                "run_id": run_id,
                "reporter_source_sha256": digest(Path(__file__)),
                "tfidf": tfidf_evidence,
                "run_receipt_sha256": {name: digest(path / "receipt.json") for name, path in runs.items()},
                "run_source_sha256": {name: receipt.get("source_sha256") for name, receipt in receipts.items()},
                "model_identity": receipts["dense"].get("model_identity"),
                "repeat_checks": repeat_checks, "common_company_order": tickers,
                "included_file_hashes": [{key: r[key] for key in
                                          ("ticker", "cik", "accession", "item1_sha256", "raw_sha256")}
                                         for r in corpus_rows]}
    report = ["# M6 saved-output self-check", "",
              f"Run: `{run_id}`. Same five real saved 10-K Item 1 Business documents: " +
              ", ".join(tickers) + ". All saved source and output hashes, company order, fixed model "
              "identity and exact repeated inference passed the checks in `audit.json`.", "",
              f"Dense model: `{embedding['model_id']}` at fixed revision "
              f"`{embedding['revision']}`. The tokenizer uses the same recorded revision.", "",
              "This is a same-agent self-check of a small descriptive engineering demonstration. "
              "The fixed-weight repeat is not a separate training seed. No financial experiment, "
              "return prediction, incremental-value test, independent audit, or historical "
              "availability conclusion was performed.", "",
              "## Reading order", "",
              "`audit.json` → `tfidf_same_tokenizer_fit.json` → `length_table.csv` → `method_table.csv` → "
              "`comparison_metrics.json` → `neighbors.csv` and `top2_overlap.csv` → SVG figures.", "",
              "M6 TF-IDF was refit on the same five texts and `[a-z0-9]+` tokenizer as BM25. "
              "The verified M4 TF-IDF is retained as a historical reference only; its tokenizer "
              "was `[a-z][a-z0-9]+`.", "",
              "BM25 directed query-document scores are in `directed_bm25_neighbors.csv`. "
              "They are not used as symmetric cosine or covariance matrices.", "",
              "## Selected descriptive comparisons", "",
              "| Reference | Compared | Upper-triangle Spearman | Mean top-2 overlap | Max absolute off-diagonal difference |",
              "|---|---|---:|---:|---:|"]
    for row in metrics:
        rho = row["upper_triangle_spearman"]
        value = f"{rho['value']:.4f}" if rho["value"] is not None else f"null ({rho['reason']})"
        report.append(f"| {row['reference']} | {row['compared']} | {value} | "
                      f"{row['mean_top2_overlap']:.3f} | "
                      f"{row['matrix_difference']['max_absolute_off_diagonal']:.5f} |")
    report += ["", "Metrics use ten dependent company pairs, with no p-values. "
               "Changed similarity does not imply improvement; stable similarity does not establish "
               "financial validity or rule out forward-looking bias.", ""]
    (args.output / "report.md").write_text("\n".join(report), encoding="utf-8")
    evidence["output_sha256"] = {p.name: digest(p) for p in sorted(args.output.iterdir())
                                  if p.is_file() and p.name != "audit.json"}
    write_json(args.output / "audit.json", evidence)
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("corpus-index", "tfidf-dir", "lexical-run", "dense-run",
                 "repeat-run", "config", "output"):
        parser.add_argument("--" + flag, type=Path, required=True)
    args = parser.parse_args(argv)
    for name in ("corpus_index", "tfidf_dir", "lexical_run", "dense_run", "repeat_run", "config"):
        path = getattr(args, name).resolve()
        need(path.exists(), f"Missing input {name}: {path}")
        setattr(args, name, path)
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    try:
        evidence = build(args)
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        write_json(args.output / "audit.json", {"status": "FAILED", "kind": "M6_SAVED_OUTPUT_SELF_CHECK",
                   "reason": f"{type(exc).__name__}: {exc}",
                   "checked_utc": datetime.now(timezone.utc).isoformat(),
                   "financial_experiment": "NOT_RUN", "descriptive_comparison": "NOT_ACCEPTED"})
        print(json.dumps({"status": "FAILED", "reason": str(exc), "output": str(args.output)}))
        return 1
    print(json.dumps({"status": evidence["status"], "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
