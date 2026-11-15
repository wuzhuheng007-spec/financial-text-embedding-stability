"""Synthetic source-cell checks for prior-quarter comparison overlay."""

from __future__ import annotations

import unittest

from bs4 import BeautifulSoup

from scripts.market_observer_financial_text_v2.review_fact_comparisons import comparison


def review(period: str = "Q1 FY26", topic: str = "revenue") -> dict:
    return {"topic": topic, "status": "CODEX_REVIEW_SOURCE_SUPPORTED",
            "final": {"evidence_locator": "table[1]/tr[3]", "value_text": "$100",
                      "period_text": period, "unit_text": "million", "currency": "USD",
                      "basis": "GAAP"},
            "field_evidence": {"unit": [{"dom_path": "table[1]/tr[2]",
                                          "rendered_visible_text": "($ in millions)"}]}}


class FactComparisonTests(unittest.TestCase):
    def test_same_row_prior_quarter_gaap_value(self) -> None:
        soup = BeautifulSoup("<table><tr><td colspan='3'>GAAP</td></tr>"
                             "<tr><td>($ in millions)</td><td>Q1 FY26</td><td>Q4 FY25</td></tr>"
                             "<tr><td>Revenue</td><td>$100</td><td>$90</td></tr></table>", "html.parser")
        fields, proof = comparison(review(), {}, soup)
        self.assertEqual(fields["comparison_value_text"], "$90")
        self.assertEqual(fields["comparison_period_text"], "Q4 FY25")
        self.assertEqual(fields["comparison_basis"], "GAAP")
        self.assertIsNotNone(proof["comparison_value"])

    def test_mixed_columns_choose_prior_gaap_not_non_gaap(self) -> None:
        soup = BeautifulSoup("<table><tr><td></td><td colspan='2'>GAAP (1)</td>"
                             "<td colspan='2'>Non-GAAP (2)</td></tr>"
                             "<tr><td>Units</td><td>FQ3-25</td><td>FQ2-25</td>"
                             "<td>FQ3-25</td><td>FQ2-25</td></tr>"
                             "<tr><td>Revenue</td><td>100</td><td>90</td><td>110</td><td>95</td></tr></table>",
                             "html.parser")
        old = review("FQ3-25")
        old["final"].update(value_text="100", basis="GAAP_FIRST_EXPLICIT_COLUMN")
        fields, _ = comparison(old, {}, soup)
        self.assertEqual(fields["comparison_value_text"], "90")
        self.assertEqual(fields["comparison_period_text"], "FQ2-25")

    def test_missing_period_header_keeps_comparison_unknown(self) -> None:
        soup = BeautifulSoup("<table><tr><td colspan='3'>GAAP</td></tr>"
                             "<tr><td>Revenue</td><td>$100</td><td>$90</td></tr></table>", "html.parser")
        old = review()
        old["final"]["evidence_locator"] = "table[1]/tr[2]"
        fields, proof = comparison(old, {}, soup)
        self.assertIsNone(fields["comparison_value_text"])
        self.assertIn("Fiscal period", proof["reason"])

    def test_split_dollar_cells_stay_in_same_fiscal_gaap_group(self) -> None:
        soup = BeautifulSoup("<table><tr><td></td><td colspan='6'>GAAP (1)</td>"
                             "<td colspan='6'>Non-GAAP (2)</td></tr>"
                             "<tr><td>Units</td><td colspan='3'>FQ3-25</td>"
                             "<td colspan='3'>FQ2-25</td><td colspan='3'>FQ3-25</td>"
                             "<td colspan='3'>FQ2-25</td></tr>"
                             "<tr><td>Revenue</td><td>$</td><td>100</td><td></td>"
                             "<td>$</td><td>90</td><td></td><td>$</td><td>110</td>"
                             "<td></td><td>$</td><td>95</td><td></td></tr></table>", "html.parser")
        old = review("FQ3-25")
        old["final"].update(value_text="$ 100", basis="GAAP_FIRST_EXPLICIT_COLUMN")
        fields, _ = comparison(old, {}, soup)
        self.assertEqual(fields["comparison_value_text"], "90")
        self.assertEqual(fields["comparison_period_text"], "FQ2-25")

    def test_guidance_has_no_invented_prior_comparison(self) -> None:
        fields, _ = comparison(review(topic="guidance"), {}, BeautifulSoup("<div>Revenue outlook $100</div>", "html.parser"))
        self.assertIsNone(fields["comparison_value_text"])


if __name__ == "__main__":
    unittest.main()
