"""Synthetic guard for unlinked note markers and parenthetical negatives."""
import hashlib
import unittest

from scripts.market_observer_financial_text_v23.structure import _unlinked_note_marker, parse_document
from bs4 import BeautifulSoup


def parsed(raw_text: str) -> dict:
    raw = raw_text.encode("utf-8")
    return parse_document(raw, {
        "raw_body_sha256": hashlib.sha256(raw).hexdigest(),
        "document_id": "synthetic-footnote", "event_group_id": "synthetic-event",
        "symbol": "ZZZ", "source_kind": "ISSUER_EARNINGS",
        "announcement_date": "2025-01-01", "source_url": "https://example.invalid/",
    })


class UnlinkedFootnoteTests(unittest.TestCase):
    def test_plain_marker_does_not_silently_attach_a_note(self) -> None:
        doc = parsed("""<table><tr><th>Metric</th><th>2025 USD millions</th></tr>
          <tr><td>Revenue (1)</td><td>42.0</td></tr></table>
          <p>(1) This note is outside the table and has no explicit link.</p>""")
        group = next(g for g in doc["evidence_groups"] if
                     next(c for c in doc["numeric_cells"] if c["cell_id"] == g["anchor_cell_id"])["text"] == "42.0")
        self.assertEqual(group["relation_status"]["footnote"], "UNKNOWN")
        self.assertEqual(group["eligibility"], "FOOTNOTE_UNRESOLVED")

    def test_parenthetical_negative_remains_value_not_note(self) -> None:
        doc = parsed("""<table><tr><th>Metric</th><th>2025 USD millions</th></tr>
          <tr><td>Net income</td><td>(1)</td></tr></table>""")
        group = next(g for g in doc["evidence_groups"] if
                     next(c for c in doc["numeric_cells"] if c["cell_id"] == g["anchor_cell_id"])["text"] == "(1)")
        self.assertEqual(group["relation_status"]["footnote"], "NOT_APPLICABLE")

    def test_multiplication_asterisk_is_not_a_footnote_marker(self) -> None:
        node = BeautifulSoup("<p>Revenue = 2 * 3 units</p>", "html.parser").p
        self.assertFalse(_unlinked_note_marker(node))

    def test_attached_row_label_marker_remains_unresolved(self) -> None:
        doc = parsed("""<table><tr><th>Metric</th><th>2025 USD millions</th></tr>
          <tr><td>Revenue(1)</td><td>42.0</td></tr></table>""")
        group = next(g for g in doc["evidence_groups"] if
                     next(c for c in doc["numeric_cells"] if c["cell_id"] == g["anchor_cell_id"])["text"] == "42.0")
        self.assertEqual(group["relation_status"]["footnote"], "UNKNOWN")

    def test_attached_value_marker_remains_unresolved(self) -> None:
        doc = parsed("""<table><tr><th>Metric</th><th>2025 USD millions</th></tr>
          <tr><td>Revenue</td><td>42.0(1)</td></tr></table>""")
        group = next(g for g in doc["evidence_groups"] if
                     next(c for c in doc["numeric_cells"] if c["cell_id"] == g["anchor_cell_id"])["text"] == "42.0(1)")
        self.assertEqual(group["relation_status"]["footnote"], "UNKNOWN")

    def test_attached_basis_header_marker_is_detected(self) -> None:
        node = BeautifulSoup("<th>GAAP(1)</th>", "html.parser").th
        self.assertTrue(_unlinked_note_marker(node))


if __name__ == "__main__":
    unittest.main()
