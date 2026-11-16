"""Synthetic checks for four independent quote sessions and source-time limits."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.quant_personal_feed.session_poll import ingest_response
from scripts.quant_personal_feed.session_quotes import parse_push_children, parse_stock_quote_row
from scripts.quant_personal_feed.store import PersonalFeedStore, read_personal_cache


def source_row(*, parent_ms=1794862800000, regular=10, pre=11, after=12, overnight=13):
    return {"code":"US.NVDA", "data_time":parent_ms, "last_price":regular,
            "pre_market":{"price":pre}, "after_market":{"price":after},
            "overnight":{"price":overnight}}


class SessionQuoteTests(unittest.TestCase):
    def test_child_time_is_not_parent_or_receipt_time(self):
        rows, states = parse_stock_quote_row(source_row(),
            received_at="2026-11-16T03:00:00Z",raw_sha256="a" * 64)
        self.assertEqual({x["session"] for x in rows},
                         {"PREMARKET","REGULAR","AFTERHOURS","OVERNIGHT"})
        self.assertEqual(states["REGULAR"],"HAS_SOURCE_TIME")
        for row in rows:
            if row["session"] != "REGULAR":
                self.assertIsNone(row["source_at"])
                self.assertIsNone(row["trade_date"])
                self.assertEqual(row["date_basis"],"CHILD_TIME_UNKNOWN")
                self.assertEqual(states[row["session"]],"SNAPSHOT_TIME_UNKNOWN")

    def test_zero_child_and_explicit_child_time_across_ny_dst(self):
        row = source_row(pre=0,overnight=13)
        row["overnight"]["data_time_ms"] = 1793511000000
        rows, states = parse_stock_quote_row(row,
            received_at="2026-11-16T03:00:00Z",raw_sha256="b" * 64)
        self.assertEqual(states["PREMARKET"],"NO_OBSERVED_VALUE")
        overnight = next(x for x in rows if x["session"] == "OVERNIGHT")
        self.assertIsNotNone(overnight["source_at"])
        self.assertIsNone(overnight["trade_date"])
        self.assertEqual(overnight["date_basis"],"SOURCE_TIME_NEW_YORK_DATE_ONLY")
        self.assertEqual(overnight["source_time_field_path"],
                         "data.quote_list[].overnight.data_time_ms")

    def test_push_child_own_timestamp_and_no_parent_regular_claim(self):
        quote={"instrument_id":"US.TQQQ","provider_time":"2026-11-13T22:00:00Z",
               "received_at":"2026-11-16T03:00:00Z",
               "payload":{"provider_data":{"last_price":100,
                   "pre_market":{"price":101,"data_time_ms":1793511000000},
                   "after_market":{"price":102,"data_time_ms":1794862800000},
                   "overnight":{"price":103,"data_time_ms":1794798000000}}}}
        rows=parse_push_children(quote,raw_sha256="c"*64)
        self.assertEqual({x["session"] for x in rows},
                         {"PREMARKET","AFTERHOURS","OVERNIGHT"})
        self.assertTrue(all(x["source_at"] is not None for x in rows))
        self.assertTrue(all(x["trade_date"] is None for x in rows))
        self.assertTrue(all(x["source_time_field_path"].endswith("data_time_ms") for x in rows))
        self.assertTrue(all(x["parent_source_at_hint"] == quote["provider_time"] for x in rows))

    def test_session_separation_dedup_late_packet_and_revision(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"cache.sqlite3"
            store = PersonalFeedStore(path,7)
            try:
                def ingest(row, at):
                    payload={"ret_code":0,"data":{"quote_list":[row]}}
                    raw=json.dumps(payload,sort_keys=True)
                    return ingest_response(store,7,payload,raw,
                        hashlib.sha256(raw.encode()).hexdigest(),{"NVDA":"US.NVDA"},at)
                first=ingest(source_row(),"2026-11-16T03:00:00Z")
                self.assertEqual(first["inserted"],4)
                duplicate=ingest(source_row(),"2026-11-16T03:01:00Z")
                self.assertEqual(duplicate["duplicate"],4)
                later=source_row(parent_ms=1794862860000,regular=10,overnight=14)
                self.assertEqual(ingest(later,"2026-11-16T03:02:00Z")["inserted"],2)
                old=source_row(parent_ms=1794862740000,regular=9)
                ingest(old,"2026-11-16T03:03:00Z")
                visible={x["session"]:x for x in read_personal_cache(path,7)["session_quotes"]}
                self.assertEqual(visible["REGULAR"]["price_text"],"10")
                self.assertEqual(visible["OVERNIGHT"]["price_text"],"14")
                self.assertEqual(visible["PREMARKET"]["price_text"],"11")
                self.assertIsNone(visible["OVERNIGHT"]["source_at"])
                self.assertEqual(read_personal_cache(path,8)["cache_state"],"OWNER_MISMATCH")
            finally:
                store.close()

    def test_bad_raw_hash_does_not_commit(self):
        with tempfile.TemporaryDirectory() as folder:
            store=PersonalFeedStore(Path(folder)/"cache.sqlite3",7)
            try:
                with self.assertRaisesRegex(ValueError,"hash mismatch"):
                    store.put_session_quotes(7,[],states={},checked_at="2026-11-16T03:00:00Z",
                        raw_json="{}",raw_sha256="0"*64,source_channel="REST_STOCK_QUOTE")
                self.assertEqual(store.db.execute("SELECT COUNT(*) FROM session_raw_responses").fetchone()[0],0)
            finally:
                store.close()

    def test_quiet_session_keeps_raw_evidence_and_repairs_legacy_gap(self):
        with tempfile.TemporaryDirectory() as folder:
            store=PersonalFeedStore(Path(folder)/"cache.sqlite3",7)
            try:
                def ingest(i):
                    payload={"ret_code":0,"data":{"quote_list":[
                        source_row(parent_ms=1794862800000+i*60000,regular=10+i)]}}
                    raw=json.dumps(payload,sort_keys=True)
                    return ingest_response(store,7,payload,raw,
                        hashlib.sha256(raw.encode()).hexdigest(),{"NVDA":"US.NVDA"},
                        f"2026-11-16T03:{i//60:02d}:{i%60:02d}Z")
                ingest(0)
                for i in range(1,105):
                    ingest(i)
                visible=store.db.execute("""SELECT raw_sha256 FROM session_quotes
                    WHERE session='PREMARKET' ORDER BY id DESC LIMIT 1""").fetchone()[0]
                self.assertTrue(store.db.execute("SELECT 1 FROM session_raw_responses WHERE sha256=?",
                                                 (visible,)).fetchone())
                self.assertLessEqual(store.db.execute("SELECT COUNT(*) FROM session_raw_responses").fetchone()[0],104)
                store.db.execute("DELETE FROM session_raw_responses WHERE sha256=?",(visible,))
                self.assertGreaterEqual(ingest(105)["inserted"],4)
                latest=store.db.execute("""SELECT raw_sha256 FROM session_quotes
                    WHERE session='PREMARKET' ORDER BY id DESC LIMIT 1""").fetchone()[0]
                self.assertNotEqual(latest,visible)
                self.assertTrue(store.db.execute("SELECT 1 FROM session_raw_responses WHERE sha256=?",
                                                 (latest,)).fetchone())
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
