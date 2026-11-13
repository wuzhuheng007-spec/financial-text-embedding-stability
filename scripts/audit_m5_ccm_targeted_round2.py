"""Targeted, offline CCM representation audit over a bound saved index.

The complete relation index is read but never rebuilt. Raw tokens, rows,
identifiers, paths, and source locators remain in Git-ignored local files.
Only bounded representation categories are written to the safe summary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import audit_m5_ccm_query11905330 as prior


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_ROWS = 123_388
EXPECTED_OTHER = 4
EXPECTED_UNPARSED_END = 26_386
EXPECTED_SOURCE_ZIP_SHA256 = prior.SOURCE_ZIP_SHA256
EXPECTED_SOURCE_CSV_SHA256 = "ad5481feb396a47e265951eacd59b4ff7cb84dba9e74e43e80392c0db836bfe9"
EXPECTED_INDEX_SHA256 = "5d990d3d2dba1eb7203cbeebd24638e0b53923b48d8c5dbe50bb65c36b4e2879"
PRIVATE_NAME = "m5_ccm_targeted_rows_private.jsonl"
SAFE_NAME = "m5_ccm_targeted_summary_safe.json"
REGISTRY_NAME = "m5_ccm_targeted_registry_private.json"
MANIFEST_NAME = "m5_ccm_targeted_manifest_private.json"
VERIFY_NAME = "m5_ccm_targeted_verify_safe.json"
CONFIG = {"schema": "m5_ccm_targeted_representation_config_v1",
          "expected_rows": EXPECTED_ROWS,
          "expected_linktype_other": EXPECTED_OTHER,
          "expected_unparsed_end": EXPECTED_UNPARSED_END,
          "linktype_allowed": list(prior.LINKTYPE_CODES),
          "endpoint_status": "UNPARSED_DATE_REPRESENTATION",
          "semantic_promotion": False}


class TargetError(ValueError):
    """Fixed diagnostic code, without any source values or paths."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise TargetError(code)


def digest(path: Path) -> str:
    return prior.sha256_file(path)


def config_sha256() -> str:
    return hashlib.sha256(prior.json_bytes(CONFIG)).hexdigest()


def code_identity() -> dict:
    require(sys.version_info[:2] == (3, 11), "PYTHON_VERSION_MISMATCH")
    require(Path(prior.__file__).resolve() == (ROOT / "scripts/audit_m5_ccm_query11905330.py").resolve(),
            "PRIOR_CODE_IMPORT_PATH_MISMATCH")
    require(Path(prior.old_intake.__file__).resolve() ==
            (ROOT / "scripts/audit_m5_wrds_intake.py").resolve(),
            "DECODER_IMPORT_PATH_MISMATCH")
    require(digest(ROOT / "configs/m5_protocol_company_proxy_conditional_20261113.json") ==
            prior.CANDIDATE_SHA256, "CANDIDATE_HASH_MISMATCH")
    return {"targeted_script_sha256": digest(Path(__file__)),
            "prior_intake_script_sha256": digest(Path(prior.__file__)),
            "decoder_script_sha256": digest(Path(prior.old_intake.__file__)),
            "candidate_sha256": prior.CANDIDATE_SHA256,
            "python_executable_sha256": digest(Path(sys.executable)),
            "python_version": sys.version.split()[0],
            "config_sha256": config_sha256()}


def _bound_identity(saved: Path) -> dict:
    """Check existing saved identities without re-running the intake."""
    saved = prior.ensure_ignored(saved)
    manifest = prior.load_json(saved / prior.MANIFEST_NAME)
    require(manifest.get("schema") == "m5_ccm_saved_manifest_private_v1" and
            set(manifest.get("files", {})) == {prior.INDEX_NAME, prior.EXCEPTIONS_NAME,
                                             prior.SUMMARY_NAME, prior.REGISTRY_NAME},
            "PRIOR_MANIFEST_INVALID")
    for name, identity in manifest["files"].items():
        path = saved / name
        require(path.is_file() and path.stat().st_size == identity.get("bytes") and
                digest(path) == identity.get("sha256"), "PRIOR_FILE_HASH_MISMATCH")
    registry = prior.load_json(saved / prior.REGISTRY_NAME)
    summary = prior.load_json(saved / prior.SUMMARY_NAME)
    require(manifest["files"][prior.INDEX_NAME]["sha256"] == EXPECTED_INDEX_SHA256,
            "PRIOR_INDEX_EXPECTED_HASH_MISMATCH")
    require(registry.get("schema") == "m5_ccm_local_registry_private_v1" and
            summary.get("schema") == "m5_ccm_query11905330_safe_summary_v1" and
            summary.get("status") == "COMPLETE_CANDIDATE_INDEX_STRUCTURE_ONLY",
            "PRIOR_RECEIPT_STATUS_MISMATCH")
    require(registry.get("code_identity", {}).get("intake_script_sha256") ==
            digest(Path(prior.__file__)) == summary.get("intake_code_sha256") and
            registry.get("code_identity", {}).get("id_decoder_script_sha256") ==
            digest(Path(prior.old_intake.__file__)) == summary.get("id_decoder_code_sha256"),
            "PRIOR_CODE_IDENTITY_MISMATCH")
    source = registry.get("source_bundle", {})
    require(source.get("source_zip_sha256") == summary.get("source_zip_sha256") ==
            EXPECTED_SOURCE_ZIP_SHA256 and
            source.get("source_csv_sha256") == summary.get("source_csv_sha256") ==
            EXPECTED_SOURCE_CSV_SHA256, "PRIOR_SOURCE_BINDING_MISMATCH")
    for key, expected in (("source_zip_path", EXPECTED_SOURCE_ZIP_SHA256),
                          ("source_csv_path", EXPECTED_SOURCE_CSV_SHA256),
                          ("outer_zip_path", summary.get("handoff_outer_zip_sha256"))):
        path = Path(source[key])
        require(path.is_file() and digest(path) == expected,
                "PRIOR_SOURCE_FILE_HASH_MISMATCH")
    packet = Path(source["packet_dir"])
    require(digest(packet / "FILE_HASHES.json") ==
            summary.get("handoff_manifest_sha256"), "PRIOR_PACKET_HASH_MISMATCH")
    seed = registry.get("seed_binding", {})
    for key, sha_key in (("seed_txt_path", "seed_txt_sha256"),
                         ("seed_registry_path", "seed_registry_sha256"),
                         ("seed_plan_path", "seed_plan_sha256"),
                         ("upstream_seed_json_path", "upstream_seed_json_sha256")):
        if key == "upstream_seed_json_path" and key not in seed:
            continue
        path = Path(seed[key])
        require(path.is_file() and digest(path) == seed.get(sha_key),
                "PRIOR_SEED_FILE_HASH_MISMATCH")
    require(summary.get("actual_csv_data_rows") == summary.get("index_entries") ==
            EXPECTED_ROWS and summary.get("linktype_allowlisted_frequency", {}).get("OTHER") ==
            EXPECTED_OTHER and
            summary.get("date_status_counts", {}).get("linkenddt", {}).get(
                "UNPARSED_DATE_REPRESENTATION") == EXPECTED_UNPARSED_END,
            "PRIOR_AGGREGATE_MISMATCH")
    mapping = registry.get("header_mapping")
    require(isinstance(mapping, dict) and
            all(isinstance(mapping.get(k), int) for k in prior.EXPECTED) and
            len(set(mapping.values())) == len(prior.EXPECTED),
            "PRIOR_HEADER_MAPPING_INVALID")
    return {"saved": saved, "manifest": manifest, "registry": registry,
            "summary": summary, "mapping": mapping,
            "source_csv_sha256": EXPECTED_SOURCE_CSV_SHA256,
            "source_zip_sha256": EXPECTED_SOURCE_ZIP_SHA256,
            "source_binding_id": registry.get("source_binding_id"),
            "index_sha256": EXPECTED_INDEX_SHA256,
            "index_bytes": manifest["files"][prior.INDEX_NAME]["bytes"],
            "prior_manifest_sha256": digest(saved / prior.MANIFEST_NAME),
            "prior_registry_sha256": digest(saved / prior.REGISTRY_NAME),
            "prior_summary_sha256": digest(saved / prior.SUMMARY_NAME)}


def endpoint_category(raw: str) -> str:
    """Describe text shape only; do not infer an open endpoint."""
    value = raw.strip()
    if re.fullmatch(r"[A-Za-z]", value, re.ASCII):
        return "SINGLE_ASCII_LETTER"
    if re.fullmatch(r"\.[A-Za-z]", value, re.ASCII):
        return "DOT_ASCII_LETTER"
    if re.fullmatch(r"[0-9]{8}", value, re.ASCII):
        return "EIGHT_DIGIT_SHAPE"
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value, re.ASCII):
        return "ISO_DATE_SHAPE"
    if re.fullmatch(r"\.[^\s]+", value, re.ASCII):
        return "DOT_PREFIX_OTHER"
    if value.isascii() and value.isalnum():
        return "OTHER_ASCII_ALPHANUMERIC"
    if value.isascii():
        return "OTHER_ASCII_TEXT"
    return "OTHER_UNICODE_TEXT"


def scan_index(index: Path, ledger: Path, mapping: dict[str, int],
               *, expected_rows: int = EXPECTED_ROWS,
               expected_other: int = EXPECTED_OTHER,
               expected_unparsed: int = EXPECTED_UNPARSED_END,
               expected_source_zip: str = EXPECTED_SOURCE_ZIP_SHA256,
               expected_source_csv: str = EXPECTED_SOURCE_CSV_SHA256,
               expected_binding_id: str | None = None) -> dict:
    """Derive both target classes in one streaming pass over the saved index."""
    require(not ledger.exists(), "TARGET_LEDGER_ALREADY_EXISTS")
    token_categories: dict[str, str] = {}
    type_counts: Counter[str] = Counter()
    endpoint_counts: Counter[str] = Counter()
    rows = other = unparsed = both = 0
    try:
        with index.open("r", encoding="utf-8", errors="strict") as src, \
             ledger.open("wb") as dst:
            for line in src:
                rows += 1
                record = json.loads(line)
                require(isinstance(record, dict) and
                        record.get("schema") == "m5_ccm_complete_relation_row_v1" and
                        record.get("row_ordinal") == rows and
                        record.get("source_zip_sha256") == expected_source_zip and
                        record.get("source_csv_sha256") == expected_source_csv and
                        (expected_binding_id is None or
                         record.get("source_binding_id") == expected_binding_id) and
                        record.get("relation_status") == "CANDIDATE_NOT_ADMITTED",
                        "INDEX_ROW_IDENTITY_MISMATCH")
                cells = record.get("raw_cells")
                require(isinstance(cells, list) and len(cells) == len(prior.EXPECTED) and
                        all(isinstance(v, str) for v in cells), "INDEX_RAW_CELLS_INVALID")
                raw_type = cells[mapping["linktype"]]
                raw_end = cells[mapping["linkenddt"]]
                saved_type = record.get("link_cells", {}).get("linktype")
                saved_end = record.get("parsed_dates", {}).get("linkenddt", {})
                recomputed_end = prior.parse_endpoint(raw_end)
                require(saved_type == raw_type and saved_end == recomputed_end and
                        record.get("link_cells", {}).get("linkenddt") == raw_end,
                        "INDEX_SAVED_PARSE_MISMATCH")
                is_other = prior.code_bucket(raw_type, prior.LINKTYPE_CODES) == "OTHER"
                is_unparsed = recomputed_end["status"] == "UNPARSED_DATE_REPRESENTATION"
                other += int(is_other)
                unparsed += int(is_unparsed)
                both += int(is_other and is_unparsed)
                if not (is_other or is_unparsed):
                    continue
                normalized = raw_type.strip().upper()
                type_label = None
                if is_other:
                    if normalized not in token_categories:
                        token_categories[normalized] = f"UNDEFINED_LINKTYPE_{len(token_categories) + 1}"
                    type_label = token_categories[normalized]
                    type_counts[type_label] += 1
                end_label = endpoint_category(raw_end) if is_unparsed else None
                if end_label:
                    endpoint_counts[end_label] += 1
                private = {"schema": "m5_ccm_targeted_private_row_v1",
                           "source_row_ordinal": rows,
                           "source_binding_id": record.get("source_binding_id"),
                           "source_zip_sha256": expected_source_zip,
                           "source_csv_sha256": expected_source_csv,
                           "linktype_other": is_other,
                           "linkenddt_unparsed": is_unparsed,
                           "raw_linktype": raw_type,
                           "normalized_linktype": normalized,
                           "raw_linkenddt": raw_end,
                           "parsed_linkenddt": recomputed_end,
                           "type_category": type_label,
                           "end_representation_category": end_label,
                           "raw_cells_if_linktype_other": cells if is_other else None,
                           "semantic_status": "UNKNOWN_NO_SOURCE_BOUND_EXPORT_DEFINITION"}
                dst.write(prior.canonical_line(private))
    except (UnicodeError, json.JSONDecodeError, TypeError, IndexError, KeyError,
            AttributeError) as exc:
        raise TargetError("INDEX_STREAM_INVALID") from exc
    require(rows == expected_rows, "INDEX_ROW_COUNT_MISMATCH")
    require(other == expected_other, "LINKTYPE_OTHER_COUNT_MISMATCH")
    require(unparsed == expected_unparsed, "UNPARSED_ENDPOINT_COUNT_MISMATCH")
    require(sum(type_counts.values()) == other and sum(endpoint_counts.values()) == unparsed,
            "CATEGORY_ACCOUNTING_MISMATCH")
    return {"schema": "m5_ccm_targeted_representation_summary_safe_v1",
            "scope": "ENGINEERING_ONLY_NOT_FORMAL_M5",
            "status": "TARGETED_REPRESENTATIONS_CLASSIFIED_SEMANTICS_UNKNOWN",
            "source_record_count": rows,
            "linktype_other_source_records": other,
            "unparsed_end_source_records": unparsed,
            "overlap_source_records": both,
            "union_target_source_records": other + unparsed - both,
            "linktype_other_representation_category_count": len(type_counts),
            "linktype_other_category_frequency": dict(sorted(type_counts.items())),
            "linktype_other_categories": {
                label: {"source_records": count,
                        "source_definition_status": "NOT_SUPPLIED_SOURCE_BOUND"}
                for label, count in sorted(type_counts.items())},
            "linktype_other_definition_status": "NOT_SUPPLIED_SOURCE_BOUND",
            "unparsed_end_representation_category_count": len(endpoint_counts),
            "unparsed_end_category_frequency": dict(sorted(endpoint_counts.items())),
            "unparsed_end_categories": {
                label: {"source_records": count,
                        "source_definition_status": "NOT_SUPPLIED_SOURCE_BOUND"}
                for label, count in sorted(endpoint_counts.items())},
            "unparsed_end_definition_status": "NOT_SUPPLIED_SOURCE_BOUND",
            "raw_linktype_preserved_source_records": other,
            "raw_linkenddt_preserved_source_records": unparsed,
            "full_raw_cells_preserved_other_records": other,
            "supported_open_endpoint_rows": 0,
            "formal_company_count": None,
            "formal_event_count": None,
            "historical_identity_adjudicated": False,
            "financial_experiment_run": False}


def _augment(summary: dict, bound: dict, code: dict) -> dict:
    result = dict(summary)
    result.update({"query_id": prior.QUERY_ID,
                   "source_zip_sha256": bound["source_zip_sha256"],
                   "source_csv_sha256": bound["source_csv_sha256"],
                   "saved_complete_index_sha256": bound["index_sha256"],
                   "saved_complete_index_bytes": bound["index_bytes"],
                   "prior_manifest_sha256": bound["prior_manifest_sha256"],
                   "prior_registry_sha256": bound["prior_registry_sha256"],
                   "prior_summary_sha256": bound["prior_summary_sha256"],
                   "targeted_code_sha256": code["targeted_script_sha256"],
                   "prior_intake_code_sha256": code["prior_intake_script_sha256"],
                   "decoder_code_sha256": code["decoder_script_sha256"],
                   "candidate_sha256": code["candidate_sha256"],
                   "config_sha256": code["config_sha256"],
                   "source_bound_definitions": False,
                   "native_final_parameters": "NOT_SUPPLIED",
                   "native_sql": "NOT_SUPPLIED"})
    return result


def run(saved: Path, output: Path) -> dict:
    output = prior.ensure_ignored(output)
    require(not output.exists(), "OUTPUT_ALREADY_EXISTS")
    bound = _bound_identity(saved)
    code = code_identity()
    output.mkdir(parents=True)
    raw = scan_index(bound["saved"] / prior.INDEX_NAME, output / PRIVATE_NAME,
                     bound["mapping"],
                     expected_rows=CONFIG["expected_rows"],
                     expected_other=CONFIG["expected_linktype_other"],
                     expected_unparsed=CONFIG["expected_unparsed_end"],
                     expected_source_zip=bound["source_zip_sha256"],
                     expected_source_csv=bound["source_csv_sha256"],
                     expected_binding_id=bound["source_binding_id"])
    safe = _augment(raw, bound, code)
    (output / SAFE_NAME).write_bytes(prior.json_bytes(safe))
    registry = {"schema": "m5_ccm_targeted_registry_private_v1",
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "python_executable": sys.executable,
                "python_version": sys.version,
                "command": sys.argv,
                "saved_prior_intake_path": str(bound["saved"]),
                "saved_prior_intake_manifest_sha256": bound["prior_manifest_sha256"],
                "saved_prior_index_sha256": bound["index_sha256"],
                "source_zip_sha256": bound["source_zip_sha256"],
                "source_csv_sha256": bound["source_csv_sha256"],
                "code_identity": code,
                "config": CONFIG}
    (output / REGISTRY_NAME).write_bytes(prior.json_bytes(registry))
    manifest = {"schema": "m5_ccm_targeted_saved_manifest_private_v1",
                "files": {name: {"sha256": digest(output / name),
                                 "bytes": (output / name).stat().st_size}
                          for name in (PRIVATE_NAME, SAFE_NAME, REGISTRY_NAME)}}
    (output / MANIFEST_NAME).write_bytes(prior.json_bytes(manifest))
    return safe


def verify(output: Path) -> dict:
    output = prior.ensure_ignored(output)
    manifest = prior.load_json(output / MANIFEST_NAME)
    require(manifest.get("schema") == "m5_ccm_targeted_saved_manifest_private_v1" and
            set(manifest.get("files", {})) == {PRIVATE_NAME, SAFE_NAME, REGISTRY_NAME},
            "TARGETED_MANIFEST_INVALID")
    for name, identity in manifest["files"].items():
        path = output / name
        require(path.is_file() and path.stat().st_size == identity.get("bytes") and
                digest(path) == identity.get("sha256"), "TARGETED_FILE_HASH_MISMATCH")
    registry = prior.load_json(output / REGISTRY_NAME)
    require(registry.get("schema") == "m5_ccm_targeted_registry_private_v1" and
            registry.get("code_identity") == code_identity() and
            registry.get("config") == CONFIG,
            "TARGETED_REGISTRY_IDENTITY_MISMATCH")
    bound = _bound_identity(Path(registry["saved_prior_intake_path"]))
    require(registry["saved_prior_intake_manifest_sha256"] == bound["prior_manifest_sha256"] and
            registry["saved_prior_index_sha256"] == bound["index_sha256"] and
            registry["source_zip_sha256"] == bound["source_zip_sha256"] and
            registry["source_csv_sha256"] == bound["source_csv_sha256"],
            "TARGETED_SOURCE_BINDING_MISMATCH")
    saved_safe = prior.load_json(output / SAFE_NAME)
    with tempfile.TemporaryDirectory(prefix="m5_ccm_target_verify_", dir=output.parent) as directory:
        rebuilt_private = Path(directory) / PRIVATE_NAME
        rebuilt_safe = _augment(scan_index(bound["saved"] / prior.INDEX_NAME,
                                           rebuilt_private, bound["mapping"],
                                           expected_rows=CONFIG["expected_rows"],
                                           expected_other=CONFIG["expected_linktype_other"],
                                           expected_unparsed=CONFIG["expected_unparsed_end"],
                                           expected_source_zip=bound["source_zip_sha256"],
                                           expected_source_csv=bound["source_csv_sha256"],
                                           expected_binding_id=bound["source_binding_id"]),
                                bound, code_identity())
        require(prior.json_bytes(rebuilt_safe) == (output / SAFE_NAME).read_bytes() and
                digest(rebuilt_private) == manifest["files"][PRIVATE_NAME]["sha256"],
                "TARGETED_RECOMPUTE_MISMATCH")
    require(saved_safe == rebuilt_safe, "TARGETED_SAFE_SUMMARY_MISMATCH")
    receipt = output / VERIFY_NAME
    if receipt.exists():
        old = prior.load_json(receipt)
        require(old.get("schema") == "m5_ccm_targeted_independent_verify_safe_v1" and
                old.get("status") == "PASS_TARGETED_SAVED_OUTPUT_RECOMPUTED" and
                old.get("safe_summary_sha256") == digest(output / SAFE_NAME) and
                old.get("manifest_sha256") == digest(output / MANIFEST_NAME) and
                old.get("targeted_code_sha256") == digest(Path(__file__)),
                "TARGETED_VERIFY_RECEIPT_MISMATCH")
    return saved_safe


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--saved-intake", type=Path, required=True)
    run_parser.add_argument("--output", type=Path, required=True)
    verify_parser = commands.add_parser("verify-output")
    verify_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            run(args.saved_intake, args.output)
            print("M5_CCM_TARGETED_REPRESENTATION_AUDIT_COMPLETE_SEMANTICS_UNKNOWN")
        else:
            safe = verify(args.output)
            receipt = args.output / VERIFY_NAME
            if not receipt.exists():
                receipt.write_bytes(prior.json_bytes({
                    "schema": "m5_ccm_targeted_independent_verify_safe_v1",
                    "verified_at_utc": datetime.now(timezone.utc).isoformat(),
                    "status": "PASS_TARGETED_SAVED_OUTPUT_RECOMPUTED",
                    "source_record_count": safe["source_record_count"],
                    "linktype_other_source_records": safe["linktype_other_source_records"],
                    "unparsed_end_source_records": safe["unparsed_end_source_records"],
                    "safe_summary_sha256": digest(args.output / SAFE_NAME),
                    "manifest_sha256": digest(args.output / MANIFEST_NAME),
                    "targeted_code_sha256": digest(Path(__file__)),
                    "financial_experiment_run": False}))
            print("M5_CCM_TARGETED_SAVED_OUTPUT_RECOMPUTED_PASS")
        return 0
    except (TargetError, prior.IntakeError) as exc:
        print(str(exc), file=sys.stderr)
    except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError):
        print("M5_CCM_TARGETED_INPUT_OR_OUTPUT_INVALID", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
