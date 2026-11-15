"""Synthetic rowspan, fiscal-predecessor, and public-basis checks."""

from __future__ import annotations

import unittest

from bs4 import BeautifulSoup

from scripts.market_observer_financial_text_v2.review_fact_comparisons_v4 import (
    comparison, is_immediately_prior_fiscal_quarter, public_basis,
)


def source_table(prior_period: str) -> BeautifulSoup:
    return BeautifulSoup(
        "<table><tr><td colspan='3' rowspan='2'>(in millions)</td>"
        "<td colspan='6'>GAAP (1)</td><td colspan='6'>Non-GAAP (2)</td></tr>"
        "<tr><td colspan='3'>FQ3-25</td><td colspan='3'>" + prior_period + "</td>"
        "<td colspan='3'>FQ3-25</td><td colspan='3'>" + prior_period + "</td></tr>"
        "<tr><td colspan='3'>Revenue</td><td>$</td><td>100</td><td></td>"
        "<td>$</td><td>90</td><td></td><td>$</td><td>110</td><td></td>"
        "<td>$</td><td>95</td><td></td></tr></table>", "html.parser")


def reviewed() -> dict:
    return {"topic": "revenue", "status": "CODEX_REVIEW_SOURCE_SUPPORTED",
            "final": {"evidence_locator": "table[1]/tr[3]", "value_text": "$ 100",
                      "period_text": "FQ3-25", "unit_text": "million", "currency": "USD",
                      "basis": "GAAP_FIRST_EXPLICIT_COLUMN"},
            "field_evidence": {"unit": [{"dom_path": "table[1]/tr[1]",
                                          "rendered_visible_text": "(in millions)"}]}}


class FiscalComparisonV4Tests(unittest.TestCase):
    def test_rowspan_header_maps_current_and_prior_gaap_columns(self) -> None:
        fields, proof = comparison(reviewed(), {}, source_table("FQ2-25"))
        self.assertEqual(fields["comparison_value_text"], "90")
        self.assertEqual(fields["comparison_period_text"], "FQ2-25")
        self.assertEqual(fields["comparison_status"], "SOURCE_BOUND_PRIOR_FISCAL_QUARTER")
        self.assertIsNone(proof["reason"])

    def test_prior_year_same_quarter_is_not_prior_quarter(self) -> None:
        fields, proof = comparison(reviewed(), {}, source_table("FQ3-24"))
        self.assertIsNone(fields["comparison_value_text"])
        self.assertIn("immediately preceding", proof["reason"])

    def test_equal_period_is_not_prior_quarter(self) -> None:
        fields, _ = comparison(reviewed(), {}, source_table("FQ3-25"))
        self.assertIsNone(fields["comparison_value_text"])

    def test_fiscal_year_turnover(self) -> None:
        self.assertTrue(is_immediately_prior_fiscal_quarter("Q1 FY26", "Q4 FY25"))
        self.assertTrue(is_immediately_prior_fiscal_quarter("FQ1-26", "FQ4-25"))
        self.assertFalse(is_immediately_prior_fiscal_quarter("Q1 FY26", "Q1 FY25"))

    def test_basis_display_normalizes_internal_mapping_rule(self) -> None:
        self.assertEqual(public_basis("GAAP_FIRST_EXPLICIT_COLUMN")[0], "GAAP")
        self.assertEqual(public_basis("ADJUSTED_SOURCE_LABEL")[0], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
