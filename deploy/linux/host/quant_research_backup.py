"""Research backup and isolated restore check (runs inside the app image as uid 10001, no network).

backup SRC_VAR OUT_DIR
    SRC_VAR is the personal_var volume (research files under research/). The CALLER HOLDS the
    research lock (/run/lock/quant-research.lock), so no research job can write while this runs:
    the registry copy and the file set belong to one checkpoint. Registry: SQLite online backup,
    single-file copy, quick_check, streaming full-table SHA-256 (no row limit). Files: every file
    under research/{datasets,runs,audits} copied with relative path, size and SHA-256. Runs that
    are not in a terminal state at the checkpoint are listed as INCOMPLETE and their files flagged.
    Frozen protocols (configs/quant_research_*.json from the image), code version and image id are
    recorded. Writes RESEARCH_MANIFEST.json.
verify BACKUP_DIR RESTORE_DIR
    Restores ONLY from BACKUP_DIR into RESTORE_DIR (never reads the production volume), checks
    every file hash and size, quick_check + per-table digests of the registry, dataset hashes vs
    registry, required files of every completed run; then, in the restored root, re-runs every
    completed run from the restored snapshot and its original frozen config under a NEW run_id and
    compares every ledger byte-for-byte and the results_digest. Prints a JSON report.
"""
import hashlib, json, os, pathlib, shutil, sqlite3, sys, datetime

TERMINAL = ("SUCCEEDED", "FAILED", "CANCELLED")
RUN_FILES = ("config.json", "metrics.json", "curves.json", "verify.json", "run.log")


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha_file(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


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


def backup(src: pathlib.Path, out: pathlib.Path) -> dict:
    research = src / "research"
    if not (research / "registry.sqlite3").is_file():
        return {"state": "NO_RESEARCH_REGISTRY"}
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
    runs = [dict(r) for r in con.execute("select run_id, status, results_digest, artifact_dir, dataset_id, dataset_version from runs order by created_at")]
    datasets = [dict(r) for r in con.execute("select dataset_id, dataset_version, content_sha256, snapshot_path from datasets")]
    con.close()
    for side in ("-wal", "-shm"):
        pathlib.Path(str(reg_copy) + side).unlink(missing_ok=True)
    incomplete = {r["run_id"] for r in runs if r["status"] not in TERMINAL}
    files = []
    for top in ("datasets", "runs", "audits"):
        base = research / top
        if not base.is_dir():
            continue
        for p in sorted(x for x in base.rglob("*") if x.is_file()):
            rel = p.relative_to(research).as_posix()
            dst = rout / "files" / rel; dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst)
            digest = sha_file(dst)
            run_id = rel.split("/")[1] if rel.startswith("runs/") else None
            files.append({"rel": rel, "size": dst.stat().st_size, "sha256": digest, "run_id": run_id,
                          "complete": run_id not in incomplete})
    protocols = []
    (rout / "protocols").mkdir()
    for p in sorted(pathlib.Path("/app/configs").glob("quant_research_*.json")):
        shutil.copy2(p, rout / "protocols" / p.name)
        protocols.append({"name": p.name, "sha256": sha_file(rout / "protocols" / p.name)})
    manifest = {"schema": "quant_research_backup_v1", "checkpoint_utc": now(),
                "code_version": os.environ.get("QUANT_CODE_VERSION"), "image_id": os.environ.get("QUANT_IMAGE_ID"),
                "registry": {"rel": "registry.sqlite3", "size": reg_copy.stat().st_size, "sha256": sha_file(reg_copy),
                             "quick_check": check, "tables": table_digests(reg_copy)},
                "runs": runs, "incomplete_runs": sorted(incomplete), "datasets": datasets,
                "files": files, "protocols": protocols,
                "counts": {"files": len(files), "bytes": sum(f["size"] for f in files), "runs": len(runs),
                           "succeeded_runs": sum(r["status"] == "SUCCEEDED" for r in runs)}}
    (rout / "RESEARCH_MANIFEST.json").write_text(json.dumps(manifest, indent=1))
    return {"state": "OK", "checkpoint_utc": manifest["checkpoint_utc"], "quick_check": check, **manifest["counts"],
            "incomplete_runs": manifest["incomplete_runs"]}


def verify(bdir: pathlib.Path, rdir: pathlib.Path) -> dict:
    b = bdir / "research"
    if not (b / "RESEARCH_MANIFEST.json").is_file():
        return {"ok": True, "state": "NO_RESEARCH_BACKUP"}
    m = json.loads((b / "RESEARCH_MANIFEST.json").read_text())
    root = rdir / "research"; root.mkdir(parents=True, exist_ok=False)
    problems = []
    # 1. restore registry and files from the backup copy only
    shutil.copy2(b / "registry.sqlite3", root / "registry.sqlite3")
    if sha_file(root / "registry.sqlite3") != m["registry"]["sha256"]:
        problems.append("registry file hash differs from manifest")
    con = sqlite3.connect(root / "registry.sqlite3"); qc = con.execute("pragma quick_check").fetchone()[0]; con.close()
    if qc != "ok":
        problems.append(f"registry quick_check {qc}")
    td = table_digests(root / "registry.sqlite3")
    if td != m["registry"]["tables"]:
        problems.append("registry table digests differ")
    if any(v["sha256"] is None for v in td.values()):
        problems.append("a table digest is NULL")
    on_disk = sorted(p.relative_to(b / "files").as_posix() for p in (b / "files").rglob("*") if p.is_file())
    if on_disk != sorted(f["rel"] for f in m["files"]):
        problems.append("backup file set differs from manifest")
    for f in m["files"]:
        dst = root / f["rel"]; dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(b / "files" / f["rel"], dst)
        if dst.stat().st_size != f["size"] or sha_file(dst) != f["sha256"]:
            problems.append(f"file mismatch {f['rel']}")
    for d in m["datasets"]:
        snap = root / d["snapshot_path"] / "data.csv"
        if not snap.is_file() or sha_file(snap) != d["content_sha256"]:
            problems.append(f"dataset {d['dataset_id']}@{d['dataset_version']} snapshot missing or hash differs")
    done = [r for r in m["runs"] if r["status"] == "SUCCEEDED"]
    for r in done:
        for name in RUN_FILES:
            if not (root / "runs" / r["run_id"] / name).is_file():
                problems.append(f"{r['run_id']} missing {name}")
    # 2. rerun every completed run in the restored root under a new run_id
    os.environ["QUANT_RESEARCH_ROOT"] = str(root); os.environ["QUANT_LIVE_WORKERS"] = ""
    sys.path.insert(0, "/app")
    from scripts.quant_research_v1 import registry, worker
    from scripts.quant_research_v1.engine import FACTORS
    from scripts.quant_research_v1.audit import VERIFIER_VERSION, audit_run
    db = registry.connect()
    for spec in FACTORS.values():           # binds implementation fingerprints in the restored copy only
        registry.register_factor(db, spec)
    reruns = []
    for r in done:
        orig = db.execute("select * from runs where run_id=?", (r["run_id"],)).fetchone()
        new_id = registry.create_run(db, (orig["dataset_id"], orig["dataset_version"]), (orig["factor_id"], orig["factor_version"]),
                                     json.loads(orig["config_json"]), requested_by=f"restore-check:{r['run_id']}")
        code = worker.run_once(300)
        new = db.execute("select status, results_digest from runs where run_id=?", (new_id,)).fetchone()
        ledgers = sorted(p.name for p in (root / "runs" / r["run_id"]).glob("ledger_cost*bps.csv"))
        same = {n: (root / "runs" / r["run_id"] / n).read_bytes() == (root / "runs" / new_id / n).read_bytes() for n in ledgers}
        orig_audit = audit_run(root, r["run_id"])
        entry = {"original_run_id": r["run_id"], "new_run_id": new_id, "worker_exit": code, "new_status": new["status"],
                 "original_results_digest": r["results_digest"], "new_results_digest": new["results_digest"],
                 "digest_equal": new["results_digest"] == r["results_digest"], "ledgers_byte_equal": same,
                 "original_run_independent_audit_ok": orig_audit["ok"], "original_run_audit_problems": orig_audit["problems"][:5]}
        reruns.append(entry)
        if not (new["status"] == "SUCCEEDED" and entry["digest_equal"] and all(same.values()) and ledgers and orig_audit["ok"]):
            problems.append(f"rerun mismatch for {r['run_id']}")
    db.close()
    return {"ok": not problems, "checked_at_utc": now(), "checkpoint_utc": m["checkpoint_utc"], "verifier_version": VERIFIER_VERSION,
            "backup_code_version": m["code_version"], "backup_image_id": m["image_id"],
            "files_checked": len(m["files"]), "bytes_checked": m["counts"]["bytes"], "registry_tables": {k: v["rows"] for k, v in td.items()},
            "incomplete_runs_at_checkpoint": m["incomplete_runs"], "reruns": reruns, "problems": problems,
            "restore_source": "backup directory only (production volume not mounted)"}


if __name__ == "__main__":
    mode, a, b = sys.argv[1], pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])
    res = backup(a, b) if mode == "backup" else verify(a, b)
    print(json.dumps(res))
    sys.exit(0 if mode == "backup" or res["ok"] else 1)
