"""Prepare a source-bound WRDS PERMNO upload list without querying WRDS.

The input identifiers, source CSV and receipts stay in ignored local artifacts.
This tool verifies an existing starting-security set; it never selects an M5
cohort, adds successor securities, or computes financial labels.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import audit_m5_delist_intake as delist_module
import audit_m5_wrds_intake as wrds_module
from audit_m5_wrds_intake import (
    AuditError, EXPECTED_COLUMNS, PAGE_ROW_COUNT, inspect_snapshot, parse_query,
    sha256_file,
)
from audit_m5_delist_intake import PRIOR_WRDS_HASHES, QUERY_SHA256, WINDOW_END, WINDOW_START


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "artifacts" / "m5_delist_intake_20261111_v3"
OUTPUT_DIR = ROOT / "artifacts" / "m5_daily_download_prepare_20261111_v1"
DOWNLOAD_DOC = ROOT / "docs" / "M5_WRDS_NEXT_DOWNLOAD.md"
EXPECTED_SOURCE_MANIFEST_SHA256 = (
    "333209acc538d21a41e5c6ba96b639e8157a61fd69ecf2d01c8322a3c7cca095"
)
SEED_NAME = "m5_daily_seed_permnos_private.json"
UPLOAD_NAME = "m5_daily_seed_permnos_upload.txt"
REGISTRY_NAME = "m5_daily_upload_registry_private.json"
PLAN_NAME = "m5_daily_download_plan_private.json"
SEGMENTS = (
    ("2019-11-01", "2019-12-31"),
    ("2020-01-01", "2020-12-31"),
    ("2021-01-01", "2021-12-31"),
    ("2022-01-01", "2022-12-31"),
    ("2023-01-01", "2023-12-31"),
    ("2024-01-01", "2024-12-31"),
    ("2025-01-01", "2025-12-31"),
    ("2026-01-01", "2026-02-28"),
)
DECIMAL_ID = re.compile(r"[1-9][0-9]*\Z", re.ASCII)


def require(condition: bool, code: str) -> None:
    if not condition:
        raise AuditError(code)


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), "SOURCE_JSON_INVALID")
    return value


def check_hash(path: Path, expected: str, code: str) -> str:
    actual = sha256_file(path)
    require(actual == expected, code)
    return actual


def checked_ids(values: object, code: str, *, require_sorted: bool = False) -> set[str]:
    require(isinstance(values, list), code)
    require(all(isinstance(value, str) and DECIMAL_ID.fullmatch(value)
                for value in values), code)
    require(len(values) == len(set(values)), code)
    if require_sorted:
        require(values == sorted(values), code)
    return set(values)


def verify_seed_relationship(seed: dict, prior_index: dict, snapshot_ids: set[str],
                             expected_prior_index_sha256: str) -> set[str]:
    require(seed.get("schema") == "m5_daily_seed_identifiers_private_v1"
            and seed.get("scope") == "DOWNLOAD_INPUT_ONLY_NOT_FORMAL_COHORT"
            and seed.get("successor_ids_auto_added") is False
            and seed.get("source_prior_index_sha256") == expected_prior_index_sha256,
            "SEED_SOURCE_BINDING_INVALID")
    seed_ids = checked_ids(seed.get("permnos"), "SEED_IDENTIFIERS_INVALID",
                           require_sorted=True)
    require(type(seed.get("permno_count")) is int
            and seed["permno_count"] == len(seed_ids), "SEED_COUNT_MISMATCH")
    require(prior_index.get("accepted") is False,
            "PRIOR_INDEX_UNEXPECTEDLY_ACCEPTED")
    securities = prior_index.get("starting_securities")
    require(isinstance(securities, list), "PRIOR_STARTING_IDENTIFIERS_INVALID")
    index_ids = checked_ids([item.get("permno") for item in securities]
                            if all(isinstance(item, dict) for item in securities) else None,
                            "PRIOR_STARTING_IDENTIFIERS_INVALID")
    require(seed_ids == index_ids == snapshot_ids, "STARTING_IDENTIFIER_SET_MISMATCH")
    return seed_ids


def verify_download_window(doc: Path) -> str:
    text = doc.read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.startswith("| 6. Next:")
            and "StkDlySecurityData" in line]
    dates = re.findall(r"\b20[0-9]{2}-[0-9]{2}-[0-9]{2}\b", rows[0]) if len(rows) == 1 else []
    require(len(rows) == 1
            and dates == [SEGMENTS[0][0], SEGMENTS[-1][1]]
            and WINDOW_START.isoformat() == SEGMENTS[0][0]
            and WINDOW_END.isoformat() == SEGMENTS[-1][1],
            "DAILY_BUFFER_DOCUMENT_CONFLICT")
    return sha256_file(doc)


def verify_import_sources() -> None:
    require(Path(wrds_module.__file__).resolve() == ROOT / "scripts" / "audit_m5_wrds_intake.py"
            and Path(delist_module.__file__).resolve() ==
            ROOT / "scripts" / "audit_m5_delist_intake.py",
            "IMPORTED_AUDIT_CODE_PATH_MISMATCH")


def verify_source_bundle(source_dir: Path, *,
                         expected_manifest_sha256: str = EXPECTED_SOURCE_MANIFEST_SHA256,
                         expected_snapshot_rows: int = PAGE_ROW_COUNT) -> dict:
    """Verify saved hashes and re-read only the original starting snapshot."""
    verify_import_sources()
    manifest_path = source_dir / "m5_delist_manifest.json"
    check_hash(manifest_path, expected_manifest_sha256, "DELIST_MANIFEST_HASH_MISMATCH")
    manifest = read_json(manifest_path)
    names = {"m5_delist_registry.json", "m5_delist_candidates_private.json",
             "m5_delist_summary.json", SEED_NAME}
    require(manifest.get("schema") == "m5_delist_saved_files_manifest_v1"
            and set(manifest.get("file_sha256", {})) == names,
            "DELIST_MANIFEST_INVALID")
    for name in names:
        check_hash(source_dir / name, manifest["file_sha256"][name],
                   "DELIST_SAVED_FILE_HASH_MISMATCH")
    registry = read_json(source_dir / "m5_delist_registry.json")
    summary = read_json(source_dir / "m5_delist_summary.json")
    seed = read_json(source_dir / SEED_NAME)
    require(registry.get("schema") == "m5_delist_local_registry_v1"
            and registry.get("scope") == "ENGINEERING_ONLY"
            and registry.get("daily_seed_basis") ==
            "ALL_PRIOR_STARTING_PERMNOS_NO_SUCCESSOR_AUTO_ADDITION"
            and registry.get("formal_m5_cohort_selected") is False
            and summary.get("schema") == "m5_delist_intake_summary_v1"
            and summary.get("formal_m5_cohort_selected") is False,
            "DELIST_SOURCE_STATUS_INVALID")
    require(summary.get("registry_sha256") == manifest["file_sha256"]["m5_delist_registry.json"]
            and summary.get("index_sha256") ==
            manifest["file_sha256"]["m5_delist_candidates_private.json"]
            and summary.get("query_sql_sha256") == registry.get("sql_sha256") == QUERY_SHA256
            and summary.get("export_csv_sha256") == registry.get("sha256"),
            "DELIST_SOURCE_CHAIN_MISMATCH")
    delist_script = ROOT / "scripts" / "audit_m5_delist_intake.py"
    prior_script = ROOT / "scripts" / "audit_m5_wrds_intake.py"
    require(Path(registry["script_path"]).resolve() == delist_script
            and Path(registry["helper_script_path"]).resolve() == prior_script,
            "SOURCE_CODE_PATH_MISMATCH")
    check_hash(delist_script, registry["script_sha256"], "DELIST_CODE_HASH_MISMATCH")
    check_hash(prior_script, registry["helper_script_sha256"],
               "PRIOR_CODE_HASH_MISMATCH")
    require(summary.get("audit_script_sha256") == registry["script_sha256"],
            "DELIST_CODE_BINDING_MISMATCH")
    delist_sql = Path(registry["sql_path"])
    delist_csv = Path(registry["csv_path"])
    check_hash(delist_sql, registry["sql_sha256"], "DELIST_SQL_HASH_MISMATCH")
    check_hash(delist_csv, registry["sha256"], "DELIST_CSV_HASH_MISMATCH")

    prior_dir = Path(registry["prior_wrds_artifacts_dir"])
    require(registry.get("prior_wrds_artifact_sha256") == PRIOR_WRDS_HASHES
            and summary.get("prior_wrds_artifact_sha256") == PRIOR_WRDS_HASHES,
            "PRIOR_RECEIPT_IDENTITY_MISMATCH")
    prior_names = {"registry": "m5_wrds_intake_registry.json",
                   "index": "m5_wrds_history_candidates_private.json",
                   "summary": "m5_wrds_intake_aggregate_summary.json"}
    for key, name in prior_names.items():
        check_hash(prior_dir / name, PRIOR_WRDS_HASHES[key],
                   "PRIOR_RECEIPT_HASH_MISMATCH")
    prior_registry = read_json(prior_dir / prior_names["registry"])
    prior_index = read_json(prior_dir / prior_names["index"])
    prior_summary = read_json(prior_dir / prior_names["summary"])
    require(prior_registry.get("query_spec_status") == "QUERY_SPEC_VERIFIED"
            and prior_registry.get("local_binding_status") ==
            "LOCAL_FILE_MATCHES_RECEIPT_AND_QUERY_SHAPE"
            and prior_registry.get("formal_m5_cohort_selected") is False
            and prior_registry.get("intake_script_sha256") == sha256_file(prior_script),
            "PRIOR_SOURCE_STATUS_INVALID")
    snapshot_info = prior_registry["local_snapshot"]
    snapshot_path = Path(snapshot_info["path"])
    require(snapshot_path.name == "wrxk2qlypw7cgvuo.csv", "SNAPSHOT_ROLE_MISMATCH")
    check_hash(snapshot_path, snapshot_info["sha256"], "SNAPSHOT_SOURCE_HASH_MISMATCH")
    prior_sql = Path(prior_registry["query_sql_path"])
    check_hash(prior_sql, prior_registry["query_sql_sha256"],
               "PRIOR_QUERY_HASH_MISMATCH")
    columns = parse_query(prior_sql.read_bytes())
    require(columns == EXPECTED_COLUMNS, "PRIOR_QUERY_COLUMNS_MISMATCH")
    saved_receipt = prior_registry["saved_v2_receipt"]
    check_hash(Path(saved_receipt["path"]), saved_receipt["sha256"],
               "PRIOR_LOCAL_RECEIPT_HASH_MISMATCH")
    snapshot_meta, rows = inspect_snapshot(snapshot_path, columns, expected_snapshot_rows)
    snapshot_ids = {item[0] for item in rows.values()}
    require(snapshot_meta["sha256"] == snapshot_info["sha256"]
            and snapshot_meta["row_count"] == snapshot_info["row_count"]
            == prior_summary["local_snapshot_rows"]
            and snapshot_meta["unique_permno_count"] ==
            snapshot_info["unique_permno_count"] == prior_summary["local_unique_permnos"]
            and snapshot_meta["column_count"] == snapshot_info["column_count"]
            == prior_summary["local_snapshot_columns"]
            and prior_index.get("source_bindings", {}).get("snapshot_sha256") ==
            snapshot_info["sha256"], "SNAPSHOT_RECEIPT_MISMATCH")
    seed_ids = verify_seed_relationship(seed, prior_index, snapshot_ids,
                                         PRIOR_WRDS_HASHES["index"])
    require(summary.get("daily_seed_starting_permno_count") == len(seed_ids),
            "DELIST_SEED_COUNT_MISMATCH")
    for key, name in prior_names.items():
        check_hash(prior_dir / name, PRIOR_WRDS_HASHES[key],
                   "PRIOR_RECEIPT_CHANGED_DURING_READ")
    check_hash(prior_sql, prior_registry["query_sql_sha256"],
               "PRIOR_QUERY_CHANGED_DURING_READ")
    check_hash(Path(saved_receipt["path"]), saved_receipt["sha256"],
               "PRIOR_LOCAL_RECEIPT_CHANGED_DURING_READ")
    check_hash(delist_sql, registry["sql_sha256"],
               "DELIST_SQL_CHANGED_DURING_READ")
    check_hash(delist_csv, registry["sha256"],
               "DELIST_CSV_CHANGED_DURING_READ")
    for name in names:
        check_hash(source_dir / name, manifest["file_sha256"][name],
                   "DELIST_SOURCE_CHANGED_DURING_READ")
    check_hash(manifest_path, expected_manifest_sha256,
               "DELIST_MANIFEST_CHANGED_DURING_READ")
    check_hash(snapshot_path, snapshot_info["sha256"],
               "SNAPSHOT_CHANGED_DURING_READ")
    return {"ids": seed_ids, "seed_sha256": manifest["file_sha256"][SEED_NAME],
            "manifest_sha256": expected_manifest_sha256,
            "delist_registry_sha256": manifest["file_sha256"]["m5_delist_registry.json"],
            "delist_summary_sha256": manifest["file_sha256"]["m5_delist_summary.json"],
            "delist_sql_sha256": registry["sql_sha256"],
            "delist_csv_sha256": registry["sha256"],
            "prior_registry_sha256": PRIOR_WRDS_HASHES["registry"],
            "prior_index_sha256": PRIOR_WRDS_HASHES["index"],
            "prior_summary_sha256": PRIOR_WRDS_HASHES["summary"],
            "snapshot_path": str(snapshot_path.resolve()),
            "snapshot_sha256": snapshot_info["sha256"],
            "snapshot_rows": snapshot_meta["row_count"],
            "prior_query_sha256": prior_registry["query_sql_sha256"],
            "prior_local_receipt_sha256": saved_receipt["sha256"]}


def upload_bytes(ids: set[str]) -> bytes:
    checked_ids(list(ids), "UPLOAD_IDENTIFIERS_INVALID")
    return "".join(f"{value}\n" for value in sorted(ids, key=lambda value: (len(value), value))).encode("ascii")


def readback_upload(path: Path, expected_ids: set[str], expected_hash: str) -> int:
    data = path.read_bytes()
    require(data and data.endswith(b"\n") and b"\r" not in data
            and not data.startswith(b"\xef\xbb\xbf"), "UPLOAD_FORMAT_INVALID")
    try:
        lines = data.decode("ascii").splitlines()
    except UnicodeError as exc:
        raise AuditError("UPLOAD_ENCODING_INVALID") from exc
    actual_ids = checked_ids(lines, "UPLOAD_IDENTIFIERS_INVALID")
    require(actual_ids == expected_ids, "UPLOAD_SET_MISMATCH")
    require(hashlib.sha256(data).hexdigest() == expected_hash,
            "UPLOAD_HASH_MISMATCH")
    return len(lines)


def plan_for(upload_sha256: str, count: int) -> dict:
    return {"schema": "m5_daily_download_plan_private_v1",
            "scope": "DOWNLOAD_INPUT_ONLY_NOT_FORMAL_COHORT",
            "status": "PROPOSED_MANUAL_WRDS_QUERIES_NOT_SUBMITTED",
            "product_navigation": "CRSP > Quarterly Update > Stock - Version 2 > Daily Stock File",
            "vendor_source_role": "StkDlySecurityData",
            "wrds_table_identity": "VERIFY_IN_AUTHENTICATED_DICTIONARY_AND_EXPORT_SQL",
            "company_code_type": "PERMNO", "company_code_file": UPLOAD_NAME,
            "company_code_file_sha256": upload_sha256,
            "starting_identifier_count": count,
            "successor_ids_auto_added": False,
            "scope_filter": "ALL_BOUND_2020_STARTING_IDENTIFIERS_ONLY",
            "do_not_add_filters": ["current ticker", "positive price", "complete return",
                                   "current survival", "market", "security type"],
            "segments_inclusive": [{"start": start, "end": end}
                                   for start, end in SEGMENTS],
            "segments_are_train_validation_test_split": False,
            "first_segment_is_structure_check": True,
            "wrds_queries_submitted": 0}


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def ensure_ignored(output: Path) -> None:
    artifacts = ROOT / "artifacts"
    require(output.is_relative_to(artifacts) and output != artifacts,
            "OUTPUT_OUTSIDE_LOCAL_ARTIFACTS")
    for path in (output, output / UPLOAD_NAME, output / PLAN_NAME,
                 output / REGISTRY_NAME):
        relative = path.relative_to(ROOT)
        ignored = subprocess.run(["git", "check-ignore", "-q", "--", str(relative)],
                                 cwd=ROOT, capture_output=True, check=False)
        tracked = subprocess.run(["git", "ls-files", "--error-unmatch", "--",
                                  str(relative)], cwd=ROOT, capture_output=True,
                                 check=False)
        require(ignored.returncode == 0 and tracked.returncode != 0,
                "OUTPUT_NOT_IGNORED_OR_IS_TRACKED")


def git_identity() -> dict:
    values = {}
    for key, command in (("head", ["git", "rev-parse", "HEAD"]),
                         ("branch", ["git", "branch", "--show-current"])):
        result = subprocess.run(command, cwd=ROOT, capture_output=True,
                                text=True, check=False)
        require(result.returncode == 0 and result.stdout.strip(),
                "GIT_IDENTITY_UNAVAILABLE")
        values[key] = result.stdout.strip()
    return values


def verify_saved(output: Path, bundle: dict, code_sha256: str,
                 doc_sha256: str, expected_upload_sha256: str) -> dict:
    registry = read_json(output / REGISTRY_NAME)
    plan = read_json(output / PLAN_NAME)
    require(registry.get("schema") == "m5_daily_upload_registry_private_v1"
            and registry.get("scope") == "DOWNLOAD_INPUT_ONLY_NOT_FORMAL_COHORT"
            and registry.get("status") == "M5_DAILY_PERMNO_UPLOAD_READY"
            and registry.get("formal_m5_cohort_selected") is False
            and registry.get("wrds_queries_submitted") == 0
            and registry.get("conversion_code_sha256") == code_sha256
            and registry.get("download_doc_sha256") == doc_sha256,
            "UPLOAD_REGISTRY_INVALID")
    bindings = {key: value for key, value in bundle.items() if key != "ids"}
    require(registry.get("input_bindings") == bindings,
            "UPLOAD_SOURCE_BINDING_MISMATCH")
    require(registry.get("upload_sha256") == expected_upload_sha256
            and registry.get("upload_file") == UPLOAD_NAME
            and registry.get("encoding") == "ASCII"
            and registry.get("newline") == "LF_FINAL_NEWLINE",
            "UPLOAD_REGISTRY_OUTPUT_MISMATCH")
    expected_plan = plan_for(expected_upload_sha256, len(bundle["ids"]))
    require(plan == expected_plan
            and sha256_file(output / PLAN_NAME) == registry.get("plan_sha256"),
            "DOWNLOAD_PLAN_MISMATCH")
    line_count = readback_upload(output / UPLOAD_NAME, bundle["ids"],
                                 expected_upload_sha256)
    require(registry.get("line_count") == line_count
            and registry.get("unique_count") == line_count
            and registry.get("set_equal_to_seed_index_and_snapshot") is True,
            "UPLOAD_COUNT_OR_SET_MISMATCH")
    return registry


def choose_output(base: Path, bundle: dict, code_sha256: str,
                  doc_sha256: str, upload_sha256: str) -> tuple[Path, bool]:
    require(re.fullmatch(r"m5_daily_download_prepare_[0-9]{8}_v[1-9][0-9]*",
                         base.name) is not None, "OUTPUT_VERSION_NAME_INVALID")
    prefix, number = base.name.rsplit("_v", 1)
    for version in range(int(number), int(number) + 1000):
        candidate = base.with_name(f"{prefix}_v{version}").resolve()
        ensure_ignored(candidate)
        if not candidate.exists():
            return candidate, False
        try:
            verify_saved(candidate, bundle, code_sha256, doc_sha256, upload_sha256)
            return candidate, True
        except (AuditError, OSError, UnicodeError, ValueError, KeyError, TypeError):
            continue
    raise AuditError("OUTPUT_VERSION_SPACE_EXHAUSTED")


def publish(output: Path, upload: bytes, plan: dict, registry: dict,
            bundle: dict, code_sha256: str, doc_sha256: str) -> None:
    ensure_ignored(output)
    require(not output.exists(), "OUTPUT_ALREADY_EXISTS")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="m5-daily-upload-stage-",
                                     dir=output.parent) as stage_name:
        stage = Path(stage_name)
        for name, data in ((UPLOAD_NAME, upload), (PLAN_NAME, json_bytes(plan)),
                           (REGISTRY_NAME, json_bytes(registry))):
            with (stage / name).open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        verify_saved(stage, bundle, code_sha256, doc_sha256,
                     registry["upload_sha256"])
        require(not output.exists(), "OUTPUT_ALREADY_EXISTS")
        stage.rename(output)


def prepare(source_dir: Path = SOURCE_DIR, output_base: Path = OUTPUT_DIR) -> tuple[Path, bool]:
    doc_sha256 = verify_download_window(DOWNLOAD_DOC)
    bundle = verify_source_bundle(source_dir.resolve(strict=True))
    code_sha256 = sha256_file(Path(__file__).resolve())
    upload = upload_bytes(bundle["ids"])
    upload_sha256 = hashlib.sha256(upload).hexdigest()
    output, reused = choose_output(output_base.resolve(), bundle, code_sha256,
                                   doc_sha256, upload_sha256)
    if not reused:
        plan = plan_for(upload_sha256, len(bundle["ids"]))
        registry = {
            "schema": "m5_daily_upload_registry_private_v1",
            "scope": "DOWNLOAD_INPUT_ONLY_NOT_FORMAL_COHORT",
            "status": "M5_DAILY_PERMNO_UPLOAD_READY",
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "input_bindings": {key: value for key, value in bundle.items() if key != "ids"},
            "conversion_code_sha256": code_sha256,
            "conversion_code_path": str(Path(__file__).resolve()),
            "download_doc_sha256": doc_sha256,
            "python_executable": sys.executable,
            "python_version": sys.version,
            "git_identity": git_identity(),
            "pythonpath_src_present": str(ROOT / "src").lower() in
            [part.lower() for part in os.environ.get("PYTHONPATH", "").split(os.pathsep)],
            "command": "prepare", "pre_publish_readback_required": True,
            "upload_file": UPLOAD_NAME, "upload_sha256": upload_sha256,
            "line_count": len(bundle["ids"]), "unique_count": len(bundle["ids"]),
            "set_equal_to_seed_index_and_snapshot": True,
            "encoding": "ASCII", "newline": "LF_FINAL_NEWLINE",
            "plan_sha256": hashlib.sha256(json_bytes(plan)).hexdigest(),
            "formal_m5_cohort_selected": False, "wrds_queries_submitted": 0,
        }
        publish(output, upload, plan, registry, bundle, code_sha256,
                doc_sha256)
    verify_saved(output, bundle, code_sha256, doc_sha256, upload_sha256)
    return output, reused


def verify_output(output: Path, source_dir: Path = SOURCE_DIR) -> None:
    doc_sha256 = verify_download_window(DOWNLOAD_DOC)
    bundle = verify_source_bundle(source_dir.resolve(strict=True))
    upload_sha256 = hashlib.sha256(upload_bytes(bundle["ids"])).hexdigest()
    ensure_ignored(output.resolve(strict=True))
    verify_saved(output.resolve(), bundle, sha256_file(Path(__file__).resolve()),
                 doc_sha256, upload_sha256)


def save_rejection(code: str, missing_path: str | None) -> None:
    """Keep a fixed-code local diagnostic without saving identifiers."""
    parent = ROOT / "artifacts"
    for version in range(1, 1000):
        output = parent / f"m5_daily_download_prepare_20261111_rejected_v{version}"
        ensure_ignored(output)
        if output.exists():
            continue
        record = {"schema": "m5_daily_upload_rejection_private_v1",
                  "scope": "DOWNLOAD_INPUT_ONLY_NOT_FORMAL_COHORT",
                  "status": "REJECTED_LOCAL_SOURCE_OR_OUTPUT_BINDING",
                  "issue_code": code, "missing_path": missing_path,
                  "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
                  "conversion_code_sha256": sha256_file(Path(__file__).resolve()),
                  "command_exit_code": 2, "wrds_queries_submitted": 0,
                  "formal_m5_cohort_selected": False}
        with tempfile.TemporaryDirectory(prefix="m5-daily-rejection-stage-",
                                         dir=parent) as stage_name:
            stage = Path(stage_name)
            (stage / "m5_daily_upload_rejection_private.json").write_bytes(
                json_bytes(record))
            stage.rename(output)
        return


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "verify-output"))
    parser.add_argument("--source-dir", type=Path, default=SOURCE_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            prepare(args.source_dir, args.output_dir)
            print("M5_DAILY_PERMNO_UPLOAD_READY")
        else:
            verify_output(args.output_dir, args.source_dir)
            print("M5_DAILY_PERMNO_UPLOAD_SAVED_OUTPUT_VERIFIED")
        return 0
    except FileNotFoundError as exc:
        code, missing = "MISSING_LOCAL_FILE", exc.filename
    except AuditError as exc:
        code, missing = str(exc), None
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, csv.Error):
        code, missing = "LOCAL_IO_OR_FORMAT_ERROR", None
    try:
        save_rejection(code, missing)
    except (AuditError, OSError):
        pass
    print("M5_DAILY_PERMNO_UPLOAD_REVIEW_REQUIRED", code,
          missing if missing is not None else "")
    return 2


if __name__ == "__main__":
    sys.exit(main())
