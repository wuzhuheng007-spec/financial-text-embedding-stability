"""Review one saved 2020 distribution-only multi-sequence key offline.

The result is an engineering diagnosis of saved extracts. Row-level values and
identifiers remain in Git-ignored artifacts; the CLI emits fixed status codes.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path, PurePosixPath
import sqlite3
import sys
import zipfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import audit_m5_distribution_multiyear as merged


ART = ROOT / "artifacts"
MERGED_DIR = ART / "m5_distributions_20200101_20260228_20261112_v1"
INVENTORY = ART / "m5_daily_eight_segments_receipt_review_20261112_v1" / (
    "m5_daily_eight_segments_inventory_safe.json")
M5_CANDIDATE = ROOT / "configs" / "m5_financial_protocol_round8.json"
HANDOFF_SHA = "a6743f5a03f2deaf002d782292bdc160ae03d01ddb0de6c977ebc468172a8800"
PRIOR_RESULTS_SHA = "91d0628b28e934e038f5a6b9ee4ec5a53bc5f049c469574caab543e81f8bd601"
PRIOR_RESULTS_MEMBER = (
    "10_INPUT_RESULTS/M5_distributions_2020_2026Feb_Codex_results_20261112.zip")
MERGED_CODE_SHA = "89eeb259889b762d1bcee825598f475c271b87852549b668c1179a2cee51d221"
MERGED_CSV_SHA = "e4116783a981161f6bd2da06e9acd3f682cb0d6fdd5eb6da3cb71e56285306f1"
MERGED_SUMMARY_SHA = "ebd24c590ed63b5ea4b9d2b02ea63364be6b53fc173ec7d23dff70a7a56bdf91"
MERGED_COMPARISON_SHA = "c9266b58b85a57bedbb21896810bce6d05bd8e587df7bebb4b4fb309c28e177c"
MERGED_MANIFEST_SHA = "2a121219d67aabb181668987745e42e261bce739890fbac26082ea2b64b18a53"
INVENTORY_SHA = "13cbec98b9ec2d6ef59a6b8f42b89a872b6789388861d114fe8413bbef6a64cd"
DAILY_SUMMARY_SHA = "4e097ead82585135b4cc9a744f3e6100f5e81dafabf6e10457f0c468462f7b4f"
DAILY_MANIFEST_SHA = "2d5160bbdb88497d5f02d8e3395a88e119749dda7ea20c159295464795110008"
DAILY_EXCEPTION_SHA = "4cb63491c080b372d24abc1cf748b27411b7136ef3faa1da78eafb081f127ecd"
DAILY_INDEX_SHA = "bd8db7f91d25241d6f043aa31acc2e89f4879abf03d1be0a3d805d643b32663e"
DAILY_CSV_SHA = "f8442af8835c83db88051c2a014944fe4534448ab8edb515423e897e057e86ac"
M5_CANDIDATE_SHA = "f1fa28949353adea6aa179854441b48658415ef577a006fe76bd01c779ddaa38"
TARGET_STATE = "DISTRIBUTION_MULTI_SEQUENCE_ONLY_RELATIVE_TO_DAILY_DUPLICATE_SET"
SAFE = "single_group_summary_safe.json"
PRIVATE = "single_group_detail_private.json"
SOURCE = "source_registry_private.json"
MANIFEST = "output_manifest_private.json"
EXECUTION = "execution_private.json"

sha = merged.sha
require = merged.require
json_bytes = merged.json_bytes
write_json_new = merged.write_json_new
read_json = merged.read_json
IntakeError = merged.IntakeError


def checked(path: Path, expected: str, code: str) -> None:
    require(path.is_file() and sha(path) == expected, code)


def inside_root(relative: str) -> Path:
    require(merged.base.safe_member_name(relative), "REGISTERED_PATH_INVALID")
    target = (ROOT / PurePosixPath(relative)).resolve()
    require(ROOT.resolve() in target.parents, "REGISTERED_PATH_OUTSIDE_REPO")
    return target


def handoff_identity(path: Path) -> None:
    checked(path, HANDOFF_SHA, "HANDOFF_IDENTITY_MISMATCH")
    with merged.base.checked_zip(path) as archive:
        manifest = json.loads(archive.read("05_FILE_MANIFEST.json"))
        entries = manifest.get("files")
        require(manifest.get("schema") == "m5_single_group_followup_handoff_manifest_v1"
                and isinstance(entries, dict)
                and set(entries) == set(archive.namelist()) - {"05_FILE_MANIFEST.json"},
                "HANDOFF_MANIFEST_INVALID")
        for name, item in entries.items():
            data = archive.read(name)
            require(len(data) == item["bytes"]
                    and hashlib.sha256(data).hexdigest() == item["sha256"],
                    "HANDOFF_MEMBER_MISMATCH")
        nested = archive.read(PRIOR_RESULTS_MEMBER)
        require(hashlib.sha256(nested).hexdigest() == PRIOR_RESULTS_SHA,
                "PRIOR_RESULTS_PACKAGE_MISMATCH")


def _unique_saved(manifest: dict, suffix: str) -> tuple[str, str]:
    matches = [(name, value) for name, value in manifest["saved_file_sha256"].items()
               if name.endswith(suffix)]
    require(len(matches) == 1, "REGISTERED_FILE_NOT_UNIQUE")
    return matches[0]


def bind_sources(handoff: Path, *, merged_dir: Path = MERGED_DIR,
                 inventory_path: Path = INVENTORY) -> dict:
    """Bind saved files without rerunning either historical CSV audit."""
    handoff_identity(handoff)
    checked(ROOT / "scripts/audit_m5_distribution_multiyear.py", MERGED_CODE_SHA,
            "MERGED_CODE_CHANGED")
    checked(M5_CANDIDATE, M5_CANDIDATE_SHA, "M5_CANDIDATE_CHANGED")
    checked(inventory_path, INVENTORY_SHA, "DAILY_INVENTORY_CHANGED")
    checked(merged_dir / "output_manifest_private.json", MERGED_MANIFEST_SHA,
            "MERGED_MANIFEST_CHANGED")
    merged_manifest = read_json(merged_dir / "output_manifest_private.json")
    merged_registry = read_json(merged_dir / "source_registry_private.json")
    require(merged_manifest.get("schema") == "m5_distribution_multiyear_manifest_private_v1"
            and merged_registry.get("schema") == "m5_distribution_multiyear_source_registry_private_v1"
            and merged_manifest.get("audit_code_sha256") == MERGED_CODE_SHA
            and merged_registry.get("audit_code_sha256") == MERGED_CODE_SHA,
            "MERGED_RECEIPT_IDENTITY_MISMATCH")
    saved = merged_manifest["saved_file_sha256"]
    for name in (merged.EVENT_DB, merged.COMPARISON_PRIVATE, merged.SUMMARY,
                 merged.ANNUAL, merged.COMPARISON, merged.SOURCE_REGISTRY):
        checked(merged_dir / name, saved[name], "MERGED_SAVED_FILE_CHANGED")
    checked(merged_dir / merged.SUMMARY, MERGED_SUMMARY_SHA,
            "MERGED_SUMMARY_CHANGED")
    checked(merged_dir / merged.COMPARISON, MERGED_COMPARISON_SHA,
            "MERGED_COMPARISON_CHANGED")
    csv_path = merged_dir / "raw" / merged.CSV_MEMBER
    checked(csv_path, MERGED_CSV_SHA, "MERGED_CSV_CHANGED")
    require(merged_manifest["source_file_identities"]["raw/" + merged.CSV_MEMBER]
            == MERGED_CSV_SHA, "MERGED_CSV_BINDING_MISMATCH")
    merged_summary = read_json(merged_dir / merged.SUMMARY)
    merged_comparison = read_json(merged_dir / merged.COMPARISON)
    require(merged_summary.get("status") == "PASS_STRUCTURE_ONLY"
            and merged_summary.get("source_csv_sha256") == MERGED_CSV_SHA
            and merged_comparison["segments"]["2020"]["distribution_only_group_count"] == 1
            and merged_comparison["segments"]["2020"]["daily_only_group_count"] == 0,
            "MERGED_EXPECTED_RESULT_MISMATCH")

    inventory = read_json(inventory_path)
    rows = [row for row in inventory["segments"] if row.get("year") == 2020]
    require(len(rows) == 1, "DAILY_2020_INVENTORY_NOT_UNIQUE")
    row = rows[0]
    summary_path = inside_root(row["summary_path"])
    manifest_path = inside_root(row["manifest_path"])
    checked(summary_path, DAILY_SUMMARY_SHA, "DAILY_SUMMARY_CHANGED")
    checked(manifest_path, DAILY_MANIFEST_SHA, "DAILY_MANIFEST_CHANGED")
    require(row["summary_sha256"] == DAILY_SUMMARY_SHA
            and row["manifest_sha256"] == DAILY_MANIFEST_SHA,
            "DAILY_INVENTORY_BINDING_MISMATCH")
    daily_manifest = read_json(manifest_path)
    daily_summary = read_json(summary_path)
    require(daily_manifest.get("schema") == "m5_daily_2020_manifest_private_v1"
            and daily_manifest.get("source_csv_sha256") == DAILY_CSV_SHA
            and daily_summary.get("csv_sha256") == DAILY_CSV_SHA
            and daily_summary.get("duplicate_key_group_count") == 325
            and daily_summary.get("distinct_date_count") == 253
            and daily_summary.get("scope") == "ENGINEERING_ONLY"
            and all(daily_summary.get(field) == 0 for field in (
                "invalid_date_row_count", "invalid_permno_row_count",
                "invalid_row_width_count", "outside_requested_window_row_count",
                "outside_upload_row_count")),
            "DAILY_2020_RECEIPT_IDENTITY_MISMATCH")
    directory = manifest_path.parent
    index_name, index_hash = _unique_saved(daily_manifest, "key_index_private.sqlite3")
    exception_name, exception_hash = _unique_saved(daily_manifest, "exceptions_private.jsonl")
    csv_name, csv_hash = _unique_saved(daily_manifest, ".csv")
    registry_name, registry_hash = _unique_saved(daily_manifest, "registry_private.json")
    require(index_hash == DAILY_INDEX_SHA and exception_hash == DAILY_EXCEPTION_SHA
            and csv_hash == DAILY_CSV_SHA,
            "DAILY_2020_FILE_BINDING_MISMATCH")
    for name, expected in ((exception_name, exception_hash), (csv_name, csv_hash),
                           (registry_name, registry_hash)):
        checked(directory / name, expected, "DAILY_2020_SAVED_FILE_CHANGED")
    index_path = directory / index_name
    actual_index_hash = sha(index_path) if index_path.is_file() else None
    index_status = ("BOUND_FULL_INDEX_VERIFIED" if actual_index_hash == DAILY_INDEX_SHA
                    else "NOT_VERIFIED_INDEX_MISSING" if actual_index_hash is None
                    else "SOURCE_MISMATCH_INDEX_HASH")
    daily_registry = read_json(directory / registry_name)
    code_path = Path(daily_registry["audit_code_path"])
    require(daily_registry.get("scope") == "ENGINEERING_ONLY"
            and daily_registry.get("audit_code_sha256")
            == daily_manifest.get("audit_code_sha256"), "DAILY_CODE_BINDING_MISMATCH")
    checked(code_path, daily_manifest["audit_code_sha256"], "DAILY_CODE_CHANGED")
    if index_status == "BOUND_FULL_INDEX_VERIFIED":
        with closing(sqlite3.connect(
                f"file:{index_path.as_posix()}?mode=ro&immutable=1", uri=True)) as db:
            columns = {item[1] for item in db.execute("PRAGMA table_info(record_key)")}
            require({"permno", "session", "has_duplicate", "has_conflict"} <= columns,
                    "DAILY_FULL_INDEX_SCHEMA_MISMATCH")
            key_count, duplicate_count = db.execute(
                "SELECT COUNT(*),SUM(has_duplicate) FROM record_key").fetchone()
        require(key_count == daily_manifest["index_semantic_fingerprints"]["record_key"]["row_count"]
                and duplicate_count == daily_summary["duplicate_key_group_count"]
                and key_count + daily_summary["duplicate_extra_row_count"]
                == daily_summary["row_count"],
                "DAILY_FULL_INDEX_COUNT_MISMATCH")
    return {
        "handoff": handoff, "merged_dir": merged_dir, "inventory": inventory_path,
        "merged_event_db": merged_dir / merged.EVENT_DB,
        "merged_comparison_private": merged_dir / merged.COMPARISON_PRIVATE,
        "merged_summary": merged_summary,
        "merged_annual": read_json(merged_dir / merged.ANNUAL),
        "merged_comparison": merged_comparison,
        "daily_summary": daily_summary,
        "daily_index": index_path if index_status == "BOUND_FULL_INDEX_VERIFIED" else None,
        "daily_index_path": index_path,
        "daily_index_status": index_status,
        "daily_exceptions": directory / exception_name,
        "daily_csv": directory / csv_name,
        "daily_manifest": manifest_path,
        "daily_registry": directory / registry_name,
        "daily_audit_code": code_path,
        "file_sha256": {
            "handoff_zip": HANDOFF_SHA,
            "nested_prior_results_zip": PRIOR_RESULTS_SHA,
            "merged_summary": MERGED_SUMMARY_SHA,
            "merged_manifest": MERGED_MANIFEST_SHA,
            "merged_comparison": MERGED_COMPARISON_SHA,
            "merged_event_index": saved[merged.EVENT_DB],
            "merged_private_comparison": saved[merged.COMPARISON_PRIVATE],
            "merged_csv": MERGED_CSV_SHA,
            "daily_inventory": INVENTORY_SHA,
            "daily_summary": DAILY_SUMMARY_SHA,
            "daily_manifest": DAILY_MANIFEST_SHA,
            "daily_full_index_expected": DAILY_INDEX_SHA,
            "daily_full_index_actual": actual_index_hash,
            "daily_exceptions": DAILY_EXCEPTION_SHA,
            "daily_csv": DAILY_CSV_SHA,
            "daily_audit_code": daily_manifest["audit_code_sha256"],
            "m5_candidate": M5_CANDIDATE_SHA,
            "targeted_review_code": sha(Path(__file__)),
        },
        "query_gaps": {
            "distribution_native_sql": "NOT_LOCATED",
            "distribution_server_input_dat": "NOT_BOUND",
            "daily_backend_sql_and_join": "NOT_VERIFIED",
            "provider_release": "UNKNOWN",
            "license_export_scope": "UNKNOWN",
            "formal_m5_admission": False,
        },
    }


def _distribution_dates(event_db: sqlite3.Connection) -> set[str]:
    return {value for (value,) in event_db.execute(
        "SELECT DISTINCT exdate FROM event_row WHERE valid_date=1 AND in_window=1 "
        "AND exdate BETWEEN '2020-01-01' AND '2020-12-31'")}


def diagnose_target(event_db_path: Path, comparison_jsonl: Path,
                    daily_db_path: Path | None, daily_exceptions: Path | None,
                    *, expected_dist_dates: int, expected_daily_dates: int) -> tuple[dict, dict]:
    """Return safe aggregate and private details from bound saved indexes."""
    targets = []
    with comparison_jsonl.open("r", encoding="utf-8") as source:
        for line in source:
            item = json.loads(line)
            if item.get("segment") == "2020" and item.get("comparison_state") == TARGET_STATE:
                targets.append(item)
    require(len(targets) == 1, "RESULT_MISMATCH")
    target = targets[0]
    identifier, day = target["permno"], target["session"]
    require(isinstance(identifier, str) and merged.base.ID_PATTERN.fullmatch(identifier)
            and isinstance(day, str) and day.startswith("2020-"),
            "TARGET_KEY_INVALID")
    with closing(sqlite3.connect(
            f"file:{event_db_path.as_posix()}?mode=ro&immutable=1", uri=True)) as event_db:
        columns = {item[1] for item in event_db.execute("PRAGMA table_info(event_row)")}
        require({"permno", "exdate", "seq", "row_sha", "valid_date", "in_window"} <= columns,
                "MERGED_EVENT_INDEX_SCHEMA_MISMATCH")
        event_rows = event_db.execute(
            "SELECT row_no,seq,row_sha,event_sha,header_sha FROM event_row "
            "WHERE permno=? AND exdate=? AND valid_id=1 AND valid_date=1 AND in_window=1 "
            "ORDER BY row_no", (identifier, day)).fetchall()
        sequence_count = len({row[1] for row in event_rows if row[1] is not None})
        require(len(event_rows) == target["distribution_row_count"]
                and sequence_count == target["distribution_distinct_sequence_count"]
                and sequence_count > 1,
                "TARGET_DISTRIBUTION_COUNT_MISMATCH")
        distribution_dates = _distribution_dates(event_db)
        require(len(distribution_dates) == expected_dist_dates,
                "SAVED_DATE_SET_COUNT_MISMATCH")
        if daily_db_path is None or daily_exceptions is None:
            safe = {
                "schema": "m5_distribution_2020_single_group_safe_v1",
                "scope": "ENGINEERING_ONLY",
                "target_group_count": 1,
                "distribution_event_row_count": len(event_rows),
                "distribution_distinct_sequence_count": sequence_count,
                "daily_target_key_row_count": None,
                "classification": "NOT_VERIFIED",
                "daily_full_index_available": False,
                "distribution_date_count": len(distribution_dates),
                "daily_date_count": None,
                "date_intersection_count": None,
                "distribution_only_date_count": None,
                "daily_only_date_count": None,
                "target_date_in_daily_saved_set": None,
                "target_security_seen_on_any_daily_date": None,
                "target_security_seen_on_other_daily_date": None,
                "target_date_in_distribution_only_set": None,
                "formal_m5_admission": False,
            }
            return safe, {"target": target, "distribution_event_rows": event_rows,
                          "daily_source_unavailable": True}
        with closing(sqlite3.connect(
                f"file:{daily_db_path.as_posix()}?mode=ro&immutable=1", uri=True)) as daily_db:
            daily_dates = {value for (value,) in daily_db.execute(
                "SELECT DISTINCT session FROM record_key")}
            require(len(daily_dates) == expected_daily_dates,
                    "SAVED_DATE_SET_COUNT_MISMATCH")
            daily_key = daily_db.execute(
                "SELECT first_row,first_sha,has_duplicate,has_conflict FROM record_key "
                "WHERE permno=? AND session=?", (identifier, day)).fetchone()
            seen_any = daily_db.execute(
                "SELECT EXISTS(SELECT 1 FROM record_key WHERE permno=? LIMIT 1)",
                (identifier,)).fetchone()[0] == 1
            previous = daily_db.execute(
                "SELECT MAX(session) FROM record_key WHERE permno=? AND session<?",
                (identifier, day)).fetchone()[0]
            following = daily_db.execute(
                "SELECT MIN(session) FROM record_key WHERE permno=? AND session>?",
                (identifier, day)).fetchone()[0]
            extras = 0
            with daily_exceptions.open("r", encoding="utf-8") as source:
                for line in source:
                    item = json.loads(line)
                    if (item.get("permno") == identifier and item.get("dlycaldt") == day
                            and "PERMNO_DATE_DUPLICATE" in item.get("codes", [])):
                        extras += 1
            if daily_key is None:
                require(extras == 0, "DAILY_INDEX_EXCEPTION_MISMATCH")
                daily_count, classification = 0, "NO_SAVED_DAILY_ROW_FOR_TARGET_KEY"
            elif daily_key[2] == 0:
                require(daily_key[3] == 0, "DAILY_INDEX_FLAG_MISMATCH")
                require(extras == 0, "DAILY_INDEX_EXCEPTION_MISMATCH")
                daily_count, classification = 1, "ONE_SAVED_DAILY_ROW_NO_DUPLICATE"
            else:
                require(extras > 0, "DAILY_INDEX_EXCEPTION_MISMATCH")
                daily_count, classification = 1 + extras, "MULTIPLE_SAVED_DAILY_ROWS_REVIEW_REQUIRED"
            distribution_only = distribution_dates - daily_dates
            daily_only = daily_dates - distribution_dates
            safe = {
                "schema": "m5_distribution_2020_single_group_safe_v1",
                "scope": "ENGINEERING_ONLY",
                "target_group_count": 1,
                "distribution_event_row_count": len(event_rows),
                "distribution_distinct_sequence_count": sequence_count,
                "daily_target_key_row_count": daily_count,
                "classification": classification,
                "daily_full_index_available": True,
                "distribution_date_count": len(distribution_dates),
                "daily_date_count": len(daily_dates),
                "date_intersection_count": len(distribution_dates & daily_dates),
                "distribution_only_date_count": len(distribution_only),
                "daily_only_date_count": len(daily_only),
                "target_date_in_daily_saved_set": day in daily_dates,
                "target_security_seen_on_any_daily_date": seen_any,
                "target_security_seen_on_other_daily_date": previous is not None or following is not None,
                "target_date_in_distribution_only_set": day in distribution_only,
                "formal_m5_admission": False,
            }
            private = {
                "schema": "m5_distribution_2020_single_group_detail_private_v1",
                "target": target,
                "distribution_event_rows": [
                    {"row_number": row[0], "sequence": row[1], "row_sha256": row[2],
                     "event_sha256": row[3], "header_sha256": row[4]} for row in event_rows],
                "daily_first_row_number": daily_key[0] if daily_key else None,
                "daily_first_row_sha256": daily_key[1] if daily_key else None,
                "daily_has_duplicate_flag": bool(daily_key[2]) if daily_key else None,
                "daily_has_conflict_flag": bool(daily_key[3]) if daily_key else None,
                "daily_exception_extra_row_count": extras,
                "nearest_previous_saved_date_for_security": previous,
                "nearest_following_saved_date_for_security": following,
                "distribution_only_saved_dates": sorted(distribution_only),
                "daily_only_saved_dates": sorted(daily_only),
            }
            return safe, private


def derive(sources: dict) -> tuple[dict, dict]:
    safe, private = diagnose_target(
        sources["merged_event_db"], sources["merged_comparison_private"],
        sources["daily_index"],
        sources["daily_exceptions"] if sources["daily_index"] is not None else None,
        expected_dist_dates=sources["merged_annual"]["segments"]["2020"]["actual_disexdt_count"],
        expected_daily_dates=sources["daily_summary"]["distinct_date_count"])
    if sources["daily_index_status"] == "SOURCE_MISMATCH_INDEX_HASH":
        safe["classification"] = "SOURCE_MISMATCH"
    safe["daily_full_index_source_status"] = sources["daily_index_status"]
    safe["source_file_sha256"] = sources["file_sha256"]
    safe["source_gaps"] = sources["query_gaps"]
    safe["review_code_sha256"] = sha(Path(__file__))
    safe["source_status"] = (
        "BOUND_SAVED_INDEXES_VERIFIED"
        if safe["classification"] in ("NO_SAVED_DAILY_ROW_FOR_TARGET_KEY",
                                      "ONE_SAVED_DAILY_ROW_NO_DUPLICATE")
        else "SOURCE_MISMATCH" if safe["classification"] == "SOURCE_MISMATCH"
        else "NOT_VERIFIED" if safe["classification"] == "NOT_VERIFIED"
        else "REVIEW_REQUIRED")
    return safe, private


def verify_saved_derivation(output: Path, safe: dict, private: dict) -> None:
    require(json_bytes(safe) == (output / SAFE).read_bytes()
            and json_bytes(private) == (output / PRIVATE).read_bytes(),
            "SAVED_DERIVATION_MISMATCH")


def verify_output(output: Path) -> dict:
    merged.base.ignored_output(output)
    manifest = read_json(output / MANIFEST)
    source = read_json(output / SOURCE)
    require(manifest.get("schema") == "m5_distribution_2020_single_group_manifest_private_v1"
            and manifest.get("review_code_sha256") == sha(Path(__file__))
            and source.get("schema") == "m5_distribution_2020_single_group_source_private_v1"
            and source.get("review_code_sha256") == sha(Path(__file__)),
            "REVIEW_CODE_OR_MANIFEST_CHANGED")
    handoff = Path(source["handoff_zip_path"])
    sources = bind_sources(
        handoff, merged_dir=Path(source["merged_dir_path"]),
        inventory_path=Path(source["inventory_path"]))
    require(sources["file_sha256"] == manifest["source_file_sha256"],
            "SOURCE_FILE_IDENTITY_CHANGED")
    for name, expected in manifest["saved_file_sha256"].items():
        require(merged.base.safe_member_name(name), "OUTPUT_FILE_NAME_INVALID")
        checked(output / name, expected, "SAVED_OUTPUT_CHANGED")
    safe, private = derive(sources)
    verify_saved_derivation(output, safe, private)
    execution_path = output / EXECUTION
    if execution_path.exists():
        execution = read_json(execution_path)
        require(execution.get("schema") == "m5_distribution_2020_single_group_execution_private_v1"
                and execution.get("run_exit_code") == 0
                and execution.get("internal_verify_exit_code") == 0
                and execution.get("manifest_sha256") == sha(output / MANIFEST),
                "EXECUTION_LEDGER_INVALID")
    return safe


def run(handoff: Path, output: Path) -> dict:
    merged.base.ignored_output(output)
    require(not output.exists(), "OUTPUT_ALREADY_EXISTS")
    started = datetime.now(timezone.utc).isoformat()
    output.mkdir(parents=True, exist_ok=False)
    try:
        sources = bind_sources(handoff)
        safe, private = derive(sources)
        write_json_new(output / SAFE, safe)
        write_json_new(output / PRIVATE, private)
        source = {
            "schema": "m5_distribution_2020_single_group_source_private_v1",
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "handoff_zip_path": str(handoff.resolve()),
            "merged_dir_path": str(MERGED_DIR.resolve()),
            "inventory_path": str(INVENTORY.resolve()),
            "daily_index_path": str(sources["daily_index_path"].resolve()),
            "daily_csv_path": str(sources["daily_csv"].resolve()),
            "daily_manifest_path": str(sources["daily_manifest"].resolve()),
            "review_code_sha256": sha(Path(__file__)),
            "source_file_sha256": sources["file_sha256"],
            "python_executable": sys.executable,
            "queries_submitted": 0,
            "raw_csvs_rescanned": False,
            "model_or_financial_experiment_run": False,
        }
        write_json_new(output / SOURCE, source)
        manifest = {
            "schema": "m5_distribution_2020_single_group_manifest_private_v1",
            "review_code_sha256": sha(Path(__file__)),
            "source_file_sha256": sources["file_sha256"],
            "saved_file_sha256": {name: sha(output / name)
                                  for name in (SAFE, PRIVATE, SOURCE)},
        }
        write_json_new(output / MANIFEST, manifest)
        result = verify_output(output)
        write_json_new(output / EXECUTION, {
            "schema": "m5_distribution_2020_single_group_execution_private_v1",
            "started_at_utc": started,
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            "run_exit_code": 0,
            "internal_verify_exit_code": 0,
            "manifest_sha256": sha(output / MANIFEST),
            "peak_memory": "UNKNOWN_NOT_MEASURED",
        })
        return result
    except Exception as error:
        write_json_new(output / "failure_private.json", {
            "schema": "m5_distribution_2020_single_group_failure_private_v1",
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "error_code": error.code if isinstance(error, IntakeError)
                          else "UNHANDLED_SOURCE_OR_IO_ERROR",
            "error_type": type(error).__name__,
        })
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "verify-output"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--handoff-zip", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "run":
            require(args.handoff_zip is not None, "HANDOFF_ZIP_REQUIRED")
            result = run(args.handoff_zip, args.output_dir)
            prefix = "M5_DISTRIBUTION_SINGLE_GROUP_SAVED_"
        else:
            result = verify_output(args.output_dir)
            prefix = "M5_DISTRIBUTION_SINGLE_GROUP_SAVED_OUTPUT_VERIFIED_"
        print(prefix + result["classification"])
        return 0
    except Exception:
        print("M5_DISTRIBUTION_SINGLE_GROUP_REVIEW_REQUIRED")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
