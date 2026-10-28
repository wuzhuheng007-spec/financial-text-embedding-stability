"""Recheck saved M8 10-Q sources and derive a conservative English period ledger.

This script is read-only with respect to M7/M8. It makes no network requests and
does not interpret a matched period phrase as whole-chapter coverage.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


TICKERS = ("MSFT", "AMZN", "GOOGL", "META")
M8 = Path("artifacts/m8/20261025T230645Z-four-10q")
M7 = Path("artifacts/m7/20261024T223714Z-tail-bm25-10q/tenq/pilot_v3")
M7_REVIEW = Path("artifacts/m7_review/20261024T235855Z-closeout")
PERIOD_SCAN = re.compile(
    r"\b(?:(?:three|six|nine|twelve)\s+months?|(?:trailing|rolling|last)\s+twelve\s+months?|"
    r"year|quarter)\s+(?:ended|ending)|\bas\s+of\b", re.I
)


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def assert_hash(path: Path, expected: str) -> str:
    observed = sha_file(path)
    if observed != expected:
        raise ValueError(f"SHA-256 mismatch: {path}: {observed} != {expected}")
    return observed


def classify(phrase: str, context: str) -> tuple[str, str, str]:
    """Return period class, narrower role, and evidence level.

    Lexical categories are deliberately narrow. Ambiguous context stays unresolved.
    """
    p = phrase.casefold()
    c = context.casefold()
    if "three months ending" in p:
        return "quarterly", "future_schedule_not_realized_result", "same_agent_checked"
    if "three months ended" in p:
        return "quarterly", "explicit_three_month_span", "programmatic"
    if "six months ended" in p:
        return "year-to-date", "explicit_six_month_span_in_fiscal_q2", "same_agent_checked"
    if "trailing twelve months ended" in p or "twelve months ended" in p:
        return "trailing-twelve-month", "explicit_twelve_month_span", "programmatic"
    if "year ended" in p or "year ending" in p:
        if "annual report on form 10-k" in c:
            return "annual-reference", "cross_reference_to_10_k", "same_agent_checked"
        return "other/unresolved", "annual_phrase_context_unresolved", "programmatic"
    if "as of" in p:
        return "other/unresolved", "point_in_time_not_interval", "programmatic"
    return "other/unresolved", "phrase_context_unresolved", "programmatic"


def source_inputs(root: Path, ticker: str) -> tuple[dict, dict, dict, str, dict]:
    ingest = root / M8 / "ingest" / ticker
    extract = root / M8 / "extraction_v4" / ticker
    review = root / M8 / "review_v7" / ticker
    identity = load_json(ingest / "source_identity.json")
    extraction_receipt = load_json(extract / "extraction_receipt.json")
    review_receipt = load_json(review / "receipt.json")
    boundary = load_json(extract / "boundary_candidates.json")
    section = (extract / "item2_candidate.txt").read_text(encoding="utf-8")
    files = {
        "original_submission": ingest / "original_submission.txt",
        "primary_document": ingest / "primary_document_source.html",
        "normalized_primary_text": ingest / "primary_text.txt",
        "item2_section": extract / "item2_candidate.txt",
        "boundary_evidence": extract / "boundary_candidates.json",
        "period_ledger": extract / "period_ledger.json",
        "table_index": extract / "table_index.json",
        "review_page": review / "index.html",
        "review_receipt": review / "receipt.json",
    }
    expected = {
        "original_submission": identity["original_submission_sha256"],
        "primary_document": identity["primary_document_sha256"],
        "normalized_primary_text": identity["primary_text_sha256"],
        "item2_section": boundary["selected_span"]["section_sha256"],
        "review_page": review_receipt["review_page_sha256"],
    }
    observed = {name: assert_hash(path, expected[name]) if name in expected else sha_file(path)
                for name, path in files.items()}
    for name, key in (("period_ledger", "period_ledger.json"), ("table_index", "table_index.json")):
        if observed[name] != extraction_receipt["outputs"][key]["sha256"]:
            raise ValueError(f"M8 extraction receipt mismatch for {ticker} {name}")
    raw = files["original_submission"].read_bytes()
    primary = files["primary_document"].read_bytes()
    raw_start, raw_end = identity["primary_raw_byte_span"]
    if raw[raw_start:raw_end] != primary:
        raise ValueError(f"Raw-embedded primary mismatch for {ticker}")
    primary_text = files["normalized_primary_text"].read_text(encoding="utf-8")
    section_start, section_end = boundary["selected_span"]["start_char"], boundary["selected_span"]["end_char_exclusive"]
    if primary_text[section_start:section_end] != section:
        raise ValueError(f"Saved Item 2 is not the recorded normalized-primary span for {ticker}")
    table_index = load_json(files["table_index"])
    checked_tables = 0
    for table in table_index["tables"]:
        a, b = table["html_source_byte_start"], table["html_source_byte_end_exclusive"]
        c, d = table["complete_submission_byte_start"], table["complete_submission_byte_end_exclusive"]
        e, f = table["flattened_section_char_start"], table["flattened_section_char_end_exclusive"]
        fragment = primary[a:b]
        if fragment != raw[c:d] or sha_bytes(fragment) != table["raw_html_fragment_sha256"]:
            raise ValueError(f"Raw table source mismatch for {ticker} {table['table_id']}")
        if sha_bytes(section[e:f].encode("utf-8")) != table["flattened_text_sha256"]:
            raise ValueError(f"Flattened table span mismatch for {ticker} {table['table_id']}")
        checked_tables += 1
    receipt = {
        "ticker": ticker,
        "cik": identity["cik"],
        "accession": identity["accession"],
        "form": identity["form"],
        "report_period_end": identity["report_period_end"],
        "filing_date": identity["filing_date"],
        "source_url": identity["original_submission_url"],
        "source_version_status": identity["primary_version_status"],
        "human_review": review_receipt["human_decision"] or "PENDING_HUMAN_REVIEW",
        "browser_render_status": review_receipt["browser_render_status"],
        "checked_table_source_spans": checked_tables,
        "files": {name: {"path": path.relative_to(root).as_posix(), "sha256": observed[name], "bytes": path.stat().st_size}
                  for name, path in files.items()},
    }
    return identity, load_json(files["period_ledger"]), table_index, section, receipt


def collect_periods(ticker: str, identity: dict, ledger: dict, table_index: dict, section: str, section_hash: str) -> list[dict]:
    candidates = []
    for item in ledger["narrative_periods"]:
        candidates.append(("narrative", None, item))
    for table in ledger["table_periods"]:
        for hint in table["period_hints"]:
            candidates.append((f"table:{hint.get('location_kind', 'UNSPECIFIED')}", table["table_id"], hint))
    for item in ledger["other_periods"]:
        candidates.append(("other", None, item))
    tables = {t["table_id"]: t for t in table_index["tables"]}
    rows = {}
    for location, table_id, hint in candidates:
        a, b = hint["source_start_char"], hint["source_end_char_exclusive"]
        excerpt = section[a:b]
        if excerpt != hint["source_excerpt"] or sha_bytes(excerpt.encode("utf-8")) != hint["source_excerpt_sha256"]:
            raise ValueError(f"M8 period evidence span mismatch: {ticker}:{a}:{b}")
        if hint["phrase"].casefold() not in excerpt.casefold():
            raise ValueError(f"M8 period phrase absent from saved excerpt: {ticker}:{a}:{b}")
        ca, cb = max(0, a - 220), min(len(section), b + 220)
        context = section[ca:cb]
        period_class, narrower_role, evidence_level = classify(hint["phrase"], context)
        source_table = tables.get(table_id) if table_id else None
        key = (a, b, hint["phrase"].casefold())
        row = {
            "ticker": ticker,
            "cik": identity["cik"],
            "accession": identity["accession"],
            "item2_section_sha256": section_hash,
            "location_file": (M8 / "extraction_v4" / ticker / "item2_candidate.txt").as_posix(),
            "location_unit": "Python Unicode character indices in saved UTF-8 Item 2 text; end exclusive",
            "source_start_char": a,
            "source_end_char_exclusive": b,
            "source_excerpt": excerpt,
            "source_excerpt_sha256": hint["source_excerpt_sha256"],
            "source_context_start_char": ca,
            "source_context_end_char_exclusive": cb,
            "source_context_sha256": sha_bytes(context.encode("utf-8")),
            "source_context_excerpt": context,
            "phrase": hint["phrase"],
            "period_class": period_class,
            "narrower_role": narrower_role,
            "classification_evidence_level": evidence_level,
            "source_locations": [location],
            "table_id": table_id,
            "table_primary_html_byte_span": [source_table["html_source_byte_start"], source_table["html_source_byte_end_exclusive"]] if source_table else None,
            "table_raw_submission_byte_span": [source_table["complete_submission_byte_start"], source_table["complete_submission_byte_end_exclusive"]] if source_table else None,
            "table_raw_html_fragment_sha256": source_table["raw_html_fragment_sha256"] if source_table else None,
            "date_mentions_in_saved_excerpt": hint.get("date_mentions_in_excerpt", []),
            "researcher_period_review": "PENDING_HUMAN_REVIEW",
            "whole_chapter_coverage": None,
            "whole_chapter_coverage_reason": "A local phrase or table heading does not define MD&A-wide coverage.",
        }
        if key in rows:
            if location not in rows[key]["source_locations"]:
                rows[key]["source_locations"].append(location)
            if table_id and not rows[key]["table_id"]:
                for field in ("table_id", "table_primary_html_byte_span", "table_raw_submission_byte_span", "table_raw_html_fragment_sha256"):
                    rows[key][field] = row[field]
        else:
            rows[key] = row
    return sorted(rows.values(), key=lambda r: (r["source_start_char"], r["source_end_char_exclusive"]))


def verify_aapl(root: Path) -> dict:
    pilot = root / M7
    handoff = root / M7_REVIEW / "review_handoff_v4"
    page = root / M7_REVIEW / "review_page_v4" / "index.html"
    state_path = handoff / "review_state_v9.json"
    state = load_json(state_path)
    bound = state["file_identity"]
    sources = {
        "raw_submission": pilot / "original_submission.txt",
        "item2_section": pilot / "mda_section_program_candidate.txt",
        "review_page_v4": page,
        "review_state_v9": state_path,
        "acceptance_self_check_v1": handoff / "acceptance_self_check_v1.json",
        "historical_manifest_v2_correction_v1": handoff / "human_messages" / "historical_manifest_v2_correction_v1.json",
        "mda4_event_hash_correction_v1": handoff / "human_messages" / "mda4_event_hash_correction_v1.json",
    }
    expected = {"raw_submission": bound["raw_submission_sha256"], "item2_section": bound["section_sha256"], "review_page_v4": bound["review_page_v4_sha256"]}
    hashes = {name: assert_hash(path, expected[name]) if name in expected else sha_file(path) for name, path in sources.items()}
    raw_message_files = []
    required = ("start", "end", "MDA-1", "MDA-2", "MDA-3", "MDA-4", "MDA-5", "MDA-6", "body_and_anomalies")
    for item in required:
        decision = state["items"][item]
        if decision["decision"] != "ACCEPT":
            raise ValueError(f"AAPL {item} is not ACCEPT in saved review state")
        message_path = handoff / decision["source_message_path"]
        event = load_json(message_path)
        binding = event["bound_evidence"]
        for field, actual in (("raw_submission_sha256", hashes["raw_submission"]), ("review_page_v4_sha256", hashes["review_page_v4"])):
            if binding[field] != actual:
                raise ValueError(f"AAPL {item} {field} does not bind to saved file")
        section_field = binding.get("extracted_section_sha256", binding.get("section_sha256"))
        if section_field != hashes["item2_section"]:
            raise ValueError(f"AAPL {item} section binding mismatch")
        if event["decision"] != "ACCEPT" or event["item_id"] != item:
            raise ValueError(f"AAPL {item} event identity/decision mismatch")
        raw_message_files.append({"item": item, "path": message_path.relative_to(root).as_posix(), "sha256": sha_file(message_path)})
    self_check = load_json(sources["acceptance_self_check_v1"])
    if self_check["review_state_v9_sha256"] != hashes["review_state_v9"] or self_check["accepted_item_count"] != 9:
        raise ValueError("AAPL historical acceptance self-check binding mismatch")
    mda4_correction = load_json(sources["mda4_event_hash_correction_v1"])
    mda4_event = next(x for x in raw_message_files if x["item"] == "MDA-4")
    if mda4_correction["historical_event_sha256"] != mda4_event["sha256"]:
        raise ValueError("AAPL append-only MDA-4 correction does not bind to event")
    manifest_correction = load_json(sources["historical_manifest_v2_correction_v1"])
    if manifest_correction["recomputed_file_sha256"] != next(x["sha256"] for x in raw_message_files if x["item"] == "end"):
        raise ValueError("AAPL append-only manifest correction does not bind to end event")
    return {
        "cik": bound["cik"], "accession": bound["accession"],
        "item_level_decisions": "9/9 ACCEPT, each bound to the rehashed raw submission, Item 2 text, and v4 page",
        "overall_user_signature_or_decision": state["explicit_overall_user_decision"],
        "user_entered_review_time": state["user_entered_review_time"],
        "browser_render_export_reload": state["browser_render_export_reload_check"],
        "historical_self_check_status": self_check["status"],
        "historical_self_check_is_independent_audit": self_check["independent_audit"],
        "append_only_corrections": [sources["historical_manifest_v2_correction_v1"].relative_to(root).as_posix(), sources["mda4_event_hash_correction_v1"].relative_to(root).as_posix()],
        "files": {name: {"path": path.relative_to(root).as_posix(), "sha256": hashes[name], "bytes": path.stat().st_size} for name, path in sources.items()},
        "decision_events": raw_message_files,
    }


def write_json(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output if args.output.is_absolute() else root / args.output
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"Output directory must be new or empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    input_receipts = {}
    rows = []
    scan_coverage = {}
    for ticker in TICKERS:
        identity, ledger, table_index, section, receipt = source_inputs(root, ticker)
        input_receipts[ticker] = receipt
        ticker_rows = collect_periods(ticker, identity, ledger, table_index, section, receipt["files"]["item2_section"]["sha256"])
        row_starts = {r["source_start_char"] for r in ticker_rows}
        scan_hits = list(PERIOD_SCAN.finditer(section))
        unmatched = [{"source_start_char": hit.start(), "phrase": hit.group(0)} for hit in scan_hits
                     if hit.start() not in row_starts]
        scan_coverage[ticker] = {"saved_section_pattern_hits": len(scan_hits),
                                 "saved_ledger_unique_entries": len(ticker_rows),
                                 "unmatched_pattern_hits": unmatched,
                                 "scope": "Independent scan for the explicitly declared period-phrase pattern only; not a semantic completeness claim"}
        if unmatched:
            raise ValueError(f"Unmapped explicit period phrase in saved {ticker} section: {unmatched[:3]}")
        rows.extend(ticker_rows)
    aapl = verify_aapl(root)
    counts = {ticker: dict(Counter(row["period_class"] for row in rows if row["ticker"] == ticker)) for ticker in TICKERS}
    key_checks = {
        "msft_quarterly_three_month_phrase_found": any(r["ticker"] == "MSFT" and r["period_class"] == "quarterly" and "three months ended" in r["phrase"].casefold() for r in rows),
        "msft_six_month_ytd_phrase_found": any(r["ticker"] == "MSFT" and r["period_class"] == "year-to-date" and "six months ended" in r["phrase"].casefold() for r in rows),
        "amzn_quarterly_three_month_phrase_found": any(r["ticker"] == "AMZN" and r["period_class"] == "quarterly" and "three months ended" in r["phrase"].casefold() for r in rows),
        "amzn_twelve_month_phrase_found": any(r["ticker"] == "AMZN" and r["period_class"] == "trailing-twelve-month" for r in rows),
        "all_entries_have_exact_verified_section_spans": True,
        "all_declared_period_pattern_hits_mapped": all(not x["unmatched_pattern_hits"] for x in scan_coverage.values()),
        "all_four_whole_chapter_periods_unresolved": all(r["whole_chapter_coverage"] is None for r in rows),
    }
    if not all(key_checks.values()):
        raise ValueError(f"Key period evidence check failed: {key_checks}")
    write_json(output / "source_integrity.json", {"kind": "M9_SAVED_SOURCE_REHASH", "checked_at_utc": datetime.now(timezone.utc).isoformat(), "m8_run": M8.as_posix(), "tickers": input_receipts, "aapl_historical_decision_binding": aapl})
    write_json(output / "period_evidence.json", {"kind": "M9_LOCAL_PERIOD_PHRASE_EVIDENCE", "scope": "Four saved M8 Item 2 candidates; local phrases and table headings only", "evidence_status": "PROGRAMMATIC_AND_SAME_AGENT_CHECKED_NOT_RESEARCHER_CONFIRMED", "input_section_hashes": {t: input_receipts[t]["files"]["item2_section"]["sha256"] for t in TICKERS}, "pattern_scan_coverage": scan_coverage, "counts_by_company_and_class": counts, "key_checks": key_checks, "entries": rows})
    fields = ("ticker", "accession", "location_file", "source_start_char", "source_end_char_exclusive", "source_excerpt_sha256", "phrase", "period_class", "narrower_role", "classification_evidence_level", "table_id", "source_excerpt", "date_mentions_in_saved_excerpt", "researcher_period_review")
    with (output / "period_evidence.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: json.dumps(row[field], ensure_ascii=False) if isinstance(row[field], (list, dict)) else row[field] for field in fields})
    exception_rows = [
        {"ticker": r["ticker"], "accession": r["accession"], "item2_section_sha256": r["item2_section_sha256"],
         "source_start_char": r["source_start_char"], "source_end_char_exclusive": r["source_end_char_exclusive"],
         "source_excerpt_sha256": r["source_excerpt_sha256"], "phrase": r["phrase"],
         "period_class": r["period_class"], "narrower_role": r["narrower_role"],
         "table_id": r["table_id"], "researcher_review": r["researcher_period_review"]}
        for r in rows if r["period_class"] in {"annual-reference", "other/unresolved"}
        or r["narrower_role"] == "future_schedule_not_realized_result"
    ]
    mixed_table_rows = []
    for ticker in TICKERS:
        table_ids = sorted({r["table_id"] for r in rows if r["ticker"] == ticker and r["table_id"]
                            and "table:FLATTENED_TABLE_SECTION_TEXT" in r["source_locations"]})
        for table_id in table_ids:
            classes = sorted({r["period_class"] for r in rows if r["ticker"] == ticker and r["table_id"] == table_id
                              and "table:FLATTENED_TABLE_SECTION_TEXT" in r["source_locations"]})
            if len(classes) > 1:
                mixed_table_rows.append({"ticker": ticker, "table_id": table_id, "local_period_classes": classes,
                                         "interpretation": "Multiple explicit period headings occur within this saved flattened table; no single chapter-wide interval is inferred."})
    write_json(output / "period_exceptions.json", {
        "kind": "M9_PERIOD_CONTEXT_EXCEPTIONS",
        "note": "These are local phrase classifications, not a complete semantic inventory or a chapter-wide coverage assignment.",
        "annual_references_points_in_time_and_future_schedules": exception_rows,
        "tables_with_multiple_explicit_period_classes": mixed_table_rows,
        "whole_chapter_period_by_ticker": {ticker: None for ticker in TICKERS},
        "whole_chapter_period_reason": "The saved MD&A text mixes reporting, comparison, point-in-time, annual cross-reference, and in some cases future periods.",
    })
    readme = """# M9 saved-source and period evidence (English)

This directory derives from four fixed, saved M8 SEC 10-Q submissions and the saved M7 AAPL decision packet. The derivation made no network request, changed no historical file, and ran no representation or financial experiment. All checks here were performed by the same agent and are not an independent audit or researcher review.

## Read order

1. `source_integrity.json`: observed file hashes, source identities, raw-embedded primary and section-span checks, literal table source checks, and the AAPL historical decision binding.
2. `period_evidence.csv` or `period_evidence.json`: exact Item 2 Unicode character spans, excerpt/context SHA-256 values, local period classes, and table-source links.
3. `period_exceptions.json`: annual 10-K references, point-in-time phrases, future schedule, and mixed-duration table headings.
4. `receipt.json`: actual command, source-code hash, output hashes, and key pass checks.

## Source-backed findings

- MSFT's saved Item 2 contains explicit three-month and six-month headings, including both types in MDA-4. Its `year ended June 30, 2024` occurrence refers to its annual 10-K. One `Three Months Ending` table describes expected future recognition, not a realized quarterly result.
- AMZN's saved Item 2 contains three-month and twelve-month headings, including both types in MDA-1. The saved text also explicitly says `trailing twelve months ended` near cash-flow discussion. Its `as of` phrases are point-in-time values, not trailing or quarterly periods.
- GOOGL and META have many three-month headings and 10-K annual cross-references. One GOOGL cross-reference says `year ending December 31, 2024`; the original wording is preserved rather than silently corrected.
- No local phrase is promoted to an MD&A-wide coverage start or end. Different periods may coexist in narrative and tables. Every new M8 researcher-review state remains pending.

## AAPL historical state

All nine saved item-level ACCEPT events are bound to the rehashed original submission, Item 2 candidate, and v4 page. The saved record has no separate overall user signature or user-entered review time. Browser rendering, export, and reload remain marked pending. Two historical hash transcription errors have append-only correction records; this derivation does not rewrite them.

## Limits

The ledger enumerates and verifies the period hints present in the saved M8 period ledgers. It does not assert that these rules identify every semantic time period in the filing. Classification levels are `programmatic` or `same_agent_checked`; none is `researcher_confirmed`. Saved raw/standalone primary version equivalence and historical first-public or tradable timestamps are not established. The current SEC filing detail page identity check is separate from these saved-byte checks. No formal M5 parameters or financial conclusions are established here.
"""
    (output / "README.md").write_text(readme, encoding="utf-8")
    receipt = {
        "kind": "M9_ENGLISH_SOURCE_AND_PERIOD_DERIVATION_RECEIPT",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "command": f"python scripts/m9_period_evidence.py --output {output.relative_to(root).as_posix()}",
        "code_sha256": sha_file(Path(__file__)),
        "network_calls": "NONE",
        "researcher_review_for_four_M8_files": "PENDING_HUMAN_REVIEW",
        "aapl_item_decisions": "9/9 ACCEPT, overall signature/time and browser export/reload remain unverified",
        "period_entry_count": len(rows),
        "counts_by_company_and_class": counts,
        "key_checks": key_checks,
        "outputs": {p.name: {"sha256": sha_file(p), "bytes": p.stat().st_size} for p in sorted(output.glob("*")) if p.is_file()},
    }
    write_json(output / "receipt.json", receipt)
    print(json.dumps({"status": "PASS_SAME_AGENT_SELF_CHECK", "output": str(output), "entry_count": len(rows), "counts": counts, "key_checks": key_checks}, indent=2))


if __name__ == "__main__":
    main()
