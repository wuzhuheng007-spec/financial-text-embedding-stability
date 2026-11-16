"""Owner-only cache endpoint; synthetic data and isolated Django test DB."""
import tempfile
import sys
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .personal_feed_cache import REPO_ROOT
from .models import WatchlistItem

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
                self.assertEqual(self.client.post(reverse("personal_quote_selection"),
                                  {"symbol":"TQQQ","action":"unsubscribe"}).status_code,404)
                self.assertEqual(self.client.post(reverse("personal_news_interest"),
                                  {"interest_key":"SPACEX","enabled":"1"}).status_code,404)
                self.assertEqual(self.client.post(reverse("personal_event_preferences"),
                                  {"entity":"NVDA"}).status_code,404)

    def test_owner_can_edit_event_preset_without_changing_existing_interest(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"sidecar.sqlite3"
            with override_settings(PERSONAL_MODE=True,PERSONAL_FEED_DB_PATH=path):
                owner=get_user_model().objects.create_user("owner@local.invalid",password="test-password-long")
                store=PersonalFeedStore(path,owner.pk)
                for key in ("SNDK","SKHYNIX","SPACEX"):
                    store.set_news_interest(key,True)
                store.close()
                self.client.force_login(owner)
                reply=self.client.post(reverse("personal_event_preferences"),
                                       {"entity":["NVDA","SPACEX"],"category":["EARNINGS_GUIDANCE"]})
                self.assertEqual(reply.status_code,200)
                data=self.client.get(reverse("personal_feed_json")).json()
                self.assertTrue(data["event_preferences_effective"]["explicit"])
                self.assertEqual(data["event_preferences_effective"]["entities"],["NVDA","SPACEX"])
                self.assertEqual(len(data["news_interests"]),3)
                self.assertEqual(self.client.post(reverse("personal_event_preferences"),
                                     {"entity":"INVALID"}).status_code,400)

    def test_news_rule_failure_does_not_hide_quote_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"sidecar.sqlite3"
            with override_settings(PERSONAL_MODE=True,PERSONAL_FEED_DB_PATH=path):
                owner=get_user_model().objects.create_user("owner@local.invalid",password="test-password-long")
                store=PersonalFeedStore(path,owner.pk)
                store.put_quote(owner.pk,{"instrument_id":"US.TQQQ",
                    "provider_time":"2026-11-13T22:00:00Z","received_at":"2026-11-16T01:00:00Z",
                    "payload":{"price":10}},symbol="TQQQ",display_name="Synthetic",market_state="US_11",limit=5)
                news={"id":"synthetic","content":"NVIDIA announces earnings."}
                received="2026-11-16T01:00:00Z"
                store.put_news(owner.pk,news,received_at=received,max_items=5,max_versions=2)
                from scripts.quant_personal_feed.relevance_v15 import screen
                store.record_news_screening(owner.pk,news,screen(news).as_dict(),received_at=received,
                                            insertion_result="NEW")
                store.close()
                self.client.force_login(owner)
                with patch("scripts.quant_personal_feed.event_rules.classify",side_effect=ValueError("synthetic")):
                    data=self.client.get(reverse("personal_feed_json")).json()
                self.assertEqual(data["quotes"][0]["symbol"],"TQQQ")
                self.assertEqual(data["news"][0]["event_rule_error"],"ValueError")

    def test_owner_news_interest_is_separate_from_quote_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            cache_path=Path(folder) / "sidecar.sqlite3"
            with override_settings(PERSONAL_MODE=True,PERSONAL_FEED_DB_PATH=cache_path):
                owner=get_user_model().objects.create_user("owner@local.invalid",password="test-password-long")
                self.client.force_login(owner)
                cache=PersonalFeedStore(cache_path,owner.pk)
                cache.ensure_selection(["TQQQ"])
                cache.close()
                response=self.client.post(reverse("personal_news_interest"),
                    {"interest_key":"SPACEX","enabled":"0"})
                self.assertEqual(response.status_code,200)
                data=self.client.get(reverse("personal_feed_json")).json()
                self.assertFalse(next(x for x in data["news_interests"] if x["interest_key"]=="SPACEX")["enabled"])
                self.assertEqual([x["symbol"] for x in data["selection"] if x["desired"]],["TQQQ"])

    def test_watchlist_add_does_not_subscribe_and_replacement_is_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            cache_path=Path(folder) / "sidecar.sqlite3"
            with override_settings(PERSONAL_MODE=True,PERSONAL_FEED_DB_PATH=cache_path):
                owner=get_user_model().objects.create_user("owner@local.invalid",password="test-password-long")
                self.client.force_login(owner)
                cache=PersonalFeedStore(cache_path,owner.pk)
                cache.ensure_selection(["TQQQ","QQQ","SOXX","SPY","NVDA","MU"])
                cache.close()
                WatchlistItem.objects.create(user=owner,symbol="AMD",asset_type="EQUITY")
                denied=self.client.post(reverse("personal_quote_selection"),
                    {"symbol":"AMD","action":"subscribe"})
                self.assertEqual(denied.json()["result"],"BUDGET_FULL")
                cache=PersonalFeedStore(cache_path,owner.pk)
                self.assertNotIn("AMD",cache.selection()[1])
                cache.close()
                accepted=self.client.post(reverse("personal_quote_selection"),
                    {"symbol":"AMD","action":"subscribe","replace_symbol":"MU"})
                self.assertEqual(accepted.json()["result"],"PENDING_SUBSCRIBE")
                cache=PersonalFeedStore(cache_path,owner.pk)
                self.assertEqual(len(cache.selection()[1]),6)
                self.assertIn("AMD",cache.selection()[1])
                self.assertNotIn("MU",cache.selection()[1])
                cache.close()

    def test_selected_symbol_must_stop_before_watchlist_removal(self):
        with tempfile.TemporaryDirectory() as folder:
            cache_path=Path(folder) / "sidecar.sqlite3"
            with override_settings(PERSONAL_MODE=True,PERSONAL_FEED_DB_PATH=cache_path):
                owner=get_user_model().objects.create_user("owner@local.invalid",password="test-password-long")
                item=WatchlistItem.objects.create(user=owner,symbol="TQQQ",asset_type="ETF")
                cache=PersonalFeedStore(cache_path,owner.pk)
                cache.ensure_selection(["TQQQ"])
                cache.close()
                self.client.force_login(owner)
                self.client.post(reverse("watchlist_remove",args=[item.pk]))
                self.assertTrue(WatchlistItem.objects.filter(pk=item.pk).exists())
                cache=PersonalFeedStore(cache_path,owner.pk)
                cache.change_selection("TQQQ","unsubscribe",{"TQQQ"},6)
                cache.symbol_state(owner.pk,"TQQQ","STOPPED","USER_UNSUBSCRIBED")
                cache.close()
                self.client.post(reverse("watchlist_remove",args=[item.pk]))
                self.assertFalse(WatchlistItem.objects.filter(pk=item.pk).exists())
