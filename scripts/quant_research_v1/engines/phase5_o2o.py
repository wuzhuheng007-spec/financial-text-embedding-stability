"""phase5_o2o_v1 engine adapter: runs the FROZEN phase5 program as a child process and packages its outputs.

The frozen research/market_sentiment/phase5_strategy_v1/{run.py,check.py,config.json,PROTOCOL_zh.md} are the
authority for signals, execution (close t -> next open -> next open), post-fee target weights, cash/units drift,
per-period reset, episode-end liquidation fees and calendar-time CAGR. This adapter never reimplements or edits
them: it verifies their hashes, runs them, stops them on cancel/timeout/SIGTERM, and writes platform artifacts.

Inputs are read from a read-only snapshot (QUANT_RESEARCH_INPUT_ROOT when the job mounts one) whose bytes must
match the registered dataset hash. Exact mode is used only when that hash equals the frozen config's
expected_db_sha256; any other snapshot runs in the frozen program's 'external' mode and is labelled so.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import os
import platform
import subprocess
import sys
import tarfile
import time
from pathlib import Path

ENGINE = ("phase5_o2o_v1", "1")
REPO = Path(__file__).resolve().parents[3]
FROZEN_DIR = REPO / "research/market_sentiment/phase5_strategy_v1"
FROZEN_SHA256 = {"run.py": "09682c2161fe260d0df41328771368202fb350de5b4d92d68211b85a54604eae",
                 "check.py": "7195c6a39f9a348f4feeea765f1dbd02986d1878d8c4881ed9d5cab693d1e756",
                 "config.json": "0db1ebb476ec5db341658b6e564a2aa7dc8a279681a24ce781139d0c27d84efb",
                 "PROTOCOL_zh.md": "7281efccfa693831b623c45168b92bbda2a929970b20dfcccc749f7e8eb07a8a"}
FREEZE_COMMIT = "1cfe1acb9e82c5c22f2ec1122c41cc2dd1209953"
MKT = ("US.QQQ", "US..SOX", "SH.000001", "SH.000688")
STRATS = ("BUY_HOLD", "MA200", "P1_TREND", "P2_RECOVERY", "P3_RISK_BUDGET")
ARTIFACTS = ("resolved_config.json", "input_manifest.json", "code_manifest.json", "environment.json", "metrics.csv", "contrasts.csv",
             "factor_diagnostics.csv", "program_run_manifest.json", "private_output_hashes.json", "self_check.json", "mechanical_check.json",
             "science.json", "report.json", "signals_primary.csv.gz", "ledgers_all.tar", "program_stdout.log", "run.log")


def sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def capability() -> dict:
    """What this image can actually execute: libraries importable AND frozen files byte-identical."""
    out = {"engine": "@".join(ENGINE), "python": platform.python_version(), "ok": False}
    try:
        import numpy, pandas, scipy  # noqa: E401
        out["libs"] = {"numpy": numpy.__version__, "pandas": pandas.__version__, "scipy": scipy.__version__}
    except ImportError as exc:
        out["reason"] = f"MISSING_LIBRARY: {exc.name}"; return out
    actual = {n: sha_file(FROZEN_DIR / n) if (FROZEN_DIR / n).is_file() else None for n in FROZEN_SHA256}
    out["frozen_files"] = actual
    bad = [n for n, h in FROZEN_SHA256.items() if actual[n] != h]
    if bad:
        out["reason"] = f"FROZEN_FILE_MISMATCH: {bad}"; return out
    out["lockfile_sha256"] = os.environ.get("QUANT_NUMERIC_LOCK_SHA256")
    out["ok"] = True
    return out


def _child(cmd: list, cwd: Path, log, stop_now, poll: float = 0.5) -> tuple[int, float, float]:
    """Run the frozen program; stop_now() raises to cancel/timeout. The child is always terminated on exit."""
    t0 = time.monotonic()
    proc = subprocess.Popen(cmd, cwd=str(cwd), stdout=log, stderr=subprocess.STDOUT, env={**os.environ, "PYTHONHASHSEED": "0"})
    try:
        while proc.poll() is None:
            stop_now()
            time.sleep(poll)
        return proc.returncode, time.monotonic() - t0, _peak_child_rss_mb()
    finally:
        if proc.poll() is None:  # cancel / timeout / SIGTERM / any error: stop the child, never leave it running
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait(timeout=10)
            log.write(f"\n# child pid {proc.pid} stopped by the adapter (returncode {proc.returncode})\n"); log.flush()


def _peak_child_rss_mb():
    try:
        import resource
        return round(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024, 1)  # Linux: KiB
    except ImportError:  # Windows dev machine
        return None


def _deterministic_tar(src: Path, dst: Path):
    with tarfile.open(dst, "w", format=tarfile.PAX_FORMAT) as tar:
        for p in sorted(x for x in src.rglob("*") if x.is_file()):
            info = tar.gettarinfo(str(p), arcname=p.relative_to(src).as_posix())
            info.mtime = 0; info.uid = info.gid = 0; info.uname = info.gname = ""; info.mode = 0o644
            with open(p, "rb") as f:
                tar.addfile(info, f)


def _read_gz_csv(p: Path) -> list[dict]:
    return list(csv.DictReader(io.StringIO(gzip.decompress(p.read_bytes()).decode("utf-8"))))


def _num_diff(a_rows, b_rows, keys, skip=()):
    ix = {tuple(r[k] for k in keys): r for r in b_rows}; worst = {}; unmatched = 0
    for r in a_rows:
        o = ix.get(tuple(r[k] for k in keys))
        if o is None:
            unmatched += 1; continue
        for c, v in r.items():
            if c in keys or c in skip:
                continue
            try:
                x, y = float(v), float(o[c])
            except (TypeError, ValueError):
                if v != o.get(c):
                    worst[c] = "TEXT_DIFFERS"
                continue
            if x != x and y != y:
                continue
            d = abs(x - y)
            if isinstance(worst.get(c), float) or c not in worst:
                worst[c] = max(worst.get(c, 0.0), d)
    return {"unmatched_rows": unmatched, "max_abs_diff": worst}


def execute(run, ds, research_root: Path, out: Path, say, stop_now) -> dict:
    cap = capability()
    if not cap["ok"]:
        raise RuntimeError(f"ENGINE_UNAVAILABLE: {cap.get('reason')}")
    cfg = json.loads((FROZEN_DIR / "config.json").read_text(encoding="utf-8"))
    protocol = json.loads(run["config_json"])
    input_root = Path(os.environ.get("QUANT_RESEARCH_INPUT_ROOT") or research_root)
    snap = input_root / ds["snapshot_path"] / "market_research.sqlite"
    say(f"engine {ENGINE[0]}@{ENGINE[1]}; frozen program verified ({', '.join(f'{k} {v[:12]}' for k, v in cap['frozen_files'].items())})")
    t0 = time.monotonic(); sha = sha_file(snap)
    if sha != ds["content_sha256"]:
        raise ValueError(f"INPUT_HASH_MISMATCH: snapshot {sha} != registered {ds['content_sha256']}")
    mode = "exact" if sha == cfg["expected_db_sha256"] else "external"
    manifest = json.loads((input_root / ds["snapshot_path"] / "manifest.json").read_text(encoding="utf-8"))
    end = cfg["data_end"] if mode == "exact" else manifest["end_date"]
    say(f"input verified sha256={sha} ({time.monotonic() - t0:.1f}s); mode={mode}; data_end={end}; read-only root={input_root}")
    (out / "resolved_config.json").write_text(json.dumps({"engine": {"engine_id": ENGINE[0], "engine_version": ENGINE[1]}, "protocol": protocol,
        "frozen_config": cfg, "mode": mode, "data_end": end, "freeze_commit": FREEZE_COMMIT,
        "contracts": json.loads(run["contracts_json"]) if run["contracts_json"] else None}, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "input_manifest.json").write_text(json.dumps({"dataset_id": ds["dataset_id"], "dataset_version": ds["dataset_version"], "snapshot_rel": f"{ds['snapshot_path']}/market_research.sqlite",
        "sha256": sha, "bytes": snap.stat().st_size, "series": manifest.get("series"), "unresolved_dates": manifest.get("unresolved_dates"),
        "read_only_mount": bool(os.environ.get("QUANT_RESEARCH_INPUT_ROOT"))}, ensure_ascii=False, indent=1), encoding="utf-8")
    adapter_sha = sha_file(Path(__file__))
    (out / "code_manifest.json").write_text(json.dumps({"frozen_program": {n: cap["frozen_files"][n] for n in FROZEN_SHA256}, "freeze_commit": FREEZE_COMMIT,
        "adapter": {"path": "scripts/quant_research_v1/engines/phase5_o2o.py", "sha256": adapter_sha},
        "platform_code_version": os.environ.get("QUANT_CODE_VERSION")}, indent=1), encoding="utf-8")
    priv, pub = out / "program_private", out / "program_public"
    py = sys.executable
    with open(out / "program_stdout.log", "w", encoding="utf-8") as log:
        cmd = [py, "run.py", "--db", str(snap), "--private-out", str(priv), "--public-out", str(pub), "--freeze-commit", FREEZE_COMMIT, "--mode", mode]
        if mode == "external":
            cmd += ["--end", end]
        log.write("# " + " ".join(c if not c.startswith(str(input_root)) else "<input>" for c in cmd) + "\n"); log.flush()
        rc, secs, rss = _child(cmd, FROZEN_DIR, log, stop_now)
        say(f"frozen run.py exit={rc} in {secs:.1f}s peak_child_rss_mb={rss}")
        if rc != 0:
            raise RuntimeError(f"FROZEN_PROGRAM_FAILED: run.py exit {rc}")
        log.write("# check.py --db <input> --private-out ... --public-out ...\n"); log.flush()
        rc2, secs2, rss2 = _child([py, "check.py", "--db", str(snap), "--private-out", str(priv), "--public-out", str(pub)], FROZEN_DIR, log, stop_now)
        say(f"frozen check.py exit={rc2} in {secs2:.1f}s")
    self_check = json.loads((pub / "self_check.json").read_text(encoding="utf-8")) if (pub / "self_check.json").is_file() else None
    for n, dst in (("metrics.csv", "metrics.csv"), ("contrasts.csv", "contrasts.csv"), ("factor_diagnostics.csv", "factor_diagnostics.csv"),
                   ("run_manifest.json", "program_run_manifest.json"), ("private_output_hashes.json", "private_output_hashes.json"), ("self_check.json", "self_check.json")):
        if (pub / n).is_file():
            (out / dst).write_bytes((pub / n).read_bytes().replace(b"\r\n", b"\n"))
    stop_now()
    metrics = list(csv.DictReader(io.StringIO((out / "metrics.csv").read_text(encoding="utf-8"))))
    contrasts = list(csv.DictReader(io.StringIO((out / "contrasts.csv").read_text(encoding="utf-8"))))
    hashes = json.loads((out / "private_output_hashes.json").read_text(encoding="utf-8"))
    # ---- mechanical check: frozen check.py + (exact mode) reconciliation against the first-round results ----
    mech = {"frozen_check_exit": rc2, "frozen_check_status": (self_check or {}).get("real_run", {}).get("status"),
            "ledger_files_rechecked": (self_check or {}).get("real_run", {}).get("ledger_files_recomputed"), "mode": mode}
    if mode == "exact":
        first = FROZEN_DIR / "results"
        fh = json.loads((first / "private_output_hashes.json").read_text(encoding="utf-8"))
        same = sum(1 for k, v in fh.items() if hashes.get(k, {}).get("uncompressed_sha256") == v["uncompressed_sha256"])
        mech["same_input_reconciliation"] = {
            "kind": "same frozen program, same input: repeatability / engineering integration check, NOT an independent strategy validation",
            "ledger_files": [len(fh), len(hashes)], "ledger_raw_sha256_identical": same, "ledger_set_equal": set(fh) == set(hashes),
            "metrics": _num_diff(metrics, list(csv.DictReader(io.StringIO((first / "metrics.csv").read_text(encoding="utf-8")))), ("symbol", "period", "cost_bps", "strategy")),
            "contrasts": _num_diff(contrasts, list(csv.DictReader(io.StringIO((first / "contrasts.csv").read_text(encoding="utf-8")))), ("symbol", "strategy", "period", "cost_bps", "block", "seed"))}
    ok_mech = rc2 == 0 and mech["frozen_check_status"] == "PASS_SAME_AUTHOR_LEDGER_AND_INPUT_CHECK"
    mech["status"] = "PASS" if ok_mech else "FAIL"
    (out / "mechanical_check.json").write_text(json.dumps(mech, indent=1, default=str), encoding="utf-8")
    if not ok_mech:
        raise RuntimeError(f"MECHANICAL_CHECK_FAILED: {mech['frozen_check_status']}")
    # ---- science ----
    prim = [c for c in contrasts if c["primary_test"] == "True"]
    passes = sum(c.get("first_screen_return_increment_pass") == "True" for c in prim)
    executed = [m for m in metrics if m["status"] == "EXECUTED_PRICE_ONLY"]
    status = "INSUFFICIENT_SAMPLE" if not prim else ("FIRST_SCREEN_PASSED_REPLICATION_PENDING" if passes else "FIRST_SCREEN_NOT_PASSED")
    science = {"scientific_status": status, "primary_tests": len(prim), "primary_passes": passes,
               "no_eligible_interval_rows": sum(m["status"] == "NO_ELIGIBLE_INTERVALS" for m in metrics), "executed_rows": len(executed),
               "text_candidates": {"T1": "BLOCKED_INPUTS", "T2": "BLOCKED_INPUTS"},
               "meaning": "Software success and mechanical PASS do not make a strategy effective; the return-increment screen decides only the first screen."}
    (out / "science.json").write_text(json.dumps(science, ensure_ascii=False, indent=1), encoding="utf-8")
    # ---- signals with availability time (primary period, 10 bps ledgers) and all ledgers ----
    buf = io.StringIO(); w = csv.writer(buf, lineterminator="\n")
    w.writerow(["symbol", "strategy", "signal_close_date", "available_after", "execution_open_date", "mark_open_date", "target_weight", "weight_during"])
    curves = {}
    for sym in MKT:
        curves[sym] = {}
        for cost in ("0", "10", "25"):
            series, dates = {}, None
            for st in STRATS:
                f = priv / "ledgers" / sym.replace(".", "_") / "primary" / f"{st}_c{cost}.csv.gz"
                if not f.is_file():
                    continue
                rows = _read_gz_csv(f)
                if cost == "10":
                    for r in rows:
                        w.writerow([sym, st, r["signal_date"], f"close of {r['signal_date']}", r["exec_date"], r["mark_date"], r["target_weight"], r["weight_during"]])
                dates = [r["mark_date"] for r in rows]; series[st] = [float(r["end_nav"]) for r in rows]
            if dates:
                step = max(1, len(dates) // 400); idx = [i for i in range(len(dates)) if i % step == 0 or i == len(dates) - 1]
                curves[sym][cost] = {"date": [dates[i] for i in idx], "nav": {k: [round(v[i], 8) for i in idx] for k, v in series.items()}}
    (out / "signals_primary.csv.gz").write_bytes(gzip.compress(buf.getvalue().encode(), mtime=0))
    _deterministic_tar(priv, out / "ledgers_all.tar")
    fl = lambda x: None if x in ("", None) else float(x)
    report = {"engine": "@".join(ENGINE), "mode": mode, "summary_line":
              f"phase5 冻结程序 · {('同输入（exact）' if mode == 'exact' else '外部输入（external）')} · 输入 {sha[:12]}… 截至 {end} · 12 项主检验 {passes} 项通过 · 机械复核 {mech['status']}",
              "primary_metrics": [{k: (fl(m[k]) if k in ("cagr", "max_drawdown", "mean_exposure", "cost_fraction_sum", "descriptive_sharpe_sqrt252", "terminal_wealth") else m[k])
                                   for k in ("symbol", "strategy", "cost_bps", "cagr", "max_drawdown", "mean_exposure", "cost_fraction_sum", "descriptive_sharpe_sqrt252", "terminal_wealth", "first_exec", "last_mark", "intervals")}
                                  for m in executed if m["period"] == "primary"],
              "primary_tests": [{k: c[k] for k in ("symbol", "strategy", "n", "mean_daily_net_delta", "ci_low_daily", "ci_high_daily", "p_one_sided_positive", "holm_p",
                                                   "mean_daily_delta_cost25", "first_screen_return_increment_pass")} for c in prim],
              "curves": curves, "science": science, "mechanical": {"status": mech["status"], "same_input": mech.get("same_input_reconciliation", {}).get("ledger_raw_sha256_identical")},
              "limits_zh": "这是工程接入与同实现复算，不是新的独立策略验证。QQQ 为 ETF（a1 前复权，未认证总收益），其余三个为指数代理（不可直接交易）；数据为当前版本回填、非时点；历史已看过。成本与现金收益为假设。T1/T2 缺历史新闻/事件输入。"}
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    env = {"python": platform.python_version(), "libs": cap["libs"], "image_id": os.environ.get("QUANT_IMAGE_ID"), "lockfile_sha256": os.environ.get("QUANT_NUMERIC_LOCK_SHA256"),
           "code_version": os.environ.get("QUANT_CODE_VERSION"), "network": "none expected (job container)",
           "resources": {"run_py_seconds": round(secs, 1), "check_py_seconds": round(secs2, 1), "peak_child_rss_mb": max([x for x in (rss, rss2) if x is not None], default=None)}}
    (out / "environment.json").write_text(json.dumps(env, indent=1), encoding="utf-8")
    h = hashlib.sha256()
    for n in ("metrics.csv", "contrasts.csv", "factor_diagnostics.csv"):
        h.update(n.encode()); h.update(hashlib.sha256((out / n).read_bytes()).digest())
    for k in sorted(hashes):
        h.update(k.encode()); h.update(hashes[k]["uncompressed_sha256"].encode())
    digest = h.hexdigest()
    summary = {"engine": "@".join(ENGINE), "mode": mode, "results_digest": digest, "scientific_status": status, "primary_passes": passes,
               "mechanical_check": mech["status"], "ledger_files": len(hashes), "resources": env["resources"]}
    return {"artifact_dir": f"runs/{run['run_id']}", "results_digest": digest, "summary_json": json.dumps(summary), "exit_reason": "COMPLETED",
            "_artifacts": list(ARTIFACTS), "_grade": {"software": "SUCCEEDED", "mechanical": f"{mech['status']} (frozen check.py{'; same-input ledgers ' + str(mech['same_input_reconciliation']['ledger_raw_sha256_identical']) + '/' + str(len(hashes)) + ' byte-identical' if mode == 'exact' else ''})",
                                                    "scientific": status, "mode": mode, "digest": digest}}
