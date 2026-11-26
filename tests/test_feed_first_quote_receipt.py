"""Receipt for the 2026-11-27 first due period: the 16:59 set is the fixed denominator, matching is per subscription
across windows, and the three failure classes are kept apart."""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.quant_personal_feed import first_quote_evidence as fq, market_hours as mh

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("receipt", ROOT / "scripts/quant_personal_feed/acceptance_20261127/receipt.py")
rc = importlib.util.module_from_spec(spec); spec.loader.exec_module(rc)
UTC = timezone.utc
S, E = datetime(2026, 11, 27, 9, 0, tzinfo=UTC), datetime(2026, 11, 27, 9, 11, tzinfo=UTC)
SALT = bytes.fromhex("00112233445566778899aabbccddeeff")
SIX = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]


@pytest.fixture(scope="module")
def cal():
    return mh.load(ROOT)


def q(sym, sec):
    t = S + timedelta(seconds=sec)
    return {"symbol": sym, "session": "PREMARKET", "received_at": fq.iso(t), "source_at": fq.iso(t - timedelta(milliseconds=80)),
            "source_channel": "WS_QUOTE", "date_basis": "SOURCE_TIME_NEW_YORK_DATE_ONLY"}


def baseline(cal, subs, salt=SALT, taken="2026-11-27T08:59:02Z"):
    ev = fq.summarize(session_rows=[], subscriptions=subs, start=S - timedelta(hours=1), end=S - timedelta(minutes=1), cal=cal, key_salt=salt)
    return {"taken_utc": taken, "health": {"business": "MARKET_CLOSED"}, "evidence": ev}


def live_health(business="OK"):
    return [{"t": fq.iso(S + timedelta(seconds=5 + 20 * i)), "business": "WAITING_FIRST_PUSH" if i == 0 else business, "ok": True}
            for i in range(36)]


def test_six_at_baseline_five_left_at_review_is_five_of_six_with_one_dropped(cal):
    review = fq.summarize(session_rows=[q(s, 1 + i) for i, s in enumerate(SIX[1:])] + [q("AAA", 0.5)],
                          subscriptions=SIX[1:], start=S, end=E, cal=cal, key_salt=SALT)
    assert review["labels_with_valid_quote"] == ["S1", "S2", "S3", "S4", "S5"]          # review labels shifted by one
    r = rc.build(baseline=baseline(cal, SIX), review=review, health=live_health())
    assert r["result"].startswith("5/6 ") and "dropped 1," in r["result"]
    assert r["counts"] == {"received": 5, "subscribed_no_valid_quote": 0, "dropped": 1, "new": 0}
    assert r["denominator"] == {"n": 6, "source": "BASELINE_1659_CONFIRMED_SET"}
    assert r["per_subscription"]["S1"]["state"] == "DROPPED"                            # AAA, matched by key not by label
    assert r["per_subscription"]["S1"]["valid_quote_before_drop_utc"] == "2026-11-27T09:00:00.500000Z"   # shown, not counted
    assert all(r["per_subscription"][f"S{i}"]["state"] == "RECEIVED" for i in range(2, 7))
    assert r["quote_outcome"] == "PARTIAL" and r["failure_classes"] == [] and r["observation"]["mode"] == "LIVE"


def test_still_subscribed_without_quote_and_new_subscription_are_apart(cal):
    review = fq.summarize(session_rows=[q("AAA", 3), q("ZZZ", 4)], subscriptions=["AAA", "BBB", "ZZZ"], start=S, end=E, cal=cal, key_salt=SALT)
    r = rc.build(baseline=baseline(cal, ["AAA", "BBB"]), review=review, health=live_health())
    assert r["result"].startswith("1/2 ") and r["counts"] == {"received": 1, "subscribed_no_valid_quote": 1, "dropped": 0, "new": 1}
    assert r["per_subscription"]["S2"]["state"] == "SUBSCRIBED_NO_VALID_QUOTE"
    assert r["new_subscriptions"]["N1"]["state"] == "NEW_SUBSCRIPTION" and r["new_subscriptions"]["N1"]["first_valid"]


def test_missed_observation_uses_the_cache_look_back_and_says_live_health_is_missing(cal):
    review = fq.summarize(session_rows=[q(s, 2) for s in SIX], subscriptions=SIX, start=S, end=E, cal=cal, key_salt=SALT)
    r = rc.build(baseline=None, review=review, health=[], backfill=True)
    assert r["failure_classes"] == ["OBSERVATION_MISSED"] and r["observation"]["missing"] == ["BASELINE_1659", "LIVE_HEALTH"]
    assert r["observation"]["live_health"].startswith("MISSING") and r["observation"]["mode"] == "CACHE_LOOKBACK"
    assert r["denominator"]["source"] == "REVIEW_SET_BASELINE_MISSING" and r["result"].startswith("6/6 ")
    late = rc.build(baseline=baseline(cal, SIX, taken="2026-11-27T09:20:00Z"), review=review, health=live_health()[20:])
    assert late["observation"]["missing"] == ["BASELINE_1659", "LIVE_HEALTH"] and late["observation"]["health_coverage"]["reason"] == "FIRST_SAMPLE_LATE"
    gap = [h for h in live_health() if not "09:03" <= h["t"][11:16] < "09:06"]
    assert rc.build(baseline=baseline(cal, SIX), review=review, health=gap)["observation"]["health_coverage"]["reason"] == "SAMPLE_GAP"


def test_feed_no_valid_quote_is_its_own_class(cal):
    review = fq.summarize(session_rows=[], subscriptions=SIX, start=S, end=E, cal=cal, key_salt=SALT)
    r = rc.build(baseline=baseline(cal, SIX), review=review, health=live_health("NO_FIRST_PUSH"))
    assert r["failure_classes"] == ["FEED_NO_VALID_QUOTE"] and r["quote_outcome"] == "FEED_NO_VALID_QUOTE"
    assert r["result"].startswith("0/6 ") and r["counts"]["subscribed_no_valid_quote"] == 6
    assert r["health_first_seen_utc"]["NO_FIRST_PUSH"] and r["health_first_seen_utc"]["WAITING_FIRST_PUSH"]


def test_receipt_names_no_symbol_and_no_key(cal):
    review = fq.summarize(session_rows=[q("ZZZSECRET", 1)], subscriptions=["ZZZSECRET"], start=S, end=E, cal=cal, key_salt=SALT)
    r = rc.build(baseline=baseline(cal, ["ZZZSECRET"]), review=review, health=live_health())
    k = fq.match_key("ZZZSECRET", SALT)
    assert "ZZZSECRET" not in repr(r) and k not in repr(r) and r["result"].startswith("1/1 ")
    assert fq.match_key("ZZZSECRET", None) is None and fq.match_key("ZZZSECRET", b"other") != k
