"""Focused private-cache risks; all rows here are synthetic."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

from scripts.quant_personal_feed.collector import _retry_after_seconds
from scripts.quant_personal_feed.store import PersonalFeedStore, read_personal_cache


class PersonalFeedCacheTests(unittest.TestCase):
    def test_retry_after_respects_numeric_and_http_date(self):
        self.assertEqual(_retry_after_seconds("120", 60), 120)
        future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=120), usegmt=True)
        self.assertGreater(_retry_after_seconds(future, 60), 110)

    def test_owner_boundary_duplicate_quote_and_revision(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "feed.sqlite3"
            cache = PersonalFeedStore(path, 7)
            quote = {"instrument_id": "US.TQQQ", "provider_time": "2026-11-13T22:00:00Z",
                     "received_at": "2026-11-16T01:00:00Z", "delay_status": "UNKNOWN",
                     "payload": {"price": 10.0, "provider_data": {"last_price": 10.0}}}
            try:
                with self.assertRaisesRegex(ValueError,"Owner ID mismatch"):
                    cache.put_quote(8, quote, symbol="TQQQ", display_name="TQQQ",
                                    market_state="US_11", limit=10)
                self.assertTrue(cache.put_quote(7, quote, symbol="TQQQ", display_name="TQQQ",
                                                market_state="US_11", limit=10))
                self.assertFalse(cache.put_quote(7, quote, symbol="TQQQ", display_name="TQQQ",
                                                 market_state="US_11", limit=10))
                same_display = {**quote, "payload": {"price": 10.0,
                                  "provider_data": {"last_price": 10.0, "changing_auxiliary_field": 2}}}
                self.assertFalse(cache.put_quote(7, same_display, symbol="TQQQ", display_name="TQQQ",
                                                 market_state="US_11", limit=10))
                changed = {**quote, "payload": {"price": 10.1,
                                                  "provider_data": {"last_price": 10.1}}}
                self.assertTrue(cache.put_quote(7, changed, symbol="TQQQ", display_name="TQQQ",
                                                market_state="US_11", limit=10))
            finally:
                cache.close()
            self.assertEqual(read_personal_cache(path, 7)["quotes"][0]["price_text"], "10.1")
            self.assertEqual(read_personal_cache(path, 8)["cache_state"], "OWNER_MISMATCH")
            self.assertEqual(read_personal_cache(path, 8)["quotes"], [])

    def test_news_version_dedup_and_untrusted_url(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "feed.sqlite3"
            cache = PersonalFeedStore(path, 7)
            item = {"title": "synthetic", "content": "<img src=x onerror=alert(1)>",
                    "time": "2026-11-16T09:00:00+08:00", "url": "javascript:alert(1)"}
            try:
                self.assertEqual(cache.put_news(7,item,received_at="2026-11-16T01:00:01Z",
                                                max_items=5,max_versions=2), "NEW")
                self.assertEqual(cache.put_news(7,item,received_at="2026-11-16T01:00:02Z",
                                                max_items=5,max_versions=2), "DUPLICATE")
                revised = {**item, "content": "<script>changed</script>"}
                self.assertEqual(cache.put_news(7,revised,received_at="2026-11-16T01:00:03Z",
                                                max_items=5,max_versions=2), "REVISION")
            finally:
                cache.close()
            result = read_personal_cache(path,7)
            self.assertEqual(result["news_total"],1)
            self.assertEqual(result["news"][0]["version"],2)
            self.assertIsNone(result["news"][0]["source_url"])
            self.assertEqual(result["news"][0]["content"],"<script>changed</script>")


if __name__ == "__main__":
    unittest.main()
