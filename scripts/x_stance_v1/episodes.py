"""Sentiment episodes: runs of consecutive sessions with the same dominant state.

A session whose state is UNKNOWN or absent is a gap. Nothing is carried across a gap: the run before it is
right-censored (its end was not observed), and the run after it is left-censored (its start was not observed).
The first run of the data window is left-censored, and a run still open at the end of the data is right-censored.
Only a change to a different known state, with no gap in between, confirms an end.

`sessions` and `calendar_days` of a censored episode are OBSERVED lengths, i.e. lower bounds. The end date and the
final length are outcomes: they are never available at the start of an episode and are not features.
"""
from __future__ import annotations

import hashlib
import statistics
from datetime import date

STATES = ("bullish", "bearish", "neutral")


def build(series: list[tuple[str, str | None]], *, all_sessions: list[str], topic_id: str, target_id: str) -> list[dict]:
    """series: (session_date, state) for the sessions that have an aggregate. all_sessions: every exchange session
    of the study range in order; a session missing from `series` is a gap."""
    state = {d: (s if s in STATES else None) for d, s in series}
    out, cur = [], None
    prev_known = False                                      # was the previous session a known state (no gap just before)?
    for i, d in enumerate(all_sessions):
        s = state.get(d)
        if s is None:
            if cur:
                cur["right_censored"], cur["right_censor_reason"] = True, "GAP"; out.append(cur); cur = None
            prev_known = False
            continue
        if cur and cur["state"] == s:
            cur["end_session"], cur["sessions"] = d, cur["sessions"] + 1
        else:
            if cur:
                cur["right_censored"] = False; out.append(cur)
            left = not prev_known
            cur = {"state": s, "start_session": d, "end_session": d, "sessions": 1, "left_censored": left,
                   "left_censor_reason": (None if not left else ("WINDOW_START" if i == 0 or not any(state.get(x) for x in all_sessions[:i]) else "AFTER_GAP")),
                   "right_censored": None, "right_censor_reason": None}
        prev_known = True
    if cur:
        cur["right_censored"], cur["right_censor_reason"] = True, "DATA_END_STILL_OPEN"; out.append(cur)
    for e in out:
        e["calendar_days"] = (date.fromisoformat(e["end_session"]) - date.fromisoformat(e["start_session"])).days + 1
        e["topic_id"], e["target_id"] = topic_id, target_id
        e["episode_id"] = hashlib.sha256(f"{topic_id}|{target_id}|{e['start_session']}|{e['state']}".encode()).hexdigest()[:20]
        e["length_is_lower_bound"] = bool(e["right_censored"] or e["left_censored"])
    return out


def _dist(xs: list[int]) -> dict:
    if not xs:
        return {"n": 0, "mean": None, "median": None, "p25": None, "p75": None, "max": None}
    q = statistics.quantiles(xs, n=4, method="inclusive") if len(xs) > 1 else [xs[0]] * 3
    return {"n": len(xs), "mean": statistics.fmean(xs), "median": statistics.median(xs), "p25": q[0], "p75": q[2], "max": max(xs)}


def summary(eps: list[dict]) -> dict:
    ended = [e for e in eps if not e["right_censored"]]
    full = [e for e in ended if not e["left_censored"]]
    open_now = [e for e in eps if e["right_censor_reason"] == "DATA_END_STILL_OPEN"]
    return {"episodes": len(eps), "left_censored": sum(e["left_censored"] for e in eps), "right_censored": sum(bool(e["right_censored"]) for e in eps),
            "ended_with_observed_start": {s: _dist([e["sessions"] for e in full if e["state"] == s]) for s in STATES},
            "ended_any_start": {s: _dist([e["sessions"] for e in ended if e["state"] == s]) for s in STATES},
            "still_open": [{"state": e["state"], "start_session": e["start_session"], "observed_sessions_lower_bound": e["sessions"], "end_session": None} for e in open_now],
            "note": "Statistics describe ended episodes only. They are not an unbiased mean lifetime of all episodes: long and still-open ones are under-represented."}
