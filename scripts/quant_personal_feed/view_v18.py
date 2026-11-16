"""Owner-only reviewed v1.8 translation and tone overlay; no generation or provider IO."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from .analysis_v18_store import DB


def overlay(data: dict, *, owner_id: int, db_path: Path = DB) -> dict:
    data["v18_mode"] = "BOUNDED_DEVELOPMENT_OLD_AUTO_QUEUE_PAUSED"
    data["v18_translation_model_preference"] = "baseline_then_candidate"
    if not db_path.exists():
        data["v18_status"] = "NOT_IMPORTED"
        return data
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        binding = db.execute("SELECT owner_id FROM owner_binding").fetchone()
        if not binding or binding[0] != owner_id:
            data["v18_status"] = "OWNER_MISMATCH"
            return data
        translations = [dict(row) for row in db.execute("""SELECT item_key,news_version,source_hash,
            model_revision,prompt_version,translation_en,review_status,completed_at_utc
            FROM translation_cache WHERE owner_id=? AND review_status IN
            ('CODEX_REVIEW_ACCEPT','CODEX_CORRECTED_MACHINE_TRANSLATION')""", (owner_id,))]
        features = [dict(row) for row in db.execute("""SELECT item_key,news_version,source_hash,event_id,
            entity_id,tone,uncertainty,evidence_ids_json,feature_tier,review_status,analysis_completed_at_utc,
            signal_available_at_utc FROM prospective_tone WHERE owner_id=? AND feature_tier='QUALIFIED_PROSPECTIVE'""",
            (owner_id,))]
    by_news: dict[tuple[str, int, str], list[dict]] = {}
    for row in translations:
        by_news.setdefault((row["item_key"], row["news_version"], row["source_hash"]), []).append(row)
    feature_news: dict[tuple[str, int, str], list[dict]] = {}
    for row in features:
        feature_news.setdefault((row["item_key"], row["news_version"], row["source_hash"]), []).append(row)
    from .analysis_v18_run import read
    config = read(Path(__file__).resolve().parents[2] / "configs/quant_portal_personal_v18_20261117.json")
    preference = (config["baseline_revision"], config["candidate_revision"])
    for item in data.get("news", []):
        key = (item.get("item_key"), item.get("version"), item.get("content_sha256"))
        choices = by_news.get(key, [])
        if choices:
            choices.sort(key=lambda row: preference.index(row["model_revision"]) if row["model_revision"] in preference else 99)
            chosen = choices[0]
            item["source_translation_en"] = chosen["translation_en"]
            item["source_translation_status"] = ("MACHINE_TRANSLATION_CODEX_CORRECTED" if
                chosen["review_status"] == "CODEX_CORRECTED_MACHINE_TRANSLATION" else
                "MACHINE_TRANSLATION_CODEX_REVIEWED")
            item["source_translation_completed_at"] = chosen["completed_at_utc"]
            item["source_translation_model_revision"] = chosen["model_revision"]
        else:
            item["source_translation_status"] = "NOT_REVIEWED_OR_NOT_RUN"
        if feature_news.get(key):
            item["v18_event_features"] = [{k: row[k] for k in ("event_id", "entity_id", "tone", "uncertainty",
                "evidence_ids_json", "review_status", "analysis_completed_at_utc", "signal_available_at_utc")}
                for row in feature_news[key]]
    data["v18_status"] = "REVIEWED_CACHE_AVAILABLE"
    data["v18_reviewed_translations"] = len(translations)
    data["v18_qualified_feature_rows"] = len(features)
    return data
