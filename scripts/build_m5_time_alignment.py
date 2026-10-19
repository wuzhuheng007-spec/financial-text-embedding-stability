"""Build a descriptive SEC-text/price chronology from saved M1 and M4 evidence.

This is a provenance check, not a point-in-time availability certification or
financial experiment. It never contacts a provider or changes a frozen run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def checked_hash(path: Path, expected: str) -> str:
    actual = sha256(path)
    if actual != expected:
        raise ValueError(f"SHA-256 mismatch: {path}")
    return actual


def header_value(raw: bytes, field: str) -> str:
    match = re.search(rf"(?m)^{re.escape(field)}:\s*([^\r\n]+)", raw[:10000].decode("latin-1"))
    if not match:
        raise ValueError(f"SEC header missing {field}")
    return match.group(1).strip()


def saved_submissions_scan(filing: dict, cache_dir: Path) -> dict:
    """Check same-period 10-K rows only within saved 2026 submissions snapshots."""
    sources, matches = [], []
    for source in filing["submissions_sources"]:
        paths = [p.with_suffix(".bin") for p in cache_dir.glob("*.json")
                 if (receipt := read_json(p)).get("url") == source["url"]
                 and receipt.get("sha256") == source["sha256"]]
        if len(paths) != 1:
            raise ValueError(f"SEC submissions cache missing/ambiguous: {source['url']}")
        path = paths[0]
        checked_hash(path, source["sha256"])
        payload = read_json(path)
        columns = payload.get("filings", {}).get("recent", payload)
        for i, accession in enumerate(columns["accessionNumber"]):
            form, period = columns["form"][i], columns["reportDate"][i]
            if accession == filing["accession"] or (form in {"10-K", "10-K/A"} and period == filing["report_date"]):
                matches.append({"accession": accession, "form": form, "report_date": period,
                                "filing_date": columns["filingDate"][i],
                                "acceptance_source": columns["acceptanceDateTime"][i]})
        sources.append({"url": source["url"], "sha256": source["sha256"],
                        "retrieved_utc": source["retrieved_utc"],
                        "rows_scanned": len(columns["accessionNumber"])})
    originals = [m for m in matches if m["accession"] == filing["accession"]]
    if len(originals) != 1 or originals[0]["form"] != "10-K":
        raise ValueError(f"Original accession absent from saved submissions: {filing['accession']}")
    return {"sources": sources, "same_period_10k_rows": matches,
            "same_period_10ka_count": sum(m["form"] == "10-K/A" for m in matches)}


def build(corpus_path: Path, m4_run: Path, cache_dir: Path = Path(".cache/sec")) -> dict:
    corpus = read_json(corpus_path)
    m4_manifest_path = m4_run / "manifest.json"
    m4_manifest = read_json(m4_manifest_path)
    config_path = Path("configs/m4_demo.yaml")
    checked_hash(config_path, m4_manifest["m4_config_sha256"])
    masks = read_json(m4_run / "common_masks.json")
    date_order = masks["date_order"]
    if not date_order or date_order != sorted(set(date_order)):
        raise ValueError("M4 date order is missing, unsorted or duplicated")
    if set(masks["ticker_order"]) != {e["ticker"] for e in corpus["entries"]}:
        raise ValueError("M1 and M4 company sets differ")

    rows = []
    for entry in corpus["entries"]:
        if entry["status"] != "INCLUDED":
            raise ValueError(f"Non-included corpus entry: {entry['ticker']}")
        ticker = entry["ticker"]
        filing_dir = Path(entry["filing_dir"])
        filing = read_json(filing_dir / "filing_metadata.json")
        item = read_json(filing_dir / "item1_metadata.json")
        raw_path = filing_dir / "original_submission.txt"
        checked_hash(raw_path, entry["raw_sha256"])
        checked_hash(filing_dir / "item1_business.txt", entry["item1_sha256"])
        if (filing["accession"] != entry["accession"]
                or item["source_raw_sha256"] != entry["raw_sha256"]
                or item["item1_sha256"] != entry["item1_sha256"]):
            raise ValueError(f"M1 accession/source mismatch: {ticker}")
        raw = raw_path.read_bytes()
        acceptance_match = re.search(rb"<ACCEPTANCE-DATETIME>(\d{14})", raw[:10000])
        if not acceptance_match:
            raise ValueError(f"SEC header acceptance missing: {ticker}")
        header = {
            "accession": header_value(raw, "ACCESSION NUMBER"),
            "form": header_value(raw, "CONFORMED SUBMISSION TYPE"),
            "report_date": header_value(raw, "CONFORMED PERIOD OF REPORT"),
            "filed_as_of": header_value(raw, "FILED AS OF DATE"),
            "date_as_of_change": header_value(raw, "DATE AS OF CHANGE"),
            "acceptance_raw": acceptance_match.group(1).decode("ascii"),
        }
        if (header["accession"] != filing["accession"] or header["form"] != "10-K"
                or header["report_date"] != filing["report_date"].replace("-", "")
                or header["filed_as_of"] != filing["filing_date"].replace("-", "")
                or header["acceptance_raw"] != filing["raw_sec_header"]["acceptance_datetime_raw_header"]):
            raise ValueError(f"SEC raw header identity/date mismatch: {ticker}")
        revisions = saved_submissions_scan(filing, cache_dir)
        bars_path = m4_run / "market" / f"bars_{ticker}.json"
        bars = read_json(bars_path)
        bar_dates = [bar["date_et"] for bar in bars["bars"]]
        if bar_dates != date_order:
            raise ValueError(f"Price dates differ from M4 date mask: {ticker}")
        receipt_path = m4_run / "market" / "raw" / f"bars_{ticker}" / "page_001_attempt_01.receipt.json"
        receipt = read_json(receipt_path)
        checked_hash(Path(receipt["raw_path"]), receipt["sha256"])
        if receipt["http_status"] != 200 or bars["adjusted"] is not True:
            raise ValueError(f"Unexpected price source/adjustment: {ticker}")
        header_et = filing["raw_sec_header"]["acceptance_datetime_eastern"]
        next_sessions = [date for date in bar_dates if date > filing["filing_date"]]
        snapshot_utc = filing["raw_retrieval"]["retrieved_utc"]
        rows.append({
            "ticker": ticker,
            "cik": entry["cik"],
            "accession": entry["accession"],
            "report_period_end": filing["report_date"],
            "filing_date": filing["filing_date"],
            "sec_raw_header_acceptance_et": header_et,
            "submissions_api_acceptance_source": filing["acceptance_datetime_sec_source"],
            "api_minus_raw_header_seconds": filing["api_minus_raw_header_seconds"],
            "raw_header_fields": header,
            "saved_submissions_revision_scan": revisions,
            "post_acceptance_correction_evidence": "DATE AS OF CHANGE equals the raw acceptance calendar date; same-day correction and later content revision are not ruled out. No PAC history or content-vintage archive verified.",
            "exact_first_public_instant": None,
            "text_snapshot_retrieved_utc": snapshot_utc,
            "text_raw_sha256": entry["raw_sha256"],
            "item1_sha256": entry["item1_sha256"],
            "sec_archive_url": filing["archive_url"],
            "price_window_configured": [masks["window_start"], masks["window_end"]],
            "price_window_observed": [bar_dates[0], bar_dates[-1]],
            "price_bar_count": len(bar_dates),
            "proposed_conservative_session": next_sessions[0] if next_sessions else None,
            "proposed_proxy_assumptions": "Original filing disseminated by official filing-date end; decision-time Item 1 content unchanged by correction; first regular-session open strictly after filing date; historical security/price vintages valid. Unapproved.",
            "price_processed_sha256": sha256(bars_path),
            "price_raw_sha256": receipt["sha256"],
            "price_raw_retrieved_utc": receipt["retrieved_utc"],
            "price_2025_role": "UNASSIGNED: M4 price-only retrospective engineering input. Possible formal roles: trailing feature, future label, static comparison.",
            "unsupported_historical_conclusions": ["exact first public timestamp", "absence of same-day PAC/content revision", "point-in-time identity of 2026 saved text", "predictive validity or text incremental value"],
            "time_check": "HISTORICAL_FILING_VERIFIED; EXACT_PUBLICATION_AND_TEXT_VINTAGE_UNVERIFIED; PRICE_ROLE_UNASSIGNED",
        })
    return {
        "status": "DESCRIPTIVE_HISTORICAL_FILING_AUDIT_ONLY",
        "corpus_index_sha256": sha256(corpus_path),
        "m4_manifest_sha256": sha256(m4_manifest_path),
        "m4_config_sha256": sha256(config_path),
        "time_policy": "2026 download is snapshot provenance, not evidence of 2025 unavailability. Raw SEC filing and acceptance fields verify historical filing identity, not exact publication or unchanged text vintage. The 2025 price role is unassigned.",
        "rows": rows,
    }


def markdown(data: dict) -> str:
    lines = [
        "# M5 preparation: corrected SEC filing–price chronology (19 October 2026)",
        "",
        "**Historical original 10-K filing identity is verified for all five. Exact first public dissemination and point-in-time identity of the saved Item 1 text are unverified.** The 2026 download date is snapshot provenance, not evidence of 2025 unavailability. The previous v1 report is retained as superseded evidence.",
        "",
        "| Firm / accession | Period; raw-header acceptance ET; filing / change date | Download UTC; exact first public instant | Revision/correction evidence | Conservative candidate ET open | 2025 price role / limit |",
        "|---|---|---|---|---|---|",
    ]
    for row in data["rows"]:
        scan = row["saved_submissions_revision_scan"]
        lines.append(
            f"| {row['ticker']} / `{row['accession']}` | {row['report_period_end']}; {row['sec_raw_header_acceptance_et']}; "
            f"{row['filing_date']} / {row['raw_header_fields']['date_as_of_change']} | "
            f"{row['text_snapshot_retrieved_utc']}; **unknown** | "
            f"saved submissions: {len(scan['same_period_10k_rows'])} same-period 10-K row(s), "
            f"{scan['same_period_10ka_count']} 10-K/A; same-date change header; PAC not excluded | "
            f"{row['proposed_conservative_session']} (proposal only) | unassigned; exact public time and text vintage unverified |"
        )
    lines += [
        "",
        "## Interpretation and proposed time rules",
        "",
        "The raw headers match M1 accession, Form 10-K, report period, filing date and acceptance fields. The JSON lists raw/source SHA-256 and all saved submissions-page hashes. The revision scan covers only those 2026 SEC snapshots and same-period rows: zero matching 10-K/A rows does **not** rule out same-day post-acceptance correction or another later change. `DATE AS OF CHANGE` equals the acceptance calendar date in these headers but is not a full PAC history. The saved submissions API acceptance strings differ from raw-header Eastern instants by 4–5 hours; both are retained. Exact first public timestamps are unavailable in these artifacts. After-5:30 p.m. ET submissions may be assigned the next business-day filing date and disseminated then. Thus the proposed conservative proxy is the first regular-session open **strictly after the official filing date**, assuming publication by filing-date end and unchanged decision-time Item 1 content; these assumptions require an approved evidence/revision policy.",
        "",
        "Proposed sensitivity: compare this rule with one and two additional trading-session lags while holding text, universe and outcome fixed. An earlier same-day rule requires exact dissemination evidence. No rule is frozen.",
        "",
        "M4's observed 2025 price window was 2025-01-02–2025-12-31, with 250 daily bars per security, as a retrospective **price-only** PCA engineering input. Its formal role is **UNASSIGNED**. For trailing features, use only bars before each decision and fit transforms on past data. For labels, use a predeclared post-decision horizon. For static comparison, full-window price geometry may be described without predictive or contemporaneous information claims. No blanket valid/invalid pairing follows before role selection. Bars are split-adjusted and omit dividends.",
        "",
        "## Traceability and check command",
        "",
        f"- M1 corpus index SHA-256: `{data['corpus_index_sha256']}`.",
        f"- M4 manifest SHA-256: `{data['m4_manifest_sha256']}`; M4 config SHA-256: `{data['m4_config_sha256']}`.",
        "- The companion JSON records raw filing and Item 1 hashes, SEC archive URLs, 2026 submissions-source receipts and scanned matching rows, raw/processed price hashes, and unsupported conclusions.",
        "- Rebuild: `python scripts/build_m5_time_alignment.py --corpus artifacts/runs/20261019T003627Z-d3043c3e/corpus_index.json --m4-run artifacts/runs/20261019T004950Z-828af5af`.",
        "- Source policy: [SEC Webmaster FAQ](https://www.sec.gov/about/webmaster-frequently-asked-questions), [SEC after-hours filing status](https://www.sec.gov/submit-filings/filer-support-resources/how-do-i-guides/determine-status-my-filing), [EDGAR correction/index policy](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data), [Massive split/dividend FAQ](https://massive.com/knowledge-base/article/is-massives-stock-data-adjusted-for-splits-or-dividends).",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--m4-run", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/sec"))
    parser.add_argument("--output-json", type=Path, default=Path("reports/2026-10-19_text_price_time_alignment.json"))
    parser.add_argument("--output-md", type=Path, default=Path("reports/2026-10-19_text_price_time_alignment.md"))
    args = parser.parse_args()
    data = build(args.corpus, args.m4_run, args.cache_dir)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_md.write_text(markdown(data), encoding="utf-8")
    print(f"HISTORICAL_FILING_VERIFIED: {len(data['rows'])} companies; public instant/text vintage unverified")


if __name__ == "__main__":
    main()
