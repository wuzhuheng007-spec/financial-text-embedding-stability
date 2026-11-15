"""Select complete, model-visible source evidence groups under a fixed budget.

This module consumes only v2.1 parsed source blocks. It does not read reviewed
facts, candidate answers, returns, model scores, or later-period documents.
Parser hints remain unconfirmed context; this adapter creates no financial fact.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict

from scripts.market_observer_financial_text_v21.parser import (
    BASIS_RE, MONTH_DAY_RE, NUMERIC_RE, PERIOD_RE, TOPIC_RE, UNITS_RE,
)


TOPIC_ORDER = (
    "revenue", "net_income", "earnings_per_share", "gross_margin",
    "next_quarter_or_full_year_guidance", "capital_expenditures",
    "operating_cash_flow", "free_cash_flow", "important_segment_revenue",
)
MAX_TABLE_HEADER_ROWS = 24
MAX_SECTION_DISTANCE = 8
MAX_FOOTNOTE_DISTANCE = 12
FORWARD_GUIDANCE_RE = re.compile(r"\b(guidance|outlook|expects?|forecast|anticipates?|projects?|projected|upcoming quarter|next quarter)\b", re.I)


def _stable_id(prefix: str, *parts: object) -> str:
    return prefix + hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


def _sort_key(block: dict) -> tuple:
    return (0 if block["source_kind"] == "ISSUER_EARNINGS" else 1,
            block["document_id"], block["sequence"], block["block_id"])


def _topic_hits(block: dict) -> list[str]:
    """Parser topic hints name themes only; they never certify a source fact."""
    return [t for t in TOPIC_ORDER if t in block.get("topic_hits", ())]


def _visible_topic(topic: str, text: str) -> bool:
    if topic == "next_quarter_or_full_year_guidance":
        return bool(FORWARD_GUIDANCE_RE.search(text))
    return bool(TOPIC_RE[topic].search(text))


def _has_measurement(text: str) -> bool:
    """A fiscal year or date alone is not a disclosed numeric measurement."""
    if re.search(r"[$€£]\s*[−–-]?\s*\d|\d[\d,.]*\s*%", text):
        return True
    without_period = PERIOD_RE.sub(" ", MONTH_DAY_RE.sub(" ", text))
    return bool(NUMERIC_RE.search(without_period))


def _numeric_topic(block: dict) -> bool:
    if block.get("selection_status") == "STRUCTURAL_BOILERPLATE" or not _topic_hits(block):
        return False
    if block.get("element_kind") == "table_row":
        cells = block.get("table_cells") or []
        return bool(block.get("row_header") and any(_has_measurement(c.get("text", ""))
                                                    for c in cells[1:]))
    return block.get("element_kind") == "paragraph" and _has_measurement(block.get("raw_text", ""))


def _context(block: dict, role: str) -> dict:
    return {
        "context_id": _stable_id("C", block["document_id"], block["block_id"], role),
        "document_id": block["document_id"], "source_block_id": block["block_id"],
        "source_sha256": block["source_sha256"], "dom_path": block["dom_path"],
        "raw_locator_kind": block["raw_locator_kind"], "raw_text": block["raw_text"],
        "role": role,
        "cells": [{"column_start": c["column_start"], "text": c["text"]}
                  for c in block.get("table_cells") or [] if c.get("text")],
    }


def _heading_for(block: dict, doc_blocks: list[dict], *, first_table_sequence: int | None) -> dict | None:
    """Use only a heading adjacent to this section, never one across a table."""
    heading_text = block.get("heading") or ""
    if not heading_text:
        return None
    boundary = first_table_sequence if first_table_sequence is not None else block["sequence"]
    preceding = [b for b in doc_blocks if b["sequence"] < boundary]
    if not preceding:
        return None
    heading = next((b for b in reversed(preceding)
                    if b["element_kind"] == "heading" and b["raw_text"] == heading_text), None)
    if heading is None or boundary - heading["sequence"] > MAX_SECTION_DISTANCE:
        return None
    between = [b for b in preceding if heading["sequence"] < b["sequence"]]
    if any(b["element_kind"] == "heading" or b["element_kind"] == "table_row" for b in between):
        return None
    return heading


def _table_headers(block: dict, table_rows: list[dict]) -> tuple[list[dict], str | None]:
    """Bind only source rows preceding the first topic-bearing numeric row."""
    first_numeric = next((b["sequence"] for b in table_rows if _numeric_topic(b)), None)
    if first_numeric is None:
        return [], "CONTEXT_UNRESOLVED"
    headers = [b for b in table_rows if b["sequence"] < first_numeric and b["raw_text"]]
    if len(headers) > MAX_TABLE_HEADER_ROWS:
        return [], "CONTEXT_UNRESOLVED"
    # A second unit/period/basis header inside the same HTML table may start a
    # new subtable. The v2.1 parser does not prove that relation, so abstain.
    intervening = [b for b in table_rows if first_numeric < b["sequence"] < block["sequence"]]
    if any(not _numeric_topic(b) and (PERIOD_RE.search(b["raw_text"])
               or MONTH_DAY_RE.search(b["raw_text"]) or UNITS_RE.search(b["raw_text"])
               or BASIS_RE.search(b["raw_text"])) for b in intervening):
        return [], "CONTEXT_UNRESOLVED"
    return headers, None


def _adjacent_paragraph_context(block: dict, doc_blocks: list[dict]) -> dict | None:
    prior = next((b for b in reversed(doc_blocks) if b["sequence"] < block["sequence"]), None)
    if (prior is None or prior["sequence"] != block["sequence"] - 1
            or prior["element_kind"] != "paragraph" or _numeric_topic(prior)
            or prior.get("selection_status") == "STRUCTURAL_BOILERPLATE"):
        return None
    return prior if (PERIOD_RE.search(prior["raw_text"]) or MONTH_DAY_RE.search(prior["raw_text"])
                     or UNITS_RE.search(prior["raw_text"]) or BASIS_RE.search(prior["raw_text"])) else None


def _adjacent_table_preamble(doc_blocks: list[dict], first_table_sequence: int) -> dict | None:
    """Bind only a directly preceding source-visible nonnumeric table preamble."""
    prior = next((b for b in reversed(doc_blocks) if b["sequence"] < first_table_sequence), None)
    if (prior is None or prior["sequence"] != first_table_sequence - 1
            or prior["element_kind"] != "paragraph" or _numeric_topic(prior)
            or prior.get("selection_status") == "STRUCTURAL_BOILERPLATE"):
        return None
    return prior if (PERIOD_RE.search(prior["raw_text"]) or MONTH_DAY_RE.search(prior["raw_text"])
                     or UNITS_RE.search(prior["raw_text"]) or BASIS_RE.search(prior["raw_text"])) else None


def _footnote_contexts(anchor: dict, contexts: list[dict], doc_blocks: list[dict]) -> tuple[list[dict], str | None]:
    refs = set(anchor.get("footnote_refs") or ())
    for context in contexts:
        refs.update(context.get("footnote_refs") or ())
    if not refs:
        return [], None
    resolved = []
    for ref in sorted(refs):
        candidates = [b for b in doc_blocks if anchor["sequence"] < b["sequence"]
                      <= anchor["sequence"] + MAX_FOOTNOTE_DISTANCE
                      and b.get("raw_text", "").lstrip().lower().startswith(ref.lower())
                      and b["block_id"] != anchor["block_id"]]
        if anchor.get("element_kind") == "table_row":
            candidates = [b for b in candidates if
                          (b.get("table_index") == anchor.get("table_index") and b["element_kind"] == "table_row")
                          or (b["element_kind"] == "paragraph" and
                              not any(x["element_kind"] == "table_row" and
                                      x.get("table_index") != anchor.get("table_index")
                                      for x in doc_blocks if anchor["sequence"] < x["sequence"] < b["sequence"]))]
        if not candidates:
            return [], "CONTEXT_UNRESOLVED"
        resolved.append(candidates[0])
    return resolved, None


def _group_for(block: dict, doc_blocks: list[dict], table_rows: list[dict] | None) -> dict:
    group = {
        "group_id": _stable_id("G", block["document_id"], block["block_id"]),
        "document_id": block["document_id"], "anchor_block_id": block["block_id"],
        "source_sha256": block["source_sha256"], "dom_path": block["dom_path"],
        "raw_locator_kind": block["raw_locator_kind"], "raw_text": block["raw_text"],
        "source_kind": block["source_kind"], "sequence": block["sequence"],
        "element_kind": block["element_kind"], "topics": _topic_hits(block),
        "content_role": block.get("content_role", "other"),
        "context_ids": [], "contexts": [], "omission_reason": None,
    }
    context_blocks = []
    if table_rows is not None:
        headers, error = _table_headers(block, table_rows)
        if error:
            group["omission_reason"] = error
            return group
        first_sequence = table_rows[0]["sequence"]
        heading = _heading_for(block, doc_blocks, first_table_sequence=first_sequence)
        if heading:
            context_blocks.append((heading, "SECTION_HEADING"))
        preamble = _adjacent_table_preamble(doc_blocks, first_sequence)
        if preamble:
            context_blocks.append((preamble, "TABLE_PREAMBLE"))
        context_blocks.extend((b, "TABLE_HEADER") for b in headers)
    else:
        heading = _heading_for(block, doc_blocks, first_table_sequence=None)
        if heading:
            context_blocks.append((heading, "SECTION_HEADING"))
        adjacent = _adjacent_paragraph_context(block, doc_blocks)
        if adjacent and (not heading or adjacent["block_id"] != heading["block_id"]):
            context_blocks.append((adjacent, "ADJACENT_SECTION_CONTEXT"))

    visible = " ".join([block["raw_text"]] + [b["raw_text"] for b, _ in context_blocks])
    visible_topics = [topic for topic in group["topics"] if _visible_topic(topic, visible)]
    if not visible_topics:
        group["omission_reason"] = "CONTEXT_UNRESOLVED"
        return group
    group["topics"] = visible_topics
    has_period = bool(PERIOD_RE.search(visible) or MONTH_DAY_RE.search(visible))
    has_unit = bool(UNITS_RE.search(visible))
    if not has_period or not has_unit:
        group["omission_reason"] = "MISSING_REQUIRED_CONTEXT"
        return group
    if block.get("basis_hint") and not BASIS_RE.search(visible):
        group["omission_reason"] = "CONTEXT_UNRESOLVED"
        return group
    footnotes, error = _footnote_contexts(block, [b for b, _ in context_blocks], doc_blocks)
    if error:
        group["omission_reason"] = error
        return group
    context_blocks.extend((b, "FOOTNOTE") for b in footnotes)
    seen = set()
    contexts = []
    for source, role in context_blocks:
        if source["block_id"] in seen:
            continue
        seen.add(source["block_id"])
        if (source["document_id"] != block["document_id"]
                or source["source_sha256"] != block["source_sha256"]
                or not source.get("dom_path") or not source.get("raw_text")):
            group["omission_reason"] = "MISSING_PROVENANCE"
            return group
        contexts.append(_context(source, role))
    if not block.get("dom_path") or not block.get("source_sha256") or not block.get("raw_text"):
        group["omission_reason"] = "MISSING_PROVENANCE"
        return group
    group["contexts"] = contexts
    group["context_ids"] = [c["context_id"] for c in contexts]
    if table_rows is not None:
        group["cells"] = [{"column_start": c["column_start"], "text": c["text"]}
                          for c in block.get("table_cells") or [] if c.get("text")]
    return group


def _context_line(context: dict) -> str:
    line = (f"[CTX id={context['context_id']} doc={context['document_id']} "
            f"block={context['source_block_id']} role={context['role']}] " + context["raw_text"])
    if context["role"] == "TABLE_HEADER":
        cells = " | ".join(f"c{c['column_start'] + 1}={c['text']}" for c in context["cells"])
        line += " [source cell positions: " + cells + "]"
    return line


def _group_line(group: dict) -> str:
    refs = ",".join(group["context_ids"]) or "none"
    line = (f"[GROUP id={group['group_id']} doc={group['document_id']} "
            f"block={group['anchor_block_id']} refs={refs} "
            f"source_role={group['content_role']} kind={group['element_kind']}] " + group["raw_text"])
    if group["element_kind"] == "table_row":
        cells = " | ".join(f"c{c['column_start'] + 1}={c['text']}" for c in group.get("cells", ()))
        line += " [source cell positions: " + cells + "]"
    return line


def _priority(groups: list[dict]) -> list[dict]:
    eligible = [g for g in groups if g["omission_reason"] is None]
    ordered = sorted(eligible, key=lambda g: (0 if g["source_kind"] == "ISSUER_EARNINGS" else 1,
                                               g["document_id"], g["sequence"], g["group_id"]))
    first = []
    for topic in TOPIC_ORDER:
        candidate = next((g for g in ordered if topic in g["topics"] and g["group_id"] not in first), None)
        if candidate:
            first.append(candidate["group_id"])
    return [next(g for g in ordered if g["group_id"] == gid) for gid in first] + [
        g for g in ordered if g["group_id"] not in first]


def assemble(event_blocks: list[dict], max_characters: int) -> dict:
    """Return one bounded model-visible string and complete omission receipts."""
    if not isinstance(max_characters, int) or max_characters <= 0:
        raise ValueError("max_characters must be a positive integer")
    if not event_blocks:
        raise ValueError("event_blocks cannot be empty")
    ordered = sorted(event_blocks, key=_sort_key)
    if len({b["block_id"] for b in ordered}) != len(ordered):
        raise ValueError("duplicate source block ID")
    if len({b["event_group_id"] for b in ordered}) != 1 or len({b["symbol"] for b in ordered}) != 1:
        raise ValueError("cross-event or cross-company blocks")
    by_doc: dict[str, list[dict]] = defaultdict(list)
    by_table: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for block in ordered:
        by_doc[block["document_id"]].append(block)
        if block.get("element_kind") == "table_row" and block.get("table_index") is not None:
            by_table[(block["document_id"], block["table_index"])].append(block)
    if any(len({b["source_sha256"] for b in blocks}) != 1 for blocks in by_doc.values()):
        raise ValueError("source hash changed within one document")
    groups = []
    noncandidate = []
    for block in ordered:
        if not _numeric_topic(block):
            noncandidate.append({"block_id": block["block_id"],
                                 "reason": "STRUCTURAL_BOILERPLATE" if block.get("selection_status") == "STRUCTURAL_BOILERPLATE"
                                 else "NOT_TOPIC_NUMERIC"})
            continue
        table_rows = by_table.get((block["document_id"], block.get("table_index"))) if block["element_kind"] == "table_row" else None
        groups.append(_group_for(block, by_doc[block["document_id"]], table_rows))
    by_group = {g["group_id"]: g for g in groups}
    selected_lines: list[str] = []
    selected_line_meta: list[dict] = []
    seen_contexts: set[str] = set()
    selected_groups: set[str] = set()
    selected_group_order: list[str] = []
    for group in _priority(groups):
        needed = [c for c in group["contexts"] if c["context_id"] not in seen_contexts]
        lines = [_context_line(c) for c in needed] + [_group_line(group)]
        current_length = sum(len(line) for line in selected_lines) + max(0, len(selected_lines) - 1)
        incremental = len("\n".join(lines)) + (1 if selected_lines else 0)
        if current_length + incremental > max_characters:
            group["omission_reason"] = "WHOLE_GROUP_OVER_BUDGET"
            continue
        for context, line in zip(needed, lines[:-1]):
            seen_contexts.add(context["context_id"])
            selected_lines.append(line)
            selected_line_meta.append({"document_id": context["document_id"],
                                       "source_block_id": context["source_block_id"],
                                       "role": "CONTEXT_" + context["role"],
                                       "context_id": context["context_id"], "group_id": None,
                                       "source_sha256": context["source_sha256"],
                                       "dom_path": context["dom_path"],
                                       "raw_text": context["raw_text"]})
        selected_lines.append(lines[-1])
        selected_line_meta.append({"document_id": group["document_id"],
                                   "source_block_id": group["anchor_block_id"],
                                   "role": "ANCHOR_" + group["element_kind"].upper(),
                                   "context_id": None, "group_id": group["group_id"],
                                   "source_sha256": group["source_sha256"],
                                   "dom_path": group["dom_path"],
                                   "raw_text": group["raw_text"]})
        selected_groups.add(group["group_id"])
        selected_group_order.append(group["group_id"])
    text = "\n".join(selected_lines)
    if len(text) > max_characters:
        raise AssertionError("serialized model input exceeded fixed budget")
    spans = []
    cursor = 0
    for line, meta in zip(selected_lines, selected_line_meta):
        end = cursor + len(line)
        raw_start = line.index(meta["raw_text"], line.index("] ") + 2)
        span = {k: v for k, v in meta.items() if k != "raw_text"}
        span.update({"start": cursor, "end": end,
                     "raw_start": cursor + raw_start,
                     "raw_end": cursor + raw_start + len(meta["raw_text"]),
                     "offset_status": "MODEL_VISIBLE_CLEAN_TEXT_NOT_RAW_HTML_OFFSET"})
        if text[span["start"]:span["end"]] != line or text[span["raw_start"]:span["raw_end"]] != meta["raw_text"]:
            raise AssertionError("model-visible source span mismatch")
        spans.append(span)
        cursor = end + 1
    contexts_by_id = {c["context_id"]: c for g in groups for c in g["contexts"]}
    selected_contexts = [contexts_by_id[span["context_id"]] for span in spans if span["context_id"]]
    repeated = sum(len(_context_line(c)) + 1 for g in groups if g["group_id"] in selected_groups
                   for c in g["contexts"])
    shared = sum(len(_context_line(c)) + 1 for c in selected_contexts)
    inventory = [{"group_id": g["group_id"], "document_id": g["document_id"],
                  "anchor_block_id": g["anchor_block_id"], "context_ids": g["context_ids"],
                  "topics": g["topics"], "content_role": g["content_role"],
                  "status": "SELECTED" if g["group_id"] in selected_groups else "OMITTED",
                  "omission_reason": None if g["group_id"] in selected_groups else g["omission_reason"]}
                 for g in groups]
    selected_source_blocks = list(dict.fromkeys(span["source_block_id"] for span in spans))
    return {
        "schema": "financial_text_v22_model_input_v1",
        "event_group_id": ordered[0]["event_group_id"], "symbol": ordered[0]["symbol"],
        "selected_text": text, "selected_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "selected_text_characters": len(text), "max_characters": max_characters,
        "selected_group_ids": selected_group_order,
        "selected_context_ids": [c["context_id"] for c in selected_contexts],
        "selected_block_ids": selected_source_blocks,
        "omitted_groups": [{"group_id": g["group_id"], "document_id": g["document_id"],
                            "anchor_block_id": g["anchor_block_id"], "reason": g["omission_reason"]}
                           for g in groups if g["group_id"] not in selected_groups],
        "noncandidate_blocks": noncandidate,
        "group_inventory": inventory, "model_visible_spans": spans,
        "gross_duplicate_context_characters_avoided": repeated - shared,
        "reference_policy": "SHARED_SOURCE_CONTEXT_VISIBLE_BEFORE_GROUP;NO_SIDECAR_EVIDENCE",
        "selection_policy": "FIRST_COMPLETE_PER_FIXED_TOPIC_THEN_SOURCE_DOCUMENT_SEQUENCE",
        "character_count_policy": "PYTHON_LEN_OF_FINAL_UNICODE_TEXT_WITH_IDS_REFS_AND_NEWLINES",
        "model_fitted": False, "model_inference_calls": 0,
    }
