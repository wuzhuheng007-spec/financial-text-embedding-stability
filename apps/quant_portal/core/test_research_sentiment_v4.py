"""Isolated owner-only research-cache checks; no provider or model calls."""
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse


class ResearchSentimentViewTests(TestCase):
    @override_settings(PERSONAL_MODE=True, PERSONAL_FEED_OWNER_USERNAME="owner@example.invalid")
    def test_owner_only_and_no_cache_state(self):
        with TemporaryDirectory() as folder, patch("core.personal_feed_cache.REPO_ROOT", Path(folder)):
            url = reverse("research_sentiment_json")
            self.assertEqual(self.client.get(url).status_code, 302)
            other = get_user_model().objects.create_user("other@example.test", password="synthetic-secret")
            self.client.force_login(other)
            self.assertEqual(self.client.get(url).status_code, 404)
            owner = get_user_model().objects.create_user("owner@example.invalid", password="synthetic-secret")
            self.client.force_login(owner)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["state"], "NO_RESEARCH_CACHE")
            self.assertEqual(response["Cache-Control"], "private, no-store")

    @override_settings(PERSONAL_MODE=True, PERSONAL_FEED_OWNER_USERNAME="owner@example.invalid")
    def test_owner_reads_only_local_synthetic_cache(self):
        with TemporaryDirectory() as folder, patch("core.personal_feed_cache.REPO_ROOT", Path(folder)):
            db = Path(folder) / "artifacts/news_sentiment_factor_v4_20261117/news_research.sqlite3"
            db.parent.mkdir(parents=True)
            with closing(sqlite3.connect(db)) as con:
                con.executescript("""
                CREATE TABLE articles(provider TEXT, article_id TEXT, version INTEGER, publisher TEXT,
                                      language TEXT, observation_mode TEXT);
                CREATE TABLE scores(provider TEXT, article_id TEXT, version INTEGER, input_kind TEXT, net_score REAL,
                                    scored_at_utc TEXT, score_origin TEXT, status TEXT);
                INSERT INTO articles VALUES('synthetic','x',1,'Synthetic publisher','ENGLISH_CANDIDATE','PROSPECTIVE');
                INSERT INTO scores VALUES('synthetic','x',1,'TITLE',0.25,'2026-11-17T00:00:00Z','PROSPECTIVE_RECEIPT','DONE');
                """)
                con.commit()
            owner = get_user_model().objects.create_user("owner@example.invalid", password="synthetic-secret")
            self.client.force_login(owner)
            response = self.client.get(reverse("research_sentiment_json"))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["completed_scores"], 1)
            self.assertEqual(response.json()["rows"][0]["score"], 0.25)
            self.assertNotIn("title", response.json()["rows"][0])
