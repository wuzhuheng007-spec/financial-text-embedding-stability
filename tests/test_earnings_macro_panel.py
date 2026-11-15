"""Replay captured official macro pages and the bounded three-card panel."""

from __future__ import annotations

import hashlib
import json
import re
import unittest
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CAPTURES = ROOT / "artifacts/earnings_20261115/sources/macro"
PANEL = ROOT / "results/news_earnings_macro_20261115/macro_cards.json"


class TextOnly(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def captured_text(stem: str) -> tuple[str, dict]:
    source = CAPTURES / f"{stem}.html"
    receipt = json.loads((CAPTURES / f"{stem}.receipt.json").read_text(encoding="utf-8"))
    content = source.read_bytes()
    assert hashlib.sha256(content).hexdigest() == receipt["sha256"]
    parser = TextOnly()
    parser.feed(content.decode("utf-8", errors="replace"))
    return " ".join(" ".join(parser.parts).split()), receipt


class MacroPanelReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.panel = json.loads(PANEL.read_text(encoding="utf-8"))
        cls.cards = {row["event_id"]: row for row in cls.panel["cards"]}

    def test_cutoff_source_hashes_and_no_unverified_surprises(self) -> None:
        cutoff = datetime.fromisoformat(self.panel["as_of_utc"].replace("Z", "+00:00"))
        self.assertEqual(len(self.cards), 3)
        mapping = {
            "CPI_2026_10": ("cpi_202610", "cpi_202609"),
            "JOBS_2026_10": ("jobs_202610", "jobs_202609"),
            "FOMC_2026_10_28": ("fomc_20261028", "fomc_20260916"),
        }
        for event_id, (current_stem, prior_stem) in mapping.items():
            card = self.cards[event_id]
            current, current_receipt = captured_text(current_stem)
            prior, prior_receipt = captured_text(prior_stem)
            self.assertTrue(current and prior)
            self.assertEqual(card["source_url"], current_receipt["requested_url"])
            self.assertEqual(card["source_sha256"], current_receipt["sha256"])
            self.assertEqual(card["prior_source_url"], prior_receipt["requested_url"])
            self.assertEqual(card["prior_source_sha256"], prior_receipt["sha256"])
            official = datetime.fromisoformat(card["official_time_utc_derived"].replace("Z", "+00:00"))
            self.assertLess(official, cutoff)
            self.assertIsNone(card["first_available_at_utc"])
            self.assertEqual(card["surprise_status"], "NOT_COMPUTED_NO_DATED_CONSENSUS")
            self.assertEqual(card["market_outcome_status"], "NOT_RUN")
            for row in card["values"]:
                self.assertIsNone(row["pre_release_consensus"])
                self.assertEqual(row["consensus_missing_reason"], "NOT_FOUND")

    def test_replayed_macro_claims_are_in_issuer_pages(self) -> None:
        cpi, cpi_receipt = captured_text("cpi_202610")
        cpi_prior, _ = captured_text("cpi_202609")
        jobs, _ = captured_text("jobs_202610")
        jobs_prior, _ = captured_text("jobs_202609")
        fed, _ = captured_text("fomc_20261028")
        fed_prior, _ = captured_text("fomc_20260916")
        self.assertIn("CPI-U) increased 0.4 percent", cpi)
        self.assertIn("after rising 0.6 percent in September", cpi)
        self.assertIn("CPI-U) increased 0.6 percent", cpi_prior)
        self.assertIn("all items less food and energy rose 0.3 percent", cpi)
        self.assertIn("all items less food and energy rose 0.3 percent", cpi_prior)
        self.assertIn("nonfarm payroll employment rose by 165,000", jobs)
        self.assertIn("unemployment rate changed little at 4.1 percent", jobs)
        self.assertIn("from +29,000 to +60,000", jobs)
        self.assertIn("(+29,000) and the unemployment rate (4.2 percent)", jobs_prior)
        self.assertRegex(fed, re.compile(r"maintain the target range.*3-3/4 to 4 percent"))
        self.assertRegex(fed_prior, re.compile(r"raise the target range.*1/4 percentage point to 3-3/4 to 4 percent"))
        self.assertEqual(cpi_receipt["http_status"], 200)


if __name__ == "__main__":
    unittest.main()
