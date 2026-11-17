"""Freeze a pre-outcome, source-heading review of accession EX-99.1 exhibits.

CODEX_REVIEW describes a same-team development check, not independent gold.
The complete narrative is still subject to later section and version audits.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from collections import Counter
from pathlib import Path

from .source import CONFIG, PRIVATE, PUBLIC, digest, now, write_json


def plain_prefix(raw: bytes) -> str:
    text = raw[:30000].decode("utf-8", "replace")
    return " ".join(html.unescape(re.sub(r"(?s)<[^>]+>", " ", text)).split())


def review() -> dict:
    catalog_path = PRIVATE / "source_catalog_pre_review_private.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))["rows"]
    target = PRIVATE / "source_review_private.json"
    if target.exists():
        raise FileExistsError(target)
    rows = []
    for source in catalog:
        row = {k: source.get(k) for k in ("symbol", "cik", "accessionNumber", "acceptanceDateTime",
                                          "filingDate", "index_sha256", "full_submission_sha256",
                                          "exhibit_sha256", "exhibit_filename", "exhibit_type",
                                          "exhibit_private_path", "index_url", "full_submission_url",
                                          "header_acceptance_raw", "header_acceptance_utc",
                                          "api_acceptance_utc", "resolution_status")}
        if not source.get("exhibit_private_path"):
            row["review_status"] = "EXCLUDE_NO_SOURCE_EXHIBIT"
            rows.append(row)
            continue
        exhibit = Path(source["exhibit_private_path"])
        if digest(exhibit) != source["exhibit_sha256"]:
            raise ValueError("SOURCE_HASH_CHANGED")
        prefix = plain_prefix(exhibit.read_bytes())
        evidence = re.search(r"(?i)(?:quarter|fiscal|financial)[^.!?]{0,100}(?:results|earnings)|results[^.!?]{0,100}(?:quarter|fiscal|financial)", prefix)
        if source.get("head_source_class") == "OPERATING_DELIVERIES_NOT_FINANCIAL_RELEASE":
            row["review_status"] = "EXCLUDE_OPERATING_DELIVERIES_NOT_EARNINGS"
            row["review_basis"] = "EX99.1 heading is production/deliveries/deployments, not an earnings release"
        elif source.get("head_source_class") == "EARNINGS_RELEASE_HEADING_CANDIDATE" or evidence:
            row["review_status"] = "ACCEPT_EARNINGS_EX99_1_SOURCE_HEADING"
            row["review_basis"] = "indexed EX99.1 with fiscal/quarter financial results heading or financial summary"
        else:
            row["review_status"] = "UNRESOLVED_HEADING"
            row["review_basis"] = "no clear financial release heading in first 30000 bytes"
        row["source_head_private"] = prefix[:700]
        row["supporting_phrase_private"] = evidence.group(0) if evidence else None
        row["review_scope"] = "CODEX_REVIEW_PRE_OUTCOME_HEAD_AND_PREFIX_NOT_FULL_TEXT_GOLD"
        rows.append(row)
    write_json(target, {"schema": "sentiment_v3_source_review_private_1", "reviewed_at_utc": now(),
                        "catalog_sha256": digest(catalog_path), "config_sha256": digest(CONFIG),
                        "outcome_labels_opened": False, "rows": rows})
    accepted = [r for r in rows if r["review_status"] == "ACCEPT_EARNINGS_EX99_1_SOURCE_HEADING"]
    dates = sorted({r["acceptanceDateTime"][:10] for r in accepted})
    date_folds = []
    for fraction in (0.50, 0.65, 0.80):
        boundary = dates[int(len(dates) * fraction)] if dates else None
        date_folds.append({"cut_fraction": fraction, "cut_date": boundary,
                           "events_before": sum(r["acceptanceDateTime"][:10] < boundary for r in accepted) if boundary else 0,
                           "events_from_cut": sum(r["acceptanceDateTime"][:10] >= boundary for r in accepted) if boundary else 0})
    counts = Counter(r["symbol"] for r in accepted)
    summary = {"schema": "sentiment_v3_source_review_redacted_1", "reviewed_at_utc": now(),
               "source_review_private_sha256": digest(target), "source_catalog_sha256": digest(catalog_path),
               "review_status_counts": dict(Counter(r["review_status"] for r in rows)),
               "accepted_events": len(accepted), "accepted_issuer_counts": dict(counts),
               "accepted_distinct_dates": len(dates),
               "potential_F2_events": sum(max(0, n - 2) for n in counts.values()),
               "source_only_date_fold_feasibility": date_folds,
               "reference_type": "CODEX_REVIEW_PRE_OUTCOME_HEAD_AND_PREFIX_NOT_INDEPENDENT_GOLD",
               "strict_historical_first_visibility": "NOT_PROVEN",
               "returns_read": False}
    write_json(PUBLIC / "source_review_redacted.json", summary)
    return {k: summary[k] for k in ("review_status_counts", "accepted_events", "accepted_distinct_dates", "potential_F2_events")}


if __name__ == "__main__":
    print(json.dumps(review()))
