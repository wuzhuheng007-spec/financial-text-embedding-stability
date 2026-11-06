"""Tokenizer-only preflight for the proposed 128-base boundary-shift probe.

This module never constructs an Encoder, an ONNX session, or model vectors.
Each source document is tokenized once and its complete body-token IDs are
sliced under each declared shift without clipping, splitting or overlap.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sys

from .analysis import AnalysisError, validate_corpus
from .config import load_config
from .m6 import chunk_evidence
from .model import Chunk, ModelError
from .run import git_state, sha256, write_json
from .sensitivity_preflight import (compare_historical_map, file_sha256,
                                    inspect_model_files, load_local_tokenizer,
                                    pooling_weights, chunk_map_sha256)
from .small_chunk_provenance import (RuntimeProvenanceError,
                                     capture_runtime_provenance,
                                     verify_runtime_provenance)


COMPANIES = ("AAPL", "MSFT", "AMZN", "GOOGL", "META")
SHIFTS = (-10, -5, 0, 5, 10)
POOLING = ("equal_chunks", "body_token_weighted")
MODEL_CONFIG = "configs/smoke.yaml"
METRIC_CONTRACT = "docs/SMALL_CHUNK_METRICS.md"
S3_METRIC_CONTRACT = "docs/S3_BOUNDARY_SHIFT_METRICS.md"
NORMALIZATION_ORDER = (
    "L2-normalize each chunk, pool by declared weights, then L2-normalize "
    "each company vector"
)
VERSION_FIELDS = frozenset((
    "ticker", "cik", "accession", "form", "section", "item1_sha256"
))
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _require_sha256(value: object, label: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")


def load_boundary_candidate(path: Path) -> dict:
    """Validate the separate S3 proposal without changing the round-4 plan."""
    candidate = json.loads(Path(path).read_text(encoding="utf-8"))
    if (candidate.get("schema_version") != 1 or
            candidate.get("kind") != "boundary_shift_engineering_probe_candidate" or
            candidate.get("status") != "PROPOSED" or
            candidate.get("researcher_accepted") is not False or
            candidate.get("real_inference_status") != "NOT_RUN"):
        raise ValueError("S3 candidate must be an unaccepted, not-run proposal")
    source = candidate.get("input")
    if not isinstance(source, dict) or (source.get("form"), source.get("section")) != (
            "10-K", "ITEM_1_BUSINESS") or source.get("company_order") != list(COMPANIES):
        raise ValueError("S3 requires the complete original five-company Item 1 stratum")
    _require_sha256(source.get("corpus_index_sha256"), "Corpus index")
    versions = source.get("versions")
    if not isinstance(versions, list) or len(versions) != len(COMPANIES):
        raise ValueError("S3 requires five exact ordered input versions")
    for ticker, version in zip(COMPANIES, versions):
        if (not isinstance(version, dict) or set(version) != VERSION_FIELDS or
                version["ticker"] != ticker or version["form"] != "10-K" or
                version["section"] != "ITEM_1_BUSINESS" or
                not isinstance(version["cik"], str) or
                re.fullmatch(r"[0-9]{10}", version["cik"]) is None or
                not isinstance(version["accession"], str) or
                re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", version["accession"]) is None):
            raise ValueError(f"S3 input version is invalid for {ticker}")
        _require_sha256(version["item1_sha256"], f"{ticker} Item 1")
    model = candidate.get("model_config")
    if not isinstance(model, dict) or model.get("path") != MODEL_CONFIG:
        raise ValueError("S3 requires the pinned historical smoke config")
    _require_sha256(model.get("sha256"), "Model config")
    if candidate.get("provider") != "CPUExecutionProvider":
        raise ValueError("S3 requires CPUExecutionProvider")
    normalization = candidate.get("normalization")
    if (not isinstance(normalization, dict) or
            normalization.get("normalize_chunks") is not True or
            normalization.get("normalize_documents") is not True or
            normalization.get("order") != NORMALIZATION_ORDER):
        raise ValueError("S3 normalization order differs from the pinned implementation")
    chunking = candidate.get("chunking")
    if (not isinstance(chunking, dict) or
            chunking.get("reference_body_tokens") != 128 or
            chunking.get("boundary_shifts") != list(SHIFTS) or
            chunking.get("overlap_tokens") != 0 or
            chunking.get("rule") != "shift_all_internal_cuts"):
        raise ValueError("S3 boundary-shift grid or slicing rule changed")
    if (candidate.get("pooling") != list(POOLING) or
            any(candidate.get(key) is not False for key in
                ("query_rewriting", "generation", "training"))):
        raise ValueError("S3 requires only the two existing pooling rules")
    metric = candidate.get("metric_family_reference")
    if not isinstance(metric, dict) or metric.get("path") != METRIC_CONTRACT:
        raise ValueError("S3 metric-family reference must be the unchanged contract")
    _require_sha256(metric.get("sha256"), "Metric-family contract")
    _require_sha256(candidate.get("historical_128_map_sha256"), "Historical 128 map")
    return candidate


def boundary_shift_chunks(ids, offsets, text: str, body_tokens: int,
                          delta: int, special_count: int,
                          max_input_tokens: int) -> list[Chunk]:
    """Slice one complete tokenization using 0, L*j+delta, ..., T cuts."""
    if (type(body_tokens) is not int or body_tokens <= 0 or
            type(delta) is not int or type(special_count) is not int or
            special_count < 0 or type(max_input_tokens) is not int or
            max_input_tokens <= 0):
        raise ModelError("Boundary length, shift and model budget must be valid integers")
    if (not isinstance(ids, (list, tuple)) or not ids or
            not isinstance(offsets, (list, tuple)) or len(offsets) != len(ids)):
        raise ModelError("Complete body token IDs and matching offsets are required")
    if any(type(token) is not int or token < 0 for token in ids):
        raise ModelError("Body token IDs are invalid")
    if any(not isinstance(pair, (tuple, list)) or len(pair) != 2 or
           type(pair[0]) is not int or type(pair[1]) is not int or
           not 0 <= pair[0] <= pair[1] <= len(text) for pair in offsets):
        raise ModelError("Tokenizer source offsets are invalid")
    if max(end for _, end in offsets) < len(text.rstrip()) - 2:
        raise ModelError("Tokenizer appears to have truncated the document")
    total = len(ids)
    count = (total + body_tokens - 1) // body_tokens
    cuts = [0, *(body_tokens * index + delta for index in range(1, count)), total]
    if any(not 0 <= start < end <= total for start, end in zip(cuts, cuts[1:])):
        raise ModelError("Shifted cuts are empty, out of bounds or not strictly increasing")
    chunks = []
    for start, end in zip(cuts, cuts[1:]):
        length = end - start
        if length + special_count > max_input_tokens:
            raise ModelError("Shifted body chunk plus specials exceeds model input budget")
        spans = [pair for pair in offsets[start:end] if pair[1] > pair[0]]
        if not spans:
            raise ModelError("Shifted chunk has no nonempty source offsets")
        char_start, char_end = spans[0][0], spans[-1][1]
        if not 0 <= char_start < char_end <= len(text):
            raise ModelError("Shifted source span is invalid")
        chunks.append(Chunk(start, end, char_start, char_end, tuple(ids[start:end]),
                            sha256(text[char_start:char_end].encode("utf-8"))))
    if (len(chunks) != count or chunks[0].token_start != 0 or
            chunks[-1].token_end_exclusive != total or
            tuple(token for chunk in chunks for token in chunk.token_ids) != tuple(ids)):
        raise ModelError("Shifted chunks do not cover each body token exactly once")
    return chunks


def _binding(path: Path) -> dict:
    return {"path": str(path.resolve(strict=True)), "sha256": file_sha256(path)}


def _ensure_imported_module(root: Path) -> None:
    expected = (root / "src/finembed/boundary_shift_preflight.py").resolve(strict=True)
    if Path(__file__).resolve(strict=True) != expected:
        raise RuntimeProvenanceError("Imported S3 preflight module comes from another checkout")


def _historical_rows(doc: dict, chunks: list[Chunk], special_count: int) -> list[dict]:
    return [{"ticker": doc["ticker"], "item1_sha256": doc["item1_sha256"],
             "chunk_index": index, "token_start": chunk.token_start,
             "token_end_exclusive": chunk.token_end_exclusive,
             "char_start": chunk.char_start,
             "char_end_exclusive": chunk.char_end_exclusive,
             "source_span_sha256": chunk.source_span_sha256,
             "model_input_tokens_including_specials": len(chunk.token_ids) + special_count,
             "body_token_ids": list(chunk.token_ids)}
            for index, chunk in enumerate(chunks)]


def build_preflight(candidate_path: Path, corpus_path: Path,
                    historical_map: Path) -> dict:
    """Recompute every S3 arm using only local tokenizer and source files."""
    receipt = {"schema_version": 1, "kind": "BOUNDARY_SHIFT_TOKENIZATION_PREFLIGHT",
               "status": "RUNNING", "created_utc": datetime.now(timezone.utc).isoformat(),
               "candidate_status": "PROPOSED", "researcher_accepted": False,
               "real_inference_status": "NOT_RUN", "encoder_session_created": False,
               "actual_model_forward_calls": 0, "actual_model_vectors": 0,
               "generation_status": "NOT_RUN", "financial_experiment_status": "NOT_RUN",
               "bindings": [], "historical_128_alignment": {"status": "NOT_VERIFIED"}}
    try:
        candidate_path = candidate_path.resolve(strict=True)
        root = candidate_path.parent.parent
        if candidate_path.parent != root / "configs":
            raise ValueError("S3 candidate must be in the selected checkout's configs directory")
        _ensure_imported_module(root)
        receipt["candidate_path"] = str(candidate_path)
        receipt["source_commit_at_preflight"] = git_state(root)["head"]
        receipt["runtime_provenance"] = capture_runtime_provenance(root)
        candidate = load_boundary_candidate(candidate_path)
        receipt["candidate_sha256"] = file_sha256(candidate_path)
        receipt["boundary_shifts"] = list(SHIFTS)
        receipt["bindings"].append(_binding(candidate_path))
        metric_path = root / METRIC_CONTRACT
        if file_sha256(metric_path) != candidate["metric_family_reference"]["sha256"]:
            raise ValueError("Referenced metric-family contract hash changed")
        receipt["metric_contract_sha256"] = file_sha256(metric_path)
        receipt["bindings"].append(_binding(metric_path))
        s3_metric_path = root / S3_METRIC_CONTRACT
        receipt["s3_metric_contract_sha256"] = file_sha256(s3_metric_path)
        receipt["bindings"].append(_binding(s3_metric_path))
        config_path = root / MODEL_CONFIG
        if file_sha256(config_path) != candidate["model_config"]["sha256"]:
            raise ValueError("Pinned smoke config hash changed")
        config = load_config(config_path)
        if (config.body_tokens != 128 or config.overlap_tokens != 0 or
                config.normalize_chunks is not True or
                config.normalize_documents is not True or
                config.provider != candidate["provider"] or
                [company.ticker for company in config.companies] != list(COMPANIES)):
            raise ValueError("S3 candidate differs from pinned model, provider or normalization")
        receipt["reference_config_sha256"] = config.source_sha256
        receipt["bindings"].append(_binding(config_path))
        corpus_path = corpus_path.resolve(strict=True)
        if file_sha256(corpus_path) != candidate["input"]["corpus_index_sha256"]:
            raise ValueError("M1 corpus index hash differs from S3 candidate")
        receipt["corpus_path"] = str(corpus_path)
        receipt["bindings"].append(_binding(corpus_path))
        documents, audit = validate_corpus(config, corpus_path)
        receipt["input_audit"] = [{"ticker": row["ticker"], "status": row["status"],
                                   "accession": row.get("accession"),
                                   "item1_sha256": row.get("item1_sha256"),
                                   "reason": row.get("reason")} for row in audit["entries"]]
        versions = [{"ticker": doc["ticker"], "cik": doc["cik"],
                     "accession": doc["accession"], "form": "10-K",
                     "section": "ITEM_1_BUSINESS", "item1_sha256": doc["item1_sha256"]}
                    for doc in documents]
        if audit["valid_count"] != len(COMPANIES) or versions != candidate["input"]["versions"]:
            raise ValueError("Validated exact M1 input versions differ from S3 candidate")
        receipt["input_versions"] = versions
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
        historical_map = historical_map.resolve(strict=True)
        if file_sha256(historical_map) != candidate["historical_128_map_sha256"]:
            raise ValueError("Historical base-128 map hash differs from S3 candidate")
        receipt["historical_map_path"] = str(historical_map)
        receipt["bindings"].append(_binding(historical_map))
        rows, reference_rows = [], []
        total_chunks = 0
        for doc in documents:
            text = doc["item1_text"]
            encoding = tokenizer.encode(text, add_special_tokens=False)
            ids, offsets = encoding.ids, encoding.offsets
            row = {"ticker": doc["ticker"], "cik": doc["cik"],
                   "accession": doc["accession"], "form": "10-K",
                   "section": "ITEM_1_BUSINESS", "item1_sha256": doc["item1_sha256"],
                   "body_tokens": len(ids), "shifts": {}}
            for delta in SHIFTS:
                chunks = boundary_shift_chunks(ids, offsets, text, 128, delta,
                                               special_count, model["max_input_tokens"])
                evidence, stats = chunk_evidence(
                    {"id": f"{doc['ticker']}|{doc['accession']}",
                     "ticker": doc["ticker"], "text": text,
                     "text_sha256": doc["item1_sha256"]}, chunks,
                    special_count, model["max_input_tokens"])
                lengths = [len(chunk.token_ids) for chunk in chunks]
                if (stats["body_tokens"] != len(ids) or len(chunks) != (len(ids) + 127) // 128 or
                        stats["coverage_check"] != "PASS" or
                        (len(chunks) > 1 and (lengths[0] != 128 + delta or
                         any(length != 128 for length in lengths[1:-1]) or
                         lengths[-1] != len(ids) - 128 * (len(chunks) - 1) - delta))):
                    raise ModelError("Shifted chunk lengths or coverage differ from the declared rule")
                weights = pooling_weights(chunks)
                if (weights["equal_chunks"]["tail_weight"] != 1 / len(chunks) or
                        not math.isclose(weights["body_token_weighted"]["tail_weight"],
                                         lengths[-1] / len(ids), rel_tol=0, abs_tol=1e-15)):
                    raise ModelError("Pooling weights differ from actual body-token lengths")
                row["shifts"][str(delta)] = {
                    "body_tokens": len(ids), "chunk_count": len(chunks),
                    "first_tokens": lengths[0], "interior_tokens": lengths[1:-1],
                    "tail_tokens": lengths[-1], "chunk_body_token_lengths": lengths,
                    "coverage_check": stats["coverage_check"],
                    "max_model_input_tokens": max(x["model_input_tokens_including_specials"] for x in evidence),
                    "model_input_limit": model["max_input_tokens"],
                    "chunk_map_sha256": chunk_map_sha256(chunks),
                    "pooling": weights,
                }
                total_chunks += len(chunks)
                if delta == 0:
                    reference_rows.extend(_historical_rows(doc, chunks, special_count))
            if len({arm["chunk_count"] for arm in row["shifts"].values()}) != 1:
                raise ModelError("Shifted chunk count changed within one company")
            rows.append(row)
        receipt["documents"] = rows
        receipt["historical_128_alignment"] = compare_historical_map(historical_map, reference_rows)
        if receipt["historical_128_alignment"]["status"] != "PASS":
            raise ValueError("Delta-zero map differs row-by-row from historical base-128 map")
        receipt["planned_counts"] = {
            "shift_settings": len(SHIFTS), "pooling_rules": len(POOLING),
            "document_shift_jobs": len(documents) * len(SHIFTS),
            "chunk_vectors": total_chunks,
            "company_vectors": len(documents) * len(SHIFTS) * len(POOLING),
            "all_counts_are_future_plans": True,
            "reuse": "One future chunk encoding per company and shift; both pooling rules reuse its chunk vectors",
        }
        code_files = sorted((root / "src/finembed").glob("*.py"))
        receipt["code_inventory"] = [str(path.resolve()) for path in code_files]
        receipt["bindings"].extend(_binding(path) for path in code_files)
        receipt["status"] = "PASS_TOKENIZATION_ONLY"
    except (OSError, ValueError, KeyError, TypeError, AttributeError, UnicodeError,
            AnalysisError, ModelError, ImportError, RuntimeProvenanceError,
            json.JSONDecodeError) as exc:
        receipt["status"] = "BLOCKED" if isinstance(exc, (FileNotFoundError, ImportError)) else "FAILED"
        receipt["reason"] = f"{type(exc).__name__}: {exc}"
    return receipt


def _summary(path: Path, receipt: dict) -> None:
    lines = ["# S3 boundary-shift tokenizer-only preflight", "",
             f"Status: **{receipt['status']}**. Candidate: **PROPOSED / NOT_RUN**.",
             "Encoder sessions, forward calls and model vectors: **0** in this preflight.", ""]
    if "reason" in receipt:
        lines.extend([f"Reason: {receipt['reason']}", ""])
    if receipt.get("documents"):
        lines.extend(["| Company | Accession | Body tokens | Chunks at each shift | First / tail body tokens by shift (-10, -5, 0, 5, 10) |",
                      "| --- | --- | ---: | ---: | --- |"])
        for row in receipt["documents"]:
            arms = [row["shifts"][str(delta)] for delta in SHIFTS]
            spans = ", ".join(f"{arm['first_tokens']}/{arm['tail_tokens']}" for arm in arms)
            lines.append(f"| {row['ticker']} | `{row['accession']}` | {row['body_tokens']} | {arms[0]['chunk_count']} | {spans} |")
        alignment = receipt["historical_128_alignment"]
        lines.extend(["", f"Delta-zero historical row check: **{alignment['status']}**; "
                      f"{alignment.get('preflight_rows', 'UNKNOWN')} rows.", ""])
    if receipt.get("planned_counts"):
        counts = receipt["planned_counts"]
        lines.extend([f"Future plans only: {counts['document_shift_jobs']} document-shift jobs, "
                      f"{counts['chunk_vectors']} chunk vectors and {counts['company_vectors']} company vectors.", ""])
    lines.append("No S3 model result, causal boundary effect, or financial outcome is established.\n")
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def create_preflight(candidate_path: Path, corpus_path: Path, historical_map: Path,
                     output: Path) -> dict:
    root = candidate_path.resolve(strict=True).parent.parent
    output = output.resolve()
    if not output.is_relative_to((root / "artifacts").resolve()):
        raise ValueError("S3 receipt must be saved under the checkout's ignored artifacts directory")
    output.mkdir(parents=True, exist_ok=False)
    receipt = build_preflight(candidate_path, corpus_path, historical_map)
    write_json(output / "receipt.json", receipt)
    _summary(output / "summary.md", receipt)
    return receipt


def verify_receipt(path: Path) -> dict:
    """Reject stale or edited conclusions after rehashing and retokenizing."""
    saved = json.loads(Path(path).read_text(encoding="utf-8"))
    if saved.get("kind") != "BOUNDARY_SHIFT_TOKENIZATION_PREFLIGHT" or saved.get("status") != "PASS_TOKENIZATION_ONLY":
        raise ValueError("Only a passing S3 tokenizer-only receipt can be reused")
    candidate_path = Path(saved["candidate_path"]).resolve(strict=True)
    root = candidate_path.parent.parent
    if candidate_path.parent != root / "configs":
        raise ValueError("S3 candidate source root is invalid")
    current_code = [str(path.resolve()) for path in sorted((root / "src/finembed").glob("*.py"))]
    if current_code != saved.get("code_inventory"):
        raise ValueError("S3 source-code inventory changed after preflight")
    verify_runtime_provenance(root, saved["runtime_provenance"])
    for bound in saved["bindings"]:
        source = Path(bound["path"])
        if not source.is_file() or file_sha256(source) != bound["sha256"]:
            raise ValueError("S3 bound input, model, candidate or code changed")
    fresh = build_preflight(candidate_path, Path(saved["corpus_path"]),
                            Path(saved["historical_map_path"]))
    if fresh.get("status") != "PASS_TOKENIZATION_ONLY":
        raise ValueError(f"Fresh S3 tokenization failed: {fresh.get('reason')}")
    for key in ("created_utc", "source_commit_at_preflight"):
        saved.pop(key, None)
        fresh.pop(key, None)
    if saved != fresh:
        raise ValueError("Saved S3 preflight conclusions differ from fresh tokenization")
    return {"status": "PASS_BOUND_FILES_AND_TOKENIZATION_UNCHANGED",
            "bound_file_count": len(saved["bindings"]),
            "encoder_session_created": False, "actual_model_forward_calls": 0,
            "real_inference_status": "NOT_RUN"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="Create tokenizer-only S3 receipt in a new ignored directory")
    plan.add_argument("--candidate", type=Path, required=True)
    plan.add_argument("--corpus", type=Path, required=True)
    plan.add_argument("--historical-map", type=Path, required=True)
    plan.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify", help="Rehash and recompute saved S3 tokenization")
    verify.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            result = create_preflight(args.candidate, args.corpus,
                                      args.historical_map, args.output)
            print(json.dumps({"status": result["status"],
                              "receipt": str(args.output / "receipt.json"),
                              "summary": str(args.output / "summary.md"),
                              "real_inference_status": "NOT_RUN"}))
            return 0 if result["status"] == "PASS_TOKENIZATION_ONLY" else 2
        print(json.dumps(verify_receipt(args.receipt)))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeProvenanceError) as exc:
        print(f"S3_PREFLIGHT_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
