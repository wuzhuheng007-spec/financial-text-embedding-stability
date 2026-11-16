"""Discover saved source tables and serialize source-bound evidence groups.

Production discovery and selection use HTML, saved document metadata, v2.4
relations, and a fixed topic order. Reviewed candidate answers, filing returns,
and validation scores are deliberately absent from this module. A structural
binding remains an unreviewed candidate, even when all references are visible.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
from typing import Any

from bs4 import BeautifulSoup, Tag

from scripts.market_observer_financial_text_v21.parser import dom_path, visible_text
from scripts.market_observer_financial_text_v23.structure import (
    TOPIC_ORDER, _grid, _topics, _without_nested_table_text,
)
from scripts.market_observer_financial_text_v24 import semantic as frozen_v24


SCHEMA = "financial_text_v25_expansion_dev1"
PACK_SCHEMA = "financial_text_v25_model_visible_dev1"
DEFAULT_BUDGET = 20_000
TOPIC_PRIORITY = tuple(TOPIC_ORDER)
TABLE_STATUSES = ("PROCESSED", "NESTED_OR_LAYOUT", "STRUCTURE_UNSUPPORTED", "PARSE_FAILED")
OMISSION_REASONS = (
    "RELATION_UNRESOLVED", "RELATION_CONFLICT", "NON_TARGET_TOPIC",
    "DUPLICATE_EVIDENCE", "STRUCTURE_UNSUPPORTED", "WHOLE_GROUP_OVER_BUDGET",
    "KNOWN_WRONG_RELATION", "SOURCE_PATH_UNRESOLVED",
)
RELATION_NAMES = (
    "row_metric", "column_header", "period", "currency", "scale",
    "measurement_kind", "basis", "footnote",
)


def _sha(raw: bytes) -> str:
    return sha256(raw).hexdigest()


def _id(prefix: str, *parts: str) -> str:
    return prefix + _sha("|".join(parts).encode("utf-8"))[:20]


def _direct_table_rows(table: Tag) -> list[Tag]:
    return [r for r in table.find_all("tr") if r.find_parent("table") is table]


def _discover_one(table: Tag) -> dict[str, Any]:
    """Classify from HTML geometry and cell roles, never target topic or answer."""
    path = dom_path(table)
    nested = table.find_parent("table") is not None
    rows = _direct_table_rows(table)
    record: dict[str, Any] = {
        "table_dom_path": path, "nested": nested, "direct_row_count": len(rows),
        "status": None, "reason": None, "numeric_cell_count": 0,
        "binding_count": 0,
    }
    if len(rows) < 2:
        record.update(status="NESTED_OR_LAYOUT", reason="FEWER_THAN_TWO_DIRECT_ROWS")
        return record
    try:
        logical_rows, physical = _grid(table)
    except Exception as exc:  # preserve a failure for every discovered table
        record.update(status="PARSE_FAILED", reason=f"GRID_{type(exc).__name__}")
        return record
    if not physical or any(c["span_status"] != "EXACT" for c in physical):
        record.update(status="STRUCTURE_UNSUPPORTED", reason="UNRESOLVED_GRID_SPAN")
        return record
    record["numeric_cell_count"] = sum(frozen_v24._measure(c["text"]) for c in physical)
    max_columns = max((c["column_end"] for c in physical), default=0)
    if max_columns < 2 or not record["numeric_cell_count"]:
        record.update(status="NESTED_OR_LAYOUT", reason="NO_TWO_COLUMN_NUMERIC_GRID")
        return record
    # A measure and geometry are enough to attempt the frozen semantic kernel.
    # Missing row labels are recorded by the parser, not silently filtered.
    record.update(status="DISCOVERED_FOR_PARSE", reason="SOURCE_GRID_WITH_NUMERIC_CELLS")
    return record


def discover_tables(raw_html: bytes, meta: dict) -> list[dict]:
    """List every table in document DOM order, including all exclusions."""
    if _sha(raw_html) != meta["raw_body_sha256"]:
        raise ValueError("source HTML hash mismatch")
    soup = BeautifulSoup(raw_html, "html.parser")
    return [_discover_one(t) for t in soup.find_all("table")]


def _paragraph_inventory(soup: BeautifulSoup, document_id: str) -> list[dict]:
    """Keep paragraph candidates separate; no table relation is invented."""
    rows = []
    for tag in soup.find_all(["p", "li", "div"]):
        if tag.find_parent("table") is not None:
            continue
        if tag.name == "div" and tag.find(["p", "li", "div", "table"]):
            continue  # a container must not duplicate its source paragraphs
        if tag.name == "li" and tag.find(["p", "div", "table"]):
            continue
        body = _without_nested_table_text(tag)
        topics = _topics(body)
        if not topics:
            continue
        path = dom_path(tag)
        rows.append({
            "paragraph_id": _id("P", document_id, path),
            "dom_path": path, "text_sha256": _sha(body.encode("utf-8")),
            "characters": len(body), "topics": topics,
            "status": "PARAGRAPH_CANDIDATE_NOT_TABLE_PARSED",
            "reason": "paragraph facts require a separate source-bound contract",
        })
    return rows


def parse_document_expanded(raw_html: bytes, meta: dict) -> dict:
    """Call the unchanged v2.4 table kernel on each discovered source table.

    Each table has its own receipt, so one bad table cannot erase other output.
    This is one parsing pass over one source; `parse_document` is not repeatedly
    invoked with reference-selected table paths.
    """
    if _sha(raw_html) != meta["raw_body_sha256"]:
        raise ValueError("source HTML hash mismatch")
    soup = BeautifulSoup(raw_html, "html.parser")
    inventory: list[dict] = []
    parsed_tables: list[dict] = []
    for table in soup.find_all("table"):
        row = _discover_one(table)
        if row["status"] == "DISCOVERED_FOR_PARSE":
            try:
                parsed = frozen_v24._table_bindings(
                    table, meta["document_id"], meta["raw_body_sha256"]
                )
                row["binding_count"] = len(parsed["bindings"])
                if parsed["bindings"]:
                    row.update(status="PROCESSED", reason="FROZEN_V24_KERNEL_COMPLETED")
                    parsed_tables.append(parsed)
                else:
                    row.update(status="STRUCTURE_UNSUPPORTED", reason="NO_BINDABLE_NUMERIC_ROW")
            except Exception as exc:
                row.update(status="PARSE_FAILED", reason=f"V24_{type(exc).__name__}")
        inventory.append(row)
    output = {
        "schema": SCHEMA, "kernel_schema": frozen_v24.SCHEMA,
        "document_id": meta["document_id"],
        "event_group_id": meta["event_group_id"], "symbol": meta["symbol"],
        "announcement_date": meta["announcement_date"],
        "source_kind": meta["source_kind"],
        "source_sha256": meta["raw_body_sha256"],
        "table_inventory": inventory, "tables": parsed_tables,
        "paragraph_inventory": _paragraph_inventory(soup, meta["document_id"]),
        "semantic_review_status": "NOT_REVIEWED_BY_PRODUCTION_PARSER",
        "source_contents_published": False,
    }
    if len(inventory) != len(soup.find_all("table")):
        raise AssertionError("table census is incomplete")
    if sum(x["status"] == "PROCESSED" for x in inventory) != len(parsed_tables):
        raise AssertionError("table census does not match output")
    return output


def _relations(binding: dict) -> dict[str, dict]:
    return {
        "row_metric": binding["row_metric"],
        "column_header": binding["column_header"],
        "period": binding["period"],
        "currency": binding["unit"]["currency"],
        "scale": binding["unit"]["scale"],
        "measurement_kind": binding["unit"]["measurement_kind"],
        "basis": binding["basis_relation"],
        "footnote": binding["footnote"],
    }


def _group(binding: dict, document: dict, table_order: int, binding_order: int) -> dict:
    doc = document["document_id"]
    value_path = binding["value_cell_dom_path"]
    relations = _relations(binding)
    return {
        "group_id": _id("G", doc, value_path), "document_id": doc,
        "event_group_id": document["event_group_id"], "symbol": document["symbol"],
        "source_kind": document["source_kind"], "source_sha256": document["source_sha256"],
        "table_dom_path": binding["table_dom_path"],
        "value_cell_dom_path": value_path,
        "table_order": table_order, "binding_order": binding_order,
        "topics": binding["topics"], "kernel_full_status": binding["full_status"],
        "relations": {name: {"status": r["status"], "source_dom_paths": r["source_dom_paths"]}
                      for name, r in relations.items()},
        "semantic_review_status": binding["semantic_review_status"],
        "selected": False, "omission_reason": None,
    }


def _sort_groups(groups: list[dict]) -> list[dict]:
    def within_status(rows: list[dict]) -> list[dict]:
        ordered = sorted(rows, key=lambda g: (
            0 if g["source_kind"] == "ISSUER_EARNINGS" else 1,
            g["document_id"], g["table_order"], g["binding_order"], g["group_id"],
        ))
        first_ids = []
        for topic in TOPIC_PRIORITY:
            first = next((g for g in ordered if topic in g["topics"]
                          and g["group_id"] not in first_ids), None)
            if first is not None:
                first_ids.append(first["group_id"])
        by_id = {g["group_id"]: g for g in ordered}
        return [by_id[gid] for gid in first_ids] + [g for g in ordered
                                                     if g["group_id"] not in first_ids]

    # A field-UNKNOWN group can remain in a diagnostic input, but it must not
    # consume the fixed budget before a fully explicit relation group. This
    # ordering uses only frozen kernel statuses, never reviewed answers.
    complete = [g for g in groups if g["kernel_full_status"] == "MECHANICALLY_COMPLETE"]
    partial = [g for g in groups if g["kernel_full_status"] != "MECHANICALLY_COMPLETE"]
    return within_status(complete) + within_status(partial)


def _source_node(soup: BeautifulSoup, path: str) -> Tag | None:
    try:
        node = soup.select_one(path)
    except Exception:
        return None
    return node if isinstance(node, Tag) else None


def _source_text(node: Tag) -> str:
    return _without_nested_table_text(node) if node.name in {"td", "th", "tr"} else visible_text(node)


def _eligible_reason(group: dict, *, rejected_keys: set[tuple[str, str]]) -> str | None:
    if (group["document_id"], group["value_cell_dom_path"]) in rejected_keys:
        return "KNOWN_WRONG_RELATION"
    if not group["topics"]:
        return "NON_TARGET_TOPIC"
    statuses = [r["status"] for r in group["relations"].values()]
    if "CONFLICT" in statuses:
        return "RELATION_CONFLICT"
    if group["relations"]["row_metric"]["status"] != "EXPLICIT":
        return "RELATION_UNRESOLVED"
    if group["relations"]["column_header"]["status"] != "EXPLICIT":
        return "RELATION_UNRESOLVED"
    # UNKNOWN is explicitly retained in a selected candidate; it is never a
    # fully confirmed fact. A conflicting explicit relation is never packed.
    return None


def assemble_event(parsed_docs: list[dict], source_html_by_document: dict[str, bytes],
                   max_characters: int = DEFAULT_BUDGET, *,
                   rejected_binding_keys: set[tuple[str, str]] | None = None) -> dict:
    """Return one exact model-visible diagnostic text and full omission ledger.

    Reviewed source judgments can veto a known-wrong binding but never change
    topic ranking or positively select a group. Source HTML must be the saved
    SHA-matched originals. All contexts, IDs, separators and status labels count
    in Python ``len(final_text)``; groups are never truncated to fit.
    """
    if not isinstance(max_characters, int) or max_characters < 1:
        raise ValueError("positive Unicode-character budget required")
    if not parsed_docs:
        raise ValueError("no parsed documents")
    event_ids = {d["event_group_id"] for d in parsed_docs}
    symbols = {d["symbol"] for d in parsed_docs}
    if len(event_ids) != 1 or len(symbols) != 1:
        raise ValueError("cross-event or cross-company documents")
    docs = {d["document_id"]: d for d in parsed_docs}
    if len(docs) != len(parsed_docs) or set(docs) != set(source_html_by_document):
        raise ValueError("duplicate or missing source document")
    soups: dict[str, BeautifulSoup] = {}
    for doc_id, document in docs.items():
        raw = source_html_by_document[doc_id]
        if _sha(raw) != document["source_sha256"]:
            raise ValueError("source HTML hash mismatch")
        soups[doc_id] = BeautifulSoup(raw, "html.parser")
    groups = [_group(binding, d, t_order, b_order)
              for d in parsed_docs
              for t_order, table in enumerate(d["tables"])
              for b_order, binding in enumerate(table["bindings"])]
    if len({g["group_id"] for g in groups}) != len(groups):
        raise ValueError("duplicate value-cell provenance")
    rejected = rejected_binding_keys or set()
    selected_lines: list[str] = []
    visible_contexts: dict[str, dict] = {}
    spans: list[dict] = []
    selected_ids: list[str] = []
    selected_paragraph_ids: list[str] = []
    paragraph_groups: list[dict] = []
    visible_value_keys: set[tuple[str, str]] = set()

    def append_line(line: str, *, kind: str, item_id: str, doc_id: str,
                    path: str, text: str, group_id: str | None) -> None:
        start = sum(len(x) + 1 for x in selected_lines)
        selected_lines.append(line)
        raw_start = line.index("] ") + 2
        if line[raw_start:] != text:
            raise AssertionError("source text changed during serialization")
        spans.append({
            "kind": kind, "id": item_id, "group_id": group_id,
            "document_id": doc_id, "source_sha256": docs[doc_id]["source_sha256"],
            "dom_path": path, "start": start, "end": start + len(line),
            "raw_start": start + raw_start, "raw_end": start + len(line),
            "raw_text_sha256": _sha(text.encode("utf-8")),
        })

    for group in _sort_groups(groups):
        reason = _eligible_reason(group, rejected_keys=rejected)
        if reason is not None:
            group["omission_reason"] = reason
            continue
        value_key = (group["document_id"], group["value_cell_dom_path"])
        if value_key in visible_value_keys:
            group["omission_reason"] = "DUPLICATE_EVIDENCE"
            continue
        soup = soups[group["document_id"]]
        value_node = _source_node(soup, group["value_cell_dom_path"])
        if value_node is None:
            group["omission_reason"] = "SOURCE_PATH_UNRESOLVED"
            continue
        value_text = _source_text(value_node)
        context_rows: list[tuple[str, str, str]] = []
        refs: dict[str, list[str]] = {}
        unresolved = False
        for name in RELATION_NAMES:
            paths = group["relations"][name]["source_dom_paths"]
            ids: list[str] = []
            for path in paths:
                if path == group["value_cell_dom_path"]:
                    continue  # direct marker resides in the model-visible value cell
                ref_id = _id("R", group["document_id"], path)
                ids.append(ref_id)
                if ref_id in visible_contexts or any(row[0] == ref_id for row in context_rows):
                    continue
                node = _source_node(soup, path)
                if node is None:
                    unresolved = True
                    break
                context_rows.append((ref_id, path, _source_text(node)))
            if unresolved:
                break
            refs[name] = list(dict.fromkeys(ids))
        if unresolved:
            group["omission_reason"] = "SOURCE_PATH_UNRESOLVED"
            continue
        lines = [f"[CTX id={rid} doc={group['document_id']} path={path}] {txt}"
                 for rid, path, txt in context_rows]
        ref_string = ";".join(
            f"{name}={group['relations'][name]['status']}:{','.join(refs[name]) or 'none'}"
            for name in RELATION_NAMES
        )
        value_line = (f"[VALUE id={group['group_id']} doc={group['document_id']} "
                      f"path={group['value_cell_dom_path']} refs={ref_string}] {value_text}")
        lines.append(value_line)
        current_length = len("\n".join(selected_lines))
        increment = len("\n".join(lines)) + (1 if selected_lines else 0)
        if current_length + increment > max_characters:
            group["omission_reason"] = "WHOLE_GROUP_OVER_BUDGET"
            continue
        for (rid, path, txt), line in zip(context_rows, lines):
            append_line(line, kind="CONTEXT", item_id=rid, doc_id=group["document_id"],
                        path=path, text=txt, group_id=None)
            visible_contexts[rid] = {
                "document_id": group["document_id"], "dom_path": path,
                "source_sha256": group["source_sha256"],
                "raw_text_sha256": _sha(txt.encode("utf-8")), "text": txt,
            }
        append_line(value_line, kind="VALUE", item_id=group["group_id"],
                    doc_id=group["document_id"], path=group["value_cell_dom_path"],
                    text=value_text, group_id=group["group_id"])
        group["selected"] = True
        group["relation_refs"] = refs
        group["model_visible_value_id"] = group["group_id"]
        selected_ids.append(group["group_id"])
        visible_value_keys.add(value_key)
    # Paragraph evidence remains a separately labeled source passage. It is
    # never promoted into v2.4's table relation or a complete numeric fact.
    for d in sorted(parsed_docs, key=lambda item: (
            0 if item["source_kind"] == "ISSUER_EARNINGS" else 1,
            item["document_id"])):
        for record in d["paragraph_inventory"]:
            row = {
                **record, "document_id": d["document_id"],
                "source_sha256": d["source_sha256"],
                "status": "OMITTED", "omission_reason": None,
                "structured_fact_status": "NOT_EVALUATED_PARAGRAPH_SEPARATE",
            }
            paragraph_groups.append(row)
            node = _source_node(soups[d["document_id"]], record["dom_path"])
            if node is None:
                row["omission_reason"] = "SOURCE_PATH_UNRESOLVED"
                continue
            paragraph_text = _source_text(node)
            if _sha(paragraph_text.encode("utf-8")) != record["text_sha256"]:
                row["omission_reason"] = "SOURCE_PATH_UNRESOLVED"
                continue
            line = (f"[PARAGRAPH id={record['paragraph_id']} doc={d['document_id']} "
                    f"path={record['dom_path']} status=UNSTRUCTURED_SOURCE_PASSAGE] {paragraph_text}")
            increment = len(line) + (1 if selected_lines else 0)
            if len("\n".join(selected_lines)) + increment > max_characters:
                row["omission_reason"] = "WHOLE_GROUP_OVER_BUDGET"
                continue
            append_line(line, kind="PARAGRAPH", item_id=record["paragraph_id"],
                        doc_id=d["document_id"], path=record["dom_path"],
                        text=paragraph_text, group_id=None)
            row["status"] = "PACKED_DIAGNOSTIC_PARAGRAPH"
            selected_paragraph_ids.append(record["paragraph_id"])
    final_text = "\n".join(selected_lines)
    if len(final_text) > max_characters:
        raise AssertionError("serialized input exceeds budget")
    _validate_serialized(final_text, groups, spans, visible_contexts)
    return {
        "schema": PACK_SCHEMA, "event_group_id": next(iter(event_ids)),
        "symbol": next(iter(symbols)), "max_characters": max_characters,
        "model_visible_text": final_text,
        "model_visible_text_sha256": _sha(final_text.encode("utf-8")),
        "selected_text_characters": len(final_text),
        "selected_group_ids": selected_ids, "group_inventory": groups,
        "selected_paragraph_ids": selected_paragraph_ids,
        "paragraph_group_inventory": paragraph_groups,
        "context_dictionary": visible_contexts, "source_spans": spans,
        "omission_counts": dict(Counter(g["omission_reason"] for g in groups if not g["selected"])),
        "selection_policy": "MECHANICALLY_COMPLETE_FIRST_THEN_FIXED_TOPIC_ISSUER_SOURCE_ORDER_NO_REFERENCE_INPUT",
        "character_count_policy": "PYTHON_LEN_FINAL_UNICODE_TEXT_ALL_IDS_REFS_LABELS_NEWLINES",
        "promotion_status": "REJECTED_CANDIDATE",
        "promotion_reason": "source semantic review of all selected relations is not supplied by this adapter",
        "model_fit_calls": 0, "model_inference_calls": 0,
    }


def _validate_serialized(text: str, groups: list[dict], spans: list[dict],
                         context_dictionary: dict[str, dict]) -> None:
    """Read back exact bytes and require every selected reference to be visible."""
    for span in spans:
        line = text[span["start"]:span["end"]]
        raw = text[span["raw_start"]:span["raw_end"]]
        prefix = {"CONTEXT": "[CTX ", "VALUE": "[VALUE ",
                  "PARAGRAPH": "[PARAGRAPH "}[span["kind"]]
        if (not line.startswith(prefix)
                or _sha(raw.encode("utf-8")) != span["raw_text_sha256"]):
            raise AssertionError("serialized span readback failed")
    by_id = {s["id"]: s for s in spans}
    if len(by_id) != len(spans):
        raise AssertionError("duplicate model-visible identifier")
    for group in groups:
        if not group["selected"]:
            continue
        value_span = by_id.get(group["group_id"])
        if value_span is None or value_span["kind"] != "VALUE":
            raise AssertionError("selected value is absent")
        line = text[value_span["start"]:value_span["end"]]
        for ids in group["relation_refs"].values():
            for rid in ids:
                ref = by_id.get(rid)
                if (rid not in context_dictionary or ref is None or ref["kind"] != "CONTEXT"
                        or ref["start"] >= value_span["start"] or rid not in line):
                    raise AssertionError("dangling or non-visible relation reference")


def validate_model_visible_event(event: dict) -> None:
    """Public readback hook for saved private pack output."""
    if event["schema"] != PACK_SCHEMA:
        raise ValueError("wrong pack schema")
    text = event["model_visible_text"]
    if len(text) != event["selected_text_characters"] or len(text) > event["max_characters"]:
        raise ValueError("character count mismatch")
    if _sha(text.encode("utf-8")) != event["model_visible_text_sha256"]:
        raise ValueError("model text hash mismatch")
    _validate_serialized(text, event["group_inventory"], event["source_spans"],
                         event["context_dictionary"])
