"""Offline, source-bound CCM candidate intake; never admits an M5 sample.

Only fixed aggregate counts go to stdout or the safe summary. Raw source rows,
identifiers, paths and exceptions remain in a Git-ignored local directory.
The submitted parameter transcription is not native SQL or a final server log.
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
import time
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from pathlib import PurePosixPath

import audit_m5_wrds_intake as old_intake


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
QUERY_ID = 11905330
PAGE_ROWS = 123388
SOURCE_ZIP_SHA256 = "ab660b8b6f31fcc424c759531c9c6009359ac3ea79c2c3b0fb9873ddb0bc1bcb"
OUTER_ZIP_SHA256 = "434f3a2e62630f5a7c6351f605d196fc55b6b3f9d12346c05334250ba3ff3084"
SOURCE_ZIP_BYTES = 2562440
CSV_MEMBER = "bvu211dfutaxkh9u.csv"
CSV_MEMBER_BYTES = 11707909
CSV_MEMBER_CRC32 = 0x40A30CEE
CANDIDATE_SHA256 = "bb5649176ea913990b11082e9c5214bc5e3ffe96a98a3acd53f0e8d8f906ee51"
SEED_SHA256 = "017224e43461409fa6f2ef007d182a5b0d870f02cd1edc4c528f0a901a543318"
LINK_FIELDS = ("gvkey", "liid", "linkdt", "linkenddt", "linkprim", "linktype", "lpermco", "lpermno")
NAMES_FIELDS = ("conm", "tic", "cusip", "cik", "sic", "naics")
COMPHEAD_FIELDS = ("priusa",)
EXPECTED = LINK_FIELDS + NAMES_FIELDS + COMPHEAD_FIELDS
LINKTYPE_CODES = ("LC", "LU", "LS", "LX", "LD", "LN", "NR", "NU")
LINKPRIM_CODES = ("P", "C")
NULL_LITERAL = re.compile(r"(?:\.|\.[A-Za-z]|NA|N/A|NULL)", re.IGNORECASE | re.ASCII)
POSITIVE_DECIMAL_ID = re.compile(r"[1-9][0-9]*\Z", re.ASCII)
DATE_ISO = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z", re.ASCII)
DATE_COMPACT = re.compile(r"[0-9]{8}\Z", re.ASCII)
INDEX_NAME = "m5_ccm_complete_relation_index_private.jsonl"
EXCEPTIONS_NAME = "m5_ccm_exceptions_private.jsonl"
SUMMARY_NAME = "m5_ccm_summary_safe.json"
REGISTRY_NAME = "m5_ccm_registry_private.json"
MANIFEST_NAME = "m5_ccm_manifest_private.json"


class IntakeError(ValueError):
    """A fixed diagnostic code with no licensed values or private paths."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise IntakeError(code)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def canonical_line(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), "SOURCE_JSON_INVALID")
    return value


def checked_code() -> dict:
    require(sys.version_info[:2] == (3, 11), "PYTHON_VERSION_MISMATCH")
    require(Path(old_intake.__file__).resolve() == (ROOT / "scripts/audit_m5_wrds_intake.py").resolve(),
            "IMPORTED_DECODER_PATH_MISMATCH")
    candidate_path = ROOT / "configs/m5_protocol_company_proxy_conditional_20261113.json"
    require(sha256_file(candidate_path) == CANDIDATE_SHA256, "M5_CANDIDATE_HASH_MISMATCH")
    candidate = load_json(candidate_path)
    require(candidate.get("status") == "PROPOSED" and candidate.get("researcher_accepted") is False
            and candidate.get("formal_financial_experiment_status") == "NOT_RUN",
            "M5_CANDIDATE_STATUS_MISMATCH")
    return {"intake_script_sha256": sha256_file(Path(__file__)),
            "id_decoder_script_sha256": sha256_file(Path(old_intake.__file__)),
            "python_executable_sha256": sha256_file(Path(sys.executable)),
            "python_version": sys.version.split()[0],
            "candidate_sha256": CANDIDATE_SHA256}


def ensure_ignored(directory: Path) -> Path:
    directory = directory.resolve()
    require(directory != ARTIFACTS and ARTIFACTS in directory.parents,
            "OUTPUT_NOT_WITHIN_ARTIFACTS")
    ignored = subprocess.run(["git", "check-ignore", "--quiet", "--", str(directory / "probe")],
                             cwd=ROOT, capture_output=True, check=False)
    require(ignored.returncode == 0, "OUTPUT_NOT_GIT_IGNORED")
    return directory


def _safe_member_name(name: str) -> bool:
    path = PurePosixPath(name)
    return (name == path.as_posix() and not path.is_absolute() and
            not any(part in ("", ".", "..") for part in path.parts) and
            ":" not in name and "\\" not in name)


def check_source_bundle(packet_dir: Path, outer_zip: Path) -> dict:
    packet_dir = packet_dir.resolve(strict=True)
    outer_zip = outer_zip.resolve(strict=True)
    require(sha256_file(outer_zip) == OUTER_ZIP_SHA256, "OUTER_ZIP_HASH_MISMATCH")
    handoff_manifest_path = packet_dir / "FILE_HASHES.json"
    handoff_manifest = load_json(handoff_manifest_path)
    listed = handoff_manifest.get("files")
    require(handoff_manifest.get("schema") == "m5_ccm_query11905330_local_input_handoff_hashes_v1"
            and isinstance(listed, dict) and listed,
            "HANDOFF_MANIFEST_INVALID")
    names = set(listed)
    require(all(isinstance(name, str) and _safe_member_name(name) for name in names)
            and len(names) == len({name.casefold() for name in names})
            and "FILE_HASHES.json" not in names,
            "HANDOFF_MEMBER_PATH_INVALID")
    for name, identity in listed.items():
        path = packet_dir / name
        require(path.is_file() and path.resolve().is_relative_to(packet_dir)
                and path.stat().st_size == identity.get("bytes")
                and sha256_file(path) == identity.get("sha256"),
                "HANDOFF_EXTRACTED_MEMBER_MISMATCH")
    with zipfile.ZipFile(outer_zip) as archive:
        archive_names = archive.namelist()
        require(archive.testzip() is None and len(archive_names) == len(set(archive_names))
                and set(archive_names) == names | {"FILE_HASHES.json"},
                "OUTER_ZIP_CRC_OR_MEMBERS_MISMATCH")
        require(archive.read("FILE_HASHES.json") == handoff_manifest_path.read_bytes(),
                "OUTER_MANIFEST_BYTES_MISMATCH")
        for name, identity in listed.items():
            info = archive.getinfo(name)
            require(info.file_size == identity["bytes"], "OUTER_MEMBER_SIZE_MISMATCH")
            with archive.open(info) as stream:
                digest = hashlib.sha256()
                for block in iter(lambda: stream.read(1 << 20), b""):
                    digest.update(block)
            require(digest.hexdigest() == identity["sha256"], "OUTER_MEMBER_HASH_MISMATCH")
    source_zip = packet_dir / "raw/bvu211dfutaxkh9u_csv.zip"
    csv_path = packet_dir / "raw_extracted/bvu211dfutaxkh9u.csv"
    require(source_zip.is_file() and csv_path.is_file(), "SOURCE_FILE_MISSING")
    require(source_zip.stat().st_size == SOURCE_ZIP_BYTES
            and sha256_file(source_zip) == SOURCE_ZIP_SHA256, "SOURCE_ZIP_IDENTITY_MISMATCH")
    csv_sha = sha256_file(csv_path)
    require(csv_path.stat().st_size == CSV_MEMBER_BYTES, "CSV_MEMBER_SIZE_MISMATCH")
    with zipfile.ZipFile(source_zip) as archive:
        require(archive.namelist() == [CSV_MEMBER] and archive.testzip() is None,
                "SOURCE_ZIP_MEMBER_OR_CRC_MISMATCH")
        info = archive.getinfo(CSV_MEMBER)
        require(info.file_size == CSV_MEMBER_BYTES and info.CRC == CSV_MEMBER_CRC32,
                "SOURCE_ZIP_MEMBER_IDENTITY_MISMATCH")
        digest = hashlib.sha256()
        with archive.open(info) as stream:
            for block in iter(lambda: stream.read(1 << 20), b""):
                digest.update(block)
        require(digest.hexdigest() == csv_sha, "EXTRACTED_CSV_SOURCE_MISMATCH")
    evidence_names = (
        "OBSERVED_PAGE_AND_TRANSPORT.json", "PARAMETERS_STRUCTURED.json",
        "SOURCE_ROLE_LEDGER.json", "SCREENSHOT_IDENTITIES.json",
        "USER_PASTED_PARAMETERS_TRANSCRIPT.txt",
    )
    evidence_paths = {name: packet_dir / "evidence" / name for name in evidence_names}
    require(all(path.is_file() for path in evidence_paths.values()), "SOURCE_EVIDENCE_MISSING")
    page = load_json(evidence_paths["OBSERVED_PAGE_AND_TRANSPORT.json"])
    params = load_json(evidence_paths["PARAMETERS_STRUCTURED.json"])
    roles = load_json(evidence_paths["SOURCE_ROLE_LEDGER.json"])
    require(page.get("result_page", {}).get("query_id") == QUERY_ID
            and page["result_page"].get("displayed_rows") == PAGE_ROWS
            and page["result_page"].get("product") == "crsp_a_ccm"
            and page["result_page"].get("status") == "Success"
            and page.get("source_zip", {}).get("sha256") == SOURCE_ZIP_SHA256,
            "PAGE_EVIDENCE_MISMATCH")
    p = params.get("parameters", {})
    require(params.get("source") == "USER_PASTED_TEXT_NOT_NATIVE_SERVER_EXPORT_OR_SQL"
            and params.get("query_id_from_result_screenshot") == QUERY_ID
            and tuple(p.get("var", [])) == LINK_FIELDS
            and tuple(p.get("extra6", [])) == NAMES_FIELDS
            and p.get("extra8") == "priusa"
            and tuple(p.get("extra10", [])) == LINKTYPE_CODES,
            "TRANSCRIPT_EVIDENCE_MISMATCH")
    statements = roles.get("observed_form_source_statement", {})
    require(tuple(statements.get("link_information", {}).get("fields", [])) == LINK_FIELDS
            and tuple(statements.get("identifying_information", {}).get("fields", [])) == NAMES_FIELDS
            and tuple(statements.get("company_header_information", {}).get("fields", [])) == COMPHEAD_FIELDS,
            "SOURCE_ROLE_EVIDENCE_MISMATCH")
    return {"packet_dir": str(packet_dir), "outer_zip_path": str(outer_zip),
            "outer_zip_sha256": OUTER_ZIP_SHA256,
            "handoff_manifest_sha256": sha256_file(handoff_manifest_path),
            "handoff_member_count": len(listed),
            "source_zip_path": str(source_zip),
            "source_zip_sha256": SOURCE_ZIP_SHA256, "source_csv_path": str(csv_path),
            "source_csv_sha256": csv_sha,
            "evidence_paths": {name: str(path) for name, path in evidence_paths.items()},
            "evidence_sha256": {name: sha256_file(path) for name, path in evidence_paths.items()},
            "page_rows": PAGE_ROWS,
            "result_page_status": "SCREENSHOT_VISIBLE_SUCCESS_NOT_NATIVE_EXECUTION_LOG",
            "form_source_role_status": "OBSERVED_PRE_SUBMISSION_FORM_ROLES",
            "final_parameter_status": "USER_PASTED_TRANSCRIPT_ONLY",
            "native_sql_status": "NOT_SUPPLIED",
            "backend_join_status": "UNKNOWN"}


def check_seed(seed_txt: Path) -> tuple[set[str], dict]:
    seed_txt = seed_txt.resolve(strict=True)
    require(seed_txt.name == "m5_daily_seed_permnos_upload.txt", "SEED_NAME_MISMATCH")
    digest = sha256_file(seed_txt)
    require(digest == SEED_SHA256, "SEED_HASH_MISMATCH")
    data = seed_txt.read_bytes()
    require(data and data.endswith(b"\n") and b"\r" not in data and not data.startswith(b"\xef\xbb\xbf"),
            "SEED_FORMAT_MISMATCH")
    try:
        lines = data.decode("ascii").splitlines()
    except UnicodeError as exc:
        raise IntakeError("SEED_ENCODING_MISMATCH") from exc
    require(all(POSITIVE_DECIMAL_ID.fullmatch(value) for value in lines)
            and len(lines) == len(set(lines)), "SEED_IDENTIFIER_SET_INVALID")
    registry_path = seed_txt.parent / "m5_daily_upload_registry_private.json"
    plan_path = seed_txt.parent / "m5_daily_download_plan_private.json"
    require(registry_path.is_file() and plan_path.is_file(), "SEED_SOURCE_RECEIPT_MISSING")
    registry = load_json(registry_path)
    plan = load_json(plan_path)
    require(registry.get("schema") == "m5_daily_upload_registry_private_v1"
            and registry.get("upload_sha256") == digest
            and registry.get("line_count") == len(lines)
            and registry.get("unique_count") == len(lines)
            and registry.get("set_equal_to_seed_index_and_snapshot") is True
            and registry.get("formal_m5_cohort_selected") is False
            and registry.get("plan_sha256") == sha256_file(plan_path),
            "SEED_REGISTRY_BINDING_MISMATCH")
    require(plan.get("schema") == "m5_daily_download_plan_private_v1"
            and plan.get("company_code_file_sha256") == digest
            and plan.get("starting_identifier_count") == len(lines)
            and plan.get("successor_ids_auto_added") is False,
            "SEED_PLAN_BINDING_MISMATCH")
    seed_json = ARTIFACTS / "m5_delist_intake_20261111_v3/m5_daily_seed_permnos_private.json"
    expected_seed_sha = registry.get("input_bindings", {}).get("seed_sha256")
    require(isinstance(expected_seed_sha, str) and seed_json.is_file()
            and sha256_file(seed_json) == expected_seed_sha,
            "SEED_UPSTREAM_JSON_BINDING_MISMATCH")
    upstream = load_json(seed_json)
    upstream_ids = upstream.get("permnos")
    require(upstream.get("schema") == "m5_daily_seed_identifiers_private_v1"
            and upstream.get("scope") == "DOWNLOAD_INPUT_ONLY_NOT_FORMAL_COHORT"
            and upstream.get("successor_ids_auto_added") is False
            and isinstance(upstream_ids, list)
            and all(isinstance(value, str) and POSITIVE_DECIMAL_ID.fullmatch(value)
                    for value in upstream_ids)
            and len(upstream_ids) == len(set(upstream_ids))
            and upstream_ids == sorted(upstream_ids)
            and upstream.get("permno_count") == len(upstream_ids)
            and set(upstream_ids) == set(lines),
            "SEED_UPSTREAM_SET_MISMATCH")
    return set(lines), {"seed_txt_path": str(seed_txt), "seed_txt_sha256": digest,
                        "seed_count": len(lines), "seed_registry_path": str(registry_path),
                        "seed_registry_sha256": sha256_file(registry_path),
                        "seed_plan_path": str(plan_path), "seed_plan_sha256": sha256_file(plan_path),
                        "upstream_seed_json_path": str(seed_json),
                        "upstream_seed_json_sha256": expected_seed_sha}


def header_mapping(raw_header: list[str]) -> tuple[dict[str, int] | None, dict]:
    exact_duplicates = len(raw_header) - len(set(raw_header))
    normalized = [value.strip().casefold() for value in raw_header]
    normalized_duplicates = len(normalized) - len(set(normalized))
    expected_missing = sorted(set(EXPECTED) - set(normalized))
    extra_count = sum(value not in EXPECTED for value in normalized)
    status = {"actual_column_count": len(raw_header), "requested_column_count": len(EXPECTED),
              "exact_duplicate_header_count": exact_duplicates,
              "normalized_colliding_header_count": normalized_duplicates,
              "blank_header_count": normalized.count(""),
              "missing_requested_fields": expected_missing, "extra_column_count": extra_count,
              "mapping_unambiguous": not (exact_duplicates or normalized_duplicates or
                                           normalized.count("") or expected_missing)}
    mapping = {value: position for position, value in enumerate(normalized)} if status["mapping_unambiguous"] else None
    return mapping, status


def parse_endpoint(raw: str) -> dict:
    value = raw.strip()
    if value == "":
        return {"raw": raw, "parsed": None, "status": "BLANK_UNKNOWN_ENDPOINT_SEMANTICS"}
    if NULL_LITERAL.fullmatch(value):
        return {"raw": raw, "parsed": None, "status": "SENTINEL_UNKNOWN_ENDPOINT_SEMANTICS"}
    if DATE_ISO.fullmatch(value):
        text = value
    elif DATE_COMPACT.fullmatch(value):
        text = f"{value[:4]}-{value[4:6]}-{value[6:8]}"
    else:
        return {"raw": raw, "parsed": None, "status": "UNPARSED_DATE_REPRESENTATION"}
    try:
        parsed = date.fromisoformat(text).isoformat()
    except ValueError:
        return {"raw": raw, "parsed": None, "status": "INVALID_GREGORIAN_DATE"}
    return {"raw": raw, "parsed": parsed, "status": "PARSED_FINITE"}


def normalize_security_id(raw: str) -> dict:
    value = raw.strip()
    if value == "":
        status, normalized = "BLANK", None
    else:
        normalized = old_intake.normalize_id(value)
        if normalized is not None:
            status = "VALID_POSITIVE_INTEGER"
        elif re.fullmatch(r"0+(?:\.0+)?", value, re.ASCII):
            status = "ZERO_NOT_LINKABLE"
        else:
            status = "UNPARSED_OR_NONPOSITIVE"
    return {"raw": raw, "normalized": normalized, "status": status}


def code_bucket(raw: str, allowlist: tuple[str, ...]) -> str:
    value = raw.strip().upper()
    return "BLANK" if not value else value if value in allowlist else "OTHER"


def _record(line: bytes, stream, fingerprint: hashlib._Hash) -> None:
    stream.write(line)
    fingerprint.update(hashlib.sha256(line).digest())


def scan_csv(csv_path: Path, index_path: Path, exception_path: Path,
             *, source_binding_id: str, seed_ids: set[str] | None,
             page_rows: int = PAGE_ROWS,
             source_zip_sha256: str = "SYNTHETIC_TEST_ONLY",
             source_csv_sha256: str = "SYNTHETIC_TEST_ONLY") -> tuple[dict, dict]:
    """Rebuild every row into a deterministic private index; return safe counts.

    A bad header or row width is preserved, counted and marked blocked rather
    than silently repaired. No source value appears in the safe summary.
    """
    require(not index_path.exists() and not exception_path.exists(), "DERIVATION_TARGET_EXISTS")
    counts: dict[str, object] = {}
    relation_counter: Counter[tuple[str, ...]] = Counter()
    full_row_counter: Counter[tuple[str, ...]] = Counter()
    relation_rows: dict[tuple[str, ...], list[int]] = defaultdict(list)
    full_row_rows: dict[tuple[str, ...], list[int]] = defaultdict(list)
    distinct_gvkeys: set[str] = set()
    distinct_liids: set[str] = set()
    distinct_gvkey_liid_pairs: set[tuple[str, str]] = set()
    distinct_header_ciks: set[str] = set()
    distinct_security_ids: set[str] = set()
    distinct_company_ids: set[str] = set()
    security_to_gvkeys: dict[str, set[str]] = defaultdict(set)
    gvkey_to_securities: dict[str, set[str]] = defaultdict(set)
    gvkey_to_header_ciks: dict[str, set[str]] = defaultdict(set)
    security_gvkey_rows: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    gvkey_security_rows: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    gvkey_header_cik_rows: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    seed_hit_ids: set[str] = set()
    seed_hit_relations: set[tuple[str, ...]] = set()
    missing = {field: {"blank": 0, "null_literal": 0} for field in EXPECTED}
    date_status = {field: Counter() for field in ("linkdt", "linkenddt")}
    id_status = {field: Counter() for field in ("lpermco", "lpermno")}
    linktype_freq = Counter({code: 0 for code in (*LINKTYPE_CODES, "BLANK", "OTHER")})
    linkprim_freq = Counter({code: 0 for code in (*LINKPRIM_CODES, "BLANK", "OTHER")})
    index_fingerprint = hashlib.sha256()
    exception_fingerprint = hashlib.sha256()
    row_count = width_errors = reversed_finite = unknown_endpoint_rows = 0
    seed_hit_rows = exception_count = group_exception_count = 0
    try:
        with csv_path.open("r", encoding="utf-8-sig", errors="strict", newline="") as source, \
             index_path.open("wb") as index_out, exception_path.open("wb") as exception_out:
            reader = csv.reader(source, strict=True)
            raw_header = next(reader, None)
            require(raw_header is not None, "CSV_HEADER_MISSING")
            mapping, header_status = header_mapping(raw_header)
            for cells in reader:
                row_count += 1
                errors: list[str] = []
                record: dict = {"schema": "m5_ccm_complete_relation_row_v1",
                                "row_ordinal": row_count,
                                "source_binding_id": source_binding_id,
                                "source_zip_sha256": source_zip_sha256,
                                "source_csv_sha256": source_csv_sha256,
                                "raw_cells": cells}
                if len(cells) != len(raw_header):
                    width_errors += 1
                    errors.append("ROW_WIDTH_MISMATCH")
                    record["relation_status"] = "NOT_DERIVED_INVALID_WIDTH"
                elif mapping is None:
                    record["relation_status"] = "NOT_DERIVED_HEADER_AMBIGUOUS"
                else:
                    fields = {field: cells[mapping[field]] for field in EXPECTED}
                    link = {field: fields[field] for field in LINK_FIELDS}
                    names = {field: fields[field] for field in NAMES_FIELDS}
                    comphead = {field: fields[field] for field in COMPHEAD_FIELDS}
                    dates = {field: parse_endpoint(fields[field]) for field in ("linkdt", "linkenddt")}
                    ids = {field: normalize_security_id(fields[field]) for field in ("lpermco", "lpermno")}
                    record.update({"relation_status": "CANDIDATE_NOT_ADMITTED",
                                   "link_source_role": "FORM_DECLARED_CCMXPF_LNKHIST_NOT_NATIVE_SQL_VERIFIED",
                                   "link_cells": link,
                                   "header_observations": {
                                       "compustat_names_form_declared": names,
                                       "crsp_comphead_form_declared": comphead},
                                   "parsed_dates": dates, "normalized_security_ids": ids})
                    relation = tuple(link[field] for field in LINK_FIELDS)
                    relation_counter[relation] += 1
                    relation_rows[relation].append(row_count)
                    full_row_counter[tuple(cells)] += 1
                    full_row_rows[tuple(cells)].append(row_count)
                    for field, value in fields.items():
                        stripped = value.strip()
                        if not stripped:
                            missing[field]["blank"] += 1
                        elif NULL_LITERAL.fullmatch(stripped):
                            missing[field]["null_literal"] += 1
                    for field, value in dates.items():
                        date_status[field][value["status"]] += 1
                        if value["status"] != "PARSED_FINITE":
                            errors.append(field.upper() + "_UNKNOWN_OR_INVALID")
                    if any(value["status"] != "PARSED_FINITE" for value in dates.values()):
                        unknown_endpoint_rows += 1
                    elif dates["linkdt"]["parsed"] > dates["linkenddt"]["parsed"]:
                        reversed_finite += 1
                        errors.append("REVERSED_FINITE_INTERVAL")
                    for field, value in ids.items():
                        id_status[field][value["status"]] += 1
                        if value["status"] != "VALID_POSITIVE_INTEGER":
                            errors.append(field.upper() + "_NOT_LINKABLE")
                    linktype_freq[code_bucket(fields["linktype"], LINKTYPE_CODES)] += 1
                    linkprim_freq[code_bucket(fields["linkprim"], LINKPRIM_CODES)] += 1
                    gvkey = fields["gvkey"]
                    liid = fields["liid"]
                    cik = fields["cik"]
                    if gvkey.strip():
                        distinct_gvkeys.add(gvkey)
                    if liid.strip():
                        distinct_liids.add(liid)
                    if gvkey.strip() and liid.strip():
                        distinct_gvkey_liid_pairs.add((gvkey, liid))
                    if cik.strip():
                        distinct_header_ciks.add(cik)
                        if gvkey.strip():
                            gvkey_to_header_ciks[gvkey].add(cik)
                            gvkey_header_cik_rows[gvkey][cik].append(row_count)
                    permno = ids["lpermno"]["normalized"]
                    permco = ids["lpermco"]["normalized"]
                    if permno is not None:
                        distinct_security_ids.add(permno)
                        if gvkey.strip():
                            security_to_gvkeys[permno].add(gvkey)
                            gvkey_to_securities[gvkey].add(permno)
                            security_gvkey_rows[permno][gvkey].append(row_count)
                            gvkey_security_rows[gvkey][permno].append(row_count)
                        if seed_ids is not None and permno in seed_ids:
                            seed_hit_rows += 1
                            seed_hit_ids.add(permno)
                            seed_hit_relations.add(relation)
                    if permco is not None:
                        distinct_company_ids.add(permco)
                record["diagnostic_codes"] = errors
                _record(canonical_line(record), index_out, index_fingerprint)
                if errors:
                    exception_count += 1
                    _record(canonical_line({"schema": "m5_ccm_exception_row_v1",
                                            "row_ordinal": row_count,
                                            "diagnostic_codes": errors,
                                            "source_binding_id": source_binding_id}),
                            exception_out, exception_fingerprint)
            for relation, ordinals in sorted(relation_rows.items()):
                if len(ordinals) > 1:
                    group_exception_count += 1
                    _record(canonical_line({"schema": "m5_ccm_private_group_diagnostic_v1",
                                            "kind": "DUPLICATE_EIGHT_CELL_RELATION",
                                            "link_cells": dict(zip(LINK_FIELDS, relation)),
                                            "row_ordinals": ordinals}),
                            exception_out, exception_fingerprint)
            for full_row, ordinals in sorted(full_row_rows.items()):
                if len(ordinals) > 1:
                    group_exception_count += 1
                    _record(canonical_line({"schema": "m5_ccm_private_group_diagnostic_v1",
                                            "kind": "DUPLICATE_FULL_PARSED_ROW",
                                            "raw_cells": list(full_row),
                                            "row_ordinals": ordinals}),
                            exception_out, exception_fingerprint)
            for kind, groups in (
                ("SECURITY_WITH_MULTIPLE_GVKEY_OBSERVATIONS", security_gvkey_rows),
                ("GVKEY_WITH_MULTIPLE_SECURITY_OBSERVATIONS", gvkey_security_rows),
                ("GVKEY_WITH_MULTIPLE_HEADER_CIK_OBSERVATIONS", gvkey_header_cik_rows),
            ):
                for key, related in sorted(groups.items()):
                    if len(related) > 1:
                        group_exception_count += 1
                        _record(canonical_line({"schema": "m5_ccm_private_group_diagnostic_v1",
                                                "kind": kind, "group_key": key,
                                                "related_keys_and_row_ordinals":
                                                    {item: ordinals for item, ordinals in sorted(related.items())}}),
                                exception_out, exception_fingerprint)
    except UnicodeError as exc:
        raise IntakeError("CSV_ENCODING_INVALID") from exc
    except csv.Error as exc:
        raise IntakeError("CSV_QUOTING_INVALID") from exc
    mapping_valid = header_status["mapping_unambiguous"]
    status = ("STRUCTURE_BLOCKED" if not mapping_valid or width_errors else
              "PAGE_ROW_MISMATCH" if row_count != page_rows else
              "COMPLETE_CANDIDATE_INDEX_STRUCTURE_ONLY")
    counts.update({"schema": "m5_ccm_query11905330_safe_summary_v1",
                   "scope": "ENGINEERING_ONLY_NOT_FORMAL_M5",
                   "status": status,
                   "query_id": QUERY_ID,
                   "page_display_rows": page_rows,
                   "actual_csv_data_rows": row_count,
                   "page_count_matches_csv": row_count == page_rows,
                   "header": header_status,
                   "width_error_rows": width_errors,
                   "index_entries": row_count,
                   "exception_rows": exception_count,
                   "private_group_diagnostic_records": group_exception_count,
                   "missing_by_field": missing if mapping_valid else None,
                   "date_status_counts": {k: dict(v) for k, v in date_status.items()} if mapping_valid else None,
                   "unknown_endpoint_rows": unknown_endpoint_rows if mapping_valid else None,
                   "supported_open_endpoint_rows": 0,
                   "reversed_finite_interval_rows": reversed_finite if mapping_valid else None,
                   "security_id_status_counts": {k: dict(v) for k, v in id_status.items()} if mapping_valid else None,
                   "linktype_allowlisted_frequency": dict(linktype_freq) if mapping_valid else None,
                   "linkprim_allowlisted_frequency": dict(linkprim_freq) if mapping_valid else None,
                   "distinct_source_eight_cell_relation_tuples": len(relation_counter) if mapping_valid else None,
                   "duplicate_eight_cell_relation_groups": sum(v > 1 for v in relation_counter.values()) if mapping_valid else None,
                   "duplicate_eight_cell_relation_excess_rows": sum(v - 1 for v in relation_counter.values() if v > 1) if mapping_valid else None,
                   "duplicate_full_parsed_row_groups": sum(v > 1 for v in full_row_counter.values()) if mapping_valid else None,
                   "duplicate_full_parsed_row_excess_rows": sum(v - 1 for v in full_row_counter.values() if v > 1) if mapping_valid else None,
                   "distinct_nonblank_gvkey_raw": len(distinct_gvkeys) if mapping_valid else None,
                   "distinct_nonblank_liid_raw": len(distinct_liids) if mapping_valid else None,
                   "distinct_nonblank_gvkey_liid_raw_pairs": len(distinct_gvkey_liid_pairs) if mapping_valid else None,
                   "distinct_nonblank_header_cik_raw": len(distinct_header_ciks) if mapping_valid else None,
                   "distinct_positive_normalized_lpermno": len(distinct_security_ids) if mapping_valid else None,
                   "distinct_positive_normalized_lpermco": len(distinct_company_ids) if mapping_valid else None,
                   "security_ids_with_multiple_gvkey_observations": sum(len(v) > 1 for v in security_to_gvkeys.values()) if mapping_valid else None,
                   "gvkeys_with_multiple_security_ids": sum(len(v) > 1 for v in gvkey_to_securities.values()) if mapping_valid else None,
                   "gvkeys_with_multiple_header_cik_observations": sum(len(v) > 1 for v in gvkey_to_header_ciks.values()) if mapping_valid else None,
                   "overlap_interpretation": "NOT_COMPUTED_SOURCE_ENDPOINT_SEMANTICS_UNKNOWN",
                   "seed_coverage": None if seed_ids is None or not mapping_valid else {
                       "status": "OFFLINE_COVERAGE_DIAGNOSTIC_ONLY",
                       "seed_identifiers": len(seed_ids),
                       "seed_identifiers_present_in_export": len(seed_hit_ids),
                       "seed_identifiers_absent_from_export": len(seed_ids - seed_hit_ids),
                       "exported_identifiers_outside_seed": len(distinct_security_ids - seed_ids),
                       "source_rows_hitting_seed": seed_hit_rows,
                       "distinct_eight_cell_relations_hitting_seed": len(seed_hit_relations)},
                   "formal_cohort_count": None,
                   "admitted_event_count": None,
                   "historical_cik_security_validity": None,
                   "primary_security_rule_accepted": False,
                   "financial_labels_computed": False,
                   "financial_model_run": False})
    private = {"raw_header": raw_header, "mapping": mapping,
               "index_logical_fingerprint_sha256": index_fingerprint.hexdigest(),
               "exception_logical_fingerprint_sha256": exception_fingerprint.hexdigest(),
               "source_binding_id": source_binding_id}
    return counts, private


def augment_summary(counts: dict, source: dict, seed_meta: dict, code: dict) -> dict:
    result = dict(counts)
    result.update({"source_zip_sha256": source["source_zip_sha256"],
                   "source_csv_sha256": source["source_csv_sha256"],
                   "handoff_outer_zip_sha256": source["outer_zip_sha256"],
                   "handoff_manifest_sha256": source["handoff_manifest_sha256"],
                   "handoff_manifest_member_count": source["handoff_member_count"],
                   "source_evidence_sha256": source["evidence_sha256"],
                   "source_binding_precision": {
                       "result_page": source["result_page_status"],
                       "source_roles": source["form_source_role_status"],
                       "final_parameters": source["final_parameter_status"],
                       "native_sql": source["native_sql_status"],
                       "backend_join": source["backend_join_status"],
                       "full_database_server_execution_verified": False,
                       "exact_saved_release_verified": False},
                   "seed_txt_sha256": seed_meta["seed_txt_sha256"],
                   "seed_registry_sha256": seed_meta["seed_registry_sha256"],
                   "seed_plan_sha256": seed_meta["seed_plan_sha256"],
                   "upstream_seed_json_sha256": seed_meta["upstream_seed_json_sha256"],
                   "intake_code_sha256": code["intake_script_sha256"],
                   "id_decoder_code_sha256": code["id_decoder_script_sha256"],
                   "candidate_sha256": CANDIDATE_SHA256,
                   "candidate_status": "PROPOSED_RESEARCHER_NOT_ACCEPTED_NOT_RUN",
                   "source_member_crc32": f"{CSV_MEMBER_CRC32:08x}",
                   "source_member_bytes": CSV_MEMBER_BYTES,
                   "raw_zip_bytes": SOURCE_ZIP_BYTES,
                   "page_display_size": "2.4 MiB (rounded screenshot value)",
                   "header_cik_role": "COMP_NAMES_HEADER_OBSERVATION_NOT_LINK_VALID_TIME",
                   "priusa_role": "CRSP_COMPHEAD_HEADER_OBSERVATION_NOT_DATED_PRIMARY",
                   "linkprim_role": "RAW_LINK_MARKER_NOT_ACCEPTED_PRIMARY_RULE",
                   "link_interval_endpoint_semantics": "UNKNOWN_NO_OFFICIAL_OPEN_OR_INCLUSIVITY_ASSUMPTION",
                   "formal_m5_cohort_count": None,
                   "formal_m5_admitted_event_count": None})
    return result


def _file_manifest(output: Path) -> dict:
    names = (INDEX_NAME, EXCEPTIONS_NAME, SUMMARY_NAME, REGISTRY_NAME)
    return {"schema": "m5_ccm_saved_manifest_private_v1",
            "files": {name: {"sha256": sha256_file(output / name),
                             "bytes": (output / name).stat().st_size}
                      for name in names}}


def verify_saved(output: Path) -> dict:
    """Re-read the CSV and ZIP; rebuild a fresh complete index independently."""
    output = ensure_ignored(output)
    manifest = load_json(output / MANIFEST_NAME)
    require(manifest.get("schema") == "m5_ccm_saved_manifest_private_v1"
            and set(manifest.get("files", {})) == {INDEX_NAME, EXCEPTIONS_NAME,
                                                   SUMMARY_NAME, REGISTRY_NAME},
            "SAVED_MANIFEST_INVALID")
    for name, identity in manifest["files"].items():
        require((output / name).is_file()
                and sha256_file(output / name) == identity.get("sha256")
                and (output / name).stat().st_size == identity.get("bytes"),
                "SAVED_FILE_HASH_MISMATCH")
    registry = load_json(output / REGISTRY_NAME)
    saved_summary = load_json(output / SUMMARY_NAME)
    require(registry.get("schema") == "m5_ccm_local_registry_private_v1",
            "SAVED_REGISTRY_INVALID")
    current_code = checked_code()
    require(current_code == registry.get("code_identity"), "SAVED_CODE_IDENTITY_MISMATCH")
    source = check_source_bundle(Path(registry["source_bundle"]["packet_dir"]),
                                 Path(registry["source_bundle"]["outer_zip_path"]))
    require(source == registry.get("source_bundle"), "SAVED_SOURCE_BINDING_MISMATCH")
    seed_ids, seed_meta = check_seed(Path(registry["seed_binding"]["seed_txt_path"]))
    require(seed_meta == registry.get("seed_binding"), "SAVED_SEED_BINDING_MISMATCH")
    binding_id = hashlib.sha256((source["source_zip_sha256"] + ":" + source["source_csv_sha256"]).encode()).hexdigest()
    require(binding_id == registry.get("source_binding_id"), "SAVED_SOURCE_BINDING_ID_MISMATCH")
    with tempfile.TemporaryDirectory(prefix="m5_ccm_verify_", dir=output.parent) as temp_name:
        temp = Path(temp_name)
        counts, private = scan_csv(Path(source["source_csv_path"]), temp / INDEX_NAME,
                                   temp / EXCEPTIONS_NAME, source_binding_id=binding_id,
                                   seed_ids=seed_ids, page_rows=PAGE_ROWS,
                                   source_zip_sha256=source["source_zip_sha256"],
                                   source_csv_sha256=source["source_csv_sha256"])
        rebuilt_summary = augment_summary(counts, source, seed_meta, current_code)
        require(rebuilt_summary == saved_summary, "SAVED_SUMMARY_RECOMPUTE_MISMATCH")
        require(private["raw_header"] == registry.get("raw_header")
                and private["mapping"] == registry.get("header_mapping")
                and private["index_logical_fingerprint_sha256"] ==
                registry.get("index_logical_fingerprint_sha256")
                and private["exception_logical_fingerprint_sha256"] ==
                registry.get("exception_logical_fingerprint_sha256"),
                "SAVED_LOGICAL_INDEX_MISMATCH")
        require(sha256_file(temp / INDEX_NAME) == manifest["files"][INDEX_NAME]["sha256"]
                and sha256_file(temp / EXCEPTIONS_NAME) == manifest["files"][EXCEPTIONS_NAME]["sha256"],
                "SAVED_INDEX_REBUILD_MISMATCH")
    require(saved_summary["index_entries"] == saved_summary["actual_csv_data_rows"]
            and saved_summary["exception_rows"] <= saved_summary["index_entries"],
            "SAVED_ACCOUNTING_MISMATCH")
    internal_path = output / "m5_ccm_internal_verify_safe.json"
    if internal_path.exists():
        internal = load_json(internal_path)
        require(internal.get("schema") == "m5_ccm_internal_saved_verification_safe_v1"
                and internal.get("status") == "PASS_SAVED_OUTPUT_RECOMPUTED_IN_PROCESS"
                and internal.get("summary_sha256") == sha256_file(output / SUMMARY_NAME)
                and internal.get("manifest_sha256") == sha256_file(output / MANIFEST_NAME)
                and internal.get("source_csv_sha256") == source["source_csv_sha256"]
                and internal.get("source_zip_sha256") == source["source_zip_sha256"]
                and internal.get("code_sha256") == current_code["intake_script_sha256"]
                and internal.get("row_count") == saved_summary["actual_csv_data_rows"]
                and internal.get("index_entries") == saved_summary["index_entries"]
                and internal.get("financial_experiment_run") is False,
                "INTERNAL_VERIFY_RECEIPT_MISMATCH")
    external_path = output / "m5_ccm_independent_verify_safe.json"
    if external_path.exists():
        external = load_json(external_path)
        require(external.get("schema") == "m5_ccm_independent_invocation_verify_safe_v1"
                and external.get("status") == "PASS_SAVED_OUTPUT_RECOMPUTED"
                and external.get("summary_sha256") == sha256_file(output / SUMMARY_NAME)
                and external.get("source_zip_sha256") == source["source_zip_sha256"]
                and external.get("code_sha256") == current_code["intake_script_sha256"]
                and external.get("row_count") == saved_summary["actual_csv_data_rows"]
                and external.get("index_entries") == saved_summary["index_entries"]
                and external.get("financial_experiment_run") is False,
                "INDEPENDENT_VERIFY_RECEIPT_MISMATCH")
    return saved_summary


def intake(packet_dir: Path, outer_zip: Path, seed_txt: Path, output: Path) -> dict:
    started = datetime.now(timezone.utc)
    tick = time.perf_counter()
    output = ensure_ignored(output)
    require(not output.exists(), "OUTPUT_ALREADY_EXISTS")
    code = checked_code()
    source = check_source_bundle(packet_dir, outer_zip)
    seed_ids, seed_meta = check_seed(seed_txt)
    binding_id = hashlib.sha256((source["source_zip_sha256"] + ":" + source["source_csv_sha256"]).encode()).hexdigest()
    output.mkdir(parents=True)
    counts, private = scan_csv(Path(source["source_csv_path"]), output / INDEX_NAME,
                               output / EXCEPTIONS_NAME, source_binding_id=binding_id,
                               seed_ids=seed_ids, page_rows=PAGE_ROWS,
                               source_zip_sha256=source["source_zip_sha256"],
                               source_csv_sha256=source["source_csv_sha256"])
    summary = augment_summary(counts, source, seed_meta, code)
    (output / SUMMARY_NAME).write_bytes(json_bytes(summary))
    registry = {"schema": "m5_ccm_local_registry_private_v1",
                "scope": "ENGINEERING_ONLY_NOT_FORMAL_M5",
                "started_at_utc": started.isoformat(),
                "saved_at_utc": datetime.now(timezone.utc).isoformat(),
                "preverify_wall_seconds": time.perf_counter() - tick,
                "peak_memory_bytes": None,
                "command": sys.argv,
                "python_executable": sys.executable,
                "python_version": sys.version,
                "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "code_identity": code,
                "source_bundle": source,
                "seed_binding": seed_meta,
                "source_binding_id": binding_id,
                "raw_header": private["raw_header"],
                "header_mapping": private["mapping"],
                "index_logical_fingerprint_sha256": private["index_logical_fingerprint_sha256"],
                "exception_logical_fingerprint_sha256": private["exception_logical_fingerprint_sha256"],
                "intake_status": summary["status"]}
    (output / REGISTRY_NAME).write_bytes(json_bytes(registry))
    (output / MANIFEST_NAME).write_bytes(json_bytes(_file_manifest(output)))
    verified = verify_saved(output)
    internal_receipt = {"schema": "m5_ccm_internal_saved_verification_safe_v1",
                        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
                        "status": "PASS_SAVED_OUTPUT_RECOMPUTED_IN_PROCESS",
                        "summary_sha256": sha256_file(output / SUMMARY_NAME),
                        "manifest_sha256": sha256_file(output / MANIFEST_NAME),
                        "source_csv_sha256": source["source_csv_sha256"],
                        "source_zip_sha256": source["source_zip_sha256"],
                        "code_sha256": code["intake_script_sha256"],
                        "row_count": verified["actual_csv_data_rows"],
                        "index_entries": verified["index_entries"],
                        "financial_experiment_run": False}
    (output / "m5_ccm_internal_verify_safe.json").write_bytes(json_bytes(internal_receipt))
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("intake")
    run.add_argument("--packet-dir", type=Path, required=True)
    run.add_argument("--outer-zip", type=Path, required=True)
    run.add_argument("--seed-txt", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    verify = sub.add_parser("verify-output")
    verify.add_argument("--output", type=Path, required=True)
    verify.add_argument("--receipt", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "intake":
            summary = intake(args.packet_dir, args.outer_zip, args.seed_txt, args.output)
            print("M5_CCM_INTAKE_" + summary["status"])
            return 0 if summary["status"] == "COMPLETE_CANDIDATE_INDEX_STRUCTURE_ONLY" else 2
        start = time.perf_counter()
        summary = verify_saved(args.output)
        if args.receipt is not None:
            receipt = ensure_ignored(args.receipt)
            require(not receipt.exists(), "VERIFY_RECEIPT_ALREADY_EXISTS")
            receipt.parent.mkdir(parents=True, exist_ok=True)
            obj = {"schema": "m5_ccm_independent_invocation_verify_safe_v1",
                   "verified_at_utc": datetime.now(timezone.utc).isoformat(),
                   "status": "PASS_SAVED_OUTPUT_RECOMPUTED",
                   "summary_sha256": sha256_file(args.output / SUMMARY_NAME),
                   "source_zip_sha256": SOURCE_ZIP_SHA256,
                   "code_sha256": sha256_file(Path(__file__)),
                   "row_count": summary["actual_csv_data_rows"],
                   "index_entries": summary["index_entries"],
                   "wall_seconds": time.perf_counter() - start,
                   "financial_experiment_run": False}
            receipt.write_bytes(json_bytes(obj))
        print("M5_CCM_SAVED_OUTPUT_RECOMPUTED_" + summary["status"])
        return 0 if summary["status"] == "COMPLETE_CANDIDATE_INDEX_STRUCTURE_ONLY" else 2
    except (IntakeError, FileNotFoundError, KeyError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        code = str(exc) if isinstance(exc, IntakeError) else "INPUT_OR_SAVED_ARTIFACT_INVALID"
        print("M5_CCM_" + code)
        return 2
    except Exception:
        print("M5_CCM_UNEXPECTED_FAILURE")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
