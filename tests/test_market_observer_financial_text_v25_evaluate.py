"""Fictional contract counterexamples for the dynamic v2.5 evaluator."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import unittest

from scripts.market_observer_financial_text_v25.evaluate import (
    ExpandedEvaluationError, assess_pack_closure, evaluate_candidates,
    evaluate_frozen_tables, score_expected_cell,
)


TABLE = "html:nth-of-type(1) > body:nth-of-type(1) > table:nth-of-type(1)"
VALUE = TABLE + " > tr:nth-of-type(3) > td:nth-of-type(2)"
LABEL = TABLE + " > tr:nth-of-type(3) > td:nth-of-type(1)"
HEADER = TABLE + " > tr:nth-of-type(2) > td:nth-of-type(2)"
UNIT = TABLE + " > tr:nth-of-type(1) > td:nth-of-type(1)"
BASIS = TABLE + " > tr:nth-of-type(1) > td:nth-of-type(2)"
WRONG = TABLE + " > tr:nth-of-type(3) > td:nth-of-type(3)"


def rel(value, status, *paths):
    return {"value": value, "status": status, "source_dom_paths": list(paths),
            "scope_id": "fictional", "reason": "fictional explicit source"}


def fixture():
    expected = {"value_dom_path": VALUE, "value_text": "$12",
                "row_index_zero_based": 2, "logical_column_span": [1, 2],
                "row_metric": "Revenue", "row_label_dom_path": LABEL,
                "column_header_dom_path": HEADER, "period_text": "Q1 2025",
                "unit_dom_path": UNIT, "currency": "$", "scale": "millions",
                "measurement_kind": "amount", "basis_text": "GAAP",
                "basis_dom_path": BASIS,
                "footnote_status": "NONE_REQUIRED_IN_SELECTED_TABLE"}
    binding = {"value_cell_dom_path": VALUE, "value_text": "$12",
               "row_index": 2, "column_start": 1, "column_end": 2,
               "row_metric_text": "Revenue", "row_metric": rel("Revenue", "EXPLICIT", LABEL),
               "column_header_dom_path": HEADER,
               "column_header": rel("Q1 2025", "EXPLICIT", HEADER),
               "period_text": "Q1 2025", "period": rel("Q1 2025", "EXPLICIT", HEADER),
               "unit_dom_path": UNIT, "currency": "$", "scale": "millions",
               "measurement_kind": "amount",
               "unit": {"currency": rel("$", "EXPLICIT", VALUE),
                        "scale": rel("millions", "EXPLICIT", UNIT),
                        "measurement_kind": rel("amount", "EXPLICIT", LABEL, VALUE)},
               "basis": "GAAP", "basis_relation": rel("GAAP", "EXPLICIT", BASIS),
               "footnote": rel(None, "NOT_APPLICABLE"),
               "full_status": "MECHANICALLY_COMPLETE"}
    cells = {VALUE: {"dom_path": VALUE, "text": "$12", "row_start": 2,
                     "column_start": 1, "column_end": 2},
             LABEL: {"dom_path": LABEL, "text": "Revenue", "row_start": 2,
                     "column_start": 0, "column_end": 1},
             HEADER: {"dom_path": HEADER, "text": "Q1 2025", "row_start": 1,
                      "column_start": 1, "column_end": 2},
             UNIT: {"dom_path": UNIT, "text": "USD millions", "row_start": 0,
                    "column_start": 0, "column_end": 1},
             BASIS: {"dom_path": BASIS, "text": "GAAP", "row_start": 0,
                     "column_start": 1, "column_end": 2}}
    return expected, binding, cells


class DynamicCellTests(unittest.TestCase):
    def test_good_fictional_cell_and_dynamic_denominator(self):
        exp, got, cells = fixture()
        self.assertTrue(score_expected_cell(exp, got, table_cells=cells)["all_fields_complete"])
        ref = {"expected_tables": [
            {"document_id": "d1", "table_dom_path": TABLE, "raw_body_sha256": "h1",
             "expected_value_cells": [exp]},
            {"document_id": "d2", "table_dom_path": "other", "raw_body_sha256": "h2",
             "expected_value_cells": [dict(exp, value_dom_path="other > td:nth-of-type(1)")]}]}
        doc = {"document_id": "d1", "source_sha256": "h1",
               "tables": [{"dom_path": TABLE, "bindings": [got],
                           "rows": [{"row_index": 2, "cells": list(cells.values())}]}]}
        _, public = evaluate_frozen_tables(ref, [doc])
        self.assertEqual(public["denominators"]["tables"], 2)
        self.assertEqual(public["counts"]["missed_cells"], 1)
        self.assertEqual(public["counts"]["all_fields_complete"], 1)

    def test_all_abstain_is_not_complete(self):
        exp, got, cells = fixture()
        for field in ("row_metric", "column_header", "period", "basis"):
            got["basis_relation" if field == "basis" else field] = rel(None, "UNKNOWN")
        for field in ("currency", "scale", "measurement_kind"):
            got["unit"][field] = rel(None, "UNKNOWN")
        got.update(row_metric_text=None, column_header_dom_path=None,
                   period_text=None, basis=None, currency=None,
                   scale=None, measurement_kind=None)
        result = score_expected_cell(exp, got, table_cells=cells)
        self.assertFalse(result["known_fields_complete"])
        self.assertFalse(result["all_fields_complete"])

    def test_missing_duplicate_and_wrong_cell_are_rejected(self):
        exp, got, cells = fixture()
        self.assertEqual(score_expected_cell(exp, None)["state"], "MISSED_CELL")
        with self.assertRaises(ExpandedEvaluationError):
            score_expected_cell(exp, dict(got, value_cell_dom_path=WRONG), table_cells=cells)
        doc = {"document_id": "d1", "source_sha256": "h1",
               "tables": [{"dom_path": TABLE, "bindings": [got, deepcopy(got)], "rows": []}]}
        ref = {"expected_tables": [{"document_id": "d1", "table_dom_path": TABLE,
                                    "raw_body_sha256": "h1", "expected_value_cells": [exp]}]}
        with self.assertRaises(ExpandedEvaluationError):
            evaluate_frozen_tables(ref, [doc])

    def test_extra_explicit_path_fails_even_with_correct_header(self):
        exp, got, cells = fixture()
        got["column_header"]["source_dom_paths"].append(WRONG)
        result = score_expected_cell(exp, got, table_cells=cells,
                                     forbidden_header_paths={WRONG})
        self.assertIn("column_header:" + WRONG, result["wrong_explicit_paths"])
        self.assertIn(WRONG, result["forbidden_header_paths"])
        self.assertFalse(result["known_fields_complete"])

    def test_multilevel_header_has_multiple_required_paths(self):
        exp, got, cells = fixture()
        parent = TABLE + " > tr:nth-of-type(1) > td:nth-of-type(3)"
        exp["field_source_paths"] = {"column_header": {
            "required": [parent, HEADER], "allowed": [parent, HEADER]}}
        got["column_header"]["source_dom_paths"].insert(0, parent)
        self.assertTrue(score_expected_cell(exp, got, table_cells=cells)["all_fields_complete"])
        got["column_header"]["source_dom_paths"].remove(parent)
        self.assertFalse(score_expected_cell(exp, got, table_cells=cells)["all_fields_complete"])

    def test_unknown_footnote_cannot_be_mislabeled_not_applicable(self):
        exp, got, cells = fixture()
        cells[BASIS]["text"] = "GAAP (1)"
        exp["footnote_marker"] = "(1)"
        exp["footnote_expectation"] = "MARKER_UNRESOLVED"
        got["footnote"] = rel(None, "NOT_APPLICABLE")
        result = score_expected_cell(exp, got, table_cells=cells)
        self.assertEqual(result["field_decisions"]["footnote"], "WRONG_MARKER_OR_APPLICABILITY")
        self.assertFalse(result["all_fields_complete"])
        got["footnote"] = rel(None, "UNKNOWN", BASIS)
        result = score_expected_cell(exp, got, table_cells=cells)
        self.assertEqual(result["field_decisions"]["footnote"], "CORRECT_ABSTENTION")
        self.assertFalse(result["all_fields_complete"])

    def test_false_semantic_complete_claim_is_detected(self):
        exp, got, cells = fixture()
        got["basis"] = None
        got["basis_relation"] = rel(None, "UNKNOWN")
        got["full_status"] = "SOURCE_SUPPORTED"
        result = score_expected_cell(exp, got, table_cells=cells)
        self.assertTrue(result["wrong_complete_declaration"])


class CandidateAndPackTests(unittest.TestCase):
    def test_pack_budget_and_dangling_reference(self):
        ctx = "[CTX id=R1 doc=d1 path=/header] Q1 2025"
        val = "[VALUE id=G1 doc=d1 refs=period:R1] $12"
        visible = ctx + "\n" + val
        def span(kind, name, line, start, source_path):
            raw = line.index("] ") + 2
            return {"kind": kind, "id": name, "start": start, "end": start + len(line),
                    "raw_start": start + raw, "raw_end": start + len(line),
                    "raw_text_sha256": hashlib.sha256(line[raw:].encode()).hexdigest(),
                    "dom_path": source_path, "source_sha256": "h1"}
        pack = {"event_group_id": "e1", "max_characters": 90,
                "model_visible_text": visible,
                "selected_group_ids": ["G1"],
                "group_inventory": [{"group_id": "G1", "selected": True,
                                     "document_id": "d1",
                                     "relation_refs": {"period": ["R1"]}}],
                "source_spans": [span("CONTEXT", "R1", ctx, 0, "/header"),
                                 span("VALUE", "G1", val, len(ctx) + 1, "/value")],
                "context_dictionary": {"R1": {"document_id": "d1", "dom_path": "/header",
                                              "source_sha256": "h1"}}}
        self.assertTrue(assess_pack_closure(pack)["closure_pass"])
        pack["group_inventory"][0]["relation_refs"]["period"] = ["R2"]
        self.assertFalse(assess_pack_closure(pack)["closure_pass"])

    def test_reference_substring_in_source_text_does_not_close(self):
        raw = "[VALUE id=G1 doc=d1 refs=period:R1] R1 Q1"
        pack = {"event_group_id": "e1", "max_characters": 100,
                "model_visible_text": raw,
                "selected_group_ids": ["G1"],
                "group_inventory": [{"group_id": "G1", "selected": True,
                                     "document_id": "d1",
                                     "relation_refs": {"period": ["R1"]}}],
                "source_spans": [{"kind": "VALUE", "id": "G1", "start": 0,
                                  "end": len(raw), "raw_start": raw.index("] ") + 2,
                                  "raw_end": len(raw),
                                  "raw_text_sha256": hashlib.sha256(b"R1 Q1").hexdigest(),
                                  "dom_path": "/value", "source_sha256": "h1"}],
                "context_dictionary": {}}
        self.assertFalse(assess_pack_closure(pack)["closure_pass"])

    def test_candidate_requires_locator_metric_and_exact_value(self):
        exp, got, cells = fixture()
        doc = {"document_id": "d1", "source_sha256": "h1",
               "tables": [{"dom_path": TABLE, "bindings": [got]}]}
        row_path = TABLE + " > tr:nth-of-type(3)"
        candidate = {"candidate_id": "c1", "event_id": "e1",
                     "source_document_id": "d1", "source_document_sha256": "h1",
                     "status": "CODEX_REVIEW_SOURCE_SUPPORTED",
                     "final": {"value_text": "$12"},
                     "field_evidence": {
                         "value": [{"dom_path": row_path}],
                         "metric": [{"dom_path": row_path,
                                     "rendered_visible_text": "Revenue"}]}}
        _, public = evaluate_candidates({"candidates": [candidate]}, [doc])
        self.assertEqual(public["candidate_statuses"][0]["match_state"],
                         "UNIQUE_SOURCE_ANCHOR")
        self.assertEqual(public["candidate_statuses"][0]["source_binding_status"],
                         "NOT_REVIEWED")
        candidate["field_evidence"]["metric"][0]["rendered_visible_text"] = "Profit"
        _, public = evaluate_candidates({"candidates": [candidate]}, [doc])
        self.assertEqual(public["candidate_statuses"][0]["match_state"],
                         "NO_SOURCE_ANCHOR")

    def test_source_complete_cannot_override_unknown_parser_fields(self):
        exp, got, cells = fixture()
        got["period"] = rel(None, "UNKNOWN")
        doc = {"document_id": "d1", "source_sha256": "h1",
               "tables": [{"dom_path": TABLE, "bindings": [got]}]}
        row_path = TABLE + " > tr:nth-of-type(3)"
        candidate = {"candidate_id": "c1", "event_id": "e1",
                     "source_document_id": "d1", "source_document_sha256": "h1",
                     "review_status": "CODEX_REVIEW_SOURCE_SUPPORTED",
                     "review_v4": {"final": {"value_text": "$12"},
                                   "field_evidence": {
                                       "value": [{"dom_path": row_path}],
                                       "metric": [{"dom_path": row_path,
                                                   "rendered_visible_text": "Revenue"}]}}}
        audit = {"c1": {"document_id": "d1", "table_dom_path": TABLE,
                        "value_cell_dom_path": VALUE, "decision": "COMPLETE",
                        "field_review": {f: "CORRECT" for f in (
                            "row_metric", "column_header", "period", "currency",
                            "scale", "measurement_kind", "basis", "footnote")}}}
        with self.assertRaises(ExpandedEvaluationError):
            evaluate_candidates({"candidates": [candidate]}, [doc], source_audit=audit)

    def test_paragraph_candidate_is_separate_from_table_miss(self):
        paragraph = "html:nth-of-type(1) > body:nth-of-type(1) > div:nth-of-type(2)"
        candidate = {"candidate_id": "p1", "event_id": "e1",
                     "source_document_id": "d1", "source_document_sha256": "h1",
                     "review_status": "CODEX_REVIEW_SOURCE_SUPPORTED",
                     "review_v4": {"final": {"value_text": "12"},
                                   "field_evidence": {
                                       "value": [{"dom_path": paragraph}],
                                       "metric": [{"dom_path": paragraph,
                                                   "rendered_visible_text": "Revenue"}]}}}
        doc = {"document_id": "d1", "source_sha256": "h1", "tables": [],
               "paragraph_inventory": [{"dom_path": paragraph}]}
        _, public = evaluate_candidates({"candidates": [candidate]}, [doc])
        row = public["candidate_statuses"][0]
        self.assertEqual(row["match_state"], "PARAGRAPH_SEPARATE")
        self.assertEqual(row["parsed_status"], "PARAGRAPH_INVENTORIED_UNSTRUCTURED")
        self.assertNotIn("COMPLETE", row["pack_status"])

    def test_ambiguous_source_cannot_be_promoted_even_with_audit_flag(self):
        _, got, _ = fixture()
        doc = {"document_id": "d1", "source_sha256": "h1",
               "tables": [{"dom_path": TABLE, "bindings": [got]}]}
        row_path = TABLE + " > tr:nth-of-type(3)"
        candidate = {"candidate_id": "c1", "event_id": "e1",
                     "source_document_id": "d1", "source_document_sha256": "h1",
                     "review_status": "CODEX_REVIEW_AMBIGUOUS",
                     "review_v4": {"final": {"value_text": "$12"},
                                   "field_evidence": {
                                       "value": [{"dom_path": row_path}],
                                       "metric": [{"dom_path": row_path,
                                                   "rendered_visible_text": "Revenue"}]}}}
        audit = {"c1": {"document_id": "d1", "table_dom_path": TABLE,
                        "value_cell_dom_path": VALUE, "source_sha256": "h1",
                        "review_role": "CODEX_REVIEW", "decision": "COMPLETE",
                        "field_review": {f: "CORRECT" for f in (
                            "value", "row_metric", "column_header", "period", "currency",
                            "scale", "measurement_kind", "basis", "footnote")}}}
        with self.assertRaises(ExpandedEvaluationError):
            evaluate_candidates({"candidates": [candidate]}, [doc], source_audit=audit)


if __name__ == "__main__":
    unittest.main()
