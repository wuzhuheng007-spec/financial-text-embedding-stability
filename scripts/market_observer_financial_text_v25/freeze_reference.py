"""Freeze the private training-only evaluation reference for v2.5.

The reference is deliberately separate from production table discovery and
packing. Only an evaluator should read the private output. No source text or
financial values are written to the public receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


DOCUMENTS = "artifacts/market_observer_financial_text_v2_20261115/documents.private.json"
SOURCE_MANIFEST = "artifacts/market_observer_official_20261115/corpus_manifest.json"
CANDIDATES = "artifacts/market_observer_financial_text_v2_20261115/fact_cards/fact_candidates_v2.private.json"
REVIEW = "artifacts/market_observer_financial_text_v2_20261115/fact_cards/fact_review_v4.private.json"
RETENTION = "artifacts/market_observer_financial_text_v2_20261115/retention_comparison.private.json"
V24_REFERENCE = "artifacts/market_observer_financial_text_v24_20261116/expected_relations.private.json"
PRIVATE_OUTPUT = "artifacts/market_observer_financial_text_v25_20261116/reference_frozen_v1.private.json"
PUBLIC_RECEIPT = "results/market_observer_financial_text_v25_20261116/reference_freeze_redacted.json"


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load_json(repo: Path, relative: str) -> tuple[dict, str]:
    raw = (repo / relative).read_bytes()
    return json.loads(raw.decode("utf-8-sig")), sha256_bytes(raw)


def validate_document_hashes(repo: Path, documents: list[dict], manifest: list[dict]) -> None:
    """Require each frozen raw file to agree with both saved source ledgers."""
    source_by_id = {entry["document_id"]: entry for entry in manifest}
    if len(source_by_id) != len(manifest):
        raise ValueError("duplicate source manifest document_id")
    if len({entry["document_id"] for entry in documents}) != len(documents):
        raise ValueError("duplicate frozen document_id")
    for entry in documents:
        source = source_by_id.get(entry["document_id"])
        if source is None:
            raise ValueError("frozen document absent from source manifest")
        if entry["raw_body_sha256"] != source["raw_body_sha256"]:
            raise ValueError("source manifest raw hash disagreement")
        if entry["announcement_date"] != source["announcement_date"]:
            raise ValueError("source manifest date disagreement")
        if not "2025-04-01" <= entry["announcement_date"] <= "2026-03-31":
            raise ValueError("source document outside training window")
        raw_path = repo / entry["raw_body_path"]
        if not raw_path.is_file():
            raise FileNotFoundError("frozen source file missing")
        if sha256_bytes(raw_path.read_bytes()) != entry["raw_body_sha256"]:
            raise ValueError("frozen source file hash mismatch")


def assemble_reference(repo: Path) -> tuple[dict, dict]:
    documents_json, documents_sha = load_json(repo, DOCUMENTS)
    manifest_json, manifest_sha = load_json(repo, SOURCE_MANIFEST)
    candidates_json, candidates_sha = load_json(repo, CANDIDATES)
    review_json, review_sha = load_json(repo, REVIEW)
    retention_json, retention_sha = load_json(repo, RETENTION)
    v24_json, v24_sha = load_json(repo, V24_REFERENCE)

    if documents_json["manifest_sha256"] != manifest_sha:
        raise ValueError("frozen document manifest hash mismatch")
    if review_json["candidate_sha256"] != candidates_sha:
        raise ValueError("review candidate hash mismatch")
    if review_json["source_manifest_sha256"] != manifest_sha:
        raise ValueError("review source manifest hash mismatch")

    documents = documents_json["documents"]
    validate_document_hashes(repo, documents, manifest_json["documents"])
    if len(documents) != 16 or len({d["event_group_id"] for d in documents}) != 8:
        raise ValueError("unexpected frozen training document/event count")
    document_by_id = {d["document_id"]: d for d in documents}
    source_shas = {d["raw_body_sha256"] for d in documents}

    candidates = candidates_json["candidates"]
    reviews = review_json["reviews"]
    retained = retention_json["fact_rows"]
    ids = [c["candidate_id"] for c in candidates]
    if len(ids) != 71 or len(set(ids)) != 71:
        raise ValueError("unexpected candidate identifiers")
    review_by_id = {r["candidate_id"]: r for r in reviews}
    retained_by_id = {r["candidate_id"]: r for r in retained}
    if len(review_by_id) != 71 or len(retained_by_id) != 71:
        raise ValueError("duplicate or missing review/retention identifiers")
    if set(ids) != set(review_by_id) or set(ids) != set(retained_by_id):
        raise ValueError("candidate, review, and retention IDs disagree")
    if Counter(r["status"] for r in reviews) != {
        "CODEX_REVIEW_SOURCE_SUPPORTED": 60,
        "CODEX_REVIEW_AMBIGUOUS": 11,
    }:
        raise ValueError("source review denominator changed")

    old_complete_ids = sorted(
        r["candidate_id"] for r in retained
        if r["fact_retention"]["old"] == "COMPLETE"
    )
    if len(old_complete_ids) != 34:
        raise ValueError("old complete fragment denominator changed")
    if any(review_by_id[cid]["status"] != "CODEX_REVIEW_SOURCE_SUPPORTED"
           for cid in old_complete_ids):
        raise ValueError("old complete fragment has unsupported source status")

    frozen_candidates = []
    for candidate in candidates:
        cid = candidate["candidate_id"]
        review = review_by_id[cid]
        source_document_id = candidate["source_document_id"]
        source = document_by_id.get(source_document_id)
        if source is None or candidate["source_document_sha256"] not in source_shas:
            raise ValueError("candidate source absent from frozen documents")
        if candidate["source_document_sha256"] != source["raw_body_sha256"]:
            raise ValueError("candidate source document hash disagreement")
        if review["final"]["source_document_sha256"] != source["raw_body_sha256"]:
            raise ValueError("review final source document hash disagreement")
        if candidate["event_id"] != review["event_id"] != source["event_group_id"]:
            raise ValueError("candidate event identity disagreement")
        if candidate["symbol"] != review["symbol"] != source["symbol"]:
            raise ValueError("candidate symbol disagreement")
        if candidate["source_url"] != source["source_url"]:
            raise ValueError("candidate source URL disagreement")
        frozen_candidates.append({
            "candidate_id": cid,
            "event_id": candidate["event_id"],
            "symbol": candidate["symbol"],
            "topic": candidate["topic"],
            "source_document_id": source_document_id,
            "source_document_sha256": source["raw_body_sha256"],
            "source_url": source["source_url"],
            "review_status": review["status"],
            "review_v4": review,
            "old_fragment_complete": cid in old_complete_ids,
            "footnote_reference_status": "UNREVIEWED_OUTSIDE_V24_CELL_REFERENCE",
        })

    v24_tables = v24_json["expected_tables"]
    if len(v24_tables) != 4 or sum(len(t["expected_value_cells"]) for t in v24_tables) != 138:
        raise ValueError("prior four-table reference denominator changed")
    for table in v24_tables:
        source = document_by_id.get(table["document_id"])
        if source is None or source["raw_body_sha256"] != table["raw_body_sha256"]:
            raise ValueError("v2.4 table source hash disagreement")

    now = datetime.now(timezone.utc)
    source_fingerprint = {
        DOCUMENTS: documents_sha,
        SOURCE_MANIFEST: manifest_sha,
        CANDIDATES: candidates_sha,
        REVIEW: review_sha,
        RETENTION: retention_sha,
        V24_REFERENCE: v24_sha,
    }
    private = {
        "schema": "market_observer_financial_text_v25_private_reference_v1",
        "frozen_at_utc": now.isoformat().replace("+00:00", "Z"),
        "frozen_at_asia_shanghai": now.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
        "review_role": "CODEX_REFERENCE_DRAFT_ON_SEEN_TRAINING_MATERIAL",
        "independent_gold": False,
        "source_window": {"start": "2025-04-01", "end": "2026-03-31"},
        "source_files_sha256": source_fingerprint,
        "source_documents": documents,
        "candidates": frozen_candidates,
        "old_complete_candidate_ids": old_complete_ids,
        "v24_four_table_reference": v24_json,
        "footnote_policy": "Prior v2.4 table expectations are preserved unchanged; other candidate footnotes are unreviewed until explicit source review. A missing field is never automatically NOT_APPLICABLE.",
        "production_access_forbidden": True,
    }
    public = {
        "schema": "market_observer_financial_text_v25_reference_freeze_receipt_v1",
        "frozen_at_utc": private["frozen_at_utc"],
        "frozen_at_asia_shanghai": private["frozen_at_asia_shanghai"],
        "role": private["review_role"],
        "independent_gold": False,
        "training_events": 8,
        "training_documents_hash_verified": 16,
        "candidates": 71,
        "source_supported": 60,
        "source_ambiguous": 11,
        "old_fragment_complete": 34,
        "v24_prior_reviewed_tables": 4,
        "v24_prior_reviewed_numeric_cells": 138,
        "source_files_sha256": source_fingerprint,
        "private_reference_path_local_only": PRIVATE_OUTPUT,
    }
    return private, public


def freeze(repo: Path) -> dict:
    private, public = assemble_reference(repo)
    private_path = repo / PRIVATE_OUTPUT
    public_path = repo / PUBLIC_RECEIPT
    if private_path.exists() or public_path.exists():
        raise FileExistsError("v2.5 reference already frozen; no in-place overwrite")
    private_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    private_raw = (json.dumps(private, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    public["private_reference_sha256"] = sha256_bytes(private_raw)
    public_raw = (json.dumps(public, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with private_path.open("xb") as out:
        out.write(private_raw)
    try:
        with public_path.open("xb") as out:
            out.write(public_raw)
    except BaseException:
        private_path.unlink()
        raise
    return public


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    receipt = freeze(args.repo)
    print(json.dumps({
        "status": "FROZEN",
        "public_receipt": PUBLIC_RECEIPT,
        "private_reference_sha256": receipt["private_reference_sha256"],
        "training_events": receipt["training_events"],
        "training_documents_hash_verified": receipt["training_documents_hash_verified"],
        "candidates": receipt["candidates"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
