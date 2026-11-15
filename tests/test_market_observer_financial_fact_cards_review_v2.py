"""Synthetic regressions for the source review's discovered parser misses."""

from __future__ import annotations

import unittest

from bs4 import BeautifulSoup

from scripts.market_observer_financial_text_v2.review_fact_candidates_v2 import (
    micron_narrative_correction, micron_narrative_period, nvidia_report_period,
)


class FactCardReviewCorrectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.soup = BeautifulSoup(
            "<div><span>For the third quarter of 2025, investments in capital expenditures, net</span>"
            "<sup>(2)</sup><span> were $2.66 billion and adjusted free cash flow</span>"
            "<sup>(2)</sup><span> was $1.95 billion.</span></div>", "html.parser")

    def test_split_html_nodes_preserve_capex_value_and_period(self) -> None:
        value, unit, tag = micron_narrative_correction({"topic": "capital_expenditures"}, self.soup)
        self.assertEqual((value, unit), ("$2.66 billion", "billion"))
        self.assertEqual(micron_narrative_period(tag), "FQ3-25")

    def test_split_html_nodes_preserve_adjusted_fcf(self) -> None:
        value, unit, tag = micron_narrative_correction({"topic": "free_cash_flow"}, self.soup)
        self.assertEqual((value, unit), ("$1.95 billion", "billion"))
        self.assertIsNotNone(tag)

    def test_fourth_quarter_and_fiscal_year_title(self) -> None:
        soup = BeautifulSoup("<div>NVIDIA Announces Financial Results for Fourth Quarter and Fiscal 2026</div>",
                             "html.parser")
        period, evidence = nvidia_report_period(soup)
        self.assertEqual(period, "Q4 FY26")
        self.assertIsNotNone(evidence)


if __name__ == "__main__":
    unittest.main()
