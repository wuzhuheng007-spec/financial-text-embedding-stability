"""Synthetic provenance and fail-closed checks for candidate discovery."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_m5_candidate_discovery_20261114.py"
spec = importlib.util.spec_from_file_location("m5_candidate_discovery", SCRIPT)
assert spec and spec.loader
discovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(discovery)


class CandidateDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scope = {"scan_scope_id": "scope", "screen_private_sha256": "a" * 64,
                      "screen_private_path": "private-screen.json", "development_inventory_sha256": "b" * 64}
        self.source = {"url": "https://data.sec.gov/submissions/CIK0000000001.json",
                       "sha256": "c" * 64, "retrieved_utc": "2026-11-14T00:00:00+00:00"}
        self.raw = {"accessionNumber": "0000000001-21-000001", "filingDate": "2021-03-01",
                    "reportDate": "2020-12-31", "form": "10-K", "primaryDocument": "example.htm"}

    def test_scope_is_deterministic_and_excludes_development(self) -> None:
        relations = {f"{i:010d}": [{"issue_evidence_id": str(i)}] for i in range(1, 50)}
        relations["0000320193"] = [{"issue_evidence_id": "development"}]
        first = discovery.select_scope(relations)
        self.assertEqual(first, discovery.select_scope(dict(reversed(list(relations.items())))))
        self.assertEqual(len(first), 32)
        self.assertNotIn("0000320193", first)

    def test_real_row_requires_real_accession_and_source_hash(self) -> None:
        relation = [{"permno": "12345", "identity_candidate_id": "edge:1", "issue_evidence_id": "issue:1"}]
        row = discovery.filing_row("0000000001", self.raw, self.source, 4, self.scope, "d" * 64, relation, False)
        self.assertEqual(row["accession"], self.raw["accessionNumber"])
        self.assertEqual(row["source_evidence_hash"], self.source["sha256"])
        self.assertEqual(row["source_row_index"], 4)
        self.assertIsNone(row["security_id"])
        self.assertIsNone(row["item1_hash"])
        self.assertFalse(row["formal_admitted"])
        self.assertEqual(row["identity_status"], "unsupported")
        malformed = {**self.raw, "accessionNumber": "invented"}
        with self.assertRaises(ValueError):
            discovery.filing_row("0000000001", malformed, self.source, 4, self.scope, "d" * 64, relation, False)

    def test_duplicate_conflict_is_not_silently_removed(self) -> None:
        a = discovery.filing_row("0000000001", self.raw, self.source, 1, self.scope, "d" * 64, [], False)
        b = {**a, "filing_date": "2021-03-02"}
        with self.assertRaises(ValueError):
            discovery.deduplicate([a, b])
        kept, duplicates = discovery.deduplicate([a, dict(a)])
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(duplicates), 1)

    def test_event_impact_remains_unknown_with_undated_screen(self) -> None:
        row = discovery.filing_row("0000000001", self.raw, self.source, 1, self.scope, "d" * 64, [], False)
        ledger = {"nondevelopment_candidate_rows": [row]}
        scope = {"scan_scope_id": "scope", "candidate_relations": {"0000000001": []}}
        impact = discovery.impact_table(ledger, scope)
        self.assertEqual(impact["known_event_impacts"], 0)
        self.assertEqual(impact["unknown_event_impacts"], 1)
        self.assertEqual(impact["rows"][0]["LINKENDDT_issue_to_event_impact"], "unknown")

    def test_heading_fallback_rejects_toc_and_requires_unique_body(self) -> None:
        body = "The company reports products and services in its operating businesses. " * 60
        toc = "Item 1.\nItem 1A.\nItem 1B.\n"
        rendered = toc + "Item 1.\nBusiness\n" + body + "\nItem 1A.\n" + ("Risk factors follow. " * 140)
        item, locator = discovery.extract_item1_candidate(rendered)
        self.assertEqual(locator["heading_template"], "standalone_item_number")
        self.assertEqual(item, rendered[locator["start_char"]:locator["end_char_exclusive"]])
        self.assertNotEqual(locator["start_char"], 0)
        duplicate = rendered + "\nItem 1.\nBusiness\n" + body + "\nItem 1A.\n" + ("Risk factors follow. " * 140)
        with self.assertRaises(discovery.ExtractionError):
            discovery.extract_item1_candidate(duplicate)

    def test_heading_fallback_accepts_observed_trailing_periods(self) -> None:
        body = "The company reports products and services in its operating businesses. " * 60
        rendered = "Item 1. Business.\n" + body + "\nItem 1A. Risk Factors.\n" + ("Risk factors follow. " * 140)
        item, locator = discovery.extract_item1_candidate(rendered)
        self.assertEqual(locator["heading_template"], "business_trailing_punctuation")
        self.assertTrue(item.startswith("Item 1. Business."))


if __name__ == "__main__":
    unittest.main()
