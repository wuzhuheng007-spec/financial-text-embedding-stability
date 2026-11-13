"""Prepare two private, source-bound WRDS LINKENDDT examples.

The packet is local-only. Its public summary contains no source row, key,
ordinal or endpoint spelling. This script does not infer a missing-value rule.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import zipfile
from datetime import date
from pathlib import Path


CSV_SHA256 = "ad5481feb396a47e265951eacd59b4ff7cb84dba9e74e43e80392c0db836bfe9"
TARGET_SHA256 = "cfdd797502fa25af16306ec15b686a0f9c1d53056753a8edfad97bdccac1c17b"
SOURCE_ZIP_SHA256 = "ab660b8b6f31fcc424c759531c9c6009359ac3ea79c2c3b0fb9873ddb0bc1bcb"
KEYS = ("GVKEY", "LIID", "LINKDT", "LINKTYPE", "LINKPRIM", "LPERMNO", "LPERMCO")
SEARCH_SCOPE = (
    "m5_ccm_query11905330_packet_20261113_v1",
    "m5_ccm_query11905330_intake_20261113_v1",
    "m5_ccm_query11905330_runlogs_20261113_v1",
    "m5_ccm_linktype_source_packet_20261113_v2",
    "m5_ccm_source_interpretation_20261113_v1",
    "m5_ccm_source_review_runlogs_20261113_v1",
)


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def classify(raw: str) -> str:
    token = raw.strip()
    if len(token) == 1 and token.isascii() and token.isalpha():
        return "single_ascii_letter"
    date.fromisoformat(token)
    return "finite_iso_date"


def choose_pair(source_csv: Path) -> tuple[list[dict], int]:
    selected: dict[str, dict] = {}
    total = 0
    with source_csv.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not set(KEYS + ("LINKENDDT",)).issubset(reader.fieldnames or []):
            raise ValueError("missing original link-side fields")
        for ordinal, row in enumerate(reader, 1):
            total = ordinal
            if not all(row[key].strip() for key in KEYS):
                continue
            date.fromisoformat(row["LINKDT"].strip())
            kind = classify(row["LINKENDDT"])
            if kind not in selected:
                selected[kind] = {"source_row_ordinal": ordinal, "kind": kind, "row": row}
    if total != 123388 or set(selected) != {"single_ascii_letter", "finite_iso_date"}:
        raise ValueError("expected source scope or pair categories not found")
    return [selected["single_ascii_letter"], selected["finite_iso_date"]], total


def check_targeted(targeted: Path, selected: dict, source_hash: str) -> None:
    found = None
    with targeted.open(encoding="utf-8") as stream:
        for line in stream:
            value = json.loads(line)
            if int(value["source_row_ordinal"]) == selected["source_row_ordinal"]:
                if found is not None:
                    raise ValueError("duplicate selected ordinal in targeted ledger")
                found = value
    if found is None or found["source_csv_sha256"] != source_hash:
        raise ValueError("selected letter row not bound to targeted ledger")
    if found["linkenddt_unparsed"] is not True or found["raw_linkenddt"] != selected["row"]["LINKENDDT"]:
        raise ValueError("targeted letter spelling differs from source CSV")


def make_private_row(selected: dict, source_hash: str) -> dict[str, object]:
    row = selected["row"]
    raw = row["LINKENDDT"]
    value: dict[str, object] = {
        "row_kind": selected["kind"],
        "source_row_ordinal": selected["source_row_ordinal"],
        "query_id": 11905330,
        "source_csv_sha256": source_hash,
    }
    value.update({key: row[key] for key in KEYS})
    value.update({
        "LINKENDDT_raw_cell": raw,
        "LINKENDDT_python_repr": repr(raw),
        "LINKENDDT_unicode_codepoints": " ".join(f"U+{ord(char):04X}" for char in raw),
        "LINKENDDT_utf8_bytes_hex": raw.encode("utf-8").hex(),
        "LINKENDDT_utf8_byte_count": len(raw.encode("utf-8")),
        "native_data_type": "UNRESOLVED",
        "native_special_value_class": "UNRESOLVED",
        "saved_export_semantics": "UNRESOLVED",
    })
    return value


def csv_bytes(rows: list[dict[str, object]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source_csv", "targeted_ledger", "observed_page", "parameters",
                 "source_roles", "request_text", "registered_artifacts", "out"):
        parser.add_argument("--" + name.replace("_", "-"), type=Path, required=True)
    args = parser.parse_args()
    intended_names = ("wrds_pair_request.csv", "source_context.json", "02_WRDS_REQUEST.txt",
                      "README_PRIVATE.md", "SHA256SUMS_PRIVATE.json", "wrds_pair_support_private.zip")
    if any((args.out / name).exists() for name in intended_names):
        raise FileExistsError(f"refusing to replace an existing pair packet: {args.out}")
    if (args.out.parent / "s1_wrds_pair_safe.json").exists():
        raise FileExistsError("refusing to replace an existing safe summary")
    actual_csv, actual_target = sha(args.source_csv), sha(args.targeted_ledger)
    if (actual_csv, actual_target) != (CSV_SHA256, TARGET_SHA256):
        raise ValueError("source or targeted ledger version changed")
    page = json.loads(args.observed_page.read_text(encoding="utf-8"))
    params = json.loads(args.parameters.read_text(encoding="utf-8"))
    roles = json.loads(args.source_roles.read_text(encoding="utf-8"))
    if page["result_page"]["query_id"] != 11905330 or page["source_zip"]["sha256"] != SOURCE_ZIP_SHA256:
        raise ValueError("observed result page/transport not bound to source ZIP")
    if params["query_id_from_result_screenshot"] != 11905330 or params["parameters"]["datef"] != "YYMMDDd10.":
        raise ValueError("saved query parameter context differs")
    if roles["user_parameter_dictionary_table"] != "ccmxpf_lnkhist":
        raise ValueError("saved source-role context differs")
    pair, total = choose_pair(args.source_csv)
    check_targeted(args.targeted_ledger, pair[0], actual_csv)
    private_rows = [make_private_row(item, actual_csv) for item in pair]
    roots = [args.registered_artifacts / name for name in SEARCH_SCOPE]
    if not all(path.is_dir() for path in roots):
        raise FileNotFoundError("one or more registered local source directories missing")
    native_files = sorted(str(path) for root in roots for path in root.rglob("*")
                          if path.is_file() and path.suffix.lower() in {".sql", ".sas"})
    if native_files:
        raise ValueError("native SQL/SAS files found: inspect before declaring unavailable")
    context = {
        "schema": "m5_wrds_pair_private_source_context_v1",
        "status": "SOURCE_PENDING_NOT_SENT",
        "query_id": 11905330,
        "source_csv_path_local_only": str(args.source_csv),
        "source_csv_sha256": actual_csv,
        "targeted_ledger_sha256": actual_target,
        "original_source_zip_sha256": SOURCE_ZIP_SHA256,
        "result_page_product_observed": page["result_page"]["product"],
        "result_page_status_observed": page["result_page"]["status"],
        "result_page_submitted_display": page["result_page"]["submitted_display"],
        "result_page_finished_display": page["result_page"]["work_finished_display"],
        "result_page_display_timezone": page["result_page"]["displayed_timezone"],
        "result_page_retention_until_display": page["result_page"]["retention_until_display"],
        "query_form_interface_file_observed": params["parameters"]["file"],
        "query_dictionary_table_observed": params["parameters"]["data_dictionary_table"],
        "query_output_format_observed": params["parameters"]["format"],
        "query_date_format_observed": params["parameters"]["datef"],
        "query_form_environment_observed": params["parameters"]["query_form_environment"],
        "query_form_wrdsversion_observed_not_data_release": params["parameters"]["wrdsversion"],
        "original_query_association": page["association"],
        "backend_sql_or_execution_log_in_registered_local_evidence": "NOT_AVAILABLE",
        "registered_search_directories": [str(path) for path in roots],
        "native_sql_or_sas_file_count": len(native_files),
        "exact_backend_table_joins_predicates": None,
        "saved_product_release_id": None,
        "linkenddt_native_type": None,
        "linkenddt_special_value_class": None,
        "linkenddt_csv_conversion": None,
        "linkenddt_date_semantics": None,
        "linkprim_saved_release_dictionary": None,
        "evidence": {str(path): sha(path) for path in (args.observed_page, args.parameters, args.source_roles)},
        "pair_selection_rule": "minimum one-based source CSV data-row ordinal in each endpoint class, requiring all seven original link-side key cells nonempty and a parseable LINKDT; finite control requires ISO date LINKENDDT",
        "pair_classes": [value["row_kind"] for value in private_rows],
        "route_a": "Ask original-query support to explain Query 11905330 native type, special value, CSV conversion and release against the two original rows",
        "route_b": "If historical query is unavailable, acquire a newly source-bound current snapshot as a distinct input version; preserve original and compare drift; it cannot reconstruct the old release",
        "private_packet_sent": False,
    }
    readme = """# Private WRDS pair packet — NOT SENT\n\nThe CSV has two original saved-export link rows, selected by the frozen minimum-ordinal rule in source_context.json. The cell bytes are UTF-8 encodings of the exact parsed CSV cells. They are not native SAS values. Do not infer the endpoint meaning.\n\nUse 02_WRDS_REQUEST.txt as the question text. Route A seeks a release-bound answer for original Query 11905330. If the original execution is unavailable, Route B is a new versioned source snapshot and must not replace or recreate the old input silently. Share this licensed row packet only through the appropriate private support channel after the user chooses to send it.\n""".encode("utf-8")
    files = {
        "wrds_pair_request.csv": csv_bytes(private_rows),
        "source_context.json": json_bytes(context),
        "02_WRDS_REQUEST.txt": args.request_text.read_bytes(),
        "README_PRIVATE.md": readme,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (args.out / name).write_bytes(content)
    manifest = {name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())}
    (args.out / "SHA256SUMS_PRIVATE.json").write_bytes(json_bytes({"schema": "m5_wrds_pair_private_sha256_v1", "files": manifest}))
    archive_path = args.out / "wrds_pair_support_private.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, content in sorted(files.items()):
            archive.writestr(name, content)
        archive.writestr("SHA256SUMS_PRIVATE.json", (args.out / "SHA256SUMS_PRIVATE.json").read_bytes())
    with zipfile.ZipFile(archive_path) as archive:
        if archive.testzip() is not None or set(archive.namelist()) != set(manifest) | {"SHA256SUMS_PRIVATE.json"}:
            raise ValueError("private ZIP CRC or member list mismatch")
        if any(hashlib.sha256(archive.read(name)).hexdigest() != digest for name, digest in manifest.items()):
            raise ValueError("private ZIP content hash mismatch")
    safe = {
        "schema": "m5_wrds_pair_safe_v1",
        "status": "SOURCE_PENDING_PRIVATE_PACKET_PREPARED_NOT_SENT",
        "query_id": 11905330,
        "source_csv_sha256": actual_csv,
        "targeted_ledger_sha256": actual_target,
        "source_csv_data_rows": total,
        "selected_private_rows": len(private_rows),
        "selected_endpoint_classes": [value["row_kind"] for value in private_rows],
        "all_link_key_cells_present": all(all(value[key] for key in KEYS) for value in private_rows),
        "same_linktype_and_linkprim_between_pair": all(private_rows[0][key] == private_rows[1][key] for key in ("LINKTYPE", "LINKPRIM")),
        "selected_single_letter_matches_targeted_ledger": True,
        "selection_rule": context["pair_selection_rule"],
        "native_sql_or_execution_log_in_registered_local_evidence": "NOT_AVAILABLE",
        "saved_release_id": None,
        "csv_special_value_semantics": "UNRESOLVED",
        "private_csv_sha256": sha(args.out / "wrds_pair_request.csv"),
        "private_source_context_sha256": sha(args.out / "source_context.json"),
        "private_support_zip_sha256": sha(archive_path),
        "private_support_zip_crc": "PASS",
        "private_packet_sent": False,
        "route_a": "original-query source explanation",
        "route_b": "new snapshot only as separately versioned input",
        "formal_financial_experiment": "NOT_RUN",
    }
    (args.out.parent / "s1_wrds_pair_safe.json").write_bytes(json_bytes(safe))
    print(json.dumps({"status": safe["status"], "private_rows": len(private_rows), "safe_summary_sha256": sha(args.out.parent / "s1_wrds_pair_safe.json"), "private_zip_sha256": safe["private_support_zip_sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
