"""Research backup and isolated restore check (runs inside the app image as uid 10001, no network).

backup SRC_VAR OUT_DIR [--scope SCOPE_JSON]
    SRC_VAR is the personal_var volume (research files under research/). The CALLER HOLDS the
    research lock, so the registry copy and the file set belong to one checkpoint. Registry: SQLite
    online backup, single-file copy, quick_check, streaming full-table SHA-256. Files: every file
    under research/{datasets,runs,audits} with relative path, size and SHA-256. Frozen protocols,
    code version and image id recorded. If research backup is REQUIRED by the scope file and the
    registry is missing, the backup FAILS (exit 1).
verify BACKUP_DIR RESTORE_DIR [--scope SCOPE_JSON] [--run RUN_ID]
    Restores ONLY from BACKUP_DIR into RESTORE_DIR (production volume never mounted) and checks the
    required scope: registry, manifest, every manifest file (hash and size), required per-dataset and
    per-run files, protocols, table digests. Then re-runs every SUCCEEDED run under a NEW run id via
    run_specific (exactly that id; earlier QUEUED/RUNNING requests are never claimed or changed), and
    compares config hash, data hash, results_digest and every ledger byte-for-byte. Missing scope =
    structured failure, exit 1. SKIPPED only when the scope file says NOT_REQUIRED.
    v2 runs (engine_id set, e.g. phase5_o2o_v1): every artifact registered for the run is REQUIRED and must match
    the registry's size and SHA-256. Re-execution happens only for runs whose engine THIS image supports
    (QUANT_WORKER_ENGINES + live capability); others are hash-checked and reported as not re-executed here.
    --run RUN_ID restores everything but re-executes ONLY that run (new run id, attempt_of = original, same
    frozen config and engine) and compares results_digest and every deterministic artifact byte-for-byte.
    --v2-via-launcher (closeout 2026-11-20, F1): v2 re-executions are NOT run inside this process. The new run rows
    are queued (attempt_of = original) and listed in pending_protected_reruns with state PENDING_PROTECTED_RERUNS;
    the host then executes exactly those ids with deploy/linux/host/quant-research-job.sh --var-dir RESTORE_DIR
    (the same control/compute separation as production) and calls verify-finish.
verify-finish RESTORE_DIR
    Compares every protected re-execution listed by the prepare step (status, attempt_of, config hash,
    results_digest, deterministic artifacts byte-for-byte) and checks that no original run record changed.
Exit code: 0 only when ok (prepare with pending reruns: 0 = ready for the launcher). stdout is one JSON document.
"""
import contextlib, datetime, hashlib, json, os, pathlib, shutil, sqlite3, sys, traceback

TERMINAL = ("SUCCEEDED", "FAILED", "CANCELLED")
RUN_FILES = ("config.json", "metrics.json", "curves.json", "verify.json", "run.log")
# v2 artifacts that must be byte-identical on a re-execution of the same frozen config and input
# (timestamps live only in run.log, program_stdout.log, environment.json, self_check.json, program_run_manifest.json,
# input_manifest.json/code_manifest.json may differ only in mount flags / platform code version).
DETERMINISTIC_V2 = ("metrics.csv", "contrasts.csv", "factor_diagnostics.csv", "private_output_hashes.json", "signals_primary.csv.gz",
                    "ledgers_all.tar", "report.json", "science.json", "mechanical_check.json")
DEFAULT_SCOPE = {"research": {"requirement": "REQUIRED", "required": {"protocols": ["quant_research_mom20_v1.json"]}}}


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha_file(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_scope(path) -> dict:
    """The scope must be explicit. Without a readable scope file research backup is REQUIRED."""
    if path:
        try:
            return json.loads(pathlib.Path(path).read_text())
        except (OSError, ValueError):
            pass
    return DEFAULT_SCOPE


def table_digests(path: pathlib.Path) -> dict:
    """Streaming SHA-256 over every row of every table, ordered by all columns. No size limit."""
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        out = {}
        for (name,) in con.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name"):
            cols = [r[1] for r in con.execute(f'pragma table_info("{name}")')]
            q = ",".join('"' + c + '"' for c in cols)
            h, n = hashlib.sha256(), 0
            for row in con.execute(f'select {q} from "{name}" order by {q}'):
                h.update(json.dumps(row, default=str).encode()); h.update(b"\n"); n += 1
            out[name] = {"rows": n, "sha256": h.hexdigest()}
        return out
    finally:
        con.close()


def backup(src: pathlib.Path, out: pathlib.Path, scope: dict) -> dict:
    research = src / "research"
    required = scope["research"]["requirement"] == "REQUIRED"
    if not (research / "registry.sqlite3").is_file():
        return {"ok": not required, "state": "FAILED_MISSING_REGISTRY" if required else "SKIPPED_NOT_REQUIRED",
                "detail": f"{research / 'registry.sqlite3'} not found", "requirement": scope["research"]["requirement"]}
    rout = out / "research"; (rout / "files").mkdir(parents=True, exist_ok=False)
    reg_copy = rout / "registry.sqlite3"
    s, d = sqlite3.connect(research / "registry.sqlite3", timeout=60), sqlite3.connect(reg_copy)
    try:
        s.backup(d)
    finally:
        s.close(); d.close()
    con = sqlite3.connect(reg_copy); con.execute("pragma journal_mode=DELETE")
    check = con.execute("pragma quick_check").fetchone()[0]
    con.row_factory = sqlite3.Row
    cols = {r[1] for r in con.execute("pragma table_info(runs)")}
    eng = ", engine_id, engine_version" if "engine_id" in cols else ""
    runs = [dict(r) for r in con.execute(f"select run_id, status, results_digest, artifact_dir, dataset_id, dataset_version, config_sha256{eng} from runs order by created_at")]
    datasets = [dict(r) for r in con.execute("select dataset_id, dataset_version, content_sha256, snapshot_path, manifest_json from datasets")]
    for d in datasets:
        d["files"] = json.loads(d.pop("manifest_json")).get("files") or ["data.csv", "manifest.json"]
    tabs = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    artifacts = [dict(r) for r in con.execute("select run_id, name, rel_path, bytes, sha256 from artifacts order by run_id, name")] if "artifacts" in tabs else []
    con.close()
    for side in ("-wal", "-shm"):
        pathlib.Path(str(reg_copy) + side).unlink(missing_ok=True)
    incomplete = {r["run_id"] for r in runs if r["status"] not in TERMINAL}
    files = []
    for top in ("datasets", "runs", "audits", "references"):
        base = research / top
        if not base.is_dir():
            continue
        for p in sorted(x for x in base.rglob("*") if x.is_file()):
            rel = p.relative_to(research).as_posix()
            dst = rout / "files" / rel; dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst)
            run_id = rel.split("/")[1] if rel.startswith("runs/") else None
            files.append({"rel": rel, "size": dst.stat().st_size, "sha256": sha_file(dst), "run_id": run_id, "complete": run_id not in incomplete})
    protocols = []
    (rout / "protocols").mkdir()
    for p in sorted(pathlib.Path(os.environ.get("QUANT_PROTOCOL_DIR", "/app/configs")).glob("quant_research_*.json")):
        shutil.copy2(p, rout / "protocols" / p.name)
        protocols.append({"name": p.name, "sha256": sha_file(rout / "protocols" / p.name)})
    manifest = {"schema": "quant_research_backup_v1", "checkpoint_utc": now(), "scope": scope["research"],
                "code_version": os.environ.get("QUANT_CODE_VERSION"), "image_id": os.environ.get("QUANT_IMAGE_ID"),
                "registry": {"rel": "registry.sqlite3", "size": reg_copy.stat().st_size, "sha256": sha_file(reg_copy),
                             "quick_check": check, "tables": table_digests(reg_copy)},
                "runs": runs, "incomplete_runs": sorted(incomplete), "datasets": datasets, "artifacts": artifacts, "files": files, "protocols": protocols,
                "counts": {"research_files": len(files), "research_file_bytes": sum(f["size"] for f in files), "protocol_files": len(protocols),
                           "registry_files": 1, "runs": len(runs), "succeeded_runs": sum(r["status"] == "SUCCEEDED" for r in runs)}}
    (rout / "RESEARCH_MANIFEST.json").write_text(json.dumps(manifest, indent=1))
    return {"ok": check == "ok", "state": "OK" if check == "ok" else "FAILED_QUICK_CHECK", "checkpoint_utc": manifest["checkpoint_utc"],
            "quick_check": check, **manifest["counts"], "incomplete_runs": manifest["incomplete_runs"]}


def required_files(m: dict, scope: dict, cfg_costs) -> list:
    """Every file the scope requires, derived from the backed-up registry rows (not from what is present)."""
    req = []
    for d in m["datasets"]:
        for n in d.get("files") or scope["research"]["required"].get("per_registered_dataset", ["data.csv", "manifest.json"]):
            req.append(f"{d['snapshot_path']}/{n}")
    v2 = {}
    for a in m.get("artifacts", []):
        v2.setdefault(a["run_id"], []).append(a["rel_path"])
    for r in m["runs"]:
        if r["status"] != "SUCCEEDED":
            continue
        if r.get("engine_id"):  # v2: everything registered for the run is required
            req += v2.get(r["run_id"], [f"runs/{r['run_id']}/NO_ARTIFACTS_REGISTERED"])
        else:
            req += [f"runs/{r['run_id']}/{n}" for n in RUN_FILES]
            req += [f"runs/{r['run_id']}/ledger_cost{int(b)}bps.csv" for b in cfg_costs.get(r["run_id"], [])]
    return req


def _engine_of(x: dict, legacy) -> tuple:
    return (x["engine_id"], x["engine_version"]) if x.get("engine_id") else tuple(legacy)


def verify(bdir: pathlib.Path, rdir: pathlib.Path, scope: dict, only_run: str | None = None, v2_launcher: bool = False) -> dict:
    report = {"ok": False, "checked_at_utc": now(), "requirement": scope["research"]["requirement"], "problems": [], "reruns": [],
              "restore_source": "backup directory only (production volume not mounted)"}
    P = report["problems"]
    b = bdir / "research"
    if scope["research"]["requirement"] != "REQUIRED":
        report.update(ok=True, state="SKIPPED_NOT_REQUIRED", detail="scope file explicitly says research backup is not required")
        return report
    if not b.is_dir():
        P.append("research backup directory missing"); report["state"] = "FAILED_MISSING_DIRECTORY"; return report
    try:
        m = json.loads((b / "RESEARCH_MANIFEST.json").read_text())
        if m.get("schema") != "quant_research_backup_v1":
            raise ValueError(f"unexpected schema {m.get('schema')!r}")
    except FileNotFoundError:
        P.append("RESEARCH_MANIFEST.json missing"); report["state"] = "FAILED_MISSING_MANIFEST"; return report
    except (OSError, ValueError) as exc:
        P.append(f"RESEARCH_MANIFEST.json unreadable: {type(exc).__name__}: {exc}"[:300]); report["state"] = "FAILED_CORRUPT_MANIFEST"; return report
    report.update(checkpoint_utc=m["checkpoint_utc"], backup_code_version=m.get("code_version"), backup_image_id=m.get("image_id"))
    if not (b / "registry.sqlite3").is_file():
        P.append("registry.sqlite3 missing"); report["state"] = "FAILED_MISSING_REGISTRY"; return report
    root = rdir / "research"; root.mkdir(parents=True, exist_ok=False)
    # 1. registry
    shutil.copy2(b / "registry.sqlite3", root / "registry.sqlite3")
    if sha_file(root / "registry.sqlite3") != m["registry"]["sha256"]:
        P.append("registry file hash differs from manifest")
    con = sqlite3.connect(root / "registry.sqlite3"); qc = con.execute("pragma quick_check").fetchone()[0]; con.close()
    if qc != "ok":
        P.append(f"registry quick_check {qc}")
    td = table_digests(root / "registry.sqlite3")
    if td != m["registry"]["tables"]:
        P.append("registry table digests differ")
    report["registry_tables"] = {k: v["rows"] for k, v in td.items()}
    # 2. files listed in the manifest, then files REQUIRED by scope (derived from registry rows)
    present = {p.relative_to(b / "files").as_posix() for p in (b / "files").rglob("*") if p.is_file()} if (b / "files").is_dir() else set()
    listed = {f["rel"] for f in m["files"]}
    for rel in sorted(listed - present):
        P.append(f"missing file listed in manifest: {rel}")
    for f in m["files"]:
        if f["rel"] not in present:
            continue
        dst = root / f["rel"]; dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(b / "files" / f["rel"], dst)
        if dst.stat().st_size != f["size"] or sha_file(dst) != f["sha256"]:
            P.append(f"file differs from manifest: {f['rel']}")
    costs = {}
    for r in m["runs"]:
        cfgp = root / "runs" / r["run_id"] / "config.json"
        if r["status"] == "SUCCEEDED" and not r.get("engine_id") and cfgp.is_file():
            try:
                costs[r["run_id"]] = json.loads(cfgp.read_text())["costs"]["scenarios_bps_one_way"]
            except (ValueError, KeyError):
                P.append(f"unreadable config for {r['run_id']}")
    req = required_files(m, scope, costs)
    missing_req = [x for x in req if not (root / x).is_file()]
    P.extend(f"missing required file: {x}" for x in missing_req)
    for name in scope["research"]["required"].get("protocols", []):
        if not (b / "protocols" / name).is_file():
            P.append(f"missing required protocol: {name}")
    for d in m["datasets"]:
        main = "market_research.sqlite" if "market_research.sqlite" in (d.get("files") or []) else "data.csv"
        snap = root / d["snapshot_path"] / main
        if snap.is_file() and sha_file(snap) != d["content_sha256"]:
            P.append(f"dataset {d['dataset_id']}@{d['dataset_version']} hash differs from registry")
    for a in m.get("artifacts", []):
        f = root / a["rel_path"]
        if f.is_file() and (f.stat().st_size != a["bytes"] or sha_file(f) != a["sha256"]):
            P.append(f"artifact differs from registry: {a['rel_path']}")
    report["artifacts_checked"] = len(m.get("artifacts", []))
    report.update(files_checked=len(m["files"]), required_files_checked=len(req), counts=m["counts"])
    if P:
        report["state"] = "FAILED_SCOPE_CHECK"; return report
    # 3. isolated reruns: exactly the new run ids; original requests snapshotted and compared
    os.environ["QUANT_RESEARCH_ROOT"] = str(root); os.environ["QUANT_LIVE_WORKERS"] = ""
    sys.path.insert(0, "/app")
    from scripts.quant_research_v1 import registry, worker
    from scripts.quant_research_v1.engine import FACTORS
    from scripts.quant_research_v1.audit import VERIFIER_VERSION, audit_run
    report["verifier_version"] = VERIFIER_VERSION
    db = registry.connect()
    for spec in FACTORS.values():
        registry.register_factor(db, spec)
    supported = worker.worker_engines(db)
    report["engines_in_this_image"] = ["@".join(e) for e in supported]
    snap_cols = "run_id,status,started_at,finished_at,worker_id,lease_expires_at,cancel_requested,results_digest"
    before = {r[0]: tuple(r) for r in db.execute(f"select {snap_cols} from runs")}
    report["original_runs_at_checkpoint"] = {k: v[1] for k, v in before.items()}
    targets = [x for x in m["runs"] if x["status"] == "SUCCEEDED"]
    if only_run:
        targets = [x for x in m["runs"] if x["run_id"] == only_run]
        if not targets or targets[0]["status"] != "SUCCEEDED":
            P.append(f"--run {only_run}: not a SUCCEEDED run in this backup"); report["state"] = "FAILED_TARGET"; db.close(); return report
    runnable = {tuple(e) for e in supported} | ({tuple(e) for e in worker.V2_ENGINES} if v2_launcher else set())
    report["v2_reexecution"] = ("PROTECTED_LAUNCHER (separate control/compute containers)" if v2_launcher
                                else "IN_PROCESS_TEST_ONLY" if worker.inprocess_v2_allowed() else "NOT_AVAILABLE_IN_THIS_IMAGE")
    report["not_reexecuted_here"] = [x["run_id"] for x in targets if _engine_of(x, registry.LEGACY_ENGINE) not in runnable]
    if only_run and report["not_reexecuted_here"]:
        P.append(f"engine of {only_run} is not supported by this image")
    pending = []
    for r in [x for x in targets if x["run_id"] not in report["not_reexecuted_here"]]:
        entry = {"original_run_id": r["run_id"]}
        try:
            orig = db.execute("select * from runs where run_id=?", (r["run_id"],)).fetchone()
            if orig["engine_id"] if "engine_id" in orig.keys() else None:
                if v2_launcher and not worker.inprocess_v2_allowed():
                    pending.append({"original_run_id": r["run_id"], "new_run_id": _queue_v2_rerun(db, orig)})
                    continue
                entry.update(_rerun_v2(db, root, orig, registry, worker)); report["reruns"].append(entry)
                if not entry.get("ok"):
                    P.append(f"rerun mismatch for {r['run_id']}")
                continue
            ds = db.execute("select content_sha256 from datasets where dataset_id=? and dataset_version=?", (orig["dataset_id"], orig["dataset_version"])).fetchone()
            new_id = registry.create_run(db, (orig["dataset_id"], orig["dataset_version"]), (orig["factor_id"], orig["factor_version"]),
                                         json.loads(orig["config_json"]), requested_by=f"restore-check:{r['run_id']}")
            entry["new_run_id"] = new_id
            with contextlib.redirect_stdout(sys.stderr):
                entry["worker_exit"] = worker.run_specific(new_id, 300)
            new = db.execute("select * from runs where run_id=?", (new_id,)).fetchone()
            ledgers = sorted(p.name for p in (root / "runs" / r["run_id"]).glob("ledger_cost*bps.csv"))
            nd = root / "runs" / new_id
            entry.update(new_status=new["status"], new_exit_reason=new["exit_reason"],
                         config_sha256_equal=new["config_sha256"] == orig["config_sha256"],
                         data_sha256=ds["content_sha256"], original_results_digest=r["results_digest"], new_results_digest=new["results_digest"],
                         digest_equal=new["results_digest"] == r["results_digest"],
                         ledgers_byte_equal={n: (nd / n).is_file() and (root / "runs" / r["run_id"] / n).read_bytes() == (nd / n).read_bytes() for n in ledgers})
            a = audit_run(root, r["run_id"]); entry.update(original_run_independent_audit_ok=a["ok"], original_run_audit_problems=a["problems"][:5])
            ok = (new["status"] == "SUCCEEDED" and entry["digest_equal"] and entry["config_sha256_equal"] and ledgers and all(entry["ledgers_byte_equal"].values()) and a["ok"])
            if not ok:
                P.append(f"rerun mismatch for {r['run_id']}")
        except Exception as exc:  # noqa: BLE001 - every failure becomes part of the structured report
            entry["error"] = f"{type(exc).__name__}: {exc}"[:400]; entry["traceback_tail"] = traceback.format_exc().splitlines()[-3:]
            P.append(f"rerun error for {r['run_id']}: {entry['error']}")
        report["reruns"].append(entry)
    if pending and not P:
        report["pending_protected_reruns"] = pending
        state = {"schema": "quant_research_restore_state_v1", "report": report, "pending": pending,
                 "before": {k: list(v) for k, v in before.items()}, "snap_cols": snap_cols}
        (rdir / "restore_state.json").write_text(json.dumps(state, indent=1))
        db.close()
        report["ok"] = True; report["state"] = "PENDING_PROTECTED_RERUNS"
        return report
    after = {r[0]: tuple(r) for r in db.execute(f"select {snap_cols} from runs") if r[0] in before}
    changed = sorted(k for k in before if before[k] != after.get(k))
    report["original_runs_changed_by_restore_check"] = changed
    if changed:
        P.append(f"restore check changed original run records: {changed}")
    db.close()
    report["ok"] = not P
    report["state"] = "OK" if not P else "FAILED_RERUN"
    return report


def verify_finish(rdir: pathlib.Path) -> dict:
    """Second half of a restore check whose v2 re-executions ran through the protected launcher."""
    st = json.loads((rdir / "restore_state.json").read_text())
    report = st["report"]; P = report["problems"]
    root = rdir / "research"
    os.environ["QUANT_RESEARCH_ROOT"] = str(root)
    sys.path.insert(0, "/app")
    from scripts.quant_research_v1 import registry
    db = registry.connect()
    for item in st["pending"]:
        entry = {"original_run_id": item["original_run_id"]}
        try:
            orig = db.execute("select * from runs where run_id=?", (item["original_run_id"],)).fetchone()
            entry.update(_compare_v2(db, root, orig, item["new_run_id"], None))
            entry["executed_by"] = "protected launcher (quant-research-job.sh)"
            if not entry.get("ok"):
                P.append(f"rerun mismatch for {item['original_run_id']}")
        except Exception as exc:  # noqa: BLE001
            entry["error"] = f"{type(exc).__name__}: {exc}"[:400]; P.append(f"rerun error for {item['original_run_id']}: {entry['error']}")
        report["reruns"].append(entry)
    before = {k: tuple(v) for k, v in st["before"].items()}
    after = {r[0]: tuple(r) for r in db.execute(f"select {st['snap_cols']} from runs") if r[0] in before}
    changed = sorted(k for k in before if before[k] != after.get(k))
    report["original_runs_changed_by_restore_check"] = changed
    if changed:
        P.append(f"restore check changed original run records: {changed}")
    db.close()
    report.pop("pending_protected_reruns", None)
    report["protected_reruns"] = st["pending"]
    report["finished_at_utc"] = now()
    report["ok"] = not P
    report["state"] = "OK" if not P else "FAILED_RERUN"
    return report


def _queue_v2_rerun(db, orig) -> str:
    from scripts.quant_research_v1 import catalog
    return catalog.create_run_v2(db, (orig["dataset_id"], orig["dataset_version"]), (orig["factor_id"], orig["factor_version"]),
                                 json.loads(orig["config_json"]), (orig["engine_id"], orig["engine_version"]),
                                 requested_by=f"restore-check:{orig['run_id']}", attempt_of=orig["run_id"])


def _rerun_v2(db, root, orig, registry, worker) -> dict:
    """IN-PROCESS (unit tests only, QUANT_RESEARCH_INPROCESS_V2=1): re-execute one v2 run under a NEW run id
    (attempt_of = original); exactly that id is claimed. Production uses --v2-via-launcher + verify-finish."""
    new_id = _queue_v2_rerun(db, orig)
    with contextlib.redirect_stdout(sys.stderr):
        code = worker.run_specific(new_id, int(os.environ.get("QUANT_RESTORE_MAX_SECONDS", "900")))
    return _compare_v2(db, root, orig, new_id, code)


def _compare_v2(db, root, orig, new_id: str, code) -> dict:
    eng = (orig["engine_id"], orig["engine_version"])
    new = db.execute("select * from runs where run_id=?", (new_id,)).fetchone()
    od, nd = root / "runs" / orig["run_id"], root / "runs" / new_id
    byte_equal = {n: (od / n).is_file() and (nd / n).is_file() and (od / n).read_bytes() == (nd / n).read_bytes() for n in DETERMINISTIC_V2 if (od / n).is_file()}
    out = {"engine": "@".join(eng), "new_run_id": new_id, "worker_exit": code, "new_status": new["status"], "new_exit_reason": new["exit_reason"],
           "attempt_of": new["attempt_of"], "config_sha256_equal": new["config_sha256"] == orig["config_sha256"],
           "original_results_digest": orig["results_digest"], "new_results_digest": new["results_digest"],
           "digest_equal": new["results_digest"] == orig["results_digest"], "artifacts_byte_equal": byte_equal}
    out["ok"] = (new["status"] == "SUCCEEDED" and out["digest_equal"] and out["config_sha256_equal"] and bool(byte_equal) and all(byte_equal.values())
                 and new["attempt_of"] == orig["run_id"])
    return out


def main(argv) -> int:
    mode = argv[1]
    if mode == "verify-finish":
        try:
            res = verify_finish(pathlib.Path(argv[2]))
        except Exception as exc:  # noqa: BLE001
            res = {"ok": False, "state": "FAILED_EXCEPTION", "error": f"{type(exc).__name__}: {exc}"[:400], "traceback_tail": traceback.format_exc().splitlines()[-3:]}
        print(json.dumps(res))
        return 0 if res["ok"] else 1
    a, b = pathlib.Path(argv[2]), pathlib.Path(argv[3])
    scope = load_scope(argv[argv.index("--scope") + 1] if "--scope" in argv else None)
    only_run = argv[argv.index("--run") + 1] if "--run" in argv else None
    try:
        res = backup(a, b, scope) if mode == "backup" else verify(a, b, scope, only_run, v2_launcher="--v2-via-launcher" in argv)
    except Exception as exc:  # noqa: BLE001
        res = {"ok": False, "state": "FAILED_EXCEPTION", "error": f"{type(exc).__name__}: {exc}"[:400], "traceback_tail": traceback.format_exc().splitlines()[-3:]}
    print(json.dumps(res))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
