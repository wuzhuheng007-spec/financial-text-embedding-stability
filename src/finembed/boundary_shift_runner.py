"""Version-gated S3 boundary-shift runner and saved-output verifier.

The CLI cannot construct an Encoder until source, runtime, preflight and a
separate researcher decision have all passed. Synthetic tests call the
internal execution function with a fake encoder and label their output.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib
import json
import math
from pathlib import Path
import sys

import numpy as np

from .analysis import validate_corpus
from .boundary_shift_preflight import (
    SHIFTS, POOLING, boundary_shift_chunks, build_preflight,
    load_boundary_candidate, verify_receipt,
)
from .boundary_shift_decision import validate_boundary_decision
from .boundary_shift_metrics import compute_boundary_shift_metrics
from .config import load_config
from .m6 import chunk_evidence
from .model import Encoder, ModelError, aggregate
from .run import git_state, write_json
from .sensitivity_preflight import (chunk_map_sha256, file_sha256,
                                    inspect_model_files)
from .small_chunk_decision import canonical_json_hash, derive_input_manifest_sha256
from .small_chunk_provenance import (capture_runtime_provenance,
                                     verify_runtime_provenance)


METRIC_CONTRACT = "docs/S3_BOUNDARY_SHIFT_METRICS.md"
FAILURE_POLICY = "FULL_FIVE_COMPANIES_ALL_ARMS_NO_IMPUTATION"


class ProbeGateError(RuntimeError):
    """A proposed probe failed an execution gate before model construction."""


def _require_bound_modules(root: Path, receipt: dict) -> dict:
    """Check actual imported S3 code locations and bytes against the receipt."""
    bound = {Path(row["path"]).resolve(): row["sha256"]
             for row in receipt.get("bindings", [])}
    checked = {}
    for short in ("boundary_shift_preflight", "boundary_shift_runner",
                  "boundary_shift_metrics", "boundary_shift_decision"):
        module = importlib.import_module(f"finembed.{short}")
        source = Path(module.__file__).resolve(strict=True)
        expected = (root / "src" / "finembed" / f"{short}.py").resolve()
        if source != expected or bound.get(source) != file_sha256(source):
            raise ProbeGateError(f"S3 imported module source is unbound or shadowed: {short}")
        checked[short] = {"path": str(source), "sha256": bound[source]}
    return checked


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
    """Complete every S3 version and decision gate before Encoder construction."""
    candidate_path = candidate_path.resolve(strict=True)
    root = candidate_path.parent.parent
    if candidate_path.parent != root / "configs":
        raise ProbeGateError("Candidate must reside in the selected checkout's configs directory")
    runtime = capture_runtime_provenance(root, required_provider="CPUExecutionProvider")
    candidate = load_boundary_candidate(candidate_path)
    receipt_path = receipt_path.resolve(strict=True)
    receipt_sha = file_sha256(receipt_path)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("kind") != "BOUNDARY_SHIFT_TOKENIZATION_PREFLIGHT" or receipt.get("status") != "PASS_TOKENIZATION_ONLY":
        raise ProbeGateError("A passing S3 tokenizer-only receipt is required")
    verify_receipt(receipt_path)
    verify_runtime_provenance(root, receipt.get("runtime_provenance"),
                              required_provider="CPUExecutionProvider")
    bound_modules = _require_bound_modules(root, receipt)
    if receipt.get("candidate_sha256") != file_sha256(candidate_path):
        raise ProbeGateError("Preflight was made for another S3 candidate version")
    if receipt.get("boundary_shifts") != list(SHIFTS):
        raise ProbeGateError("Preflight S3 shift grid differs from the candidate")
    if receipt.get("historical_128_alignment", {}).get("status") != "PASS":
        raise ProbeGateError("Historical delta-zero alignment is not passing")
    historical_sha = receipt["historical_128_alignment"].get("historical_map_sha256")
    historical_paths = [Path(bound["path"]) for bound in receipt.get("bindings", [])
                        if bound.get("sha256") == historical_sha and
                        Path(bound["path"]).name == "chunk_map.jsonl"]
    if len(historical_paths) != 1:
        raise ProbeGateError("Passing historical map has no unique bound source file")
    fresh = build_preflight(candidate_path, corpus_path, historical_paths[0])
    if fresh.get("status") != "PASS_TOKENIZATION_ONLY":
        raise ProbeGateError(f"Fresh S3 tokenizer-only preflight failed: {fresh.get('reason')}")
    without_capture_metadata = lambda value: {key: item for key, item in value.items()
                                              if key not in {"created_utc", "source_commit_at_preflight"}}
    if canonical_json_hash(without_capture_metadata(receipt)) != canonical_json_hash(without_capture_metadata(fresh)):
        raise ProbeGateError("Saved S3 preflight conclusions differ from fresh tokenization")
    contract_path = root / METRIC_CONTRACT
    contract_sha = file_sha256(contract_path)
    if (receipt.get("s3_metric_contract_sha256") != contract_sha or
            not any(Path(b["path"]) == contract_path.resolve() and b["sha256"] == contract_sha
                    for b in receipt.get("bindings", []))):
        raise ProbeGateError("S3 metric contract is not bound to the final preflight")
    if receipt.get("metric_contract_sha256") != candidate["metric_family_reference"]["sha256"]:
        raise ProbeGateError("Original metric family differs from the S3 proposal")
    config_path = root / candidate["model_config"]["path"]
    config = load_config(config_path)
    if (config.source_sha256 != candidate["model_config"]["sha256"] or
            config.provider != "CPUExecutionProvider" or config.batch_size != 8 or
            config.normalize_chunks is not True or config.normalize_documents is not True or
            config.overlap_tokens != 0 or config.body_tokens != 128):
        raise ProbeGateError("Pinned model/provider/batch/normalization config differs from S3")
    model = inspect_model_files(config)
    if (_model_identity({"model": model}, config) != _model_identity(receipt, config) or
            receipt.get("reference_config_sha256") != config.source_sha256):
        raise ProbeGateError("Preflight model/config identity differs from current files")
    corpus_path = corpus_path.resolve(strict=True)
    if file_sha256(corpus_path) != candidate["input"]["corpus_index_sha256"]:
        raise ProbeGateError("M1 corpus index differs from the S3 candidate")
    docs, audit = validate_corpus(config, corpus_path)
    if (audit.get("valid_count") != 5 or len(docs) != 5 or
            [doc["ticker"] for doc in docs] != candidate["input"]["company_order"]):
        raise ProbeGateError("The original five-company input stratum is incomplete")
    versions = _input_versions(receipt)
    current_versions = [{"ticker": doc["ticker"], "cik": doc["cik"],
                         "accession": doc["accession"], "form": "10-K",
                         "section": "ITEM_1_BUSINESS", "item1_sha256": doc["item1_sha256"]}
                        for doc in docs]
    if current_versions != versions or versions != candidate["input"]["versions"]:
        raise ProbeGateError("Current M1 input versions differ from S3 candidate/preflight")
    input_manifest_sha = derive_input_manifest_sha256(
        candidate["input"]["corpus_index_sha256"], versions)
    decision = validate_boundary_decision(
        decision_path, candidate_path=candidate_path,
        input_manifest_sha256=input_manifest_sha, input_versions=versions,
        model_identity=_model_identity(receipt, config),
        metric_contract_path=contract_path)
    if decision["status"] != "ACCEPTED":
        raise ProbeGateError(f"S3 researcher decision gate: {decision['status']}: {decision['reason']}")
    return {"root": root, "candidate": candidate, "candidate_path": candidate_path,
            "corpus_path": corpus_path, "receipt_path": receipt_path,
            "receipt_sha256": receipt_sha, "receipt": receipt,
            "decision_path": Path(decision_path).resolve(strict=True),
            "decision": decision, "decision_sha256": file_sha256(decision_path),
            "metric_contract_sha256": contract_sha,
            "input_manifest_sha256": input_manifest_sha,
            "input_versions": versions, "model_identity": _model_identity(receipt, config),
            "runtime_provenance": runtime, "bound_s3_modules": bound_modules,
            "docs": docs, "config": config, "failure_policy": FAILURE_POLICY}

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
    """Execute the fixed S3 grid; injected encoders are synthetic tests only."""
    import time
    if ((evidence_class == "REAL_MODEL" and encoder_factory is not Encoder) or
            (evidence_class == "SYNTHETIC_TEST_ONLY" and encoder_factory is Encoder) or
            evidence_class not in {"REAL_MODEL", "SYNTHETIC_TEST_ONLY"}):
        raise ValueError("Encoder factory and evidence class disagree")
    output = output.resolve()
    if evidence_class == "REAL_MODEL" and not output.is_relative_to(
            (prepared["root"] / "artifacts").resolve()):
        raise ProbeGateError("Real output must stay in the ignored artifacts directory")
    candidate = prepared["candidate"]
    shifts = candidate["chunking"]["boundary_shifts"]
    ids = candidate["input"]["company_order"]
    if ([doc["ticker"] for doc in prepared["docs"]] != ids or len(ids) != 5 or
            shifts != list(SHIFTS) or candidate["pooling"] != list(POOLING)):
        raise ProbeGateError("Complete ordered S3 five-company grid is required")
    started = time.perf_counter()
    output.mkdir(parents=True, exist_ok=False)
    manifest = {"schema_version": 1, "kind": "BOUNDARY_SHIFT_PROBE_RUN",
                "axis": "boundary_shift", "reference_boundary_shift": 0,
                "reference_body_tokens": 128,
                "boundary_shifts": shifts, "pooling": list(POOLING),
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
                "bound_s3_modules": prepared["bound_s3_modules"],
                "source_commit_at_preflight": prepared["receipt"].get("source_commit_at_preflight"),
                "git_at_run": git_state(prepared["root"]),
                "batch_size": prepared["config"].batch_size,
                "logical_job_count_planned": len(ids) * len(shifts),
                "chunk_vector_count_planned": prepared["receipt"]["planned_counts"]["chunk_vectors"],
                "company_vector_count_planned": len(ids) * len(shifts) * len(POOLING),
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
            if encoder.session.get_providers() != ["CPUExecutionProvider"]:
                raise ModelError("Encoder session did not use CPUExecutionProvider alone")
        tokenized = {}
        for doc in prepared["docs"]:
            encoding = encoder.tokenizer.encode(doc["item1_text"], add_special_tokens=False)
            tokenized[doc["ticker"]] = (encoding.ids, encoding.offsets)
        for delta in shifts:
            for doc in prepared["docs"]:
                ticker = doc["ticker"]
                job = {"ticker": ticker, "accession": doc["accession"],
                       "item1_sha256": doc["item1_sha256"], "boundary_shift": delta,
                       "reference_body_tokens": 128, "status": "RUNNING", "files": {}}
                manifest["jobs"].append(job)
                try:
                    body_ids, offsets = tokenized[ticker]
                    chunks = boundary_shift_chunks(
                        body_ids, offsets, doc["item1_text"], 128, delta,
                        encoder.special_count, encoder.identity["max_input_tokens"])
                    evidence, stats = chunk_evidence(
                        {"id": f"{ticker}|{doc['accession']}", "ticker": ticker,
                         "text": doc["item1_text"], "text_sha256": doc["item1_sha256"]},
                        chunks, encoder.special_count,
                        encoder.identity["max_input_tokens"])
                    expected = prepared["receipt"]["documents"][ids.index(ticker)]["shifts"][str(delta)]
                    lengths = [len(chunk.token_ids) for chunk in chunks]
                    if (stats["body_tokens"] != expected["body_tokens"] or
                            stats["chunk_count"] != expected["chunk_count"] or
                            lengths != expected["chunk_body_token_lengths"] or
                            chunk_map_sha256(chunks) != expected["chunk_map_sha256"] or
                            stats["coverage_check"] != "PASS" or
                            max(x["model_input_tokens_including_specials"] for x in evidence) !=
                            expected["max_model_input_tokens"]):
                        raise ModelError("Actual S3 chunk map differs from tokenizer-only preflight")
                    batch_before = encoder.forward_batch_call_attempts
                    chunk_vectors = np.asarray(encoder.embed_chunks(chunks), dtype=np.float64)
                    expected_batches = (len(chunks) + prepared["config"].batch_size - 1) // prepared["config"].batch_size
                    if encoder.forward_batch_call_attempts - batch_before != expected_batches:
                        raise ModelError("Actual batch-call count differs from encoded chunk batches")
                    if (chunk_vectors.ndim != 2 or chunk_vectors.shape[0] != len(chunks) or
                            chunk_vectors.shape[1] == 0 or not np.isfinite(chunk_vectors).all() or
                            (evidence_class == "REAL_MODEL" and chunk_vectors.shape[1] != 384)):
                        raise ModelError("Encoder returned invalid S3 chunk vectors")
                    relative = f"vectors/delta_{delta:+d}/{ticker}"
                    job["files"]["chunks"] = _save_array(output, relative + "_chunks.npy", chunk_vectors)
                    job["chunk_body_token_lengths"] = lengths
                    job["chunk_count"] = len(chunks)
                    job["chunk_map_sha256"] = chunk_map_sha256(chunks)
                    manifest["chunk_vectors_completed"] += len(chunks)
                    for pooling in POOLING:
                        weights = None if pooling == "equal_chunks" else lengths
                        company_vector = aggregate(chunk_vectors, True, True, weights=weights)
                        job["files"][pooling] = _save_array(
                            output, relative + f"_{pooling}.npy", company_vector)
                        manifest["company_vectors_completed"] += 1
                    job["status"] = "COMPLETE"
                    manifest["logical_jobs_completed"] += 1
                except Exception as exc:
                    job["status"] = "FAILED"
                    job["reason"] = f"{type(exc).__name__}: {exc}"
                    manifest["failures"].append({"ticker": ticker, "boundary_shift": delta,
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
                manifest["company_vectors_completed"] == manifest["company_vector_count_planned"] and
                manifest["actual_batch_call_attempts"] ==
                sum((job["chunk_count"] + manifest["batch_size"] - 1) //
                    manifest["batch_size"] for job in manifest["jobs"])):
            vectors = {}
            for delta in shifts:
                for pooling in POOLING:
                    rows = [np.load(output / next(job for job in manifest["jobs"]
                            if job["ticker"] == ticker and job["boundary_shift"] == delta)
                            ["files"][pooling]["path"], allow_pickle=False) for ticker in ids]
                    vectors[(delta, pooling)] = np.vstack(rows)
            metrics = compute_boundary_shift_metrics(ids, vectors, shifts)
            metrics["inference_provenance"] = evidence_class
            write_json(output / "metrics.json", metrics)
            manifest["metric_file"] = {"path": "metrics.json",
                                       "sha256": file_sha256(output / "metrics.json")}
            manifest["status"] = "COMPLETE"
        else:
            if not manifest["failures"]:
                manifest["failures"].append({"stage": "completion_accounting",
                    "reason": "Completed job, vector or batch counts differ from the declared S3 grid"})
            manifest["status"] = "PARTIAL_FAILURE"
    except Exception as exc:
        if encoder is None and evidence_class == "REAL_MODEL" and manifest["encoder_construction_attempts"]:
            manifest["real_encoder_sessions_created"] = None
        manifest["failures"].append({"stage": "encoder_initialization_or_analysis",
                                     "reason": f"{type(exc).__name__}: {exc}"})
        manifest["status"] = "PARTIAL_FAILURE"
    manifest["ended_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["process_wall_seconds_from_output_creation"] = time.perf_counter() - started
    _save_manifest(output, manifest)
    return manifest

def run_candidate(candidate_path: Path, corpus_path: Path, receipt_path: Path,
                  decision_path: Path | None, output: Path) -> dict:
    """Public inference entry; no injectable encoder or boolean acceptance flag."""
    prepared = prepare_run(candidate_path, corpus_path, receipt_path, decision_path)
    return _execute_prepared(prepared, output)


def verify_saved_output(output: Path) -> dict:
    """Rehash S3 arrays and recompute both pooling arms and all metrics."""
    output = output.resolve(strict=True)
    manifest_path = output / "manifest.json"
    if file_sha256(manifest_path) != (output / "manifest.sha256").read_text(encoding="ascii").strip():
        raise ValueError("Saved S3 manifest hash differs from its sidecar")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("kind") != "BOUNDARY_SHIFT_PROBE_RUN" or
            manifest.get("status") not in {"COMPLETE", "PARTIAL_FAILURE"} or
            manifest.get("axis") != "boundary_shift" or
            manifest.get("reference_boundary_shift") != 0 or
            manifest.get("reference_body_tokens") != 128 or
            manifest.get("boundary_shifts") != list(SHIFTS) or
            manifest.get("pooling") != list(POOLING)):
        raise ValueError("Saved output has no finalized exact S3 manifest")
    ids = [row["ticker"] for row in manifest["input_versions"]]
    if len(ids) != 5 or len(set(ids)) != 5:
        raise ValueError("Saved S3 company inventory is incomplete")
    prepared = None
    if manifest["evidence_class"] == "REAL_MODEL":
        prepared = prepare_run(Path(manifest["candidate_path"]),
                               Path(manifest["corpus_index_path"]),
                               Path(manifest["receipt_path"]), Path(manifest["decision_path"]))
        for key, expected in (
                ("candidate_sha256", file_sha256(prepared["candidate_path"])),
                ("receipt_sha256", prepared["receipt_sha256"]),
                ("decision_sha256", prepared["decision_sha256"]),
                ("metric_contract_sha256", prepared["metric_contract_sha256"]),
                ("input_manifest_sha256", prepared["input_manifest_sha256"]),
                ("input_versions", prepared["input_versions"]),
                ("model_identity", prepared["model_identity"]),
                ("runtime_provenance", prepared["runtime_provenance"]),
                ("bound_s3_modules", prepared["bound_s3_modules"]),
                ("decision_validation", prepared["decision"]),
                ("source_commit_at_preflight", prepared["receipt"].get("source_commit_at_preflight")),
                ("failure_policy", FAILURE_POLICY),
                ("batch_size", prepared["config"].batch_size),
                ("logical_job_count_planned", 25),
                ("chunk_vector_count_planned", prepared["receipt"]["planned_counts"]["chunk_vectors"]),
                ("company_vector_count_planned", 50)):
            if manifest.get(key) != expected:
                raise ValueError(f"Saved S3 run binding changed: {key}")
    elif manifest["evidence_class"] != "SYNTHETIC_TEST_ONLY":
        raise ValueError("Unknown saved S3 evidence class")
    if (manifest["failure_policy"] != FAILURE_POLICY or
            type(manifest.get("batch_size")) is not int or manifest["batch_size"] <= 0 or
            manifest["logical_job_count_planned"] != 25 or
            type(manifest.get("chunk_vector_count_planned")) is not int or
            manifest["chunk_vector_count_planned"] <= 0 or
            manifest["company_vector_count_planned"] != 50):
        raise ValueError("Saved S3 grid or accounting policy is invalid")
    if (type(manifest.get("process_wall_seconds_from_output_creation")) not in (int, float) or
            not math.isfinite(manifest["process_wall_seconds_from_output_creation"]) or
            manifest["process_wall_seconds_from_output_creation"] < 0 or
            any(type(manifest.get(key)) is not int or manifest[key] < 0 for key in
                ("logical_jobs_completed", "chunk_vectors_completed",
                 "company_vectors_completed", "encoder_construction_attempts"))):
        raise ValueError("Saved S3 duration or counter is invalid")
    expected_jobs = {(ticker, delta) for ticker in ids for delta in SHIFTS}
    job_keys = [(job["ticker"], job["boundary_shift"]) for job in manifest["jobs"]]
    if len(set(job_keys)) != len(job_keys) or not set(job_keys).issubset(expected_jobs):
        raise ValueError("Saved S3 job grid has duplicate or unexpected cells")
    if manifest["logical_jobs_completed"] != sum(job["status"] == "COMPLETE" for job in manifest["jobs"]):
        raise ValueError("Saved S3 completed-job count differs from job records")
    if manifest["chunk_vectors_completed"] != sum(
            job.get("chunk_count", 0) for job in manifest["jobs"] if "chunks" in job["files"]):
        raise ValueError("Saved S3 chunk-vector count differs from job records")
    if manifest["company_vectors_completed"] != sum(
            int(pool in job["files"]) for job in manifest["jobs"] for pool in POOLING):
        raise ValueError("Saved S3 company-vector count differs from job records")
    for job in manifest["jobs"]:
        ticker, delta = job["ticker"], job["boundary_shift"]
        identity = next((row for row in manifest["input_versions"] if row["ticker"] == ticker), None)
        if (identity is None or job["accession"] != identity["accession"] or
                job["item1_sha256"] != identity["item1_sha256"] or
                job.get("reference_body_tokens") != 128 or
                job["status"] not in {"COMPLETE", "FAILED"}):
            raise ValueError("Saved S3 job identity or status is invalid")
        files = job["files"]
        expected_names = {"chunks", *POOLING}
        if (not isinstance(files, dict) or not set(files).issubset(expected_names) or
                (job["status"] == "COMPLETE" and set(files) != expected_names)):
            raise ValueError("Saved S3 job has missing or unexpected vector files")
        stem = f"vectors/delta_{delta:+d}/{ticker}"
        expected_paths = {"chunks": stem + "_chunks.npy",
                          "equal_chunks": stem + "_equal_chunks.npy",
                          "body_token_weighted": stem + "_body_token_weighted.npy"}
        arrays = {}
        for file_kind, record in files.items():
            if record["path"] != expected_paths[file_kind]:
                raise ValueError("Saved S3 vector file is outside its declared cell")
            path = (output / record["path"]).resolve()
            if not path.is_relative_to(output) or file_sha256(path) != record["sha256"]:
                raise ValueError("Saved S3 output file hash/path mismatch")
            array = np.load(path, allow_pickle=False)
            if (array.dtype != np.float64 or record["dtype"] != "float64" or
                    list(array.shape) != record["shape"] or not np.isfinite(array).all()):
                raise ValueError("Saved S3 vector shape, dtype or finite-value check failed")
            arrays[file_kind] = array
        if "chunks" in files:
            chunks = arrays["chunks"]
            body_lengths = job.get("chunk_body_token_lengths")
            special_count = (prepared["receipt"]["special_tokens_per_chunk"]
                             if prepared is not None else 2)
            model_limit = (prepared["receipt"]["model"]["max_input_tokens"]
                           if prepared is not None else 256)
            if (chunks.ndim != 2 or chunks.shape[1] <= 0 or
                    (prepared is not None and chunks.shape[1] != 384) or
                    type(job.get("chunk_count")) is not int or
                    chunks.shape[0] != job["chunk_count"] or
                    not isinstance(body_lengths, list) or
                    len(body_lengths) != job["chunk_count"] or
                    any(type(n) is not int or n <= 0 or n + special_count > model_limit
                        for n in body_lengths)):
                raise ValueError("Saved S3 chunk rows or actual body lengths are invalid")
            if prepared is not None:
                expected = prepared["receipt"]["documents"][ids.index(ticker)]["shifts"][str(delta)]
                if (body_lengths != expected["chunk_body_token_lengths"] or
                        job["chunk_count"] != expected["chunk_count"] or
                        job.get("chunk_map_sha256") != expected["chunk_map_sha256"]):
                    raise ValueError("Saved S3 body lengths or chunk map differ from final preflight")
            elif sum(body_lengths) <= 0:
                raise ValueError("Saved synthetic S3 body lengths are empty")
            for pooling in POOLING:
                if pooling in arrays:
                    saved = arrays[pooling]
                    if saved.shape != (chunks.shape[1],):
                        raise ValueError("Saved S3 company vector dimension differs from chunks")
                    weights = None if pooling == "equal_chunks" else body_lengths
                    if not np.array_equal(saved, aggregate(chunks, True, True, weights=weights)):
                        raise ValueError("Saved S3 company vector differs from retained chunks")
        elif any(pool in files for pool in POOLING):
            raise ValueError("Saved S3 pooling vector lacks source chunks")
        if job["status"] == "FAILED" and not job.get("reason"):
            raise ValueError("Failed S3 job lacks its reason")
    if manifest["status"] == "COMPLETE":
        expected_batches = sum((job["chunk_count"] + manifest["batch_size"] - 1) //
                               manifest["batch_size"] for job in manifest["jobs"])
        if (manifest["failures"] or len(manifest["jobs"]) != 25 or
                manifest["logical_jobs_completed"] != 25 or
                manifest["company_vectors_completed"] != 50 or
                manifest["chunk_vectors_completed"] != manifest["chunk_vector_count_planned"] or
                type(manifest["actual_batch_call_attempts"]) is not int or
                manifest["actual_batch_call_attempts"] != expected_batches or
                manifest["encoder_construction_attempts"] != 1 or
                manifest["real_encoder_sessions_created"] !=
                (1 if manifest["evidence_class"] == "REAL_MODEL" else 0) or
                set(job_keys) != expected_jobs):
            raise ValueError("Complete S3 manifest has incomplete job grid")
        expected_vector_paths = {record["path"] for job in manifest["jobs"]
                                 for record in job["files"].values()}
        actual_vector_paths = {path.relative_to(output).as_posix()
                               for path in (output / "vectors").rglob("*.npy")}
        if actual_vector_paths != expected_vector_paths:
            raise ValueError("Complete S3 vector file inventory differs from manifest")
        vectors = {(delta, pooling): np.vstack([
            np.load(output / next(job for job in manifest["jobs"] if
                    job["ticker"] == ticker and job["boundary_shift"] == delta)
                    ["files"][pooling]["path"], allow_pickle=False)
            for ticker in ids]) for delta in SHIFTS for pooling in POOLING}
        recomputed = compute_boundary_shift_metrics(ids, vectors, SHIFTS)
        recomputed["inference_provenance"] = manifest["evidence_class"]
        metric_file = manifest["metric_file"]
        if not isinstance(metric_file, dict) or metric_file.get("path") != "metrics.json":
            raise ValueError("Saved S3 metric path differs from declared output")
        metric_path = (output / metric_file["path"]).resolve()
        if (not metric_path.is_relative_to(output) or
                file_sha256(metric_path) != metric_file["sha256"]):
            raise ValueError("Saved S3 metric file hash changed")
        if json.loads(metric_path.read_text(encoding="utf-8")) != recomputed:
            raise ValueError("Saved S3 metrics differ from recomputed vectors")
    elif (not manifest["failures"] or manifest["metric_file"] is not None or
          (output / "metrics.json").exists()):
        raise ValueError("Partial S3 run must retain failure reasons and no full-grid metrics")
    return {"status": "PASS_SAVED_OUTPUT_RECOMPUTED",
            "run_status": manifest["status"], "evidence_class": manifest["evidence_class"],
            "jobs_checked": len(manifest["jobs"]),
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
