import unittest

from scripts.quant_personal_feed.analysis_contract_v18 import (
    backfill_evidence, entity_candidates, event_spans, sentence_spans,
    validate_a, validate_translation,
)


class V18ContractTests(unittest.TestCase):
    def test_numbered_bulletin_retains_every_event_and_offsets(self):
        original = "1. 英伟达宣布投资。\n2. 公司否认传闻。\n3. 美光公布指引。"
        events = event_spans(original)
        self.assertEqual(len(events), 3)
        for event in events:
            self.assertEqual(original[event["start"]:event["end"]], event["text"])
            for sentence in sentence_spans(event):
                self.assertEqual(original[sentence["start"]:sentence["end"]], sentence["text"])

    def test_entity_mention_does_not_become_actor_automatically(self):
        candidates = entity_candidates("某媒体提及英伟达和美光。")
        self.assertEqual({x["id"] for x in candidates}, {"NVDA", "MU"})

    def test_bad_attribution_and_evidence_rejected(self):
        event = event_spans("公司否认传闻。 ")[0]
        payload = {"sentences": sentence_spans(event), "candidate_entities": []}
        obj = {"subject": None, "subject_evidence_id": None, "entity_roles": [],
               "category": "UNKNOWN", "category_evidence_id": None,
               "attribution": "路透", "attribution_evidence_id": "E01-S01",
               "claim_kind": "DENIAL", "claim_evidence_id": "E01-S01",
               "tone": "UNKNOWN", "tone_evidence_id": None,
               "uncertainty": "UNKNOWN", "uncertainty_evidence_id": None}
        self.assertIn("ATTRIBUTION_NOT_IN_SENTENCE", validate_a(obj, payload)["errors"])
        obj["attribution"] = None
        obj["attribution_evidence_id"] = None
        obj["claim_evidence_id"] = "E99-S01"
        self.assertIn("CLAIM_BAD_EVIDENCE_ID", validate_a(obj, payload)["errors"])

    def test_correct_abstention_is_counted_as_abstention(self):
        payload = {"sentences": sentence_spans(event_spans("暂无更多信息。 ")[0]), "candidate_entities": []}
        obj = {"subject": None, "subject_evidence_id": None, "entity_roles": [],
               "category": "UNKNOWN", "category_evidence_id": None,
               "attribution": None, "attribution_evidence_id": None,
               "claim_kind": "UNKNOWN", "claim_evidence_id": None,
               "tone": "UNKNOWN", "tone_evidence_id": None,
               "uncertainty": "UNKNOWN", "uncertainty_evidence_id": None}
        result = validate_a(obj, payload)
        self.assertTrue(result["valid"])
        self.assertTrue(result["abstained"])
        self.assertEqual(backfill_evidence(obj, payload)["evidence"], {})

    def test_translation_checks_numbers_and_negation(self):
        self.assertFalse(validate_translation({"translation_en": "Revenue 7%."}, "公司否认营收增长7%。")["valid"])
        self.assertTrue(validate_translation({"translation_en": "The company denied revenue growth of 7%."},
                                             "公司否认营收增长7%。")["valid"])


if __name__ == "__main__":
    unittest.main()
