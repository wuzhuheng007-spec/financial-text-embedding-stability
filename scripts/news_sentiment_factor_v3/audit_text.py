"""Source locator and previous-selector omission audit; no returns are read."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from .source import PRIVATE, PUBLIC, digest, now, write_json
from .text import source_nodes

ROOT = Path(__file__).resolve().parents[2]
OLD = ROOT / "artifacts/news_sentiment_factor_v2_20261117/input_manifest_private.json"
NEW = PRIVATE / "text_input_private.json"


def audit():
    target = PUBLIC / "text_coverage_audit_redacted.json"
    if target.exists():
        raise FileExistsError("TEXT_AUDIT_EXISTS")
    frozen = json.loads(NEW.read_text(encoding="utf-8"))
    old = json.loads(OLD.read_text(encoding="utf-8"))
    by_path = defaultdict(list)
    for row in frozen["rows"]:
        by_path[row["source_path"]].append(row)
    exact = 0
    mismatched = []
    for path, rows in by_path.items():
        nodes = dict(source_nodes(Path(path).read_bytes()))
        for row in rows:
            text = nodes.get(row["dom_path"])
            start, end = row["normalized_node_offset"]
            if text is not None and text[start:end] == row["text"] and digest(Path(path)) == row["source_sha256"]:
                exact += 1
            else:
                mismatched.append(row["block_id"])
    old_skips = Counter()
    old_by_issuer = defaultdict(Counter)
    for entry in old["selection_ledger"]:
        old_skips.update(entry["skipped"])
        old_by_issuer[entry["symbol"]].update(entry["skipped"])
    new_by_issuer = defaultdict(Counter)
    for row in frozen["rows"]:
        new_by_issuer[row["symbol"]][row["section"]] += 1
    report = {"schema": "sentiment_v3_text_coverage_audit_redacted_1", "checked_at_utc": now(),
              "new_input_sha256": digest(NEW), "old_input_sha256": digest(OLD),
              "new_model_visible_blocks": len(frozen["rows"]), "exact_normalized_source_spans": exact,
              "locator_or_hash_mismatches": len(mismatched), "mismatch_ids": mismatched,
              "old_eight_events_selected_blocks": len(old["blocks"]),
              "old_eight_skip_reasons": dict(old_skips),
              "old_skip_reasons_by_issuer": {k: dict(v) for k, v in old_by_issuer.items()},
              "new_sections_by_issuer": {k: dict(v) for k, v in new_by_issuer.items()},
              "comparison_limit": "old and new SEC raw bytes have different hashes; selection counts are not a proof of equivalent narrative or semantics",
              "semantic_review": "source headings and fixed pre-outcome spot check only; full content and tone not independently certified"}
    write_json(target, report)
    return {k: report[k] for k in ("new_model_visible_blocks", "exact_normalized_source_spans", "locator_or_hash_mismatches", "old_eight_skip_reasons")}


if __name__ == "__main__":
    print(json.dumps(audit()))
