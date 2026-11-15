"""Synthetic checks for the versioned training-source header correction."""

import unittest

from scripts.market_observer_financial_text_v21.adapter import assemble
from scripts.market_observer_financial_text_v21.parser import parse_html


META = {
    "raw_body_sha256": "a" * 64,
    "document_id": "synthetic:exhibit",
    "event_group_id": "synthetic:event",
    "symbol": "MU",
    "announcement_date": "2025-06-25",
    "source_kind": "ISSUER_EARNINGS",
    "source_url": "https://www.sec.gov/synthetic-only",
}


class FinancialTextV21Tests(unittest.TestCase):
    def test_explicit_fq_header_unit_and_gaap_bind_to_source_row(self):
        html = b"""<html><body><table>
          <tr><td>Quarterly Financial Results</td></tr>
          <tr><td>(in millions, except per share)</td><td>GAAP</td><td>Non-GAAP</td></tr>
          <tr><td></td><td>FQ3-25</td><td>FQ2-25</td></tr>
          <tr><td>Revenue</td><td>$ 9,301</td><td>$ 8,053</td></tr>
        </table></body></html>"""
        blocks = parse_html(html, META)
        row = next(b for b in blocks if b["row_header"] == "Revenue")
        self.assertIn("FQ3-25", row["period_hint"])
        self.assertIn("FQ2-25", row["column_headers"][2])
        self.assertIn("(in millions, except per share)", row["units_hint"])
        self.assertIn("source_header_rows: (in millions, except per share) GAAP Non-GAAP | FQ3-25 FQ2-25", row["text"])
        self.assertIn("source_row: Revenue $ 9,301 $ 8,053", row["text"])
        selected = assemble(blocks, 5000)["selected_text"]
        self.assertIn("FQ3-25 FQ2-25", selected)
        self.assertIn("Revenue $ 9,301 $ 8,053", selected)

    def test_late_cash_flow_period_header_is_inherited(self):
        html = b"""<html><body><table>
          <tr><td>COMPANY</td></tr><tr><td>CASH FLOWS</td></tr>
          <tr><td>(In millions)</td></tr><tr><td>Unaudited</td></tr>
          <tr><td>Three Months Ended</td></tr>
          <tr><td></td><td>April 27,</td><td>April 28,</td></tr>
          <tr><td></td><td>2025</td><td>2024</td></tr>
          <tr><td>Cash flows from operating activities:</td></tr>
          <tr><td>Net income</td><td>18,775</td><td>14,881</td></tr>
        </table></body></html>"""
        row = next(b for b in parse_html(html, META) if b["row_header"] == "Net income")
        self.assertIn("Three Months Ended", row["period_hint"])
        self.assertIn("2025", row["column_headers"][1])
        self.assertIn("(In millions)", row["units_hint"])
        self.assertIn("source_header_rows: (In millions) | Three Months Ended", row["text"])
        self.assertIn("source_row: Net income 18,775 14,881", row["text"])

    def test_missing_source_period_stays_unknown(self):
        html = b"<html><body><table><tr><td>Revenue</td><td>$200</td></tr></table></body></html>"
        row = next(b for b in parse_html(html, META) if b["row_header"] == "Revenue")
        self.assertIsNone(row["period_hint"])
        self.assertIn("period: UNKNOWN", row["text"])


if __name__ == "__main__":
    unittest.main()
