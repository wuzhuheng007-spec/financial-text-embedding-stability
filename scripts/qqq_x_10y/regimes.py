"""Frozen regime rules for US.QQQ daily closes (protocol qqq-x-10y-regimes-v1, 2026-11-28).

All quantities at day t use closes up to and including t only (known at t's regular close).

Narrow range ("10 points"): range_W(t) = max(close[t-W+1..t]) / min(close[t-W+1..t]) - 1 <= 0.10, W = 63 main,
  21 and 126 sensitivity. Reported alongside: the intraday amplitude max(high)/min(low) - 1 over the same days, the
  first-to-last net change close[t]/close[t-W+1] - 1, and path efficiency |close[t] - close[t-W+1]| / sum |daily change|.
  A window needs W bars; earlier days are warm-up (None).

Drawdown event (state machine, one pass in date order):
  peak63(t) = max close over the 63 trading days ending at t; breach(t) = close(t) <= 0.90 * peak63(t).
  NORMAL --(breach(t) and not breach(t-1))--> DRAWDOWN: event id assigned, trigger date t, the peak value and date known
           at t, drop = close/peak63 - 1. The first bar has no t-1 and cannot trigger (warm-up).
  DRAWDOWN: running minimum close from the trigger day (inclusive) is updated each day. Rebound trigger at the first t with
           close(t) >= 1.10 * running minimum (minimum taken through t-1 plus t; a new minimum on t cannot be a rebound on t).
           The event closes: rebound date t, state back to NORMAL.
  After a rebound a new drawdown needs a NEW down-cross: breach(t) and not breach(t-1). Staying under the threshold
  does not re-trigger.
  Same-day priority: the rebound check runs before the drawdown check; a drawdown can trigger at the earliest the day
  after a rebound (it needs not breach(t-1), and the rebound day itself counts as t-1 only if it is not in breach).
  An event without a rebound by the data end is OPEN.
  Ex-post peak-to-trough figures (the event's lowest close, max drawdown from the true pre-trigger peak) are descriptive
  only and never feed the daily state.

Daily flags: underwater (close < peak63), dd_trigger, in_drawdown (trigger day through the day before the rebound),
  rebound_trigger, rebound_phase (rebound day and the next 20 trading days, ended early by a new drawdown).
Mutually exclusive main state, priority REBOUND_TRIGGER > DRAWDOWN_TRIGGER > NARROW (range63 <= 10%) > OTHER for the
  trigger-day contrast; and the phase state used for the forecast tables, priority REBOUND > DRAWDOWN > NARROW > OTHER.
  Overlaps (e.g. a narrow-range day inside a drawdown) are kept in the flags and counted.
"""
from __future__ import annotations

from dataclasses import dataclass, field

PROTOCOL = "qqq-x-10y-regimes-v1"
THRESH = 0.10
PEAK_W = 63
REBOUND_PHASE_DAYS = 20
WINDOWS = (21, 63, 126)


def rolling_range(close, high, low, w):
    n = len(close)
    out = []
    for t in range(n):
        if t < w - 1:
            out.append(None); continue
        c = close[t - w + 1:t + 1]
        steps = sum(abs(c[i] - c[i - 1]) for i in range(1, len(c)))
        out.append({"range": max(c) / min(c) - 1, "amplitude": max(high[t - w + 1:t + 1]) / min(low[t - w + 1:t + 1]) - 1,
                    "net": c[-1] / c[0] - 1, "efficiency": (abs(c[-1] - c[0]) / steps) if steps else 0.0})
    return out


@dataclass
class Event:
    event_id: str
    trigger_i: int
    peak_i: int
    peak_close: float
    trigger_close: float
    drop_at_trigger: float
    min_i: int = -1
    min_close: float = 0.0
    rebound_i: int | None = None
    rebound_close: float | None = None
    status: str = "OPEN"


@dataclass
class Scan:
    dates: list
    close: list
    peak63: list = field(default_factory=list)
    breach: list = field(default_factory=list)
    flags: list = field(default_factory=list)
    events: list = field(default_factory=list)


def scan(dates, close, thresh=THRESH, peak_w=PEAK_W, phase_days=REBOUND_PHASE_DAYS) -> Scan:
    s = Scan(dates, close)
    state, ev, run_min, phase_left = "NORMAL", None, None, 0
    for t, c in enumerate(close):
        lo = max(0, t - peak_w + 1)
        window = close[lo:t + 1]
        pk = max(window); pk_i = lo + max(range(len(window)), key=lambda k: (window[k], k))
        full = t >= peak_w - 1
        br = c <= (1 - thresh) * pk
        s.peak63.append(pk); s.breach.append(br)
        f = {"underwater": c < pk, "dd_trigger": False, "in_drawdown": False, "rebound_trigger": False,
             "rebound_phase": False, "event_id": None, "peak_known_close": pk, "peak_known_date": dates[pk_i],
             "drawdown_from_peak63": c / pk - 1, "warmup": not full}
        if state == "DRAWDOWN":
            if c >= (1 + thresh) * run_min:                       # rebound check first
                ev.rebound_i, ev.rebound_close, ev.status = t, c, "REBOUNDED"
                f["rebound_trigger"] = True; f["event_id"] = ev.event_id
                state, phase_left = "NORMAL", phase_days + 1
            else:
                if c < run_min:
                    run_min = c; ev.min_i, ev.min_close = t, c
                f["in_drawdown"] = True; f["event_id"] = ev.event_id
        elif full and t > 0 and br and not s.breach[t - 1]:
            ev = Event(event_id=f"DD{len(s.events) + 1:03d}-{dates[t].isoformat()}", trigger_i=t, peak_i=pk_i,
                       peak_close=pk, trigger_close=c, drop_at_trigger=c / pk - 1, min_i=t, min_close=c)
            s.events.append(ev); run_min = c; state = "DRAWDOWN"; phase_left = 0
            f["dd_trigger"] = f["in_drawdown"] = True; f["event_id"] = ev.event_id
        if phase_left > 0 and not f["in_drawdown"]:
            f["rebound_phase"] = True; phase_left -= 1
        s.flags.append(f)
    return s


def label_days(s: Scan, ranges63):
    """Two mutually exclusive labels per day: trigger contrast and phase state."""
    out = []
    for f, r in zip(s.flags, ranges63):
        narrow = r is not None and r["range"] <= THRESH
        trig = "REBOUND_TRIGGER" if f["rebound_trigger"] else "DRAWDOWN_TRIGGER" if f["dd_trigger"] else "NARROW" if narrow else "OTHER"
        phase = "REBOUND" if f["rebound_phase"] else "DRAWDOWN" if f["in_drawdown"] else "NARROW" if narrow else "OTHER"
        overlaps = sum([bool(f["rebound_phase"]), bool(f["in_drawdown"]), narrow])
        out.append({"trigger_label": trig, "phase": phase, "narrow63": narrow, "overlap_count": overlaps})
    return out


def window_stats(dates, close, high, low, i0, i1):
    """Descriptive statistics for the closed index range [i0, i1] (ex-post; for cases and charts only)."""
    c = close[i0:i1 + 1]
    hi_i = i0 + max(range(len(c)), key=lambda k: c[k]); lo_i = i0 + min(range(len(c)), key=lambda k: c[k])
    # max peak-then-trough drawdown and max trough-then-peak run-up on closes
    mdd, pk, pk_i, mdd_pair = 0.0, c[0], i0, (i0, i0)
    mru, tr, tr_i, mru_pair = 0.0, c[0], i0, (i0, i0)
    for k, x in enumerate(c):
        i = i0 + k
        if x > pk: pk, pk_i = x, i
        if x / pk - 1 < mdd: mdd, mdd_pair = x / pk - 1, (pk_i, i)
        if x < tr: tr, tr_i = x, i
        if x / tr - 1 > mru: mru, mru_pair = x / tr - 1, (tr_i, i)
    steps = sum(abs(c[k] - c[k - 1]) for k in range(1, len(c)))
    return {"start": dates[i0], "end": dates[i1], "trading_days": len(c),
            "close_range": max(c) / min(c) - 1, "intraday_amplitude": max(high[i0:i1 + 1]) / min(low[i0:i1 + 1]) - 1,
            "net_change": c[-1] / c[0] - 1, "path_efficiency": abs(c[-1] - c[0]) / steps if steps else 0.0,
            "high_close_date": dates[hi_i], "low_close_date": dates[lo_i],
            "max_drawdown": mdd, "max_drawdown_peak": dates[mdd_pair[0]], "max_drawdown_trough": dates[mdd_pair[1]],
            "max_runup": mru, "max_runup_trough": dates[mru_pair[0]], "max_runup_peak": dates[mru_pair[1]]}
