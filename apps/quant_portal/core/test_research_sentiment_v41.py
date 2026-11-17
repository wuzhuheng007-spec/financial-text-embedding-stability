"""Synthetic owner-only cache contract checks; no private source data."""
import sqlite3
import json
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse


class ChineseResearchViewTests(TestCase):
    @override_settings(PERSONAL_MODE=True, PERSONAL_FEED_OWNER_USERNAME="owner@example.invalid")
    def test_dead_worker_is_not_reported_running(self):
        with TemporaryDirectory() as folder, patch("core.personal_feed_cache.REPO_ROOT", Path(folder)):
            public = Path(folder) / "results/news_sentiment_factor_v41_20261117/worker_status_redacted.json"
            private = Path(folder) / "artifacts/news_sentiment_factor_v41_20261117/worker_state_private.json"
            public.parent.mkdir(parents=True)
            private.parent.mkdir(parents=True)
            public.write_text(json.dumps({"run_id": "synthetic", "state": "RUNNING"}), encoding="utf-8")
            private.write_text(json.dumps({"run_id": "synthetic", "pid": 999999999}), encoding="utf-8")
            owner = get_user_model().objects.create_user("owner@example.invalid", password="synthetic-secret")
            self.client.force_login(owner)
            self.assertEqual(self.client.get(reverse("research_sentiment_json")).json()["chinese"]["worker"]["state"], "STALE_EXIT")

    @override_settings(PERSONAL_MODE=True, PERSONAL_FEED_OWNER_USERNAME="owner@example.invalid")
    def test_owner_only_and_separate_chinese_cache(self):
        with TemporaryDirectory() as folder, patch("core.personal_feed_cache.REPO_ROOT", Path(folder)):
            url = reverse("research_sentiment_json")
            self.assertEqual(self.client.get(url).status_code, 302)
            other = get_user_model().objects.create_user("other@example.test", password="synthetic-secret")
            self.client.force_login(other)
            self.assertEqual(self.client.get(url).status_code, 404)
            owner = get_user_model().objects.create_user("owner@example.invalid", password="synthetic-secret")
            self.client.force_login(owner)
            before = self.client.get(url).json()
            self.assertEqual(before["state"], "NO_RESEARCH_CACHE")
            self.assertEqual(before["chinese"]["state"], "NO_CHINESE_RESEARCH_CACHE")

            db = Path(folder) / "artifacts/news_sentiment_factor_v41_20261117/chinese_tone.sqlite3"
            db.parent.mkdir(parents=True)
            with closing(sqlite3.connect(db)) as con:
                con.executescript("""
                CREATE TABLE articles(provider TEXT,article_id TEXT,version INTEGER,publisher TEXT,
                    source_url TEXT,published_at_utc TEXT,received_at_utc TEXT,cohort TEXT);
                CREATE TABLE segments(provider TEXT,article_id TEXT,version INTEGER,segment_id TEXT,
                    segment_rule_version TEXT,kind TEXT,company_association TEXT,association_status TEXT,
                    entity_candidates_json TEXT);
                CREATE TABLE scores(provider TEXT,article_id TEXT,version INTEGER,segment_id TEXT,
                    segment_rule_version TEXT,net_score REAL,status TEXT,scored_at_utc TEXT,
                    score_origin TEXT);
                INSERT INTO articles VALUES('synthetic','one',1,NULL,'https://example.org/source',
                    '2026-11-17T00:00:00Z','2026-11-17T00:01:00Z','CACHE_REPLAY');
                INSERT INTO segments VALUES('synthetic','one',1,'B0001','rule','BODY_SEGMENT',
                    'NVDA','ONE_EXPLICIT_COMPANY','[{"id":"NVDA","phrase":"英伟达"}]');
                INSERT INTO scores VALUES('synthetic','one',1,'B0001','rule',0.3,'DONE',
                    '2026-11-17T00:02:00Z','CACHE_REPLAY_DEVELOPMENT');
                """)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Cache-Control"], "private, no-store")
            data = response.json()["chinese"]
            self.assertEqual(data["state"], "READY")
            self.assertEqual(data["scored_segments"], 1)
            self.assertEqual(data["rows"][0]["text_tone_score"], 0.3)
            self.assertEqual(data["rows"][0]["company_refs"][0]["matched_phrase"], "英伟达")
            self.assertNotIn("body", data["rows"][0])
