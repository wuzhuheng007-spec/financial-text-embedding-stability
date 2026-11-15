"""Focused synthetic checks for v2 source context and no silent truncation."""
import unittest

from scripts.market_observer_financial_text_v2.adapter import assemble
from scripts.market_observer_financial_text_v2.interface import cell_context, feature_record
from scripts.market_observer_financial_text_v2.parser import parse_html


META = {
    "raw_body_sha256": "a" * 64,
    "document_id": "synthetic:exhibit",
    "event_group_id": "synthetic:event",
    "symbol": "MU",
    "announcement_date": "2025-06-25",
    "source_kind": "ISSUER_EARNINGS",
    "source_url": "https://www.sec.gov/synthetic-only",
}


class FinancialTextV2Tests(unittest.TestCase):
    def test_table_header_unit_period_basis_and_numeric_symbols(self):
        html = """<html><body>
          <div><b>Fiscal Q3 2025 results</b></div>
          <table><tr><td>USD millions, except per share</td>
                     <td colspan='2'>Three months ended 2025</td></tr>
                 <tr><td>GAAP</td><td>Current</td><td>Prior</td></tr>
                 <tr><td>Net income</td><td>($1,234)</td><td>−$800</td></tr></table>
          <div>Non-GAAP gross margin was 35.2% to 36.1%.</div>
        </body></html>"""
        blocks = parse_html(html.encode(), META)
        value = next(b for b in blocks if b["row_header"] == "Net income")
        self.assertEqual(value["element_kind"], "table_row")
        self.assertIn("USD millions", value["units_hint"])
        self.assertIn("Three months ended 2025", value["period_hint"])
        self.assertIn("GAAP", value["basis_hint"])
        self.assertIn("($1,234)", value["text"])
        self.assertIn("−$800", value["text"])
        self.assertEqual([x["text"] for x in value["table_cells"]], ["Net income", "($1,234)", "−$800"])
        self.assertIn("non-gaap", " ".join(b["raw_text"].lower() for b in blocks))
        self.assertTrue(value["dom_path"].startswith("html:nth-of-type(1) > body:nth-of-type(1)"))

    def test_rowspan_and_colspan_keep_column_positions(self):
        html = """<html><body><table>
          <tr><td rowspan='2'>Metric</td><td colspan='2'>Fiscal year 2025</td></tr>
          <tr><td>GAAP</td><td>Non-GAAP</td></tr>
          <tr><td>Gross margin</td><td>40%</td><td>41%</td></tr>
        </table></body></html>"""
        value = next(b for b in parse_html(html.encode(), META) if b["row_header"] == "Gross margin")
        self.assertIn("Fiscal year 2025", value["period_hint"])
        self.assertIn("GAAP", value["basis_hint"])
        self.assertEqual([c["column_start"] for c in value["table_cells"]], [0, 1, 2])
        self.assertEqual(value["header_relation_status"], "EXPLICIT_CONTEXT")
        contexts = [cell_context(value, c) for c in value["table_cells"]]
        self.assertEqual(contexts[1]["column_basis_candidate"], "GAAP_CANDIDATE")
        self.assertEqual(contexts[2]["column_basis_candidate"], "NON_GAAP_CANDIDATE")
        self.assertTrue(all(c["fact_confirmation"] == "NOT_CONFIRMED" for c in contexts))

    def test_adapter_records_every_omitted_whole_block(self):
        html = """<html><body><div>Revenue was $200 million.</div>
          <div>Guidance assumes no new export restriction.</div>
          <div>Cash flow from operations was ($30 million).</div>
        </body></html>"""
        blocks = parse_html(html.encode(), META)
        result = assemble(blocks, 95)
        self.assertLessEqual(result["selected_text_characters"], 95)
        self.assertEqual(len(result["selected_block_ids"]) + len(result["omitted_blocks"]), len(blocks))
        for offset in result["v2_clean_text_offsets"]:
            self.assertEqual(offset["offset_status"], "CLEAN_TEXT_NOT_RAW_HTML_OFFSET")
        self.assertEqual(result["model_fitted"], False)
        result["event_group_id"] = META["event_group_id"]
        record = feature_record(result)
        self.assertEqual(record["fit_status"], "NOT_FITTED")
        self.assertEqual(record["source_block_ids"], result["selected_block_ids"])


if __name__ == "__main__":
    unittest.main()
