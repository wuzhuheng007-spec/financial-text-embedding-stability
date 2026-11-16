"""Owner and source-version boundaries for the reviewed v1.8 cache."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from scripts.quant_personal_feed.view_v18 import overlay


class ReviewedOverlayTests(unittest.TestCase):
    def test_only_owner_and_exact_source_version_receive_translation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "reviewed.sqlite3"
            with closing(sqlite3.connect(path)) as db:
                db.execute("CREATE TABLE owner_binding(owner_id INTEGER PRIMARY KEY)")
                db.execute("INSERT INTO owner_binding VALUES(7)")
                db.execute("""CREATE TABLE translation_cache(
                    owner_id INTEGER,item_key TEXT,news_version INTEGER,source_hash TEXT,
                    model_revision TEXT,prompt_version TEXT,translation_en TEXT,
                    review_status TEXT,completed_at_utc TEXT)""")
                db.execute("""CREATE TABLE prospective_tone(
                    owner_id INTEGER,item_key TEXT,news_version INTEGER,source_hash TEXT,
                    event_id TEXT,entity_id TEXT,tone TEXT,uncertainty TEXT,
                    evidence_ids_json TEXT,feature_tier TEXT,review_status TEXT,
                    analysis_completed_at_utc TEXT,signal_available_at_utc TEXT)""")
                db.execute("INSERT INTO translation_cache VALUES(?,?,?,?,?,?,?,?,?)",
                           (7, "n1", 2, "hash2", "aa8e72537993ba99e69dfaafa59ed015b17504d1",
                            "v18", "reviewed English", "CODEX_CORRECTED_MACHINE_TRANSLATION", "2026-11-17T00:00:00Z"))
                db.commit()
            rows = [{"item_key": "n1", "version": 1, "content_sha256": "hash2"},
                    {"item_key": "n1", "version": 2, "content_sha256": "hash1"},
                    {"item_key": "n1", "version": 2, "content_sha256": "hash2"}]
            denied = overlay({"news": [dict(row) for row in rows]}, owner_id=8, db_path=path)
            self.assertEqual(denied["v18_status"], "OWNER_MISMATCH")
            self.assertFalse(any("source_translation_en" in row for row in denied["news"]))
            allowed = overlay({"news": [dict(row) for row in rows]}, owner_id=7, db_path=path)
            self.assertEqual([row.get("source_translation_en") for row in allowed["news"]],
                             [None, None, "reviewed English"])
            self.assertEqual(allowed["news"][2]["source_translation_status"],
                             "MACHINE_TRANSLATION_CODEX_CORRECTED")


if __name__ == "__main__":
    unittest.main()
