"""Synthetic contract tests for the v2.2 model-visible comparison."""

import hashlib
import unittest

from scripts.market_observer_financial_text_v22.compare_retention import (
    binding_for_review,
    classify_prior_loss,
    event_dates_from_blocks,
    validate_event,
)


def source_block(block_id, document_id, raw_text):
    return {"block_id": block_id, "document_id": document_id,
            "event_group_id": "event-1", "symbol": "NVDA", "raw_text": raw_text}


def fixture(two_groups=False):
    context = "Quarter ended 2025; in USD millions"
    anchor = "Revenue $10"
    lines = [f"[context=C1 doc=d1] {context}", f"[group=G1 doc=d1 refs=C1] {anchor}"]
    blocks = {"b-context": source_block("b-context", "d1", context),
              "b-revenue": source_block("b-revenue", "d1", anchor)}
    groups = [{"group_id": "G1", "document_id": "d1", "anchor_block_id": "b-revenue",
               "context_ids": ["C1"], "status": "SELECTED"}]
    if two_groups:
        lines = [f"[group=G1 doc=d1 refs=none] {anchor}",
                 f"[context=C1 doc=d1] {context}",
                 "[group=G2 doc=d1 refs=C1] Gross margin 50%"]
        blocks["b-margin"] = source_block("b-margin", "d1", "Gross margin 50%")
        groups[0]["context_ids"] = []
        groups.append({"group_id": "G2", "document_id": "d1", "anchor_block_id": "b-margin",
                       "context_ids": ["C1"], "status": "SELECTED"})
    text = "\n".join(lines)
    spans = []
    for line in lines:
        start = text.index(line)
        if "context=C1" in line:
            spans.append({"start": start, "end": start + len(line), "document_id": "d1",
                          "raw_start": start + line.index(context), "raw_end": start + line.index(context) + len(context),
                          "source_block_id": "b-context", "context_id": "C1"})
        elif "group=G1" in line:
            spans.append({"start": start, "end": start + len(line), "document_id": "d1",
                          "raw_start": start + line.index(anchor), "raw_end": start + line.index(anchor) + len(anchor),
                          "source_block_id": "b-revenue", "group_id": "G1"})
        else:
            spans.append({"start": start, "end": start + len(line), "document_id": "d1",
                          "raw_start": start + line.index("Gross margin 50%"),
                          "raw_end": start + line.index("Gross margin 50%") + len("Gross margin 50%"),
                          "source_block_id": "b-margin", "group_id": "G2"})
    event = {"event_group_id": "event-1", "selected_text": text,
             "symbol": "NVDA",
             "selected_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
             "selected_text_characters": len(text), "max_characters": len(text),
             "model_visible_spans": spans, "selected_block_ids": list(blocks),
             "selected_group_ids": [g["group_id"] for g in groups],
             "selected_context_ids": ["C1"], "group_inventory": groups}
    review = {"status": "CODEX_REVIEW_SOURCE_SUPPORTED",
              "final": {"basis": None, "value_text": "$10", "comparison_status": None},
              "field_evidence": {"metric": [{"rendered_visible_text": "Revenue"}],
                                 "value": [{"rendered_visible_text": "$10"}],
                                 "period": [{"rendered_visible_text": "Quarter ended 2025"}],
                                 "unit": [{"rendered_visible_text": "USD millions"}]}}
    return event, blocks, review


class V22RetentionContractTest(unittest.TestCase):
    def test_event_dates_come_from_consistent_saved_source_metadata(self):
        rows = [{"event_group_id": "e1", "announcement_date": "2025-06-25"},
                {"event_group_id": "e1", "announcement_date": "2025-06-25"}]
        self.assertEqual(event_dates_from_blocks(rows), {"e1": "2025-06-25"})
        rows[1]["announcement_date"] = "2025-06-26"
        with self.assertRaisesRegex(ValueError, "conflicting announcement dates"):
            event_dates_from_blocks(rows)

    def test_visible_shared_context_can_bind_one_group(self):
        event, blocks, review = fixture()
        checked = validate_event(event, blocks, {"d1"}, len(event["selected_text"]))
        self.assertEqual(binding_for_review(review, "d1", checked),
                         "BOUND_GROUP_KNOWN_FIELDS_BASIS_UNKNOWN")

    def test_fields_present_in_document_do_not_imply_one_bound_group(self):
        event, blocks, review = fixture(two_groups=True)
        checked = validate_event(event, blocks, {"d1"}, len(event["selected_text"]))
        self.assertEqual(binding_for_review(review, "d1", checked), "NO_SINGLE_BOUND_GROUP")

    def test_sidecar_only_context_reference_is_rejected(self):
        event, blocks, _ = fixture()
        event["selected_text"] = event["selected_text"].replace("refs=C1", "refs=none")
        event["selected_text_characters"] = len(event["selected_text"])
        event["selected_text_sha256"] = hashlib.sha256(event["selected_text"].encode()).hexdigest()
        event["max_characters"] = len(event["selected_text"])
        event["model_visible_spans"][1]["end"] = len(event["selected_text"])
        event["model_visible_spans"][1]["raw_start"] = event["selected_text"].index("Revenue $10")
        event["model_visible_spans"][1]["raw_end"] = event["model_visible_spans"][1]["raw_start"] + len("Revenue $10")
        with self.assertRaisesRegex(ValueError, "sidecar context reference"):
            validate_event(event, blocks, {"d1"}, len(event["selected_text"]))

    def test_tampered_raw_offsets_are_rejected(self):
        event, blocks, _ = fixture()
        event["model_visible_spans"][1]["raw_start"] -= 1
        with self.assertRaisesRegex(ValueError, "raw offsets"):
            validate_event(event, blocks, {"d1"}, len(event["selected_text"]))

    def test_overlapping_spans_are_rejected(self):
        event, blocks, _ = fixture()
        event["model_visible_spans"][1]["start"] = event["model_visible_spans"][0]["end"] - 1
        with self.assertRaisesRegex(ValueError, "overlap"):
            validate_event(event, blocks, {"d1"}, len(event["selected_text"]))

    def test_referenced_context_must_precede_group(self):
        event, blocks, _ = fixture()
        event["selected_text"] = "\n".join(event["selected_text"].splitlines()[::-1])
        event["selected_text_characters"] = len(event["selected_text"])
        event["selected_text_sha256"] = hashlib.sha256(event["selected_text"].encode()).hexdigest()
        for span in event["model_visible_spans"]:
            fragment = "Revenue $10" if span.get("group_id") else "Quarter ended 2025; in USD millions"
            line = next(x for x in event["selected_text"].splitlines() if fragment in x)
            span["start"] = event["selected_text"].index(line)
            span["end"] = span["start"] + len(line)
            span["raw_start"] = event["selected_text"].index(fragment)
            span["raw_end"] = span["raw_start"] + len(fragment)
        with self.assertRaisesRegex(ValueError, "context appears after"):
            validate_event(event, blocks, {"d1"}, len(event["selected_text"]))

    def test_cross_document_context_is_rejected(self):
        event, blocks, _ = fixture()
        blocks["b-context"]["document_id"] = "d2"
        event["model_visible_spans"][0]["document_id"] = "d2"
        with self.assertRaisesRegex(ValueError, "cross-document"):
            validate_event(event, blocks, {"d1", "d2"}, len(event["selected_text"]))

    def test_cross_company_source_span_is_rejected(self):
        event, blocks, _ = fixture()
        blocks["b-context"]["symbol"] = "MU"
        with self.assertRaisesRegex(ValueError, "source document or event mismatch"):
            validate_event(event, blocks, {"d1"}, len(event["selected_text"]))

    def test_actual_serialized_budget_is_enforced(self):
        event, blocks, _ = fixture()
        with self.assertRaisesRegex(ValueError, "budget"):
            validate_event(event, blocks, {"d1"}, len(event["selected_text"]) - 1)

    def test_prior_source_ambiguity_and_budget_loss_stay_distinct(self):
        row = {"review_status": "CODEX_REVIEW_AMBIGUOUS", "footnote_unresolved": False,
               "fact_retention": {"v21_full_blocks": "COMPLETE", "v21_selected": "PARTIAL"}}
        self.assertEqual(classify_prior_loss(row), "SOURCE_REVIEW_AMBIGUOUS")
        row["review_status"] = "CODEX_REVIEW_SOURCE_SUPPORTED"
        self.assertEqual(classify_prior_loss(row), "COMPLETE_IN_FULL_STORE_OMITTED_BY_BUDGET")
        row["footnote_unresolved"] = True
        self.assertEqual(classify_prior_loss(row), "RELATION_OR_FOOTNOTE_UNRESOLVED")


if __name__ == "__main__":
    unittest.main()
