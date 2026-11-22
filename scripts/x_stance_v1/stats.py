"""Conditional statistics for every state x horizon x return kind, next to the unconditional figure of the same
range, with moving-block-bootstrap intervals over the session sequence (forward returns overlap, so neither days
nor episode starts are treated as independent). Nothing is selected: every declared cell is emitted, including the
ones that cannot be estimated, and "no relationship" is a normal outcome.

Verdicts here are about estimability only. A cell is INCONCLUSIVE when the series is shorter than two blocks or
the state has too few days. Significance, if read at all, must use the Holm-adjusted values over the whole family.
"""
from __future__ import annotations

import numpy as np

from .config import HORIZONS, LABELS

KINDS = ("desc_return", "next_open_return")


def split(rows: list[dict], *, holdout_start: str | None, purge_sessions: int = 30) -> dict:
    """rows are in session order. Development rows whose 30-session label would reach into the holdout are purged.
    Without a frozen holdout start nothing is called out-of-sample."""
    if holdout_start is None:
        return {"status": "HOLDOUT_NOT_FROZEN", "dev": list(rows), "holdout": [], "purged": 0, "label": "EXPLORATORY_UNSPLIT"}
    first = next((i for i, r in enumerate(rows) if r["session_date"] >= holdout_start), len(rows))
    cut = max(0, first - purge_sessions)
    return {"status": "SPLIT", "dev": rows[:cut], "holdout": rows[first:], "purged": first - cut, "label": "DEV_AND_HOLDOUT"}


def _blocks(t: int, block: int, reps: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = -(-t // block)
    starts = rng.integers(0, t - block + 1, size=(reps, n))
    return (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(reps, n * block)[:, :t]


def _desc(x: np.ndarray) -> dict:
    if not len(x):
        return {"mean": None, "median": None, "up_share": None, "quantiles": None}
    q = np.quantile(x, [0.05, 0.25, 0.75, 0.95])
    return {"mean": float(x.mean()), "median": float(np.median(x)), "up_share": float((x > 0).mean()),
            "quantiles": {"p05": float(q[0]), "p25": float(q[1]), "p75": float(q[2]), "p95": float(q[3])}}


def conditional(rows: list[dict], *, block: int, reps: int, seed: int, min_state_days: int) -> list[dict]:
    """rows: one per exchange session of the range, in order: session_date, state (None/UNKNOWN allowed),
    is_episode_start, and per kind a dict h -> value or None."""
    t = len(rows)
    states = np.array([r.get("state") if r.get("state") in LABELS else "" for r in rows])
    starts = np.array([bool(r.get("is_episode_start")) for r in rows])
    idx = _blocks(t, block, reps, seed) if t >= 2 * block else None
    out = []
    for kind in KINDS:
        for h in HORIZONS:
            y = np.array([np.nan if (r.get(kind) or {}).get(h) is None else (r[kind][h]) for r in rows], dtype=float)
            ok = ~np.isnan(y)
            unc = _desc(y[ok])
            for s in LABELS:
                in_state = states == s
                m = in_state & ok
                cell = {"kind": kind, "h": h, "state": s, "n_dates": int(m.sum()), "n_episode_starts": int((m & starts).sum()),
                        "state_days_without_return": int((in_state & ~ok).sum()), **_desc(y[m]),
                        "episode_start_mean": (float(y[m & starts].mean()) if (m & starts).any() else None),
                        "unconditional": {"n_dates": int(ok.sum()), **unc},
                        "difference_from_unconditional_mean": None, "mean_ci95": None, "difference_ci95": None, "p_boot_two_sided": None,
                        "p_holm": None, "status": "ESTIMATED", "status_reason": None, "block_sessions": block, "bootstrap_repetitions": reps, "seed": seed}
                if m.any() and ok.any():
                    cell["difference_from_unconditional_mean"] = cell["mean"] - unc["mean"]
                if idx is None:
                    cell["status"], cell["status_reason"] = "INCONCLUSIVE", "SERIES_SHORTER_THAN_TWO_BLOCKS"
                elif m.sum() < min_state_days:
                    cell["status"], cell["status_reason"] = "INCONCLUSIVE", "TOO_FEW_STATE_DAYS"
                else:
                    yb, mb, okb = y[idx], m[idx], ok[idx]
                    with np.errstate(invalid="ignore", divide="ignore"):
                        cm = np.where(mb, yb, 0.0).sum(axis=1) / mb.sum(axis=1)
                        um = np.where(okb, yb, 0.0).sum(axis=1) / okb.sum(axis=1)
                    d = cm - um
                    good = np.isfinite(d)
                    if good.sum() < 0.9 * reps:
                        cell["status"], cell["status_reason"] = "INCONCLUSIVE", "STATE_ABSENT_IN_MANY_RESAMPLES"
                    else:
                        cell["mean_ci95"] = [float(v) for v in np.quantile(cm[good], [0.025, 0.975])]
                        cell["difference_ci95"] = [float(v) for v in np.quantile(d[good], [0.025, 0.975])]
                        p = 2 * min((d[good] <= 0).mean(), (d[good] >= 0).mean())
                        cell["p_boot_two_sided"] = float(min(1.0, max(p, 1.0 / (good.sum() + 1))))
                out.append(cell)
    holm([c for c in out if c["p_boot_two_sided"] is not None])
    return out


def holm(cells: list[dict]) -> None:
    """Holm step-down over every estimated cell of the declared family."""
    order = sorted(cells, key=lambda c: c["p_boot_two_sided"])
    running, m = 0.0, len(order)
    for i, c in enumerate(order):
        running = max(running, min(1.0, (m - i) * c["p_boot_two_sided"]))
        c["p_holm"] = running


def with_controls(rows: list[dict], *, kind: str, h: int, block: int, reps: int, seed: int, min_state_days: int) -> dict:
    """return ~ const + bullish + bearish + ret_1 + ret_5 + ret_20 + rv_20 (neutral is the baseline state; days
    with an unknown state are left out). Asks whether the state says anything beyond recent returns and volatility."""
    names = ["const", "bullish", "bearish", "ret_1", "ret_5", "ret_20", "rv_20"]
    X, y, pos = [], [], []
    for i, r in enumerate(rows):
        v, c = (r.get(kind) or {}).get(h), r.get("controls") or {}
        if v is None or r.get("state") not in LABELS or any(c.get(k) is None for k in names[3:]):
            continue
        X.append([1.0, float(r["state"] == "bullish"), float(r["state"] == "bearish")] + [c[k] for k in names[3:]]); y.append(v); pos.append(i)
    out = {"kind": kind, "h": h, "n": len(y), "terms": names, "coef": None, "ci95": None, "status": "ESTIMATED", "status_reason": None}
    X, y, pos = np.array(X, dtype=float).reshape(-1, len(names)), np.array(y, dtype=float), np.array(pos)
    n_state = min(int(X[:, 1].sum()), int(X[:, 2].sum())) if len(y) else 0
    if len(rows) < 2 * block or n_state < min_state_days or np.linalg.matrix_rank(X) < len(names):
        out["status"], out["status_reason"] = "INCONCLUSIVE", "TOO_FEW_ROWS_OR_STATE_DAYS_OR_COLLINEAR"
        return out
    out["coef"] = dict(zip(names, (float(v) for v in np.linalg.lstsq(X, y, rcond=None)[0])))
    where = np.full(len(rows), -1); where[pos] = np.arange(len(pos))
    boots = []
    for draw in _blocks(len(rows), block, reps, seed):
        sel = where[draw]; sel = sel[sel >= 0]
        if len(sel) > len(names) and np.linalg.matrix_rank(X[sel]) == len(names):
            boots.append(np.linalg.lstsq(X[sel], y[sel], rcond=None)[0])
    if len(boots) < 0.9 * reps:
        out["status"], out["status_reason"] = "INCONCLUSIVE", "UNSTABLE_UNDER_RESAMPLING"
        return out
    lo, hi = np.quantile(np.array(boots), [0.025, 0.975], axis=0)
    out["ci95"] = {n: [float(a), float(b)] for n, a, b in zip(names, lo, hi)}
    return out


def study_gate(*, collection_receipt: bool, label_quality_receipt: bool, holdout_frozen: bool, classifier_pinned: bool, price_input_frozen: bool) -> dict:
    """The relationship study may be read as more than exploratory only when every input below is in place."""
    missing = [n for n, ok in (("COLLECTION_QUALITY_RECEIPT", collection_receipt), ("LABEL_QUALITY_RECEIPT", label_quality_receipt),
                              ("HOLDOUT_FROZEN", holdout_frozen), ("CLASSIFIER_PINNED", classifier_pinned), ("PRICE_INPUT_FROZEN", price_input_frozen)) if not ok]
    return {"verdict": "INCONCLUSIVE" if missing else "READY_TO_EVALUATE", "missing": missing}
