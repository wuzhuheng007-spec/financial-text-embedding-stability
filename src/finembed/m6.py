"""Offline M6 engineering entry point; requires real, hash-checked source files."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import sys

from .analysis import validate_corpus, upper_triangle_spearman, topk, _cosine
from .config import load_config
from .extract import primary_text
from .lexical import BM25, tokenize, TOKENIZER_ID
from .model import Encoder, aggregate, ModelError
from .run import sha256, write_json
from .sec import raw_header

SECTIONS = {
    "10-K": {"ITEM_1_BUSINESS", "ITEM_1A_RISK_FACTORS", "ITEM_7_MDA"},
    "10-Q": {"PART_I_ITEM_2_MDA", "PART_II_ITEM_1A_RISK_FACTORS"},
}


def load_sections(index_path: Path) -> tuple[list[dict], dict]:
    """Verify bytes, SEC header and exact primary-text substring.

    Semantic heading boundaries remain a supplied human-review attestation;
    this function does not pretend to solve arbitrary SEC section extraction.
    Relative file paths are resolved relative to the section index.
    """
    index = json.loads(index_path.read_text(encoding="utf-8"))
    entries = index.get("entries", [])
    if not entries:
        raise ValueError("Section index has no entries")
    out = []
    seen = set()
    for r in entries:
        form, section = r["form"], r["section"]
        if section not in SECTIONS.get(form, set()):
            raise ValueError("Unsupported form/section: 10-Q Item 1 is NOT Business")
        if r.get("boundary_reviewed") is not True:
            raise ValueError("Section boundaries require explicit review attestation")
        key = (r["cik"], r["accession"], section)
        if key in seen:
            raise ValueError("Duplicate company/filing/section")
        seen.add(key)
        raw_path = index_path.parent / r["raw_path"]
        text_path = index_path.parent / r["text_path"]
        raw, body = raw_path.read_bytes(), text_path.read_bytes()
        if sha256(raw) != r["raw_sha256"] or sha256(body) != r["text_sha256"]:
            raise ValueError("Raw or section text SHA-256 mismatch")
        header = raw_header(raw, r["accession"], form)
        rendered, _ = primary_text(raw, form)
        start, end = r["start_char"], r["end_char_exclusive"]
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(rendered):
            raise ValueError("Invalid section character offsets")
        text = body.decode("utf-8")
        if rendered[start:end] != text:
            raise ValueError("Section is not the exact saved primary-text substring")
        out.append({"id": "|".join(key), "ticker": r["ticker"], "cik": r["cik"],
                    "accession": r["accession"], "form": form, "section": section, "text": text,
                    "text_sha256": sha256(body), "raw_sha256": sha256(raw), "header": header,
                    "boundary_evidence": "USER_ATTESTED_SEMANTIC_BOUNDARY; substring checked"})
    if len({(r["form"], r["section"]) for r in out}) != 1:
        raise ValueError("Run each form/section stratum separately; mixed corpora not silently pooled")
    return out, {"source": "section-index", "index_sha256": sha256(index_path.read_bytes()),
                 "raw_and_substring_checks": "PASS", "historical_public_availability": "NOT_VERIFIED"}


def legacy_corpus(config, path: Path):
    valid, audit = validate_corpus(config, path)
    if audit["valid_count"] != audit["planned_count"] or not valid:
        raise ValueError("M1 corpus incomplete or invalid; inspect corpus audit")
    return [{"id": r["ticker"] + "|" + r["accession"], "ticker": r["ticker"],
             "cik": r["cik"], "accession": r["accession"], "form": "10-K", "section": "ITEM_1_BUSINESS",
             "text": r["item1_text"], "text_sha256": r["item1_sha256"],
             "raw_sha256": r["files"]["original_submission.txt"]["sha256"]} for r in valid], audit


def save_matrix(path: Path, ids: list[str], matrix):
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["document_id", *ids])
        w.writerows([[label, *row] for label, row in zip(ids, matrix)])


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sources = p.add_mutually_exclusive_group(required=True)
    sources.add_argument("--corpus", type=Path, help="Existing complete real M1 corpus_index.json")
    sources.add_argument("--section-index", type=Path, help="Hash-checked original 10-K or 10-Q sections")
    p.add_argument("--config", default="configs/smoke.yaml")
    p.add_argument("--output", type=Path, required=True, help="New directory; existing evidence is never overwritten")
    p.add_argument("--dense", action="store_true", help="Also use locally pinned encoder; never downloads weights")
    p.add_argument("--k1", type=float, default=1.2)
    p.add_argument("--b-values", type=float, nargs="+", default=[0., .75, 1.])
    p.add_argument("--top-k", type=int, default=2)
    args = p.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=False)
    receipt = {"schema_version": 1, "status": "RUNNING", "kind": "M6_ENGINEERING_ONLY",
               "started_utc": datetime.now(timezone.utc).isoformat(), "python": sys.version.split()[0],
               "platform": platform.platform(), "arguments": {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
               "source_sha256": {f.name: sha256(f.read_bytes()) for f in Path(__file__).parent.glob("*.py")},
               "financial_experiment": "NOT_RUN", "scientific_protocol": "M5_NOT_FROZEN",
               "dense_status": "NOT_REQUESTED", "checks": "same-agent self-check only"}
    write_json(args.output / "receipt.json", receipt)
    try:
        if len(set(args.b_values)) != len(args.b_values):
            raise ValueError("Duplicate b values")
        if 0. not in args.b_values:
            raise ValueError("Include b=0 as the prespecified no-length-correction control")
        config = load_config(args.config)
        receipt["config_sha256"] = config.source_sha256
        docs, audit = (legacy_corpus(config, args.corpus.resolve()) if args.corpus else load_sections(args.section_index.resolve()))
        write_json(args.output / "input_audit.json", audit)
        ids = [r["id"] for r in docs]
        if len(set(ids)) != len(ids) or not 1 <= args.top_k < len(docs) - 1:
            raise ValueError("Require unique documents and 1 <= top-k < document_count-1")
        if len({r["cik"] for r in docs}) != len(docs):
            raise ValueError("Peer comparison requires one section per company per run; split repeated filings")
        tokens = [tokenize(r["text"]) for r in docs]
        rows = [{k: v for k, v in r.items() if k != "text"} for r in docs]
        for row, ts in zip(rows, tokens):
            row["lexical_token_count"] = len(ts)
        write_json(args.output / "rows.json", {"rows": rows})
        receipt["row_text_hashes"] = [r["text_sha256"] for r in docs]
        comparisons = {}
        reference = None
        for b in args.b_values:
            bm = BM25(tokens, k1=args.k1, b=b)
            cosine = bm.termweight_cosine()
            name = f"b{b:g}"
            save_matrix(args.output / f"bm25_termweight_cosine_{name}.csv", ids, cosine)
            save_matrix(args.output / f"bm25_directed_scores_{name}.csv", ids, [bm.scores(q) for q in tokens])
            if b == 0:
                reference = cosine
            comparisons[name] = {"b": b, "neighbors": topk(cosine, ids, args.top_k)}
        if reference is None:
            raise ValueError("Missing b=0 reference")
        base_peers = topk(reference, ids, args.top_k)
        for name, result in comparisons.items():
            # Read the saved matrix, so metrics and exported result use identical numbers.
            with (args.output / f"bm25_termweight_cosine_{name}.csv").open() as f:
                values = list(csv.reader(f))[1:]
            matrix = [[float(v) for v in row[1:]] for row in values]
            result["spearman_vs_b0"] = upper_triangle_spearman(reference, matrix)
            result["topk_overlap_vs_b0"] = {i: len({x["ticker"] for x in base_peers[i]} &
                {x["ticker"] for x in result["neighbors"][i]}) / args.top_k for i in ids}
        write_json(args.output / "lexical_metrics.json", {"tokenizer": TOKENIZER_ID, "k1": args.k1,
                   "query_rule": "all unique lexical terms in source document; diagonal excluded for peers",
                   "score_convention": "positive log1p IDF; exact lengths; includes k1+1",
                   "fit_corpus": "exact included rows only; no other documents", "arms": comparisons,
                   "inference": "descriptive; company pairs dependent; no p-values or financial claims"})
        if args.dense:
            receipt["dense_status"] = "RUNNING"
            enc = Encoder(config)
            receipt["model_identity"] = enc.identity
            dense_comparisons = {}
            for length in [config.body_tokens, config.alternate_body_tokens]:
                vectors = {"equal_chunks": [], "body_token_weighted": []}
                chunk_stats = []
                for r in docs:
                    chunks = enc.chunks(r["text"], length)
                    raw_vectors = enc.embed_chunks(chunks)
                    counts = [len(c.token_ids) for c in chunks]
                    chunk_stats.append({"id": r["id"], "body_tokens": sum(counts), "chunk_count": len(counts),
                                        "tail_tokens": counts[-1], "covered_token_end": chunks[-1].token_end_exclusive})
                    for method in vectors:
                        vectors[method].append(aggregate(raw_vectors, config.normalize_chunks, True,
                            weights=counts if method == "body_token_weighted" else None))
                write_json(args.output / f"dense_chunk_stats_{length}.json", {"rows": chunk_stats})
                cosines = {}
                for method, rows_v in vectors.items():
                    cosines[method] = _cosine(rows_v)
                    save_matrix(args.output / f"dense_cosine_{length}_{method}.csv", ids, cosines[method])
                    import numpy as np
                    np.save(args.output / f"dense_vectors_{length}_{method}.npy", np.asarray(rows_v), allow_pickle=False)
                dense_comparisons[str(length)] = upper_triangle_spearman(cosines["equal_chunks"], cosines["body_token_weighted"])
            receipt["dense_status"] = "DONE"
            write_json(args.output / "dense_metrics.json", {"chunk_normalization": config.normalize_chunks,
                "document_L2": True, "overlap": 0, "weight_unit": "body WordPiece tokens; excludes specials/padding",
                "comparison": dense_comparisons, "scope": "descriptive only; pooling of contextual chunks is not full-document token pooling"})
        receipt["status"] = "DONE"
        return_code = 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, ImportError) as exc:
        receipt["status"] = "BLOCKED" if isinstance(exc, (FileNotFoundError, ImportError, ModelError)) else "FAILED"
        if receipt["dense_status"] == "RUNNING":
            receipt["dense_status"] = receipt["status"]
        receipt["reason"] = f"{type(exc).__name__}: {exc}"
        return_code = 2 if receipt["status"] == "BLOCKED" else 1
    receipt["finished_utc"] = datetime.now(timezone.utc).isoformat()
    receipt["outputs"] = {f.name: {"sha256": sha256(f.read_bytes()), "bytes": f.stat().st_size}
                          for f in sorted(args.output.iterdir()) if f.is_file() and f.name != "receipt.json"}
    write_json(args.output / "receipt.json", receipt)
    print(json.dumps({"status": receipt["status"], "reason": receipt.get("reason"), "output": str(args.output)}))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
