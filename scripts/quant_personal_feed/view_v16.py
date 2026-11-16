"""Owner-only v1.6 overlay; preserves prior accepted cards and rejected records."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .analysis_contract_v16 import PROMPT_VERSION, SCHEMA_VERSION

# Three source-reviewed v16.4 mechanical drafts had unsupported content. Keep
# subsequent drafts private until a content-review path is explicitly added.
AUTO_DRAFT_DISPLAY_ENABLED = False


def _time(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError, TypeError):
        return None


def overlay(data: dict, *, owner_id: int, analysis_db: Path, config: dict) -> dict:
    jobs = {}
    worker = None
    count = 0
    if analysis_db.is_file():
        with sqlite3.connect(analysis_db.resolve().as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            jobs = {(row["item_key"], row["version"]): dict(row) for row in db.execute("""
              SELECT item_key,version,content_sha256,status,result_json,validation_json,
                model_revision,prompt_version,schema_version,completed_at,error_class
              FROM analysis_jobs WHERE owner_id=? AND model_revision=? AND prompt_version=? AND schema_version=?""",
              (owner_id, config["model_revision"], PROMPT_VERSION, SCHEMA_VERSION))}
            row = db.execute("SELECT * FROM analysis_worker_state WHERE owner_id=?", (owner_id,)).fetchone()
            worker = dict(row) if row else None
            count = db.execute("""SELECT COUNT(*) FROM analysis_jobs WHERE owner_id=? AND model_revision=?
              AND prompt_version=? AND schema_version=? AND status='PENDING'""",
              (owner_id,config["model_revision"],PROMPT_VERSION,SCHEMA_VERSION)).fetchone()[0]
    data["analysis_contract_version"] = PROMPT_VERSION
    data["analysis_schema_version"] = SCHEMA_VERSION
    data["analysis_queue_count"] = count
    if worker:
        heartbeat = _time(worker["heartbeat_at"])
        max_stale = config["model_load_timeout_seconds"] + config["generation_timeout_seconds"] + 60
        stale = heartbeat is None or (datetime.now(timezone.utc)-heartbeat).total_seconds() > max_stale
        data["analysis_worker_state"] = "DISCONNECTED" if stale else worker["state"]
    else:
        data["analysis_worker_state"] = "NOT_STARTED"
    for item in data.get("news", []):
        if item.get("analysis_review_decision") == "ACCEPT":
            item["analysis_display_kind"] = "CODEX_REVIEWED"
            continue
        job = jobs.get((item["item_key"], item["version"]))
        if not job or job["content_sha256"] != item.get("content_sha256"):
            item["analysis_status"] = "NOT_QUEUED"
            item["analysis"] = None
            item["analysis_display_kind"] = "SOURCE_RULE_FALLBACK"
            continue
        item["analysis_status"] = ("REVIEW_PENDING" if job["status"] == "DRAFT" and
                                   not AUTO_DRAFT_DISPLAY_ENABLED else job["status"])
        item["analysis_mechanical_status"] = job["status"]
        item["analysis_error_class"] = job["error_class"]
        item["analysis_model_revision"] = job["model_revision"]
        item["analysis_prompt_version"] = job["prompt_version"]
        item["analysis_schema_version"] = job["schema_version"]
        item["analysis_completed_at"] = job["completed_at"]
        item["analysis"] = (json.loads(job["result_json"]) if
                            AUTO_DRAFT_DISPLAY_ENABLED and job["status"] == "DRAFT" and
                            job["result_json"] else None)
        item["analysis_validation"] = json.loads(job["validation_json"]) if job["validation_json"] else None
        item["analysis_display_kind"] = "AI_DRAFT_AUTO_CHECKED" if item["analysis"] else "SOURCE_RULE_FALLBACK"
    by_key = {(item["item_key"], item["version"]): item for item in data.get("news", [])}
    for row in data.get("top10", []):
        item = by_key.get((row["item_key"], row["version"]))
        if item:
            row["title"] = item.get("display_title") or item.get("title") or "原文无标题"
            row["title_kind"] = item.get("display_title_kind")
            row["analysis_status"] = item["analysis_status"]
            row["tone"] = item["analysis"].get("tone", "UNKNOWN") if item.get("analysis") else "UNKNOWN"
            if item.get("analysis_display_kind") != "CODEX_REVIEWED":
                row["headline_zh"] = None
                row["headline_en"] = None
    now = datetime.now(timezone.utc)
    windows = []
    for hours in config["sentiment_windows_hours"]:
        seen = set(); tones = Counter(); pending = 0
        for item in data.get("news", []):
            published = _time(item.get("published_at_utc")) or _time(item.get("received_at"))
            if published is None or (now-published).total_seconds() > hours*3600:
                continue
            group = item.get("screening_v15", {}).get("event_group_id") or item["item_key"]
            if group in seen: continue
            seen.add(group)
            if item.get("analysis") and item["analysis_status"] in {"COMPLETE", "DRAFT"}:
                tones[item["analysis"].get("tone", "UNKNOWN")] += 1
            else: pending += 1
        windows.append({"hours": hours, "event_count": len(seen), "unanalysed_count": pending,
                        "insufficient": len(seen) < config["sentiment_min_events"],
                        "news_tone_counts": dict(tones), "observed_market_change": None,
                        "nature": "SUPPLIER_NEWS_TONE_AUTO_DRAFT_OR_REVIEWED"})
    data["sentiment_observation"] = windows
    return data
