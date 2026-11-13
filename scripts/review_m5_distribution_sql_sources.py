"""Offline, bounded review of two saved WRDS distribution SQL files.

The parser recognizes the exact single-header-join query shape observed in
the two supplied files. It never executes SQL or prints code lists. Unknown
or changed query shapes are rejected rather than given guessed semantics.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts"
HELPER_PATH = ROOT / "scripts" / "audit_m5_distribution_multiyear.py"
HELPER_SHA = "89eeb259889b762d1bcee825598f475c271b87852549b668c1179a2cee51d221"
BASE_PATH = ROOT / "scripts" / "audit_m5_distribution_segment.py"
BASE_SHA = "ab6f37d07289f960232df8be7392acf450121e3ae66ad6c95d7230a0e6f3dec0"
for path, expected in ((HELPER_PATH, HELPER_SHA), (BASE_PATH, BASE_SHA)):
    with path.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != expected:
            raise RuntimeError("BOUND_HELPER_CODE_CHANGED")
helper_spec = importlib.util.spec_from_file_location("bound_m5_merged_audit", HELPER_PATH)
if helper_spec is None or helper_spec.loader is None:
    raise RuntimeError("BOUND_HELPER_NOT_AVAILABLE")
previous = importlib.util.module_from_spec(helper_spec)
helper_spec.loader.exec_module(previous)
HANDOFF_SHA = "eced8906ca8847d1dc42899e7e06507b586f51138e1a8ae44895b9fe445eb56c"
PRIOR_SAFE_ZIP_SHA = "07310ddfd701eef06d02cdf24f3435b31a1b7ed97366decb13266097aa60b81e"
BOUND_TXT_SHA = "017224e43461409fa6f2ef007d182a5b0d870f02cd1edc4c528f0a901a543318"
M5_CANDIDATE_SHA = "f1fa28949353adea6aa179854441b48658415ef577a006fe76bd01c779ddaa38"
EXPECTED_TYPES = ("CD", "CG", "CP", "FRS", "IN", "N/A", "ROC", "SD", "SP", "TSOO")
EXPECTED_FIELDS = (
    "distype", "permno", "disexdt", "primaryexch", "nasdissuno", "siccd",
    "disseqnbr", "disordinaryflg", "disfreqtype", "dispaymenttype",
    "disdetailtype", "distaxtype", "disorigcurtype", "disdivamt",
    "disfacpr", "disfacshr", "disdeclaredt", "disrecorddt", "dispaydt",
    "dispermno", "dispermco", "disamountsourcetype",
)
HEADER_FIELDS = frozenset(("primaryexch", "nasdissuno", "siccd"))
BOUND_TXT = ART / "m5_daily_download_prepare_20261111_v1" / "m5_daily_seed_permnos_upload.txt"
M5_CANDIDATE = ROOT / "configs" / "m5_financial_protocol_round8.json"
PRIOR_SAFE_ZIP = Path("E:/M5_distribution_2020_single_group_review_Codex_results_20261112.zip")
HANDOFF_MEMBER = "10_INPUT_RESULTS/M5_distribution_2020_single_group_review_Codex_results_20261112.zip"
SCOPE = {
    "2019": {
        "query_id": "11900944", "dates": ("2019-11-01", "2019-12-31"),
        "sql_sha256": "2da4d8810b24c357f5c93e63c99eba1d0b33fef544208614555aa0bacdb81cab",
        "sql_name": "asu1cjbn71x8ajp0.sql", "csv_stem": "asu1cjbn71x8ajp0",
        "saved_dir": ART / "m5_distributions_20191101_20191231_20261112_v1",
        "safe_sha256": "17f5512d34588d525ac6e4b686f62f3545bf728ba67572b9f3f65845ba757685",
        "manifest_sha256": "402f71aae7370af5202960d6a8e5ad6016456114890c66ff1efef94292d9c648",
        "parameter_sha256": "3e02709a3779ea87ae36b7058b0aae2ac13f5594667dfab44529984adbac242b",
        "csv_sha256": "2d1232fca547d9aacb121842bf10cc2c69b5513b75ef02ff27628ca02d90b138",
    },
    "2020_2026feb": {
        "query_id": "11901007", "dates": ("2020-01-01", "2026-02-28"),
        "sql_sha256": "7b38c8292ab24f2386767c20331c1168d250f3b1108f3a17f99e060660d98756",
        "sql_name": "zgt8d3w8jqfnulor.sql", "csv_stem": "zgt8d3w8jqfnulor",
        "saved_dir": ART / "m5_distributions_20200101_20260228_20261112_v1",
        "safe_sha256": "ebd24c590ed63b5ea4b9d2b02ea63364be6b53fc173ec7d23dff70a7a56bdf91",
        "manifest_sha256": "2a121219d67aabb181668987745e42e261bce739890fbac26082ea2b64b18a53",
        "parameter_sha256": "90816969a1b41afa40ed37127c2698cbe52099c69ce217659b3be0a22b5b5750",
        "csv_sha256": "e4116783a981161f6bd2da06e9acd3f682cb0d6fdd5eb6da3cb71e56285306f1",
    },
}
SAFE = "source_matrix_safe.json"
PRIVATE = "source_review_private.json"
REGISTRY = "source_registry_private.json"
MANIFEST = "output_manifest_private.json"
EXECUTION = "execution_private.json"

sha = previous.sha
require = previous.require
write_json_new = previous.write_json_new
json_bytes = previous.json_bytes
read_json = previous.read_json
IntakeError = previous.IntakeError

# A full match is intentional: a new predicate, nested query, or clause must
# receive a new review, not silently inherit the conclusions below.
SQL_SHAPE = re.compile(r"""
    \A\s*SELECT\s+(?P<select>.*?)\s+
    FROM\s+crsp_q_stock\.stkdistributions\s+
    INNER\s+JOIN\s*\(\s*SELECT\s+(?P<header_select>.*?)\s+
    FROM\s+crsp_q_stock\.stksecurityinfohdr\s+
    WHERE\s+crsp_q_stock\.stksecurityinfohdr\.permno\s+IN\s*\((?P<codes>.*?)\)\s*
    \)\s+AS\s+id_table\s+
    ON\s+crsp_q_stock\.stkdistributions\.permno\s*=\s*id_table\.permno\s+
    WHERE\s+crsp_q_stock\.stkdistributions\.disexdt\s+BETWEEN\s+
    '(?P<start>\d{4}-\d{2}-\d{2})'::date\s+AND\s+
    '(?P<end>\d{4}-\d{2}-\d{2})'::date\s+
    AND\s*\(\s*"crsp_q_stock"\."stkdistributions"\."distype"\s*=\s*
    ANY\s*\(\s*ARRAY\s*\[(?P<types>.*?)\]\s*\)\s*\)\s*;?\s*\Z
""", re.IGNORECASE | re.DOTALL | re.VERBOSE)
FIELD = re.compile(r"(?i)(crsp_q_stock\.stkdistributions|id_table)\.([a-z][a-z0-9_]*)")
CODE = re.compile(r"'([1-9][0-9]*)'")
TYPE = re.compile(r"'([A-Za-z/]+)'")


def parse_quoted_list(source: str, pattern: re.Pattern[str], code: str) -> list[str]:
    result = []
    for part in source.split(","):
        match = pattern.fullmatch(part.strip())
        require(match is not None, code)
        result.append(match.group(1))
    require(bool(result) and len(set(result)) == len(result), code)
    return result


def parse_sql(data: bytes) -> dict:
    """Review exact statement structure; no SQL text or identifiers returned."""
    try:
        source = data.decode("utf-8-sig", errors="strict")
    except UnicodeError as error:
        raise IntakeError("SQL_NOT_UTF8") from error
    require("\x00" not in source and not re.match(r"\s*<(?:!doctype|html)", source, re.I),
            "SQL_NOT_PLAIN_STATEMENT")
    match = SQL_SHAPE.fullmatch(source)
    require(match is not None, "SQL_SHAPE_NOT_VERIFIED")
    fields = []
    field_sources = []
    for piece in match.group("select").split(","):
        field = FIELD.fullmatch(piece.strip())
        require(field is not None, "SQL_SELECT_NOT_SIMPLE_FIELDS")
        fields.append(field.group(2).lower())
        field_sources.append(field.group(1).lower())
    require(fields == list(EXPECTED_FIELDS), "SQL_SELECT_FIELDS_CHANGED")
    require(all((src == "id_table") == (name in HEADER_FIELDS)
                for name, src in zip(fields, field_sources)), "SQL_FIELD_SOURCE_CHANGED")
    header = [piece.strip().lower() for piece in match.group("header_select").split(",")]
    require(header == ["permno", "primaryexch", "nasdissuno", "siccd"],
            "SQL_HEADER_SUBQUERY_FIELDS_CHANGED")
    codes = parse_quoted_list(match.group("codes"), CODE, "SQL_CODE_LIST_INVALID")
    types = parse_quoted_list(match.group("types"), TYPE, "SQL_TYPE_LIST_INVALID")
    require(types == list(EXPECTED_TYPES), "SQL_TYPE_FILTER_CHANGED")
    return {
        "select_fields": fields,
        "distribution_field_count": len(fields) - len(HEADER_FIELDS),
        "header_field_count": len(HEADER_FIELDS),
        "table_names": ["crsp_q_stock.stkdistributions", "crsp_q_stock.stksecurityinfohdr"],
        "join_type": "INNER_JOIN_HEADER_SUBQUERY",
        "join_on": "stkdistributions.permno = id_table.permno",
        "header_filter": "stksecurityinfohdr.permno IN (inline literals)",
        "date_field": "stkdistributions.disexdt",
        "date_start_inclusive": match.group("start"),
        "date_end_inclusive": match.group("end"),
        "distype_filter_codes": types,
        "inline_list_count": len(codes),
        "inline_list_unique_count": len(set(codes)),
        "inline_list_sha256_ordered_private": hashlib.sha256("\n".join(codes).encode()).hexdigest(),
        "has_additional_clauses_in_exact_shape": False,
        "has_distinct_group_order_limit_in_exact_shape": False,
        "_codes_private": codes,
    }


def upload_ids(path: Path) -> tuple[list[str], str]:
    before = sha(path)
    raw = path.read_bytes()
    require(sha(path) == before, "UPLOAD_CHANGED_DURING_READ")
    require(raw.isascii() and raw.endswith(b"\n") and b"\r" not in raw,
            "UPLOAD_FORMAT_INVALID")
    lines = raw.decode("ascii").splitlines()
    require(all(re.fullmatch(r"[1-9][0-9]*", value) for value in lines)
            and len(lines) == len(set(lines)), "UPLOAD_IDS_INVALID")
    return lines, before


def parameter_status(path: Path, expected_sha: str, parsed: dict) -> dict:
    require(path.is_file() and sha(path) == expected_sha, "PARAMETER_FILE_CHANGED")
    text = path.read_text(encoding="utf-8-sig").lower()
    return {
        "parameter_text_sha256": expected_sha,
        "date_start_present": parsed["date_start_inclusive"] in text,
        "date_end_present": parsed["date_end_inclusive"] in text,
        "disexdt_present": "disexdt" in text,
        "distype_present": "distype" in text,
        "upload_method_marker_present": "file_as_variable_flag" in text,
        "all_selected_field_names_present": all(name in text for name in EXPECTED_FIELDS),
        "all_ten_distype_literals_present": all("'" + value.lower() + "'" in text
                                                  for value in EXPECTED_TYPES),
    }


def inspect_dat(path: Path | None, bound: list[str]) -> dict:
    if path is None:
        return {"status": "NOT_PROVIDED", "sha256": None, "bytes": None,
                "entry_count": None, "unique_count": None, "same_bytes_as_bound_txt": None,
                "same_order_as_bound_txt": None, "same_set_as_bound_txt": None}
    before = sha(path)
    raw = path.read_bytes()
    require(sha(path) == before, "DAT_CHANGED_DURING_READ")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError:
        return {"status": "UNPARSEABLE_ENCODING", "sha256": before,
                "bytes": len(raw), "entry_count": None, "unique_count": None,
                "same_bytes_as_bound_txt": None, "same_order_as_bound_txt": None,
                "same_set_as_bound_txt": None}
    values = text.splitlines()
    if not values or not all(re.fullmatch(r"[1-9][0-9]*", value) for value in values):
        return {"status": "UNPARSEABLE_CODE_FORMAT", "sha256": before,
                "bytes": len(raw), "entry_count": None, "unique_count": None,
                "same_bytes_as_bound_txt": None, "same_order_as_bound_txt": None,
                "same_set_as_bound_txt": None}
    return {"status": ("PARSED_DUPLICATE_CODES_REVIEW_REQUIRED"
                       if len(values) != len(set(values)) else
                       "PARSED_LOCAL_DAT_UNATTESTED_SERVER_LINK"),
            "sha256": before, "bytes": len(raw), "encoding": "utf-8-sig",
            "line_endings": "CRLF" if b"\r\n" in raw else "LF",
            "final_newline": raw.endswith((b"\n", b"\r\n")),
            "entry_count": len(values), "unique_count": len(set(values)),
            "same_bytes_as_bound_txt": False,
            "same_order_as_bound_txt": values == bound,
            "same_set_as_bound_txt": set(values) == set(bound)}


def review_sources(sql_paths: dict[str, Path], dat_paths: dict[str, Path | None],
                   *, handoff: Path, prior_safe: Path = PRIOR_SAFE_ZIP,
                   bound_txt: Path = BOUND_TXT) -> tuple[dict, dict]:
    require(sha(HELPER_PATH) == HELPER_SHA and sha(BASE_PATH) == BASE_SHA and
            Path(previous.__file__).resolve() == HELPER_PATH.resolve(),
            "BOUND_HELPER_CODE_CHANGED")
    require(handoff.is_file() and sha(handoff) == HANDOFF_SHA, "HANDOFF_CHANGED")
    with zipfile.ZipFile(handoff) as archive:
        require(archive.testzip() is None, "HANDOFF_ZIP_CRC_FAILED")
        package_manifest = json.loads(archive.read("06_FILE_MANIFEST.json"))
        entries = package_manifest.get("files")
        require(package_manifest.get("schema") ==
                "m5_distribution_closeout_sources_handoff_manifest_v1"
                and isinstance(entries, dict)
                and set(entries) == set(archive.namelist()) - {"06_FILE_MANIFEST.json"},
                "HANDOFF_MANIFEST_CHANGED")
        for name, metadata in entries.items():
            member = archive.read(name)
            require(len(member) == metadata["bytes"] and
                    hashlib.sha256(member).hexdigest() == metadata["sha256"],
                    "HANDOFF_MEMBER_CHANGED")
        nested = archive.read(HANDOFF_MEMBER)
        require(hashlib.sha256(nested).hexdigest() == PRIOR_SAFE_ZIP_SHA,
                "HANDOFF_PRIOR_SAFE_ZIP_CHANGED")
    require(prior_safe.is_file() and sha(prior_safe) == PRIOR_SAFE_ZIP_SHA,
            "LOCAL_PRIOR_SAFE_ZIP_CHANGED")
    require(M5_CANDIDATE.is_file() and sha(M5_CANDIDATE) == M5_CANDIDATE_SHA,
            "M5_CANDIDATE_CHANGED")
    bound, bound_hash = upload_ids(bound_txt)
    require(bound_hash == BOUND_TXT_SHA and len(bound) == 7639,
            "BOUND_UPLOAD_IDENTITY_CHANGED")
    matrix = {}
    private = {}
    normalized_sql = {}
    ordered_code_hashes = {}
    for scope, spec in SCOPE.items():
        sql_path = sql_paths[scope]
        require(sql_path.is_file() and sha(sql_path) == spec["sql_sha256"],
                "SQL_FILE_IDENTITY_CHANGED")
        raw = sql_path.read_bytes()
        require(sha(sql_path) == spec["sql_sha256"], "SQL_CHANGED_DURING_READ")
        parsed = parse_sql(raw)
        require((parsed["date_start_inclusive"], parsed["date_end_inclusive"]) ==
                spec["dates"], "SQL_DATE_SCOPE_CHANGED")
        require(parsed["inline_list_count"] == len(bound), "SQL_LIST_COUNT_CHANGED")
        codes = parsed.pop("_codes_private")
        ordered_code_hashes[scope] = parsed.pop("inline_list_sha256_ordered_private")
        list_status = {"same_set": set(codes) == set(bound),
                       "same_order": codes == bound}
        require(list_status["same_set"], "SQL_INLINE_LIST_SET_MISMATCH")
        saved = spec["saved_dir"]
        safe_path = saved / "summary_safe.json"
        saved_manifest_path = saved / "output_manifest_private.json"
        require(saved_manifest_path.is_file() and
                sha(saved_manifest_path) == spec["manifest_sha256"],
                "PRIOR_SAVED_MANIFEST_CHANGED")
        saved_manifest = read_json(saved_manifest_path)
        csv_path = saved / "raw" / (spec["csv_stem"] + ".csv")
        require(csv_path.is_file() and sha(csv_path) == spec["csv_sha256"] and
                saved_manifest["saved_file_sha256"]["raw/" + csv_path.name] ==
                spec["csv_sha256"], "PRIOR_SAVED_CSV_CHANGED")
        parameter_path = saved / "package" / "02_PARAMETERS_USER_PROVIDED.txt"
        require(safe_path.is_file() and sha(safe_path) == spec["safe_sha256"],
                "PRIOR_SAFE_SUMMARY_CHANGED")
        summary = read_json(safe_path)
        csv_header = (summary.get("csv_header") if scope == "2019"
                      else summary.get("csv_header_original_order"))
        require([name.lower() for name in csv_header] == parsed["select_fields"]
                and summary.get("source_csv_sha256") == spec["csv_sha256"]
                and summary.get("requested_disexdt_window_inclusive") == list(spec["dates"])
                and summary.get("query_id_from_user_page" if scope == "2019"
                                else "query_id_from_visible_result") == spec["query_id"],
                "SQL_SAVED_EXTRACT_SCOPE_OR_FIELDS_MISMATCH")
        params = parameter_status(parameter_path, spec["parameter_sha256"], parsed)
        require(all(value is True for key, value in params.items()
                    if key != "parameter_text_sha256"),
                "SQL_PARAMETER_SCOPE_MISMATCH")
        dat = inspect_dat(dat_paths[scope], bound)
        if dat_paths[scope] is not None and dat["status"].startswith("PARSED"):
            dat["same_bytes_as_bound_txt"] = sha(dat_paths[scope]) == bound_hash
        matrix[scope] = {
            "query_id_from_prior_user_page": spec["query_id"],
            "local_sql_file_name": spec["sql_name"],
            "local_sql_sha256": spec["sql_sha256"],
            "local_sql_size_bytes": len(raw),
            "sql_content_status": "EXACT_STATIC_TEMPLATE_REVIEWED",
            "native_download_origin": "NOT_ATTESTED_NO_SOURCE_NOTE",
            "query_page_association": "PROVISIONAL_MATCH_BY_STEM_SCOPE_FIELDS_NOT_EMBEDDED_QUERY_ID",
            "sql_structure": parsed,
            "inline_list_matches_bound_txt": list_status,
            "bound_txt_sha256": bound_hash,
            "bound_txt_identifier_count": len(bound),
            "saved_csv_sha256": spec["csv_sha256"],
            "saved_manifest_sha256": spec["manifest_sha256"],
            "saved_safe_summary_sha256": spec["safe_sha256"],
            "saved_parameter_text": params,
            "parameter_comparison_scope":
                "BOUND_USER_TRANSCRIPT_TOKEN_PRESENCE_NOT_NATIVE_PARAMETER_PROOF",
            "server_dat": dat,
            "sql_to_historical_execution": "NOT_VERIFIED_NO_EXECUTION_LOG",
            "provider_release": "UNKNOWN",
            "license_or_export_scope": "UNKNOWN",
            "supplier_universe_completeness": "NOT_VERIFIED",
            "formal_m5_admission": False,
        }
        private[scope] = {
            "sql_source_path": str(sql_path.resolve()),
            "dat_source_path": str(dat_paths[scope].resolve()) if dat_paths[scope] else None,
            "safe_association_basis": ["file_stem_matches_saved_csv_stem",
                                       "date_window_matches_prior_page_and_saved_summary",
                                       "selected_fields_match_saved_header"],
            "inline_list_sha256_ordered": ordered_code_hashes[scope],
        }
        normalized_sql[scope] = re.sub(
            r"'\d{4}-\d{2}-\d{2}'::date", "'<DATE>'::date",
            raw.decode("utf-8-sig"))
    matrix["cross_query"] = {
        "same_22_selected_fields_and_order":
            matrix["2019"]["sql_structure"]["select_fields"] ==
            matrix["2020_2026feb"]["sql_structure"]["select_fields"],
        "same_inline_list_order":
            ordered_code_hashes["2019"] == ordered_code_hashes["2020_2026feb"],
        "statements_identical_after_replacing_two_date_literals":
            normalized_sql["2019"] == normalized_sql["2020_2026feb"],
        "distribution_sql_does_not_prove_daily_join": True,
    }
    return {"schema": "m5_distribution_sql_source_matrix_safe_v1",
            "scope": "ENGINEERING_ONLY_SOURCE_REVIEW",
            "queries": matrix,
            "source_note_status": "NOT_PROVIDED",
            "server_dat_query_attachment_status": (
                "NOT_PROVIDED" if all(value is None for value in dat_paths.values())
                else "LOCAL_DAT_PRESENT_SERVER_QUERY_LINK_UNATTESTED"),
            "m5_status": "PROPOSED_RESEARCHER_NOT_ACCEPTED_NOT_RUN",
            "handoff_zip_sha256": HANDOFF_SHA,
            "prior_safe_zip_sha256": PRIOR_SAFE_ZIP_SHA,
            "candidate_sha256": M5_CANDIDATE_SHA,
            "helper_code_sha256": HELPER_SHA,
            "base_code_sha256": BASE_SHA,
            "review_code_sha256": sha(Path(__file__))}, private


def run(output: Path, handoff: Path, sql_paths: dict[str, Path],
        dat_paths: dict[str, Path | None]) -> dict:
    previous.base.ignored_output(output)
    require(not output.exists(), "OUTPUT_ALREADY_EXISTS")
    started = datetime.now(timezone.utc).isoformat()
    output.mkdir(parents=True, exist_ok=False)
    try:
        safe, private = review_sources(sql_paths, dat_paths, handoff=handoff)
        source_dir = output / "private_sources"
        source_dir.mkdir()
        for scope in SCOPE:
            shutil.copyfile(sql_paths[scope], source_dir / f"{scope}.sql")
            if dat_paths[scope] is not None:
                shutil.copyfile(dat_paths[scope], source_dir / f"{scope}.dat")
        write_json_new(output / SAFE, safe)
        write_json_new(output / PRIVATE, private)
        registry = {
            "schema": "m5_distribution_sql_source_registry_private_v1",
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "handoff_path": str(handoff.resolve()),
            "sql_paths": {scope: str(sql_paths[scope].resolve()) for scope in SCOPE},
            "dat_paths": {scope: str(dat_paths[scope].resolve()) if dat_paths[scope]
                          else None for scope in SCOPE},
            "review_code_sha256": sha(Path(__file__)),
            "helper_code_sha256": HELPER_SHA,
            "base_code_sha256": BASE_SHA,
            "python_executable": sys.executable,
            "wrds_queries_submitted": 0,
            "sql_executed": False,
            "original_sources_overwritten": False,
        }
        write_json_new(output / REGISTRY, registry)
        paths = [output / name for name in (SAFE, PRIVATE, REGISTRY)]
        paths += [path for path in source_dir.iterdir() if path.is_file()]
        manifest = {
            "schema": "m5_distribution_sql_source_manifest_private_v1",
            "review_code_sha256": sha(Path(__file__)),
            "helper_code_sha256": HELPER_SHA,
            "base_code_sha256": BASE_SHA,
            "source_handoff_sha256": sha(handoff),
            "saved_file_sha256": {str(path.relative_to(output)).replace("\\", "/"): sha(path)
                                  for path in paths},
        }
        write_json_new(output / MANIFEST, manifest)
        verify_output(output)
        write_json_new(output / EXECUTION, {
            "schema": "m5_distribution_sql_execution_private_v1",
            "started_at_utc": started,
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            "run_exit_code": 0,
            "internal_verify_exit_code": 0,
            "manifest_sha256": sha(output / MANIFEST),
            "peak_memory": "UNKNOWN_NOT_MEASURED",
        })
        return safe
    except Exception as error:
        write_json_new(output / "failure_private.json", {
            "schema": "m5_distribution_sql_failure_private_v1",
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "error_code": error.code if isinstance(error, IntakeError)
                          else "UNHANDLED_SOURCE_OR_IO_ERROR",
            "error_type": type(error).__name__,
        })
        raise


def verify_output(output: Path) -> dict:
    previous.base.ignored_output(output)
    manifest = read_json(output / MANIFEST)
    registry = read_json(output / REGISTRY)
    require(manifest.get("schema") == "m5_distribution_sql_source_manifest_private_v1"
            and registry.get("schema") == "m5_distribution_sql_source_registry_private_v1"
            and manifest.get("review_code_sha256") == sha(Path(__file__))
            and registry.get("review_code_sha256") == sha(Path(__file__))
            and manifest.get("helper_code_sha256") == HELPER_SHA
            and registry.get("helper_code_sha256") == HELPER_SHA
            and manifest.get("base_code_sha256") == BASE_SHA
            and registry.get("base_code_sha256") == BASE_SHA,
            "OUTPUT_CODE_OR_SCHEMA_CHANGED")
    for relative, expected in manifest["saved_file_sha256"].items():
        require(previous.base.safe_member_name(relative), "SAVED_PATH_INVALID")
        path = (output / relative).resolve()
        require(output.resolve() in path.parents and path.is_file() and sha(path) == expected,
                "SAVED_FILE_CHANGED")
    source_paths = {scope: Path(registry["sql_paths"][scope]) for scope in SCOPE}
    dat_paths = {scope: (Path(registry["dat_paths"][scope])
                         if registry["dat_paths"][scope] else None) for scope in SCOPE}
    safe, private = review_sources(source_paths, dat_paths,
                                   handoff=Path(registry["handoff_path"]))
    require(json_bytes(safe) == (output / SAFE).read_bytes()
            and json_bytes(private) == (output / PRIVATE).read_bytes(),
            "SAVED_DERIVATION_CHANGED")
    for scope in SCOPE:
        saved_sql = output / "private_sources" / f"{scope}.sql"
        require(sha(source_paths[scope]) == sha(saved_sql),
                "SQL_COPY_DIFFERS_FROM_SOURCE")
        require(parse_sql(saved_sql.read_bytes()) == parse_sql(source_paths[scope].read_bytes()),
                "SAVED_SQL_STRUCTURE_CHANGED")
        if dat_paths[scope] is not None:
            saved_dat = output / "private_sources" / f"{scope}.dat"
            require(sha(dat_paths[scope]) == sha(saved_dat),
                    "DAT_COPY_DIFFERS_FROM_SOURCE")
    if (output / EXECUTION).exists():
        execution = read_json(output / EXECUTION)
        require(execution.get("schema") == "m5_distribution_sql_execution_private_v1"
                and execution.get("run_exit_code") == 0
                and execution.get("internal_verify_exit_code") == 0
                and execution.get("manifest_sha256") == sha(output / MANIFEST),
                "EXECUTION_LEDGER_CHANGED")
    return safe


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "verify-output"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--handoff-zip", type=Path)
    parser.add_argument("--sql-2019", type=Path)
    parser.add_argument("--sql-merged", type=Path)
    parser.add_argument("--dat-2019", type=Path)
    parser.add_argument("--dat-merged", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "run":
            require(args.handoff_zip is not None and args.sql_2019 is not None
                    and args.sql_merged is not None, "RUN_INPUTS_REQUIRED")
            result = run(args.output_dir, args.handoff_zip,
                         {"2019": args.sql_2019, "2020_2026feb": args.sql_merged},
                         {"2019": args.dat_2019, "2020_2026feb": args.dat_merged})
            prefix = "M5_DISTRIBUTION_SQL_SOURCE_REVIEW_SAVED_"
        else:
            result = verify_output(args.output_dir)
            prefix = "M5_DISTRIBUTION_SQL_SOURCE_REVIEW_SAVED_OUTPUT_VERIFIED_"
        require(all(row["sql_content_status"] == "EXACT_STATIC_TEMPLATE_REVIEWED"
                    for key, row in result["queries"].items() if key in SCOPE),
                "SQL_REVIEW_INCOMPLETE")
        print(prefix + "CONTENT_ONLY_PAGE_ASSOCIATION_PROVISIONAL")
        return 0
    except Exception:
        print("M5_DISTRIBUTION_SQL_SOURCE_REVIEW_REQUIRED")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
