"""SYNTHETIC_TEST_ONLY: no WRDS CSV, identifiers, returns or model session."""

from __future__ import annotations

import csv
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import audit_m5_ccm_query11905330 as audit  # noqa: E402


def synthetic_row(**overrides: str) -> dict[str, str]:
    row = {field: "" for field in audit.EXPECTED}
    row.update({"gvkey": "0007", "liid": "CLASS-A", "linkdt": "2020-01-01",
                "linkenddt": "2020-12-31", "linkprim": "P", "linktype": "LC",
                "lpermco": "0008.0", "lpermno": "0009.0",
                "conm": "Synthetic Firm", "tic": "SYN", "cusip": "SYNTHETIC",
                "cik": "0000000007", "sic": "1234", "naics": "123456", "priusa": "SYN"})
    row.update(overrides)
    return row


def write_csv(path: Path, rows: list[dict[str, str] | list[str]],
              header: tuple[str, ...] = audit.EXPECTED) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(header)
        for row in rows:
            writer.writerow([row[field] for field in header] if isinstance(row, dict) else row)


def run_scan(directory: Path, rows: list[dict[str, str] | list[str]],
             *, header: tuple[str, ...] = audit.EXPECTED,
             page_rows: int | None = None,
             seed: set[str] | None = None) -> tuple[dict, dict, Path, Path, Path]:
    source = directory / "fixture.csv"
    index = directory / "index.jsonl"
    exceptions = directory / "exceptions.jsonl"
    write_csv(source, rows, header)
    summary, private = audit.scan_csv(source, index, exceptions,
                                      source_binding_id="SYNTHETIC_TEST_ONLY",
                                      seed_ids=seed, page_rows=len(rows) if page_rows is None else page_rows)
    return summary, private, source, index, exceptions


class CcmSyntheticAuditTests(unittest.TestCase):
    def test_exact_and_normalized_header_collision_block_relation_mapping(self):
        for header in [(*audit.EXPECTED, "gvkey"), (*audit.EXPECTED, " GVKEY ")]:
            mapping, status = audit.header_mapping(list(header))
            self.assertIsNone(mapping)
            self.assertFalse(status["mapping_unambiguous"])
            self.assertGreater(status["normalized_colliding_header_count"], 0)
        with tempfile.TemporaryDirectory() as directory:
            row = ["SYNTHETIC"] * (len(audit.EXPECTED) + 1)
            summary, private, _, index, _ = run_scan(Path(directory), [row],
                                                       header=(*audit.EXPECTED, " GVKEY "))
            self.assertEqual(summary["status"], "STRUCTURE_BLOCKED")
            self.assertEqual(summary["index_entries"], 1)
            self.assertIsNone(summary["distinct_source_eight_cell_relation_tuples"])
            self.assertEqual(json.loads(index.read_text())["raw_cells"], row)
            self.assertIsNone(private["mapping"])

    def test_malformed_width_is_preserved_and_not_silently_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            rows = [synthetic_row(), ["SYNTHETIC", "TOO_SHORT"]]
            summary, _, _, index, exceptions = run_scan(Path(directory), rows)
            self.assertEqual(summary["status"], "STRUCTURE_BLOCKED")
            self.assertEqual(summary["actual_csv_data_rows"], 2)
            self.assertEqual(summary["index_entries"], 2)
            self.assertEqual(summary["width_error_rows"], 1)
            records = [json.loads(line) for line in index.read_text().splitlines()]
            self.assertEqual(records[1]["raw_cells"], rows[1])
            self.assertEqual(records[1]["relation_status"], "NOT_DERIVED_INVALID_WIDTH")
            self.assertEqual(json.loads(exceptions.read_text())["diagnostic_codes"],
                             ["ROW_WIDTH_MISMATCH"])

    def test_raw_identifiers_header_roles_and_no_link_status_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            rows = [synthetic_row(lpermco="0", lpermno="0009.0", cik="0000000007"),
                    synthetic_row(lpermco="", lpermno="1.5", cik="0000000008")]
            summary, _, _, index, _ = run_scan(Path(directory), rows, seed={"9"})
            records = [json.loads(line) for line in index.read_text().splitlines()]
            self.assertEqual(records[0]["link_cells"]["gvkey"], "0007")
            self.assertEqual(records[0]["link_cells"]["liid"], "CLASS-A")
            self.assertEqual(records[0]["normalized_security_ids"]["lpermno"],
                             {"raw": "0009.0", "normalized": "9", "status": "VALID_POSITIVE_INTEGER"})
            self.assertEqual(records[0]["normalized_security_ids"]["lpermco"]["status"],
                             "ZERO_NOT_LINKABLE")
            self.assertEqual(records[1]["normalized_security_ids"]["lpermno"]["status"],
                             "UNPARSED_OR_NONPOSITIVE")
            self.assertEqual(records[0]["header_observations"]
                             ["compustat_names_form_declared"]["cik"], "0000000007")
            self.assertNotIn("cik", records[0]["link_cells"])
            self.assertEqual(summary["seed_coverage"]["source_rows_hitting_seed"], 1)
            self.assertEqual(summary["distinct_positive_normalized_lpermno"], 1)

    def test_finite_reverse_and_unknown_endpoint_semantics(self):
        self.assertEqual(audit.parse_endpoint(".B")["status"],
                         "SENTINEL_UNKNOWN_ENDPOINT_SEMANTICS")
        self.assertEqual(audit.parse_endpoint("")["status"],
                         "BLANK_UNKNOWN_ENDPOINT_SEMANTICS")
        self.assertEqual(audit.parse_endpoint("20200230")["status"],
                         "INVALID_GREGORIAN_DATE")
        self.assertEqual(audit.parse_endpoint("2020-01-01")["parsed"], "2020-01-01")
        with tempfile.TemporaryDirectory() as directory:
            rows = [synthetic_row(), synthetic_row(linkdt="2021-01-01", linkenddt="2020-12-31"),
                    synthetic_row(linkdt=".B", linkenddt=""),
                    synthetic_row(linkdt="2020-01-01", linkenddt=".E")]
            summary, _, _, _, _ = run_scan(Path(directory), rows)
            self.assertEqual(summary["reversed_finite_interval_rows"], 1)
            self.assertEqual(summary["unknown_endpoint_rows"], 2)
            self.assertEqual(summary["supported_open_endpoint_rows"], 0)

    def test_duplicate_relation_differs_from_full_row_and_header_cik_multiplicity(self):
        with tempfile.TemporaryDirectory() as directory:
            base = synthetic_row()
            rows = [base, base.copy(), synthetic_row(cik="0000000008")]
            summary, _, _, index, _ = run_scan(Path(directory), rows)
            self.assertEqual(summary["index_entries"], 3)
            self.assertEqual(summary["distinct_source_eight_cell_relation_tuples"], 1)
            self.assertEqual(summary["duplicate_eight_cell_relation_groups"], 1)
            self.assertEqual(summary["duplicate_eight_cell_relation_excess_rows"], 2)
            self.assertEqual(summary["duplicate_full_parsed_row_groups"], 1)
            self.assertEqual(summary["duplicate_full_parsed_row_excess_rows"], 1)
            self.assertEqual(summary["gvkeys_with_multiple_header_cik_observations"], 1)
            with index.open(encoding="utf-8") as stream:
                self.assertEqual(sum(1 for _ in stream), 3)
            groups = [json.loads(line) for line in (Path(directory) / "exceptions.jsonl").read_text().splitlines()
                      if json.loads(line).get("schema") == "m5_ccm_private_group_diagnostic_v1"]
            self.assertEqual(summary["private_group_diagnostic_records"], len(groups))
            self.assertIn("DUPLICATE_EIGHT_CELL_RELATION", {group["kind"] for group in groups})
            self.assertIn("GVKEY_WITH_MULTIPLE_HEADER_CIK_OBSERVATIONS",
                          {group["kind"] for group in groups})

    def test_page_count_mismatch_preserves_complete_index(self):
        with tempfile.TemporaryDirectory() as directory:
            summary, _, _, index, _ = run_scan(Path(directory), [synthetic_row()], page_rows=2)
            self.assertEqual(summary["status"], "PAGE_ROW_MISMATCH")
            self.assertEqual(summary["actual_csv_data_rows"], 1)
            self.assertEqual(summary["index_entries"], 1)
            self.assertFalse(summary["page_count_matches_csv"])
            self.assertTrue(index.is_file())

    def test_seed_receipt_and_upstream_set_are_both_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            txt_dir = root / "upload"
            txt_dir.mkdir()
            seed_txt = txt_dir / "m5_daily_seed_permnos_upload.txt"
            seed_txt.write_bytes(b"9\n10\n")
            seed_json = root / "m5_delist_intake_20261111_v3/m5_daily_seed_permnos_private.json"
            seed_json.parent.mkdir()
            upstream = {"schema": "m5_daily_seed_identifiers_private_v1",
                        "scope": "DOWNLOAD_INPUT_ONLY_NOT_FORMAL_COHORT",
                        "successor_ids_auto_added": False,
                        "permnos": ["10", "9"], "permno_count": 2}
            seed_json.write_bytes(audit.json_bytes(upstream))
            plan = {"schema": "m5_daily_download_plan_private_v1",
                    "company_code_file_sha256": audit.sha256_file(seed_txt),
                    "starting_identifier_count": 2, "successor_ids_auto_added": False}
            plan_path = txt_dir / "m5_daily_download_plan_private.json"
            plan_path.write_bytes(audit.json_bytes(plan))
            registry = {"schema": "m5_daily_upload_registry_private_v1",
                        "upload_sha256": audit.sha256_file(seed_txt),
                        "line_count": 2, "unique_count": 2,
                        "set_equal_to_seed_index_and_snapshot": True,
                        "formal_m5_cohort_selected": False,
                        "plan_sha256": audit.sha256_file(plan_path),
                        "input_bindings": {"seed_sha256": audit.sha256_file(seed_json)}}
            registry_path = txt_dir / "m5_daily_upload_registry_private.json"
            registry_path.write_bytes(audit.json_bytes(registry))
            with patch.object(audit, "ARTIFACTS", root), \
                 patch.object(audit, "SEED_SHA256", audit.sha256_file(seed_txt)):
                actual, _ = audit.check_seed(seed_txt)
                self.assertEqual(actual, {"9", "10"})
                upstream["permnos"] = ["10", "11"]
                seed_json.write_bytes(audit.json_bytes(upstream))
                registry["input_bindings"]["seed_sha256"] = audit.sha256_file(seed_json)
                registry_path.write_bytes(audit.json_bytes(registry))
                with self.assertRaisesRegex(audit.IntakeError, "SEED_UPSTREAM_SET_MISMATCH"):
                    audit.check_seed(seed_txt)

    def test_saved_output_rebuild_rejects_index_tamper_even_if_manifest_rehashed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "fixture.csv"
            write_csv(source, [synthetic_row()])
            output = root / "output"
            output.mkdir()
            code = {"intake_script_sha256": "SYNTHETIC_CODE",
                    "id_decoder_script_sha256": "SYNTHETIC_DECODER"}
            evidence = {name: "SYNTHETIC_HASH" for name in (
                "OBSERVED_PAGE_AND_TRANSPORT.json", "PARAMETERS_STRUCTURED.json",
                "SOURCE_ROLE_LEDGER.json", "SCREENSHOT_IDENTITIES.json",
                "USER_PASTED_PARAMETERS_TRANSCRIPT.txt")}
            source_bundle = {"packet_dir": str(root), "source_zip_path": str(root / "source.zip"),
                             "outer_zip_path": str(root / "outer.zip"),
                             "outer_zip_sha256": "SYNTHETIC_OUTER_ZIP",
                             "handoff_manifest_sha256": "SYNTHETIC_MANIFEST",
                             "handoff_member_count": 1,
                             "source_zip_sha256": "SYNTHETIC_SOURCE_ZIP",
                             "source_csv_path": str(source), "source_csv_sha256": audit.sha256_file(source),
                             "evidence_sha256": evidence, "result_page_status": "SYNTHETIC_PAGE",
                             "form_source_role_status": "SYNTHETIC_FORM",
                             "final_parameter_status": "SYNTHETIC_TRANSCRIPT",
                             "native_sql_status": "NOT_SUPPLIED", "backend_join_status": "UNKNOWN"}
            seed_meta = {"seed_txt_path": str(root / "seed.txt"), "seed_txt_sha256": "SYNTHETIC_SEED",
                         "seed_registry_sha256": "SYNTHETIC_REGISTRY",
                         "seed_plan_sha256": "SYNTHETIC_PLAN",
                         "upstream_seed_json_sha256": "SYNTHETIC_UPSTREAM"}
            binding = hashlib.sha256(b"SYNTHETIC_SOURCE_ZIP:" +
                                      source_bundle["source_csv_sha256"].encode()).hexdigest()
            counts, private = audit.scan_csv(source, output / audit.INDEX_NAME,
                                             output / audit.EXCEPTIONS_NAME,
                                             source_binding_id=binding, seed_ids={"9"}, page_rows=1,
                                             source_zip_sha256=source_bundle["source_zip_sha256"],
                                             source_csv_sha256=source_bundle["source_csv_sha256"])
            summary = audit.augment_summary(counts, source_bundle, seed_meta, code)
            (output / audit.SUMMARY_NAME).write_bytes(audit.json_bytes(summary))
            registry = {"schema": "m5_ccm_local_registry_private_v1",
                        "code_identity": code, "source_bundle": source_bundle,
                        "seed_binding": seed_meta, "source_binding_id": binding,
                        "raw_header": private["raw_header"],
                        "header_mapping": private["mapping"],
                        "index_logical_fingerprint_sha256": private["index_logical_fingerprint_sha256"],
                        "exception_logical_fingerprint_sha256": private["exception_logical_fingerprint_sha256"]}
            (output / audit.REGISTRY_NAME).write_bytes(audit.json_bytes(registry))
            (output / audit.MANIFEST_NAME).write_bytes(audit.json_bytes(audit._file_manifest(output)))
            with patch.object(audit, "ensure_ignored", side_effect=lambda path: path), \
                 patch.object(audit, "checked_code", return_value=code), \
                 patch.object(audit, "check_source_bundle", return_value=source_bundle), \
                 patch.object(audit, "check_seed", return_value=({"9"}, seed_meta)), \
                 patch.object(audit, "PAGE_ROWS", 1):
                self.assertEqual(audit.verify_saved(output), summary)
                internal_path = output / "m5_ccm_internal_verify_safe.json"
                internal = {"schema": "m5_ccm_internal_saved_verification_safe_v1",
                            "status": "PASS_SAVED_OUTPUT_RECOMPUTED_IN_PROCESS",
                            "summary_sha256": audit.sha256_file(output / audit.SUMMARY_NAME),
                            "manifest_sha256": audit.sha256_file(output / audit.MANIFEST_NAME),
                            "source_csv_sha256": source_bundle["source_csv_sha256"],
                            "source_zip_sha256": source_bundle["source_zip_sha256"],
                            "code_sha256": code["intake_script_sha256"],
                            "row_count": 1, "index_entries": 1,
                            "financial_experiment_run": False}
                internal_path.write_bytes(audit.json_bytes(internal))
                self.assertEqual(audit.verify_saved(output), summary)
                internal["status"] = "TAMPERED"
                internal_path.write_bytes(audit.json_bytes(internal))
                with self.assertRaisesRegex(audit.IntakeError, "INTERNAL_VERIFY_RECEIPT_MISMATCH"):
                    audit.verify_saved(output)
                internal["status"] = "PASS_SAVED_OUTPUT_RECOMPUTED_IN_PROCESS"
                internal_path.write_bytes(audit.json_bytes(internal))
                index = output / audit.INDEX_NAME
                index.write_bytes(index.read_bytes() + b"{}\n")
                (output / audit.MANIFEST_NAME).write_bytes(audit.json_bytes(audit._file_manifest(output)))
                with self.assertRaisesRegex(audit.IntakeError, "SAVED_LOGICAL_INDEX_MISMATCH|SAVED_INDEX_REBUILD_MISMATCH"):
                    audit.verify_saved(output)


if __name__ == "__main__":
    unittest.main()
