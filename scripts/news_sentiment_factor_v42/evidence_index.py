"""Hash immutable local evidence and publish paths only for redacted receipts."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .worker import PRIVATE, PUBLIC, write_json


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build() -> dict:
    backups=sorted((PRIVATE/"backups").glob("*.sqlite3"))
    private_files=[
      PRIVATE/"association_review_frozen_private.json",
      PRIVATE/"association_reference_private.json",
      PRIVATE/"association_review_result_v2_private.json",
      PRIVATE/"metadata_correction_private.json",
      PRIVATE/"observation_15m_private.jsonl",
      *sorted(PRIVATE.glob("snapshot_*_private.json")),
    ]
    public_names=[
      "association_review_v2_redacted.json",
      "fault_drills_redacted.json",
      "fault_drill_revision_redacted.json",
      "metadata_correction_redacted.json",
      "snapshot_checks_redacted.json",
      "browser_acceptance_redacted.json",
      "single_instance_live_redacted.json",
      "final_counts_redacted.json",
      "final_runtime_redacted.json",
      "plan_execution.csv",
      *[p.name for p in sorted(PUBLIC.glob("snapshot_*_redacted.json"))],
    ]
    receipt={"schema":"news_sentiment_factor_v42_evidence_index_1",
             "generated_at_utc":datetime.now(timezone.utc).isoformat(),
             "private_local_only":[{"logical_name":p.name,"sha256":sha(p),"bytes":p.stat().st_size}
                                   for p in [*backups,*private_files]],
             "redacted_shareable":[{"name":name,"sha256":sha(PUBLIC/name),
                                    "bytes":(PUBLIC/name).stat().st_size}
                                   for name in public_names],
             "live_sqlite_hash_not_used_as_immutable_input":"WAL_FILES_CAN_CHANGE_WHILE_SERVICES_RUN",
             "backups":len(backups),"private_source_text_exported":False}
    write_json(PUBLIC/"evidence_index_redacted.json",receipt)
    return {"backups":len(backups),"private_evidence_items":len(receipt["private_local_only"]),
            "redacted_items":len(receipt["redacted_shareable"])}


if __name__=="__main__":
    print(json.dumps(build(),ensure_ascii=False))
