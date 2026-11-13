"""Artificial CSV/ZIP fixtures only: no WRDS rows, identifiers or financial data."""

import csv
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from wrds_catalog import local


class PrivateRowDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / ".wrds_private" / "rows.sqlite"

    def tearDown(self):
        self.temp.cleanup()

    def csv(self, name, header, rows):
        source = self.root / name
        with source.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(header)
            writer.writerows(rows)
        return source

    def cells(self):
        result = local.query_readonly(self.db, "SELECT cells_json FROM records ORDER BY row_ordinal")
        return [json.loads(row[0]) for row in result["rows"]]

    def test_leading_zeros_quoted_multiline_empty_and_unknown_dates_are_unchanged(self):
        rows = [["0007", "CLASS-A", "a quoted\nsecond line, with comma", ".E", ""],
                ["0008", "", 'a "quoted" word', "UNKNOWN_DATE", "000000"]]
        source = self.csv("artificial.csv", ["id", "class", "text", "date", "extra"], rows)
        original = source.read_bytes()
        report = local.import_csv(self.db, source, "synthetic")
        self.assertEqual(self.cells(), rows)
        self.assertEqual(report["row_count"], 2)
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(report["csv_sha256"], hashlib.sha256(original).hexdigest())
        verified = local.verify_import(self.db)
        self.assertEqual(verified["verified_row_count"], 2)

    def test_duplicate_and_blank_headers_retain_every_position(self):
        source = self.csv("headers.csv", ["id", "id", " ID ", ""], [["001", "002", "003", "004"]])
        report = local.import_csv(self.db, source, "synthetic")
        self.assertEqual(report["header_diagnostics"], {
            "exact_duplicate_headers": 1, "normalized_header_collisions": 2, "blank_headers": 1})
        self.assertEqual(local.query_readonly(self.db, "SELECT name FROM headers ORDER BY column_ordinal")["rows"],
                         [["id"], ["id"], [" ID "], [""]])
        self.assertEqual(self.cells(), [["001", "002", "003", "004"]])
        local.verify_import(self.db)

    def test_width_mismatches_and_blank_records_are_preserved(self):
        rows = [["short"], [], ["long", "row", "extra"], ["right", "width"]]
        source = self.csv("width.csv", ["a", "b"], rows)
        report = local.import_csv(self.db, source, "synthetic")
        self.assertEqual((report["row_count"], report["width_mismatch_rows"]), (4, 3))
        self.assertEqual(self.cells(), rows)
        self.assertEqual(local.verify_import(self.db)["verified_row_count"], 4)

    def test_archive_requires_explicit_member_if_multiple(self):
        archive = self.root / "many.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("one.csv", "id\n001\n")
            output.writestr("two.csv", "id\n002\n")
        with self.assertRaisesRegex(local.LocalDatabaseError, "^ARCHIVE_MEMBER_REQUIRED$"):
            local.import_csv(self.db, archive, "synthetic")
        self.assertFalse(self.db.exists())
        local.import_csv(self.db, archive, "synthetic", member="two.csv")
        self.assertEqual(self.cells(), [["002"]])
        local.verify_import(self.db)

    def test_archive_traversal_or_case_collisions_rejected_even_without_extraction(self):
        for number, names in enumerate([["../escape.csv"], ["A.csv", "a.csv"], ["folder\\bad.csv"]]):
            archive = self.root / f"bad-{number}.zip"
            with zipfile.ZipFile(archive, "w") as output:
                for name in names:
                    output.writestr(name, "id\n001\n")
            with self.assertRaisesRegex(local.LocalDatabaseError, "UNSAFE_ARCHIVE_MEMBER|DUPLICATE_ARCHIVE_MEMBER"):
                local.import_csv(self.db, archive, "synthetic")
            self.assertFalse(self.db.exists())

    def test_identical_csv_in_different_transports_shares_content_retains_occurrences(self):
        source = self.csv("plain.csv", ["id"], [["0007"]])
        archive = self.root / "wrapped.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("inside.csv", source.read_bytes())
        first = local.import_csv(self.db, source, "daily_candidate")
        second = local.import_csv(self.db, archive, "another_candidate")
        self.assertEqual(first["content_id"], second["content_id"])
        self.assertTrue(first["new_content"])
        self.assertFalse(second["new_content"])
        self.assertNotEqual(first["source_sha256"], second["source_sha256"])
        result = local.verify_import(self.db)
        self.assertEqual((result["verified_content_count"], result["verified_occurrence_count"],
                          result["verified_row_count"]), (1, 2, 1))

    def test_source_tamper_rejected(self):
        source = self.csv("source.csv", ["id"], [["0007"]])
        local.import_csv(self.db, source, "synthetic")
        source.write_text("id\n0008\n", encoding="utf-8")
        with self.assertRaisesRegex(local.LocalDatabaseError, "SAVED_SOURCE_IDENTITY_MISMATCH"):
            local.verify_import(self.db)

    def test_database_cell_tamper_rejected_even_if_local_hashes_are_changed(self):
        source = self.csv("source.csv", ["id"], [["0007"]])
        local.import_csv(self.db, source, "synthetic")
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE records SET cells_json=?,record_sha256=?,cells_sha256=?",
                               ('["forged"]', "forged", "forged"))
        with self.assertRaisesRegex(local.LocalDatabaseError, "SAVED_RECORD_MISMATCH"):
            local.verify_import(self.db)

    def test_database_header_tamper_rejected(self):
        source = self.csv("source.csv", ["id"], [["0007"]])
        local.import_csv(self.db, source, "synthetic")
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE headers SET name='forged'")
        with self.assertRaisesRegex(local.LocalDatabaseError, "SAVED_HEADER_MISMATCH"):
            local.verify_import(self.db)

    def test_parse_failure_rolls_back_new_and_existing_database(self):
        malformed = self.root / "malformed.csv"
        malformed.write_text('id,text\n001,valid\n002,"unterminated\n', encoding="utf-8")
        with self.assertRaisesRegex(local.LocalDatabaseError, "CSV_PARSE_ERROR"):
            local.import_csv(self.db, malformed, "synthetic")
        self.assertFalse(self.db.exists())
        source = self.csv("good.csv", ["id"], [["0007"]])
        local.import_csv(self.db, source, "synthetic")
        with self.assertRaisesRegex(local.LocalDatabaseError, "CSV_PARSE_ERROR"):
            local.import_csv(self.db, malformed, "synthetic")
        self.assertEqual(local.query_readonly(self.db, "SELECT COUNT(*) FROM contents")["rows"], [[1]])
        self.assertEqual(local.query_readonly(self.db, "SELECT COUNT(*) FROM source_occurrences")["rows"], [[1]])
        local.verify_import(self.db)

    def test_source_change_during_import_rolls_back(self):
        source = self.csv("source.csv", ["id"], [["0007"]])
        original = local._snapshot_source
        calls = 0

        def changed(path, member=None):
            nonlocal calls
            calls += 1
            if calls == 2:
                source.write_text("id\n0008\n", encoding="utf-8")
            return original(path, member)

        with patch.object(local, "_snapshot_source", side_effect=changed):
            with self.assertRaisesRegex(local.LocalDatabaseError, "SOURCE_CHANGED_DURING_IMPORT"):
                local.import_csv(self.db, source, "synthetic")
        self.assertFalse(self.db.exists())

    def test_public_path_rejected_and_external_directory_requires_explicit_opt_in(self):
        source = self.csv("source.csv", ["id"], [["0007"]])
        for path, root in [(self.root / "database/wrds/raw.sqlite", self.root),
                           (self.root / ".wrds_private/database/wrds/raw.sqlite", None)]:
            with self.assertRaisesRegex(local.LocalDatabaseError, "PUBLIC_DATABASE_DIRECTORY_FORBIDDEN"):
                local.import_csv(path, source, "synthetic", output_root=root)
        external = self.root / "external" / "rows.sqlite"
        with self.assertRaisesRegex(local.LocalDatabaseError, "PRIVATE_DATABASE_DIRECTORY_REQUIRED"):
            local.import_csv(external, source, "synthetic")
        local.import_csv(external, source, "synthetic", output_root=self.root / "external")
        self.assertEqual(local.verify_import(external)["verified_row_count"], 1)

    def test_readonly_query_enforces_sqlite_authorizer_and_preserves_column_alias_collisions(self):
        source = self.csv("source.csv", ["id"], [["0007"], ["0008"]])
        local.import_csv(self.db, source, "synthetic")
        result = local.query_readonly(self.db, "SELECT ? AS x, ? AS x", ("first", "second"))
        self.assertEqual(result, {"columns": ["x", "x"], "rows": [["first", "second"]], "truncated": False})
        self.assertTrue(local.query_readonly(self.db, "SELECT cells_json FROM records", limit=1)["truncated"])
        for sql in ("PRAGMA user_version=2", "ATTACH DATABASE ':memory:' AS attached",
                    "DELETE FROM records", "WITH c AS (SELECT 1) DELETE FROM records",
                    "SELECT load_extension('not_an_extension')", "SELECT 1; DELETE FROM records"):
            with self.assertRaises(local.LocalDatabaseError):
                local.query_readonly(self.db, sql)
        self.assertEqual(local.verify_import(self.db)["verified_row_count"], 2)

    def test_public_metadata_database_can_be_queried_but_not_mutated(self):
        public = self.root / "public_catalog.sqlite"
        with sqlite3.connect(public) as connection:
            connection.execute("CREATE TABLE safe_datasets (name TEXT, reported_rows INTEGER)")
            connection.execute("INSERT INTO safe_datasets VALUES (?,?)", ("artificial", 2))
        self.assertEqual(local.query_readonly(public, "SELECT name,reported_rows FROM safe_datasets")["rows"],
                         [["artificial", 2]])
        for sql in ("UPDATE safe_datasets SET reported_rows=9",
                    "WITH c AS (SELECT 1) UPDATE safe_datasets SET reported_rows=9",
                    "PRAGMA user_version=9", "ATTACH DATABASE ':memory:' AS attached"):
            with self.assertRaises(local.LocalDatabaseError):
                local.query_readonly(public, sql)
        self.assertEqual(local.query_readonly(public, "SELECT reported_rows FROM safe_datasets")["rows"], [[2]])
        with self.assertRaisesRegex(local.LocalDatabaseError, "NOT_A_PRIVATE_WRDS_DATABASE"):
            local.verify_import(public)


if __name__ == "__main__":
    unittest.main()
