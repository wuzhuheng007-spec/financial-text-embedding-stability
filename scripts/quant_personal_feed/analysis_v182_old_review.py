"""Record same-team semantic rereview of five adapted old responses."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .collector import ROOT

PRIVATE = ROOT / "artifacts/news_extraction_v182_20261117"
PUBLIC = ROOT / "results/news_extraction_v182_20261117"
OLD = ROOT / "artifacts/quant_personal_v18_1_20261117/frozen_tasks.json"

# Decisions are based on the cited frozen sentence and adapted field; these
# codes never modify the old response or grant owner-visible acceptance.
DECISIONS = {
    "V18-01-E03": {"actor": "SUPPORTED", "attribution": "OMITTED_SPEAKER",
                     "claim": "WRONG_ALLEGATION_FOR_OPINION", "category": "OMITTED_ENERGY_TOPIC",
                     "tone": "UNRESOLVED", "accept": False},
    "V18-03-E01": {"actor": "SUPPORTED", "attribution": "OMITTED_SPEAKER",
                     "claim": "WRONG_REPORT_FOR_OPINION", "category": "OMITTED_RATE_TOPIC",
                     "tone": "SUPPORTED_OR_NEUTRAL", "accept": False},
    "V18-08-E01": {"actor": "SUPPORTED", "attribution": "OMITTED_SPEAKER",
                     "claim": "UNRESOLVED_RATING_ACTION", "category": "UNRESOLVED",
                     "tone": "WRONG_NEUTRAL_FOR_UPGRADE_WORDING", "accept": False},
    "V18-08-E02": {"actor": "SUPPORTED", "attribution": "SUPPORTED_DENIAL_SOURCE",
                     "claim": "SUPPORTED_DENIAL", "category": "UNRESOLVED",
                     "tone": "UNRESOLVED_MIXED_RUMOR_AND_DENIAL", "accept": False},
    "V18-08-E03": {"actor": "SUPPORTED", "attribution": "NOT_EXPLICIT_IN_SENTENCE",
                     "claim": "SUPPORTED_ANNOUNCED_ACTION", "category": "UNRESOLVED",
                     "tone": "SUPPORTED_OR_NEUTRAL", "accept": False,
                     "scope": "OUTSIDE_TECH_FOCUS"},
}


def run() -> dict:
    adapted = {x["task_id"]: x for x in map(json.loads,
        (PRIVATE / "old_adapted_private.jsonl").read_text(encoding="utf-8").splitlines())}
    frozen = {x["task_id"]: x for x in json.loads(OLD.read_text(encoding="utf-8"))["tasks"]}
    if {key for key, item in adapted.items() if item["adapted_validation"]["valid"]} != set(DECISIONS):
        raise RuntimeError("MECHANICAL_PASS_SET_CHANGED")
    records = []
    for key, fields in DECISIONS.items():
        task = frozen[key]
        ids = {s["id"]: s["text"] for s in task["payload"]["sentences"]}
        view = adapted[key]["adapted_fields"]
        if not all(ref in ids for name, ref in view.items()
                   if name.endswith("_evidence_id") and ref is not None):
            raise RuntimeError("SOURCE_EVIDENCE_ID_CHANGED")
        records.append({"task_id": key, "old_raw_sha256": adapted[key]["old_raw_sha256"],
                        "source_hash": task["source_hash"], "decisions": fields,
                        "reviewer": "CODEX_REVIEW", "content_accept": False})
    target = PRIVATE / "old_semantic_rereview_private.json"
    if target.exists():
        raise FileExistsError("REREVIEW_EXISTS")
    target.write_text(json.dumps({"schema": "private_v182_semantic_rereview_1",
                                  "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
                                  "records": records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    issue_counts = Counter(v for r in records for k, v in r["decisions"].items()
                           if k != "accept" and v not in {"SUPPORTED", "SUPPORTED_DENIAL", "SUPPORTED_DENIAL_SOURCE", "SUPPORTED_ANNOUNCED_ACTION", "SUPPORTED_OR_NEUTRAL"})
    receipt = {"schema": "news_extraction_v182_old_semantic_rereview_redacted_1",
               "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
               "same_team_reviewed_mechanical_pass": len(records),
               "semantic_accepts": 0, "issue_labels": dict(issue_counts),
               "task_issues": {r["task_id"]: {k: v for k, v in r["decisions"].items() if k != "accept"}
                               for r in records},
               "old_source_or_response_modified": False,
               "private_review_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
               "claim": "representation-only adapter; no semantic promotion"}
    PUBLIC.mkdir(parents=True, exist_ok=True)
    (PUBLIC / "old_semantic_rereview_redacted.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"reviewed": len(records), "semantic_accepts": 0}


if __name__ == "__main__":
    print(json.dumps(run()))
