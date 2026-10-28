"""Fetch the four frozen original 10-Q submissions; never select a replacement.

The prior official submissions snapshots and candidate primary HTML are verified
before reuse. New complete submissions and filing indexes use one rate-limited
SEC client and an isolated, initially empty cache. No human review is inferred.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

from finembed.config import load_config
from finembed.extract import primary_text
from finembed.qpilot import _check_primary_filename, _primary_source
from finembed.run import sha256, write_json
from finembed.sec import (SecClient, SecError, MissingContact, _rows, contact_from_environment,
                          raw_filer_cik, raw_header, submission_url)


FIXED = (
    ("MSFT", "0000789019", "0000950170-25-010491"),
    ("AMZN", "0001018724", "0001018724-25-000036"),
    ("GOOGL", "0001652044", "0001652044-25-000043"),
    ("META", "0001326801", "0001326801-25-000054"),
)
FROZEN_CANDIDATES_SHA256 = "7c4351b66306bcc6334edde1cf2ed40486e724196fb0370622962fc8dedf90cb"
_HISTORY = re.compile(r"CIK\d{10}-submissions-\d{3}\.json\Z")


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require(ok: bool, message: str) -> None:
    if not ok:
        raise SecError(message)


def _file_info(path: Path) -> dict:
    body = path.read_bytes()
    return {"path": str(path.resolve()), "sha256": sha256(body), "bytes": len(body)}


def _save_exclusive(path: Path, body: bytes) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(body)
    result = _file_info(path)
    _require(result["sha256"] == sha256(body) and result["bytes"] == len(body),
             "Saved response hash/size mismatch")
    return result


def _verify_saved_source(spec: dict, url: str, prior_root: Path) -> tuple[bytes, dict]:
    _require(spec.get("url") == url, f"Saved SEC source URL differs: {url}")
    file_spec = spec.get("file")
    _require(isinstance(file_spec, dict) and isinstance(file_spec.get("path"), str),
             f"Saved SEC source receipt missing file: {url}")
    path = Path(file_spec["path"]).resolve()
    _require(path.is_relative_to(prior_root.resolve()) and path.is_file(),
             f"Saved SEC source missing/outside prior run: {url}")
    body = path.read_bytes()
    _require(file_spec.get("sha256") == sha256(body) and file_spec.get("bytes") == len(body),
             f"Saved SEC source hash/size mismatch: {url}")
    return body, {"url": url, "prior_file": str(path), "prior_sha256": sha256(body),
                  "prior_bytes": len(body), "prior_retrieved_utc": spec.get("retrieved_utc"),
                  "prior_http_status": spec.get("http_status"),
                  "method": "COPIED_VERIFIED_PRIOR_OFFICIAL_SNAPSHOT"}


def _saved_submissions(row: dict, prior_root: Path, destination: Path,
                       cik: str, accession: str) -> tuple[dict, list[dict], list[dict]]:
    specs = row.get("submissions_source_receipts")
    _require(isinstance(specs, list), "Candidate lacks prior SEC source receipts")
    base_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    selected_rows: list[dict] = []
    all_rows: list[dict] = []
    receipts: list[dict] = []
    seen_urls: set[str] = set()
    for spec in specs:
        url = spec.get("url")
        if not isinstance(url, str) or not url.startswith("https://data.sec.gov/submissions/"):
            continue
        _require(url not in seen_urls, "Duplicate saved submissions response URL")
        seen_urls.add(url)
        if url == base_url:
            filename = "submissions_response.json"
        else:
            filename = url.rsplit("/", 1)[-1]
            _require(bool(_HISTORY.fullmatch(filename)), "Unexpected SEC historical submissions URL")
            filename = f"submissions_history/{filename}"
        body, receipt = _verify_saved_source(spec, url, prior_root)
        saved = _save_exclusive(destination / filename, body)
        receipt.update({"saved": saved, "snapshot_saved_utc": _utc()})
        receipts.append(receipt)
        payload = json.loads(body)
        if url == base_url:
            _require(str(payload.get("cik", "")).zfill(10) == cik,
                     "Saved SEC submissions CIK differs from frozen candidate")
            entries = _rows(payload.get("filings", {}).get("recent", {}))
        else:
            entries = _rows(payload)
        all_rows.extend(entries)
        selected_rows.extend(r for r in entries if r.get("accessionNumber") == accession)
    _require(base_url in seen_urls, "Frozen candidate lacks saved official main submissions response")
    _require(len(selected_rows) == 1, "Frozen accession absent or duplicated in saved SEC submissions")
    selected = selected_rows[0]
    for key, expected in (("form", "10-Q"), ("accessionNumber", accession),
                          ("filingDate", row.get("filing_date")),
                          ("reportDate", row.get("report_period_end")),
                          ("primaryDocument", row.get("primary_document"))):
        _require(selected.get(key) == expected, f"Saved SEC submissions {key} differs from frozen candidate")
    _require(selected.get("acceptanceDateTime") == row.get("submissions_acceptance_raw_string"),
             "Saved SEC submissions acceptance string differs from frozen candidate")
    amendments = [r for r in all_rows if r.get("form") == "10-Q/A"
                  and r.get("reportDate") == selected["reportDate"]]
    return selected, amendments, receipts


def _raw_report_period(raw: bytes) -> dict:
    end = raw.find(b"</SEC-HEADER>")
    _require(end >= 0, "Complete SEC submission lacks closed header")
    header = raw[:end]
    match = re.search(rb"(?m)^CONFORMED PERIOD OF REPORT:[ \t]*(\d{8})[ \t]*\r?$", header)
    _require(match is not None, "Raw SEC header lacks CONFORMED PERIOD OF REPORT")
    original = match.group(1).decode("ascii")
    try:
        parsed = datetime.strptime(original, "%Y%m%d").date().isoformat()
    except ValueError as exc:
        raise SecError("Invalid raw SEC report period") from exc
    return {"conformed_period_of_report_raw": original, "report_period_end_raw_header": parsed}


def _index_primary(index: dict, primary: str) -> dict:
    directory = index.get("directory")
    _require(isinstance(directory, dict) and isinstance(directory.get("item"), list),
             "SEC filing index lacks directory items")
    matches = [item for item in directory["item"] if isinstance(item, dict)
               and item.get("name") == primary]
    _require(len(matches) == 1, "SEC filing index primary-document filename absent or duplicated")
    return {"primary_item": matches[0], "item_count": len(directory["item"])}


def _time_comparison(api_string: str, raw_eastern: str) -> dict:
    result = {"submissions_acceptance_raw_string": api_string,
              "raw_header_acceptance_eastern": raw_eastern,
              "same_instant": None, "seconds_difference": None,
              "first_public_or_tradable_time": "NOT_ESTABLISHED"}
    try:
        api = datetime.fromisoformat(api_string)
        raw = datetime.fromisoformat(raw_eastern)
        if api.tzinfo is not None and raw.tzinfo is not None:
            delta = (api - raw).total_seconds()
            result["seconds_difference"] = delta
            result["same_instant"] = delta == 0
        else:
            result["interpretation"] = "At least one timestamp lacks a zone; no instant comparison"
    except (TypeError, ValueError):
        result["interpretation"] = "Timestamp parse failed; original strings preserved"
    return result


def _network_saved(client: SecClient, url: str, path: Path) -> tuple[bytes, dict]:
    body, meta = client.get(url)
    _require(meta.get("url") == url and meta.get("sha256") == sha256(body)
             and meta.get("bytes") == len(body), "SEC client response receipt mismatch")
    _require(not meta.get("cache_hit"), "New isolated SEC cache unexpectedly contained URL")
    saved = _save_exclusive(path, body)
    return body, {"url": url, "http_status": meta.get("http_status"),
                  "retrieved_utc": meta.get("retrieved_utc"),
                  "cache_hit": meta.get("cache_hit"), "saved": saved,
                  "snapshot_saved_utc": _utc(), "method": "NEW_OFFICIAL_NETWORK_RESPONSE"}


def _current_submissions(client: SecClient, company_dir: Path, cik: str,
                         accession: str, report_period: str) -> tuple[dict | None, list[dict], dict]:
    """Save a new official snapshot without replacing the prior candidate snapshot."""
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    body, main_receipt = _network_saved(client, url,
                                        company_dir / "submissions_current_response.json")
    payload = json.loads(body)
    _require(str(payload.get("cik", "")).zfill(10) == cik,
             "Current SEC submissions CIK differs from frozen candidate")
    rows = _rows(payload.get("filings", {}).get("recent", {}))
    history_receipts = []
    for spec in payload.get("filings", {}).get("files", []):
        name = spec.get("name", "")
        _require(bool(_HISTORY.fullmatch(name)), "Unexpected SEC historical submissions filename")
        start, end = str(spec.get("filingFrom", "")), str(spec.get("filingTo", ""))
        _require(bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", start)) and
                 bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", end)),
                 "Current SEC historical-file date range missing")
        if start > "2025-12-31" or end < "2025-01-01":
            continue
        hurl = f"https://data.sec.gov/submissions/{name}"
        hbody, hreceipt = _network_saved(client, hurl,
            company_dir / "submissions_current_history" / name)
        history_receipts.append(hreceipt)
        rows.extend(_rows(json.loads(hbody)))
    selected = [row for row in rows if row.get("accessionNumber") == accession]
    _require(len(selected) <= 1, "Current SEC submissions duplicates fixed accession")
    amendments = [row for row in rows if row.get("form") == "10-Q/A"
                  and row.get("reportDate") == report_period]
    receipt = {"main": main_receipt, "history": history_receipts,
               "selection_status": "FOUND" if selected else "MISSING_IN_CHECKED_SNAPSHOT",
               "amendment_search_scope": "current recent rows and saved historical files covering 2025; other correction forms/publication history unverified"}
    return selected[0] if selected else None, amendments, receipt


def _one(client: SecClient, row: dict, company_dir: Path, prior_root: Path,
         ticker: str, cik: str, accession: str, company: str) -> dict:
    identity = {"status": "RUNNING", "ticker": ticker, "company": company,
                "cik": cik, "accession": accession, "form": "10-Q",
                "report_period_end": None, "filing_date": None,
                "primary_document": None, "original_submission_sha256": None,
                "human_review": "PENDING_HUMAN_REVIEW",
                "first_public_or_tradable_time": "NOT_ESTABLISHED"}
    receipts: dict = {"status": "RUNNING", "ticker": ticker,
                      "candidate_snapshot": None, "submissions": [],
                      "submissions_current": None,
                      "prior_primary_html": None, "raw_submission": None,
                      "filing_index": None}
    try:
        _require(row.get("ticker") == ticker and row.get("cik") == cik
                 and row.get("accession") == accession,
                 "Frozen candidate conflicts with user-fixed ticker/CIK/accession")
        _require(row.get("form") == "10-Q", "Frozen candidate is not an original 10-Q")
        receipts["candidate_snapshot"] = {
            "record_sha256": sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode("utf-8")),
            "fixed_identity_match": True,
            "basis": "frozen M7 candidate ledger; original accession is never reselected"}
        identity.update({"report_period_end": row.get("report_period_end"),
                         "filing_date": row.get("filing_date"),
                         "primary_document": row.get("primary_document")})
        selected, amendments, old_receipts = _saved_submissions(
            row, prior_root, company_dir, cik, accession)
        receipts["submissions"] = old_receipts
        identity["submissions_row"] = selected
        identity["same_report_period_10q_amendments_in_saved_submissions"] = amendments
        identity["amendment_search_scope"] = "verified saved official submissions snapshots only"
        primary_url = row.get("primary_document_url")
        expected_primary_url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                                f"{accession.replace('-', '')}/{selected['primaryDocument']}")
        _require(primary_url == expected_primary_url, "Frozen primary-document URL differs from fixed identity")
        prior_spec = row.get("primary_html")
        _require(isinstance(prior_spec, dict) and isinstance(prior_spec.get("path"), str),
                 "Frozen candidate primary HTML receipt missing")
        prior_path = Path(prior_spec["path"]).resolve()
        _require(prior_path.is_relative_to(prior_root.resolve()) and prior_path.is_file(),
                 "Frozen candidate primary HTML missing/outside prior run")
        prior_bytes = prior_path.read_bytes()
        _require(prior_spec.get("sha256") == sha256(prior_bytes)
                 and prior_spec.get("bytes") == len(prior_bytes),
                 "Frozen candidate primary HTML hash/size mismatch")
        receipts["prior_primary_html"] = {"path": str(prior_path),
                                          "sha256": sha256(prior_bytes),
                                          "bytes": len(prior_bytes),
                                          "url": primary_url,
                                          "method": "VERIFIED_PRIOR_OFFICIAL_SNAPSHOT"}

        raw_url = submission_url(cik, accession)
        raw, raw_receipt = _network_saved(client, raw_url,
                                          company_dir / "original_submission.txt")
        receipts["raw_submission"] = raw_receipt
        identity["original_submission_url"] = raw_url
        identity["original_submission_sha256"] = sha256(raw)
        header = raw_header(raw, accession, "10-Q")
        header.update(_raw_report_period(raw))
        filer_cik = raw_filer_cik(raw)
        _require(filer_cik == cik, "Raw SEC FILER CIK differs from frozen candidate")
        _require(header["filing_date_raw_header"] == selected["filingDate"],
                 "Raw SEC filing date differs from saved submissions")
        _require(header["report_period_end_raw_header"] == selected["reportDate"],
                 "Raw SEC report period differs from saved submissions")
        identity["raw_header"] = header
        identity["raw_filer_cik"] = filer_cik
        identity["acceptance_time_comparison"] = _time_comparison(
            selected["acceptanceDateTime"], header["acceptance_datetime_eastern"])

        primary_html, primary_meta = _primary_source(raw)
        _check_primary_filename(primary_meta, selected)
        primary_path = company_dir / "primary_document_source.html"
        _save_exclusive(primary_path, primary_html)
        rendered, rendering_meta = primary_text(raw, "10-Q")
        text_path = company_dir / "primary_text.txt"
        _save_exclusive(text_path, rendered.encode("utf-8"))
        identity["primary_document_url"] = primary_url
        identity["primary_document_sha256"] = sha256(primary_html)
        identity["primary_text_sha256"] = sha256(rendered.encode("utf-8"))
        identity["primary_raw_byte_span"] = [primary_meta["raw_byte_start"],
                                             primary_meta["raw_byte_end_exclusive"]]
        identity["primary_raw_filename"] = primary_meta["filename_raw_header"]
        identity["rendering"] = rendering_meta
        identity["prior_primary_comparison"] = {
            "prior_sha256": sha256(prior_bytes), "raw_embedded_primary_sha256": sha256(primary_html),
            "same_bytes": prior_bytes == primary_html,
            "interpretation": "Different bytes require separate version review, not silent substitution"}

        version_difference = prior_bytes != primary_html
        identity["primary_version_status"] = (
            "NEEDS_VERSION_REVIEW" if version_difference else "MATCHES_VERIFIED_PRIOR_SNAPSHOT")
        auxiliary_warnings: list[str] = (["Raw-embedded primary differs from the verified prior primary HTML snapshot; extraction requires version review"]
                                         if version_difference else [])
        try:
            current_row, current_amendments, current_receipt = _current_submissions(
                client, company_dir, cik, accession, selected["reportDate"])
            receipts["submissions_current"] = current_receipt
            identity["current_submissions_row"] = current_row
            identity["same_report_period_10q_amendments_in_current_submissions"] = current_amendments
            if current_row is None:
                identity["current_submissions_status"] = "FIXED_ACCESSION_MISSING_IN_CHECKED_SNAPSHOT"
                auxiliary_warnings.append("Current submissions snapshot does not list fixed accession")
            else:
                compare_keys = ("form", "accessionNumber", "filingDate", "reportDate", "primaryDocument")
                conflicts = {key: {"prior": selected.get(key), "current": current_row.get(key)}
                             for key in compare_keys if selected.get(key) != current_row.get(key)}
                identity["current_submissions_comparison"] = {
                    "identity_conflicts": conflicts,
                    "prior_acceptance_string": selected.get("acceptanceDateTime"),
                    "current_acceptance_string": current_row.get("acceptanceDateTime"),
                    "acceptance_strings_equal": selected.get("acceptanceDateTime") ==
                                                current_row.get("acceptanceDateTime"),
                    "note": "A changed acceptance string is preserved, not interpreted as first public time"}
                identity["current_submissions_status"] = (
                    "IDENTITY_CONFLICT_NEEDS_REVIEW" if conflicts else "IDENTITY_MATCHED")
                if conflicts:
                    auxiliary_warnings.append("Current submissions identity fields conflict with prior snapshot")
        except Exception as exc:
            receipts["submissions_current"] = {"status": "FAILED", "reason": f"{type(exc).__name__}: {exc}"}
            identity["current_submissions_status"] = "FAILED_TO_VERIFY_NEW_SNAPSHOT"
            auxiliary_warnings.append("Current submissions response unavailable/invalid")

        index_url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                     f"{accession.replace('-', '')}/index.json")
        identity["filing_index_url"] = index_url
        try:
            index_body, index_receipt = _network_saved(
                client, index_url, company_dir / "filing_index_response.json")
            receipts["filing_index"] = index_receipt
            identity["filing_index"] = _index_primary(json.loads(index_body),
                                                       selected["primaryDocument"])
            identity["filing_index_sha256"] = sha256(index_body)
            identity["filing_index_status"] = "PRIMARY_LISTED"
        except Exception as exc:
            receipts["filing_index"] = {"status": "FAILED", "reason": f"{type(exc).__name__}: {exc}"}
            identity["filing_index_status"] = "FAILED_OR_PRIMARY_NOT_LISTED"
            auxiliary_warnings.append("Current official filing index unavailable/inconsistent")
        identity["source_paths"] = {name: str((company_dir / filename).resolve()) for name, filename in {
            "original_submission": "original_submission.txt",
            "primary_document_source": "primary_document_source.html",
            "primary_text": "primary_text.txt",
            "submissions_response": "submissions_response.json",
            "submissions_current_response": "submissions_current_response.json",
            "filing_index_response": "filing_index_response.json"}.items()
            if (company_dir / filename).is_file()}
        identity["status"] = ("NEEDS_VERSION_REVIEW" if version_difference else
                              "VERIFIED_RAW_AND_PRIMARY")
        identity["acquisition_status"] = (
            "PARTIAL_VERSION_REVIEW" if version_difference else
            "COMPLETE" if not auxiliary_warnings else "PARTIAL_AUXILIARY")
        identity["auxiliary_warnings"] = auxiliary_warnings
        identity["verified_utc"] = _utc()
        receipts["status"] = identity["acquisition_status"]
    except Exception as exc:
        identity["status"] = "FAILED_SOURCE_VALIDATION"
        identity["reason"] = f"{type(exc).__name__}: {exc}"
        receipts["status"] = "FAILED"
        receipts["reason"] = identity["reason"]
    receipts["finished_utc"] = _utc()
    write_json(company_dir / "source_identity.json", identity)
    write_json(company_dir / "source_receipts.json", receipts)
    return identity


def run(config_path: Path, candidates_path: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    receipt = {"kind": "FOUR_FIXED_ORIGINAL_10Q_ACQUISITION", "status": "RUNNING",
               "started_utc": _utc(), "fixed": [list(item) for item in FIXED],
               "human_review": "PENDING_HUMAN_REVIEW", "batch_representation": "NOT_RUN",
               "financial_experiment": "NOT_RUN", "companies": []}
    write_json(output / "receipt.json", receipt)
    try:
        config = load_config(config_path)
        receipt["config_path"] = str(config.source)
        receipt["config_sha256"] = config.source_sha256
        candidate_bytes = candidates_path.read_bytes()
        candidate_data = json.loads(candidate_bytes)
        _require(isinstance(candidate_data, dict) and isinstance(candidate_data.get("rows"), list),
                 "Frozen candidate ledger is not a row list")
        _require(sha256(candidate_bytes) == FROZEN_CANDIDATES_SHA256,
                 "Candidate ledger differs from the previously frozen M7 version")
        receipt["candidates_path"] = str(candidates_path.resolve())
        receipt["candidates_sha256"] = sha256(candidate_bytes)
        receipt["source_code_sha256"] = sha256(Path(__file__).read_bytes())
        receipt["effective_fresh_cache"] = str((output / "cache").resolve())
        contact = contact_from_environment()  # Gate before any network; never save value.
        client = SecClient(replace(config, cache=output / "cache"), contact)
        company_names = {item.ticker: item.name for item in config.companies}
        _require(all(ticker in company_names for ticker, _cik, _accession in FIXED),
                 "Configured company list lacks a frozen 10-Q candidate")
        prior_root = candidates_path.resolve().parent
        for ticker, cik, accession in FIXED:
            company_dir = output / "ingest" / ticker
            company_dir.mkdir(parents=True, exist_ok=False)
            matches = [row for row in candidate_data["rows"] if row.get("ticker") == ticker]
            if len(matches) != 1:
                result = {"status": "FAILED_SOURCE_VALIDATION", "ticker": ticker,
                          "company": company_names.get(ticker), "cik": cik,
                          "accession": accession, "form": "10-Q",
                          "reason": f"Frozen candidate count for {ticker} is {len(matches)}",
                          "human_review": "PENDING_HUMAN_REVIEW"}
                write_json(company_dir / "source_identity.json", result)
                write_json(company_dir / "source_receipts.json", {"status": "FAILED", "reason": result["reason"]})
            else:
                result = _one(client, matches[0], company_dir, prior_root,
                              ticker, cik, accession, company_names[ticker])
            receipt["companies"].append({"ticker": ticker, "accession": accession,
                                         "status": result["status"],
                                         "acquisition_status": result.get("acquisition_status"),
                                         "reason": result.get("reason")})
        raw_evidence = sum(row["status"] in {"VERIFIED_RAW_AND_PRIMARY", "NEEDS_VERSION_REVIEW"}
                           for row in receipt["companies"])
        complete = sum(row["acquisition_status"] == "COMPLETE" for row in receipt["companies"])
        receipt["status"] = ("DONE" if complete == len(FIXED) else
                             "PARTIAL" if raw_evidence else "FAILED")
    except Exception as exc:
        receipt["status"] = "BLOCKED" if isinstance(exc, (FileNotFoundError, MissingContact)) else "FAILED"
        receipt["reason"] = f"{type(exc).__name__}: {exc}"
        if isinstance(exc, MissingContact):
            for ticker, cik, accession in FIXED:
                company_dir = output / "ingest" / ticker
                company_dir.mkdir(parents=True, exist_ok=False)
                identity = {"status": "BLOCKED_MISSING_SEC_CONTACT", "ticker": ticker,
                            "cik": cik, "accession": accession, "form": "10-Q",
                            "human_review": "PENDING_HUMAN_REVIEW",
                            "reason": "Real SEC contact identity absent from process environment"}
                write_json(company_dir / "source_identity.json", identity)
                write_json(company_dir / "source_receipts.json",
                           {"status": "BLOCKED", "reason": identity["reason"]})
                receipt["companies"].append({"ticker": ticker, "accession": accession,
                                             "status": identity["status"], "reason": identity["reason"]})
    receipt["finished_utc"] = _utc()
    receipt["outputs"] = {str(path.relative_to(output)).replace("\\", "/"):
                          {"bytes": path.stat().st_size, "sha256": sha256(path.read_bytes())}
                          for path in sorted(output.rglob("*")) if path.is_file()
                          and path.name != "receipt.json"}
    write_json(output / "receipt.json", receipt)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = run(args.config, args.candidates, args.output)
    print(json.dumps({"status": result["status"], "output": str(args.output),
                      "companies": result.get("companies"), "reason": result.get("reason")}))
    return 0 if result["status"] == "DONE" else 1


if __name__ == "__main__":
    sys.exit(main())
