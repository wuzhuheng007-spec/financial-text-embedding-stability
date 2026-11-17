"""Focused engineering checks; these are not semantic accuracy estimates."""
import unittest

from scripts.quant_personal_feed.analysis_contract_v182 import validate
from scripts.quant_personal_feed.analysis_v182_adapter import adapt


class Extraction182ContractTests(unittest.TestCase):
    def setUp(self):
        self.payload = {"sentences": [{"id": "E01-S01", "text":
            "According to Reuters, analyst Lee predicts Company A growth."}]}
        self.value = {"roles": [
            {"role": "REPORTER", "text": "Reuters", "evidence_id": "E01-S01"},
            {"role": "SPEAKER", "text": "analyst Lee", "evidence_id": "E01-S01"},
            {"role": "CLAIM_OBJECT", "text": "Company A", "evidence_id": "E01-S01"}],
            "claim_kinds": ["REPORT", "PREDICTION"], "claim_evidence_id": "E01-S01",
            "tone": None, "tone_evidence_id": None,
            "uncertainty": "PRESENT", "uncertainty_evidence_id": "E01-S01",
            "category": None, "category_evidence_id": None}

    def test_nested_report_and_prediction_allowed(self):
        self.assertTrue(validate(self.value, self.payload)["valid"])

    def test_reported_allegation_and_denial_remain_distinct(self):
        self.payload["sentences"] = [{"id": "E01-S01", "text":
            "Outlet X reported an accusation against Company A."},
            {"id": "E01-S02", "text": "Company A denied the accusation."}]
        self.value["roles"] = [
            {"role": "REPORTER", "text": "Outlet X", "evidence_id": "E01-S01"},
            {"role": "CLAIM_OBJECT", "text": "Company A", "evidence_id": "E01-S01"},
            {"role": "SPEAKER", "text": "Company A", "evidence_id": "E01-S02"}]
        self.value["claim_kinds"] = ["REPORT", "ALLEGATION", "DENIAL"]
        self.value["claim_evidence_id"] = "E01-S01"
        self.value["uncertainty_evidence_id"] = "E01-S01"
        self.assertTrue(validate(self.value, self.payload)["valid"])
        # This check only verifies the cited sentence exists. Semantic review
        # must still notice that the denial is in S02, not S01.

    def test_unsupported_role_rejected(self):
        self.value["roles"][0]["text"] = "Bloomberg"
        self.assertIn("ROLE_TEXT_NOT_IN_SOURCE", validate(self.value, self.payload)["errors"])

    def test_bad_evidence_rejected(self):
        self.value["claim_evidence_id"] = "E01-S99"
        self.assertIn("CLAIM_EVIDENCE_ID", validate(self.value, self.payload)["errors"])

    def test_unknown_claim_cannot_carry_evidence(self):
        self.value["claim_kinds"] = []
        self.assertIn("CLAIM_UNEXPECTED_EVIDENCE", validate(self.value, self.payload)["errors"])

    def test_empty_abstention_is_not_coverage(self):
        self.value.update(roles=[], claim_kinds=[], claim_evidence_id=None,
                          uncertainty=None, uncertainty_evidence_id=None)
        result = validate(self.value, self.payload)
        self.assertTrue(result["valid"])
        self.assertTrue(result["abstained"])

    def test_old_unknown_adapter_only_exact_sentinel(self):
        source = {"claim_kind": "UNKNOWN", "claim_evidence_id": "UNKNOWN",
                  "tone": "UNKNOWN", "tone_evidence_id": "E01-S01"}
        derived, changed = adapt(source)
        self.assertIsNone(derived["claim_evidence_id"])
        self.assertEqual(derived["tone_evidence_id"], "E01-S01")
        self.assertEqual(source["claim_evidence_id"], "UNKNOWN")
        self.assertEqual(changed, ["claim_evidence_id"])


if __name__ == "__main__":
    unittest.main()
