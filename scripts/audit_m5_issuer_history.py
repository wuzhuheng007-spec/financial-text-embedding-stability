"""Audit two local CRSP issuer-history exports and retain unaccepted joins.

Licensed rows and identifiers remain in a new ignored artifacts directory.
The terminal reports fixed status codes only. No cohort or financial label is made.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from audit_m5_wrds_intake import (
    AuditError, check_ignored_output, normalize_id, row_digest, sha256_file,
    write_directory,
)


EXPECTED_COLUMNS = (
    "permco", "issinfostartdt", "issinfoenddt", "issuerbegdt", "issuerenddt",
    "issuerhdrflg", "cnum", "issuernm", "securityrangecnt", "securitytotalcnt",
    "usincflg", "issuertype", "issuerstatustype", "siccd", "naics",
    "icbindustry", "uesindustry", "nasdcompno",
)
SPECS = {
    "EARLY": ("2846728fa21637dda76406f14f2540cdaa100f5bd70062ccb3aee781eaa651e6",
              date(1925, 12, 31), date(2020, 1, 1)),
    "LATER": ("7f234d4338b6674de35b646e15577e511c7ce268137e852052702231d9d2c0b6",
              date(2020, 1, 2), date(2026, 6, 30)),
}
PRIOR_HASHES = {
    "registry": "8482df5d3dfccc72e5513179aa1b91f0847321488c0c1c9161f95d8e10cf08ec",
    "index": "de8df60b998d94d76a8452685bb94b27aa68ff864b5850a796b612824d3895bf",
    "summary": "9d33b02743f3d7683462a286e0f25221c957ad00e75788152817091712ef6ebe",
}
SOURCE_TABLE = "crsp_q_stock.stkissuerinfohist"
ENDPOINT_CONVENTION = "CLOSED_DATE_COMPARISON_ONLY_PROVIDER_ENDPOINT_RULE_UNVERIFIED"


def require(condition: bool, code: str) -> None:
    if not condition:
        raise AuditError(code)


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def parse_query(data: bytes, role: str, *, expected_hash: str | None = None) -> tuple[str, ...]:
    expected, first, last = SPECS[role]
    require(hashlib.sha256(data).hexdigest() == (expected_hash or expected),
            "ISSUER_SQL_HASH_MISMATCH")
    try:
        sql = data.decode("utf-8-sig")
    except UnicodeError as exc:
        raise AuditError("ISSUER_SQL_ENCODING_INVALID") from exc
    match = re.fullmatch(
        r"\s*SELECT\s+(?P<columns>.*?)\s+FROM\s+(?P<table>[a-z0-9_.]+)\s+"
        r"WHERE\s+(?P<where>.*?)\s*;?\s*", sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    require(match is not None, "ISSUER_SQL_SHAPE_MISMATCH")
    require(match.group("table").lower() == SOURCE_TABLE, "ISSUER_SQL_TABLE_MISMATCH")
    prefix = SOURCE_TABLE + "."
    expressions = [item.strip().lower() for item in match.group("columns").split(",")]
    require(tuple(expressions) == tuple(prefix + field for field in EXPECTED_COLUMNS),
            "ISSUER_SQL_COLUMNS_MISMATCH")
    where = re.sub(r"\s+", " ", match.group("where").strip()).lower()
    expected_where = f"issinfostartdt between '{first}'::date and '{last}'::date"
    require(where == expected_where, "ISSUER_SQL_FILTER_MISMATCH")
    return EXPECTED_COLUMNS


def parse_iso(value: str) -> date | None:
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value.strip()) is None:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _scan_issuer_csv(path: Path, role: str, columns: tuple[str, ...]):
    """Collect row-level structural issues; a bad row blocks dependent joins."""
    before = sha256_file(path)
    records = []
    issues = []
    count = 0
    header = None
    first, last = SPECS[role][1:]
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream, strict=True)
        raw_header = next(reader, None)
        if raw_header is not None:
            header = tuple(cell.strip().lower() for cell in raw_header)
        if header != columns:
            issues.append({"source_role": role, "source_record": None,
                           "codes": ["ISSUER_CSV_HEADER_SQL_MISMATCH"]})
        for cells in reader:
            count += 1
            codes = []
            if header != columns or len(cells) != len(columns):
                codes.append("ISSUER_CSV_ROW_WIDTH_OR_HEADER_INVALID")
                issues.append({"source_role": role, "source_record": count,
                               "codes": codes})
                continue
            row = dict(zip(columns, cells))
            permco = normalize_id(row["permco"].strip())
            start = parse_iso(row["issinfostartdt"])
            end = parse_iso(row["issinfoenddt"])
            if permco is None:
                codes.append("ISSUER_PERMCO_INVALID")
            if start is None or end is None:
                codes.append("ISSUER_INTERVAL_DATE_INVALID")
            if start is not None and not first <= start <= last:
                codes.append("ISSUER_START_OUTSIDE_QUERY")
            if start is not None and end is not None and start > end:
                codes.append("ISSUER_INTERVAL_REVERSED")
            if codes:
                issues.append({"source_role": role, "source_record": count,
                               "normalized_row_sha256": row_digest(row), "codes": codes})
                continue
            records.append({"source_role": role, "source_record": count,
                            "source_sha256": before, "normalized_row_sha256": row_digest(row),
                            "permco": permco, "start": start.isoformat(), "end": end.isoformat(),
                            "raw_fields": row})
    require(sha256_file(path) == before, "ISSUER_CSV_CHANGED_DURING_READ")
    return {"sha256": before, "bytes": path.stat().st_size, "row_count": count,
            "valid_row_count": len(records), "column_count": len(header or ()),
            "header_matches_sql": header == columns,
            "unique_valid_permco_count": len({row["permco"] for row in records}),
            "structural_issue_count": len(issues)}, records, issues


def inspect_issuer_csv(path: Path, role: str, columns: tuple[str, ...] = EXPECTED_COLUMNS):
    try:
        return _scan_issuer_csv(path, role, columns)
    except (csv.Error, UnicodeError):
        source_hash = sha256_file(path)
        issue = {"source_role": role, "source_record": None,
                 "codes": ["ISSUER_CSV_PARSE_OR_ENCODING_ERROR"]}
        return {"sha256": source_hash, "bytes": path.stat().st_size,
                "row_count": None, "valid_row_count": None,
                "column_count": None, "header_matches_sql": False,
                "unique_valid_permco_count": None, "structural_issue_count": 1}, [], [issue]


def load_prior(prior_dir: Path, *, expected_hashes: dict[str, str] | None = None):
    """Read prior private results by pinned identity, without rerunning the audit."""
    hashes = PRIOR_HASHES if expected_hashes is None else expected_hashes
    paths = {
        "registry": prior_dir / "m5_wrds_intake_registry.json",
        "index": prior_dir / "m5_wrds_history_candidates_private.json",
        "summary": prior_dir / "m5_wrds_intake_aggregate_summary.json",
    }
    for key, path in paths.items():
        require(sha256_file(path) == hashes[key], "PRIOR_" + key.upper() + "_HASH_MISMATCH")
    registry = json.loads(paths["registry"].read_text(encoding="utf-8"))
    index = json.loads(paths["index"].read_text(encoding="utf-8"))
    summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
    require(registry.get("local_binding_status") == "LOCAL_FILE_MATCHES_RECEIPT_AND_QUERY_SHAPE"
            and registry.get("formal_m5_cohort_selected") is False
            and index.get("accepted") is False
            and summary.get("formal_m5_cohort_selected") is False,
            "PRIOR_STATUS_INVALID")
    raw = {"STARTING_SNAPSHOT": registry["local_snapshot"],
           "LATER_CHANGES": registry["local_changes"]}
    raw["LATER_CHANGES"]["column_names"] = raw["STARTING_SNAPSHOT"]["column_names"]
    for role, source in raw.items():
        path = Path(source["path"]).resolve(strict=True)
        require(sha256_file(path) == source["sha256"], "PRIOR_RAW_HASH_MISMATCH")
        key = "snapshot_sha256" if role == "STARTING_SNAPSHOT" else "changes_sha256"
        require(index["source_bindings"][key] == source["sha256"]
                and summary[key] == source["sha256"], "PRIOR_RAW_BINDING_MISMATCH")
    require(index["source_bindings"]["links_sha256"] == registry["local_links"]["sha256"]
            and index["source_bindings"]["v2_receipt_sha256"] ==
            registry["saved_v2_receipt"]["sha256"]
            and index["source_bindings"]["intake_script_sha256"] ==
            registry["intake_script_sha256"], "PRIOR_BINDING_MISMATCH")
    require(sha256_file(Path(registry["local_links"]["path"])) ==
            registry["local_links"]["sha256"]
            and sha256_file(Path(registry["saved_v2_receipt"]["path"])) ==
            registry["saved_v2_receipt"]["sha256"]
            and sha256_file(Path(__file__).with_name("audit_m5_wrds_intake.py")) ==
            registry["intake_script_sha256"], "PRIOR_SOURCE_OR_CODE_HASH_MISMATCH")
    for key, path in paths.items():
        require(sha256_file(path) == hashes[key], "PRIOR_CHANGED_DURING_READ")
    return registry, index, summary, raw, hashes


def load_security_intervals(index: dict, raw: dict):
    """Verify every retained source row against the prior index and recover PERMCO."""
    needed = defaultdict(dict)
    for security in index["starting_securities"]:
        for entry in security["history"]:
            role, number = entry["source"], entry["source_record"]
            require(role in raw and isinstance(number, int), "PRIOR_SECURITY_ENTRY_INVALID")
            needed[role][number] = (security["permno"], entry)
    source_rows = {}
    for role, source in raw.items():
        found = {}
        with Path(source["path"]).open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            require(reader.fieldnames is not None and
                    {name.lower() for name in reader.fieldnames} == set(source["column_names"]),
                    "PRIOR_SECURITY_HEADER_INVALID")
            for number, row in enumerate(reader, 1):
                if number in needed[role]:
                    normalized = {key.strip().lower(): value.strip() for key, value in row.items()}
                    found[number] = normalized
        require(set(found) == set(needed[role]), "PRIOR_SECURITY_ROW_MISSING")
        require(sha256_file(Path(source["path"])) == source["sha256"],
                "PRIOR_RAW_CHANGED_DURING_READ")
        source_rows[role] = found
    intervals = []
    starting_permcos = set()
    changed_permcos = set()
    changed_security_count = 0
    issues = []
    for security in index["starting_securities"]:
        original = None
        entries = []
        for entry in security["history"]:
            role, number = entry["source"], entry["source_record"]
            row = source_rows[role][number]
            require(row_digest(row) == entry["normalized_row_sha256"]
                    and normalize_id(row["permno"]) == security["permno"]
                    and row["secinfostartdt"] == entry["start"]
                    and row["secinfoenddt"] == entry["end"],
                    "PRIOR_SECURITY_ROW_BINDING_MISMATCH")
            permco = normalize_id(row["permco"])
            if permco is None:
                issues.append({"permno": security["permno"], "source_role": role,
                               "source_record": number, "codes": ["SECURITY_PERMCO_INVALID"]})
            if role == "STARTING_SNAPSHOT":
                require(original is None, "PRIOR_STARTING_ROW_DUPLICATE")
                original = permco
            entries.append({"permno": security["permno"], "permco": permco,
                            "source_role": role, "source_record": number,
                            "source_sha256": raw[role]["sha256"],
                            "normalized_row_sha256": entry["normalized_row_sha256"],
                            "start": entry["start"], "end": entry["end"],
                            "historical_cusip": entry["historical_cusip"],
                            "prior_cik_candidates_accepted": False})
        require(original is not None or any(item["source_role"] == "STARTING_SNAPSHOT"
                                        for item in entries), "PRIOR_STARTING_ROW_MISSING")
        if original is not None:
            starting_permcos.add(original)
        changed = False
        for item in entries:
            if item["permco"] is not None and item["permco"] != original:
                changed_permcos.add(item["permco"])
                changed = True
                item["historical_permco_change_candidate"] = True
            else:
                item["historical_permco_change_candidate"] = False
        changed_security_count += changed
        intervals.extend(entries)
    return intervals, starting_permcos, changed_permcos, changed_security_count, issues


def issuer_interval_issues(records: list[dict]) -> list[dict]:
    grouped = defaultdict(list)
    for record in records:
        grouped[record["permco"]].append(record)
    issues = []
    for permco, rows in grouped.items():
        ordered = sorted(rows, key=lambda x: (x["start"], x["end"],
                                               x["source_role"], x["source_record"]))
        seen = {}
        furthest_end = None
        furthest_row = None
        for row in ordered:
            key = (row["start"], row["end"])
            if key in seen:
                code = ("EXACT_DUPLICATE_ISSUER_ROW" if
                        row["raw_fields"] == seen[key]["raw_fields"] else
                        "SAME_INTERVAL_ATTRIBUTE_CONFLICT")
                issues.append({"permco": permco, "source_records": [
                    [seen[key]["source_role"], seen[key]["source_record"]],
                    [row["source_role"], row["source_record"]]], "codes": [code]})
            else:
                seen[key] = row
            if furthest_end is not None:
                start = date.fromisoformat(row["start"])
                end = date.fromisoformat(furthest_end)
                if start < end:
                    code = "ISSUER_INTERVAL_OVERLAP"
                elif start == end:
                    code = "ISSUER_ENDPOINT_TOUCH_SEMANTICS_PENDING"
                elif start > end + timedelta(days=1):
                    code = "ISSUER_INTERVAL_GAP"
                else:
                    code = None
                if code:
                    issues.append({"permco": permco, "source_records": [
                        [furthest_row["source_role"], furthest_row["source_record"]],
                        [row["source_role"], row["source_record"]]], "codes": [code]})
            if furthest_end is None or row["end"] > furthest_end:
                furthest_end, furthest_row = row["end"], row
    return issues


def join_candidates(securities: list[dict], issuer_rows: list[dict]):
    by_permco = defaultdict(list)
    for record in issuer_rows:
        by_permco[record["permco"]].append(record)
    output = []
    issues = []
    counts = Counter()
    for security in securities:
        matches = []
        for issuer in by_permco.get(security["permco"], ()):
            start = max(security["start"], issuer["start"])
            end = min(security["end"], issuer["end"])
            if start <= end:
                matches.append({"issuer_source_role": issuer["source_role"],
                                "issuer_source_record": issuer["source_record"],
                                "issuer_source_sha256": issuer["source_sha256"],
                                "issuer_row_sha256": issuer["normalized_row_sha256"],
                                "intersection_start": start, "intersection_end": end,
                                "endpoint_equality_review":
                                start == issuer["end"] or end == issuer["start"],
                                "accepted": False})
        matches.sort(key=lambda x: (x["intersection_start"], x["intersection_end"],
                                    x["issuer_source_role"], x["issuer_source_record"]))
        if not matches:
            counts["zero"] += 1
            issues.append({"permno": security["permno"], "permco": security["permco"],
                           "source_role": security["source_role"],
                           "source_record": security["source_record"],
                           "codes": ["NO_ISSUER_INTERVAL_INTERSECTION"]})
        elif len(matches) == 1:
            counts["one"] += 1
        else:
            counts["multiple"] += 1
            issues.append({"permno": security["permno"], "permco": security["permco"],
                           "source_role": security["source_role"],
                           "source_record": security["source_record"],
                           "codes": ["MULTIPLE_ISSUER_INTERVAL_INTERSECTIONS"]})
        cursor = date.fromisoformat(security["start"])
        last = date.fromisoformat(security["end"])
        covered = False
        for match in matches:
            begin = date.fromisoformat(match["intersection_start"])
            finish = date.fromisoformat(match["intersection_end"])
            if begin <= cursor <= finish:
                if finish >= last:
                    covered = True
                    break
                cursor = finish + timedelta(days=1)
        if not covered:
            counts["not_fully_covered"] += 1
            issues.append({"permno": security["permno"], "permco": security["permco"],
                           "source_role": security["source_role"],
                           "source_record": security["source_record"],
                           "codes": ["ISSUER_DATES_DO_NOT_COVER_SECURITY_INTERVAL"]})
        output.append({**security, "issuer_matches": matches,
                       "issuer_match_count": len(matches),
                       "issuer_date_coverage_under_closed_comparison": covered,
                       "accepted": False})
    counts["security_interval_count"] = len(output)
    counts["issuer_candidate_rows"] = sum(item["issuer_match_count"] for item in output)
    for key in ("zero", "one", "multiple", "not_fully_covered"):
        counts.setdefault(key, 0)
    return output, issues, dict(counts)


def make_outputs(early: tuple, later: tuple, prior: tuple, args,
                 *, script_hash: str, query_hashes: dict[str, str]):
    early_meta, early_rows, early_issues = early
    later_meta, later_rows, later_issues = later
    registry, prior_index, prior_summary, raw, prior_hashes = prior
    all_rows = early_rows + later_rows
    structural_issues = early_issues + later_issues
    base = {
        "schema": "m5_issuer_history_local_registry_v1", "scope": "ENGINEERING_ONLY",
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "downloaded_at_utc": None, "wrds_query_ids": {"EARLY": None, "LATER": None},
        "provider_release_vintage": None, "license_or_export_terms_verified": False,
        "acquisition_scope_change": "TWO_FULL_LIBRARY_START_DATE_SEGMENTS_INSTEAD_OF_INITIAL_PERMCO_LIST",
        "query_spec_status": "ISSUER_QUERY_SPEC_VERIFIED",
        "issuer_sources": {
            "EARLY": {"csv_path": str(args.early_csv.resolve()), "sql_path": str(args.early_sql.resolve()),
                      "sql_sha256": query_hashes["EARLY"], **early_meta},
            "LATER": {"csv_path": str(args.later_csv.resolve()), "sql_path": str(args.later_sql.resolve()),
                      "sql_sha256": query_hashes["LATER"], **later_meta},
        },
        "prior_artifacts": {"directory": str(args.prior_dir.resolve()),
                            "sha256": prior_hashes},
        "prior_source_sha256": {role: source["sha256"] for role, source in raw.items()},
        "prior_source_paths": {role: source["path"] for role, source in raw.items()},
        "prior_links_path": registry["local_links"]["path"],
        "prior_links_sha256": registry["local_links"]["sha256"],
        "prior_v2_receipt_path": registry["saved_v2_receipt"]["path"],
        "prior_v2_receipt_sha256": registry["saved_v2_receipt"]["sha256"],
        "prior_intake_script_sha256": registry["intake_script_sha256"],
        "issuer_audit_script_path": str(Path(__file__).resolve()),
        "issuer_audit_script_sha256": script_hash,
        "python_executable": sys.executable, "python_version": sys.version,
        "issuer_endpoint_convention": ENDPOINT_CONVENTION,
        "server_page_row_reconciliation": "PENDING_QUERY_IDS_AND_PAGE_COUNTS",
        "provider_universe_completeness": "NOT_VERIFIED",
        "historical_cik_link_validity": "NOT_ESTABLISHED",
        "formal_m5_cohort_selected": False, "m5_protocol_status": "PROPOSED",
        "financial_experiment_run": False,
    }
    if structural_issues:
        base["local_csv_binding_status"] = "REJECTED_STRUCTURAL_INPUT"
        output = {"schema": "m5_issuer_history_rejection_v1", "scope": "ENGINEERING_ONLY",
                  "status": "REJECTED_STRUCTURAL_INPUT", "issues": structural_issues,
                  "accepted": False}
        summary = {"schema": "m5_issuer_history_summary_v1", "scope": "ENGINEERING_ONLY",
                   "status": "REJECTED_STRUCTURAL_INPUT", "issuer_early_rows": early_meta["row_count"],
                   "issuer_later_rows": later_meta["row_count"],
                   "structural_issue_count": len(structural_issues),
                   "issuer_candidate_rows": None, "formal_m5_cohort_selected": False,
                   "index_sha256": hashlib.sha256(json_bytes(output)).hexdigest(),
                   "registry_sha256": hashlib.sha256(json_bytes(base)).hexdigest()}
        return base, output, summary
    security_rows, initial, changed, changed_count, security_issues = load_security_intervals(
        prior_index, raw)
    related = initial | changed
    issuer_issues = issuer_interval_issues(all_rows)
    joined, join_issues, join_counts = join_candidates(security_rows, all_rows)
    relevant_rows = [row for row in all_rows if row["permco"] in related]
    unrelated_count = len(all_rows) - len(relevant_rows)
    issue_counts = Counter(code for issue in security_issues + issuer_issues + join_issues
                           for code in issue["codes"])
    earliest_end = date(2025, 12, 31)
    latest_issuer_end = {}
    for row in relevant_rows:
        latest_issuer_end[row["permco"]] = max(
            latest_issuer_end.get(row["permco"], "0001-01-01"), row["end"])
    early_ending = {permco for permco, end in latest_issuer_end.items()
                    if date.fromisoformat(end) < earliest_end}
    output = {
        "schema": "m5_issuer_history_candidates_private_v1", "scope": "ENGINEERING_ONLY",
        "accepted": False, "formal_m5_cohort_selected": False,
        "issuer_endpoint_convention": ENDPOINT_CONVENTION,
        "starting_permco_membership": sorted(initial),
        "changed_permco_identity_candidates": sorted(changed),
        "related_issuer_records": relevant_rows,
        "security_interval_issuer_candidates": joined,
        "exceptions": security_issues + issuer_issues + join_issues,
        "summary": {
            "issuer_early_rows": early_meta["row_count"],
            "issuer_later_rows": later_meta["row_count"],
            "issuer_total_rows": len(all_rows),
            "issuer_total_unique_permcos": len({row["permco"] for row in all_rows}),
            "initial_related_unique_permcos": len(initial),
            "changed_related_unique_permcos": len(changed),
            "securities_with_historical_permco_change_candidates": changed_count,
            "related_issuer_rows": len(relevant_rows),
            "unrelated_full_library_issuer_rows": unrelated_count,
            "related_permcos_last_observed_issinfoenddt_before_2025_12_31": len(early_ending),
            "issuer_issue_counts": dict(sorted(issue_counts.items())),
            "exception_record_count": len(security_issues + issuer_issues + join_issues),
            **join_counts,
        },
    }
    base["local_csv_binding_status"] = "LOCAL_ISSUER_CSV_STRUCTURE_AND_HASH_BOUND"
    base["candidate_status"] = "UNACCEPTED_IDENTITY_AND_INTERVAL_CANDIDATES"
    summary = {
        "schema": "m5_issuer_history_summary_v1", "scope": "ENGINEERING_ONLY",
        "status": "LOCAL_ISSUER_CSV_STRUCTURE_AND_HASH_BOUND",
        "query_sql_sha256": query_hashes,
        "issuer_csv_sha256": {"EARLY": early_meta["sha256"], "LATER": later_meta["sha256"]},
        "prior_artifact_sha256": prior_hashes,
        "issuer_audit_script_sha256": script_hash,
        "issuer_early_columns": early_meta["column_count"],
        "issuer_later_columns": later_meta["column_count"],
        **output["summary"],
        "issuer_endpoint_convention": ENDPOINT_CONVENTION,
        "provider_universe_completeness": "NOT_VERIFIED",
        "historical_cik_link_validity": "NOT_ESTABLISHED",
        "formal_m5_cohort_selected": False,
        "financial_experiment_run": False,
    }
    summary["index_sha256"] = hashlib.sha256(json_bytes(output)).hexdigest()
    summary["registry_sha256"] = hashlib.sha256(json_bytes(base)).hexdigest()
    return base, output, summary


def run(args, *, sql_hashes: dict[str, str] | None = None,
        prior_hashes: dict[str, str] | None = None):
    check_ignored_output(args.output_dir.resolve())
    sql_hashes = {role: (sql_hashes or {}).get(role, SPECS[role][0]) for role in SPECS}
    paths = {"EARLY": (args.early_csv.resolve(strict=True), args.early_sql.resolve(strict=True)),
             "LATER": (args.later_csv.resolve(strict=True), args.later_sql.resolve(strict=True))}
    require(len({path for pair in paths.values() for path in pair}) == 4,
            "ISSUER_SOURCE_PATHS_NOT_DISTINCT")
    query_hashes = {}
    inspected = {}
    for role, (csv_path, sql_path) in paths.items():
        sql_data = sql_path.read_bytes()
        columns = parse_query(sql_data, role, expected_hash=sql_hashes[role])
        query_hashes[role] = sha256_file(sql_path)
        inspected[role] = inspect_issuer_csv(csv_path, role, columns)
        require(sha256_file(sql_path) == query_hashes[role],
                "ISSUER_SQL_CHANGED_DURING_READ")
    prior = load_prior(args.prior_dir.resolve(strict=True), expected_hashes=prior_hashes)
    script_hash = sha256_file(Path(__file__).resolve())
    registry, index, summary = make_outputs(inspected["EARLY"], inspected["LATER"], prior,
                                            args, script_hash=script_hash,
                                            query_hashes=query_hashes)
    for role, (csv_path, sql_path) in paths.items():
        require(sha256_file(csv_path) == inspected[role][0]["sha256"]
                and sha256_file(sql_path) == query_hashes[role],
                "ISSUER_SOURCE_CHANGED_DURING_AUDIT")
    write_directory(args.output_dir.resolve(), {
        "m5_issuer_history_registry.json": registry,
        "m5_issuer_history_index_private.json": index,
        "m5_issuer_history_summary.json": summary,
    })
    return summary


def verify_output(output_dir: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    require(output_dir.is_relative_to(root / "artifacts"), "OUTPUT_NOT_UNDER_LOCAL_ARTIFACTS")
    registry_path = output_dir / "m5_issuer_history_registry.json"
    index_path = output_dir / "m5_issuer_history_index_private.json"
    summary_path = output_dir / "m5_issuer_history_summary.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    require(sha256_file(registry_path) == summary["registry_sha256"]
            and sha256_file(index_path) == summary["index_sha256"],
            "ISSUER_OUTPUT_HASH_MISMATCH")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    require(Path(registry["issuer_audit_script_path"]).resolve() == Path(__file__).resolve()
            and registry["issuer_audit_script_sha256"] == sha256_file(Path(__file__).resolve())
            == summary.get("issuer_audit_script_sha256", registry["issuer_audit_script_sha256"]),
            "ISSUER_CODE_HASH_MISMATCH")
    for role, record in registry["issuer_sources"].items():
        require(sha256_file(Path(record["csv_path"])) == record["sha256"],
                "ISSUER_CSV_HASH_MISMATCH")
        require(sha256_file(Path(record["sql_path"])) == record["sql_sha256"],
                "ISSUER_SQL_HASH_MISMATCH")
        parse_query(Path(record["sql_path"]).read_bytes(), role)
    for key, expected in registry["prior_artifacts"]["sha256"].items():
        filename = {"registry": "m5_wrds_intake_registry.json",
                    "index": "m5_wrds_history_candidates_private.json",
                    "summary": "m5_wrds_intake_aggregate_summary.json"}[key]
        require(sha256_file(Path(registry["prior_artifacts"]["directory"]) / filename) == expected,
                "PRIOR_ARTIFACT_HASH_MISMATCH")
    for role, path in registry["prior_source_paths"].items():
        require(sha256_file(Path(path)) == registry["prior_source_sha256"][role],
                "PRIOR_RAW_HASH_MISMATCH")
    require(sha256_file(Path(registry["prior_links_path"])) ==
            registry["prior_links_sha256"]
            and sha256_file(Path(registry["prior_v2_receipt_path"])) ==
            registry["prior_v2_receipt_sha256"], "PRIOR_SOURCE_HASH_MISMATCH")
    require(index.get("accepted") is False
            and index.get("formal_m5_cohort_selected") is False
            and summary.get("formal_m5_cohort_selected") is False,
            "ISSUER_OUTPUT_STATUS_INVALID")
    if summary["status"] == "LOCAL_ISSUER_CSV_STRUCTURE_AND_HASH_BOUND":
        require(registry["local_csv_binding_status"] == summary["status"]
                and summary["query_sql_sha256"] ==
                {role: item["sql_sha256"] for role, item in registry["issuer_sources"].items()}
                and summary["issuer_csv_sha256"] ==
                {role: item["sha256"] for role, item in registry["issuer_sources"].items()}
                and summary["prior_artifact_sha256"] ==
                registry["prior_artifacts"]["sha256"]
                and all(summary.get(key) == value for key, value in index["summary"].items())
                and len(index["security_interval_issuer_candidates"]) ==
                summary["security_interval_count"]
                and len(index["related_issuer_records"]) == summary["related_issuer_rows"]
                and len(index["exceptions"]) == summary["exception_record_count"],
                "ISSUER_OUTPUT_COUNT_MISMATCH")
        require(all(item.get("accepted") is False and
                    item.get("prior_cik_candidates_accepted") is False and
                    all(match.get("accepted") is False for match in item["issuer_matches"])
                    for item in index["security_interval_issuer_candidates"]),
                "ISSUER_CANDIDATE_ACCEPTANCE_INVALID")


def write_failure(args, code: str) -> None:
    """Keep a fixed-code private receipt when a fatal gate rejects the intake."""
    sources = {}
    for name in ("early_csv", "early_sql", "later_csv", "later_sql"):
        path = getattr(args, name, None)
        sources[name] = {"path": str(path) if path else None,
                         "sha256": sha256_file(path) if path and path.is_file() else None}
    record = {"schema": "m5_issuer_history_fatal_rejection_v1",
              "scope": "ENGINEERING_ONLY", "status": "REJECTED_LOCAL_BINDING",
              "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
              "issue_codes": [code], "sources": sources,
              "prior_dir": str(args.prior_dir) if args.prior_dir else None,
              "formal_m5_cohort_selected": False}
    try:
        write_directory(args.output_dir.resolve(),
                        {"m5_issuer_history_fatal_rejection_private.json": record})
    except (AuditError, OSError):
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "verify-output"))
    parser.add_argument("--early-csv", type=Path)
    parser.add_argument("--early-sql", type=Path)
    parser.add_argument("--later-csv", type=Path)
    parser.add_argument("--later-sql", type=Path)
    parser.add_argument("--prior-dir", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "run":
            require(all(getattr(args, name) is not None for name in
                        ("early_csv", "early_sql", "later_csv", "later_sql", "prior_dir")),
                    "ISSUER_INPUT_ARGUMENT_MISSING")
            summary = run(args)
            if summary["status"] == "REJECTED_STRUCTURAL_INPUT":
                print("M5_ISSUER_HISTORY_STRUCTURAL_REVIEW_REQUIRED")
                return 2
            print("M5_ISSUER_HISTORY_LOCAL_CANDIDATES_COMPLETE")
            print("M5_ISSUER_VALIDITY_AND_FORMAL_COHORT_PENDING")
        else:
            verify_output(args.output_dir.resolve(strict=True))
            print("M5_ISSUER_HISTORY_SAVED_OUTPUT_VERIFIED")
        return 0
    except (AuditError, OSError, UnicodeError, ValueError, KeyError, csv.Error) as exc:
        code = str(exc) if isinstance(exc, AuditError) else "LOCAL_IO_OR_FORMAT_ERROR"
        if args.command == "run":
            write_failure(args, code)
        print("M5_ISSUER_HISTORY_AUDIT_REVIEW_REQUIRED")
        return 2


if __name__ == "__main__":
    sys.exit(main())
