"""Validate saved original bytes, extracted text and a fixed-order identity sample."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .build_corpus import PRIVATE, PUBLIC, write_json


def digest(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main() -> None:
    manifest_path = PRIVATE / "corpus_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    documents = manifest["documents"]
    failures = []
    for row in documents:
        if row["full_body_status"] != "FULL_BODY":
            failures.append({"document_id": row["document_id"], "reason": "body missing"})
            continue
        if digest(row["raw_body_path"]) != row["raw_body_sha256"]:
            failures.append({"document_id": row["document_id"], "reason": "raw hash mismatch"})
        if digest(row["body_path"]) != row["body_sha256"]:
            failures.append({"document_id": row["document_id"], "reason": "text hash mismatch"})
        if row["source_kind"] in {"SEC_FILINGS", "ISSUER_EARNINGS"}:
            if row["sec_accession"].replace("-", "") not in row["source_url"]:
                failures.append({"document_id": row["document_id"], "reason": "accession URL mismatch"})
            accepted = datetime.fromisoformat(row["sec_acceptance_at"].replace("Z", "+00:00"))
            et_date = accepted.astimezone(ZoneInfo("America/New_York")).date().isoformat()
            if et_date != row["sec_acceptance_date_et"] or row["announcement_date"] != max(row["sec_filing_date"], et_date):
                failures.append({"document_id": row["document_id"], "reason": "SEC date mismatch"})
    sample = []
    for kind in ("SEC_FILINGS", "ISSUER_EARNINGS", "FOMC_STATEMENTS"):
        rows = [x for x in documents if x["source_kind"] == kind]
        for row in (rows[0], rows[-1]):
            text = Path(row["body_path"]).read_text(encoding="utf-8").lower()
            if kind == "SEC_FILINGS":
                identity = "securities and exchange commission" in text and (
                    "nvidia" in text if row["symbol"] == "NVDA" else "micron" in text
                )
            elif kind == "ISSUER_EARNINGS":
                identity = ("nvidia" in text if row["symbol"] == "NVDA" else "micron" in text) and "financial results" in text and "revenue" in text
            else:
                identity = ("fomc statement" in text and
                            row["announcement_date"].split("-")[0] in text and
                            "for release at" in text and "federal funds rate" in text)
            sample.append({"document_id": row["document_id"], "source_kind": kind,
                           "identity_markers_present": identity, "raw_sha256": row["raw_body_sha256"],
                           "text_sha256": row["body_sha256"]})
            if not identity:
                failures.append({"document_id": row["document_id"], "reason": "fixed-order identity sample failed"})
    exhibits = [x for x in documents if x["source_kind"] == "ISSUER_EARNINGS"]
    exhibit_checks = []
    for row in exhibits:
        text = Path(row["body_path"]).read_text(encoding="utf-8").lower()
        relevant = all(marker in text for marker in ("financial results", "revenue")) and (
            "outlook" in text or "guidance" in text
        )
        exhibit_checks.append({"document_id": row["document_id"], "earnings_and_outlook_markers_present": relevant})
        if not relevant:
            failures.append({"document_id": row["document_id"], "reason": "earnings exhibit marker check failed"})
    receipt = {
        "schema_version": "official_corpus_validation_v1",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "documents_checked": len(documents),
        "raw_hashes_checked": len(documents),
        "extracted_text_hashes_checked": len(documents),
        "fixed_order_identity_samples": sample,
        "earnings_exhibit_checks": exhibit_checks,
        "failures": failures,
        "status": "PASS" if not failures else "FAIL",
        "identity_check_limit": "Keyword and hash verification of saved bodies, not independent verification of historical public availability or source completeness"
    }
    write_json(PUBLIC / "corpus_validation_redacted.json", receipt)
    if failures:
        raise SystemExit(f"Validation failed for {len(failures)} items")
    print(json.dumps({"status": receipt["status"], "documents_checked": len(documents),
                      "fixed_order_samples": len(sample), "earnings_exhibits": len(exhibits)}))


if __name__ == "__main__":
    main()
