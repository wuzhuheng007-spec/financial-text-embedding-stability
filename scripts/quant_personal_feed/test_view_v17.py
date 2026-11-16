"""The owner overlay must not release rejected interpretations or stale fields."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from .view_v17 import overlay


class StagedOverlayTests(unittest.TestCase):
    def test_partial_fields_do_not_release_rejected_interpretation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "attempts").mkdir()
            task = {"task_id": "T1", "item_key": "key", "news_version": 2,
                    "source_hash": "news-hash", "source_text_hash": "text-hash",
                    "source_text": "Company announced a plan.",
                    "candidate_count": 1, "selected_candidate_index": 1}
            (root / "selection_private.json").write_text(
                json.dumps({"tasks": [task]}), encoding="utf-8")
            (root / "semantic_review_private.json").write_text(json.dumps({
                "T1": {"A": {"decision": "ACCEPT_PARTIAL", "source_text_hash": "text-hash",
                             "raw_sha256": "raw-a", "accepted_field_paths": ["entity"]},
                       "B": {"decision": "REJECT", "raw_sha256": "raw-b"}}
            }), encoding="utf-8")
            (root / "attempts/T1_A.json").write_text(json.dumps({
                "raw_sha256": "raw-a", "parsed": {"entity": {
                    "value": "Company", "quote": "Company"}}
            }), encoding="utf-8")
            (root / "attempts/T1_B.json").write_text(json.dumps({
                "raw_sha256": "raw-b", "validation": {"mechanical_valid": True},
                "parsed": {"claim": "unsupported"}
            }), encoding="utf-8")
            data = {"news": [
                {"item_key": "key", "version": 2, "content_sha256": "news-hash",
                 "analysis_status": "REJECTED"},
                {"item_key": "key", "version": 1, "content_sha256": "old-hash",
                 "analysis_status": "CODEX_REVIEWED",
                 "analysis_display_kind": "CODEX_REVIEWED"},
            ], "top10": [{"item_key": "key", "version": 2}]}
            actual = overlay(data, private_dir=root)
            self.assertEqual(actual["news"][0]["analysis_status"], "STAGED_PARTIAL")
            self.assertEqual(len(actual["news"][0]["staged_extraction"]), 1)
            self.assertNotIn("staged_interpretation", actual["news"][0])
            self.assertEqual(actual["news"][1]["analysis_status"], "CODEX_REVIEWED")
            self.assertEqual(actual["top10"][0]["analysis_status"], "STAGED_PARTIAL")

    def test_multievent_screening_phrase_is_not_assigned_to_other_subevent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "attempts").mkdir()
            task = {"task_id": "T1", "item_key": "key", "news_version": 1,
                    "source_hash": "news-hash", "source_text_hash": "text-hash",
                    "source_text": "1. Revenue increased.",
                    "candidate_count": 2, "selected_candidate_index": 1}
            (root / "selection_private.json").write_text(json.dumps({"tasks": [task]}), encoding="utf-8")
            (root / "semantic_review_private.json").write_text(json.dumps({
                "T1": {"A": {"decision": "REJECT", "source_text_hash": "text-hash",
                             "raw_sha256": "raw-a", "accepted_field_paths": []}}
            }), encoding="utf-8")
            (root / "attempts/T1_A.json").write_text(json.dumps({"raw_sha256": "raw-a", "parsed": {}}), encoding="utf-8")
            data = {"news": [{"item_key": "key", "version": 1, "content_sha256": "news-hash",
                              "screening_v15": {"evidence_phrase": "oil price"}}], "top10": []}
            actual = overlay(data, private_dir=root)
            self.assertEqual(actual["news"][0]["staged_relevance_scope"], "FULL_REPORT_ONLY")
            self.assertEqual(actual["news"][0]["screening_v15"]["evidence_phrase"], "oil price")


if __name__ == "__main__":
    unittest.main()
