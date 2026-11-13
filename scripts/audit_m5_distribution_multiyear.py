"""Offline M5 distribution intake for the 2020-2026 merged WRDS export.

All row-level material remains in ignored artifacts. Terminal output contains
only fixed statuses. This is engineering receipt work, not a financial study.
"""

from __future__ import annotations

import argparse
import collections
from contextlib import closing
import csv
import hashlib
import importlib.util
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts"
BASE_PATH = ROOT / "scripts" / "audit_m5_distribution_segment.py"
BASE_SHA = "ab6f37d07289f960232df8be7392acf450121e3ae66ad6c95d7230a0e6f3dec0"
BASE_SPEC = importlib.util.spec_from_file_location("m5_distribution_first_segment", BASE_PATH)
if BASE_SPEC is None or BASE_SPEC.loader is None:
    raise RuntimeError("BASE_MODULE_UNAVAILABLE")
base = importlib.util.module_from_spec(BASE_SPEC)
BASE_SPEC.loader.exec_module(base)
IntakeError = base.IntakeError
require = base.require
sha = base.sha
json_bytes = base.json_bytes
read_json = base.read_json
write_json_new = base.write_json_new

HANDOFF_SHA = "88433072a22f1f9635d1d5d962d257bc12e8c8d6575e4a5b6a4c47271034b67d"
RAW_ZIP_SHA = "7243e04cfa20557caefe5d2976a4bb78ae0d35026d1ca031599b992fc959de7c"
BOUND_SHA = base.BOUND_SHA
EIGHT_INVENTORY_SHA = "13cbec98b9ec2d6ef59a6b8f42b89a872b6789388861d114fe8413bbef6a64cd"
PRIOR_SAFE_ZIP_SHA = "9d87f1e287226a71e8e070cfc5dded9fbac2cd0023396bbf5363a0bfca927bfa"
PRIOR_SUMMARY_SHA = "17f5512d34588d525ac6e4b686f62f3545bf728ba67572b9f3f65845ba757685"
PRIOR_EVENT_INDEX_SHA = "c5335eb2a0c06f01efd8b6ca2b8ca85c34437e437e6caf5838328418f8e3f493"
M5_CANDIDATE_SHA = "f1fa28949353adea6aa179854441b48658415ef577a006fe76bd01c779ddaa38"
PACKAGE_MANIFEST = "06_PACKAGE_MANIFEST.json"
FIELD_MAPPING = "04_FIELD_MAPPING_SAFE.json"
PARAMETERS = "02_PARAMETERS_USER_PROVIDED.txt"
RAW_ZIP_MEMBER = "10_current_merged/raw/zgt8d3w8jqfnulor.csv.zip"
UPLOAD_MEMBER = "10_current_merged/raw/m5_daily_seed_permnos_upload (8).txt"
PRIOR_SAFE_MEMBER = "20_prior_verified_safe/M5_distributions_2019NovDec_Codex_results_20261112.zip"
CSV_MEMBER = "zgt8d3w8jqfnulor.csv"
QUERY_ID = "11901007"
PAGE_ROWS = 129215
WINDOW_START, WINDOW_END = date(2020, 1, 1), date(2026, 2, 28)
SEGMENTS = tuple((str(y), date(y, 1, 1), date(y, 12, 31)) for y in range(2020, 2026)) + (
    ("2026janfeb", date(2026, 1, 1), date(2026, 2, 28)),)
SEGMENT_WINDOWS = {name: (start, end) for name, start, end in SEGMENTS}
HEADER_CONTEXT = frozenset(("siccd", "nasdissuno", "primaryexch"))
OTHER_DATES = ("disdeclaredt", "disrecorddt", "dispaydt")
SELECTED_TYPES = frozenset(("CD", "CG", "CP", "FRS", "IN", "N/A", "ROC", "SD", "SP", "TSOO"))
BOUND_TXT = ART / "m5_daily_download_prepare_20261111_v1" / "m5_daily_seed_permnos_upload.txt"
EIGHT_INVENTORY = ART / "m5_daily_eight_segments_receipt_review_20261112_v1" / "m5_daily_eight_segments_inventory_safe.json"
PRIOR_DIR = ART / "m5_distributions_20191101_20191231_20261112_v1"
M5_CANDIDATE = ROOT / "configs" / "m5_financial_protocol_round8.json"
SUMMARY = "summary_safe.json"
ANNUAL = "annual_summary_safe.json"
COMPARISON = "daily_comparison_safe.json"
EVENT_DB = "event_index_private.sqlite3"
EXCEPTIONS = "exceptions_private.jsonl"
COMPARISON_PRIVATE = "daily_comparison_private.jsonl"
SOURCE_REGISTRY = "source_registry_private.json"
OUTPUT_MANIFEST = "output_manifest_private.json"
EXECUTION = "execution_private.json"


def object_sha(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                           .encode("utf-8")).hexdigest()


def segment_for(day: date | None) -> str | None:
    if day is None:
        return None
    for name, start, end in SEGMENTS:
        if start <= day <= end:
            return name
    return None


def source_gate(handoff: Path, bound_txt: Path, inventory: Path, prior_dir: Path,
                *, synthetic_test_only: bool) -> None:
    if synthetic_test_only:
        return
    require(sha(BASE_PATH) == BASE_SHA and sha(handoff) == HANDOFF_SHA and
            sha(bound_txt) == BOUND_SHA and sha(inventory) == EIGHT_INVENTORY_SHA and
            sha(prior_dir / SUMMARY) == PRIOR_SUMMARY_SHA and
            sha(prior_dir / EVENT_DB.replace(".sqlite3", ".jsonl")) == PRIOR_EVENT_INDEX_SHA and
            sha(M5_CANDIDATE) == M5_CANDIDATE_SHA, "REAL_SOURCE_BINDING_MISMATCH")


def inspect_mapping(mapping: dict, parameter_text: str) -> tuple[list[str], dict]:
    require(mapping.get("schema") == "m5_distribution_user_transcript_field_mapping_v1",
            "FIELD_MAPPING_SCHEMA_INVALID")
    fields = mapping.get("fields_in_ui_index_order")
    require(isinstance(fields, list) and len(fields) == 22, "FIELD_MAPPING_COUNT_INVALID")
    fields = sorted(fields, key=lambda item: item["ui_index"])
    names = [item["name"].strip().casefold() for item in fields]
    require(len(set(names)) == 22 and [item["ui_index"] for item in fields] == list(range(22)),
            "FIELD_MAPPING_INDEX_INVALID")
    require({item["table_id"] for item in fields} == {273698, 273706} and
            mapping.get("selected_fields_using_273776") == [],
            "FIELD_MAPPING_TABLE_ID_INVALID")
    for item in fields:
        expected_table = ("crsp_q_stock.stksecurityinfohdr"
                          if item["name"] in HEADER_CONTEXT else
                          "crsp_q_stock.stkdistributions")
        require(item["declared_table"] == expected_table and
                item["name"] in parameter_text, "FIELD_MAPPING_TRANSCRIPT_MISMATCH")
    required_tokens = (
        "begin_date\t:\t2020-01-01", "end_date\t:\t2026-02-28",
        "crsp_q_stock.stkdistributions.disexdt",
        "crsp_q_stock.stksecurityinfohdr.permno", "file_as_variable_flag",
        "source_saved_query\t:\t7991527", "compress\t:\tzip", "format\t:\tcsv",
    )
    require(all(token in parameter_text for token in required_tokens) and
            all("'" + code + "'" in parameter_text for code in SELECTED_TYPES),
            "PARAMETER_TRANSCRIPT_MISMATCH")
    return names, {
        "mapping_scope": "USER_PASTED_UI_TRANSCRIPT_NOT_NATIVE_EXPORT",
        "selected_field_count": 22,
        "declared_distribution_field_count": 19,
        "declared_header_field_count": 3,
        "header_context_fields": sorted(HEADER_CONTEXT),
        "selected_annual_header_lookup_count": 0,
        "ui_order_not_assumed_csv_order": True,
        "selected_distype_filter": sorted(SELECTED_TYPES),
        "source_saved_query_is_query_id": False,
        "native_sql_and_join_verified": False,
    }


def verify_transport_and_extract(handoff: Path, output: Path) -> dict:
    before = sha(handoff)
    with base.checked_zip(handoff) as outer:
        manifest = json.loads(outer.read(PACKAGE_MANIFEST))
        entries = manifest.get("files")
        require(manifest.get("schema") == "m5_distribution_merged_handoff_manifest_v1" and
                isinstance(entries, dict) and
                set(entries) == set(outer.namelist()) - {PACKAGE_MANIFEST},
                "PACKAGE_MANIFEST_INVALID")
        for name, meta in entries.items():
            content = outer.read(name)
            require(len(content) == meta.get("bytes") and
                    hashlib.sha256(content).hexdigest() == meta.get("sha256"),
                    "PACKAGE_MEMBER_HASH_MISMATCH")
        for member in outer.infolist():
            target = output / "package" / PurePosixPath(member.filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            with outer.open(member) as source, target.open("xb") as dest:
                shutil.copyfileobj(source, dest)
    require(sha(handoff) == before, "HANDOFF_CHANGED_DURING_READ")
    nested = output / "package" / RAW_ZIP_MEMBER
    require(sha(nested) == RAW_ZIP_SHA or before != HANDOFF_SHA,
            "RAW_ZIP_IDENTITY_MISMATCH")
    with base.checked_zip(nested, expected_names={CSV_MEMBER}) as inner:
        csv_path = output / "raw" / CSV_MEMBER
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with inner.open(CSV_MEMBER) as source, csv_path.open("xb") as dest:
            shutil.copyfileobj(source, dest)
        require(csv_path.stat().st_size == inner.getinfo(CSV_MEMBER).file_size,
                "CSV_MEMBER_SIZE_MISMATCH")
    prior = output / "package" / PRIOR_SAFE_MEMBER
    with base.checked_zip(prior) as prior_archive:
        prior_manifest = json.loads(prior_archive.read("11_FILE_MANIFEST.json"))
        prior_entries = prior_manifest.get("files")
        require(isinstance(prior_entries, dict) and
                set(prior_entries) == set(prior_archive.namelist()) - {"11_FILE_MANIFEST.json"},
                "PRIOR_SAFE_MANIFEST_INVALID")
        for name, meta in prior_entries.items():
            content = prior_archive.read(name)
            require(len(content) == meta["bytes"] and
                    hashlib.sha256(content).hexdigest() == meta["sha256"],
                    "PRIOR_SAFE_MEMBER_HASH_MISMATCH")
        prior_summary_bytes = prior_archive.read("03_MACHINE_SUMMARY_SAFE.json")
    require(sha(prior) == PRIOR_SAFE_ZIP_SHA or before != HANDOFF_SHA,
            "PRIOR_SAFE_ZIP_IDENTITY_MISMATCH")
    return {
        "handoff_zip_sha256": before,
        "nested_zip_sha256": sha(nested),
        "saved_csv_sha256": sha(csv_path),
        "saved_csv_size_bytes": csv_path.stat().st_size,
        "prior_safe_zip_sha256": sha(prior),
        "prior_safe_summary_sha256": hashlib.sha256(prior_summary_bytes).hexdigest(),
        "package_member_count": len(entries) + 1,
    }


def _bucket() -> dict:
    return {
        "rows": 0, "ids": set(), "dates": set(),
        "blank_cells": collections.Counter(), "types": collections.Counter(),
        "other_date_outside": collections.Counter(),
        "other_date_unparsed": collections.Counter(),
        "special_related_ids": collections.Counter(),
        "sequence_uncertain": 0, "bad_width": 0,
        "candidate_duplicate_classes": collections.Counter(),
        "candidate_duplicate_extra_rows": 0,
        "interval_repeat_extra_rows_within_candidate_keys": 0,
        "multi_sequence_groups": 0,
    }


def _public_bucket(value: dict, allowed_count: int, *, window: tuple[date, date]) -> dict:
    return {
        "row_count": value["rows"],
        "observed_bound_identifier_count": len(value["ids"]),
        "unobserved_bound_identifier_count_not_a_failure": allowed_count - len(value["ids"]),
        "actual_disexdt_first": min(value["dates"]) if value["dates"] else None,
        "actual_disexdt_last": max(value["dates"]) if value["dates"] else None,
        "actual_disexdt_count": len(value["dates"]),
        "requested_disexdt_window_inclusive": [x.isoformat() for x in window],
        "blank_cell_counts": dict(sorted(value["blank_cells"].items())),
        "distype_counts_literal": dict(sorted(value["types"].items())),
        "other_date_outside_exdate_window_counts": dict(sorted(value["other_date_outside"].items())),
        "other_date_unparsed_row_counts": dict(sorted(value["other_date_unparsed"].items())),
        "related_id_non_decimal_or_special_row_counts": dict(sorted(value["special_related_ids"].items())),
        "sequence_uncertain_row_count": value["sequence_uncertain"],
        "bad_width_row_count": value["bad_width"],
        "candidate_three_field_key_duplicate_groups": sum(value["candidate_duplicate_classes"].values()),
        "candidate_three_field_key_extra_rows": value["candidate_duplicate_extra_rows"],
        "candidate_duplicate_group_classes": dict(sorted(value["candidate_duplicate_classes"].items())),
        "parser_consumed_interval_hash_repeat_extra_rows_within_candidate_keys":
        value["interval_repeat_extra_rows_within_candidate_keys"],
        "permno_disexdt_groups_with_multiple_sequence_numbers": value["multi_sequence_groups"],
    }


def _index_fingerprint(db: sqlite3.Connection) -> str:
    h = hashlib.sha256()
    query = ("SELECT row_no,permno,exdate,seq,distype,row_sha,event_sha,header_sha,"
             "interval_sha,codes,valid_id,valid_date,in_window FROM event_row ORDER BY row_no")
    for row in db.execute(query):
        h.update(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def analyze_csv(csv_path: Path, allowed: set[str], expected_fields: list[str],
                db_path: Path, exceptions_path: Path,
                *, expected_rows: int = PAGE_ROWS) -> tuple[dict, dict, dict]:
    """Stream saved CSV into a local SQLite index and produce aggregate statistics.

    The database and exception file are private. No row values are returned or
    printed. Raw interval hashes cover parser-consumed physical lines, which
    can include blank lines preceding a logical record.
    """
    before = sha(csv_path)
    buckets = {"merged": _bucket(), **{name: _bucket() for name, _, _ in SEGMENTS}}
    structural = set()
    blank_physical = 0
    unassigned_rows = 0
    with closing(sqlite3.connect(db_path)) as db:
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("CREATE TABLE event_row (row_no INTEGER PRIMARY KEY, permno TEXT, exdate TEXT, "
                   "seq TEXT, distype TEXT, row_sha TEXT NOT NULL, event_sha TEXT, "
                   "header_sha TEXT, interval_sha TEXT NOT NULL, codes TEXT NOT NULL, "
                   "valid_id INTEGER NOT NULL, valid_date INTEGER NOT NULL, in_window INTEGER NOT NULL)")
        with csv_path.open("r", encoding="utf-8-sig", newline="") as source:
            capture = base.IntervalCapture(source)
            reader = csv.reader(capture, strict=True)
            header = next(reader, [])
            capture.take()
            normalized = [name.strip().casefold() for name in header]
            if not header or any(not name for name in normalized):
                structural.add("HEADER_MISSING_OR_BLANK")
            if len(normalized) != len(set(normalized)):
                structural.add("HEADER_DUPLICATED_AFTER_NORMALIZATION")
            lookup = {name: pos for pos, name in enumerate(normalized)}
            missing_fields = [name for name in expected_fields if name not in lookup]
            extra_fields = [name for name in normalized if name not in expected_fields]
            if missing_fields:
                structural.add("SELECTED_FIELDS_MISSING")
            for row_no, row in enumerate(reader, start=2):
                interval_sha = capture.take()
                if not row:
                    blank_physical += 1
                    continue
                buckets["merged"]["rows"] += 1
                codes = []
                if len(row) != len(header):
                    buckets["merged"]["bad_width"] += 1
                    codes.append("CSV_ROW_WIDTH_MISMATCH")
                    structural.add("CSV_ROW_WIDTH_MISMATCH")
                    db.execute("INSERT INTO event_row VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               (row_no, None, None, None, None, object_sha(row), None, None,
                                interval_sha, json.dumps(codes), 0, 0, 0))
                    unassigned_rows += 1
                    continue
                def cell(name: str) -> str:
                    return row[lookup[name]].strip() if name in lookup else ""
                identifier, raw_date, raw_seq, kind = (
                    cell("permno"), cell("disexdt"), cell("disseqnbr"), cell("distype"))
                valid_id = bool(base.ID_PATTERN.fullmatch(identifier))
                if not valid_id:
                    structural.add("PERMNO_INVALID")
                    codes.append("PERMNO_INVALID")
                elif identifier not in allowed:
                    structural.add("PERMNO_OUTSIDE_BOUND_SET")
                    codes.append("PERMNO_OUTSIDE_BOUND_SET")
                parsed = base.parse_date(raw_date)
                if parsed is None:
                    structural.add("DISEXDT_INVALID")
                    codes.append("DISEXDT_INVALID")
                in_window = parsed is not None and WINDOW_START <= parsed <= WINDOW_END
                if parsed is not None and not in_window:
                    structural.add("DISEXDT_OUTSIDE_WINDOW")
                    codes.append("DISEXDT_OUTSIDE_WINDOW")
                segment = segment_for(parsed) if in_window else None
                seq = (raw_seq.lstrip("0") or "0") if base.SEQ_PATTERN.fullmatch(raw_seq) else None
                if seq is None:
                    codes.append("DISSEQNBR_NOT_DECIMAL")
                if kind not in SELECTED_TYPES:
                    structural.add("DISTYPE_OUTSIDE_RECORDED_PREDICATE")
                    codes.append("DISTYPE_OUTSIDE_RECORDED_PREDICATE")
                target_buckets = [("merged", buckets["merged"])]
                if segment is not None:
                    year_bucket = buckets[segment]
                    year_bucket["rows"] += 1
                    target_buckets.append((segment, year_bucket))
                else:
                    unassigned_rows += 1
                for bucket_name, bucket in target_buckets:
                    date_start, date_end = (WINDOW_START, WINDOW_END) if bucket_name == "merged" else SEGMENT_WINDOWS[bucket_name]
                    if valid_id and identifier in allowed and in_window:
                        bucket["ids"].add(identifier)
                    if parsed is not None:
                        bucket["dates"].add(parsed.isoformat())
                    for field, value in zip(normalized, row):
                        if value.strip() == "":
                            bucket["blank_cells"][field] += 1
                    if kind:
                        bucket["types"][kind] += 1
                    if seq is None:
                        bucket["sequence_uncertain"] += 1
                    for field in OTHER_DATES:
                        value = cell(field)
                        if not value:
                            continue
                        day = base.parse_date(value)
                        if day is None:
                            bucket["other_date_unparsed"][field] += 1
                        elif not date_start <= day <= date_end:
                            bucket["other_date_outside"][field] += 1
                    for field in ("dispermno", "dispermco"):
                        value = cell(field)
                        if value and not base.ID_PATTERN.fullmatch(value):
                            bucket["special_related_ids"][field] += 1
                event_cells = [row[lookup[name]] for name in expected_fields
                               if name not in HEADER_CONTEXT and name in lookup]
                header_cells = [row[lookup[name]] for name in sorted(HEADER_CONTEXT)
                                if name in lookup]
                db.execute("INSERT INTO event_row VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (row_no, identifier, parsed.isoformat() if parsed else raw_date,
                            seq, kind, object_sha(row), object_sha(event_cells),
                            object_sha(header_cells), interval_sha, json.dumps(codes),
                            int(valid_id), int(parsed is not None), int(in_window)))
        db.execute("CREATE INDEX event_two_key ON event_row (permno,exdate)")
        db.execute("CREATE INDEX event_three_key ON event_row (permno,exdate,seq)")
        db.commit()
        require(sha(csv_path) == before, "CSV_CHANGED_DURING_READ")
        uncertain_keys: set[tuple[str, str]] = set()
        duplicate_query = ("SELECT permno,exdate,seq,COUNT(*) FROM event_row "
                           "WHERE valid_id=1 AND valid_date=1 AND in_window=1 AND seq IS NOT NULL "
                           "GROUP BY permno,exdate,seq HAVING COUNT(*)>1 "
                           "ORDER BY permno,exdate,seq")
        with exceptions_path.open("x", encoding="utf-8", newline="\n") as exceptions:
            for identifier, exdate, seq, count in db.execute(duplicate_query):
                hashes = db.execute(
                    "SELECT row_sha,event_sha,header_sha,interval_sha,row_no FROM event_row "
                    "WHERE permno=? AND exdate=? AND seq=? ORDER BY row_no",
                    (identifier, exdate, seq)).fetchall()
                full_hashes, event_hashes, header_hashes, interval_hashes = (
                    set(row[i] for row in hashes) for i in range(4))
                category = ("parsed_full_row_repeat" if len(full_hashes) == 1 else
                            "provisional_header_only_variant" if
                            len(event_hashes) == 1 and len(header_hashes) > 1 else
                            "automatic_extra_field_only_variant" if len(event_hashes) == 1 else
                            "non_header_field_conflict")
                segment = segment_for(date.fromisoformat(exdate))
                for bucket in (buckets["merged"], buckets[segment]):
                    bucket["candidate_duplicate_classes"][category] += 1
                    bucket["candidate_duplicate_extra_rows"] += count - 1
                    bucket["interval_repeat_extra_rows_within_candidate_keys"] += count - len(interval_hashes)
                uncertain_keys.add((identifier, exdate))
                exceptions.write(json.dumps({
                    "permno": identifier, "disexdt": exdate, "disseqnbr_normalized": seq,
                    "row_numbers": [x[4] for x in hashes], "group_count": count,
                    "classification": category,
                }, sort_keys=True) + "\n")
            for row_no, identifier, exdate, seq, codes, row_sha, interval_sha in db.execute(
                    "SELECT row_no,permno,exdate,seq,codes,row_sha,interval_sha "
                    "FROM event_row WHERE codes <> '[]' ORDER BY row_no"):
                exceptions.write(json.dumps({
                    "row_number": row_no, "permno": identifier, "disexdt": exdate,
                    "disseqnbr_normalized": seq, "codes": json.loads(codes),
                    "row_sha256": row_sha, "parser_consumed_interval_sha256": interval_sha,
                }, sort_keys=True) + "\n")
        multi = {name: {} for name, _, _ in SEGMENTS}
        two_query = ("SELECT permno,exdate,COUNT(*),COUNT(DISTINCT seq),"
                     "SUM(CASE WHEN seq IS NULL THEN 1 ELSE 0 END) FROM event_row "
                     "WHERE valid_id=1 AND valid_date=1 AND in_window=1 "
                     "GROUP BY permno,exdate HAVING COUNT(DISTINCT seq)>1 "
                     "ORDER BY permno,exdate")
        for identifier, exdate, row_count, distinct_seq, null_seq in db.execute(two_query):
            segment = segment_for(date.fromisoformat(exdate))
            item = (row_count, distinct_seq, (identifier, exdate) in uncertain_keys or null_seq > 0)
            multi[segment][(identifier, exdate)] = item
            buckets[segment]["multi_sequence_groups"] += 1
            buckets["merged"]["multi_sequence_groups"] += 1
        fingerprint = _index_fingerprint(db)
        db.commit()
    require(sum(buckets[name]["rows"] for name, _, _ in SEGMENTS) + unassigned_rows ==
            buckets["merged"]["rows"], "YEAR_ROW_RECONCILIATION_FAILED")
    if buckets["merged"]["rows"] != expected_rows:
        structural.add("PAGE_ROW_COUNT_MISMATCH")
    if buckets["merged"]["rows"] == 0:
        structural.add("EMPTY_CSV")
    summary = _public_bucket(buckets["merged"], len(allowed), window=(WINDOW_START, WINDOW_END))
    summary.update({
        "schema": "m5_distribution_merged_summary_safe_v1", "scope": "ENGINEERING_ONLY",
        "source_csv_sha256": before, "query_id_from_visible_result": QUERY_ID,
        "csv_header_original_order": header, "csv_column_count": len(header),
        "expected_selected_field_count": len(expected_fields),
        "missing_selected_fields": missing_fields, "extra_or_automatic_fields": extra_fields,
        "page_displayed_row_count": expected_rows,
        "page_count_matches_local": buckets["merged"]["rows"] == expected_rows,
        "blank_physical_line_record_count": blank_physical,
        "unassigned_or_outside_window_row_count": unassigned_rows,
        "candidate_key_certified_provider_primary_key": False,
        "isolated_raw_record_byte_equality_verified": False,
        "parser_consumed_interval_hash_scope": "Can include blank physical lines before a logical CSV record",
        "index_logical_fingerprint_sha256": fingerprint,
        "structural_issue_codes": sorted(structural),
        "status": "PASS_STRUCTURE_ONLY" if not structural else "STRUCTURE_REVIEW_REQUIRED",
        "event_key_review_status": ("REVIEW_REQUIRED" if
                                    summary["candidate_three_field_key_duplicate_groups"] or
                                    summary["sequence_uncertain_row_count"] else
                                    "CANDIDATE_KEYS_UNIQUE_WITHIN_SAVED_EXTRACT"),
        "formal_m5_admission": False,
    })
    annual = {
        "schema": "m5_distribution_annual_summary_safe_v1", "scope": "ENGINEERING_ONLY",
        "source_csv_sha256": before,
        "segments": {name: _public_bucket(buckets[name], len(allowed), window=(start, end))
                     for name, start, end in SEGMENTS},
        "row_count_sum": sum(buckets[name]["rows"] for name, _, _ in SEGMENTS),
        "row_count_sum_plus_unassigned_equals_merged": True,
        "annual_identifier_counts_not_additive": True,
    }
    return summary, annual, {"multi": multi, "uncertain_keys": uncertain_keys,
                             "observed_ids": buckets["merged"]["ids"]}


def read_daily_duplicate_indexes(inventory_path: Path, *, repo: Path = ROOT) -> tuple[dict, dict]:
    """Read only manifest-bound duplicate exception JSONL, never daily CSV rows."""
    inventory = read_json(inventory_path)
    require(inventory.get("schema") == "m5_daily_eight_segments_inventory_safe_v1" and
            inventory.get("duplicate_key_definition") ==
            "PERMNO plus DlyCalDt within non-overlapping saved date sets",
            "DAILY_INVENTORY_SCHEMA_OR_KEY_INVALID")
    rows = {x.get("year"): x for x in inventory.get("segments", [])}
    require(all(y in rows for y in range(2020, 2027)), "DAILY_INVENTORY_SEGMENTS_MISSING")
    result: dict[str, collections.Counter | None] = {}
    evidence: dict[str, dict] = {}
    for name, start, end in SEGMENTS:
        year = int(name[:4])
        row = rows[year]
        summary_path = repo / row["summary_path"]
        manifest_path = repo / row["manifest_path"]
        directory = summary_path.parent
        candidates = list(directory.glob("*exceptions_private.jsonl")) if directory.exists() else []
        if not summary_path.is_file() or not manifest_path.is_file() or len(candidates) != 1:
            result[name] = None
            evidence[name] = {"status": "INDEX_NOT_LOCATED", "summary_sha256": None,
                              "manifest_sha256": None, "exception_index_sha256": None}
            continue
        exception_path = candidates[0]
        try:
            require(sha(summary_path) == row["summary_sha256"] and
                    sha(manifest_path) == row["manifest_sha256"],
                    "DAILY_SAVED_RECEIPT_HASH_MISMATCH")
            saved_manifest = read_json(manifest_path)
            require(sha(exception_path) ==
                    saved_manifest["saved_file_sha256"][exception_path.name],
                    "DAILY_EXCEPTION_INDEX_HASH_MISMATCH")
            summary = read_json(summary_path)
            require(summary.get("scope") == "ENGINEERING_ONLY", "DAILY_SCOPE_INVALID")
            groups: collections.Counter = collections.Counter()
            with exception_path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    item = json.loads(line)
                    if "PERMNO_DATE_DUPLICATE" not in item.get("codes", []):
                        continue
                    day = base.parse_date(item.get("dlycaldt", ""))
                    require(day is not None and start <= day <= end and
                            bool(base.ID_PATTERN.fullmatch(item.get("permno", ""))),
                            "DAILY_EXCEPTION_KEY_INVALID")
                    groups[(item["permno"], day.isoformat())] += 1
            require(len(groups) == summary["duplicate_key_group_count"] ==
                    row["duplicate_key_group_count"] and
                    sum(groups.values()) == summary["duplicate_extra_row_count"] ==
                    row["duplicate_extra_row_count"], "DAILY_DUPLICATE_COUNTS_MISMATCH")
            result[name] = groups
            evidence[name] = {
                "status": "BOUND_EXCEPTION_INDEX_VERIFIED",
                "summary_sha256": sha(summary_path),
                "manifest_sha256": sha(manifest_path),
                "exception_index_sha256": sha(exception_path),
                "daily_duplicate_group_count_from_index": len(groups),
                "daily_duplicate_extra_rows_from_index": sum(groups.values()),
            }
        except (OSError, KeyError, TypeError, ValueError, IntakeError, json.JSONDecodeError):
            result[name] = None
            evidence[name] = {"status": "INDEX_SOURCE_OR_KEY_REVIEW_REQUIRED",
                              "summary_sha256": sha(summary_path),
                              "manifest_sha256": sha(manifest_path),
                              "exception_index_sha256": sha(exception_path)}
    return result, evidence


def compare_seven_segments(database: Path, index_data: dict,
                           daily_groups: dict, daily_evidence: dict,
                           private_path: Path) -> dict:
    segments = {}
    complete = True
    aggregate = collections.Counter()
    with closing(sqlite3.connect(f"file:{database.as_posix()}?mode=ro&immutable=1", uri=True)) as db, \
            private_path.open("x", encoding="utf-8", newline="\n") as private:
        for name, _, _ in SEGMENTS:
            multi = index_data["multi"][name]
            daily = daily_groups[name]
            if daily is None:
                complete = False
                segments[name] = {
                    "status": "DAILY_INDEX_UNAVAILABLE_OR_CHANGED",
                    "daily_source": daily_evidence[name],
                    "daily_duplicate_group_denominator": None,
                    "daily_duplicate_extra_row_count": None,
                    "distribution_multi_sequence_group_count": len(multi),
                    "intersection_group_count": None,
                    "daily_only_group_count": None,
                    "distribution_only_group_count": None,
                    "exact_raw_row_multiplicity_match_count": None,
                    "exact_distinct_sequence_multiplicity_match_count": None,
                    "both_multiplicities_match_count": None,
                    "both_multiplicities_mismatch_count": None,
                    "uncertain_intersection_group_count": None,
                }
                continue
            daily_keys, distribution_keys = set(daily), set(multi)
            both = daily_keys & distribution_keys
            daily_only, distribution_only = daily_keys - distribution_keys, distribution_keys - daily_keys
            counters = collections.Counter()
            for key in sorted(daily_keys | distribution_keys):
                extra = daily.get(key)
                distribution = multi.get(key)
                if extra is not None and distribution is not None:
                    row_count, distinct_seq, uncertain = distribution
                    daily_multiplicity = extra + 1
                    raw_match = row_count == daily_multiplicity
                    seq_match = distinct_seq == daily_multiplicity
                    counters["raw_match"] += raw_match
                    counters["sequence_match"] += seq_match
                    counters["both_match"] += raw_match and seq_match
                    counters["uncertain_intersection"] += uncertain
                    state = "INTERSECTION"
                elif extra is not None:
                    cursor = db.execute(
                        "SELECT COUNT(*),COUNT(DISTINCT seq),"
                        "SUM(CASE WHEN seq IS NULL THEN 1 ELSE 0 END) FROM event_row "
                        "WHERE permno=? AND exdate=? AND valid_id=1 AND valid_date=1 AND in_window=1",
                        key)
                    row_count, distinct_seq, null_seq = cursor.fetchone()
                    uncertain = (null_seq or 0) > 0 or key in index_data["uncertain_keys"]
                    counters["daily_only_with_any_distribution_row"] += row_count > 0
                    counters["daily_only_uncertain"] += uncertain
                    daily_multiplicity = extra + 1
                    raw_match = seq_match = False
                    state = "DAILY_DUPLICATE_ONLY_RELATIVE_TO_MULTI_SEQUENCE_SET"
                else:
                    row_count, distinct_seq, uncertain = distribution
                    daily_multiplicity = None
                    raw_match = seq_match = False
                    state = "DISTRIBUTION_MULTI_SEQUENCE_ONLY_RELATIVE_TO_DAILY_DUPLICATE_SET"
                private.write(json.dumps({
                    "segment": name, "permno": key[0], "session": key[1],
                    "comparison_state": state,
                    "daily_group_multiplicity": daily_multiplicity,
                    "distribution_row_count": row_count,
                    "distribution_distinct_sequence_count": distinct_seq,
                    "raw_row_multiplicity_matches": raw_match,
                    "distinct_sequence_multiplicity_matches": seq_match,
                    "candidate_key_uncertain": bool(uncertain),
                }, sort_keys=True) + "\n")
            value = {
                "status": "BOUND_INDEX_COMPARISON_COMPLETE",
                "daily_source": daily_evidence[name],
                "daily_duplicate_group_denominator": len(daily_keys),
                "daily_duplicate_extra_row_count": sum(daily.values()),
                "distribution_multi_sequence_group_count": len(distribution_keys),
                "intersection_group_count": len(both),
                "daily_only_group_count": len(daily_only),
                "distribution_only_group_count": len(distribution_only),
                "exact_raw_row_multiplicity_match_count": counters["raw_match"],
                "exact_distinct_sequence_multiplicity_match_count": counters["sequence_match"],
                "both_multiplicities_match_count": counters["both_match"],
                "both_multiplicities_mismatch_count": len(both) - counters["both_match"],
                "uncertain_intersection_group_count": counters["uncertain_intersection"],
                "daily_only_with_any_distribution_row_count":
                    counters["daily_only_with_any_distribution_row"],
                "daily_only_uncertain_group_count": counters["daily_only_uncertain"],
                "distribution_only_is_not_a_nonduplicate_daily_denominator": True,
            }
            require(value["intersection_group_count"] + value["daily_only_group_count"] ==
                    value["daily_duplicate_group_denominator"] and
                    value["intersection_group_count"] + value["distribution_only_group_count"] ==
                    value["distribution_multi_sequence_group_count"],
                    "COMPARISON_SET_ARITHMETIC_FAILED")
            segments[name] = value
            for field in ("daily_duplicate_group_denominator", "daily_duplicate_extra_row_count",
                          "distribution_multi_sequence_group_count", "intersection_group_count",
                          "daily_only_group_count", "distribution_only_group_count",
                          "exact_raw_row_multiplicity_match_count",
                          "exact_distinct_sequence_multiplicity_match_count",
                          "both_multiplicities_match_count", "both_multiplicities_mismatch_count",
                          "uncertain_intersection_group_count"):
                aggregate[field] += value[field]
    full_fields = ("daily_duplicate_group_denominator", "daily_duplicate_extra_row_count",
                   "distribution_multi_sequence_group_count", "intersection_group_count",
                   "daily_only_group_count", "distribution_only_group_count",
                   "exact_raw_row_multiplicity_match_count",
                   "exact_distinct_sequence_multiplicity_match_count",
                   "both_multiplicities_match_count", "both_multiplicities_mismatch_count",
                   "uncertain_intersection_group_count")
    full = {field: aggregate[field] if complete else None for field in full_fields}
    full.update({"status": "COMPLETE_SEVEN_SEGMENT_INDEX_COMPARISON" if complete else
                 "PARTIAL_SEVEN_SEGMENT_INDEX_COMPARISON",
                 "missing_or_changed_segments": [name for name, value in segments.items()
                                                 if value["status"] != "BOUND_INDEX_COMPARISON_COMPLETE"],
                 "company_pair_or_day_keys_not_independent_financial_samples": True,
                 "backend_join_root_cause": "UNKNOWN",
                 "full_nonduplicate_daily_key_denominator_available": False})
    return {"schema": "m5_distribution_seven_segment_comparison_safe_v1",
            "scope": "ENGINEERING_ONLY_SAVED_DUPLICATE_INDEX_ASSOCIATION",
            "segments": segments, "all_seven": full,
            "daily_csvs_rescanned": False,
            "no_causal_or_provider_completeness_claim": True}


def verify_upload(output: Path, bound_txt: Path, *, expected_ids: int) -> tuple[set[str], dict]:
    local_before = sha(bound_txt)
    local = base.upload_format(bound_txt.read_bytes())
    package = base.upload_format((output / "package" / UPLOAD_MEMBER).read_bytes())
    require(sha(bound_txt) == local_before, "BOUND_UPLOAD_CHANGED_DURING_READ")
    require(len(local["ids"]) == expected_ids and
            (local_before == BOUND_SHA if expected_ids == 7639 else True),
            "BOUND_UPLOAD_IDENTITY_MISMATCH")
    require(set(local["ids"]) == set(package["ids"]), "PACKAGE_UPLOAD_SET_MISMATCH")
    return set(local["ids"]), {
        "bound_txt_sha256": local_before,
        "package_txt_sha256": package["sha256"],
        "bound_identifier_count": len(local["ids"]),
        "package_identifier_count": len(package["ids"]),
        "same_set": True,
        "same_order": local["ids"] == package["ids"],
        "same_bytes": local_before == package["sha256"],
        "bound_line_endings": local["line_endings"],
        "package_line_endings": package["line_endings"],
        "bound_final_newline": local["final_newline"],
        "package_final_newline": package["final_newline"],
        "server_input_dat_bound_to_query": False,
    }


def prior_first_segment_context(prior_dir: Path, prior_safe_zip: Path,
                                merged_observed: set[str]) -> dict:
    saved_summary = (prior_dir / SUMMARY).read_bytes()
    with base.checked_zip(prior_safe_zip) as prior:
        in_package = prior.read("03_MACHINE_SUMMARY_SAFE.json")
    require(in_package == saved_summary, "PRIOR_SAFE_SUMMARY_LOCAL_MISMATCH")
    prior_summary = json.loads(saved_summary)
    require(prior_summary.get("scope") == "ENGINEERING_ONLY" and
            prior_summary.get("requested_disexdt_window_inclusive") ==
            ["2019-11-01", "2019-12-31"], "PRIOR_WINDOW_OR_SCOPE_MISMATCH")
    old_index = prior_dir / "event_index_private.jsonl"
    old_manifest = read_json(prior_dir / "output_manifest_private.json")
    require(sha(old_index) == old_manifest["saved_file_sha256"][old_index.name],
            "PRIOR_EVENT_INDEX_HASH_MISMATCH")
    old_ids = set()
    with old_index.open("r", encoding="utf-8") as stream:
        for line in stream:
            record = json.loads(line)
            identifier = record.get("permno")
            if identifier and base.ID_PATTERN.fullmatch(identifier):
                old_ids.add(identifier)
    require(len(old_ids) == prior_summary["observed_bound_identifier_count"],
            "PRIOR_EVENT_IDENTIFIER_COUNT_MISMATCH")
    return {
        "schema": "m5_distribution_two_collection_window_context_safe_v1",
        "first_window_inclusive": ["2019-11-01", "2019-12-31"],
        "merged_window_inclusive": [WINDOW_START.isoformat(), WINDOW_END.isoformat()],
        "acquisition_windows_nonoverlap": True,
        "prior_first_segment_summary_sha256": sha(prior_dir / SUMMARY),
        "prior_first_segment_event_index_sha256": sha(old_index),
        "prior_first_segment_row_count": prior_summary["row_count"],
        "merged_row_count": None,
        "both_collection_windows_row_count": None,
        "prior_distinct_identifier_count": len(old_ids),
        "merged_distinct_identifier_count": len(merged_observed),
        "union_distinct_identifier_count": len(old_ids | merged_observed),
        "distinct_identifier_counts_are_not_additive": True,
        "first_segment_csv_rescanned": False,
    }


def derive(output: Path, temp: Path, *, bound_txt: Path, inventory: Path,
           prior_dir: Path, expected_rows: int, expected_ids: int,
           repo: Path = ROOT) -> tuple[dict, dict, dict, dict]:
    mapping = read_json(output / "package" / FIELD_MAPPING)
    parameter_text = (output / "package" / PARAMETERS).read_text(encoding="utf-8-sig")
    expected_fields, mapping_review = inspect_mapping(mapping, parameter_text)
    allowed, upload = verify_upload(output, bound_txt, expected_ids=expected_ids)
    db_path = temp / EVENT_DB
    exception_path = temp / EXCEPTIONS
    private_comparison_path = temp / COMPARISON_PRIVATE
    summary, annual, index_data = analyze_csv(
        output / "raw" / CSV_MEMBER, allowed, expected_fields, db_path,
        exception_path, expected_rows=expected_rows)
    daily_groups, daily_evidence = read_daily_duplicate_indexes(inventory, repo=repo)
    comparison = compare_seven_segments(db_path, index_data, daily_groups,
                                        daily_evidence, private_comparison_path)
    summary["seven_daily_index_comparison_status"] = comparison["all_seven"]["status"]
    historical = prior_first_segment_context(prior_dir, output / "package" / PRIOR_SAFE_MEMBER,
                                             index_data["observed_ids"])
    historical["merged_row_count"] = summary["row_count"]
    historical["both_collection_windows_row_count"] = (
        historical["prior_first_segment_row_count"] + summary["row_count"])
    summary["upload_binding"] = upload
    summary["source_mapping_review"] = mapping_review
    summary["collection_window_context"] = historical
    summary["source_status"] = {
        "query_page": "USER_SCREENSHOT_QUERY_11901007",
        "user_parameter_text": "EXPANDED_BUT_NOT_NATIVE_SERVER_EXPORT_OR_SQL",
        "explicit_distype_filter": sorted(SELECTED_TYPES),
        "server_input_dat": "NOT_BOUND_TO_THIS_QUERY",
        "native_backend_sql_and_join": "NOT_VERIFIED",
        "provider_release": "UNKNOWN_WRDSVERSION_3_NOT_RELEASE_EVIDENCE",
        "project_license_export_terms": "UNKNOWN",
        "supplier_universe_completeness": "NOT_VERIFIED",
    }
    comparison["source_csv_sha256"] = summary["source_csv_sha256"]
    comparison["daily_inventory_sha256"] = sha(inventory)
    annual["source_csv_sha256"] = summary["source_csv_sha256"]
    return summary, annual, comparison, {
        "db": db_path, "exceptions": exception_path,
        "private_comparison": private_comparison_path,
    }


def _check_saved_paths(output: Path, saved: dict) -> None:
    for relative, digest in saved.items():
        require(isinstance(relative, str) and base.safe_member_name(relative),
                "OUTPUT_FILE_PATH_INVALID")
        target = (output / PurePosixPath(relative)).resolve()
        require(output.resolve() in target.parents and target.is_file() and
                sha(target) == digest, "SAVED_FILE_HASH_OR_PATH_MISMATCH")


def verify_output(output: Path) -> dict:
    base.ignored_output(output)
    manifest = read_json(output / OUTPUT_MANIFEST)
    require(manifest.get("schema") == "m5_distribution_multiyear_manifest_private_v1" and
            manifest.get("audit_code_sha256") == sha(Path(__file__)) and
            manifest.get("base_code_sha256") == sha(BASE_PATH),
            "OUTPUT_MANIFEST_OR_CODE_MISMATCH")
    handoff = Path(manifest["handoff_zip_path"])
    bound_txt = Path(manifest["bound_txt_path"])
    inventory = Path(manifest["daily_inventory_path"])
    prior_dir = Path(manifest["prior_dir_path"])
    synthetic = manifest.get("scope") == "SYNTHETIC_TEST_ONLY"
    source_gate(handoff, bound_txt, inventory, prior_dir,
                synthetic_test_only=synthetic)
    require(sha(handoff) == manifest["handoff_zip_sha256"] and
            sha(bound_txt) == manifest["bound_txt_sha256"] and
            sha(inventory) == manifest["daily_inventory_sha256"],
            "INPUT_SOURCE_CHANGED")
    with base.checked_zip(handoff) as outer:
        source_manifest = json.loads(outer.read(PACKAGE_MANIFEST))
        entries = source_manifest.get("files")
        require(isinstance(entries, dict) and
                set(entries) == set(outer.namelist()) - {PACKAGE_MANIFEST},
                "PACKAGE_MANIFEST_INVALID")
        for name, metadata in entries.items():
            data = outer.read(name)
            require(len(data) == metadata.get("bytes") and
                    hashlib.sha256(data).hexdigest() == metadata.get("sha256"),
                    "PACKAGE_MEMBER_HASH_MISMATCH")
    _check_saved_paths(output, manifest["saved_file_sha256"])
    with base.checked_zip(output / "package" / RAW_ZIP_MEMBER,
                          expected_names={CSV_MEMBER}) as inner:
        source_csv_hash = hashlib.sha256(inner.read(CSV_MEMBER)).hexdigest()
    require(source_csv_hash == sha(output / "raw" / CSV_MEMBER),
            "SAVED_ZIP_CSV_MISMATCH")
    with tempfile.TemporaryDirectory(prefix="m5-dist-merged-verify-", dir=output.parent) as tmp:
        summary, annual, comparison, private = derive(
            output, Path(tmp), bound_txt=bound_txt, inventory=inventory,
            prior_dir=prior_dir, expected_rows=manifest["expected_page_rows"],
            expected_ids=manifest["expected_bound_id_count"],
            repo=Path(manifest["repo_path"]))
        summary["source_file_identities"] = manifest["source_file_identities"]
        summary["audit_code_sha256"] = sha(Path(__file__))
        summary["base_code_sha256"] = sha(BASE_PATH)
        if synthetic:
            summary["scope"] = annual["scope"] = comparison["scope"] = "SYNTHETIC_TEST_ONLY"
        require(json_bytes(summary) == (output / SUMMARY).read_bytes() and
                json_bytes(annual) == (output / ANNUAL).read_bytes() and
                json_bytes(comparison) == (output / COMPARISON).read_bytes(),
                "SAVED_SAFE_DERIVATION_MISMATCH")
        require(private["exceptions"].read_bytes() == (output / EXCEPTIONS).read_bytes() and
                private["private_comparison"].read_bytes() ==
                (output / COMPARISON_PRIVATE).read_bytes(),
                "SAVED_PRIVATE_INDEX_DERIVATION_MISMATCH")
        with closing(sqlite3.connect(
                f"file:{(output / EVENT_DB).as_posix()}?mode=ro&immutable=1", uri=True)) as db:
            require(_index_fingerprint(db) == summary["index_logical_fingerprint_sha256"],
                    "SAVED_SQLITE_INDEX_FINGERPRINT_MISMATCH")
    execution_path = output / EXECUTION
    if execution_path.exists():
        execution = read_json(execution_path)
        require(execution.get("schema") == "m5_distribution_multiyear_execution_private_v1" and
                execution.get("run_exit_code") == 0 and
                execution.get("run_internal_verify_exit_code") == 0 and
                execution.get("audit_code_sha256") == sha(Path(__file__)) and
                execution.get("summary_sha256") == sha(output / SUMMARY) and
                execution.get("manifest_sha256") == sha(output / OUTPUT_MANIFEST),
                "EXECUTION_LEDGER_MISMATCH")
    return summary


def run(handoff: Path, output: Path, *, bound_txt: Path = BOUND_TXT,
        inventory: Path = EIGHT_INVENTORY, prior_dir: Path = PRIOR_DIR,
        expected_rows: int = PAGE_ROWS, expected_ids: int = 7639,
        synthetic_test_only: bool = False, repo: Path = ROOT) -> dict:
    base.ignored_output(output)
    require(not output.exists(), "OUTPUT_ALREADY_EXISTS")
    require(handoff.is_file(), "HANDOFF_ZIP_NOT_FOUND")
    source_gate(handoff, bound_txt, inventory, prior_dir,
                synthetic_test_only=synthetic_test_only)
    require(expected_rows == PAGE_ROWS and expected_ids == 7639 or synthetic_test_only,
            "REAL_EXPECTED_COUNTS_CHANGED")
    started = datetime.now(timezone.utc).isoformat()
    output.mkdir(parents=True, exist_ok=False)
    try:
        source = verify_transport_and_extract(handoff, output)
        with tempfile.TemporaryDirectory(prefix="m5-dist-merged-derive-", dir=output.parent) as tmp:
            summary, annual, comparison, private = derive(
                output, Path(tmp), bound_txt=bound_txt, inventory=inventory,
                prior_dir=prior_dir, expected_rows=expected_rows,
                expected_ids=expected_ids, repo=repo)
            shutil.copyfile(private["db"], output / EVENT_DB)
            shutil.copyfile(private["exceptions"], output / EXCEPTIONS)
            shutil.copyfile(private["private_comparison"], output / COMPARISON_PRIVATE)
        source_paths = [path for path in (output / "package").rglob("*") if path.is_file()]
        source_paths.append(output / "raw" / CSV_MEMBER)
        source_identities = {
            str(path.relative_to(output)).replace("\\", "/"): sha(path)
            for path in source_paths}
        summary["source_file_identities"] = source_identities
        summary["audit_code_sha256"] = sha(Path(__file__))
        summary["base_code_sha256"] = sha(BASE_PATH)
        if synthetic_test_only:
            summary["scope"] = annual["scope"] = comparison["scope"] = "SYNTHETIC_TEST_ONLY"
        write_json_new(output / SUMMARY, summary)
        write_json_new(output / ANNUAL, annual)
        write_json_new(output / COMPARISON, comparison)
        registry = {
            "schema": "m5_distribution_multiyear_source_registry_private_v1",
            "scope": summary["scope"],
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "python_executable": sys.executable,
            "repo_path": str(repo.resolve()),
            "handoff_zip_path": str(handoff.resolve()),
            "handoff_zip_sha256": source["handoff_zip_sha256"],
            "bound_txt_path": str(bound_txt.resolve()),
            "bound_txt_sha256": sha(bound_txt),
            "daily_inventory_path": str(inventory.resolve()),
            "daily_inventory_sha256": sha(inventory),
            "prior_dir_path": str(prior_dir.resolve()),
            "audit_code_sha256": sha(Path(__file__)),
            "base_code_sha256": sha(BASE_PATH),
            "query_id_from_user_result_page": QUERY_ID,
            "native_sql_status": "NOT_LOCATED",
            "server_input_dat_status": "NOT_BOUND_TO_QUERY",
            "wrds_queries_submitted_by_auditor": 0,
            "original_files_overwritten": False,
            "source_file_identities": source_identities,
        }
        write_json_new(output / SOURCE_REGISTRY, registry)
        saved = {str(path.relative_to(output)).replace("\\", "/"): sha(path)
                 for path in (output / "package").rglob("*") if path.is_file()}
        saved.update({name: sha(output / name) for name in
                      ("raw/" + CSV_MEMBER, SUMMARY, ANNUAL, COMPARISON,
                       EVENT_DB, EXCEPTIONS, COMPARISON_PRIVATE, SOURCE_REGISTRY)})
        manifest = {
            "schema": "m5_distribution_multiyear_manifest_private_v1",
            "scope": summary["scope"],
            "repo_path": str(repo.resolve()),
            "handoff_zip_path": str(handoff.resolve()),
            "handoff_zip_sha256": source["handoff_zip_sha256"],
            "bound_txt_path": str(bound_txt.resolve()),
            "bound_txt_sha256": sha(bound_txt),
            "daily_inventory_path": str(inventory.resolve()),
            "daily_inventory_sha256": sha(inventory),
            "prior_dir_path": str(prior_dir.resolve()),
            "audit_code_sha256": sha(Path(__file__)),
            "base_code_sha256": sha(BASE_PATH),
            "expected_page_rows": expected_rows,
            "expected_bound_id_count": expected_ids,
            "source_file_identities": source_identities,
            "saved_file_sha256": saved,
        }
        write_json_new(output / OUTPUT_MANIFEST, manifest)
        result = verify_output(output)
        write_json_new(output / EXECUTION, {
            "schema": "m5_distribution_multiyear_execution_private_v1",
            "scope": summary["scope"],
            "started_at_utc": started,
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            "run_exit_code": 0,
            "run_internal_verify_count": 1,
            "run_internal_verify_exit_code": 0,
            "audit_code_sha256": sha(Path(__file__)),
            "summary_sha256": sha(output / SUMMARY),
            "manifest_sha256": sha(output / OUTPUT_MANIFEST),
            "peak_memory_measured": False,
            "model_or_financial_experiment_run": False,
        })
        return result
    except Exception as error:
        with (output / "failure_private.json").open("x", encoding="utf-8", newline="\n") as out:
            json.dump({
                "schema": "m5_distribution_multiyear_failure_private_v1",
                "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
                "error_type": type(error).__name__,
                "error_code": error.code if isinstance(error, IntakeError) else
                "UNHANDLED_INPUT_OR_IO_ERROR",
                "handoff_zip_sha256": sha(handoff) if handoff.is_file() else None,
            }, out, sort_keys=True, indent=2)
            out.write("\n")
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
            prefix = "M5_DISTRIBUTION_MERGED_SAVED_"
        else:
            result = verify_output(args.output_dir)
            prefix = "M5_DISTRIBUTION_MERGED_SAVED_OUTPUT_VERIFIED_"
        structure = ("STRUCTURE_PASS" if result["status"] == "PASS_STRUCTURE_ONLY" else
                     "STRUCTURE_REVIEW_REQUIRED")
        comparison = ("SEVEN_INDEX_COMPLETE" if result["seven_daily_index_comparison_status"] ==
                      "COMPLETE_SEVEN_SEGMENT_INDEX_COMPARISON" else "SEVEN_INDEX_PARTIAL")
        print(prefix + structure + "_" + comparison)
        return 0
    except Exception:
        print("M5_DISTRIBUTION_MERGED_REVIEW_REQUIRED")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
