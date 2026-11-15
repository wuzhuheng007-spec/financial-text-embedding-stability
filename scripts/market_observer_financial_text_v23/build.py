"""Build one frozen, training-only v2.3 evidence input per saved SEC event.

The production builder receives only source HTML, its saved metadata and the
frozen v2.3 configuration. Reviewed candidate answers enter the evaluator
later; they cannot affect parsing, group selection or serialization.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from bs4 import BeautifulSoup

from scripts.market_observer_financial_text_v21.build import select_training_documents
from .adapter import assemble_event
from .structure import _without_nested_table_text, parse_document


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs/market_observer_financial_text_v23_20261116.json"
OLD_CONFIG = ROOT / "configs/market_observer_financial_text_v21.json"
MANIFEST = ROOT / "artifacts/market_observer_official_20261115/corpus_manifest.json"
PRIVATE = ROOT / "artifacts/market_observer_financial_text_v23_20261116"
PUBLIC = ROOT / "results/market_observer_financial_text_v23_20261116"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def checked_preflight(run_id: str, config: dict) -> dict:
    path = PUBLIC / f"preflight_{run_id}_redacted.json"
    receipt = json.loads(path.read_text(encoding="utf-8"))
    if (receipt.get("schema") != "financial_text_v23_preflight_v1"
            or receipt.get("run_id") != run_id or receipt.get("source_document_count") != 16
            or receipt.get("event_groups") != 8 or config["frozen_at_utc"] is None):
        raise RuntimeError("missing or mismatched frozen v2.3 preflight")
    for relative, expected in receipt["code_config_and_reference_sha256"].items():
        file = ROOT / relative
        if not file.is_file() or sha(file.read_bytes()) != expected:
            raise RuntimeError("preflight input hash differs: " + relative)
    return receipt


def selected_sources(config: dict) -> list[dict]:
    legacy = json.loads(OLD_CONFIG.read_text(encoding="utf-8"))
    scope = config["source"]
    old_scope = legacy["source_scope"]
    for key in ("symbols", "announcement_date_start", "announcement_date_end"):
        if scope[key] != old_scope[key]:
            raise RuntimeError("v2.3 source scope differs from frozen training scope")
    docs = select_training_documents(json.loads(MANIFEST.read_text(encoding="utf-8")), legacy)
    if len(docs) != scope["expected_earnings_exhibits"] + scope["expected_matched_primary_filings"]:
        raise RuntimeError("unexpected training document count")
    if len({d["event_group_id"] for d in docs}) != scope["expected_events"]:
        raise RuntimeError("unexpected training event count")
    return docs


def validate_parsed_document(parsed: dict, doc: dict, raw: bytes) -> int:
    if (parsed["document_id"] != doc["document_id"]
            or parsed["event_group_id"] != doc["event_group_id"]
            or parsed["symbol"] != doc["symbol"]
            or parsed["announcement_date"] != doc["announcement_date"]
            or parsed["source_sha256"] != sha(raw)):
        raise RuntimeError("parsed document provenance differs from saved original")
    fragments = {f["fragment_id"]: f for f in parsed["source_fragments"]}
    cells = {c["cell_id"]: c for c in parsed["numeric_cells"]}
    relations = {r["relation_id"]: r for r in parsed["relations"]}
    groups = {g["group_id"]: g for g in parsed["evidence_groups"]}
    if (len(fragments) != len(parsed["source_fragments"])
            or len(cells) != len(parsed["numeric_cells"])
            or len(relations) != len(parsed["relations"])
            or len(groups) != len(parsed["evidence_groups"])):
        raise RuntimeError("duplicate parsed source ID")
    soup = BeautifulSoup(raw, "html.parser")
    checked = 0
    for fragment in fragments.values():
        node = soup.select_one(fragment["dom_path"])
        if (fragment["document_id"] != doc["document_id"] or node is None
                or fragment["text_sha256"] != sha(fragment["text"].encode("utf-8"))
                or fragment["text"] != _without_nested_table_text(node)):
            raise RuntimeError("parsed context fragment differs from source DOM")
        checked += 1
    for cell in cells.values():
        node = soup.select_one(cell["cell_dom_path"])
        if (cell["document_id"] != doc["document_id"] or node is None
                or cell["text_sha256"] != sha(cell["text"].encode("utf-8"))):
            raise RuntimeError("parsed numeric cell differs from source DOM")
        dom_text = _without_nested_table_text(node)
        if cell.get("anchor_kind") == "PARAGRAPH":
            start, end = cell.get("text_start"), cell.get("text_end")
            if (not isinstance(start, int) or not isinstance(end, int)
                    or not 0 <= start < end <= len(dom_text)
                    or dom_text[start:end] != cell["text"]
                    or cell["measurement_text"] != cell["text"]
                    or any(cell.get(key) is not None for key in
                           ("row_index", "column_index", "row_start", "row_end",
                            "column_start", "column_end"))):
                raise RuntimeError("paragraph numeric token differs from source DOM")
        elif cell.get("anchor_kind") == "TABLE_CELL":
            if cell["text"] != dom_text:
                raise RuntimeError("table numeric cell differs from source DOM")
        else:
            raise RuntimeError("unknown numeric anchor kind")
        checked += 1
    for relation in relations.values():
        if (relation["from_id"] not in cells
                or any(target not in fragments and target not in cells for target in relation["to_ids"])
                or relation["source_dom_paths"] != [
                    fragments[target]["dom_path"] if target in fragments else cells[target]["cell_dom_path"]
                    for target in relation["to_ids"]]
                or relation["status"] not in {"EXPLICIT", "UNKNOWN", "CONFLICT", "NOT_APPLICABLE"}):
            raise RuntimeError("parsed relation has invalid source or status")
    for group in groups.values():
        if (group["anchor_cell_id"] not in cells
                or any(rid not in relations or relations[rid]["from_id"] != group["anchor_cell_id"]
                       for rid in group["relation_ids"])):
            raise RuntimeError("parsed group has invalid cell or relation")
    return checked


def validate_event(event: dict, parsed_docs: list[dict], budget: int) -> None:
    text = event["selected_text"]
    if (not isinstance(text, str) or len(text) > budget
            or event["selected_text_characters"] != len(text)
            or event["selected_text_sha256"] != sha(text.encode("utf-8"))
            or event["max_characters"] != budget):
        raise RuntimeError("actual serialized input exceeds budget or differs from receipt")
    cells = {c["cell_id"]: c for doc in parsed_docs for c in doc["numeric_cells"]}
    fragments = {f["fragment_id"]: f for doc in parsed_docs for f in doc["source_fragments"]}
    parsed_groups = {g["group_id"]: g for doc in parsed_docs for g in doc["evidence_groups"]}
    spans = event["model_visible_spans"]
    spans_by_id = {}
    intervals = []
    for span in spans:
        start, end = span["start"], span["end"]
        if (not isinstance(start, int) or not isinstance(end, int)
                or not 0 <= start < end <= len(text) or span["id"] in spans_by_id
                or span["id"] != span["source_id"]
                or span["kind"] not in {"CELL", "PARA", "CTX"}):
            raise RuntimeError("invalid or duplicate model-visible source span")
        spans_by_id[span["id"]] = span
        intervals.append((start, end))
        source = cells.get(span["source_id"]) if span["kind"] in {"CELL", "PARA"} else fragments.get(span["source_id"])
        line = text[start:end]
        source_dom = source.get("cell_dom_path") if source is not None and span["kind"] in {"CELL", "PARA"} else (
            source.get("dom_path") if source is not None else None)
        raw_start, raw_end = span.get("raw_start"), span.get("raw_end")
        if (source is None or source["document_id"] != span["document_id"]
                or source["source_sha256"] != span["source_sha256"]
                or source_dom != span["dom_path"]
                or not line.startswith(f"[{span['kind']} id={span['id']} ")
                or not isinstance(raw_start, int) or not isinstance(raw_end, int)
                or not start <= raw_start < raw_end <= end
                or text[raw_start:raw_end] != source["text"]):
            raise RuntimeError("model-visible span has absent or cross-document source")
    if any(a[1] > b[0] for a, b in zip(sorted(intervals), sorted(intervals)[1:])):
        raise RuntimeError("model-visible spans overlap")
    inventory = event["group_inventory"]
    inventory_by_id = {g["group_id"]: g for g in inventory}
    selected = {gid: g for gid, g in inventory_by_id.items() if g["status"] == "SELECTED"}
    if (len(inventory_by_id) != len(inventory) or set(inventory_by_id) != set(parsed_groups)
            or set(selected) != set(event["selected_group_ids"])
            or len(event["selected_group_ids"]) != len(selected)
            or {g["group_id"] for g in event["selected_groups"]} != set(selected)):
        raise RuntimeError("selected group IDs differ from inventory")
    omitted = {gid: g["omission_reason"] for gid, g in inventory_by_id.items()
               if g["status"] == "OMITTED"}
    if (any(not reason for reason in omitted.values())
            or omitted != {g["group_id"]: g["reason"] for g in event["omitted_groups"]}):
        raise RuntimeError("omitted group reasons differ from the complete inventory")
    visible_contexts = {sid for sid, span in spans_by_id.items() if span["kind"] == "CTX"}
    if visible_contexts != set(event["selected_context_ids"]):
        raise RuntimeError("context sidecar differs from model-visible spans")
    if "visible_context_ids" in event and visible_contexts != set(event["visible_context_ids"]):
        raise RuntimeError("visible context alias differs from model-visible spans")
    for gid, group in selected.items():
        parsed = parsed_groups[gid]
        if (group["document_id"] != parsed["document_id"]
                or group["anchor_cell_id"] != parsed["anchor_cell_id"]
                or group["relation_ids"] != parsed["relation_ids"]
                or group["relation_refs"] != parsed["relation_refs"]
                or group["relation_status"] != parsed["relation_status"]):
            raise RuntimeError("selected group differs from parsed source relations")
        event_group = next(g for g in event["selected_groups"] if g["group_id"] == gid)
        if (event_group["document_id"] != group["document_id"]
                or event_group["anchor_cell_id"] != group["anchor_cell_id"]
                or event_group["relation_refs"] != group["relation_refs"]
                or event_group["context_ids"] != group["context_ids"]):
            raise RuntimeError("selected group sidecars disagree")
        anchor = spans_by_id.get(group["anchor_cell_id"])
        if (anchor is None or anchor["kind"] not in {"CELL", "PARA"}
                or anchor["document_id"] != group["document_id"]):
            raise RuntimeError("selected numeric cell is not model-visible in the same document")
        expected_contexts = list(dict.fromkeys(
            fid for key, ids in group["relation_refs"].items() if key != "comparison_value" for fid in ids))
        if expected_contexts != group["context_ids"]:
            raise RuntimeError("selected context IDs differ from parsed relation targets")
        expected_refs = ";".join(f"{key}:{','.join(ids) or 'none'}"
                                 for key, ids in sorted(group["relation_refs"].items()))
        anchor_line = text[anchor["start"]:anchor["end"]]
        if f"role=ANCHOR refs={expected_refs}]" not in anchor_line.split("] ", 1)[0] + "]":
            raise RuntimeError("model-visible anchor references differ from parsed source")
        for fid in expected_contexts:
            context = spans_by_id.get(fid)
            if (context is None or context["kind"] != "CTX"
                    or context["document_id"] != group["document_id"]
                    or context["end"] > anchor["start"]):
                raise RuntimeError("context reference is absent, cross-document or after the numeric cell")
        for cid in group["relation_refs"].get("comparison_value", []):
            companion = spans_by_id.get(cid)
            if (companion is None or companion["kind"] != "CELL"
                    or companion["document_id"] != group["document_id"]
                    or companion["end"] > anchor["start"]):
                raise RuntimeError("comparison cell reference is absent or out of scope")


def run(run_id: str) -> dict:
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,31}", run_id):
        raise ValueError("Use a short lowercase run_id")
    output_paths = [PRIVATE / f"parsed_{run_id}.private.jsonl",
                    PRIVATE / f"event_inputs_{run_id}.private.jsonl",
                    PUBLIC / f"build_{run_id}_redacted.json"]
    if any(p.exists() for p in output_paths):
        raise FileExistsError("Refusing to overwrite an existing v2.3 build result")
    config_bytes = CONFIG.read_bytes()
    config = json.loads(config_bytes)
    if (config["schema"] != "market_observer_financial_text_v23_plan_v1"
            or config["source"]["new_downloads"] != 0
            or config["adapter"]["max_characters_per_event"] != 20_000):
        raise RuntimeError("v2.3 scope or budget differs from frozen plan")
    preflight = checked_preflight(run_id, config)
    docs = selected_sources(config)
    preflight_docs = {d["document_id"]: d for d in preflight["source_documents"]}
    if {d["document_id"] for d in docs} != set(preflight_docs):
        raise RuntimeError("build source set differs from frozen preflight")
    by_event: dict[str, list[dict]] = defaultdict(list)
    parsed_rows = []
    dom_checks = 0
    for doc in docs:
        raw = Path(doc["raw_body_path"]).read_bytes()
        if sha(raw) != preflight_docs[doc["document_id"]]["raw_body_sha256"]:
            raise RuntimeError("saved raw source changed after preflight")
        parsed = parse_document(raw, doc)
        dom_checks += validate_parsed_document(parsed, doc, raw)
        parsed_rows.append(parsed)
        by_event[doc["event_group_id"]].append(parsed)
    events = []
    for event_id in sorted(by_event, key=lambda eid: (
            by_event[eid][0]["announcement_date"], by_event[eid][0]["symbol"], eid)):
        parsed = by_event[event_id]
        event = assemble_event(parsed, config["adapter"]["max_characters_per_event"])
        if event["event_group_id"] != event_id or event["symbol"] != parsed[0]["symbol"]:
            raise RuntimeError("assembled event identity differs from source")
        validate_event(event, parsed, config["adapter"]["max_characters_per_event"])
        events.append(event)
    parsed_bytes = b"".join(canonical(row) for row in parsed_rows)
    event_bytes = b"".join(canonical(row) for row in events)
    omitted = Counter(g["omission_reason"] for e in events for g in e["group_inventory"]
                      if g["status"] != "SELECTED")
    summary = {
        "schema": "financial_text_v23_build_redacted_v1", "run_id": run_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_scope": "SAVED_NVDA_MU_2025-04-01_TO_2026-03-31_EARNINGS_ONLY",
        "config_sha256": sha(config_bytes),
        "preflight_redacted_sha256": sha((PUBLIC / f"preflight_{run_id}_redacted.json").read_bytes()),
        "source_manifest_sha256": sha(MANIFEST.read_bytes()),
        "source_document_count": len(docs), "event_count": len(events),
        "parsed_table_count": sum(len(p["tables"]) for p in parsed_rows),
        "parsed_numeric_cell_count": sum(len(p["numeric_cells"]) for p in parsed_rows),
        "parsed_relation_count": sum(len(p["relations"]) for p in parsed_rows),
        "parsed_group_count": sum(len(p["evidence_groups"]) for p in parsed_rows),
        "parsed_group_eligibility_counts": dict(Counter(g["eligibility"] for p in parsed_rows
                                                        for g in p["evidence_groups"])),
        "parsed_relation_status_counts": dict(Counter(r["status"] for p in parsed_rows
                                                      for r in p["relations"])),
        "source_dom_fragment_or_cell_checks": dom_checks,
        "selected_group_count": sum(len(e["selected_group_ids"]) for e in events),
        "omitted_group_count": sum(1 for e in events for g in e["group_inventory"] if g["status"] != "SELECTED"),
        "omission_reasons": dict(omitted),
        "events": [{"event_group_id": e["event_group_id"], "symbol": e["symbol"],
                    "selected_text_characters": e["selected_text_characters"],
                    "selected_text_sha256": e["selected_text_sha256"],
                    "selected_groups": len(e["selected_group_ids"]),
                    "omitted_groups": sum(g["status"] != "SELECTED" for g in e["group_inventory"])} for e in events],
        "private_parsed_sha256": sha(parsed_bytes), "private_event_inputs_sha256": sha(event_bytes),
        "new_downloads": 0, "april_july_bodies_or_labels_read": False,
        "model_fit_calls": 0, "model_inference_calls": 0,
        "source_contents_published": False,
        "note": "Parsed relations and selected inputs are unconfirmed engineering candidates, not verified financial facts.",
    }
    for path in output_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
    for path, contents in zip(output_paths, (parsed_bytes, event_bytes, canonical(summary))):
        with path.open("xb") as out:
            out.write(contents)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    summary = run(args.run_id)
    if args.print_summary:
        print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
