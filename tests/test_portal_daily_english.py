import os
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import patch

from scripts.quant_news_v1 import store
from scripts.quant_personal_feed.build_daily_history import build_snapshot
from scripts.quant_personal_feed.daily_history import read_daily_history


class DailyAndEnglishPortalTests(unittest.TestCase):
    def test_non_english_items_do_not_consume_english_list_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "news.sqlite"
            titles = [
                "Nvidia shares rise after the company raises its annual sales forecast",
                "Les actions de Nvidia progressent après la publication des résultats",
                "아마존, 월요일 주가 3.5% 상승한 이유는?",
                "Chip stocks rally as demand for artificial intelligence accelerates",
            ]
            with closing(store.connect(path)) as db, db:
                for i, title in enumerate(titles):
                    stamp = f"2026-11-23T0{i}:00:00Z"
                    store.apply(db, {"upstream": "benzinga", "upstream_id": str(i),
                                    "title": title, "body": "한국어 본문" if i == 3 else None,
                                    "provider_created_at": stamp},
                                provider="benzinga_stream", via="stream", received_at=stamp)
            result = store.read_for_portal(1, path, english_only=True)
            self.assertEqual([i["upstream_id"] for i in result["items"]], ["0"])
            self.assertEqual(len(store.read_for_portal(4, path)["items"]), 4)

    def test_language_verdict_is_computed_once_per_version_and_reading_stops_when_the_list_is_full(self):
        from scripts.quant_news_v1 import language
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "news.sqlite"
            en = "Chip stocks rally as demand for artificial intelligence accelerates again today"
            fr = "Les actions de Nvidia progressent après la publication des résultats"
            with closing(store.connect(path)) as db, db:
                for i in range(8):                               # newest first: 7 fr, 6 en, 5 fr, 4 en, ...
                    stamp = f"2026-11-23T0{i}:00:00Z"
                    store.apply(db, {"upstream": "benzinga", "upstream_id": str(i), "title": en if i % 2 == 0 else fr,
                                    "provider_created_at": stamp, "provider_updated_at": stamp},
                                provider="benzinga_stream", via="stream", received_at=stamp)
            store._ENGLISH_VERDICT.clear()
            calls = []
            real = language.is_english_news
            with patch.object(language, "is_english_news", side_effect=lambda *a: calls.append(a[0]) or real(*a)):
                first = store.read_for_portal(2, path, english_only=True)
                self.assertEqual([i["upstream_id"] for i in first["items"]], ["6", "4"])
                self.assertEqual(len(calls), 4)                  # 7, 6, 5, 4: older items are not even read
                again = store.read_for_portal(2, path, english_only=True)
                self.assertEqual(again, first)
                self.assertEqual(len(calls), 4)                  # a poll costs no detection
                self.assertEqual([i["upstream_id"] for i in store.read_for_portal(10, path, english_only=True)["items"]], ["6", "4", "2", "0"])
                self.assertEqual(len(calls), 8)
                with closing(store.connect(path)) as db, db:      # item 7 is republished in English: a new version, a new verdict
                    store.apply(db, {"upstream": "benzinga", "upstream_id": "7", "title": en + " update",
                                    "provider_created_at": "2026-11-23T07:00:00Z", "provider_updated_at": "2026-11-23T09:00:00Z"},
                                provider="benzinga_stream", via="stream", received_at="2026-11-23T09:00:00Z")
                self.assertEqual([i["upstream_id"] for i in store.read_for_portal(2, path, english_only=True)["items"]], ["7", "6"])
                self.assertEqual(len(calls), 9)
            self.assertTrue(first["providers"] is not None and first["state"] == "READY")

    def test_snapshot_reads_by_symbol_and_calendar_year_without_filling_missing_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            csv = root / "export.csv"
            csv.write_text(
                "symbol,local_date,frequency,autype,open,high,low,close,research_eligible\n"
                "US.QQQ,2025-11-21,DAY,0,10,12,9,11,1\n"
                "US.QQQ,2025-11-24,DAY,0,11,13,10,12,1\n"
                "US.QQQ,2026-11-23,DAY,0,20,22,19,21,1\n"
                "US.NVDA,2026-11-23,DAY,0,30,33,29,32,1\n",
                encoding="utf-8",
            )
            dst = root / "versioned.sqlite"
            build_snapshot(dst, [csv])
            with patch.dict(os.environ, {"MOOMOO_DISPLAY_HISTORY_DB": str(dst)}):
                self.assertEqual(read_daily_history(root, "QQQ", as_of=date(2026, 11, 24))["bars"],
                                 [{"date": "2025-11-24", "close": 12.0},
                                  {"date": "2026-11-23", "close": 21.0}])
                self.assertEqual(read_daily_history(root, "NVDA", as_of=date(2026, 11, 24))["bars"][0]["close"], 32)
                self.assertEqual(read_daily_history(root, "MU", as_of=date(2026, 11, 24))["state"],
                                 "NO_DAILY_BARS")
            with self.assertRaises(FileExistsError):
                build_snapshot(dst, [csv])
