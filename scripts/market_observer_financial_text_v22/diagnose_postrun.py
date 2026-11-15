"""Read-only post-run locator diagnosis for the frozen v2.2 training comparison.

Candidate/review evidence enters only this diagnostic. Nothing here is imported
by the production adapter. It never changes a frozen build or retention score.
"""

from __future__ import annotations

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
    norm,
    source_element_present,
)


ROOT = Path(__file__).resolve().parents[2]
BLOCKS = ROOT / "artifacts/market_observer_financial_text_v21_20261115/blocks.private.jsonl"
EVENTS = ROOT / "artifacts/market_observer_financial_text_v22_20261116/event_inputs.private.jsonl"
CANDIDATES = ROOT / "artifacts/market_observer_financial_text_v2_20261115/fact_cards/fact_candidates_v2.private.json"
REVIEW = ROOT / "artifacts/market_observer_financial_text_v2_20261115/fact_cards/fact_review_v4.private.json"
RETENTION = ROOT / "artifacts/market_observer_financial_text_v22_20261116/retention_comparison.private.json"
OUT_PRIVATE = ROOT / "artifacts/market_observer_financial_text_v22_20261116/postrun_loss_diagnosis.private.json"
OUT_PUBLIC = ROOT / "results/market_observer_financial_text_v22_20261116/postrun_loss_diagnosis_redacted.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def normalize_dom(path: str) -> str:
    """Convert reviewed XPath-like locators to v2.1 parser CSS-style DOM paths."""
    if not path:
        return ""
    if ":nth-of-type(" in path:
        return " > ".join(part.strip() for part in path.split(" > "))
    parts = []
    for part in path.split("/"):
        match = re.fullmatch(r"([A-Za-z0-9_-]+)\[(\d+)\]", part)
        if not match:
            return ""
        parts.append(f"{match.group(1)}:nth-of-type({match.group(2)})")
    return " > ".join(parts)


def match_evidence(evidence: dict, document_blocks: list[dict]) -> tuple[str, list[str]]:
    """Keep DOM+text matching distinct from text-only and unverified locators."""
    target = normalize_dom(str(evidence.get("dom_path") or ""))
    exact = [b["block_id"] for b in document_blocks if target and normalize_dom(b["dom_path"]) == target
             and source_element_present(evidence, b["raw_text"])]
    if exact:
        return "EXACT_DOM_AND_TEXT", exact
    related = [b["block_id"] for b in document_blocks
               if target and (normalize_dom(b["dom_path"]).startswith(target + " > ")
                              or target.startswith(normalize_dom(b["dom_path"]) + " > "))
               and source_element_present(evidence, b["raw_text"])]
    if related:
        return "RELATED_DOM_AND_TEXT", related
    text_only = [b["block_id"] for b in document_blocks if source_element_present(evidence, b["raw_text"])]
    if text_only:
        return "TEXT_ONLY_AMBIGUOUS", text_only
    dom_only = [b["block_id"] for b in document_blocks if target and normalize_dom(b["dom_path"]) == target]
    if dom_only:
        return "DOM_ONLY_TEXT_MISMATCH", dom_only
    return "NO_BLOCK_MATCH", []


def field_visible(evidence: list[dict], text: str) -> bool:
    return bool(evidence) and all(source_element_present(item, text) for item in evidence)


def candidate_diagnosis(row: dict, candidate: dict, review: dict,
                        event: dict, document_blocks: list[dict]) -> dict:
    did = candidate["source_document_id"]
    spans = event["model_visible_spans"]
    anchor_spans = {s["group_id"]: s for s in spans if s["group_id"]}
    context_spans = {s["context_id"]: s for s in spans if s["context_id"]}
    inventory = {g["group_id"]: g for g in event["group_inventory"] if g["document_id"] == did}
    groups_by_anchor: dict[str, list[dict]] = defaultdict(list)
    for group in inventory.values():
        groups_by_anchor[group["anchor_block_id"]].append(group)
    value_matches = [match_evidence(e, document_blocks) for e in review["field_evidence"].get("value", [])]
    period_matches = [match_evidence(e, document_blocks) for e in review["field_evidence"].get("period", [])]
    value_block_ids = set.intersection(*(set(ids) for _, ids in value_matches)) if value_matches else set()
    if not value_block_ids and value_matches:
        value_block_ids = set().union(*(set(ids) for _, ids in value_matches))
    anchor_groups = [g for bid in value_block_ids for g in groups_by_anchor.get(bid, [])]
    anchor_groups = list({g["group_id"]: g for g in anchor_groups}.values())
    selected = [g for g in anchor_groups if g["status"] == "SELECTED"]
    omitted = [g for g in anchor_groups if g["status"] == "OMITTED"]
    if selected:
        anchor_state = "SELECTED_VALUE_ANCHOR"
    elif omitted:
        anchor_state = "OMITTED_VALUE_ANCHOR"
    elif value_block_ids:
        anchor_state = "MATCHED_VALUE_BLOCK_NOT_AN_ANCHOR"
    else:
        anchor_state = "NO_VALUE_BLOCK_MATCH"
    omit_reasons = sorted({g["omission_reason"] for g in omitted if g.get("omission_reason")})
    fields = list(FIELDS)
    if review["final"].get("basis") is None:
        fields.remove("basis")
    if review["final"].get("comparison_status") == COMPARISON_SUPPORTED:
        fields += list(COMPARISON_FIELDS)
        if review["final"].get("comparison_basis") is None:
            fields.remove("comparison_basis")
    group_checks = []
    for group in selected:
        gid = group["group_id"]
        if gid not in anchor_spans or any(cid not in context_spans for cid in group["context_ids"]):
            group_checks.append({"group_id": gid, "relation": "SIDECAR_VISIBLE_SPAN_MISMATCH", "missing_fields": fields})
            continue
        source_spans = [anchor_spans[gid]] + [context_spans[cid] for cid in group["context_ids"]]
        if any(s["document_id"] != did for s in source_spans):
            group_checks.append({"group_id": gid, "relation": "CROSS_DOCUMENT_CONTEXT", "missing_fields": fields})
            continue
        group_text = "\n".join(event["selected_text"][s["start"]:s["end"]] for s in source_spans)
        missing = [field for field in fields if not field_visible(review["field_evidence"].get(field, []), group_text)]
        literal = norm(str(review["final"].get("value_text") or ""))
        if not literal or literal not in norm(group_text):
            missing.append("literal_value")
        relation = "ALL_REQUIRED_VISIBLE" if not missing else "FIELD_CONTEXT_NOT_BOUND"
        group_checks.append({"group_id": gid, "relation": relation, "missing_fields": sorted(set(missing))})
    if group_checks:
        best = min(group_checks, key=lambda x: (len(x["missing_fields"]), x["group_id"]))
        best_missing = best["missing_fields"]
    else:
        best = None
        best_missing = []
    doc_visible = "\n".join(event["selected_text"][s["start"]:s["end"]] for s in spans if s["document_id"] == did)
    if not review["field_evidence"].get("period"):
        period_relation = "SOURCE_PERIOD_REFERENCE_UNKNOWN"
    elif best and "period" not in best_missing:
        period_relation = "BOUND_IN_SELECTED_VALUE_GROUP"
    elif field_visible(review["field_evidence"]["period"], doc_visible):
        period_relation = "VISIBLE_ELSEWHERE_NOT_BOUND_TO_VALUE_GROUP"
    elif any(ids for _, ids in period_matches):
        period_relation = "PARSER_BLOCK_PRESENT_NOT_MODEL_VISIBLE"
    else:
        period_relation = "PARSER_BLOCK_OR_SOURCE_CONTEXT_NOT_MATCHED"
    return {
        "candidate_id": row["candidate_id"], "document_id": did,
        "event_id": row["event_id"], "symbol": row["symbol"],
        "filing_date": row["filing_date"], "topic": row["topic"],
        "source_status": row["source_status"], "v22_selected": row["v22_selected"],
        "v22_binding": row["v22_binding"], "anchor_state": anchor_state,
        "value_locator_modes": [mode for mode, _ in value_matches],
        "period_locator_modes": [mode for mode, _ in period_matches],
        "value_block_ids": sorted(value_block_ids),
        "period_block_ids": sorted(set().union(*(set(ids) for _, ids in period_matches)) if period_matches else set()),
        "anchor_group_ids": sorted(g["group_id"] for g in anchor_groups),
        "selected_anchor_group_count": len(selected),
        "omitted_anchor_group_count": len(omitted),
        "omitted_anchor_reasons": omit_reasons,
        "best_selected_group": best,
        "period_relation": period_relation,
    }


def run() -> dict:
    for output in (OUT_PRIVATE, OUT_PUBLIC):
        if output.exists():
            raise FileExistsError(f"Refusing to overwrite frozen diagnostic: {output}")
    retention = json.loads(RETENTION.read_text(encoding="utf-8"))
    expected = retention["input_sha256"]
    for path in (BLOCKS, EVENTS, CANDIDATES, REVIEW):
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        if expected.get(rel, expected.get(rel.replace("/", "\\"))) != sha(path):
            raise ValueError(f"Input differs from frozen comparison: {rel}")
    blocks = read_jsonl(BLOCKS)
    by_doc = defaultdict(list)
    for block in blocks:
        by_doc[block["document_id"]].append(block)
    events = {event["event_group_id"]: event for event in read_jsonl(EVENTS)}
    candidates = {c["candidate_id"]: c for c in json.loads(CANDIDATES.read_text(encoding="utf-8"))["candidates"]}
    reviews = {r["candidate_id"]: r for r in json.loads(REVIEW.read_text(encoding="utf-8"))["reviews"]}
    rows = retention["fact_rows"]
    if not (len(rows) == len(candidates) == len(reviews) == 71 and len(events) == 8):
        raise ValueError("Frozen result denominator changed")
    diagnostics = []
    for row in rows:
        cid = row["candidate_id"]
        if cid not in candidates or cid not in reviews or row["event_id"] not in events:
            raise ValueError("Candidate/review/event identity mismatch")
        diagnostics.append(candidate_diagnosis(row, candidates[cid], reviews[cid], events[row["event_id"]],
                                               by_doc[candidates[cid]["source_document_id"]]))
    strict = [d for d in diagnostics if d["v22_selected"] == "COMPLETE"]
    if len(strict) != 8:
        raise ValueError("Frozen v2.2 strict-complete count changed")
    now = datetime.now(timezone.utc).isoformat()
    inputs = {str(p.relative_to(ROOT)).replace("\\", "/"): sha(p)
              for p in (BLOCKS, EVENTS, CANDIDATES, REVIEW, RETENTION, Path(__file__))}
    private = {"schema": "financial_text_v22_postrun_loss_diagnosis_private_v1",
               "created_at_utc": now, "role": "POSTRUN_READ_ONLY_NO_COMPARISON_RERUN",
               "input_sha256": inputs, "rows": diagnostics}
    public = {"schema": "financial_text_v22_postrun_loss_diagnosis_redacted_v1",
              "created_at_utc": now, "role": private["role"], "input_sha256": inputs,
              "denominators": {"all_candidates": 71, "source_supported": 60,
                               "source_ambiguous": 11, "strict_fragment_complete": len(strict)},
              "group_inventory": {
                  "selected": sum(g["status"] == "SELECTED" for e in events.values() for g in e["group_inventory"]),
                  "omitted": sum(g["status"] == "OMITTED" for e in events.values() for g in e["group_inventory"]),
                  "omission_reasons": dict(Counter(g["omission_reason"] for e in events.values()
                                                   for g in e["group_inventory"] if g["status"] == "OMITTED"))},
              "candidate_anchor_state": dict(Counter(d["anchor_state"] for d in diagnostics)),
              "source_supported_anchor_state": dict(Counter(d["anchor_state"] for d in diagnostics
                                                            if d["source_status"] == "CODEX_REVIEW_SOURCE_SUPPORTED")),
              "omitted_anchor_reason_by_topic": {
                  topic: dict(Counter(reason for d in diagnostics if d["topic"] == topic
                                      for reason in d["omitted_anchor_reasons"]))
                  for topic in sorted({d["topic"] for d in diagnostics})},
              "period_relation": dict(Counter(d["period_relation"] for d in diagnostics)),
              "strict_fragment_complete_binding_audit": [
                  {"symbol": d["symbol"], "filing_date": d["filing_date"], "topic": d["topic"],
                   "binding": d["v22_binding"], "anchor_state": d["anchor_state"],
                   "period_relation": d["period_relation"],
                   "best_group_missing_fields": d["best_selected_group"]["missing_fields"]
                   if d["best_selected_group"] else None}
                  for d in strict],
              "limitations": [
                  "DOM/text matching maps saved reviewed evidence to saved v2.1 source blocks; text-only or multiple matches remain ambiguous.",
                  "An anchor group may correspond to a source row but not to the exact reviewed metric or value column; this is mechanical diagnosis, not fact confirmation.",
                  "A period fragment visible elsewhere in one document is not proof of binding to a numeric row.",
                  "Source review status remains frozen; no model fit, inference or return evaluation was run."],
              }
    OUT_PRIVATE.parent.mkdir(parents=True, exist_ok=True)
    OUT_PUBLIC.parent.mkdir(parents=True, exist_ok=True)
    OUT_PRIVATE.write_text(json.dumps(private, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    OUT_PUBLIC.write_text(json.dumps(public, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return public


if __name__ == "__main__":
    result = run()
    print(json.dumps({"denominators": result["denominators"],
                      "candidate_anchor_state": result["candidate_anchor_state"],
                      "strict_fragment_complete_binding_audit": result["strict_fragment_complete_binding_audit"]},
                     ensure_ascii=False, sort_keys=True))
