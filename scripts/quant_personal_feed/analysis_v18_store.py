"""Private, owner-bound reviewed translation cache and prospective tone ledger."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone

from .analysis_v15b import owner_id
from .analysis_v18_freeze import PRIVATE
from .analysis_contract_v18 import validate_translation
from .collector import ROOT

DB = ROOT / "apps/quant_portal/var/personal_news_v18.sqlite3"


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS owner_binding(owner_id INTEGER PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS translation_cache (
      owner_id INTEGER NOT NULL,item_key TEXT NOT NULL,news_version INTEGER NOT NULL,
      source_hash TEXT NOT NULL,source_original_hash TEXT NOT NULL,
      model_revision TEXT NOT NULL,prompt_version TEXT NOT NULL,target_language TEXT NOT NULL,
      raw_sha256 TEXT NOT NULL,translation_en TEXT NOT NULL,
      mechanical_status TEXT NOT NULL,review_status TEXT NOT NULL,
      completed_at_utc TEXT NOT NULL,stored_at_utc TEXT NOT NULL,
      PRIMARY KEY(owner_id,item_key,news_version,source_hash,model_revision,prompt_version,target_language));
    CREATE TABLE IF NOT EXISTS prospective_tone (
      owner_id INTEGER NOT NULL,item_key TEXT NOT NULL,news_version INTEGER NOT NULL,
      event_id TEXT NOT NULL,entity_id TEXT NOT NULL,source_hash TEXT NOT NULL,
      event_text_hash TEXT NOT NULL,event_group_id TEXT NOT NULL,
      published_at_utc TEXT,first_received_at_utc TEXT,
      analysis_completed_at_utc TEXT NOT NULL,signal_available_at_utc TEXT NOT NULL,
      model_revision TEXT NOT NULL,prompt_version TEXT NOT NULL,
      tone TEXT,uncertainty TEXT,evidence_ids_json TEXT NOT NULL,
      automatic_status TEXT NOT NULL,review_status TEXT NOT NULL,
      feature_tier TEXT NOT NULL,raw_sha256 TEXT,
      PRIMARY KEY(owner_id,item_key,news_version,event_id,entity_id,model_revision,prompt_version));
    """)
    owner = owner_id()
    rows = [row[0] for row in db.execute("SELECT owner_id FROM owner_binding")]
    if rows and rows != [owner]:
        db.close()
        raise RuntimeError("OWNER_MISMATCH")
    if not rows:
        db.execute("INSERT INTO owner_binding(owner_id) VALUES(?)", (owner,))
    db.commit()
    return db


def ingest() -> dict:
    manifest = _read(PRIVATE / "selection_private.json")
    counts = {"translations_review_accepted": 0, "translations_review_corrected": 0, "translation_candidates": 0,
              "tone_candidate_rows": 0, "tone_qualified_rows": 0, "missing_attempts": 0}
    with connect() as db:
        owner = owner_id()
        for news in manifest["tasks"]:
            for kind in ("baseline", "candidate"):
                trans_path = PRIVATE / "attempts" / f"{kind}_{news['task_id']}_T_SOURCE.json"
                if trans_path.exists():
                    attempt = _read(trans_path)
                    review_path = PRIVATE / "reviews" / f"{kind}_{news['task_id']}_T_SOURCE.json"
                    review = _read(review_path) if review_path.exists() else {}
                    accepted = (review.get("decision") == "ACCEPT" and
                                review.get("raw_sha256") == attempt.get("raw_sha256") and
                                (attempt.get("validation") or {}).get("valid"))
                    corrected = (review.get("decision") == "ACCEPT_CORRECTED" and
                                 review.get("raw_sha256") == attempt.get("raw_sha256") and
                                 isinstance(review.get("reviewed_translation_en"), str) and
                                 validate_translation({"translation_en": review["reviewed_translation_en"]},
                                                      news["source_original"])["valid"])
                    if (accepted or corrected) and isinstance((attempt.get("parsed") or {}).get("translation_en"), str):
                        text = review["reviewed_translation_en"] if corrected else attempt["parsed"]["translation_en"]
                        db.execute("""INSERT OR IGNORE INTO translation_cache VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (owner, news["item_key"], news["news_version"], news["source_hash"],
                             news["source_original_hash"], attempt["model_revision"] if "model_revision" in attempt else
                             (_read(ROOT / "configs/quant_portal_personal_v18_20261117.json")
                              ["baseline_revision" if kind == "baseline" else "candidate_revision"]),
                             "personal_news_v18_flat_1", "en", attempt["raw_sha256"],
                             text, "PASS_CORRECTED_VIEW" if corrected else "PASS_RAW",
                             "CODEX_CORRECTED_MACHINE_TRANSLATION" if corrected else "CODEX_REVIEW_ACCEPT",
                             attempt["completed_at_utc"], _now()))
                        counts["translations_review_corrected" if corrected else "translations_review_accepted"] += 1
                    else:
                        counts["translation_candidates"] += 1
                for event in news["events"]:
                    if event["run_gate"] != "READY":
                        continue
                    task_id = news["task_id"] + "-" + event["event_id"]
                    path = PRIVATE / "attempts" / f"{kind}_{task_id}_A.json"
                    if not path.exists():
                        counts["missing_attempts"] += 1
                        continue
                    attempt = _read(path)
                    review_path = PRIVATE / "reviews" / f"{kind}_{task_id}_A.json"
                    review = _read(review_path) if review_path.exists() else {}
                    valid = bool((attempt.get("validation") or {}).get("valid"))
                    accepted = valid and review.get("decision") == "ACCEPT" and review.get("raw_sha256") == attempt.get("raw_sha256")
                    fields = attempt.get("parsed") or {}
                    entities = sorted({row.get("entity_id") for row in fields.get("entity_roles", []) if isinstance(row, dict) and row.get("entity_id")}) if valid else []
                    if valid and fields.get("subject"):
                        entities = sorted(set(entities) | {fields["subject"]})
                    if not entities: entities = ["UNKNOWN"]
                    completed = attempt["completed_at_utc"]
                    received = news.get("first_received_at")
                    available = max(completed, received or completed)
                    event_hash = hashlib.sha256(event["text"].encode()).hexdigest()
                    group_id = hashlib.sha256((str(news.get("published_at") or "") + "|" +
                         "".join(event["text"].casefold().split())).encode()).hexdigest()[:24]
                    evidence_ids = sorted({value for key, value in fields.items() if key.endswith("_evidence_id") and isinstance(value, str)}) if valid else []
                    for entity in entities:
                        db.execute("""INSERT OR IGNORE INTO prospective_tone VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (owner, news["item_key"], news["news_version"], event["event_id"], entity,
                             news["source_hash"], event_hash, group_id, news.get("published_at"), received,
                             completed, available,
                             _read(ROOT / "configs/quant_portal_personal_v18_20261117.json")
                             ["baseline_revision" if kind == "baseline" else "candidate_revision"],
                             "personal_news_v18_flat_1", fields.get("tone") if valid else None,
                             fields.get("uncertainty") if valid else None,
                             json.dumps(evidence_ids), "PASS" if valid else "FAIL",
                             "CODEX_REVIEW_ACCEPT" if accepted else review.get("decision", "NOT_REVIEWED"),
                             "QUALIFIED_PROSPECTIVE" if accepted else "CANDIDATE_ONLY", attempt.get("raw_sha256")))
                        counts["tone_qualified_rows" if accepted else "tone_candidate_rows"] += 1
        db.commit()
    return counts


if __name__ == "__main__":
    print(json.dumps(ingest(), ensure_ascii=False))
