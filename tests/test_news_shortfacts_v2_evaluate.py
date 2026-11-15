"""Small synthetic checks for v2 format and reference accounting risks."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.news_shortfacts_v2.evaluate import (
    FIELDS, digest, evaluate, parse_response, schema_errors,
)


def fact(**changes):
    obj = {"entity": "Example Corp", "metric": "revenue", "value_text": "$8 million",
           "unit_text": "million", "period_text": "Q1 2026", "basis_text": None,
           "evidence_id": "EV-T1", "quote": "Example Corp revenue was $8 million in Q1 2026"}
    obj.update(changes)
    return obj


def decision(task_id="T1", fact_index=0, reference_index=0, **changes):
    row = {"task_id": task_id, "fact_index": fact_index,
           "reference_index": reference_index, "semantic_support": "SUPPORTED",
           "field_checks": {field: "CORRECT" for field in FIELDS},
           "error_labels": [], "reason": "Synthetic direct sentence supports the value."}
    row.update(changes)
    return row


class FormatContractTests(unittest.TestCase):
    def test_nullable_entity_and_required_metric(self):
        self.assertEqual([], schema_errors({"facts": [fact(entity=None)]}))
        self.assertTrue(schema_errors({"facts": [fact(metric=None)]}))
        self.assertTrue(schema_errors({"facts": [fact(period_text=7)]}))

    def test_exact_single_fence_only(self):
        raw = b'```json\n{"facts": []}\n```'
        parsed = parse_response(raw)
        self.assertEqual("FENCE_ADAPTED", parsed["kind"])
        self.assertEqual(hashlib.sha256(b'{"facts": []}').hexdigest(), parsed["view_sha256"])
        self.assertNotEqual(parsed["raw_sha256"], parsed["view_sha256"])
        for invalid in (b'prefix\n' + raw, raw + b'\ntrailing prose',
                        b'```json\n```json\n{"facts": []}\n```\n```',
                        b'{"facts":[],"facts":[]}', b'{"facts":NaN}',
                        b'{"facts":[]}\n{"facts":[]}'):
            with self.subTest(invalid=invalid):
                self.assertEqual("INVALID", parse_response(invalid)["kind"])
        self.assertEqual("RAW_JSON", parse_response(b'{"facts":[]}')["kind"])


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.inputs = self.base / "inputs"
        self.inputs.mkdir()
        self.run = self.base / "run"
        (self.run / "raw_responses").mkdir(parents=True)
        self.manifest_path = self.base / "manifest.json"
        self.reference_path = self.base / "reference.json"
        self.ledger_path = self.run / "attempt_ledger.jsonl"
        self.decisions_path = self.base / "decisions.json"
        self.reference_fact = fact()
        manifest_tasks = []
        reference_tasks = []
        for index in range(1, 7):
            task_id = f"T{index}"
            ev = f"EV-{task_id}"
            text = f"Example Corp revenue was $8 million in Q1 2026"
            passage = self.inputs / f"{task_id}.txt"
            passage.write_text(text, encoding="utf-8")
            source = self.inputs / f"{task_id}.source.txt"
            source.write_text(text, encoding="utf-8")
            manifest_tasks.append({"task_id": task_id, "evidence_id": ev,
                "text_path": str(passage), "text_sha256": digest(passage.read_bytes()),
                "source_path": str(source), "source_sha256": digest(source.read_bytes()),
                "source_locator": "sentence 1"})
            reference_tasks.append({"task_id": task_id,
                "facts": [fact(evidence_id=ev)]})
        self.manifest_path.write_text(json.dumps({"tasks": manifest_tasks}), encoding="utf-8")
        self.reference_path.write_text(json.dumps({"status": "CODEX_REFERENCE_DRAFT_V3",
            "input_manifest_sha256": digest(self.manifest_path.read_bytes()),
            "tasks": reference_tasks}), encoding="utf-8")

    def run_score(self, facts, decisions, fence=False):
        raw = json.dumps({"facts": facts}).encode("utf-8")
        if fence:
            raw = b"```json\n" + raw + b"\n```"
        path = self.run / "raw_responses" / "T1.txt"
        path.write_bytes(raw)
        self.ledger_path.write_text(json.dumps({"task_id": "T1", "status": "RESPONSE_COMPLETED",
            "generation_started": True, "response_completed": True,
            "raw_response_path": "raw_responses/T1.txt", "raw_response_sha256": digest(raw)}) + "\n")
        self.decisions_path.write_text(json.dumps({"reviewer": "CODEX_REVIEW",
            "reference_sha256": digest(self.reference_path.read_bytes()),
            "decisions": decisions}), encoding="utf-8")
        return evaluate(self.manifest_path, self.reference_path, self.ledger_path,
                        self.base / "score", self.decisions_path)

    def test_empty_does_not_count_as_success(self):
        _, _, summary = self.run_score([], [])
        self.assertEqual(1, summary["counts"]["empty_fact_response_tasks"])
        self.assertEqual(0, summary["counts"]["fully_correct_facts"])
        self.assertEqual(6, summary["counts"]["unmatched_reference_facts"])
        self.assertEqual(6, summary["complete_correct_recall"]["denominator"])

    def test_duplicate_cannot_increase_reference_recall(self):
        _, _, summary = self.run_score([fact(), fact()],
            [decision(), decision(fact_index=1)])
        self.assertEqual(1, summary["counts"]["matched_reference_facts"])
        self.assertEqual(1, summary["counts"]["fully_correct_facts"])
        self.assertEqual(1, summary["counts"]["duplicate_output_facts"])
        self.assertEqual(5, summary["counts"]["unmatched_reference_facts"])

    def test_fence_adaptation_keeps_raw_format_error(self):
        _, _, summary = self.run_score([fact()], [decision()], fence=True)
        self.assertEqual(0, summary["counts"]["strict_raw_json_valid_tasks"])
        self.assertEqual(1, summary["counts"]["fence_adapted_schema_valid_tasks"])
        self.assertEqual(1, summary["error_labels"]["FORMAT_ERROR"])

    def test_wrong_metric_and_period_are_separate_field_errors(self):
        changed = fact(metric="profit", period_text="2025")
        checks = {field: "CORRECT" for field in FIELDS}
        checks["metric"] = "WRONG"
        checks["period_text"] = "WRONG"
        _, _, summary = self.run_score([changed], [decision(field_checks=checks,
            semantic_support="PARTIAL", error_labels=["WRONG_METRIC", "WRONG_PERIOD"])])
        self.assertEqual(1, summary["counts"]["matched_with_field_error_facts"])
        self.assertEqual(0, summary["counts"]["fully_correct_facts"])
        self.assertEqual(1, summary["fields"]["metric"]["WRONG"])
        self.assertEqual(1, summary["fields"]["period_text"]["WRONG"])

    def test_null_entity_can_be_correct_and_unknown_is_counted(self):
        obj = json.loads(self.reference_path.read_text(encoding="utf-8"))
        obj["tasks"][0]["facts"][0]["entity"] = None
        self.reference_path.write_text(json.dumps(obj), encoding="utf-8")
        checks = {field: "CORRECT" for field in FIELDS}
        checks["basis_text"] = "UNKNOWN"
        _, _, summary = self.run_score([fact(entity=None)], [decision(field_checks=checks)])
        self.assertEqual(1, summary["counts"]["unresolved_output_facts"])
        self.assertEqual(1, summary["fields"]["basis_text"]["UNKNOWN"])
        self.assertEqual(1, summary["fields"]["basis_text"]["denominator"])

    def test_wrong_reference_match_cannot_claim_full_correct(self):
        obj = json.loads(self.reference_path.read_text(encoding="utf-8"))
        obj["tasks"][0]["facts"].append(fact(value_text="$9 million",
            quote="Example Corp revenue was $8 million in Q1 2026"))
        self.reference_path.write_text(json.dumps(obj), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "All-correct review contradicts"):
            self.run_score([fact()], [decision(reference_index=1)])


if __name__ == "__main__":
    unittest.main()
