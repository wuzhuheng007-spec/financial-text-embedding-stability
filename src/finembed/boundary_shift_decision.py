"""Validate a separate, version-bound S3 decision without model loading.

A matching record documents a claim of researcher acceptance. Hashes and local
source checks cannot authenticate the person who made the decision.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any, Mapping

from .boundary_shift_preflight import load_boundary_candidate
from .small_chunk_decision import (COMPANY_FAILURE_POLICY, _check_source_reference,
                                   _decision_time_error, _valid_input_versions,
                                   _valid_model_identity, derive_input_manifest_sha256,
                                   file_sha256)


EXPECTED_SCOPE = "s3_boundary_shift_engineering_probe_real_inference_five_company_full_grid"
INTERPRETATION_SCOPE = "local_cpu_fixed_five_company_s3_engineering_grid_only"
EXPECTED_RESEARCHER_NAME = "zhuheng wu"
EXPECTED_CANDIDATE_SHA256 = "a16dc7786e33c607eabb833eb1adb7f8c9652735cfb21eaa862f12a3661199e2"
EXPECTED_MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
EXPECTED_MODEL_FILE_SHA256 = {
    "onnx/model.onnx": "6fd5d72fe4589f189f8ebc006442dbb529bb7ce38f8082112682524616046452",
    "tokenizer.json": "be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037",
    "config.json": "953f9c0d463486b10a6871cc2fd59f223b2c70184f49815e7efbcab5d8908b41",
    "sentence_bert_config.json": "fc1993fde0a95c24ec6c022539d41cf6e2f7c9721e5415d6fb6897472a9cd4b7",
}
BOUNDARY_SHIFTS = [-10, -5, 0, 5, 10]
POOLING = ["equal_chunks", "body_token_weighted"]
METRIC_CONTRACT = "docs/S3_BOUNDARY_SHIFT_METRICS.md"
LEGACY_METRIC_CONTRACT = "docs/SMALL_CHUNK_METRICS.md"


def _result(status: str, reason: str) -> dict[str, str | bool]:
    return {"status": status, "reason": reason,
            "record_binding_match": status == "ACCEPTED",
            "source_authenticity": "NOT_VERIFIED_BY_SOFTWARE"}


def _private_record_check(record_file: Path, root: Path) -> dict[str, str | bool] | None:
    try:
        relative = record_file.resolve(strict=True).relative_to(root).as_posix()
    except (OSError, ValueError):
        return _result("MISSING", "S3 decision record must be in this checkout's private artifacts directory")
    if not relative.startswith("artifacts/"):
        return _result("MISSING", "S3 decision record must be in this checkout's private artifacts directory")
    try:
        ignored = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", "--", relative],
                                 capture_output=True, check=False).returncode == 0
        tracked = subprocess.run(["git", "-C", str(root), "ls-files", "--error-unmatch", "--", relative],
                                 capture_output=True, check=False).returncode == 0
    except OSError:
        return _result("MISSING", "Git is unavailable to check private S3 decision-record scope")
    if not ignored or tracked:
        return _result("MISSING", "S3 decision record is not confirmed ignored and untracked")
    return None


def validate_boundary_decision(
    record_path: str | Path | None,
    *,
    candidate_path: str | Path,
    input_manifest_sha256: str,
    input_versions: list[dict],
    model_identity: Mapping[str, Any],
    metric_contract_path: str | Path,
    expected_scope: str = EXPECTED_SCOPE,
) -> dict[str, str | bool]:
    """Check S3 acceptance against caller-verified inputs, model and metrics.

    The caller must independently verify actual source text, local model files,
    code imports and preflight bindings. This function checks the private
    decision record against those independently verified identities.
    """
    if record_path is None:
        return _result("MISSING", "No S3 decision record was provided")
    record_file = Path(record_path)
    if not record_file.is_file():
        return _result("MISSING", "S3 decision record file is absent")
    try:
        record = json.loads(record_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _result("MISSING", "S3 decision record is unreadable or invalid JSON")
    if (not isinstance(record, dict) or record.get("schema_version") != 1 or
            record.get("kind") != "boundary_shift_decision_record"):
        return _result("MISSING", "S3 decision record schema or kind is missing")
    if expected_scope != EXPECTED_SCOPE or record.get("decision_scope") != EXPECTED_SCOPE:
        return _result("WRONG_SCOPE", "Decision scope does not authorize the exact S3 full grid")
    decision = record.get("decision")
    if decision not in ("ACCEPT", "REJECT"):
        return _result("MISSING", "No explicit S3 ACCEPT or REJECT decision is recorded")

    candidate_file = Path(candidate_path).resolve()
    root = candidate_file.parent.parent
    if candidate_file != root / "configs" / "boundary_shift_probe_round5.json":
        return _result("VERSION_MISMATCH", "S3 candidate must be the exact round-5 file in this checkout")
    private_check = _private_record_check(record_file, root)
    if private_check is not None:
        return private_check
    source_check = _check_source_reference(record.get("source_reference"), candidate_file)
    if source_check is not None:
        return source_check
    time_error = _decision_time_error(record)
    if time_error is not None:
        return _result("MISSING", time_error)
    if record.get("researcher_name") != EXPECTED_RESEARCHER_NAME:
        return _result("VERSION_MISMATCH", "Researcher name differs from the name supplied for this exact S3 decision")
    if decision == "REJECT":
        return _result("REJECTED", "Researcher record explicitly rejects this S3 scope")

    contract_file = Path(metric_contract_path).resolve()
    if contract_file != root / METRIC_CONTRACT:
        return _result("VERSION_MISMATCH", "S3 metric contract path differs from the declared contract")
    try:
        candidate_hash = file_sha256(candidate_file)
        candidate = load_boundary_candidate(candidate_file)
        contract_hash = file_sha256(contract_file)
        legacy_contract_hash = file_sha256(root / LEGACY_METRIC_CONTRACT)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, TypeError):
        return _result("VERSION_MISMATCH", "S3 candidate or metric contract is unreadable or invalid")
    if candidate_hash != EXPECTED_CANDIDATE_SHA256:
        return _result("VERSION_MISMATCH", "S3 candidate bytes differ from the accepted proposal")
    if legacy_contract_hash != candidate["metric_family_reference"]["sha256"]:
        return _result("VERSION_MISMATCH", "Unchanged source metric-family contract bytes differ")

    source = candidate["input"]
    if (not _valid_input_versions(input_versions) or
            input_versions != source["versions"]):
        return _result("VERSION_MISMATCH", "S3 expected input versions differ from the exact five-source candidate")
    manifest_hash = derive_input_manifest_sha256(source["corpus_index_sha256"], input_versions)
    if input_manifest_sha256 != manifest_hash:
        return _result("VERSION_MISMATCH", "S3 input manifest does not bind the exact corpus and versions")
    if (not isinstance(model_identity, Mapping) or
            not _valid_model_identity(dict(model_identity), candidate["model_config"]["sha256"])):
        return _result("VERSION_MISMATCH", "S3 model/tokenizer identity is invalid")
    current_model = dict(model_identity)
    if (current_model["revision"] != EXPECTED_MODEL_REVISION or
            current_model["model_file_sha256"] != EXPECTED_MODEL_FILE_SHA256):
        return _result("VERSION_MISMATCH", "S3 model/tokenizer files differ from the accepted fixed version")
    expected_fields = {
        "candidate_sha256": candidate_hash,
        "input_manifest_sha256": manifest_hash,
        "input_versions": input_versions,
        "model_identity": current_model,
        "metric_contract_sha256": contract_hash,
        "company_failure_policy": COMPANY_FAILURE_POLICY,
        "axis": "boundary_shift",
        "reference_body_tokens": 128,
        "reference_shift": 0,
        "boundary_shifts": BOUNDARY_SHIFTS,
        "pooling": POOLING,
        "interpretation_scope": INTERPRETATION_SCOPE,
    }
    changed = [field for field, expected in expected_fields.items()
               if record.get(field) != expected]
    if changed:
        return _result("VERSION_MISMATCH", "S3 decision bindings differ: " + ", ".join(changed))
    return _result("ACCEPTED", "Explicit S3 decision and version bindings match; source authenticity requires independent review")
