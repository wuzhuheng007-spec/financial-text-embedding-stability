"""Bind new CCM source images to four saved LINKTYPE exception records.

The old complete index, original CSV, and old targeted output remain unchanged.
Unknown raw tokens, identifiers, and row locators stay in an ignored directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile

import audit_m5_ccm_targeted_round2 as targeted


ROOT = Path(__file__).resolve().parents[1]
PRIOR_LEDGER_SHA256 = "cfdd797502fa25af16306ec15b686a0f9c1d53056753a8edfad97bdccac1c17b"
PRIOR_LEDGER_BYTES = 18_559_092
PRIOR_SUMMARY_SHA256 = "f1130f7314140ed3a239c91d326aa34b7a770f468e4159d4eb57065d333e2813"
PRIOR_RECEIPT_SHA256 = "6669b5ac48e7b9b7d1e3e74765754546388ba29a2b81ee4dbb190e50a1bf8d57"
PACKET_SHA256 = "a769c2ba138ae1916972131ed933b419af84e4911c16d6c22a723cfb150c3af4"
PRIOR_SAFE_ZIP_SHA256 = "962e2ed350efc470a8863f2776ef8423ea222fb1199145b410ce800423014d58"
DICTIONARY_IMAGE_SHA256 = "a020d3c377d3a8f4667b606c918d7b7ee595c7cd44de01de5c8c0d7b7b3b3216"
END_HELP_IMAGE_SHA256 = "57b2d6adc6ecd793397fc41d5182be203edce8bce25a67ec3d72c08b7ccfcd33"
PARTIAL_PAGE_IMAGE_SHA256 = "2c17c60a6c7e16d01e52cf5aeadbdbe0a0cb46ef793e7f8b27416f5ee82abd48"
USER_IMAGE_SHA256 = {
    "user_image_01.png": "e624a42711f6765577c223fd54a05451a54f3d48e5a05a8b81edb64ce89329ff",
    "user_image_02.png": "59175db4e6984617b28ac1405117691775e84b5817e098751d34d206be3c2f9c",
    "user_image_03.png": "52a76bf1760943c0212886049608af08a70ef3beec9a7920057da30cc02b93a1",
    "user_image_04.png": "09a64138517750c2c2a57bbc1b3d0faedbfeb783468c6fb8268046ffcd384999",
}
PRIVATE_NAME = "m5_ccm_linktype_source_private.json"
SAFE_NAME = "m5_ccm_source_interpretation_safe.json"
REGISTRY_NAME = "m5_ccm_source_interpretation_registry_private.json"
MANIFEST_NAME = "m5_ccm_source_interpretation_manifest_private.json"
VERIFY_NAME = "m5_ccm_source_interpretation_verify_safe.json"


class SourceReviewError(ValueError):
    """Fixed public error code; no source value or path is included."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise SourceReviewError(code)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    require(isinstance(value, dict), "JSON_OBJECT_REQUIRED")
    return value


def ignored_output(path: Path) -> Path:
    path = path.resolve()
    require(path.is_relative_to(ROOT / "artifacts"), "OUTPUT_OUTSIDE_ARTIFACTS")
    result = subprocess.run(["git", "check-ignore", "-q", str(path)], cwd=ROOT,
                            capture_output=True, check=False)
    require(result.returncode == 0, "OUTPUT_NOT_GIT_IGNORED")
    return path


def code_identity() -> dict:
    require(sys.version_info[:2] == (3, 11), "PYTHON_VERSION_MISMATCH")
    require(Path(targeted.__file__).resolve() ==
            (ROOT / "scripts/audit_m5_ccm_targeted_round2.py").resolve(),
            "TARGETED_IMPORT_SOURCE_MISMATCH")
    require(Path(targeted.prior.__file__).resolve() ==
            (ROOT / "scripts/audit_m5_ccm_query11905330.py").resolve(),
            "PRIOR_IMPORT_SOURCE_MISMATCH")
    require(Path(targeted.prior.old_intake.__file__).resolve() ==
            (ROOT / "scripts/audit_m5_wrds_intake.py").resolve(),
            "DECODER_IMPORT_SOURCE_MISMATCH")
    candidate_sha = sha256_file(ROOT / "configs/m5_protocol_company_proxy_conditional_20261113.json")
    require(candidate_sha == targeted.prior.CANDIDATE_SHA256, "CANDIDATE_HASH_MISMATCH")
    return {"source_review_code_sha256": sha256_file(Path(__file__)),
            "targeted_code_sha256": sha256_file(Path(targeted.__file__)),
            "prior_intake_code_sha256": sha256_file(Path(targeted.prior.__file__)),
            "decoder_code_sha256": sha256_file(Path(targeted.prior.old_intake.__file__)),
            "candidate_sha256": candidate_sha,
            "python_executable_sha256": sha256_file(Path(sys.executable)),
            "python_version": sys.version.split()[0]}


def bind_saved(targeted_output: Path) -> dict:
    """Check immutable receipts and source files, without scanning their rows."""
    targeted_output = targeted.prior.ensure_ignored(targeted_output)
    manifest_path = targeted_output / targeted.MANIFEST_NAME
    manifest = load_json(manifest_path)
    require(manifest.get("schema") == "m5_ccm_targeted_saved_manifest_private_v1" and
            set(manifest.get("files", {})) == {targeted.PRIVATE_NAME, targeted.SAFE_NAME,
                                             targeted.REGISTRY_NAME},
            "TARGETED_MANIFEST_INVALID")
    for name, identity in manifest["files"].items():
        path = targeted_output / name
        require(path.is_file() and path.stat().st_size == identity.get("bytes") and
                sha256_file(path) == identity.get("sha256"), "TARGETED_FILE_HASH_MISMATCH")
    ledger = targeted_output / targeted.PRIVATE_NAME
    summary_path = targeted_output / targeted.SAFE_NAME
    receipt_path = targeted_output / targeted.VERIFY_NAME
    require(ledger.stat().st_size == PRIOR_LEDGER_BYTES and
            sha256_file(ledger) == PRIOR_LEDGER_SHA256 and
            sha256_file(summary_path) == PRIOR_SUMMARY_SHA256 and
            receipt_path.is_file() and sha256_file(receipt_path) == PRIOR_RECEIPT_SHA256,
            "TARGETED_EXPECTED_IDENTITY_MISMATCH")
    summary = load_json(summary_path)
    registry = load_json(targeted_output / targeted.REGISTRY_NAME)
    receipt = load_json(receipt_path)
    require(summary.get("schema") == "m5_ccm_targeted_representation_summary_safe_v1" and
            summary.get("query_id") == 11905330 and
            summary.get("union_target_source_records") == 26_390 and
            summary.get("linktype_other_source_records") == 4 and
            summary.get("unparsed_end_source_records") == 26_386 and
            summary.get("status") == "TARGETED_REPRESENTATIONS_CLASSIFIED_SEMANTICS_UNKNOWN" and
            summary.get("candidate_sha256") == targeted.prior.CANDIDATE_SHA256,
            "TARGETED_SUMMARY_IDENTITY_MISMATCH")
    require(registry.get("schema") == "m5_ccm_targeted_registry_private_v1" and
            registry.get("code_identity") == targeted.code_identity() and
            registry.get("saved_prior_index_sha256") == targeted.EXPECTED_INDEX_SHA256 and
            receipt.get("schema") == "m5_ccm_targeted_independent_verify_safe_v1" and
            receipt.get("status") == "PASS_TARGETED_SAVED_OUTPUT_RECOMPUTED" and
            receipt.get("safe_summary_sha256") == PRIOR_SUMMARY_SHA256 and
            receipt.get("manifest_sha256") == sha256_file(manifest_path),
            "TARGETED_RECEIPT_BINDING_MISMATCH")
    prior_bound = targeted._bound_identity(Path(registry["saved_prior_intake_path"]))
    require(prior_bound["index_sha256"] == summary.get("saved_complete_index_sha256") and
            prior_bound["source_zip_sha256"] == summary.get("source_zip_sha256") and
            prior_bound["source_csv_sha256"] == summary.get("source_csv_sha256"),
            "TARGETED_PRIOR_SOURCE_MISMATCH")
    return {"targeted_output": targeted_output, "ledger": ledger,
            "prior_source_binding_id": prior_bound["source_binding_id"],
            "targeted_manifest_sha256": sha256_file(manifest_path),
            "targeted_summary_sha256": PRIOR_SUMMARY_SHA256,
            "targeted_receipt_sha256": PRIOR_RECEIPT_SHA256,
            "targeted_ledger_sha256": PRIOR_LEDGER_SHA256,
            "prior_index_sha256": prior_bound["index_sha256"],
            "source_zip_sha256": prior_bound["source_zip_sha256"],
            "source_csv_sha256": prior_bound["source_csv_sha256"]}


def bind_packet(packet_dir: Path, outer_zip: Path, extra_images: Path) -> dict:
    """Bind the transferred current-account screenshots and later user images."""
    packet_dir, outer_zip, extra_images = packet_dir.resolve(), outer_zip.resolve(), extra_images.resolve()
    require(outer_zip.is_file() and sha256_file(outer_zip) == PACKET_SHA256,
            "SOURCE_PACKET_ZIP_HASH_MISMATCH")
    manifest_path = packet_dir / "FILE_HASHES.json"
    manifest = load_json(manifest_path)
    require(manifest.get("schema") is not None and isinstance(manifest.get("files"), dict),
            "SOURCE_PACKET_MANIFEST_INVALID")
    with ZipFile(outer_zip) as archive:
        require(archive.testzip() is None, "SOURCE_PACKET_ZIP_CRC_ERROR")
        names = archive.namelist()
        require(len(names) == len(set(names)) and
                set(manifest["files"]) == set(names) - {"FILE_HASHES.json"} and
                all(not Path(name).is_absolute() and ".." not in Path(name).parts
                    for name in names), "SOURCE_PACKET_ZIP_NAMES_INVALID")
        require(archive.read("FILE_HASHES.json") == manifest_path.read_bytes(),
                "SOURCE_PACKET_MANIFEST_HASH_MISMATCH")
        for name, identity in manifest["files"].items():
            original = archive.read(name)
            extracted = packet_dir / name
            require(extracted.is_file() and extracted.read_bytes() == original,
                    "SOURCE_PACKET_EXTRACTED_FILE_MISMATCH")
            expected_hash = identity["sha256"] if isinstance(identity, dict) else identity
            require(sha256_file(extracted) == expected_hash and
                    (not isinstance(identity, dict) or extracted.stat().st_size == identity["bytes"]),
                    "SOURCE_PACKET_MEMBER_HASH_MISMATCH")
    context_zip = packet_dir / "context/M5_CCM_targeted_review_Codex_results_20261113.zip"
    require(sha256_file(context_zip) == PRIOR_SAFE_ZIP_SHA256,
            "PRIOR_SAFE_CONTEXT_HASH_MISMATCH")
    evidence = packet_dir / "evidence"
    dictionary = load_json(evidence / "LINKTYPE_SCREENSHOT_DICTIONARY.json")
    end_help = load_json(evidence / "LINKENDDT_HELP_OBSERVED.json")
    partial = load_json(evidence / "FINAL_PARAMETER_PAGE_PARTIAL_OBSERVED.json")
    require(dictionary.get("source_sha256") == DICTIONARY_IMAGE_SHA256 and
            sha256_file(packet_dir / dictionary["source_file"]) == DICTIONARY_IMAGE_SHA256 and
            dictionary.get("visible_url") ==
            "https://wrds-www.wharton.upenn.edu/data-dictionary/crsp_a_ccm/ccmxpf_lnkhist/linktype/" and
            dictionary.get("visible_variable_name") == "LINKTYPE" and
            {entry.get("code") for entry in dictionary.get("visible_codes", [])} ==
            {"LC", "LU", "LX", "LD", "LS", "LN", "LO", "NR", "NU"},
            "LINKTYPE_DICTIONARY_SOURCE_MISMATCH")
    require(end_help.get("source_sha256") == END_HELP_IMAGE_SHA256 and
            sha256_file(packet_dir / end_help["source_file"]) == END_HELP_IMAGE_SHA256 and
            end_help.get("visible_body_transcription", "").endswith("LINKENDDT is set to .") and
            end_help.get("field_meaning_status") == "LAST_VALID_DATE_DEFINITION_OBSERVED",
            "LINKENDDT_HELP_SOURCE_MISMATCH")
    require(partial.get("source_sha256") == PARTIAL_PAGE_IMAGE_SHA256 and
            sha256_file(packet_dir / partial["source_file"]) == PARTIAL_PAGE_IMAGE_SHA256 and
            partial.get("query_id_from_url") == 11905330 and
            partial.get("input_parameters_section_expanded") is True,
            "FINAL_PARAMETER_PARTIAL_SOURCE_MISMATCH")
    for name, expected_hash in USER_IMAGE_SHA256.items():
        require(sha256_file(extra_images / name) == expected_hash,
                "ADDITIONAL_USER_IMAGE_HASH_MISMATCH")
    return {"packet_dir": packet_dir, "outer_zip": outer_zip,
            "extra_images": extra_images, "packet_sha256": PACKET_SHA256,
            "packet_manifest_sha256": sha256_file(manifest_path),
            "dictionary_json_sha256": sha256_file(evidence / "LINKTYPE_SCREENSHOT_DICTIONARY.json"),
            "dictionary_image_sha256": DICTIONARY_IMAGE_SHA256,
            "end_help_json_sha256": sha256_file(evidence / "LINKENDDT_HELP_OBSERVED.json"),
            "end_help_image_sha256": END_HELP_IMAGE_SHA256,
            "partial_page_json_sha256": sha256_file(evidence / "FINAL_PARAMETER_PAGE_PARTIAL_OBSERVED.json"),
            "partial_page_image_sha256": PARTIAL_PAGE_IMAGE_SHA256,
            "additional_user_image_sha256": dict(USER_IMAGE_SHA256)}


def compare_four(ledger: Path, expected_binding_id: str,
                 *, expected_rows: int = 26_390, expected_other: int = 4) -> tuple[dict, dict]:
    """One pass over the saved targeted ledger, never the full original index."""
    entries = 0
    other = 0
    lo = 0
    private_rows: list[dict] = []
    try:
        with ledger.open("r", encoding="utf-8", errors="strict") as stream:
            for line in stream:
                entries += 1
                row = json.loads(line)
                require(row.get("schema") == "m5_ccm_targeted_private_row_v1" and
                        row.get("source_binding_id") == expected_binding_id and
                        isinstance(row.get("linktype_other"), bool),
                        "TARGET_LEDGER_ROW_IDENTITY_MISMATCH")
                if not row["linktype_other"]:
                    continue
                other += 1
                raw = row.get("raw_linktype")
                require(isinstance(raw, str) and
                        row.get("normalized_linktype") == raw.strip().upper() and
                        row.get("type_category") is not None and
                        isinstance(row.get("source_row_ordinal"), int),
                        "TARGET_LEDGER_LINKTYPE_ROW_INVALID")
                matched = raw.strip().upper() == "LO"
                lo += int(matched)
                private_rows.append({"source_row_ordinal": row["source_row_ordinal"],
                                     "source_binding_id": expected_binding_id,
                                     "raw_linktype": raw,
                                     "normalized_linktype": raw.strip().upper(),
                                     "metadata_code_lo_match": matched,
                                     "source_definition_status":
                                         "ACCOUNT_DICTIONARY_CODE_OBSERVED_RELEASE_UNVERIFIED"
                                         if matched else "UNKNOWN_UNMATCHED"})
    except (UnicodeError, json.JSONDecodeError, TypeError, KeyError) as exc:
        raise SourceReviewError("TARGET_LEDGER_STREAM_INVALID") from exc
    require(entries == expected_rows, "TARGET_LEDGER_ENTRY_COUNT_MISMATCH")
    require(other == expected_other, "TARGET_LEDGER_OTHER_COUNT_MISMATCH")
    return ({"source_target_ledger_entries": entries, "linktype_other_source_records": other,
             "metadata_code_lo_match_source_records": lo,
             "unmatched_linktype_other_source_records": other - lo,
             "all_four_metadata_code_lo": lo == expected_other},
            {"schema": "m5_ccm_linktype_source_private_v1", "source_records": private_rows})


def safe_summary(counts: dict, saved: dict, source: dict, code: dict) -> dict:
    return {
        "schema": "m5_ccm_source_interpretation_safe_v1",
        "scope": "ENGINEERING_ONLY_NOT_FORMAL_M5",
        "status": "SOURCE_CODE_COMPARISON_COMPLETE_RESEARCH_RULES_PENDING",
        "query_id": 11905330,
        **counts,
        "metadata_code_definition": {
            "code": "LO" if counts["metadata_code_lo_match_source_records"] else None,
            "description": "No issue-level link; a company-level link exists"
                           if counts["metadata_code_lo_match_source_records"] else None,
            "evidence_role": "CURRENT_ACCOUNT_DATA_DICTIONARY_SCREENSHOT",
            "exact_saved_release_applicability": "UNKNOWN",
            "issue_level_security_bridge_accepted": False,
        },
        "linktype_dictionary_image_sha256": source["dictionary_image_sha256"],
        "linkenddt_help": {
            "last_valid_date_definition": "OBSERVED_IN_CURRENT_ACCOUNT_HELP",
            "rendered_terminal_text": "LINKENDDT is set to .",
            "terminal_dot_meaning": "UNKNOWN",
            "single_letter_csv_export_conversion": "UNKNOWN",
            "open_endpoint_semantics_accepted": False,
            "image_sha256": source["end_help_image_sha256"],
        },
        "final_parameter_page": {
            "original_partial_result_page": "OBSERVED",
            "no_input_files_display": "OBSERVED_IN_ORIGINAL_PARTIAL_IMAGE",
            "additional_user_images": "USER_SUPPLIED_VISUAL_FRAGMENTS",
            "additional_extra10_codes_visible": ["LC", "LU", "LS", "LX", "LD", "LN", "NR", "NU"],
            "eight_code_list_status": "OBSERVED_IN_USER_IMAGE_04_WITH_QUERY_URL",
            "additional_var_and_extra6_values_status":
                "OBSERVED_IN_USER_IMAGES_02_AND_03_QUERY_URL_NOT_VISIBLE_IN_SAME_FRAME",
            "method_value_visible": 3,
            "native_sql": "NOT_SUPPLIED",
            "backend_join_and_predicates": "NOT_VERIFIED",
            "image_sha256_by_role": {
                "original_partial": source["partial_page_image_sha256"],
                "non_vendor_advice": source["additional_user_image_sha256"]["user_image_01.png"],
                "expanded_vars": source["additional_user_image_sha256"]["user_image_02.png"],
                "expanded_extra10": source["additional_user_image_sha256"]["user_image_03.png"],
                "query_url_and_lower_parameters":
                    source["additional_user_image_sha256"]["user_image_04.png"],
            },
        },
        "linkprim_value_definitions": "NOT_SUPPLIED",
        "header_cik_priusa_role": "UNDATED_HEADER_OBSERVATIONS",
        "candidate_status": "PROPOSED",
        "researcher_accepted": False,
        "real_inference_status": "NOT_RUN",
        "formal_company_count": None,
        "formal_event_count": None,
        "financial_experiment_run": False,
        "packet_sha256": source["packet_sha256"],
        "packet_manifest_sha256": source["packet_manifest_sha256"],
        "dictionary_json_sha256": source["dictionary_json_sha256"],
        "end_help_json_sha256": source["end_help_json_sha256"],
        "partial_page_json_sha256": source["partial_page_json_sha256"],
        "targeted_ledger_sha256": saved["targeted_ledger_sha256"],
        "prior_index_sha256": saved["prior_index_sha256"],
        "prior_source_zip_sha256": saved["source_zip_sha256"],
        "prior_source_csv_sha256": saved["source_csv_sha256"],
        "prior_targeted_summary_sha256": saved["targeted_summary_sha256"],
        "prior_targeted_verify_sha256": saved["targeted_receipt_sha256"],
        "source_review_code_sha256": code["source_review_code_sha256"],
        "targeted_code_sha256": code["targeted_code_sha256"],
        "candidate_sha256": code["candidate_sha256"],
    }


def run(targeted_output: Path, packet_dir: Path, outer_zip: Path,
        extra_images: Path, output: Path) -> dict:
    output = ignored_output(output)
    require(not output.exists(), "SOURCE_REVIEW_OUTPUT_ALREADY_EXISTS")
    saved = bind_saved(targeted_output)
    source = bind_packet(packet_dir, outer_zip, extra_images)
    code = code_identity()
    counts, private = compare_four(saved["ledger"], saved["prior_source_binding_id"])
    safe = safe_summary(counts, saved, source, code)
    output.mkdir(parents=True)
    (output / PRIVATE_NAME).write_bytes(json_bytes(private))
    (output / SAFE_NAME).write_bytes(json_bytes(safe))
    registry = {"schema": "m5_ccm_source_interpretation_registry_private_v1",
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "python_executable": sys.executable,
                "command": sys.argv,
                "targeted_output_path": str(saved["targeted_output"]),
                "packet_dir": str(source["packet_dir"]),
                "packet_zip": str(source["outer_zip"]),
                "additional_images_dir": str(source["extra_images"]),
                "code_identity": code,
                "targeted_ledger_sha256": saved["targeted_ledger_sha256"],
                "packet_sha256": source["packet_sha256"]}
    (output / REGISTRY_NAME).write_bytes(json_bytes(registry))
    manifest = {"schema": "m5_ccm_source_interpretation_manifest_private_v1",
                "files": {name: {"sha256": sha256_file(output / name),
                                 "bytes": (output / name).stat().st_size}
                          for name in (PRIVATE_NAME, SAFE_NAME, REGISTRY_NAME)}}
    (output / MANIFEST_NAME).write_bytes(json_bytes(manifest))
    return safe


def verify(output: Path) -> dict:
    output = ignored_output(output)
    manifest = load_json(output / MANIFEST_NAME)
    require(manifest.get("schema") == "m5_ccm_source_interpretation_manifest_private_v1" and
            set(manifest.get("files", {})) == {PRIVATE_NAME, SAFE_NAME, REGISTRY_NAME},
            "SOURCE_REVIEW_MANIFEST_INVALID")
    for name, identity in manifest["files"].items():
        path = output / name
        require(path.is_file() and path.stat().st_size == identity.get("bytes") and
                sha256_file(path) == identity.get("sha256"),
                "SOURCE_REVIEW_SAVED_FILE_HASH_MISMATCH")
    registry = load_json(output / REGISTRY_NAME)
    require(registry.get("schema") == "m5_ccm_source_interpretation_registry_private_v1" and
            registry.get("code_identity") == code_identity(),
            "SOURCE_REVIEW_REGISTRY_CODE_MISMATCH")
    saved = bind_saved(Path(registry["targeted_output_path"]))
    source = bind_packet(Path(registry["packet_dir"]), Path(registry["packet_zip"]),
                         Path(registry["additional_images_dir"]))
    require(registry["targeted_ledger_sha256"] == saved["targeted_ledger_sha256"] and
            registry["packet_sha256"] == source["packet_sha256"],
            "SOURCE_REVIEW_REGISTRY_SOURCE_MISMATCH")
    counts, private = compare_four(saved["ledger"], saved["prior_source_binding_id"])
    recomputed_safe = safe_summary(counts, saved, source, code_identity())
    require((output / PRIVATE_NAME).read_bytes() == json_bytes(private) and
            (output / SAFE_NAME).read_bytes() == json_bytes(recomputed_safe),
            "SOURCE_REVIEW_SAVED_RECOMPUTE_MISMATCH")
    receipt = output / VERIFY_NAME
    if receipt.exists():
        existing = load_json(receipt)
        require(existing.get("schema") == "m5_ccm_source_independent_verify_safe_v1" and
                existing.get("status") == "PASS_SOURCE_MATCH_SAVED_RECOMPUTED" and
                existing.get("safe_summary_sha256") == sha256_file(output / SAFE_NAME) and
                existing.get("manifest_sha256") == sha256_file(output / MANIFEST_NAME) and
                existing.get("source_review_code_sha256") == sha256_file(Path(__file__)),
                "SOURCE_REVIEW_VERIFY_RECEIPT_MISMATCH")
    return recomputed_safe


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--targeted-output", type=Path, required=True)
    run_parser.add_argument("--packet-dir", type=Path, required=True)
    run_parser.add_argument("--packet-zip", type=Path, required=True)
    run_parser.add_argument("--additional-images", type=Path, required=True)
    run_parser.add_argument("--output", type=Path, required=True)
    verify_parser = commands.add_parser("verify-output")
    verify_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            run(args.targeted_output, args.packet_dir, args.packet_zip,
                args.additional_images, args.output)
            print("M5_CCM_SOURCE_MATCH_COMPLETE_RESEARCH_RULES_PENDING")
        else:
            result = verify(args.output)
            receipt = args.output / VERIFY_NAME
            if not receipt.exists():
                receipt.write_bytes(json_bytes({
                    "schema": "m5_ccm_source_independent_verify_safe_v1",
                    "verified_at_utc": datetime.now(timezone.utc).isoformat(),
                    "status": "PASS_SOURCE_MATCH_SAVED_RECOMPUTED",
                    "safe_summary_sha256": sha256_file(args.output / SAFE_NAME),
                    "manifest_sha256": sha256_file(args.output / MANIFEST_NAME),
                    "source_review_code_sha256": sha256_file(Path(__file__)),
                    "linktype_other_source_records": result["linktype_other_source_records"],
                    "metadata_code_lo_match_source_records":
                        result["metadata_code_lo_match_source_records"],
                    "financial_experiment_run": False}))
            print("M5_CCM_SOURCE_MATCH_SAVED_OUTPUT_RECOMPUTED_PASS")
        return 0
    except (SourceReviewError, targeted.TargetError, targeted.prior.IntakeError) as exc:
        print(str(exc), file=sys.stderr)
    except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError):
        print("M5_CCM_SOURCE_REVIEW_INPUT_OR_OUTPUT_INVALID", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
