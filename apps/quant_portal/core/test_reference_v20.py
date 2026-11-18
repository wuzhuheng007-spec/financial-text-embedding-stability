"""v20 display references: explicit currency metadata and same-message change bases."""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from .personal_feed_cache import REPO_ROOT

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from scripts.quant_personal_feed.reference_v20 import instrument_metadata, quote_changes  # noqa: E402


def make_db(path: Path, rows):
    con = sqlite3.connect(path)
    con.executescript("""CREATE TABLE session_quotes (id INTEGER PRIMARY KEY, owner_id INTEGER, symbol TEXT,
        session TEXT, price_text TEXT, source_at TEXT, raw_sha256 TEXT);
        CREATE TABLE session_raw_responses (sha256 TEXT PRIMARY KEY, received_at TEXT, source_channel TEXT, raw_json TEXT);""")
    for i, (sym, sess, price, src, raw) in enumerate(rows):
        sha = f"h{i}"
        con.execute("INSERT INTO session_raw_responses VALUES(?,?,?,?)", (sha, "2026-11-18T23:00:00Z", "WS_QUOTE", json.dumps(raw)))
        con.execute("INSERT INTO session_quotes(owner_id,symbol,session,price_text,source_at,raw_sha256) VALUES(1,?,?,?,?,?)",
                    (sym, sess, price, src, sha))
    con.commit(); con.close()


class InstrumentMetadataTests(SimpleTestCase):
    def test_only_listed_instruments_get_a_currency(self):
        meta = instrument_metadata()
        self.assertEqual({k: v["currency"] for k, v in meta.items()},
                         {s: "USD" for s in ("TQQQ", "QQQ", "SOXX", "SPY", "NVDA", "MU")})
        self.assertNotIn("AMD", meta)  # a US. prefix alone never implies USD

    def test_invalid_or_missing_file_gives_no_currency(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "m.json"
            bad.write_text(json.dumps({"schema": "other", "instruments": {"US.X": {"symbol": "X", "currency": "USD"}}}))
            self.assertEqual(instrument_metadata(bad), {})
            self.assertEqual(instrument_metadata(Path(d) / "missing.json"), {})


class QuoteChangeTests(SimpleTestCase):
    def test_extended_hours_change_uses_regular_price_from_same_message(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "c.sqlite3"
            make_db(db, [("NVDA", "AFTERHOURS", "152.3565", "2026-11-18T23:39:37.588Z",
                          {"last_price": 150.38, "prev_close_price": 148.0, "after_market": {"price": 152.3565}})])
            out = quote_changes(db, 1, {"NVDA": {"session": "AFTERHOURS", "price_text": "152.3565",
                                                 "source_at": "2026-11-18T23:39:37.588Z"}})
        c = out["NVDA"]
        self.assertEqual((c["base_text"], c["base_field"], c["change_text"], c["change_pct_text"]),
                         ("150.38", "data.last_price", "1.98", "1.31"))

    def test_regular_change_needs_matching_last_price(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "c.sqlite3"
            make_db(db, [("MU", "AFTERHOURS", "704.38", "2026-11-18T21:35:40Z", {"last_price": 705.27, "prev_close_price": 700.0})])
            match = quote_changes(db, 1, {"MU": {"session": "UNKNOWN", "price_text": "705.27", "source_at": "x"}})
            other = quote_changes(db, 1, {"MU": {"session": "UNKNOWN", "price_text": "706.00", "source_at": "x"}})
        self.assertEqual((match["MU"]["base_field"], match["MU"]["change_text"]), ("data.prev_close_price", "5.27"))
        self.assertEqual(other, {})  # no attributable base -> no number

    def test_missing_base_or_row_gives_no_change(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "c.sqlite3"
            make_db(db, [("SPY", "AFTERHOURS", "729.98", "t1", {"after_market": {"price": 729.98}})])
            self.assertEqual(quote_changes(db, 1, {"SPY": {"session": "AFTERHOURS", "price_text": "729.98", "source_at": "t1"}}), {})
            self.assertEqual(quote_changes(db, 1, {"SPY": {"session": "AFTERHOURS", "price_text": "1.00", "source_at": "t9"}}), {})
