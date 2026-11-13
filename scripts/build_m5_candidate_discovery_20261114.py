"""Bounded SEC filing discovery for M5; candidate registration is not admission.

Run in three explicit stages so that the CIK scope and the <=20-document
selection are frozen before the corresponding SEC requests. Private JSON files
contain security candidates and must not be placed in the shareable ZIP.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from finembed.config import load_config
from finembed.extract import ExtractionError, comparison, extract_item1, primary_text
from finembed.sec import SecClient, SecError, _rows, contact_from_environment, raw_filer_cik, raw_header, submission_url

PUBLISH = ROOT.parent / "financial-text-embedding-stability-publish" / "artifacts"
SCREEN = PUBLISH / "m5_preparation_candidate_20261113_v1" / "m5_preparation_candidate_screen_private.json"
DEV_INVENTORY = PUBLISH / "round8" / "sec_metadata_inventory_v1.json"
DEFAULT_OUT = ROOT / "artifacts" / "m5_next_20261114_0325"
DEVELOPMENT_CIKS = {"0000320193", "0000789019", "0001018724", "0001652044", "0001326801"}
START_DATE, END_DATE = "2020-01-01", "2025-12-31"
SCOPE_SIZE, ORIGINAL_DOWNLOAD_CAP = 32, 20
ACCESSION = re.compile(r"\d{10}-\d{2}-\d{6}\Z")
CIK = re.compile(r"\d{10}\Z")
HISTORY = re.compile(r"CIK\d{10}-submissions-\d{3}\.json\Z")
STATUSES = {"supported", "unsupported", "ambiguous", "not_checked", "not_applicable"}


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def body_sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def save(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def candidate_relations(screen: dict) -> dict[str, list[dict]]:
    issues = {x["issue_evidence_id"]: x for x in screen["issue_evidence"]
              if x.get("starting_snapshot_membership") is True}
    by_cik: dict[str, list[dict]] = defaultdict(list)
    for edge in screen["cik_observation_edge"]:
        if edge.get("quality_screen_status") != "CANDIDATE_SCREEN_PASS" or edge.get("accepted") is not False:
            continue
        cik = edge.get("cik")
        issue = issues.get(edge.get("issue_evidence_id"))
        if not isinstance(cik, str) or not CIK.fullmatch(cik) or not issue:
            continue
        by_cik[cik].append({"issue_evidence_id": edge["issue_evidence_id"],
                            "identity_candidate_id": edge["identity_candidate_id"],
                            "source_record_id": edge.get("source_record_id"),
                            "permno": issue.get("permno"), "permco": issue.get("permco"),
                            "security_record_id": issue.get("security_record_id"),
                            "historical_link_validity_established": False})
    return {cik: sorted(v, key=lambda x: (x["issue_evidence_id"], x["identity_candidate_id"]))
            for cik, v in by_cik.items()}


def select_scope(relations: dict[str, list[dict]], n: int = SCOPE_SIZE) -> list[str]:
    eligible = (c for c in relations if c not in DEVELOPMENT_CIKS and relations[c])
    return sorted(eligible, key=lambda c: (hashlib.sha256(("m5_discovery_20261114_v1:" + c).encode()).hexdigest(), c))[:n]


def freeze(out: Path, screen_path: Path = SCREEN, dev_path: Path = DEV_INVENTORY) -> dict:
    target = out / "discovery_scope_manifest_private.json"
    if target.exists():
        raise ValueError(f"Frozen scope already exists: {target}")
    screen, dev = read(screen_path), read(dev_path)
    relations = candidate_relations(screen)
    scope = select_scope(relations)
    if len(scope) != SCOPE_SIZE:
        raise ValueError(f"Only {len(scope)} eligible nondevelopment CIKs; expected {SCOPE_SIZE}")
    if set(scope) & DEVELOPMENT_CIKS:
        raise AssertionError("Development CIK entered nondevelopment scope")
    screen_hash, dev_hash = file_sha(screen_path), file_sha(dev_path)
    scope_id = body_sha(json.dumps({"ciks": scope, "screen_sha256": screen_hash, "window": [START_DATE, END_DATE]},
                                   sort_keys=True).encode())
    value = {"kind": "M5_CANDIDATE_DISCOVERY_SCOPE_ONLY", "frozen_at_utc": now(),
             "scan_scope_id": scope_id, "selection_rule": "first 32 SHA-256 rank of m5_discovery_20261114_v1:+CIK among CANDIDATE_SCREEN_PASS fixed-start CIKs, excluding five development CIKs",
             "scope_size": SCOPE_SIZE, "ciks": scope, "filing_date_from": START_DATE,
             "filing_date_through": END_DATE, "form": "original 10-K only",
             "screen_private_path": str(screen_path), "screen_private_sha256": screen_hash,
             "development_inventory_path": str(dev_path), "development_inventory_sha256": dev_hash,
             "development_ciks": sorted(DEVELOPMENT_CIKS),
             "candidate_relations": {c: relations[c] for c in scope},
             "candidate_relation_caveat": "Undated screened identity candidates; no event-time security mapping or formal admission.",
             "exhaustive_full_cohort": False, "max_original_filing_downloads": ORIGINAL_DOWNLOAD_CAP}
    save(target, value)
    return value


def check_frozen(out: Path) -> tuple[dict, str]:
    path = out / "discovery_scope_manifest_private.json"
    scope = read(path)
    if file_sha(Path(scope["screen_private_path"])) != scope["screen_private_sha256"]:
        raise ValueError("Screen input changed after scope freeze")
    if file_sha(Path(scope["development_inventory_path"])) != scope["development_inventory_sha256"]:
        raise ValueError("Development inventory changed after scope freeze")
    if scope["ciks"] != select_scope(candidate_relations(read(Path(scope["screen_private_path"])))):
        raise ValueError("Frozen CIK scope does not reproduce")
    return scope, file_sha(path)


def sec_client(out: Path) -> SecClient:
    # The actual contact is intentionally never serialized or logged.
    ua = contact_from_environment()
    config = replace(load_config(ROOT / "configs" / "smoke.yaml"),
                     cache=out / "sec_cache_private", min_interval_seconds=0.5,
                     max_attempts=2, timeout_seconds=30)
    return SecClient(config, ua)


def history_overlaps(file: dict) -> bool:
    left, right = file.get("filingFrom"), file.get("filingTo")
    # Unknown bounds must be fetched to avoid silently truncating the window.
    if not isinstance(left, str) or not isinstance(right, str):
        return True
    return left <= END_DATE and right >= START_DATE


def filing_row(cik: str, raw: dict, source: dict, source_index: int, scope: dict,
               scope_hash: str, relations: list[dict], development: bool) -> dict | None:
    if raw.get("form") != "10-K":
        return None
    accession, filing_date = raw.get("accessionNumber"), raw.get("filingDate")
    if not isinstance(accession, str) or not ACCESSION.fullmatch(accession):
        raise ValueError(f"Malformed SEC accession for CIK {cik}")
    if not isinstance(filing_date, str) or not START_DATE <= filing_date <= END_DATE:
        return None
    datetime.fromisoformat(filing_date)
    url = source["url"]
    source_hash = source["sha256"]
    if not re.fullmatch(r"[0-9a-f]{64}", source_hash):
        raise ValueError("Missing source body hash")
    archive = submission_url(cik, accession)
    permnos = sorted({r["permno"] for r in relations if r.get("permno")})
    report_date = raw.get("reportDate") or None
    if report_date is not None:
        datetime.fromisoformat(report_date)
    row = {"accession": accession, "cik": cik, "filing_date": filing_date, "form": "10-K",
           "fiscal_period": report_date, "fiscal_period_semantics": "SEC submissions reportDate" if report_date else None,
           "source_document_id": accession + ".txt", "observed_primary_document": raw.get("primaryDocument") or None,
           "source_locator": url, "source_evidence_hash": source_hash,
           "source_row_index": source_index, "source_retrieved_utc": source.get("retrieved_utc"),
           "source_inventory_hash": source.get("inventory_sha256"),
           "original_filing_url": archive, "raw_submission_sha256": None,
           "security_id": None, "candidate_security_ids": permnos,
           "candidate_relation_source": None if development else {"screen_sha256": scope["screen_private_sha256"],
                  "identity_candidate_ids": [x["identity_candidate_id"] for x in relations],
                  "issue_evidence_ids": [x["issue_evidence_id"] for x in relations],
                  "historical_validity_established": False},
           "item1_hash": None, "calendar_id": None, "entry_session": None, "exit_session": None,
           "source_document_hash": None, "extractor_version": None, "extraction_locator": None,
           "identity_status": "not_applicable" if development else ("ambiguous" if len(permnos) > 1 else "unsupported"),
           "text_status": "not_checked", "calendar_status": "not_checked", "price_status": "not_checked",
           "exit_status": "not_checked", "filing_metadata_status": "supported",
           "discovery_run_id": "m5_next_20261114_0325", "scan_scope_id": scope["scan_scope_id"],
           "development_only": development, "formal_admitted": False,
           "selection_manifest_hash": scope_hash, "source_field_provenance": {
               "accession,cik,filing_date,form,fiscal_period,observed_primary_document":
                   {"source_locator": url, "source_evidence_hash": source_hash, "source_row_index": source_index},
               "candidate_security_ids": None if development else {"source_locator": scope["screen_private_path"],
                                                "source_evidence_hash": scope["screen_private_sha256"]}},
           "raw_header_check": None, "item1_extraction_error": None}
    return row


def scan_one(client: SecClient, cik: str) -> tuple[list[tuple[dict, dict, int]], dict]:
    base = f"https://data.sec.gov/submissions/CIK{cik}.json"
    record = {"cik": cik, "main_url": base, "main_status": "not_checked", "history_attempted": 0,
              "history_success": 0, "history_failed": [], "scan_complete_for_window": False,
              "metadata_rows_seen": 0, "original_10k_rows_in_window": 0}
    found: list[tuple[dict, dict, int]] = []
    try:
        main, meta = client.json(base)
        if str(main.get("cik", "")).zfill(10) != cik:
            raise ValueError("SEC main response CIK mismatch")
        current = _rows(main.get("filings", {}).get("recent", {}))
        record["main_status"] = "supported"
        record["main_source_sha256"] = meta["sha256"]
        found.extend((row, meta, i) for i, row in enumerate(current))
        prior_files = main.get("filings", {}).get("files", [])
        if not isinstance(prior_files, list):
            raise ValueError("SEC history manifest is not a list")
        for item in prior_files:
            if not isinstance(item, dict) or not HISTORY.fullmatch(str(item.get("name", ""))):
                record["history_failed"].append({"name": str(item), "reason": "invalid history manifest name"})
                continue
            if not history_overlaps(item):
                continue
            name = item["name"]
            url = "https://data.sec.gov/submissions/" + name
            record["history_attempted"] += 1
            try:
                older, old_meta = client.json(url)
                rows = _rows(older)
                found.extend((row, old_meta, i) for i, row in enumerate(rows))
                record["history_success"] += 1
            except (SecError, ValueError) as exc:
                record["history_failed"].append({"name": name, "reason": str(exc)})
        record["metadata_rows_seen"] = len(found)
        record["scan_complete_for_window"] = not record["history_failed"]
    except (SecError, ValueError) as exc:
        record["main_status"] = "unsupported"
        record["main_error"] = str(exc)
    return found, record


def dev_rows(dev: dict, scope: dict, scope_hash: str) -> list[dict]:
    sources = {x["body_sha256"]: x for x in dev["sources"]}
    result = []
    for firm in dev["firms"].values():
        cik = firm["cik"]
        if cik not in DEVELOPMENT_CIKS:
            raise ValueError("Unexpected development CIK in prior inventory")
        for index, raw in enumerate(firm["rows"]):
            src = sources.get(raw["source_body_sha256"])
            if not src:
                raise ValueError("Development row has no source body registration")
            meta = {"url": src["url"], "sha256": src["body_sha256"],
                    "retrieved_utc": src["original_retrieved_utc"],
                    "inventory_sha256": scope["development_inventory_sha256"]}
            row = filing_row(cik, raw, meta, index, scope, scope_hash, [], True)
            if row:
                row["source_row_index"] = None  # prior inventory does not preserve SEC array offset
                row["inventory_firm_row_index"] = index
                row["source_field_provenance"]["accession,cik,filing_date,form,fiscal_period,observed_primary_document"]["source_row_index"] = None
                row["source_field_provenance"]["accession,cik,filing_date,form,fiscal_period,observed_primary_document"]["inventory_firm_row_index"] = index
                result.append(row)
    return result


def deduplicate(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    by_key: dict[tuple[str, str], dict] = {}
    duplicates = []
    for row in rows:
        key = (row["accession"], row["source_document_id"])
        if key in by_key:
            a = by_key[key]
            if any(a[k] != row[k] for k in ("cik", "filing_date", "form", "fiscal_period")):
                raise ValueError(f"Conflicting duplicate SEC filing identity: {key}")
            duplicates.append({"accession": key[0], "source_document_id": key[1],
                               "kept_source_hash": a["source_evidence_hash"],
                               "duplicate_source_hash": row["source_evidence_hash"]})
            continue
        by_key[key] = row
    return sorted(by_key.values(), key=lambda r: (r["filing_date"], r["cik"], r["accession"])), duplicates


def scan(out: Path) -> dict:
    scope, scope_hash = check_frozen(out)
    if (out / "candidate_events_private.json").exists():
        raise ValueError("Discovery ledger already exists; freeze a new run for another scan")
    client = sec_client(out)
    all_rows = []
    records = []
    for cik in scope["ciks"]:
        found, rec = scan_one(client, cik)
        relations = scope["candidate_relations"][cik]
        for raw, meta, row_index in found:
            row = filing_row(cik, raw, meta, row_index, scope, scope_hash, relations, False)
            if row:
                all_rows.append(row)
                rec["original_10k_rows_in_window"] += 1
        records.append(rec)
        print(f"scan {len(records)}/{len(scope['ciks'])}: main={rec['main_status']} original_10k={rec['original_10k_rows_in_window']} history_failures={len(rec['history_failed'])}", flush=True)
    nondev, duplicate_rows = deduplicate(all_rows)
    dev, dev_duplicates = deduplicate(dev_rows(read(Path(scope["development_inventory_path"])), scope, scope_hash))
    if len(dev) != 30 or any(x["cik"] in DEVELOPMENT_CIKS for x in nondev):
        raise ValueError("Development control separation failed")
    selection = nondev[:ORIGINAL_DOWNLOAD_CAP]
    download_manifest = {"kind": "M5_ORIGINAL_FILING_DOWNLOAD_SELECTION_ONLY", "frozen_at_utc": now(),
                         "scope_manifest_sha256": scope_hash, "scan_scope_id": scope["scan_scope_id"],
                         "candidate_ledger_sort": ["filing_date", "cik", "accession"],
                         "selection_rule": "first 20 nondevelopment original 10-K candidate rows after stable filing_date, CIK, accession ordering",
                         "selected_count": len(selection), "cap": ORIGINAL_DOWNLOAD_CAP,
                         "selected": [{"accession": r["accession"], "cik": r["cik"],
                                       "filing_date": r["filing_date"], "source_evidence_hash": r["source_evidence_hash"],
                                       "original_filing_url": r["original_filing_url"]} for r in selection],
                         "full_cohort_exhaustive": False}
    ledger = {"kind": "M5_REAL_FILING_CANDIDATE_DISCOVERY_NOT_FORMAL_ADMISSION",
              "created_at_utc": now(), "scope_manifest_sha256": scope_hash,
              "scan_scope_id": scope["scan_scope_id"], "scan_records": records,
              "nondevelopment_candidate_rows": nondev, "development_only_rows": dev,
              "duplicates_explicitly_removed": duplicate_rows + dev_duplicates,
              "formal_admitted_count": 0,
              "formal_cohort_count": None,
              "scope_caveat": "Bounded 32-CIK fixed-start candidate scan; failures/partial scans are recorded; no historical event-time security admission."}
    save(out / "candidate_events_private.json", ledger)
    save(out / "original_filing_selection_manifest_private.json", download_manifest)
    safe = summarize(ledger, download_manifest, out)
    save(out / "candidate_discovery_summary_safe.json", safe)
    save(out / "candidate_issue_impact_private.json", impact_table(ledger, scope))
    return safe


def impact_table(ledger: dict, scope: dict) -> dict:
    by_cik = scope["candidate_relations"]
    rows = []
    for event in ledger["nondevelopment_candidate_rows"]:
        rels = by_cik[event["cik"]]
        rows.append({"accession": event["accession"], "cik": event["cik"],
                     "filing_date": event["filing_date"], "candidate_issue_ids": sorted({x["issue_evidence_id"] for x in rels}),
                     "candidate_security_ids": event["candidate_security_ids"],
                     "historical_event_time_identity": "unknown",
                     "LINKENDDT_issue_to_event_impact": "unknown",
                     "reason": "Screened CIK/CUSIP relation has observation time only; event-time security relationship and saved CSV LINKENDDT semantics remain unresolved."})
    return {"kind": "M5_DISCOVERY_ISSUE_TO_EVENT_IMPACT_PRIVATE", "created_at_utc": now(),
            "scan_scope_id": scope["scan_scope_id"], "rows": rows,
            "known_event_impacts": 0, "unknown_event_impacts": len(rows),
            "caveat": "Zero known impacts does not mean zero impacted events; no event-time security join is supported."}


def extract_item1_candidate(text: str) -> tuple[str, dict]:
    """Use a narrow, audited heading fallback when the base extractor declines.

    This keeps the base extraction strict while accepting two observed SEC
    layouts: trailing full stops, and an otherwise bare `Item 1.` heading.
    Each fallback still needs one substantive span and a following heading.
    """
    try:
        item, locator = extract_item1(text)
        return item, {**locator, "heading_template": "base_exact_business"}
    except ExtractionError as base_error:
        base_reason = str(base_error)
    templates = [
        ("business_trailing_punctuation", re.compile(r"^[ \t]*item[ \t]+1[ \t]*[.·:\-][ \t]*business[ \t]*[.·:\-][ \t]*$", re.I | re.M)),
        ("standalone_item_number", re.compile(r"^[ \t]*item[ \t]+1[ \t]*[.·:\-][ \t]*$", re.I | re.M)),
    ]
    next_heading = re.compile(
        r"^[ \t]*item[ \t]+(?:1[ \t]*a|1[ \t]*b|2)[ \t]*[.·:\-][ \t]*(?:risk[ \t]+factors|unresolved[ \t]+staff[ \t]+comments|properties)?[ \t]*[.·:\-]?[ \t]*$",
        re.I | re.M)
    ends = list(next_heading.finditer(text))
    accepted = []
    for label, start_pattern in templates:
        starts = list(start_pattern.finditer(text))
        for start in starts:
            if label == "standalone_item_number":
                following = text[start.end():].lstrip(" \t\n")
                if not re.match(r"(?i)^business[.·:\-]?[ \t]*\n", following):
                    continue
            end = next((x for x in ends if x.start() > start.end()), None)
            if end is None:
                continue
            section = text[start.start():end.start()]
            if len(section) < 1000 or len(section.split()) < 150 or len(section) > len(text) * 0.7:
                continue
            if start_pattern.search(section[30:]):
                continue
            accepted.append((label, start.start(), end.start(), section, len(starts)))
    if len(accepted) != 1:
        raise ExtractionError(f"Base: {base_reason}; conservative heading fallback found {len(accepted)} substantive spans")
    label, start, end, section, heading_count = accepted[0]
    return section, {"start_char": start, "end_char_exclusive": end,
                     "characters": len(section), "word_count": len(section.split()),
                     "heading_template": label, "start_heading_count": heading_count,
                     "next_heading_count": len(ends), "substantive_candidate_count": 1}


def summarize(ledger: dict, selection: dict, out: Path) -> dict:
    recs = ledger["scan_records"]
    nondev = ledger["nondevelopment_candidate_rows"]
    return {"kind": "M5_CANDIDATE_DISCOVERY_SAFE_AGGREGATE", "created_at_utc": now(),
            "scope_manifest_sha256": ledger["scope_manifest_sha256"],
            "scope_manifest_file_sha256": file_sha(out / "discovery_scope_manifest_private.json"),
            "scan_scope_id": ledger["scan_scope_id"], "scan_cik_denominator": len(recs),
            "main_success_count": sum(x["main_status"] == "supported" for x in recs),
            "main_failure_count": sum(x["main_status"] != "supported" for x in recs),
            "complete_window_scan_count": sum(x["scan_complete_for_window"] for x in recs),
            "partial_or_failed_scan_count": sum(not x["scan_complete_for_window"] for x in recs),
            "history_attempt_count": sum(x["history_attempted"] for x in recs),
            "history_success_count": sum(x["history_success"] for x in recs),
            "history_failure_count": sum(len(x["history_failed"]) for x in recs),
            "nondevelopment_original_10k_candidate_count": len(nondev),
            "nondevelopment_distinct_cik_count": len({x["cik"] for x in nondev}),
            "candidate_filing_year_counts": dict(sorted(Counter(x["filing_date"][:4] for x in nondev).items())),
            "development_only_original_10k_count": len(ledger["development_only_rows"]),
            "original_filing_download_selected_count": selection["selected_count"],
            "original_filing_download_cap": selection["cap"],
            "download_selection_manifest_sha256": file_sha(out / "original_filing_selection_manifest_private.json"),
            "duplicate_metadata_rows_removed": len(ledger["duplicates_explicitly_removed"]),
            "formal_admitted_count": 0, "formal_cohort_count": None,
            "security_identity_supported_count": 0,
            "unknown_issue_to_event_impact_count": len(nondev),
            "private_ledger_sha256": file_sha(out / "candidate_events_private.json"),
            "scope_exhaustive_full_cohort": False,
            "real_metadata_vs_original_body": "SEC accession metadata are real source-hashed records; original filing bodies and Item 1 are separate downstream checks.",
            "financial_experiment_status": "NOT_RUN"}


def download(out: Path) -> dict:
    scope, scope_hash = check_frozen(out)
    ledger_path = out / "candidate_events_private.json"
    ledger = read(ledger_path)
    selection_path = out / "original_filing_selection_manifest_private.json"
    selection = read(selection_path)
    if selection["scope_manifest_sha256"] != scope_hash or selection["selected_count"] > ORIGINAL_DOWNLOAD_CAP:
        raise ValueError("Original filing download selection does not match frozen scope")
    selected_keys = [(x["cik"], x["accession"]) for x in selection["selected"]]
    expected_keys = [(x["cik"], x["accession"]) for x in ledger["nondevelopment_candidate_rows"][:ORIGINAL_DOWNLOAD_CAP]]
    if selected_keys != expected_keys:
        raise ValueError("Frozen selection does not reproduce from discovered candidates")
    client = sec_client(out)
    by_key = {(x["cik"], x["accession"]): x for x in ledger["nondevelopment_candidate_rows"]}
    log = []
    extractor_sha = file_sha(ROOT / "src" / "finembed" / "extract.py")
    fallback_sha = file_sha(Path(__file__))
    for i, item in enumerate(selection["selected"], 1):
        key = (item["cik"], item["accession"])
        row = by_key[key]
        row["item1_hash"] = None
        row["text_status"] = "not_checked"
        row["extractor_version"] = None
        row["extraction_locator"] = None
        row["item1_extraction_error"] = None
        result = {"cik": key[0], "accession": key[1], "url": item["original_filing_url"],
                  "download_status": "not_checked", "header_status": "not_checked", "item1_status": "not_checked"}
        try:
            raw, meta = client.get(item["original_filing_url"])
            result["download_status"] = "supported"
            result["raw_submission_sha256"] = meta["sha256"]
            row["raw_submission_sha256"] = meta["sha256"]
            row["source_document_hash"] = meta["sha256"]
            row["source_document_retrieved_utc"] = meta["retrieved_utc"]
            try:
                header = raw_header(raw, key[1], "10-K")
                raw_cik = raw_filer_cik(raw)
                if raw_cik != key[0] or header["filing_date_raw_header"] != row["filing_date"]:
                    raise ValueError("Complete submission header CIK or filing date differs from metadata")
                row["raw_header_check"] = {**header, "raw_filer_cik": raw_cik, "status": "supported"}
                result["header_status"] = "supported"
                try:
                    rendered, primary_locator = primary_text(raw)
                    item1, offsets = extract_item1_candidate(rendered)
                    comparison(rendered, item1, offsets)
                    path = out / "item1_text_private" / f"{key[1]}.txt"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    # Write exact UTF-8 bytes: text-mode newline translation on
                    # Windows would change the audited Item 1 digest.
                    path.write_bytes(item1.encode("utf-8"))
                    if file_sha(path) != body_sha(item1.encode("utf-8")):
                        raise ValueError("Item 1 written text hash mismatch")
                    row["item1_hash"] = file_sha(path)
                    row["text_status"] = "supported"
                    row["extractor_version"] = {"base_extract_py_sha256": extractor_sha,
                                                "candidate_fallback_script_sha256": fallback_sha,
                                                "heading_template": offsets["heading_template"]}
                    row["extraction_locator"] = {"raw_submission_url": item["original_filing_url"],
                                                  "primary": primary_locator, "item1": offsets,
                                                  "text_path_private": str(path)}
                    result["item1_status"] = "supported"
                    result["item1_hash"] = row["item1_hash"]
                except (ExtractionError, ValueError) as exc:
                    row["text_status"] = "ambiguous"
                    row["item1_extraction_error"] = str(exc)
                    result["item1_status"] = "ambiguous"
                    result["item1_error"] = str(exc)
            except (SecError, ValueError) as exc:
                row["raw_header_check"] = {"status": "ambiguous", "reason": str(exc)}
                row["text_status"] = "unsupported"
                result["header_status"] = "ambiguous"
                result["header_error"] = str(exc)
        except SecError as exc:
            result["download_status"] = "unsupported"
            result["download_error"] = str(exc)
            row["text_status"] = "unsupported"
            row["item1_extraction_error"] = str(exc)
        log.append(result)
        print(f"download {i}/{len(selection['selected'])}: body={result['download_status']} header={result['header_status']} item1={result['item1_status']}", flush=True)
    save(ledger_path, ledger)
    save(out / "original_filing_download_log_private.json", {"selection_manifest_sha256": file_sha(selection_path),
                                                            "download_attempts": log})
    safe = summarize(ledger, selection, out)
    safe.update({"original_filing_download_attempt_count": len(log),
                 "original_filing_download_success_count": sum(x["download_status"] == "supported" for x in log),
                 "raw_header_supported_count": sum(x["header_status"] == "supported" for x in log),
                 "item1_extraction_supported_count": sum(x["item1_status"] == "supported" for x in log),
                 "item1_extraction_ambiguous_count": sum(x["item1_status"] == "ambiguous" for x in log)})
    save(out / "candidate_discovery_summary_safe.json", safe)
    return safe


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["freeze", "scan", "download"])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    if args.stage == "freeze":
        result = freeze(args.out)
        print(json.dumps({"stage": "freeze", "scan_scope_id": result["scan_scope_id"], "scope_size": len(result["ciks"])}))
    elif args.stage == "scan":
        result = scan(args.out)
        print(json.dumps({"stage": "scan", **result}, ensure_ascii=False))
    else:
        result = download(args.out)
        print(json.dumps({"stage": "download", **result}, ensure_ascii=False))


if __name__ == "__main__":
    main()
