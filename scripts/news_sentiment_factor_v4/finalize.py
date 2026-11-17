"""Aggregate private receipts without exporting provider text, IDs or account data."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing

from .preflight import PRIVATE, PUBLIC, save, sha, CONFIG
from .probe import LEDGER
from .store import DB, english_candidate, summarize, utc_now


def main() -> dict:
    requests = [json.loads(x) for x in LEDGER.read_text(encoding="utf-8").splitlines() if x] if LEDGER.exists() else []
    coverage = summarize(DB)
    with closing(sqlite3.connect(DB)) as con:
        rows = con.execute("SELECT title,abstract,body,published_at_utc,time_evidence FROM articles").fetchall()
        tiers = {name: con.execute("SELECT COUNT(*) FROM articles WHERE time_evidence=?", (name,)).fetchone()[0]
                 for name in ("VERSION_PROVEN_AT_TIME", "PUBLISH_OR_UPDATE_PROXY", "UNKNOWN")}
    source_dates = sorted(x[3][:10] for x in rows if x[3])
    actual = {"schema": "news_sentiment_factor_v4_actual_redacted_1", "generated_at_utc": utc_now(),
              "config_sha256": sha(CONFIG), "provider_requests_total": len(requests),
              "provider_business_success": sum(x.get("http_status") == 200 and x.get("ret_code") == 0 for x in requests),
              "provider_business_failure": sum(not (x.get("http_status") == 200 and x.get("ret_code") == 0) for x in requests),
              "provider_response_rows": sum(int(x.get("item_count") or 0) for x in requests),
              "request_budget_total_max": json.loads(CONFIG.read_text(encoding="utf-8"))["request_budget"]["total_max"],
              "moomoo_requests": len(requests), "wrds_requests": 0, "massive_requests": 0,
              "distinct_article_versions_saved": coverage["article_versions"],
              "nonempty_body_versions_saved": sum(bool(x[2]) for x in rows),
              "english_title_candidates": sum(english_candidate(x[0] or "") for x in rows),
              "english_abstract_candidates": sum(english_candidate(x[1] or "") for x in rows),
              "english_body_candidates": sum(english_candidate(x[2] or "") for x in rows),
              "earliest_source_date_utc": source_dates[0] if source_dates else None,
              "latest_source_date_utc": source_dates[-1] if source_dates else None,
              "time_evidence_tiers": tiers,
              "historical_window_article_versions": coverage["historical_window_versions"],
              "source_language_semantic_review": "NOT_RUN_NO_ELIGIBLE_ENGLISH_ARTICLES",
              "fixed_english_semantic_sample_size": 0,
              "model_title_scores": coverage["completed_scores"],
              "qualified_prospective_scores": coverage["prospective_completed_scores"],
              "verified_event_clusters": coverage["verified_distinct_event_clusters"],
              "N1_N2_company_days": coverage["valid_company_days"],
              "A_B_C_D_fit_calls": 0,
              "april_july_2026_return_labels_read_or_scored": 0,
              "status": "ENGLISH_NEWS_SOURCE_NOT_QUALIFIED"}
    save(PUBLIC / "actual_summary_redacted.json", actual)
    return actual


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False))
