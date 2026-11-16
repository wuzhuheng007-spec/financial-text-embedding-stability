"""Export only reviewed, version-bound live development features; never labels."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.quant_personal_feed.analysis_contract_v17 import PROMPT_VERSION
from scripts.quant_personal_feed.collector import ROOT

PRIVATE = ROOT / "artifacts/quant_personal_v17_20261117"
PUBLIC = ROOT / "results/news_sentiment_factor_v1_20261117"
DEST = ROOT / "artifacts/news_sentiment_factor_v1_20261117/shadow_features_private.jsonl"


def stamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except ValueError:
        return None


def main() -> None:
    manifest = json.loads((PRIVATE / "selection_private.json").read_text(encoding="utf-8"))
    review_path = PRIVATE / "semantic_review_private.json"
    reviewed = json.loads(review_path.read_text(encoding="utf-8")) if review_path.exists() else {}
    accepted = []
    for task in manifest["tasks"]:
        if reviewed.get(task["task_id"], {}).get("A", {}).get("decision") != "ACCEPT":
            continue
        path = PRIVATE / "attempts" / f"{task['task_id']}_A.json"
        if not path.exists():
            continue
        item = json.loads(path.read_text(encoding="utf-8"))
        if not (item.get("validation") or {}).get("mechanical_valid"):
            continue
        completed = stamp(item.get("completed_at"))
        first = stamp(task.get("first_received_at"))
        if completed is None or first is None:
            continue
        usable = max(first, completed)
        parsed = item["parsed"]
        # Same-origin reprints are not resolved in the v1.7 small batch. These
        # records remain candidates, not additive daily sentiment weights.
        accepted.append({"news_id": task["item_key"], "news_version": task["news_version"],
            "event_id": hashlib.sha256(f"{task['item_key']}:{task['selected_candidate_index']}".encode()).hexdigest(),
            "event_dedup_status": "CROSS_SOURCE_UNVERIFIED",
            "entity_id": None, "entity_mapping_status": "NOT_VERIFIED",
            "published_at": task.get("published_at"), "first_received_at": task["first_received_at"],
            "analysis_completed_at": item["completed_at"], "usable_at_not_before": usable.isoformat(),
            "source_hash": task["source_hash"], "model_revision": "aa8e72537993ba99e69dfaafa59ed015b17504d1",
            "prompt_version": PROMPT_VERSION, "tone": parsed.get("tone", {}).get("value") if isinstance(parsed.get("tone"), dict) else None,
            "uncertainty": parsed.get("uncertainty", {}).get("value") if isinstance(parsed.get("uncertainty"), dict) else None,
            "evidence_refs": [value["quote"] for key, value in parsed.items() if isinstance(value, dict) and
                isinstance(value.get("quote"), str)],
            "validation_status": "CODEX_DEVELOPMENT_REVIEWED_SUPPLIER_CLAIM_UNVERIFIED",
            "future_return_label": None, "financial_experiment_status": "NOT_RUN"})
    DEST.parent.mkdir(parents=True, exist_ok=True)
    with DEST.open("w", encoding="utf-8") as handle:
        for record in accepted:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    PUBLIC.mkdir(parents=True, exist_ok=True)
    receipt = {"schema": "news_sentiment_shadow_v1_redacted", "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "frozen_source_items": len(manifest["tasks"]), "reviewed_extraction_records": len(accepted),
        "private_file_sha256": hashlib.sha256(DEST.read_bytes()).hexdigest(),
        "status": "NO_REVIEWED_FEATURE_RECORDS" if not accepted else
            "NO_FORWARD_FACTOR_WEIGHT_ALLOWED" if any(r["event_dedup_status"] != "CROSS_SOURCE_VERIFIED" for r in accepted)
            else "CANDIDATE_ONLY_LABELS_PENDING",
        "historical_training_ingest": False, "return_labels_computed": False}
    (PUBLIC / "shadow_receipt_redacted.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"records": len(accepted), "status": receipt["status"]}))


if __name__ == "__main__":
    main()
