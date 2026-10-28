"""One original 10-Q MD&A pilot with auditable ingestion and engineering inference.

Programmatic and same-agent boundary checks never become human attestation.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import unicodedata

from .config import load_config
from .extract import primary_text, ExtractionError
from .model import Encoder, aggregate, ModelError
from .run import sha256, write_json
from .sec import (SecClient, SecError, MissingContact, contact_from_environment,
                  raw_header, raw_filer_cik, submission_url, _rows)


CIK = "0000320193"
TICKER = "AAPL"
YEAR = 2025
_ACCESSION = re.compile(r"\d{10}-\d{2}-\d{6}\Z")
_H_ITEM2 = re.compile(r"^item\s+2\s*[.·:\-]?\s*management['s]*\s+discussion\s+and\s+analysis\b", re.I)
_H_ITEM3 = re.compile(r"^item\s+3\s*[.·:\-]?\s*quantitative\s+and\s+qualitative\s+disclosures\s+about\s+market\s+risk\b", re.I)
_H_PART_I = re.compile(r"^part\s+i(?:\s*[-—–·:]\s*financial\s+information)?\s*$", re.I)
_H_PART_II = re.compile(r"^part\s+ii(?:\s*[-—–·:]\s*other\s+information)?\s*$", re.I)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid SEC filing date: {value!r}") from exc


def select_earliest_original(rows: list[dict], year: int = YEAR) -> tuple[list[dict], list[dict]]:
    """Select date first; resolve any same-date tie only after raw-header checks."""
    relevant = [r for r in rows if r.get("form") in {"10-Q", "10-Q/A"}
                and str(r.get("filingDate", "")).startswith(f"{year}-")]
    for row in relevant:
        if (not _ACCESSION.fullmatch(str(row.get("accessionNumber", ""))) or
                _check_date(row.get("filingDate")) .year != year or
                not row.get("reportDate") or not row.get("acceptanceDateTime")):
            raise ValueError("Invalid 10-Q submission identity/date column")
    originals = [r for r in relevant if r["form"] == "10-Q"]
    if not originals:
        raise SecError(f"No original 10-Q filed in {year} for CIK {CIK}")
    earliest = min(_check_date(r["filingDate"]) for r in originals)
    tied = sorted((r for r in originals if _check_date(r["filingDate"]) == earliest),
                  key=lambda r: r["accessionNumber"])
    return tied, sorted(relevant, key=lambda r: (r["filingDate"], r["accessionNumber"]))


def _simple_row(row: dict) -> dict:
    return {key: row.get(key) for key in
            ("form", "accessionNumber", "filingDate", "acceptanceDateTime",
             "reportDate", "primaryDocument")}


def _normalize(line: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", line).replace("’", "'")
                    .replace("‘", "'").split()).casefold()


def _lines(text: str) -> list[tuple[int, int, str]]:
    out = []
    cursor = 0
    for line in text.splitlines(keepends=True):
        end = cursor + len(line)
        out.append((cursor, end, line.rstrip("\r\n")))
        cursor = end
    if cursor != len(text):
        raise ValueError("Line offsets do not cover primary text")
    return out


def _heading(lines: list[tuple[int, int, str]], index: int,
             pattern: re.Pattern) -> tuple[int, int, str] | None:
    """Allow a line-wrapped heading, but never consume a following Item/Part."""
    first = _normalize(lines[index][2])
    if not re.match(r"^item\s+[23]\s*[.·:\-]?(?:\s|$)", first):
        return None
    components = []
    end = lines[index][1]
    for j in range(index, min(index + 5, len(lines))):
        part = _normalize(lines[j][2])
        if j > index and re.match(r"^(?:item\s+[0-9]|part\s+[ivx])", part):
            break
        if part:
            components.append(part)
            end = lines[j][1]
        title = " ".join(components)
        if pattern.match(title):
            return lines[index][0], end, title
        if len(title) > 210:
            break
    return None


def boundary_candidates(text: str) -> dict:
    """Find and retain TOC/body candidates; never fall back to the full filing."""
    lines = _lines(text)
    parts_i = [start for start, _end, line in lines if _H_PART_I.fullmatch(_normalize(line))]
    parts_ii = [start for start, _end, line in lines if _H_PART_II.fullmatch(_normalize(line))]
    starts = [match for i in range(len(lines)) if (match := _heading(lines, i, _H_ITEM2))]
    ends = [match for i in range(len(lines)) if (match := _heading(lines, i, _H_ITEM3))]
    candidates = []
    for start, heading_end, title in starts:
        later = next((entry for entry in ends if entry[0] > heading_end), None)
        end = later[0] if later else None
        content = text[start:end] if end is not None else None
        before_part_i = max((p for p in parts_i if p < start), default=None)
        next_part_ii = min((p for p in parts_ii if p > start), default=None)
        words = len(content.split()) if content is not None else None
        substantive = (content is not None and len(content) >= 5000 and words >= 500 and
                       len(content) <= 0.6 * len(text) and before_part_i is not None and
                       next_part_ii is not None and end < next_part_ii)
        candidates.append({"start_char": start, "heading_end_char_exclusive": heading_end,
                           "end_char_exclusive": end, "heading": title,
                           "next_heading": later[2] if later else None,
                           "preceding_part_i_char": before_part_i,
                           "following_part_ii_char": next_part_ii,
                           "characters": len(content) if content is not None else None,
                           "words": words, "content_sha256": sha256(content.encode("utf-8")) if content else None,
                           "classification": "PROGRAM_SUBSTANTIVE_BODY_CANDIDATE" if substantive else
                           ("TOC_OR_SHORT_CANDIDATE" if content is not None and len(content) < 5000 else
                            "NEEDS_REVIEW"),
                           "start_context": text[max(0, start - 250):min(len(text), start + 380)],
                           "end_context": text[max(0, end - 300):min(len(text), end + 300)] if end is not None else None})
    substantive = [i for i, c in enumerate(candidates)
                   if c["classification"] == "PROGRAM_SUBSTANTIVE_BODY_CANDIDATE"]
    selected_index = substantive[0] if len(substantive) == 1 else None
    return {"status": "PROGRAM_BOUNDARY_PASS" if selected_index is not None else "NEEDS_REVIEW",
            "selected_candidate_index": selected_index,
            "item2_heading_count": len(starts), "item3_heading_count": len(ends),
            "part_i_heading_count": len(parts_i), "part_ii_heading_count": len(parts_ii),
            "substantive_candidate_count": len(substantive), "candidates": candidates,
            "selection_rule": "one >=5000-char/>=500-word Item 2-to-Item 3 span inside Part I, <=60% of primary text; short TOC retained"}


def _primary_source(raw: bytes) -> tuple[bytes, dict]:
    documents = list(re.finditer(rb"<DOCUMENT>\s*(.*?)\s*</DOCUMENT>", raw, re.I | re.S))
    found = []
    for document in documents:
        content = document.group(1)
        kind = re.search(rb"<TYPE>\s*([^\r\n<]+)", content, re.I)
        if kind and kind.group(1).strip().upper() == b"10-Q":
            name = re.search(rb"<FILENAME>\s*([^\r\n<]+)", content, re.I)
            if not name:
                raise ExtractionError("Primary 10-Q document has no raw FILENAME")
            try:
                filename = name.group(1).strip().decode("ascii")
            except UnicodeDecodeError as exc:
                raise ExtractionError("Primary 10-Q filename is not ASCII") from exc
            if not filename or Path(filename).name != filename or "\\" in filename:
                raise ExtractionError("Primary 10-Q filename is invalid")
            text_match = re.search(rb"<TEXT>\s*(.*?)\s*</TEXT>", content, re.I | re.S)
            if text_match:
                a = document.start(1) + text_match.start(1)
                b = document.start(1) + text_match.end(1)
                found.append((raw[a:b], a, b, filename))
    if len(found) != 1:
        raise ExtractionError(f"Expected one byte-level primary 10-Q TEXT; found {len(found)}")
    value, start, end, filename = found[0]
    return value, {"raw_byte_start": start, "raw_byte_end_exclusive": end,
                   "sha256": sha256(value), "bytes": len(value), "filename_raw_header": filename,
                   "exact_raw_substring": raw[start:end] == value}


def _check_primary_filename(source_meta: dict, submission_row: dict) -> str:
    """Fail closed if the raw SEC primary-document identity differs from submissions."""
    raw_name = source_meta.get("filename_raw_header")
    api_name = submission_row.get("primaryDocument")
    if not raw_name or not api_name or raw_name != api_name:
        raise SecError("Primary 10-Q raw filename differs from SEC submissions primaryDocument")
    return raw_name


def _copy_verified_cache(source: Path, target: Path, url: str) -> dict | None:
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    old_body, old_meta = source / f"{key}.bin", source / f"{key}.json"
    if not old_body.exists() and not old_meta.exists():
        return None
    if not old_body.is_file() or not old_meta.is_file():
        raise SecError(f"Incomplete previous SEC cache for {url}")
    data = old_body.read_bytes()
    meta = json.loads(old_meta.read_text(encoding="utf-8"))
    if meta.get("url") != url or meta.get("sha256") != sha256(data) or meta.get("bytes") != len(data):
        raise SecError(f"Previous SEC cache integrity failed for {url}")
    target.mkdir(parents=True, exist_ok=True)
    if (target / old_body.name).exists() or (target / old_meta.name).exists():
        raise SecError("Isolated SEC cache destination already exists")
    shutil.copyfile(old_body, target / old_body.name)
    shutil.copyfile(old_meta, target / old_meta.name)
    return {"source_cache_body": str(old_body), "source_cache_meta": str(old_meta),
            "source_sha256": sha256(data), "bytes": len(data),
            "source_retrieved_utc": meta.get("retrieved_utc"),
            "copied_utc": _utc(), "method": "COPIED_VERIFIED_CACHE"}


def _fetch_saved(client: SecClient, url: str, path: Path,
                 old_cache: Path, *, seed_previous: bool = False) -> dict:
    copied = _copy_verified_cache(old_cache, client.config.cache, url) if seed_previous else None
    body, meta = client.get(url)
    if copied and (meta.get("sha256") != copied["source_sha256"] or not meta.get("cache_hit")):
        raise SecError("Copied SEC cache was not reused and verified")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    if sha256(path.read_bytes()) != meta["sha256"]:
        raise SecError("Saved SEC response hash differs from verified cache/network response")
    return {"path": str(path), "url": url, "sha256": meta["sha256"], "bytes": meta["bytes"],
            "http_status": meta.get("http_status"), "source_retrieved_utc": meta.get("retrieved_utc"),
            "snapshot_saved_utc": _utc(), "cache_hit": meta.get("cache_hit"),
            "cache_source": copied or ("NETWORK_RESPONSE" if not meta.get("cache_hit") else "ISOLATED_CACHE")}


def _source_diagnostics(text: str, html: bytes, section: str) -> dict:
    crossrefs = list(re.finditer(r"(?i)\b(?:see|refer to)\s+(?:note|item|part|section)\b", section))
    controls = [hex(ord(c)) for c in section if ord(c) < 32 and c not in "\n\t\r"]
    unusual = {hex(ord(c)): section.count(c) for c in set(section) if c == "\ufffd" or
               unicodedata.category(c) in {"Cc", "Cf"} and c not in "\n\t\r"}
    lower = html.lower()
    return {"primary_html_table_open_tags": len(re.findall(rb"<table\b", lower)),
            "primary_html_page_break_mentions": lower.count(b"page-break"),
            "primary_html_ixbrl_mentions": len(re.findall(rb"<ix:", lower)),
            "rendered_primary_characters": len(text),
            "section_newline_count": section.count("\n"),
            "section_cross_reference_count": len(crossrefs),
            "section_cross_reference_offsets_first_12": [m.start() for m in crossrefs[:12]],
            "section_control_characters": controls[:12], "section_unusual_character_counts": unusual,
            "limitation": "HTMLParser retains text but does not prove table cell layout, pagination or linked references are semantically complete; source HTML and full submission are preserved."}


def run(config_path: Path, output: Path, seed_cache_from: Path | None = None) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    receipt: dict = {"kind": "ONE_REAL_10Q_MDA_SOURCE_BOUNDARY_PILOT", "status": "RUNNING",
                     "started_utc": _utc(), "financial_experiment": "NOT_RUN",
                     "human_review": "PENDING", "same_agent_boundary_check": "PENDING"}
    write_json(output / "receipt.json", receipt)
    try:
        original_config = load_config(config_path)
        receipt["config_path"] = str(original_config.source)
        receipt["config_sha256"] = original_config.source_sha256
        receipt["effective_cache_override"] = str(output / "cache")
        receipt["verified_discovery_cache_seed"] = str(seed_cache_from) if seed_cache_from else None
        client = SecClient(replace(original_config, cache=output / "cache"),
                           contact_from_environment())
        discovery_seed = seed_cache_from if seed_cache_from is not None else original_config.cache
        ticker_url = "https://www.sec.gov/files/company_tickers.json"
        ticker_receipt = _fetch_saved(client, ticker_url,
            output / "discovery" / "company_tickers_response.json", discovery_seed,
            seed_previous=seed_cache_from is not None)
        tickers = json.loads((output / "discovery" / "company_tickers_response.json").read_bytes())
        matches = [r for r in tickers.values() if isinstance(r, dict) and
                   str(r.get("ticker", "")).upper() == TICKER]
        if len(matches) != 1 or f"{int(matches[0]['cik_str']):010d}" != CIK:
            raise SecError("Official ticker mapping does not identify selected AAPL CIK")
        source_receipts = [ticker_receipt]
        base_url = f"https://data.sec.gov/submissions/CIK{CIK}.json"
        source_receipts.append(_fetch_saved(client, base_url,
            output / "discovery" / f"CIK{CIK}_submissions_response.json", discovery_seed,
            seed_previous=seed_cache_from is not None))
        main = json.loads((output / "discovery" / f"CIK{CIK}_submissions_response.json").read_bytes())
        if str(main.get("cik", "")).zfill(10) != CIK:
            raise SecError("SEC submissions CIK differs from AAPL mapping")
        rows = _rows(main.get("filings", {}).get("recent", {}))
        for entry in main.get("filings", {}).get("files", []):
            name = entry.get("name", "")
            if not re.fullmatch(r"CIK\d{10}-submissions-\d{3}\.json", name):
                raise SecError("Unexpected SEC historical submissions filename")
            url = f"https://data.sec.gov/submissions/{name}"
            path = output / "discovery" / name
            source_receipts.append(_fetch_saved(client, url, path, discovery_seed,
                                               seed_previous=seed_cache_from is not None))
            rows.extend(_rows(json.loads(path.read_bytes())))
        tied, relevant = select_earliest_original(rows)
        same_day = []
        for candidate in tied:
            accession = candidate["accessionNumber"]
            url = submission_url(CIK, accession)
            path = output / "same_day_originals" / f"{accession}.txt"
            response = _fetch_saved(client, url, path, original_config.cache, seed_previous=True)
            raw = path.read_bytes()
            header = raw_header(raw, accession, "10-Q")
            filer_cik = raw_filer_cik(raw)
            if filer_cik != CIK or header["filing_date_raw_header"] != candidate["filingDate"]:
                raise SecError("Raw header CIK or filing date differs from selected SEC row")
            same_day.append({"submission_row": _simple_row(candidate), "response": response,
                             "raw_header": header, "raw_filer_cik": filer_cik})
        same_day.sort(key=lambda entry: (entry["raw_header"]["acceptance_datetime_eastern"],
                                         entry["submission_row"]["accessionNumber"]))
        chosen = same_day[0]
        selected_row = chosen["submission_row"]
        selected_accession = selected_row["accessionNumber"]
        related_amendments = [_simple_row(r) for r in rows if r.get("form") == "10-Q/A"
                              and r.get("reportDate") == selected_row["reportDate"]]
        selection = {"rule": "AAPL; earliest filingDate in calendar 2025 among original 10-Q; same-day tie by raw-header Eastern acceptance then accession",
                     "cik": CIK, "ticker": TICKER, "selected": chosen,
                     "same_day_original_candidates": same_day,
                     "all_2025_10q_and_amendments": [_simple_row(r) for r in relevant],
                     "same_report_period_amendments_any_year": related_amendments,
                     "submissions_source_receipts": source_receipts,
                     "api_vs_raw_acceptance": {"api_value": selected_row["acceptanceDateTime"],
                                               "raw_eastern": chosen["raw_header"]["acceptance_datetime_eastern"],
                                               "same_instant": datetime.fromisoformat(selected_row["acceptanceDateTime"]).astimezone(timezone.utc) ==
                                               datetime.fromisoformat(chosen["raw_header"]["acceptance_datetime_eastern"]).astimezone(timezone.utc),
                                               "interpretation": "Compare as timezone-aware instants; neither field alone establishes first public availability."}}
        write_json(output / "selection_ledger.json", selection)
        raw_path = Path(chosen["response"]["path"])
        selected_raw = output / "original_submission.txt"
        shutil.copyfile(raw_path, selected_raw)
        if sha256(selected_raw.read_bytes()) != chosen["response"]["sha256"]:
            raise SecError("Selected raw copy hash mismatch")
        raw = selected_raw.read_bytes()
        source_html, html_receipt = _primary_source(raw)
        _check_primary_filename(html_receipt, selected_row)
        (output / "primary_document_source.html").write_bytes(source_html)
        text, text_receipt = primary_text(raw, "10-Q")
        # Preserve exact rendered bytes on Windows as well as Unix; text-mode
        # newline translation would invalidate source-span hashes.
        (output / "primary_text.txt").write_bytes(text.encode("utf-8"))
        boundary = boundary_candidates(text)
        for i, candidate in enumerate(boundary["candidates"]):
            if candidate["end_char_exclusive"] is not None:
                span = text[candidate["start_char"]:candidate["end_char_exclusive"]]
                (output / f"candidate_{i:02d}.txt").write_bytes(span.encode("utf-8"))
                if sha256((output / f"candidate_{i:02d}.txt").read_bytes()) != candidate["content_sha256"]:
                    raise SecError("Saved MD&A candidate hash mismatch")
        index = boundary["selected_candidate_index"]
        selected_section = None
        if index is not None:
            candidate = boundary["candidates"][index]
            selected_section = text[candidate["start_char"]:candidate["end_char_exclusive"]]
            (output / "mda_section_program_candidate.txt").write_bytes(selected_section.encode("utf-8"))
            if sha256((output / "mda_section_program_candidate.txt").read_bytes()) != candidate["content_sha256"]:
                raise SecError("Saved selected section hash mismatch")
            boundary["program_check"] = "PASS_EXACT_SUBSTRING_AND_UNIQUE_SUBSTANTIVE_CANDIDATE"
            boundary["same_agent_boundary_check"] = "PENDING_DIRECT_CONTEXT_INSPECTION"
            boundary["human_review"] = "PENDING"
            boundary["section_diagnostics"] = _source_diagnostics(text, source_html, selected_section)
        else:
            boundary["program_check"] = "NEEDS_REVIEW"
            boundary["same_agent_boundary_check"] = "NEEDS_REVIEW"
            boundary["human_review"] = "PENDING"
            boundary["section_diagnostics"] = None
        boundary["form"] = "10-Q"
        boundary["section"] = "PART_I_ITEM_2_MDA"
        boundary["accession"] = selected_accession
        boundary["raw_submission_sha256"] = sha256(raw)
        boundary["primary_source_html"] = html_receipt
        boundary["primary_text_sha256"] = sha256(text.encode("utf-8"))
        boundary["rendering"] = {**text_receipt,
             "conversion": "Existing finembed.extract.primary_text: UTF-8 decode with replacement limit; HTMLParser convert_charrefs, script/style suppression, newline on block tags; normalize NBSP/spaces/newlines; exact saved output rechecked."}
        write_json(output / "boundary_evidence.json", boundary)
        receipt["status"] = "DONE_BOUNDARY_PROGRAM_PASS_HUMAN_PENDING" if index is not None else "NEEDS_REVIEW"
        receipt["selected_accession"] = selected_accession
        receipt["selected_raw_sha256"] = sha256(raw)
        receipt["program_boundary_status"] = boundary["program_check"]
        receipt["same_agent_boundary_check"] = boundary["same_agent_boundary_check"]
        receipt["historical_public_availability"] = "NOT_ESTABLISHED"
    except (SecError, ExtractionError, OSError, ValueError, KeyError, TypeError) as exc:
        receipt["status"] = "BLOCKED" if isinstance(exc, (MissingContact, FileNotFoundError)) else "FAILED"
        receipt["reason"] = f"{type(exc).__name__}: {exc}"
    receipt["finished_utc"] = _utc()
    receipt["outputs"] = {str(path.relative_to(output)): {"bytes": path.stat().st_size,
                           "sha256": sha256(path.read_bytes())} for path in sorted(output.rglob("*"))
                          if path.is_file() and path.name != "receipt.json"}
    write_json(output / "receipt.json", receipt)
    return receipt


def _verified_program_candidate(source: Path, same_agent_review: Path) -> tuple[str, dict, dict]:
    """Reopen the immutable acquisition output, not a human-attested section index."""
    receipt_path = source / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("status") != "DONE_BOUNDARY_PROGRAM_PASS_HUMAN_PENDING":
        raise ValueError("Source pilot has no program-valid boundary")
    for name, expected in receipt.get("outputs", {}).items():
        file = source / name
        if (not file.is_file() or file.stat().st_size != expected["bytes"] or
                sha256(file.read_bytes()) != expected["sha256"]):
            raise ValueError(f"Source pilot output hash mismatch: {file}")
    raw = (source / "original_submission.txt").read_bytes()
    ledger = json.loads((source / "selection_ledger.json").read_text(encoding="utf-8"))
    chosen = ledger["selected"]
    accession = chosen["submission_row"]["accessionNumber"]
    if (accession != receipt["selected_accession"] or
            sha256(raw) != receipt["selected_raw_sha256"] or
            raw_filer_cik(raw) != CIK or raw_header(raw, accession, "10-Q") != chosen["raw_header"]):
        raise ValueError("Source raw identity differs from selected AAPL 10-Q")
    source_html, source_meta = _primary_source(raw)
    _check_primary_filename(source_meta, chosen["submission_row"])
    if (source_html != (source / "primary_document_source.html").read_bytes() or
            source_meta["sha256"] != sha256(source_html)):
        raise ValueError("Source primary HTML bytes do not reproduce")
    rendered, _ = primary_text(raw, "10-Q")
    if rendered.encode("utf-8") != (source / "primary_text.txt").read_bytes():
        raise ValueError("Rendered primary text cannot be reproduced from raw")
    boundary = json.loads((source / "boundary_evidence.json").read_text(encoding="utf-8"))
    if boundary.get("program_check") != "PASS_EXACT_SUBSTRING_AND_UNIQUE_SUBSTANTIVE_CANDIDATE":
        raise ValueError("Source boundary program check is not PASS")
    candidate = boundary["candidates"][boundary["selected_candidate_index"]]
    section = rendered[candidate["start_char"]:candidate["end_char_exclusive"]]
    if (sha256(section.encode("utf-8")) != candidate["content_sha256"] or
            section.encode("utf-8") != (source / "mda_section_program_candidate.txt").read_bytes()):
        raise ValueError("Section candidate differs from exact raw-derived primary substring")
    review = json.loads(same_agent_review.read_text(encoding="utf-8"))
    if (review.get("source_receipt_sha256") != sha256(receipt_path.read_bytes()) or
            review.get("selected_sha256") != candidate["content_sha256"] or
            review.get("same_agent_boundary_check") !=
            "PROVISIONAL_PASS_FOR_SINGLE_DOCUMENT_ENGINEERING_INFERENCE" or
            review.get("human_review") != "PENDING"):
        raise ValueError("Same-agent boundary evidence is missing or not linked to source")
    return section, {"source_receipt_sha256": sha256(receipt_path.read_bytes()),
                     "source_boundary_sha256": sha256((source / "boundary_evidence.json").read_bytes()),
                     "same_agent_review_sha256": sha256(same_agent_review.read_bytes()),
                     "raw_submission_sha256": sha256(raw),
                     "primary_text_sha256": sha256(rendered.encode("utf-8")),
                     "section_sha256": candidate["content_sha256"],
                     "accession": accession, "cik": CIK, "ticker": TICKER,
                     "primary_document_filename": source_meta["filename_raw_header"],
                     "form": "10-Q", "section": "PART_I_ITEM_2_MDA",
                     "start_char": candidate["start_char"],
                     "end_char_exclusive": candidate["end_char_exclusive"],
                     "human_review": "PENDING"}, boundary


def run_representation(config_path: Path, source: Path, same_agent_review: Path,
                       output: Path) -> dict:
    """Single-document engineering inference after a program-valid boundary."""
    import numpy as np

    output.mkdir(parents=True, exist_ok=False)
    receipt: dict = {"kind": "SINGLE_REAL_10Q_MDA_ENGINEERING_REPRESENTATION",
                     "status": "RUNNING", "started_utc": _utc(),
                     "financial_experiment": "NOT_RUN", "human_review": "PENDING",
                     "no_company_similarity": True,
                     "qpilot_source_sha256": sha256(Path(__file__).read_bytes())}
    write_json(output / "receipt.json", receipt)
    try:
        config = load_config(config_path)
        section, source_identity, boundary = _verified_program_candidate(source, same_agent_review)
        receipt["config_sha256"] = config.source_sha256
        receipt["source_identity"] = source_identity
        enc = Encoder(config)
        receipt["model_identity"] = enc.identity
        stats = []
        for body_length in (config.body_tokens, config.alternate_body_tokens):
            chunks = enc.chunks(section, body_length)
            cursor = 0
            map_rows = []
            for i, chunk in enumerate(chunks):
                count = len(chunk.token_ids)
                if (chunk.token_start != cursor or chunk.token_end_exclusive != cursor + count or
                        count <= 0 or count + enc.special_count > enc.identity["max_input_tokens"] or
                        sha256(section[chunk.char_start:chunk.char_end_exclusive].encode("utf-8")) !=
                        chunk.source_span_sha256):
                    raise ModelError("Token coverage, model input limit or source-span hash failed")
                map_rows.append({"chunk_index": i, "body_token_start": cursor,
                                 "body_token_end_exclusive": chunk.token_end_exclusive,
                                 "body_token_count": count, "source_char_start_in_section": chunk.char_start,
                                 "source_char_end_exclusive_in_section": chunk.char_end_exclusive,
                                 "source_char_start_in_primary": source_identity["start_char"] + chunk.char_start,
                                 "source_char_end_exclusive_in_primary": source_identity["start_char"] + chunk.char_end_exclusive,
                                 "source_span_sha256": chunk.source_span_sha256,
                                 "model_input_tokens_including_specials": count + enc.special_count})
                cursor = chunk.token_end_exclusive
            if cursor != sum(len(c.token_ids) for c in chunks) or chunks[0].token_start != 0:
                raise ModelError("Incomplete or overlapping token map")
            raw_vectors = enc.embed_chunks(chunks)
            if raw_vectors.shape[0] != len(chunks) or not np.isfinite(raw_vectors).all():
                raise ModelError("Invalid saved 10-Q chunk vectors")
            np.save(output / f"chunk_vectors_{body_length}.npy", raw_vectors, allow_pickle=False)
            counts = np.asarray([len(c.token_ids) for c in chunks], dtype=float)
            vector_arms = {}
            for method, weights in (("equal_chunks", None), ("body_token_weighted", counts)):
                document = aggregate(raw_vectors, config.normalize_chunks,
                                     config.normalize_documents, weights=weights)
                vector_arms[method] = document
                np.save(output / f"document_vector_{body_length}_{method}.npy",
                        document, allow_pickle=False)
                reloaded = np.load(output / f"document_vector_{body_length}_{method}.npy",
                                   allow_pickle=False)
                if not np.isfinite(reloaded).all() or abs(float(np.linalg.norm(reloaded)) - 1) > 1e-8:
                    raise ModelError("Saved 10-Q document vector is not finite unit norm")
            with (output / f"chunk_map_{body_length}.jsonl").open("w", encoding="utf-8", newline="\n") as file:
                for row in map_rows:
                    file.write(json.dumps(row, sort_keys=True) + "\n")
            stats.append({"body_chunk_length": body_length, "body_tokens": cursor,
                          "chunk_count": len(chunks), "tail_body_tokens": int(counts[-1]),
                          "max_actual_model_input_tokens": max(r["model_input_tokens_including_specials"] for r in map_rows),
                          "model_input_limit": enc.identity["max_input_tokens"],
                          "token_coverage": "PASS", "source_span_hashes": "PASS",
                          "chunk_vectors_shape": list(raw_vectors.shape),
                          "same_document_equal_vs_weighted_cosine": float(np.dot(
                              vector_arms["equal_chunks"], vector_arms["body_token_weighted"])),
                          "same_document_equal_vs_weighted_vector_max_abs": float(np.max(np.abs(
                              vector_arms["equal_chunks"] - vector_arms["body_token_weighted"]))),
                          "interpretation": "Single-document aggregation sensitivity only; no company-neighbor or financial statistic"})
        write_json(output / "representation_checks.json", {
            "source_identity": source_identity, "model_identity": enc.identity,
            "normalization_order": "model attention-mask mean chunk pooling; optional chunk L2; equal or body-token-weighted mean; document L2",
            "special_tokens_excluded_from_aggregation_weights": True,
            "nonoverlap": config.overlap_tokens == 0,
            "selected_boundary_program_check": boundary["program_check"],
            "same_agent_boundary_review": "PROVISIONAL_PASS_FOR_ENGINEERING_ONLY",
            "human_review": "PENDING", "lengths": stats,
            "not_computed": ["BM25/TF-IDF corpus fit", "company Top-2", "company-pair Spearman",
                             "returns", "risk", "trading backtest"]})
        receipt["status"] = "DONE_ENGINEERING_ONLY_HUMAN_REVIEW_PENDING"
    except (OSError, ValueError, KeyError, TypeError, ModelError, ExtractionError, SecError) as exc:
        receipt["status"] = "BLOCKED" if isinstance(exc, (FileNotFoundError, ModelError)) else "FAILED"
        receipt["reason"] = f"{type(exc).__name__}: {exc}"
    receipt["finished_utc"] = _utc()
    receipt["outputs"] = {str(path.relative_to(output)): {"bytes": path.stat().st_size,
                           "sha256": sha256(path.read_bytes())} for path in sorted(output.rglob("*"))
                          if path.is_file() and path.name != "receipt.json"}
    write_json(output / "receipt.json", receipt)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed-cache-from", type=Path,
                        help="Prior isolated SEC cache; every copied URL/body/metadata is hash-checked")
    parser.add_argument("--represent-source", type=Path,
                        help="Program-checked 10-Q pilot directory; no human attestation is implied")
    parser.add_argument("--same-agent-review", type=Path,
                        help="Linked provisional boundary review, required with --represent-source")
    args = parser.parse_args(argv)
    if args.represent_source:
        if not args.same_agent_review or args.seed_cache_from:
            parser.error("representation needs --same-agent-review and excludes --seed-cache-from")
        result = run_representation(args.config, args.represent_source,
                                    args.same_agent_review, args.output)
    else:
        if args.same_agent_review:
            parser.error("--same-agent-review requires --represent-source")
        result = run(args.config, args.output, args.seed_cache_from)
    print(json.dumps({"status": result["status"], "output": str(args.output),
                      "reason": result.get("reason")}))
    return 0 if result["status"] in {"DONE_BOUNDARY_PROGRAM_PASS_HUMAN_PENDING",
                                     "DONE_ENGINEERING_ONLY_HUMAN_REVIEW_PENDING", "NEEDS_REVIEW"} else 1


if __name__ == "__main__":
    sys.exit(main())
