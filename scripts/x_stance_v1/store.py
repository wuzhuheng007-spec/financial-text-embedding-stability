"""Private SQLite store for the X stance study. Raw post text lives here and nowhere else; this file is never
committed. Table names follow section 12 of the contract."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

SCHEMA = """
CREATE TABLE IF NOT EXISTS x_posts(
  provider TEXT NOT NULL, post_id TEXT NOT NULL, author_id TEXT NOT NULL, created_at_utc TEXT NOT NULL,
  first_seen_at_utc TEXT NOT NULL, fetched_at_utc TEXT NOT NULL, ingest_mode TEXT NOT NULL,
  source_url TEXT, text TEXT NOT NULL, author_text TEXT NOT NULL, lang TEXT, text_hash TEXT NOT NULL,
  edit_status TEXT NOT NULL, kind TEXT NOT NULL, parent_available INTEGER, raw_batch TEXT NOT NULL,
  engagement_snapshot_json TEXT, PRIMARY KEY(provider, post_id));
CREATE INDEX IF NOT EXISTS x_posts_created ON x_posts(created_at_utc);
CREATE TABLE IF NOT EXISTS x_queries(
  query_hash TEXT PRIMARY KEY, provider TEXT NOT NULL, topic_id TEXT NOT NULL, lang TEXT NOT NULL,
  vocab_version TEXT NOT NULL, query_text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS x_query_runs(
  run_id TEXT PRIMARY KEY, provider TEXT NOT NULL, query_hash TEXT NOT NULL, topic_id TEXT, lang TEXT,
  window_start_utc TEXT NOT NULL, window_end_utc TEXT NOT NULL, parent_run_id TEXT, status TEXT NOT NULL,
  stop_reason TEXT, cursor TEXT, pages INTEGER NOT NULL DEFAULT 0, requests INTEGER NOT NULL DEFAULT 0,
  returned INTEGER NOT NULL DEFAULT 0, in_window INTEGER NOT NULL DEFAULT 0, out_of_window INTEGER NOT NULL DEFAULT 0,
  duplicates INTEGER NOT NULL DEFAULT 0, new_posts INTEGER NOT NULL DEFAULT 0, earliest_utc TEXT, latest_utc TEXT,
  cost_estimate_usd TEXT NOT NULL DEFAULT '0', billing_source TEXT NOT NULL DEFAULT 'ESTIMATE_FROM_QUOTED_RATE',
  error_class TEXT, started_at_utc TEXT, updated_at_utc TEXT);
CREATE TABLE IF NOT EXISTS x_page_cache(
  provider TEXT NOT NULL, query_hash TEXT NOT NULL, window_start_utc TEXT NOT NULL, window_end_utc TEXT NOT NULL,
  cursor TEXT NOT NULL, response_sha256 TEXT NOT NULL, response_json TEXT NOT NULL, fetched_at_utc TEXT NOT NULL,
  PRIMARY KEY(provider, query_hash, window_start_utc, window_end_utc, cursor));
CREATE TABLE IF NOT EXISTS x_run_posts(run_id TEXT NOT NULL, provider TEXT NOT NULL, post_id TEXT NOT NULL, PRIMARY KEY(run_id, provider, post_id));
CREATE TABLE IF NOT EXISTS x_post_topics(
  provider TEXT NOT NULL, post_id TEXT NOT NULL, topic_version TEXT NOT NULL, topic_id TEXT NOT NULL, target_id TEXT NOT NULL,
  relevance TEXT NOT NULL, quality TEXT NOT NULL, matched_keywords_json TEXT NOT NULL,
  PRIMARY KEY(provider, post_id, topic_version, topic_id, target_id));
CREATE TABLE IF NOT EXISTS x_stance_labels(
  provider TEXT NOT NULL, post_id TEXT NOT NULL, topic_id TEXT NOT NULL, target_id TEXT NOT NULL, label TEXT,
  quality_status TEXT NOT NULL, is_relevant INTEGER, evidence_text TEXT, reason_short TEXT, model_id TEXT, model_revision TEXT,
  prompt_hash TEXT, inferred_at_utc TEXT NOT NULL, raw_output_hash TEXT, cache_key TEXT NOT NULL,
  PRIMARY KEY(provider, post_id, topic_id, target_id, cache_key));
CREATE TABLE IF NOT EXISTS x_inference_cache(cache_key TEXT PRIMARY KEY, raw_output TEXT NOT NULL, raw_output_hash TEXT NOT NULL, created_at_utc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS x_daily_sentiment(
  window_kind TEXT NOT NULL, window_label TEXT NOT NULL, window_start_utc TEXT NOT NULL, window_end_utc TEXT NOT NULL,
  topic_id TEXT NOT NULL, target_id TEXT NOT NULL, lang_scope TEXT NOT NULL, mode TEXT NOT NULL, payload_json TEXT NOT NULL,
  state TEXT NOT NULL, signal_committed_at_utc TEXT NOT NULL,
  PRIMARY KEY(window_kind, window_label, topic_id, target_id, lang_scope, mode));
CREATE TABLE IF NOT EXISTS x_narratives(
  window_kind TEXT NOT NULL, window_label TEXT NOT NULL, topic_id TEXT NOT NULL, target_id TEXT NOT NULL,
  cluster_version TEXT NOT NULL, rank INTEGER NOT NULL, payload_json TEXT NOT NULL,
  PRIMARY KEY(window_kind, window_label, topic_id, target_id, cluster_version, rank));
CREATE TABLE IF NOT EXISTS x_sentiment_episodes(episode_id TEXT PRIMARY KEY, topic_id TEXT NOT NULL, target_id TEXT NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS x_event_returns(
  event_id TEXT NOT NULL, event_kind TEXT NOT NULL, topic_id TEXT NOT NULL, target_id TEXT NOT NULL, h INTEGER NOT NULL,
  payload_json TEXT NOT NULL, PRIMARY KEY(event_id, event_kind, h));
CREATE TABLE IF NOT EXISTS x_run_manifest(manifest_id TEXT PRIMARY KEY, created_at_utc TEXT NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS x_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at_utc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS x_sample_plans(plan_id TEXT PRIMARY KEY, job TEXT NOT NULL, plan_key TEXT NOT NULL, frozen_at_utc TEXT NOT NULL,
  sha256 TEXT NOT NULL, plan_json TEXT NOT NULL, UNIQUE(job, plan_key));
CREATE TABLE IF NOT EXISTS x_cost_ledger(
  request_id TEXT PRIMARY KEY, job_type TEXT NOT NULL, pool TEXT NOT NULL, is_preflight INTEGER NOT NULL,
  run_id TEXT, window_start_utc TEXT, window_end_utc TEXT, cursor TEXT, attempt INTEGER NOT NULL,
  reserved_credits INTEGER NOT NULL, reserved_posts INTEGER NOT NULL, settled_credits INTEGER, returned INTEGER, unique_ids INTEGER,
  status TEXT NOT NULL, cost_source TEXT NOT NULL, note TEXT, budget_day TEXT NOT NULL, created_at_utc TEXT NOT NULL, settled_at_utc TEXT);
CREATE INDEX IF NOT EXISTS x_cost_ledger_day ON x_cost_ledger(budget_day);
CREATE INDEX IF NOT EXISTS x_cost_ledger_created ON x_cost_ledger(created_at_utc);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def db_path() -> Path:
    """QUANT_X_STANCE_DB wins. Otherwise the portal's private state directory when it exists (on the server that is
    the persistent volume the web service already reads), else the ignored local_only folder."""
    if os.environ.get("QUANT_X_STANCE_DB"):
        return Path(os.environ["QUANT_X_STANCE_DB"])
    var = REPO_ROOT / "apps" / "quant_portal" / "var"
    return (var / "x_stance" if var.is_dir() else REPO_ROOT / "local_only" / "x_stance_v1") / "x_stance.sqlite3"


def connect(path: str | Path | None = None, *, readonly: bool = False) -> sqlite3.Connection:
    p = Path(path or db_path())
    if readonly:
        db = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=10)
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(p, timeout=30)
        db.executescript(SCHEMA)
    db.row_factory = sqlite3.Row
    return db


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def put_post(db, p: dict) -> bool:
    """Insert once. A post seen again keeps its ORIGINAL first_seen_at_utc and text: a later fetch never rewrites
    when it was first received, and a changed text is recorded as an unknown edit rather than replacing the copy."""
    row = db.execute("SELECT text_hash, edit_status FROM x_posts WHERE provider=? AND post_id=?", (p["provider"], p["post_id"])).fetchone()
    if row:
        if row["text_hash"] != p["text_hash"] and row["edit_status"] != "CHANGED_AFTER_FIRST_SEEN_VERSION_UNKNOWN":
            db.execute("UPDATE x_posts SET edit_status='CHANGED_AFTER_FIRST_SEEN_VERSION_UNKNOWN' WHERE provider=? AND post_id=?", (p["provider"], p["post_id"]))
        return False
    db.execute(
        "INSERT INTO x_posts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (p["provider"], p["post_id"], p["author_id"], p["created_at_utc"], p["first_seen_at_utc"], p["fetched_at_utc"], p["ingest_mode"],
         p.get("source_url"), p["text"], p["author_text"], p.get("lang"), p["text_hash"], p["edit_status"], p["kind"],
         None if p.get("parent_available") is None else int(bool(p["parent_available"])), p["raw_batch"],
         json.dumps(p["engagement_snapshot"], sort_keys=True) if p.get("engagement_snapshot") else None))
    return True


def upsert_run(db, run: dict) -> None:
    cols = list(run)
    db.execute(f"INSERT INTO x_query_runs({','.join(cols)}) VALUES({','.join('?' * len(cols))}) "
               f"ON CONFLICT(run_id) DO UPDATE SET {','.join(c + '=excluded.' + c for c in cols if c != 'run_id')}", [run[c] for c in cols])


def coverage(db, topic_id: str, start_utc: str, end_utc: str, *, vocab_version: str | None = None) -> dict:
    """Collection status of [start, end) for one topic. COMPLETE means: every registered query of the topic has
    finished, paginated runs whose windows jointly contain the interval. It describes the collection PROGRAM; it is
    not a statement about how much of X was returned."""
    q = "SELECT query_hash, lang, provider FROM x_queries WHERE topic_id=?" + (" AND vocab_version=?" if vocab_version else "")
    queries = db.execute(q, (topic_id, vocab_version) if vocab_version else (topic_id,)).fetchall()
    if not queries:
        return {"status": "MISSING", "reason": "NO_QUERY_REGISTERED", "gaps": []}
    gaps, kinds = [], set()
    for qr in queries:
        rows = db.execute(
            "SELECT window_start_utc s, window_end_utc e, status FROM x_query_runs WHERE query_hash=? AND status!='SPLIT' "
            "AND window_end_utc>? AND window_start_utc<? ORDER BY window_start_utc", (qr["query_hash"], start_utc, end_utc)).fetchall()
        cursor = start_utc
        for r in rows:
            if r["status"] not in ("COMPLETE", "COMPLETE_DECLARED", "PLANNED_SAMPLE"):
                if r["s"] > cursor:
                    gaps.append({"query_hash": qr["query_hash"], "from": cursor, "to": r["s"], "status": "NOT_RUN"})
                gaps.append({"query_hash": qr["query_hash"], "from": r["s"], "to": r["e"], "status": r["status"]})
                cursor = max(cursor, r["e"])
                continue
            kinds.add(r["status"])
            if r["s"] > cursor:
                gaps.append({"query_hash": qr["query_hash"], "from": cursor, "to": r["s"], "status": "NOT_RUN"})
            cursor = max(cursor, r["e"])
        if cursor < end_utc:
            gaps.append({"query_hash": qr["query_hash"], "from": cursor, "to": end_utc, "status": "NOT_RUN"})
    if gaps:
        only_missing = all(g["status"] == "NOT_RUN" and g["from"] <= start_utc and g["to"] >= end_utc for g in gaps)
        return {"status": "MISSING" if only_missing else "PARTIAL", "reason": "GAPS", "gaps": gaps}
    status = "PLANNED_SAMPLE" if "PLANNED_SAMPLE" in kinds else ("COMPLETE_DECLARED" if "COMPLETE_DECLARED" in kinds else "COMPLETE")
    return {"status": status, "reason": None, "gaps": []}
