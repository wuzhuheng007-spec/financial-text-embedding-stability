"""Version-gated small-chunk probe runner and saved-output verifier.

The CLI cannot construct an Encoder until source, runtime, preflight and a
separate researcher decision have all passed. Synthetic tests call the
internal execution function with a fake encoder and label their output.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import numpy as np

from .analysis import validate_corpus
from .config import load_config
from .m6 import chunk_evidence
from .model import Encoder, ModelError, aggregate
from .run import git_state, write_json
from .sensitivity_preflight import (build_preflight, chunk_map_sha256, file_sha256,
                                    inspect_model_files, load_candidate, verify_receipt)
from .small_chunk_decision import (canonical_json_hash, derive_input_manifest_sha256,
                                   validate_decision)
from .small_chunk_metrics import compute_small_chunk_metrics
from .small_chunk_provenance import (capture_runtime_provenance,
                                     verify_runtime_provenance)


METRIC_CONTRACT = "docs/SMALL_CHUNK_METRICS.md"
FAILURE_POLICY = "FULL_FIVE_COMPANIES_ALL_ARMS_NO_IMPUTATION"


class ProbeGateError(RuntimeError):
    """A proposed probe failed an execution gate before model construction."""


def _input_versions(receipt: dict) -> list[dict]:
    fields = ("ticker", "cik", "accession", "form", "section", "item1_sha256")
    rows = receipt.get("documents")
    if not isinstance(rows, list) or len(rows) != 5:
        raise ProbeGateError("Preflight lacks the complete five-company inventory")
    return [{field: row[field] for field in fields} for row in rows]


def _model_identity(receipt: dict, config) -> dict:
    model = receipt.get("model", {})
    files = model.get("files", {})
    if not isinstance(files, dict) or set(files) != {
            "onnx/model.onnx", "tokenizer.json", "config.json", "sentence_bert_config.json"}:
        raise ProbeGateError("Preflight model-file inventory is incomplete")
    return {"model_id": model["model_id"], "revision": model["revision"],
            "tokenizer_revision": model["tokenizer_revision"],
            "model_file_sha256": {name: files[name]["sha256"] for name in sorted(files)},
            "provider": config.provider, "reference_config_sha256": config.source_sha256}


def prepare_run(candidate_path: Path, corpus_path: Path, receipt_path: Path,
                decision_path: Path | None) -> dict:
    """Perform every disk/source/decision check before Encoder construction."""
    candidate_path = candidate_path.resolve(strict=True)
    root = candidate_path.parent.parent
    if candidate_path.parent != root / "configs":
        raise ProbeGateError("Candidate must reside in the selected checkout's configs directory")
    # The root is derived from the requested candidate, never the imported package.
    runtime = capture_runtime_provenance(root)
    candidate = load_candidate(candidate_path)
    receipt_path = receipt_path.resolve(strict=True)
    receipt_sha = file_sha256(receipt_path)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("status") != "PASS_TOKENIZATION_ONLY":
        raise ProbeGateError("A passing tokenizer-only preflight is required")
    verify_receipt(receipt_path)
    verify_runtime_provenance(root, receipt.get("runtime_provenance"),
                              required_provider=runtime["required_provider"])
    if receipt.get("candidate_config_sha256") != file_sha256(candidate_path):
        raise ProbeGateError("Preflight was made for another candidate version")
    if receipt.get("candidate_body_tokens") != candidate["chunking"]["candidate_body_tokens"]:
        raise ProbeGateError("Preflight grid differs from the candidate")
    if receipt.get("historical_128_alignment", {}).get("status") != "PASS":
        raise ProbeGateError("Historical 128-token alignment is not passing")
    historical_sha = receipt["historical_128_alignment"].get("historical_map_sha256")
    historical_paths = [Path(bound["path"]) for bound in receipt.get("bindings", [])
                        if bound.get("sha256") == historical_sha and
                        Path(bound["path"]).name == "chunk_map.jsonl"]
    if len(historical_paths) != 1:
        raise ProbeGateError("Passing historical map has no unique bound source file")
    # Bound file hashes alone cannot attest to editable fields inside a receipt.
    # Recompute every preflight conclusion with tokenizer-only code before Encoder.
    fresh = build_preflight(candidate_path, corpus_path, historical_paths[0])
    if fresh.get("status") != "PASS_TOKENIZATION_ONLY":
        raise ProbeGateError(f"Fresh tokenizer-only preflight failed: {fresh.get('reason')}")
    without_capture_metadata = lambda value: {key: item for key, item in value.items()
                                              if key not in {"created_utc", "source_commit_at_preflight"}}
    if canonical_json_hash(without_capture_metadata(receipt)) != canonical_json_hash(without_capture_metadata(fresh)):
        raise ProbeGateError("Saved preflight conclusions differ from fresh tokenization")
    contract_path = root / METRIC_CONTRACT
    contract_sha = file_sha256(contract_path)
    if not any(Path(b["path"]) == contract_path.resolve() and b["sha256"] == contract_sha
               for b in receipt.get("bindings", [])):
        raise ProbeGateError("Metric contract is not bound to the final preflight")
    config_path = root / candidate["model_config"]["path"]
    config = load_config(config_path)
    if (config.source_sha256 != candidate["model_config"]["sha256"] or
            config.provider != runtime["required_provider"] or
            config.normalize_chunks is not True or config.normalize_documents is not True or
            config.overlap_tokens != 0 or config.body_tokens != 128):
        raise ProbeGateError("Reference configuration differs from the proposed probe")
    model = inspect_model_files(config)
    if (_model_identity({"model": model}, config) != _model_identity(receipt, config) or
            receipt.get("reference_config_sha256") != config.source_sha256):
        raise ProbeGateError("Preflight model/config identity differs from current files")
    corpus_path = corpus_path.resolve(strict=True)
    if file_sha256(corpus_path) != candidate["input"]["corpus_index_sha256"]:
        raise ProbeGateError("M1 corpus index differs from the candidate")
    docs, audit = validate_corpus(config, corpus_path)
    if (audit.get("valid_count") != 5 or len(docs) != 5 or
            [doc["ticker"] for doc in docs] != candidate["input"]["company_order"]):
        raise ProbeGateError("The original five-company input stratum is incomplete")
    versions = _input_versions(receipt)
    current_versions = [{"ticker": doc["ticker"], "cik": doc["cik"],
                         "accession": doc["accession"], "form": "10-K",
                         "section": "ITEM_1_BUSINESS", "item1_sha256": doc["item1_sha256"]}
                        for doc in docs]
    if current_versions != versions:
        raise ProbeGateError("Current M1 input versions differ from preflight")
    input_manifest_sha = derive_input_manifest_sha256(
        candidate["input"]["corpus_index_sha256"], versions)
    decision = validate_decision(
        decision_path, candidate_path=candidate_path,
        input_manifest_sha256=input_manifest_sha, input_versions=versions,
        model_identity=_model_identity(receipt, config), metric_contract_path=contract_path)
    if decision["status"] != "ACCEPTED":
        raise ProbeGateError(f"Researcher decision gate: {decision['status']}: {decision['reason']}")
    return {"root": root, "candidate": candidate, "candidate_path": candidate_path,
            "corpus_path": corpus_path, "receipt_path": receipt_path,
            "receipt_sha256": receipt_sha, "receipt": receipt, "decision_path": decision_path,
            "decision": decision, "decision_sha256": file_sha256(decision_path),
            "metric_contract_sha256": contract_sha, "input_manifest_sha256": input_manifest_sha,
            "input_versions": versions, "model_identity": _model_identity(receipt, config),
            "runtime_provenance": runtime, "docs": docs, "config": config,
            "failure_policy": FAILURE_POLICY}


def _save_array(output: Path, relative_path: str, values) -> dict:
    path = output / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    array = np.asarray(values, dtype=np.float64)
    if not np.isfinite(array).all():
        raise ModelError("Nonfinite output vector")
    np.save(path, array, allow_pickle=False)
    return {"path": relative_path, "sha256": file_sha256(path),
            "shape": list(array.shape), "dtype": str(array.dtype)}


def _save_manifest(output: Path, manifest: dict) -> None:
    write_json(output / "manifest.json", manifest)
    (output / "manifest.sha256").write_text(
        file_sha256(output / "manifest.json") + "\n", encoding="ascii")


def _execute_prepared(prepared: dict, output: Path, *, encoder_factory=Encoder,
                      evidence_class: str = "REAL_MODEL") -> dict:
    """Execute a checked plan; injected encoders are confined to synthetic tests."""
    if ((evidence_class == "REAL_MODEL" and encoder_factory is not Encoder) or
            (evidence_class == "SYNTHETIC_TEST_ONLY" and encoder_factory is Encoder) or
            evidence_class not in {"REAL_MODEL", "SYNTHETIC_TEST_ONLY"}):
        raise ValueError("Encoder factory and evidence class disagree")
    output = output.resolve()
    if evidence_class == "REAL_MODEL" and not output.is_relative_to(
            (prepared["root"] / "artifacts").resolve()):
        raise ProbeGateError("Real output must stay in the ignored artifacts directory")
    candidate = prepared["candidate"]
    lengths = candidate["chunking"]["candidate_body_tokens"]
    ids = candidate["input"]["company_order"]
    if ([doc["ticker"] for doc in prepared["docs"]] != ids or len(ids) != 5):
        raise ProbeGateError("Complete ordered five-company input is required")
    output.mkdir(parents=True, exist_ok=False)
    manifest = {"schema_version": 1, "kind": "SMALL_CHUNK_PROBE_RUN",
                "status": "RUNNING", "evidence_class": evidence_class,
                "started_utc": datetime.now(timezone.utc).isoformat(),
                "candidate_path": str(prepared["candidate_path"]),
                "candidate_sha256": file_sha256(prepared["candidate_path"]),
                "corpus_index_path": str(prepared["corpus_path"]),
                "input_manifest_sha256": prepared["input_manifest_sha256"],
                "input_versions": prepared["input_versions"],
                "model_identity": prepared["model_identity"],
                "metric_contract_sha256": prepared["metric_contract_sha256"],
                "receipt_path": str(prepared["receipt_path"]),
                "receipt_sha256": prepared["receipt_sha256"],
                "decision_path": str(prepared["decision_path"]),
                "decision_sha256": prepared["decision_sha256"],
                "decision_validation": prepared["decision"],
                "failure_policy": FAILURE_POLICY,
                "runtime_provenance": prepared["runtime_provenance"],
                "source_commit_at_preflight": prepared["receipt"].get("source_commit_at_preflight"),
                "git_at_run": git_state(prepared["root"]),
                "batch_size": prepared["config"].batch_size,
                "logical_job_count_planned": len(ids) * len(lengths),
                "chunk_vector_count_planned": prepared["receipt"]["planned_counts"]["chunk_vectors"],
                "company_vector_count_planned": len(ids) * len(lengths) * 2,
                "logical_jobs_completed": 0, "chunk_vectors_completed": 0,
                "company_vectors_completed": 0, "actual_batch_call_attempts": 0,
                "encoder_construction_attempts": 0, "real_encoder_sessions_created": 0,
                "jobs": [], "failures": [], "metric_file": None}
    _save_manifest(output, manifest)
    encoder = None
    try:
        manifest["encoder_construction_attempts"] = 1
        encoder = encoder_factory(prepared["config"])
        if evidence_class == "REAL_MODEL":
            manifest["real_encoder_sessions_created"] = 1
        for length in lengths:
            for doc in prepared["docs"]:
                ticker = doc["ticker"]
                job = {"ticker": ticker, "accession": doc["accession"],
                       "item1_sha256": doc["item1_sha256"], "body_tokens_per_chunk": length,
                       "status": "RUNNING", "files": {}}
                manifest["jobs"].append(job)
                try:
                    chunks = encoder.chunks(doc["item1_text"], length)
                    evidence, stats = chunk_evidence(
                        {"id": f"{ticker}|{doc['accession']}", "ticker": ticker,
                         "text": doc["item1_text"], "text_sha256": doc["item1_sha256"]},
                        chunks, encoder.special_count,
                        encoder.identity["max_input_tokens"])
                    expected = prepared["receipt"]["documents"][ids.index(ticker)]["lengths"][str(length)]
                    if (stats["body_tokens"] != expected["body_tokens"] or
                            stats["chunk_count"] != expected["chunk_count"] or
                            stats["tail_tokens"] != expected["tail_tokens"] or
                            chunk_map_sha256(chunks) != expected["chunk_map_sha256"] or
                            stats["coverage_check"] != "PASS" or
                            max(x["model_input_tokens_including_specials"] for x in evidence) !=
                            expected["max_model_input_tokens"]):
                        raise ModelError("Actual chunk map differs from tokenizer-only preflight")
                    batch_before = encoder.forward_batch_call_attempts
                    chunk_vectors = np.asarray(encoder.embed_chunks(chunks), dtype=np.float64)
                    expected_batches = (len(chunks) + prepared["config"].batch_size - 1) // prepared["config"].batch_size
                    if encoder.forward_batch_call_attempts - batch_before != expected_batches:
                        raise ModelError("Actual batch-call count differs from encoded chunk batches")
                    if (chunk_vectors.ndim != 2 or chunk_vectors.shape[0] != len(chunks) or
                            chunk_vectors.shape[1] == 0 or not np.isfinite(chunk_vectors).all() or
                            (evidence_class == "REAL_MODEL" and chunk_vectors.shape[1] != 384)):
                        raise ModelError("Encoder returned invalid chunk vectors")
                    relative = f"vectors/{length}/{ticker}"
                    job["files"]["chunks"] = _save_array(output, relative + "_chunks.npy", chunk_vectors)
                    job["chunk_body_token_lengths"] = [len(chunk.token_ids) for chunk in chunks]
                    job["chunk_count"] = len(chunks)
                    manifest["chunk_vectors_completed"] += len(chunks)
                    for pooling in candidate["pooling"]:
                        weights = (None if pooling == "equal_chunks"
                                   else job["chunk_body_token_lengths"])
                        company_vector = aggregate(chunk_vectors, True, True, weights=weights)
                        job["files"][pooling] = _save_array(
                            output, relative + f"_{pooling}.npy", company_vector)
                        manifest["company_vectors_completed"] += 1
                    job["status"] = "COMPLETE"
                    manifest["logical_jobs_completed"] += 1
                except Exception as exc:
                    job["status"] = "FAILED"
                    job["reason"] = f"{type(exc).__name__}: {exc}"
                    manifest["failures"].append({"ticker": ticker, "length": length,
                                                 "reason": job["reason"]})
                finally:
                    manifest["actual_batch_call_attempts"] = getattr(
                        encoder, "forward_batch_call_attempts", None)
                    _save_manifest(output, manifest)
        if (not manifest["failures"] and
                type(manifest["actual_batch_call_attempts"]) is int and
                manifest["actual_batch_call_attempts"] >= 0 and
                manifest["logical_jobs_completed"] == manifest["logical_job_count_planned"] and
                manifest["chunk_vectors_completed"] == manifest["chunk_vector_count_planned"] and
                manifest["company_vectors_completed"] == manifest["company_vector_count_planned"]):
            vectors = {}
            for length in lengths:
                for pooling in candidate["pooling"]:
                    rows = [np.load(output / next(job for job in manifest["jobs"]
                            if job["ticker"] == ticker and job["body_tokens_per_chunk"] == length)
                            ["files"][pooling]["path"], allow_pickle=False) for ticker in ids]
                    vectors[(length, pooling)] = np.vstack(rows)
            metrics = compute_small_chunk_metrics(ids, vectors, lengths)
            metrics["inference_provenance"] = evidence_class
            write_json(output / "metrics.json", metrics)
            manifest["metric_file"] = {"path": "metrics.json",
                                       "sha256": file_sha256(output / "metrics.json")}
            manifest["status"] = "COMPLETE"
        else:
            if not manifest["failures"]:
                manifest["failures"].append({"stage": "completion_accounting",
                    "reason": "Completed job, vector or batch counts differ from the declared grid"})
            manifest["status"] = "PARTIAL_FAILURE"
    except Exception as exc:
        if encoder is None and evidence_class == "REAL_MODEL" and manifest["encoder_construction_attempts"]:
            manifest["real_encoder_sessions_created"] = None
        manifest["failures"].append({"stage": "encoder_initialization_or_analysis",
                                     "reason": f"{type(exc).__name__}: {exc}"})
        manifest["status"] = "PARTIAL_FAILURE"
    manifest["ended_utc"] = datetime.now(timezone.utc).isoformat()
    _save_manifest(output, manifest)
    return manifest


def run_candidate(candidate_path: Path, corpus_path: Path, receipt_path: Path,
                  decision_path: Path | None, output: Path) -> dict:
    """Public inference entry; no injectable encoder or boolean acceptance flag."""
    prepared = prepare_run(candidate_path, corpus_path, receipt_path, decision_path)
    return _execute_prepared(prepared, output)


def verify_saved_output(output: Path) -> dict:
    """Rehash retained arrays and recompute pooling and complete-grid metrics."""
    output = output.resolve(strict=True)
    manifest_path = output / "manifest.json"
    if file_sha256(manifest_path) != (output / "manifest.sha256").read_text(encoding="ascii").strip():
        raise ValueError("Saved manifest hash differs from its sidecar")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("kind") != "SMALL_CHUNK_PROBE_RUN" or manifest.get("status") not in {
            "COMPLETE", "PARTIAL_FAILURE"}:
        raise ValueError("Saved output has no finalized small-chunk manifest")
    ids = [row["ticker"] for row in manifest["input_versions"]]
    lengths = sorted({job["body_tokens_per_chunk"] for job in manifest["jobs"]})
    prepared = None
    if manifest["evidence_class"] == "REAL_MODEL":
        prepared = prepare_run(Path(manifest["candidate_path"]),
                               Path(manifest["corpus_index_path"]),
                               Path(manifest["receipt_path"]), Path(manifest["decision_path"]))
        for key, expected in (("candidate_sha256", file_sha256(prepared["candidate_path"])),
                              ("receipt_sha256", prepared["receipt_sha256"]),
                              ("decision_sha256", prepared["decision_sha256"]),
                              ("metric_contract_sha256", prepared["metric_contract_sha256"]),
                              ("input_manifest_sha256", prepared["input_manifest_sha256"]),
                              ("input_versions", prepared["input_versions"]),
                              ("model_identity", prepared["model_identity"]),
                              ("runtime_provenance", prepared["runtime_provenance"]),
                              ("decision_validation", prepared["decision"]),
                              ("source_commit_at_preflight", prepared["receipt"].get("source_commit_at_preflight")),
                              ("failure_policy", FAILURE_POLICY),
                              ("batch_size", prepared["config"].batch_size),
                              ("logical_job_count_planned", len(ids) * len(prepared["candidate"]["chunking"]["candidate_body_tokens"])),
                              ("chunk_vector_count_planned", prepared["receipt"]["planned_counts"]["chunk_vectors"]),
                              ("company_vector_count_planned", len(ids) * len(prepared["candidate"]["chunking"]["candidate_body_tokens"]) * 2)):
            if manifest[key] != expected:
                raise ValueError(f"Saved run binding changed: {key}")
    elif manifest["evidence_class"] != "SYNTHETIC_TEST_ONLY":
        raise ValueError("Unknown saved output evidence class")
    if type(manifest.get("batch_size")) is not int or manifest["batch_size"] <= 0:
        raise ValueError("Saved batch size is invalid")
    if prepared is not None:
        lengths = prepared["candidate"]["chunking"]["candidate_body_tokens"]
    if manifest["failure_policy"] != FAILURE_POLICY:
        raise ValueError("Saved failure policy differs from executable policy")
    expected_jobs = {(ticker, length) for ticker in ids for length in lengths}
    job_keys = [(job["ticker"], job["body_tokens_per_chunk"]) for job in manifest["jobs"]]
    if len(set(job_keys)) != len(job_keys) or not set(job_keys).issubset(expected_jobs):
        raise ValueError("Saved job grid has duplicate or unexpected cells")
    if manifest["logical_jobs_completed"] != sum(job["status"] == "COMPLETE" for job in manifest["jobs"]):
        raise ValueError("Saved completed-job count differs from job records")
    if manifest["chunk_vectors_completed"] != sum(
            job.get("chunk_count", 0) for job in manifest["jobs"] if "chunks" in job["files"]):
        raise ValueError("Saved chunk-vector count differs from job records")
    if manifest["company_vectors_completed"] != sum(
            int(pool in job["files"]) for job in manifest["jobs"]
            for pool in ("equal_chunks", "body_token_weighted")):
        raise ValueError("Saved company-vector count differs from job records")
    for job in manifest["jobs"]:
        key = (job["ticker"], job["body_tokens_per_chunk"])
        identity = next((row for row in manifest["input_versions"] if row["ticker"] == job["ticker"]), None)
        if (identity is None or job["accession"] != identity["accession"] or
                job["item1_sha256"] != identity["item1_sha256"] or
                job["status"] not in {"COMPLETE", "FAILED"}):
            raise ValueError("Saved job identity or status is invalid")
        expected_names = {"chunks", "equal_chunks", "body_token_weighted"}
        files = job["files"]
        if (not isinstance(files, dict) or not set(files).issubset(expected_names) or
                (job["status"] == "COMPLETE" and set(files) != expected_names)):
            raise ValueError("Saved job has missing or unexpected vector files")
        stem = f"vectors/{key[1]}/{key[0]}"
        expected_paths = {"chunks": stem + "_chunks.npy",
                          "equal_chunks": stem + "_equal_chunks.npy",
                          "body_token_weighted": stem + "_body_token_weighted.npy"}
        arrays = {}
        for file_kind, record in files.items():
            if record["path"] != expected_paths[file_kind]:
                raise ValueError("Saved vector file is outside its declared job cell")
            path = (output / record["path"]).resolve()
            if not path.is_relative_to(output) or file_sha256(path) != record["sha256"]:
                raise ValueError("Saved output file hash/path mismatch")
            array = np.load(path, allow_pickle=False)
            if (array.dtype != np.float64 or record["dtype"] != "float64" or
                    list(array.shape) != record["shape"] or not np.isfinite(array).all()):
                raise ValueError("Saved vector shape, dtype or finite-value check failed")
            arrays[file_kind] = array
        if "chunks" in files:
            chunks = arrays["chunks"]
            body_lengths = job.get("chunk_body_token_lengths")
            if (chunks.ndim != 2 or chunks.shape[1] <= 0 or
                    (prepared is not None and chunks.shape[1] != 384) or
                    type(job.get("chunk_count")) is not int or
                    chunks.shape[0] != job["chunk_count"] or
                    not isinstance(body_lengths, list) or
                    len(body_lengths) != job["chunk_count"] or
                    any(type(n) is not int or not 0 < n <= key[1] for n in body_lengths)):
                raise ValueError("Saved chunk rows or body-token lengths are invalid")
            if prepared is not None:
                expected = prepared["receipt"]["documents"][ids.index(key[0])]["lengths"][str(key[1])]
                if (job["chunk_count"] != expected["chunk_count"] or
                        sum(body_lengths) != expected["body_tokens"] or
                        body_lengths[-1] != expected["tail_tokens"]):
                    raise ValueError("Saved chunk structure differs from final preflight")
            elif sum(body_lengths) <= 0:
                raise ValueError("Saved synthetic body-token lengths are empty")
        if job["status"] == "COMPLETE":
            for pooling in ("equal_chunks", "body_token_weighted"):
                saved = arrays[pooling]
                if saved.shape != (chunks.shape[1],):
                    raise ValueError("Saved company-vector dimension differs from chunk vectors")
                weights = None if pooling == "equal_chunks" else job["chunk_body_token_lengths"]
                if not np.array_equal(saved, aggregate(chunks, True, True, weights=weights)):
                    raise ValueError("Saved company vector differs from retained chunks")
        elif not job.get("reason"):
            raise ValueError("Failed job lacks its reason")
    if manifest["status"] == "COMPLETE":
        expected_batches = sum((job["chunk_count"] + manifest["batch_size"] - 1) //
                               manifest["batch_size"] for job in manifest["jobs"])
        if (manifest["failures"] or len(manifest["jobs"]) != manifest["logical_job_count_planned"] or
                manifest["logical_jobs_completed"] != manifest["logical_job_count_planned"] or
                manifest["company_vectors_completed"] != manifest["company_vector_count_planned"] or
                manifest["chunk_vectors_completed"] != manifest["chunk_vector_count_planned"] or
                manifest["actual_batch_call_attempts"] != expected_batches or
                manifest["real_encoder_sessions_created"] !=
                (1 if manifest["evidence_class"] == "REAL_MODEL" else 0) or
                set(job_keys) != expected_jobs):
            raise ValueError("Complete run manifest has incomplete job grid")
        candidate = load_candidate(Path(manifest["candidate_path"]))
        lengths = candidate["chunking"]["candidate_body_tokens"]
        vectors = {(length, pooling): np.vstack([
            np.load(output / next(job for job in manifest["jobs"] if
                    job["ticker"] == ticker and job["body_tokens_per_chunk"] == length)
                    ["files"][pooling]["path"], allow_pickle=False)
            for ticker in ids]) for length in lengths for pooling in candidate["pooling"]}
        recomputed = compute_small_chunk_metrics(ids, vectors, lengths)
        recomputed["inference_provenance"] = manifest["evidence_class"]
        metric_file = manifest["metric_file"]
        metric_path = (output / metric_file["path"]).resolve()
        if (not metric_path.is_relative_to(output) or
                file_sha256(metric_path) != metric_file["sha256"]):
            raise ValueError("Saved metric file hash changed")
        if json.loads(metric_path.read_text(encoding="utf-8")) != recomputed:
            raise ValueError("Saved metrics differ from recomputed vectors")
    elif manifest["metric_file"] is not None:
        raise ValueError("Partial run must not report full-grid metrics")
    return {"status": "PASS_SAVED_OUTPUT_RECOMPUTED", "run_status": manifest["status"],
            "evidence_class": manifest["evidence_class"], "jobs_checked": len(manifest["jobs"]),
            "metrics_recomputed": manifest["status"] == "COMPLETE"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "run"):
        command = sub.add_parser(name)
        command.add_argument("--candidate", required=True, type=Path)
        command.add_argument("--corpus", required=True, type=Path)
        command.add_argument("--receipt", required=True, type=Path)
        command.add_argument("--decision", type=Path)
        if name == "run":
            command.add_argument("--output", required=True, type=Path)
    verify = sub.add_parser("verify-output")
    verify.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify-output":
            result = verify_saved_output(args.output)
        elif args.command == "check":
            prepared = prepare_run(args.candidate, args.corpus, args.receipt, args.decision)
            result = {"status": "VERSION_GATES_PASSED_SOURCE_AUTHENTICITY_NOT_VERIFIED_BY_SOFTWARE",
                      "candidate_sha256": file_sha256(prepared["candidate_path"]),
                      "researcher_decision": prepared["decision"]["status"],
                      "encoder_session_created": False}
        else:
            result = run_candidate(args.candidate, args.corpus, args.receipt,
                                   args.decision, args.output)
        print(json.dumps(result if args.command != "run" else {
            "status": result["status"], "output": str(args.output),
            "actual_batch_call_attempts": result["actual_batch_call_attempts"]}))
        return 0 if result.get("status") != "PARTIAL_FAILURE" else 2
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        print(f"PROBE_GATE_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
