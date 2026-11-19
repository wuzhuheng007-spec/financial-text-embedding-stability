"""Frozen daily snapshots, registered factors and the chronological long/cash backtest."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import sqlite3
from contextlib import closing
from pathlib import Path

SNAPSHOT_FIELDS = ("date", "open", "high", "low", "close")


# ---------- snapshots ----------
def build_snapshot(source_sqlite: Path, symbol: str, autype: int, out_dir: Path, *, dataset_id: str,
                   dataset_version: str, title: str, source_receipt: dict, audit_status: str) -> dict:
    """Freeze one research_daily series into data.csv + manifest.json; refuses to overwrite."""
    out_dir.mkdir(parents=True, exist_ok=False)
    with closing(sqlite3.connect(Path(source_sqlite).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        rows = db.execute("""SELECT local_date, open, high, low, close, source_archive_sha256
            FROM research_daily WHERE symbol=? AND autype=? ORDER BY local_date""", (symbol, autype)).fetchall()
    if not rows:
        raise ValueError("no rows for series")
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(SNAPSHOT_FIELDS)
    for d, o, h, l, c, _ in rows:
        if None in (o, h, l, c):
            raise ValueError(f"missing OHLC on {d}")
        w.writerow([d, repr(float(o)), repr(float(h)), repr(float(l)), repr(float(c))])
    data = buf.getvalue().encode("utf-8")
    (out_dir / "data.csv").write_bytes(data)
    manifest = {
        "schema": "quant_research_dataset_v1", "dataset_id": dataset_id, "dataset_version": dataset_version,
        "title": title, "symbol": symbol, "frequency": "DAY",
        "adjustment": f"vendor autype a{autype}" + (" (forward-adjusted)" if autype == 1 else " (unadjusted)"),
        "currency": None,  # set only from explicit metadata (worker snapshot --currency)
        "start_date": rows[0][0], "end_date": rows[-1][0], "rows": len(rows),
        "fields": list(SNAPSHOT_FIELDS), "content_sha256": hashlib.sha256(data).hexdigest(),
        "source_archives_sha256": sorted({r[5] for r in rows}), "source_receipt": source_receipt,
        "calendar": "Trading days are the dates present in the vendor series after the audited research filter; consecutive rows are treated as consecutive trading days.",
        "audit_status": audit_status, "point_in_time": False,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest


def load_snapshot(snapshot_dir: Path, expected_sha256: str) -> list[dict]:
    data = (Path(snapshot_dir) / "data.csv").read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError("snapshot content hash mismatch")
    out = []
    for r in csv.DictReader(io.StringIO(data.decode("utf-8"))):
        out.append({"date": r["date"], **{k: float(r[k]) for k in ("open", "high", "low", "close")}})
    dates = [r["date"] for r in out]
    if dates != sorted(dates) or len(set(dates)) != len(dates):
        raise ValueError("snapshot dates not strictly increasing")
    return out


# ---------- factors (only registered entries can run; no user code) ----------
def factor_mom(bars: list[dict], lookback_days: int) -> list[float | None]:
    """close_t / close_{t-lookback} - 1 using only data up to t; None during warm-up."""
    out: list[float | None] = []
    for i, b in enumerate(bars):
        out.append(None if i < lookback_days else b["close"] / bars[i - lookback_days]["close"] - 1.0)
    return out


FACTORS = {
    ("mom20", "1"): {
        "factor_id": "mom20", "factor_version": "1", "title": "20 交易日价格动量",
        "entry": "scripts.quant_research_v1.engine:factor_mom", "params": {"lookback_days": 20},
        "input_requirements": {"frequency": "DAY", "fields": ["close"], "min_rows": 21},
        "output_definition": "close_t / close_{t-20} - 1, computed at the close of t from data up to t",
        "missing_rule": "None for the first 20 rows (warm-up); signal treats None as cash",
        "fn": factor_mom,
    },
}


# ---------- backtest ----------
def _metrics(net: list[float], positions: list[float], turnovers: list[float], cfg: dict) -> dict:
    n = len(net)
    ann = cfg["metrics"]["annualization_days"]
    base = {"days": n, "trades": sum(1 for t in turnovers if t > 0), "turnover_sum": sum(turnovers),
            "exposure": (sum(positions) / n) if n else None}
    if n < cfg["metrics"]["min_days_for_metrics"]:
        return {**base, "total_return": None, "cagr": None, "ann_vol": None, "sharpe_rf0": None,
                "max_drawdown": None, "reason": "INSUFFICIENT_SAMPLE"}
    nav, peak, mdd = 1.0, 1.0, 0.0
    for r in net:
        nav *= 1 + r
        peak = max(peak, nav)
        mdd = min(mdd, nav / peak - 1)
    mean = sum(net) / n
    sd = math.sqrt(sum((r - mean) ** 2 for r in net) / (n - 1))
    return {**base, "total_return": nav - 1, "cagr": nav ** (ann / n) - 1, "ann_vol": sd * math.sqrt(ann),
            "sharpe_rf0": (mean / sd * math.sqrt(ann)) if sd > 0 else None, "max_drawdown": mdd, "reason": None}


def backtest(bars: list[dict], factor: list[float | None], cfg: dict, cost_bps: float) -> dict:
    """Signal at close t -> trade at open t+1. Returns ledger rows and per-segment metrics."""
    warm = cfg["warmup_trading_days"]
    first = warm + 1  # first day whose open can execute a signal formed at a close with a factor
    if len(bars) <= first:
        raise ValueError("not enough rows after warm-up")
    c = cost_bps / 10000.0
    ledger, nav, bnav = [], 1.0, 1.0
    p_old, b_old = 0.0, 0.0
    for i in range(first, len(bars)):
        prev, bar = bars[i - 1], bars[i]
        f_prev = factor[i - 1]
        signal_prev = 1.0 if (f_prev is not None and f_prev > 0) else 0.0
        p_new = signal_prev
        r_on, r_id = bar["open"] / prev["close"] - 1, bar["close"] / bar["open"] - 1
        turnover = abs(p_new - p_old)
        cost = turnover * c
        gross = (1 + p_old * r_on) * (1 + p_new * r_id) - 1
        nav_new = nav * (1 + p_old * r_on) * (1 - cost) * (1 + p_new * r_id)
        net = nav_new / nav - 1
        b_turn = abs(1.0 - b_old)
        bnav_new = bnav * (1 + b_old * r_on) * (1 - b_turn * c) * (1 + 1.0 * r_id)
        f_today = factor[i]
        ledger.append({
            "date": bar["date"], "prev_close": prev["close"], "open": bar["open"], "close": bar["close"],
            "signal_from_prev_close": signal_prev, "factor_prev_close": f_prev,
            "factor_close": f_today, "target_next_open": 1.0 if (f_today is not None and f_today > 0) else 0.0,
            "position_overnight": p_old, "position_after_open": p_new, "exec_price": bar["open"],
            "turnover": turnover, "cost": cost, "gross_return": gross, "net_return": net, "nav": nav_new,
            "benchmark_position": 1.0, "benchmark_turnover": b_turn, "benchmark_net_return": bnav_new / bnav - 1,
            "benchmark_nav": bnav_new,
        })
        nav, bnav, p_old, b_old = nav_new, bnav_new, p_new, 1.0
    segments = []
    for seg in cfg["segments"]:
        rows = [r for r in ledger if seg["start"] <= r["date"] <= seg["end"]]
        segments.append({
            "name": seg["name"], "start": rows[0]["date"] if rows else None, "end": rows[-1]["date"] if rows else None,
            "strategy": _metrics([r["net_return"] for r in rows], [r["position_after_open"] for r in rows],
                                 [r["turnover"] for r in rows], cfg),
            "benchmark": _metrics([r["benchmark_net_return"] for r in rows], [1.0] * len(rows),
                                  [r["benchmark_turnover"] for r in rows], cfg),
        })
    full = {"strategy": _metrics([r["net_return"] for r in ledger], [r["position_after_open"] for r in ledger],
                                 [r["turnover"] for r in ledger], cfg),
            "benchmark": _metrics([r["benchmark_net_return"] for r in ledger], [1.0] * len(ledger),
                                  [r["benchmark_turnover"] for r in ledger], cfg)}
    return {"cost_bps": cost_bps, "ledger": ledger, "segments": segments, "full_period": full}


LEDGER_COLUMNS = ("date", "prev_close", "open", "close", "factor_prev_close", "signal_from_prev_close",
                  "position_overnight", "position_after_open", "exec_price", "turnover", "cost",
                  "gross_return", "net_return", "nav", "factor_close", "target_next_open",
                  "benchmark_position", "benchmark_turnover", "benchmark_net_return", "benchmark_nav")


def ledger_csv(ledger: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(LEDGER_COLUMNS)
    for r in ledger:
        w.writerow(["" if r[k] is None else (repr(r[k]) if isinstance(r[k], float) else r[k]) for k in LEDGER_COLUMNS])
    return buf.getvalue()
