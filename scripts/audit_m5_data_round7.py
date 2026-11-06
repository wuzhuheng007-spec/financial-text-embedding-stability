"""Audit saved M1 text and M4 daily-bar availability without computing returns.

This inventory is a current-file check for protocol design, not a financial
experiment or a point-in-time certification. No network or model is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


BAR_FIELDS = ("date_et", "t", "o", "h", "l", "c", "v", "vw", "n")
PRICE_FIELDS = ("o", "h", "l", "c", "vw")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def require_hash(path: Path, expected: str) -> str:
    actual = file_sha256(path)
    if actual != expected:
        raise ValueError(f"SHA-256 mismatch for {path.name}: expected {expected}, got {actual}")
    return actual


def validate_bars(rows: list[dict], expected_dates: list[str]) -> dict:
    if not rows or len(rows) != len(expected_dates):
        raise ValueError("Price bars missing or date-count mismatch")
    dates = [row.get("date_et") for row in rows]
    if dates != expected_dates or dates != sorted(set(dates)):
        raise ValueError("Price dates are unsorted, duplicated or disagree with the common mask")
    missing = {field: 0 for field in BAR_FIELDS}
    for row in rows:
        for field in BAR_FIELDS:
            if field not in row or row[field] is None:
                missing[field] += 1
        if any(field not in row or row[field] is None for field in BAR_FIELDS):
            continue
        for field in PRICE_FIELDS:
            value = row[field]
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"Invalid price field {field}")
        for field in ("v", "n"):
            value = row[field]
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"Invalid activity field {field}")
        stamp = row["t"]
        if not isinstance(stamp, int):
            raise ValueError("Daily-bar timestamp must be integer milliseconds")
        day = datetime.fromtimestamp(stamp / 1000, tz=timezone.utc).astimezone(
            ZoneInfo("America/New_York")
        ).date().isoformat()
        if day != row["date_et"]:
            raise ValueError("Daily-bar timestamp and Eastern date disagree")
    if any(missing.values()):
        raise ValueError(f"Missing daily-bar fields: {missing}")
    return {"field_names": sorted(set().union(*(set(row) for row in rows))),
            "missing_by_required_field": missing, "date_start": dates[0],
            "date_end": dates[-1], "date_count": len(dates)}


def event_coverage(dates: list[str], filing_date: str, horizon_sessions: int) -> dict:
    """Check saved close-pair existence; never calculate a return label."""
    after = [i for i, date in enumerate(dates) if date > filing_date]
    if not after:
        return {"first_post_filing_bar_date": None,
                "future_close_date": None, "close_pair_exists": False,
                "reason": "No saved bar strictly after the filing date"}
    first = after[0]
    last = first + horizon_sessions
    if last >= len(dates):
        return {"first_post_filing_bar_date": dates[first],
                "future_close_date": None, "close_pair_exists": False,
                "reason": "Saved series ends before the proposed future close"}
    return {"first_post_filing_bar_date": dates[first],
            "future_close_date": dates[last], "close_pair_exists": True,
            "reason": "Calendar and saved-price existence only; public time, text vintage and execution price remain unapproved"}


def saved_submission_versions(filing: dict, sec_cache_dir: Path | None) -> dict:
    if sec_cache_dir is None:
        return {"same_period_rows": None,
                "reason": "Saved SEC submissions cache was not supplied"}
    same_period = []
    matched_sources = []
    for source in filing["submissions_sources"]:
        matching = []
        for receipt_path in sec_cache_dir.glob("*.json"):
            receipt = read_json(receipt_path)
            if (receipt.get("url") == source["url"]
                    and receipt.get("sha256") == source["sha256"]):
                matching.append(receipt_path)
        if len(matching) != 1:
            raise ValueError("Saved SEC submissions source missing or ambiguous")
        bin_path = matching[0].with_suffix(".bin")
        require_hash(bin_path, source["sha256"])
        payload = read_json(bin_path)
        columns = payload.get("filings", {}).get("recent", payload)
        for i, accession in enumerate(columns["accessionNumber"]):
            form = columns["form"][i]
            report_date = columns["reportDate"][i]
            if (accession == filing["accession"] or
                    (form in {"10-K", "10-K/A"} and report_date == filing["report_date"])):
                same_period.append({"accession": accession, "form": form,
                                    "filing_date": columns["filingDate"][i],
                                    "report_period_end": report_date})
        matched_sources.append({"sha256": source["sha256"],
                                "retrieved_utc": source["retrieved_utc"]})
    if sum(row["accession"] == filing["accession"] and row["form"] == "10-K"
           for row in same_period) != 1:
        raise ValueError("Original 10-K absent or duplicated in saved submissions")
    return {"same_period_rows": same_period,
            "same_period_10ka_count": sum(row["form"] == "10-K/A" for row in same_period),
            "saved_source_hashes_and_times": matched_sources,
            "scope_note": "A 2026 saved submissions snapshot cannot rule out same-day PAC or unarchived content changes"}


def other_local_texts(m8_root: Path | None, m9_root: Path | None,
                      m7_root: Path | None = None) -> dict | None:
    if m8_root is None or m9_root is None:
        return None
    originals = []
    accession_by_ticker = {}
    for identity_path in sorted((m8_root / "ingest").glob("*/source_identity.json")):
        identity = read_json(identity_path)
        raw_path = identity_path.parent / "original_submission.txt"
        require_hash(raw_path, identity["original_submission_sha256"])
        ticker = identity["ticker"]
        accession_by_ticker[ticker] = identity["accession"]
        originals.append({
            "ticker": ticker, "accession": identity["accession"],
            "form": "10-Q", "section": "COMPLETE_SUBMISSION_SOURCE",
            "report_period_end": identity["report_period_end"],
            "filing_date": identity["filing_date"],
            "raw_sha256": identity["original_submission_sha256"],
            "source_identity_sha256": file_sha256(identity_path),
            "version_status": identity["status"],
        })
    if not originals or len(accession_by_ticker) != len(originals):
        raise ValueError("M8 source inventory missing or duplicate ticker")
    m7_originals = []
    if m7_root is not None:
        m7_versions: dict[tuple[str, str], list[dict]] = {}
        for ledger_path in sorted((m7_root / "tenq").glob("pilot_v*/selection_ledger.json")):
            selected = read_json(ledger_path)["selected"]
            pilot_dir = ledger_path.parent
            raw_path = pilot_dir / "original_submission.txt"
            expected = selected["response"]["sha256"]
            require_hash(raw_path, expected)
            accession = selected["raw_header"]["accession"]
            if selected["submission_row"]["accessionNumber"] != accession:
                raise ValueError("M7 accession/selection mismatch")
            m7_versions.setdefault((accession, expected), []).append({
                "pilot_version": pilot_dir.name,
                "receipt_sha256": file_sha256(pilot_dir / "receipt.json"),
                "receipt_status": read_json(pilot_dir / "receipt.json")["status"],
            })
        for (accession, digest), versions in sorted(m7_versions.items()):
            selected = read_json(m7_root / "tenq" / versions[-1]["pilot_version"] /
                                 "selection_ledger.json")["selected"]
            m7_originals.append({
                "ticker": "AAPL", "accession": accession, "form": "10-Q",
                "section": "COMPLETE_SUBMISSION_SOURCE_FOR_ITEM_2_PILOT",
                "report_period_end": selected["submission_row"]["reportDate"],
                "filing_date": selected["submission_row"]["filingDate"],
                "raw_sha256": digest, "physical_pilot_copies": len(versions),
                "pilot_versions": versions,
                "status": versions[-1]["receipt_status"],
            })
        if not m7_originals or any(row["ticker"] in accession_by_ticker for row in m7_originals):
            raise ValueError("M7 pilot is missing or duplicates an M8 ticker")
    groups: dict[tuple[str, str], list[str]] = {}
    for path in sorted(m9_root.rglob("*structured*text*.txt")):
        ticker = path.name.split("_", 1)[0].upper()
        if ticker not in accession_by_ticker:
            raise ValueError("M9 derivative has no verified M8 source ticker")
        groups.setdefault((ticker, file_sha256(path)), []).append(
            path.relative_to(m9_root).as_posix())
    derivatives = [
        {"ticker": ticker, "source_accession": accession_by_ticker[ticker],
         "source_form": "10-Q", "section": "PART_I_ITEM_2_MDA_DERIVATIVE",
         "sha256": digest, "physical_copies": len(labels),
         "source_labels": labels, "status": "TRANSFORMED_NOT_INDEPENDENT_FILING"}
        for (ticker, digest), labels in sorted(groups.items())
    ]
    return {"scope": "NON_ELIGIBLE_FOR_10K_ITEM1_M5_CANDIDATE",
            "m7_original_10q_count": len(m7_originals), "m7_original_10q": m7_originals,
            "m8_original_10q_count": len(originals), "m8_original_10q": originals,
            "m8_receipt_sha256": file_sha256(m8_root / "receipt.json"),
            "m9_structured_item2_distinct_derivative_count": len(derivatives),
            "m9_structured_item2_physical_copy_count": sum(
                row["physical_copies"] for row in derivatives),
            "m9_structured_item2_derivatives": derivatives,
            "note": "Quarterly Item 2 and transformed derivatives do not increase the five independent annual Item 1 samples"}


def audit(corpus_path: Path, m4_run: Path, m4_config_path: Path,
          calendar_comparison_path: Path | None = None,
          text_inventory_root: Path | None = None,
          sec_cache_dir: Path | None = None,
          m8_root: Path | None = None,
          m9_root: Path | None = None,
          m7_root: Path | None = None) -> dict:
    corpus_path, m4_run, m4_config_path = (
        corpus_path.resolve(), m4_run.resolve(), m4_config_path.resolve())
    corpus = read_json(corpus_path)
    entries = corpus["entries"]
    if not entries or len({e["ticker"] for e in entries}) != len(entries):
        raise ValueError("Corpus has no entries or duplicate ticker")
    if any(e["status"] != "INCLUDED" for e in entries):
        raise ValueError("Corpus contains an unavailable filing")
    manifest_path = m4_run / "manifest.json"
    manifest = read_json(manifest_path)
    require_hash(m4_config_path, manifest["m4_config_sha256"])
    if not any(row.get("sha256") == file_sha256(corpus_path) for row in manifest["inputs"]):
        raise ValueError("M4 manifest does not bind the supplied M1 corpus")
    output_hashes = {Path(row["path"]).name: row["sha256"] for row in manifest["outputs"]}
    masks_path = m4_run / "common_masks.json"
    masks = read_json(masks_path)
    dates = masks["date_order"]
    if (not dates or dates != sorted(set(dates))
            or masks["ticker_order"] != [e["ticker"] for e in entries]
            or len(masks["complete_date_mask"]) != len(dates)
            or len(masks["observed_return_mask_TxN"]) != len(dates)
            or any(len(row) != len(entries) for row in masks["observed_return_mask_TxN"])
            or len(masks["company_price_available"]) != len(entries)):
        raise ValueError("M4 common date, ticker or return-mask structure is invalid")
    if not all(masks["company_price_available"]):
        raise ValueError("At least one price series is unavailable")

    firm_rows = []
    for entry in entries:
        ticker = entry["ticker"]
        filing_dir = Path(entry["filing_dir"]).resolve()
        item_path = filing_dir / "item1_business.txt"
        if Path(entry["item1_path"]).resolve() != item_path:
            raise ValueError(f"Item 1 path mismatch for {ticker}")
        if not filing_dir.is_relative_to(corpus_path.parent):
            raise ValueError(f"Filing directory outside corpus run for {ticker}")
        require_hash(item_path, entry["item1_sha256"])
        raw_path = filing_dir / "original_submission.txt"
        require_hash(raw_path, entry["raw_sha256"])
        filing = read_json(filing_dir / "filing_metadata.json")
        item_meta = read_json(filing_dir / "item1_metadata.json")
        if (filing["accession"] != entry["accession"] or filing["cik"] != entry["cik"]
                or filing["form"] != "10-K" or item_meta["item1_sha256"] != entry["item1_sha256"]
                or item_meta["source_raw_sha256"] != entry["raw_sha256"]):
            raise ValueError(f"Filing, Item 1 or corpus identity mismatch for {ticker}")
        raw_header = raw_path.open("rb").read(10000)
        acceptance = re.search(rb"<ACCEPTANCE-DATETIME>(\d{14})", raw_header)
        if (acceptance is None or acceptance.group(1).decode("ascii") !=
                filing["raw_sec_header"]["acceptance_datetime_raw_header"]):
            raise ValueError(f"Raw SEC acceptance header mismatch for {ticker}")

        bars_path = m4_run / "market" / f"bars_{ticker}.json"
        bars_hash = file_sha256(bars_path)
        if output_hashes.get(bars_path.name) != bars_hash:
            raise ValueError(f"M4 manifest/bar hash mismatch for {ticker}")
        bars = read_json(bars_path)
        if (bars["ticker"] != ticker or bars["adjusted"] is not True
                or bars["price_field"] != "c"
                or bars["adjustment_scope"] != "splits only; excludes dividend total return"):
            raise ValueError(f"Price identity or adjustment mismatch for {ticker}")
        quality = validate_bars(bars["bars"], dates)
        receipt_path = (m4_run / "market" / "raw" / f"bars_{ticker}" /
                        "page_001_attempt_01.receipt.json")
        receipt = read_json(receipt_path)
        raw_price_path = receipt_path.parent / "page_001_attempt_01.json"
        if (Path(receipt["raw_path"]).resolve() != raw_price_path.resolve()
                or receipt["provider"] != "Massive REST" or receipt["http_status"] != 200):
            raise ValueError(f"Raw price receipt identity mismatch for {ticker}")
        require_hash(raw_price_path, receipt["sha256"])
        identity_path = m4_run / "market" / f"identity_{ticker}.json"
        identity = read_json(identity_path)
        if (identity["sec_cik"] != entry["cik"]
                or identity["massive"]["cik"] != entry["cik"]
                or identity["massive"]["ticker"] != ticker):
            raise ValueError(f"Security identity mismatch for {ticker}")
        row = {
            "ticker": ticker, "cik": entry["cik"], "accession": entry["accession"],
            "report_period_end": filing["report_date"],
            "official_filing_date": filing["filing_date"],
            "raw_sec_acceptance_et": filing["raw_sec_header"]["acceptance_datetime_eastern"],
            "submissions_api_acceptance": filing["acceptance_datetime_sec_source"],
            "api_minus_raw_header_seconds": filing["api_minus_raw_header_seconds"],
            "exact_first_public_utc": None,
            "exact_first_public_utc_reason": "Acceptance is not a verified first-public dissemination instant",
            "point_in_time_item1_vintage": None,
            "point_in_time_item1_vintage_reason": "Saved 2026 extraction has no verified decision-time PAC/content revision history",
            "item1_sha256": entry["item1_sha256"],
            "original_submission_sha256": entry["raw_sha256"],
            "filing_metadata_sha256": file_sha256(filing_dir / "filing_metadata.json"),
            "item1_metadata_sha256": file_sha256(filing_dir / "item1_metadata.json"),
            "text_retrieved_utc": filing["raw_retrieval"]["retrieved_utc"],
            "saved_original_10k_count": 1, "saved_distinct_item1_count": 1,
            "saved_submissions_versions": saved_submission_versions(filing, sec_cache_dir),
            "saved_price": {
                "provider": receipt["provider"], "frequency": "daily",
                "processed_sha256": bars_hash, "raw_response_sha256": receipt["sha256"],
                "raw_receipt_sha256": file_sha256(receipt_path),
                "retrieved_utc": receipt["retrieved_utc"],
                "adjusted": True, "adjustment_scope": bars["adjustment_scope"],
                "dividend_total_return_available": False,
                "dividend_total_return_reason": "No dividend or corporate-action cashflow series in this saved M4 dataset",
                "identity_sha256": file_sha256(identity_path),
                "reference_as_of": identity["as_of"],
                "reference_last_updated_utc": identity["massive"].get("last_updated_utc"),
                "composite_figi": identity["massive"].get("composite_figi"),
                "share_class_figi": identity["massive"].get("share_class_figi"),
                **quality,
            },
            "five_session_close_pair": event_coverage(dates, filing["filing_date"], 5),
            "twenty_session_close_pair": event_coverage(dates, filing["filing_date"], 20),
        }
        firm_rows.append(row)

    inventory = None
    if text_inventory_root is not None:
        copies = sorted(text_inventory_root.resolve().rglob("item1_business.txt"))
        by_firm = {e["ticker"]: set() for e in entries}
        for path in copies:
            ticker = path.parent.name
            if ticker in by_firm:
                by_firm[ticker].add(file_sha256(path))
        inventory = {"physical_item1_copies": len(copies),
                     "distinct_item1_sha256_by_firm": {k: sorted(v) for k, v in by_firm.items()},
                     "note": "Copies across runs and evidence snapshots are not independent filings"}

    calendar = {"saved_common_date_count": len(dates),
                "common_date_start": dates[0], "common_date_end": dates[-1],
                "timezone_for_saved_daily_dates": "America/New_York",
                "independent_point_in_time_exchange_calendar": None,
                "independent_point_in_time_exchange_calendar_reason":
                    "No versioned exchange session/calendar file was bound to the M4 price run"}
    if calendar_comparison_path is not None and calendar_comparison_path.exists():
        calendar["retained_calendar_comparison_sha256"] = file_sha256(calendar_comparison_path)
        calendar["retained_calendar_comparison_status"] = "descriptive saved weekday comparison only"

    return {
        "schema": "m5_local_data_availability_round7_v1",
        "status": "LOCAL_AVAILABILITY_AUDIT_ONLY_NO_FINANCIAL_EXPERIMENT",
        "audit_code_sha256": file_sha256(Path(__file__)),
        "source_labels": {
            "m1_corpus_index": f"m1-run:{corpus_path.parent.name}/corpus_index.json",
            "m4_manifest": f"m4-run:{m4_run.name}/manifest.json",
            "m4_config": f"config:{m4_config_path.name}",
            "m4_common_masks": f"m4-run:{m4_run.name}/common_masks.json",
        },
        "sources": {"m1_corpus_index_sha256": file_sha256(corpus_path),
                    "m4_manifest_sha256": file_sha256(manifest_path),
                    "m4_config_sha256": file_sha256(m4_config_path),
                    "m4_common_masks_sha256": file_sha256(masks_path)},
        "counts": {"companies": len(entries),
                   "independent_original_10k_filings": len({x["accession"] for x in firm_rows}),
                   "independent_item1_texts": len({x["item1_sha256"] for x in firm_rows}),
                   "common_price_dates": len(dates),
                   "available_stock_date_price_rows": len(dates) * len(entries),
                   "saved_close_return_mask_rows": sum(sum(bool(v) for v in row)
                                                        for row in masks["observed_return_mask_TxN"]),
                   "five_session_filing_events_with_saved_close_pair":
                       sum(x["five_session_close_pair"]["close_pair_exists"] for x in firm_rows),
                   "financial_prediction_observations_approved": None,
                   "financial_prediction_observations_approved_reason":
                       "Public-time/text-vintage, label, universe and split policies are not frozen"},
        "text_inventory": inventory,
        "other_local_texts": other_local_texts(m8_root, m9_root, m7_root),
        "calendar": calendar,
        "firms": firm_rows,
        "time_and_universe_gaps": {
            "exact_first_public_dissemination": None,
            "exact_first_public_dissemination_reason": "SEC acceptance and filing date do not establish the first public instant",
            "point_in_time_correction_and_text_vintage": None,
            "point_in_time_correction_and_text_vintage_reason": "No complete PAC/content revision archive bound to each saved Item 1",
            "dividend_cashflows": None,
            "dividend_cashflows_reason": "No local dividend series; split adjustment does not give total return",
            "historical_delisted_security_coverage": None,
            "historical_delisted_security_coverage_reason": "Five hand-selected current symbols do not form a point-in-time universe",
            "historical_ticker_identifier_changes": None,
            "historical_ticker_identifier_changes_reason": "Saved reference snapshots do not establish a complete historical identifier chain",
        },
        "interpretation": "Price rows and possible future close pairs are structural availability counts, not independent text samples, approved labels, predictions or backtests",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--m4-run", type=Path, required=True)
    parser.add_argument("--m4-config", type=Path, required=True)
    parser.add_argument("--calendar-comparison", type=Path)
    parser.add_argument("--text-inventory-root", type=Path)
    parser.add_argument("--sec-cache-dir", type=Path)
    parser.add_argument("--m8-root", type=Path)
    parser.add_argument("--m9-root", type=Path)
    parser.add_argument("--m7-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.corpus, args.m4_run, args.m4_config,
                   args.calendar_comparison, args.text_inventory_root,
                   args.sec_cache_dir, args.m8_root, args.m9_root, args.m7_root)
    result["audit_recorded_utc"] = datetime.now(timezone.utc).isoformat()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise ValueError("Refusing to overwrite an existing audit")
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(f"AUDIT_ONLY companies={result['counts']['companies']} "
          f"texts={result['counts']['independent_item1_texts']} "
          f"price_rows={result['counts']['available_stock_date_price_rows']}")


if __name__ == "__main__":
    main()
