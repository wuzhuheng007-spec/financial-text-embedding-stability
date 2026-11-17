"""Small synthetic invariants for the separate Chinese research line."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from scripts.news_sentiment_factor_v41.freeze import sha
from scripts.news_sentiment_factor_v41.prepare_v2 import CFG, clean_with_map
from scripts.news_sentiment_factor_v41.score import init
from scripts.news_sentiment_factor_v41.worker import article_from_row, existing_status, utc


class ChineseTonePipelineTests(unittest.TestCase):
    def test_html_source_map_and_visible_text(self):
        raw = '<p>英伟达利润增长</p><script>虚构内容</script><p>但仍有风险&amp;不确定性。</p>'
        visible, mapping, method = clean_with_map(raw)
        self.assertEqual(method, "HTMLPARSER_TEXT_NODE_MAP")
        self.assertIn("英伟达利润增长", visible)
        self.assertIn("但仍有风险&不确定性。", visible)
        self.assertNotIn("虚构内容", visible)
        self.assertEqual(len(visible), len(mapping))
        self.assertEqual(raw[mapping[visible.index("英")][0]:mapping[visible.index("英")][1]], "英")

    def test_distinct_versions_and_idempotent_attempts(self):
        row = {"item_key": "synthetic-1", "version": 1, "title": "合成标题", "content": "合成正文",
               "source_url": "https://example.org/synthetic", "published_at_utc": "2026-11-17T00:00:00Z",
               "first_received_at": "2026-11-17T00:01:00Z", "received_at": "2026-11-17T00:01:00Z",
               "raw_item_json": "{}"}
        article = article_from_row(row)
        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / "synthetic.sqlite3"
            init(db_path)
            with closing(sqlite3.connect(db_path)) as con:
                self.assertIsNone(existing_status(con, article))
                con.execute("""INSERT INTO attempted_versions VALUES
                  ('jin10','synthetic-1',1,'zh_html_source_map_punctuation_numbered_v2',
                  'e91b1a3af10e1e8c9c03429d3cd7d5e9a1c8000d',?, ?,
                  '2026-11-17T00:01:00Z','2026-11-17T00:02:00Z','FAILED','SYNTHETIC')""",
                  (sha(CFG), article["source_text_hash"]))
                self.assertEqual(existing_status(con, article), "ATTEMPTED_FAILED")
                changed = dict(article, source_text_hash="0" * 64)
                with self.assertRaisesRegex(ValueError, "SOURCE_VERSION_IDENTITY_COLLISION"):
                    existing_status(con, changed)
        self.assertGreater(utc("2026-11-17T00:01:00Z"), utc("2026-11-17T00:00:00+00:00"))


if __name__ == "__main__":
    unittest.main()
