"""Offline, tokenizer-only preflight for an unaccepted chunk-length candidate."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys

from .analysis import validate_corpus
from .config import load_config
from .m6 import chunk_evidence
from .model import MODEL_FILES, ModelError, chunk_text
from .run import git_state, write_json
from .small_chunk_provenance import (RuntimeProvenanceError,
                                     capture_runtime_provenance,
                                     verify_runtime_provenance)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_candidate(path: Path) -> dict:
    candidate = json.loads(path.read_text(encoding="utf-8"))
    if (candidate.get("schema_version") != 1 or
            candidate.get("kind") != "new_engineering_probe_candidate" or
            candidate.get("status") != "PROPOSED" or
            candidate.get("researcher_accepted") is not False or
            candidate.get("real_inference_status") != "NOT_RUN"):
        raise ValueError("Candidate must remain a proposed, unaccepted plan with no inference")
    source = candidate["input"]
    if (source.get("form") != "10-K" or source.get("section") != "ITEM_1_BUSINESS" or
            source.get("company_order") != ["AAPL", "MSFT", "AMZN", "GOOGL", "META"]):
        raise ValueError("Candidate requires the complete original five-company Item 1 stratum")
    chunking = candidate["chunking"]
    lengths = chunking["candidate_body_tokens"]
    if (chunking.get("reference_body_tokens") != 128 or
            not isinstance(lengths, list) or not lengths or
            any(type(length) is not int or length <= 0 for length in lengths) or
            sorted(set(lengths)) != lengths or 128 not in lengths or
            chunking.get("overlap_tokens") != 0):
        raise ValueError("Candidate chunk lengths must be unique, ordered, positive and include 128")
    if (candidate.get("pooling") != ["equal_chunks", "body_token_weighted"] or
            any(candidate.get(key) is not False for key in ("query_rewriting", "generation", "training"))):
        raise ValueError("Candidate must use only the two declared pooling rules")
    if candidate["model_config"].get("path") != "configs/smoke.yaml":
        raise ValueError("Candidate must reference the pinned historical smoke config")
    for digest in (source.get("corpus_index_sha256"), candidate["model_config"].get("sha256")):
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Candidate source/config hashes must be SHA-256")
    return candidate


def inspect_model_files(config) -> dict:
    """Hash pinned files without constructing Encoder or an ONNX session."""
    files = {}
    for name, field in MODEL_FILES.items():
        path = config.model_cache / name
        if not path.is_file():
            raise FileNotFoundError(f"Pinned local model file missing: {path}")
        actual = file_sha256(path)
        if actual != getattr(config, field):
            raise ValueError(f"Pinned local model file hash mismatch: {name}")
        files[name] = {"path": str(path), "sha256": actual, "bytes": path.stat().st_size}
    model_config = json.loads((config.model_cache / "config.json").read_text(encoding="utf-8"))
    pooling_config = json.loads((config.model_cache / "sentence_bert_config.json").read_text(encoding="utf-8"))
    max_input = min(int(model_config["max_position_embeddings"]), int(pooling_config["max_seq_length"]))
    return {"model_id": config.model_id, "revision": config.model_revision,
            "tokenizer_revision": config.tokenizer_revision, "files": files,
            "max_input_tokens": max_input, "encoder_session_created": False}


def load_local_tokenizer(path: Path):
    from tokenizers import Tokenizer

    tokenizer = Tokenizer.from_file(str(path))
    tokenizer.no_truncation()
    tokenizer.no_padding()
    special_count = tokenizer.num_special_tokens_to_add(False)
    if (special_count != 2 or any(tokenizer.token_to_id(token) is None
                                  for token in ("[CLS]", "[SEP]", "[PAD]"))):
        raise ModelError("Pinned tokenizer lacks the expected BERT special-token interface")
    return tokenizer, special_count


def pooling_weights(chunks) -> dict:
    lengths = [len(chunk.token_ids) for chunk in chunks]
    if not lengths or any(length <= 0 for length in lengths):
        raise ModelError("Pooling requires nonempty, positive-length chunks")
    total = sum(lengths)
    equal = [1.0 / len(lengths)] * len(lengths)
    weighted = [length / total for length in lengths]
    if not math.isclose(sum(equal), 1.0, abs_tol=1e-12) or not math.isclose(
            sum(weighted), 1.0, abs_tol=1e-12):
        raise ModelError("Pooling weights do not sum to one")
    return {"equal_chunks": {"definition": "1 / chunk_count", "tail_weight": equal[-1]},
            "body_token_weighted": {"definition": "chunk_body_tokens / total_body_tokens",
                                    "tail_weight": weighted[-1]},
            "zero_length_policy": "FAIL", "chunk_vector_normalization": "L2 before weighted mean",
            "document_vector_normalization": "L2 after weighted mean"}


def compare_historical_map(path: Path, current: list[dict]) -> dict:
    old = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    fields = ("ticker", "item1_sha256", "chunk_index", "token_start", "token_end_exclusive",
              "char_start", "char_end_exclusive", "source_span_sha256",
              "model_input_tokens_including_specials")
    mismatch = []
    if len(old) != len(current):
        mismatch.append(f"row count differs: historical={len(old)}, current={len(current)}")
    for index, (saved, now) in enumerate(zip(old, current)):
        if any(saved.get(field) != now.get(field) for field in fields) or saved.get("body_token_ids") != now.get("body_token_ids"):
            mismatch.append(f"row {index}: {saved.get('ticker')} differs")
    return {"status": "PASS" if not mismatch else "MISMATCH", "historical_map_sha256": file_sha256(path),
            "historical_rows": len(old), "preflight_rows": len(current), "mismatches": mismatch[:10]}


def _binding(path: Path) -> dict:
    return {"path": str(path.resolve(strict=True)), "sha256": file_sha256(path)}


def _write_summary(path: Path, receipt: dict) -> None:
    lines = ["# Small-chunk tokenization preflight summary", "",
             f"Status: **{receipt['status']}**. Candidate: **PROPOSED**, researcher accepted: **false**.",
             "Encoder, generation, retrieval evaluation and financial experiments: **NOT_RUN**.", ""]
    if receipt.get("reason"):
        lines.extend([f"Reason: {receipt['reason']}", ""])
    if receipt.get("documents"):
        length_labels = " / ".join(str(length) for length in receipt["candidate_body_tokens"])
        lines.extend([f"| Company | Accession | Item 1 SHA-256 | Body tokens | Chunks at {length_labels} |",
                      "| --- | --- | --- | ---: | --- |"])
        for row in receipt["documents"]:
            lengths = receipt["candidate_body_tokens"]
            counts = [str(row["lengths"][str(length)]["chunk_count"]) for length in lengths]
            lines.append(f"| {row['ticker']} | `{row['accession']}` | `{row['item1_sha256']}` | {row['body_tokens']} | {' / '.join(counts)} |")
        lines.append("")
    if receipt.get("historical_128_alignment"):
        lines.append(f"Historical 128-token map alignment: **{receipt['historical_128_alignment']['status']}**.")
        lines.append("")
    if receipt.get("planned_counts"):
        p = receipt["planned_counts"]
        lines.append(f"Future logical document-length encoding jobs: {p['document_length_jobs']}; "
                     f"future chunk vectors: {p['chunk_vectors']}; pooling outputs: {p['company_vectors']}. "
                     "These are planned counts, not executions.")
        lines.append("")
    lines.extend(["The preflight checks tokenization and source identity only. No small-step model result or effect size exists.", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def chunk_map_sha256(chunks) -> str:
    """Bind token IDs, body ranges and exact source spans without storing text."""
    rows = [{"token_start": chunk.token_start,
             "token_end_exclusive": chunk.token_end_exclusive,
             "char_start": chunk.char_start,
             "char_end_exclusive": chunk.char_end_exclusive,
             "token_ids": list(chunk.token_ids),
             "source_span_sha256": chunk.source_span_sha256} for chunk in chunks]
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def build_preflight(candidate_path: Path, corpus_path: Path,
                    historical_map: Path | None = None) -> dict:
    """Recompute tokenization evidence in memory, without an encoder session."""
    receipt = {"schema_version": 1, "kind": "TOKENIZATION_PREFLIGHT", "status": "RUNNING",
               "created_utc": datetime.now(timezone.utc).isoformat(),
               "candidate_status": "PROPOSED", "researcher_accepted": False,
               "real_inference_status": "NOT_RUN", "generation_status": "NOT_RUN",
               "retrieval_evaluation_status": "NOT_RUN", "financial_experiment_status": "NOT_RUN",
               "encoder_session_created": False, "actual_model_forward_calls": 0,
               "bindings": [], "historical_128_alignment": {"status": "NOT_VERIFIED"}}
    try:
        candidate_path = candidate_path.resolve(strict=True)
        root = candidate_path.parent.parent
        if candidate_path.parent != root / "configs":
            raise ValueError("Candidate must reside in the selected checkout's configs directory")
        receipt["candidate_path"] = str(candidate_path)
        receipt["source_commit_at_preflight"] = git_state(root)["head"]
        receipt["runtime_provenance"] = capture_runtime_provenance(root)
        candidate = load_candidate(candidate_path)
        receipt["candidate_body_tokens"] = candidate["chunking"]["candidate_body_tokens"]
        receipt["candidate_config_sha256"] = file_sha256(candidate_path)
        receipt["bindings"].append(_binding(candidate_path))
        contract_path = root / "docs" / "SMALL_CHUNK_METRICS.md"
        receipt["metric_contract_sha256"] = file_sha256(contract_path)
        receipt["bindings"].append(_binding(contract_path))
        config_path = root / candidate["model_config"]["path"]
        if file_sha256(config_path) != candidate["model_config"]["sha256"]:
            raise ValueError("Pinned historical smoke config hash mismatch")
        config = load_config(config_path)
        receipt["reference_config_sha256"] = config.source_sha256
        receipt["bindings"].append(_binding(config_path))
        if (config.body_tokens != 128 or config.overlap_tokens != 0 or
                config.normalize_chunks is not True or config.normalize_documents is not True or
                [c.ticker for c in config.companies] != candidate["input"]["company_order"]):
            raise ValueError("Candidate conflicts with the historical 128-token reference settings")
        corpus_path = corpus_path.resolve(strict=True)
        if file_sha256(corpus_path) != candidate["input"]["corpus_index_sha256"]:
            raise ValueError("M1 corpus index hash differs from candidate")
        receipt["bindings"].append(_binding(corpus_path))
        documents, audit = validate_corpus(config, corpus_path)
        receipt["input_audit"] = [{"ticker": row["ticker"], "status": row["status"],
                                   "accession": row.get("accession"),
                                   "item1_sha256": row.get("item1_sha256"),
                                   "reason": row.get("reason")} for row in audit["entries"]]
        if audit["valid_count"] != len(candidate["input"]["company_order"]):
            raise ValueError("Exact five-company M1 corpus validation did not pass")
        for doc in documents:
            for file in doc["files"].values():
                receipt["bindings"].append(_binding(Path(file["path"])))
        model = inspect_model_files(config)
        receipt["model"] = model
        for file in model["files"].values():
            receipt["bindings"].append(_binding(Path(file["path"])))
        tokenizer, special_count = load_local_tokenizer(config.model_cache / "tokenizer.json")
        receipt["special_tokens_per_chunk"] = special_count
        receipt["tokenizer_default_truncation"] = "DISABLED_BEFORE_FULL_TEXT_TOKENIZATION"
        receipt["tokenizer_default_padding"] = "DISABLED_BEFORE_FULL_TEXT_TOKENIZATION"
        receipt["normalization"] = {"chunk_vectors": "L2 before pooling",
                                    "company_vector": "L2 after pooling"}
        length_rows = []
        old_compare_rows = []
        total_chunks = 0
        for doc in documents:
            row = {"ticker": doc["ticker"], "cik": doc["cik"], "accession": doc["accession"],
                   "form": "10-K", "section": "ITEM_1_BUSINESS",
                   "item1_sha256": doc["item1_sha256"], "lengths": {}}
            for length in receipt["candidate_body_tokens"]:
                chunks = chunk_text(tokenizer, doc["item1_text"], length, special_count,
                                    model["max_input_tokens"])
                evidence, stats = chunk_evidence(
                    {"id": f"{doc['ticker']}|{doc['accession']}", "ticker": doc["ticker"],
                     "text": doc["item1_text"], "text_sha256": doc["item1_sha256"]},
                    chunks, special_count, model["max_input_tokens"])
                if stats["covered_token_end"] != sum(len(chunk.token_ids) for chunk in chunks):
                    raise ModelError("Body-token coverage count mismatch")
                weights = pooling_weights(chunks)
                row["lengths"][str(length)] = {"body_tokens": stats["body_tokens"],
                                               "chunk_count": stats["chunk_count"],
                                               "tail_tokens": stats["tail_tokens"],
                                               "chunk_map_sha256": chunk_map_sha256(chunks),
                                               "coverage_check": stats["coverage_check"],
                                               "max_model_input_tokens": max(x["model_input_tokens_including_specials"] for x in evidence),
                                               "pooling": weights}
                total_chunks += len(chunks)
                if length == 128:
                    old_compare_rows.extend({"ticker": doc["ticker"], "item1_sha256": doc["item1_sha256"],
                                             "chunk_index": index, "token_start": chunk.token_start,
                                             "token_end_exclusive": chunk.token_end_exclusive,
                                             "char_start": chunk.char_start,
                                             "char_end_exclusive": chunk.char_end_exclusive,
                                             "source_span_sha256": chunk.source_span_sha256,
                                             "model_input_tokens_including_specials": len(chunk.token_ids) + special_count,
                                             "body_token_ids": list(chunk.token_ids)}
                                            for index, chunk in enumerate(chunks))
            row["body_tokens"] = row["lengths"]["128"]["body_tokens"]
            if len({value["body_tokens"] for value in row["lengths"].values()}) != 1:
                raise ModelError("Whole-document token count changed across chunk lengths")
            length_rows.append(row)
        receipt["documents"] = length_rows
        if historical_map is not None:
            historical_map = historical_map.resolve(strict=True)
            receipt["bindings"].append(_binding(historical_map))
            receipt["historical_128_alignment"] = compare_historical_map(historical_map, old_compare_rows)
            if receipt["historical_128_alignment"]["status"] != "PASS":
                raise ValueError("Reference 128-token chunk map differs from historical M2/M3 output")
        receipt["planned_counts"] = {"candidate_lengths": len(receipt["candidate_body_tokens"]),
                                     "pooling_rules": len(candidate["pooling"]),
                                     "document_length_jobs": len(documents) * len(receipt["candidate_body_tokens"]),
                                     "chunk_vectors": total_chunks,
                                     "company_vectors": len(documents) * len(receipt["candidate_body_tokens"]) * len(candidate["pooling"]),
                                     "reuse": "One future chunk encoding per company and length; reuse its vectors for both pooling rules"}
        code_files = sorted((root / "src" / "finembed").glob("*.py"))
        receipt["code_inventory"] = [str(path.resolve()) for path in code_files]
        receipt["bindings"].extend(_binding(path) for path in code_files)
        receipt["status"] = "PASS_TOKENIZATION_ONLY"
    except (OSError, ValueError, KeyError, TypeError, ModelError, ImportError,
            RuntimeProvenanceError, json.JSONDecodeError) as exc:
        receipt["status"] = "BLOCKED" if isinstance(exc, (FileNotFoundError, ImportError)) else "FAILED"
        receipt["reason"] = f"{type(exc).__name__}: {exc}"
    return receipt


def create_preflight(candidate_path: Path, corpus_path: Path, output: Path,
                     historical_map: Path | None = None) -> dict:
    root = candidate_path.resolve(strict=True).parent.parent
    output = output.resolve()
    artifacts = (root / "artifacts").resolve()
    if not output.is_relative_to(artifacts):
        raise ValueError("Preflight output must be inside the ignored artifacts directory")
    output.mkdir(parents=True, exist_ok=False)
    receipt = build_preflight(candidate_path, corpus_path, historical_map)
    write_json(output / "receipt.json", receipt)
    _write_summary(output / "summary.md", receipt)
    return receipt


def verify_receipt(path: Path) -> dict:
    receipt = json.loads(path.read_text(encoding="utf-8"))
    if receipt.get("kind") != "TOKENIZATION_PREFLIGHT" or receipt.get("status") != "PASS_TOKENIZATION_ONLY":
        raise ValueError("Only a passing tokenization receipt can be reused")
    if "candidate_path" not in receipt:
        raise ValueError("Receipt predates the explicit source-root and semantic binding checks; regenerate")
    candidate_path = Path(receipt["candidate_path"]).resolve(strict=True)
    root = candidate_path.parent.parent
    if candidate_path.parent != root / "configs" or not any(
            Path(bound["path"]) == candidate_path and
            bound["sha256"] == receipt.get("candidate_config_sha256")
            for bound in receipt["bindings"]):
        raise ValueError("Receipt candidate path is not bound to the selected checkout")
    current_code = [str(p.resolve()) for p in sorted((root / "src" / "finembed").glob("*.py"))]
    if current_code != receipt.get("code_inventory"):
        raise ValueError("Source-code inventory changed after preflight")
    verify_runtime_provenance(root, receipt.get("runtime_provenance"))
    changed = []
    for bound in receipt["bindings"]:
        file = Path(bound["path"])
        if not file.is_file() or file_sha256(file) != bound["sha256"]:
            changed.append(bound["path"])
    if changed:
        raise ValueError(f"Bound input/config/code changed: {len(changed)} file(s)")
    candidate = load_candidate(candidate_path)
    lengths = candidate["chunking"]["candidate_body_tokens"]
    documents = receipt.get("documents")
    if (receipt.get("candidate_body_tokens") != lengths or
            not isinstance(documents, list) or
            [row.get("ticker") for row in documents] != candidate["input"]["company_order"]):
        raise ValueError("Receipt company grid or candidate lengths changed")
    counts = receipt.get("planned_counts", {})
    if (counts.get("document_length_jobs") != len(documents) * len(lengths) or
            counts.get("company_vectors") != len(documents) * len(lengths) * len(candidate["pooling"]) or
            counts.get("chunk_vectors") != sum(
                row["lengths"][str(length)]["chunk_count"] for row in documents for length in lengths)):
        raise ValueError("Receipt planned counts differ from tokenization rows")
    for row in documents:
        for length in lengths:
            entry = row["lengths"][str(length)]
            if (entry.get("coverage_check") != "PASS" or
                    entry.get("chunk_count", 0) <= 0 or
                    entry.get("tail_tokens", 0) <= 0 or
                    entry.get("body_tokens", 0) <= 0 or
                    not isinstance(entry.get("chunk_map_sha256"), str) or
                    len(entry["chunk_map_sha256"]) != 64):
                raise ValueError("Receipt tokenization row is incomplete")
    historical = receipt.get("historical_128_alignment", {})
    if historical.get("status") == "PASS":
        map_sha = historical.get("historical_map_sha256")
        if (not isinstance(map_sha, str) or
                len([bound for bound in receipt["bindings"] if
                     bound.get("sha256") == map_sha and
                     Path(bound["path"]).name == "chunk_map.jsonl"]) != 1):
            raise ValueError("Passing historical alignment lacks a unique bound map")
    return {"status": "PASS_BOUND_FILES_UNCHANGED", "bound_file_count": len(receipt["bindings"]),
            "real_inference_status": "NOT_RUN", "researcher_accepted": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="Create a new tokenizer-only receipt; no encoder session")
    plan.add_argument("--candidate", type=Path, required=True)
    plan.add_argument("--corpus", type=Path, required=True)
    plan.add_argument("--historical-map", type=Path)
    plan.add_argument("--output", type=Path, required=True)
    verify = sub.add_parser("verify", help="Refuse reuse after any bound file changes")
    verify.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            result = create_preflight(args.candidate, args.corpus, args.output, args.historical_map)
            print(json.dumps({"status": result["status"], "receipt": str(args.output / "receipt.json"),
                              "summary": str(args.output / "summary.md"),
                              "real_inference_status": "NOT_RUN"}))
            return 0 if result["status"] == "PASS_TOKENIZATION_ONLY" else 2
        print(json.dumps(verify_receipt(args.receipt)))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeProvenanceError) as exc:
        print(f"PREFLIGHT_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
