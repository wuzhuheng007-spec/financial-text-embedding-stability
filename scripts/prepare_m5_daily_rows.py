"""Build a private, source-bound Daily row map for engineering preparation only.

This tool never creates returns, labels, an event mask, or an admitted sample.
It reuses the saved full-field key indexes for 2020–2026. The 2019 intake
recorded duplicate keys without full-field equality, so that segment is read
once with the same logical CSV-row numbering used by its original auditor.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
import audit_m5_wrds_intake as intake_keys

POLICY = ROOT / "configs" / "m5_preparation_rules_accepted_20261113.json"
DECISION = ROOT / "artifacts" / "m5_three_preparation_rules_accepted_20261113_v1" / "preparation_decision_receipt_private.json"
INVENTORY = ROOT / "artifacts" / "m5_daily_eight_segments_receipt_review_20261112_v1" / "m5_daily_eight_segments_inventory_safe.json"
EXPECTED_INVENTORY_SHA = "13cbec98b9ec2d6ef59a6b8f42b89a872b6789388861d114fe8413bbef6a64cd"
EXPECTED_CANDIDATE_SHA = "f1fa28949353adea6aa179854441b48658415ef577a006fe76bd01c779ddaa38"
EXPECTED_POLICY_SHA = "14f22ae821d61d44848832fc5e73b5b27360f5947cafb4189ceb27603a38970e"
EXPECTED_DECISION_SHA = "cf701cb379b6849ceb5f89def2c7e38f3e9b2d16f6a1ac7971b032201bb8c56c"
EXPECTED_POLICY_SCHEMA = "m5_accepted_preparation_policy_v1"
MISSING_LITERALS = frozenset({".", "NA", "N/A", "NULL"})
DISPOSITIONS = frozenset({"SINGLETON", "EQUAL_COLLAPSED", "CONFLICT_QUARANTINE", "EQUALITY_UNRESOLVED"})


class PreparationError(ValueError):
    """A fixed, safe problem code; never interpolate private input values."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise PreparationError(code)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), "JSON_OBJECT_REQUIRED")
    return value


def safe_path(relative: str) -> Path:
    path = (ROOT / relative).resolve(strict=True)
    require(path.is_relative_to(ROOT / "artifacts"), "SOURCE_OUTSIDE_IGNORED_ARTIFACTS")
    return path


def require_ignored_output(path: Path) -> None:
    resolved = path.resolve()
    require(resolved.is_relative_to(ROOT / "artifacts"), "OUTPUT_OUTSIDE_ARTIFACTS")
    result = subprocess.run(
        ["git", "check-ignore", "--quiet", "--", str(resolved)],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        check=False,
    )
    require(result.returncode == 0, "OUTPUT_NOT_GIT_IGNORED")


def validate_policy_and_decision(policy_path: Path = POLICY,
                                 decision_path: Path = DECISION) -> dict:
    require(policy_path.is_file(), "PREPARATION_POLICY_MISSING")
    require(decision_path.is_file(), "PREPARATION_DECISION_MISSING")
    policy_hash = sha256(policy_path)
    require(policy_hash == EXPECTED_POLICY_SHA, "PREPARATION_POLICY_VERSION_CHANGED")
    policy = read_json(policy_path)
    decision_hash = sha256(decision_path)
    require(decision_hash == EXPECTED_DECISION_SHA,
            "PREPARATION_DECISION_VERSION_CHANGED")
    decision = read_json(decision_path)
    require(policy.get("schema") == EXPECTED_POLICY_SCHEMA
            and policy.get("preparation_rules_researcher_accepted") is True
            and policy.get("full_m5_protocol_researcher_accepted") is False
            and policy.get("derived_daily_and_availability", {}).get("collapse_condition")
            == "ALL_PARSED_SOURCE_COLUMNS_EQUAL_WITHIN_SAME_KEY"
            and policy.get("derived_daily_and_availability", {}).get("final_event_mask_status")
            == "NOT_CERTIFIED",
            "PREPARATION_POLICY_SCOPE_MISMATCH")
    require(policy.get("original_candidate_sha256_unchanged") == EXPECTED_CANDIDATE_SHA
            and sha256(ROOT / "configs" / "m5_financial_protocol_round8.json")
            == EXPECTED_CANDIDATE_SHA, "M5_CANDIDATE_CHANGED")
    require(decision.get("schema") == "m5_local_preparation_decision_receipt_v1"
            and decision.get("decision_scope") == "ACCEPTED_ENGINEERING_PREPARATION_ONLY"
            and decision.get("preparation_rules_researcher_accepted") is True
            and decision.get("full_m5_protocol_researcher_accepted") is False
            and decision.get("formal_financial_experiment_status") == "NOT_RUN"
            and decision.get("accepted_policy_sha256") == policy_hash
            and decision.get("original_round8_candidate_sha256") == EXPECTED_CANDIDATE_SHA
            and decision.get("accepted_rule_ids") == policy.get("rule_ids")
            and decision.get("original_message_exact_utc") is None
            and isinstance(decision.get("local_receipt_created_at_utc"), str)
            and decision.get("private_source_sha256"),
            "PREPARATION_DECISION_BINDING_INVALID")
    private_source = (decision_path.parent /
                      decision["private_source_relative_path"]).resolve(strict=True)
    require(private_source.is_relative_to(ROOT / "artifacts"),
            "PREPARATION_DECISION_SOURCE_OUTSIDE_ARTIFACTS")
    require(sha256(private_source) == decision["private_source_sha256"],
            "PREPARATION_DECISION_SOURCE_CHANGED")
    normalizer_path = Path(intake_keys.__file__).resolve()
    require(normalizer_path == ROOT / "scripts" / "audit_m5_wrds_intake.py",
            "DAILY_KEY_NORMALIZER_IMPORT_MISMATCH")
    return {"policy_sha256": policy_hash, "decision_sha256": decision_hash,
            "candidate_sha256": EXPECTED_CANDIDATE_SHA,
            "normalizer_code_sha256": sha256(normalizer_path)}


def _source_file(manifest: dict, suffix: str, required: bool = True) -> tuple[str, str] | None:
    matches = [(name, digest) for name, digest in manifest.get("saved_file_sha256", {}).items()
               if name.lower().endswith(suffix)]
    require(len(matches) == (1 if required else 0) or (not required and len(matches) == 1),
            "SOURCE_FILE_ROLE_AMBIGUOUS")
    return matches[0] if matches else None


def validate_header_coverage(summary: dict, expected: set[str] | None) -> set[str]:
    normalized = [name.strip().casefold() for name in summary["csv_header"]]
    require(all(normalized) and len(normalized) == len(set(normalized)),
            "DAILY_HEADER_INVALID")
    fields = set(normalized)
    require(expected is None or fields == expected, "DAILY_HEADER_SET_CHANGED")
    for field in ("per_column_blank_counts", "per_column_null_literal_counts"):
        counted = [name.strip().casefold() for name in summary[field]]
        require(set(counted) == fields and len(counted) == len(fields),
                "DAILY_MISSING_FIELD_COVERAGE_INVALID")
    return fields


def source_specs(inventory_path: Path = INVENTORY) -> tuple[list[dict], str]:
    require(inventory_path.is_file(), "EIGHT_SEGMENT_INVENTORY_MISSING")
    inventory_hash = sha256(inventory_path)
    require(inventory_hash == EXPECTED_INVENTORY_SHA,
            "EIGHT_SEGMENT_INVENTORY_VERSION_CHANGED")
    inventory = read_json(inventory_path)
    rows = inventory.get("segments")
    require(inventory.get("expected_segments") == 8 and isinstance(rows, list)
            and len(rows) == 8 and inventory.get("raw_rows_including_duplicate_rows") ==
            sum(item.get("row_count", -1) for item in rows),
            "EIGHT_SEGMENT_INVENTORY_INVALID")
    specs = []
    expected_header: set[str] | None = None
    expected_header_order: list[str] | None = None
    for segment, item in enumerate(rows):
        manifest_path = safe_path(item["manifest_path"])
        summary_path = safe_path(item["summary_path"])
        require(sha256(manifest_path) == item["manifest_sha256"]
                and sha256(summary_path) == item["summary_sha256"],
                "SAVED_RECEIPT_HASH_MISMATCH")
        manifest = read_json(manifest_path)
        summary = read_json(summary_path)
        csv_name, csv_hash = _source_file(manifest, ".csv")
        csv_path = (manifest_path.parent / csv_name).resolve(strict=True)
        require(csv_path.is_relative_to(ROOT / "artifacts")
                and csv_hash == item["csv_sha256"] == summary["csv_sha256"]
                and summary["row_count"] == item["row_count"]
                and summary["csv_field_count"] == len(summary["csv_header"])
                and summary["csv_field_count"] == item["csv_field_count"],
                "DAILY_SOURCE_IDENTITY_MISMATCH")
        expected_header = validate_header_coverage(summary, expected_header)
        normalized_order = [name.strip().casefold() for name in summary["csv_header"]]
        if expected_header_order is None:
            expected_header_order = normalized_order
        require(normalized_order == expected_header_order,
                "DAILY_HEADER_ORDER_CHANGED")
        for field in ("invalid_row_width_count", "invalid_permno_row_count",
                      "outside_upload_row_count", "invalid_date_row_count",
                      "outside_requested_window_row_count"):
            require(summary.get(field) == 0, "DAILY_SOURCE_EXCEPTION_REQUIRES_REVIEW")
        index_role = _source_file(manifest, "key_index_private.sqlite3", required=False)
        exception_role = _source_file(manifest, "exceptions_private.jsonl", required=False)
        if not exception_role:
            exception_role = _source_file(manifest, "exceptions_private.json", required=False)
        index_path = ((manifest_path.parent / index_role[0]).resolve(strict=True)
                      if index_role else None)
        exceptions_path = ((manifest_path.parent / exception_role[0]).resolve(strict=True)
                           if exception_role else None)
        if segment == 0:
            require(index_path is None, "FIRST_SEGMENT_INDEX_UNEXPECTED")
        else:
            require(index_path is not None and exceptions_path is not None,
                    "SAVED_KEY_INDEX_MISSING")
            with closing(sqlite3.connect(f"file:{index_path.as_posix()}?mode=ro", uri=True)) as index_db:
                columns = {row[1] for row in index_db.execute("PRAGMA table_info(record_key)")}
            require({"permno", "session", "first_row", "first_cells", "first_sha",
                     "has_duplicate", "has_conflict"}.issubset(columns),
                    "SAVED_KEY_INDEX_SCHEMA_CHANGED")
        specs.append({"segment": segment, "label": item["segment"], "year": item["year"],
                      "row_count": summary["row_count"], "csv_path": csv_path,
                      "csv_sha256": csv_hash, "csv_header": summary["csv_header"],
                      "summary": summary, "summary_path": summary_path,
                      "summary_sha256": item["summary_sha256"],
                      "manifest_path": manifest_path, "manifest_sha256": item["manifest_sha256"],
                      "index_path": index_path,
                      "index_sha256": index_role[1] if index_role else None,
                      "exceptions_path": exceptions_path,
                      "exceptions_sha256": exception_role[1] if exception_role else None})
    require(sum(spec["row_count"] for spec in specs) == 10312828,
            "DAILY_TOTAL_SOURCE_ROW_COUNT_UNEXPECTED")
    return specs, inventory_hash


def verify_source_hashes(specs: list[dict]) -> None:
    for spec in specs:
        require(sha256(spec["csv_path"]) == spec["csv_sha256"],
                "DAILY_CSV_HASH_MISMATCH")
        for role in ("index", "exceptions"):
            path = spec[f"{role}_path"]
            if path is not None:
                require(sha256(path) == spec[f"{role}_sha256"],
                        "SAVED_INDEX_HASH_MISMATCH")


def source_record(spec: dict) -> dict:
    """Private source identity persisted in the output manifest."""
    keys = ("segment", "label", "csv_path", "csv_sha256", "summary_path",
            "summary_sha256", "manifest_path", "manifest_sha256",
            "index_path", "index_sha256", "exceptions_path", "exceptions_sha256")
    return {key: str(spec[key]) if isinstance(spec[key], Path) else spec[key]
            for key in keys}


def create_row_map(db: sqlite3.Connection) -> None:
    db.execute("CREATE TABLE row_map (segment INTEGER NOT NULL, source_row INTEGER NOT NULL, "
               "permno TEXT NOT NULL, session TEXT NOT NULL, representative_row INTEGER, "
               "disposition TEXT NOT NULL, PRIMARY KEY(segment, source_row)) WITHOUT ROWID")
    db.execute("CREATE TABLE availability_missing (segment INTEGER NOT NULL, "
               "first_source_row INTEGER NOT NULL, blank_fields TEXT NOT NULL, "
               "null_literal_fields TEXT NOT NULL, PRIMARY KEY(segment, first_source_row)) "
               "WITHOUT ROWID")


def record_observed_missing(db: sqlite3.Connection, spec: dict, first_row: int,
                            cells: list[str]) -> None:
    """Store sparse field-presence evidence, without interpreting valid prices."""
    require(len(cells) == len(spec["csv_header"]), "SAVED_FIRST_CELLS_WIDTH_INVALID")
    blank, literal = [], []
    for field, cell in zip(spec["csv_header"], cells):
        require(isinstance(cell, str), "SAVED_FIRST_CELL_NOT_STRING")
        value = cell.strip()
        if value == "":
            blank.append(field.strip().casefold())
        elif value.upper() in MISSING_LITERALS:
            literal.append(field.strip().casefold())
    if blank or literal:
        db.execute("INSERT INTO availability_missing VALUES (?,?,?,?)",
                   (spec["segment"], first_row,
                    json.dumps(blank, separators=(",", ":")),
                    json.dumps(literal, separators=(",", ":"))))


def disposition(group_size: int, cells_equal: bool | None) -> str:
    if group_size == 1:
        return "SINGLETON"
    if cells_equal is None:
        return "EQUALITY_UNRESOLVED"
    return "EQUAL_COLLAPSED" if cells_equal else "CONFLICT_QUARANTINE"


def fixed_horizon_presence(sessions: list[str], decision_session: str,
                           observed_sessions: set[str], horizon: int) -> tuple[str, bool]:
    """Synthetic-only position check; an absent fixed endpoint never shifts forward.

    The real run does not call this function because the applicable exchange,
    event clock, and label horizons have not been accepted as a complete rule.
    """
    require(type(horizon) is int and horizon > 0, "HORIZON_INVALID")
    require(len(sessions) == len(set(sessions)) and sessions == sorted(sessions)
            and decision_session in sessions, "SESSION_SEQUENCE_INVALID")
    position = sessions.index(decision_session) + horizon
    require(position < len(sessions), "HORIZON_OUTSIDE_SESSION_SEQUENCE")
    endpoint = sessions[position]
    return endpoint, endpoint in observed_sessions


def _row_tuple(segment: int, source_row: int, permno: str, session: str,
               representative_row: int | None, state: str) -> tuple:
    require(state in DISPOSITIONS and source_row >= 2, "DERIVED_ROW_STATE_INVALID")
    return segment, source_row, permno, session, representative_row, state


def _check_segment_row_count(db: sqlite3.Connection, segment: int, expected: int) -> None:
    count, first, last = db.execute(
        "SELECT COUNT(*),MIN(source_row),MAX(source_row) FROM row_map WHERE segment=?",
        (segment,),
    ).fetchone()
    require((count, first, last) == (expected, 2, expected + 1),
            "SOURCE_ROW_MAPPING_INCOMPLETE")


def _read_first_segment(spec: dict, db: sqlite3.Connection) -> dict:
    """Read 2019 exactly once because its old key receipt lacks field equality."""
    states = sqlite3.connect(":memory:")
    states.execute("CREATE TABLE first_key (permno TEXT, session TEXT, first_row INTEGER, "
                   "first_cells TEXT, n INTEGER, conflict INTEGER, "
                   "PRIMARY KEY(permno,session)) WITHOUT ROWID")
    header_expected = spec["csv_header"]
    count = 0
    with spec["csv_path"].open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.reader(source, strict=True)
        header = next(reader, [])
        require(header == header_expected, "FIRST_SEGMENT_HEADER_CHANGED")
        lookup = {field.strip().casefold(): i for i, field in enumerate(header)}
        require("permno" in lookup and "dlycaldt" in lookup,
                "DAILY_KEY_FIELDS_MISSING")
        for row_number, row in enumerate(reader, start=2):
            count += 1
            require(len(row) == len(header), "FIRST_SEGMENT_ROW_WIDTH_INVALID")
            permno = intake_keys.normalize_id(row[lookup["permno"]].strip())
            raw_session = row[lookup["dlycaldt"]].strip()
            require(permno is not None
                    and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw_session),
                    "FIRST_SEGMENT_KEY_INVALID")
            try:
                session = date.fromisoformat(raw_session).isoformat()
            except ValueError as exc:
                raise PreparationError("FIRST_SEGMENT_DATE_INVALID") from exc
            current = states.execute(
                "SELECT first_row,first_cells,n,conflict FROM first_key "
                "WHERE permno=? AND session=?", (permno, session),
            ).fetchone()
            if current is None:
                record_observed_missing(db, spec, row_number, row)
                states.execute("INSERT INTO first_key VALUES (?,?,?,?,1,0)",
                               (permno, session, row_number, json.dumps(row, ensure_ascii=False)))
                db.execute("INSERT INTO row_map VALUES (?,?,?,?,?,?)",
                           _row_tuple(spec["segment"], row_number, permno, session,
                                      row_number, "SINGLETON"))
                continue
            first_row, first_cells, n, conflict = current
            equal = row == json.loads(first_cells)
            conflict = bool(conflict or not equal)
            states.execute("UPDATE first_key SET n=?,conflict=? WHERE permno=? AND session=?",
                           (n + 1, int(conflict), permno, session))
            if conflict:
                db.execute("UPDATE row_map SET representative_row=NULL, "
                           "disposition='CONFLICT_QUARANTINE' WHERE segment=? AND "
                           "permno=? AND session=?",
                           (spec["segment"], permno, session))
            else:
                db.execute("UPDATE row_map SET disposition='EQUAL_COLLAPSED' "
                           "WHERE segment=? AND source_row=?",
                           (spec["segment"], first_row))
            db.execute("INSERT INTO row_map VALUES (?,?,?,?,?,?)",
                       _row_tuple(spec["segment"], row_number, permno, session,
                                  None if conflict else first_row,
                                  disposition(n + 1, not conflict)))
    groups = states.execute("SELECT n,conflict FROM first_key").fetchall()
    states.close()
    duplicate_groups = sum(n > 1 for n, _ in groups)
    conflict_groups = sum(n > 1 and bool(conflict) for n, conflict in groups)
    require(count == spec["row_count"]
            and duplicate_groups == spec["summary"]["duplicate_key_group_count"]
            and count - len(groups) == spec["summary"]["duplicate_extra_row_count"]
            and conflict_groups == spec["summary"]["conflicting_key_group_count"],
            "FIRST_SEGMENT_RECONCILIATION_FAILED")
    _check_segment_row_count(db, spec["segment"], count)
    return {"raw_rows": count, "key_groups": len(groups),
            "equal_groups": duplicate_groups - conflict_groups,
            "conflict_groups": conflict_groups, "unresolved_groups": 0,
            "duplicate_extra_rows": count - len(groups),
            "equality_evidence": "NEW_FULL_FIELD_PARSED_CSV_SCAN"}


def _read_saved_index(spec: dict, db: sqlite3.Connection) -> dict:
    """Map all source rows using the immutable old key index and exceptions."""
    conn = sqlite3.connect(f"file:{spec['index_path'].as_posix()}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    count_keys = duplicate_groups = conflict_groups = 0
    first_by_duplicate: dict[tuple[str, str], tuple[int, str, bool]] = {}
    with closing(conn):
        for permno, session, first_row, first_cells, first_sha, is_dup, is_conflict in conn.execute(
                "SELECT permno,session,first_row,first_cells,first_sha,has_duplicate,has_conflict "
                "FROM record_key"):
            count_keys += 1
            record_observed_missing(db, spec, first_row, json.loads(first_cells))
            duplicate_groups += int(bool(is_dup))
            conflict_groups += int(bool(is_conflict))
            state = ("CONFLICT_QUARANTINE" if is_conflict else
                     "EQUAL_COLLAPSED" if is_dup else "SINGLETON")
            db.execute("INSERT INTO row_map VALUES (?,?,?,?,?,?)",
                       _row_tuple(spec["segment"], first_row, permno, session,
                                  None if is_conflict else first_row, state))
            if is_dup:
                first_by_duplicate[(permno, session)] = (first_row, first_sha,
                                                         bool(is_conflict))
        extras = 0
        seen_duplicate_keys: set[tuple[str, str]] = set()
        with spec["exceptions_path"].open("r", encoding="utf-8") as stream:
            for line in stream:
                entry = json.loads(line)
                require("PERMNO_DATE_DUPLICATE" in entry.get("codes", []),
                        "SAVED_EXCEPTION_SCOPE_UNSUPPORTED")
                key = (entry["permno"], entry["dlycaldt"])
                require(key in first_by_duplicate, "SAVED_EXCEPTION_KEY_MISMATCH")
                first_row, first_sha, is_conflict = first_by_duplicate[key]
                require(entry["first_row_number"] == first_row
                        and (is_conflict or entry["row_sha256"] == first_sha),
                        "SAVED_FULL_FIELD_EQUALITY_MISMATCH")
                state = "CONFLICT_QUARANTINE" if is_conflict else "EQUAL_COLLAPSED"
                db.execute("INSERT INTO row_map VALUES (?,?,?,?,?,?)",
                           _row_tuple(spec["segment"], entry["row_number"],
                                      key[0], key[1], None if is_conflict else first_row,
                                      state))
                extras += 1
                seen_duplicate_keys.add(key)
        require(count_keys + extras == spec["row_count"]
                and duplicate_groups == spec["summary"]["duplicate_key_group_count"]
                and conflict_groups == spec["summary"]["conflicting_key_group_count"]
                and extras == spec["summary"]["duplicate_extra_row_count"]
                and len(seen_duplicate_keys) == duplicate_groups,
                "SAVED_INDEX_RECONCILIATION_FAILED")
        _check_segment_row_count(db, spec["segment"], spec["row_count"])
    return {"raw_rows": spec["row_count"], "key_groups": count_keys,
            "equal_groups": duplicate_groups - conflict_groups,
            "conflict_groups": conflict_groups, "unresolved_groups": 0,
            "duplicate_extra_rows": extras,
            "equality_evidence": "SAVED_FULL_FIELD_INDEX_AND_EXCEPTION_HASHES"}


def mapping_fingerprint(db: sqlite3.Connection, expected_segments: int = 8) -> tuple[str, Counter]:
    digest = hashlib.sha256()
    counts: Counter = Counter()
    previous_segment, previous_row = -1, 1
    for record in db.execute("SELECT segment,source_row,permno,session,representative_row,"
                             "disposition FROM row_map ORDER BY segment,source_row"):
        segment, source_row, _, _, representative_row, state = record
        require(state in DISPOSITIONS, "SAVED_DISPOSITION_INVALID")
        if segment != previous_segment:
            require(segment == previous_segment + 1 and source_row == 2,
                    "SAVED_ROW_MAP_SEGMENT_GAP")
            previous_row = 1
            previous_segment = segment
        require(source_row == previous_row + 1, "SAVED_ROW_MAP_SOURCE_GAP")
        require((state in {"CONFLICT_QUARANTINE", "EQUALITY_UNRESOLVED"})
                == (representative_row is None), "SAVED_REPRESENTATIVE_INVALID")
        digest.update(json.dumps(record, ensure_ascii=False,
                                 separators=(",", ":")).encode("utf-8"))
        digest.update(b"\n")
        counts[state] += 1
        counts[f"segment_{segment}"] += 1
        previous_row = source_row
    require(previous_segment == expected_segments - 1, "SAVED_SEGMENT_COUNT_INVALID")
    return digest.hexdigest(), counts


def group_statistics(db: sqlite3.Connection) -> list[dict]:
    """Independently reconcile key groups from the saved row mapping.

    The key index makes this one ordered pass over groups rather than an
    in-memory list of private identifiers. Only aggregate counts leave SQLite.
    """
    query = """
        WITH groups AS (
          SELECT segment, permno, session, COUNT(*) AS n,
                 MIN(source_row) AS first_row,
                 MIN(disposition) AS low_state,
                 MAX(disposition) AS high_state,
                 COUNT(representative_row) AS representative_count,
                 MIN(representative_row) AS low_rep,
                 MAX(representative_row) AS high_rep
          FROM row_map GROUP BY segment, permno, session
        ), checked AS (
          SELECT segment, n, low_state AS state,
            CASE
              WHEN low_state != high_state THEN 1
              WHEN low_state = 'SINGLETON'
                   AND n = 1 AND representative_count = 1
                   AND low_rep = first_row AND high_rep = first_row THEN 0
              WHEN low_state = 'EQUAL_COLLAPSED'
                   AND n > 1 AND representative_count = n
                   AND low_rep = first_row AND high_rep = first_row THEN 0
              WHEN low_state IN ('CONFLICT_QUARANTINE','EQUALITY_UNRESOLVED')
                   AND n > 1 AND representative_count = 0 THEN 0
              ELSE 1 END AS invalid
          FROM groups
        )
        SELECT segment, COUNT(*) AS key_groups, SUM(n) AS raw_rows,
               SUM(CASE WHEN state='SINGLETON' THEN n ELSE 0 END) AS singleton_rows,
               SUM(CASE WHEN state='EQUAL_COLLAPSED' THEN n ELSE 0 END) AS equal_rows,
               SUM(CASE WHEN state='CONFLICT_QUARANTINE' THEN n ELSE 0 END) AS conflict_rows,
               SUM(CASE WHEN state='EQUALITY_UNRESOLVED' THEN n ELSE 0 END) AS unresolved_rows,
               SUM(CASE WHEN state='EQUAL_COLLAPSED' THEN 1 ELSE 0 END) AS equal_groups,
               SUM(CASE WHEN state='CONFLICT_QUARANTINE' THEN 1 ELSE 0 END) AS conflict_groups,
               SUM(CASE WHEN state='EQUALITY_UNRESOLVED' THEN 1 ELSE 0 END) AS unresolved_groups,
               SUM(invalid) AS invalid_groups
        FROM checked GROUP BY segment ORDER BY segment
    """
    results = []
    for row in db.execute(query):
        segment, groups, raw, singleton, equal_rows, conflict_rows, unresolved_rows, \
            equal_groups, conflict_groups, unresolved_groups, invalid = row
        require(invalid == 0, "SAVED_GROUP_SEMANTICS_INVALID")
        results.append({"segment": segment, "key_groups": groups, "raw_rows": raw,
                        "singleton_rows": singleton, "rows_in_equal_groups": equal_rows,
                        "rows_in_conflict_groups": conflict_rows,
                        "rows_with_unresolved_equality": unresolved_rows,
                        "equal_groups": equal_groups, "conflict_groups": conflict_groups,
                        "unresolved_groups": unresolved_groups,
                        "derived_candidate_rows": singleton + equal_groups})
    return results


def missing_statistics(db: sqlite3.Connection, specs: list[dict]) -> dict:
    """Recount sparse observed-field gaps from the saved private table."""
    allowed = {name.strip().casefold() for name in specs[0]["csv_header"]}
    blank_counts, literal_counts = Counter(), Counter()
    with_blank = with_literal = with_any = 0
    for segment, first_row, blank_json, literal_json, state in db.execute(
            "SELECT a.segment,a.first_source_row,a.blank_fields,a.null_literal_fields,"
            "r.disposition FROM availability_missing a LEFT JOIN row_map r ON "
            "r.segment=a.segment AND r.source_row=a.first_source_row"):
        require(segment in range(len(specs)) and 2 <= first_row <= specs[segment]["row_count"] + 1
                and state in DISPOSITIONS, "SAVED_MISSING_ROW_REFERENCE_INVALID")
        blanks, literals = json.loads(blank_json), json.loads(literal_json)
        require(isinstance(blanks, list) and isinstance(literals, list)
                and all(isinstance(x, str) for x in blanks + literals)
                and len(set(blanks + literals)) == len(blanks) + len(literals)
                and set(blanks + literals).issubset(allowed)
                and (blanks or literals), "SAVED_MISSING_FIELD_SET_INVALID")
        if state in {"CONFLICT_QUARANTINE", "EQUALITY_UNRESOLVED"}:
            continue
        with_any += 1
        with_blank += bool(blanks)
        with_literal += bool(literals)
        blank_counts.update(blanks)
        literal_counts.update(literals)
    return {"observed_keys_with_any_missing_field": with_any,
            "observed_keys_with_blank_field": with_blank,
            "observed_keys_with_null_literal_field": with_literal,
            "observed_key_blank_counts_by_field": {name: blank_counts[name] for name in sorted(allowed)},
            "observed_key_null_literal_counts_by_field":
            {name: literal_counts[name] for name in sorted(allowed)},
            "unit": "NONQUARANTINED_SECURITY_SESSION_KEY_OBSERVED_FIELDS_ONLY"}


def build(specs: list[dict], output: Path, bindings: dict, inventory_hash: str) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    db_path = output / "derived_row_index_private.sqlite3"
    started = time.perf_counter()
    per_segment = []
    with closing(sqlite3.connect(db_path)) as db:
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("PRAGMA synchronous=FULL")
        create_row_map(db)
        for spec in specs:
            with db:
                item = (_read_first_segment(spec, db) if spec["segment"] == 0
                        else _read_saved_index(spec, db))
            per_segment.append({"segment": spec["label"], "year": spec["year"],
                                **item})
        db.execute("CREATE INDEX row_map_key ON row_map(segment,permno,session)")
        groups = group_statistics(db)
        require(len(groups) == len(specs), "DERIVED_GROUP_COUNT_INVALID")
        for segment, item in enumerate(per_segment):
            counted = groups[segment]
            require(counted["segment"] == segment
                    and counted["key_groups"] == item["key_groups"]
                    and counted["raw_rows"] == item["raw_rows"]
                    and counted["equal_groups"] == item["equal_groups"]
                    and counted["conflict_groups"] == item["conflict_groups"]
                    and counted["unresolved_groups"] == item["unresolved_groups"],
                    "DERIVED_GROUP_RECONCILIATION_FAILED")
        fingerprint, mapped_counts = mapping_fingerprint(db)
        missing = missing_statistics(db, specs)
        require(sum(item["raw_rows"] for item in per_segment) == sum(mapped_counts.values())
                - sum(n for key, n in mapped_counts.items() if key.startswith("segment_")),
                "ROW_COUNT_RECONCILIATION_FAILED")
        db.commit()
    verify_source_hashes(specs)
    raw_rows = sum(item["raw_rows"] for item in per_segment)
    summary = {
        "schema": "m5_daily_derived_preparation_safe_v1",
        "scope": "ENGINEERING_PREPARATION_ONLY",
        "rule": "PERMNO_PLUS_DLYCALDT_ALL_PARSED_SOURCE_COLUMNS_EQUAL",
        "parsed_string_equality_not_numeric_equivalence": True,
        "raw_csv_rescanned_segments": [specs[0]["label"]],
        "saved_full_field_indexes_reused_segments": [s["label"] for s in specs[1:]],
        "raw_rows": raw_rows,
        "key_groups": sum(item["key_groups"] for item in per_segment),
        "singleton_rows": mapped_counts["SINGLETON"],
        "rows_in_equal_groups": mapped_counts["EQUAL_COLLAPSED"],
        "rows_in_conflict_groups": mapped_counts["CONFLICT_QUARANTINE"],
        "rows_with_unresolved_equality": mapped_counts["EQUALITY_UNRESOLVED"],
        "equal_key_groups": sum(item["equal_groups"] for item in per_segment),
        "conflict_key_groups": sum(item["conflict_groups"] for item in per_segment),
        "unresolved_key_groups": sum(item["unresolved_groups"] for item in per_segment),
        "derived_candidate_rows": sum(item["derived_candidate_rows"] for item in groups),
        "quarantined_key_groups": sum(item["conflict_groups"] + item["unresolved_groups"]
                                      for item in per_segment),
        "per_segment": per_segment,
        "observed_missing_statistics": missing,
        "per_field_raw_blank_counts": {
            name.casefold(): sum({k.casefold(): v for k, v in s["summary"]["per_column_blank_counts"].items()}
                                 [name.casefold()] for s in specs)
            for name in specs[0]["csv_header"]},
        "per_field_raw_null_literal_counts": {
            name.casefold(): sum({k.casefold(): v for k, v in s["summary"]["per_column_null_literal_counts"].items()}
                                 [name.casefold()] for s in specs)
            for name in specs[0]["csv_header"]},
        "missing_field_unit": "RAW_LOGICAL_ROWS_FROM_SAVED_AUDITS_NOT_DERIVED_CLOSE_VALIDITY",
        "unknown_exit_status": "NOT_CERTIFIED",
        "historical_attribute_status": "NOT_CERTIFIED",
        "absent_security_sessions": None,
        "accepted_price_field_or_adjustment_rule": None,
        "formal_event_mask": None,
        "admitted_observations": None,
        "formal_financial_experiment_status": "NOT_RUN",
        "source_inventory_sha256": inventory_hash,
        "policy_sha256": bindings["policy_sha256"],
        "decision_receipt_sha256": bindings["decision_sha256"],
        "execution_code_sha256": sha256(Path(__file__)),
        "key_normalizer_code_sha256": bindings["normalizer_code_sha256"],
        "mapping_logical_sha256": fingerprint,
        "wall_seconds_process_scope": round(time.perf_counter() - started, 3),
        "peak_memory_bytes": None,
    }
    require(raw_rows == (summary["singleton_rows"] + summary["rows_in_equal_groups"]
                         + summary["rows_in_conflict_groups"]
                         + summary["rows_with_unresolved_equality"]),
            "DISJOINT_ROW_DENOMINATOR_INVALID")
    safe_path_out = output / "summary_safe.json"
    safe_path_out.write_bytes(json_bytes(summary))
    private_manifest = {
        "schema": "m5_daily_derived_preparation_manifest_private_v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "inventory_path": str(INVENTORY),
        "inventory_sha256": inventory_hash,
        "policy_path": str(POLICY), "policy_sha256": bindings["policy_sha256"],
        "decision_path": str(DECISION), "decision_sha256": bindings["decision_sha256"],
        "candidate_sha256": bindings["candidate_sha256"],
        "code_path": str(Path(__file__).resolve()),
        "code_sha256": sha256(Path(__file__)),
        "normalizer_code_sha256": bindings["normalizer_code_sha256"],
        "python_executable": sys.executable,
        "sources": [source_record(spec) for spec in specs],
        "files": {"derived_row_index_private.sqlite3": sha256(db_path),
                  "summary_safe.json": sha256(safe_path_out)},
    }
    (output / "output_manifest_private.json").write_bytes(json_bytes(private_manifest))
    return summary


def verify_output(output: Path) -> dict:
    require_ignored_output(output)
    manifest_path = output / "output_manifest_private.json"
    manifest = read_json(manifest_path)
    require(manifest.get("schema") == "m5_daily_derived_preparation_manifest_private_v1",
            "SAVED_MANIFEST_SCHEMA_INVALID")
    bindings = validate_policy_and_decision()
    specs, inventory_hash = source_specs()
    require(manifest["inventory_sha256"] == inventory_hash
            and manifest["policy_sha256"] == bindings["policy_sha256"]
            and manifest["decision_sha256"] == bindings["decision_sha256"]
            and manifest["candidate_sha256"] == bindings["candidate_sha256"]
            and manifest["code_sha256"] == sha256(Path(__file__)),
            "SAVED_SOURCE_BINDING_CHANGED")
    require(manifest["normalizer_code_sha256"] == bindings["normalizer_code_sha256"],
            "SAVED_SOURCE_BINDING_CHANGED")
    require(manifest["sources"] == [source_record(spec) for spec in specs]
            and manifest["code_path"] == str(Path(__file__).resolve())
            and manifest["inventory_path"] == str(INVENTORY)
            and manifest["policy_path"] == str(POLICY)
            and manifest["decision_path"] == str(DECISION),
            "SAVED_SOURCE_RECORD_CHANGED")
    verify_source_hashes(specs)
    required_files = {"derived_row_index_private.sqlite3", "summary_safe.json"}
    require(set(manifest["files"]) == required_files
            and {path.name for path in output.iterdir()} ==
            required_files | {"output_manifest_private.json"},
            "SAVED_OUTPUT_FILE_SET_INVALID")
    for name, expected in manifest["files"].items():
        require(name in {"derived_row_index_private.sqlite3", "summary_safe.json"}
                and sha256(output / name) == expected, "SAVED_OUTPUT_HASH_MISMATCH")
    summary = read_json(output / "summary_safe.json")
    require(summary["key_normalizer_code_sha256"] == bindings["normalizer_code_sha256"]
            and summary["source_inventory_sha256"] == inventory_hash
            and summary["policy_sha256"] == bindings["policy_sha256"]
            and summary["decision_receipt_sha256"] == bindings["decision_sha256"]
            and summary["execution_code_sha256"] == sha256(Path(__file__))
            and summary["formal_financial_experiment_status"] == "NOT_RUN"
            and summary["unknown_exit_status"] == "NOT_CERTIFIED"
            and summary["historical_attribute_status"] == "NOT_CERTIFIED"
            and summary["absent_security_sessions"] is None
            and summary["accepted_price_field_or_adjustment_rule"] is None
            and summary["missing_field_unit"]
            == "RAW_LOGICAL_ROWS_FROM_SAVED_AUDITS_NOT_DERIVED_CLOSE_VALIDITY",
            "SAVED_SOURCE_BINDING_CHANGED")
    with closing(sqlite3.connect(f"file:{(output / 'derived_row_index_private.sqlite3').as_posix()}?mode=ro",
                                 uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        require(db.execute("PRAGMA quick_check").fetchone()[0] == "ok",
                "SAVED_SQLITE_INTEGRITY_INVALID")
        fingerprint, counts = mapping_fingerprint(db)
        groups = group_statistics(db)
        missing = missing_statistics(db, specs)
        for segment, spec in enumerate(specs):
            require(counts[f"segment_{segment}"] == spec["row_count"],
                    "SAVED_SEGMENT_ROW_COUNT_MISMATCH")
            require(len(groups) == len(specs)
                    and groups[segment]["segment"] == segment,
                    "SAVED_GROUP_COUNT_INVALID")
            saved_segment = summary["per_segment"][segment]
            counted = groups[segment]
            require(saved_segment["segment"] == spec["label"]
                    and saved_segment["year"] == spec["year"]
                    and saved_segment["raw_rows"] == counted["raw_rows"]
                    and saved_segment["key_groups"] == counted["key_groups"]
                    and saved_segment["equal_groups"] == counted["equal_groups"]
                    and saved_segment["conflict_groups"] == counted["conflict_groups"]
                    and saved_segment["unresolved_groups"] == counted["unresolved_groups"]
                    and saved_segment["duplicate_extra_rows"]
                    == counted["raw_rows"] - counted["key_groups"],
                    "SAVED_GROUP_SUMMARY_MISMATCH")
            require(saved_segment["equality_evidence"] == (
                "NEW_FULL_FIELD_PARSED_CSV_SCAN" if segment == 0 else
                "SAVED_FULL_FIELD_INDEX_AND_EXCEPTION_HASHES"),
                "SAVED_EQUALITY_EVIDENCE_CHANGED")
    for key, old_key in (("per_field_raw_blank_counts", "per_column_blank_counts"),
                         ("per_field_raw_null_literal_counts", "per_column_null_literal_counts")):
        expected = {name.casefold(): sum(
            {k.casefold(): v for k, v in spec["summary"][old_key].items()}[name.casefold()]
            for spec in specs) for name in specs[0]["csv_header"]}
        require(summary[key] == expected, "SAVED_MISSING_SOURCE_TOTAL_CHANGED")
    require(fingerprint == summary["mapping_logical_sha256"]
            and summary["raw_rows"] == sum(s["row_count"] for s in specs)
            and summary["raw_rows"] == sum(counts[state] for state in DISPOSITIONS)
            and summary["singleton_rows"] == counts["SINGLETON"]
            and summary["rows_in_equal_groups"] == counts["EQUAL_COLLAPSED"]
            and summary["rows_in_conflict_groups"] == counts["CONFLICT_QUARANTINE"]
            and summary["rows_with_unresolved_equality"] == counts["EQUALITY_UNRESOLVED"]
            and summary["key_groups"] == sum(item["key_groups"] for item in groups)
            and summary["equal_key_groups"] == sum(item["equal_groups"] for item in groups)
            and summary["conflict_key_groups"] == sum(item["conflict_groups"] for item in groups)
            and summary["unresolved_key_groups"] == sum(item["unresolved_groups"] for item in groups)
            and summary["derived_candidate_rows"]
            == sum(item["derived_candidate_rows"] for item in groups)
            and summary["quarantined_key_groups"]
            == sum(item["conflict_groups"] + item["unresolved_groups"] for item in groups)
            and summary["observed_missing_statistics"] == missing
            and summary["formal_event_mask"] is None
            and summary["admitted_observations"] is None,
            "SAVED_MAPPING_OR_STATUS_MISMATCH")
    return {"status": "DAILY_DERIVED_OUTPUT_VERIFIED", "raw_rows": summary["raw_rows"],
            "key_groups": summary["key_groups"], "derived_candidate_rows":
            summary["derived_candidate_rows"], "mapping_logical_sha256": fingerprint}


def main(argv: list[str] | None = None) -> int:
    class SafeArgumentParser(argparse.ArgumentParser):
        def error(self, message: str) -> None:
            print("M5_DAILY_PREPARATION_FAILED ARGUMENTS_INVALID")
            raise SystemExit(2)

    parser = SafeArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("run", "verify-output"):
        command = sub.add_parser(action)
        command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        require_ignored_output(args.output)
        if args.action == "run":
            require(not args.output.exists(), "OUTPUT_ALREADY_EXISTS")
            bindings = validate_policy_and_decision()
            specs, inventory_hash = source_specs()
            verify_source_hashes(specs)
            build(specs, args.output, bindings, inventory_hash)
            print("M5_DAILY_DERIVED_PREPARATION_COMPLETE")
        else:
            verify_output(args.output)
            print("M5_DAILY_DERIVED_OUTPUT_VERIFIED")
        return 0
    except PreparationError as exc:
        print(f"M5_DAILY_PREPARATION_FAILED {exc}")
        return 2
    except (FileNotFoundError, OSError, ValueError, sqlite3.DatabaseError,
            csv.Error, KeyError, TypeError):
        print("M5_DAILY_PREPARATION_FAILED INPUT_OR_OUTPUT_INVALID")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
