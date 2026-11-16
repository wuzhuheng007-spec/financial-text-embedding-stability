"""Targeted synthetic risks for the owner-only saved-quote timeline."""
import hashlib
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.quant_personal_feed.store import PersonalFeedStore, utc_now
from scripts.quant_personal_feed.timeline import _selected_points, read_timeline


NOW = datetime(2026, 11, 16, 4, 0, tzinfo=timezone.utc)


def insert_quote(store, *, session="OVERNIGHT", price="10", source="2026-11-16T03:00:00Z",
                 received="2026-11-16T03:00:01Z", symbol="NVDA", raw="a"*64):
    store.db.execute("""INSERT INTO session_quotes(owner_id,symbol,provider_code,session,price_text,
      currency,source_at,received_at,provider_field_path,source_time_field_path,trade_date,
      date_basis,parent_date_hint,parent_source_at_hint,delay_status,source_channel,raw_sha256)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (store.owner_id,symbol,"US."+symbol,session,price,None,source,received,"quote.price",
       "quote.data_time_ms",None,"SOURCE_TIME_NEW_YORK_DATE_ONLY",None,None,"UNKNOWN","WS_QUOTE",raw))


class TimelineTests(unittest.TestCase):
    def test_source_time_only_late_packet_revision_and_session_separation(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"cache.sqlite3"
            store=PersonalFeedStore(path,7)
            insert_quote(store,source="2026-11-16T03:10:00Z",received="2026-11-16T03:10:01Z",price="10")
            insert_quote(store,source="2026-11-16T03:10:00Z",received="2026-11-16T03:11:00Z",price="11")
            insert_quote(store,source="2026-11-16T03:05:00Z",received="2026-11-16T03:12:00Z",price="9")
            insert_quote(store,session="REGULAR",source="2026-11-16T03:10:00Z",price="20")
            insert_quote(store,source=None,price="99")
            store.close()
            result=read_timeline(path,7,symbol="NVDA",range_key="2h",now=NOW)
            self.assertEqual([(x["session"],x["price_text"]) for x in result["points"]],
                             [("OVERNIGHT","9"),("OVERNIGHT","11"),("REGULAR","20")])
            self.assertEqual(result["point_metrics"]["valid_unique_points"],3)
            self.assertEqual(read_timeline(path,8,symbol="NVDA",now=NOW)["cache_state"],"OWNER_MISMATCH")

    def test_timestamp_precision_and_mixed_iso_revision_order(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"cache.sqlite3"
            store=PersonalFeedStore(path,7)
            source="2026-11-16T03:10:00.123456Z"
            insert_quote(store,source=source,received="2026-11-16T03:10:01Z",price="10")
            insert_quote(store,source=source,received="2026-11-16T03:10:01.500000+00:00",price="11")
            store.put_news(7,{"id":"micro","title":"NVIDIA earnings",
                              "content":"NVIDIA earnings reported",
                              "time":"2026-11-16T03:10:00.654321Z"},
                           received_at="2026-11-16T03:10:02.987654Z",max_items=20,max_versions=5)
            saved_first=store.db.execute("SELECT first_received_at FROM news_items WHERE owner_id=7").fetchone()[0]
            store.close()
            result=read_timeline(path,7,symbol="NVDA",range_key="2h",now=NOW)
            self.assertEqual(result["points"][0]["source_at"],source)
            self.assertEqual(result["points"][0]["price_text"],"11")
            self.assertEqual(result["news"][0]["published_at_utc"],"2026-11-16T03:10:00.654321Z")
            self.assertEqual(result["news"][0]["first_received_at"],saved_first)

    def test_gap_and_disconnection_cut_without_interpolation(self):
        def row(i, session, at, received):
            return {"id":i,"session":session,"price_text":"12","currency":None,
                    "source_at":at,"received_at":received,"source_channel":"WS_QUOTE",
                    "delay_status":"UNKNOWN","raw_present":0}
        rows=[row(1,"REGULAR","2026-11-16T03:00:00Z","2026-11-16T03:00:00Z"),
              row(2,"REGULAR","2026-11-16T03:01:00Z","2026-11-16T03:01:00Z"),
              row(3,"REGULAR","2026-11-16T03:05:00Z","2026-11-16T03:05:00Z"),
              row(4,"OVERNIGHT","2026-11-16T03:06:00Z","2026-11-16T03:06:00Z"),
              row(5,"OVERNIGHT","2026-11-16T03:07:00Z","2026-11-16T03:07:00Z")]
        points, summary=_selected_points(rows,start=NOW-timedelta(hours=2),end=NOW,
            gap_seconds=180,max_points=500,
            interruption_times=[datetime(2026,11,16,3,6,30,tzinfo=timezone.utc)])
        self.assertEqual([x["segment"] for x in points],[0,0,1,2,3])
        self.assertEqual(summary["segment_count_before_sampling"],4)

    def test_new_york_midnight_does_not_infer_trading_date_or_break_short_segment(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"cache.sqlite3"
            store=PersonalFeedStore(path,7)
            before="2026-11-16T04:59:50Z"  # 23:59:50 New York, previous calendar day.
            after="2026-11-16T05:00:05Z"   # 00:00:05 New York.
            insert_quote(store,source=before,received="2026-11-16T04:59:51Z")
            insert_quote(store,source=after,received="2026-11-16T05:00:06Z")
            self.assertIsNone(store.db.execute("SELECT trade_date FROM session_quotes LIMIT 1").fetchone()[0])
            store.close()
            result=read_timeline(path,7,symbol="NVDA",range_key="30m",
                now=datetime(2026,11,16,5,10,tzinfo=timezone.utc))
            self.assertEqual([p["source_at"] for p in result["points"]],[before,after])
            self.assertEqual([p["segment"] for p in result["points"]],[0,0])
            new_york=ZoneInfo("America/New_York")
            self.assertNotEqual(datetime.fromisoformat(before.replace("Z","+00:00")).astimezone(new_york).date(),
                                datetime.fromisoformat(after.replace("Z","+00:00")).astimezone(new_york).date())

    def test_sampling_preserves_sparse_session_boundaries(self):
        rows=[]
        for i, session in enumerate(("PREMARKET","REGULAR","AFTERHOURS")):
            rows.append({"id":i+1,"session":session,"price_text":str(i+10),"currency":None,
                         "source_at":f"2026-11-16T00:0{i}:00Z","received_at":f"2026-11-16T00:0{i}:01Z",
                         "source_channel":"WS_QUOTE","delay_status":"UNKNOWN","raw_present":1})
        for i in range(600):
            at=datetime(2026,11,16,1,tzinfo=timezone.utc)+timedelta(seconds=i)
            rows.append({"id":i+4,"session":"OVERNIGHT","price_text":"15","currency":None,
                         "source_at":at.isoformat().replace("+00:00","Z"),
                         "received_at":(at+timedelta(seconds=1)).isoformat().replace("+00:00","Z"),
                         "source_channel":"WS_QUOTE","delay_status":"UNKNOWN","raw_present":1})
        points, summary=_selected_points(rows,start=NOW-timedelta(hours=5),end=NOW,
                                         gap_seconds=180,max_points=50)
        self.assertLessEqual(len(points),50)
        self.assertEqual({x["session"] for x in points},
                         {"PREMARKET","REGULAR","AFTERHOURS","OVERNIGHT"})
        self.assertTrue(summary["sampled"])

    def test_news_correction_and_unknown_published_time_stay_distinct(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"cache.sqlite3"
            store=PersonalFeedStore(path,7)
            store.put_news(7,{"id":"n1","title":"NVIDIA earnings","content":"NVIDIA earnings first",
                              "time":"2026-11-16T03:10:00Z"},
                           received_at="2026-11-16T03:20:00Z",max_items=20,max_versions=5)
            store.put_news(7,{"id":"n1","title":"NVIDIA earnings correction",
                              "content":"NVIDIA earnings correction","time":"2026-11-16T03:10:00Z"},
                           received_at="2026-11-16T03:21:00Z",max_items=20,max_versions=5)
            store.put_news(7,{"id":"n2","content":"CPI inflation report"},
                           received_at="2026-11-16T03:22:00Z",max_items=20,max_versions=5)
            store.close()
            result=read_timeline(path,7,symbol="NVDA",range_key="2h",now=NOW)
            self.assertEqual(result["news_metrics"]["returned_news"],2)
            self.assertEqual(result["news_metrics"]["unknown_supplier_published_time"],1)
            self.assertEqual(next(x for x in result["news"] if x["version"]==2)["versions"][0]["version"],2)
            self.assertEqual(next(x for x in result["news"] if x["version"]==2)["association_kind"],
                             "DIRECT_ENTITY_PHRASE")
            self.assertIsNone(next(x for x in result["news"] if x["association_kind"]=="SHARED_MACRO_THEME")
                              ["published_at_utc"])

    def test_invalid_window_and_symbol_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"none.sqlite3"
            with self.assertRaises(ValueError):
                read_timeline(path,7,symbol="SPCX",now=NOW)
            with self.assertRaises(ValueError):
                read_timeline(path,7,symbol="NVDA",end_at="2026-11-17T04:00:00Z",now=NOW)

    def test_recent_curve_originals_survive_bounded_raw_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"cache.sqlite3"
            store=PersonalFeedStore(path,7)
            current=utc_now()
            for i in range(105):
                raw=f'{{"synthetic":{i}}}'
                digest=hashlib.sha256(raw.encode()).hexdigest()
                store.db.execute("INSERT INTO session_raw_responses VALUES(?,?,?,?)",
                                 (digest,current,"WS_QUOTE",raw))
                insert_quote(store,raw=digest,source=current,received=current,price=str(i+1))
            store.put_session_quotes(7,[],states={},checked_at=current,source_channel="WS_QUOTE")
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM session_raw_responses").fetchone()[0],105)
            store.close()

    def test_provider_transition_is_recorded_only_when_state_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            store=PersonalFeedStore(Path(folder)/"cache.sqlite3",7)
            store.state("moomoo","CONNECTING")
            store.state("moomoo","CONNECTING")
            store.state("moomoo","DISCONNECTED")
            self.assertEqual([x[0] for x in store.db.execute(
                "SELECT state FROM provider_state_events WHERE provider='moomoo' ORDER BY id")],
                ["CONNECTING","DISCONNECTED"])
            store.close()


if __name__ == "__main__":
    unittest.main()
