"""Build versioned v2 earnings blocks from verified saved training originals."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .adapter import assemble
from .parser import parse_html


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs" / "market_observer_financial_text_v2.json"
MANIFEST = ROOT / "artifacts" / "market_observer_official_20261115" / "corpus_manifest.json"
PRIVATE = ROOT / "artifacts" / "market_observer_financial_text_v2_20261115"
PUBLIC = ROOT / "results" / "market_observer_financial_text_v2_20261115"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(data: object) -> bytes:
    return (json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite prior result: {path}")
    path.write_bytes(data)


def select_training_documents(manifest: dict, config: dict) -> list[dict]:
    scope = config["source_scope"]
    earnings = [d for d in manifest["documents"] if d.get("source_kind") == "ISSUER_EARNINGS"
                and d.get("symbol") in scope["symbols"]
                and scope["announcement_date_start"] <= d.get("announcement_date", "") <= scope["announcement_date_end"]]
    earnings.sort(key=lambda d: (d["announcement_date"], d["symbol"], d["event_group_id"], d["document_id"]))
    if len(earnings) != scope["expected_earnings_exhibits_before_run"]:
        raise RuntimeError("training earnings exhibit count changed")
    groups = {d["event_group_id"] for d in earnings}
    primary = [d for d in manifest["documents"] if d.get("source_kind") == "SEC_FILINGS"
               and d.get("event_group_id") in groups and d.get("symbol") in scope["symbols"]
               and scope["announcement_date_start"] <= d.get("announcement_date", "") <= scope["announcement_date_end"]]
    if len(primary) != scope["expected_matched_primary_filings_before_run"]:
        raise RuntimeError("training accession primary count changed")
    if len(groups) != len(earnings) or {d["event_group_id"] for d in primary} != groups:
        raise RuntimeError("exhibit-primary accession mapping is not one to one")
    docs = earnings + primary
    for doc in docs:
        if doc.get("full_body_status") != "FULL_BODY" or doc.get("version_status") != "SEC_ARCHIVE_HASHED":
            raise RuntimeError("source is not a full hashed archive: " + doc["document_id"])
        for field, hash_field in (("raw_body_path", "raw_body_sha256"), ("body_path", "body_sha256")):
            path = Path(doc[field])
            if not path.is_file() or sha(path.read_bytes()) != doc[hash_field]:
                raise RuntimeError("source hash mismatch: " + doc["document_id"] + " " + field)
    return sorted(docs, key=lambda d: (d["announcement_date"], d["symbol"], d["event_group_id"],
                                        0 if d["source_kind"] == "ISSUER_EARNINGS" else 1, d["document_id"]))


def run() -> dict:
    config_bytes, manifest_bytes = CONFIG.read_bytes(), MANIFEST.read_bytes()
    config = json.loads(config_bytes)
    manifest = json.loads(manifest_bytes)
    docs = select_training_documents(manifest, config)
    all_blocks: list[dict] = []
    document_rows = []
    for doc in docs:
        raw = Path(doc["raw_body_path"]).read_bytes()
        blocks = parse_html(raw, doc)
        if not blocks:
            raise RuntimeError("zero blocks parsed: " + doc["document_id"])
        document_rows.append({"document_id": doc["document_id"], "event_group_id": doc["event_group_id"],
                              "symbol": doc["symbol"], "announcement_date": doc["announcement_date"],
                              "source_kind": doc["source_kind"], "source_url": doc["source_url"],
                              "raw_body_sha256": doc["raw_body_sha256"], "body_sha256": doc["body_sha256"],
                              "raw_body_path": doc["raw_body_path"], "block_count": len(blocks),
                              "block_ids_sha256": sha(canonical([b["block_id"] for b in blocks]))})
        all_blocks.extend(blocks)
    by_event: dict[str, list[dict]] = defaultdict(list)
    for block in all_blocks:
        by_event[block["event_group_id"]].append(block)
    event_rows = []
    for event_id in sorted(by_event):
        blocks = by_event[event_id]
        adapted = assemble(blocks, config["v2_input"]["model_adapter_max_characters"])
        event_rows.append({"event_group_id": event_id, "symbol": blocks[0]["symbol"],
                           "announcement_date": blocks[0]["announcement_date"],
                           "ordered_document_ids": list(dict.fromkeys(b["document_id"] for b in sorted(
                               blocks, key=lambda b: (0 if b["source_kind"] == "ISSUER_EARNINGS" else 1, b["sequence"])))),
                           "available_block_count": len(blocks), **adapted})
    block_path = PRIVATE / "blocks.private.jsonl"
    event_path = PRIVATE / "event_inputs.private.jsonl"
    doc_path = PRIVATE / "documents.private.json"
    # All source content stays private. Each write refuses to replace old bytes.
    write_new(block_path, b"".join(canonical(b) for b in all_blocks))
    write_new(event_path, b"".join(canonical(r) for r in event_rows))
    write_new(doc_path, canonical({"documents": document_rows,
                                   "manifest_sha256": sha(manifest_bytes), "config_sha256": sha(config_bytes)}))
    counts = Counter(b["element_kind"] for b in all_blocks)
    roles = Counter(b["content_role"] for b in all_blocks)
    summary = {
        "schema": "financial_text_v2_build_redacted_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "role": config["role"], "source_manifest_sha256": sha(manifest_bytes),
        "config_sha256": sha(config_bytes), "scope": config["source_scope"],
        "source_document_count": len(docs), "earnings_exhibit_count": sum(d["source_kind"] == "ISSUER_EARNINGS" for d in docs),
        "associated_primary_count": sum(d["source_kind"] == "SEC_FILINGS" for d in docs),
        "event_group_count": len(event_rows), "block_count": len(all_blocks),
        "block_kinds": dict(counts), "content_roles": dict(roles),
        "selected_blocks": sum(len(r["selected_block_ids"]) for r in event_rows),
        "omitted_blocks": sum(len(r["omitted_blocks"]) for r in event_rows),
        "events_with_adapter_omissions": sum(bool(r["omitted_blocks"]) for r in event_rows),
        "table_rows_with_explicit_context": sum(b["header_relation_status"] == "EXPLICIT_CONTEXT" for b in all_blocks),
        "table_rows_with_ambiguous_context": sum(b["header_relation_status"] == "AMBIGUOUS_CONTEXT" for b in all_blocks),
        "private_output_hashes": {"blocks": sha(block_path.read_bytes()),
                                  "event_inputs": sha(event_path.read_bytes()),
                                  "documents": sha(doc_path.read_bytes())},
        "new_downloads": 0, "model_fit_calls": 0,
        "source_offsets_note": "DOM paths locate raw HTML elements; assembled-text offsets are not raw HTML offsets.",
        "table_context_note": "Header inference is a candidate aid, not a confirmed financial fact.",
    }
    write_new(PUBLIC / "build_redacted.json", canonical(summary))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    summary = run()
    if args.print_summary:
        print(json.dumps({k: v for k, v in summary.items() if k not in {"scope", "private_output_hashes"}},
                         ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
