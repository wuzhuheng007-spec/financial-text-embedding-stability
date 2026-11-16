"""Owner-only cache endpoint; synthetic data and isolated Django test DB."""
import tempfile
import sys
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .personal_feed_cache import REPO_ROOT

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from scripts.quant_personal_feed.store import PersonalFeedStore


class PersonalFeedViewTests(TestCase):
    def test_unlogged_and_other_account_cannot_read_owner_feed(self):
        with tempfile.TemporaryDirectory() as folder:
            with override_settings(PERSONAL_MODE=True,
                                   PERSONAL_FEED_DB_PATH=Path(folder) / "sidecar.sqlite3"):
                url = reverse("personal_feed_json")
                self.assertEqual(self.client.get(url).status_code,302)
                user = get_user_model().objects.create_user("other@example.test",password="test-password-long")
                self.client.force_login(user)
                self.assertEqual(self.client.get(url).status_code,404)

    def test_owner_endpoint_reads_cache_without_supplier_call(self):
        with tempfile.TemporaryDirectory() as folder:
            with override_settings(PERSONAL_MODE=True,
                                   PERSONAL_FEED_DB_PATH=Path(folder) / "sidecar.sqlite3"):
                owner = get_user_model().objects.create_user("owner@local.invalid",password="test-password-long")
                self.client.force_login(owner)
                response = self.client.get(reverse("personal_feed_json"))
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.json()["cache_state"],"NOT_STARTED")
                self.assertEqual(response["Cache-Control"],"private, no-store")

    def test_owner_reads_synthetic_sidecar_rows_and_other_user_cannot(self):
        with tempfile.TemporaryDirectory() as folder:
            cache_path = Path(folder) / "sidecar.sqlite3"
            with override_settings(PERSONAL_MODE=True, PERSONAL_FEED_DB_PATH=cache_path):
                owner = get_user_model().objects.create_user("owner@local.invalid", password="test-password-long")
                store = PersonalFeedStore(cache_path, owner.pk)
                try:
                    store.put_quote(owner.pk, {"instrument_id": "US.TQQQ",
                        "provider_time": "2026-11-13T22:00:00Z", "received_at": "2026-11-16T01:00:00Z",
                        "delay_status": "UNKNOWN", "payload": {"price": 10,
                        "provider_data": {"last_price": 10}}}, symbol="TQQQ",
                        display_name="Synthetic ETF", market_state="US_11", limit=5)
                    store.put_news(owner.pk, {"content": "<script>synthetic</script>",
                        "time": "2026-11-16T09:00:00+08:00", "url": "https://example.test/synthetic"},
                        received_at="2026-11-16T01:00:00Z", max_items=5, max_versions=2)
                finally:
                    store.close()
                self.client.force_login(owner)
                response = self.client.get(reverse("personal_feed_json"))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["quotes"][0]["symbol"], "TQQQ")
                self.assertEqual(response.json()["news_total"], 1)
                self.assertEqual(response["Cache-Control"], "private, no-store")
                other = get_user_model().objects.create_user("other@example.test",password="test-password-long")
                self.client.force_login(other)
                self.assertEqual(self.client.get(reverse("personal_feed_json")).status_code, 404)
