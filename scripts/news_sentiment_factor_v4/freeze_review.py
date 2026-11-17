"""Freeze an outcome-blind English source-review sample before any historical score."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing

from .preflight import CONFIG, PRIVATE, PUBLIC, save, sha
from .store import DB, utc_now


def main() -> dict:
    private_path = PRIVATE / "source_review_selection_private.json"
    if private_path.exists():
        raise FileExistsError("REVIEW_SELECTION_ALREADY_FROZEN")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    with closing(sqlite3.connect(DB)) as con:
        rows = con.execute("""SELECT provider,article_id,version,text_sha256,
                 tickers_json,content_type,published_at_utc FROM articles
                 WHERE language='ENGLISH_CANDIDATE' AND observation_mode='BASELINE'
                 ORDER BY provider,tickers_json,content_type,published_at_utc,article_id,version
                 LIMIT ?""", (config["scoring"]["semantic_review_max"],)).fetchall()
    selected = [{"provider": r[0], "article_id": r[1], "version": r[2],
                 "text_sha256": r[3], "company_candidates": json.loads(r[4]),
                 "content_type": r[5], "published_at_utc": r[6]} for r in rows]
    save(private_path, {"schema": "news_v4_private_review_selection_1", "selected": selected})
    receipt = {"schema": "news_v4_review_selection_redacted_1", "frozen_at_utc": utc_now(),
               "config_sha256": sha(CONFIG), "selection_private_sha256": sha(private_path),
               "selected_article_versions": len(selected), "semantic_source_reviews_completed": 0,
               "selection_rule": config["scoring"]["semantic_review_selection"],
               "status": "NO_ELIGIBLE_ENGLISH_ARTICLES" if not selected else "AWAITING_SOURCE_REVIEW"}
    save(PUBLIC / "review_selection_redacted.json", receipt)
    return receipt


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False))
