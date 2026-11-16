"""Focused tests for bounded staged analysis and source links."""
import unittest

from .analysis_contract_v17 import parse_validate, segments, validate_a, validate_b, validate_t


class ContractTest(unittest.TestCase):
    def test_numbered_bulletin_splits_without_losing_text(self):
        source = "1. Alpha reports sales.\n2. Beta denies a deal."
        parts = segments(None, source)
        self.assertEqual(len(parts), 2)
        self.assertIn("Alpha", parts[0])
        self.assertIn("Beta", parts[1])

    def test_ordinary_paragraph_not_heuristically_split(self):
        self.assertEqual(len(segments("Title", "Alpha reports sales. Beta denies a deal.")), 1)

    def test_every_extraction_field_needs_own_literal_quote(self):
        source = "甲公司称收入为12亿元。"
        obj = {"actor": {"value": "甲公司", "quote": "甲公司称"}, "entities": [],
            "category": None, "attribution": None, "claim_kind": {"value": "REPORT", "quote": "甲公司称"},
            "numbers": [{"value": "12", "unit": "亿元", "quote": "收入为12亿元"}],
            "tone": None, "uncertainty": None}
        self.assertTrue(validate_a(obj, source)["mechanical_valid"])
        obj["numbers"][0]["quote"] = "甲公司称"
        self.assertIn("numbers_0_QUOTE", validate_a(obj, source)["errors"])

    def test_source_name_cannot_be_added(self):
        obj = {"actor": {"value": "Takahashi", "quote": "高田创"}, "entities": [],
            "category": None, "attribution": None, "claim_kind": None,
            "numbers": [], "tone": None, "uncertainty": None}
        self.assertIn("actor_VALUE_NOT_IN_QUOTE", validate_a(obj, "高田创发言")["errors"])

    def test_duplicate_keys_are_rejected(self):
        with self.assertRaises(ValueError):
            parse_validate("B", '{"channel":"UNKNOWN","channel":"DEMAND"}', "source")

    def test_explanation_new_number_blocked(self):
        result = validate_b({"channel": "COST", "interpretation": "利润可能增加50%", "condition": None, "gap": None}, "成本上升")
        self.assertIn("interpretation_NEW_NUMBER", result["errors"])

    def test_translation_new_number_blocked(self):
        result = validate_t({"interpretation_en": "Profit rises 50%", "condition_en": None, "gap_en": None}, "利润或变动")
        self.assertIn("interpretation_en_NEW_NUMBER", result["errors"])


if __name__ == "__main__":
    unittest.main()
