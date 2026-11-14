"""Bounded candidate-catalog contract checks, including the saved input."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_macro_tech_catalog import validate  # noqa: E402


class CatalogContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = json.loads((
            ROOT / "results/news_macro_tech_20261115/candidate_events.json"
        ).read_text(encoding="utf-8"))

    def test_saved_catalog_matches_bounded_receipt_counts(self) -> None:
        summary = validate(self.catalog)
        receipt = json.loads((
            ROOT / "results/news_macro_tech_20261115/catalog_build_receipt.json"
        ).read_text(encoding="utf-8"))
        for key, value in summary.items():
            self.assertEqual(receipt[key], value)

    def test_unbound_date_source_is_rejected(self) -> None:
        bad = copy.deepcopy(self.catalog)
        bad["events"][0]["date_evidence"]["url"] = "https://example.invalid/date"
        with self.assertRaisesRegex(ValueError, "Unbound event date"):
            validate(bad)

    def test_outcome_and_surprise_are_rejected(self) -> None:
        bad = copy.deepcopy(self.catalog)
        bad["events"][0]["market_return_status"] = "LABELLED"
        with self.assertRaisesRegex(ValueError, "Market outcome"):
            validate(bad)
        bad = copy.deepcopy(self.catalog)
        bad["events"][0]["surprise_status"] = "COMPUTED"
        with self.assertRaisesRegex(ValueError, "Unverified numerical surprise"):
            validate(bad)

    def test_saved_drafts_are_bound_and_limited(self) -> None:
        drafts = json.loads((
            ROOT / "results/news_macro_tech_20261115/analyst_drafts.json"
        ).read_text(encoding="utf-8"))
        catalog_bytes = (
            ROOT / "results/news_macro_tech_20261115/candidate_events.json"
        ).read_bytes()
        self.assertEqual(drafts["catalog_sha256"], hashlib.sha256(catalog_bytes).hexdigest())
        self.assertEqual(drafts["author_role"], "AI_ANALYST_DRAFT")
        self.assertEqual(drafts["model_calls"], 0)
        self.assertEqual(drafts["financial_labels"], "NOT_RUN")
        self.assertEqual(len(drafts["drafts"]), 2)
        ids = {event["id"] for event in self.catalog["events"]}
        for draft in drafts["drafts"]:
            self.assertIn(draft["event_id"], ids)
            self.assertEqual(draft["role"], "AI_ANALYST_DRAFT")
            paths = draft["CHANNEL_HYPOTHESES"]["paths"]
            self.assertLessEqual(len(paths), 3)
            for path in paths:
                self.assertLessEqual(len(path["edges"]), 2)
            self.assertEqual(draft["TECH_EXPOSURES"]["direction"], "UNKNOWN")

    def test_g01_event_date_facts_use_june_14_source_only(self) -> None:
        card = next(event for event in self.catalog["events"] if event["id"] == "G01")
        self.assertEqual(card["date"], "2026-06-14")
        self.assertEqual(len(card["source_urls"]), 1)
        self.assertTrue(all(row["url"] == card["date_evidence"]["url"]
                            for row in card["fact_evidence"]))


if __name__ == "__main__":
    unittest.main()
