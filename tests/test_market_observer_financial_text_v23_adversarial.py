"""Synthetic counterexamples for v2.3 source structure and evidence binding.

These are invented HTML snippets. They do not use reviewed fact answers or
saved issuer documents, and a passing test is not a source-truth judgment.
"""

from __future__ import annotations

import hashlib
import copy
import unittest

from scripts.market_observer_financial_text_v23.adapter import assemble_event
from scripts.market_observer_financial_text_v23.build import validate_event
from scripts.market_observer_financial_text_v23.structure import parse_document


def parsed(html: str, *, document_id: str = "synthetic-document") -> dict:
    raw = html.encode("utf-8")
    return parse_document(raw, {
        "raw_body_sha256": hashlib.sha256(raw).hexdigest(),
        "document_id": document_id,
        "event_group_id": "synthetic-event",
        "symbol": "ZZZ",
        "source_kind": "ISSUER_EARNINGS",
        "announcement_date": "2025-05-01",
        "source_url": "https://example.invalid/synthetic",
    })


def value_group(doc: dict, value: str, *, column: int | None = None) -> tuple[dict, dict]:
    cells = {c["cell_id"]: c for c in doc["numeric_cells"]}
    matches = [(g, cells[g["anchor_cell_id"]]) for g in doc["evidence_groups"]
               if cells[g["anchor_cell_id"]]["text"] == value
               and (column is None or cells[g["anchor_cell_id"]]["column_index"] == column)]
    if len(matches) != 1:
        raise AssertionError(f"Expected one exact numeric-cell group for value={value!r}, column={column}; found {len(matches)}")
    return matches[0]


def context_text(doc: dict, group: dict, kind: str) -> str:
    fragments = {f["fragment_id"]: f for f in doc["source_fragments"]}
    return " | ".join(fragments[fid]["text"] for fid in group["relation_refs"][kind])


class V23AdversarialStructureTests(unittest.TestCase):
    def test_identical_amounts_in_different_columns_keep_distinct_periods(self) -> None:
        doc = parsed("""<html><body><table>
          <tr><th>Metric</th><th>2025 USD millions</th><th>2024 USD millions</th></tr>
          <tr><td>Revenue</td><td>42.0</td><td>42.0</td></tr>
        </table></body></html>""")
        left, left_cell = value_group(doc, "42.0", column=1)
        right, right_cell = value_group(doc, "42.0", column=2)
        self.assertNotEqual(left_cell["cell_dom_path"], right_cell["cell_dom_path"])
        self.assertIn("2025", context_text(doc, left, "period"))
        self.assertNotIn("2024", context_text(doc, left, "period"))
        self.assertIn("2024", context_text(doc, right, "period"))
        self.assertNotIn("2025", context_text(doc, right, "period"))

    def test_multilevel_colspan_header_includes_wide_period_and_exact_year(self) -> None:
        doc = parsed("""<html><body><table>
          <tr><th rowspan="2">Metric</th><th colspan="2">Three months ended</th></tr>
          <tr><th>2025 USD millions</th><th>2024 USD millions</th></tr>
          <tr><td>Revenue</td><td>42.0</td><td>41.0</td></tr>
        </table></body></html>""")
        current, _ = value_group(doc, "42.0", column=1)
        prior, _ = value_group(doc, "41.0", column=2)
        self.assertIn("Three months ended", context_text(doc, current, "period"))
        self.assertIn("2025", context_text(doc, current, "period"))
        self.assertNotIn("2024", context_text(doc, current, "period"))
        self.assertIn("2024", context_text(doc, prior, "period"))

    def test_one_wide_header_with_two_years_cannot_assign_a_year_to_each_column(self) -> None:
        doc = parsed("""<html><body><table>
          <tr><th>Metric</th><th colspan="2">2025 and 2024 USD millions</th></tr>
          <tr><td>Revenue</td><td>42.0</td><td>41.0</td></tr>
        </table></body></html>""")
        for value in ("42.0", "41.0"):
            group, _ = value_group(doc, value)
            self.assertNotEqual(group["relation_status"]["period"], "EXPLICIT")

    def test_gaap_and_non_gaap_columns_do_not_cross(self) -> None:
        doc = parsed("""<html><body><table>
          <tr><th>Metric</th><th>GAAP 2025 USD millions</th><th>Non-GAAP 2025 USD millions</th></tr>
          <tr><td>Net income</td><td>42.0</td><td>43.0</td></tr>
        </table></body></html>""")
        gaap, _ = value_group(doc, "42.0", column=1)
        adjusted, _ = value_group(doc, "43.0", column=2)
        self.assertEqual(gaap["relation_status"]["basis"], "EXPLICIT")
        self.assertEqual(adjusted["relation_status"]["basis"], "EXPLICIT")
        self.assertEqual(context_text(doc, gaap, "basis"), "GAAP 2025 USD millions")
        self.assertEqual(context_text(doc, adjusted, "basis"), "Non-GAAP 2025 USD millions")

    def test_conflicting_row_and_column_basis_must_abstain(self) -> None:
        doc = parsed("""<html><body><table>
          <tr><th>Metric</th><th>GAAP 2025 USD millions</th></tr>
          <tr><td>Non-GAAP net income</td><td>42.0</td></tr>
        </table></body></html>""")
        group, _ = value_group(doc, "42.0")
        self.assertEqual(group["relation_status"]["basis"], "CONFLICT")
        self.assertEqual(group["eligibility"], "RELATION_CONFLICT")

    def test_repeated_header_resets_unit_period_and_basis_scope(self) -> None:
        doc = parsed("""<html><body><table>
          <tr><th>Metric</th><th>GAAP 2025 USD millions</th></tr>
          <tr><td>Revenue</td><td>42.0</td></tr>
          <tr><th>Metric</th><th>Non-GAAP 2026 USD billions</th></tr>
          <tr><td>Revenue</td><td>43.0</td></tr>
        </table></body></html>""")
        first, first_cell = value_group(doc, "42.0")
        second, second_cell = value_group(doc, "43.0")
        self.assertNotEqual(first_cell["subtable_id"], second_cell["subtable_id"])
        for kind in ("period", "unit", "basis"):
            self.assertNotIn("2025 USD millions", context_text(doc, second, kind))
            self.assertNotIn("2026 USD billions", context_text(doc, first, kind))
        self.assertIn("Non-GAAP 2026 USD billions", context_text(doc, second, "basis"))

    def test_unit_outside_table_is_scoped_into_numeric_group(self) -> None:
        doc = parsed("""<html><body>
          <p>Results for the quarter ended June 30, 2025; amounts in USD millions.</p>
          <table><tr><th>Metric</th><th>June 30, 2025</th></tr>
          <tr><td>Revenue</td><td>42.0</td></tr></table>
        </body></html>""")
        group, _ = value_group(doc, "42.0")
        self.assertEqual(group["relation_status"]["unit"], "EXPLICIT")
        self.assertIn("USD millions", context_text(doc, group, "unit"))

    def test_table_caption_unit_and_period_are_in_the_table_scope(self) -> None:
        doc = parsed("""<html><body><table>
          <caption>Quarter ended June 30, 2025; USD millions</caption>
          <tr><th>Metric</th><th>Value</th></tr>
          <tr><td>Revenue</td><td>42.0</td></tr>
        </table></body></html>""")
        group, _ = value_group(doc, "42.0")
        self.assertIn("June 30, 2025", context_text(doc, group, "period"))
        self.assertIn("USD millions", context_text(doc, group, "unit"))

    def test_per_share_exception_does_not_apply_table_millions_as_exact_unit(self) -> None:
        doc = parsed("""<html><body><table>
          <caption>USD millions, except per share data</caption>
          <tr><th>Metric</th><th>2025</th></tr>
          <tr><td>Diluted earnings per share</td><td>$0.42</td></tr>
        </table></body></html>""")
        group, _ = value_group(doc, "$0.42")
        unit_text = context_text(doc, group, "unit")
        self.assertIn("per share", unit_text.lower())
        self.assertNotEqual(group["relation_status"]["unit"], "CONFLICT")
        self.assertNotEqual(group["eligibility"], "ELIGIBLE_FULL_FIELDS")

    def test_new_chapter_intro_replaces_prior_period_and_unit(self) -> None:
        doc = parsed("""<html><body>
          <p>Results for the quarter ended March 31, 2025; amounts in USD millions.</p>
          <table><tr><th>Metric</th><th>Value</th></tr><tr><td>Revenue</td><td>42.0</td></tr></table>
          <p>Results for the quarter ended June 30, 2025; amounts in USD billions.</p>
          <table><tr><th>Metric</th><th>Value</th></tr><tr><td>Revenue</td><td>43.0</td></tr></table>
        </body></html>""")
        second, _ = value_group(doc, "43.0")
        self.assertIn("June 30, 2025", context_text(doc, second, "period"))
        self.assertNotIn("March 31, 2025", context_text(doc, second, "period"))
        self.assertIn("USD billions", context_text(doc, second, "unit"))
        self.assertNotIn("USD millions", context_text(doc, second, "unit"))

    def test_rowspan_label_applies_to_both_value_rows(self) -> None:
        doc = parsed("""<html><body><table>
          <tr><th>Metric</th><th>Year</th><th>USD millions</th></tr>
          <tr><td rowspan="2">Revenue</td><td>2025</td><td>42.0</td></tr>
          <tr><td>2024</td><td>41.0</td></tr>
        </table></body></html>""")
        second, cell = value_group(doc, "41.0")
        self.assertEqual(cell["row_index"], 2)
        self.assertIn("Revenue", context_text(doc, second, "row_header"))
        self.assertIn("2024", context_text(doc, second, "period"))

    def test_colspan_crossing_an_occupied_rowspan_is_not_eligible(self) -> None:
        doc = parsed("""<html><body><table>
          <tr><th rowspan="2">Metric</th><th>Year</th>
          <th rowspan="2">USD millions</th><th>Amount</th></tr>
          <tr><td colspan="2">Revenue 2025</td><td>42.0</td></tr>
        </table></body></html>""")
        group, _ = value_group(doc, "42.0")
        self.assertNotIn(group["eligibility"], {"ELIGIBLE_FULL_FIELDS", "ELIGIBLE_KNOWN_FIELDS"})

    def test_nested_table_is_parsed_separately_and_not_bound_to_outer_cell(self) -> None:
        doc = parsed("""<html><body><table><tr><td>Container</td><td>
          <table><tr><th>Metric</th><th>2025 USD millions</th></tr>
          <tr><td>Revenue</td><td>42.0</td></tr></table>
        </td></tr></table></body></html>""")
        group, cell = value_group(doc, "42.0")
        self.assertEqual(len(doc["tables"]), 2)
        self.assertIn("2025", context_text(doc, group, "period"))
        self.assertNotEqual(cell["table_id"], doc["tables"][0]["table_id"])

    def test_duplicate_footnote_target_id_cannot_select_first_match(self) -> None:
        doc = parsed("""<html><body>
          <table><tr><th>Metric</th><th>2025 USD millions</th></tr>
          <tr><td>Revenue</td><td>42.0<a href="#note"><sup>1</sup></a></td></tr></table>
          <p id="note">First table note</p>
          <table><tr><th>Metric</th><th>2026 USD millions</th></tr>
          <tr><td>Revenue</td><td>43.0<a href="#note"><sup>1</sup></a></td></tr></table>
          <p id="note">Second table note</p>
        </body></html>""")
        second, _ = value_group(doc, "43.0 1")
        self.assertNotEqual(second["relation_status"]["footnote"], "EXPLICIT")
        self.assertNotEqual(second["eligibility"], "ELIGIBLE_FULL_FIELDS")

    def test_unlinked_repeated_superscript_does_not_cross_tables(self) -> None:
        doc = parsed("""<html><body>
          <table><tr><th>Metric</th><th>2025 USD millions</th></tr>
          <tr><td>Revenue</td><td>42.0<sup>1</sup></td></tr></table>
          <p>1. First table note</p>
          <table><tr><th>Metric</th><th>2026 USD millions</th></tr>
          <tr><td>Revenue</td><td>43.0<sup>1</sup></td></tr></table>
          <p>1. Second table note</p>
        </body></html>""")
        group, _ = value_group(doc, "43.0 1")
        self.assertEqual(group["relation_status"]["footnote"], "UNKNOWN")
        self.assertNotIn(group["eligibility"], {"ELIGIBLE_FULL_FIELDS", "ELIGIBLE_KNOWN_FIELDS"})

    def test_current_actual_and_future_guidance_columns_have_distinct_roles(self) -> None:
        doc = parsed("""<html><body><table>
          <tr><th>Metric</th><th>Actual quarter ended June 30, 2025 USD millions</th>
          <th>Next-quarter guidance USD millions</th></tr>
          <tr><td>Revenue</td><td>42.0</td><td>43.0</td></tr>
        </table></body></html>""")
        actual, _ = value_group(doc, "42.0", column=1)
        guidance, _ = value_group(doc, "43.0", column=2)
        self.assertNotEqual(actual["content_role"], guidance["content_role"])
        self.assertEqual(guidance["content_role"], "FUTURE_GUIDANCE")
        self.assertNotIn("June 30, 2025", context_text(doc, guidance, "period"))
        self.assertNotEqual(guidance["relation_status"]["period"], "EXPLICIT")


def _eligible_document(*, document_id: str = "synthetic-document") -> dict:
    return parsed("""<html><body><table>
      <tr><th>Metric</th><th>2025 USD millions</th></tr>
      <tr><td>Revenue</td><td>42.0</td></tr>
    </table></body></html>""", document_id=document_id)


class V23AdversarialModelInputTests(unittest.TestCase):
    def test_visible_prior_quarter_companion_is_not_repeated_as_anchor(self) -> None:
        doc = parsed("""<html><body><table>
          <tr><th>Metric</th><th>Current quarter 2025 USD millions</th>
          <th>Prior quarter 2025 USD millions</th></tr>
          <tr><td>Revenue</td><td>42.0</td><td>41.0</td></tr>
        </table></body></html>""")
        current, _ = value_group(doc, "42.0")
        prior, _ = value_group(doc, "41.0")
        self.assertEqual(current["relation_refs"]["comparison_value"], [prior["anchor_cell_id"]])
        event = assemble_event([doc], 20_000)
        validate_event(event, [doc], 20_000)
        self.assertIn(current["group_id"], event["selected_group_ids"])
        self.assertNotIn(prior["group_id"], event["selected_group_ids"])
        reasons = {item["group_id"]: item["reason"] for item in event["omitted_groups"]}
        self.assertEqual(reasons[prior["group_id"]], "ANCHOR_ALREADY_VISIBLE_AS_COMPARISON")
        companion_spans = [s for s in event["model_visible_spans"]
                           if s["source_id"] == prior["anchor_cell_id"]]
        self.assertEqual(len(companion_spans), 1)
        self.assertEqual(companion_spans[0]["role"], "COMPANION")

    def test_missing_required_unit_omits_numeric_group_whole(self) -> None:
        doc = parsed("""<html><body><table><tr><th>Metric</th><th>2025</th></tr>
          <tr><td>Revenue</td><td>42.0</td></tr></table></body></html>""")
        event = assemble_event([doc], 20_000)
        self.assertEqual(event["selected_group_ids"], [])
        self.assertNotIn("42.0", event["selected_text"])
        validate_event(event, [doc], 20_000)

    def test_under_budget_group_is_omitted_without_partial_context(self) -> None:
        doc = _eligible_document()
        full = assemble_event([doc], 20_000)
        self.assertTrue(full["selected_group_ids"])
        budget = len(full["selected_text"]) - 1
        short = assemble_event([doc], budget)
        self.assertEqual(short["selected_group_ids"], [])
        self.assertEqual(short["selected_text"], "")
        self.assertIn("WHOLE_GROUP_OVER_BUDGET", [x["reason"] for x in short["omitted_groups"]])
        validate_event(short, [doc], budget)

    def test_repeated_build_is_byte_identical_and_counts_actual_serialization(self) -> None:
        doc = _eligible_document()
        first = assemble_event([doc], 20_000)
        second = assemble_event([doc], 20_000)
        self.assertEqual(first, second)
        self.assertEqual(first["selected_text_characters"], len(first["selected_text"]))
        validate_event(first, [doc], 20_000)

    def test_tampered_model_visible_reference_is_rejected(self) -> None:
        doc = _eligible_document()
        event = assemble_event([doc], 20_000)
        validate_event(event, [doc], 20_000)
        group = event["selected_groups"][0]
        ref = group["context_ids"][0]
        self.assertIn(ref, event["selected_text"])
        tampered = copy.deepcopy(event)
        anchor = next(s for s in event["model_visible_spans"]
                      if s["source_id"] == group["anchor_cell_id"] and s["role"] == "ANCHOR")
        before = tampered["selected_text"][:anchor["start"]]
        line = tampered["selected_text"][anchor["start"]:anchor["end"]]
        after = tampered["selected_text"][anchor["end"]:]
        self.assertIn(ref, line)
        tampered["selected_text"] = before + line.replace(ref, "X" * len(ref), 1) + after
        tampered["selected_text_sha256"] = hashlib.sha256(tampered["selected_text"].encode()).hexdigest()
        with self.assertRaises((RuntimeError, ValueError)):
            validate_event(tampered, [doc], 20_000)

    def test_cross_document_context_sidecar_is_rejected(self) -> None:
        first = _eligible_document(document_id="synthetic-one")
        second = _eligible_document(document_id="synthetic-two")
        event = assemble_event([first, second], 20_000)
        validate_event(event, [first, second], 20_000)
        self.assertGreaterEqual(len(event["selected_groups"]), 2)
        first_group = event["selected_groups"][0]
        other_context = next(f["fragment_id"] for f in second["source_fragments"]
                             if f["fragment_id"] in event["selected_context_ids"])
        tampered = copy.deepcopy(event)
        target = next(g for g in tampered["group_inventory"] if g["group_id"] == first_group["group_id"])
        target["context_ids"].append(other_context)
        with self.assertRaises((RuntimeError, ValueError)):
            validate_event(tampered, [first, second], 20_000)

    def test_wrong_same_document_period_sidecar_is_rejected(self) -> None:
        doc = parsed("""<html><body><table>
          <tr><th>Metric</th><th>2025 USD millions</th><th>2024 USD millions</th></tr>
          <tr><td>Revenue</td><td>42.0</td><td>41.0</td></tr>
        </table></body></html>""")
        event = assemble_event([doc], 20_000)
        validate_event(event, [doc], 20_000)
        self.assertEqual(len(event["selected_groups"]), 2)
        first = event["selected_groups"][0]
        second = event["selected_groups"][1]
        self.assertNotEqual(first["relation_refs"]["period"], second["relation_refs"]["period"])
        tampered = copy.deepcopy(event)
        target = next(g for g in tampered["group_inventory"] if g["group_id"] == first["group_id"])
        target["relation_refs"]["period"] = second["relation_refs"]["period"][:]
        with self.assertRaises((RuntimeError, ValueError)):
            validate_event(tampered, [doc], 20_000)


if __name__ == "__main__":
    unittest.main()
