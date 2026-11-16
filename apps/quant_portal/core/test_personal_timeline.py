"""Owner isolation and bounded loopback JSON contract for synthetic timeline."""
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .personal_feed_cache import REPO_ROOT
import sys
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0,str(REPO_ROOT))
from scripts.quant_personal_feed.store import PersonalFeedStore


class PersonalTimelineViewTests(TestCase):
    def test_owner_only_read_and_invalid_query(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"sidecar.sqlite3"
            with override_settings(PERSONAL_MODE=True,PERSONAL_FEED_DB_PATH=path):
                owner=get_user_model().objects.create_user("owner@local.invalid",password="synthetic-test-password")
                other=get_user_model().objects.create_user("other@example.test",password="synthetic-test-password")
                cache=PersonalFeedStore(path,owner.pk)
                cache.db.execute("""INSERT INTO session_quotes(owner_id,symbol,provider_code,session,
                  price_text,currency,source_at,received_at,provider_field_path,source_time_field_path,
                  trade_date,date_basis,parent_date_hint,parent_source_at_hint,delay_status,source_channel,raw_sha256)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(owner.pk,"NVDA","US.NVDA","OVERNIGHT",
                  "100",None,"2026-11-16T03:00:00Z","2026-11-16T03:00:01Z","price",
                  "data_time_ms",None,"UNKNOWN",None,None,"UNKNOWN","WS_QUOTE","a"*64))
                cache.close()
                url=reverse("personal_timeline_json")
                self.assertEqual(self.client.get(url,{"symbol":"NVDA"}).status_code,302)
                self.client.force_login(other)
                self.assertEqual(self.client.get(url,{"symbol":"NVDA"}).status_code,404)
                self.client.force_login(owner)
                response=self.client.get(url,{"symbol":"NVDA","range":"all"})
                self.assertEqual(response.status_code,200)
                self.assertEqual(response["Cache-Control"],"private, no-store")
                self.assertEqual(response.json()["symbol"],"NVDA")
                self.assertEqual(response.json()["quote_kind"],"SAVED_SUPPLIER_QUOTE_NOT_TRADE_OR_BAR")
                self.assertEqual(self.client.get(url,{"symbol":"SPCX"}).status_code,400)
