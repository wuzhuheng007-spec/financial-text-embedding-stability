"""Focused source-context retention checks; synthetic inputs only."""

import unittest

from scripts.market_observer_financial_text_v2.compare_retention import (
    COMPARISON_SUPPORTED, classify_element_evidence, fact_grade, field_retention,
)


class RetentionChecks(unittest.TestCase):
    def test_old_clipped_row_v2_selected_table_context(self):
        evidence = [{"rendered_visible_text": "Revenue $1,250"}]
        table_block = {"raw_text": "Revenue $1,250",
                       "text": "TABLE 1; period: Three months ended 2025; units: USD millions; col 1: Revenue | col 2: $1,250"}
        out = classify_element_evidence("LOCATED", evidence, old_text="unrelated prefix",
                                        selected_text=table_block["text"], full_block_text=table_block["text"],
                                        selected_blocks=[table_block], all_blocks=[table_block])
        self.assertEqual(out["old"], "LOST")
        self.assertEqual(out["v2_selected"], "AMBIGUOUS_REFORMATTED")

    def test_missing_header_cannot_be_complete_field_evidence(self):
        evidence = [{"rendered_visible_text": "USD millions"}]
        block = {"raw_text": "Revenue 1,250", "text": "col 1: Revenue | col 2: 1,250"}
        out = field_retention(evidence, old_text="Revenue 1,250", selected_text=block["text"],
                              full_block_text=block["text"], selected_blocks=[block], all_blocks=[block])
        self.assertEqual(out["old"], "MISSING")
        self.assertEqual(out["v2_selected"], "MISSING")

    def test_unlinked_footnote_blocks_complete_status(self):
        review = {"status": "CODEX_REVIEW_SOURCE_SUPPORTED", "final": {"basis": None}}
        fields = {field: {"old": "PRESENT"} for field in ("metric", "value", "period", "unit", "basis")}
        self.assertEqual(fact_grade(review, fields, "old", footnote_unresolved=True,
                                    literal_value_present=True), "AMBIGUOUS")
        self.assertEqual(fact_grade(review, fields, "old", footnote_unresolved=False,
                                    literal_value_present=True), "COMPLETE_KNOWN_FIELDS_BASIS_UNKNOWN")
        self.assertEqual(fact_grade(review, fields, "old", footnote_unresolved=False,
                                    literal_value_present=False), "PARTIAL")
        review["final"]["basis"] = "GAAP"
        self.assertEqual(fact_grade(review, fields, "old", footnote_unresolved=False,
                                    literal_value_present=True), "COMPLETE")

    def test_supported_prior_quarter_requires_its_own_period_and_value(self):
        review = {"status": "CODEX_REVIEW_SOURCE_SUPPORTED",
                  "final": {"basis": "GAAP", "comparison_status": COMPARISON_SUPPORTED,
                            "comparison_basis": "GAAP"}}
        fields = {field: {"old": "PRESENT"} for field in
                  ("metric", "value", "period", "unit", "basis",
                   "comparison_value", "comparison_period", "comparison_unit", "comparison_basis")}
        self.assertEqual(fact_grade(review, fields, "old", footnote_unresolved=False,
                                    literal_value_present=True,
                                    literal_comparison_value_present=False), "PARTIAL")
        fields["comparison_period"]["old"] = "MISSING"
        self.assertEqual(fact_grade(review, fields, "old", footnote_unresolved=False,
                                    literal_value_present=True,
                                    literal_comparison_value_present=True), "PARTIAL")


if __name__ == "__main__":
    unittest.main()
