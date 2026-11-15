"""Compare training-only original evidence with old and v2.1 model-input text.

The frozen checklist is used as an element-level reference. Reviewed fact
field evidence is reported separately. This is content engineering on reused
training material, never an extraction accuracy or return-model evaluation.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import unicodedata

from scripts.market_observer_official_v1.text_comparison import clean_text, load_sec_events


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "artifacts/market_observer_official_20261115/corpus_manifest.json"
OLD_CONFIG = ROOT / "configs/market_observer_official_text_v1.json"
PRIOR_TEXT_AUDIT = ROOT / "results/market_observer_official_repro_20261115/text_input_audit_redacted.json"
PRIVATE = ROOT / "artifacts/market_observer_financial_text_v21_20261115"
CHECKLIST = ROOT / "artifacts/market_observer_financial_text_v2_20261115/fact_cards/content_checklist_reviewed.private.json"
REVIEW = ROOT / "artifacts/market_observer_financial_text_v2_20261115/fact_cards/fact_review_v4.private.json"
REVIEW_V2 = ROOT / "artifacts/market_observer_financial_text_v2_20261115/fact_cards/fact_review_v2.private.json"
CANDIDATES = ROOT / "artifacts/market_observer_financial_text_v2_20261115/fact_cards/fact_candidates_v2.private.json"
BLOCKS = PRIVATE / "blocks.private.jsonl"
EVENTS = PRIVATE / "event_inputs.private.jsonl"
V21_DOCUMENTS = PRIVATE / "documents.private.json"
V21_BUILD = ROOT / "results/market_observer_financial_text_v21_20261115/build_redacted.json"
AGGREGATE_META = ROOT / "artifacts/market_observer_official_text_audit_20261115/aggregate_rows.private.jsonl"
PRIVATE_RESULT = PRIVATE / "retention_comparison.private.json"
PUBLIC_RESULT = ROOT / "results/market_observer_financial_text_v21_20261115/retention_redacted.json"
FROZEN_V2_RESULT = ROOT / "results/market_observer_financial_text_v2_20261115/retention_redacted.json"
EXPECTED_FROZEN_V2_SHA256 = "2cdb00d715447b1850b1db6473308376943e21d05a2abbb1e5bcc62211681e0f"
TRAIN_END = date(2026, 3, 31)
FIELDS = ("metric", "value", "period", "unit", "basis")
COMPARISON_FIELDS = ("comparison_value", "comparison_period", "comparison_unit", "comparison_basis")
COMPARISON_SUPPORTED = "SOURCE_BOUND_PRIOR_FISCAL_QUARTER"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def norm(value: str) -> str:
    result = unicodedata.normalize("NFKC", value).casefold()
    result = result.replace("−", "-").replace("–", "-")
    result = re.sub(r"\s+", " ", result).strip()
    return re.sub(r"([$€£])\s+(?=\d)", r"\1", result)


def evidence_text(item: dict) -> str:
    return str(item.get("rendered_visible_text") or item.get("text") or "")


def source_element_present(item: dict, text: str) -> bool:
    needle = norm(evidence_text(item))
    return bool(needle) and needle in norm(text)


def raw_element_mapped(item: dict, blocks: list[dict]) -> bool:
    needle = norm(evidence_text(item))
    return bool(needle) and any(needle in norm(block["raw_text"]) for block in blocks)


def classify_element_evidence(status: str, evidence: list[dict], *, old_text: str,
                              selected_text: str, full_block_text: str,
                              selected_blocks: list[dict], all_blocks: list[dict]) -> dict:
    if status == "NOT_FOUND":
        return {"old": "ORIGINAL_NOT_LOCATED", "v21_selected": "ORIGINAL_NOT_LOCATED",
                "v21_full_blocks": "ORIGINAL_NOT_LOCATED", "fragment_count": 0}
    if status != "LOCATED" or not evidence:
        return {"old": "AMBIGUOUS", "v21_selected": "AMBIGUOUS",
                "v21_full_blocks": "AMBIGUOUS", "fragment_count": len(evidence)}
    def grade(present: list[bool], mapped: list[bool] | None = None) -> str:
        if all(present):
            return "COMPLETE"
        if any(present):
            return "PARTIAL"
        if mapped is not None and any(mapped):
            return "AMBIGUOUS_REFORMATTED"
        return "LOST"
    return {
        "old": grade([source_element_present(e, old_text) for e in evidence]),
        "v21_selected": grade([source_element_present(e, selected_text) for e in evidence],
                             [raw_element_mapped(e, selected_blocks) for e in evidence]),
        "v21_full_blocks": grade([source_element_present(e, full_block_text) for e in evidence],
                                [raw_element_mapped(e, all_blocks) for e in evidence]),
        "fragment_count": len(evidence),
    }


def field_retention(evidence: list[dict], *, old_text: str,
                    selected_text: str, full_block_text: str,
                    selected_blocks: list[dict], all_blocks: list[dict]) -> dict:
    if not evidence:
        return {"old": "UNKNOWN_REFERENCE", "v21_selected": "UNKNOWN_REFERENCE",
                "v21_full_blocks": "UNKNOWN_REFERENCE"}
    def grade(results: list[bool], mapped: list[bool] | None = None) -> str:
        if all(results):
            return "PRESENT"
        if any(results):
            return "PARTIAL"
        if mapped is not None and any(mapped):
            return "AMBIGUOUS_REFORMATTED"
        return "MISSING"
    return {
        "old": grade([source_element_present(e, old_text) for e in evidence]),
        "v21_selected": grade([source_element_present(e, selected_text) for e in evidence],
                             [raw_element_mapped(e, selected_blocks) for e in evidence]),
        "v21_full_blocks": grade([source_element_present(e, full_block_text) for e in evidence],
                                [raw_element_mapped(e, all_blocks) for e in evidence]),
    }


def fact_grade(review: dict, fields: dict, version: str,
               *, footnote_unresolved: bool, literal_value_present: bool,
               literal_comparison_value_present: bool = True) -> str:
    if review["status"] != "CODEX_REVIEW_SOURCE_SUPPORTED":
        return "AMBIGUOUS"
    essential = [fields[k][version] for k in ("metric", "value", "period", "unit")]
    if any(x == "UNKNOWN_REFERENCE" for x in essential):
        return "AMBIGUOUS"
    if review["final"].get("basis") is not None:
        essential.append(fields["basis"][version])
        if essential[-1] == "UNKNOWN_REFERENCE":
            return "AMBIGUOUS"
    if review["final"].get("comparison_status") == COMPARISON_SUPPORTED:
        essential.extend(fields[k][version] for k in ("comparison_value", "comparison_period", "comparison_unit"))
        if review["final"].get("comparison_basis") is not None:
            essential.append(fields["comparison_basis"][version])
        if any(x == "UNKNOWN_REFERENCE" for x in essential):
            return "AMBIGUOUS"
    if footnote_unresolved:
        return "AMBIGUOUS"
    if not literal_value_present:
        return "PARTIAL" if any(x == "PRESENT" for x in essential) else "LOST"
    if not literal_comparison_value_present:
        return "PARTIAL" if any(x == "PRESENT" for x in essential) else "LOST"
    if all(x == "PRESENT" for x in essential):
        return "COMPLETE" if review["final"].get("basis") is not None else "COMPLETE_KNOWN_FIELDS_BASIS_UNKNOWN"
    if all(x == "MISSING" for x in essential):
        return "LOST"
    return "PARTIAL"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def train_aggregate_metadata_audit(path: Path, accession_ids: set[str]) -> dict:
    """Read saved event/date/hash metadata only; never open qualified row bodies or labels."""
    uses = Counter()
    train_rows = 0
    for line in path.open(encoding="utf-8"):
        if not line.strip():
            continue
        row = json.loads(line)
        if "announcement_text" in row or "label_up" in row or "price_features" in row:
            raise RuntimeError("aggregate metadata unexpectedly includes text, label or prices")
        if row.get("split") != "train":
            continue
        if date.fromisoformat(row["label_day"]) > TRAIN_END:
            raise RuntimeError("train metadata crosses date boundary")
        if row.get("event_group_aggregate_clips"):
            raise RuntimeError("old train aggregate clipping found")
        train_rows += 1
        for event_id in row["event_ids"]:
            if event_id in accession_ids:
                uses[event_id] += 1
    if set(uses) != accession_ids:
        raise RuntimeError("not every selected earnings event participates in saved training rows")
    return {"train_metadata_rows": train_rows,
            "selected_earnings_events_in_train_rows": len(uses),
            "selected_earnings_event_group_day_uses": sum(uses.values()),
            "aggregate_clipped_train_rows": 0,
            "no_body_label_or_price_fields": True}


def old_train_exhibit_text(manifest: dict, config: dict, checklist: dict) -> tuple[dict[str, str], dict]:
    """Filter manifest metadata before old builder opens any body."""
    accessions = {row["event_id"] for row in checklist["rows"]}
    selected_docs = [doc for doc in manifest["documents"]
                     if doc.get("event_id") in accessions
                     and doc.get("source_kind") in {"ISSUER_EARNINGS", "SEC_FILINGS"}
                     and doc.get("symbol") in {"NVDA", "MU"}
                     and "2025-04-01" <= str(doc.get("announcement_date", "")) <= "2026-03-31"]
    if len(selected_docs) != 16:
        raise RuntimeError("old reconstruction selected an unexpected document count")
    for doc in selected_docs:
        raw = Path(doc["raw_body_path"])
        body = Path(doc["body_path"])
        if sha(raw.read_bytes()) != doc["raw_body_sha256"] or sha(body.read_bytes()) != doc["body_sha256"]:
            raise RuntimeError("saved train source hash mismatch")
    filtered_manifest = {"documents": selected_docs}
    events, problems = load_sec_events(filtered_manifest, config=config, latest_decision=TRAIN_END)
    if problems or len(events) != len(accessions) or any(event["problems"] for event in events):
        raise RuntimeError("old training-only event reconstruction failed")
    old_doc_text: dict[str, str] = {}
    for event in events:
        docs = sorted((d for d in selected_docs if d["event_id"] == event["event_id"]),
                      key=lambda d: (0 if d["source_kind"] == "ISSUER_EARNINGS" else 1, d["document_id"]))
        per_doc_budget = max(1, int(config["event_text_max_characters"]) // len(docs))
        slices = []
        for doc in docs:
            old_doc_text[doc["document_id"]] = clean_text(Path(doc["body_path"]).read_text(encoding="utf-8"))[:per_doc_budget]
            slices.append(old_doc_text[doc["document_id"]])
        if " ".join(slices)[:config["event_text_max_characters"]] != event["text"]:
            raise RuntimeError("old event text differs from frozen builder")
    return old_doc_text, {"selected_train_source_documents": len(selected_docs),
                          "reconstructed_old_event_groups": len(events),
                          "event_text_sha256_by_id": {e["event_id"]: e["text_sha256"] for e in events}}


def run() -> dict:
    if PRIVATE_RESULT.exists() or PUBLIC_RESULT.exists():
        raise FileExistsError("refusing to overwrite retention comparison")
    if sha(FROZEN_V2_RESULT.read_bytes()) != EXPECTED_FROZEN_V2_SHA256:
        raise RuntimeError("frozen v2 comparison changed")
    frozen_v2 = json.loads(FROZEN_V2_RESULT.read_text(encoding="utf-8"))
    input_paths = {name: path for name, path in {
        "manifest": MANIFEST, "old_config": OLD_CONFIG, "prior_text_audit": PRIOR_TEXT_AUDIT,
        "frozen_reviewed_checklist": CHECKLIST,
        "fact_review_v4": REVIEW, "fact_review_v2": REVIEW_V2,
        "old_aggregate_metadata_only": AGGREGATE_META,
        "fact_candidates_v2": CANDIDATES,
        "v21_blocks": BLOCKS, "v21_event_inputs": EVENTS,
        "v21_documents": V21_DOCUMENTS, "v21_build": V21_BUILD,
    }.items()}
    inputs = {name: sha(path.read_bytes()) for name, path in input_paths.items()}
    built = json.loads(V21_BUILD.read_text(encoding="utf-8"))
    if (built["private_output_hashes"]["blocks"] != inputs["v21_blocks"]
            or built["private_output_hashes"]["event_inputs"] != inputs["v21_event_inputs"]
            or built["private_output_hashes"]["documents"] != inputs["v21_documents"]):
        raise RuntimeError("v2.1 build receipt/output hash mismatch")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    config = json.loads(OLD_CONFIG.read_text(encoding="utf-8"))
    checklist = json.loads(CHECKLIST.read_text(encoding="utf-8"))
    reviewed = json.loads(REVIEW.read_text(encoding="utf-8"))
    reviewed_v2 = json.loads(REVIEW_V2.read_text(encoding="utf-8"))
    candidates = json.loads(CANDIDATES.read_text(encoding="utf-8"))
    prior_text_audit = json.loads(PRIOR_TEXT_AUDIT.read_text(encoding="utf-8"))
    if any(prior_text_audit["symbols"][symbol]["splits"]["train"]["aggregate_clipped_rows"] != 0
           for symbol in ("US.NVDA", "US.MU")):
        raise RuntimeError("old train aggregate clipping differs from prior read-only audit")
    if reviewed["previous_review_sha256"] != inputs["fact_review_v2"] or \
            reviewed_v2["reviewed_checklist_sha256"] != inputs["frozen_reviewed_checklist"] or \
            reviewed["candidate_sha256"] != inputs["fact_candidates_v2"] or \
            reviewed_v2["candidate_sha256"] != inputs["fact_candidates_v2"]:
        raise RuntimeError("reviewed fact input versions do not match")
    old_text, old_audit = old_train_exhibit_text(manifest, config, checklist)
    old_train_meta = train_aggregate_metadata_audit(
        AGGREGATE_META, {row["event_id"] for row in checklist["rows"]})
    blocks = read_jsonl(BLOCKS)
    events = read_jsonl(EVENTS)
    by_event = {e["event_group_id"]: e for e in events}
    by_block_id = {b["block_id"]: b for b in blocks}
    if len(by_block_id) != len(blocks):
        raise RuntimeError("duplicate v2 block identifier")
    by_doc_all = defaultdict(list)
    by_doc_selected = defaultdict(list)
    selected_doc_parts = defaultdict(list)
    selected_by_event = {event["event_group_id"]: set(event["selected_block_ids"]) for event in events}
    for block in blocks:
        by_doc_all[block["document_id"]].append(block)
        if block["block_id"] in selected_by_event[block["event_group_id"]]:
            by_doc_selected[block["document_id"]].append(block)
    for event in events:
        for offset in event["v2_clean_text_offsets"]:
            bid = offset["block_id"]
            block = by_block_id[bid]
            if block["event_group_id"] != event["event_group_id"] or bid not in selected_by_event[event["event_group_id"]]:
                raise RuntimeError("selected offset has wrong event or block")
            part = event["selected_text"][offset["v2_assembled_text_start"]:offset["v2_assembled_text_end"]]
            if part != f"[source={block['source_kind']}; block={bid}] {block['text']}":
                raise RuntimeError("selected-text offset differs from block text")
            selected_doc_parts[block["document_id"]].append(part)
    selected_doc_text = {did: "\n".join(parts) for did, parts in selected_doc_parts.items()}
    full_doc_text = {did: "\n".join(block["text"] for block in doc_blocks)
                     for did, doc_blocks in by_doc_all.items()}
    if len(by_event) != 8 or len(blocks) != 2398:
        raise RuntimeError("v2 training input count differs from frozen build receipt")
    topic_rows = []
    topic_lookup = {}
    for row in checklist["rows"]:
        did = row["earnings_document_id"]
        if did not in old_text or row["event_id"] not in by_event:
            raise RuntimeError("checklist document/event not in old/v2 input")
        for topic, source in row["topics"].items():
            result = classify_element_evidence(source["status"], source["evidence"],
                                               old_text=old_text[did],
                                               selected_text=selected_doc_text.get(did, ""),
                                               full_block_text=full_doc_text.get(did, ""),
                                               selected_blocks=by_doc_selected[did],
                                               all_blocks=by_doc_all[did])
            item = {"event_id": row["event_id"], "symbol": row["symbol"],
                    "filing_date": row["filing_date"], "topic": topic,
                    "source_url": source["source_url"], "source_status": source["status"], **result}
            topic_rows.append(item)
            topic_lookup[(row["event_id"], topic)] = result
    if len(topic_rows) != 72:
        raise RuntimeError("frozen reviewed checklist is not 8 by 9")
    candidate_by_id = {c["candidate_id"]: c for c in candidates["candidates"]}
    fact_rows = []
    field_count = {name: Counter() for name in ("old", "v21_selected", "v21_full_blocks")}
    candidate_corrections = Counter()
    candidate_wrong_nonnull = Counter()
    comparison_applicable = 0
    for review in reviewed["reviews"]:
        did = candidate_by_id[review["candidate_id"]]["source_document_id"]
        topic = review["topic"]
        evidence_grade = topic_lookup[(review["event_id"], topic)]
        compare_applicable = review["final"].get("comparison_status") == COMPARISON_SUPPORTED
        if compare_applicable:
            comparison_applicable += 1
        fields_to_check = FIELDS + (COMPARISON_FIELDS if compare_applicable else ())
        field_result = {field: field_retention(review["field_evidence"].get(field, []),
                                              old_text=old_text[did],
                                              selected_text=selected_doc_text.get(did, ""),
                                              full_block_text=full_doc_text.get(did, ""),
                                              selected_blocks=by_doc_selected[did],
                                              all_blocks=by_doc_all[did]) for field in fields_to_check}
        for field, result in field_result.items():
            for version in field_count:
                field_count[version][field + ":" + result[version]] += 1
        candidate = candidate_by_id[review["candidate_id"]]
        for field, candidate_key, review_key in (("value", "value_text", "value_text"),
                                                 ("period", "period_text", "period_text"),
                                                 ("unit", "unit_text", "unit_text"),
                                                 ("basis", "basis_candidate", "basis")):
            if candidate.get(candidate_key) != review["final"].get(review_key):
                candidate_corrections[field] += 1
                if candidate.get(candidate_key) is not None:
                    candidate_wrong_nonnull[field] += 1
        possible_value_blocks = [b for b in by_doc_all[did]
                                 if any(source_element_present(e, b["raw_text"])
                                        for e in review["field_evidence"].get("value", []))]
        footnote_unresolved = any(b.get("footnote_refs") for b in possible_value_blocks) and \
            not review["field_evidence"].get("footnote")
        literal_value = norm(str(review["final"].get("value_text") or ""))
        literal_compare = norm(str(review["final"].get("comparison_value_text") or ""))
        value_present = {
            "old": bool(literal_value) and literal_value in norm(old_text[did]),
            "v21_selected": bool(literal_value) and literal_value in norm(selected_doc_text.get(did, "")),
            "v21_full_blocks": bool(literal_value) and literal_value in norm(full_doc_text.get(did, "")),
        }
        comparison_value_present = {
            "old": not compare_applicable or (bool(literal_compare) and literal_compare in norm(old_text[did])),
            "v21_selected": not compare_applicable or (bool(literal_compare) and literal_compare in norm(selected_doc_text.get(did, ""))),
            "v21_full_blocks": not compare_applicable or (bool(literal_compare) and literal_compare in norm(full_doc_text.get(did, ""))),
        }
        grades = {version: fact_grade(review, field_result, version,
                                      footnote_unresolved=footnote_unresolved,
                                      literal_value_present=value_present[version],
                                      literal_comparison_value_present=comparison_value_present[version])
                  for version in field_count}
        fact_rows.append({"candidate_id": review["candidate_id"], "event_id": review["event_id"],
                          "symbol": review["symbol"], "filing_date": review["filing_date"],
                          "topic": topic, "review_status": review["status"],
                          "source_url": review["final"]["source_url"],
                          "field_retention": field_result, "topic_evidence_retention": evidence_grade,
                          "footnote_unresolved": bool(footnote_unresolved),
                          "literal_value_present": value_present,
                          "literal_comparison_value_present": comparison_value_present,
                          "comparison_applicable": compare_applicable, "fact_retention": grades})
    if len(fact_rows) != len(candidates["candidates"]) or len(fact_rows) != 71:
        raise RuntimeError("reviewed fact denominator differs from candidate count")
    versions = ("old", "v21_selected", "v21_full_blocks")
    topic_counts = {version: dict(Counter(row[version] for row in topic_rows)) for version in versions}
    fact_counts = {version: dict(Counter(row["fact_retention"][version] for row in fact_rows)) for version in versions}
    doc_counts = {}
    for version in versions:
        doc_counts[version] = {
            "earning_documents_with_any_complete_reviewed_fact": len({r["event_id"] for r in fact_rows
                                                                          if r["fact_retention"][version] == "COMPLETE"}),
            "earning_documents_with_all_located_checklist_spans_complete": sum(
                all(row[version] == "COMPLETE" for row in topic_rows
                    if row["event_id"] == event and row["source_status"] == "LOCATED")
                for event in {row["event_id"] for row in topic_rows}),
        }
    transition = dict(Counter(r["fact_retention"]["old"] + " -> " + r["fact_retention"]["v21_selected"]
                              for r in fact_rows))
    restored = [r for r in fact_rows if r["fact_retention"]["old"] != "COMPLETE"
                and r["fact_retention"]["v21_selected"] == "COMPLETE"]
    v21_fail = [r for r in fact_rows if r["review_status"] == "CODEX_REVIEW_SOURCE_SUPPORTED"
               and r["fact_retention"]["v21_selected"] not in {"COMPLETE", "COMPLETE_KNOWN_FIELDS_BASIS_UNKNOWN"}]
    examples = lambda rows, max_count: [{"symbol": r["symbol"], "filing_date": r["filing_date"],
                                          "topic": r["topic"], "old_status": r["fact_retention"]["old"],
                                          "v21_selected_status": r["fact_retention"]["v21_selected"],
                                          "source_url": r["source_url"]} for r in rows[:max_count]]
    private = {"schema": "financial_text_v21_retention_private_v1",
        "inputs_sha256": inputs, "old_builder_reconstruction": old_audit,
        "old_train_metadata_only_check": old_train_meta,
               "topic_rows": topic_rows, "fact_rows": fact_rows}
    public = {
        "schema": "financial_text_v21_retention_redacted_v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "role": "TRAINING_CONTENT_ENGINEERING_NOT_EXTRACTION_ACCURACY_OR_PREDICTIVE_EVALUATION",
        "input_sha256": inputs, "old_builder_reconstruction": old_audit,
        "old_train_metadata_only_check": old_train_meta,
        "denominators": {"source_documents": 16, "earnings_documents": 8,
                         "frozen_checklist_topics": len(topic_rows), "reviewed_candidate_facts": len(fact_rows),
                         "core_field_slots": len(fact_rows) * len(FIELDS),
                         "facts_with_source_bound_prior_fiscal_quarter": comparison_applicable,
                         "comparison_field_slots": comparison_applicable * len(COMPARISON_FIELDS),
                         "assessed_field_slots_total": len(fact_rows) * len(FIELDS) + comparison_applicable * len(COMPARISON_FIELDS)},
        "checklist_source_status_counts": dict(Counter(r["source_status"] for r in topic_rows)),
        "checklist_span_retention": topic_counts,
        "reviewed_fact_retention": fact_counts,
        "frozen_v2_comparison_sha256": EXPECTED_FROZEN_V2_SHA256,
        "frozen_v2_strict_complete": {
            "selected": frozen_v2["reviewed_fact_retention"]["v2_selected"].get("COMPLETE", 0),
            "full_blocks": frozen_v2["reviewed_fact_retention"]["v2_full_blocks"].get("COMPLETE", 0),
        },
        "v21_strict_complete_delta_from_v2": {
            "selected": fact_counts["v21_selected"].get("COMPLETE", 0) - frozen_v2["reviewed_fact_retention"]["v2_selected"].get("COMPLETE", 0),
            "full_blocks": fact_counts["v21_full_blocks"].get("COMPLETE", 0) - frozen_v2["reviewed_fact_retention"]["v2_full_blocks"].get("COMPLETE", 0),
        },
        "reviewed_fact_transition_old_to_v21_selected": transition,
        "field_retention": {version: dict(counter) for version, counter in field_count.items()},
        "document_retention": doc_counts,
        "candidate_to_review_field_change_counts": dict(candidate_corrections),
        "candidate_nonnull_field_rejected_or_changed_counts": dict(candidate_wrong_nonnull),
        "source_supported_facts_with_basis_unknown": sum(
            r["status"] == "CODEX_REVIEW_SOURCE_SUPPORTED" and r["final"].get("basis") is None
            for r in reviewed["reviews"]),
        "possible_unlinked_footnote_fact_count": sum(r["footnote_unresolved"] for r in fact_rows),
        "old_incomplete_v21_complete_examples": examples(restored, 3),
        "v21_still_incomplete_examples": examples(v21_fail, 3),
        "source_supported_facts_with_incomplete_v21_input": len(v21_fail),
        "old_train_aggregate_clipped_rows_by_symbol": {
            symbol: prior_text_audit["symbols"][symbol]["splits"]["train"]["aggregate_clipped_rows"]
            for symbol in ("US.NVDA", "US.MU")},
        "old_train_aggregate_note": "Only saved event/date/hash metadata without body, label or price fields was read for train participation; zero clipping also matches the saved prior audit hash above. Mixed qualified-row files were not opened.",
        "limitations": [
            "Mechanical source-element and reviewed field-string presence is conservative; semantic support was same-team CODEX_REVIEW and is not independent human gold.",
            "Only actual adapter block text counts as mechanically retained; a source fragment seen only in raw_text is AMBIGUOUS_REFORMATTED, not COMPLETE.",
            "A mechanically exact source fragment does not independently prove semantic period/column support; the v2.1 header copies are table-bound and full source relationships still require review.",
            "Unresolved supporting footnote references block COMPLETE status; footnote text is not automatically attached to each table cell.",
            "P1 checklist topic-span retention and P3 reviewed-fact field retention are separate; a fact is graded on its own reviewed field evidence, not every topic-level candidate span.",
            "COMPLETE_KNOWN_FIELDS_BASIS_UNKNOWN is not strict complete financial context: metric, value, period and unit survived, but accounting basis remains unknown in source review.",
            "No model fit, validation-period input, price label, or predictive comparison in this run.",
        ],
    }
    PRIVATE_RESULT.parent.mkdir(parents=True, exist_ok=True)
    PUBLIC_RESULT.parent.mkdir(parents=True, exist_ok=True)
    PRIVATE_RESULT.write_text(json.dumps(private, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    PUBLIC_RESULT.write_text(json.dumps(public, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"earnings_documents": 8, "checklist_topics": len(topic_rows),
                      "reviewed_facts": len(fact_rows),
                      "assessed_field_slots": len(fact_rows) * len(FIELDS) + comparison_applicable * len(COMPARISON_FIELDS),
                      "topic_counts": topic_counts, "fact_counts": fact_counts,
                      "restored_fact_count": len(restored), "footnote_unresolved": public["possible_unlinked_footnote_fact_count"],
                      "private_sha256": sha(PRIVATE_RESULT.read_bytes()),
                      "public_sha256": sha(PUBLIC_RESULT.read_bytes())}, ensure_ascii=False))
    return public


if __name__ == "__main__":
    run()
