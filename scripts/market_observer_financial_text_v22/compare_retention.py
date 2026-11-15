"""One training-only comparison of frozen old/v2.1 and model-visible v2.2 text.

The production adapter never imports this module. Candidate/review material is
read here only after the adapter configuration and output have been frozen.
This is a mechanical content audit, not independent fact or return validation.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from scripts.market_observer_financial_text_v21.compare_retention import (
    COMPARISON_FIELDS,
    COMPARISON_SUPPORTED,
    FIELDS,
    fact_grade,
    field_retention,
    norm,
    source_element_present,
)


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs/market_observer_financial_text_v22_20261116.json"
BLOCKS = ROOT / "artifacts/market_observer_financial_text_v21_20261115/blocks.private.jsonl"
EVENTS = ROOT / "artifacts/market_observer_financial_text_v22_20261116/event_inputs.private.jsonl"
CANDIDATES = ROOT / "artifacts/market_observer_financial_text_v2_20261115/fact_cards/fact_candidates_v2.private.json"
REVIEW = ROOT / "artifacts/market_observer_financial_text_v2_20261115/fact_cards/fact_review_v4.private.json"
FROZEN = ROOT / "artifacts/market_observer_financial_text_v21_20261115/retention_comparison.private.json"
MANIFEST = ROOT / "artifacts/market_observer_official_20261115/corpus_manifest.json"
PRIVATE_OUT = ROOT / "artifacts/market_observer_financial_text_v22_20261116/retention_comparison.private.json"
PUBLIC_OUT = ROOT / "results/market_observer_financial_text_v22_20261116/retention_redacted.json"
LOSS_PRIVATE_OUT = ROOT / "artifacts/market_observer_financial_text_v22_20261116/loss_stage.private.json"
LOSS_PUBLIC_OUT = ROOT / "results/market_observer_financial_text_v22_20261116/loss_stage_redacted.json"

FROZEN_SHA256 = {
    CANDIDATES: "4b349b448ac7238d7182e49ec4a3d3906ed10eddad8f49b9a1df12cd277c6f5f",
    REVIEW: "523fd9e58256fdf179d55e11faa2d4b0ae8193cc873585d7b7263c8bbd1f92b9",
    FROZEN: "dcbd2581f78c8f1a5278912092ad7c380cb4ef1ed8946b0e83f3a15553537494",
    BLOCKS: "37f7088f21a68cf6d922218822427a0910945b0d0223880d70712fb4ec8142b5",
    MANIFEST: "7b81012cdd41a4e9a5ea9c7c97eb521f03ffd4365127ad681ee113924c6dbbad",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def canonical(data: object) -> bytes:
    return (json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _ref_visible(line: str, context_id: str) -> bool:
    return re.search(r"(?<![A-Za-z0-9_])" + re.escape(context_id) + r"(?![A-Za-z0-9_])", line) is not None


def validate_event(event: dict, by_block: dict[str, dict], allowed_docs: set[str], budget: int) -> dict:
    """Read spans from the *actual serialized text* and verify visible references."""
    text = event["selected_text"]
    if not isinstance(text, str) or len(text) > budget or len(text) != event["selected_text_characters"]:
        raise ValueError("selected text character count or budget mismatch")
    if sha(text.encode("utf-8")) != event["selected_text_sha256"]:
        raise ValueError("selected text hash mismatch")
    if event.get("max_characters") != budget:
        raise ValueError("event budget differs from frozen configuration")
    spans = event["model_visible_spans"]
    spans_by_doc: dict[str, list[tuple[int, str]]] = defaultdict(list)
    context_spans: dict[str, tuple[str, str]] = {}
    anchor_spans: dict[str, tuple[str, str, str]] = {}
    span_intervals: list[tuple[int, int]] = []
    context_offsets: dict[str, tuple[int, int]] = {}
    anchor_offsets: dict[str, tuple[int, int]] = {}
    selected_blocks: set[str] = set()
    for span in spans:
        start, end = span["start"], span["end"]
        if not (isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(text)):
            raise ValueError("model-visible span outside selected text")
        span_intervals.append((start, end))
        block_id = span["source_block_id"]
        block = by_block.get(block_id)
        did = span["document_id"]
        if (block is None or did not in allowed_docs or block["document_id"] != did
                or block["event_group_id"] != event["event_group_id"]
                or block["symbol"] != event["symbol"]):
            raise ValueError("span source document or event mismatch")
        fragment = text[start:end]
        raw_start, raw_end = span.get("raw_start"), span.get("raw_end")
        if (not isinstance(raw_start, int) or not isinstance(raw_end, int)
                or not start <= raw_start < raw_end <= end
                or text[raw_start:raw_end] != block["raw_text"]):
            raise ValueError("source raw offsets do not match model-visible source text")
        spans_by_doc[did].append((start, fragment))
        selected_blocks.add(block_id)
        context_id = span.get("context_id")
        group_id = span.get("group_id")
        if bool(context_id) == bool(group_id):
            raise ValueError("span needs exactly one context_id or group_id")
        if context_id:
            if context_id in context_spans:
                raise ValueError("duplicate context span")
            if not _ref_visible(fragment, context_id):
                raise ValueError("context ID absent from model-visible context line")
            context_spans[context_id] = (did, fragment)
            context_offsets[context_id] = (start, end)
        else:
            if group_id in anchor_spans:
                raise ValueError("duplicate anchor span")
            if not _ref_visible(fragment, group_id):
                raise ValueError("group ID absent from model-visible group line")
            anchor_spans[group_id] = (did, block_id, fragment)
            anchor_offsets[group_id] = (start, end)
    for (_, previous_end), (next_start, _) in zip(sorted(span_intervals), sorted(span_intervals)[1:]):
        if next_start < previous_end:
            raise ValueError("model-visible spans overlap")
    if selected_blocks != set(event["selected_block_ids"]):
        raise ValueError("selected block IDs do not match visible source spans")
    inventory = event["group_inventory"]
    if len({g["group_id"] for g in inventory}) != len(inventory):
        raise ValueError("duplicate group inventory ID")
    selected_groups = {g["group_id"]: g for g in inventory if g["status"].upper() == "SELECTED"}
    if set(selected_groups) != set(event["selected_group_ids"]) or set(anchor_spans) != set(selected_groups):
        raise ValueError("selected groups and visible anchors disagree")
    for gid, group in selected_groups.items():
        did, block_id, anchor = anchor_spans[gid]
        if did != group["document_id"] or block_id != group["anchor_block_id"]:
            raise ValueError("group anchor source mismatch")
        refs = group.get("context_ids", [])
        if len(refs) != len(set(refs)):
            raise ValueError("duplicate context reference")
        for cid in refs:
            if cid not in context_spans or context_spans[cid][0] != did:
                raise ValueError("cross-document or absent context reference")
            if context_offsets[cid][1] > anchor_offsets[gid][0]:
                raise ValueError("referenced context appears after its group")
            if not _ref_visible(anchor, cid):
                raise ValueError("sidecar context reference absent from model-visible group line")
    if set(context_spans) != set(event.get("selected_context_ids", context_spans)):
        raise ValueError("selected context IDs and visible spans disagree")
    by_doc_text = {did: "\n".join(fragment for _, fragment in sorted(parts))
                   for did, parts in spans_by_doc.items()}
    return {
        "doc_text": by_doc_text,
        "contexts": context_spans,
        "anchors": anchor_spans,
        "selected_groups": selected_groups,
        "selected_block_ids": selected_blocks,
    }


def binding_for_review(review: dict, document_id: str, validated: dict) -> str:
    """Mechanically require one visible group to contain all reviewed fragments."""
    if review["status"] != "CODEX_REVIEW_SOURCE_SUPPORTED":
        return "SOURCE_REVIEW_AMBIGUOUS"
    fields = list(FIELDS)
    if review["final"].get("comparison_status") == COMPARISON_SUPPORTED:
        fields += list(COMPARISON_FIELDS)
    # A source-unknown basis remains unknown even if model text appears to imply one.
    if review["final"].get("basis") is None:
        fields.remove("basis")
    if "comparison_basis" in fields and review["final"].get("comparison_basis") is None:
        fields.remove("comparison_basis")
    for gid, group in validated["selected_groups"].items():
        if group["document_id"] != document_id:
            continue
        anchor = validated["anchors"][gid][2]
        contexts = [validated["contexts"][cid][1] for cid in group.get("context_ids", [])]
        visible = "\n".join([anchor, *contexts])
        if not all(all(source_element_present(e, visible) for e in review["field_evidence"].get(field, []))
                   and bool(review["field_evidence"].get(field)) for field in fields):
            continue
        value = norm(str(review["final"].get("value_text") or ""))
        if not value or value not in norm(visible):
            continue
        if review["final"].get("comparison_status") == COMPARISON_SUPPORTED:
            compare_value = norm(str(review["final"].get("comparison_value_text") or ""))
            if not compare_value or compare_value not in norm(visible):
                continue
        return "BOUND_GROUP_KNOWN_FIELDS_BASIS_UNKNOWN" if review["final"].get("basis") is None else "BOUND_GROUP_COMPLETE"
    return "NO_SINGLE_BOUND_GROUP"


def classify_prior_loss(row: dict) -> str:
    """Classify the frozen v2.1 stage without changing source review status."""
    if row["review_status"] != "CODEX_REVIEW_SOURCE_SUPPORTED":
        return "SOURCE_REVIEW_AMBIGUOUS"
    if row["footnote_unresolved"]:
        return "RELATION_OR_FOOTNOTE_UNRESOLVED"
    full = row["fact_retention"]["v21_full_blocks"]
    selected = row["fact_retention"]["v21_selected"]
    if full in {"PARTIAL", "LOST"}:
        return "PARSER_OR_SOURCE_CONTEXT_MISSING"
    if full == "AMBIGUOUS":
        return "RELATION_OR_SOURCE_CONTEXT_UNRESOLVED"
    if full in {"COMPLETE", "COMPLETE_KNOWN_FIELDS_BASIS_UNKNOWN"} and selected not in {
        "COMPLETE", "COMPLETE_KNOWN_FIELDS_BASIS_UNKNOWN"
    }:
        return "COMPLETE_IN_FULL_STORE_OMITTED_BY_BUDGET"
    if full == "COMPLETE_KNOWN_FIELDS_BASIS_UNKNOWN":
        return "KNOWN_FIELDS_PRESENT_SOURCE_BASIS_UNKNOWN"
    return "STRICT_COMPLETE_IN_SELECTED_INPUT"


def event_dates_from_blocks(blocks: list[dict]) -> dict[str, str]:
    """Use saved source metadata for dates; the adapter need not repeat it."""
    dates: dict[str, set[str]] = defaultdict(set)
    for block in blocks:
        dates[block["event_group_id"]].add(block["announcement_date"])
    if any(len(values) != 1 for values in dates.values()):
        raise ValueError("conflicting announcement dates within an event")
    return {event_id: next(iter(values)) for event_id, values in dates.items()}


def evaluate(events: list[dict], blocks: list[dict], candidates: dict, review: dict,
             frozen: dict, config: dict) -> tuple[dict, dict, dict, dict]:
    candidate_by_id = {c["candidate_id"]: c for c in candidates["candidates"]}
    review_by_id = {r["candidate_id"]: r for r in review["reviews"]}
    frozen_by_id = {r["candidate_id"]: r for r in frozen["fact_rows"]}
    if not (len(candidate_by_id) == len(review_by_id) == len(frozen_by_id) == 71
            and set(candidate_by_id) == set(review_by_id) == set(frozen_by_id)):
        raise ValueError("frozen 71-candidate identity changed")
    if Counter(r["status"] for r in review["reviews"]) != Counter({
        "CODEX_REVIEW_SOURCE_SUPPORTED": 60, "CODEX_REVIEW_AMBIGUOUS": 11
    }):
        raise ValueError("frozen 60/11 source review denominator changed")
    by_block = {b["block_id"]: b for b in blocks}
    if len(by_block) != len(blocks) or len(blocks) != 2398:
        raise ValueError("frozen v2.1 block set changed")
    event_dates = event_dates_from_blocks(blocks)
    doc_to_event = {b["document_id"]: b["event_group_id"] for b in blocks}
    allowed_docs = set(doc_to_event)
    if len(allowed_docs) != 16:
        raise ValueError("frozen source document count changed")
    by_event = {}
    by_document = {}
    budget = config["input_adapter"]["max_characters_per_event"]
    for event in events:
        eid = event["event_group_id"]
        if eid in by_event:
            raise ValueError("duplicate event input")
        validated = validate_event(event, by_block, allowed_docs, budget)
        by_event[eid] = (event, validated)
        for did in validated["doc_text"]:
            if did in by_document or doc_to_event[did] != eid:
                raise ValueError("document appears in wrong or multiple events")
            by_document[did] = validated
    if len(by_event) != config["source"]["expected_events"] or set(by_event) != set(doc_to_event.values()):
        raise ValueError("v2.2 event set changed")

    rows = []
    field_counter = Counter()
    for cid, source_row in frozen_by_id.items():
        r = review_by_id[cid]
        c = candidate_by_id[cid]
        if source_row["review_status"] != r["status"] or source_row["topic"] != r["topic"]:
            raise ValueError("frozen review/retention mismatch")
        did = c["source_document_id"]
        validated = by_document.get(did)
        doc_text = validated["doc_text"].get(did, "") if validated else ""
        selected_blocks = [by_block[bid] for bid in (validated["selected_block_ids"] if validated else [])
                           if by_block[bid]["document_id"] == did]
        compare = r["final"].get("comparison_status") == COMPARISON_SUPPORTED
        fields_to_check = FIELDS + (COMPARISON_FIELDS if compare else ())
        status_by_field = {}
        for field in fields_to_check:
            grade = field_retention(r["field_evidence"].get(field, []), old_text="",
                                    selected_text=doc_text, full_block_text="",
                                    selected_blocks=selected_blocks, all_blocks=[])["v21_selected"]
            status_by_field[field] = grade
            field_counter[field + ":" + grade] += 1
        value = norm(str(r["final"].get("value_text") or ""))
        compare_value = norm(str(r["final"].get("comparison_value_text") or ""))
        value_present = bool(value) and value in norm(doc_text)
        compare_present = not compare or (bool(compare_value) and compare_value in norm(doc_text))
        v22_fields = {k: {"v22_selected": v} for k, v in status_by_field.items()}
        grade = fact_grade(r, v22_fields, "v22_selected",
                           footnote_unresolved=source_row["footnote_unresolved"],
                           literal_value_present=value_present,
                           literal_comparison_value_present=compare_present)
        if source_row["footnote_unresolved"]:
            binding = "FOOTNOTE_RELATION_UNRESOLVED"
        else:
            binding = binding_for_review(r, did, validated) if validated else "NO_VISIBLE_DOCUMENT"
        rows.append({
            "candidate_id": cid, "document_id": did, "event_id": r["event_id"],
            "symbol": r["symbol"], "filing_date": r["filing_date"], "topic": r["topic"],
            "source_url": r["final"]["source_url"], "source_status": r["status"],
            "old": source_row["fact_retention"]["old"],
            "v21_selected": source_row["fact_retention"]["v21_selected"],
            "v21_full_blocks": source_row["fact_retention"]["v21_full_blocks"],
            "v22_selected": grade, "v22_field_retention": status_by_field,
            "v22_literal_value_present": value_present,
            "v22_literal_comparison_value_present": compare_present,
            "v22_binding": binding,
            "prior_loss_stage": classify_prior_loss(source_row),
        })
    if len(rows) != 71:
        raise ValueError("fact result denominator changed")
    supported = [r for r in rows if r["source_status"] == "CODEX_REVIEW_SOURCE_SUPPORTED"]
    versions = ("old", "v21_selected", "v22_selected")
    counts = {v: dict(Counter(r[v] for r in rows)) for v in versions}
    supported_counts = {v: dict(Counter(r[v] for r in supported)) for v in versions}
    loss_rows = [{"candidate_id": r["candidate_id"], "symbol": r["symbol"],
                  "filing_date": r["filing_date"], "topic": r["topic"],
                  "source_status": r["source_status"], "loss_stage": r["prior_loss_stage"],
                  "v21_selected": r["v21_selected"], "v21_full_blocks": r["v21_full_blocks"]}
                 for r in rows]
    regress = [r for r in supported if r["old"] == "COMPLETE" and r["v22_selected"] != "COMPLETE"]
    now = datetime.now(timezone.utc).isoformat()
    input_hashes = {str(p.relative_to(ROOT)): sha(p.read_bytes())
                    for p in (CONFIG, BLOCKS, EVENTS, CANDIDATES, REVIEW, FROZEN, MANIFEST, Path(__file__))}
    private = {"schema": "financial_text_v22_retention_private_v1", "created_at_utc": now,
               "role": "TRAINING_CONTENT_ENGINEERING_NOT_EXTRACTION_OR_RETURN_VALIDATION",
               "input_sha256": input_hashes, "fact_rows": rows,
               "source_status_counts": dict(Counter(r["source_status"] for r in rows))}
    public = {"schema": "financial_text_v22_retention_redacted_v1", "created_at_utc": now,
              "role": private["role"], "input_sha256": input_hashes,
              "denominators": {"all_candidates": 71, "source_supported": 60,
                               "source_ambiguous": 11, "source_documents": 16, "events": 8,
                               "assessed_field_slots": sum(field_counter.values())},
              "source_status_counts": private["source_status_counts"],
              "fact_retention_all_candidates": counts,
              "fact_retention_source_supported": supported_counts,
              "strict_complete_all_candidates": {v: counts[v].get("COMPLETE", 0) for v in versions},
              "strict_complete_source_supported": {v: supported_counts[v].get("COMPLETE", 0) for v in versions},
              "v22_field_retention": dict(field_counter),
              "v22_binding_counts_source_supported": dict(Counter(r["v22_binding"] for r in supported)),
              "old_complete_v22_incomplete": [
                  {k: r[k] for k in ("symbol", "filing_date", "topic", "source_url", "old", "v22_selected", "v22_binding")}
                  for r in regress],
              "old_complete_v22_incomplete_count": len(regress),
              "event_input_budget": [{"symbol": e["symbol"], "announcement_date": event_dates[e["event_group_id"]],
                                      "selected_characters": e["selected_text_characters"],
                                      "selected_groups": len(e["selected_group_ids"]),
                                      "omitted_groups": len(e["omitted_groups"]),
                                      "gross_duplicate_context_characters_avoided": e.get("gross_duplicate_context_characters_avoided")}
                                     for e, _ in by_event.values()],
              "limitations": [
                  "All results use reused training material after prior validation exposure.",
                  "Same-team source review is separate from model-input retention and is not independent gold.",
                  "The shared legacy fragment evaluator grades visible source fragments; binding audit separately checks one explicit visible group, but does not prove semantic/accounting correctness.",
                  "Source-ambiguous records are not promoted. No model fit, inference, return label or trade is involved."],
              }
    loss_private = {"schema": "financial_text_v22_prior_loss_stage_private_v1",
                    "created_at_utc": now, "input_sha256": input_hashes,
                    "classification": "Frozen v2.1 rows only; never used by production adapter selection",
                    "rows": loss_rows}
    loss_public = {"schema": "financial_text_v22_prior_loss_stage_redacted_v1",
                   "created_at_utc": now, "input_sha256": input_hashes,
                   "denominators": {"all_candidates": 71, "source_supported": 60, "source_ambiguous": 11},
                   "loss_stage_counts": dict(Counter(r["loss_stage"] for r in loss_rows)),
                   "source_supported_loss_stage_counts": dict(Counter(r["loss_stage"] for r in loss_rows
                                                              if r["source_status"] == "CODEX_REVIEW_SOURCE_SUPPORTED")),
                   "strict_full_store_complete_but_selected_incomplete": sum(
                       r["v21_full_blocks"] == "COMPLETE" and r["v21_selected"] != "COMPLETE"
                       for r in loss_rows),
                   "note": "Stage labels are descriptive diagnostics under frozen training review, not source truth or model outcome."}
    return private, public, loss_private, loss_public


def run() -> dict:
    for path in (PRIVATE_OUT, PUBLIC_OUT, LOSS_PRIVATE_OUT, LOSS_PUBLIC_OUT):
        if path.exists():
            raise FileExistsError(f"Refusing to overwrite an existing comparison: {path}")
    for path, expected in FROZEN_SHA256.items():
        if sha(path.read_bytes()) != expected:
            raise ValueError(f"Frozen input changed: {path.relative_to(ROOT)}")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    events = read_jsonl(EVENTS)
    blocks = read_jsonl(BLOCKS)
    candidates = json.loads(CANDIDATES.read_text(encoding="utf-8"))
    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    frozen = json.loads(FROZEN.read_text(encoding="utf-8"))
    outputs = evaluate(events, blocks, candidates, review, frozen, config)
    for path, output in zip((PRIVATE_OUT, PUBLIC_OUT, LOSS_PRIVATE_OUT, LOSS_PUBLIC_OUT), outputs):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical(output))
    return outputs[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", help="Run the one frozen comparison")
    args = parser.parse_args()
    if not args.run:
        raise SystemExit("Use --run only after v2.2 preflight and selection are frozen")
    result = run()
    print(json.dumps({"schema": result["schema"], "denominators": result["denominators"],
                      "strict_complete_source_supported": result["strict_complete_source_supported"]},
                     sort_keys=True))


if __name__ == "__main__":
    main()
