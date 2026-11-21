"""Stress episode census (market risk branch): hand-computed cases on made-up series. No real price is used here,
no model is fitted. State enumeration and future labels are tested apart, because only the labels may read later
quotes."""
import random
from datetime import date, timedelta

import pytest

from scripts.market_stress_v1 import census as cs
from scripts.market_stress_v1.census import Quote


def days(n, start=date(2000, 1, 3)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def series(closes, opens=None):
    ds = days(len(closes))
    return [Quote(ds[i], (opens[i] if opens else closes[i]), closes[i]) for i in range(len(closes))]


FLAT = [100.0] * 252                                    # exactly the warm-up: the first valid drawdown is on the 252nd quote, and it is 0


def test_the_first_valid_drawdown_needs_252_quotes_and_never_starts_an_episode():
    d = cs.drawdowns([100.0] * 251 + [90.0])
    assert d[:251] == [None] * 251 and d[251] == pytest.approx(-0.10)             # quote 252, the day itself included
    c = cs.enumerate_states("X", series([100.0] * 251 + [90.0, 90.0, 90.0]), set())
    assert c.episodes == [] and [x["reason"] for x in c.exclusions] == ["FIRST_VALID_DRAWDOWN_ALREADY_AT_OR_BELOW_ENTRY_NO_AUTOMATIC_ENTRY"]   # the state before it is unknown
    later = cs.enumerate_states("X", series([100.0] * 251 + [90.0, 99.0, 94.0]), set())                                                         # above -5 %, then a real crossing
    assert [(e["start_date"], e["status"]) for e in later.episodes] == [(days(254)[253], "BOUNDARY_CENSORED")]
    assert cs.enumerate_states("X", series([100.0] * 251), set()).exclusions[0]["reason"] == "SEGMENT_SHORTER_THAN_WARMUP"


def test_entry_recovery_and_the_landmark_schedule_match_the_hand_computation():
    # after the warm-up: -4.9 % (no entry), -5 % exactly (entry, landmark 0), then 4 quotes; landmark 1 on the 6th quote at -3 % (kept, not included);
    # then five quotes above -2 %: the episode ends on the fifth.
    tail = [95.1, 95.0, 94.0, 93.0, 96.5, 96.8, 97.0, 98.5, 98.6, 98.7, 98.8, 98.9, 99.5]
    c = cs.enumerate_states("X", series(FLAT + tail), set()); ds = days(252 + len(tail))
    assert len(c.episodes) == 1
    e = c.episodes[0]
    assert (e["start_date"], e["end_date"], e["status"], e["quotes"]) == (ds[253], ds[263], "RECOVERED", 11)
    assert e["min_drawdown"] == pytest.approx(-0.07) and e["min_drawdown_date"] == ds[255]
    assert [(m["date"], m["ordinal"], m["included"], m["reason"]) for m in c.landmarks] == [
        (ds[253], 0, True, "INCLUDED"), (ds[258], 1, False, "DRAWDOWN_ABOVE_ENTRY_LEVEL_ON_SCHEDULED_QUOTE"), (ds[263], 2, False, "DRAWDOWN_ABOVE_ENTRY_LEVEL_ON_SCHEDULED_QUOTE")]
    assert c.landmarks[0]["drawdown"] == pytest.approx(-0.05) and (e["landmarks_scheduled"], e["landmarks_included"]) == (3, 1)
    four = cs.enumerate_states("X", series(FLAT + [94.0, 98.5, 98.6, 98.7, 98.8, 97.0, 98.5]), set())      # only four quotes above -2 %, then back below: still the same episode
    assert len(four.episodes) == 1 and four.episodes[0]["status"] == "BOUNDARY_CENSORED" and four.episodes[0]["boundary"] == "END_OF_DATA"


def test_a_rebound_followed_by_a_second_fall_is_two_episodes_and_small_ones_are_kept():
    tail = [94.0] + [99.0] * 5 + [99.0] * 3 + [93.0] + [99.5] * 5
    c = cs.enumerate_states("X", series(FLAT + tail), set())
    assert [(e["status"], e["quotes"], round(e["min_drawdown"], 2)) for e in c.episodes] == [("RECOVERED", 6, -0.06), ("RECOVERED", 6, -0.07)]   # neither became a crisis; both are recorded
    assert cs.summarize(c, [])["episodes"]["never_reached_minus_10pct"] == 2


def test_a_registered_hole_cuts_the_series_and_nothing_rolls_across_it():
    closes = FLAT + [94.0, 93.0] + [100.0] * 300; qs = series(closes); hole = qs[254].date
    quotes = [q for q in qs if q.date != hole]                                   # the provider has no row for that day
    c = cs.enumerate_states("X", quotes, {hole})
    assert c.coverage["segments"] == 2 and [s["quotes"] for s in c.coverage["segment_ranges"]] == [254, 299]      # 554 quotes, one day absent: 254 before it, 299 after
    assert [(e["status"], e.get("boundary"), e["quotes"]) for e in c.episodes] == [("BOUNDARY_CENSORED", "REGISTERED_HOLE", 2)]
    assert c.coverage["segment_ranges"][1]["with_drawdown"] == 299 - 251         # the second segment warms up again from its own first quote
    unsplit = cs.enumerate_states("X", quotes, set())                            # without the registered hole the same rows would be treated as contiguous
    assert unsplit.coverage["segments"] == 1 and unsplit.episodes[0]["status"] == "RECOVERED"
    missing_close = cs.split_segments([Quote("2000-01-03", 1, 1.0), Quote("2000-01-04", 1, None), Quote("2000-01-05", 1, 1.0)], set())
    assert [len(s) for s in missing_close] == [1, 1]                             # a quote without a close is a hole, not a day that never existed


def test_state_rows_do_not_change_when_later_prices_change_or_are_cut_off():
    rnd = random.Random(20261121); closes = [100.0]
    for _ in range(1500):
        closes.append(closes[-1] * (1 + rnd.gauss(0, 0.013)))
    qs = series(closes); full = cs.enumerate_states("X", qs, set()); cut = 900; cut_date = qs[cut].date
    assert len(full.episodes) >= 4 and sum(m["included"] for m in full.landmarks) >= 20
    prefix = cs.enumerate_states("X", qs[:cut + 1], set())
    key = lambda m: (m["episode"], m["date"], m["ordinal"], m["included"], round(m["drawdown"], 12))
    assert [key(m) for m in prefix.landmarks] == [key(m) for m in full.landmarks if m["date"] <= cut_date]          # prefix invariance
    closed = [e for e in full.episodes if e["status"] == "RECOVERED" and e["end_date"] <= cut_date]
    assert [(e["start_date"], e["end_date"]) for e in prefix.episodes if e["status"] == "RECOVERED"] == [(e["start_date"], e["end_date"]) for e in closed]
    shocked = [Quote(q.date, q.open, q.close * (0.5 if i > cut else 1.0)) for i, q in enumerate(qs)]                 # the future is rewritten
    again = cs.enumerate_states("X", shocked, set())
    assert [key(m) for m in again.landmarks if m["date"] <= cut_date] == [key(m) for m in full.landmarks if m["date"] <= cut_date]


def test_path_labels_and_persistent_weakness_from_the_next_open():
    def seg(future_closes, origin_open=100.0):
        s = [Quote("d0", 100.0, 95.0), Quote("d1", origin_open, future_closes[0])] + [Quote(f"d{i + 2}", c, c) for i, c in enumerate(future_closes[1:])]
        return s
    down = cs.future_label(seg([99.0, 95.0, 90.0] + [99.0] * 37), 0)
    assert (down["path_label"], down["first_down_quote"], down["first_up_quote"], down["origin_date"]) == ("DOWN_FIRST", 3, None, "d1")     # close exactly at 0.90 E counts
    up = cs.future_label(seg([101.0, 110.0] + [80.0] * 38), 0)
    assert (up["path_label"], up["first_up_quote"], up["first_down_quote"]) == ("UP_FIRST", 2, 3)                                             # the first barrier decides; the later fall is recorded
    neither = cs.future_label(seg([95.0] * 40), 0)
    assert neither["path_label"] == "NEITHER" and neither["persistent_weakness"] == 1 and neither["terminal_return"] == pytest.approx(-0.05)   # 40 below, last at -5 % exactly
    assert cs.future_label(seg([95.0] * 39 + [95.1]), 0)["persistent_weakness"] == 0                                                        # last close above -5 %
    exactly_30 = cs.future_label(seg([99.0] * 29 + [101.0] * 10 + [95.0]), 0)                                                              # 29 + the last one = 30 closes below the origin
    assert exactly_30["closes_below_origin"] == 30 and exactly_30["persistent_weakness"] == 1                                               # "at least 30" includes 30
    only_29 = cs.future_label(seg([99.0] * 28 + [101.0] * 11 + [95.0]), 0)
    assert only_29["closes_below_origin"] == 29 and only_29["persistent_weakness"] == 0
    weak_but_never_touched = cs.future_label(seg([99.0] * 39 + [94.0]), 0)
    assert (weak_but_never_touched["path_label"], weak_but_never_touched["persistent_weakness"]) == ("NEITHER", 1)                          # the two tasks are not the same thing
    gap = cs.future_label(seg([95.0] * 40, origin_open=120.0), 0)
    assert gap["path_label"] == "DOWN_FIRST" and gap["first_down_quote"] == 1                                                               # measured from the next OPEN, not from the decision close


def test_an_incomplete_window_is_censored_never_neither_and_missing_is_not_zero():
    s = [Quote("d0", 100.0, 95.0), Quote("d1", 100.0, 99.0)] + [Quote(f"d{i}", 99.0, 99.0) for i in range(2, 31)]
    lab = cs.future_label(s, 0)
    assert (lab["path_label"], lab["censor_reason"], lab["window_quotes"], lab["persistent_weakness"], lab["touched_before_censoring"]) == ("CENSORED", "WINDOW_NOT_COMPLETE", 30, None, None)
    hit = [Quote("d0", 100.0, 95.0), Quote("d1", 100.0, 89.0), Quote("d2", 89.0, 89.0)]
    lab2 = cs.future_label(hit, 0)
    assert lab2["path_label"] == "CENSORED" and lab2["touched_before_censoring"] == "DOWN_FIRST"      # the definition asks for a complete window; the touch is kept as a note
    assert cs.future_label(hit, 2)["censor_reason"] == "NO_NEXT_QUOTE_IN_SEGMENT"
    assert cs.future_label([Quote("d0", 1.0, 1.0), Quote("d1", None, 1.0)] + [Quote(f"e{i}", 1.0, 1.0) for i in range(40)], 0)["path_label"] == "EXCLUDED"


def test_labels_are_written_for_every_scheduled_landmark_and_the_summary_holds_counts_only():
    tail = [94.0, 93.0, 92.0, 91.0, 90.0, 89.0] + [89.0] * 60 + [100.0] * 10
    qs = series(FLAT + tail); c = cs.enumerate_states("X", qs, set()); labels = cs.label_landmarks("X", qs, set(), c.landmarks)
    assert len(labels) == len(c.landmarks) and {l["path_label"] for l in labels} >= {"NEITHER", "CENSORED"}
    s = cs.summarize(c, labels)
    assert s["models_fitted"] == 0 and s["landmarks"]["scheduled"] == len(c.landmarks) and sum(s["labels_on_included_landmarks"]["path"].values()) == s["landmarks"]["included"]
    assert s["labels_on_included_landmarks"]["persistent_weakness"].get("MISSING", 0) == s["labels_on_included_landmarks"]["path"].get("CENSORED", 0)
    import json
    assert "2000-" not in json.dumps({k: v for k, v in s.items() if k not in ("coverage", "episodes")})   # dates appear only in coverage ranges and the first / last start
