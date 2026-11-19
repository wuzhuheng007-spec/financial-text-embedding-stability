"""Independent re-computation audit of a research run (verifier v2).

Independence: this module does NOT import engine.py, worker.py or verify.py. It re-reads the
immutable snapshot (whose hash must match the registry), re-validates it, recomputes the factor,
signals, positions, execution timing, overnight/intraday returns, costs, the buy-and-hold
benchmark and every metric from the frozen config, then compares ALL of it with the run files:
each ledger cell, every scenario/segment/full_period metric, scenario set, row set, the results
digest, and the bindings between registry and files (dataset hash, config hash, factor
implementation fingerprint, code version, image id).

What it proves: the saved outputs equal an independent implementation of the written protocol
on the registered input. What it does not prove: that the vendor data are correct, that the
protocol is a good strategy, or anything beyond this one run.

Usage: python -m scripts.quant_research_v1.audit RUN_ID [--root DIR] [--out FILE]
"""
from __future__ import annotations

import argparse
import ast
import csv
import datetime as dt
import hashlib
import io
import json
import math
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

VERIFIER_VERSION = "audit-v2.0"
LEDGER_COLUMNS = ("date", "prev_close", "open", "close", "factor_prev_close", "signal_from_prev_close",
                  "position_overnight", "position_after_open", "exec_price", "turnover", "cost",
                  "gross_return", "net_return", "nav", "factor_close", "target_next_open",
                  "benchmark_position", "benchmark_turnover", "benchmark_net_return", "benchmark_nav")
METRIC_KEYS = ("total_return", "cagr", "ann_vol", "sharpe_rf0", "max_drawdown", "trades", "turnover_sum", "exposure", "days", "reason")
RTOL = 1e-10


class AuditFail(Exception):
    pass


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _canon(o) -> str:
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _finite(x: str, where: str) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        raise AuditFail(f"{where}: not a number {x!r}") from None
    if not math.isfinite(v):
        raise AuditFail(f"{where}: non-finite value {x!r}")
    return v


def _close(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, str) or isinstance(b, str):
        return a == b
    return abs(a - b) <= RTOL * max(1.0, abs(a), abs(b))


# ---------- snapshot (own parser and validation) ----------
def read_snapshot(path: Path) -> list[tuple]:
    text = path.read_text(encoding="utf-8")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or tuple(rows[0]) != ("date", "open", "high", "low", "close"):
        raise AuditFail("snapshot header mismatch")
    out, prev = [], None
    for n, r in enumerate(rows[1:], 2):
        if len(r) != 5:
            raise AuditFail(f"snapshot line {n}: {len(r)} fields")
        try:
            d = dt.date.fromisoformat(r[0])
        except ValueError:
            raise AuditFail(f"snapshot line {n}: invalid date {r[0]!r}") from None
        if prev is not None and d <= prev:
            raise AuditFail(f"snapshot line {n}: date not strictly increasing")
        o, h, l, c = (_finite(x, f"snapshot line {n}") for x in r[1:])
        if min(o, h, l, c) <= 0 or l > min(o, c) or h < max(o, c):
            raise AuditFail(f"snapshot line {n}: invalid OHLC")
        out.append((r[0], o, h, l, c)); prev = d
    return out


# ---------- independent simulation ----------
def simulate(bars, cfg, bps):
    lb = int(cfg["factor"]["params"]["lookback_days"]); warm = int(cfg["warmup_trading_days"])
    closes = [b[4] for b in bars]
    mom = [None if i < lb else closes[i] / closes[i - lb] - 1.0 for i in range(len(bars))]
    rate = bps / 10000.0
    rows, nav, bnav, held, bheld = [], 1.0, 1.0, 0.0, 0.0
    for i in range(warm + 1, len(bars)):
        d, o, _h, _l, c = bars[i]; pc = bars[i - 1][4]
        fprev = mom[i - 1]
        want = 1.0 if (fprev is not None and fprev > 0) else 0.0          # decided at the previous close
        on, intra = o / pc - 1.0, c / o - 1.0                               # traded at today's open
        turn = abs(want - held); cost = turn * rate
        new_nav = nav * (1 + held * on) * (1 - cost) * (1 + want * intra)
        bturn = abs(1.0 - bheld)
        new_bnav = bnav * (1 + bheld * on) * (1 - bturn * rate) * (1 + intra)
        ftoday = mom[i]
        rows.append({"date": d, "prev_close": pc, "open": o, "close": c, "factor_prev_close": fprev,
                     "signal_from_prev_close": want, "position_overnight": held, "position_after_open": want,
                     "exec_price": o, "turnover": turn, "cost": cost,
                     "gross_return": (1 + held * on) * (1 + want * intra) - 1, "net_return": new_nav / nav - 1,
                     "nav": new_nav, "factor_close": ftoday, "target_next_open": 1.0 if (ftoday is not None and ftoday > 0) else 0.0,
                     "benchmark_position": 1.0, "benchmark_turnover": bturn, "benchmark_net_return": new_bnav / bnav - 1,
                     "benchmark_nav": new_bnav})
        nav, bnav, held, bheld = new_nav, new_bnav, want, 1.0
    return rows


def stats(rets, pos, turns, cfg):
    n = len(rets); ann = cfg["metrics"]["annualization_days"]
    out = {"days": n, "trades": sum(1 for t in turns if t > 0), "turnover_sum": sum(turns), "exposure": sum(pos) / n if n else None}
    if n < cfg["metrics"]["min_days_for_metrics"]:
        return {**out, "total_return": None, "cagr": None, "ann_vol": None, "sharpe_rf0": None, "max_drawdown": None, "reason": "INSUFFICIENT_SAMPLE"}
    v, peak, mdd = 1.0, 1.0, 0.0
    for r in rets:
        v *= 1 + r; peak = max(peak, v); mdd = min(mdd, v / peak - 1)
    mu = sum(rets) / n; sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / (n - 1))
    return {**out, "total_return": v - 1, "cagr": v ** (ann / n) - 1, "ann_vol": sd * math.sqrt(ann),
            "sharpe_rf0": mu / sd * math.sqrt(ann) if sd > 0 else None, "max_drawdown": mdd, "reason": None}


def expected_metrics(rows, cfg):
    def block(sel):
        return {"strategy": stats([r["net_return"] for r in sel], [r["position_after_open"] for r in sel], [r["turnover"] for r in sel], cfg),
                "benchmark": stats([r["benchmark_net_return"] for r in sel], [1.0] * len(sel), [r["benchmark_turnover"] for r in sel], cfg)}
    segs = []
    for s in cfg["segments"]:
        sel = [r for r in rows if s["start"] <= r["date"] <= s["end"]]
        segs.append({"name": s["name"], "start": sel[0]["date"] if sel else None, "end": sel[-1]["date"] if sel else None, **block(sel)})
    return segs, block(rows)


def parse_ledger(text: str, where: str) -> list[dict]:
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or tuple(rows[0]) != LEDGER_COLUMNS:
        raise AuditFail(f"{where}: ledger columns differ")
    out, seen = [], set()
    for n, r in enumerate(rows[1:], 2):
        if len(r) != len(LEDGER_COLUMNS):
            raise AuditFail(f"{where} line {n}: {len(r)} fields")
        rec = {"date": r[0]}
        if r[0] in seen:
            raise AuditFail(f"{where} line {n}: duplicate date {r[0]}")
        seen.add(r[0])
        for k, v in zip(LEDGER_COLUMNS[1:], r[1:]):
            rec[k] = None if (v == "" and k in ("factor_prev_close", "factor_close")) else _finite(v, f"{where} line {n} {k}")
        out.append(rec)
    return out


def factor_source_fingerprint(repo_root: Path) -> str:
    """Fingerprint of engine.factor_mom computed from the source text (the function is NOT executed)."""
    src = (repo_root / "scripts/quant_research_v1/engine.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "factor_mom")
    lines = src.splitlines(keepends=True)
    return _sha("".join(lines[node.lineno - 1:node.end_lineno]).encode("utf-8"))


def audit_run(root: Path, run_id: str, repo_root: Path | None = None) -> dict:
    repo_root = repo_root or Path(__file__).resolve().parents[2]
    checks, problems = {}, []

    def check(name, ok, detail=None):
        checks[name] = {"ok": bool(ok), **({"detail": detail} if detail is not None else {})}
        if not ok:
            problems.append(f"{name}: {detail}")

    with closing(sqlite3.connect((root / "registry.sqlite3").resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        run = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if run is None:
            raise AuditFail("run not in registry")
        ds = db.execute("SELECT * FROM datasets WHERE dataset_id=? AND dataset_version=?", (run["dataset_id"], run["dataset_version"])).fetchone()
        fac = db.execute("SELECT * FROM factors WHERE factor_id=? AND factor_version=?", (run["factor_id"], run["factor_version"])).fetchone()
        cols = {r[1] for r in db.execute("PRAGMA table_info(factors)")}
    rd = root / "runs" / run_id
    result = {"verifier_version": VERIFIER_VERSION, "run_id": run_id, "audited_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "registry_status": run["status"]}
    try:
        cfg_text = (rd / "config.json").read_text(encoding="utf-8"); cfg = json.loads(cfg_text)
        metrics = json.loads((rd / "metrics.json").read_text(encoding="utf-8"))
        # ---- bindings: registry vs files ----
        snap = root / ds["snapshot_path"] / "data.csv"
        snap_sha = _sha(snap.read_bytes())
        check("dataset_hash_file_eq_registry", snap_sha == ds["content_sha256"], f"{snap_sha} vs {ds['content_sha256']}")
        check("dataset_hash_metrics_eq_registry", metrics["dataset"]["content_sha256"] == ds["content_sha256"])
        check("dataset_ids_match", (metrics["dataset"]["dataset_id"], metrics["dataset"]["dataset_version"]) == (run["dataset_id"], run["dataset_version"]))
        cfg_sha = _sha(_canon(cfg).encode("utf-8"))
        check("config_hash_file_eq_registry", cfg_sha == run["config_sha256"], f"{cfg_sha} vs {run['config_sha256']}")
        check("config_hash_metrics_eq_registry", metrics["protocol"]["config_sha256"] == run["config_sha256"])
        check("config_json_eq_registry_config", _canon(json.loads(run["config_json"])) == _canon(cfg))
        check("factor_ids_match", (metrics["factor"]["factor_id"], metrics["factor"]["factor_version"]) == (run["factor_id"], run["factor_version"])
              and (cfg["factor"]["factor_id"], cfg["factor"]["factor_version"]) == (run["factor_id"], run["factor_version"]))
        check("factor_params_eq_registry", _canon(metrics["factor"]["params"]) == fac["params_json"] == _canon(cfg["factor"]["params"]))
        src_fp = factor_source_fingerprint(repo_root)
        reg_fp = fac["impl_sha256"] if "impl_sha256" in cols else None
        run_fp = metrics["factor"].get("impl_sha256")
        checks["factor_impl_fingerprint"] = {"ok": True, "source": src_fp, "registry": reg_fp, "run": run_fp or "NOT_RECORDED_LEGACY_RUN"}
        if reg_fp is not None and reg_fp != src_fp:
            check("factor_impl_registry_eq_source", False, f"{reg_fp} vs {src_fp}")
        if run_fp is not None:
            check("factor_impl_run_eq_registry", run_fp == reg_fp, f"{run_fp} vs {reg_fp}")
        rt = metrics.get("runtime") or {}
        checks["code_and_image"] = {"ok": True, "registry_code_version": run["code_version"],
                                    "run_code_version": rt.get("code_version", "NOT_RECORDED_LEGACY_RUN"),
                                    "registry_image_id": run["image_id"] if "image_id" in run.keys() else None,
                                    "run_image_id": rt.get("image_id", "NOT_RECORDED_LEGACY_RUN")}
        if rt.get("code_version") and run["code_version"]:
            check("code_version_run_eq_registry", rt["code_version"] == run["code_version"])
        if rt.get("image_id") and "image_id" in run.keys() and run["image_id"]:
            check("image_id_run_eq_registry", rt["image_id"] == run["image_id"])
        # ---- independent recomputation ----
        bars = read_snapshot(snap)
        costs = cfg["costs"]["scenarios_bps_one_way"]
        check("scenario_set_eq_config", sorted(s["cost_bps"] for s in metrics["scenarios"]) == sorted(costs),
              f"{[s['cost_bps'] for s in metrics['scenarios']]} vs {costs}")
        ledger_files = sorted(p.name for p in rd.glob("ledger_cost*bps.csv"))
        check("ledger_file_set_eq_config", ledger_files == sorted(f"ledger_cost{int(b)}bps.csv" for b in costs), ledger_files)
        ledgers = {}
        scen_report = {}
        for bps in costs:
            name = f"ledger_cost{int(bps)}bps.csv"
            text = (rd / name).read_text(encoding="utf-8"); ledgers[name] = text
            got = parse_ledger(text, name)
            exp = simulate(bars, cfg, bps)
            bad_cells = []
            if len(got) != len(exp):
                bad_cells.append(f"rows {len(got)} vs expected {len(exp)}")
            for g, e in zip(got, exp):
                for k in LEDGER_COLUMNS:
                    if not _close(g[k], e[k]):
                        bad_cells.append(f"{e['date']} {k}: file {g[k]!r} vs independent {e[k]!r}")
                        if len(bad_cells) > 20:
                            break
                if len(bad_cells) > 20:
                    break
            segs, full = expected_metrics(exp, cfg)
            ms = next((s for s in metrics["scenarios"] if s["cost_bps"] == bps), None)
            bad_metrics = []
            if ms is None:
                bad_metrics.append("scenario missing in metrics.json")
            else:
                if [s["name"] for s in ms["segments"]] != [s["name"] for s in segs]:
                    bad_metrics.append("segment list differs")
                for want, have in [(s, h) for s, h in zip(segs, ms["segments"])] + [({"name": "full_period", **full}, {"name": "full_period", **ms["full_period"]})]:
                    for k in ("start", "end"):
                        if k in want and want.get(k) != have.get(k):
                            bad_metrics.append(f"{want['name']}.{k}: {have.get(k)} vs {want.get(k)}")
                    for side in ("strategy", "benchmark"):
                        for k in METRIC_KEYS:
                            if not _close(have[side].get(k), want[side].get(k)):
                                bad_metrics.append(f"{want['name']}.{side}.{k}: file {have[side].get(k)!r} vs independent {want[side].get(k)!r}")
                for k, v in (("rows", len(exp)), ("first_trade_date", exp[0]["date"]), ("last_date", exp[-1]["date"])):
                    if ms.get(k) != v:
                        bad_metrics.append(f"{k}: {ms.get(k)} vs {v}")
            scen_report[str(bps)] = {"ledger_rows": len(got), "expected_rows": len(exp), "ledger_mismatches": bad_cells[:20],
                                     "metric_mismatches": bad_metrics[:40]}
            check(f"ledger_{int(bps)}bps_eq_independent", not bad_cells, bad_cells[:3] or None)
            check(f"metrics_{int(bps)}bps_eq_independent", not bad_metrics, bad_metrics[:3] or None)
        result["scenarios"] = scen_report
        # ---- results digest: same definition, recomputed from the files ----
        h = hashlib.sha256(); h.update(_canon(metrics["scenarios"]).encode())
        for k in sorted(ledgers):
            h.update(k.encode()); h.update(hashlib.sha256(ledgers[k].encode()).digest())
        dig = h.hexdigest()
        check("results_digest_files_eq_metrics", dig == metrics["results_digest"], f"{dig} vs {metrics['results_digest']}")
        check("results_digest_eq_registry", run["results_digest"] is None and run["status"] != "SUCCEEDED" or dig == run["results_digest"],
              f"{dig} vs {run['results_digest']}")
        result["results_digest_recomputed"] = dig
    except AuditFail as exc:
        check("parse_and_validate", False, str(exc))
    except (OSError, KeyError, ValueError, TypeError) as exc:
        check("files_readable", False, f"{type(exc).__name__}: {exc}")
    result["checks"] = checks
    result["problems"] = problems
    result["ok"] = not problems
    result["scope"] = ("Independent re-implementation of protocol + bindings for this run only; "
                       "does not validate vendor prices or strategy merit.")
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id"); ap.add_argument("--root"); ap.add_argument("--out")
    a = ap.parse_args(argv)
    import os
    root = Path(a.root or os.environ.get("QUANT_RESEARCH_ROOT") or Path(__file__).resolve().parents[2] / "apps/quant_portal/var/research")
    res = audit_run(root, a.run_id)
    text = json.dumps(res, ensure_ascii=False, indent=1)
    if a.out:
        out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists():
            raise SystemExit("refusing to overwrite an existing audit file")
        out.write_text(text, encoding="utf-8")
    print(text)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
