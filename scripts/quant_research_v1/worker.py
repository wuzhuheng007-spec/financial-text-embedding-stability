"""Research job worker and admin CLI.

  worker --once [--max-seconds 300]     recover lost runs, then claim one QUEUED run and execute it
  recover                               only mark confirmed-lost RUNNING runs as FAILED/WORKER_LOST
  retry RUN_ID                          queue a NEW run linked to a failed/cancelled run
  snapshot --source SQLITE --symbol US.QQQ --autype 1 --dataset-id ID --dataset-version V --receipt JSON
  register-snapshot datasets/ID/VER     register an uploaded frozen snapshot (hash-checked)
  register-defaults                     register built-in factors (binds implementation fingerprints)
  enqueue --dataset ID@V --factor ID@V --protocol CONFIG.json
  verify RUN_ID                         legacy ledger-consistency check (v1); see audit.py for v2
  capability                            print this image's verified engine capabilities (no registry write)
  admission pause --reason TEXT | open | status   pause/resume NEW research requests from the web (release/rollback)
  cancel-queued --engine ID@V --reason TEXT       cancel QUEUED runs of one engine (CANCELLED_BEFORE_START), e.g.
                                                  before rolling the app back to an image whose worker lacks engines
  withdraw-capability --engine ID@V               delete recorded capabilities of an engine (page shows it as not built)
  register-reference --source DIR --input-sha SHA   copy + verify the designated reference ledgers (phase5 exact input)
Protected v2 jobs (CONTROL role; started by deploy/linux/host/quant-research-job.sh, never by the web):
  job-claim --worker-id TOKEN [--run RUN_ID] --max-seconds S   claim one v2 run, prepare runs/<id>/work, print JSON
  job-supervise RUN_ID --worker-id TOKEN --max-seconds S       renew the lease; exit 10 cancel, 11 timeout, 12 not running
  job-finalize RUN_ID --compute-exit N --stop-reason R         verify the compute work tree, accept or fail the run
v2 engines are NOT executed inside `worker --once` / `run-one` (that would give the numeric child the registry and
every run directory); they run only through the protected launcher. QUANT_RESEARCH_INPROCESS_V2=1 re-enables the
in-process path for unit tests only, and such runs are recorded as IN_PROCESS_TEST_ONLY.
Runs never write outside runs/<run_id>/ and never overwrite an existing directory or file.

Environment set by the host runner: QUANT_CODE_VERSION (git revision of the image),
QUANT_IMAGE_ID (image digest), QUANT_LIVE_WORKERS (ids of research job containers already
running, comma separated; used to confirm a lost worker), HOSTNAME (this container's id).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import signal
import sys
import threading
import time
import traceback
from pathlib import Path

from . import registry
from .engine import FACTORS, backtest, build_snapshot, impl_fingerprint, ledger_csv, load_snapshot


class RunTimeout(Exception):
    pass


class RunCancelled(Exception):
    pass


class RunTerminated(Exception):
    """SIGTERM (e.g. docker stop): stop at once, record a clean terminal state, keep partial files."""


def results_digest(scenarios: list[dict], ledgers: dict[str, str]) -> str:
    """Content digest excluding timestamps and run ids: metrics + exact ledger text."""
    h = hashlib.sha256()
    h.update(registry.canonical(scenarios).encode())
    for k in sorted(ledgers):
        h.update(k.encode()); h.update(hashlib.sha256(ledgers[k].encode()).digest())
    return h.hexdigest()


def worker_identity() -> str:
    return f"{os.environ.get('HOSTNAME') or platform.node()}:{os.getpid()}"


def live_workers() -> set[str] | None:
    v = os.environ.get("QUANT_LIVE_WORKERS")
    return None if v is None else {x.strip()[:12] for x in v.split(",") if x.strip()}


class Heartbeat(threading.Thread):
    """Extends the run's lease every few seconds on its own connection; records cancel requests."""

    def __init__(self, run_id: str, wid: str, max_seconds: int, every: float | None = None):
        super().__init__(daemon=True)
        self.run_id, self.wid = run_id, wid
        self.every = every or float(os.environ.get("QUANT_RESEARCH_HEARTBEAT_SECONDS", "5"))
        self.deadline = time.monotonic() + max_seconds
        self.cancel = threading.Event(); self.timeout = threading.Event(); self.stop = threading.Event()

    def run(self):
        db = registry.connect()
        try:
            while not self.stop.wait(self.every):
                if time.monotonic() > self.deadline:
                    self.timeout.set()
                try:
                    if registry.heartbeat(db, self.run_id, self.wid):
                        self.cancel.set()
                except Exception:  # noqa: BLE001 - a missed beat only shortens the lease
                    pass
        finally:
            db.close()


V2_ENGINES = (("phase5_o2o_v1", "1"),)


def inprocess_v2_allowed() -> bool:
    return os.environ.get("QUANT_RESEARCH_INPROCESS_V2") == "1"


def worker_engines(db=None, for_inprocess: bool = True) -> list:
    """Engines this worker will claim: the ones requested for this job image (QUANT_WORKER_ENGINES, default the
    legacy engine) that ALSO pass a live capability self-test here. Verified capabilities are recorded in the
    registry (worker_capabilities) under this image id; the browser never declares them.
    for_inprocess=True (worker --once / run-one): v2 engines are excluded unless QUANT_RESEARCH_INPROCESS_V2=1,
    because in-process execution has no mount isolation. The protected launcher's job-claim passes False."""
    requested = [tuple(x.split("@")) for x in (os.environ.get("QUANT_WORKER_ENGINES") or "legacy_mom20_v1@1").split(",") if x.strip()]
    ok = []
    for eng in requested:
        if eng == registry.LEGACY_ENGINE:
            if for_inprocess:
                ok.append(eng)
            continue
        if eng in V2_ENGINES and for_inprocess and not inprocess_v2_allowed():
            print(f"{eng[0]}@{eng[1]} runs only through the protected launcher (quant-research-job.sh); not claimed here", file=sys.stderr)
            continue
        if eng == ("phase5_o2o_v1", "1"):
            from .engines import phase5_o2o
            cap = phase5_o2o.capability()
            if db is not None and os.environ.get("QUANT_IMAGE_ID"):
                db.execute("INSERT OR REPLACE INTO worker_capabilities VALUES(?,?,?,?,?)",
                           (os.environ["QUANT_IMAGE_ID"], eng[0], eng[1], json.dumps(cap), registry.utcnow())); db.commit()
            if cap["ok"]:
                ok.append(eng)
    return ok


def execute(db, run, research_root: Path, hb: Heartbeat) -> dict:
    if registry.engine_of(run) != registry.LEGACY_ENGINE:
        return execute_v2(db, run, research_root, hb)
    ds = db.execute("SELECT * FROM datasets WHERE dataset_id=? AND dataset_version=?",
                    (run["dataset_id"], run["dataset_version"])).fetchone()
    fac = db.execute("SELECT * FROM factors WHERE factor_id=? AND factor_version=?",
                     (run["factor_id"], run["factor_version"])).fetchone()
    spec = FACTORS[(run["factor_id"], run["factor_version"])]
    cfg = json.loads(run["config_json"])
    out = research_root / "runs" / run["run_id"]
    out.mkdir(parents=True, exist_ok=False)
    with open(out / "run.log", "w", encoding="utf-8") as log:
        return _execute(db, run, research_root, ds, fac, spec, cfg, out, log, hb)


def _execute(db, run, research_root, ds, fac, spec, cfg, out, log, hb) -> dict:
    def say(msg):
        log.write(f"{registry.utcnow()} {msg}\n"); log.flush()

    def stop_now():
        if hb.timeout.is_set():
            raise RunTimeout()
        if hb.cancel.is_set():
            raise RunCancelled()
        return False
    say(f"run {run['run_id']} worker {run['worker_id']} dataset {ds['dataset_id']}@{ds['dataset_version']} "
        f"factor {spec['factor_id']}@{spec['factor_version']}")
    # Execution identity is mandatory for new runs: no code version / image id, no run.
    if not os.environ.get("QUANT_CODE_VERSION") or not os.environ.get("QUANT_IMAGE_ID"):
        raise ValueError("RUNTIME_IDENTITY_MISSING: QUANT_CODE_VERSION and QUANT_IMAGE_ID must be set by the runner")
    # The code that will actually execute must be the registered implementation (blind spot B).
    fp = impl_fingerprint(spec["fn"])
    if fac["impl_sha256"] is None or fp != fac["impl_sha256"]:
        raise ValueError(f"FACTOR_IMPL_MISMATCH: executing {fp} but registered {fac['impl_sha256']}")
    bars = load_snapshot(research_root / ds["snapshot_path"], ds["content_sha256"])
    say(f"snapshot verified sha256={ds['content_sha256']} rows={len(bars)} {bars[0]['date']}..{bars[-1]['date']}")
    params = json.loads(registry.canonical(cfg["factor"]["params"]))
    if params != spec["params"]:
        raise ValueError("protocol parameters differ from the registered factor version")
    fac_values = spec["fn"](bars, **params)
    (out / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    scenarios, ledgers, last = [], {}, None
    for bps in cfg["costs"]["scenarios_bps_one_way"]:
        stop_now()
        if os.environ.get("QUANT_RESEARCH_TEST_ALLOC_MB"):  # drill hook: exceed the container memory limit (OOM)
            hog = [bytearray(1 << 20) for _ in range(int(os.environ["QUANT_RESEARCH_TEST_ALLOC_MB"]))]
            del hog
        if os.environ.get("QUANT_RESEARCH_TEST_DELAY"):  # test hook: makes a run long enough to kill or cancel
            for _ in range(int(float(os.environ["QUANT_RESEARCH_TEST_DELAY"]) * 10)):
                time.sleep(0.1); stop_now()
        res = backtest(bars, fac_values, cfg, bps, should_stop=stop_now)
        text = ledger_csv(res["ledger"])
        name = f"ledger_cost{int(bps)}bps.csv"
        (out / name).write_text(text, encoding="utf-8")
        ledgers[name] = text; last = res
        scenarios.append({"cost_bps": bps, "segments": res["segments"], "full_period": res["full_period"],
                          "first_trade_date": res["ledger"][0]["date"], "last_date": res["ledger"][-1]["date"],
                          "rows": len(res["ledger"])})
        say(f"cost {bps} bps: {len(res['ledger'])} ledger rows written")
    step = max(1, len(last["ledger"]) // 600)
    curves = {}
    for bps in cfg["costs"]["scenarios_bps_one_way"]:
        lines = ledgers[f"ledger_cost{int(bps)}bps.csv"].splitlines()
        idx = {k: i for i, k in enumerate(lines[0].split(","))}
        rows = [r.split(",") for r in lines[1:]]
        pick = [r for i, r in enumerate(rows) if i % step == 0 or i == len(rows) - 1]
        curves[str(bps)] = {"date": [r[idx["date"]] for r in pick], "nav": [float(r[idx["nav"]]) for r in pick],
                            "benchmark_nav": [float(r[idx["benchmark_nav"]]) for r in pick]}
    digest = results_digest(scenarios, ledgers)
    runtime = {"code_version": os.environ["QUANT_CODE_VERSION"],
               "image_id": os.environ.get("QUANT_IMAGE_ID"), "python": platform.python_version(), "worker_id": run["worker_id"]}
    metrics = {"run_id": run["run_id"],
               "dataset": {k: ds[k] for k in ("dataset_id", "dataset_version", "content_sha256", "start_date", "end_date")},
               "factor": {"factor_id": spec["factor_id"], "factor_version": spec["factor_version"], "params": spec["params"], "impl_sha256": fp},
               "protocol": {"protocol_id": cfg["protocol_id"], "protocol_version": cfg["protocol_version"], "config_sha256": run["config_sha256"]},
               "runtime": runtime, "return_label": cfg["price_series"]["return_label"], "scenarios": scenarios, "results_digest": digest}
    (out / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "curves.json").write_text(json.dumps(curves), encoding="utf-8")
    from .verify import verify_run
    check = verify_run(out)
    (out / "verify.json").write_text(json.dumps(check, ensure_ascii=False, indent=1), encoding="utf-8")
    stop_now()
    # Record the binding fields first so the independent audit can compare registry and files.
    db.execute("UPDATE runs SET code_version=?, image_id=?, factor_impl_sha256=?, results_digest=? WHERE run_id=?",
               (runtime["code_version"], runtime["image_id"], fp, digest, run["run_id"])); db.commit()
    from .audit import VERIFIER_VERSION, audit_run
    aud = audit_run(research_root, run["run_id"])
    (out / f"audit_{VERIFIER_VERSION}.json").write_text(json.dumps(aud, ensure_ascii=False, indent=1), encoding="utf-8")
    say(f"ledger consistency (v1) ok={check['ok']}; independent audit {VERIFIER_VERSION} ok={aud['ok']}; results_digest={digest}")
    if not check["ok"] or not aud["ok"]:
        raise ValueError(f"VERIFICATION_FAILED: {(aud['problems'] or ['ledger consistency'])[:3]}")
    summary = {"results_digest": digest, "verify_ok": True, "audit_ok": True, "verifier_version": VERIFIER_VERSION,
               "return_label": metrics["return_label"], "rows": scenarios[0]["rows"],
               "first_trade_date": scenarios[0]["first_trade_date"], "last_date": scenarios[0]["last_date"],
               "headline": [{"cost_bps": s["cost_bps"], "segments": [{"name": g["name"], "strategy_total_return": g["strategy"]["total_return"],
                             "benchmark_total_return": g["benchmark"]["total_return"]} for g in s["segments"]]} for s in scenarios]}
    return {"artifact_dir": f"runs/{run['run_id']}", "results_digest": digest, "summary_json": json.dumps(summary),
            "verifier_version": VERIFIER_VERSION, "exit_reason": "COMPLETED"}


def execute_v2(db, run, research_root: Path, hb: Heartbeat) -> dict:
    """Versioned engines (phase5_o2o_v1): same lease/heartbeat/cancel/timeout machinery as the legacy path."""
    if not os.environ.get("QUANT_CODE_VERSION") or not os.environ.get("QUANT_IMAGE_ID"):
        raise ValueError("RUNTIME_IDENTITY_MISSING: QUANT_CODE_VERSION and QUANT_IMAGE_ID must be set by the runner")
    eng = registry.engine_of(run)
    if eng != ("phase5_o2o_v1", "1"):
        raise ValueError(f"UNSUPPORTED_ENGINE {eng}")
    from .engines import phase5_o2o
    ds = db.execute("SELECT * FROM datasets WHERE dataset_id=? AND dataset_version=?", (run["dataset_id"], run["dataset_version"])).fetchone()
    out = research_root / "runs" / run["run_id"]
    out.mkdir(parents=True, exist_ok=False)
    with open(out / "run.log", "w", encoding="utf-8") as log:
        def say(msg):
            log.write(f"{registry.utcnow()} {msg}\n"); log.flush()

        def stop_now():
            if hb.timeout.is_set():
                raise RunTimeout()
            if hb.cancel.is_set():
                raise RunCancelled()
        say(f"run {run['run_id']} worker {run['worker_id']} image {os.environ.get('QUANT_IMAGE_ID')} dataset {ds['dataset_id']}@{ds['dataset_version']}")
        if not inprocess_v2_allowed():
            raise RuntimeError("INPROCESS_V2_REFUSED: v2 engines run only through the protected launcher")
        fields = phase5_o2o.execute(run, ds, research_root, out, say, stop_now)
        say(f"results_digest={fields['results_digest']}; registering artifacts")
    _register_v2(db, run, out, fields)
    return fields


def _register_v2(db, run, out: Path, fields: dict):
    from . import catalog
    names = [n for n in fields.pop("_artifacts") if (out / n).is_file()]
    catalog.register_artifacts(db, run["run_id"], out, names)
    db.execute("UPDATE runs SET code_version=?, image_id=?, runtime_image_digest=?, lockfile_hash=?, evidence_grade=? WHERE run_id=?",
               (os.environ["QUANT_CODE_VERSION"], os.environ["QUANT_IMAGE_ID"], os.environ.get("QUANT_IMAGE_DIGEST") or "LOCAL_IMAGE_ID_ONLY",
                os.environ.get("QUANT_NUMERIC_LOCK_SHA256"), "PLATFORM_EXECUTED_SAME_IMPLEMENTATION", run["run_id"]))
    db.commit()


def _record_grade(db, run_id: str, final: str, grade: dict | None):
    if not grade:
        return
    from . import catalog
    catalog.record_grade(db, "run", run_id, software_status=final, mechanical_check=grade["mechanical"],
                         data_pit_eligibility="CURRENT_VINTAGE_NOT_POINT_IN_TIME", scientific_status=grade["scientific"] if final == "SUCCEEDED" else "NOT_EVALUATED",
                         replication_scope=("same frozen program and same input (engineering integration / repeatability), not independent validation"
                                            if grade["mode"] == "exact" else "same frozen program on a different input version (external mode); not unseen data"),
                         evidence={"results_digest": grade["digest"], "artifacts": f"runs/{run_id}/"})


def run_once(max_seconds: int) -> int:
    """Normal queue processing: recover confirmed-lost runs, then claim the oldest QUEUED run."""
    research_root = registry.root()
    db = registry.connect()
    lost = registry.recover_lost(db, live_workers(), max_seconds)
    for r in lost:
        d = research_root / "runs" / r["run_id"]
        if d.is_dir() and not (d / "recovery.json").exists():  # keep partial artifacts; add a note, overwrite nothing
            (d / "recovery.json").write_text(json.dumps({**r, "status": "FAILED", "exit_reason": "WORKER_LOST"}, indent=1), encoding="utf-8")
        print(f"recovered {r['run_id']} -> FAILED/WORKER_LOST")
    wid = worker_identity()
    engines = worker_engines(db)
    run = registry.claim_next(db, wid, engines=engines)
    if run is None:
        db.close(); print(f"no queued run for engines {['@'.join(e) for e in engines]}"); return 0
    return _run_claimed(db, run, research_root, wid, max_seconds)


def run_specific(run_id: str, max_seconds: int) -> int:
    """Execute exactly one QUEUED run by id (restore checks). No recovery pass, no other run is touched."""
    research_root = registry.root()
    db = registry.connect()
    wid = worker_identity()
    run = registry.claim_run(db, run_id, wid, engines=worker_engines(db))
    if run is None:
        db.close(); print(f"{run_id} not claimable (not QUEUED or engine not supported here)"); return 2
    return _run_claimed(db, run, research_root, wid, max_seconds)


def _run_claimed(db, run, research_root: Path, wid: str, max_seconds: int) -> int:
    hb = Heartbeat(run["run_id"], wid, max_seconds); hb.start()

    def on_alarm(*_):
        raise RunTimeout()

    def on_term(*_):
        raise RunTerminated()
    if hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, on_alarm); signal.alarm(max_seconds)
    old_term = signal.signal(signal.SIGTERM, on_term) if threading.current_thread() is threading.main_thread() else None
    try:
        fields = execute(db, run, research_root, hb)
        grade = fields.pop("_grade", None)
        final = registry.finish(db, run["run_id"], "SUCCEEDED", **fields)
        _record_grade(db, run["run_id"], final, grade)  # after the terminal transition: a won cancel stays CANCELLED
        print(f"{run['run_id']} {final} {fields['results_digest']}"); return 0
    except RunCancelled:
        registry.finish(db, run["run_id"], "CANCELLED", error_class="CANCELLED_BY_OWNER", exit_reason="CANCELLED_WHILE_RUNNING",
                        artifact_dir=f"runs/{run['run_id']}")
        print(f"{run['run_id']} CANCELLED"); return 0
    except RunTimeout:
        registry.finish(db, run["run_id"], "FAILED", error_class="TIMEOUT", exit_reason="TIMEOUT",
                        error_detail=f"exceeded {max_seconds}s", artifact_dir=f"runs/{run['run_id']}"); return 1
    except RunTerminated:
        registry.finish(db, run["run_id"], "FAILED", error_class="TERMINATED", exit_reason="TERMINATED_BY_SIGNAL",
                        error_detail="SIGTERM received (container stop); partial files kept", artifact_dir=f"runs/{run['run_id']}")
        print(f"{run['run_id']} FAILED/TERMINATED_BY_SIGNAL"); return 143
    except Exception as exc:  # noqa: BLE001 - a failed run is recorded, never crashes collection
        registry.finish(db, run["run_id"], "FAILED", error_class=type(exc).__name__, error_detail=str(exc)[:500],
                        exit_reason="ERROR", artifact_dir=f"runs/{run['run_id']}")
        traceback.print_exc(); return 1
    finally:
        if hasattr(signal, "SIGALRM"):
            signal.alarm(0)
        if old_term is not None:
            signal.signal(signal.SIGTERM, old_term)
        hb.stop.set(); hb.join(timeout=10)
        db.close()


# ---------------------------------------------------------------- protected v2 jobs (CONTROL role)
_SAFE_REL = re.compile(r"^datasets/[A-Za-z0-9._@-]+/[A-Za-z0-9._@-]+$")


def job_claim(worker_id: str, run_id: str | None, max_seconds: int) -> int:
    """Claim one v2 run for the protected launcher and prepare runs/<id>/work (the ONLY place compute may write).
    Prints one JSON line: {"run_id": null} when idle, else the subpaths the launcher mounts."""
    research_root = registry.root(); db = registry.connect()
    try:
        engines = [e for e in worker_engines(db, for_inprocess=False) if e in V2_ENGINES]
        if not engines:
            print(json.dumps({"run_id": None, "reason": "no verified v2 engine capability in this image"})); return 0
        run = registry.claim_run(db, run_id, worker_id, engines=engines) if run_id else registry.claim_next(db, worker_id, engines=engines)
        if run is None:
            print(json.dumps({"run_id": None, "reason": (f"{run_id} not claimable" if run_id else f"no queued run for {['@'.join(e) for e in engines]}")}))
            return 2 if run_id else 0
        rid = run["run_id"]
        try:
            if not os.environ.get("QUANT_CODE_VERSION") or not os.environ.get("QUANT_IMAGE_ID"):
                raise ValueError("RUNTIME_IDENTITY_MISSING: QUANT_CODE_VERSION and QUANT_IMAGE_ID must be set by the launcher")
            ds = db.execute("SELECT * FROM datasets WHERE dataset_id=? AND dataset_version=?", (run["dataset_id"], run["dataset_version"])).fetchone()
            if not _SAFE_REL.match(ds["snapshot_path"]):
                raise ValueError(f"UNSAFE_SNAPSHOT_PATH: {ds['snapshot_path']!r}")
            out = research_root / "runs" / rid
            out.mkdir(parents=True, exist_ok=False); (out / "work").mkdir()
            from .engines import phase5_o2o
            sha = phase5_o2o.sha_file(research_root / ds["snapshot_path"] / "market_research.sqlite")
            with open(out / "run.log", "w", encoding="utf-8") as log:
                log.write(f"{registry.utcnow()} run {rid} claimed by protected job {worker_id} image {os.environ.get('QUANT_IMAGE_ID')} "
                          f"dataset {ds['dataset_id']}@{ds['dataset_version']}\n")
                log.write(f"{registry.utcnow()} input sha256 before run {sha} (control step)\n")
            if sha != ds["content_sha256"]:
                raise ValueError(f"INPUT_HASH_MISMATCH: snapshot {sha} != registered {ds['content_sha256']}")
            print(json.dumps({"run_id": rid, "input_subpath": f"research/{ds['snapshot_path']}", "work_subpath": f"research/runs/{rid}/work",
                              "content_sha256": ds["content_sha256"], "max_seconds": max_seconds}))
            return 0
        except Exception as exc:  # noqa: BLE001
            registry.finish(db, rid, "FAILED", error_class=type(exc).__name__, error_detail=str(exc)[:500], exit_reason="ERROR", artifact_dir=f"runs/{rid}")
            print(json.dumps({"run_id": rid, "abort": f"{type(exc).__name__}: {exc}"[:300]})); return 1
    finally:
        db.close()


def job_supervise(run_id: str, worker_id: str, max_seconds: int) -> int:
    """Lease keeper for a protected job: renews the lease, watches cancel and the deadline. Never touches files."""
    every = float(os.environ.get("QUANT_RESEARCH_HEARTBEAT_SECONDS", "5"))
    deadline = time.monotonic() + max_seconds
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    db = registry.connect()
    try:
        while True:
            time.sleep(every)
            if time.monotonic() > deadline:
                print("TIMEOUT", flush=True); return 11
            try:
                if registry.heartbeat(db, run_id, worker_id):
                    print("CANCEL", flush=True); return 10
                st = db.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()
                if not st or st[0] != "RUNNING":
                    print(f"NOT_RUNNING {st[0] if st else None}", flush=True); return 12
            except Exception:  # noqa: BLE001 - a missed beat only shortens the lease
                pass
    finally:
        db.close()


def job_finalize(run_id: str, compute_exit: int, stop_reason: str) -> int:
    """Terminal transition for a protected job. stop_reason: none | cancel | timeout | terminated | supervisor_lost."""
    research_root = registry.root(); db = registry.connect()
    try:
        run = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if run is None or run["status"] != "RUNNING":
            print(f"{run_id} not RUNNING ({run['status'] if run else 'missing'}); nothing to finalize"); return 0
        out = research_root / "runs" / run_id; work = out / "work"
        art = f"runs/{run_id}"
        with open(out / "run.log", "a", encoding="utf-8") as log:
            def say(msg):
                log.write(f"{registry.utcnow()} {msg}\n"); log.flush()
            say(f"compute container exit {compute_exit}; stop reason {stop_reason}")
            if stop_reason == "cancel":
                registry.finish(db, run_id, "CANCELLED", error_class="CANCELLED_BY_OWNER", exit_reason="CANCELLED_WHILE_RUNNING", artifact_dir=art)
                print(f"{run_id} CANCELLED"); return 0
            if stop_reason == "timeout":
                registry.finish(db, run_id, "FAILED", error_class="TIMEOUT", exit_reason="TIMEOUT", error_detail="exceeded the job time limit", artifact_dir=art)
                print(f"{run_id} FAILED/TIMEOUT"); return 1
            if stop_reason in ("terminated", "supervisor_lost"):
                registry.finish(db, run_id, "FAILED", error_class="TERMINATED", exit_reason="TERMINATED_BY_SIGNAL",
                                error_detail=f"{stop_reason}: job stopped (container stop); partial files kept", artifact_dir=art)
                print(f"{run_id} FAILED/TERMINATED_BY_SIGNAL"); return 143
            from .engines import phase5_o2o
            ce = {}
            try:
                ce = json.loads((work / "compute_exit.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
            if ce.get("status") == "TIMEOUT" or compute_exit == 124:
                registry.finish(db, run_id, "FAILED", error_class="TIMEOUT", exit_reason="TIMEOUT", error_detail="compute deadline", artifact_dir=art)
                print(f"{run_id} FAILED/TIMEOUT"); return 1
            if ce.get("status") == "TERMINATED" or compute_exit == 143:
                registry.finish(db, run_id, "FAILED", error_class="TERMINATED", exit_reason="TERMINATED_BY_SIGNAL",
                                error_detail="compute received SIGTERM; partial files kept", artifact_dir=art)
                print(f"{run_id} FAILED/TERMINATED_BY_SIGNAL"); return 143
            try:
                ds = db.execute("SELECT * FROM datasets WHERE dataset_id=? AND dataset_version=?", (run["dataset_id"], run["dataset_version"])).fetchone()
                fields = phase5_o2o.finalize(run, ds, research_root, out, work, say,
                                             isolation=os.environ.get("QUANT_JOB_ISOLATION") or "UNDECLARED_BY_LAUNCHER")
                say(f"results_digest={fields['results_digest']}; registering artifacts")
                grade = fields.pop("_grade", None)
                _register_v2(db, run, out, fields)
                final = registry.finish(db, run_id, "SUCCEEDED", **fields)
                _record_grade(db, run_id, final, grade)
                print(f"{run_id} {final} {fields['results_digest']}"); return 0
            except Exception as exc:  # noqa: BLE001 - recorded, never crashes the scheduler
                detail = str(exc)[:500] + (" (compute exit 137: killed, e.g. out of memory)" if compute_exit == 137 else "")
                say(f"not accepted: {type(exc).__name__}: {detail}")
                registry.finish(db, run_id, "FAILED", error_class=type(exc).__name__, error_detail=detail[:500], exit_reason="ERROR", artifact_dir=art)
                print(f"{run_id} FAILED {type(exc).__name__}: {detail[:200]}"); return 1
    finally:
        db.close()


def register_reference(src: Path, input_sha: str) -> dict:
    """Copy the designated reference ledger set for the phase5 EXACT input into references/phase5_strategy_v1/<sha>/,
    verifying every file against the frozen first-round hash index. Idempotent; refuses partial or different sets."""
    import gzip
    import shutil
    from .engines import phase5_o2o
    cfg = json.loads((phase5_o2o.FROZEN_DIR / "config.json").read_text(encoding="utf-8"))
    if input_sha != cfg["expected_db_sha256"]:
        raise ValueError("NO_INDEX_FOR_INPUT: only the frozen exact input has a first-round hash index")
    idx_path = phase5_o2o.FROZEN_DIR / "results" / "private_output_hashes.json"
    fh = json.loads(idx_path.read_text(encoding="utf-8"))
    dst = registry.root() / "references" / "phase5_strategy_v1" / input_sha
    man = dst / "REFERENCE_MANIFEST.json"
    if man.is_file():
        m = json.loads(man.read_text(encoding="utf-8"))
        if m.get("index_sha256") == phase5_o2o.sha_file(idx_path) and m.get("files") == len(fh):
            return {**m, "state": "ALREADY_REGISTERED"}
        raise ValueError("REFERENCE_CONFLICT: a different reference set is registered for this input")
    bad = [k for k, v in fh.items() if not (src / k).is_file()
           or hashlib.sha256(gzip.decompress((src / k).read_bytes())).hexdigest() != v["uncompressed_sha256"]]
    if bad:
        raise ValueError(f"REFERENCE_MISMATCH: {len(bad)} of {len(fh)} files missing or differing, e.g. {bad[:3]}")
    dst.mkdir(parents=True, exist_ok=False)
    for k in sorted(fh):
        (dst / k).parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(src / k, dst / k)
    m = {"schema": "phase5_reference_ledgers_v1", "study_id": "phase5_strategy_v1", "input_sha256": input_sha, "files": len(fh),
         "index": "research/market_sentiment/phase5_strategy_v1/results/private_output_hashes.json", "index_sha256": phase5_o2o.sha_file(idx_path),
         "registered_at": registry.utcnow(), "nature": "first-round private ledgers of the frozen program (same author); private, never published"}
    man.write_text(json.dumps(m, indent=1), encoding="utf-8")
    return {**m, "state": "REGISTERED"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("worker"); w.add_argument("--once", action="store_true", required=True); w.add_argument("--max-seconds", type=int, default=300)
    sub.add_parser("recover")
    ro = sub.add_parser("run-one", help="execute exactly this QUEUED run (never another one)"); ro.add_argument("run_id"); ro.add_argument("--max-seconds", type=int, default=900)
    rt = sub.add_parser("retry"); rt.add_argument("run_id")
    s = sub.add_parser("snapshot")
    for a in ("--source", "--symbol", "--dataset-id", "--dataset-version", "--title", "--receipt", "--audit-status"):
        s.add_argument(a, required=True)
    s.add_argument("--autype", type=int, required=True); s.add_argument("--currency", default=None)
    sub.add_parser("register-defaults")
    rb = sub.add_parser("register-bundle", help="freeze a multi-series research SQLite (phase5 input) byte-for-byte and register it with v2 metadata")
    for x in ("--source", "--dataset-id", "--dataset-version", "--title", "--expected-sha256"):
        rb.add_argument(x, required=True)
    rs = sub.add_parser("register-snapshot"); rs.add_argument("rel_dir", help="datasets/<id>/<version> under the research root")
    e = sub.add_parser("enqueue"); e.add_argument("--dataset", required=True); e.add_argument("--factor", required=True); e.add_argument("--protocol", required=True)
    v = sub.add_parser("verify"); v.add_argument("run_id")
    sub.add_parser("capability")
    ad = sub.add_parser("admission"); ad.add_argument("action", choices=["pause", "open", "status"]); ad.add_argument("--reason", default="release or rollback maintenance")
    cq = sub.add_parser("cancel-queued"); cq.add_argument("--engine", required=True); cq.add_argument("--reason", required=True)
    wc = sub.add_parser("withdraw-capability"); wc.add_argument("--engine", required=True)
    rr = sub.add_parser("register-reference"); rr.add_argument("--source", required=True); rr.add_argument("--input-sha", required=True)
    jc = sub.add_parser("job-claim"); jc.add_argument("--worker-id", required=True); jc.add_argument("--run"); jc.add_argument("--max-seconds", type=int, default=900)
    js = sub.add_parser("job-supervise"); js.add_argument("run_id"); js.add_argument("--worker-id", required=True); js.add_argument("--max-seconds", type=int, default=900)
    jf = sub.add_parser("job-finalize"); jf.add_argument("run_id"); jf.add_argument("--compute-exit", type=int, required=True)
    jf.add_argument("--stop-reason", choices=["none", "cancel", "timeout", "terminated", "supervisor_lost"], default="none")
    a = ap.parse_args(argv)
    if a.cmd == "admission":
        f = registry.root() / registry.ADMISSION_FLAG
        if a.action == "pause":
            registry.root().mkdir(parents=True, exist_ok=True); f.write_text(f"{a.reason} (since {registry.utcnow()})\n", encoding="utf-8")
        elif a.action == "open":
            f.unlink(missing_ok=True)
        print(json.dumps({"admission": "PAUSED" if registry.admission_paused() else "OPEN", "reason": registry.admission_paused()})); return 0
    if a.cmd in ("cancel-queued", "withdraw-capability"):
        eid, ever = a.engine.split("@")
        db = registry.connect()
        try:
            if a.cmd == "cancel-queued":
                ids = [r[0] for r in db.execute("SELECT run_id FROM runs WHERE status='QUEUED' AND COALESCE(engine_id,?)=? AND COALESCE(engine_version,?)=?",
                                                (registry.LEGACY_ENGINE[0], eid, registry.LEGACY_ENGINE[1], ever))]
                for rid in ids:
                    db.execute("""UPDATE runs SET status='CANCELLED', cancel_requested=1, finished_at=?, exit_reason='CANCELLED_BEFORE_START',
                                  error_class='CANCELLED_BY_OPERATOR', error_detail=? WHERE run_id=? AND status='QUEUED'""", (registry.utcnow(), a.reason[:300], rid))
                db.commit(); print(json.dumps({"cancelled": ids}))
            else:
                n = db.execute("DELETE FROM worker_capabilities WHERE engine_id=? AND engine_version=?", (eid, ever)).rowcount
                db.commit(); print(json.dumps({"withdrawn_capability_rows": n, "engine": a.engine}))
        finally:
            db.close()
        return 0
    if a.cmd == "capability":
        from .engines import phase5_o2o
        print(json.dumps({"phase5_o2o_v1@1": phase5_o2o.capability()})); return 0
    if a.cmd == "job-claim":
        return job_claim(a.worker_id, a.run, a.max_seconds)
    if a.cmd == "job-supervise":
        return job_supervise(a.run_id, a.worker_id, a.max_seconds)
    if a.cmd == "job-finalize":
        return job_finalize(a.run_id, a.compute_exit, a.stop_reason)
    if a.cmd == "register-reference":
        print(json.dumps(register_reference(Path(a.source), a.input_sha))); return 0
    if a.cmd == "worker":
        return run_once(a.max_seconds)
    if a.cmd == "run-one":
        return run_specific(a.run_id, a.max_seconds)
    db = registry.connect()
    if a.cmd == "recover":
        print(json.dumps(registry.recover_lost(db, live_workers())))
    elif a.cmd == "retry":
        print(registry.retry_run(db, a.run_id, requested_by="cli"))
    elif a.cmd == "snapshot":
        rel = Path("datasets") / a.dataset_id / a.dataset_version
        m = build_snapshot(Path(a.source), a.symbol, a.autype, registry.root() / rel, dataset_id=a.dataset_id,
                           dataset_version=a.dataset_version, title=a.title, audit_status=a.audit_status,
                           source_receipt=json.loads(Path(a.receipt).read_text(encoding="utf-8")))
        if a.currency:
            m["currency"] = a.currency
            (registry.root() / rel / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
        registry.register_dataset(db, m, rel.as_posix()); print(json.dumps({k: m[k] for k in ("dataset_id", "dataset_version", "rows", "content_sha256")}))
    elif a.cmd == "register-snapshot":
        d = registry.root() / a.rel_dir
        m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        load_snapshot(d, m["content_sha256"])  # refuses a snapshot whose content does not match its manifest
        registry.register_dataset(db, m, Path(a.rel_dir).as_posix())
        print(json.dumps({k: m[k] for k in ("dataset_id", "dataset_version", "rows", "content_sha256")}))
    elif a.cmd == "register-defaults":
        for spec in FACTORS.values():
            registry.register_factor(db, spec)
        from . import catalog
        specs = catalog.register_builtin_specs(db)       # v2 specs: idempotent, conflicts refused
        imported = catalog.import_phase5(db)             # historical external import (not a run)
        print("registered", [f"{k[0]}@{k[1]}" for k in FACTORS], json.dumps(specs), imported)
    elif a.cmd == "register-bundle":
        from . import catalog
        m = catalog.register_bundle_snapshot(db, Path(a.source), a.dataset_id, a.dataset_version, a.title, catalog.PHASE5_DATASET_META,
                                             expected_sha256=a.expected_sha256)
        print(json.dumps({k: m.get(k) for k in ("state", "dataset_id", "dataset_version", "rows", "content_sha256")}))
    elif a.cmd == "enqueue":
        ds, fv = tuple(a.dataset.split("@")), tuple(a.factor.split("@"))
        print(registry.create_run(db, ds, fv, json.loads(Path(a.protocol).read_text(encoding="utf-8")), requested_by="cli"))
    elif a.cmd == "verify":
        from .verify import verify_run
        out = verify_run(registry.root() / "runs" / a.run_id); print(json.dumps(out, indent=1)); return 0 if out["ok"] else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
