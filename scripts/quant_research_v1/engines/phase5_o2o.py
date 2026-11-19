"""phase5_o2o_v1 engine adapter: runs the FROZEN phase5 program as a child process and packages its outputs.

The frozen research/market_sentiment/phase5_strategy_v1/{run.py,check.py,config.json,PROTOCOL_zh.md} are the
authority for signals, execution (close t -> next open -> next open), post-fee target weights, cash/units drift,
per-period reset, episode-end liquidation fees and calendar-time CAGR. This adapter never reimplements or edits
them: it verifies their hashes, runs them, stops them on cancel/timeout/SIGTERM, and writes platform artifacts.

Two roles (closeout 2026-11-20, finding F1):
  COMPUTE  `python -m scripts.quant_research_v1.engines.phase5_o2o compute --input DIR --work DIR --sha SHA`
           runs in its own container that sees ONLY the registered input snapshot (read-only mount) and the run's
           work directory (read-write); no registry, no other run, no network. It writes program outputs and
           compute_exit.json into the work directory, nothing else.
  CONTROL  finalize() runs in the scheduler's control container (registry access). It never trusts the compute
           step's claims: it re-hashes the input, refuses symlinks/special files in the work tree, recomputes every
           ledger hash, reconciles against the designated reference (finding F3) and only then packages artifacts.
execute() chains both in one process; it exists for unit tests and records isolation IN_PROCESS_TEST_ONLY.

Acceptance (finding F3): three required layers, reported separately -
  internal_self_check     the frozen check.py (same author) exit 0 + PASS over every ledger it claims
  output_integrity        control recomputes every ledger's raw SHA-256; must equal the program's index, same set
  reference_reconciliation  versioned rule phase5_reference_reconcile_v1 (pinned by hash): tables row by row in both
                          directions; ledgers by raw-hash identity, or row by row against the designated reference
                          ledger set when any hash differs. No reference -> INCOMPLETE, never PASS.
mechanical PASS (and a SUCCEEDED run) only when all three layers are PASS.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import os
import platform
import signal
import stat
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
RULE_PATH = Path(__file__).with_name("phase5_reconcile_rule_v1.json")
RULE_SHA256 = "491584b4dce2b7da7e0db4df696ca1fc2e0a1e00f82b3e57ec6932cbfe6f2165"  # pinned: editing the rule changes capability (see tests)
MKT = ("US.QQQ", "US..SOX", "SH.000001", "SH.000688")
STRATS = ("BUY_HOLD", "MA200", "P1_TREND", "P2_RECOVERY", "P3_RISK_BUDGET")
ARTIFACTS = ("resolved_config.json", "input_manifest.json", "code_manifest.json", "environment.json", "metrics.csv", "contrasts.csv",
             "factor_diagnostics.csv", "program_run_manifest.json", "private_output_hashes.json", "self_check.json", "mechanical_check.json",
             "science.json", "report.json", "signals_primary.csv.gz", "ledgers_all.tar", "program_stdout.log", "run.log")
PUBLIC_FILES = (("metrics.csv", "metrics.csv"), ("contrasts.csv", "contrasts.csv"), ("factor_diagnostics.csv", "factor_diagnostics.csv"),
                ("run_manifest.json", "program_run_manifest.json"), ("private_output_hashes.json", "private_output_hashes.json"),
                ("self_check.json", "self_check.json"))


class MechanicalCheckFailed(RuntimeError):
    """A required acceptance layer failed: the run is not accepted (files are kept for inspection)."""


class ReferenceUnavailable(MechanicalCheckFailed):
    """No designated reference for this input (or reference ledgers needed but absent): INCOMPLETE, not accepted."""


class ComputeFailed(RuntimeError):
    """The compute step did not complete (program failure, input mismatch, crash, OOM)."""


def sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_rule() -> dict:
    return json.loads(RULE_PATH.read_text(encoding="utf-8"))


def capability() -> dict:
    """What this image can actually execute: libraries importable AND frozen files AND the reconcile rule byte-identical."""
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
    rule_sha = sha_file(RULE_PATH) if RULE_PATH.is_file() else None
    out["reconcile_rule"] = {"rule_id": "phase5_reference_reconcile_v1", "sha256": rule_sha}
    if rule_sha != RULE_SHA256:
        out["reason"] = "RECONCILE_RULE_MISMATCH"; return out
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
        for p in sorted(x for x in src.rglob("*") if x.is_file() and not x.is_symlink()):
            info = tar.gettarinfo(str(p), arcname=p.relative_to(src).as_posix())
            info.mtime = 0; info.uid = info.gid = 0; info.uname = info.gname = ""; info.mode = 0o644
            with open(p, "rb") as f:
                tar.addfile(info, f)


# ---------------------------------------------------------------- strict comparison (F3)
def cell_class(v) -> str:
    if v is None:
        return "MISSING_COLUMN"
    s = str(v).strip()
    if s == "":
        return "EMPTY"
    try:
        x = float(s)
    except ValueError:
        return "TEXT"
    if math.isnan(x):
        return "NAN"
    if math.isinf(x):
        return "POS_INF" if x > 0 else "NEG_INF"
    return "FINITE"


def reconcile_rows(actual: list[dict], reference: list[dict], keys, *, header_a=None, header_r=None, status_column=None,
                   allowed_status=None, ordered: bool = False, rule: dict | None = None) -> dict:
    """Strict row-by-row reconciliation of `actual` against `reference` (rule phase5_reference_reconcile_v1).
    Checks schema, duplicate keys on both sides, key sets in both directions, optional row order, legal statuses,
    the missing / non-finite class of every cell, text equality and the pre-declared finite tolerance."""
    tol = (rule or load_rule())["finite_numeric"]
    keys = tuple(keys)
    ha = list(header_a) if header_a is not None else (list(actual[0].keys()) if actual else [])
    hr = list(header_r) if header_r is not None else (list(reference[0].keys()) if reference else [])
    res = {"rows": [len(actual), len(reference)], "schema_equal": ha == hr, "problems": []}
    P = res["problems"]
    if ha != hr:
        P.append(f"SCHEMA_MISMATCH: actual {ha[:30]} reference {hr[:30]}")
    missing_key_cols = [k for k in keys if k not in ha or k not in hr]
    if missing_key_cols:
        P.append(f"KEY_COLUMNS_MISSING: {missing_key_cols}")
        res.update(ok=False, unmatched_rows=len(actual) + len(reference), max_abs_diff={}); return res

    def index(rows, side):
        ix, dup = {}, []
        for r in rows:
            k = tuple(r.get(c) for c in keys)
            if k in ix:
                dup.append(k)
            else:
                ix[k] = r
        if dup:
            P.append(f"DUPLICATE_KEYS_{side}: {len(dup)} e.g. {list(dup[:3])}")
        return ix, dup
    ia, dup_a = index(actual, "ACTUAL")
    ir, dup_r = index(reference, "REFERENCE")
    only_a = [k for k in ia if k not in ir]; only_r = [k for k in ir if k not in ia]
    if only_a:
        P.append(f"EXTRA_ROWS_IN_ACTUAL: {len(only_a)} e.g. {only_a[:3]}")
    if only_r:
        P.append(f"MISSING_ROWS_IN_ACTUAL: {len(only_r)} e.g. {only_r[:3]}")
    if ordered and not only_a and not only_r and not dup_a and not dup_r:
        if [tuple(r.get(c) for c in keys) for r in actual] != [tuple(r.get(c) for c in keys) for r in reference]:
            P.append("ROW_ORDER_DIFFERS")
    cols = list(dict.fromkeys(ha + hr))
    worst, exceeded, class_bad, text_bad, illegal = {}, 0, 0, 0, []
    for k, a in ia.items():
        r = ir.get(k)
        if r is None:
            continue
        for c in cols:
            if c in keys:
                continue
            va, vr = a.get(c), r.get(c)
            ca, cr = cell_class(va), cell_class(vr)
            if ca != cr:
                class_bad += 1
                if class_bad <= 5:
                    P.append(f"CELL_CLASS_DIFFERS {k} {c}: actual {ca} reference {cr}")
                continue
            if ca == "TEXT" and str(va).strip() != str(vr).strip():
                text_bad += 1
                if text_bad <= 5:
                    P.append(f"TEXT_DIFFERS {k} {c}")
                continue
            if ca == "FINITE":
                x, y = float(va), float(vr); d = abs(x - y)
                worst[c] = max(worst.get(c, 0.0), d)
                if d > tol["abs_tol"] + tol["rel_tol"] * abs(y):
                    exceeded += 1
                    if exceeded <= 5:
                        P.append(f"TOLERANCE_EXCEEDED {k} {c}: |{x} - {y}| = {d:.3g}")
        if status_column:
            st = a.get(status_column)
            if allowed_status is not None and st not in allowed_status:
                illegal.append(st)
    if illegal:
        P.append(f"ILLEGAL_STATUS: {sorted(set(map(str, illegal)))}")
    for side, rows in (("ACTUAL", actual), ("REFERENCE", reference)):
        if status_column and allowed_status is not None and side == "REFERENCE":
            bad = sorted({str(r.get(status_column)) for r in rows if r.get(status_column) not in allowed_status})
            if bad:
                P.append(f"ILLEGAL_STATUS_IN_REFERENCE: {bad}")
    res.update(unmatched_rows=len(only_a) + len(only_r), duplicate_keys=[len(dup_a), len(dup_r)], cells_class_differs=class_bad,
               cells_text_differs=text_bad, cells_over_tolerance=exceeded, max_abs_diff=worst, ok=not P)
    return res


def _num_diff(a_rows, b_rows, keys, skip=()):
    """Kept name (review probe F3): now the strict reconciler; `ok` is False on any mismatch."""
    return reconcile_rows(a_rows, b_rows, keys)


def _read_csv(p: Path) -> tuple[list, list[dict]]:
    text = p.read_text(encoding="utf-8")
    rd = csv.DictReader(io.StringIO(text))
    rows = list(rd)
    return (rd.fieldnames or []), rows


def _read_gz_csv(p: Path) -> list[dict]:
    return list(csv.DictReader(io.StringIO(gzip.decompress(p.read_bytes()).decode("utf-8"))))


def _gz_csv_with_header(p: Path) -> tuple[list, list[dict]]:
    rd = csv.DictReader(io.StringIO(gzip.decompress(p.read_bytes()).decode("utf-8")))
    rows = list(rd)
    return (rd.fieldnames or []), rows


def _unsafe_entries(root: Path) -> list[str]:
    """Anything in the compute work tree that is not a plain directory or regular file (symlink, fifo, device...)."""
    bad = []
    if not root.exists():
        return bad
    stack = [root]
    while stack:
        d = stack.pop()
        with os.scandir(d) as it:
            for e in it:
                st = e.stat(follow_symlinks=False)
                if stat.S_ISLNK(st.st_mode) or not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)):
                    bad.append(Path(e.path).relative_to(root).as_posix())
                elif stat.S_ISDIR(st.st_mode):
                    stack.append(Path(e.path))
    return sorted(bad)


def ledger_index(priv: Path) -> dict:
    """rel path ('ledgers/<market>/<period>/<strategy>_c<cost>.csv.gz') -> raw SHA-256 and rows, recomputed here."""
    out = {}
    base = priv / "ledgers"
    if not base.is_dir():
        return out
    for p in sorted(base.rglob("*.csv.gz")):
        if p.is_symlink() or not p.is_file():
            continue
        raw = gzip.decompress(p.read_bytes())
        out[p.relative_to(priv).as_posix()] = {"uncompressed_sha256": hashlib.sha256(raw).hexdigest(), "rows": max(0, raw.count(b"\n") - 1)}
    return out


def reference_for(mode: str, input_sha: str, research_root: Path) -> dict | None:
    """The designated reference for an input: only the frozen first-round results, and only for the exact input."""
    if mode != "exact":
        return None
    ref_ledgers = research_root / "references" / "phase5_strategy_v1" / input_sha
    return {"kind": "FROZEN_FIRST_ROUND_RESULTS (same frozen program, same input)", "results_dir": FROZEN_DIR / "results",
            "ledger_root": ref_ledgers if (ref_ledgers / "REFERENCE_MANIFEST.json").is_file() else None}


def mechanical_check(work: Path, ce: dict, mode: str, input_sha: str, research_root: Path) -> dict:
    if sha_file(RULE_PATH) != RULE_SHA256:
        raise MechanicalCheckFailed("RECONCILE_RULE_MISMATCH: the versioned rule file differs from the pinned hash")
    rule = load_rule()
    priv, pub = work / "program_private", work / "program_public"
    claimed = json.loads((pub / "private_output_hashes.json").read_text(encoding="utf-8")) if (pub / "private_output_hashes.json").is_file() else {}
    sc = json.loads((pub / "self_check.json").read_text(encoding="utf-8")) if (pub / "self_check.json").is_file() else None
    rr = (sc or {}).get("real_run", {}) if isinstance(sc, dict) else {}
    # L1: the frozen program's own check (same author, same input)
    l1 = {"what": "frozen check.py over the ledgers it produced (same author; not independent)", "check_py_exit": ce.get("check_py_exit"),
          "status_reported": rr.get("status"), "ledger_files_recomputed": rr.get("ledger_files_recomputed"), "ledger_files_claimed": len(claimed)}
    l1["status"] = "PASS" if (ce.get("check_py_exit") == 0 and rr.get("status") == "PASS_SAME_AUTHOR_LEDGER_AND_INPUT_CHECK"
                              and len(claimed) > 0 and rr.get("ledger_files_recomputed") == len(claimed)) else "FAIL"
    # L2: control recomputes what is actually on disk
    unsafe = _unsafe_entries(work)
    actual = ledger_index(priv)
    missing_pub = [n for n, _ in PUBLIC_FILES if not (pub / n).is_file()]
    mism = sorted(k for k in set(actual) & set(claimed) if actual[k]["uncompressed_sha256"] != claimed[k].get("uncompressed_sha256"))
    l2 = {"what": "control step re-hashes every ledger file on disk and compares with the program's own index",
          "unsafe_entries": unsafe[:20], "missing_public_files": missing_pub, "ledgers_on_disk": len(actual), "ledgers_claimed": len(claimed),
          "on_disk_not_claimed": sorted(set(actual) - set(claimed))[:10], "claimed_not_on_disk": sorted(set(claimed) - set(actual))[:10],
          "hash_differs_from_claim": mism[:10]}
    l2["status"] = "PASS" if (not unsafe and not missing_pub and actual and set(actual) == set(claimed) and not mism) else "FAIL"
    # L3: designated reference
    ref = reference_for(mode, input_sha, research_root)
    if ref is None:
        l3 = {"status": "NOT_AVAILABLE", "reason": f"no designated reference for input {input_sha[:12]} (mode {mode}); results are NOT verified"}
    else:
        first = ref["results_dir"]
        l3 = {"reference": ref["kind"], "rule_id": rule["rule_id"], "rule_sha256": sha_file(RULE_PATH), "tables": {}}
        ok3 = True
        for name, spec in rule["tables"].items():
            if not (pub / name).is_file() or not (first / name).is_file():
                l3["tables"][name] = {"ok": False, "problems": ["file missing on one side"]}; ok3 = False; continue
            ha, ra = _read_csv(pub / name); hr, rf = _read_csv(first / name)
            t = reconcile_rows(ra, rf, spec["keys"], header_a=ha, header_r=hr, status_column=spec.get("status_column"),
                               allowed_status=spec.get("allowed_status"), rule=rule)
            t["byte_identical"] = (pub / name).read_bytes().replace(b"\r\n", b"\n") == (first / name).read_bytes().replace(b"\r\n", b"\n")
            t["problems"] = t["problems"][:12]
            l3["tables"][name] = t; ok3 = ok3 and t["ok"]
        fh = json.loads((first / "private_output_hashes.json").read_text(encoding="utf-8"))
        same = sum(1 for k, v in fh.items() if actual.get(k, {}).get("uncompressed_sha256") == v["uncompressed_sha256"])
        led = {"reference_ledgers": len(fh), "actual_ledgers": len(actual), "set_equal": set(fh) == set(actual), "raw_sha256_identical": same,
               "missing_in_actual": sorted(set(fh) - set(actual))[:10], "extra_in_actual": sorted(set(actual) - set(fh))[:10]}
        if not led["set_equal"] or not fh:
            led.update(path="LEDGER_SET_DIFFERS", ok=False, problem="ledger sets differ (missing or extra ledgers): a definite mismatch")
        elif same == len(fh):
            led.update(path="RAW_SHA256_IDENTICAL", ok=True)
        elif ref["ledger_root"] is None:
            led.update(path="ROW_BY_ROW_REQUIRED", ok=False, reference_ledgers_available=False,
                       problem="raw hashes differ (or sets differ) and no designated reference ledger set is registered for this input")
        else:
            lr = ref["ledger_root"]; bad_ref, rows_bad = [], {}
            for k in sorted(fh):
                rp, ap = lr / k, priv / k
                if not rp.is_file():
                    bad_ref.append(k); continue
                if hashlib.sha256(gzip.decompress(rp.read_bytes())).hexdigest() != fh[k]["uncompressed_sha256"]:
                    bad_ref.append(k); continue
                if actual[k]["uncompressed_sha256"] == fh[k]["uncompressed_sha256"]:
                    continue
                ha, ra = _gz_csv_with_header(ap); hr, rf = _gz_csv_with_header(rp)
                t = reconcile_rows(ra, rf, rule["ledgers"]["keys"], header_a=ha, header_r=hr, ordered=True, rule=rule)
                if not t["ok"]:
                    rows_bad[k] = t["problems"][:5]
            led.update(path="ROW_BY_ROW_AGAINST_REFERENCE_LEDGERS", reference_ledgers_available=True, reference_ledgers_corrupt=bad_ref[:10],
                       ledgers_failing=len(rows_bad), failing_examples=dict(list(rows_bad.items())[:5]), ok=not bad_ref and not rows_bad)
        l3["ledgers"] = led
        ok3 = ok3 and led["ok"]
        l3["status"] = "PASS" if ok3 else ("NOT_AVAILABLE" if led.get("path") == "ROW_BY_ROW_REQUIRED" and all(
            t.get("ok") for t in l3["tables"].values()) else "FAIL")
    layers = {"internal_self_check": l1, "output_integrity": l2, "reference_reconciliation": l3}
    states = [v["status"] for v in layers.values()]
    status = "PASS" if all(s == "PASS" for s in states) else ("FAIL" if "FAIL" in states else "INCOMPLETE")
    return {"schema": "phase5_mechanical_check_v2", "rule_id": rule["rule_id"], "rule_sha256": sha_file(RULE_PATH), "mode": mode,
            "required_layers": rule["required_layers"], "layers": layers, "status": status,
            "meaning": "PASS = all required layers passed; INCOMPLETE = a reference is missing (not accepted); FAIL = a layer found a mismatch"}


# ---------------------------------------------------------------- COMPUTE role
def _mount_summary() -> list:
    """What this process can see as mounts (Linux): evidence written by the compute step, checked by tests."""
    try:
        lines = Path("/proc/self/mountinfo").read_text().splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        parts = ln.split()
        if len(parts) > 5 and parts[4] in ("/input", "/work", "/research", "/app/apps/quant_portal/var", "/inputs", "/"):
            out.append({"mount": parts[4], "root": parts[3], "options": parts[5]})
    return out


def compute(input_dir: Path, work: Path, content_sha256: str, stop_now, say) -> dict:
    """Run the frozen program on the snapshot in `input_dir`; write only below `work`. Returns compute_exit."""
    ce = {"schema": "phase5_compute_exit_v1", "status": "ERROR", "engine": "@".join(ENGINE)}
    try:
        cap = capability()
        ce.update(python=platform.python_version(), libs=cap.get("libs"))
        if not cap["ok"]:
            raise ComputeFailed(f"ENGINE_UNAVAILABLE: {cap.get('reason')}")
        cfg = json.loads((FROZEN_DIR / "config.json").read_text(encoding="utf-8"))
        snap = input_dir / "market_research.sqlite"
        t0 = time.monotonic(); sha = sha_file(snap)
        ce["input_sha256"] = sha
        if sha != content_sha256:
            ce["status"] = "INPUT_HASH_MISMATCH"; raise ComputeFailed(f"INPUT_HASH_MISMATCH: snapshot {sha} != registered {content_sha256}")
        mode = "exact" if sha == cfg["expected_db_sha256"] else "external"
        manifest = json.loads((input_dir / "manifest.json").read_text(encoding="utf-8"))
        end = cfg["data_end"] if mode == "exact" else manifest["end_date"]
        ce.update(mode=mode, data_end=end, mounts=_mount_summary())
        say(f"input verified sha256={sha} ({time.monotonic() - t0:.1f}s); mode={mode}; data_end={end}")
        priv, pub = work / "program_private", work / "program_public"
        py = sys.executable
        with open(work / "program_stdout.log", "w", encoding="utf-8") as log:
            cmd = [py, "run.py", "--db", str(snap), "--private-out", str(priv), "--public-out", str(pub), "--freeze-commit", FREEZE_COMMIT, "--mode", mode]
            if mode == "external":
                cmd += ["--end", end]
            log.write("# " + " ".join("<input>" if c == str(snap) else c for c in cmd) + "\n"); log.flush()
            rc, secs, rss = _child(cmd, FROZEN_DIR, log, stop_now)
            ce.update(run_py_exit=rc, run_py_seconds=round(secs, 1), peak_child_rss_mb=rss)
            say(f"frozen run.py exit={rc} in {secs:.1f}s peak_child_rss_mb={rss}")
            if rc != 0:
                ce["status"] = "PROGRAM_FAILED"; raise ComputeFailed(f"FROZEN_PROGRAM_FAILED: run.py exit {rc}")
            log.write("# check.py --db <input> --private-out ... --public-out ...\n"); log.flush()
            rc2, secs2, rss2 = _child([py, "check.py", "--db", str(snap), "--private-out", str(priv), "--public-out", str(pub)], FROZEN_DIR, log, stop_now)
            ce.update(check_py_exit=rc2, check_py_seconds=round(secs2, 1),
                      peak_child_rss_mb=max([x for x in (rss, rss2) if x is not None], default=None))
            say(f"frozen check.py exit={rc2} in {secs2:.1f}s")
        ce["status"] = "COMPLETED"
        return ce
    except BaseException as exc:
        if ce["status"] == "ERROR":
            ce["status"] = {"RunTimeoutSignal": "TIMEOUT", "RunTerminatedSignal": "TERMINATED"}.get(type(exc).__name__, "ERROR")
        ce["error"] = f"{type(exc).__name__}: {exc}"[:400]
        raise
    finally:
        (work / "compute_exit.json").write_text(json.dumps(ce, indent=1, default=str), encoding="utf-8")


class RunTimeoutSignal(Exception):
    pass


class RunTerminatedSignal(Exception):
    pass


def compute_main(argv=None) -> int:
    """Entry of the COMPUTE container. Exit 0 = completed; 3 = program/input failure; 124 = timeout; 143 = SIGTERM."""
    ap = argparse.ArgumentParser()
    ap.add_argument("role", choices=["compute"])
    ap.add_argument("--input", required=True); ap.add_argument("--work", required=True); ap.add_argument("--sha", required=True)
    ap.add_argument("--max-seconds", type=int, default=900)
    a = ap.parse_args(argv)
    deadline = time.monotonic() + a.max_seconds

    def on_term(*_):
        raise RunTerminatedSignal()
    signal.signal(signal.SIGTERM, on_term)

    def stop_now():
        if time.monotonic() > deadline:
            raise RunTimeoutSignal()
    work = Path(a.work)
    with open(work / "compute.log", "a", encoding="utf-8") as lg:
        def say(msg):
            lg.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}\n"); lg.flush()
        try:
            compute(Path(a.input), work, a.sha, stop_now, say); return 0
        except RunTimeoutSignal:
            say("compute deadline reached; frozen child stopped"); return 124
        except RunTerminatedSignal:
            say("SIGTERM; frozen child stopped"); return 143
        except Exception as exc:  # noqa: BLE001
            say(f"compute failed: {type(exc).__name__}: {exc}"[:400]); return 3


# ---------------------------------------------------------------- CONTROL role
def finalize(run, ds, research_root: Path, out: Path, work: Path, say, isolation: str) -> dict:
    """Verify the compute step's work tree independently, decide acceptance, then package the platform artifacts in `out`."""
    cfg = json.loads((FROZEN_DIR / "config.json").read_text(encoding="utf-8"))
    protocol = json.loads(run["config_json"])
    snap = research_root / ds["snapshot_path"] / "market_research.sqlite"
    unsafe = _unsafe_entries(work)
    if unsafe:
        raise MechanicalCheckFailed(f"UNSAFE_WORK_TREE: {unsafe[:5]}")
    cep = work / "compute_exit.json"
    if not cep.is_file():
        raise ComputeFailed("COMPUTE_EXIT_MISSING: the compute step ended without a report (crash, kill or OOM)")
    ce = json.loads(cep.read_text(encoding="utf-8"))
    if ce.get("status") != "COMPLETED":
        raise ComputeFailed(f"COMPUTE_{ce.get('status')}: {ce.get('error', '')}"[:400])
    sha_after = sha_file(snap)
    if sha_after != ds["content_sha256"]:
        raise MechanicalCheckFailed(f"INPUT_CHANGED_DURING_RUN: {sha_after} != registered {ds['content_sha256']}")
    mode = "exact" if ds["content_sha256"] == cfg["expected_db_sha256"] else "external"
    if ce.get("mode") != mode or ce.get("input_sha256") != ds["content_sha256"]:
        raise MechanicalCheckFailed(f"COMPUTE_REPORT_INCONSISTENT: mode {ce.get('mode')} vs {mode}")
    manifest = json.loads((research_root / ds["snapshot_path"] / "manifest.json").read_text(encoding="utf-8"))
    end = cfg["data_end"] if mode == "exact" else manifest["end_date"]
    pub, priv = work / "program_public", work / "program_private"
    (out / "resolved_config.json").write_text(json.dumps({"engine": {"engine_id": ENGINE[0], "engine_version": ENGINE[1]}, "protocol": protocol,
        "frozen_config": cfg, "mode": mode, "data_end": end, "freeze_commit": FREEZE_COMMIT,
        "contracts": json.loads(run["contracts_json"]) if run["contracts_json"] else None}, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "input_manifest.json").write_text(json.dumps({"dataset_id": ds["dataset_id"], "dataset_version": ds["dataset_version"],
        "snapshot_rel": f"{ds['snapshot_path']}/market_research.sqlite", "sha256": ds["content_sha256"], "sha256_after_run": sha_after,
        "bytes": snap.stat().st_size, "series": manifest.get("series"), "unresolved_dates": manifest.get("unresolved_dates"),
        "isolation": isolation, "compute_mounts_seen_by_compute": ce.get("mounts")}, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "code_manifest.json").write_text(json.dumps({"frozen_program": dict(FROZEN_SHA256), "freeze_commit": FREEZE_COMMIT,
        "adapter": {"path": "scripts/quant_research_v1/engines/phase5_o2o.py", "sha256": sha_file(Path(__file__))},
        "reconcile_rule": {"path": "scripts/quant_research_v1/engines/phase5_reconcile_rule_v1.json", "sha256": sha_file(RULE_PATH)},
        "platform_code_version": os.environ.get("QUANT_CODE_VERSION")}, indent=1), encoding="utf-8")
    if (work / "program_stdout.log").is_file() and work != out:
        (out / "program_stdout.log").write_bytes((work / "program_stdout.log").read_bytes())
    for n, dst in PUBLIC_FILES:
        if (pub / n).is_file():
            (out / dst).write_bytes((pub / n).read_bytes().replace(b"\r\n", b"\n"))
    mech = mechanical_check(work, ce, mode, ds["content_sha256"], research_root)
    (out / "mechanical_check.json").write_text(json.dumps(mech, indent=1, default=str), encoding="utf-8")
    say("acceptance layers: " + ", ".join(f"{k}={v['status']}" for k, v in mech["layers"].items()) + f" -> mechanical {mech['status']}")
    if mech["status"] == "INCOMPLETE":
        raise ReferenceUnavailable(f"REFERENCE_UNAVAILABLE: {mech['layers']['reference_reconciliation'].get('reason') or 'reference ledgers needed'}"[:400])
    if mech["status"] != "PASS":
        failing = [k for k, v in mech["layers"].items() if v["status"] != "PASS"]
        raise MechanicalCheckFailed(f"MECHANICAL_CHECK_FAILED: layers {failing}"[:400])
    metrics = list(csv.DictReader(io.StringIO((out / "metrics.csv").read_text(encoding="utf-8"))))
    contrasts = list(csv.DictReader(io.StringIO((out / "contrasts.csv").read_text(encoding="utf-8"))))
    hashes = json.loads((out / "private_output_hashes.json").read_text(encoding="utf-8"))
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
              f"phase5 冻结程序 · {('同输入（exact）' if mode == 'exact' else '外部输入（external）')} · 输入 {ds['content_sha256'][:12]}… 截至 {end} · 12 项主检验 {passes} 项通过 · 机械复核 {mech['status']}",
              "primary_metrics": [{k: (fl(m[k]) if k in ("cagr", "max_drawdown", "mean_exposure", "cost_fraction_sum", "descriptive_sharpe_sqrt252", "terminal_wealth") else m[k])
                                   for k in ("symbol", "strategy", "cost_bps", "cagr", "max_drawdown", "mean_exposure", "cost_fraction_sum", "descriptive_sharpe_sqrt252", "terminal_wealth", "first_exec", "last_mark", "intervals")}
                                  for m in executed if m["period"] == "primary"],
              "primary_tests": [{k: c[k] for k in ("symbol", "strategy", "n", "mean_daily_net_delta", "ci_low_daily", "ci_high_daily", "p_one_sided_positive", "holm_p",
                                                   "mean_daily_delta_cost25", "first_screen_return_increment_pass")} for c in prim],
              "curves": curves, "science": science,
              "mechanical": {"status": mech["status"], "layers": {k: v["status"] for k, v in mech["layers"].items()},
                             "same_input": mech["layers"]["reference_reconciliation"].get("ledgers", {}).get("raw_sha256_identical"),
                             "ledger_path": mech["layers"]["reference_reconciliation"].get("ledgers", {}).get("path"), "ledger_files": len(hashes)},
              "isolation": isolation,
              "limits_zh": "这是工程接入与同实现复算，不是新的独立策略验证。QQQ 为 ETF（a1 前复权，未认证总收益），其余三个为指数代理（不可直接交易）；数据为当前版本回填、非时点；历史已看过。成本与现金收益为假设。T1/T2 缺历史新闻/事件输入。"}
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    env = {"python": ce.get("python"), "libs": ce.get("libs"), "image_id": os.environ.get("QUANT_IMAGE_ID"), "lockfile_sha256": os.environ.get("QUANT_NUMERIC_LOCK_SHA256"),
           "code_version": os.environ.get("QUANT_CODE_VERSION"), "network": "none expected (job container)", "isolation": isolation,
           "resources": {"run_py_seconds": ce.get("run_py_seconds"), "check_py_seconds": ce.get("check_py_seconds"), "peak_child_rss_mb": ce.get("peak_child_rss_mb")}}
    (out / "environment.json").write_text(json.dumps(env, indent=1), encoding="utf-8")
    h = hashlib.sha256()
    for n in ("metrics.csv", "contrasts.csv", "factor_diagnostics.csv"):
        h.update(n.encode()); h.update(hashlib.sha256((out / n).read_bytes()).digest())
    for k in sorted(hashes):
        h.update(k.encode()); h.update(hashes[k]["uncompressed_sha256"].encode())
    digest = h.hexdigest()
    led = mech["layers"]["reference_reconciliation"]["ledgers"]
    summary = {"engine": "@".join(ENGINE), "mode": mode, "results_digest": digest, "scientific_status": status, "primary_passes": passes,
               "mechanical_check": mech["status"], "ledger_files": len(hashes), "resources": env["resources"], "isolation": isolation}
    mech_txt = (f"PASS (internal self-check PASS; integrity {len(hashes)}/{len(hashes)} re-hashed; reference {led['path']} "
                f"{led['raw_sha256_identical']}/{led['reference_ledgers']}; tables per {mech['rule_id']})")
    return {"artifact_dir": f"runs/{run['run_id']}", "results_digest": digest, "summary_json": json.dumps(summary), "exit_reason": "COMPLETED",
            "_artifacts": list(ARTIFACTS), "_grade": {"software": "SUCCEEDED", "mechanical": mech_txt, "scientific": status, "mode": mode, "digest": digest}}


def execute(run, ds, research_root: Path, out: Path, say, stop_now) -> dict:
    """Both roles in ONE process (unit tests / development only): no mount isolation, recorded as such."""
    input_root = Path(os.environ.get("QUANT_RESEARCH_INPUT_ROOT") or research_root)
    say(f"engine {ENGINE[0]}@{ENGINE[1]} in-process (IN_PROCESS_TEST_ONLY: no mount isolation)")
    compute(input_root / ds["snapshot_path"], out, ds["content_sha256"], stop_now, say)
    stop_now()
    return finalize(run, ds, research_root, out, out, say, isolation="IN_PROCESS_TEST_ONLY (no mount isolation)")


if __name__ == "__main__":
    sys.exit(compute_main())
