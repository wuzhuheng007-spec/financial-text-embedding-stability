"""Record code and training-only input identities before each v2 content stage.

The SEC manifest contains later metadata, but this tool opens raw bodies only
after selecting archived training-period earnings accessions.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs/market_observer_financial_text_v2.json"
MANIFEST = ROOT / "artifacts/market_observer_official_20261115/corpus_manifest.json"
OUT = ROOT / "results/market_observer_financial_text_v2_20261115"
PRIVATE = ROOT / "artifacts/market_observer_financial_text_v2_20261115"
START, END = "2025-04-01", "2026-03-31"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_bytes(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["checklist", "checklist_review", "input_v2", "input_v2_r1", "facts", "facts_r1", "facts_review", "facts_review_r1", "facts_review_r2", "facts_review_r3", "facts_review_r4", "retention", "retention_r1", "retention_r2", "retention_r3", "retention_r4", "interface", "interface_r1"])
    args = parser.parse_args()
    target = OUT / f"preflight_{args.phase}_redacted.json"
    if target.exists():
        raise FileExistsError(target)
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    assert cfg["source_scope"]["announcement_date_start"] == START
    assert cfg["source_scope"]["announcement_date_end"] == END
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    exhibits = sorted(
        (d for d in manifest["documents"]
         if d.get("source_kind") == "ISSUER_EARNINGS"
         and d.get("symbol") in {"NVDA", "MU"}
         and START <= str(d.get("announcement_date", "")) <= END),
        key=lambda d: (d["announcement_date"], d["symbol"], d["event_id"], d["document_id"]),
    )
    assert len(exhibits) == cfg["source_scope"]["expected_earnings_exhibits_before_run"]
    accession_ids = {d["event_id"] for d in exhibits}
    primary = sorted(
        (d for d in manifest["documents"]
         if d.get("source_kind") == "SEC_FILINGS"
         and d.get("event_id") in accession_ids
         and d.get("symbol") in {"NVDA", "MU"}
         and START <= str(d.get("announcement_date", "")) <= END),
        key=lambda d: (d["announcement_date"], d["symbol"], d["event_id"], d["document_id"]),
    )
    assert len(primary) == len(exhibits) == cfg["source_scope"]["expected_matched_primary_filings_before_run"]
    assert len({d["event_id"] for d in primary}) == len(accession_ids)
    inputs = []
    for doc in sorted(exhibits + primary, key=lambda d: (d["announcement_date"], d["symbol"], d["document_id"])):
        assert doc["full_body_status"] == "FULL_BODY"
        assert doc["version_status"] == "SEC_ARCHIVE_HASHED"
        raw = Path(doc["raw_body_path"])
        body = Path(doc["body_path"])
        assert raw.is_file() and body.is_file()
        raw_hash = sha(raw.read_bytes())
        body_hash = sha(body.read_bytes())
        assert raw_hash == doc["raw_body_sha256"] and body_hash == doc["body_sha256"]
        inputs.append({"document_id": doc["document_id"], "symbol": doc["symbol"],
                       "announcement_date": doc["announcement_date"],
                       "source_kind": doc["source_kind"],
                       "raw_sha256": raw_hash, "old_extracted_sha256": body_hash})
    file_paths = [CONFIG, MANIFEST,
                  ROOT / "configs/market_observer_official_text_v1.json",
                  ROOT / "scripts/market_observer_official_v1/text_comparison.py",
                  ROOT / "scripts/market_observer_official_v1/build_corpus.py",
                  ROOT / "scripts/market_observer_official_repro_v1/text_input_audit.py",
                  ROOT / "scripts/market_observer_historical_v1/direction_baseline.py",
                  ROOT / "scripts/market_observer_historical_v1/price_baseline.py",
                  ROOT / "results/market_observer_official_repro_20261115/text_input_audit_redacted.json",
                  ROOT / "results/market_observer_official_repro_20261115/incremental_redacted.json",
                  ROOT / "results/market_observer_historical_20261115/test_contamination_redacted.json",
                  ROOT / "tests/test_market_observer_financial_text_v2.py",
                  ROOT / "tests/test_market_observer_financial_fact_cards_v2.py",
                  ROOT / "tests/test_market_observer_financial_fact_cards_review_v2.py",
                  ROOT / "tests/test_market_observer_financial_fact_comparisons.py",
                  ROOT / "tests/test_market_observer_financial_fact_comparisons_v4.py",
                  ROOT / "tests/test_market_observer_financial_text_retention.py"]
    file_paths.extend(sorted((ROOT / "scripts/market_observer_financial_text_v2").glob("*.py")))
    private_inputs = [PRIVATE / "fact_cards/content_checklist_frozen.private.json",
                      PRIVATE / "fact_cards/content_checklist_reviewed.private.json",
                      PRIVATE / "blocks.private.jsonl",
                      PRIVATE / "event_inputs.private.jsonl",
                      PRIVATE / "documents.private.json",
                      PRIVATE / "fact_cards/fact_candidates.private.json",
                      PRIVATE / "fact_cards/fact_candidates_v2.private.json",
                      PRIVATE / "fact_cards/fact_review.private.json",
                      PRIVATE / "fact_cards/fact_review_v2.private.json",
                      PRIVATE / "fact_cards/fact_review_v3.private.json",
                      PRIVATE / "fact_cards/fact_review_v4.private.json",
                      ROOT / "artifacts/market_observer_official_text_audit_20261115/aggregate_rows.private.jsonl"]
    required_private = {
        "checklist_review": [private_inputs[0]],
        "facts": [private_inputs[0], private_inputs[1], private_inputs[2]],
        "facts_r1": [private_inputs[0], private_inputs[1], private_inputs[2], private_inputs[5]],
        "facts_review": [private_inputs[0], private_inputs[1], private_inputs[2], private_inputs[6]],
        "facts_review_r1": [private_inputs[0], private_inputs[1], private_inputs[2], private_inputs[6], private_inputs[7]],
        "facts_review_r2": [private_inputs[0], private_inputs[1], private_inputs[2], private_inputs[6], private_inputs[8]],
        "facts_review_r3": [private_inputs[0], private_inputs[1], private_inputs[2], private_inputs[6], private_inputs[8]],
        "facts_review_r4": [private_inputs[0], private_inputs[1], private_inputs[2], private_inputs[6], private_inputs[8]],
        "retention": [private_inputs[0], private_inputs[1], private_inputs[2], private_inputs[3],
                      private_inputs[6], private_inputs[7]],
        "retention_r1": [private_inputs[0], private_inputs[1], private_inputs[2], private_inputs[3],
                         private_inputs[6], private_inputs[8]],
        "retention_r2": [private_inputs[0], private_inputs[1], private_inputs[2], private_inputs[3],
                         private_inputs[6], private_inputs[9]],
        "retention_r3": [private_inputs[0], private_inputs[1], private_inputs[2], private_inputs[3],
                         private_inputs[6], private_inputs[10], private_inputs[11]],
        "retention_r4": [private_inputs[0], private_inputs[1], private_inputs[2], private_inputs[3],
                         private_inputs[6], private_inputs[10], private_inputs[11]],
        "interface": [private_inputs[2], private_inputs[3]],
        "interface_r1": [private_inputs[2], private_inputs[3]],
    }
    for p in required_private.get(args.phase, []):
        if not p.is_file():
            raise FileNotFoundError(p)
    file_paths.extend(p for p in private_inputs if p.is_file())
    hashes = {p.relative_to(ROOT).as_posix(): sha(p.read_bytes()) for p in file_paths if p.is_file()}
    for p in file_paths:
        if not p.is_file():
            raise FileNotFoundError(p)
    status = git_bytes("status", "--porcelain=v1")
    receipt = {
        "schema": "market_observer_financial_text_v2_preflight_v1",
        "phase": args.phase,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_head": git_bytes("rev-parse", "HEAD").decode().strip(),
        "branch": git_bytes("branch", "--show-current").decode().strip(),
        "tracked_diff_sha256": sha(git_bytes("diff", "--binary")),
        "staged_diff_sha256": sha(git_bytes("diff", "--cached", "--binary")),
        "workspace_status_sha256": sha(status),
        "workspace_status_entry_count": len(status.splitlines()),
        "code_config_and_receipt_sha256": hashes,
        "source_documents": inputs,
        "source_document_count": len(inputs),
        "earnings_exhibit_count": len(exhibits),
        "associated_primary_count": len(primary),
        "runtime": {"python": platform.python_version(),
                    "beautifulsoup4": importlib.metadata.version("beautifulsoup4"),
                    "numpy": importlib.metadata.version("numpy")},
        "no_new_downloads": True,
        "no_validation_or_test_bodies_or_labels_opened": True,
        "no_model_fit": True,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"phase": args.phase, "source_document_count": len(inputs),
                      "earnings_exhibit_count": len(exhibits),
                      "receipt": str(target.relative_to(ROOT)), "sha256": sha(target.read_bytes())}))


if __name__ == "__main__":
    main()
