"""Bind one local filtered WRDS delisting export to prior M5 identity evidence.

All licensed rows and identifiers remain in an ignored local directory. This
script inventories review candidates only; it never computes financial labels.
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
from decimal import Decimal, InvalidOperation
from pathlib import Path

from audit_m5_wrds_intake import (
    AuditError, check_ignored_output, normalize_id, row_digest, sha256_file,
    write_directory,
)
from audit_m5_issuer_history import verify_output as verify_issuer_output


QUERY_SHA256 = "dfd1533ecc737f47891d7105a9cd0542a8dc7db053d603ba894059e9452b318f"
PRIOR_WRDS_HASHES = {
    "registry": "8482df5d3dfccc72e5513179aa1b91f0847321488c0c1c9161f95d8e10cf08ec",
    "index": "de8df60b998d94d76a8452685bb94b27aa68ff864b5850a796b612824d3895bf",
    "summary": "9d33b02743f3d7683462a286e0f25221c957ad00e75788152817091712ef6ebe",
}
PRIOR_ISSUER_HASHES = {
    "registry": "713f7deeb6aa43b5e43b62482e3c36755c49123b5b28c9f63b36c7d822be6194",
    "index": "370318027c222965de32467641c00998e5c3eb985344db41891807c4e6a0a833",
    "summary": "db71f33d7cc662099703100f4f5c38dd68a77234f352b1b7ca5d4febe96fef75",
}
WINDOW_START = date(2019, 11, 1)
WINDOW_END = date(2026, 2, 28)
ACTIONS = ("GDR", "GEX", "GLI", "LOS", "MER")
HEADER_FIELDS = ("primaryexch", "nasdissuno", "siccd")
EXPECTED_COLUMNS = (
    "delactiontype", "permno", "delistingdt", "primaryexch", "nasdissuno",
    "siccd", "deldtprc", "deldtprcflg", "delstatustype", "delreasontype",
    "delpaymenttype", "delpermno", "delpermco", "delret", "delretmisstype",
    "delnextdt", "delnextprc", "delnextprcflg", "delamtdt", "deldivamt",
    "deldistype", "deldlydt",
)
EVENT_FIELDS = tuple(field for field in EXPECTED_COLUMNS if field not in HEADER_FIELDS)
OPTIONAL_DATES = ("delnextdt", "delamtdt", "deldlydt")
OPTIONAL_IDS = ("delpermno", "delpermco")
SOURCE_SCOPE = "FILTERED_DELIST_ACTIONS_WITH_UNDATED_HEADER_LEFT_JOIN"


def require(condition: bool, code: str) -> None:
    if not condition:
        raise AuditError(code)


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def parse_iso(value: str) -> date | None:
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value.strip()) is None:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def successor_key_class(value: str) -> str:
    text = value.strip()
    if not text:
        return "BLANK"
    if normalize_id(text) is not None:
        return "POSITIVE_JOIN_CANDIDATE"
    try:
        number = Decimal(text)
        if number.is_finite() and number == number.to_integral_value() and number <= 0:
            return "NONPOSITIVE_NUMERIC_NOT_JOINED"
    except InvalidOperation:
        pass
    return "OTHER_UNRESOLVED_NOT_JOINED"


def parse_query(data: bytes, expected_hash: str = QUERY_SHA256) -> tuple[str, ...]:
    require(hashlib.sha256(data).hexdigest() == expected_hash, "DELIST_SQL_HASH_MISMATCH")
    try:
        normalized = re.sub(r"\s+", " ", data.decode("utf-8-sig").strip()).lower()
    except UnicodeError as exc:
        raise AuditError("DELIST_SQL_ENCODING_INVALID") from exc
    match = re.fullmatch(r"select (?P<fields>.*?) (?P<tail>from crsp_q_stock\.stkdelists .*?)",
                         normalized)
    require(match is not None, "DELIST_SQL_SHAPE_INVALID")
    expected_select = tuple(
        ("id_table." if field in HEADER_FIELDS else "crsp_q_stock.stkdelists.") + field
        for field in EXPECTED_COLUMNS
    )
    require(tuple(item.strip() for item in match.group("fields").split(",")) == expected_select,
            "DELIST_SQL_FIELDS_INVALID")
    expected_tail = (
        "from crsp_q_stock.stkdelists left join ( select permno, primaryexch, "
        "nasdissuno, siccd from crsp_q_stock.stksecurityinfohdr ) as id_table "
        "on crsp_q_stock.stkdelists.permno = id_table.permno "
        "where crsp_q_stock.stkdelists.delistingdt between "
        "'2019-11-01'::date and '2026-02-28'::date "
        'and ("crsp_q_stock"."stkdelists"."delactiontype" = '
        "any (array['gdr','gex','gli','los','mer']))"
    )
    require(match.group("tail") == expected_tail, "DELIST_SQL_JOIN_OR_FILTER_INVALID")
    return EXPECTED_COLUMNS


def _scan_export(path: Path, columns: tuple[str, ...]):
    """Keep every row and diagnostic; a bad core field blocks dependent joins."""
    source_hash = sha256_file(path)
    records = []
    issues = []
    core_issues = 0
    header = None
    row_count = 0
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream, strict=True)
        raw_header = next(reader, None)
        header = tuple(cell.strip().lower() for cell in raw_header) if raw_header else ()
        if len(header) != len(set(header)):
            issues.append({"source_record": None, "codes": ["DELIST_CSV_HEADER_DUPLICATE"]})
            core_issues += 1
        if header != columns:
            issues.append({"source_record": None, "codes": ["DELIST_CSV_HEADER_SQL_MISMATCH"]})
            core_issues += 1
        for cells in reader:
            row_count += 1
            if len(cells) != len(columns) or header != columns:
                issues.append({"source_record": row_count,
                               "codes": ["DELIST_CSV_ROW_WIDTH_OR_HEADER_INVALID"]})
                core_issues += 1
                continue
            raw = dict(zip(columns, cells))
            event = {key: raw[key] for key in EVENT_FIELDS}
            header_values = {key: raw[key] for key in HEADER_FIELDS}
            permno = normalize_id(raw["permno"].strip())
            event_date = parse_iso(raw["delistingdt"])
            action = raw["delactiontype"].strip()
            codes = []
            if permno is None:
                codes.append("DELIST_PERMNO_INVALID")
            if event_date is None:
                codes.append("DELIST_DATE_INVALID")
            elif not WINDOW_START <= event_date <= WINDOW_END:
                codes.append("DELIST_DATE_OUTSIDE_QUERY")
            if action not in ACTIONS:
                codes.append("DELIST_ACTION_OUTSIDE_QUERY")
            core = bool(codes)
            if core:
                core_issues += 1
            for key in OPTIONAL_IDS:
                if successor_key_class(raw[key]) in (
                        "NONPOSITIVE_NUMERIC_NOT_JOINED", "OTHER_UNRESOLVED_NOT_JOINED"):
                    codes.append(key.upper() + "_NOT_POSITIVE_JOIN_KEY")
            for key in OPTIONAL_DATES:
                if raw[key].strip() and parse_iso(raw[key]) is None:
                    codes.append(key.upper() + "_UNUSABLE")
            if raw["delret"].strip():
                try:
                    if not Decimal(raw["delret"].strip()).is_finite():
                        codes.append("DELRET_NONFINITE_OR_UNUSABLE")
                except InvalidOperation:
                    codes.append("DELRET_NONFINITE_OR_UNUSABLE")
            if codes:
                issues.append({"source_record": row_count,
                               "normalized_row_sha256": row_digest(raw),
                               "codes": codes})
            records.append({
                "source_record": row_count, "source_sha256": source_hash,
                "normalized_row_sha256": row_digest(raw),
                "permno": permno, "delistingdt": event_date.isoformat() if event_date else None,
                "delactiontype": action, "event_fields": event,
                "header_fields": header_values,
                "header_provenance": "STKSECURITYINFOHDR_UNDATED_RECENT_VALUES",
                "issues": codes, "core_valid": not core, "accepted": False,
            })
    require(sha256_file(path) == source_hash, "DELIST_CSV_CHANGED_DURING_READ")
    return {"sha256": source_hash, "bytes": path.stat().st_size,
            "row_count": row_count, "parsed_row_count": len(records),
            "column_count": len(header), "header": list(header),
            "header_matches_sql": header == columns,
            "core_issue_count": core_issues, "issue_record_count": len(issues)}, records, issues


def inspect_export(path: Path, columns: tuple[str, ...] = EXPECTED_COLUMNS):
    try:
        return _scan_export(path, columns)
    except (csv.Error, UnicodeError):
        source_hash = sha256_file(path)
        issue = {"source_record": None,
                 "codes": ["DELIST_CSV_PARSE_OR_ENCODING_ERROR"]}
        return {"sha256": source_hash, "bytes": path.stat().st_size,
                "row_count": None, "parsed_row_count": None,
                "column_count": None, "header": None,
                "header_matches_sql": False,
                "core_issue_count": 1, "issue_record_count": 1}, [], [issue]


def load_prior(wrds_dir: Path, issuer_dir: Path):
    """Rehash saved source chains; do not rerun prior extraction or inference."""
    verify_issuer_output(issuer_dir)
    wrds_names = {"registry": "m5_wrds_intake_registry.json",
                  "index": "m5_wrds_history_candidates_private.json",
                  "summary": "m5_wrds_intake_aggregate_summary.json"}
    issuer_names = {"registry": "m5_issuer_history_registry.json",
                    "index": "m5_issuer_history_index_private.json",
                    "summary": "m5_issuer_history_summary.json"}
    for key, filename in wrds_names.items():
        require(sha256_file(wrds_dir / filename) == PRIOR_WRDS_HASHES[key],
                "PRIOR_WRDS_ARTIFACT_HASH_MISMATCH")
    for key, filename in issuer_names.items():
        require(sha256_file(issuer_dir / filename) == PRIOR_ISSUER_HASHES[key],
                "PRIOR_ISSUER_ARTIFACT_HASH_MISMATCH")
    wrds_index = json.loads((wrds_dir / wrds_names["index"]).read_text(encoding="utf-8"))
    issuer_index = json.loads((issuer_dir / issuer_names["index"]).read_text(encoding="utf-8"))
    issuer_registry = json.loads((issuer_dir / issuer_names["registry"]).read_text(
        encoding="utf-8"))
    require(sha256_file(Path(__file__).with_name("audit_m5_wrds_intake.py")) ==
            issuer_registry["prior_intake_script_sha256"],
            "PRIOR_HELPER_CODE_HASH_MISMATCH")
    require(wrds_index["accepted"] is False and issuer_index["accepted"] is False,
            "PRIOR_CANDIDATES_UNEXPECTEDLY_ACCEPTED")
    starting = {security["permno"] for security in wrds_index["starting_securities"]}
    require(len(starting) == len(wrds_index["starting_securities"]),
            "PRIOR_STARTING_IDS_DUPLICATED")
    security_index = {security["permno"]: security for security in
                      wrds_index["starting_securities"]}
    issuer_by_security = defaultdict(list)
    for interval in issuer_index["security_interval_issuer_candidates"]:
        require(interval["permno"] in starting and interval["accepted"] is False,
                "PRIOR_ISSUER_SECURITY_BINDING_INVALID")
        issuer_by_security[interval["permno"]].append(interval)
    require(len(issuer_by_security) == len(starting),
            "PRIOR_ISSUER_STARTING_COVERAGE_MISMATCH")
    issuer_last_end = {}
    for row in issuer_index["related_issuer_records"]:
        issuer_last_end[row["permco"]] = max(
            issuer_last_end.get(row["permco"], "0001-01-01"), row["end"])
    for key, filename in wrds_names.items():
        require(sha256_file(wrds_dir / filename) == PRIOR_WRDS_HASHES[key],
                "PRIOR_WRDS_CHANGED_DURING_READ")
    for key, filename in issuer_names.items():
        require(sha256_file(issuer_dir / filename) == PRIOR_ISSUER_HASHES[key],
                "PRIOR_ISSUER_CHANGED_DURING_READ")
    return starting, security_index, issuer_by_security, issuer_last_end


def review_groups(records: list[dict]):
    """Preserve duplicates; review keys are not asserted event primary keys."""
    groups = defaultdict(list)
    for item in records:
        if item["core_valid"]:
            groups[(item["permno"], item["delistingdt"], item["delactiontype"])].append(item)
    reviewed = []
    counts = Counter()
    for (permno, event_date, action), rows in groups.items():
        full_signatures = Counter(item["normalized_row_sha256"] for item in rows)
        event_signatures = {row_digest(item["event_fields"]) for item in rows}
        header_signatures = {row_digest(item["header_fields"]) for item in rows}
        codes = []
        exact_extra = sum(value - 1 for value in full_signatures.values() if value > 1)
        if exact_extra:
            codes.append("EXACT_FULL_ROW_DUPLICATE")
            counts["exact_duplicate_extra_rows"] += exact_extra
        if len(event_signatures) == 1 and len(header_signatures) > 1:
            codes.append("SAME_EVENT_PAYLOAD_HEADER_VARIANT")
        if len(event_signatures) > 1:
            codes.append("REVIEW_KEY_EVENT_PAYLOAD_CONFLICT")
        if len(rows) > 1:
            counts["groups_with_multiple_rows"] += 1
        for code in codes:
            counts[code] += 1
        reviewed.append({"permno": permno, "delistingdt": event_date,
                         "delactiontype": action,
                         "source_records": [item["source_record"] for item in rows],
                         "row_count": len(rows), "distinct_full_row_count": len(full_signatures),
                         "distinct_event_payload_count": len(event_signatures),
                         "distinct_header_payload_count": len(header_signatures),
                         "codes": codes, "accepted": False})
    counts["review_group_count"] = len(reviewed)
    for key in ("groups_with_multiple_rows", "exact_duplicate_extra_rows",
                "EXACT_FULL_ROW_DUPLICATE", "SAME_EVENT_PAYLOAD_HEADER_VARIANT",
                "REVIEW_KEY_EVENT_PAYLOAD_CONFLICT"):
        counts.setdefault(key, 0)
    return reviewed, dict(counts)


def split_issuer_intersections(issuer_by_security: dict):
    """Refine prior multiple intersections without changing their saved edges."""
    details = []
    counts = Counter()
    for permno, intervals in issuer_by_security.items():
        for interval in intervals:
            matches = interval["issuer_matches"]
            if len(matches) <= 1:
                continue
            ordered = sorted(matches, key=lambda item: (item["intersection_start"],
                                                        item["intersection_end"]))
            furthest_end = None
            overlap = endpoint_touch = internal_gap = False
            for match in ordered:
                start = date.fromisoformat(match["intersection_start"])
                end = date.fromisoformat(match["intersection_end"])
                if furthest_end is not None:
                    if start < furthest_end:
                        overlap = True
                    elif start == furthest_end:
                        endpoint_touch = True
                    elif start > furthest_end + timedelta(days=1):
                        internal_gap = True
                if furthest_end is None or end > furthest_end:
                    furthest_end = end
            uncovered = not interval["issuer_date_coverage_under_closed_comparison"]
            if not (overlap or endpoint_touch or internal_gap or uncovered):
                category = "SEQUENTIAL_CLOSED_DATE_SEGMENTS"
            else:
                category = "OVERLAP_TOUCH_OR_COVERAGE_REVIEW"
            counts[category] += 1
            counts["overlap"] += overlap
            counts["endpoint_touch"] += endpoint_touch
            counts["internal_gap"] += internal_gap
            counts["uncovered"] += uncovered
            details.append({"permno": permno, "security_source_role": interval["source_role"],
                            "security_source_record": interval["source_record"],
                            "issuer_match_count": len(matches), "category": category,
                            "overlap": overlap, "endpoint_touch": endpoint_touch,
                            "internal_gap": internal_gap, "uncovered": uncovered,
                            "accepted": False})
    counts["multiple_intersection_security_intervals"] = len(details)
    for key in ("SEQUENTIAL_CLOSED_DATE_SEGMENTS", "OVERLAP_TOUCH_OR_COVERAGE_REVIEW",
                "overlap", "endpoint_touch", "internal_gap", "uncovered"):
        counts.setdefault(key, 0)
    return details, dict(counts)


def diagnose_starting(records: list[dict], groups: list[dict], starting: set[str],
                      security_index: dict, issuer_by_security: dict,
                      issuer_last_end: dict):
    by_security_groups = Counter(group["permno"] for group in groups)
    counts = Counter()
    identity_notes = []
    for permno in starting:
        n = by_security_groups[permno]
        counts["starting_with_zero_review_groups" if n == 0 else
               "starting_with_one_review_group" if n == 1 else
               "starting_with_multiple_review_groups"] += 1
    for item in records:
        if not item["core_valid"]:
            continue
        if item["permno"] not in starting:
            counts["outside_starting_export_rows"] += 1
            continue
        counts["starting_export_rows"] += 1
        intervals = issuer_by_security[item["permno"]]
        event_date = item["delistingdt"]
        active = [interval for interval in intervals if
                  interval["start"] <= event_date <= interval["end"]]
        latest_security_end = max(interval["end"] for interval in intervals)
        relation = ("AFTER_LAST_SECURITY_HISTORY_END" if event_date > latest_security_end
                    else "ON_LAST_SECURITY_HISTORY_END" if event_date == latest_security_end
                    else "BEFORE_LAST_SECURITY_HISTORY_END")
        counts["event_vs_last_security_history_" + relation] += 1
        counts["active_security_interval_count_" + str(len(active))] += 1
        permcos = {interval["permco"] for interval in active if interval["permco"]}
        last_preceding_only = False
        no_preceding = False
        if not active:
            preceding = [interval for interval in intervals if interval["end"] <= event_date]
            if preceding:
                latest_preceding_end = max(interval["end"] for interval in preceding)
                permcos = {interval["permco"] for interval in preceding
                           if interval["end"] == latest_preceding_end and interval["permco"]}
                last_preceding_only = True
            else:
                no_preceding = True
                counts["event_without_preceding_security_history_interval"] += 1
        elif not permcos:
            counts["event_active_security_permco_missing"] += 1
        issuer_relations = []
        for permco in sorted(permcos):
            end = issuer_last_end.get(permco)
            status = ("NO_ISSUER_HISTORY_ROW" if end is None else
                      "AFTER_LAST_ISSUER_RANGE_END" if event_date > end else
                      "ON_LAST_ISSUER_RANGE_END" if event_date == end else
                      "BEFORE_LAST_ISSUER_RANGE_END")
            prefix = ("event_vs_last_preceding_issuer_history_" if last_preceding_only else
                      "event_vs_active_issuer_history_")
            counts[prefix + status] += 1
            issuer_relations.append({"permco": permco, "last_issinfoenddt": end,
                                     "relation": status,
                                     "identity_basis": "LAST_PRECEDING_NOT_EVENT_DATE_VALID" if
                                     last_preceding_only else "EVENT_DATE_SECURITY_INTERVAL"})
        old = security_index[item["permno"]]
        counts["starting_event_rows_with_historical_cusip_change"] += bool(
            old["historical_cusip_changed"])
        for key in OPTIONAL_IDS:
            successor = (normalize_id(item["event_fields"][key].strip())
                         if item["event_fields"][key].strip() else None)
            if successor is not None:
                counts[key + "_candidate_rows"] += 1
        identity_notes.append({"source_record": item["source_record"],
                               "permno": item["permno"], "delistingdt": event_date,
                               "active_security_intervals": [
                                   {"source_role": interval["source_role"],
                                    "source_record": interval["source_record"],
                                    "permco": interval["permco"]} for interval in active],
                               "latest_security_history_end": latest_security_end,
                               "security_history_end_relation": relation,
                               "no_preceding_security_interval": no_preceding,
                               "issuer_history_end_relations": issuer_relations,
                               "historical_cusip_changed": old["historical_cusip_changed"],
                               "successor_permno_candidate":
                               normalize_id(item["event_fields"]["delpermno"].strip()),
                               "successor_permco_candidate":
                               normalize_id(item["event_fields"]["delpermco"].strip()),
                               "successor_link_accepted": False,
                               "formal_cohort_admitted": False})
    counts["starting_security_count"] = len(starting)
    counts["outside_starting_distinct_permnos"] = len({item["permno"] for item in records
        if item["core_valid"] and item["permno"] not in starting})
    counts["starting_review_groups"] = sum(item["permno"] in starting for item in groups)
    counts["outside_starting_review_groups"] = len(groups) - counts["starting_review_groups"]
    for key in ("starting_export_rows", "outside_starting_export_rows",
                "starting_with_zero_review_groups", "starting_with_one_review_group",
                "starting_with_multiple_review_groups", "delpermno_candidate_rows",
                "delpermco_candidate_rows"):
        counts.setdefault(key, 0)
    return identity_notes, dict(counts)


def make_outputs(args, query_hash: str, export: tuple, prior: tuple,
                 script_hash: str):
    csv_meta, records, structural_issues = export
    starting, security_index, issuer_by_security, issuer_last_end = prior
    buffer_weekdays = sum((WINDOW_START + timedelta(days=offset)).weekday() < 5
                          for offset in range((WINDOW_END - WINDOW_START).days + 1))
    groups, group_counts = review_groups(records)
    refinements, refinement_counts = split_issuer_intersections(issuer_by_security)
    identity_notes, identity_counts = diagnose_starting(
        records, groups, starting, security_index, issuer_by_security, issuer_last_end)
    action_counts = Counter(item["delactiontype"] for item in records if item["core_valid"])
    availability = {key: sum(bool(item["header_fields" if key in HEADER_FIELDS
                                  else "event_fields"][key].strip()) for item in records)
                    for key in EXPECTED_COLUMNS}
    empty = {key: len(records) - nonempty for key, nonempty in availability.items()}
    successor_classes = {key: dict(sorted(Counter(
        successor_key_class(item["event_fields"][key]) for item in records).items()))
        for key in OPTIONAL_IDS}
    issue_counts = Counter(code for item in structural_issues for code in item["codes"])
    candidate_index = {
        "schema": "m5_delist_candidates_private_v1", "scope": "ENGINEERING_ONLY",
        "accepted": False, "formal_m5_cohort_selected": False,
        "source_scope": SOURCE_SCOPE, "header_history_status": "UNDATED_HEADER_NOT_HISTORICAL",
        "event_primary_key_status": "NOT_ESTABLISHED",
        "join_multiplicity_status": "JOIN_MULTIPLICITY_NOT_INDEPENDENTLY_VERIFIED",
        "issuer_endpoint_comparison": "CLOSED_DATE_DIAGNOSTIC_ONLY",
        "raw_export_records": records, "review_groups": groups,
        "starting_identity_diagnostics": identity_notes,
        "issuer_multiple_intersection_refinement": refinements,
        "exceptions": structural_issues,
    }
    summary = {
        "schema": "m5_delist_intake_summary_v1", "scope": "ENGINEERING_ONLY",
        "status": "LOCAL_DELIST_SOURCE_BOUND_REVIEW_ONLY",
        "query_sql_sha256": query_hash, "export_csv_sha256": csv_meta["sha256"],
        "audit_script_sha256": script_hash, "export_row_count": csv_meta["row_count"],
        "parsed_export_row_count": csv_meta["parsed_row_count"],
        "export_column_count": csv_meta["column_count"],
        "distinct_export_permnos": len({item["permno"] for item in records
                                       if item["core_valid"]}),
        "action_row_counts": {action: action_counts[action] for action in ACTIONS},
        "field_nonempty_row_counts": availability,
        "field_empty_row_counts": empty,
        "successor_join_key_class_counts": successor_classes,
        "source_issue_code_counts": dict(sorted(issue_counts.items())),
        "source_issue_record_count": len(structural_issues),
        "core_issue_count": csv_meta["core_issue_count"],
        "daily_seed_starting_permno_count": len(starting),
        "daily_buffer_weekday_grid_days": buffer_weekdays,
        "daily_planning_grid_cells_not_observed_rows": len(starting) * buffer_weekdays,
        **group_counts, **identity_counts,
        "issuer_multiple_intersection_refinement": refinement_counts,
        "prior_wrds_artifact_sha256": PRIOR_WRDS_HASHES,
        "prior_issuer_artifact_sha256": PRIOR_ISSUER_HASHES,
        "wrds_query_id": None, "provider_page_row_count": None,
        "provider_universe_completeness": "NOT_VERIFIED",
        "event_source_uniqueness": "NOT_INDEPENDENTLY_VERIFIED",
        "historical_cik_link_validity": "NOT_ESTABLISHED",
        "formal_m5_cohort_selected": False, "m5_protocol_status": "PROPOSED",
        "financial_label_calculated": False, "financial_experiment_run": False,
    }
    registry = {
        "schema": "m5_delist_local_registry_v1", "scope": "ENGINEERING_ONLY",
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "downloaded_at_utc": None, "wrds_query_id": None,
        "provider_page_row_count": None, "provider_release_vintage": None,
        "license_or_export_terms_verified": False,
        "sql_path": str(args.sql.resolve()), "sql_sha256": query_hash,
        "csv_path": str(args.csv.resolve()), **csv_meta,
        "source_table": "crsp_q_stock.stkdelists",
        "header_join_table": "crsp_q_stock.stksecurityinfohdr",
        "header_join": "LEFT_ON_PERMNO_WITHOUT_EVENT_DATE_CONDITION",
        "main_event_field_count": len(EVENT_FIELDS),
        "header_field_names": list(HEADER_FIELDS),
        "date_filter_inclusive": [WINDOW_START.isoformat(), WINDOW_END.isoformat()],
        "action_filter": list(ACTIONS),
        "permno_list_filter": False, "query_limit": None,
        "source_scope": SOURCE_SCOPE,
        "daily_seed_basis": "ALL_PRIOR_STARTING_PERMNOS_NO_SUCCESSOR_AUTO_ADDITION",
        "prior_wrds_artifacts_dir": str(args.prior_wrds_dir.resolve()),
        "prior_wrds_artifact_sha256": PRIOR_WRDS_HASHES,
        "prior_issuer_artifacts_dir": str(args.prior_issuer_dir.resolve()),
        "prior_issuer_artifact_sha256": PRIOR_ISSUER_HASHES,
        "script_path": str(Path(__file__).resolve()), "script_sha256": script_hash,
        "helper_script_path": str(Path(__file__).with_name("audit_m5_wrds_intake.py").resolve()),
        "helper_script_sha256": sha256_file(Path(__file__).with_name(
            "audit_m5_wrds_intake.py")),
        "issuer_verifier_path": str(Path(__file__).with_name(
            "audit_m5_issuer_history.py").resolve()),
        "issuer_verifier_sha256": sha256_file(Path(__file__).with_name(
            "audit_m5_issuer_history.py")),
        "python_executable": sys.executable, "python_version": sys.version,
        "formal_m5_cohort_selected": False, "m5_protocol_status": "PROPOSED",
        "financial_experiment_run": False,
    }
    summary["index_sha256"] = hashlib.sha256(json_bytes(candidate_index)).hexdigest()
    summary["registry_sha256"] = hashlib.sha256(json_bytes(registry)).hexdigest()
    return registry, candidate_index, summary


def make_manifest(files: dict[str, dict]) -> dict:
    return {"schema": "m5_delist_saved_files_manifest_v1",
            "scope": "ENGINEERING_ONLY",
            "file_sha256": {name: hashlib.sha256(json_bytes(data)).hexdigest()
                            for name, data in files.items()}}


def write_structural_rejection(output: Path, args, query_hash: str, export: tuple):
    meta, records, issues = export
    registry = {"schema": "m5_delist_structural_registry_v1",
                "scope": "ENGINEERING_ONLY", "status": "REJECTED_CORE_STRUCTURE",
                "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
                "sql_path": str(args.sql.resolve()), "sql_sha256": query_hash,
                "csv_path": str(args.csv.resolve()), **meta,
                "script_path": str(Path(__file__).resolve()),
                "script_sha256": sha256_file(Path(__file__).resolve()),
                "formal_m5_cohort_selected": False}
    private = {"schema": "m5_delist_structural_issues_private_v1",
               "scope": "ENGINEERING_ONLY", "status": "REJECTED_CORE_STRUCTURE",
               "raw_export_records": records, "issues": issues,
               "formal_m5_cohort_selected": False}
    summary = {"schema": "m5_delist_structural_summary_v1",
               "scope": "ENGINEERING_ONLY", "status": "REJECTED_CORE_STRUCTURE",
               "query_sql_sha256": query_hash, "export_csv_sha256": meta["sha256"],
               "export_row_count": meta["row_count"],
               "core_issue_count": meta["core_issue_count"],
               "issue_record_count": len(issues),
               "candidate_join_run": False, "formal_m5_cohort_selected": False}
    files = {"m5_delist_registry.json": registry,
             "m5_delist_structural_issues_private.json": private,
             "m5_delist_summary.json": summary}
    files["m5_delist_manifest.json"] = make_manifest(files)
    write_directory(output, files)


def run(args, *, expected_sql_hash: str = QUERY_SHA256):
    output = args.output_dir.resolve()
    check_ignored_output(output)
    sql = args.sql.resolve(strict=True)
    csv_path = args.csv.resolve(strict=True)
    require(sql != csv_path, "DELIST_INPUT_PATHS_NOT_DISTINCT")
    sql_bytes = sql.read_bytes()
    columns = parse_query(sql_bytes, expected_sql_hash)
    query_hash = sha256_file(sql)
    export = inspect_export(csv_path, columns)
    if export[0]["core_issue_count"]:
        write_structural_rejection(output, args, query_hash, export)
        return {"status": "REJECTED_CORE_STRUCTURE"}
    prior = load_prior(args.prior_wrds_dir.resolve(strict=True),
                       args.prior_issuer_dir.resolve(strict=True))
    script_hash = sha256_file(Path(__file__).resolve())
    registry, index, summary = make_outputs(args, query_hash, export, prior, script_hash)
    require(sha256_file(sql) == query_hash and
            sha256_file(csv_path) == export[0]["sha256"],
            "DELIST_SOURCE_CHANGED_DURING_AUDIT")
    files = {
        "m5_delist_registry.json": registry,
        "m5_delist_candidates_private.json": index,
        "m5_delist_summary.json": summary,
        "m5_daily_seed_permnos_private.json": {
            "schema": "m5_daily_seed_identifiers_private_v1",
            "scope": "DOWNLOAD_INPUT_ONLY_NOT_FORMAL_COHORT",
            "source_prior_index_sha256": PRIOR_WRDS_HASHES["index"],
            "permno_count": len(prior[0]),
            "permnos": sorted(prior[0]),
            "successor_ids_auto_added": False,
        },
    }
    files["m5_delist_manifest.json"] = make_manifest(files)
    write_directory(output, files)
    return summary


def verify_output(output: Path):
    root = Path(__file__).resolve().parents[1]
    require(output.is_relative_to(root / "artifacts"), "OUTPUT_NOT_UNDER_LOCAL_ARTIFACTS")
    paths = {"registry": output / "m5_delist_registry.json",
             "index": output / "m5_delist_candidates_private.json",
             "summary": output / "m5_delist_summary.json",
             "daily_seed": output / "m5_daily_seed_permnos_private.json"}
    manifest = json.loads((output / "m5_delist_manifest.json").read_text(encoding="utf-8"))
    require(manifest.get("schema") == "m5_delist_saved_files_manifest_v1"
            and set(manifest.get("file_sha256", {})) == {path.name for path in paths.values()},
            "DELIST_MANIFEST_INVALID")
    for path in paths.values():
        require(sha256_file(path) == manifest["file_sha256"][path.name],
                "DELIST_MANIFEST_FILE_HASH_MISMATCH")
    registry = json.loads(paths["registry"].read_text(encoding="utf-8"))
    summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
    require(sha256_file(paths["registry"]) == summary["registry_sha256"]
            and sha256_file(paths["index"]) == summary["index_sha256"],
            "DELIST_OUTPUT_HASH_MISMATCH")
    index = json.loads(paths["index"].read_text(encoding="utf-8"))
    require(Path(registry["script_path"]).resolve() == Path(__file__).resolve()
            and sha256_file(Path(__file__).resolve()) == registry["script_sha256"]
            == summary["audit_script_sha256"], "DELIST_CODE_HASH_MISMATCH")
    require(Path(registry["helper_script_path"]).resolve() ==
            Path(__file__).with_name("audit_m5_wrds_intake.py").resolve()
            and sha256_file(Path(registry["helper_script_path"])) ==
            registry["helper_script_sha256"]
            and Path(registry["issuer_verifier_path"]).resolve() ==
            Path(__file__).with_name("audit_m5_issuer_history.py").resolve()
            and sha256_file(Path(registry["issuer_verifier_path"])) ==
            registry["issuer_verifier_sha256"], "DELIST_HELPER_CODE_HASH_MISMATCH")
    require(sha256_file(Path(registry["csv_path"])) == registry["sha256"]
            == summary["export_csv_sha256"] and
            sha256_file(Path(registry["sql_path"])) == registry["sql_sha256"]
            == summary["query_sql_sha256"], "DELIST_INPUT_HASH_MISMATCH")
    parse_query(Path(registry["sql_path"]).read_bytes())
    for prefix in ("wrds", "issuer"):
        directory = Path(registry[f"prior_{prefix}_artifacts_dir"])
        names = ({"registry": "m5_wrds_intake_registry.json",
                  "index": "m5_wrds_history_candidates_private.json",
                  "summary": "m5_wrds_intake_aggregate_summary.json"} if prefix == "wrds" else
                 {"registry": "m5_issuer_history_registry.json",
                  "index": "m5_issuer_history_index_private.json",
                  "summary": "m5_issuer_history_summary.json"})
        for key, filename in names.items():
            require(sha256_file(directory / filename) ==
                    registry[f"prior_{prefix}_artifact_sha256"][key],
                    "DELIST_PRIOR_ARTIFACT_HASH_MISMATCH")
    verify_issuer_output(Path(registry["prior_issuer_artifacts_dir"]))
    meta, saved_source_rows, source_issues = inspect_export(Path(registry["csv_path"]))
    require(meta["core_issue_count"] == 0
            and saved_source_rows == index["raw_export_records"]
            and source_issues == index["exceptions"],
            "DELIST_SAVED_ROWS_SOURCE_MISMATCH")
    prior = load_prior(Path(registry["prior_wrds_artifacts_dir"]),
                       Path(registry["prior_issuer_artifacts_dir"]))
    daily_seed = json.loads(paths["daily_seed"].read_text(encoding="utf-8"))
    weekdays = sum((WINDOW_START + timedelta(days=offset)).weekday() < 5
                   for offset in range((WINDOW_END - WINDOW_START).days + 1))
    require(daily_seed["source_prior_index_sha256"] == PRIOR_WRDS_HASHES["index"]
            and daily_seed["scope"] == "DOWNLOAD_INPUT_ONLY_NOT_FORMAL_COHORT"
            and daily_seed["permnos"] == sorted(prior[0])
            and daily_seed["permno_count"] == len(prior[0])
            and daily_seed["successor_ids_auto_added"] is False
            and summary["daily_seed_starting_permno_count"] == len(prior[0])
            and summary["daily_buffer_weekday_grid_days"] == weekdays
            and summary["daily_planning_grid_cells_not_observed_rows"] ==
            len(prior[0]) * weekdays, "DELIST_DAILY_SEED_MISMATCH")
    groups, group_counts = review_groups(saved_source_rows)
    refinements, refinement_counts = split_issuer_intersections(prior[2])
    notes, identity_counts = diagnose_starting(saved_source_rows, groups, *prior)
    require(groups == index["review_groups"]
            and refinements == index["issuer_multiple_intersection_refinement"]
            and notes == index["starting_identity_diagnostics"]
            and all(summary.get(key) == value for key, value in
                    {**group_counts, **identity_counts}.items())
            and summary["issuer_multiple_intersection_refinement"] == refinement_counts,
            "DELIST_SAVED_DERIVATION_MISMATCH")
    action_counts = Counter(item["delactiontype"] for item in saved_source_rows)
    nonempty = {key: sum(bool(item["header_fields" if key in HEADER_FIELDS
                                   else "event_fields"][key].strip())
                         for item in saved_source_rows) for key in EXPECTED_COLUMNS}
    require(summary["action_row_counts"] == {action: action_counts[action]
                                              for action in ACTIONS}
            and summary["field_nonempty_row_counts"] == nonempty
            and summary["field_empty_row_counts"] == {
                key: len(saved_source_rows) - value for key, value in nonempty.items()}
            and summary["successor_join_key_class_counts"] == {
                key: dict(sorted(Counter(successor_key_class(
                    item["event_fields"][key]) for item in saved_source_rows).items()))
                for key in OPTIONAL_IDS}
            and summary["distinct_export_permnos"] == len({item["permno"]
                for item in saved_source_rows})
            and summary["source_issue_record_count"] == len(source_issues)
            and summary["source_issue_code_counts"] == dict(sorted(Counter(
                code for issue in source_issues for code in issue["codes"]).items()))
            and summary["core_issue_count"] == meta["core_issue_count"]
            and summary["export_row_count"] == meta["row_count"]
            and summary["parsed_export_row_count"] == meta["parsed_row_count"]
            and summary["export_column_count"] == meta["column_count"],
            "DELIST_SAVED_SUMMARY_MISMATCH")
    require(index["accepted"] is False and summary["formal_m5_cohort_selected"] is False
            and index["formal_m5_cohort_selected"] is False
            and all(item["accepted"] is False for item in index["raw_export_records"])
            and all(item["accepted"] is False for item in index["review_groups"])
            and all(item["successor_link_accepted"] is False for item in
                    index["starting_identity_diagnostics"]),
            "DELIST_ACCEPTANCE_STATUS_INVALID")
    require(len(index["raw_export_records"]) == summary["export_row_count"]
            and len(index["review_groups"]) == summary["review_group_count"]
            and len(index["starting_identity_diagnostics"]) ==
            summary["starting_export_rows"]
            and len(index["issuer_multiple_intersection_refinement"]) ==
            summary["issuer_multiple_intersection_refinement"][
                "multiple_intersection_security_intervals"],
            "DELIST_OUTPUT_COUNT_MISMATCH")


def write_failure(args, code: str):
    sources = {}
    for key in ("csv", "sql"):
        path = getattr(args, key, None)
        sources[key] = {"path": str(path) if path else None,
                        "sha256": sha256_file(path) if path and path.is_file() else None}
    record = {"schema": "m5_delist_fatal_rejection_v1", "scope": "ENGINEERING_ONLY",
              "status": "REJECTED_LOCAL_BINDING", "issue_codes": [code],
              "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
              "sources": sources, "formal_m5_cohort_selected": False}
    try:
        write_directory(args.output_dir.resolve(),
                        {"m5_delist_fatal_rejection_private.json": record})
    except (AuditError, OSError):
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "verify-output"))
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--sql", type=Path)
    parser.add_argument("--prior-wrds-dir", type=Path)
    parser.add_argument("--prior-issuer-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "run":
            require(all(getattr(args, key) is not None for key in
                        ("csv", "sql", "prior_wrds_dir", "prior_issuer_dir")),
                    "DELIST_INPUT_ARGUMENT_MISSING")
            result = run(args)
            if result["status"] == "REJECTED_CORE_STRUCTURE":
                print("M5_DELIST_INTAKE_REVIEW_REQUIRED")
                return 2
            print("M5_DELIST_LOCAL_CANDIDATES_COMPLETE")
            print("M5_DELIST_SOURCE_UNIQUENESS_AND_FORMAL_COHORT_PENDING")
        else:
            verify_output(args.output_dir.resolve(strict=True))
            print("M5_DELIST_SAVED_OUTPUT_VERIFIED")
        return 0
    except (AuditError, OSError, UnicodeError, ValueError, KeyError, csv.Error) as exc:
        if args.command == "run":
            write_failure(args, str(exc) if isinstance(exc, AuditError) else
                          "LOCAL_IO_OR_FORMAT_ERROR")
        print("M5_DELIST_INTAKE_REVIEW_REQUIRED")
        return 2


if __name__ == "__main__":
    sys.exit(main())
