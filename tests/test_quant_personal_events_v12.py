"""Focused synthetic checks for private news rules and cache-only observation."""
import json
import tempfile
import unittest
from pathlib import Path

from scripts.quant_personal_feed.event_rules import classify, focus
from scripts.quant_personal_feed.open_market_acceptance import market_phase, summarize
from scripts.quant_personal_feed.store import PersonalFeedStore, read_personal_cache


def labels(text, *, at="2026-11-16T01:00:00Z"):
    return classify({"title":text,"content":"","published_at_utc":at,"received_at":at})


class EventRuleTests(unittest.TestCase):
    def test_multiple_topics_and_explainable_match(self):
        result=labels("NVIDIA 财报公布 HBM 数据中心资本支出，CPI 通胀")
        self.assertEqual([x["id"] for x in result["entities"]],["NVDA"])
        self.assertGreaterEqual(len(result["categories"]),3)
        self.assertTrue(all(x["phrase"] for x in result["categories"]))
        focused=focus({},result,{"NVDA"},set())
        self.assertTrue(focused["focus_eligible"])
        self.assertIn("原文出现",focused["focus_reason"])

    def test_ambiguous_words_do_not_create_company(self):
        result=labels("meta analysis of apple fruit and 超威品牌产品")
        self.assertEqual(result["entities"],[])
        self.assertEqual(result["categories"][0]["id"],"OTHER")

    def test_entities_securities_and_etfs_separate(self):
        result=labels("GOOG and GOOGL, SpaceX, TQQQ and 三星电子")
        ids=[x["id"] for x in result["entities"]]
        self.assertEqual(ids.count("GOOGL"),1)
        self.assertIn("SPACEX",ids)
        self.assertIn("TQQQ",ids)
        self.assertIn("SAMSUNG_ELECTRONICS",ids)
        self.assertEqual(next(x for x in result["entities"] if x["id"]=="TQQQ")["kind"],"ETF")

    def test_quarters_and_claim_stage_do_not_merge(self):
        q1=labels("NVIDIA 2026年Q1财报营收")
        q2=labels("NVIDIA 2026年Q2财报营收")
        self.assertNotEqual(q1["event_group_id"],q2["event_group_id"])
        base=labels("NVIDIA 2026年Q1财报营收")
        correction=labels("NVIDIA 2026年Q1财报营收更正")
        rumor=labels("NVIDIA 2026年Q1财报营收传闻")
        self.assertEqual({base["claim_stage"],correction["claim_stage"],rumor["claim_stage"]},
                         {"ACTUAL","CORRECTION","RUMOR"})
        self.assertEqual(len({base["event_group_id"],correction["event_group_id"],rumor["event_group_id"]}),3)

    def test_no_company_name_does_not_prevent_macro_focus(self):
        result=labels("美国CPI通胀公布")
        self.assertTrue(focus({},result,set(),{"MACRO_RATES"})["focus_eligible"])


class PreferenceAndObservationTests(unittest.TestCase):
    def test_owner_bound_prefs_do_not_change_news_or_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"private.sqlite3"
            store=PersonalFeedStore(path,1)
            store.put_news(1,{"id":"x","content":"Synthetic NVIDIA earnings"},
                           received_at="2026-11-16T01:00:00Z",max_items=5,max_versions=2)
            store.ensure_selection(["TQQQ"])
            store.set_event_preferences(["NVDA"],["EARNINGS_GUIDANCE"])
            store.close()
            data=read_personal_cache(path,1)
            self.assertEqual(data["event_preferences"]["entities"],["NVDA"])
            self.assertEqual(data["news_total"],1)
            self.assertEqual([x["symbol"] for x in data["selection"] if x["desired"]],["TQQQ"])
            self.assertEqual(read_personal_cache(path,2)["cache_state"],"OWNER_MISMATCH")

    def test_market_code_uses_fresh_cached_official_state(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"status.json"
            path.write_text(json.dumps({"observed_at_utc":"2026-11-17T01:00:00Z",
                                        "market_state_codes":{"US":11}}),encoding="utf-8")
            self.assertEqual(market_phase(path,"2026-11-17T01:00:05Z")[0],"AFTER_HOURS_END")
            self.assertEqual(market_phase(path,"2026-11-17T01:02:00Z")[0],"UNKNOWN")
            self.assertEqual(market_phase(path,"2026-11-16T01:00:05Z")[0],"CLOSED")

    def test_source_time_compared_as_aware_datetime(self):
        base={"state":"CONNECTED_MARKET_CLOSED","worker_alive":1,
              "heartbeat_at":"2026-11-16T01:00:00Z","duplicate_quote_packets":10,
              "market_phase":"CLOSED"}
        a={**base,"observed_at_utc":"2026-11-16T01:00:00Z",
           "quotes":{"TQQQ":{"provider_time":"2026-11-16T09:59:00+09:00",
                             "received_at":"2026-11-16T01:00:00Z"}}}
        b={**base,"observed_at_utc":"2026-11-16T01:00:05Z","duplicate_quote_packets":12,
           "quotes":{"TQQQ":{"provider_time":"2026-11-16T01:00:30Z",
                             "received_at":"2026-11-16T01:00:05Z"}}}
        report=summarize([a,b])
        self.assertEqual(report["per_symbol"]["TQQQ"]["observed_source_time_advances"],1)
        self.assertEqual(report["duplicate_packet_delta"],2)
        self.assertEqual(report["open_market_acceptance"],"NOT_RUN_NO_REGULAR_SESSION")


if __name__ == "__main__":
    unittest.main()
