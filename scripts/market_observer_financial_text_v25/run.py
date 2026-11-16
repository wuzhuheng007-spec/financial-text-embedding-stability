"""Run one append-only eight-event source parse and one diagnostic fixed-budget pack."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from .adapter import assemble_event, parse_document_expanded, validate_model_visible_event
from .evaluate import evaluate_frozen_tables, evaluate_candidates
from .preflight import (PRIVATE, PUBLIC, ROOT, canonical, dependency_hashes,
                        sha, source_rows)


PREFLIGHT = PUBLIC / "preflight_frozen_v1_redacted.json"
PARSED = PRIVATE / "parsed_frozen_v1.private.jsonl"
PARSE_RECEIPT = PUBLIC / "parse_frozen_v1_redacted.json"
PACKED = PRIVATE / "pack_frozen_v1.private.jsonl"
PACK_RECEIPT = PUBLIC / "pack_frozen_v1_redacted.json"
REFERENCE = PRIVATE / "reference_frozen_v1.private.json"
SOURCE_AUDIT = PRIVATE / "source_audit_frozen_v1.private.json"
EVALUATION = PRIVATE / "evaluation_frozen_v1.private.json"
EVALUATION_RECEIPT = PUBLIC / "evaluation_frozen_v1_redacted.json"


def _now() -> dict:
    t = datetime.now(timezone.utc)
    return {"utc": t.isoformat(), "asia_shanghai": t.astimezone(ZoneInfo("Asia/Shanghai")).isoformat()}


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _checked_preflight() -> tuple[dict, list[dict]]:
    receipt = _read_json(PREFLIGHT)
    if receipt.get("schema") != "financial_text_v25_preflight_v1":
        raise RuntimeError("missing frozen v2.5 preflight")
    if dependency_hashes() != receipt["dependency_sha256"]:
        raise RuntimeError("frozen code, config, evaluator, reference or manifest hash changed")
    config = _read_json(ROOT / "configs/market_observer_financial_text_v25_20261116.json")
    docs = source_rows(config)
    if ([{k: d[k] for k in ("document_id", "event_group_id", "announcement_date", "symbol", "source_kind", "raw_body_sha256")}
         for d in docs] != receipt["source_documents"]):
        raise RuntimeError("saved source version or order differs from frozen receipt")
    return receipt, docs


def _read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_once(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as f:
        f.write(raw)


def parse_once() -> dict:
    if PARSED.exists() or PARSE_RECEIPT.exists():
        raise FileExistsError("frozen full parse already executed; preserve it")
    preflight, docs = _checked_preflight()
    rows: list[dict] = []
    status_counts: Counter[str] = Counter()
    table_counts: Counter[str] = Counter()
    event_ids = {d["event_group_id"] for d in docs}
    started = _now()
    PARSED.parent.mkdir(parents=True, exist_ok=True)
    with PARSED.open("xb") as checkpoint:
        for doc in docs:
            raw = Path(doc["raw_body_path"]).read_bytes()
            if sha(raw) != doc["raw_body_sha256"]:
                raise RuntimeError("saved source hash changed after preflight")
            try:
                output = parse_document_expanded(raw, doc)
                if output["source_sha256"] != doc["raw_body_sha256"]:
                    raise RuntimeError("parser source hash mismatch")
                table_counts.update(t["status"] for t in output["table_inventory"])
                status_counts["PARSED"] += 1
                row = {"document_id": doc["document_id"], "event_group_id": doc["event_group_id"],
                       "status": "PARSED", "output": output}
            except Exception as exc:
                status_counts["PARSE_FAILED"] += 1
                row = {"document_id": doc["document_id"], "event_group_id": doc["event_group_id"],
                       "status": "PARSE_FAILED", "error_type": type(exc).__name__,
                       "error_message": str(exc)[:300]}
            rows.append(row)
            checkpoint.write(canonical(row))
            checkpoint.flush()
    raw_rows = b"".join(canonical(row) for row in rows)
    if sha(PARSED.read_bytes()) != sha(raw_rows):
        raise RuntimeError("parse checkpoint bytes differ from completed rows")
    receipt = {
        "schema": "financial_text_v25_parse_receipt_v1", "run": "frozen_v1",
        "started": started, "finished": _now(), "preflight_sha256": sha(PREFLIGHT.read_bytes()),
        "private_output_sha256": sha(raw_rows), "attempted_documents": len(rows),
        "attempted_events": len(event_ids), "document_status_counts": dict(status_counts),
        "table_status_counts": dict(table_counts),
        "per_document": [{"document_id": row["document_id"], "event_group_id": row["event_group_id"],
                          "status": row["status"],
                          "table_status_counts": dict(Counter(t["status"] for t in row["output"]["table_inventory"]))
                          if row["status"] == "PARSED" else {},
                          "paragraph_candidates": len(row["output"]["paragraph_inventory"])
                          if row["status"] == "PARSED" else 0}
                         for row in rows],
        "new_downloads": 0, "model_fit_calls": 0, "model_inference_calls": 0,
        "return_evaluation_calls": 0,
        "semantic_claim_status": "NOT_SOURCE_REVIEWED_FULL_EIGHT_EVENT_PARSE",
    }
    _write_once(PARSE_RECEIPT, canonical(receipt))
    return receipt


def pack_once() -> dict:
    if PACKED.exists() or PACK_RECEIPT.exists():
        raise FileExistsError("frozen full pack already executed; preserve it")
    preflight, docs = _checked_preflight()
    if not PARSED.exists() or not PARSE_RECEIPT.exists():
        raise RuntimeError("full parse must be saved first")
    parse_receipt = _read_json(PARSE_RECEIPT)
    if sha(PARSED.read_bytes()) != parse_receipt["private_output_sha256"]:
        raise RuntimeError("full parse output hash changed")
    parsed = _read_lines(PARSED)
    by_doc = {r["document_id"]: r for r in parsed}
    if len(by_doc) != 16 or set(by_doc) != {d["document_id"] for d in docs}:
        raise RuntimeError("incomplete frozen parse ledger")
    groups: dict[str, list[dict]] = defaultdict(list)
    for d in docs:
        groups[d["event_group_id"]].append(d)
    rows: list[dict] = []
    reasons: Counter[str] = Counter()
    paragraph_reasons: Counter[str] = Counter()
    start = _now()
    for event_id, source_docs in groups.items():
        if any(by_doc[d["document_id"]]["status"] != "PARSED" for d in source_docs):
            rows.append({"event_group_id": event_id, "status": "PARSE_INCOMPLETE",
                         "missing_documents": [d["document_id"] for d in source_docs
                                               if by_doc[d["document_id"]]["status"] != "PARSED"]})
            continue
        try:
            parsed_docs = [by_doc[d["document_id"]]["output"] for d in source_docs]
            raw_by_doc = {d["document_id"]: Path(d["raw_body_path"]).read_bytes()
                          for d in source_docs}
            # Every packed group remains a diagnostic REJECTED_CANDIDATE.
            # Post-parse review may identify wrong relations but cannot alter
            # this one frozen selection rule or silently promote the input.
            event = assemble_event(parsed_docs, raw_by_doc,
                                   preflight["budget_unicode_characters_per_event"])
            validate_model_visible_event(event)
            reasons.update(event["omission_counts"])
            paragraph_reasons.update(p["omission_reason"] for p in event["paragraph_group_inventory"]
                                     if p.get("omission_reason"))
            rows.append({"event_group_id": event_id, "status": "PACKED_DIAGNOSTIC_REJECTED_CANDIDATE",
                         "output": event})
        except Exception as exc:
            rows.append({"event_group_id": event_id, "status": "PACK_FAILED",
                         "error_type": type(exc).__name__, "error_message": str(exc)[:300]})
    raw_rows = b"".join(canonical(r) for r in rows)
    _write_once(PACKED, raw_rows)
    receipt = {
        "schema": "financial_text_v25_pack_receipt_v1", "run": "frozen_v1",
        "started": start, "finished": _now(),
        "parse_receipt_sha256": sha(PARSE_RECEIPT.read_bytes()),
        "private_output_sha256": sha(raw_rows), "attempted_events": len(rows),
        "packed_events": sum(r["status"] == "PACKED_DIAGNOSTIC_REJECTED_CANDIDATE" for r in rows),
        "omission_counts": dict(reasons),
        "paragraph_omission_counts": dict(paragraph_reasons),
        "per_event": [{"event_group_id": r["event_group_id"], "status": r["status"],
                       "selected_text_characters": r["output"]["selected_text_characters"]
                       if "output" in r else None,
                       "selected_groups": len(r["output"]["selected_group_ids"])
                       if "output" in r else None,
                       "selected_paragraph_passages": len(r["output"]["selected_paragraph_ids"])
                       if "output" in r else None,
                       "paragraph_passages_considered": len(r["output"]["paragraph_group_inventory"])
                       if "output" in r else None,
                       "model_visible_text_sha256": r["output"]["model_visible_text_sha256"]
                       if "output" in r else None}
                      for r in rows],
        "promotion_status": "REJECTED_CANDIDATE_PENDING_SOURCE_SEMANTIC_REVIEW",
        "model_fit_calls": 0, "model_inference_calls": 0, "return_evaluation_calls": 0,
    }
    _write_once(PACK_RECEIPT, canonical(receipt))
    return receipt


def review_once() -> dict:
    if EVALUATION.exists() or EVALUATION_RECEIPT.exists():
        raise FileExistsError("frozen evaluation already executed; preserve it")
    preflight, _ = _checked_preflight()
    if not PARSED.exists() or not PARSE_RECEIPT.exists() or not PACKED.exists() or not PACK_RECEIPT.exists():
        raise RuntimeError("saved parse and pack required before evaluation")
    parse_receipt, pack_receipt = _read_json(PARSE_RECEIPT), _read_json(PACK_RECEIPT)
    if (sha(PARSED.read_bytes()) != parse_receipt["private_output_sha256"]
            or sha(PACKED.read_bytes()) != pack_receipt["private_output_sha256"]):
        raise RuntimeError("saved parse or pack bytes changed")
    if sha(REFERENCE.read_bytes()) != preflight["dependency_sha256"][REFERENCE.relative_to(ROOT).as_posix()]:
        raise RuntimeError("private frozen reference hash changed")
    ref = _read_json(REFERENCE)
    parsed = [r["output"] for r in _read_lines(PARSED) if r["status"] == "PARSED"]
    packed = [r["output"] for r in _read_lines(PACKED)
              if r["status"] == "PACKED_DIAGNOSTIC_REJECTED_CANDIDATE"]
    source_audit = _read_json(SOURCE_AUDIT) if SOURCE_AUDIT.exists() else None
    if source_audit is not None and source_audit.get("schema") != "financial_text_v25_source_audit_v1":
        raise RuntimeError("invalid source audit version")
    table_private, table_public = evaluate_frozen_tables(ref, parsed)
    candidate_private, candidate_public = evaluate_candidates(
        ref, parsed, packed, source_audit.get("candidate_decisions") if source_audit else None)
    private = {
        "schema": "financial_text_v25_evaluation_private_v1", "run": "frozen_v1",
        "at": _now(), "reference_sha256": sha(REFERENCE.read_bytes()),
        "source_audit_sha256": sha(SOURCE_AUDIT.read_bytes()) if source_audit else None,
        "parse_sha256": sha(PARSED.read_bytes()), "pack_sha256": sha(PACKED.read_bytes()),
        "frozen_table_review": table_private, "candidate_review": candidate_private,
    }
    raw_private = canonical(private)
    _write_once(EVALUATION, raw_private)
    public = {
        "schema": "financial_text_v25_evaluation_redacted_v1", "run": "frozen_v1",
        "at": private["at"], "reference_sha256": private["reference_sha256"],
        "source_audit_sha256": private["source_audit_sha256"],
        "private_output_sha256": sha(raw_private),
        "frozen_table_review": table_public, "candidate_review": candidate_public,
        "source_semantic_scope": "FOUR_OLD_TABLES_PLUS_EXPLICIT_POSTRUN_AUDIT_ONLY",
        "not_independent_test": True, "model_fit_calls": 0, "model_inference_calls": 0,
        "return_evaluation_calls": 0,
    }
    _write_once(EVALUATION_RECEIPT, canonical(public))
    return public


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("phase", choices=["parse", "pack", "review"])
    args = p.parse_args()
    receipt = (parse_once() if args.phase == "parse" else
               pack_once() if args.phase == "pack" else review_once())
    print(json.dumps({k: v for k, v in receipt.items() if k not in
                      {"per_document", "per_event", "frozen_table_review", "candidate_review"}},
                     ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
