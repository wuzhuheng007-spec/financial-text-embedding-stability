"""Isolated synthetic boundary checks; never write synthetic rows to private data."""
from __future__ import annotations

import json
import sqlite3
import shutil
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.news_sentiment_factor_v44.labels import CONTRACT, digest
from . import daily_prices, labels, worker
from .metadata import VERSION as METADATA_VERSION, record_daily_currency_evidence, resolve_currency

UTC = timezone.utc
NY = ZoneInfo("America/New_York")


class PriceLabelChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "prices.sqlite3"
        self.queue = Path(self.tmp.name) / "queue.sqlite3"
        daily_prices.init(self.db)

    def _queue(self, *, n1=0.2, signal="2026-11-17T21:16:00+00:00"):
        with closing(sqlite3.connect(self.queue)) as con, con:
            con.executescript("""
              CREATE TABLE daily_label_queue(snapshot_id TEXT,entity_id TEXT,horizon_sessions INTEGER,
                status TEXT,reason TEXT,security_id TEXT,entry_session_ny TEXT,entry_at_utc TEXT,
                exit_session_ny TEXT,exit_at_utc TEXT,maturity_at_utc TEXT,price_requirements_json TEXT);
              CREATE TABLE daily_snapshot_company(snapshot_id TEXT,entity_id TEXT,n1 REAL);
              CREATE TABLE daily_snapshots(snapshot_id TEXT,kind TEXT);
              CREATE TABLE daily_snapshot_releases(snapshot_id TEXT,signal_available_at_utc TEXT);
              CREATE TABLE daily_snapshot_publication_witness(snapshot_id TEXT,release_commit_observed_at_utc TEXT);
            """)
            con.execute("INSERT INTO daily_label_queue VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("synthetic-snapshot", "NVDA", 1, "WAIT_ENTRY", "", "SEC:NVDA:US",
                 "2026-11-18", "2026-11-18T14:30:00+00:00", "2026-11-18",
                 "2026-11-18T21:00:00+00:00", "2026-11-18T21:00:00+00:00", "[]"))
            con.execute("INSERT INTO daily_snapshot_company VALUES(?,?,?)", ("synthetic-snapshot", "NVDA", n1))
            con.execute("INSERT INTO daily_snapshots VALUES(?,?)", ("synthetic-snapshot", "ON_TIME_SCHEDULED"))
            con.execute("INSERT INTO daily_snapshot_releases VALUES(?,?)", ("synthetic-snapshot", signal))
            con.execute("INSERT INTO daily_snapshot_publication_witness VALUES(?,?)",
                        ("synthetic-snapshot", "2026-11-17T21:15:30+00:00"))

    def _evidence(self, *, close="101", checked="2026-11-19T01:00:00+00:00",
                  action="PROVIDER_LISTS_NO_ACTION_IN_INTERVAL_V45"):
        with closing(sqlite3.connect(self.db)) as con, con:
            mid = digest(["metadata", "NVDA", "USD"])
            con.execute("INSERT OR IGNORE INTO v45_security_metadata VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (mid, "SEC:NVDA:US", "US.NVDA", "NASDAQ_GLOBAL_SELECT_MARKET", "USD",
                 "2026-11-18", "2026-11-30", "2026-11-17T10:00:00+00:00",
                 "{}", "{}", "synthetic", "SYNTHETIC_TEST_ONLY", METADATA_VERSION))
            for field, val in (("OPEN", "100"), ("CLOSE", close)):
                eid = digest([field, val, checked])
                con.execute("INSERT OR IGNORE INTO price_evidence VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (eid, "SEC:NVDA:US", "US.NVDA", "2026-11-18", field, val,
                     "UNKNOWN", "WEBAPI_NO_CURRENCY_FIELD", "REGULAR",
                     "UNADJUSTED_DAILY_BAR_NOT_OFFICIAL_AUCTION", "UNADJUSTED_AUTYPE_0",
                     "MOOMOO_WEBAPI", checked, "synthetic", "test"))
                con.execute("INSERT OR IGNORE INTO v45_price_currency_links VALUES(?,?,?,?,?,?)",
                    (digest([eid, mid]), eid, mid, "USD", checked, "test"))
                con.execute("INSERT OR IGNORE INTO v45_price_observations VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (eid, "synthetic-request", "2026-11-18", 1795010400000, -300,
                     checked, checked, checked, "synthetic", "{}", "NOT_PROVIDED"))
            aid = digest(["action", checked, action])
            con.execute("INSERT OR IGNORE INTO action_evidence VALUES(?,?,?,?,?,?,?,?,?,?)",
                (aid, "SEC:NVDA:US", "2026-11-18", "2026-11-18",
                 0 if action.startswith("PROVIDER_LISTS_NO_ACTION") else 1, 0,
                 action, "synthetic", "synthetic", checked))
            con.execute("INSERT OR IGNORE INTO v45_action_coverage VALUES(?,?,?,?,?,?,?,?,?)",
                (aid, "synthetic-rehab", "synthetic-dividend", "2026-11-18", "2026-11-18",
                 0, 0, "COMPLETE_PROVIDER_RESPONSE_BELOW_100_DIVIDEND_CAP", "SYNTHETIC"))

    def test_half_day_and_unfinished_day(self):
        close = datetime(2026, 11, 27, 13, 0, tzinfo=NY).astimezone(UTC)
        self.assertFalse(daily_prices._completed_session("2026-11-27", close + timedelta(minutes=44)))
        self.assertTrue(daily_prices._completed_session("2026-11-27", close + timedelta(minutes=45)))
        self.assertFalse(daily_prices._completed_session("2026-11-28", close + timedelta(days=1)))

    def test_next_possible_daily_window_is_conditional(self):
        due = worker._next_conditional_collection_window(datetime(2026, 11, 17, 9, tzinfo=UTC))
        self.assertEqual(due, "2026-11-18T21:45:00+00:00")

    def test_no_real_queue_means_zero_supplier_calls(self):
        with closing(sqlite3.connect(self.db)) as con, con:
            before = con.execute("SELECT COUNT(*) FROM v45_supplier_requests").fetchone()[0]
        receipt = daily_prices.tick(self.db, self.queue)
        with closing(sqlite3.connect(self.db)) as con, con:
            after = con.execute("SELECT COUNT(*) FROM v45_supplier_requests").fetchone()[0]
        self.assertEqual(before, after)
        self.assertEqual(receipt["qualified_queue_rows"], 0)

    def test_missing_n1_not_queued(self):
        self._queue(n1=None)
        valid, excluded = daily_prices.queue_requirements(self.queue,
            datetime(2026, 11, 19, tzinfo=UTC))
        self.assertEqual(valid, [])
        self.assertEqual(excluded[0]["reason"], "N1_MISSING")

    def test_old_source_time_and_metadata_scope(self):
        self._evidence()
        with closing(sqlite3.connect(self.db)) as con, con:
            eid = con.execute("SELECT evidence_id FROM price_evidence WHERE field='OPEN'").fetchone()[0]
            currency, _, state = resolve_currency(con, eid, datetime(2026, 11, 19, tzinfo=UTC))
            self.assertEqual(currency, "USD")
            self.assertIn("EXTERNAL", state)
            con.execute("INSERT INTO v45_security_metadata VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("other", "SEC:NVDA:US", "US.NVDA", "NASDAQ_GLOBAL_SELECT_MARKET", "EUR",
                 "2026-11-18", "2026-11-30", "2026-11-17T10:00:00+00:00",
                 "{}", "{}", "synthetic", "SYNTHETIC_CONFLICT", "test"))
            con.execute("INSERT INTO v45_price_currency_links VALUES(?,?,?,?,?,?)",
                ("conflict", eid, "other", "EUR", "2026-11-17T10:00:00+00:00", "test"))
            self.assertEqual(resolve_currency(con, eid, datetime(2026, 11, 19, tzinfo=UTC))[2],
                             "CURRENCY_CONFLICT")

    def test_future_day_requires_own_archived_bar_evidence(self):
        self._evidence()
        with closing(sqlite3.connect(self.db)) as con, con:
            eid = con.execute("SELECT evidence_id FROM price_evidence WHERE field='OPEN'").fetchone()[0]
            mid = con.execute("SELECT metadata_id FROM v45_security_metadata WHERE rule_version=?",
                              (METADATA_VERSION,)).fetchone()[0]
            con.execute("INSERT INTO v45_metadata_scope_corrections VALUES(?,?,?,?,?)",
                        ("synthetic-cap", mid, "2026-11-17", "2026-11-17T11:00:00+00:00", "synthetic"))
            self.assertEqual(resolve_currency(con, eid, datetime(2026, 11, 19, tzinfo=UTC))[2],
                             "NO_VALID_METADATA_LINK")
            record_daily_currency_evidence(con, "NVDA", "2026-11-18", "synthetic-bar-hash",
                                           "https://example.invalid/history-kline", "2026-11-18T22:00:00+00:00")
            self.assertEqual(resolve_currency(con, eid, datetime(2026, 11, 19, tzinfo=UTC))[0], "USD")

    def test_label_idempotent_then_invalidate_on_new_action(self):
        self._queue()
        self._evidence()
        at = datetime(2026, 11, 19, 2, tzinfo=UTC)
        first = labels.advance(self.db, self.queue, at)
        self.assertEqual(first["new_labels"], 1)
        self.assertEqual(first["states"], {"CURRENT_QUALIFIED": 1})
        second = labels.advance(self.db, self.queue, at)
        self.assertEqual(second["new_labels"], 0)
        self.assertEqual(second["new_decisions"], 0)
        self._evidence(checked="2026-11-20T01:00:00+00:00", action="REVIEW_ACTIONS")
        third = labels.advance(self.db, self.queue, datetime(2026, 11, 20, 2, tzinfo=UTC))
        self.assertEqual(third["states"], {"BLOCKED_ACTION_REVIEW": 1})
        with closing(sqlite3.connect(self.db)) as con, con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM label_versions WHERE contract=?",
                                         (CONTRACT,)).fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT state FROM v45_label_decisions ORDER BY decision_at_utc DESC LIMIT 1").fetchone()[0],
                             "INVALIDATED")

    def test_security_denial_not_retried(self):
        with closing(sqlite3.connect(self.db)) as con, con:
            con.execute("INSERT INTO v45_supplier_requests VALUES(?,?,?,?,?,?,?,?)",
                ("denied", "2026-11-17T00:00:00+00:00", "2026-11-17", "NVDA",
                 "2026-11-18", "history", "https://example.invalid", "test"))
            con.execute("INSERT INTO v45_supplier_results VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("denied", "2026-11-17T00:00:01+00:00", 439, -12009, None,
                 "HTTPError", None, 0, None, None))
            outcome = daily_prices._request(con, "NVDA", "2026-11-18", "history",
                "https://example.invalid", datetime(2026, 11, 17, 1, tzinfo=UTC))
            self.assertEqual(outcome["state"], "ROUTE_PAUSED")
            self.assertEqual(con.execute("SELECT COUNT(*) FROM v45_supplier_requests").fetchone()[0], 1)

    def test_business_rate_and_quota_denial_not_retried(self):
        for code in (-11, -12):
            with self.subTest(ret_code=code):
                with closing(sqlite3.connect(self.db)) as con, con:
                    rid = f"limited-{code}"
                    con.execute("INSERT INTO v45_supplier_requests VALUES(?,?,?,?,?,?,?,?)",
                        (rid, "2026-11-17T00:00:00+00:00", "2026-11-17", "NVDA",
                         "2026-11-18", "history", "https://example.invalid", "test"))
                    con.execute("INSERT INTO v45_supplier_results VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (rid, "2026-11-17T00:00:01+00:00", 200, code, "quota", None,
                         None, 0, None, None))
                    before = con.execute("SELECT COUNT(*) FROM v45_supplier_requests").fetchone()[0]
                    outcome = daily_prices._request(con, "NVDA", "2026-11-18", "history",
                        "https://example.invalid", datetime(2026, 11, 17, 1, tzinfo=UTC))
                    self.assertEqual(outcome["state"], "ROUTE_PAUSED")
                    self.assertEqual(con.execute("SELECT COUNT(*) FROM v45_supplier_requests").fetchone()[0], before)

    def test_portal_does_not_count_invalidated_label_as_current(self):
        from apps.quant_portal.core.research_chinese_cache import v43_research_summary
        root = Path(self.tmp.name)
        expected = root / "artifacts/news_sentiment_factor_v44_20261117/price_labels.sqlite3"
        expected.parent.mkdir(parents=True)
        labels.init(self.db)
        with closing(sqlite3.connect(self.db)) as con, con:
            for index, state in enumerate(("CURRENT_QUALIFIED", "INVALIDATED")):
                con.execute("INSERT INTO v45_label_decisions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (f"test-{index}", "synthetic", "NVDA", 1,
                     f"2026-11-19T00:00:0{index}+00:00", state, "test", None,
                     None, CONTRACT, "synthetic"))
        shutil.copy2(self.db, expected)
        self.assertEqual(v43_research_summary(root)["daily_bar_label_count"], 0)


if __name__ == "__main__":
    unittest.main()
