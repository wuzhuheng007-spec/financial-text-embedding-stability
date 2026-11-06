"""Validate a version-bound human decision without loading an encoder."""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping


EXPECTED_SCOPE = "small_chunk_engineering_probe_real_inference_five_company_full_grid"
COMPANY_ORDER = ("AAPL", "MSFT", "AMZN", "GOOGL", "META")
COMPANY_FAILURE_POLICY = {
    "required_company_order": list(COMPANY_ORDER),
    "missing_or_failed_company": "FAIL_ARM_NO_SUBSET",
    "zero_or_nonfinite_vector": "FAIL_ARM_WITH_REASON",
    "undefined_metric": "NULL_WITH_REASON",
}
MODEL_FILE_NAMES = (
    "onnx/model.onnx", "tokenizer.json", "config.json", "sentence_bert_config.json"
)
INPUT_VERSION_KEYS = frozenset((
    "ticker", "cik", "accession", "form", "section", "item1_sha256"
))
MODEL_IDENTITY_KEYS = frozenset((
    "model_id", "revision", "tokenizer_revision", "model_file_sha256",
    "provider", "reference_config_sha256"
))
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")


def file_sha256(path: str | Path) -> str:
    """Hash a local file's bytes; this does not authenticate its author."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json_hash(value: Any) -> str:
    """Hash UTF-8 JSON with sorted keys, compact separators and no NaN values."""
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def derive_input_manifest_sha256(corpus_index_sha256: str,
                                 input_versions: list[dict]) -> str:
    """Bind the source index and ordered exact section versions."""
    return canonical_json_hash({"corpus_index_sha256": corpus_index_sha256,
                                "documents": input_versions})


def _result(status: str, reason: str) -> dict[str, str | bool]:
    return {"status": status, "reason": reason,
            "record_binding_match": status == "ACCEPTED",
            "source_authenticity": "NOT_VERIFIED_BY_SOFTWARE"}


def _valid_input_versions(rows: Any) -> bool:
    if not isinstance(rows, list) or len(rows) != len(COMPANY_ORDER):
        return False
    for ticker, row in zip(COMPANY_ORDER, rows):
        if not isinstance(row, dict) or set(row) != INPUT_VERSION_KEYS:
            return False
        if (row["ticker"] != ticker or row["form"] != "10-K" or
                row["section"] != "ITEM_1_BUSINESS" or
                not isinstance(row["cik"], str) or
                re.fullmatch(r"[0-9]{10}", row["cik"]) is None or
                not isinstance(row["accession"], str) or
                re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", row["accession"]) is None or
                not isinstance(row["item1_sha256"], str) or
                _SHA256.fullmatch(row["item1_sha256"]) is None):
            return False
    return True


def _valid_model_identity(identity: Any, reference_config_sha256: str) -> bool:
    if not isinstance(identity, dict) or set(identity) != MODEL_IDENTITY_KEYS:
        return False
    files = identity["model_file_sha256"]
    return bool(
        identity["model_id"] == "sentence-transformers/all-MiniLM-L6-v2" and
        isinstance(identity["revision"], str) and
        _SHA1.fullmatch(identity["revision"]) and
        identity["tokenizer_revision"] == identity["revision"] and
        identity["provider"] == "CPUExecutionProvider" and
        identity["reference_config_sha256"] == reference_config_sha256 and
        isinstance(files, dict) and set(files) == set(MODEL_FILE_NAMES) and
        all(isinstance(files[name], str) and _SHA256.fullmatch(files[name])
            for name in MODEL_FILE_NAMES)
    )


def _valid_utc_time(value: Any) -> bool:
    if not isinstance(value, str) or not value.endswith("Z") or "T" not in value:
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.utcoffset() == timedelta(0)


def _decision_time_error(record: dict) -> str | None:
    """Keep unknown original-message time distinct from record creation time."""
    new_fields = "original_message_at_utc" in record or "recorded_at_utc" in record
    if not new_fields:
        if not _valid_utc_time(record.get("decided_at_utc")):
            return "A legacy decision requires an explicit ISO 8601 UTC decided_at_utc ending in Z"
        return None
    if "decided_at_utc" in record:
        return "Do not mix legacy decided_at_utc with the new two-time record"
    if "original_message_at_utc" not in record:
        return "New records must state original_message_at_utc, using null when unknown"
    recorded = record.get("recorded_at_utc")
    original = record["original_message_at_utc"]
    if not _valid_utc_time(recorded):
        return "A new decision requires an explicit ISO 8601 UTC recorded_at_utc ending in Z"
    if original is not None:
        if not _valid_utc_time(original):
            return "Original message time must be UTC or null when unknown"
        original_dt = datetime.fromisoformat(original[:-1] + "+00:00")
        recorded_dt = datetime.fromisoformat(recorded[:-1] + "+00:00")
        if original_dt > recorded_dt:
            return "Original message time cannot be after record creation time"
    return None


def _check_source_reference(source: Any, candidate_path: Path) -> dict[str, str | bool] | None:
    """Require a nonempty, hash-bound, ignored local source inside this checkout."""
    if not isinstance(source, dict) or set(source) != {"path", "sha256"}:
        return _result("MISSING", "Decision source requires path and SHA-256")
    path_value, expected_hash = source["path"], source["sha256"]
    if (not isinstance(path_value, str) or not path_value.strip() or
            not isinstance(expected_hash, str) or _SHA256.fullmatch(expected_hash) is None):
        return _result("MISSING", "Decision source path or SHA-256 is not provided")
    candidate_file = candidate_path.resolve()
    if candidate_file.parent.name != "configs":
        return _result("VERSION_MISMATCH", "Candidate must reside in this checkout's configs directory")
    root = candidate_file.parent.parent
    private_root = root / "artifacts"
    source_path = Path(path_value)
    try:
        resolved = (source_path if source_path.is_absolute() else root / source_path).resolve(strict=True)
    except (OSError, RuntimeError):
        return _result("MISSING", "Decision source file is absent or cannot be resolved")
    if not resolved.is_relative_to(private_root) or not resolved.is_file():
        return _result("MISSING", "Decision source must be a file in this checkout's private artifacts directory")
    relative = resolved.relative_to(root).as_posix()
    try:
        ignored = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", "--", relative],
                                 capture_output=True, check=False).returncode == 0
        tracked = subprocess.run(["git", "-C", str(root), "ls-files", "--error-unmatch", "--", relative],
                                 capture_output=True, check=False).returncode == 0
    except OSError:
        return _result("MISSING", "Git is unavailable to check private source scope")
    if not ignored or tracked:
        return _result("MISSING", "Decision source is not confirmed ignored and untracked")
    try:
        if not resolved.read_bytes().strip():
            return _result("MISSING", "Decision source file is empty")
        actual_hash = file_sha256(resolved)
    except OSError:
        return _result("MISSING", "Decision source file cannot be read")
    if actual_hash != expected_hash:
        return _result("VERSION_MISMATCH", "Decision source file SHA-256 changed")
    return None


def validate_decision(
    record_path: str | Path | None,
    *,
    candidate_path: str | Path,
    input_manifest_sha256: str,
    input_versions: list[dict],
    model_identity: Mapping[str, Any],
    metric_contract_path: str | Path,
    expected_scope: str = EXPECTED_SCOPE,
) -> dict[str, str | bool]:
    """Check an explicit decision against current, caller-verified versions.

    The caller must independently verify source files and model-file hashes. A
    matching record is only a syntactic and version-bound decision claim; it
    does not prove the source reference or signer's identity is authentic.
    """
    if record_path is None:
        return _result("MISSING", "No decision record was provided")
    record_file = Path(record_path)
    if not record_file.is_file():
        return _result("MISSING", "Decision record file is absent")
    try:
        record = json.loads(record_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _result("MISSING", "Decision record is unreadable or invalid JSON")
    if not isinstance(record, dict) or record.get("schema_version") != 1 or \
            record.get("kind") != "small_chunk_decision_record":
        return _result("MISSING", "Decision record schema or kind is missing")
    if record.get("decision_scope") != expected_scope:
        return _result("WRONG_SCOPE", "Decision scope does not authorize this full-grid probe")
    if expected_scope != EXPECTED_SCOPE:
        return _result("WRONG_SCOPE", "Requested scope is not the supported full-grid probe")
    decision = record.get("decision")
    if decision not in ("ACCEPT", "REJECT"):
        return _result("MISSING", "No explicit ACCEPT or REJECT decision is recorded")
    root = Path(candidate_path).resolve().parent.parent
    try:
        relative_record = record_file.resolve(strict=True).relative_to(root).as_posix()
    except (OSError, ValueError):
        return _result("MISSING", "Decision record must be in this checkout's private artifacts directory")
    if not relative_record.startswith("artifacts/"):
        return _result("MISSING", "Decision record must be in this checkout's private artifacts directory")
    try:
        ignored = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", "--", relative_record],
                                 capture_output=True, check=False).returncode == 0
        tracked = subprocess.run(["git", "-C", str(root), "ls-files", "--error-unmatch", "--", relative_record],
                                 capture_output=True, check=False).returncode == 0
    except OSError:
        return _result("MISSING", "Git is unavailable to check private decision-record scope")
    if not ignored or tracked:
        return _result("MISSING", "Decision record is not confirmed ignored and untracked")
    source_check = _check_source_reference(record.get("source_reference"), Path(candidate_path))
    if source_check is not None:
        return source_check
    time_error = _decision_time_error(record)
    if time_error is not None:
        return _result("MISSING", time_error)
    if decision == "REJECT":
        return _result("REJECTED", "Researcher record explicitly rejects this scope")

    try:
        candidate_file = Path(candidate_path)
        candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
        candidate_hash = file_sha256(candidate_file)
        contract_hash = file_sha256(metric_contract_path)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _result("VERSION_MISMATCH", "Candidate or metric contract is unreadable")
    if (not isinstance(candidate, dict) or candidate.get("schema_version") != 1 or
            candidate.get("kind") != "new_engineering_probe_candidate" or
            candidate.get("status") != "PROPOSED" or
            candidate.get("researcher_accepted") is not False or
            candidate.get("real_inference_status") != "NOT_RUN"):
        return _result("VERSION_MISMATCH", "Candidate plan status changed")
    input_cfg = candidate.get("input")
    model_cfg = candidate.get("model_config")
    chunking = candidate.get("chunking")
    if (not isinstance(input_cfg, dict) or not isinstance(model_cfg, dict) or
            not isinstance(chunking, dict)):
        return _result("VERSION_MISMATCH", "Candidate input or model identity is incomplete")
    if (chunking.get("reference_body_tokens") != 128 or
            chunking.get("candidate_body_tokens") != [118, 123, 128, 133, 138] or
            chunking.get("overlap_tokens") != 0 or
            candidate.get("pooling") != ["equal_chunks", "body_token_weighted"] or
            model_cfg.get("path") != "configs/smoke.yaml" or
            any(candidate.get(key) is not False for key in
                ("query_rewriting", "generation", "training"))):
        return _result("VERSION_MISMATCH", "Candidate grid or disabled operations changed")
    corpus_hash = input_cfg.get("corpus_index_sha256")
    if (not isinstance(corpus_hash, str) or _SHA256.fullmatch(corpus_hash) is None or
            input_cfg.get("company_order") != list(COMPANY_ORDER) or
            input_cfg.get("form") != "10-K" or
            input_cfg.get("section") != "ITEM_1_BUSINESS" or
            not _valid_input_versions(input_versions)):
        return _result("VERSION_MISMATCH", "Expected five-company input versions are invalid")
    derived_manifest_hash = derive_input_manifest_sha256(corpus_hash, input_versions)
    if input_manifest_sha256 != derived_manifest_hash:
        return _result("VERSION_MISMATCH", "Expected input manifest does not bind candidate and versions")
    if not isinstance(model_identity, Mapping) or not _valid_model_identity(dict(model_identity), model_cfg.get("sha256")):
        return _result("VERSION_MISMATCH", "Expected pinned model/tokenizer identity is invalid")
    expected_fields = {
        "candidate_sha256": candidate_hash,
        "input_manifest_sha256": derived_manifest_hash,
        "input_versions": input_versions,
        "model_identity": dict(model_identity),
        "metric_contract_sha256": contract_hash,
        "company_failure_policy": COMPANY_FAILURE_POLICY,
    }
    changed = [key for key, expected in expected_fields.items()
               if record.get(key) != expected]
    if changed:
        return _result("VERSION_MISMATCH", "Decision bindings differ: " + ", ".join(changed))
    return _result("ACCEPTED", "Explicit decision and required version bindings match; source authenticity requires independent review")
