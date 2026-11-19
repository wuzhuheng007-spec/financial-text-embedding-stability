"""SQLite registry for datasets, factors and runs (its own file: no lock shared with the feed)."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
from datetime import timedelta
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = REPO_ROOT / "apps/quant_portal/var/research"
STATUSES = ("QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED")

SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
  dataset_id TEXT NOT NULL, dataset_version TEXT NOT NULL, title TEXT NOT NULL,
  symbol TEXT NOT NULL, frequency TEXT NOT NULL, adjustment TEXT NOT NULL,
  start_date TEXT NOT NULL, end_date TEXT NOT NULL, rows INTEGER NOT NULL,
  content_sha256 TEXT NOT NULL, audit_status TEXT NOT NULL, manifest_json TEXT NOT NULL,
  snapshot_path TEXT NOT NULL, registered_at TEXT NOT NULL,
  PRIMARY KEY (dataset_id, dataset_version));
CREATE TABLE IF NOT EXISTS factors (
  factor_id TEXT NOT NULL, factor_version TEXT NOT NULL, title TEXT NOT NULL,
  entry TEXT NOT NULL, params_json TEXT NOT NULL, input_requirements_json TEXT NOT NULL,
  output_definition TEXT NOT NULL, missing_rule TEXT NOT NULL, registered_at TEXT NOT NULL,
  PRIMARY KEY (factor_id, factor_version));
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL, dataset_version TEXT NOT NULL,
  factor_id TEXT NOT NULL, factor_version TEXT NOT NULL, protocol_id TEXT NOT NULL,
  config_json TEXT NOT NULL, config_sha256 TEXT NOT NULL, code_version TEXT,
  status TEXT NOT NULL, requested_by TEXT, created_at TEXT NOT NULL,
  started_at TEXT, finished_at TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
  error_class TEXT, error_detail TEXT, artifact_dir TEXT, results_digest TEXT, summary_json TEXT,
  FOREIGN KEY (dataset_id, dataset_version) REFERENCES datasets(dataset_id, dataset_version),
  FOREIGN KEY (factor_id, factor_version) REFERENCES factors(factor_id, factor_version));
CREATE INDEX IF NOT EXISTS runs_status ON runs(status, created_at);
"""
# Additive migrations (v1.1): never drop or rewrite existing rows.
MIGRATIONS = [("factors", "impl_sha256", "TEXT"), ("runs", "worker_id", "TEXT"), ("runs", "heartbeat_at", "TEXT"),
              ("runs", "lease_expires_at", "TEXT"), ("runs", "attempt_of", "TEXT"), ("runs", "exit_reason", "TEXT"),
              ("runs", "image_id", "TEXT"), ("runs", "verifier_version", "TEXT"), ("runs", "factor_impl_sha256", "TEXT")]
TERMINAL = ("SUCCEEDED", "FAILED", "CANCELLED")
LEASE_SECONDS = int(os.environ.get("QUANT_RESEARCH_LEASE_SECONDS", "60"))


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def root() -> Path:
    return Path(os.environ.get("QUANT_RESEARCH_ROOT", DEFAULT_ROOT))


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or root() / "registry.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript(SCHEMA)
    for table, col, typ in MIGRATIONS:
        if col not in {r[1] for r in db.execute(f"PRAGMA table_info({table})")}:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    db.commit()
    return db


def _ts(delta_seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def register_dataset(db, manifest: dict, snapshot_path: str) -> None:
    keys = ("dataset_id", "dataset_version", "title", "symbol", "frequency", "adjustment",
            "start_date", "end_date", "rows", "content_sha256", "audit_status")
    row = [manifest[k] for k in keys]
    existing = db.execute("SELECT content_sha256 FROM datasets WHERE dataset_id=? AND dataset_version=?",
                          (manifest["dataset_id"], manifest["dataset_version"])).fetchone()
    if existing:
        if existing[0] != manifest["content_sha256"]:
            raise ValueError("dataset version already registered with different content; register a new version")
        return
    db.execute("INSERT INTO datasets VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (*row, canonical(manifest), snapshot_path, utcnow()))
    db.commit()


def register_factor(db, spec: dict) -> None:
    from .engine import impl_fingerprint
    fp = impl_fingerprint(spec["fn"])
    existing = db.execute("SELECT entry, params_json, impl_sha256 FROM factors WHERE factor_id=? AND factor_version=?",
                          (spec["factor_id"], spec["factor_version"])).fetchone()
    params = canonical(spec["params"])
    if existing:
        if (existing[0], existing[1]) != (spec["entry"], params):
            raise ValueError("factor version already registered with a different definition")
        if existing[2] is None:  # registered before fingerprints existed: bind once, never change later
            db.execute("UPDATE factors SET impl_sha256=? WHERE factor_id=? AND factor_version=? AND impl_sha256 IS NULL",
                       (fp, spec["factor_id"], spec["factor_version"])); db.commit()
        elif existing[2] != fp:
            raise ValueError("factor implementation changed: register a new factor version")
        return
    db.execute("""INSERT INTO factors(factor_id,factor_version,title,entry,params_json,input_requirements_json,
                  output_definition,missing_rule,registered_at,impl_sha256) VALUES(?,?,?,?,?,?,?,?,?,?)""",
               (spec["factor_id"], spec["factor_version"], spec["title"], spec["entry"], params,
                canonical(spec["input_requirements"]), spec["output_definition"], spec["missing_rule"], utcnow(), fp))
    db.commit()


def create_run(db, dataset: tuple[str, str], factor: tuple[str, str], protocol: dict,
               requested_by: str | None = None, code_version: str | None = None, attempt_of: str | None = None) -> str:
    if not db.execute("SELECT 1 FROM datasets WHERE dataset_id=? AND dataset_version=?", dataset).fetchone():
        raise LookupError("dataset not registered")
    if not db.execute("SELECT 1 FROM factors WHERE factor_id=? AND factor_version=?", factor).fetchone():
        raise LookupError("factor not registered")
    if (protocol["factor"]["factor_id"], protocol["factor"]["factor_version"]) != factor:
        raise ValueError("protocol is frozen for a different factor version")
    run_id = f"run-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"
    config = canonical(protocol)
    db.execute("""INSERT INTO runs(run_id,dataset_id,dataset_version,factor_id,factor_version,protocol_id,
                  config_json,config_sha256,code_version,status,requested_by,created_at,attempt_of)
                  VALUES(?,?,?,?,?,?,?,?,?,'QUEUED',?,?,?)""",
               (run_id, *dataset, *factor, protocol["protocol_id"], config, sha256_text(config),
                code_version, requested_by, utcnow(), attempt_of))
    db.commit()
    return run_id


def request_cancel(db, run_id: str) -> str:
    row = db.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if not row:
        raise LookupError(run_id)
    db.execute("BEGIN IMMEDIATE")
    db.execute("""UPDATE runs SET status='CANCELLED', cancel_requested=1, finished_at=?, exit_reason='CANCELLED_BEFORE_START'
                  WHERE run_id=? AND status='QUEUED'""", (utcnow(), run_id))
    db.execute("UPDATE runs SET cancel_requested=1 WHERE run_id=? AND status='RUNNING'", (run_id,))
    db.execute("COMMIT")
    return db.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()[0]


def retry_run(db, run_id: str, requested_by: str | None = None) -> str:
    """A retry is a NEW run with the same dataset, factor and frozen config, linked to the original."""
    row = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if not row:
        raise LookupError(run_id)
    if row["status"] not in ("FAILED", "CANCELLED"):
        raise ValueError("only failed or cancelled runs can be retried")
    return create_run(db, (row["dataset_id"], row["dataset_version"]), (row["factor_id"], row["factor_version"]),
                      json.loads(row["config_json"]), requested_by=requested_by, attempt_of=run_id)


def heartbeat(db, run_id: str, worker_id: str) -> bool:
    """Extend the lease; returns True when a cancel was requested."""
    db.execute("UPDATE runs SET heartbeat_at=?, lease_expires_at=? WHERE run_id=? AND worker_id=? AND status='RUNNING'",
               (utcnow(), _ts(LEASE_SECONDS), run_id, worker_id))
    db.commit()
    row = db.execute("SELECT cancel_requested FROM runs WHERE run_id=?", (run_id,)).fetchone()
    return bool(row and row[0])


def recover_lost(db, live_workers: set[str] | None, max_run_seconds: int = 300) -> list[dict]:
    """Move RUNNING runs whose worker is confirmed gone to FAILED/WORKER_LOST (artifacts kept).

    A run is lost when its lease has expired AND its worker is not in the set of live job
    containers reported by the host. If the host could not report (live_workers is None) a run is
    only declared lost once it is far past the hard run-time limit. Legacy rows without a lease use
    started_at + the hard limit.
    """
    now = utcnow(); out = []
    for r in db.execute("SELECT run_id, worker_id, lease_expires_at, started_at FROM runs WHERE status='RUNNING'").fetchall():
        hard_deadline = (datetime.fromisoformat(r["started_at"].replace("Z", "+00:00")) + timedelta(seconds=max_run_seconds * 3)
                         ).strftime("%Y-%m-%dT%H:%M:%SZ") if r["started_at"] else now
        lease_gone = (r["lease_expires_at"] or hard_deadline) < now
        host_says_gone = live_workers is not None and (r["worker_id"] or "").split(":")[0] not in live_workers
        if lease_gone and (host_says_gone or hard_deadline < now):
            cur = db.execute("""UPDATE runs SET status='FAILED', finished_at=?, error_class='WORKER_LOST', exit_reason='WORKER_LOST',
                    error_detail=? WHERE run_id=? AND status='RUNNING'""",
                    (now, f"lease expired {r['lease_expires_at']}; worker {r['worker_id']} not running", r["run_id"]))
            if cur.rowcount:
                out.append({"run_id": r["run_id"], "worker_id": r["worker_id"], "lease_expires_at": r["lease_expires_at"], "recovered_at": now})
    db.commit()
    return out


def claim_next(db, worker_id: str = "unknown") -> sqlite3.Row | None:
    """Atomically move the oldest QUEUED run to RUNNING with a lease held by worker_id."""
    db.execute("BEGIN IMMEDIATE")
    row = db.execute("SELECT run_id FROM runs WHERE status='QUEUED' ORDER BY created_at, run_id LIMIT 1").fetchone()
    if not row:
        db.execute("COMMIT")
        return None
    db.execute("UPDATE runs SET status='RUNNING', started_at=?, worker_id=?, heartbeat_at=?, lease_expires_at=? WHERE run_id=?",
               (utcnow(), worker_id, utcnow(), _ts(LEASE_SECONDS), row[0]))
    db.execute("COMMIT")
    return db.execute("SELECT * FROM runs WHERE run_id=?", (row[0],)).fetchone()


def finish(db, run_id: str, status: str, **fields) -> str:
    """Terminal transition from RUNNING only. SUCCEEDED is refused if a cancel was requested first
    (the run becomes CANCELLED_BEFORE_COMMIT); returns the status actually recorded."""
    assert status in STATUSES
    cols = ", ".join(f"{k}=?" for k in fields)
    db.execute("BEGIN IMMEDIATE")
    guard = " AND cancel_requested=0" if status == "SUCCEEDED" else ""
    cur = db.execute(f"UPDATE runs SET status=?, finished_at=?{', ' + cols if cols else ''} WHERE run_id=? AND status='RUNNING'{guard}",
                     (status, utcnow(), *fields.values(), run_id))
    if cur.rowcount == 0 and status == "SUCCEEDED":
        db.execute("""UPDATE runs SET status='CANCELLED', finished_at=?, exit_reason='CANCELLED_BEFORE_COMMIT', artifact_dir=?
                      WHERE run_id=? AND status='RUNNING'""", (utcnow(), fields.get("artifact_dir"), run_id))
    db.execute("COMMIT")
    return db.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()[0]


def read_all(path: Path | None = None) -> dict:
    """Read-only view for the portal."""
    path = path or root() / "registry.sqlite3"
    if not path.is_file():
        return {"state": "NOT_INITIALIZED", "datasets": [], "factors": [], "runs": []}
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
        db.row_factory = sqlite3.Row
        datasets = [dict(r) for r in db.execute("SELECT * FROM datasets ORDER BY dataset_id, dataset_version")]
        factors = [dict(r) for r in db.execute("SELECT * FROM factors ORDER BY factor_id, factor_version")]
        runs = [dict(r) for r in db.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT 100")]
    for d in datasets:
        d["manifest"] = json.loads(d.pop("manifest_json"))
        d.pop("snapshot_path", None)
    for f in factors:
        f["params"] = json.loads(f.pop("params_json"))
        f["input_requirements"] = json.loads(f.pop("input_requirements_json"))
    for r in runs:
        r["summary"] = json.loads(r.pop("summary_json")) if r.get("summary_json") else None
        r.pop("config_json", None)
        r.pop("artifact_dir", None)
    return {"state": "READY", "datasets": datasets, "factors": factors, "runs": runs}
