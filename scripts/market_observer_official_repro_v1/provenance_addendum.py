"""Post-run inventory of auxiliary inputs omitted from P1 preflight hashing.

This read-only audit runs after the one allowed model reproduction. It does not
relabel its observed hashes as if they were captured before fitting.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from scripts.market_observer_official_v1 import text_comparison as tc


ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "artifacts/market_observer_official_repro_20261115"
PUBLIC = ROOT / "results/market_observer_official_repro_20261115"
SOURCE_CONFIG = ROOT / "artifacts/market_observer_historical_20261115/run_config.json"
MANIFEST = ROOT / "artifacts/market_observer_official_20261115/corpus_manifest.json"
OLD_PRICE = ROOT / "results/market_observer_historical_20261115/price_direction_v3_redacted.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    preflight = json.loads((PRIVATE / "preflight.private.json").read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    old_price = json.loads(OLD_PRICE.read_text(encoding="utf-8"))
    source_hash = sha(SOURCE_CONFIG)
    prior_source_hash = old_price["config"]["source_request_sha256"]
    body_entries: list[dict] = []
    for doc in manifest["documents"]:
        if tc._symbol(doc.get("symbol")) not in tc.SYMBOLS or not tc._sec_archive_document(doc):
            continue
        try:
            d = tc.sec_proxy_announcement_date(doc)
        except (KeyError, TypeError, ValueError):
            continue
        if d > tc.VALIDATION_END or not doc.get("event_id"):
            continue
        if doc.get("version_status") != "SEC_ARCHIVE_HASHED" or doc.get("full_body_status") != "FULL_BODY":
            continue
        path = Path(doc["body_path"])
        body_entries.append({"document_id": doc["document_id"],
                             "source_path": str(path),
                             "current_sha256": sha(path),
                             "manifest_sha256": doc["body_sha256"],
                             "matches_manifest": sha(path) == doc["body_sha256"]})
    receipt = {
        "schema": "market_observer_official_repro_provenance_addendum_v1",
        "run_id": preflight["run_id"],
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "timing": "POST_RUN_READ_ONLY_AUDIT_NOT_PRE_FIT_HASH_CAPTURE",
        "preflight_scope": "frozen model/reproduction configs, corpus manifest, calendar, old receipt, NVDA/MU price responses, executed code",
        "omitted_auxiliary_input_at_preflight": {
            "historical_price_request_config_sha256_now": source_hash,
            "prior_historical_price_receipt_source_request_sha256": prior_source_hash,
            "matches_prior_receipt": source_hash == prior_source_hash,
        },
        "per_document_sec_text_at_preflight": "NOT_INDIVIDUALLY_HASHED_BEFORE_FIT",
        "per_document_sec_text_runtime": "runner verified each eligible body SHA-256 against frozen corpus manifest before using its text",
        "eligible_sec_text_body_count_post_run": len(body_entries),
        "eligible_sec_text_body_hashes_match_manifest_post_run": all(e["matches_manifest"] for e in body_entries),
        "other_repo_input_files_omitted": [],
        "runtime_dependency_binary_and_timezone_db_hashes": "NOT_CAPTURED; versions/path recorded in preflight",
        "estimator_fit_calls_in_addendum": 0,
    }
    private = dict(receipt)
    private["per_document_details"] = body_entries
    (PRIVATE / "provenance_addendum.private.json").write_text(
        json.dumps(private, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (PUBLIC / "provenance_addendum_redacted.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not receipt["eligible_sec_text_body_hashes_match_manifest_post_run"] or source_hash != prior_source_hash:
        raise AssertionError("post-run auxiliary source version mismatch")
    print(json.dumps({"eligible_sec_text_body_count": len(body_entries),
                      "hashes_match_manifest": True, "source_request_matches_prior": True,
                      "estimator_fit_calls": 0}, sort_keys=True))


if __name__ == "__main__":
    main()
