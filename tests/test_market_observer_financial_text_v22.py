"""Synthetic checks for source-bound, complete-group input engineering v2.2."""
import hashlib
import unittest

from scripts.market_observer_financial_text_v21.parser import parse_html
from scripts.market_observer_financial_text_v22.adapter import assemble


def blocks(html: str, document_id: str = "synthetic:exhibit", symbol: str = "MU") -> list[dict]:
    raw = html.encode("utf-8")
    meta = {
        "raw_body_sha256": hashlib.sha256(raw).hexdigest(),
        "document_id": document_id,
        "event_group_id": "synthetic:event",
        "symbol": symbol,
        "announcement_date": "2025-06-25",
        "source_kind": "ISSUER_EARNINGS",
        "source_url": "https://www.sec.gov/synthetic-only",
    }
    return parse_html(raw, meta)


class FinancialTextV22Tests(unittest.TestCase):
    def test_shared_header_visible_once_with_column_positions_and_source_spans(self):
        source = """<html><body><table>
        <tr><td>(USD millions)</td><td>FQ3-25 GAAP</td><td>FQ2-25 Non-GAAP</td></tr>
        <tr><td>Revenue</td><td>$ 9,301</td><td>$ 8,053</td></tr>
        <tr><td>Net income</td><td>($ 1,234)</td><td>−$ 800</td></tr>
        </table></body></html>"""
        parsed = blocks(source)
        result = assemble(parsed, 5000)
        self.assertEqual(len(result["selected_group_ids"]), 2)
        self.assertEqual(len(result["selected_context_ids"]), 1)
        self.assertGreater(result["gross_duplicate_context_characters_avoided"], 0)
        self.assertEqual(result["selected_text"].count("(USD millions) FQ3-25 GAAP FQ2-25 Non-GAAP"), 1)
        self.assertIn("c2=FQ3-25 GAAP", result["selected_text"])
        self.assertIn("Revenue $ 9,301 $ 8,053", result["selected_text"])
        self.assertIn("Net income ($ 1,234) −$ 800", result["selected_text"])
        for span in result["model_visible_spans"]:
            self.assertEqual(span["document_id"], "synthetic:exhibit")
            self.assertEqual(span["offset_status"], "MODEL_VISIBLE_CLEAN_TEXT_NOT_RAW_HTML_OFFSET")
            self.assertTrue(result["selected_text"][span["raw_start"]:span["raw_end"]])

    def test_same_metric_two_periods_uses_separate_table_context(self):
        source = """<html><body>
        <table><tr><td>USD millions</td><td>FQ3-25</td></tr>
        <tr><td>Revenue</td><td>$100</td></tr></table>
        <table><tr><td>USD millions</td><td>FQ2-25</td></tr>
        <tr><td>Revenue</td><td>$90</td></tr></table>
        </body></html>"""
        result = assemble(blocks(source), 5000)
        selected = [g for g in result["group_inventory"] if g["status"] == "SELECTED"]
        self.assertEqual(len(selected), 2)
        self.assertNotEqual(selected[0]["context_ids"], selected[1]["context_ids"])
        self.assertIn("FQ3-25", result["selected_text"])
        self.assertIn("FQ2-25", result["selected_text"])

    def test_multiple_units_and_mixed_basis_are_visible_not_auto_resolved(self):
        source = """<html><body><table>
        <tr><td>USD millions, except per share</td><td>FQ3-25 GAAP</td><td>FQ3-25 Non-GAAP</td></tr>
        <tr><td>Earnings per share</td><td>($0.12)</td><td>$0.40</td></tr>
        </table></body></html>"""
        result = assemble(blocks(source), 5000)
        self.assertEqual(len(result["selected_group_ids"]), 1)
        self.assertIn("FQ3-25 GAAP", result["selected_text"])
        self.assertIn("FQ3-25 Non-GAAP", result["selected_text"])
        self.assertIn("($0.12)", result["selected_text"])
        self.assertNotIn("GAAP_CANDIDATE", result["selected_text"])

    def test_missing_period_omits_whole_numeric_row(self):
        source = "<html><body><table><tr><td>Revenue</td><td>$200 million</td></tr></table></body></html>"
        result = assemble(blocks(source), 5000)
        self.assertEqual(result["selected_text"], "")
        self.assertEqual(result["omitted_groups"][0]["reason"], "MISSING_REQUIRED_CONTEXT")

    def test_direct_table_preamble_supplies_visible_period_without_crossing_table(self):
        source = """<html><body>
        <p>For fiscal year 2025, USD millions.</p>
        <table><tr><td>Revenue</td><td>$200</td></tr></table>
        </body></html>"""
        result = assemble(blocks(source), 5000)
        self.assertEqual(len(result["selected_group_ids"]), 1)
        self.assertIn("For fiscal year 2025, USD millions.", result["selected_text"])
        self.assertIn("Revenue $200", result["selected_text"])

    def test_period_only_table_header_is_not_a_measurement_group(self):
        source = """<html><body><h2>Revenue</h2><table>
        <tr><td>USD millions</td><td>FQ3-25</td><td>FQ2-25</td></tr>
        <tr><td>Revenue</td><td>$200</td><td>$180</td></tr>
        </table></body></html>"""
        result = assemble(blocks(source), 5000)
        self.assertEqual(len(result["group_inventory"]), 1)
        self.assertIn("FQ3-25", result["selected_text"])

    def test_inherited_topic_across_table_is_not_invisible_selection_evidence(self):
        source = """<html><body><h2>Revenue</h2>
        <table><tr><td>Presentation note</td><td>reference only</td></tr></table>
        <p>For fiscal year 2025, cash outlay was $200 million.</p>
        </body></html>"""
        result = assemble(blocks(source), 5000)
        self.assertEqual(len(result["group_inventory"]), 1)
        self.assertEqual(result["group_inventory"][0]["status"], "OMITTED")
        self.assertEqual(result["group_inventory"][0]["omission_reason"], "CONTEXT_UNRESOLVED")
        self.assertEqual(result["selected_text"], "")

    def test_resolved_and_unresolved_footnotes(self):
        source = """<html><body><table>
        <tr><td>USD millions</td><td>FQ3-25</td></tr>
        <tr><td>Revenue (a)</td><td>$200</td></tr>
        <tr><td>(a) Excludes one-time adjustment</td></tr>
        </table></body></html>"""
        result = assemble(blocks(source), 5000)
        self.assertEqual(len(result["selected_group_ids"]), 1)
        self.assertIn("(a) Excludes one-time adjustment", result["selected_text"])
        missing = source.replace("<tr><td>(a) Excludes one-time adjustment</td></tr>", "")
        failed = assemble(blocks(missing), 5000)
        self.assertEqual(failed["selected_text"], "")
        self.assertEqual(failed["omitted_groups"][0]["reason"], "CONTEXT_UNRESOLVED")

    def test_cross_company_event_is_rejected(self):
        source = "<html><body><p>For fiscal year 2025 revenue was $200 million.</p></body></html>"
        with self.assertRaisesRegex(ValueError, "cross-event or cross-company"):
            assemble(blocks(source, "d1", "MU") + blocks(source, "d2", "NVDA"), 5000)

    def test_budget_never_truncates_group_and_repeated_build_is_identical(self):
        source = """<html><body><table>
        <tr><td>USD millions</td><td>FQ3-25</td></tr>
        <tr><td>Revenue</td><td>$200</td></tr></table></body></html>"""
        parsed = blocks(source)
        full = assemble(parsed, 5000)
        self.assertEqual(full, assemble(parsed, 5000))
        short = assemble(parsed, len(full["selected_text"]) - 1)
        self.assertEqual(short["selected_text"], "")
        self.assertEqual(short["omitted_groups"][0]["reason"], "WHOLE_GROUP_OVER_BUDGET")

    def test_content_role_is_parser_candidate_not_new_fact(self):
        source = """<html><body>
        <p>For fiscal year 2025, revenue was $90 million.</p>
        <p>Compared with prior year, fiscal year 2025 revenue was $90 million.</p>
        <p>For fiscal year 2026, we expect revenue of $100 million.</p>
        </body></html>"""
        result = assemble(blocks(source), 5000)
        roles = {g["content_role"] for g in result["group_inventory"] if g["status"] == "SELECTED"}
        self.assertEqual(roles, {"historical_result", "year_over_year_comparison", "future_guidance"})
        for role in roles:
            self.assertIn("source_role=" + role, result["selected_text"])

    def test_later_table_header_reset_abstains(self):
        source = """<html><body><table>
        <tr><td>USD millions</td><td>FQ3-25</td></tr>
        <tr><td>Revenue</td><td>$200</td></tr>
        <tr><td>USD thousands</td><td>FQ2-25</td></tr>
        <tr><td>Net income</td><td>$5</td></tr>
        </table></body></html>"""
        result = assemble(blocks(source), 5000)
        by_topic = {g["topics"][0]: g for g in result["group_inventory"]}
        self.assertEqual(by_topic["revenue"]["status"], "SELECTED")
        self.assertEqual(by_topic["net_income"]["omission_reason"], "CONTEXT_UNRESOLVED")


if __name__ == "__main__":
    unittest.main()
