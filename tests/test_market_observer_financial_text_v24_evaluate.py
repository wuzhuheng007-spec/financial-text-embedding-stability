"""Fictional scorer counterexamples; no saved financial source is read here."""

from __future__ import annotations

from copy import deepcopy
import unittest

from scripts.market_observer_financial_text_v24.evaluate import score_cell


VALUE = "/table/tr[3]/td[2]"
LABEL = "/table/tr[3]/td[1]"
HEADER = "/table/tr[2]/td[2]"
UNIT = "/table/tr[1]/td[1]"
BASIS = "/table/tr[1]/td[2]"
EXTRA_DATA = "/table/tr[3]/td[3]"


def relation(value, status, *paths):
    return {"value": value, "status": status, "source_dom_paths": list(paths),
            "scope_id": "fictional-region", "reason": "fictional source structure"}


def fixture():
    expected = {"value_dom_path": VALUE, "value_text": "$12", "row_index_zero_based": 2,
                "logical_column_span": [1, 2], "row_metric": "Revenue",
                "row_label_dom_path": LABEL, "column_header_dom_path": HEADER,
                "period_text": "Q1 2025", "comparison_label": None,
                "unit_dom_path": UNIT, "currency": "$", "scale": "millions",
                "measurement_kind": "amount", "basis_text": "GAAP",
                "basis_dom_path": BASIS,
                "footnote_status": "NONE_REQUIRED_IN_SELECTED_TABLE"}
    binding = {"value_cell_dom_path": VALUE, "value_text": "$12", "row_index": 2,
               "column_start": 1, "column_end": 2,
               "row_metric_text": "Revenue", "row_metric": relation("Revenue", "EXPLICIT", LABEL),
               "column_header_dom_path": HEADER,
               "column_header": relation("Q1 2025", "EXPLICIT", HEADER),
               "period_text": "Q1 2025", "period": relation("Q1 2025", "EXPLICIT", HEADER),
               "comparison_label": None,
               "unit_dom_path": UNIT, "currency": "$", "scale": "millions",
               "measurement_kind": "amount",
               "unit": {"currency": relation("$", "EXPLICIT", VALUE),
                        "scale": relation("millions", "EXPLICIT", UNIT),
                        "measurement_kind": relation("amount", "EXPLICIT", LABEL, VALUE)},
               "basis": "GAAP", "basis_relation": relation("GAAP", "EXPLICIT", BASIS),
               "footnote": relation(None, "NOT_APPLICABLE"),
               "full_status": "MECHANICALLY_COMPLETE"}
    cells = {VALUE: {"dom_path": VALUE, "text": "$12", "row_start": 2,
                     "column_start": 1, "column_end": 2, "roles": ["NUMERIC_MEASURE"]},
             LABEL: {"dom_path": LABEL, "text": "Revenue", "row_start": 2,
                     "column_start": 0, "column_end": 1, "roles": ["TEXT_LABEL"]},
             HEADER: {"dom_path": HEADER, "text": "Q1 2025", "row_start": 1,
                      "column_start": 1, "column_end": 2, "roles": ["PERIOD_LABEL"]},
             UNIT: {"dom_path": UNIT, "text": "USD millions", "row_start": 0,
                    "column_start": 0, "column_end": 1, "roles": ["UNIT_DECLARATION"]},
             BASIS: {"dom_path": BASIS, "text": "GAAP", "row_start": 0,
                     "column_start": 1, "column_end": 2, "roles": ["BASIS_LABEL"]},
             EXTRA_DATA: {"dom_path": EXTRA_DATA, "text": "$20", "row_start": 2,
                          "column_start": 2, "column_end": 3, "roles": ["NUMERIC_MEASURE"]}}
    return expected, binding, cells


def score(expected, binding, cells):
    return score_cell(expected, binding, forbidden_header_paths={EXTRA_DATA},
                      table_cells=cells)


class SourceSemanticScorerTests(unittest.TestCase):
    def test_full_fictional_binding(self):
        exp, got, cells = fixture()
        result = score(exp, got, cells)
        self.assertTrue(result["known_fields_complete"])
        self.assertTrue(result["fully_bound"])
        self.assertFalse(result["wrong_complete_declaration"])

    def test_amount_data_cell_cannot_be_extra_header(self):
        exp, got, cells = fixture()
        got["column_header"]["source_dom_paths"].append(EXTRA_DATA)
        result = score(exp, got, cells)
        self.assertEqual(result["forbidden_header_links"], [EXTRA_DATA])
        self.assertFalse(result["known_fields_complete"])

    def test_correct_primary_header_plus_unreviewed_extra_path_fails(self):
        exp, got, cells = fixture()
        extra = "/table/tr[1]/td[4]"
        got["column_header"]["source_dom_paths"].append(extra)
        result = score(exp, got, cells)
        self.assertIn("column_header:" + extra, result["unverified_explicit_edges"])
        self.assertFalse(result["known_fields_complete"])

    def test_period_and_unit_scope_are_independent(self):
        exp, got, cells = fixture()
        got["period_text"] = "Q2 2025"
        got["period"]["value"] = "Q2 2025"
        got["unit_dom_path"] = "/outside/business-narrative"
        got["unit"]["scale"] = relation("billions", "EXPLICIT", "/outside/business-narrative")
        got["scale"] = "billions"
        result = score(exp, got, cells)
        self.assertEqual(result["fields"]["period"]["decision"], "WRONG_EXPLICIT")
        self.assertEqual(result["unit_scope_decision"], "WRONG_UNIT_SCOPE")
        self.assertIn("scale:/outside/business-narrative", result["unverified_explicit_edges"])

    def test_correct_unit_value_with_extra_outside_source_fails(self):
        exp, got, cells = fixture()
        got["unit"]["scale"]["source_dom_paths"].append("/outside/business-narrative")
        result = score(exp, got, cells)
        self.assertEqual(result["fields"]["scale"]["decision"], "CORRECT_EXPLICIT")
        self.assertFalse(result["known_fields_complete"])

    def test_comparison_is_not_an_inferred_calendar_period(self):
        exp, got, cells = fixture()
        exp["period_text"] = None
        exp["comparison_label"] = "Q/Q"
        got["period_text"] = None
        got["period"] = relation(None, "NOT_APPLICABLE")
        got["comparison_label"] = "Q/Q"
        got["unit"]["measurement_kind"] = relation("percent_change", "EXPLICIT", LABEL, HEADER)
        got["measurement_kind"] = "percent_change"
        got["unit"]["currency"] = relation(None, "NOT_APPLICABLE")
        got["currency"] = None
        got["unit"]["scale"] = relation(None, "NOT_APPLICABLE", UNIT)
        got["scale"] = None
        exp["currency"] = None
        exp["scale"] = None
        exp["measurement_kind"] = "percent_change"
        self.assertTrue(score(exp, got, cells)["known_fields_complete"])
        got["period"] = relation(None, "UNKNOWN")
        self.assertEqual(score(exp, got, cells)["fields"]["period"]["decision"],
                         "WRONG_UNKNOWN_WHEN_NA")

    def test_mu_unknown_currency_and_footnote_marker_are_abstentions(self):
        exp, got, cells = fixture()
        cells[BASIS]["text"] = "GAAP (1)"
        exp["footnote_marker"] = "(1)"
        exp["footnote_link_status"] = "SOURCE_REVIEW_MATCHED_DEFINITION_BUT_NO_DIRECT_HTML_ANCHOR"
        exp.pop("footnote_status")
        exp["currency"] = None
        got["value_text"] = exp["value_text"] = "12"
        cells[VALUE]["text"] = "12"
        got["currency"] = None
        got["unit"]["currency"] = relation(None, "UNKNOWN")
        got["footnote"] = relation(None, "UNKNOWN", BASIS)
        got["full_status"] = "PARTIAL_UNKNOWN"
        result = score(exp, got, cells)
        self.assertEqual(result["fields"]["currency"]["decision"], "CORRECT_ABSTENTION")
        self.assertEqual(result["fields"]["footnote"]["decision"], "CORRECT_ABSTENTION")
        self.assertTrue(result["known_fields_complete"])
        self.assertFalse(result["fully_bound"])
        got["footnote"] = relation(None, "UNKNOWN", "/table/footnote-definition")
        self.assertEqual(score(exp, got, cells)["fields"]["footnote"]["decision"],
                         "WRONG_FOOTNOTE_MARKER_OR_LINK")

    def test_source_unknown_cannot_be_reported_not_applicable(self):
        exp, got, cells = fixture()
        exp["currency"] = None
        got["currency"] = None
        got["unit"]["currency"] = relation(None, "NOT_APPLICABLE")
        self.assertEqual(score(exp, got, cells)["fields"]["currency"]["decision"],
                         "WRONG_NA_ON_UNKNOWN")

    def test_false_complete_with_unresolved_relation(self):
        exp, got, cells = fixture()
        got["basis"] = None
        got["basis_relation"] = relation(None, "UNKNOWN")
        result = score(exp, got, cells)
        self.assertFalse(result["known_fields_complete"])
        self.assertTrue(result["wrong_complete_declaration"])


if __name__ == "__main__":
    unittest.main()
