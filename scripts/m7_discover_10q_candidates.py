"""Metadata-first five-company 2025 original 10-Q candidate ledger.

Only official SEC metadata and selected primary HTML are retrieved. This does
not extract/review MD&A or run embeddings; AAPL reuses the frozen M7 snapshot.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import csv
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import sys

from finembed.config import load_config
from finembed.qpilot import select_earliest_original
from finembed.run import sha256, write_json
from finembed.sec import SecClient, SecError, contact_from_environment, _rows


ORDER = ("AAPL", "MSFT", "AMZN", "GOOGL", "META")
YEAR = 2025
DATE_START = date(YEAR, 1, 1)
DATE_END = date(YEAR, 12, 31)
DOCNAME = re.compile(r"[A-Za-z0-9_.-]+\Z")
HISTORY_NAME = re.compile(r"CIK\d{10}-submissions-\d{3}\.json\Z")


class Visible(HTMLParser):
    """Lightweight heading-presence/format survey, never a section extractor."""
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "head", "ix:hidden"}:
            self.skip += 1
        if tag in {"p", "div", "br", "tr", "h1", "h2", "h3", "h4", "section"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "head", "ix:hidden"} and self.skip:
            self.skip -= 1
        if tag in {"p", "div", "tr", "h1", "h2", "h3", "h4", "section"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def file_info(path: Path) -> dict:
    return {"path": str(path), "bytes": path.stat().st_size,
            "sha256": sha256(path.read_bytes())}


def require(check: bool, message: str) -> None:
    if not check:
        raise ValueError(message)


def _simple(row: dict) -> dict:
    return {k: row.get(k) for k in ("form", "accessionNumber", "filingDate",
            "reportDate", "acceptanceDateTime", "primaryDocument")}


def _fiscal_field(html: str, name: str) -> str | None:
    pattern = rf'<ix:nonNumeric\b[^>]*\bname\s*=\s*[\"\']dei:{name}[\"\'][^>]*>(.*?)</ix:nonNumeric>'
    match = re.search(pattern, html, re.I | re.S)
    if not match:
        return None
    value = re.sub(r"<[^>]+>", "", match.group(1)).strip().upper()
    return value if re.fullmatch(r"[A-Z0-9-]{1,16}", value) else None


def survey_primary_html(html_bytes: bytes, report_date: str,
                        prior_period_end: str | None) -> dict:
    """Return a source-backed format survey; no section boundary is accepted."""
    html = html_bytes.decode("utf-8", errors="strict")
    parser = Visible()
    parser.feed(html)
    parser.close()
    visible = " ".join(" ".join(parser.parts).replace("’", "'").split())
    title_matches = list(re.finditer(r"management['’]s\s+discussion\s+and\s+analysis",
                                     visible, re.I))
    heading_pairs = list(re.finditer(
        r"\bitem\s*2\s*[.·:\-]?\s*management['’]s\s+discussion\s+and\s+analysis",
        visible, re.I))
    # A duration match must occur inside one XBRL period, not merely anywhere.
    periods: set[tuple[str, str]] = set()
    period_matches = []
    for block in re.finditer(r"<xbrli:period\b[^>]*>.*?</xbrli:period>", html, re.I | re.S):
        start_match = re.search(r"<xbrli:startDate>\s*(\d{4}-\d{2}-\d{2})\s*</xbrli:startDate>", block.group(0), re.I)
        end_match = re.search(r"<xbrli:endDate>\s*(\d{4}-\d{2}-\d{2})\s*</xbrli:endDate>", block.group(0), re.I)
        if not start_match or not end_match:
            continue
        pair = (start_match.group(1), end_match.group(1))
        periods.add(pair)
        context_start = html.lower().rfind("<xbrli:context", 0, block.start())
        context_header = html[context_start:block.start()] if context_start >= 0 else ""
        id_match = re.search(r'\bid\s*=\s*[\"\']([^\"\']+)', context_header, re.I)
        period_matches.append({"start": pair[0], "end": pair[1],
                               "context_id_candidate": id_match.group(1) if id_match else None,
                               "html_decoded_char_start": block.start(),
                               "html_decoded_char_end_exclusive": block.end(),
                               "period_substring_sha256": sha256(block.group(0).encode("utf-8"))})
    start = (date.fromisoformat(prior_period_end) + timedelta(days=1)).isoformat() if prior_period_end else None
    matched = [p for p in period_matches if (p["start"], p["end"]) == (start, report_date)] if start else []
    return {"mda_title_occurrences_in_visible_primary": len(title_matches),
            "item2_mda_heading_pair_occurrences_in_parser_text": len(heading_pairs),
            "mda_title_presence_status": "ITEM2_MDA_HEADER_TEXT_PRESENT_BOUNDARY_UNREVIEWED" if heading_pairs else
            "TITLE_NOT_FOUND_NEEDS_REVIEW",
            "fiscal_period_focus": _fiscal_field(html, "DocumentFiscalPeriodFocus"),
            "fiscal_year_focus": _fiscal_field(html, "DocumentFiscalYearFocus"),
            "coverage_start_proxy": start, "coverage_end_report_date": report_date,
            "coverage_start_basis": "day after prior official 10-Q/10-K reportDate; XBRL duration checked separately"
            if start else "UNKNOWN_NO_PRIOR_PERIOD_END",
            "xbrl_exact_start_end_duration_context_found": (start, report_date) in periods if start else None,
            "matching_xbrl_duration_contexts_first_5": matched[:5],
            "xbrl_offset_units": "Python character offsets in UTF-8-decoded saved primary HTML, not rendered text or raw complete-submission bytes",
            "xbrl_duration_pair_count": len(periods),
            "inline_ixbrl_tag_count": len(re.findall(r"<ix:", html, re.I)),
            "html_table_open_count": len(re.findall(r"<table\b", html, re.I)),
            "page_break_mentions": html.lower().count("page-break"),
            "css_display_none_mentions": len(re.findall(r"display\s*:\s*none", html, re.I)),
            "utf8_replacement_characters": html.count("\ufffd"),
            "survey_limit": "Heading presence and XBRL context do not validate MD&A body boundary, table reading order, or all disclosures."}


def _seed_verified_cache(source: Path, target: Path) -> list[dict]:
    """Copy only complete URL-keyed SEC cache entries that rehash exactly."""
    require(source.is_dir(), f"Prior cache directory missing: {source}")
    copied = []
    target.mkdir(parents=True, exist_ok=True)
    for meta_path in sorted(source.glob("*.json")):
        body_path = source / f"{meta_path.stem}.bin"
        require(body_path.is_file(), f"Prior cache body missing: {body_path}")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        body = body_path.read_bytes()
        require(hashlib.sha256(str(meta.get("url", "")).encode("utf-8")).hexdigest() ==
                meta_path.stem and meta.get("sha256") == sha256(body) and
                meta.get("bytes") == len(body), f"Prior cache entry failed integrity: {meta_path}")
        require(not (target / meta_path.name).exists() and not (target / body_path.name).exists(),
                "New candidate cache destination already contains source URL")
        shutil.copyfile(meta_path, target / meta_path.name)
        shutil.copyfile(body_path, target / body_path.name)
        copied.append({"url": meta["url"], "prior_body": file_info(body_path),
                       "prior_meta": file_info(meta_path), "retrieved_utc": meta.get("retrieved_utc")})
    return copied


def _save_response(client: SecClient, url: str, target: Path) -> tuple[bytes, dict]:
    body, meta = client.get(url)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    require(sha256(target.read_bytes()) == meta["sha256"] and target.stat().st_size == meta["bytes"],
            f"Saved SEC response differs from cache/network receipt: {url}")
    return body, {"url": url, "file": file_info(target),
                  "retrieved_utc": meta.get("retrieved_utc"),
                  "http_status": meta.get("http_status"), "cache_hit": meta.get("cache_hit")}


def _history_needed(main: dict) -> list[dict]:
    recent = main.get("filings", {}).get("recent", {})
    filing_dates = [date.fromisoformat(x) for x in recent.get("filingDate", []) if x]
    if filing_dates and min(filing_dates) <= DATE_START:
        return []
    required = []
    for record in main.get("filings", {}).get("files", []):
        name = str(record.get("name", ""))
        require(bool(HISTORY_NAME.fullmatch(name)), "Unexpected SEC historical submissions filename")
        start = date.fromisoformat(record["filingFrom"])
        end = date.fromisoformat(record["filingTo"])
        if start <= DATE_END and end >= DATE_START:
            required.append(record)
    require(bool(required), "2025 date range absent from recent and historical file metadata")
    return required


def _rows_from_saved(body: bytes, cik: str) -> list[dict]:
    payload = json.loads(body)
    require(str(payload.get("cik", "")).zfill(10) == cik, "Submissions CIK mismatch")
    return _rows(payload.get("filings", {}).get("recent", {}))


def _prior_report(rows: list[dict], report_end: str) -> dict | None:
    previous = [r for r in rows if r.get("form") in {"10-Q", "10-K"} and
                r.get("reportDate") and r["reportDate"] < report_end]
    return _simple(max(previous, key=lambda r: (r["reportDate"], r.get("filingDate", "")))) if previous else None


def discover(config_path: Path, aapl_pilot: Path, output: Path,
             seed_cache_from: Path | None = None) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    receipt = {"kind": "M7_FIVE_COMPANY_10Q_METADATA_CANDIDATES", "status": "RUNNING",
               "started_utc": datetime.now(timezone.utc).isoformat(),
               "selection_rule": "calendar-2025 earliest filingDate among original 10-Q; if same-day originals >1, raw-header acceptance then accession required before selection",
               "no_batch_inference": True, "human_review": "PENDING_HUMAN_REVIEW"}
    write_json(output / "receipt.json", receipt)
    try:
        config = load_config(config_path)
        require(tuple(c.ticker for c in config.companies) == ORDER,
                "Configured five-company order changed")
        contact = contact_from_environment()  # Gate before any network call; never save it.
        receipt["verified_cache_seed"] = (_seed_verified_cache(seed_cache_from, output / "cache")
                                          if seed_cache_from else [])
        client = SecClient(replace(config, cache=output / "cache"), contact)
        old_receipt = json.loads((aapl_pilot / "receipt.json").read_text(encoding="utf-8"))
        require(old_receipt.get("status") == "DONE_BOUNDARY_PROGRAM_PASS_HUMAN_PENDING",
                "AAPL pilot is not the verified program candidate")
        for name, spec in old_receipt["outputs"].items():
            path = aapl_pilot / name
            require(path.is_file() and path.stat().st_size == spec["bytes"] and
                    sha256(path.read_bytes()) == spec["sha256"],
                    f"AAPL prior input receipt mismatch: {name}")
        old_selection = json.loads((aapl_pilot / "selection_ledger.json").read_text(encoding="utf-8"))
        old_boundary = json.loads((aapl_pilot / "boundary_evidence.json").read_text(encoding="utf-8"))
        require(old_selection["selected"]["submission_row"]["accessionNumber"] ==
                "0000320193-25-000008" and old_boundary["program_check"] ==
                "PASS_EXACT_SUBSTRING_AND_UNIQUE_SUBSTANTIVE_CANDIDATE", "AAPL saved candidate identity differs")
        ticker_path = aapl_pilot / "discovery" / "company_tickers_response.json"
        ticker_map = json.loads(ticker_path.read_bytes())
        mapping = {str(v.get("ticker", "")).upper(): f"{int(v['cik_str']):010d}"
                   for v in ticker_map.values() if isinstance(v, dict) and v.get("ticker") and v.get("cik_str")}
        source_records = [{"kind": "reused_saved_official_ticker_mapping", "file": file_info(ticker_path),
                           "source_receipt": file_info(aapl_pilot / "receipt.json")}]
        rows_out = []
        for ticker in ORDER:
            cik = mapping.get(ticker)
            require(cik is not None, f"Ticker missing in saved official SEC mapping: {ticker}")
            if ticker == "AAPL":
                require(cik == "0000320193", "Saved official AAPL ticker mapping changed")
                chosen = old_selection["selected"]["submission_row"]
                all_rows = old_selection["all_2025_10q_and_amendments"]
                prior = None
                # The previous original 10-K/10-Q row comes from the same saved
                # official submissions response, not an inferred fiscal calendar.
                aapl_main = json.loads((aapl_pilot / "discovery" /
                    "CIK0000320193_submissions_response.json").read_bytes())
                prior = _prior_report(_rows(aapl_main["filings"]["recent"]), chosen["reportDate"])
                html_path = aapl_pilot / "primary_document_source.html"
                html = html_path.read_bytes()
                source_records.append({"kind": "reused_saved_aapl_primary", "file": file_info(html_path),
                                       "selection_ledger": file_info(aapl_pilot / "selection_ledger.json")})
                source_receipts = old_selection["submissions_source_receipts"]
                tie_count = len(old_selection["same_day_original_candidates"])
                raw_status = "VERIFIED_IN_M7_AAPL_PILOT"
                candidate_status = "SELECTED_EXISTING_VERIFIED_PILOT"
            else:
                url = f"https://data.sec.gov/submissions/CIK{cik}.json"
                body, meta = _save_response(client, url, output / "sources" / ticker /
                                            f"CIK{cik}_submissions.json")
                source_receipts = [meta]
                payload = json.loads(body)
                require(str(payload.get("cik", "")).zfill(10) == cik,
                        f"Official submissions CIK differs from ticker mapping: {ticker}")
                all_submissions = _rows(payload["filings"]["recent"])
                for history in _history_needed(payload):
                    hurl = f"https://data.sec.gov/submissions/{history['name']}"
                    hbody, hmeta = _save_response(client, hurl, output / "sources" / ticker /
                                                  history["name"])
                    source_receipts.append(hmeta)
                    all_submissions.extend(_rows(json.loads(hbody)))
                tied, _ = select_earliest_original(all_submissions, YEAR)
                require(len(tied) == 1, f"Same-day original tie requires raw header resolution: {ticker}")
                chosen = _simple(tied[0])
                all_rows = [_simple(r) for r in all_submissions if r.get("form") in {"10-Q", "10-Q/A"}
                            and str(r.get("filingDate", "")).startswith(f"{YEAR}-")]
                prior = _prior_report(all_submissions, chosen["reportDate"])
                doc = chosen.get("primaryDocument")
                require(isinstance(doc, str) and bool(DOCNAME.fullmatch(doc)) and
                        doc not in {".", ".."}, f"Unsafe/missing SEC primaryDocument: {ticker}")
                accession = chosen["accessionNumber"]
                primary_url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                               f"{accession.replace('-', '')}/{doc}")
                html, primary_meta = _save_response(client, primary_url,
                    output / "sources" / ticker / doc)
                source_receipts.append(primary_meta)
                html_path = output / "sources" / ticker / doc
                tie_count = len(tied)
                raw_status = "NOT_RETRIEVED_RAW_HEADER_UNVERIFIED"
                candidate_status = "METADATA_CANDIDATE_PRIMARY_SURVEY_ONLY"
            survey = survey_primary_html(html, chosen["reportDate"],
                                          prior["reportDate"] if prior else None)
            amendments = [_simple(r) for r in (all_rows if ticker == "AAPL" else all_submissions)
                          if r.get("form") == "10-Q/A" and r.get("reportDate") == chosen["reportDate"]]
            accession = chosen["accessionNumber"]
            primary_url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                           f"{accession.replace('-', '')}/{chosen['primaryDocument']}")
            item = {"order": len(rows_out) + 1, "ticker": ticker, "cik": cik,
                    "accession": accession, "form": chosen["form"],
                    "filing_date": chosen["filingDate"], "report_period_end": chosen["reportDate"],
                    "submissions_acceptance_raw_string": chosen["acceptanceDateTime"],
                    "primary_document": chosen["primaryDocument"],
                    "primary_document_url": primary_url,
                    "primary_html": file_info(html_path),
                    "candidate_status": candidate_status,
                    "same_day_original_candidate_count": tie_count,
                    "raw_original_status": raw_status,
                    "quarter_start_prior_filing": prior,
                    "quarter_survey": survey,
                    "same_report_period_10q_amendments_in_saved_submissions": amendments,
                    "amendment_scope": "saved official submissions snapshots only; other corrections/publication changes unverified",
                    "all_2025_original_10q_and_amendments": all_rows,
                    "submissions_source_receipts": source_receipts,
                    "manual_check_needed": ["raw-header CIK/form/accession/time for new four",
                        "TOC versus substantive Part I Item 2 start/end", "HTML table and footnote reading order",
                        "quarter period label and context", "amendment/correction chronology"]}
            rows_out.append(item)
        write_json(output / "candidates.json", {
            "kind": "M7_FIVE_COMPANY_10Q_METADATA_CANDIDATE_LEDGER", "rule": receipt["selection_rule"],
            "company_order": list(ORDER), "human_review": "PENDING_HUMAN_REVIEW",
            "batch_representation": "NOT_RUN", "formal_financial_experiment": "NOT_RUN",
            "ticker_mapping_source": file_info(ticker_path), "source_records": source_records,
            "rows": rows_out})
        fields = ["order", "ticker", "cik", "accession", "form", "filing_date",
                  "report_period_end", "fiscal_year_focus", "fiscal_period_focus",
                  "coverage_start_proxy", "coverage_end", "xbrl_duration_match",
                  "primary_document", "primary_html_sha256", "mda_title_presence_status",
                  "mda_title_occurrences", "table_count", "ixbrl_tag_count", "page_break_mentions",
                  "same_period_amendment_count", "same_day_original_candidate_count",
                  "raw_original_status", "candidate_status", "manual_check_needed"]
        with (output / "candidates.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for r in rows_out:
                s = r["quarter_survey"]
                writer.writerow({"order": r["order"], "ticker": r["ticker"], "cik": r["cik"],
                    "accession": r["accession"], "form": r["form"], "filing_date": r["filing_date"],
                    "report_period_end": r["report_period_end"],
                    "fiscal_year_focus": s["fiscal_year_focus"],
                    "fiscal_period_focus": s["fiscal_period_focus"],
                    "coverage_start_proxy": s["coverage_start_proxy"],
                    "coverage_end": s["coverage_end_report_date"],
                    "xbrl_duration_match": s["xbrl_exact_start_end_duration_context_found"],
                    "primary_document": r["primary_document"],
                    "primary_html_sha256": r["primary_html"]["sha256"],
                    "mda_title_presence_status": s["mda_title_presence_status"],
                    "mda_title_occurrences": s["mda_title_occurrences_in_visible_primary"],
                    "table_count": s["html_table_open_count"],
                    "ixbrl_tag_count": s["inline_ixbrl_tag_count"],
                    "page_break_mentions": s["page_break_mentions"],
                    "same_period_amendment_count": len(r["same_report_period_10q_amendments_in_saved_submissions"]),
                    "same_day_original_candidate_count": r["same_day_original_candidate_count"],
                    "raw_original_status": r["raw_original_status"],
                    "candidate_status": r["candidate_status"],
                    "manual_check_needed": "; ".join(r["manual_check_needed"])})
        receipt["status"] = "DONE_METADATA_CANDIDATES_ONLY"
        receipt["company_count"] = len(rows_out)
        receipt["config_sha256"] = config.source_sha256
    except Exception as exc:
        receipt["status"] = "BLOCKED" if isinstance(exc, FileNotFoundError) else "FAILED"
        receipt["reason"] = f"{type(exc).__name__}: {exc}"
    receipt["finished_utc"] = datetime.now(timezone.utc).isoformat()
    receipt["output_files"] = {str(p.relative_to(output)).replace("\\", "/"): {
        "bytes": p.stat().st_size, "sha256": sha256(p.read_bytes())}
        for p in sorted(output.rglob("*")) if p.is_file() and p.name != "receipt.json"}
    write_json(output / "receipt.json", receipt)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--aapl-pilot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed-cache-from", type=Path,
                        help="Optional prior run cache; every URL/body/meta is rehashed before reuse")
    args = parser.parse_args(argv)
    result = discover(args.config, args.aapl_pilot, args.output, args.seed_cache_from)
    print(json.dumps({"status": result["status"], "output": str(args.output),
                      "reason": result.get("reason")}))
    return 0 if result["status"] == "DONE_METADATA_CANDIDATES_ONLY" else 1


if __name__ == "__main__":
    sys.exit(main())
