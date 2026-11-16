"""Fictional structural and budget counterexamples for the v2.5 adapter."""
from __future__ import annotations

from hashlib import sha256
from copy import deepcopy
import json
from pathlib import Path
import unittest

from scripts.market_observer_financial_text_v25.adapter import (
    TOPIC_PRIORITY,
    assemble_event, discover_tables, parse_document_expanded,
    validate_model_visible_event,
)


def sample(extra: str = "") -> tuple[bytes, dict]:
    raw = ("<html><body><p>Revenue outlook was $13 million.</p>"
           "<table id='layout'><tr><td>logo</td></tr></table>"
           "<table id='financial'><tr><td>In millions</td><th>Q1 2025</th></tr>"
           "<tr><td>Revenue</td><td>$12</td></tr>"
           "<tr><td>Legal cost</td><td>$3</td></tr></table>"
           + extra + "</body></html>").encode("utf-8")
    meta = {
        "document_id": "fictional-exhibit", "event_group_id": "event-a",
        "symbol": "FICTIONAL", "announcement_date": "2025-04-03",
        "source_kind": "ISSUER_EARNINGS", "raw_body_sha256": sha256(raw).hexdigest(),
    }
    return raw, meta


class V25AdapterTests(unittest.TestCase):
    def test_topic_priority_matches_frozen_plan(self):
        root = Path(__file__).resolve().parents[1]
        config = json.loads((root / "configs/market_observer_financial_text_v25_20261116.json")
                            .read_text(encoding="utf-8"))
        self.assertEqual(tuple(config["selection"]["topic_order"]), TOPIC_PRIORITY)

    def test_all_tables_censused_without_reference_paths(self):
        raw, meta = sample()
        inventory = discover_tables(raw, meta)
        self.assertEqual(len(inventory), 2)
        self.assertEqual(inventory[0]["status"], "NESTED_OR_LAYOUT")
        parsed = parse_document_expanded(raw, meta)
        self.assertEqual(len(parsed["table_inventory"]), 2)
        self.assertEqual(parsed["table_inventory"][1]["status"], "PROCESSED")
        self.assertEqual(len(parsed["tables"]), 1)
        self.assertEqual(len(parsed["tables"][0]["bindings"]), 2)
        self.assertEqual(len(parsed["paragraph_inventory"]), 1)
        self.assertEqual(parsed["paragraph_inventory"][0]["status"],
                         "PARAGRAPH_CANDIDATE_NOT_TABLE_PARSED")

    def test_final_text_counts_all_refs_and_readback(self):
        raw, meta = sample()
        parsed = parse_document_expanded(raw, meta)
        packed = assemble_event([parsed], {meta["document_id"]: raw})
        self.assertEqual(len(packed["model_visible_text"]), packed["selected_text_characters"])
        self.assertEqual(packed["promotion_status"], "REJECTED_CANDIDATE")
        self.assertEqual(len(packed["selected_group_ids"]), 1)
        self.assertEqual(packed["omission_counts"]["NON_TARGET_TOPIC"], 1)
        self.assertIn("[CTX id=", packed["model_visible_text"])
        self.assertIn("[VALUE id=", packed["model_visible_text"])
        self.assertIn("[PARAGRAPH id=", packed["model_visible_text"])
        self.assertEqual(len(packed["selected_paragraph_ids"]), 1)
        self.assertEqual(packed["paragraph_group_inventory"][0]["structured_fact_status"],
                         "NOT_EVALUATED_PARAGRAPH_SEPARATE")
        validate_model_visible_event(packed)
        dangling = deepcopy(packed)
        selected = next(g for g in dangling["group_inventory"] if g["selected"])
        selected["relation_refs"]["row_metric"] = ["R-missing"]
        with self.assertRaises(AssertionError):
            validate_model_visible_event(dangling)
        packed["model_visible_text"] += "X"
        with self.assertRaises(ValueError):
            validate_model_visible_event(packed)

    def test_known_wrong_binding_is_vetoed_without_retargeting(self):
        raw, meta = sample()
        parsed = parse_document_expanded(raw, meta)
        value_path = next(b["value_cell_dom_path"] for b in parsed["tables"][0]["bindings"]
                          if b["row_metric_text"] == "Revenue")
        packed = assemble_event([parsed], {meta["document_id"]: raw},
                                rejected_binding_keys={(meta["document_id"], value_path)})
        self.assertEqual(packed["selected_group_ids"], [])
        self.assertEqual(packed["omission_counts"]["KNOWN_WRONG_RELATION"], 1)

    def test_whole_group_budget_never_emits_bare_value(self):
        raw, meta = sample()
        parsed = parse_document_expanded(raw, meta)
        packed = assemble_event([parsed], {meta["document_id"]: raw}, max_characters=10)
        self.assertEqual(packed["model_visible_text"], "")
        self.assertEqual(packed["omission_counts"]["WHOLE_GROUP_OVER_BUDGET"], 1)

    def test_source_hash_and_cross_event_guard(self):
        raw, meta = sample()
        with self.assertRaises(ValueError):
            discover_tables(raw + b" ", meta)
        parsed = parse_document_expanded(raw, meta)
        with self.assertRaises(ValueError):
            assemble_event([parsed], {meta["document_id"]: raw + b" "})

    def test_conflicting_scale_is_not_packed_as_complete(self):
        html = ("<html><body><table><caption>In millions</caption>"
                "<tr><td>In billions</td><th>Q1 2025</th></tr>"
                "<tr><td>Revenue</td><td>$12</td></tr></table></body></html>").encode()
        meta = {"document_id": "fictional-conflict", "event_group_id": "e",
                "symbol": "F", "announcement_date": "2025-04-04",
                "source_kind": "ISSUER_EARNINGS", "raw_body_sha256": sha256(html).hexdigest()}
        parsed = parse_document_expanded(html, meta)
        packed = assemble_event([parsed], {meta["document_id"]: html})
        self.assertEqual(packed["selected_group_ids"], [])
        self.assertEqual(packed["omission_counts"]["RELATION_CONFLICT"], 1)

    def test_topic_priority_is_independent_of_source_row_order(self):
        html = ("<html><body><table>"
                "<tr><td>In millions</td><th>Q1 2025</th></tr>"
                "<tr><td>Net income</td><td>$3</td></tr>"
                "<tr><td>Revenue</td><td>$12</td></tr></table></body></html>").encode()
        meta = {"document_id": "fictional-order", "event_group_id": "e",
                "symbol": "F", "announcement_date": "2025-04-04",
                "source_kind": "ISSUER_EARNINGS", "raw_body_sha256": sha256(html).hexdigest()}
        parsed = parse_document_expanded(html, meta)
        packed = assemble_event([parsed], {meta["document_id"]: html})
        self.assertEqual(len(packed["selected_group_ids"]), 2)
        first_id = packed["selected_group_ids"][0]
        first = next(g for g in packed["group_inventory"] if g["group_id"] == first_id)
        self.assertIn("revenue", first["topics"])

    def test_complete_relation_group_precedes_earlier_partial_under_budget(self):
        html = ("<html><body>"
                "<table><tr><td>In millions</td><th>Q1 2025</th></tr>"
                "<tr><td>Revenue</td><td>$11</td></tr></table>"
                "<table><tr><th colspan='2'>GAAP</th></tr>"
                "<tr><td>In millions</td><th>Q1 2025</th></tr>"
                "<tr><td>Revenue</td><td>$12</td></tr></table>"
                "</body></html>").encode()
        meta = {"document_id": "fictional-complete-first", "event_group_id": "e",
                "symbol": "F", "announcement_date": "2025-04-04",
                "source_kind": "ISSUER_EARNINGS", "raw_body_sha256": sha256(html).hexdigest()}
        parsed = parse_document_expanded(html, meta)
        bindings = [b for t in parsed["tables"] for b in t["bindings"]]
        self.assertEqual([b["full_status"] for b in bindings],
                         ["PARTIAL_UNKNOWN", "MECHANICALLY_COMPLETE"])
        earlier = bindings[0]["value_cell_dom_path"]
        complete_only = assemble_event(
            [parsed], {meta["document_id"]: html},
            rejected_binding_keys={(meta["document_id"], earlier)},
        )
        exact_budget = complete_only["selected_text_characters"]
        packed = assemble_event([parsed], {meta["document_id"]: html},
                                max_characters=exact_budget)
        self.assertEqual(len(packed["selected_group_ids"]), 1)
        selected = next(g for g in packed["group_inventory"] if g["selected"])
        self.assertEqual(selected["kernel_full_status"], "MECHANICALLY_COMPLETE")
        self.assertEqual(packed["omission_counts"]["WHOLE_GROUP_OVER_BUDGET"], 1)


if __name__ == "__main__":
    unittest.main()
