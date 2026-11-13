"""Offline, source-bound CRSP distribution intake for the first M5 buffer segment.

The saved output is engineering evidence only. No WRDS request, return label,
financial experiment, or model inference is performed. Terminal output uses
fixed status codes; identifiers and event records stay in ignored artifacts.
"""

from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts"
BOUND_TXT = ART / "m5_daily_download_prepare_20261111_v1" / "m5_daily_seed_permnos_upload.txt"
DAILY_DIR = ART / "m5_daily_first_segment_20191101_20191231_v1"
PACKAGED_ZIP = "10_current_distribution/raw/asu1cjbn71x8ajp0.csv.zip"
PACKAGED_TXT = "10_current_distribution/raw/m5_daily_seed_permnos_upload (7).txt"
PACKAGE_MANIFEST = "05_PACKAGE_MANIFEST.json"
FIELD_REVIEW = "06_FIELDS_AND_FILTER_REVIEW_SAFE.json"
CSV_MEMBER = "asu1cjbn71x8ajp0.csv"
WINDOW = (date(2019, 11, 1), date(2019, 12, 31))
QUERY_ID = "11900944"
PAGE_ROWS = 5670
SEED_COUNT = 7639
BOUND_SHA = "017224e43461409fa6f2ef007d182a5b0d870f02cd1edc4c528f0a901a543318"
HANDOFF_SHA = "8d905753e704baf3408be9254317546e18d610dfbb4fafc7f1098840b0442ead"
DAILY_SUMMARY_SHA = "561e98eae9b33b71650e6aa62501532b03d4753d29736bef38aeeb440cb1d9ec"
DAILY_MANIFEST_SHA = "f5354c20e75c643472ea735c74be87fe278ac5850bc003d2674277fc9e6601e4"
DAILY_EXCEPTIONS_SHA = "d6822b09372a1d1d9b3f3c6d47b5e3a5cb3f2b84df9ebf53e2ac1a7ebc4dc71a"
MAX_MEMBER_BYTES = 100_000_000
ID_PATTERN = re.compile(r"[1-9][0-9]*\Z", re.ASCII)
SEQ_PATTERN = re.compile(r"[0-9]+\Z", re.ASCII)
DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z", re.ASCII)
HEADER_FIELDS = {"siccd", "primaryexch", "nasdissuno"}
OTHER_DATES = ("disdeclaredt", "disrecorddt", "dispaydt")
SELECTED_TYPES = {"CD", "CG", "CP", "FRS", "IN", "N/A", "ROC", "SD", "SP", "TSOO"}
SUMMARY = "summary_safe.json"
INDEX = "event_index_private.jsonl"
COMPARISON = "daily_duplicate_comparison_private.jsonl"
REGISTRY = "source_registry_private.json"
MANIFEST = "output_manifest_private.json"
EXECUTION = "execution_private.json"


class IntakeError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def require(condition: bool, code: str) -> None:
    if not condition:
        raise IntakeError(code)


def sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def object_sha(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                           .encode("utf-8")).hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def write_json_new(path: Path, value: object) -> None:
    with path.open("xb") as out:
        out.write(json_bytes(value))


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), "JSON_OBJECT_REQUIRED")
    return value


def ignored_output(path: Path) -> None:
    require(path.is_absolute(), "OUTPUT_MUST_BE_ABSOLUTE")
    real = path.resolve()
    require(real != ART and ART.resolve() in real.parents, "OUTPUT_OUTSIDE_ARTIFACTS")
    result = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", str(real)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    require(result.returncode == 0, "OUTPUT_NOT_GIT_IGNORED")


def safe_member_name(name: str) -> bool:
    posix = PurePosixPath(name)
    return (name and not name.endswith("/") and not posix.is_absolute()
            and all(part not in ("", ".", "..") for part in posix.parts)
            and ":" not in posix.parts[0] and "\\" not in name)


def checked_zip(path: Path, *, expected_names: set[str] | None = None) -> zipfile.ZipFile:
    archive = zipfile.ZipFile(path)
    members = archive.infolist()
    names = [member.filename for member in members]
    require(len(names) == len(set(names)) and all(safe_member_name(name) for name in names),
            "ZIP_MEMBER_PATH_INVALID")
    require(all(not member.flag_bits & 1 and member.file_size <= MAX_MEMBER_BYTES
                for member in members), "ZIP_MEMBER_ENCRYPTED_OR_TOO_LARGE")
    if expected_names is not None:
        require(set(names) == expected_names, "ZIP_MEMBER_SET_MISMATCH")
    require(archive.testzip() is None, "ZIP_CRC_FAILED")
    return archive


def verify_package_transport(handoff: Path) -> tuple[str, dict]:
    before = sha(handoff)
    with checked_zip(handoff) as archive:
        manifest = json.loads(archive.read(PACKAGE_MANIFEST))
        files = manifest.get("files")
        require(manifest.get("schema") == "transport_manifest_v1" and
                isinstance(files, list) and manifest.get("listed_member_count") == len(files),
                "PACKAGE_MANIFEST_INVALID")
        listed = {entry.get("path") for entry in files}
        require(len(listed) == len(files) and
                listed == set(archive.namelist()) - {PACKAGE_MANIFEST},
                "PACKAGE_MANIFEST_MEMBER_SET_MISMATCH")
        for entry in files:
            name = entry["path"]
            data = archive.read(name)
            require(len(data) == entry.get("bytes") and
                    hashlib.sha256(data).hexdigest() == entry.get("sha256_transport_only"),
                    "PACKAGE_MEMBER_HASH_MISMATCH")
    require(sha(handoff) == before, "PACKAGE_CHANGED_DURING_READ")
    return before, manifest


def verify_package_and_extract(handoff: Path, output: Path) -> dict:
    before, manifest = verify_package_transport(handoff)
    files = manifest["files"]
    with checked_zip(handoff) as archive:
        for info in archive.infolist():
            target = output / "package" / PurePosixPath(info.filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("xb") as sink:
                shutil.copyfileobj(source, sink)
    require(sha(handoff) == before, "PACKAGE_CHANGED_DURING_READ")
    raw_zip = output / "package" / PACKAGED_ZIP
    with checked_zip(raw_zip, expected_names={CSV_MEMBER}) as inner:
        csv_path = output / "raw" / CSV_MEMBER
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with inner.open(CSV_MEMBER) as source, csv_path.open("xb") as sink:
            shutil.copyfileobj(source, sink)
        require(csv_path.stat().st_size == inner.getinfo(CSV_MEMBER).file_size,
                "EXTRACTED_CSV_SIZE_MISMATCH")
    raw_entry = [x for x in files if x["path"] == PACKAGED_ZIP]
    require(len(raw_entry) == 1 and
            sha(raw_zip) == raw_entry[0]["sha256_transport_only"],
            "EXTRACTED_SOURCE_ZIP_MISMATCH")
    return {"handoff_zip_sha256": before, "raw_zip_sha256": sha(raw_zip),
            "csv_sha256": sha(csv_path), "csv_size_bytes": csv_path.stat().st_size,
            "package_member_count": len(files) + 1}


def upload_format(data: bytes) -> dict:
    require(not data.startswith(b"\xef\xbb\xbf"), "UPLOAD_BOM_INVALID")
    text = data.decode("ascii")
    lines = text.splitlines()
    require(lines and all(ID_PATTERN.fullmatch(line) for line in lines),
            "UPLOAD_IDENTIFIER_INVALID")
    require(len(lines) == len(set(lines)), "UPLOAD_IDENTIFIER_DUPLICATED")
    return {"ids": lines, "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data), "final_newline": data.endswith((b"\n", b"\r")),
            "line_endings": "CRLF" if b"\r\n" in data and
            data.replace(b"\r\n", b"").find(b"\n") < 0 else
            "LF" if b"\r" not in data else "MIXED_OR_CR"}


def verify_upload(output: Path, bound: Path, expected_count: int) -> tuple[set[str], dict]:
    before = sha(bound)
    local = upload_format(bound.read_bytes())
    packaged = upload_format((output / "package" / PACKAGED_TXT).read_bytes())
    require(sha(bound) == before, "BOUND_UPLOAD_CHANGED_DURING_READ")
    require(len(local["ids"]) == expected_count and local["sha256"] == BOUND_SHA
            if expected_count == SEED_COUNT else len(local["ids"]) == expected_count,
            "BOUND_UPLOAD_IDENTITY_MISMATCH")
    require(set(local["ids"]) == set(packaged["ids"]), "PACKAGE_UPLOAD_SET_MISMATCH")
    return set(local["ids"]), {
        "bound_txt_sha256": before, "package_txt_sha256": packaged["sha256"],
        "bound_count": len(local["ids"]), "package_count": len(packaged["ids"]),
        "identifier_set_equal": True, "identifier_order_equal": local["ids"] == packaged["ids"],
        "original_bytes_equal": local["sha256"] == packaged["sha256"],
        "bound_line_endings": local["line_endings"],
        "package_line_endings": packaged["line_endings"],
        "bound_final_newline": local["final_newline"],
        "package_final_newline": packaged["final_newline"],
        "server_input_dat_bound_to_query": False,
    }


class IntervalCapture:
    """Digest physical lines consumed by one csv.reader logical record."""

    def __init__(self, source):
        self.source = source
        self.digest = hashlib.sha256()

    def __iter__(self):
        return self

    def __next__(self):
        line = next(self.source)
        self.digest.update(line.encode("utf-8"))
        return line

    def take(self) -> str:
        value = self.digest.hexdigest()
        self.digest = hashlib.sha256()
        return value


def parse_date(value: str) -> date | None:
    if not DATE_PATTERN.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def analyze_csv(path: Path, allowed: set[str], expected_fields: list[str],
                index_path: Path, *, expected_rows: int = PAGE_ROWS) -> tuple[dict, dict]:
    before = sha(path)
    counts = collections.Counter()
    missing = collections.Counter()
    types = collections.Counter()
    other_date_outside = collections.Counter()
    groups: dict[tuple[str, str, str], list[dict]] = collections.defaultdict(list)
    date_groups: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    dates: set[str] = set()
    observed: set[str] = set()
    structural_codes: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as source, \
            index_path.open("x", encoding="utf-8", newline="\n") as index:
        capture = IntervalCapture(source)
        reader = csv.reader(capture, strict=True)
        header = next(reader, [])
        capture.take()
        normalized = [name.strip().casefold() for name in header]
        if not header or len(normalized) != len(set(normalized)):
            structural_codes.add("HEADER_MISSING_OR_DUPLICATED")
        lookup = {name: pos for pos, name in enumerate(normalized)}
        missing_fields = [field for field in expected_fields if field not in lookup]
        extra_fields = [field for field in normalized if field not in expected_fields]
        if missing_fields:
            structural_codes.add("SELECTED_FIELDS_MISSING")
        for row_number, row in enumerate(reader, start=2):
            interval_sha = capture.take()
            if not row:
                counts["blank_physical_line_records"] += 1
                continue
            counts["rows"] += 1
            record = {"row_number": row_number, "row_sha256": object_sha(row),
                      "parser_consumed_interval_sha256": interval_sha, "codes": []}
            if len(row) != len(header):
                counts["bad_width"] += 1
                record["codes"].append("CSV_ROW_WIDTH_MISMATCH")
                structural_codes.add("CSV_ROW_WIDTH_MISMATCH")
                index.write(json.dumps(record, sort_keys=True) + "\n")
                continue
            for name, value in zip(normalized, row):
                if value.strip() == "":
                    missing[name] += 1
            raw_id = row[lookup["permno"]].strip() if "permno" in lookup else ""
            raw_ex = row[lookup["disexdt"]].strip() if "disexdt" in lookup else ""
            raw_seq = row[lookup["disseqnbr"]].strip() if "disseqnbr" in lookup else ""
            event_type = row[lookup["distype"]].strip() if "distype" in lookup else ""
            if not ID_PATTERN.fullmatch(raw_id):
                record["codes"].append("PERMNO_INVALID")
                structural_codes.add("PERMNO_INVALID")
            elif raw_id not in allowed:
                record["codes"].append("PERMNO_OUTSIDE_BOUND_SET")
                structural_codes.add("PERMNO_OUTSIDE_BOUND_SET")
            else:
                observed.add(raw_id)
            parsed_ex = parse_date(raw_ex)
            if parsed_ex is None:
                record["codes"].append("DISEXDT_INVALID")
                structural_codes.add("DISEXDT_INVALID")
            else:
                dates.add(parsed_ex.isoformat())
                if not WINDOW[0] <= parsed_ex <= WINDOW[1]:
                    record["codes"].append("DISEXDT_OUTSIDE_WINDOW")
                    structural_codes.add("DISEXDT_OUTSIDE_WINDOW")
            seq_normalized = (raw_seq.lstrip("0") or "0") if SEQ_PATTERN.fullmatch(raw_seq) else None
            if seq_normalized is None:
                counts["sequence_uncertain_rows"] += 1
                record["codes"].append("DISSEQNBR_NOT_DECIMAL")
            if event_type:
                types[event_type] += 1
            if event_type not in SELECTED_TYPES:
                counts["type_outside_selected_predicate"] += 1
                record["codes"].append("DISTYPE_OUTSIDE_RECORDED_PREDICATE")
                structural_codes.add("DISTYPE_OUTSIDE_RECORDED_PREDICATE")
            for date_field in OTHER_DATES:
                if date_field not in lookup:
                    continue
                value = row[lookup[date_field]].strip()
                other = parse_date(value)
                if other is not None and not WINDOW[0] <= other <= WINDOW[1]:
                    other_date_outside[date_field] += 1
                elif value and other is None:
                    counts[date_field + "_unparsed"] += 1
            for identity_field in ("dispermno", "dispermco"):
                if identity_field in lookup:
                    value = row[lookup[identity_field]].strip()
                    if value and not ID_PATTERN.fullmatch(value):
                        counts[identity_field + "_non_decimal_or_special"] += 1
            event_cells = [row[lookup[name]] for name in expected_fields
                           if name not in HEADER_FIELDS and name in lookup]
            header_cells = [row[lookup[name]] for name in sorted(HEADER_FIELDS)
                            if name in lookup]
            event_sha, header_sha = object_sha(event_cells), object_sha(header_cells)
            record.update({"permno": raw_id, "disexdt": raw_ex,
                           "disseqnbr_raw": raw_seq, "disseqnbr_normalized": seq_normalized,
                           "distype": event_type, "event_fields_sha256": event_sha,
                           "provisional_header_sha256": header_sha})
            if ID_PATTERN.fullmatch(raw_id) and parsed_ex is not None:
                date_groups[(raw_id, parsed_ex.isoformat())].append(record)
                if seq_normalized is not None:
                    groups[(raw_id, parsed_ex.isoformat(), seq_normalized)].append(record)
            index.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    require(sha(path) == before, "CSV_CHANGED_DURING_READ")
    group_class = collections.Counter()
    duplicate_extra = 0
    parsed_repeat_extra = 0
    consumed_interval_repeat_extra = 0
    for records in groups.values():
        if len(records) <= 1:
            continue
        duplicate_extra += len(records) - 1
        events = {x["event_fields_sha256"] for x in records}
        headers = {x["provisional_header_sha256"] for x in records}
        full = {x["row_sha256"] for x in records}
        intervals = {x["parser_consumed_interval_sha256"] for x in records}
        parsed_repeat_extra += len(records) - len(full)
        consumed_interval_repeat_extra += len(records) - len(intervals)
        if len(full) == 1:
            group_class["parsed_full_row_repeat"] += 1
        elif len(events) == 1 and len(headers) > 1:
            group_class["provisional_header_only_variant"] += 1
        elif len(events) == 1 and len(headers) == 1:
            group_class["automatic_extra_field_only_variant"] += 1
        else:
            group_class["non_header_field_conflict"] += 1
    multi_event = sum(len({x["disseqnbr_normalized"] for x in records
                           if x["disseqnbr_normalized"] is not None}) > 1
                      for records in date_groups.values())
    if counts["rows"] != expected_rows:
        structural_codes.add("PAGE_ROW_COUNT_MISMATCH")
    if not counts["rows"]:
        structural_codes.add("EMPTY_CSV")
    summary = {
        "schema": "m5_distribution_segment_summary_safe_v1",
        "scope": "ENGINEERING_ONLY",
        "source_csv_sha256": before,
        "query_id_from_user_page": QUERY_ID,
        "requested_disexdt_window_inclusive": [x.isoformat() for x in WINDOW],
        "csv_header": header, "csv_column_count": len(header),
        "expected_selected_field_count": len(expected_fields),
        "missing_selected_fields": missing_fields,
        "extra_or_automatic_fields": extra_fields,
        "row_count": counts["rows"], "page_displayed_row_count": expected_rows,
        "page_count_matches_local": counts["rows"] == expected_rows,
        "blank_physical_line_record_count": counts["blank_physical_line_records"],
        "bad_width_row_count": counts["bad_width"],
        "observed_bound_identifier_count": len(observed),
        "unobserved_bound_identifier_count_not_a_failure": len(allowed - observed),
        "actual_disexdt_first": min(dates) if dates else None,
        "actual_disexdt_last": max(dates) if dates else None,
        "actual_disexdt_count": len(dates),
        "blank_cell_counts": dict(sorted(missing.items())),
        "distype_counts_literal": dict(sorted(types.items())),
        "other_date_outside_exdate_window_counts": dict(sorted(other_date_outside.items())),
        "sequence_uncertain_row_count": counts["sequence_uncertain_rows"],
        "dispermno_non_decimal_or_special_row_count": counts["dispermno_non_decimal_or_special"],
        "dispermco_non_decimal_or_special_row_count": counts["dispermco_non_decimal_or_special"],
        "other_date_unparsed_row_counts": {field: counts[field + "_unparsed"]
                                             for field in OTHER_DATES},
        "permno_disexdt_groups_with_multiple_sequence_numbers": multi_event,
        "candidate_three_field_key_duplicate_groups": sum(group_class.values()),
        "candidate_three_field_key_extra_rows": duplicate_extra,
        "parsed_full_row_repeat_extra_rows_within_candidate_keys": parsed_repeat_extra,
        "parser_consumed_interval_hash_repeat_extra_rows_within_candidate_keys":
        consumed_interval_repeat_extra,
        "candidate_duplicate_group_classes": dict(sorted(group_class.items())),
        "candidate_key_certified_provider_primary_key": False,
        "guide_field_roles_known_but_actual_select_mapping_unverified": True,
        "header_context_fields_from_guide": sorted(HEADER_FIELDS),
        "non_header_comparison_field_count": len([name for name in expected_fields
                                                   if name not in HEADER_FIELDS]),
        "non_header_comparison_status": "PROVISIONAL_UNTIL_NATIVE_SELECT_MAPPING_VERIFIED",
        "isolated_raw_record_byte_equality_verified": False,
        "parser_consumed_interval_sha_scope": "May include blank physical lines preceding a logical record",
        "structural_issue_codes": sorted(structural_codes),
        "status": "STRUCTURE_REVIEW_REQUIRED" if structural_codes else "PASS_STRUCTURE_ONLY",
        "event_key_review_status": "REVIEW_REQUIRED" if sum(group_class.values()) or
        counts["sequence_uncertain_rows"] else "CANDIDATE_KEYS_UNIQUE_WITHIN_SAVED_EXTRACT",
        "financial_admission": False,
    }
    return summary, {"date_groups": date_groups, "three_key_groups": groups}


def compare_daily(daily_dir: Path, distribution: dict, path: Path) -> dict:
    summary_path = daily_dir / "m5_daily_segment_summary_safe.json"
    manifest_path = daily_dir / "m5_daily_segment_manifest_private.json"
    exception_path = daily_dir / "m5_daily_segment_exceptions_private.json"
    prior = read_json(summary_path)
    manifest = read_json(manifest_path)
    require(sha(summary_path) == manifest["saved_file_sha256"][summary_path.name]
            and sha(exception_path) == manifest["saved_file_sha256"][exception_path.name],
            "DAILY_SAVED_BINDING_MISMATCH")
    require(prior.get("scope") == "ENGINEERING_ONLY", "DAILY_SCOPE_INVALID")
    rows = read_json(exception_path).get("records")
    require(isinstance(rows, list), "DAILY_EXCEPTION_INDEX_INVALID")
    groups = collections.Counter()
    for record in rows:
        if "PERMNO_DATE_DUPLICATE" in record.get("codes", []):
            groups[(record["permno"], record["dlycaldt"])] += 1
    require(len(groups) == prior.get("duplicate_key_group_count") and
            sum(groups.values()) == prior.get("duplicate_extra_row_count"),
            "DAILY_EXCEPTION_COUNTS_MISMATCH")
    counts = collections.Counter()
    type_group_counts = collections.Counter()
    with path.open("x", encoding="utf-8", newline="\n") as out:
        for (permno, session), extra in sorted(groups.items()):
            daily_multiplicity = extra + 1
            event_rows = distribution["date_groups"].get((permno, session), [])
            distinct_seq = {x["disseqnbr_normalized"] for x in event_rows
                            if x["disseqnbr_normalized"] is not None}
            candidate_triple_duplicate = any(len(rows) > 1
                                    for (identifier, exdate, _), rows in
                                    distribution["three_key_groups"].items()
                                    if identifier == permno and exdate == session)
            uncertain = (any(x["disseqnbr_normalized"] is None for x in event_rows)
                         or candidate_triple_duplicate)
            if not event_rows:
                category = "NO_DISTRIBUTION_ROW"
            elif uncertain:
                category = "MATCHED_KEY_UNCERTAIN"
            else:
                category = "MATCHED_CANDIDATE_EVENT_KEY"
            counts[category] += 1
            if event_rows:
                type_group_counts.update({x["distype"] for x in event_rows})
                counts["raw_distribution_rows_equal_daily_multiplicity"] += (
                    len(event_rows) == daily_multiplicity)
                counts["distinct_sequence_count_equal_daily_multiplicity"] += (
                    len(distinct_seq) == daily_multiplicity)
            out.write(json.dumps({
                "permno": permno, "session": session,
                "daily_extra_rows": extra,
                "daily_group_multiplicity": daily_multiplicity,
                "distribution_raw_row_count": len(event_rows),
                "distribution_distinct_candidate_sequence_count": len(distinct_seq),
                "distribution_types_literal": sorted({x["distype"] for x in event_rows}),
                "candidate_event_key_uncertain": uncertain,
                "association_category": category,
            }, ensure_ascii=False, sort_keys=True) + "\n")
    return {
        "schema": "m5_daily_distribution_limited_comparison_safe_v1",
        "scope": "2019_FIRST_SEGMENT_ONLY_ENGINEERING_ASSOCIATION",
        "daily_summary_sha256": sha(summary_path),
        "daily_manifest_sha256": sha(manifest_path),
        "daily_exception_index_sha256": sha(exception_path),
        "daily_duplicate_group_denominator": len(groups),
        "daily_duplicate_extra_rows": sum(groups.values()),
        "matched_candidate_event_key_groups": counts["MATCHED_CANDIDATE_EVENT_KEY"],
        "matched_uncertain_key_groups": counts["MATCHED_KEY_UNCERTAIN"],
        "unmatched_daily_duplicate_groups": counts["NO_DISTRIBUTION_ROW"],
        "raw_distribution_row_count_equal_daily_multiplicity_groups": counts[
            "raw_distribution_rows_equal_daily_multiplicity"],
        "distinct_candidate_sequence_count_equal_daily_multiplicity_groups": counts[
            "distinct_sequence_count_equal_daily_multiplicity"],
        "matched_distribution_type_group_counts_literal": dict(sorted(type_group_counts.items())),
        "backend_join_cause": "UNKNOWN",
        "not_generalizable_to_other_daily_segments": True,
    }


def derive(output: Path, *, expected_rows: int, expected_ids: int,
           bound_txt: Path, daily_dir: Path) -> tuple[dict, dict, bytes, bytes]:
    fields = read_json(output / "package" / FIELD_REVIEW)
    expected = fields.get("expected_selected_field_names")
    require(isinstance(expected, list) and len(expected) == 22 and
            len(expected) == len(set(expected)), "FIELD_PLAN_INVALID")
    allowed, upload = verify_upload(output, bound_txt, expected_ids)
    with tempfile.TemporaryDirectory(prefix="m5-dist-derive-", dir=output.parent) as temp:
        temp_dir = Path(temp)
        index_path = temp_dir / INDEX
        comparison_path = temp_dir / COMPARISON
        summary, distribution = analyze_csv(output / "raw" / CSV_MEMBER, allowed,
                                            expected, index_path,
                                            expected_rows=expected_rows)
        comparison = compare_daily(daily_dir, distribution, comparison_path)
        index_bytes = index_path.read_bytes()
        comparison_bytes = comparison_path.read_bytes()
    summary["upload_binding"] = upload
    summary["limited_daily_duplicate_comparison"] = comparison
    summary["source_status"] = {
        "query_page": "USER_SCREENSHOT_QUERY_11900944",
        "user_parameter_text": "BOUNDED_NOT_NATIVE_SERVER_JSON",
        "pre_submission_form_images": "EARLIER_STAGE_NOT_FINAL_QUERY_PROOF",
        "explicit_distype_filter": sorted(SELECTED_TYPES),
        "server_input_dat": "NOT_BOUND_TO_THIS_QUERY",
        "download_sql": ("COPIED_NOT_PARSED_BY_AUDITOR" if
                         (output / "package" / "saved_download_query.sql").is_file()
                         else "NOT_LOCATED"),
        "native_backend_sql_and_join": "NOT_VERIFIED",
        "provider_release": "UNKNOWN_WRDSVERSION_3_NOT_RELEASE_EVIDENCE",
        "project_license_export_terms": "UNKNOWN",
    }
    return summary, upload, index_bytes, comparison_bytes


def verify_output(output: Path) -> dict:
    ignored_output(output)
    manifest = read_json(output / MANIFEST)
    require(manifest.get("schema") == "m5_distribution_segment_manifest_private_v1",
            "OUTPUT_MANIFEST_INVALID")
    require(manifest.get("audit_code_sha256") == sha(Path(__file__)),
            "AUDIT_CODE_CHANGED")
    handoff = Path(manifest["handoff_zip_path"])
    bound = Path(manifest["bound_txt_path"])
    daily_dir = Path(manifest["daily_dir_path"])
    if manifest["scope"] != "SYNTHETIC_TEST_ONLY":
        require(manifest["scope"] == "ENGINEERING_ONLY" and
                manifest["handoff_zip_sha256"] == HANDOFF_SHA and
                manifest["expected_page_rows"] == PAGE_ROWS and
                manifest["expected_bound_id_count"] == SEED_COUNT and
                sha(daily_dir / "m5_daily_segment_summary_safe.json") == DAILY_SUMMARY_SHA and
                sha(daily_dir / "m5_daily_segment_manifest_private.json") == DAILY_MANIFEST_SHA and
                sha(daily_dir / "m5_daily_segment_exceptions_private.json") == DAILY_EXCEPTIONS_SHA,
                "REAL_SOURCE_BINDING_MISMATCH")
    require(sha(handoff) == manifest["handoff_zip_sha256"],
            "HANDOFF_ZIP_CHANGED")
    verify_package_transport(handoff)
    with checked_zip(output / "package" / PACKAGED_ZIP,
                     expected_names={CSV_MEMBER}) as inner:
        inner_hash = hashlib.sha256(inner.read(CSV_MEMBER)).hexdigest()
    require(inner_hash == sha(output / "raw" / CSV_MEMBER),
            "EXTRACTED_CSV_ZIP_MEMBER_MISMATCH")
    for relative, expected in manifest["saved_file_sha256"].items():
        require(isinstance(relative, str) and safe_member_name(relative),
                "OUTPUT_FILE_PATH_INVALID")
        target = (output / PurePosixPath(relative)).resolve()
        require(output.resolve() in target.parents, "OUTPUT_FILE_PATH_ESCAPE")
        require(target.is_file() and sha(target) == expected,
                "SAVED_FILE_HASH_CHANGED")
    require(sha(bound) == manifest["bound_txt_sha256"], "BOUND_UPLOAD_CHANGED")
    summary, _, index_bytes, comparison_bytes = derive(
        output, expected_rows=manifest["expected_page_rows"],
        expected_ids=manifest["expected_bound_id_count"],
        bound_txt=bound, daily_dir=daily_dir)
    summary["source_file_identities"] = manifest["source_file_identities"]
    if manifest["scope"] == "SYNTHETIC_TEST_ONLY":
        summary["scope"] = "SYNTHETIC_TEST_ONLY"
    require(json_bytes(summary) == (output / SUMMARY).read_bytes(),
            "SAVED_SUMMARY_DERIVATION_MISMATCH")
    require(index_bytes == (output / INDEX).read_bytes(),
            "SAVED_EVENT_INDEX_DERIVATION_MISMATCH")
    require(comparison_bytes == (output / COMPARISON).read_bytes(),
            "SAVED_DAILY_COMPARISON_DERIVATION_MISMATCH")
    execution_path = output / EXECUTION
    if execution_path.exists():
        execution = read_json(execution_path)
        require(execution.get("schema") == "m5_distribution_execution_private_v1" and
                execution.get("run_exit_code") == 0 and
                execution.get("run_internal_verify_count") == 1 and
                execution.get("run_internal_verify_exit_code") == 0 and
                execution.get("summary_sha256") == sha(output / SUMMARY) and
                execution.get("manifest_sha256") == sha(output / MANIFEST) and
                execution.get("audit_code_sha256") == sha(Path(__file__)),
                "SAVED_EXECUTION_LEDGER_MISMATCH")
    return summary


def run(handoff: Path, output: Path, bound_txt: Path = BOUND_TXT,
        daily_dir: Path = DAILY_DIR, sql: Path | None = None,
        *, expected_rows: int = PAGE_ROWS, expected_ids: int = SEED_COUNT,
        synthetic_test_only: bool = False) -> dict:
    ignored_output(output)
    require(not output.exists(), "OUTPUT_ALREADY_EXISTS")
    require(handoff.is_file(), "HANDOFF_ZIP_MISSING")
    if not synthetic_test_only:
        require(expected_rows == PAGE_ROWS and expected_ids == SEED_COUNT and
                sha(handoff) == HANDOFF_SHA and sha(bound_txt) == BOUND_SHA and
                sha(daily_dir / "m5_daily_segment_summary_safe.json") == DAILY_SUMMARY_SHA and
                sha(daily_dir / "m5_daily_segment_manifest_private.json") == DAILY_MANIFEST_SHA and
                sha(daily_dir / "m5_daily_segment_exceptions_private.json") == DAILY_EXCEPTIONS_SHA,
                "REAL_SOURCE_BINDING_MISMATCH")
    started = datetime.now(timezone.utc).isoformat()
    output.mkdir(parents=True, exist_ok=False)
    try:
        source = verify_package_and_extract(handoff, output)
        if sql is not None:
            require(sql.is_file(), "SQL_PATH_MISSING")
            sql_dst = output / "package" / "saved_download_query.sql"
            with sql.open("rb") as inp, sql_dst.open("xb") as dest:
                shutil.copyfileobj(inp, dest)
            require(sha(sql) == sha(sql_dst), "SQL_COPY_HASH_MISMATCH")
        summary, _, index_bytes, comparison_bytes = derive(
            output, expected_rows=expected_rows, expected_ids=expected_ids,
            bound_txt=bound_txt, daily_dir=daily_dir)
        source_paths = [output / "raw" / CSV_MEMBER, output / "package" / PACKAGED_ZIP,
                        output / "package" / PACKAGED_TXT,
                        output / "package" / PACKAGE_MANIFEST,
                        output / "package" / FIELD_REVIEW,
                        output / "package" / "02_PARAMETERS_USER_PROVIDED.txt"]
        source_paths.extend((output / "package" / "10_current_distribution" /
                             "evidence").glob("*.png"))
        source_paths.extend((output / "package" / "11_pre_submission_form" /
                             "evidence").glob("*.png"))
        if sql is not None:
            source_paths.append(output / "package" / "saved_download_query.sql")
        source_identities = {str(path.relative_to(output)).replace("\\", "/"): sha(path)
                             for path in source_paths}
        summary["source_file_identities"] = source_identities
        if synthetic_test_only:
            summary["scope"] = "SYNTHETIC_TEST_ONLY"
        (output / INDEX).write_bytes(index_bytes)
        (output / COMPARISON).write_bytes(comparison_bytes)
        write_json_new(output / SUMMARY, summary)
        registry = {
            "schema": "m5_distribution_source_registry_private_v1",
            "scope": summary["scope"],
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "handoff_zip_path": str(handoff.resolve()),
            "handoff_zip_sha256": source["handoff_zip_sha256"],
            "bound_txt_path": str(bound_txt.resolve()),
            "bound_txt_sha256": sha(bound_txt),
            "daily_dir_path": str(daily_dir.resolve()),
            "sql_path_if_provided": str(sql.resolve()) if sql is not None else None,
            "sql_status": "USER_SAVED_SQL_BOUND_NOT_PARSED" if sql is not None else
            "DOWNLOAD_SQL_NOT_LOCATED",
            "python_executable": sys.executable,
            "audit_code_sha256": sha(Path(__file__)),
            "source_file_identities": source_identities,
            "original_source_files_overwritten": False,
            "wrds_queries_submitted_by_auditor": 0,
        }
        write_json_new(output / REGISTRY, registry)
        saved = {str(path.relative_to(output)).replace("\\", "/"): sha(path)
                 for path in (output / "package").rglob("*") if path.is_file()}
        saved.update({str(path.relative_to(output)).replace("\\", "/"): sha(path)
                      for path in (output / "raw" / CSV_MEMBER, output / SUMMARY,
                                   output / INDEX, output / COMPARISON, output / REGISTRY)})
        manifest = {
            "schema": "m5_distribution_segment_manifest_private_v1",
            "scope": summary["scope"],
            "handoff_zip_path": str(handoff.resolve()),
            "handoff_zip_sha256": source["handoff_zip_sha256"],
            "bound_txt_path": str(bound_txt.resolve()),
            "bound_txt_sha256": sha(bound_txt),
            "daily_dir_path": str(daily_dir.resolve()),
            "audit_code_sha256": sha(Path(__file__)),
            "expected_page_rows": expected_rows,
            "expected_bound_id_count": expected_ids,
            "source_file_identities": source_identities,
            "saved_file_sha256": saved,
        }
        write_json_new(output / MANIFEST, manifest)
        result = verify_output(output)
        write_json_new(output / EXECUTION, {
            "schema": "m5_distribution_execution_private_v1",
            "scope": summary["scope"],
            "started_at_utc": started,
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            "run_exit_code": 0,
            "run_internal_verify_count": 1,
            "run_internal_verify_exit_code": 0,
            "audit_code_sha256": sha(Path(__file__)),
            "summary_sha256": sha(output / SUMMARY),
            "manifest_sha256": sha(output / MANIFEST),
            "peak_memory_measured": False,
            "model_or_financial_experiment_run": False,
        })
        return result
    except Exception as error:
        write_json_new(output / "failure_private.json", {
            "schema": "m5_distribution_failure_private_v1",
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "error_type": type(error).__name__,
            "error_code": error.code if isinstance(error, IntakeError) else "UNHANDLED_INPUT_OR_IO_ERROR",
            "source_handoff_sha256": sha(handoff) if handoff.is_file() else None,
        })
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "verify-output"))
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--handoff-zip", type=Path)
    parser.add_argument("--sql", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "run":
            require(args.handoff_zip is not None, "HANDOFF_ZIP_REQUIRED")
            result = run(args.handoff_zip, args.output_dir, sql=args.sql)
            print("M5_DISTRIBUTION_SEGMENT_SAVED_" + result["status"])
        else:
            result = verify_output(args.output_dir)
            print("M5_DISTRIBUTION_SAVED_OUTPUT_VERIFIED_" + result["status"])
        return 0
    except Exception:
        print("M5_DISTRIBUTION_INTAKE_REVIEW_REQUIRED")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
