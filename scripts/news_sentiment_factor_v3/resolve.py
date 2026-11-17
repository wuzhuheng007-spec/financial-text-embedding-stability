"""Versioned source-only resolution of ambiguous EX-99 earnings attachments.

The initial archive pass remains immutable. This pass prefers a unique indexed
EX-99.1 with financial-release heading, then records a separate source review.
No returns or prior model scores are opened.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from collections import Counter
from pathlib import Path

from .archive import parse_documents
from .source import CONFIG, PRIVATE, PUBLIC, digest, now, write_json


def text_head(raw: bytes, limit: int = 700) -> str:
    decoded = raw[:12000].decode("utf-8", "replace")
    decoded = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", decoded)
    plain = html.unescape(re.sub(r"(?s)<[^>]+>", " ", decoded))
    return " ".join(plain.split())[:limit]


def source_class(head: str) -> str:
    h = head.casefold()
    if re.search(r"production\s*,?\s*deliveries|production, deliveries|delivery and production", h):
        return "OPERATING_DELIVERIES_NOT_FINANCIAL_RELEASE"
    if re.search(r"financial results|quarterly results|earnings release|financial summary|reports (?:first|second|third|fourth|[q1-4]) quarter|quarterly earnings|income statement", h):
        return "EARNINGS_RELEASE_HEADING_CANDIDATE"
    return "HEADING_REVIEW_REQUIRED"


def resolve() -> dict:
    original = json.loads((PRIVATE / "archive_manifest_private.json").read_text(encoding="utf-8"))
    metadata = json.loads((PRIVATE / "metadata_private.json").read_text(encoding="utf-8"))
    meta_by_acc = {x["accessionNumber"]: x for x in metadata["selected_metadata_events"]}
    output = []
    for row in original["rows"]:
        item = dict(row)
        if row["status"] == "EARNINGS_EXHIBIT_UNRESOLVED":
            meta = meta_by_acc[row["accessionNumber"]]
            clean = row["accessionNumber"].replace("-", "")
            raw = PRIVATE / "raw" / f"{row['symbol']}_{clean}_complete.bin"
            index = PRIVATE / "raw" / f"{row['symbol']}_{clean}_index.bin"
            if digest(raw) != row["full_submission_sha256"] or digest(index) != row["index_sha256"]:
                item["resolution_status"] = "SOURCE_HASH_CONFLICT"
                output.append(item)
                continue
            names = {x["name"] for x in json.loads(index.read_bytes())["directory"]["item"]}
            matches = [d for d in parse_documents(raw.read_bytes())
                       if d["type"] == "EX-99.1" and d["filename"] in names and d["body"]]
            if len(matches) != 1:
                item["resolution_status"] = "UNIQUE_INDEXED_EX99_1_NOT_FOUND"
                output.append(item)
                continue
            chosen = matches[0]
            path = PRIVATE / "exhibits" / f"{row['symbol']}_{clean}.bin"
            if path.exists():
                raise FileExistsError("RESOLVER_WOULD_OVERWRITE_EXHIBIT")
            path.write_bytes(chosen["body"])
            item.update({"resolution_status": "UNIQUE_INDEXED_EX99_1_SOURCE_ONLY_RESOLUTION",
                         "exhibit_filename": chosen["filename"], "exhibit_type": chosen["type"],
                         "exhibit_sha256": hashlib.sha256(chosen["body"]).hexdigest(),
                         "exhibit_bytes": len(chosen["body"]), "exhibit_private_path": str(path),
                         "exhibit_submission_span": chosen["submission_span"]})
        else:
            item["resolution_status"] = "INITIAL_PASS_PRESERVED"
        if item.get("exhibit_private_path"):
            path = Path(item["exhibit_private_path"])
            if digest(path) != item["exhibit_sha256"]:
                raise ValueError("EXHIBIT_HASH_MISMATCH")
            head = text_head(path.read_bytes())
            item["head_private"] = head
            item["head_source_class"] = source_class(head)
            item["source_review_status"] = "PENDING_CODEX_REVIEW_BEFORE_OUTCOMES"
        output.append(item)
    if len(output) != len(meta_by_acc):
        raise ValueError("EVENT_COUNT_CHANGED")
    target = PRIVATE / "source_catalog_pre_review_private.json"
    if target.exists():
        raise FileExistsError(target)
    write_json(target, {"schema": "sentiment_v3_source_catalog_pre_review_1",
                        "created_at_utc": now(), "original_archive_sha256": digest(PRIVATE / "archive_manifest_private.json"),
                        "metadata_sha256": digest(PRIVATE / "metadata_private.json"),
                        "config_sha256": digest(CONFIG), "rows": output})
    summary = {"schema": "sentiment_v3_source_resolution_redacted_1", "created_at_utc": now(),
               "catalog_private_sha256": digest(target), "original_archive_sha256": digest(PRIVATE / "archive_manifest_private.json"),
               "source_only_resolution_count": sum(x["resolution_status"] == "UNIQUE_INDEXED_EX99_1_SOURCE_ONLY_RESOLUTION" for x in output),
               "heading_class_counts": dict(Counter(x.get("head_source_class", "NO_EXHIBIT") for x in output)),
               "all_source_review_pending": True, "outcomes_accessed": False}
    write_json(PUBLIC / "source_resolution_redacted.json", summary)
    return {k: summary[k] for k in ("source_only_resolution_count", "heading_class_counts", "outcomes_accessed")}


if __name__ == "__main__":
    print(json.dumps(resolve()))
