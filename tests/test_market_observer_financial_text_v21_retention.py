"""Synthetic checks that v2.1 grades actual adapter strings, not raw metadata."""

import unittest

from scripts.market_observer_financial_text_v21.compare_retention import (
    COMPARISON_SUPPORTED, classify_element_evidence, fact_grade, field_retention,
)


class FinancialTextV21RetentionTests(unittest.TestCase):
    def test_source_bound_header_and_row_survive_as_exact_fragments(self):
        block = {"raw_text": "Revenue $ 9,301 $ 8,053",
                 "text": "period: FQ3-25 | FQ2-25; source_header_rows: "
                         "(in millions) GAAP | FQ3-25 FQ2-25; "
                         "source_row: Revenue $ 9,301 $ 8,053"}
        for evidence in ("Revenue $ 9,301 $ 8,053", "FQ3-25 FQ2-25", "(in millions) GAAP"):
            row = classify_element_evidence("LOCATED", [{"rendered_visible_text": evidence}],
                                            old_text="", selected_text=block["text"],
                                            full_block_text=block["text"],
                                            selected_blocks=[block], all_blocks=[block])
            self.assertEqual(row["v21_selected"], "COMPLETE")
            self.assertEqual(row["old"], "LOST")

    def test_raw_metadata_alone_is_not_selected_model_input(self):
        block = {"raw_text": "FQ3-25 FQ2-25", "text": "period: UNKNOWN"}
        got = field_retention([{"rendered_visible_text": "FQ3-25 FQ2-25"}],
                              old_text="", selected_text=block["text"],
                              full_block_text=block["text"], selected_blocks=[block],
                              all_blocks=[block])
        self.assertEqual(got["v21_selected"], "AMBIGUOUS_REFORMATTED")

    def test_missing_prior_comparison_still_blocks_complete(self):
        review = {"status": "CODEX_REVIEW_SOURCE_SUPPORTED",
                  "final": {"basis": "GAAP", "comparison_status": COMPARISON_SUPPORTED,
                            "comparison_basis": "GAAP"}}
        names = ("metric", "value", "period", "unit", "basis",
                 "comparison_value", "comparison_period", "comparison_unit", "comparison_basis")
        fields = {name: {"v21_selected": "PRESENT"} for name in names}
        fields["comparison_period"]["v21_selected"] = "MISSING"
        self.assertEqual(fact_grade(review, fields, "v21_selected", footnote_unresolved=False,
                                    literal_value_present=True,
                                    literal_comparison_value_present=True), "PARTIAL")


if __name__ == "__main__":
    unittest.main()
