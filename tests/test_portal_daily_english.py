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
