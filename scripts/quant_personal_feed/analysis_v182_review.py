"""Same-team, field-level development review of frozen v1.8.2 responses.

Judgments are manually assigned after reading saved source sentences and raw
responses. This module only writes a new review version; it never repairs a
model response or promotes owner-visible/qualified prospective records.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone

from .collector import ROOT

PRIVATE = ROOT / "artifacts/news_extraction_v182_20261117"
PUBLIC = ROOT / "results/news_extraction_v182_20261117"
FIELDS = ("actor", "speaker", "reporter", "claim_object", "claim_kind",
          "category", "tone", "uncertainty")
LABELS = {"CORRECT", "ERROR", "OMITTED", "JUSTIFIED_ABSTAIN", "UNRESOLVED", "NOT_APPLICABLE"}

# Source comparison is a CODEX_REVIEW, not independent annotation or fact truth.
REVIEWS = {
    "V182-03": ("CORRECT", "ERROR", "OMITTED", "NOT_APPLICABLE", "OMITTED", "ERROR", "UNRESOLVED", "OMITTED"),
    "V182-04": ("ERROR", "ERROR", "OMITTED", "NOT_APPLICABLE", "OMITTED", "ERROR", "ERROR", "NOT_APPLICABLE"),
    "V182-06": ("CORRECT", "NOT_APPLICABLE", "OMITTED", "OMITTED", "OMITTED", "CORRECT", "JUSTIFIED_ABSTAIN", "OMITTED"),
    "V182-08": ("ERROR", "NOT_APPLICABLE", "NOT_APPLICABLE", "OMITTED", "OMITTED", "ERROR", "CORRECT", "NOT_APPLICABLE"),
    "V182-10": ("ERROR", "OMITTED", "OMITTED", "OMITTED", "OMITTED", "OMITTED", "ERROR", "NOT_APPLICABLE"),
    "V182-12": ("NOT_APPLICABLE", "NOT_APPLICABLE", "NOT_APPLICABLE", "NOT_APPLICABLE", "OMITTED", "JUSTIFIED_ABSTAIN", "JUSTIFIED_ABSTAIN", "NOT_APPLICABLE"),
    "V182-15": ("CORRECT", "ERROR", "OMITTED", "OMITTED", "ERROR", "ERROR", "CORRECT", "OMITTED"),
}
ISSUES = {
    "V182-03": ["REPORTER_MISLABELED_AS_SPEAKER", "ANNOUNCED_ACTION_OMITTED", "FUTURE_QUALIFIER_OMITTED"],
    "V182-04": ["METRIC_MISLABELED_AS_ACTOR", "REPORTER_MISLABELED_AS_SPEAKER", "GDP_CATEGORY_WRONG", "NEGATIVE_WORDING_LOST"],
    "V182-06": ["EXPLICIT_REPORTED_SOURCE_OMITTED", "SEEKING_ACQUISITION_UNCERTAINTY_OMITTED", "CLAIM_OMITTED"],
    "V182-08": ["REGULATED_OBJECT_MISLABELED_AS_ACTOR", "MACRO_CATEGORY_WRONG", "CLAIM_OMITTED"],
    "V182-10": ["STATEMENT_ISSUER_MISLABELED_AS_EVENT_ACTOR", "ATTRIBUTION_AND_CATEGORY_OMITTED", "CLAIM_OMITTED"],
    "V182-12": ["OUT_OF_FOCUS_FIRST_SUBEVENT", "FULL_ABSTENTION_NOT_EXTRACTION_SUCCESS"],
    "V182-15": ["SPEAKER_PHRASE_NOT_IN_CITED_SENTENCE", "PREDICTION_OR_OPINION_LOST", "CATEGORY_WRONG"],
}


def run() -> dict:
    freeze = json.loads((PRIVATE / "new_frozen_tasks_private.json").read_text(encoding="utf-8"))
    task_map = {x["task_id"]: x for x in freeze["tasks"]}
    receipt = json.loads((PUBLIC / "new_run_redacted.json").read_text(encoding="utf-8"))
    attempted = {x["task_id"] for x in receipt["attempts"]}
    if len(attempted) != 16 or attempted != set(task_map):
        raise RuntimeError("ATTEMPT_SET_CHANGED")
    parsed = {}
    for key in attempted:
        path = PRIVATE / "attempts" / f"{key}.json"
        item = json.loads(path.read_text(encoding="utf-8"))
        if item["parsed"] is not None:
            parsed[key] = item
    if set(parsed) != set(REVIEWS):
        raise RuntimeError("PARSED_SET_CHANGED")
    rows = []
    for key in sorted(attempted):
        item = json.loads((PRIVATE / "attempts" / f"{key}.json").read_text(encoding="utf-8"))
        if key in REVIEWS:
            assert len(REVIEWS[key]) == len(FIELDS)
            if not set(REVIEWS[key]).issubset(LABELS):
                raise RuntimeError("REVIEW_LABEL_INVALID")
            fields = dict(zip(FIELDS, REVIEWS[key]))
            status = "CODEX_REVIEW_REJECTED_OR_UNRESOLVED"
            errors = ISSUES[key]
        else:
            fields = {field: "NOT_EVALUABLE_FORMAT" for field in FIELDS}
            status = "RAW_JSON_PARSE_FAILURE"
            errors = ["FORMAT_ERROR"]
        rows.append({"task_id": key, "raw_sha256": item["raw_sha256"],
                     "source_hash": task_map[key]["source_hash"],
                     "mechanical_valid": (item["validation"] or {}).get("valid"),
                     "field_review": fields, "issues": errors,
                     "review_status": status, "content_accept": False,
                     "prospective_tone_qualified": False})
    target = PRIVATE / "new_field_review_private.json"
    if target.exists():
        raise FileExistsError("REVIEW_ALREADY_EXISTS")
    target.write_text(json.dumps({"schema": "private_v182_field_review_1",
                                  "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
                                  "reviewer": "CODEX_REVIEW", "independent_gold": False,
                                  "rows": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    by_field = {field: dict(Counter(row["field_review"][field] for row in rows)) for field in FIELDS}
    public = {"schema": "news_extraction_v182_field_review_redacted_1",
              "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
              "attempted_denominator": 16, "parseable_field_denominator": len(parsed),
              "format_not_evaluable": len(attempted) - len(parsed),
              "mechanical_valid": receipt["mechanical_valid"],
              "content_accept": 0, "prospective_tone_qualified": 0,
              "full_abstention": receipt["abstained"],
              "out_of_focus_first_subevent": 1,
              "field_status_counts": by_field,
              "issues_by_task": {row["task_id"]: row["issues"] for row in rows},
              "same_team_review": True,
              "limitation": "One claim_evidence_id cannot prove each of several claim_kinds across sentences; semantic gate did not promote any result.",
              "private_review_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
              "owner_display_updates": 0}
    PUBLIC.mkdir(parents=True, exist_ok=True)
    (PUBLIC / "new_field_review_redacted.json").write_text(
        json.dumps(public, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"attempted": 16, "parseable": len(parsed), "mechanical": receipt["mechanical_valid"],
            "content_accept": 0, "prospective_tone_qualified": 0}


if __name__ == "__main__":
    print(json.dumps(run()))
