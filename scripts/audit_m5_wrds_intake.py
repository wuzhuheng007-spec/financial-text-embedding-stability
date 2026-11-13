"""Bind one local WRDS query/export/receipt set and inventory historical links.

All input rows and per-security output stay on the local machine in an ignored
directory. This is a structural engineering audit, not M5 sample admission.
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
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path


QUERY_ID = "11894962"
QUERY_SHA256 = "f2a61ded20689753f221f1a53ae3d2357f5e8a398b2bf471ae9a7bcea945aec9"
PAGE_ROW_COUNT = 7639
PAGE_RESULT_SIZE = "1.8 MiB"  # Rounded display, not an exact byte count.
AS_OF = date(2020, 1, 1)
EXPECTED_COLUMNS = (
    "permno", "secinfostartdt", "secinfoenddt", "securitybegdt", "securityenddt",
    "securityhdrflg", "hdrcusip", "hdrcusip9", "cusip", "cusip9",
    "primaryexch", "conditionaltype", "exchangetier", "tradingstatusflg",
    "securitynm", "shareclass", "usincflg", "issuertype", "securitytype",
    "securitysubtype", "sharetype", "securityactiveflg", "delactiontype",
    "delstatustype", "delreasontype", "delpaymenttype", "ticker",
    "tradingsymbol", "permco", "siccd", "naics", "icbindustry",
    "uesindustry", "nasdcompno", "nasdissuno", "issuernm",
)
LINK_COLUMNS = frozenset({"cik", "cusip", "cusip_full", "cikdate1", "cikdate2", "validated"})
TOOL_HASHES = {
    "check_m5_security_asof_20200101.py": "d0eee18c01533f8ae97ae19d84430bae82bcc907f83f0d929748a27381937f3c",
    "check_m5_cik_candidates.py": "edfac1b207ac648fc5dc196f252ab45a533c7ab52a0113e2bf186e2a89979dcd",
    "check_m5_three_file_history.py": "7d8ae195475d22abf19f692e98bf6a96b30a70f5b4ff05672ba5f3526f324612",
    "validated_dictionary.json": "74740f29e2c2c31baecbb0d746583fd8a7eeac9772a263ca9496d545449122cb",
}
VALIDATED_CLASSES = {
    "0": "EIGHT_DIGIT_NOT_FOUND",
    "1": "EIGHT_DIGIT_FOUND_NAME_MISMATCH",
    "2": "EIGHT_DIGIT_FOUND_NAME_MATCH",
    "3": "NINE_DIGIT_FOUND_NAME_MATCH",
}
IDENTIFIER = re.compile(r"[0-9]+(?:\.0+)?\Z")
CUSIP8 = re.compile(r"[0-9A-Z*@#]{8}\Z")
CUSIP9 = re.compile(r"[0-9A-Z*@#]{9}\Z")


class AuditError(ValueError):
    """A fixed diagnostic code; never include values from licensed rows."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise AuditError(code)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_query(data: bytes, expected_hash: str = QUERY_SHA256) -> tuple[str, ...]:
    require(hashlib.sha256(data).hexdigest() == expected_hash, "SQL_HASH_MISMATCH")
    try:
        sql = data.decode("utf-8-sig")
    except UnicodeError as exc:
        raise AuditError("SQL_ENCODING_INVALID") from exc
    match = re.fullmatch(
        r"\s*SELECT\s+(?P<columns>.*?)\s+FROM\s+(?P<table>[a-z0-9_.]+)\s+WHERE\s+(?P<where>.*?)\s*;?\s*",
        sql, flags=re.IGNORECASE | re.DOTALL,
    )
    require(match is not None, "SQL_SHAPE_MISMATCH")
    require(match.group("table").lower() == "crsp_q_stock.stksecurityinfohist", "SQL_TABLE_MISMATCH")
    expressions = [item.strip().lower() for item in match.group("columns").split(",")]
    prefix = "crsp_q_stock.stksecurityinfohist."
    require(len(expressions) == len(EXPECTED_COLUMNS), "SQL_COLUMN_COUNT_MISMATCH")
    require(all(item.startswith(prefix) and re.fullmatch(r"[a-z0-9_]+", item[len(prefix):])
                for item in expressions), "SQL_COLUMN_EXPRESSION_INVALID")
    columns = tuple(item[len(prefix):] for item in expressions)
    require(columns == EXPECTED_COLUMNS, "SQL_COLUMNS_MISMATCH")
    where = re.sub(r"\s+", " ", match.group("where").strip()).lower()
    expected_where = (
        "secinfostartdt between '1925-12-31'::date and '2020-01-01'::date "
        'and ("crsp_q_stock"."stksecurityinfohist"."secinfoenddt" >= \'2020-01-01\')'
    )
    require(where == expected_where, "SQL_FILTER_MISMATCH")
    return columns


def read_csv(path: Path, required: frozenset[str] = frozenset()):
    """Yield header, then numbered stripped rows; callers consume the iterator once."""
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream, strict=True)
        raw_header = next(reader, None)
        require(bool(raw_header), "CSV_HEADER_MISSING")
        header = tuple(value.strip().lower() for value in raw_header)
        require(all(header) and len(header) == len(set(header)), "CSV_HEADER_INVALID")
        require(required.issubset(header), "CSV_REQUIRED_COLUMNS_MISSING")
        yield header, None, None
        row_number = 0
        for cells in reader:
            if not cells:
                continue
            require(len(cells) == len(header), "CSV_ROW_WIDTH_MISMATCH")
            row_number += 1
            yield header, row_number, dict(zip(header, (cell.strip() for cell in cells)))


def parse_date(value: str, code: str) -> date:
    require(re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is not None, code)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise AuditError(code) from exc


def normalize_id(value: str, max_digits: int | None = None) -> str | None:
    if IDENTIFIER.fullmatch(value) is None:
        return None
    result = value.partition(".")[0].lstrip("0")
    if not result or max_digits is not None and len(result) > max_digits:
        return None
    return result


def normalize_cusip(value: str, length: int) -> str | None:
    value = value.upper()
    pattern = CUSIP8 if length == 8 else CUSIP9
    return value if pattern.fullmatch(value) else None


def quality_code(value: str) -> str | None:
    if IDENTIFIER.fullmatch(value) is None:
        return None
    return value.partition(".")[0].lstrip("0") or "0"


def row_digest(row: dict[str, str]) -> str:
    canonical = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def inspect_snapshot(path: Path, columns: tuple[str, ...], expected_rows: int):
    source_hash = sha256_file(path)
    seen: set[str] = set()
    rows: dict[int, tuple[str, dict[str, str]]] = {}
    header = None
    for current_header, number, row in read_csv(path):
        if number is None:
            header = current_header
            require(header == columns, "SNAPSHOT_HEADER_SQL_MISMATCH")
            continue
        permno = normalize_id(row["permno"])
        require(permno is not None, "SNAPSHOT_PERMNO_INVALID")
        require(permno not in seen, "SNAPSHOT_PERMNO_DUPLICATE")
        seen.add(permno)
        start = parse_date(row["secinfostartdt"], "SNAPSHOT_DATE_INVALID")
        end = parse_date(row["secinfoenddt"], "SNAPSHOT_DATE_INVALID")
        require(start <= AS_OF <= end, "SNAPSHOT_ASOF_COVERAGE_INVALID")
        rows[number] = (permno, row)
    require(len(rows) == expected_rows, "SNAPSHOT_PAGE_ROW_COUNT_MISMATCH")
    require(len(seen) == len(rows), "SNAPSHOT_UNIQUE_PERMNO_MISMATCH")
    require(sha256_file(path) == source_hash, "SNAPSHOT_CHANGED_DURING_READ")
    return {"sha256": source_hash, "bytes": path.stat().st_size, "row_count": len(rows),
            "unique_permno_count": len(seen), "column_count": len(header or ()),
            "column_names": list(header or ())}, rows


def inspect_links(path: Path):
    source_hash = sha256_file(path)
    by_cusip: dict[str, list[dict]] = defaultdict(list)
    issues: list[dict] = []
    count = 0
    seen_rows: set[tuple[str, ...]] = set()
    quality_counts: Counter[str] = Counter()
    for _, number, row in read_csv(path, LINK_COLUMNS):
        if number is None:
            continue
        count += 1
        codes: list[str] = []
        cik = normalize_id(row["cik"], 10)
        if cik is None:
            codes.append("CIK_UNUSABLE")
        else:
            cik = cik.zfill(10)
        cusip = normalize_cusip(row["cusip"], 8)
        if cusip is None:
            codes.append("LINK_CUSIP8_UNUSABLE")
        full = row["cusip_full"]
        if full and (normalize_cusip(full, 9) is None or full.upper()[:8] != cusip):
            codes.append("LINK_CUSIP9_CONFLICT_OR_UNUSABLE")
        dates = []
        for key in ("cikdate1", "cikdate2"):
            try:
                dates.append(parse_date(row[key], "LINK_OBSERVATION_DATE_INVALID"))
            except AuditError:
                codes.append(key.upper() + "_UNUSABLE")
                dates.append(None)
        if dates[0] is not None and dates[1] is not None and dates[0] > dates[1]:
            codes.append("LINK_OBSERVATION_DATES_REVERSED")
        validated = quality_code(row["validated"])
        quality_counts[validated or "UNKNOWN_FORMAT"] += 1
        if validated not in VALIDATED_CLASSES:
            codes.append("VALIDATED_CODE_UNRECOGNIZED")
        identity = tuple(row[key] for key in
                         ("cik", "cusip", "cusip_full", "cikdate1", "cikdate2", "validated"))
        if identity in seen_rows:
            codes.append("DUPLICATE_EXACT_LINK_ROW")
        seen_rows.add(identity)
        if codes:
            issues.append({"link_source_record": number, "codes": codes})
        candidate = {"link_source_record": number, "cik": cik, "historical_cusip": cusip,
                     "validation_code": validated, "validation_class": VALIDATED_CLASSES.get(validated),
                     "first_filing_observation": dates[0].isoformat() if dates[0] else None,
                     "last_filing_observation": dates[1].isoformat() if dates[1] else None,
                     "issues": codes, "historical_link_validity": "NOT_ESTABLISHED",
                     "accepted": False}
        if cusip is not None:
            by_cusip[cusip].append(candidate)
    require(count > 0, "LINK_FILE_EMPTY")
    require(sha256_file(path) == source_hash, "LINKS_CHANGED_DURING_READ")
    return {"sha256": source_hash, "row_count": count,
            "validation_code_counts": dict(sorted(quality_counts.items()))}, by_cusip, issues


def inspect_changes(path: Path, snapshot_header: tuple[str, ...], starting_permnos: set[str]):
    source_hash = sha256_file(path)
    matched: dict[int, tuple[str, dict[str, str]]] = {}
    later_only: list[dict] = []
    count = 0
    for header, number, row in read_csv(path):
        if number is None:
            require(set(header) == set(snapshot_header), "CHANGES_HEADER_MISMATCH")
            continue
        count += 1
        permno = normalize_id(row["permno"])
        require(permno is not None, "CHANGES_PERMNO_INVALID")
        start = parse_date(row["secinfostartdt"], "CHANGES_DATE_INVALID")
        end = parse_date(row["secinfoenddt"], "CHANGES_DATE_INVALID")
        require(start <= end, "CHANGES_INTERVAL_REVERSED")
        require(date(2020, 1, 2) <= start <= date(2025, 12, 31), "CHANGES_START_OUTSIDE_QUERY")
        if permno in starting_permnos:
            matched[number] = (permno, row)
        else:
            cusip = normalize_cusip(row["cusip"], 8)
            later_only.append({"source_record": number, "permno": permno,
                               "start": start.isoformat(), "end": end.isoformat(),
                               "historical_cusip": cusip,
                               "normalized_row_sha256": row_digest(row),
                               "issues": [] if cusip else ["HISTORICAL_CUSIP_UNUSABLE"],
                               "not_in_starting_snapshot": True, "admitted": False})
    require(count > 0, "CHANGES_FILE_EMPTY")
    require(sha256_file(path) == source_hash, "CHANGES_CHANGED_DURING_READ")
    return {"sha256": source_hash, "row_count": count,
            "starting_member_rows": len(matched), "later_only_rows": len(later_only)}, matched, later_only


def verify_tool_and_receipt(receipt: dict, paths: dict[str, Path], hashes: dict[str, str],
                            tool_dir: Path, snapshot: dict, changes: dict, links: dict,
                            tool_hashes: dict[str, str]):
    require(receipt.get("schema_version") == 2 and
            receipt.get("status") == "THREE_FILE_HISTORY_AUDIT_COMPLETE_REVIEW_PENDING" and
            receipt.get("formal_cohort_selected") is False,
            "V2_RECEIPT_STATUS_MISMATCH")
    source = receipt.get("security_source", {})
    bindings = (
        (source.get("source_path"), paths["snapshot"], source.get("source_sha256"), hashes["snapshot"]),
        (receipt.get("link_source_path"), paths["links"], receipt.get("link_source_sha256"), hashes["links"]),
        (receipt.get("changes_source_path"), paths["changes"], receipt.get("changes_source_sha256"), hashes["changes"]),
    )
    for stored_path, current_path, stored_hash, current_hash in bindings:
        require(isinstance(stored_path, str) and
                os.path.normcase(str(Path(stored_path).resolve())) == os.path.normcase(str(current_path)),
                "V2_RECEIPT_INPUT_PATH_MISMATCH")
        require(stored_hash == current_hash, "V2_RECEIPT_INPUT_HASH_MISMATCH")
    for name, expected in tool_hashes.items():
        require(sha256_file(tool_dir / name) == expected, "V2_TOOL_HASH_MISMATCH")
    require(source.get("checker_sha256") == tool_hashes["check_m5_security_asof_20200101.py"] and
            receipt.get("audit_script_sha256") == tool_hashes["check_m5_cik_candidates.py"] and
            receipt.get("history_audit_script_sha256") == tool_hashes["check_m5_three_file_history.py"] and
            receipt.get("validated_dictionary_sha256") == tool_hashes["validated_dictionary.json"],
            "V2_RECEIPT_CODE_HASH_MISMATCH")
    require(source.get("row_count") == snapshot["row_count"] and
            source.get("unique_permno_count") == snapshot["unique_permno_count"] and
            source.get("column_count") == snapshot["column_count"] and
            source.get("column_names") == snapshot["column_names"],
            "V2_RECEIPT_SNAPSHOT_SUMMARY_MISMATCH")
    summary = receipt.get("summary", {})
    require(summary.get("security_records") == snapshot["row_count"] and
            summary.get("link_records") == links["row_count"] and
            summary.get("change_source_records") == changes["row_count"] and
            summary.get("changes_for_starting_securities") == changes["starting_member_rows"] and
            summary.get("changes_for_other_securities_kept_in_raw_source") == changes["later_only_rows"] and
            summary.get("accepted_cik_links") == 0,
            "V2_RECEIPT_COUNTS_MISMATCH")


def build_index(receipt: dict, snapshot_rows: dict, change_rows: dict,
                later_only: list[dict], by_cusip: dict, link_issues: list[dict]):
    securities = receipt.get("securities")
    require(isinstance(securities, list) and len(securities) == len(snapshot_rows),
            "V2_RECEIPT_SECURITY_LIST_MISMATCH")
    indexed: list[dict] = []
    exceptions: list[dict] = list(link_issues)
    history_keys: set[tuple[str, int]] = set()
    seen_permnos: set[str] = set()
    interval_count = 0
    unmatched = 0
    multi_cik = 0
    invalid_cusip = 0
    candidate_count = 0
    candidate_quality_counts: Counter[str] = Counter()
    interval_issue_counts: Counter[str] = Counter()
    for security in securities:
        permno = security.get("permno")
        require(isinstance(permno, str) and permno not in seen_permnos,
                "V2_RECEIPT_SECURITY_ID_MISMATCH")
        seen_permnos.add(permno)
        intervals = security.get("history")
        require(isinstance(intervals, list) and intervals, "V2_RECEIPT_HISTORY_MISSING")
        output_intervals = []
        cik_cusips: dict[str, set[str]] = defaultdict(set)
        for entry in intervals:
            source, record = entry.get("source"), entry.get("source_record")
            require(source in {"STARTING_SNAPSHOT", "LATER_CHANGES"} and
                    isinstance(record, int) and (source, record) not in history_keys,
                    "V2_RECEIPT_HISTORY_ROW_INVALID")
            history_keys.add((source, record))
            raw = snapshot_rows.get(record) if source == "STARTING_SNAPSHOT" else change_rows.get(record)
            require(raw is not None and raw[0] == permno and
                    entry.get("normalized_row_sha256") == row_digest(raw[1]),
                    "V2_RECEIPT_HISTORY_ROW_MISMATCH")
            require(entry.get("start") == raw[1]["secinfostartdt"] and
                    entry.get("end") == raw[1]["secinfoenddt"],
                    "V2_RECEIPT_HISTORY_DATES_MISMATCH")
            cusip = normalize_cusip(raw[1]["cusip"], 8)
            require(entry.get("historical_cusip") == cusip,
                    "V2_RECEIPT_HISTORY_CUSIP_MISMATCH")
            candidate_rows = [dict(candidate) for candidate in by_cusip.get(cusip, ())] if cusip else []
            distinct_ciks = {item["cik"] for item in candidate_rows if item["cik"] is not None}
            for candidate in candidate_rows:
                candidate_quality_counts[candidate["validation_code"] or "UNKNOWN_FORMAT"] += 1
                if candidate["cik"] is not None and cusip is not None:
                    cik_cusips[candidate["cik"]].add(cusip)
            codes = []
            if cusip is None:
                codes.append("HISTORICAL_CUSIP_UNUSABLE")
                invalid_cusip += 1
            if not candidate_rows:
                codes.append("NO_EXACT_CUSIP8_LINK_ROW")
                unmatched += 1
            if len(distinct_ciks) > 1:
                codes.append("MULTIPLE_CIK_CANDIDATES")
                multi_cik += 1
            if any(candidate["validation_code"] in {"0", "1"} for candidate in candidate_rows):
                codes.append("LOW_QUALITY_LINK_ROW_REVIEW")
            if any("DUPLICATE_EXACT_LINK_ROW" in candidate["issues"] for candidate in candidate_rows):
                codes.append("DUPLICATE_EXACT_LINK_ROW_REVIEW")
            if codes:
                exceptions.append({"permno": permno, "source": source,
                                   "source_record": record, "codes": codes})
            candidate_count += len(candidate_rows)
            interval_count += 1
            output_intervals.append({"source": source, "source_record": record,
                                     "start": entry["start"], "end": entry["end"],
                                     "historical_cusip": cusip,
                                     "normalized_row_sha256": entry["normalized_row_sha256"],
                                     "candidates": candidate_rows, "distinct_cik_count": len(distinct_ciks),
                                     "issues": codes, "accepted": False})
        for issue in security.get("history_interval_issues", ()):
            interval_issue_counts[issue["code"]] += 1
            exceptions.append({"permno": permno, "codes": [issue["code"]],
                               "source": issue["source"], "source_record": issue["source_record"]})
        for cik, cusips in cik_cusips.items():
            if len(cusips) > 1:
                exceptions.append({"permno": permno, "cik": cik,
                                   "codes": ["CIK_CANDIDATE_ACROSS_MULTIPLE_HISTORICAL_CUSIPS"]})
        indexed.append({"permno": permno, "history": output_intervals,
                        "historical_cusip_changed": security.get("historical_cusip_changed"),
                        "history_ends_before_2025_12_31": security.get("history_ends_before_2025_12_31"),
                        "history_interval_issues": security.get("history_interval_issues", []),
                        "accepted": False})
    expected_keys = {("STARTING_SNAPSHOT", number) for number in snapshot_rows}
    expected_keys |= {("LATER_CHANGES", number) for number in change_rows}
    require(history_keys == expected_keys, "V2_RECEIPT_HISTORY_COVERAGE_MISMATCH")
    require({item["permno"] for item in indexed} == {value[0] for value in snapshot_rows.values()},
            "V2_RECEIPT_SECURITY_COVERAGE_MISMATCH")
    require(receipt["summary"].get("starting_securities_with_historical_cusip_changes") ==
            sum(bool(item["historical_cusip_changed"]) for item in indexed),
            "V2_RECEIPT_CUSIP_CHANGE_COUNT_MISMATCH")
    for item in later_only:
        if item["issues"]:
            exceptions.append({"permno": item["permno"], "source": "LATER_ONLY",
                               "source_record": item["source_record"], "codes": item["issues"]})
    return {
        "schema": "m5_wrds_history_candidate_index_v1",
        "scope": "ENGINEERING_CANDIDATE_INVENTORY_ONLY",
        "accepted": False,
        "historical_link_validity": "NOT_ESTABLISHED",
        "join_rule": "Exact eight-character historical CUSIP on each retained interval; no date-validity inference",
        "starting_securities": indexed,
        "later_only_security_intervals": later_only,
        "exceptions": exceptions,
        "summary": {"starting_security_count": len(indexed), "history_interval_count": interval_count,
                    "starting_securities_with_cusip_changes": sum(
                        bool(item["historical_cusip_changed"]) for item in indexed),
                    "starting_securities_with_history_ending_early": sum(
                        bool(item["history_ends_before_2025_12_31"]) for item in indexed),
                    "history_interval_issue_counts": dict(sorted(interval_issue_counts.items())),
                    "intervals_without_exact_link_row": unmatched,
                    "intervals_with_multiple_cik_candidates": multi_cik,
                    "intervals_with_unusable_cusip": invalid_cusip,
                    "interval_candidate_rows": candidate_count,
                    "interval_candidate_validation_code_counts": dict(sorted(candidate_quality_counts.items())),
                    "later_only_interval_count": len(later_only),
                    "later_only_security_count": len({item["permno"] for item in later_only}),
                    "exception_record_count": len(exceptions)},
    }


def check_ignored_output(output_dir: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    require(output_dir.is_relative_to(root / "artifacts") and output_dir != root / "artifacts",
            "OUTPUT_NOT_UNDER_LOCAL_ARTIFACTS")
    relative = output_dir.relative_to(root)
    check = subprocess.run(["git", "check-ignore", "-q", "--", str(relative)],
                           cwd=root, capture_output=True, check=False)
    require(check.returncode == 0, "OUTPUT_NOT_GIT_IGNORED")
    require(not output_dir.exists(), "OUTPUT_ALREADY_EXISTS")


def write_directory(output_dir: Path, files: dict[str, dict]) -> None:
    """Publish a new ignored directory only after all files are complete."""
    check_ignored_output(output_dir)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="m5-intake-stage-", dir=output_dir.parent) as stage_name:
        stage = Path(stage_name)
        for filename, data in files.items():
            with (stage / filename).open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
        require(not output_dir.exists(), "OUTPUT_ALREADY_EXISTS")
        stage.rename(output_dir)


def run(args, *, expected_sql_hash: str = QUERY_SHA256,
        expected_rows: int = PAGE_ROW_COUNT,
        tool_hashes: dict[str, str] | None = None):
    """Run entirely offline; tests may inject synthetic identities."""
    tool_hashes = TOOL_HASHES if tool_hashes is None else tool_hashes
    paths = {key: Path(getattr(args, key)).resolve(strict=True)
             for key in ("snapshot", "links", "changes", "sql", "receipt")}
    require(len(set(paths.values())) == len(paths), "INPUT_PATHS_NOT_DISTINCT")
    tool_dir = Path(args.tool_dir).resolve(strict=True)
    output_dir = Path(args.output_dir).resolve()
    check_ignored_output(output_dir)
    sql_data = paths["sql"].read_bytes()
    columns = parse_query(sql_data, expected_hash=expected_sql_hash)
    sql_hash = hashlib.sha256(sql_data).hexdigest()
    snapshot, snapshot_rows = inspect_snapshot(paths["snapshot"], columns, expected_rows)
    links, by_cusip, link_issues = inspect_links(paths["links"])
    changes, change_rows, later_only = inspect_changes(
        paths["changes"], columns, {item[0] for item in snapshot_rows.values()})
    receipt_hash = sha256_file(paths["receipt"])
    try:
        receipt = json.loads(paths["receipt"].read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise AuditError("V2_RECEIPT_UNREADABLE") from exc
    require(isinstance(receipt, dict), "V2_RECEIPT_INVALID")
    verify_tool_and_receipt(receipt, paths, {"snapshot": snapshot["sha256"],
                            "links": links["sha256"], "changes": changes["sha256"]},
                            tool_dir, snapshot, changes, links, tool_hashes)
    index = build_index(receipt, snapshot_rows, change_rows, later_only, by_cusip, link_issues)
    require(sha256_file(paths["receipt"]) == receipt_hash, "V2_RECEIPT_CHANGED_DURING_READ")
    require(sha256_file(paths["sql"]) == sql_hash, "SQL_CHANGED_DURING_AUDIT")
    for key, record in (("snapshot", snapshot), ("links", links), ("changes", changes)):
        require(sha256_file(paths[key]) == record["sha256"], "INPUT_CHANGED_DURING_AUDIT")
    recorded_at = datetime.now(timezone.utc).isoformat()
    script_hash = sha256_file(Path(__file__).resolve())
    registry = {
        "schema": "m5_wrds_local_intake_registry_v1", "scope": "ENGINEERING_ONLY",
        "recorded_at_utc": recorded_at, "query_id": QUERY_ID,
        "query_spec_status": "QUERY_SPEC_VERIFIED", "query_sql_path": str(paths["sql"]),
        "query_sql_sha256": sql_hash, "query_table": "crsp_q_stock.stksecurityinfohist",
        "page_display": {"row_count": PAGE_ROW_COUNT, "result_size": PAGE_RESULT_SIZE,
                         "source": "USER_PROVIDED_WRDS_SCREENSHOT", "page_time_zone": None},
        "local_snapshot": {"path": str(paths["snapshot"]), **snapshot},
        "local_links": {"path": str(paths["links"]), **links},
        "local_changes": {"path": str(paths["changes"]), **changes},
        "saved_v2_receipt": {"path": str(paths["receipt"]), "sha256": receipt_hash},
        "tool_dir": str(tool_dir), "tool_sha256": dict(tool_hashes),
        "intake_script_sha256": script_hash,
        "local_binding_status": "LOCAL_FILE_MATCHES_RECEIPT_AND_QUERY_SHAPE",
        "provider_universe_completeness": "NOT_VERIFIED",
        "provider_release_vintage": None, "license_or_export_terms_verified": False,
        "historical_cik_link_validity": "NOT_ESTABLISHED",
        "formal_m5_cohort_selected": False, "m5_protocol_status": "PROPOSED",
        "financial_training_run": False, "prediction_evaluation_run": False,
        "backtest_run": False,
        "evidence_limit": "Hashes and local shape do not authenticate provider completeness or historical link validity",
    }
    safe_summary = {
        "schema": "m5_wrds_intake_aggregate_summary_v1", "scope": "ENGINEERING_ONLY",
        "query_id": QUERY_ID, "query_sql_sha256": sql_hash,
        "snapshot_sha256": snapshot["sha256"], "links_sha256": links["sha256"],
        "changes_sha256": changes["sha256"], "v2_receipt_sha256": receipt_hash,
        "intake_script_sha256": script_hash, "tool_sha256": dict(tool_hashes),
        "page_row_count": PAGE_ROW_COUNT, "page_result_size_display": PAGE_RESULT_SIZE,
        "local_snapshot_rows": snapshot["row_count"],
        "local_snapshot_columns": snapshot["column_count"],
        "local_unique_permnos": snapshot["unique_permno_count"],
        "link_rows": links["row_count"], "change_rows": changes["row_count"],
        "link_validation_code_counts": links["validation_code_counts"],
        "link_issue_record_count": len(link_issues),
        **index["summary"],
        "query_spec": "QUERY_SPEC_VERIFIED",
        "local_binding": "LOCAL_FILE_MATCHES_RECEIPT_AND_QUERY_SHAPE",
        "provider_universe_completeness": "NOT_VERIFIED",
        "historical_cik_link_validity": "NOT_ESTABLISHED", "formal_m5_cohort_selected": False,
        "model_or_financial_experiment_run": False,
    }
    index["source_bindings"] = {"snapshot_sha256": snapshot["sha256"],
                                 "links_sha256": links["sha256"],
                                 "changes_sha256": changes["sha256"],
                                 "v2_receipt_sha256": receipt_hash,
                                 "intake_script_sha256": script_hash}
    write_directory(output_dir, {
        "m5_wrds_intake_registry.json": registry,
        "m5_wrds_history_candidates_private.json": index,
        "m5_wrds_intake_aggregate_summary.json": safe_summary,
    })
    return safe_summary


def write_rejection(output_dir: Path, code: str, args) -> None:
    """Keep a private fixed-code diagnostic without publishing partial success."""
    record = {
        "schema": "m5_wrds_intake_rejection_v1",
        "status": "REJECTED_LOCAL_BINDING",
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "issue_codes": [code],
        "input_paths": {key: str(getattr(args, key)) for key in
                        ("snapshot", "links", "changes", "sql", "receipt", "tool_dir")},
        "formal_m5_cohort_selected": False,
    }
    try:
        write_directory(output_dir, {"m5_wrds_intake_exceptions_private.json": record})
    except (AuditError, OSError):
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("snapshot", "links", "changes", "sql", "receipt", "tool-dir", "output-dir"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    try:
        run(args)
    except AuditError as exc:
        write_rejection(Path(args.output_dir).resolve(), str(exc), args)
        print("M5_WRDS_INTAKE_REVIEW_REQUIRED", str(exc))
        return 2
    except (OSError, UnicodeError, csv.Error, json.JSONDecodeError):
        write_rejection(Path(args.output_dir).resolve(), "LOCAL_IO_OR_FORMAT_ERROR", args)
        print("M5_WRDS_INTAKE_REVIEW_REQUIRED", "LOCAL_IO_OR_FORMAT_ERROR")
        return 2
    print("M5_WRDS_LOCAL_INTAKE_AND_HISTORY_CANDIDATES_COMPLETE")
    print("M5_PROVIDER_UNIVERSE_AND_HISTORICAL_LINK_VALIDITY_PENDING")
    return 0


if __name__ == "__main__":
    sys.exit(main())
