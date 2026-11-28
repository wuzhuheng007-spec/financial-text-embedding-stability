"""Independent re-computation (no import from the study modules): one case row and one probability cell from raw sqlite.

python scripts/qqq_x_10y/verify_independent.py <market_research.sqlite> <results dir>
1. Case C01 (2025-10..2026-04): close range, intraday amplitude, net change, max drawdown, recomputed with plain SQL + loops.
2. Price-only cell TEST / DRAWDOWN / h=10: drawdown phase re-derived with its own loop (63-day peak, -10% fresh cross,
   +10% from the running minimum), outcome close[t+10]/open[t+1]-1 > 0.1%, signal dates 2024-01-01.. with t+10 in data.
Prints both, and the values in the published tables, so hits, denominator and base rate can be compared.
"""
import csv, sqlite3, sys

db = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
rows = db.execute("select local_date, open, high, low, close from observations where series_id='US.QQQ|DAY|a0' order by local_date").fetchall()
d = [r[0] for r in rows]; o = [r[1] for r in rows]; h = [r[2] for r in rows]; l = [r[3] for r in rows]; c = [r[4] for r in rows]

# 1. case row
ix = [i for i, x in enumerate(d) if "2025-10-01" <= x <= "2026-04-30"]
cc = [c[i] for i in ix]
peak, mdd = cc[0], 0.0
for x in cc:
    peak = max(peak, x); mdd = min(mdd, x / peak - 1)
mine = {"days": len(ix), "close_range_pct": round(100 * (max(cc) / min(cc) - 1), 2),
        "intraday_amplitude_pct": round(100 * (max(h[i] for i in ix) / min(l[i] for i in ix) - 1), 2),
        "net_change_pct": round(100 * (cc[-1] / cc[0] - 1), 2), "max_drawdown_pct": round(100 * mdd, 2)}
pub = next(r for r in csv.DictReader(open(f"{sys.argv[2]}/case_comparison.csv", encoding="utf-8")) if r["case_id"] == "C01" and r["window"] == "full_months")
print("C01 independent", mine)
print("C01 published  ", {"days": int(pub["trading_days"]), "close_range_pct": float(pub["close_range_pct"]), "intraday_amplitude_pct": float(pub["intraday_amplitude_pct"]),
                          "net_change_pct": float(pub["net_change_pct"]), "max_drawdown_pct": float(pub["max_drawdown_pct"])})

# 2. phase loop
state, run_min, prev_breach, phase = "N", None, False, []
reb_left = 0
for t in range(len(c)):
    pk = max(c[max(0, t - 62):t + 1]); br = c[t] <= 0.9 * pk
    lab = None
    if state == "D":
        if c[t] >= 1.1 * run_min:
            state = "N"; reb_left = 21
        else:
            run_min = min(run_min, c[t]); lab = "DRAWDOWN"
    elif t >= 62 and br and not prev_breach:
        state = "D"; run_min = c[t]; lab = "DRAWDOWN"; reb_left = 0
    if lab is None and reb_left > 0:
        lab = "REBOUND"; reb_left -= 1
    phase.append(lab); prev_breach = br
n = up = 0
base_n = base_up = 0
for t in range(len(c) - 10):
    if d[t] < "2024-01-01":
        continue
    r = c[t + 10] / o[t + 1] - 1
    base_n += 1; base_up += r > 0.001
    if phase[t] == "DRAWDOWN":
        n += 1; up += r > 0.001
print("TEST/DRAWDOWN/h10 independent: up", up, "of", n, "=", round(up / n, 4), "| TEST all-days base", base_up, "of", base_n, "=", round(base_up / base_n, 4))
for r in csv.DictReader(open(f"{sys.argv[2]}/out_of_sample_probabilities.csv", encoding="utf-8")):
    if r["table"] == "price_only_base_rate" and r["h"] == "10" and r["split"] == "TEST" and r["state"] in ("DRAWDOWN", "ALL"):
        print("published", r["state"], "up", r["up"], "of", r["signal_days"], "=", r["p_up"])
