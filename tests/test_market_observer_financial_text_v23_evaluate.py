"""Focused synthetic counterexamples for the frozen v2.3 structural evaluator."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import unittest

from scripts.market_observer_financial_text_v23.evaluate import (
    StructureContractError, candidate_cell_state, exact_numeric_cell_match,
    index_parsed_documents, index_validated_events, legacy_structure_status, relation_scope_state,
    structural_status_for_candidate, validate_visible_event,
)


ROOT_DOM = "html:nth-of-type(1) > body:nth-of-type(1) > table:nth-of-type(1)"


def fixture():
    doc_id = "SYNTH_DOC"
    def fragment(fid, text, path, kind, row=None, col=None, table="T1", subtable="SUB1"):
        return {"fragment_id": fid, "document_id": doc_id, "source_sha256": "a" * 64,
                "dom_path": path, "text": text, "kind": kind, "table_id": table,
                "subtable_id": subtable, "section_id": "SEC1",
                "row_start": row, "row_end": None if row is None else row + 1,
                "column_start": col, "column_end": None if col is None else col + 1}
    rowdom = ROOT_DOM + " > tr:nth-of-type(2)"
    coldom = ROOT_DOM + " > tr:nth-of-type(1)"
    frags = [
        fragment("FROW", "Revenue", rowdom + " > th:nth-of-type(1)", "ROW_HEADER", row=1, col=0),
        fragment("FCOL", "Quarter ended", coldom + " > th:nth-of-type(2)", "COLUMN_HEADER", row=0, col=1),
        fragment("FPER", "April 27, 2025", coldom + " > td:nth-of-type(2)", "PERIOD", row=0, col=1),
        fragment("FUNIT", "USD billions", ROOT_DOM + " > caption:nth-of-type(1)", "UNIT", row=None, col=1),
    ]
    cell = {"cell_id": "C1", "document_id": doc_id, "source_sha256": "a" * 64,
            "table_id": "T1", "subtable_id": "SUB1", "section_id": "SEC1",
            "row_index": 1, "column_index": 1, "row_start": 1, "row_end": 2,
            "column_start": 1, "column_end": 2, "cell_dom_path": rowdom + " > td:nth-of-type(1)",
            "row_dom_path": rowdom, "text": "44.1", "anchor_kind": "TABLE_CELL"}
    rels = []
    for rid, kind, target, method in [
        ("RROW", "ROW_HEADER", "FROW", "SAME_ROW_LEFT_LABEL"),
        ("RCOL", "COLUMN_HEADER", "FCOL", "COLUMN_GRID_OVERLAP"),
        ("RPER", "PERIOD", "FPER", "COLUMN_GRID_OVERLAP"),
        ("RUNIT", "UNIT", "FUNIT", "CELL_ROW_OR_COLUMN_DECLARATION"),
    ]:
        rels.append({"relation_id": rid, "from_id": "C1", "kind": kind,
                     "to_ids": [target], "method": method, "scope_id": "SUB1",
                     "source_dom_paths": [next(f["dom_path"] for f in frags if f["fragment_id"] == target)],
                     "status": "EXPLICIT"})
    rels += [
        {"relation_id": "RBASIS", "from_id": "C1", "kind": "BASIS", "to_ids": [],
         "method": "SOURCE_UNKNOWN", "scope_id": "SUB1", "source_dom_paths": [], "status": "UNKNOWN"},
        {"relation_id": "RNOTE", "from_id": "C1", "kind": "FOOTNOTE", "to_ids": [],
         "method": "NO_MARKER", "scope_id": "SUB1", "source_dom_paths": [], "status": "NOT_APPLICABLE"},
    ]
    group = {"group_id": "G1", "anchor_cell_id": "C1", "relation_ids": [r["relation_id"] for r in rels]}
    doc = {"document_id": doc_id, "event_group_id": "E1", "symbol": "SYNTH",
           "source_fragments": frags, "numeric_cells": [cell], "relations": rels,
           "evidence_groups": [group]}
    lines = [f"[CTX id={f['fragment_id']}] {f['text']}" for f in frags]
    lines.append("[CELL id=C1 refs=FROW,FCOL,FPER,FUNIT] 44.1")
    selected = "\n".join(lines)
    spans = []
    pos = 0
    for i, line in enumerate(lines):
        ident = frags[i]["fragment_id"] if i < len(frags) else "C1"
        spans.append({"id": ident, "kind": "CTX" if i < len(frags) else "CELL",
                      "document_id": doc_id, "start": pos, "end": pos + len(line),
                      "source_id": ident})
        pos += len(line) + 1
    event = {"event_group_id": "E1", "symbol": "SYNTH", "max_characters": 20000,
             "selected_text": selected, "selected_text_characters": len(selected),
             "selected_text_sha256": hashlib.sha256(selected.encode()).hexdigest(),
             "model_visible_spans": spans, "visible_context_ids": [f["fragment_id"] for f in frags],
             "selected_group_ids": ["G1"],
             "group_inventory": [{"group_id": "G1", "status": "SELECTED", "document_id": doc_id,
                                  "anchor_cell_id": "C1", "relation_ids": group["relation_ids"]}]}
    candidate = {"candidate_id": "SYNTH_FACT", "source_document_id": doc_id}
    review = {"status": "CODEX_REVIEW_SOURCE_SUPPORTED", "final": {"value_text": "44.1",
              "basis": None, "comparison_status": "UNKNOWN_OR_NOT_REQUIRED"},
              "field_evidence": {"value": [{"dom_path": rowdom}]}}
    return doc, event, candidate, review


class V23EvaluatorTests(unittest.TestCase):
    def test_shared_context_reachable_and_exact_numeric_cell(self):
        doc, event, candidate, review = fixture()
        index = index_parsed_documents([doc])
        visible = validate_visible_event(event, index, 20000)
        row = structural_status_for_candidate(candidate, review, index[doc["document_id"]], visible)
        self.assertEqual(row["cell_match"], "UNIQUE_EXACT_VALUE_CELL")
        self.assertEqual(row["reference"], "REACHABLE")
        self.assertEqual(row["structure"], "EXACT_CELL_RELATIONS_MECHANICAL")
        self.assertEqual(row["all_fields"], "SOURCE_BASIS_UNKNOWN")

    def test_reference_only_in_sidecar_is_rejected(self):
        doc, event, _, _ = fixture()
        event["selected_text"] = event["selected_text"].replace("refs=FROW,FCOL,FPER,FUNIT", "refs=FROW,FCOL,FUNIT")
        event["selected_text_characters"] = len(event["selected_text"])
        event["selected_text_sha256"] = hashlib.sha256(event["selected_text"].encode()).hexdigest()
        with self.assertRaises(StructureContractError):
            validate_visible_event(event, index_parsed_documents([doc]), 20000)

    def test_wrong_column_period_is_rejected_even_in_same_table(self):
        doc, _, _, _ = fixture()
        cell, period = doc["numeric_cells"][0], next(f for f in doc["source_fragments"] if f["fragment_id"] == "FPER")
        wrong = deepcopy(period)
        wrong["column_start"], wrong["column_end"] = 2, 3
        relation = next(r for r in doc["relations"] if r["relation_id"] == "RPER")
        self.assertEqual(relation_scope_state(cell, relation, wrong), "WRONG_BINDING")

    def test_unit_from_previous_subtable_is_rejected(self):
        doc, _, _, _ = fixture()
        cell, unit = doc["numeric_cells"][0], next(f for f in doc["source_fragments"] if f["fragment_id"] == "FUNIT")
        wrong = deepcopy(unit)
        wrong["subtable_id"] = "PREVIOUS_SUBTABLE"
        relation = next(r for r in doc["relations"] if r["relation_id"] == "RUNIT")
        self.assertEqual(relation_scope_state(cell, relation, wrong), "WRONG_BINDING")

    def test_explicit_relation_with_obsolete_to_id_is_rejected(self):
        doc, _, _, _ = fixture()
        relation = next(r for r in doc["relations"] if r["relation_id"] == "RPER")
        relation["to_id"] = relation.pop("to_ids")[0]
        with self.assertRaises(StructureContractError):
            index_parsed_documents([doc])

    def test_duplicate_amount_in_same_row_is_ambiguous(self):
        doc, _, candidate, review = fixture()
        another = deepcopy(doc["numeric_cells"][0])
        another.update(cell_id="C2", column_index=2, column_start=2, column_end=3,
                       cell_dom_path=another["row_dom_path"] + " > td:nth-of-type(2)")
        doc["numeric_cells"].append(another)
        state, ident = candidate_cell_state(candidate, review, index_parsed_documents([doc])["SYNTH_DOC"])
        self.assertEqual((state, ident), ("AMBIGUOUS_CELL_MATCH", None))

    def test_div_reference_does_not_promote_nested_table_cell(self):
        doc, _, candidate, review = fixture()
        review["field_evidence"]["value"][0]["dom_path"] = "html:nth-of-type(1) > body:nth-of-type(1) > div:nth-of-type(1)"
        state, ident = candidate_cell_state(candidate, review, index_parsed_documents([doc])["SYNTH_DOC"])
        self.assertEqual((state, ident), ("PARAGRAPH_NO_GRID_UNEVALUABLE", None))

    def test_explicit_paragraph_anchor_is_separate_from_table_metric(self):
        doc, _, candidate, review = fixture()
        doc["source_fragments"] = []
        doc["relations"] = []
        p = doc["numeric_cells"][0]
        p.update(anchor_kind="PARAGRAPH", table_id=None, subtable_id=None,
                 row_start=None, row_end=None, column_start=None, column_end=None,
                 cell_dom_path="html:nth-of-type(1) > body:nth-of-type(1) > div:nth-of-type(1)",
                 row_dom_path=None, text="Revenue was $44.1 billion", measurement_text="44.1")
        doc["evidence_groups"][0]["relation_ids"] = []
        review["field_evidence"]["value"][0]["dom_path"] = p["cell_dom_path"]
        state, ident = candidate_cell_state(candidate, review, index_parsed_documents([doc])["SYNTH_DOC"])
        self.assertEqual((state, ident), ("UNIQUE_EXACT_VALUE_CELL", "C1"))

    def test_source_ambiguity_is_never_promoted(self):
        doc, event, candidate, review = fixture()
        review["status"] = "CODEX_REVIEW_AMBIGUOUS"
        indexed = index_parsed_documents([doc])
        result = structural_status_for_candidate(candidate, review, indexed["SYNTH_DOC"],
                                                 validate_visible_event(event, indexed, 20000))
        self.assertEqual(result["all_fields"], "SOURCE_REVIEW_AMBIGUOUS_NOT_PROMOTED")

    def test_literal_number_identity_and_old_structure_status(self):
        self.assertTrue(exact_numeric_cell_match("approximately $44.1 billion", "44.1"))
        self.assertTrue(exact_numeric_cell_match("27% to 28%", "27%-28%"))
        self.assertFalse(exact_numeric_cell_match("44.1", "144.1"))
        self.assertEqual(legacy_structure_status(), "STRUCTURE_UNEVALUABLE_NO_CELL_RELATION_SCHEMA")

    def test_parsed_primary_without_visible_spans_still_maps_to_its_event(self):
        exhibit, event, _, _ = fixture()
        primary = deepcopy(exhibit)
        primary["document_id"] = "SYNTH_PRIMARY"
        primary["source_fragments"] = []
        primary["numeric_cells"] = []
        primary["relations"] = []
        primary["evidence_groups"] = []
        indexed = index_parsed_documents([exhibit, primary])
        by_document, omissions = index_validated_events(indexed, [event], 20000)
        self.assertEqual(set(by_document), {"SYNTH_DOC", "SYNTH_PRIMARY"})
        self.assertEqual(by_document["SYNTH_PRIMARY"]["by_document_text"].get("SYNTH_PRIMARY", ""), "")
        self.assertFalse(omissions)

    def test_unmatched_parsed_event_is_rejected_even_if_no_spans(self):
        exhibit, event, _, _ = fixture()
        orphan = deepcopy(exhibit)
        orphan["document_id"] = "SYNTH_ORPHAN"
        orphan["event_group_id"] = "UNMATCHED_EVENT"
        orphan["source_fragments"] = []
        orphan["numeric_cells"] = []
        orphan["relations"] = []
        orphan["evidence_groups"] = []
        with self.assertRaises(StructureContractError):
            index_validated_events(index_parsed_documents([exhibit, orphan]), [event], 20000)


if __name__ == "__main__":
    unittest.main()
