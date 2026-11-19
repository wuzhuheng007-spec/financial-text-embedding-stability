"""news.json: owner only, reads the collector cache, honest connection labels, relevance without invented ETF tags."""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import WatchlistItem

PASSWORD = "GraniteRiver!23Complex"


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@override_settings(PERSONAL_MODE=True, PERSONAL_FEED_OWNER_USERNAME="owner@example.org")
class NewsJsonTests(TestCase):
    def setUp(self):
        from scripts.quant_news_v1 import store
        self.store = store
        self.tmp = TemporaryDirectory(); os.environ["QUANT_NEWS_DB"] = str(Path(self.tmp.name) / "n.sqlite3")
        users = get_user_model().objects
        self.owner = users.create_user(username="owner@example.org", email="owner@example.org", password=PASSWORD)
        self.other = users.create_user(username="other@example.org", email="other@example.org", password=PASSWORD)
        for s in ("NVDA", "QQQ", "SOXX"):
            WatchlistItem.objects.create(user=self.owner, symbol=s, asset_type="EQUITY")

    def tearDown(self):
        os.environ.pop("QUANT_NEWS_DB", None); self.tmp.cleanup()

    def get(self):
        self.client.force_login(self.owner)
        r = self.client.get(reverse("news_json")); self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Cache-Control"], "private, no-store")
        return r.json()

    def item(self, uid, symbols, channels=(), title="t"):
        return {"upstream": "benzinga", "upstream_id": uid, "message_id": None, "action": "created", "title": title,
                "teaser": None, "body": None, "url": "https://www.benzinga.com/x", "source": "Benzinga", "authors": ["A", "B"],
                "symbols": list(symbols), "channels": list(channels), "provider_created_at": "2026-11-19T13:00:00Z",
                "provider_updated_at": "2026-11-19T13:00:00Z", "raw": {}}

    def test_owner_only(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("news_json")).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("news_json")).status_code, 302)

    def test_unconfigured_is_not_connected_and_has_no_items(self):
        d = self.get()
        self.assertEqual(d["connection"]["kind"], "off"); self.assertEqual(d["items"], [])
        self.assertIn("benzinga_stream", d["sources"])
        self.assertEqual(d["sources"]["massive_benzinga"]["rights"]["model_training"], "NOT_GRANTED_WITHOUT_EXPLICIT_LICENCE")

    def test_connected_without_news_is_not_called_connected(self):
        db = self.store.connect()
        self.store.set_state(db, "benzinga_stream", "CONNECTED_NO_MESSAGES_YET", detail="WEBSOCKET_PUSH")
        db.close()
        c = self.get()["connection"]
        self.assertEqual((c["kind"], c["label"]), ("idle", "新闻源已连接，尚未观察到新消息"))

    def test_receiving_relevance_and_stale_heartbeat(self):
        db = self.store.connect()
        self.store.apply(db, self.item("1", ["NVDA", "AMD"]), provider="benzinga_stream", via="stream", received_at=_iso(datetime.now(timezone.utc)))
        self.store.apply(db, self.item("2", ["XLK"], channels=["Economics"]), provider="benzinga_stream", via="stream", received_at=_iso(datetime.now(timezone.utc)))
        self.store.apply(db, self.item("3", ["QQQ"]), provider="benzinga_stream", via="stream", received_at=_iso(datetime.now(timezone.utc)))
        self.store.set_state(db, "benzinga_stream", "RECEIVING", detail="WEBSOCKET_PUSH")
        d = self.get()
        self.assertEqual(d["connection"]["kind"], "ok")
        rel = {i["upstream_id"]: i["relevance"] for i in d["items"]}
        self.assertEqual(rel["1"]["direct_watch"], ["NVDA"]); self.assertEqual(rel["1"]["direct_watch_etf"], [])
        self.assertTrue(rel["2"]["macro"]); self.assertEqual(rel["2"]["direct_watch"], [])  # XLK is not inferred as SOXX/QQQ
        self.assertEqual(rel["3"]["direct_watch_etf"], ["QQQ"])
        old = _iso(datetime.now(timezone.utc) - timedelta(minutes=10))
        db.execute("UPDATE provider_state SET last_heartbeat_at=?", (old,)); db.commit(); db.close()
        self.assertEqual(self.get()["connection"]["kind"], "down")

    def test_dashboard_has_news_mounts(self):
        self.client.force_login(self.owner)
        html = self.client.get(reverse("dashboard")).content.decode()
        for needle in ('id="nv"', 'id="ov-news2"', 'id="news-src-card"', "news_v20.js", 'data-scope="watch"'):
            self.assertIn(needle, html)
        self.assertNotIn("Benzinga Newsdesk", html)  # no demo data in the page
