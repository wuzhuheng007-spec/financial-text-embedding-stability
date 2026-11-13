"""Input-bound readback verification of the bounded M5 real candidate ledger."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from finembed.extract import primary_text


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def get_cache(cache: Path, url: str) -> bytes:
    key = sha(url.encode("utf-8"))
    body = (cache / f"{key}.bin").read_bytes()
    sidecar = json.loads((cache / f"{key}.json").read_text(encoding="utf-8"))
    assert sidecar["url"] == url and sidecar["sha256"] == sha(body) and sidecar["bytes"] == len(body)
    return body


def verify(out: Path) -> dict:
    ledger_path = out / "candidate_events_private.json"
    ledger_bytes = ledger_path.read_bytes()
    ledger = json.loads(ledger_bytes)
    scope_path = out / "discovery_scope_manifest_private.json"
    scope = json.loads(scope_path.read_text(encoding="utf-8"))
    selection_path = out / "original_filing_selection_manifest_private.json"
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    safe = json.loads((out / "candidate_discovery_summary_safe.json").read_text(encoding="utf-8"))
    cache = out / "sec_cache_private"
    assert safe["private_ledger_sha256"] == sha(ledger_bytes)
    assert ledger["scope_manifest_sha256"] == sha(scope_path.read_bytes())
    assert selection["scope_manifest_sha256"] == ledger["scope_manifest_sha256"]
    assert len(scope["ciks"]) == 32 and len(ledger["scan_records"]) == 32
    assert all(x["scan_complete_for_window"] for x in ledger["scan_records"])
    assert len(selection["selected"]) <= 20
    assert selection["selected"] == [
        {"accession": r["accession"], "cik": r["cik"], "filing_date": r["filing_date"],
         "source_evidence_hash": r["source_evidence_hash"], "original_filing_url": r["original_filing_url"]}
        for r in ledger["nondevelopment_candidate_rows"][:20]]

    source_bodies: dict[str, bytes] = {}
    source_rows_checked = 0
    keys = set()
    for row in ledger["nondevelopment_candidate_rows"]:
        identity = (row["accession"], row["source_document_id"])
        assert identity not in keys
        keys.add(identity)
        assert row["cik"] in scope["ciks"] and not row["development_only"]
        assert row["security_id"] is None and row["formal_admitted"] is False
        assert row["item1_hash"] is None or row["text_status"] == "supported"
        url = row["source_locator"]
        if url not in source_bodies:
            source_bodies[url] = get_cache(cache, url)
        body = source_bodies[url]
        assert sha(body) == row["source_evidence_hash"]
        source = json.loads(body)
        columns = source.get("filings", {}).get("recent") if "filings" in source else source
        i = row["source_row_index"]
        assert columns["accessionNumber"][i] == row["accession"]
        assert columns["filingDate"][i] == row["filing_date"]
        assert columns["form"][i] == row["form"] == "10-K"
        assert (columns["reportDate"][i] or None) == row["fiscal_period"]
        source_rows_checked += 1

    dev_inventory_path = Path(scope["development_inventory_path"])
    dev_inventory_bytes = dev_inventory_path.read_bytes()
    assert sha(dev_inventory_bytes) == scope["development_inventory_sha256"]
    dev_inventory = json.loads(dev_inventory_bytes)
    prior_rows = {(f["cik"], r["accessionNumber"]): r
                  for f in dev_inventory["firms"].values() for r in f["rows"]}
    dev_source_hashes = {x["body_sha256"] for x in dev_inventory["sources"]}
    for row in ledger["development_only_rows"]:
        assert row["development_only"] and row["formal_admitted"] is False
        assert row["security_id"] is None and row["candidate_security_ids"] == []
        assert row["cik"] in scope["development_ciks"]
        prior = prior_rows[(row["cik"], row["accession"])]
        assert prior["filingDate"] == row["filing_date"]
        assert prior["source_body_sha256"] == row["source_evidence_hash"] in dev_source_hashes

    selected = {(x["cik"], x["accession"]) for x in selection["selected"]}
    document_count = 0
    text_count = 0
    header_status = Counter()
    text_status = Counter()
    heading_templates = Counter()
    for row in ledger["nondevelopment_candidate_rows"]:
        if (row["cik"], row["accession"]) not in selected:
            assert row["source_document_hash"] is None and row["item1_hash"] is None
            continue
        raw = get_cache(cache, row["original_filing_url"])
        assert row["raw_submission_sha256"] == sha(raw) == row["source_document_hash"]
        document_count += 1
        header_status[(row["raw_header_check"] or {}).get("status", "not_checked")] += 1
        text_status[row["text_status"]] += 1
        if row["item1_hash"]:
            path = Path(row["extraction_locator"]["text_path_private"])
            item = path.read_bytes()
            assert sha(item) == row["item1_hash"]
            rendered, _ = primary_text(raw)
            loc = row["extraction_locator"]["item1"]
            assert rendered[loc["start_char"]:loc["end_char_exclusive"]].encode("utf-8") == item
            heading_templates[loc["heading_template"]] += 1
            text_count += 1
    assert document_count == len(selected)
    assert text_count == safe["item1_extraction_supported_count"]
    assert all(x["formal_admitted"] is False for x in ledger["nondevelopment_candidate_rows"])

    return {"kind": "M5_CANDIDATE_DISCOVERY_INDEPENDENT_READBACK_SAFE", "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "ledger_sha256": sha(ledger_bytes), "scope_manifest_sha256": sha(scope_path.read_bytes()),
            "selection_manifest_sha256": sha(selection_path.read_bytes()),
            "nondevelopment_metadata_rows_revalidated": source_rows_checked,
            "distinct_live_sec_json_bodies_rehashed": len(source_bodies),
            "development_inventory_rows_revalidated": len(ledger["development_only_rows"]),
            "original_complete_submissions_rehashed": document_count,
            "item1_bytes_hash_and_source_offsets_revalidated": text_count,
            "raw_header_status_counts": dict(header_status), "text_status_counts": dict(text_status),
            "item1_heading_template_counts": dict(heading_templates),
            "formal_admitted_count": 0, "all_checks_passed": True,
            "development_source_caveat": "Development source JSON bodies are not in this run cache; prior source hashes and row values were rechecked against the saved, previously hash-verified inventory."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "artifacts" / "m5_next_20261114_0325")
    args = parser.parse_args()
    result = verify(args.out)
    path = args.out / "candidate_discovery_verify_safe.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
