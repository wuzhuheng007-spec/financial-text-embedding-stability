"""Fictional HTML counterexamples for the v2.4 table kernel.

These source-free tests challenge false positive bindings. They do not score
the four saved training tables or constitute independent financial validation.
"""

from __future__ import annotations

from hashlib import sha256
import unittest

from bs4 import BeautifulSoup

from scripts.market_observer_financial_text_v21.parser import dom_path
from scripts.market_observer_financial_text_v24.semantic import parse_document


def parse(table_html: str, *, before: str = "") -> dict:
    raw = f"<html><body>{before}{table_html}</body></html>".encode("utf-8")
    soup = BeautifulSoup(raw, "html.parser")
    target = soup.find("table", id="target")
    result = parse_document(raw, {"document_id": "fictional", "raw_body_sha256": sha256(raw).hexdigest()},
                            [dom_path(target)])
    return result["tables"][0]


def binding(table: dict, metric: str, value: str) -> dict:
    hits = [b for b in table["bindings"]
            if b["row_metric_text"] == metric and b["value_text"] == value]
    if len(hits) != 1:
        raise AssertionError(f"expected one fictional binding for {metric}={value}, found {len(hits)}")
    return hits[0]


class StructureBeforeTopicTests(unittest.TestCase):
    def test_quarter_digits_are_headers_and_non_topic_amounts_are_data(self):
        table = parse("""<table id='target'>
          <tr><th colspan='3'>GAAP</th></tr>
          <tr><td>In millions</td><th>Q1 2025</th><th>Q/Q</th></tr>
          <tr><td>Revenue</td><td>$12</td><td>5%</td></tr>
          <tr><td>Legal settlement</td><td>$7</td><td>2%</td></tr>
          </table>""", before="<p>Our service market totals billions.</p>")
        self.assertEqual([r["role"] for r in table["rows"]],
                         ["BASIS_HEADER", "UNIT_AND_COLUMN_HEADER", "DATA", "DATA"])
        amount = binding(table, "Legal settlement", "$7")
        self.assertEqual(amount["period_text"], "Q1 2025")
        self.assertEqual(amount["scale"], "millions")
        self.assertEqual(amount["basis"], "GAAP")
        self.assertNotIn(amount["value_cell_dom_path"], amount["column_header_dom_paths"])
        self.assertNotIn("billions", str(amount["unit"]))
        comparison = binding(table, "Legal settlement", "2%")
        self.assertIsNone(comparison["period_text"])
        self.assertEqual(comparison["comparison_label"], "Q/Q")
        self.assertEqual(comparison["period"]["status"], "NOT_APPLICABLE")

    def test_multilayer_header_and_mixed_basis_do_not_bleed(self):
        table = parse("""<table id='target'>
          <tr><td>In millions</td><th colspan='2'>GAAP</th><th colspan='2'>Non-GAAP</th></tr>
          <tr><td></td><th>Q1 2025</th><th>Q4 2024</th><th>Q1 2025</th><th>Q4 2024</th></tr>
          <tr><td>Revenue</td><td>$12</td><td>$11</td><td>$14</td><td>$13</td></tr>
          </table>""")
        values = [b for b in table["bindings"] if b["row_metric_text"] == "Revenue"]
        self.assertEqual([b["basis"] for b in values], ["GAAP", "GAAP", "Non-GAAP", "Non-GAAP"])
        self.assertEqual([b["period_text"] for b in values],
                         ["Q1 2025", "Q4 2024", "Q1 2025", "Q4 2024"])

    def test_separate_currency_and_eps_exception_do_not_set_amount_scale(self):
        table = parse("""<table id='target'>
          <tr><td>In millions, except per share amounts</td><th colspan='2'>Q1 2025</th></tr>
          <tr><td>Revenue</td><td>$</td><td>12</td></tr>
          <tr><td>Earnings per share</td><td>$</td><td>0.54</td></tr>
          </table>""")
        amount = binding(table, "Revenue", "12")
        eps = binding(table, "Earnings per share", "0.54")
        self.assertEqual(amount["currency"], "$")
        self.assertEqual(amount["scale"], "millions")
        self.assertEqual(amount["measurement_kind"], "amount")
        self.assertEqual(eps["currency"], "$")
        self.assertIsNone(eps["scale"])
        self.assertEqual(eps["measurement_kind"], "per_share_amount")
        self.assertNotEqual(amount["value_cell_dom_path"], amount["column_header_dom_path"])

    def test_percentage_cannot_inherit_currency_or_scale(self):
        table = parse("""<table id='target'>
          <tr><td>In millions</td><th>Q1 2025</th></tr>
          <tr><td>Gross margin</td><td>34%</td></tr>
          </table>""")
        item = binding(table, "Gross margin", "34%")
        self.assertEqual(item["measurement_kind"], "percentage")
        self.assertIsNone(item["currency"])
        self.assertIsNone(item["scale"])
        self.assertEqual(item["unit"]["currency"]["status"], "NOT_APPLICABLE")

    def test_repeated_header_carries_only_valid_scope(self):
        table = parse("""<table id='target'>
          <tr><td>In millions</td><th>Q1 2025</th></tr>
          <tr><td>Revenue</td><td>$12</td></tr>
          <tr><td>In millions</td><th>Q4 2024</th></tr>
          <tr><td>Operating expense</td><td>$7</td></tr>
          </table>""")
        item = binding(table, "Operating expense", "$7")
        self.assertEqual(item["period_text"], "Q4 2024")
        self.assertEqual(item["scale"], "millions")
        self.assertNotEqual(item["column_header_dom_path"],
                            binding(table, "Revenue", "$12")["column_header_dom_path"])

    def test_same_scope_incompatible_unit_declarations_are_conflict(self):
        table = parse("""<table id='target'>
          <caption>In millions</caption>
          <tr><td>In billions</td><th>Q1 2025</th></tr>
          <tr><td>Revenue</td><td>$12</td></tr>
          </table>""")
        item = binding(table, "Revenue", "$12")
        self.assertEqual(item["unit"]["scale"]["status"], "CONFLICT")
        self.assertIsNone(item["scale"])

    def test_nested_table_is_not_parent_table_value_or_header(self):
        table = parse("""<table id='target'>
          <tr><td>In millions</td><th>Q1 2025</th></tr>
          <tr><td>Revenue</td><td>$12</td><td><table><tr><td>Q4 2024</td><td>$999</td></tr></table></td></tr>
          </table>""")
        self.assertEqual(len(table["rows"]), 2)
        self.assertEqual([b["value_text"] for b in table["bindings"]], ["$12"])
        self.assertEqual(binding(table, "Revenue", "$12")["period_text"], "Q1 2025")

    def test_year_only_header_is_not_monetary_data(self):
        table = parse("""<table id='target'>
          <tr><td>In millions</td><th>2025</th></tr>
          <tr><td>Revenue</td><td>$12</td></tr>
          </table>""")
        self.assertEqual(table["rows"][0]["role"], "UNIT_AND_COLUMN_HEADER")
        self.assertEqual(len(table["bindings"]), 1)
        self.assertEqual(table["bindings"][0]["period_text"], "2025")


if __name__ == "__main__":
    unittest.main()
