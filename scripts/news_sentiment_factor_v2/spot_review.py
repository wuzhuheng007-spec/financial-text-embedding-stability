"""Record a post-outcome source spot check without changing frozen scores."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone

from .corpus import PRIVATE, PUBLIC, write_json

ISSUES = {
    "64196df6ed2bf7c22649": "COUNTERFACTUAL_ADJUSTED_BASIS",
    "a6ef9e88b4708e723ce4": "MIXED_OPERATIONAL_DISCLOSURE",
    "a9e83335ccf7a9bb6c00": "ATTRIBUTED_MANAGEMENT_OPINION",
}


def main() -> None:
    manifest_path = PRIVATE / "input_manifest_private.json"
    blocks = json.loads(manifest_path.read_text(encoding="utf-8"))["blocks"]
    seen: Counter = Counter()
    selected = []
    for block in blocks:
        if len(block["text"]) <= 90 or seen[block["event_group_id"]] >= 2:
            continue
        seen[block["event_group_id"]] += 1
        selected.append(block)
    if len(selected) != 16 or {b["event_group_id"] for b in selected} != {b["event_group_id"] for b in blocks}:
        raise RuntimeError("SPOT_REVIEW_SELECTION_CHANGED")
    if not set(ISSUES).issubset({x["block_id"] for x in selected}):
        raise RuntimeError("REVIEW_IDS_CHANGED")
    records = [{"block_id": b["block_id"], "event_group_id": b["event_group_id"],
                "document_id": b["document_id"], "source_raw_sha256": b["raw_body_sha256"],
                "source_url": b["source_url"], "dom_path": b["dom_path"],
                "text_sha256": b["text_sha256"],
                "review_status": "CODEX_REVIEW_AFTER_OUTCOME",
                "source_identity": "SEC_ISSUER_EARNINGS_EXHIBIT_LINKED",
                "content_kind": ISSUES.get(b["block_id"], "ISSUER_RESULT_OR_INTRO"),
                "tone_validity": "WORDING_ONLY_NOT_FACT_TRUTH_OR_PRICE_DIRECTION"}
               for b in selected]
    private = PRIVATE / "source_spot_review_private.json"
    write_json(private, {"schema": "sentiment_v2_post_outcome_spot_review_1",
                         "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
                         "limitation": "Performed after descriptive return correlations; cannot satisfy blind pre-outcome review.",
                         "records": records})
    receipt = {"schema": "sentiment_v2_source_spot_review_redacted_1",
               "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
               "selection": "first two blocks over 90 characters per event in frozen block order",
               "source_blocks_total": len(blocks), "spot_reviewed_blocks": len(records),
               "spot_reviewed_events": len(seen),
               "content_kinds": dict(Counter(r["content_kind"] for r in records)),
               "review_status": "CODEX_REVIEW_AFTER_OUTCOME",
               "source_link_check": "eight SEC exhibit URLs opened in current run",
               "strict_blind_pre_outcome_review": "NOT_DONE",
               "private_review_sha256": hashlib.sha256(private.read_bytes()).hexdigest(),
               "input_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
               "promotion": "NONE"}
    write_json(PUBLIC / "source_spot_review_redacted.json", receipt)
    print(json.dumps({"reviewed_blocks": len(records), "reviewed_events": len(seen),
                      "blind_pre_outcome": False}))


if __name__ == "__main__":
    main()
