"""Focused contract checks; synthetic text only, no model or research labels."""

from __future__ import annotations

import hashlib
import unittest

from bs4 import BeautifulSoup

from scripts.market_observer_financial_text_v2.build_fact_candidates_v2 import (
    first_value, guidance_bounds, period_from_own_evidence,
)
from scripts.market_observer_financial_text_v2.review_fact_candidates import (
    basis_from_exact_table, micron_quarterly_ocf_period, overlapping_header,
    review_one,
)


class FactCardContractTests(unittest.TestCase):
    def test_plus_sign_does_not_become_symmetric_range(self) -> None:
        self.assertIsNone(guidance_bounds("$10 billion + $200 million"))
        self.assertEqual(guidance_bounds("$10 billion ± $200 million")["lower_text"], "$9.8 billion")

    def test_parenthesized_negative_is_preserved(self) -> None:
        self.assertEqual(first_value("Investments in capital expenditures, net (2,660) (3,085)",
                                     "capital_expenditures"), "(2,660)")

    def test_period_never_inherited_from_another_topic(self) -> None:
        own = [{"rendered_visible_text": "Net cash provided by operating activities 11,795 5,102",
                "table_headers": [{"rendered_visible_text": "Nine Months Ended May 29, 2025"}]}]
        self.assertIsNone(period_from_own_evidence(own))

    def test_mixed_header_requires_exact_value_column(self) -> None:
        soup = BeautifulSoup("<table><tr><td colspan='2'>GAAP</td><td colspan='2'>Non-GAAP</td></tr>"
                             "<tr><td>Revenue</td><td>10</td><td>20</td><td>30</td></tr></table>", "html.parser")
        row = soup.find_all("tr")[-1]
        candidate = {"topic": "revenue", "symbol": "MU"}
        basis, evidence = basis_from_exact_table(candidate, row)
        self.assertEqual(basis, "GAAP_FIRST_EXPLICIT_COLUMN")
        self.assertEqual(len(evidence), 2)

    def test_three_month_header_bound_to_first_value_column(self) -> None:
        soup = BeautifulSoup("<table><tr><td></td><td colspan='2'>Three Months Ended</td>"
                             "<td colspan='2'>Six Months Ended</td></tr>"
                             "<tr><td>Operating cash</td><td>100</td><td>90</td><td>300</td><td>250</td></tr></table>",
                             "html.parser")
        row = soup.find_all("tr")[-1]
        self.assertIsNotNone(overlapping_header(soup.table, row, r"^Three Months Ended$"))
        self.assertIsNone(overlapping_header(soup.table, row, r"^Six Months Ended$"))

    def test_micron_quarter_period_requires_value_roundtrip(self) -> None:
        soup = BeautifulSoup("<table><tr><td>3rd Qtr.</td></tr><tr><td>May 29, 2025</td></tr>"
                             "<tr><td>GAAP net cash provided by operating activities</td><td>4,609</td></tr></table>",
                             "html.parser")
        candidate = {"period_hint_from_revenue_topic": "FQ3-25"}
        self.assertEqual(micron_quarterly_ocf_period(candidate, soup, "$4.61 billion")[0], "FQ3-25")
        self.assertIsNone(micron_quarterly_ocf_period(candidate, soup, "$4.51 billion")[0])

    def test_unknown_scale_cannot_be_promoted(self) -> None:
        soup = BeautifulSoup("<table><tr><td>Cloud Memory Business Unit</td></tr>"
                             "<tr><td>Revenue</td><td>$ 4,543</td></tr></table>", "html.parser")
        row = soup.find_all("tr")[-1]
        row_text = "Revenue $ 4,543"
        source_hash = hashlib.sha256(row_text.encode()).hexdigest()
        candidate = {
            "candidate_id": "synthetic", "event_id": "synthetic", "symbol": "MU",
            "filing_date": "2025-09-23", "topic": "segment_revenue",
            "metric_normalized": "segment_revenue", "metric_original": "Revenue",
            "value_text": "$ 4,543", "source_evidence": [{"dom_path": "table[1]/tr[2]",
                "element_visible_text_sha256": source_hash, "rendered_visible_text": row_text,
                "table_headers": []}], "evidence_locator": "table[1]/tr[2]",
            "period_hint_from_revenue_topic": None, "basis_candidate": None,
            "fact_kind": "HISTORICAL_ACTUAL_OR_SOURCE_DEFINED_MEASURE", "range": None,
            "source_url": "https://www.sec.gov/example", "source_document_sha256": "synthetic",
        }
        review = review_one(candidate, soup, {})
        self.assertEqual(review["status"], "CODEX_REVIEW_AMBIGUOUS")
        self.assertIsNone(review["final"]["unit_text"])


if __name__ == "__main__":
    unittest.main()
