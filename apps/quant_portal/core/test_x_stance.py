"""x-stance.json: owner only, never cached, reads the study store without calling a provider or a model, and with
no store it reports what is missing instead of showing sample rows. The rows used below are made up."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

PASSWORD = "GraniteRiver!23Complex"


@override_settings(PERSONAL_MODE=True, PERSONAL_FEED_OWNER_USERNAME="owner@example.org")
class XStanceJsonTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.db = Path(self.tmp.name) / "x.sqlite3"
        os.environ["QUANT_X_STANCE_DB"] = str(self.db)
        users = get_user_model().objects
        self.owner = users.create_user(username="owner@example.org", email="owner@example.org", password=PASSWORD)
        self.other = users.create_user(username="other@example.org", email="other@example.org", password=PASSWORD)

    def tearDown(self):
        os.environ.pop("QUANT_X_STANCE_DB", None); os.environ.pop("X_STANCE_TWITTERAPI_IO_KEY", None); self.tmp.cleanup()

    def get(self):
        self.client.force_login(self.owner)
        r = self.client.get(reverse("x_stance_json")); self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Cache-Control"], "private, no-store")
        return r

    def test_owner_only(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("x_stance_json")).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("x_stance_json")).status_code, 302)

    def test_no_store_means_empty_panels_and_a_list_of_missing_inputs(self):
        d = self.get().json()
        self.assertFalse(d["has_data"])
        for k in ("daily", "narratives", "episodes", "returns", "conditional"):
            self.assertEqual(d[k], [])
        self.assertFalse(self.db.exists())                                         # reading does not create a store
        missing = {m["input"] for m in d["missing_inputs"]}
        self.assertTrue({"PROVIDER_KEY", "acquisition.daily_budget_usd", "acquisition.total_budget_usd", "classifier.immutable_revision",
                         "market.input_version", "validation.holdout_start"} <= missing)
        self.assertEqual(d["collection"]["state"], "NOT_STARTED")
        self.assertFalse(d["settings"]["acquisition_gate"]["allowed"])
        self.assertEqual([t["id"] for t in d["settings"]["topics"]], ["memory", "technology", "market", "qqq"])
        self.assertFalse(d["settings"]["automatic_paid_fallback"])

    def test_a_configured_key_is_never_echoed(self):
        os.environ["X_STANCE_TWITTERAPI_IO_KEY"] = "tw-FAKE-not-a-real-key-000"
        body = self.get().content.decode()
        self.assertNotIn("tw-FAKE", body)
        self.assertNotIn("PROVIDER_KEY_MISSING", body)
        self.assertFalse(json.loads(body)["settings"]["acquisition_gate"]["allowed"])   # a key alone opens nothing

    def test_stored_rows_are_served_with_their_mode_and_without_post_id_lists(self):
        from scripts.x_stance_v1 import store
        db = store.connect(self.db)
        payload = {"shares": {"bullish": 0.5, "bearish": 0.25, "neutral": 0.25}, "accounts_total": 40, "posts_total": 55, "unjudged_total": 3, "state": "bullish",
                   "state_reason": None, "lang_scope": "all", "mode": "RETROSPECTIVE_VISIBLE_NOW", "vote_post_ids": ["1", "2"], "availability": "RETROSPECTIVE_VISIBLE_NOW_HYPOTHETICAL"}
        db.execute("INSERT INTO x_daily_sentiment VALUES(?,?,?,?,?,?,?,?,?,?,?)", ("session", "2025-06-02", "a", "b", "qqq", "QQQ", "all", "RETROSPECTIVE_VISIBLE_NOW",
                                                                               json.dumps(payload), "bullish", "2026-11-22T00:00:00Z"))
        db.commit(); db.close()
        d = self.get().json()
        self.assertTrue(d["has_data"])
        row = d["daily"][0]
        self.assertEqual((row["window_label"], row["mode"], row["state"]), ("2025-06-02", "RETROSPECTIVE_VISIBLE_NOW", "bullish"))
        self.assertNotIn("vote_post_ids", row)

    def test_dashboard_carries_the_panel_and_its_script(self):
        self.client.force_login(self.owner)
        html = self.client.get(reverse("dashboard")).content.decode()
        self.assertIn('id="x-stance"', html); self.assertIn("core/x_stance_v1.js", html)
        for tab in ("settings", "daily", "views", "history"):
            self.assertIn(f'data-xtab="{tab}"', html)
