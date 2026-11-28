"""Receipt 1, price part: recompute the named cases and scan ten years with the frozen rules.

python -m scripts.qqq_x_10y.build_regimes --snapshot <market_research.sqlite> --private <dir> --public <dir>
Private output (price levels): regime_daily_private.csv, provenance.json. Public output (dates, percentages, counts):
regime_events.csv, case_comparison.csv, regime_by_month.csv, price_provenance.json.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from . import calendar_us as cal, prices, regimes as R

STUDY_START, STUDY_END = date(2016, 11, 28), date(2026, 11, 27)
CASES = [  # (case_id, kind, first month, last month) as named by the owner
    ("C01", "range", "2025-10", "2026-04"), ("C02", "range", "2026-05", "2026-07"),
    ("C03", "decline", "2025-04", "2025-04"), ("C04", "decline", "2026-04", "2026-04"), ("C05", "decline", "2026-08", "2026-08"),
    ("C06", "decline", "2026-10", "2026-10"), ("C07", "decline", "2026-11", "2026-11"),
    ("C08", "rebound", "2025-05", "2025-10"), ("C09", "rebound", "2026-04", "2026-07"),
]


def month_bounds(m0, m1):
    y0, mo0 = map(int, m0.split("-")); y1, mo1 = map(int, m1.split("-"))
    a = date(y0, mo0, 1)
    z = date(y1 + (mo1 == 12), mo1 % 12 + 1, 1)
    return a, date.fromordinal(z.toordinal() - 1)


def runs(flags, dates, i0, i1):
    out, start = [], None
    for i in range(i0, i1 + 2):
        on = i <= i1 and flags[i]
        if on and start is None:
            start = i
        if not on and start is not None:
            out.append((dates[start], dates[i - 1], i - start)); start = None
    return out


def pct(x, nd=2):
    return None if x is None else round(100 * x, nd)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True); ap.add_argument("--private", required=True); ap.add_argument("--public", required=True)
    a = ap.parse_args(argv)
    priv, pub = Path(a.private), Path(a.public); priv.mkdir(parents=True, exist_ok=True); pub.mkdir(parents=True, exist_ok=True)
    b = prices.load(a.snapshot)
    D, C, H, L = b.dates, b.close, b.high, b.low
    rr = {w: R.rolling_range(C, H, L, w) for w in R.WINDOWS}
    s = R.scan(D, C)
    lab = R.label_days(s, rr[63])
    i_start = next(i for i, d in enumerate(D) if d >= STUDY_START); i_end = len(D) - 1
    # ---- private daily table
    with open(priv / "regime_daily_private.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "open", "high", "low", "close", "close_div_adj", "peak63_known", "peak63_known_date", "dd_from_peak63",
                    "range21", "range63", "range126", "amp63", "net63", "eff63", "underwater", "dd_trigger", "in_drawdown",
                    "rebound_trigger", "rebound_phase", "event_id", "trigger_label", "phase", "overlap_count", "early_close"])
        for i in range(i_start, i_end + 1):
            f_, l_ = s.flags[i], lab[i]
            g = lambda win, k: None if rr[win][i] is None else round(rr[win][i][k], 6)
            w.writerow([D[i], b.open[i], H[i], L[i], C[i], b.adj_close[i], f_["peak_known_close"], f_["peak_known_date"],
                        round(f_["drawdown_from_peak63"], 6), g(21, "range"), g(63, "range"), g(126, "range"), g(63, "amplitude"),
                        g(63, "net"), g(63, "efficiency"), int(f_["underwater"]), int(f_["dd_trigger"]), int(f_["in_drawdown"]),
                        int(f_["rebound_trigger"]), int(f_["rebound_phase"]), f_["event_id"] or "", l_["trigger_label"], l_["phase"],
                        l_["overlap_count"], int(D[i] in cal.early_closes(D[i].year))])
    # ---- events (public: dates and percentages)
    ev_rows = []
    for e in s.events:
        if (e.rebound_i is not None and D[e.rebound_i] < STUDY_START) and D[e.trigger_i] < STUDY_START:
            continue
        reb = e.rebound_i
        ev_rows.append({"event_id": e.event_id, "type": "DRAWDOWN_10_FROM_63D_PEAK", "status": e.status,
                        "peak_known_at_trigger_date": D[e.peak_i], "trigger_date": D[e.trigger_i], "drop_at_trigger_pct": pct(e.drop_at_trigger),
                        "lowest_close_date_expost": D[e.min_i], "lowest_vs_known_peak_pct_expost": pct(e.min_close / e.peak_close - 1),
                        "rebound_trigger_date": D[reb] if reb is not None else "", "rebound_vs_running_min_pct": pct(e.rebound_close / e.min_close - 1) if reb is not None else "",
                        "trading_days_trigger_to_rebound": (reb - e.trigger_i) if reb is not None else "",
                        "trading_days_trigger_to_low_expost": e.min_i - e.trigger_i, "in_study_window": int(D[e.trigger_i] >= STUDY_START)})
    nar = runs([l["narrow63"] for l in lab], D, i_start, i_end)
    for k, (d0, d1, n) in enumerate(nar, 1):
        ev_rows.append({"event_id": f"NR{k:03d}-{d0}", "type": "NARROW63_RUN (range63 <= 10% on each day)", "status": "OPEN" if d1 == D[i_end] else "ENDED",
                        "trigger_date": d0, "rebound_trigger_date": "", "trading_days_trigger_to_rebound": "", "narrow_run_end": d1, "narrow_run_days": n,
                        "in_study_window": 1})
    keys = ["event_id", "type", "status", "peak_known_at_trigger_date", "trigger_date", "drop_at_trigger_pct", "lowest_close_date_expost",
            "lowest_vs_known_peak_pct_expost", "rebound_trigger_date", "rebound_vs_running_min_pct", "trading_days_trigger_to_rebound",
            "trading_days_trigger_to_low_expost", "narrow_run_end", "narrow_run_days", "in_study_window"]
    with open(pub / "regime_events.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, keys); w.writeheader(); [w.writerow(r) for r in ev_rows]
    # ---- cases
    day_cases = defaultdict(list)
    case_rows = []
    for cid, kind, m0, m1 in CASES:
        a0, a1 = month_bounds(m0, m1)
        idx = [i for i, d in enumerate(D) if a0 <= d <= a1]
        windows = [("full_months", idx[0], idx[-1])] if idx else []
        if idx:
            for i in idx: day_cases[D[i]].append(cid)
            # the months hold the tail of a decline that began earlier: one cross-month window per such event, from the peak
            # known at its trigger to the last day of the months
            seen = set()
            for i in idx:
                eid = s.flags[i]["event_id"]
                ev = next((e for e in s.events if e.event_id == eid), None) if eid else None
                if ev and ev.peak_i < idx[0] and eid not in seen:
                    seen.add(eid); windows.append((f"cross_month_from_known_peak:{eid}", ev.peak_i, idx[-1]))
        for wname, i0, i1 in windows:
            st = R.window_stats(D, C, H, L, i0, i1)
            sub = runs([l["narrow63"] for l in lab], D, i0, i1)
            evs = sorted({s.flags[i]["event_id"] for i in range(i0, i1 + 1) if s.flags[i]["event_id"]})
            ph = Counter(lab[i]["phase"] for i in range(i0, i1 + 1))
            case_rows.append({
                "case_id": cid, "owner_label": kind, "months": f"{m0}..{m1}", "window": wname, "start": st["start"], "end": st["end"],
                "data_end_truncated": int(a1 > D[i_end] and wname == "full_months"), "trading_days": st["trading_days"],
                "close_range_pct": pct(st["close_range"]), "intraday_amplitude_pct": pct(st["intraday_amplitude"]),
                "net_change_pct": pct(st["net_change"]), "path_efficiency": round(st["path_efficiency"], 4),
                "high_close_date": st["high_close_date"], "low_close_date": st["low_close_date"],
                "max_drawdown_pct": pct(st["max_drawdown"]), "max_drawdown_peak": st["max_drawdown_peak"], "max_drawdown_trough": st["max_drawdown_trough"],
                "max_runup_pct": pct(st["max_runup"]), "max_runup_trough": st["max_runup_trough"], "max_runup_peak": st["max_runup_peak"],
                "whole_window_is_10pt_range": int(st["close_range"] <= R.THRESH),
                "days_range63_le_10": sum(lab[i]["narrow63"] for i in range(i0, i1 + 1)),
                "days_range21_le_10": sum(1 for i in range(i0, i1 + 1) if rr[21][i] and rr[21][i]["range"] <= R.THRESH),
                "days_range126_le_10": sum(1 for i in range(i0, i1 + 1) if rr[126][i] and rr[126][i]["range"] <= R.THRESH),
                "range63_subintervals": "; ".join(f"{x}..{y} ({n}d)" for x, y, n in sub),
                "drawdown_triggers": "; ".join(D[i].isoformat() for i in range(i0, i1 + 1) if s.flags[i]["dd_trigger"]),
                "rebound_triggers": "; ".join(D[i].isoformat() for i in range(i0, i1 + 1) if s.flags[i]["rebound_trigger"]),
                "events_touched": "; ".join(evs),
                "phase_days": "; ".join(f"{k}={ph[k]}" for k in ("REBOUND", "DRAWDOWN", "NARROW", "OTHER") if ph[k]),
            })
    shared = {d: v for d, v in day_cases.items() if len(v) > 1}
    for r in case_rows:
        if r["window"] == "full_months":
            mine = [d for d, v in day_cases.items() if r["case_id"] in v]
            r["days_shared_with_other_cases"] = sum(1 for d in mine if d in shared)
            r["unique_day_weight_sum"] = round(sum(1 / len(day_cases[d]) for d in mine), 2)
    ck = list(case_rows[0].keys()) + ["days_shared_with_other_cases", "unique_day_weight_sum"]
    with open(pub / "case_comparison.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, ck); w.writeheader(); [w.writerow(r) for r in case_rows]
    # ---- month table (all trading months of the study window)
    by_m = defaultdict(list)
    for i in range(i_start, i_end + 1):
        by_m[D[i].strftime("%Y-%m")].append(i)
    with open(pub / "regime_by_month.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["month", "trading_days", "close_range_pct", "intraday_amplitude_pct", "net_change_pct", "path_efficiency", "max_drawdown_pct",
                    "max_runup_pct", "days_NARROW", "days_DRAWDOWN", "days_REBOUND", "days_OTHER", "dd_triggers", "rebound_triggers"])
        for m, ii in sorted(by_m.items()):
            st = R.window_stats(D, C, H, L, ii[0], ii[-1]); ph = Counter(lab[i]["phase"] for i in ii)
            w.writerow([m, len(ii), pct(st["close_range"]), pct(st["intraday_amplitude"]), pct(st["net_change"]), round(st["path_efficiency"], 4),
                        pct(st["max_drawdown"]), pct(st["max_runup"]), ph["NARROW"], ph["DRAWDOWN"], ph["REBOUND"], ph["OTHER"],
                        sum(s.flags[i]["dd_trigger"] for i in ii), sum(s.flags[i]["rebound_trigger"] for i in ii)])
    # ---- provenance
    phase_all = Counter(lab[i]["phase"] for i in range(i_start, i_end + 1))
    prov = {"protocol": R.PROTOCOL, "price": b.meta, "study_window_requested": [STUDY_START.isoformat(), STUDY_END.isoformat()],
            "last_closed_trading_day_by_calendar": cal.last_closed_trading_day(cal.close_utc(STUDY_END)).isoformat(),
            "data_end_used": D[i_end].isoformat(),
            "calendar_check": prices.check_calendar(b, STUDY_START, STUDY_END), "corporate_actions": {k: v for k, v in prices.check_splits(b, STUDY_START).items()},
            "warmup_first_bar": D[0].isoformat(), "study_trading_days": i_end - i_start + 1,
            "phase_days_in_study": dict(phase_all),
            "overlap_days_narrow_inside_drawdown": sum(1 for i in range(i_start, i_end + 1) if lab[i]["narrow63"] and s.flags[i]["in_drawdown"]),
            "overlap_days_narrow_inside_rebound_phase": sum(1 for i in range(i_start, i_end + 1) if lab[i]["narrow63"] and s.flags[i]["rebound_phase"]),
            "drawdown_events_triggered_in_study": sum(1 for e in s.events if D[e.trigger_i] >= STUDY_START),
            "narrow63_runs_in_study": len(nar),
            "outputs_sha256": {}}
    for p in [priv / "regime_daily_private.csv", pub / "regime_events.csv", pub / "case_comparison.csv", pub / "regime_by_month.csv"]:
        prov["outputs_sha256"][p.name] = prices.sha256(p)
    pub_prov = {k: v for k, v in prov.items()}
    pub_prov["price"] = {k: v for k, v in b.meta.items()}
    (pub / "price_provenance.json").write_text(json.dumps(pub_prov, indent=1, default=str, ensure_ascii=False), encoding="utf-8")
    (priv / "provenance.json").write_text(json.dumps(prov, indent=1, default=str, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"data_end": D[i_end].isoformat(), "events": prov["drawdown_events_triggered_in_study"], "narrow_runs": len(nar),
                      "phase_days": dict(phase_all), "cases": len(case_rows)}, default=str))


if __name__ == "__main__":
    main()
