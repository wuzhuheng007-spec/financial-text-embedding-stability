"""Research job worker and admin CLI.

  worker --once [--max-seconds 300]     claim one QUEUED run and execute it (job container only)
  snapshot --source SQLITE --symbol US.QQQ --autype 1 --dataset-id ID --dataset-version V --receipt JSON
  register-snapshot datasets/ID/VER     register an uploaded frozen snapshot (hash-checked)
  register-defaults                     register built-in factors
  enqueue --dataset ID@V --factor ID@V --protocol CONFIG.json
  verify RUN_ID
Runs never write outside runs/<run_id>/ and never overwrite an existing directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sys
import traceback
from pathlib import Path

from . import registry
from .engine import FACTORS, backtest, build_snapshot, ledger_csv, load_snapshot


class RunTimeout(Exception):
    pass


class RunCancelled(Exception):
    pass


def results_digest(scenarios: list[dict], ledgers: dict[str, str]) -> str:
    """Content digest excluding timestamps and run ids: metrics + exact ledger text."""
    h = hashlib.sha256()
    h.update(registry.canonical(scenarios).encode())
    for k in sorted(ledgers):
        h.update(k.encode()); h.update(hashlib.sha256(ledgers[k].encode()).digest())
    return h.hexdigest()


def execute(db, run, research_root: Path) -> dict:
    ds = db.execute("SELECT * FROM datasets WHERE dataset_id=? AND dataset_version=?",
                    (run["dataset_id"], run["dataset_version"])).fetchone()
    spec = FACTORS[(run["factor_id"], run["factor_version"])]
    cfg = json.loads(run["config_json"])
    out = research_root / "runs" / run["run_id"]
    out.mkdir(parents=True, exist_ok=False)
    with open(out / "run.log", "w", encoding="utf-8") as log:
        return _execute(db, run, research_root, ds, spec, cfg, out, log)


def _execute(db, run, research_root, ds, spec, cfg, out, log) -> dict:
    def say(msg):
        log.write(f"{registry.utcnow()} {msg}\n"); log.flush()
    say(f"run {run['run_id']} dataset {ds['dataset_id']}@{ds['dataset_version']} factor {spec['factor_id']}@{spec['factor_version']}")
    bars = load_snapshot(research_root / ds["snapshot_path"], ds["content_sha256"])
    say(f"snapshot verified sha256={ds['content_sha256']} rows={len(bars)} {bars[0]['date']}..{bars[-1]['date']}")
    params = json.loads(registry.canonical(cfg["factor"]["params"]))
    if params != spec["params"]:
        raise ValueError("protocol parameters differ from the registered factor version")
    fac = spec["fn"](bars, **params)
    (out / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    scenarios, ledgers = [], {}
    for bps in cfg["costs"]["scenarios_bps_one_way"]:
        if db.execute("SELECT cancel_requested FROM runs WHERE run_id=?", (run["run_id"],)).fetchone()[0]:
            raise RunCancelled()
        res = backtest(bars, fac, cfg, bps)
        text = ledger_csv(res["ledger"])
        name = f"ledger_cost{int(bps)}bps.csv"
        (out / name).write_text(text, encoding="utf-8")
        ledgers[name] = text
        scenarios.append({"cost_bps": bps, "segments": res["segments"], "full_period": res["full_period"],
                          "first_trade_date": res["ledger"][0]["date"], "last_date": res["ledger"][-1]["date"],
                          "rows": len(res["ledger"])})
        say(f"cost {bps} bps: {len(res['ledger'])} ledger rows written")
    # Equity curves for the page (downsampled to <= 600 points per series; ledger keeps every day).
    step = max(1, len(res["ledger"]) // 600)
    curves = {}
    for bps in cfg["costs"]["scenarios_bps_one_way"]:
        rows = [r.split(",") for r in ledgers[f"ledger_cost{int(bps)}bps.csv"].splitlines()[1:]]
        idx = {k: i for i, k in enumerate(ledgers[f"ledger_cost{int(bps)}bps.csv"].splitlines()[0].split(","))}
        pick = [r for i, r in enumerate(rows) if i % step == 0 or i == len(rows) - 1]
        curves[str(bps)] = {"date": [r[idx["date"]] for r in pick], "nav": [float(r[idx["nav"]]) for r in pick],
                            "benchmark_nav": [float(r[idx["benchmark_nav"]]) for r in pick]}
    digest = results_digest(scenarios, ledgers)
    metrics = {"run_id": run["run_id"], "dataset": {k: ds[k] for k in ("dataset_id", "dataset_version", "content_sha256", "start_date", "end_date")},
               "factor": {k: spec[k] for k in ("factor_id", "factor_version", "params")},
               "protocol": {"protocol_id": cfg["protocol_id"], "protocol_version": cfg["protocol_version"], "config_sha256": run["config_sha256"]},
               "return_label": cfg["price_series"]["return_label"], "scenarios": scenarios, "results_digest": digest}
    (out / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "curves.json").write_text(json.dumps(curves), encoding="utf-8")
    from .verify import verify_run
    check = verify_run(out)
    (out / "verify.json").write_text(json.dumps(check, ensure_ascii=False, indent=1), encoding="utf-8")
    say(f"independent recomputation ok={check['ok']}; results_digest={digest}")
    if not check["ok"]:
        raise ValueError("independent recomputation mismatch")
    summary = {"results_digest": digest, "verify_ok": True, "return_label": metrics["return_label"],
               "rows": scenarios[0]["rows"], "first_trade_date": scenarios[0]["first_trade_date"], "last_date": scenarios[0]["last_date"],
               "headline": [{"cost_bps": s["cost_bps"], "segments": [{"name": g["name"], "strategy_total_return": g["strategy"]["total_return"],
                             "benchmark_total_return": g["benchmark"]["total_return"]} for g in s["segments"]]} for s in scenarios]}
    return {"artifact_dir": f"runs/{run['run_id']}", "results_digest": digest, "summary_json": json.dumps(summary)}


def run_once(max_seconds: int) -> int:
    research_root = registry.root()
    db = registry.connect()
    run = registry.claim_next(db)
    if run is None:
        db.close(); print("no queued run"); return 0
    def on_alarm(*_):
        raise RunTimeout()
    if hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, on_alarm); signal.alarm(max_seconds)
    try:
        fields = execute(db, run, research_root)
        registry.finish(db, run["run_id"], "SUCCEEDED", code_version=os.environ.get("QUANT_CODE_VERSION") or run["code_version"], **fields)
        print(f"{run['run_id']} SUCCEEDED {fields['results_digest']}"); return 0
    except RunCancelled:
        registry.finish(db, run["run_id"], "CANCELLED", error_class="CANCELLED_BY_OWNER"); return 0
    except RunTimeout:
        registry.finish(db, run["run_id"], "FAILED", error_class="TIMEOUT", error_detail=f"exceeded {max_seconds}s"); return 1
    except Exception as exc:  # noqa: BLE001 - a failed run is recorded, never crashes collection
        registry.finish(db, run["run_id"], "FAILED", error_class=type(exc).__name__, error_detail=str(exc)[:500],
                        artifact_dir=f"runs/{run['run_id']}")
        traceback.print_exc(); return 1
    finally:
        if hasattr(signal, "SIGALRM"):
            signal.alarm(0)
        db.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("worker"); w.add_argument("--once", action="store_true", required=True); w.add_argument("--max-seconds", type=int, default=300)
    s = sub.add_parser("snapshot")
    for a in ("--source", "--symbol", "--dataset-id", "--dataset-version", "--title", "--receipt", "--audit-status"):
        s.add_argument(a, required=True)
    s.add_argument("--autype", type=int, required=True); s.add_argument("--currency", default=None)
    sub.add_parser("register-defaults")
    rs = sub.add_parser("register-snapshot"); rs.add_argument("rel_dir", help="datasets/<id>/<version> under the research root")
    e = sub.add_parser("enqueue"); e.add_argument("--dataset", required=True); e.add_argument("--factor", required=True); e.add_argument("--protocol", required=True)
    v = sub.add_parser("verify"); v.add_argument("run_id")
    a = ap.parse_args(argv)
    if a.cmd == "worker":
        return run_once(a.max_seconds)
    db = registry.connect()
    if a.cmd == "snapshot":
        rel = Path("datasets") / a.dataset_id / a.dataset_version
        m = build_snapshot(Path(a.source), a.symbol, a.autype, registry.root() / rel, dataset_id=a.dataset_id,
                           dataset_version=a.dataset_version, title=a.title, audit_status=a.audit_status,
                           source_receipt=json.loads(Path(a.receipt).read_text(encoding="utf-8")))
        if a.currency:
            m["currency"] = a.currency
            (registry.root() / rel / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
        registry.register_dataset(db, m, rel.as_posix()); print(json.dumps({k: m[k] for k in ("dataset_id", "dataset_version", "rows", "content_sha256")}))
    elif a.cmd == "register-snapshot":
        from .engine import load_snapshot
        d = registry.root() / a.rel_dir
        m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        load_snapshot(d, m["content_sha256"])  # refuses a snapshot whose content does not match its manifest
        registry.register_dataset(db, m, Path(a.rel_dir).as_posix())
        print(json.dumps({k: m[k] for k in ("dataset_id", "dataset_version", "rows", "content_sha256")}))
    elif a.cmd == "register-defaults":
        for spec in FACTORS.values():
            registry.register_factor(db, spec)
        print("registered", [f"{k[0]}@{k[1]}" for k in FACTORS])
    elif a.cmd == "enqueue":
        ds, fv = tuple(a.dataset.split("@")), tuple(a.factor.split("@"))
        print(registry.create_run(db, ds, fv, json.loads(Path(a.protocol).read_text(encoding="utf-8")), requested_by="cli"))
    elif a.cmd == "verify":
        from .verify import verify_run
        out = verify_run(registry.root() / "runs" / a.run_id); print(json.dumps(out, indent=1)); return 0 if out["ok"] else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
