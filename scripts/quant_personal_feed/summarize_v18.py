"""Publish aggregate v1.8 receipts without private news or model responses."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone

from .analysis_v18_freeze import PRIVATE, PUBLIC
from .analysis_v18_store import DB


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def summarize():
    selection = read(PUBLIC / "selection_manifest_redacted.json")
    stages = {}
    for stage in ("T_SOURCE", "A", "B"):
        receipt = read(PUBLIC / f"run_baseline_{stage.lower()}_redacted.json")
        attempts = receipt["attempts"]
        reviews = [read(PRIVATE / "reviews" / f"baseline_{row['task_id']}_{stage}.json")
                   for row in attempts if (PRIVATE / "reviews" / f"baseline_{row['task_id']}_{stage}.json").exists()]
        stages[stage] = {
            "counts": receipt["counts"],
            "error_types": dict(sorted(Counter(row["error"] for row in attempts if row.get("error")).items())),
            "mechanical_error_labels": dict(sorted(Counter(label for row in attempts
                for label in (row.get("mechanical_errors") or [])).items())),
            "review_decisions": dict(sorted(Counter(row.get("decision", "UNREVIEWED") for row in reviews).items())),
            "raw_model_semantic_accepts": sum(row.get("decision") == "ACCEPT" for row in reviews),
            "separately_corrected_views": sum(row.get("decision") == "ACCEPT_CORRECTED" for row in reviews),
            "peak_cuda_bytes_max": max((row.get("metadata") or {}).get("peak_cuda_bytes") or 0 for row in attempts) if attempts else None,
            "generated_tokens_total": sum((row.get("metadata") or {}).get("generated_tokens") or 0 for row in attempts),
            "elapsed_seconds_total": round(sum(row.get("elapsed_seconds") or 0 for row in attempts), 3),
        }
    with sqlite3.connect(DB) as db:
        cache = {
            "reviewed_translation_rows": db.execute("SELECT count(*) FROM translation_cache").fetchone()[0],
            "candidate_only_tone_rows": db.execute("SELECT count(*) FROM prospective_tone WHERE feature_tier='CANDIDATE_ONLY'").fetchone()[0],
            "qualified_prospective_tone_rows": db.execute("SELECT count(*) FROM prospective_tone WHERE feature_tier='QUALIFIED_PROSPECTIVE'").fetchone()[0],
            "availability_before_completion_errors": db.execute("SELECT count(*) FROM prospective_tone WHERE signal_available_at_utc < analysis_completed_at_utc").fetchone()[0],
        }
    result = {
        "schema": "personal_news_v18_assessment_redacted_1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "selection": {"news": len(selection["tasks"]),
                      "additional_after_v17": len(selection["tasks"]) - 8,
                      "missing_fixed_categories": selection["additional_missing_categories"],
                      "ready_event_tasks": sum(row["event_run_count"] for row in selection["tasks"]),
                      "omitted_sub_events_by_fixed_budget": sum(row["event_omitted_count"] for row in selection["tasks"]),
                      "independent_test": False},
        "baseline": stages,
        "candidate": read(PUBLIC / "candidate_access_redacted.json"),
        "private_owner_cache_aggregate": cache,
        "interpretation_auto_queue": "PAUSED_NO_V18_B_CALLS",
        "financial_return_or_ic": "NOT_RUN",
        "source_verification": "SUPPLIER_REPORT_ONLY_NOT_INDEPENDENTLY_VERIFIED",
    }
    out = PUBLIC / "assessment_redacted.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    print(summarize())
