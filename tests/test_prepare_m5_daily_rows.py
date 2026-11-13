"""SYNTHETIC_TEST_ONLY checks for the M5 Daily preparation tool."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import prepare_m5_daily_rows as daily


class DailyPreparationSyntheticTests(unittest.TestCase):
    def test_first_segment_maps_every_row_and_quarantines_field_conflicts(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "synthetic.csv"
            header = ["PERMNO", "DlyCalDt", "DlyClose", "DlyPrcFlg"]
            rows = [
                ["00100.0", "2020-01-02", "1", "A"],
                ["00100.0", "2020-01-02", "1", "A"],
                ["101", "2020-01-03", "1", "A"],
                ["101", "2020-01-03", "1.0", "A"],
                ["102", "2020-01-06", "", "A"],
                ["102", "2020-01-06", "NA", "A"],
                ["103", "2020-01-07", "2", "A"],
            ]
            with path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(header)
                writer.writerows(rows)
            spec = {"segment": 0, "csv_path": path, "csv_header": header,
                    "row_count": len(rows),
                    "summary": {"duplicate_key_group_count": 3,
                                "duplicate_extra_row_count": 3,
                                "conflicting_key_group_count": 2}}
            with sqlite3.connect(":memory:") as db:
                daily.create_row_map(db)
                result = daily._read_first_segment(spec, db)
                statuses = [row[0] for row in db.execute(
                    "SELECT disposition FROM row_map ORDER BY source_row")]
                self.assertEqual(result["raw_rows"], 7)
                self.assertEqual(result["key_groups"], 4)
                self.assertEqual(result["equal_groups"], 1)
                self.assertEqual(result["conflict_groups"], 2)
                self.assertEqual(statuses, ["EQUAL_COLLAPSED"] * 2
                                 + ["CONFLICT_QUARANTINE"] * 4 + ["SINGLETON"])
                self.assertEqual(db.execute(
                    "SELECT COUNT(*) FROM row_map WHERE representative_row IS NULL"
                ).fetchone()[0], 4)
                self.assertEqual(db.execute(
                    "SELECT representative_row FROM row_map WHERE source_row=3"
                ).fetchone()[0], 2)
                self.assertEqual(db.execute(
                    "SELECT permno FROM row_map WHERE source_row=2"
                ).fetchone()[0], "100")
                db.execute("CREATE INDEX row_map_key ON row_map(segment,permno,session)")
                groups = daily.group_statistics(db)
                self.assertEqual(groups[0]["key_groups"], 4)
                self.assertEqual(groups[0]["equal_groups"], 1)
                self.assertEqual(groups[0]["conflict_groups"], 2)
                db.execute("UPDATE row_map SET disposition='SINGLETON' WHERE source_row=3")
                with self.assertRaisesRegex(daily.PreparationError,
                                            "SAVED_GROUP_SEMANTICS_INVALID"):
                    daily.group_statistics(db)

    def test_first_segment_rejects_invalid_calendar_date(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "synthetic.csv"
            with path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["PERMNO", "DlyCalDt", "DlyClose"])
                writer.writerow(["00042.0", "2020-02-30", "1"])
            spec = {"segment": 0, "csv_path": path,
                    "csv_header": ["PERMNO", "DlyCalDt", "DlyClose"],
                    "row_count": 1, "summary": {"duplicate_key_group_count": 0,
                                                "duplicate_extra_row_count": 0,
                                                "conflicting_key_group_count": 0}}
            with sqlite3.connect(":memory:") as db:
                daily.create_row_map(db)
                with self.assertRaisesRegex(daily.PreparationError,
                                            "FIRST_SEGMENT_DATE_INVALID"):
                    daily._read_first_segment(spec, db)

    def test_sparse_missing_counts_keys_once_and_excludes_conflict(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "synthetic.csv"
            header = ["PERMNO", "DlyCalDt", "DlyClose"]
            rows = [
                ["10", "2020-01-02", ""], ["10", "2020-01-02", ""],
                ["11", "2020-01-03", "NA"], ["11", "2020-01-03", "NA"],
                ["12", "2020-01-06", ""], ["12", "2020-01-06", "NA"],
            ]
            with path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(header)
                writer.writerows(rows)
            spec = {"segment": 0, "csv_path": path, "csv_header": header,
                    "row_count": 6,
                    "summary": {"duplicate_key_group_count": 3,
                                "duplicate_extra_row_count": 3,
                                "conflicting_key_group_count": 1}}
            with sqlite3.connect(":memory:") as db:
                daily.create_row_map(db)
                daily._read_first_segment(spec, db)
                stats = daily.missing_statistics(db, [spec])
                self.assertEqual(stats["observed_keys_with_any_missing_field"], 2)
                self.assertEqual(stats["observed_key_blank_counts_by_field"]["dlyclose"], 1)
                self.assertEqual(stats["observed_key_null_literal_counts_by_field"]["dlyclose"], 1)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM availability_missing")
                                 .fetchone()[0], 3)

    def _saved_fixture(self, directory: Path, conflicting: bool = False) -> dict:
        index = directory / "synthetic.sqlite3"
        exceptions = directory / "synthetic.jsonl"
        row = ["100", "2020-01-02", "1"]
        serialized = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        with closing(sqlite3.connect(index)) as db:
            db.execute("CREATE TABLE record_key (permno TEXT, session TEXT, first_row INT, "
                       "first_cells TEXT, first_sha TEXT, has_duplicate INT, has_conflict INT)")
            db.execute("INSERT INTO record_key VALUES (?,?,?,?,?,?,?)",
                       ("100", "2020-01-02", 2, serialized, digest, 1, 0))
            db.execute("INSERT INTO record_key VALUES (?,?,?,?,?,?,?)",
                       ("101", "2020-01-03", 4, serialized, digest, 0, 0))
            db.commit()
        entry = {"permno": "100", "dlycaldt": "2020-01-02", "first_row_number": 2,
                 "row_number": 3, "row_sha256": "0" * 64 if conflicting else digest,
                 "codes": ["PERMNO_DATE_DUPLICATE"]}
        exceptions.write_text(json.dumps(entry) + "\n", encoding="utf-8")
        return {"segment": 1, "index_path": index, "exceptions_path": exceptions,
                "csv_header": ["PERMNO", "DlyCalDt", "DlyClose"],
                "row_count": 3,
                "summary": {"duplicate_key_group_count": 1,
                            "duplicate_extra_row_count": 1,
                            "conflicting_key_group_count": 0}}

    def test_saved_full_field_index_reuses_equal_rows_with_all_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            spec = self._saved_fixture(Path(temporary))
            with sqlite3.connect(":memory:") as db:
                daily.create_row_map(db)
                result = daily._read_saved_index(spec, db)
                self.assertEqual(result["raw_rows"], 3)
                self.assertEqual(result["key_groups"], 2)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM row_map").fetchone()[0], 3)
                self.assertEqual(db.execute("SELECT representative_row FROM row_map "
                                            "WHERE source_row=3").fetchone()[0], 2)

    def test_saved_index_rejects_false_equality_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            spec = self._saved_fixture(Path(temporary), conflicting=True)
            with sqlite3.connect(":memory:") as db:
                daily.create_row_map(db)
                with self.assertRaisesRegex(daily.PreparationError,
                                            "SAVED_FULL_FIELD_EQUALITY_MISMATCH"):
                    daily._read_saved_index(spec, db)

    def test_fixed_horizon_does_not_advance_to_later_price(self):
        sessions = ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"]
        endpoint, available = daily.fixed_horizon_presence(
            sessions, "2020-01-02", {"2020-01-07"}, 2)
        self.assertEqual(endpoint, "2020-01-06")
        self.assertFalse(available)
        with self.assertRaisesRegex(daily.PreparationError, "HORIZON_INVALID"):
            daily.fixed_horizon_presence(sessions, "2020-01-02", set(), 0)

    def test_source_hash_change_and_unaccepted_decision_stop_before_output(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temporary:
            directory = Path(temporary)
            source = directory / "synthetic.csv"
            source.write_text("SYNTHETIC_TEST_ONLY\n", encoding="utf-8")
            spec = {"csv_path": source, "csv_sha256": daily.sha256(source),
                    "index_path": None, "exceptions_path": None}
            daily.verify_source_hashes([spec])
            source.write_text("CHANGED SYNTHETIC_TEST_ONLY\n", encoding="utf-8")
            with self.assertRaisesRegex(daily.PreparationError,
                                        "DAILY_CSV_HASH_MISMATCH"):
                daily.verify_source_hashes([spec])
            output = directory / "run"
            with mock.patch.object(daily, "validate_policy_and_decision",
                                   side_effect=daily.PreparationError(
                                       "PREPARATION_DECISION_MISSING")):
                with redirect_stdout(io.StringIO()) as capture:
                    code = daily.main(["run", "--output", str(output)])
            self.assertEqual(code, 2)
            self.assertFalse(output.exists())
            self.assertEqual(capture.getvalue().strip(),
                             "M5_DAILY_PREPARATION_FAILED PREPARATION_DECISION_MISSING")

    def test_inventory_version_and_header_coverage_must_match(self):
        summary = {"csv_header": ["PERMNO", "DlyCalDt", "DlyClose"],
                   "per_column_blank_counts": {"permno": 0, "dlycaldt": 0,
                                               "dlyclose": 0},
                   "per_column_null_literal_counts": {"permno": 0,
                                                      "dlycaldt": 0,
                                                      "dlyclose": 0}}
        self.assertEqual(daily.validate_header_coverage(summary, None),
                         {"permno", "dlycaldt", "dlyclose"})
        with self.assertRaisesRegex(daily.PreparationError,
                                    "DAILY_HEADER_SET_CHANGED"):
            daily.validate_header_coverage(summary, {"permno", "dlycaldt"})
        del summary["per_column_blank_counts"]["dlyclose"]
        with self.assertRaisesRegex(daily.PreparationError,
                                    "DAILY_MISSING_FIELD_COVERAGE_INVALID"):
            daily.validate_header_coverage(summary, None)
        with tempfile.TemporaryDirectory() as temporary:
            inventory = Path(temporary) / "inventory.json"
            inventory.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(daily.PreparationError,
                                        "EIGHT_SEGMENT_INVENTORY_VERSION_CHANGED"):
                daily.source_specs(inventory)

    def test_verify_output_recomputes_groups_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temporary:
            output = Path(temporary) / "run"
            output.mkdir()
            db_path = output / "derived_row_index_private.sqlite3"
            with closing(sqlite3.connect(db_path)) as db:
                daily.create_row_map(db)
                for segment in range(8):
                    db.execute("INSERT INTO row_map VALUES (?,?,?,?,?,?)",
                               (segment, 2, str(segment + 1), "2020-01-02", 2,
                                "SINGLETON"))
                db.execute("CREATE INDEX row_map_key ON row_map(segment,permno,session)")
                fingerprint, _ = daily.mapping_fingerprint(db)
                db.commit()
            segment_specs = [{"row_count": 1, "label": f"synthetic_{i}", "year": 2020,
                              "segment": i, "csv_header": ["permno", "dlycaldt"],
                              "csv_path": "SYNTHETIC_TEST_ONLY",
                              "csv_sha256": "synthetic", "summary_path": "synthetic",
                              "summary_sha256": "synthetic",
                              "manifest_path": "synthetic", "manifest_sha256": "synthetic",
                              "index_path": None, "index_sha256": None,
                              "exceptions_path": None, "exceptions_sha256": None,
                              "summary": {"per_column_blank_counts":
                                          {"permno": 0, "dlycaldt": 0},
                                          "per_column_null_literal_counts":
                                          {"permno": 0, "dlycaldt": 0}}}
                             for i in range(8)]
            segment_summary = [{"segment": f"synthetic_{i}", "year": 2020,
                                "raw_rows": 1, "key_groups": 1, "equal_groups": 0,
                                "conflict_groups": 0, "unresolved_groups": 0,
                                "duplicate_extra_rows": 0,
                                "equality_evidence": (
                                    "NEW_FULL_FIELD_PARSED_CSV_SCAN" if i == 0 else
                                    "SAVED_FULL_FIELD_INDEX_AND_EXCEPTION_HASHES")}
                               for i in range(8)]
            summary = {"mapping_logical_sha256": fingerprint, "raw_rows": 8,
                       "key_normalizer_code_sha256": "n",
                       "source_inventory_sha256": "i", "policy_sha256": "p",
                       "decision_receipt_sha256": "d",
                       "execution_code_sha256": daily.sha256(Path(daily.__file__)),
                       "formal_financial_experiment_status": "NOT_RUN",
                       "unknown_exit_status": "NOT_CERTIFIED",
                       "historical_attribute_status": "NOT_CERTIFIED",
                       "absent_security_sessions": None,
                       "accepted_price_field_or_adjustment_rule": None,
                       "missing_field_unit":
                       "RAW_LOGICAL_ROWS_FROM_SAVED_AUDITS_NOT_DERIVED_CLOSE_VALIDITY",
                       "per_field_raw_blank_counts": {"permno": 0, "dlycaldt": 0},
                       "per_field_raw_null_literal_counts": {"permno": 0, "dlycaldt": 0},
                       "singleton_rows": 8, "rows_in_equal_groups": 0,
                       "rows_in_conflict_groups": 0,
                       "rows_with_unresolved_equality": 0,
                       "key_groups": 8, "equal_key_groups": 0,
                       "conflict_key_groups": 0, "unresolved_key_groups": 0,
                       "derived_candidate_rows": 8, "quarantined_key_groups": 0,
                       "per_segment": segment_summary,
                       "observed_missing_statistics": {
                           "observed_keys_with_any_missing_field": 0,
                           "observed_keys_with_blank_field": 0,
                           "observed_keys_with_null_literal_field": 0,
                           "observed_key_blank_counts_by_field": {"dlycaldt": 0, "permno": 0},
                           "observed_key_null_literal_counts_by_field": {"dlycaldt": 0, "permno": 0},
                           "unit": "NONQUARANTINED_SECURITY_SESSION_KEY_OBSERVED_FIELDS_ONLY"},
                       "formal_event_mask": None,
                       "admitted_observations": None}
            summary_path = output / "summary_safe.json"
            summary_path.write_bytes(daily.json_bytes(summary))
            bindings = {"policy_sha256": "p", "decision_sha256": "d",
                        "candidate_sha256": "c", "normalizer_code_sha256": "n"}

            def write_manifest():
                manifest = {"schema": "m5_daily_derived_preparation_manifest_private_v1",
                            "inventory_sha256": "i", "policy_sha256": "p",
                            "decision_sha256": "d", "candidate_sha256": "c",
                            "normalizer_code_sha256": "n",
                            "code_path": str(Path(daily.__file__).resolve()),
                            "inventory_path": str(daily.INVENTORY),
                            "policy_path": str(daily.POLICY),
                            "decision_path": str(daily.DECISION),
                            "sources": [daily.source_record(spec) for spec in segment_specs],
                            "code_sha256": daily.sha256(Path(daily.__file__)),
                            "files": {"derived_row_index_private.sqlite3":
                                      daily.sha256(db_path),
                                      "summary_safe.json": daily.sha256(summary_path)}}
                (output / "output_manifest_private.json").write_bytes(
                    daily.json_bytes(manifest))

            write_manifest()
            with mock.patch.object(daily, "validate_policy_and_decision",
                                   return_value=bindings), \
                    mock.patch.object(daily, "source_specs",
                                      return_value=(segment_specs, "i")), \
                    mock.patch.object(daily, "verify_source_hashes"):
                self.assertEqual(daily.verify_output(output)["raw_rows"], 8)
                manifest_path = output / "output_manifest_private.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["sources"][0]["csv_sha256"] = "tampered"
                manifest_path.write_bytes(daily.json_bytes(manifest))
                with self.assertRaisesRegex(daily.PreparationError,
                                            "SAVED_SOURCE_RECORD_CHANGED"):
                    daily.verify_output(output)
                write_manifest()
                summary["unknown_exit_status"] = "CERTIFIED"
                summary_path.write_bytes(daily.json_bytes(summary))
                write_manifest()
                with self.assertRaisesRegex(daily.PreparationError,
                                            "SAVED_SOURCE_BINDING_CHANGED"):
                    daily.verify_output(output)
                summary["unknown_exit_status"] = "NOT_CERTIFIED"
                summary_path.write_bytes(daily.json_bytes(summary))
                write_manifest()
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                del manifest["files"]["summary_safe.json"]
                manifest_path.write_bytes(daily.json_bytes(manifest))
                with self.assertRaisesRegex(daily.PreparationError,
                                            "SAVED_OUTPUT_FILE_SET_INVALID"):
                    daily.verify_output(output)
                write_manifest()
                extra = output / "unexpected.txt"
                extra.write_text("SYNTHETIC_TEST_ONLY", encoding="utf-8")
                with self.assertRaisesRegex(daily.PreparationError,
                                            "SAVED_OUTPUT_FILE_SET_INVALID"):
                    daily.verify_output(output)
                extra.unlink()
                summary["key_groups"] = 7
                summary_path.write_bytes(daily.json_bytes(summary))
                with self.assertRaisesRegex(daily.PreparationError,
                                            "SAVED_OUTPUT_HASH_MISMATCH"):
                    daily.verify_output(output)
                write_manifest()
                with self.assertRaisesRegex(daily.PreparationError,
                                            "SAVED_MAPPING_OR_STATUS_MISMATCH"):
                    daily.verify_output(output)
                summary["key_groups"] = 8
                summary_path.write_bytes(daily.json_bytes(summary))
                summary["observed_missing_statistics"]["observed_keys_with_any_missing_field"] = 1
                summary_path.write_bytes(daily.json_bytes(summary))
                write_manifest()
                with self.assertRaisesRegex(daily.PreparationError,
                                            "SAVED_MAPPING_OR_STATUS_MISMATCH"):
                    daily.verify_output(output)
                summary["observed_missing_statistics"]["observed_keys_with_any_missing_field"] = 0
                summary_path.write_bytes(daily.json_bytes(summary))
                with closing(sqlite3.connect(db_path)) as db:
                    db.execute("UPDATE row_map SET disposition='EQUAL_COLLAPSED' "
                               "WHERE segment=0")
                    db.commit()
                write_manifest()
                with self.assertRaisesRegex(daily.PreparationError,
                                            "SAVED_GROUP_SEMANTICS_INVALID"):
                    daily.verify_output(output)

    def test_policy_requires_version_bound_decision_and_no_full_m5_acceptance(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temporary:
            directory = Path(temporary)
            source_path = directory / "source.json"
            source_path.write_text("SYNTHETIC_TEST_ONLY\n", encoding="utf-8")
            policy = {
                "schema": daily.EXPECTED_POLICY_SCHEMA,
                "preparation_rules_researcher_accepted": True,
                "full_m5_protocol_researcher_accepted": False,
                "original_candidate_sha256_unchanged": daily.EXPECTED_CANDIDATE_SHA,
                "rule_ids": ["candidate_link_screening", "fixed_2020_starting_scope",
                             "derived_daily_and_availability"],
                "derived_daily_and_availability": {
                    "collapse_condition": "ALL_PARSED_SOURCE_COLUMNS_EQUAL_WITHIN_SAME_KEY",
                    "final_event_mask_status": "NOT_CERTIFIED"},
            }
            policy_path = directory / "policy.json"
            policy_path.write_bytes(daily.json_bytes(policy))
            decision = {
                "schema": "m5_local_preparation_decision_receipt_v1",
                "decision_scope": "ACCEPTED_ENGINEERING_PREPARATION_ONLY",
                "preparation_rules_researcher_accepted": True,
                "full_m5_protocol_researcher_accepted": False,
                "formal_financial_experiment_status": "NOT_RUN",
                "accepted_policy_sha256": daily.sha256(policy_path),
                "original_round8_candidate_sha256": daily.EXPECTED_CANDIDATE_SHA,
                "accepted_rule_ids": policy["rule_ids"],
                "original_message_exact_utc": None,
                "local_receipt_created_at_utc": "SYNTHETIC_TEST_ONLY",
                "private_source_relative_path": "source.json",
                "private_source_sha256": daily.sha256(source_path),
            }
            decision_path = directory / "decision.json"
            decision_path.write_bytes(daily.json_bytes(decision))

            def validate_fixture():
                with mock.patch.object(daily, "EXPECTED_POLICY_SHA",
                                       daily.sha256(policy_path)), \
                        mock.patch.object(daily, "EXPECTED_DECISION_SHA",
                                          daily.sha256(decision_path)):
                    return daily.validate_policy_and_decision(policy_path,
                                                              decision_path)

            validate_fixture()
            decision["full_m5_protocol_researcher_accepted"] = True
            decision_path.write_bytes(daily.json_bytes(decision))
            with self.assertRaisesRegex(daily.PreparationError,
                                        "PREPARATION_DECISION_BINDING_INVALID"):
                validate_fixture()
            decision["full_m5_protocol_researcher_accepted"] = False
            decision["accepted_policy_sha256"] = daily.sha256(policy_path)
            decision_path.write_bytes(daily.json_bytes(decision))
            source_path.write_text("CHANGED SYNTHETIC_TEST_ONLY\n", encoding="utf-8")
            with self.assertRaisesRegex(daily.PreparationError,
                                        "PREPARATION_DECISION_SOURCE_CHANGED"):
                validate_fixture()
            decision["full_m5_protocol_researcher_accepted"] = False
            decision["accepted_policy_sha256"] = "0" * 64
            decision_path.write_bytes(daily.json_bytes(decision))
            with self.assertRaisesRegex(daily.PreparationError,
                                        "PREPARATION_DECISION_BINDING_INVALID"):
                validate_fixture()
            with self.assertRaisesRegex(daily.PreparationError,
                                        "PREPARATION_POLICY_VERSION_CHANGED"):
                daily.validate_policy_and_decision(policy_path, decision_path)


if __name__ == "__main__":
    unittest.main()
