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
        os.environ.pop("QUANT_X_STANCE_DB", None); os.environ.pop("TWITTERAPI_IO_API_KEY", None); self.tmp.cleanup()

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
        self.assertTrue({"PROVIDER_KEY", "PREFLIGHT", "validation.holdout_start"} <= missing)
        self.assertFalse(any(m.startswith(("classifier.", "market.")) for m in missing))           # the model and the price bundle are pinned
        self.assertEqual((d["classifier"]["quality"], d["market"]["input_version"]), ("UNVALIDATED", "phase5_bundle/c5b5ec22"))
        self.assertEqual((d["budget"]["today"]["cap_posts"], d["budget"]["today"]["cap_usd"], d["budget"]["rolling"]["cap_usd"], d["budget"]["today"]["returned_posts"]), (5000, "0.75", "22.5", 0))
        self.assertEqual(d["settings"]["key_env_name"], "TWITTERAPI_IO_API_KEY")
        self.assertEqual((d["preflight"]["state"], d["history_queue"]["state"]), ("NOT_RUN", "NOT_STARTED"))
        self.assertEqual(d["collection"]["state"], "NOT_STARTED")
        self.assertFalse(d["settings"]["acquisition_gate"]["allowed"])
        self.assertEqual([t["id"] for t in d["settings"]["topics"]], ["memory", "technology", "market", "qqq"])
        self.assertFalse(d["settings"]["automatic_paid_fallback"])

    def test_a_configured_key_is_never_echoed(self):
        os.environ["TWITTERAPI_IO_API_KEY"] = "tw-FAKE-not-a-real-key-000"
        body = self.get().content.decode()
        self.assertNotIn("tw-FAKE", body)
        self.assertNotIn("PROVIDER_KEY_MISSING", body)
        self.assertEqual(json.loads(body)["settings"]["acquisition_gate"]["reasons"], ["PREFLIGHT_NOT_PASSED"])   # a key alone starts nothing

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

    # ---------- keyword editing ----------
    def post(self, changes, user=None, csrf=False):
        from django.test import Client
        c = Client(enforce_csrf_checks=csrf)
        c.force_login(user or self.owner)
        return c.post(reverse("x_stance_vocabulary"), data=json.dumps({"changes": changes}), content_type="application/json")

    def test_keyword_edit_is_owner_only_post_only_and_needs_csrf(self):
        self.assertEqual(self.post([{"action": "add", "topic_id": "qqq", "keyword": "QQQM"}], user=self.other).status_code, 404)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("x_stance_vocabulary")).status_code, 405)
        self.assertEqual(self.post([{"action": "add", "topic_id": "qqq", "keyword": "QQQM"}], csrf=True).status_code, 403)
        self.assertFalse(self.db.exists())                                         # none of the refused requests wrote anything

    def test_keyword_edit_creates_a_pending_version_that_the_page_then_shows(self):
        r = self.post([{"action": "add", "topic_id": "memory", "keyword": "HBM4"}, {"action": "disable", "topic_id": "market", "keyword": "关税"}])
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["result"], "PENDING_VERSION_CREATED")
        self.assertTrue(d["version"].startswith("vocab-"))
        self.assertFalse(d["impact"]["budget_changed"])
        view = self.get().json()
        self.assertEqual(view["vocabulary"]["pending"]["version"], d["version"])
        self.assertEqual(view["vocabulary"]["active"]["version"], "vocab-20261122-a")                # still the old one until the next batch
        self.assertEqual(view["settings"]["vocabulary_version"], "vocab-20261122-a")
        self.assertIn("HBM4", next(t for t in view["vocabulary"]["pending"]["topics"] if t["id"] == "memory")["keywords"])

    def test_keyword_edit_rejects_query_syntax_and_bad_requests(self):
        for changes, reason in (([{"action": "add", "topic_id": "qqq", "keyword": "QQQ) OR (bitcoin"}], "KEYWORD_HAS_QUERY_SYNTAX"),
                                ([{"action": "add", "topic_id": "qqq", "keyword": "$QQQ"}], "KEYWORD_EXISTS"), ([], "BAD_REQUEST"), ("x", "BAD_REQUEST")):
            r = self.post(changes)
            self.assertEqual(r.status_code, 400)
            self.assertEqual(r.json()["reason"], reason)
        self.client.force_login(self.owner)
        self.assertIsNone(self.client.get(reverse("x_stance_json")).json()["vocabulary"]["pending"])
