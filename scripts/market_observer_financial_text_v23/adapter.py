"""Serialize exact source cells and reachable structural context within 20k.

Only parsed source structure and frozen topic order affect selection. The
reviewed 71-candidate ledger is deliberately absent from this module.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict

from .structure import TOPIC_ORDER


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _context_line(f: dict) -> str:
    scope = f["subtable_id"] or f["section_id"] or "none"
    rc = (f"r={f['row_start']}:{f['row_end']} c={f['column_start']}:{f['column_end']}"
          if f["row_start"] is not None else "r=na c=na")
    return (f"[CTX id={f['fragment_id']} doc={f['document_id']} scope={scope} "
            f"kind={f['kind']} {rc}] {f['text']}")


def _cell_line(cell: dict, group: dict | None = None, *, companion: bool = False) -> str:
    if companion:
        refs = "none"
        role = "COMPARISON"
    else:
        assert group is not None
        refs = ";".join(f"{key}:{','.join(ids) or 'none'}"
                        for key, ids in sorted(group["relation_refs"].items()))
        role = "ANCHOR"
    label = "PARA" if cell.get("anchor_kind") == "PARAGRAPH" else "CELL"
    row = cell["row_index"] if cell["row_index"] is not None else "na"
    col = cell["column_index"] if cell["column_index"] is not None else "na"
    return (f"[{label} id={cell['cell_id']} doc={cell['document_id']} "
            f"table={cell['table_id'] or 'paragraph'} region={cell['subtable_id']} "
            f"r={row} c={col} role={role} refs={refs}] "
            + cell["text"])


def _validate_relation_target(cell: dict, relation: dict, fragment: dict) -> None:
    if fragment["document_id"] != cell["document_id"] or fragment["source_sha256"] != cell["source_sha256"]:
        raise ValueError("cross-document relation")
    if relation["kind"] == "FOOTNOTE" and relation["method"] == "HTML_HREF_TARGET":
        return  # explicit same-document HTML target may be outside the table
    if fragment["table_id"] is not None and fragment["table_id"] != cell["table_id"]:
        raise ValueError("cross-table context relation")
    if fragment["subtable_id"] is not None and fragment["subtable_id"] != cell["subtable_id"]:
        raise ValueError("cross-subtable context relation")
    if fragment["table_id"] is None and fragment["section_id"] != cell["section_id"]:
        raise ValueError("cross-section context relation")


def _priority(groups: list[dict]) -> list[dict]:
    ordered = sorted(groups, key=lambda g: (0 if g["source_kind"] == "ISSUER_EARNINGS" else 1,
                                            g["document_id"], g["source_order"], g["group_id"]))
    first: list[str] = []
    for topic in TOPIC_ORDER:
        match = next((g for g in ordered if topic in g["topics"] and g["group_id"] not in first), None)
        if match:
            first.append(match["group_id"])
    by_id = {g["group_id"]: g for g in ordered}
    return [by_id[gid] for gid in first] + [g for g in ordered if g["group_id"] not in first]


def assemble_event(parsed_docs: list[dict], max_characters: int) -> dict:
    """Return actual model input and complete selected/omitted group receipts."""
    if not isinstance(max_characters, int) or max_characters < 1:
        raise ValueError("max_characters must be a positive integer")
    if not parsed_docs:
        raise ValueError("parsed_docs is empty")
    if len({d["document_id"] for d in parsed_docs}) != len(parsed_docs):
        raise ValueError("duplicate document ID")
    if len({d["event_group_id"] for d in parsed_docs}) != 1 or len({d["symbol"] for d in parsed_docs}) != 1:
        raise ValueError("cross-event or cross-company parsed documents")
    by_fragment = {f["fragment_id"]: f for d in parsed_docs for f in d["source_fragments"]}
    by_cell = {c["cell_id"]: c for d in parsed_docs for c in d["numeric_cells"]}
    by_relation = {r["relation_id"]: r for d in parsed_docs for r in d["relations"]}
    groups = [g.copy() for d in parsed_docs for g in d["evidence_groups"]]
    if (len(by_fragment) != sum(len(d["source_fragments"]) for d in parsed_docs)
            or len(by_cell) != sum(len(d["numeric_cells"]) for d in parsed_docs)
            or len(by_relation) != sum(len(d["relations"]) for d in parsed_docs)):
        raise ValueError("duplicate source object ID")
    if len({g["group_id"] for g in groups}) != len(groups):
        raise ValueError("duplicate evidence group ID")
    for d in parsed_docs:
        for i, g in enumerate(d["evidence_groups"]):
            target = next(x for x in groups if x["group_id"] == g["group_id"])
            target["source_order"] = i
    for group in groups:
        cell = by_cell.get(group["anchor_cell_id"])
        if cell is None or cell["document_id"] != group["document_id"]:
            raise ValueError("group anchor missing or cross-document")
        if len(set(group["relation_ids"])) != len(group["relation_ids"]):
            raise ValueError("duplicate group relation")
        for rid in group["relation_ids"]:
            rel = by_relation.get(rid)
            if rel is None or rel["from_id"] != cell["cell_id"]:
                raise ValueError("group relation missing or wrong source cell")
            if rel["to_ids"] != group["relation_refs"][rel["kind"].lower()]:
                raise ValueError("group relation refs differ from parsed relation")
            if rel["status"] != group["relation_status"][rel["kind"].lower()]:
                raise ValueError("group relation status differs")
            for target_id in rel["to_ids"]:
                if rel["kind"] == "COMPARISON_VALUE":
                    other = by_cell.get(target_id)
                    if (other is None or other["document_id"] != cell["document_id"]
                            or other["table_id"] != cell["table_id"]
                            or other["subtable_id"] != cell["subtable_id"]
                            or other["row_index"] != cell["row_index"]):
                        raise ValueError("comparison cell crosses row or scope")
                    continue
                fragment = by_fragment.get(target_id)
                if fragment is None:
                    raise ValueError("referenced context does not exist")
                _validate_relation_target(cell, rel, fragment)
    eligible = [g for g in groups if g["topics"] and g["eligibility"] in
                ("ELIGIBLE_KNOWN_FIELDS", "ELIGIBLE_FULL_FIELDS")]
    omitted = {g["group_id"]: (g["eligibility"] if g["topics"] else "NOT_TOPIC_NUMERIC")
               for g in groups if g not in eligible}
    text_lines: list[str] = []
    line_objects: list[tuple[str, str, str, str, str | None]] = []  # kind, source ID, line, role, group
    visible_contexts: set[str] = set()
    visible_cells: set[str] = set()
    selected_ids: list[str] = []
    selected_groups: list[dict] = []
    for group in _priority(eligible):
        cell = by_cell[group["anchor_cell_id"]]
        if cell["cell_id"] in visible_cells:
            omitted[group["group_id"]] = "ANCHOR_ALREADY_VISIBLE_AS_COMPARISON"
            continue
        context_ids = list(dict.fromkeys(
            fid for key, ids in group["relation_refs"].items()
            if key != "comparison_value" for fid in ids))
        companion_ids = group["relation_refs"].get("comparison_value", [])
        new_contexts = [fid for fid in context_ids if fid not in visible_contexts]
        new_companions = [cid for cid in companion_ids if cid not in visible_cells]
        candidate: list[tuple[str, str, str, str, str | None]] = []
        for fid in new_contexts:
            candidate.append(("CTX", fid, _context_line(by_fragment[fid]), "CONTEXT", None))
        for cid in new_companions:
            candidate.append(("CELL", cid, _cell_line(by_cell[cid], companion=True), "COMPANION", group["group_id"]))
        anchor_kind = "PARA" if cell.get("anchor_kind") == "PARAGRAPH" else "CELL"
        candidate.append((anchor_kind, cell["cell_id"], _cell_line(cell, group), "ANCHOR", group["group_id"]))
        increment = sum(len(line) for _, _, line, _, _ in candidate) + len(candidate)
        if text_lines and len("\n".join(text_lines)) + increment > max_characters:
            omitted[group["group_id"]] = "WHOLE_GROUP_OVER_BUDGET"
            continue
        if not text_lines and increment - 1 > max_characters:
            omitted[group["group_id"]] = "WHOLE_GROUP_OVER_BUDGET"
            continue
        for kind, sid, line, role, gid in candidate:
            text_lines.append(line)
            line_objects.append((kind, sid, line, role, gid))
        visible_contexts.update(new_contexts)
        visible_cells.update(new_companions)
        visible_cells.add(cell["cell_id"])
        selected_ids.append(group["group_id"])
        selected_groups.append({**group, "status": "SELECTED", "omission_reason": None,
                                "context_ids": context_ids})
    selected_text = "\n".join(text_lines)
    if len(selected_text) > max_characters:
        raise AssertionError("actual serialized input exceeded fixed budget")
    spans = []
    pos = 0
    for kind, sid, line, role, gid in line_objects:
        obj = by_fragment[sid] if kind == "CTX" else by_cell[sid]
        raw = obj["text"]
        raw_pos = line.index("] ") + 2
        if line[raw_pos:] != raw:
            raise AssertionError("source fragment was changed in model text")
        span = {
            "id": sid,
            "kind": kind, "role": role, "group_id": gid, "source_id": sid,
            "document_id": obj["document_id"], "source_sha256": obj["source_sha256"],
            "dom_path": obj["dom_path"] if kind == "CTX" else obj["cell_dom_path"],
            "start": pos, "end": pos + len(line),
            "raw_start": pos + raw_pos, "raw_end": pos + len(line),
        }
        if selected_text[span["start"]:span["end"]] != line or selected_text[span["raw_start"]:span["raw_end"]] != raw:
            raise AssertionError("source span not intact in actual model input")
        spans.append(span)
        pos += len(line) + 1
    context_by_source = {s["source_id"]: s for s in spans if s["kind"] == "CTX"}
    for group in selected_groups:
        anchor = next((s for s in spans if s["source_id"] == group["anchor_cell_id"]
                       and s["role"] == "ANCHOR" and s["group_id"] == group["group_id"]), None)
        if anchor is None:
            raise AssertionError("selected anchor line missing")
        group["anchor_visible_id"] = anchor["id"]
        line = selected_text[anchor["start"]:anchor["end"]]
        for fid in group["context_ids"]:
            ref = context_by_source.get(fid)
            if ref is None or ref["kind"] != "CTX" or ref["start"] >= anchor["start"] or fid not in line:
                raise AssertionError("model-visible context reference cannot be reached")
        for cid in group["relation_refs"].get("comparison_value", []):
            ref = next((s for s in spans if s["source_id"] == cid and s["kind"] == "CELL"
                        and s["start"] < anchor["start"]), None)
            if ref is None or cid not in line:
                raise AssertionError("model-visible comparison cell cannot be reached")
    inventory = [{"group_id": g["group_id"], "document_id": g["document_id"],
                  "anchor_cell_id": g["anchor_cell_id"], "topics": g["topics"],
                  "eligibility": g["eligibility"], "relation_ids": g["relation_ids"],
                  "relation_refs": g["relation_refs"], "relation_status": g["relation_status"],
                  "context_ids": next((x["context_ids"] for x in selected_groups
                                       if x["group_id"] == g["group_id"]), []),
                  "anchor_visible_id": next((x["anchor_visible_id"] for x in selected_groups
                                             if x["group_id"] == g["group_id"]), None),
                  "status": "SELECTED" if g["group_id"] in selected_ids else "OMITTED",
                  "omission_reason": omitted.get(g["group_id"])} for g in groups]
    return {
        "schema": "financial_text_v23_model_input_v1",
        "event_group_id": parsed_docs[0]["event_group_id"], "symbol": parsed_docs[0]["symbol"],
        "selected_text": selected_text, "selected_text_sha256": _sha(selected_text.encode("utf-8")),
        "selected_text_characters": len(selected_text), "max_characters": max_characters,
        "selected_group_ids": selected_ids, "selected_groups": selected_groups,
        "selected_context_ids": [s["source_id"] for s in spans if s["kind"] == "CTX"],
        "visible_context_ids": [s["source_id"] for s in spans if s["kind"] == "CTX"],
        "model_visible_spans": spans, "group_inventory": inventory,
        "omitted_groups": [{"group_id": gid, "reason": reason} for gid, reason in omitted.items()],
        "selection_policy": "SOURCE_STRUCTURE_AND_FIXED_TOPIC_PRIORITY_NO_REVIEWED_ANSWERS",
        "character_count_policy": "PYTHON_LEN_FINAL_UNICODE_TEXT_ALL_IDS_REFS_NEWLINES",
        "model_fit_calls": 0, "model_inference_calls": 0,
    }
