"""Deterministic, no-fit whole-block adapter for a later text model."""
from __future__ import annotations

import hashlib
from collections import defaultdict


TOPIC_ORDER = (
    "revenue", "net_income", "earnings_per_share", "gross_margin",
    "next_quarter_or_full_year_guidance", "capital_expenditures",
    "operating_cash_flow", "free_cash_flow", "important_segment_revenue",
)


def _key(block: dict) -> tuple:
    return (0 if block["source_kind"] == "ISSUER_EARNINGS" else 1,
            block["sequence"], block["block_id"])


def _item_text(block: dict) -> str:
    return f"[source={block['source_kind']}; block={block['block_id']}] {block['text']}"


def assemble(event_blocks: list[dict], max_characters: int) -> dict:
    """Select complete blocks, preserve provenance, and record all omissions.

    The topic-first pass reserves room for the first available block of each
    financial theme before filling remaining space. It never cuts a block.
    This is an engineering adapter only: it does not fit a vocabulary/model.
    """
    if max_characters <= 0:
        raise ValueError("max_characters must be positive")
    ordered = sorted(event_blocks, key=_key)
    by_id = {b["block_id"]: b for b in ordered}
    if len(by_id) != len(ordered):
        raise ValueError("duplicate block ID")
    contenders = [b for b in ordered if b["selection_status"] != "STRUCTURAL_BOILERPLATE"]
    priority_ids: list[str] = []
    # Theme coverage is deterministic and never consults a label/validation row.
    for topic in TOPIC_ORDER:
        for block in contenders:
            if topic in block["topic_hits"] and block["block_id"] not in priority_ids:
                priority_ids.append(block["block_id"])
                break
    for block in contenders:
        if block["topic_hits"] and block["block_id"] not in priority_ids:
            priority_ids.append(block["block_id"])
    # Context neighbors preserve statements and caveats around keyword hits.
    neighbor_ids: set[str] = set()
    by_doc: dict[str, list[dict]] = defaultdict(list)
    for block in ordered:
        by_doc[block["document_id"]].append(block)
    for docs in by_doc.values():
        for i, block in enumerate(docs):
            if block["topic_hits"]:
                for j in (i - 1, i + 1):
                    if 0 <= j < len(docs):
                        neighbor_ids.add(docs[j]["block_id"])
    for block in contenders:
        if block["block_id"] in neighbor_ids and block["block_id"] not in priority_ids:
            priority_ids.append(block["block_id"])
    for block in contenders:
        if block["block_id"] not in priority_ids:
            priority_ids.append(block["block_id"])
    selected_ids: set[str] = set()
    used = 0
    for bid in priority_ids:
        part = _item_text(by_id[bid])
        additional = len(part) + (1 if selected_ids else 0)
        if used + additional <= max_characters:
            selected_ids.add(bid)
            used += additional
    selected = [b for b in ordered if b["block_id"] in selected_ids]
    pieces = []
    offsets = []
    cursor = 0
    for block in selected:
        part = _item_text(block)
        if pieces:
            cursor += 1
        offsets.append({"block_id": block["block_id"],
                        "v2_assembled_text_start": cursor,
                        "v2_assembled_text_end": cursor + len(part),
                        "offset_status": "CLEAN_TEXT_NOT_RAW_HTML_OFFSET"})
        pieces.append(part)
        cursor += len(part)
    text = "\n".join(pieces)
    if len(text) > max_characters:
        raise AssertionError("adapter exceeded character budget")
    omitted = [{"block_id": b["block_id"],
                "reason": "EXACT_STRUCTURAL_BOILERPLATE" if b["selection_status"] == "STRUCTURAL_BOILERPLATE"
                else "WHOLE_BLOCK_NOT_SELECTED_WITHIN_BUDGET"}
               for b in ordered if b["block_id"] not in selected_ids]
    return {"selected_block_ids": [b["block_id"] for b in selected],
            "omitted_blocks": omitted, "selected_text": text,
            "selected_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "selected_text_characters": len(text), "max_characters": max_characters,
            "v2_clean_text_offsets": offsets,
            "selection_policy": "TOPIC_FIRST_THEN_NEIGHBORS_THEN_SOURCE_ORDER_COMPLETE_BLOCKS",
            "model_fitted": False}
