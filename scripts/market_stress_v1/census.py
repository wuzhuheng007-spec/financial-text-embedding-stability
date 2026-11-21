"""Descriptive census of stress episodes and their future-path labels (market risk branch).

Operational definitions: stress_transition_v1_design_20261121 (a design specification written after the history had
been looked at; nothing here is a pre-registered test, and no model is fitted). Two things are kept apart on purpose:

  STATE ENUMERATION (uses closes up to and including the day, nothing later)
    - a segment is a run of valid daily quotes with no registered hole; nothing rolls across a hole;
    - D_t = C_t / max(C[t-251 .. t]) - 1 needs 252 quotes of the segment (the day itself included);
    - an episode starts when, not being in one, the previous valid D was > -5 % and D is <= -5 %; the first valid D
      of a segment never starts one (the state before it is unknown);
    - it ends on the fifth consecutive quote with D > -2 %; an episode still open at a hole or at the end of the
      data is BOUNDARY_CENSORED;
    - landmarks are scheduled on the start quote and every 5th quote after it while the episode is open; a
      scheduled quote is INCLUDED only if D <= -5 % that day, otherwise it is kept with the reason.

  FUTURE LABELS (the only place later prices are read)
    - origin E = the OPEN of the next quote of the same segment; the window is the 40 closes starting with that quote;
    - DOWN_FIRST / UP_FIRST: the first close <= 0.90 E / >= 1.10 E; NEITHER: a complete window touching neither;
      CENSORED: the window is not complete (end of data or a hole) - never counted as NEITHER, even if a barrier was
      already touched (that is recorded separately as `touched_before_censoring`);
    - persistent weakness = 1 when at least 30 of the 40 closes are below E and the 40th close is <= 0.95 E; 0 for any
      other complete window; missing when the window is not complete.

Missing is never zero. Nothing is selected by outcome: every scheduled landmark of every episode is written.
"""
from __future__ import annotations

from dataclasses import dataclass, field

PROTOCOL_ID = "stress_transition_v1_design_20261121"
WARMUP, ENTRY, RECOVERY, RECOVERY_RUN, SPACING = 252, -0.05, -0.02, 5, 5
HORIZON, DOWN, UP, WEAK_MIN_DAYS, WEAK_TERMINAL = 40, 0.90, 1.10, 30, -0.05


@dataclass
class Quote:
    date: str
    open: float | None
    close: float | None


@dataclass
class Census:
    episodes: list = field(default_factory=list)
    landmarks: list = field(default_factory=list)
    exclusions: list = field(default_factory=list)
    coverage: dict = field(default_factory=dict)


def split_segments(quotes: list[Quote], hole_dates: set[str]) -> list[list[Quote]]:
    """Valid quotes in date order, cut wherever a registered hole lies between two consecutive quotes. A quote
    without a usable close is itself a hole (it is not skipped as if the day had not existed)."""
    quotes = sorted(quotes, key=lambda q: q.date); holes = sorted(hole_dates)
    segs, cur = [], []
    for q in quotes:
        bad = q.close is None or not (q.close > 0)
        if cur and (bad or any(cur[-1].date < h < q.date for h in holes) or q.date in hole_dates):
            segs.append(cur); cur = []
        if not bad and q.date not in hole_dates:
            cur.append(q)
    if cur:
        segs.append(cur)
    return segs


def drawdowns(closes: list[float]) -> list[float | None]:
    """D for each quote of one segment; None during the warm-up. Monotonic deque: O(n), uses the past and the day only."""
    from collections import deque
    out, dq = [], deque()
    for i, c in enumerate(closes):
        while dq and closes[dq[-1]] <= c:
            dq.pop()
        dq.append(i)
        while dq[0] <= i - WARMUP:
            dq.popleft()
        out.append(c / closes[dq[0]] - 1.0 if i >= WARMUP - 1 else None)
    return out


def enumerate_states(series_id: str, quotes: list[Quote], hole_dates: set[str], last_segment_reason: str = "END_OF_DATA") -> Census:
    """Episodes and scheduled landmarks. Reads nothing after the quote being classified."""
    res = Census(); segs = split_segments(quotes, hole_dates)
    res.coverage = {"series_id": series_id, "quotes_given": len(quotes), "valid_quotes": sum(len(s) for s in segs), "segments": len(segs), "segment_ranges": [],
                    "quotes_with_drawdown": 0, "quotes_in_warmup": 0, "quotes_in_episode": 0, "registered_holes": sorted(hole_dates)}
    ep_no = 0
    for si, seg in enumerate(segs):
        closes = [q.close for q in seg]; D = drawdowns(closes)
        res.coverage["segment_ranges"].append({"segment": si, "first": seg[0].date, "last": seg[-1].date, "quotes": len(seg), "with_drawdown": sum(d is not None for d in D)})
        res.coverage["quotes_with_drawdown"] += sum(d is not None for d in D); res.coverage["quotes_in_warmup"] += sum(d is None for d in D)
        if len(seg) < WARMUP:
            res.exclusions.append({"series_id": series_id, "segment": si, "date": seg[0].date, "reason": "SEGMENT_SHORTER_THAN_WARMUP", "quotes": len(seg)})
        active = None; prev = None; run = 0
        for i, d in enumerate(D):
            if d is None:
                continue
            if active is None:
                if prev is None:
                    if d <= ENTRY:                                           # unknown state before the first valid D: no automatic entry
                        res.exclusions.append({"series_id": series_id, "segment": si, "date": seg[i].date, "reason": "FIRST_VALID_DRAWDOWN_ALREADY_AT_OR_BELOW_ENTRY_NO_AUTOMATIC_ENTRY"})
                elif prev > ENTRY and d <= ENTRY:
                    ep_no += 1; active = {"series_id": series_id, "episode": ep_no, "segment": si, "start_index": i, "start_date": seg[i].date, "min_drawdown": d, "min_drawdown_date": seg[i].date}
                    run = 0
            if active is not None:
                res.coverage["quotes_in_episode"] += 1
                if d < active["min_drawdown"]:
                    active["min_drawdown"], active["min_drawdown_date"] = d, seg[i].date
                k = i - active["start_index"]
                if k % SPACING == 0:
                    inc = d <= ENTRY
                    res.landmarks.append({"series_id": series_id, "episode": active["episode"], "segment": si, "index_in_segment": i, "date": seg[i].date, "ordinal": k // SPACING,
                                          "drawdown": d, "included": inc, "reason": "INCLUDED" if inc else "DRAWDOWN_ABOVE_ENTRY_LEVEL_ON_SCHEDULED_QUOTE"})
                run = run + 1 if d > RECOVERY else 0
                if run >= RECOVERY_RUN:
                    active.update(end_index=i, end_date=seg[i].date, status="RECOVERED", quotes=i - active["start_index"] + 1); res.episodes.append(active); active = None; run = 0
            prev = d
        if active is not None:
            last = si == len(segs) - 1
            active.update(end_index=len(seg) - 1, end_date=seg[-1].date, status="BOUNDARY_CENSORED", boundary=last_segment_reason if last else "REGISTERED_HOLE", quotes=len(seg) - active["start_index"])
            res.episodes.append(active)
    for e in res.episodes:
        marks = [m for m in res.landmarks if m["episode"] == e["episode"]]
        e["landmarks_scheduled"] = len(marks); e["landmarks_included"] = sum(m["included"] for m in marks)
    return res


def future_label(seg: list[Quote], t: int, horizon: int = HORIZON, down: float = DOWN, up: float = UP) -> dict:
    """Label of a decision made at the close of seg[t]. This function, and only this one, reads quotes after t."""
    if t + 1 >= len(seg):
        return {"path_label": "CENSORED", "censor_reason": "NO_NEXT_QUOTE_IN_SEGMENT", "origin_date": None, "window_quotes": 0, "persistent_weakness": None, "touched_before_censoring": None}
    o = seg[t + 1]
    if o.open is None or not (o.open > 0):
        return {"path_label": "EXCLUDED", "censor_reason": "ORIGIN_OPEN_MISSING", "origin_date": o.date, "window_quotes": 0, "persistent_weakness": None, "touched_before_censoring": None}
    E = o.open; win = seg[t + 1: t + 1 + horizon]; touched = None; first_down = first_up = None
    for k, q in enumerate(win, 1):
        r = q.close / E
        if first_down is None and r <= down:
            first_down = k
        if first_up is None and r >= up:
            first_up = k
        if touched is None and (r <= down or r >= up):
            touched = "DOWN_FIRST" if r <= down else "UP_FIRST"
    below = sum(q.close < E for q in win); mdd = min(q.close / E - 1.0 for q in win)
    base = {"origin_date": o.date, "window_quotes": len(win), "first_down_quote": first_down, "first_up_quote": first_up, "max_close_drawdown_from_origin": mdd, "closes_below_origin": below}
    if len(win) < horizon:
        return {**base, "path_label": "CENSORED", "censor_reason": "WINDOW_NOT_COMPLETE", "persistent_weakness": None, "touched_before_censoring": touched, "terminal_return": None}
    term = win[-1].close / E - 1.0
    return {**base, "path_label": touched or "NEITHER", "censor_reason": None, "touched_before_censoring": None, "terminal_return": term,
            "persistent_weakness": int(below >= WEAK_MIN_DAYS and term <= WEAK_TERMINAL)}


def label_landmarks(series_id: str, quotes: list[Quote], hole_dates: set[str], landmarks: list[dict], **kw) -> list[dict]:
    """One label row per scheduled landmark (included or not), built from the same segments."""
    segs = split_segments(quotes, hole_dates)
    return [{"series_id": series_id, "episode": m["episode"], "date": m["date"], "included": m["included"], **future_label(segs[m["segment"]], m["index_in_segment"], **kw)} for m in landmarks]


def summarize(census: Census, labels: list[dict]) -> dict:
    """Counts only: safe to leave the private directory."""
    from collections import Counter
    inc = [l for l in labels if l["included"]]
    return {"protocol_id": PROTOCOL_ID, "coverage": {k: v for k, v in census.coverage.items()},
            "episodes": {"total": len(census.episodes), "by_status": dict(Counter(e["status"] for e in census.episodes)),
                         "by_boundary": dict(Counter(e.get("boundary") for e in census.episodes if e["status"] == "BOUNDARY_CENSORED")),
                         "length_quotes": _dist([e["quotes"] for e in census.episodes]), "min_drawdown": _dist([e["min_drawdown"] for e in census.episodes]),
                         "ended_within_20_quotes": sum(e["status"] == "RECOVERED" and e["quotes"] <= 20 for e in census.episodes),
                         "never_reached_minus_10pct": sum(e["min_drawdown"] > -0.10 for e in census.episodes), "reached_minus_20pct": sum(e["min_drawdown"] <= -0.20 for e in census.episodes),
                         "first_start": min((e["start_date"] for e in census.episodes), default=None), "last_start": max((e["start_date"] for e in census.episodes), default=None)},
            "landmarks": {"scheduled": len(census.landmarks), "included": sum(m["included"] for m in census.landmarks), "not_included_by_reason": dict(Counter(m["reason"] for m in census.landmarks if not m["included"]))},
            "labels_on_included_landmarks": {"path": dict(Counter(l["path_label"] for l in inc)), "censor_reasons": dict(Counter(l["censor_reason"] for l in inc if l["censor_reason"])),
                                             "persistent_weakness": dict(Counter("MISSING" if l["persistent_weakness"] is None else str(l["persistent_weakness"]) for l in inc)),
                                             "touched_before_censoring": dict(Counter(str(l["touched_before_censoring"]) for l in inc if l["path_label"] == "CENSORED"))},
            "labels_on_scheduled_but_not_included": {"path": dict(Counter(l["path_label"] for l in labels if not l["included"]))},
            "exclusions": dict(Counter(x["reason"] for x in census.exclusions)), "models_fitted": 0}


def _dist(xs):
    if not xs:
        return None
    s = sorted(xs); n = len(s); q = lambda p: s[min(n - 1, int(p * (n - 1) + 0.5))]
    return {"n": n, "min": s[0], "p25": q(0.25), "median": q(0.5), "p75": q(0.75), "max": s[-1]}
