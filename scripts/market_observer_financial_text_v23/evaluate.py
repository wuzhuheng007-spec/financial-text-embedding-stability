"""Frozen v2.3 *structural* evidence audit, separate from source review.

The production parser/adapter must never import this module.  The old fragment
retention scores (34/18/8) are immutable.  A new exact-cell relationship audit
cannot be backfilled onto old outputs that did not record a cell graph.

Four questions are deliberately distinct:
  1. Is a reviewed source fragment visible in serialized model input?
  2. Is an explicit reference from a visible numeric cell to visible context
     reachable in the same document and event?
  3. Does the linked row/column/scope actually correspond to that exact cell?
  4. Does a separate CODEX_REVIEW support the financial interpretation?

Neither the first three nor their conjunction imply the fourth.  This module
does not fit models, calculate returns, or alter source review judgments.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from scripts.market_observer_financial_text_v21.compare_retention import (
    COMPARISON_FIELDS, COMPARISON_SUPPORTED, FIELDS, fact_grade, field_retention, norm,
    source_element_present,
)


DEFINITION_VERSION = "v23_structure_audit_v1"
RELATION_KINDS = {"ROW_HEADER", "COLUMN_HEADER", "PERIOD", "UNIT", "BASIS", "FOOTNOTE",
                  "COMPARISON_VALUE", "COMPARISON_PERIOD", "COMPARISON_UNIT", "COMPARISON_BASIS"}
RELATION_STATES = {"EXPLICIT", "UNKNOWN", "CONFLICT", "NOT_APPLICABLE"}
NUMBER_RE = re.compile(r"(?<![A-Za-z0-9])(?:\(?\s*[+\-−–]?\s*[$€£]?\s*\d[\d,]*(?:\.\d+)?\s*\)?)(?![A-Za-z0-9])")


class StructureContractError(ValueError):
    """The parser/adapter contract itself is malformed or internally false."""


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_dom(path: str | None) -> str:
    """Compare saved XPath-like review locators with CSS-style parser paths."""
    if not path:
        return ""
    if ":nth-of-type(" in path:
        return " > ".join(x.strip().lower() for x in path.split(" > "))
    parts = []
    for part in path.split("/"):
        match = re.fullmatch(r"([A-Za-z0-9_-]+)\[(\d+)\]", part)
        if not match:
            return ""
        parts.append(f"{match.group(1).lower()}:nth-of-type({match.group(2)})")
    return " > ".join(parts)


def dom_contains(ancestor: str | None, descendant: str | None) -> bool:
    a, d = normalize_dom(ancestor), normalize_dom(descendant)
    return bool(a and d) and (a == d or d.startswith(a + " > "))


def numeric_tokens(text: str | None) -> tuple[str, ...]:
    """Literal number identity; never turn a partial row substring into a cell."""
    out = []
    for match in NUMBER_RE.finditer(text or ""):
        token = match.group().strip().replace("−", "-").replace("–", "-")
        previous = (text or "")[:match.start()].rstrip()[-1:]
        range_separator = previous in "0123456789%)]" and bool(previous)
        negative = (token.startswith("(") and token.endswith(")")) or (token.startswith("-") and not range_separator)
        token = token.strip("() ").replace("$", "").replace("€", "").replace("£", "")
        token = token.replace("+", "").replace("-", "").replace(",", "").strip()
        try:
            number = Decimal(token)
        except InvalidOperation:
            continue
        if negative:
            number = -number
        out.append(str(number.normalize()))
    return tuple(out)


def exact_numeric_cell_match(value_text: str, cell_text: str) -> bool:
    """Extra numbers in a cell make the numeric identity unresolved."""
    wanted, actual = numeric_tokens(value_text), numeric_tokens(cell_text)
    return bool(wanted) and wanted == actual


def _ids(rows: list[dict], key: str, label: str) -> dict[str, dict]:
    values = {r[key]: r for r in rows}
    if len(values) != len(rows) or any(not k for k in values):
        raise StructureContractError(f"duplicate or empty {label} ID")
    return values


def index_parsed_documents(documents: list[dict]) -> dict[str, dict]:
    """Check source graph and keep it separate from the serialized text graph."""
    indexed = {}
    for doc in documents:
        did = doc["document_id"]
        if did in indexed:
            raise StructureContractError("duplicate parsed document")
        fragments = _ids(doc["source_fragments"], "fragment_id", "fragment")
        cells = _ids(doc["numeric_cells"], "cell_id", "cell")
        relations = _ids(doc["relations"], "relation_id", "relation")
        groups = _ids(doc["evidence_groups"], "group_id", "group")
        for fid, fragment in fragments.items():
            if fragment["document_id"] != did or not fragment.get("dom_path") or not fragment.get("text"):
                raise StructureContractError(f"fragment identity/provenance invalid: {fid}")
        for cid, cell in cells.items():
            if cell["document_id"] != did or not cell.get("cell_dom_path") or not cell.get("text"):
                raise StructureContractError(f"cell identity/provenance invalid: {cid}")
            if cell.get("anchor_kind", "TABLE_CELL") == "TABLE_CELL":
                for axis in ("row", "column"):
                    start, end = cell.get(axis + "_start"), cell.get(axis + "_end")
                    if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start:
                        raise StructureContractError(f"invalid {axis} span: {cid}")
            elif cell["anchor_kind"] != "PARAGRAPH":
                raise StructureContractError(f"unknown numeric anchor kind: {cid}")
        for rid, relation in relations.items():
            if relation.get("kind") not in RELATION_KINDS or relation.get("status") not in RELATION_STATES:
                raise StructureContractError(f"invalid relation kind/state: {rid}")
            if relation.get("from_id") not in cells or not relation.get("method"):
                raise StructureContractError(f"relation has no source cell or method: {rid}")
            targets = relation.get("to_ids", [])
            if not isinstance(targets, list) or len(targets) != len(set(targets)):
                raise StructureContractError(f"relation target IDs malformed: {rid}")
            if relation["status"] == "EXPLICIT" and (not targets or not relation.get("scope_id")
                                                     or len(relation.get("source_dom_paths", [])) != len(targets)):
                raise StructureContractError(f"explicit relation lacks target/scope/source: {rid}")
            if any(target not in fragments and target not in cells for target in targets):
                raise StructureContractError(f"relation target missing in parsed document: {rid}")
        for gid, group in groups.items():
            anchor = group.get("anchor_cell_id")
            if anchor not in cells:
                raise StructureContractError(f"group has no numeric cell: {gid}")
            refs = group.get("relation_ids", [])
            if len(refs) != len(set(refs)) or any(r not in relations or relations[r]["from_id"] != anchor for r in refs):
                raise StructureContractError(f"group relation mismatch: {gid}")
        indexed[did] = {"meta": doc, "fragments": fragments, "cells": cells,
                        "relations": relations, "groups": groups}
    return indexed


def _span_line(text: str, span: dict, prefix: str) -> str:
    start, end = span.get("start"), span.get("end")
    if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(text):
        raise StructureContractError("model-visible span outside serialized input")
    line = text[start:end]
    if not line.startswith(prefix + span["id"]):
        raise StructureContractError("span ID is absent from the model-visible line")
    return line


def validate_visible_event(event: dict, docs: dict[str, dict], budget: int) -> dict[str, Any]:
    """Validate actual serialized [CTX]/[CELL] lines, not sidecar-only refs."""
    text = event["selected_text"]
    if (not isinstance(text, str) or len(text) > budget or len(text) != event["selected_text_characters"]
            or event.get("max_characters") != budget or sha(text.encode("utf-8")) != event["selected_text_sha256"]):
        raise StructureContractError("serialized character count, hash or budget mismatch")
    seen: set[str] = set()
    intervals = []
    span_by_id = {}
    anchor_span_by_source_id = {}
    visible_contexts: dict[str, tuple[str, dict, str]] = {}
    visible_cells: dict[str, tuple[str, dict, str]] = {}
    by_doc_text = defaultdict(list)
    for span in event["model_visible_spans"]:
        sid, kind, did = span["id"], span["kind"], span["document_id"]
        if sid in seen or did not in docs or docs[did]["meta"]["event_group_id"] != event["event_group_id"]:
            raise StructureContractError("duplicate span or cross-event document")
        if docs[did]["meta"]["symbol"] != event["symbol"]:
            raise StructureContractError("cross-company model span")
        seen.add(sid)
        span_by_id[sid] = span
        intervals.append((span["start"], span["end"]))
        if kind == "CTX":
            source = docs[did]["fragments"].get(span.get("source_id"))
            line = _span_line(text, span, "[CTX id=")
            if source is None or source["text"] not in line:
                raise StructureContractError("context span does not reveal its source text")
            if span["source_id"] in visible_contexts:
                raise StructureContractError("duplicate visible source context")
            visible_contexts[span["source_id"]] = (did, source, line)
        elif kind in {"CELL", "PARA"}:
            source = docs[did]["cells"].get(span.get("source_id"))
            line = _span_line(text, span, "[CELL id=" if kind == "CELL" else "[PARA id=")
            if source is None or source["text"] not in line:
                raise StructureContractError("cell span does not reveal its exact source cell text")
            if (kind == "CELL") != (source.get("anchor_kind", "TABLE_CELL") == "TABLE_CELL"):
                raise StructureContractError("numeric anchor kind differs from model-visible line")
            source_id = span["source_id"]
            if source_id in visible_cells and span.get("role", "ANCHOR") != "COMPANION":
                raise StructureContractError("duplicate source cell anchors")
            if source_id not in visible_cells or span.get("role", "ANCHOR") == "ANCHOR":
                visible_cells[source_id] = (did, source, line)
                anchor_span_by_source_id[source_id] = span
        else:
            raise StructureContractError("unknown model-visible span kind")
        by_doc_text[did].append((span["start"], line))
    for (_, end), (start, _) in zip(sorted(intervals), sorted(intervals)[1:]):
        if start < end:
            raise StructureContractError("model-visible spans overlap")
    if set(visible_contexts) != set(event["visible_context_ids"]):
        raise StructureContractError("visible context sidecar differs from text")
    groups = {g["group_id"]: g for g in event["group_inventory"]}
    if len(groups) != len(event["group_inventory"]):
        raise StructureContractError("duplicate group inventory ID")
    selected = {gid: g for gid, g in groups.items() if g["status"] == "SELECTED"}
    if set(selected) != set(event["selected_group_ids"]):
        raise StructureContractError("selected group inventory differs from text")
    for gid, group in selected.items():
        did, cid = group["document_id"], group["anchor_cell_id"]
        if cid not in visible_cells or visible_cells[cid][0] != did:
            raise StructureContractError("selected group cell is not visible")
        visible_anchor = group.get("anchor_visible_id", cid)
        anchor_span = span_by_id.get(visible_anchor)
        if (anchor_span is None or anchor_span["source_id"] != cid
                or anchor_span.get("role", "ANCHOR") != "ANCHOR"
                or anchor_span.get("group_id", gid) != gid):
            raise StructureContractError("selected group does not point to its visible anchor")
        parsed_group = docs[did]["groups"].get(gid)
        if parsed_group is None or parsed_group["anchor_cell_id"] != cid:
            raise StructureContractError("selected group differs from parsed graph")
        if set(group["relation_ids"]) != set(parsed_group["relation_ids"]):
            raise StructureContractError("selected relation IDs differ from parsed graph")
        cell_line = visible_cells[cid][2]
        for rid in group["relation_ids"]:
            relation = docs[did]["relations"][rid]
            if relation["status"] != "EXPLICIT":
                continue
            for target in relation["to_ids"]:
                visible_target = visible_contexts.get(target) or visible_cells.get(target)
                if visible_target is None or visible_target[0] != did:
                    raise StructureContractError("explicit relation target is not model-visible in same document")
                target_span = anchor_span_by_source_id.get(target) or span_by_id.get(target)
                if target_span is None or target_span["end"] > anchor_span["start"]:
                    raise StructureContractError("explicit relation target does not precede its numeric anchor")
                if not re.search(r"(?<![A-Za-z0-9_])" + re.escape(target) + r"(?![A-Za-z0-9_])", cell_line):
                    raise StructureContractError("cell line omits its context reference")
    return {"contexts": visible_contexts, "cells": visible_cells, "groups": groups,
            "selected_groups": selected, "by_document_text": {
                did: "\n".join(line for _, line in sorted(parts)) for did, parts in by_doc_text.items()},
            "span_by_id": span_by_id, "anchor_span_by_source_id": anchor_span_by_source_id}


def relation_scope_state(cell: dict, relation: dict, target: dict) -> str:
    """Check structural reach only; never declare a financial interpretation."""
    if relation["status"] == "CONFLICT":
        return "CONFLICT"
    if relation["status"] != "EXPLICIT" or not relation.get("to_ids"):
        return "UNRESOLVED"
    if target["document_id"] != cell["document_id"]:
        return "WRONG_BINDING"
    kind = relation["kind"].lower()
    if cell.get("anchor_kind", "TABLE_CELL") == "PARAGRAPH":
        if target.get("section_id") != cell.get("section_id"):
            return "WRONG_BINDING"
        if kind in {"row_header", "column_header", "comparison_value"}:
            return "UNRESOLVED"
        return "STRUCTURALLY_REACHABLE"
    if kind in {"row_header", "column_header"}:
        if (target.get("table_id") != cell.get("table_id")
                or target.get("subtable_id") != cell.get("subtable_id")):
            return "WRONG_BINDING"
        axis = "row" if kind == "row_header" else "column"
        lo, hi = target.get(axis + "_start"), target.get(axis + "_end")
        if not isinstance(lo, int) or not isinstance(hi, int):
            return "UNRESOLVED"
        if not lo <= cell[axis + "_start"] < hi:
            return "WRONG_BINDING"
        if kind == "row_header" and relation.get("method") == "SAME_ROW_LEFT_LABEL":
            if not isinstance(target.get("column_end"), int) or target["column_end"] > cell["column_start"]:
                return "WRONG_BINDING"
    elif kind == "comparison_value":
        if (target.get("table_id") != cell.get("table_id")
                or target.get("subtable_id") != cell.get("subtable_id")
                or target.get("row_start") != cell.get("row_start")
                or target.get("column_start") == cell.get("column_start")):
            return "WRONG_BINDING"
    elif target.get("table_id") is not None:
        if target.get("table_id") != cell.get("table_id"):
            return "WRONG_BINDING"
        if target.get("subtable_id") not in (None, cell.get("subtable_id")):
            return "WRONG_BINDING"
        if kind in {"period", "unit", "basis", "comparison_period", "comparison_unit", "comparison_basis"}:
            method = relation.get("method")
            tlo, thi = target.get("column_start"), target.get("column_end")
            rlo, rhi = target.get("row_start"), target.get("row_end")
            column_overlap = (isinstance(tlo, int) and isinstance(thi, int)
                              and tlo < cell["column_end"] and cell["column_start"] < thi)
            row_overlap = (isinstance(rlo, int) and isinstance(rhi, int)
                           and rlo < cell["row_end"] and cell["row_start"] < rhi)
            if method in {"COLUMN_GRID_OVERLAP", "COMPANION_COLUMN_PERIOD"} and not column_overlap:
                return "WRONG_BINDING"
            if method in {"COMPANION_COLUMN_UNIT", "COMPANION_COLUMN_BASIS"}:
                if target.get("kind") != "TABLE_CAPTION" and not (column_overlap or row_overlap):
                    return "WRONG_BINDING"
            if method == "CELL_ROW_OR_COLUMN_DECLARATION" and not (column_overlap or row_overlap):
                return "WRONG_BINDING"
            if method in {"SAME_ROW_PERIOD_CELL", "ROW_LABEL"}:
                if not row_overlap or not isinstance(target.get("column_end"), int) or target["column_end"] > cell["column_start"]:
                    return "WRONG_BINDING"
            if method == "ROW_AND_COLUMN_AGREEMENT" and not (column_overlap or row_overlap):
                return "WRONG_BINDING"
    elif target.get("section_id") != cell.get("section_id"):
        return "WRONG_BINDING"
    if not relation.get("scope_id") or not relation.get("method"):
        return "UNRESOLVED"
    if kind == "footnote" and relation.get("method") != "HTML_HREF_TARGET":
        return "UNRESOLVED"
    return "STRUCTURALLY_REACHABLE"


def candidate_cell_state(candidate: dict, review: dict, doc: dict) -> tuple[str, str | None]:
    """Row/div review locators cannot certify a numeric column by themselves."""
    evidence = review.get("field_evidence", {}).get("value", [])
    locators = [normalize_dom(e.get("dom_path")) for e in evidence if e.get("dom_path")]
    if not locators:
        return "REFERENCE_LOCATOR_UNKNOWN", None
    value = str(review["final"].get("value_text") or "")
    paragraph_locator = all(re.search(r"div:nth-of-type\(\d+\)$", x) for x in locators)
    eligible_kind = "PARAGRAPH" if paragraph_locator else "TABLE_CELL"
    exact = [cell for cell in doc["cells"].values()
             if cell.get("anchor_kind", "TABLE_CELL") == eligible_kind
             if any(dom_contains(locator, cell.get("cell_dom_path"))
                    or dom_contains(locator, cell.get("row_dom_path")) for locator in locators)
             and exact_numeric_cell_match(value, cell.get("measurement_text") or cell["text"])]
    if len(exact) == 1:
        return "UNIQUE_EXACT_VALUE_CELL", exact[0]["cell_id"]
    if len(exact) > 1:
        return "AMBIGUOUS_CELL_MATCH", None
    # A reviewed div can be a paragraph; an absent table grid does not
    # establish an erroneous column relation.
    if paragraph_locator:
        return "PARAGRAPH_NO_GRID_UNEVALUABLE", None
    return "NO_EXACT_VALUE_CELL", None


def structural_status_for_candidate(candidate: dict, review: dict, doc: dict,
                                    validated_event: dict) -> dict:
    """Return mechanical states and immutable CODEX_REVIEW state separately."""
    source_status = review["status"]
    if source_status not in {"CODEX_REVIEW_SOURCE_SUPPORTED", "CODEX_REVIEW_AMBIGUOUS"}:
        raise StructureContractError("unexpected frozen source review state")
    cell_state, cell_id = candidate_cell_state(candidate, review, doc)
    result = {"candidate_id": candidate["candidate_id"], "source_review": source_status,
              "cell_match": cell_state, "fragment": "MISSING", "reference": "NOT_REACHABLE",
              "structure": "UNRESOLVED", "known_fields": "UNRESOLVED",
              "all_fields": "UNRESOLVED", "error_reasons": []}
    visible_doc = validated_event["by_document_text"].get(candidate["source_document_id"], "")
    if any(source_element_present(e, visible_doc) for e in review.get("field_evidence", {}).get("value", [])):
        result["fragment"] = "PRESENT_REVIEWED_FRAGMENT"
    elif any(did == candidate["source_document_id"] and exact_numeric_cell_match(
            str(review["final"].get("value_text") or ""), source.get("measurement_text") or source["text"])
             for did, source, _ in validated_event["cells"].values()):
        result["fragment"] = "PRESENT_NUMERIC_TOKEN_UNBOUND"
    if cell_id is None:
        result["structure"] = cell_state
        return result
    group = next((g for g in doc["groups"].values() if g["anchor_cell_id"] == cell_id), None)
    if group is None:
        result["structure"] = "NO_PARSED_EVIDENCE_GROUP"
        return result
    visible = validated_event["cells"].get(cell_id)
    if visible is None:
        result["structure"] = "GROUP_NOT_IN_MODEL_INPUT"
        return result
    result["fragment"] = "PRESENT_EXACT_SOURCE_CELL"
    selected = validated_event["selected_groups"].get(group["group_id"])
    if selected is None:
        result["structure"] = "GROUP_NOT_IN_MODEL_INPUT"
        return result
    relation_states = {}
    comparison_relation = next((doc["relations"][rid] for rid in group["relation_ids"]
                                if doc["relations"][rid]["kind"] == "COMPARISON_VALUE"), None)
    comparison_cell = (doc["cells"].get(comparison_relation["to_ids"][0])
                       if comparison_relation and len(comparison_relation.get("to_ids", [])) == 1 else None)
    for rid in group["relation_ids"]:
        rel = doc["relations"][rid]
        kind = rel["kind"].lower()
        targets = [doc["fragments"].get(t) or doc["cells"].get(t) for t in rel.get("to_ids", [])]
        if rel["status"] == "NOT_APPLICABLE":
            relation_states[kind] = "NOT_APPLICABLE"
            continue
        if not targets or any(t is None for t in targets):
            relation_states[kind] = "UNRESOLVED"
            continue
        if any(tid not in validated_event["contexts"] and tid not in validated_event["cells"]
               for tid in rel["to_ids"]):
            relation_states[kind] = "NOT_REACHABLE"
            continue
        base_cell = (comparison_cell if kind in {"comparison_period", "comparison_unit", "comparison_basis"}
                     and comparison_cell is not None else doc["cells"][cell_id])
        states = [relation_scope_state(base_cell, rel, target) for target in targets]
        relation_states[kind] = ("WRONG_BINDING" if "WRONG_BINDING" in states else
                                 "CONFLICT" if "CONFLICT" in states else
                                 "UNRESOLVED" if "UNRESOLVED" in states else
                                 "STRUCTURALLY_REACHABLE")
    paragraph = doc["cells"][cell_id].get("anchor_kind", "TABLE_CELL") == "PARAGRAPH"
    required = ("period", "unit") if paragraph else ("row_header", "column_header", "period", "unit")
    result["relation_states"] = relation_states
    result["reference"] = ("REACHABLE" if all(relation_states.get(k) not in (None, "NOT_REACHABLE", "UNRESOLVED")
                                            for k in required) else "NOT_REACHABLE")
    missing = [k for k in required if relation_states.get(k) != "STRUCTURALLY_REACHABLE"]
    if missing:
        result["error_reasons"] = missing
        result["structure"] = "WRONG_BINDING" if any(relation_states.get(k) == "WRONG_BINDING" for k in missing) else "UNRESOLVED"
        return result
    result["structure"] = "PARAGRAPH_RELATIONS_MECHANICAL" if paragraph else "EXACT_CELL_RELATIONS_MECHANICAL"
    result["known_fields"] = "MECHANICALLY_COMPLETE_PENDING_CODEX_REVIEW"
    if source_status == "CODEX_REVIEW_AMBIGUOUS":
        result["all_fields"] = "SOURCE_REVIEW_AMBIGUOUS_NOT_PROMOTED"
        return result
    if review["final"].get("comparison_status") == COMPARISON_SUPPORTED:
        for kind in ("comparison_value", "comparison_period", "comparison_unit"):
            if relation_states.get(kind) != "STRUCTURALLY_REACHABLE":
                result["all_fields"] = "COMPARISON_RELATION_UNRESOLVED"
                result["known_fields"] = "COMPARISON_RELATION_UNRESOLVED"
                return result
        if review["final"].get("comparison_basis") is not None and relation_states.get("comparison_basis") != "STRUCTURALLY_REACHABLE":
            result["all_fields"] = "COMPARISON_BASIS_RELATION_UNRESOLVED"
            return result
        if review["final"].get("comparison_basis") is None:
            result["all_fields"] = "SOURCE_COMPARISON_BASIS_UNKNOWN"
            return result
    if relation_states.get("footnote") in ("UNRESOLVED", "CONFLICT", "WRONG_BINDING", "NOT_REACHABLE"):
        result["all_fields"] = "FOOTNOTE_RELATION_UNRESOLVED"
        return result
    if review["final"].get("basis") is None:
        result["all_fields"] = "SOURCE_BASIS_UNKNOWN"
    elif relation_states.get("basis") == "STRUCTURALLY_REACHABLE":
        result["all_fields"] = "MECHANICALLY_COMPLETE_PENDING_SOURCE_REVIEW"
    else:
        result["known_fields"] = "BASIS_RELATION_UNRESOLVED"
        result["all_fields"] = "BASIS_RELATION_UNRESOLVED"
    return result


def legacy_structure_status() -> str:
    """Old text-only outputs cannot be penalized for absent v2.3 lineage."""
    return "STRUCTURE_UNEVALUABLE_NO_CELL_RELATION_SCHEMA"


def private_witness_for_candidate(candidate: dict, review: dict, doc: dict,
                                  validated_event: dict, result: dict) -> dict | None:
    """Build a local-only audit witness; never put this object in public JSON."""
    if (result["source_review"] != "CODEX_REVIEW_SOURCE_SUPPORTED"
            or result["structure"] != "EXACT_CELL_RELATIONS_MECHANICAL"):
        return None
    cell_state, cell_id = candidate_cell_state(candidate, review, doc)
    if cell_state != "UNIQUE_EXACT_VALUE_CELL" or cell_id not in validated_event["cells"]:
        return None
    cell = doc["cells"][cell_id]
    group = next(g for g in doc["groups"].values() if g["anchor_cell_id"] == cell_id)
    relations = []
    for rid in group["relation_ids"]:
        relation = doc["relations"][rid]
        targets = []
        for target_id in relation.get("to_ids", []):
            target = doc["fragments"].get(target_id) or doc["cells"].get(target_id)
            targets.append({"target_id": target_id, "source_dom_path": target["dom_path"] if "dom_path" in target
                            else target["cell_dom_path"], "source_text": target["text"],
                            "model_visible_span": (validated_event["anchor_span_by_source_id"].get(target_id)
                                                   or validated_event["span_by_id"].get(target_id))})
        relations.append({"relation_id": rid, "kind": relation["kind"], "status": relation["status"],
                          "method": relation["method"], "scope_id": relation["scope_id"], "targets": targets})
    return {"candidate_id": candidate["candidate_id"], "review_role": "CODEX_REVIEW_NOT_INDEPENDENT",
            "frozen_source_review_status": review["status"], "mechanical_result": result,
            "cell_id": cell_id, "cell_dom_path": cell["cell_dom_path"],
            "row_dom_path": cell.get("row_dom_path"), "cell_text": cell["text"],
            "table_id": cell.get("table_id"), "subtable_id": cell.get("subtable_id"),
            "row_span": [cell["row_start"], cell["row_end"]],
            "column_span": [cell["column_start"], cell["column_end"]],
            "cell_model_visible_span": validated_event["anchor_span_by_source_id"][cell_id],
            "relations": relations, "semantic_field_decision": "PENDING_CODEX_REVIEW"}


ROOT = Path(__file__).resolve().parents[2]
PRIVATE_DIR = ROOT / "artifacts/market_observer_financial_text_v23_20261116"
PUBLIC_DIR = ROOT / "results/market_observer_financial_text_v23_20261116"
CONFIG = ROOT / "configs/market_observer_financial_text_v23_20261116.json"
CANDIDATES = ROOT / "artifacts/market_observer_financial_text_v2_20261115/fact_cards/fact_candidates_v2.private.json"
REVIEW = ROOT / "artifacts/market_observer_financial_text_v2_20261115/fact_cards/fact_review_v4.private.json"
LEGACY = ROOT / "artifacts/market_observer_financial_text_v22_20261116/retention_comparison.private.json"
FROZEN_V21 = ROOT / "artifacts/market_observer_financial_text_v21_20261115/retention_comparison.private.json"
FROZEN_INPUT_SHA256 = {
    CANDIDATES: "4b349b448ac7238d7182e49ec4a3d3906ed10eddad8f49b9a1df12cd277c6f5f",
    REVIEW: "523fd9e58256fdf179d55e11faa2d4b0ae8193cc873585d7b7263c8bbd1f92b9",
    LEGACY: "73681ef1bd0098f1f6c8c326eb7a6d17ba848e0dd681a3ef7648ae3e85238ff7",
    FROZEN_V21: "dcbd2581f78c8f1a5278912092ad7c380cb4ef1ed8946b0e83f3a15553537494",
}


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _canonical(obj: object) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def index_validated_events(indexed_docs: dict[str, dict], events: list[dict],
                           budget: int) -> tuple[dict[str, dict], Counter]:
    """Map every parsed document to its event, even when it has no visible spans.

    Primary filings may be parsed but contribute no selected evidence group.
    Their absence from serialized text is an omission, not a missing event.
    """
    by_event = {}
    omissions = Counter()
    for event in events:
        event_id = event["event_group_id"]
        if event_id in by_event:
            raise StructureContractError("duplicate serialized event")
        validated = validate_visible_event(event, indexed_docs, budget)
        by_event[event_id] = validated
        for group in event["group_inventory"]:
            if group["status"] != "SELECTED":
                omissions[group.get("omission_reason") or "UNKNOWN_REASON"] += 1
    expected_events = {doc["meta"]["event_group_id"] for doc in indexed_docs.values()}
    if set(by_event) != expected_events:
        raise StructureContractError("parsed and serialized event groups differ")
    by_document = {did: by_event[doc["meta"]["event_group_id"]]
                   for did, doc in indexed_docs.items()}
    return by_document, omissions


def evaluate_frozen_inputs(documents: list[dict], events: list[dict],
                           candidates: list[dict], reviews: list[dict],
                           legacy_rows: list[dict], frozen_v21_rows: list[dict],
                           config: dict) -> tuple[dict, dict, dict]:
    """One comparable fragment check plus separately labeled v2.3 cell audit."""
    by_candidate = _ids(candidates, "candidate_id", "candidate")
    by_review = _ids(reviews, "candidate_id", "review")
    by_legacy = _ids(legacy_rows, "candidate_id", "legacy row")
    by_v21 = _ids(frozen_v21_rows, "candidate_id", "v2.1 frozen row")
    if not (len(by_candidate) == len(by_review) == len(by_legacy) == 71
            and len(by_v21) == 71 and set(by_candidate) == set(by_review) == set(by_legacy) == set(by_v21)):
        raise StructureContractError("frozen 71-candidate identity differs")
    if Counter(r["status"] for r in reviews) != Counter({
            "CODEX_REVIEW_SOURCE_SUPPORTED": 60, "CODEX_REVIEW_AMBIGUOUS": 11}):
        raise StructureContractError("frozen 60/11 source review differs")
    indexed_docs = index_parsed_documents(documents)
    if len(indexed_docs) != config["source"]["expected_earnings_exhibits"] + config["source"]["expected_matched_primary_filings"]:
        raise StructureContractError("parsed source document denominator differs")
    budget = config["adapter"]["max_characters_per_event"]
    if len(events) != config["source"]["expected_events"]:
        raise StructureContractError("event denominator differs")
    validated_by_doc, omit = index_validated_events(indexed_docs, events, budget)
    rows = []
    witnesses = []
    field_counts = Counter()
    for candidate_id, candidate in by_candidate.items():
        review = by_review[candidate_id]
        legacy = by_legacy[candidate_id]
        did = candidate["source_document_id"]
        doc = indexed_docs.get(did)
        if doc is None:
            raise StructureContractError("candidate source document is outside frozen input")
        event = validated_by_doc[did]
        selected_text = event["by_document_text"].get(did, "")
        compare = review["final"].get("comparison_status") == COMPARISON_SUPPORTED
        fields = FIELDS + (COMPARISON_FIELDS if compare else ())
        field_states = {}
        for field in fields:
            state = field_retention(review["field_evidence"].get(field, []), old_text="",
                                    selected_text=selected_text, full_block_text="",
                                    selected_blocks=[], all_blocks=[])["v21_selected"]
            field_states[field] = state
            field_counts[field + ":" + state] += 1
        literal = norm(str(review["final"].get("value_text") or ""))
        compare_literal = norm(str(review["final"].get("comparison_value_text") or ""))
        grade = fact_grade(review, {field: {"v23_selected": state} for field, state in field_states.items()},
                           "v23_selected", footnote_unresolved=by_v21[candidate_id]["footnote_unresolved"],
                           literal_value_present=bool(literal) and literal in norm(selected_text),
                           literal_comparison_value_present=(not compare or bool(compare_literal)
                                                            and compare_literal in norm(selected_text)))
        structure = structural_status_for_candidate(candidate, review, doc, event)
        row = {"candidate_id": candidate_id, "symbol": review["symbol"],
               "filing_date": review["filing_date"], "topic": review["topic"],
               "source_status": review["status"], "source_document_id": did,
               "fragment_grade": {"old": legacy["old"], "v21_selected": legacy["v21_selected"],
                                  "v22_selected": legacy["v22_selected"], "v23_selected": grade},
               "v23_fields": field_states,
               "legacy_structure": {"old": legacy_structure_status(),
                                    "v21_selected": legacy_structure_status(),
                                    "v22_selected": legacy_structure_status()},
               "v23_structure": structure}
        rows.append(row)
        witness = private_witness_for_candidate(candidate, review, doc, event, structure)
        if witness is not None:
            witnesses.append(witness)
    supported = [r for r in rows if r["source_status"] == "CODEX_REVIEW_SOURCE_SUPPORTED"]
    grades = ("old", "v21_selected", "v22_selected", "v23_selected")
    counts_all = {v: dict(Counter(r["fragment_grade"][v] for r in rows)) for v in grades}
    counts_supported = {v: dict(Counter(r["fragment_grade"][v] for r in supported)) for v in grades}
    if [counts_all[v].get("COMPLETE", 0) for v in grades[:3]] != [34, 18, 8]:
        raise StructureContractError("frozen old/v2.1/v2.2 fragment scores changed")
    regressions = [r for r in supported if r["fragment_grade"]["old"] == "COMPLETE"
                   and r["fragment_grade"]["v23_selected"] != "COMPLETE"]
    now = datetime.now(timezone.utc).isoformat()
    private = {"schema": "financial_text_v23_evaluation_private_v1", "created_at_utc": now,
               "definition_version": DEFINITION_VERSION, "rows": rows,
               "source_review_note": "Frozen same-team CODEX_REVIEW, not independent gold"}
    witness_private = {"schema": "financial_text_v23_witness_private_v1", "created_at_utc": now,
                       "review_role": "CODEX_REVIEW_NOT_INDEPENDENT", "witnesses": witnesses}
    public = {"schema": "financial_text_v23_evaluation_redacted_v1", "created_at_utc": now,
              "definition_version": DEFINITION_VERSION,
              "denominators": {"all_candidates": 71, "same_team_source_supported": 60,
                               "source_ambiguous": 11, "documents": len(indexed_docs), "events": len(events),
                               "field_slots": sum(field_counts.values())},
              "fragment_retention_all_candidates": counts_all,
              "fragment_retention_source_supported": counts_supported,
              "field_retention": dict(field_counts),
              "fragment_state_source_supported": dict(Counter(r["v23_structure"]["fragment"] for r in supported)),
              "cell_match_source_supported": dict(Counter(r["v23_structure"]["cell_match"] for r in supported)),
              "reference_source_supported": dict(Counter(r["v23_structure"]["reference"] for r in supported)),
              "structure_source_supported": dict(Counter(r["v23_structure"]["structure"] for r in supported)),
              "known_fields_source_supported": dict(Counter(r["v23_structure"]["known_fields"] for r in supported)),
              "all_fields_source_supported": dict(Counter(r["v23_structure"]["all_fields"] for r in supported)),
              "omission_reasons": dict(omit), "old_complete_v23_fragment_incomplete_count": len(regressions),
              "old_complete_v23_fragment_incomplete": [
                  {"symbol": r["symbol"], "filing_date": r["filing_date"], "topic": r["topic"],
                   "v23_grade": r["fragment_grade"]["v23_selected"],
                   "v23_structure": r["v23_structure"]["structure"]} for r in regressions],
              "legacy_structure_status": legacy_structure_status(),
              "mechanical_complete_witnesses_pending_codex_review": len(witnesses),
              "limitations": ["Old fragment grades are copied unchanged; new v2.3 fragment grade uses the same frozen v2.1 functions on actual serialized text.",
                              "New structure metrics are not comparable to text-only legacy outputs, which lack exact cell lineage.",
                              "Unique cell and reachable relation checks are mechanical; source semantics require separate same-team CODEX_REVIEW.",
                              "Paragraph/div candidates without proved paragraph anchors remain UNEVALUABLE, not wrong table-column bindings.",
                              "No model inference, fitting, return evaluation or trade was performed."]}
    return private, witness_private, public


def run(run_id: str, parsed_path: Path, events_path: Path) -> dict:
    """Consume one saved v2.3 run and refuse to overwrite any older result."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
        raise ValueError("run_id must be a safe simple identifier")
    parsed_path, events_path = parsed_path.resolve(), events_path.resolve()
    if not parsed_path.is_relative_to(PRIVATE_DIR.resolve()) or not events_path.is_relative_to(PRIVATE_DIR.resolve()):
        raise ValueError("v2.3 private inputs must remain in the dedicated artifact directory")
    private_out = PRIVATE_DIR / f"evaluation_{run_id}.private.json"
    witness_out = PRIVATE_DIR / f"witnesses_{run_id}.private.json"
    public_out = PUBLIC_DIR / f"evaluation_{run_id}_redacted.json"
    for path in (private_out, witness_out, public_out):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite existing v2.3 evaluation: {path}")
    for path, expected in FROZEN_INPUT_SHA256.items():
        if sha(path.read_bytes()) != expected:
            raise StructureContractError(f"frozen reference changed: {path.relative_to(ROOT)}")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if not config.get("frozen_at_utc"):
        raise StructureContractError("v2.3 configuration is not frozen")
    documents = _read_jsonl(parsed_path)
    events = _read_jsonl(events_path)
    candidates = json.loads(CANDIDATES.read_text(encoding="utf-8"))["candidates"]
    reviews = json.loads(REVIEW.read_text(encoding="utf-8"))["reviews"]
    legacy_rows = json.loads(LEGACY.read_text(encoding="utf-8"))["fact_rows"]
    frozen_v21_rows = json.loads(FROZEN_V21.read_text(encoding="utf-8"))["fact_rows"]
    private, witnesses, public = evaluate_frozen_inputs(documents, events, candidates, reviews,
                                                        legacy_rows, frozen_v21_rows, config)
    input_paths = [CONFIG, Path(__file__), parsed_path, events_path, CANDIDATES, REVIEW, LEGACY, FROZEN_V21]
    hashes = {str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in input_paths}
    for output in (private, witnesses, public):
        output["run_id"] = run_id
        output["input_sha256"] = hashes
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    private_out.write_bytes(_canonical(private))
    witness_out.write_bytes(_canonical(witnesses))
    public["private_evaluation_sha256"] = sha(private_out.read_bytes())
    public["private_witnesses_sha256"] = sha(witness_out.read_bytes())
    public_out.write_bytes(_canonical(public))
    return {"public_path": str(public_out.relative_to(ROOT)), "public_sha256": sha(public_out.read_bytes()),
            "fragment_complete_v23": public["fragment_retention_all_candidates"]["v23_selected"].get("COMPLETE", 0),
            "mechanical_witnesses": len(witnesses["witnesses"])}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--parsed", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.run_id, args.parsed, args.events), sort_keys=True))


if __name__ == "__main__":
    main()
