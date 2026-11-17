"""Synthetic contract checks; they do not establish supplier coverage or model quality."""
import json
import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path

from scripts.news_sentiment_factor_v4.store import ingest_moomoo_probe, english_candidate, frozen_window_bounds_utc


class ArticleVersionTests(unittest.TestCase):
    def test_new_york_window_converts_dst_boundary(self):
        bounds = frozen_window_bounds_utc()
        self.assertEqual(bounds[0][0], "2025-05-01T04:00:00+00:00")
        self.assertEqual(bounds[2][0], "2025-11-01T04:00:00+00:00")
        self.assertEqual(bounds[2][1], "2025-11-08T05:00:00+00:00")

    def test_language_gate_does_not_translate_chinese(self):
        self.assertFalse(english_candidate("英伟达最新消息"))
        self.assertTrue(english_candidate("Company raises guidance"))

    def test_duplicate_and_revision_are_append_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "isolated.sqlite3"
            raw = root / "synthetic.json"
            item = {"news_id": "SYN-1", "title": "Company raises guidance",
                    "abstract": "", "content": "", "publish_time": 1762200000000,
                    "source": "Synthetic publisher", "url": "https://example.invalid/syn"}
            raw.write_text(json.dumps({"ret_code": 0, "data": {"items": [item]}}), encoding="utf-8")
            self.assertEqual(ingest_moomoo_probe(raw, mode="BASELINE", db=db)["new_versions"], 1)
            self.assertEqual(ingest_moomoo_probe(raw, mode="BASELINE", db=db)["duplicate_versions"], 1)
            item["title"] = "Company lowers guidance"
            raw.write_text(json.dumps({"ret_code": 0, "data": {"items": [item]}}), encoding="utf-8")
            self.assertEqual(ingest_moomoo_probe(raw, mode="BASELINE", db=db)["new_versions"], 1)
            with closing(sqlite3.connect(db)) as con:
                rows = con.execute("SELECT version,title FROM articles ORDER BY version").fetchall()
            self.assertEqual(rows, [(1, "Company raises guidance"), (2, "Company lowers guidance")])

    def test_old_published_time_cannot_be_prospective(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "isolated.sqlite3"
            raw = root / "synthetic.json"
            raw.write_text(json.dumps({"ret_code": 0, "data": {"items": [
                {"news_id": "SYN-2", "title": "Company raises guidance", "publish_time": 1762200000000}
            ]}}), encoding="utf-8")
            ingest_moomoo_probe(raw, mode="PROSPECTIVE", observation_start_utc="2026-11-17T00:00:00+00:00", db=db)
            with closing(sqlite3.connect(db)) as con:
                mode = con.execute("SELECT observation_mode FROM articles").fetchone()[0]
            self.assertEqual(mode, "LATE_FIRST_RECEIPT")


if __name__ == "__main__":
    unittest.main()
