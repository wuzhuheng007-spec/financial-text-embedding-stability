"""Post-build format/provenance check; reads training-only private v2 outputs."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from .build import PRIVATE, PUBLIC, canonical, write_new
from .interface import cell_context, feature_record
from .parser import visible_text


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run() -> dict:
    docs = json.loads((PRIVATE / "documents.private.json").read_text(encoding="utf-8"))["documents"]
    blocks = read_jsonl(PRIVATE / "blocks.private.jsonl")
    events = read_jsonl(PRIVATE / "event_inputs.private.jsonl")
    by_doc = {d["document_id"]: d for d in docs}
    by_id = {b["block_id"]: b for b in blocks}
    if len(by_id) != len(blocks):
        raise RuntimeError("duplicate block ID")
    dom_checked = 0
    raw_phrase_missing = []
    cell_context_status = Counter()
    for doc in docs:
        raw = Path(doc["raw_body_path"]).read_bytes()
        if sha(raw) != doc["raw_body_sha256"]:
            raise RuntimeError("source hash changed after build: " + doc["document_id"])
        soup = BeautifulSoup(raw, "html.parser")
        for block in (b for b in blocks if b["document_id"] == doc["document_id"]):
            if block["source_sha256"] != doc["raw_body_sha256"]:
                raise RuntimeError("block source hash mismatch")
            if sha(block["raw_text"].encode()) != block["raw_text_sha256"] or sha(block["text"].encode()) != block["text_sha256"]:
                raise RuntimeError("block text hash mismatch")
            node = soup.select_one(block["dom_path"])
            if node is None:
                raise RuntimeError("DOM source locator not found: " + block["block_id"])
            dom_checked += 1
            if block["raw_text"] not in visible_text(node):
                raw_phrase_missing.append(block["block_id"])
            if block["element_kind"] == "table_row":
                for cell in block["table_cells"]:
                    cell_context_status[cell_context(block, cell)["basis_status"]] += 1
    if raw_phrase_missing:
        raise RuntimeError("block phrase not found at DOM source locator: " + raw_phrase_missing[0])
    seen_events = set()
    selected_count = 0
    omitted_count = 0
    for event in events:
        if event["event_group_id"] in seen_events:
            raise RuntimeError("duplicate event group")
        seen_events.add(event["event_group_id"])
        candidates = {b["block_id"] for b in blocks if b["event_group_id"] == event["event_group_id"]}
        selected = event["selected_block_ids"]
        omitted = [r["block_id"] for r in event["omitted_blocks"]]
        if len(selected) != len(set(selected)) or len(omitted) != len(set(omitted)):
            raise RuntimeError("duplicate selection or omission")
        if set(selected) & set(omitted) or set(selected) | set(omitted) != candidates:
            raise RuntimeError("silent or duplicate omission")
        if len(event["selected_text"]) > event["max_characters"] or sha(event["selected_text"].encode()) != event["selected_text_sha256"]:
            raise RuntimeError("adapter budget or hash mismatch")
        feature = feature_record(event)
        if feature["fit_status"] != "NOT_FITTED":
            raise RuntimeError("unexpected fit status")
        for offset in event["v2_clean_text_offsets"]:
            bid = offset["block_id"]
            part = f"[source={by_id[bid]['source_kind']}; block={bid}] {by_id[bid]['text']}"
            if event["selected_text"][offset["v2_assembled_text_start"]:offset["v2_assembled_text_end"]] != part:
                raise RuntimeError("assembled offset mismatch")
            if offset["offset_status"] != "CLEAN_TEXT_NOT_RAW_HTML_OFFSET":
                raise RuntimeError("raw offset confusion")
        selected_count += len(selected)
        omitted_count += len(omitted)
    receipt = {
        "schema": "financial_text_v2_format_validation_redacted_v1",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_document_count": len(docs), "block_count": len(blocks),
        "event_group_count": len(events), "dom_locators_checked": dom_checked,
        "selected_blocks": selected_count, "explicit_omitted_blocks": omitted_count,
        "table_cell_basis_candidate_status_counts": dict(cell_context_status),
        "raw_text_at_locator": "PASS", "source_hashes": "PASS", "adapter_whole_block_coverage": "PASS",
        "assembled_clean_offsets": "PASS", "model_fit_calls": 0,
        "source_contents_published": False,
        "basis_note": "Per-cell basis remains candidate or unresolved; no table cell is certified as a fact by this check.",
    }
    write_new(PUBLIC / "format_validation_redacted.json", canonical(receipt))
    return receipt


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
