"""Focused synthetic checks for source actors and per-field evidence."""
import json
import unittest

from scripts.quant_personal_feed.analysis_contract_v18_1 import (
    backfill, parse_validate, sentence_mentions, validate,
)


S1 = "虚构研究所分析员甲表示：合成设备计划推迟。"
S2 = "英伟达在本合成测试中只作为提及对象。"
PAYLOAD = {
    "event_id": "E01",
    "sentences": [
        {"id": "E01-S01", "start": 0, "end": len(S1), "text": S1},
        {"id": "E01-S02", "start": len(S1), "end": len(S1) + len(S2), "text": S2},
    ],
}


def example():
    return {
        "actor_text": "分析员甲", "actor_evidence_id": "E01-S01",
        "attribution_text": "分析员甲表示", "attribution_evidence_id": "E01-S01",
        "claim_kind": "REPORT", "claim_evidence_id": "E01-S01",
        "category": "UNKNOWN", "category_evidence_id": None,
        "tone": "NEUTRAL", "tone_evidence_id": "E01-S01",
        "uncertainty": "UNKNOWN", "uncertainty_evidence_id": None,
    }


class V181ContractTests(unittest.TestCase):
    def test_unlisted_source_actor_is_allowed_with_exact_evidence(self):
        result = validate(example(), PAYLOAD)
        self.assertTrue(result["valid"], result)
        filled = backfill(example(), PAYLOAD)
        self.assertEqual(filled["evidence"]["actor_evidence_id"]["start"], 0)
        self.assertEqual(filled["watchlist_mentions"][0]["entity_id"], "NVDA")

    def test_actor_in_wrong_sentence_is_rejected(self):
        obj = example()
        obj["actor_evidence_id"] = "E01-S02"
        self.assertIn("ACTOR_TEXT_NOT_IN_EVIDENCE", validate(obj, PAYLOAD)["errors"])

    def test_invented_actor_is_rejected(self):
        obj = example()
        obj["actor_text"] = "虚构机构"
        self.assertIn("ACTOR_TEXT_NOT_IN_EVIDENCE", validate(obj, PAYLOAD)["errors"])

    def test_missing_tone_evidence_is_rejected(self):
        obj = example()
        obj["tone_evidence_id"] = None
        self.assertIn("TONE_BAD_EVIDENCE_ID", validate(obj, PAYLOAD)["errors"])

    def test_unknown_field_cannot_carry_evidence(self):
        obj = example()
        obj["uncertainty_evidence_id"] = "E01-S01"
        self.assertIn("UNCERTAINTY_UNEXPECTED_EVIDENCE", validate(obj, PAYLOAD)["errors"])

    def test_unhashable_bad_reference_returns_error(self):
        obj = example()
        obj["claim_evidence_id"] = []
        self.assertIn("CLAIM_KIND_BAD_EVIDENCE_ID", validate(obj, PAYLOAD)["errors"])

    def test_full_abstention_is_counted_separately(self):
        obj = {k: None for k in example()}
        obj.update(claim_kind="UNKNOWN", category="UNKNOWN", tone="UNKNOWN",
                   uncertainty="UNKNOWN")
        result = validate(obj, PAYLOAD)
        self.assertTrue(result["valid"])
        self.assertTrue(result["abstained"])

    def test_duplicate_json_key_is_not_repaired(self):
        raw = json.dumps(example(), ensure_ascii=False).replace(
            '"actor_text": "分析员甲"', '"actor_text": "分析员甲", "actor_text": "分析员甲"', 1)
        with self.assertRaises(ValueError):
            parse_validate(raw, PAYLOAD)

    def test_watchlist_mention_is_not_assigned_subject_role(self):
        mentions = sentence_mentions(PAYLOAD["sentences"])
        self.assertEqual(mentions[0]["entity_id"], "NVDA")
        self.assertNotIn("role", mentions[0])


if __name__ == "__main__":
    unittest.main()
