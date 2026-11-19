"""SQLite registry for datasets, factors and runs (its own file: no lock shared with the feed)."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
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
    return db


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
    existing = db.execute("SELECT entry, params_json FROM factors WHERE factor_id=? AND factor_version=?",
                          (spec["factor_id"], spec["factor_version"])).fetchone()
    params = canonical(spec["params"])
    if existing:
        if (existing[0], existing[1]) != (spec["entry"], params):
            raise ValueError("factor version already registered with a different definition")
        return
    db.execute("INSERT INTO factors VALUES(?,?,?,?,?,?,?,?,?)",
               (spec["factor_id"], spec["factor_version"], spec["title"], spec["entry"], params,
                canonical(spec["input_requirements"]), spec["output_definition"], spec["missing_rule"], utcnow()))
    db.commit()


def create_run(db, dataset: tuple[str, str], factor: tuple[str, str], protocol: dict,
               requested_by: str | None = None, code_version: str | None = None) -> str:
    if not db.execute("SELECT 1 FROM datasets WHERE dataset_id=? AND dataset_version=?", dataset).fetchone():
        raise LookupError("dataset not registered")
    if not db.execute("SELECT 1 FROM factors WHERE factor_id=? AND factor_version=?", factor).fetchone():
        raise LookupError("factor not registered")
    if (protocol["factor"]["factor_id"], protocol["factor"]["factor_version"]) != factor:
        raise ValueError("protocol is frozen for a different factor version")
    run_id = f"run-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"
    config = canonical(protocol)
    db.execute("""INSERT INTO runs(run_id,dataset_id,dataset_version,factor_id,factor_version,protocol_id,
                  config_json,config_sha256,code_version,status,requested_by,created_at)
                  VALUES(?,?,?,?,?,?,?,?,?,'QUEUED',?,?)""",
               (run_id, *dataset, *factor, protocol["protocol_id"], config, sha256_text(config),
                code_version, requested_by, utcnow()))
    db.commit()
    return run_id


def request_cancel(db, run_id: str) -> str:
    row = db.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if not row:
        raise LookupError(run_id)
    if row[0] == "QUEUED":
        db.execute("UPDATE runs SET status='CANCELLED', cancel_requested=1, finished_at=? WHERE run_id=? AND status='QUEUED'",
                   (utcnow(), run_id))
    elif row[0] == "RUNNING":
        db.execute("UPDATE runs SET cancel_requested=1 WHERE run_id=?", (run_id,))
    db.commit()
    return db.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()[0]


def claim_next(db) -> sqlite3.Row | None:
    """Atomically move the oldest QUEUED run to RUNNING (one worker container at a time)."""
    db.execute("BEGIN IMMEDIATE")
    row = db.execute("SELECT run_id FROM runs WHERE status='QUEUED' ORDER BY created_at, run_id LIMIT 1").fetchone()
    if not row:
        db.execute("COMMIT")
        return None
    db.execute("UPDATE runs SET status='RUNNING', started_at=? WHERE run_id=?", (utcnow(), row[0]))
    db.execute("COMMIT")
    return db.execute("SELECT * FROM runs WHERE run_id=?", (row[0],)).fetchone()


def finish(db, run_id: str, status: str, **fields) -> None:
    assert status in STATUSES
    cols = ", ".join(f"{k}=?" for k in fields)
    db.execute(f"UPDATE runs SET status=?, finished_at=?{', ' + cols if cols else ''} WHERE run_id=?",
               (status, utcnow(), *fields.values(), run_id))
    db.commit()


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
