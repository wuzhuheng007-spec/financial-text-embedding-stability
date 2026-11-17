"""Frozen same-team segment/entity review; separate from tone comparison."""
from __future__ import annotations

import json
from collections import Counter

from .freeze import PRIVATE, PUBLIC, now, sha, write


def main() -> dict:
    target = PUBLIC / "association_audit_redacted.json"
    if target.exists():
        raise FileExistsError(target)
    source = PRIVATE / "source_review_comparison_private.json"
    rows = json.loads(source.read_text(encoding="utf-8"))
    table = Counter((r["company_binding_review"], r["association_status"]) for r in rows)
    result = {"schema": "news_sentiment_v41_association_audit_redacted_1",
              "created_at_utc": now(), "reviewed_units": len(rows),
              "reviewer": "CODEX_REVIEW_NOT_INDEPENDENT_GOLD",
              "private_review_comparison_sha256": sha(source),
              "status_pairs": [{"source_review": a, "rule_output": b, "count": n}
                               for (a, b), n in sorted(table.items())],
              "partial_entity_recall_units": sum(r["company_binding_review"] == "PARTIAL_ENTITY_RECALL" for r in rows),
              "price_recap_units_in_review": sum(r["price_recap"] for r in rows),
              "candidate_factor_requires_entity_and_price_recap_review": True,
              "not_general_entity_accuracy": True}
    write(target, result)
    return result


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False))
