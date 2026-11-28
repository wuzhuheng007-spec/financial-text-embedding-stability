from datetime import date, datetime, timedelta, timezone

from scripts.qqq_x_10y import calendar_us as cal, coverage as C, forecast as F, sentiment as S


def test_author_majority_and_tie_is_neutral():
    assert S.author_vote(["bullish", "bullish", "bearish"]) == "bullish"
    assert S.author_vote(["bullish", "bearish"]) == "neutral"
    assert S.author_vote(["bullish", "neutral"]) == "neutral"
    assert S.author_vote(["failure", "irrelevant"]) is None


def test_one_vote_per_author_and_zero_denominators():
    posts = [{"signal_day": "d", "author_id": "a", "label_class": "bullish"}] * 5 + [
        {"signal_day": "d", "author_id": "b", "label_class": "bearish"},
        {"signal_day": "e", "author_id": "c", "label_class": "neutral"},
        {"signal_day": "f", "author_id": "x", "label_class": "bullish"},
        {"signal_day": "g", "author_id": "y", "label_class": "failure"}]
    d = S.daily(posts)
    assert (d["d"]["B"], d["d"]["S"], d["d"]["p"]) == (1, 1, 0.5) and d["d"]["p_posts"] == 5 / 6
    assert d["e"]["p"] is None and d["e"]["missing_p"] == 1 and d["e"]["bucket"] is None
    assert d["f"]["p"] == 1.0 and d["f"]["boundary_S0"] == 1 and d["f"]["bucket"] == "P4"
    assert d["g"]["authors_voting"] == 0 and d["g"]["failure_rate"] == 1.0 and d["g"]["share_bull_of_all"] is None


def test_buckets_fixed_edges_two_to_one_in_top():
    assert S.bucket(0.0) == "P1" and S.bucket(1 / 3) == "P2" and S.bucket(0.5) == "P3" and S.bucket(2 / 3) == "P4" and S.bucket(1.0) == "P4"


def test_signal_window_close_to_close_weekend_and_half_day():
    sd = C.SignalDays(date(2026, 11, 20), date(2026, 12, 4))
    fri_close = cal.close_utc(date(2026, 11, 20))
    assert sd.of(fri_close - timedelta(seconds=1)) == date(2026, 11, 20)
    assert sd.of(fri_close) == date(2026, 11, 23)                                           # at the close: next signal
    assert sd.of(datetime(2026, 11, 22, 15, tzinfo=timezone.utc)) == date(2026, 11, 23)     # Sunday -> Monday
    assert sd.of(datetime(2026, 11, 26, 15, tzinfo=timezone.utc)) == date(2026, 11, 27)     # Thanksgiving -> Friday
    assert sd.of(datetime(2026, 11, 27, 18, 30, tzinfo=timezone.utc)) == date(2026, 11, 30)  # after the 13:00 half-day close


def test_main_sample_rules():
    base = {"lang": "en", "kind": "original"}
    assert C.eligibility({**base, "text": "$QQQ looks heavy into CPI"})[0]
    assert C.eligibility({**base, "text": "QQQ breaking out"})[0]
    assert C.eligibility({**base, "text": "$TQQQ to the moon"})[1] == "only_tqqq_sqqq_qqqm"
    assert C.eligibility({**base, "text": "Nasdaq 100 futures up"})[1] == "index_only_not_qqq"
    assert C.eligibility({**base, "text": "$QQQ $SPY $NVDA $AAPL $TSLA"})[1] == "ticker_string_only"
    assert C.eligibility({**base, "kind": "reply", "text": "$QQQ"})[1] == "reply"
    assert C.eligibility({**base, "kind": "quote", "text": "$QQQ lower"})[2] == "quote_own_text"


def test_pagination_truncation_is_a_state():
    assert C.run_state({"status": "PLANNED_SAMPLE", "stop_reason": "PLANNED_PAGE_LIMIT", "cursor_left": 1}) == "truncated_page_limit"
    assert C.run_state({"status": "COMPLETE", "stop_reason": "END_OF_RESULTS_EMPTY"}) == "zero_results"
    assert C.run_state({"status": "PARTIAL", "stop_reason": "ERROR:HTTP_402"}) == "api_failure"


def _days(n, start=date(2021, 12, 1)):
    return [d for d in cal.trading_days(start, start + timedelta(days=3 * n))][:n]


def test_outcomes_purge_at_split_boundary_and_no_immature_label():
    d = _days(60)
    o = [100 + i for i in range(60)]; c = [100.5 + i for i in range(60)]
    out = F.outcomes(d, o, c, 10)
    last_dev = max(i for i, x in enumerate(d) if x <= date(2021, 12, 31))
    assert all(F.split_of(d[i + 10]) == F.split_of(d[i]) for i in out)
    assert not any(last_dev - 10 < i <= last_dev for i in out)                   # crossing labels dropped
    assert max(out) == len(d) - 11                                                # the last 10 days have no outcome yet
    t = min(out); assert abs(out[t] - (c[t + 10] / o[t + 1] - 1)) < 1e-12         # entry at next open, exit at h-th close


def test_table_counts_reconcile_and_base_rate_is_same_state():
    d = _days(200, date(2024, 1, 2))
    o = [100.0] * 200; c = [100.0 * (1.01 if i % 2 else 0.99) for i in range(200)]
    state = {i: ("NARROW" if i < 100 else "OTHER") for i in range(200)}
    bucket = {i: "P4" for i in range(0, 200, 3)}
    rows = F.table(d, o, c, state, bucket, {}, set(bucket), horizons=(1,))
    r = next(x for x in rows if x["state"] == "NARROW" and x["bucket"] == "P4")
    assert r["up"] + r["down"] + r["flat"] == r["signal_days"]
    assert r["base_days"] == 100                                                  # every NARROW day has a 1-day outcome
    assert r["signal_days"] == len([i for i in range(100) if i % 3 == 0])
    assert abs(r["base_rate"] - r["base_up"] / r["base_days"]) < 1e-12
