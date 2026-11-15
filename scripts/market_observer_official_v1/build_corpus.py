"""Bounded official corpus from already saved SEC metadata and the Fed calendar.

Raw source bodies and per-request receipts are private. This script never infers
historical first receipt from a present-day download.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs" / "market_observer_official_v1.json"
OLD = Path(os.environ.get("OFFICIAL_PRIOR_SOURCE_DIR", str(ROOT / "artifacts" / "historical_news_probe_20261115")))
PRIVATE = ROOT / "artifacts" / "market_observer_official_20261115"
PUBLIC = ROOT / "results" / "market_observer_official_20261115"
META = OLD / "sec_filings_metadata_v2_private.jsonl"
CALENDAR = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def html_text(data: bytes, source_kind: str = "") -> str:
    soup = BeautifulSoup(data, "html.parser")
    if source_kind == "FOMC_STATEMENTS":
        article = soup.find(id="article")
        if article is not None:
            soup = article
    for tag in soup(["script", "style", "noscript", "ix:header"]):
        tag.decompose()
    return re.sub(r"[ \t\xa0]+", " ", soup.get_text(" ", strip=True)).strip()


def existing_cache() -> dict[str, tuple[Path, str | None]]:
    cache: dict[str, tuple[Path, str | None]] = {}
    for name in ("official_originals_metadata_private.jsonl", "macro_originals_metadata_private.jsonl"):
        for row in read_lines(OLD / name):
            path = OLD / (row.get("local_file") or "")
            if path.is_file() and row.get("source_url") and sha(path.read_bytes()) == row.get("source_sha256"):
                cache[row["source_url"]] = (path, row.get("retrieved_at_utc"))
    row_path = OLD / "official_supplement_receipt_private.json"
    if row_path.exists():
        row = json.loads(row_path.read_text(encoding="utf-8"))
        path = OLD / (row.get("file") or "")
        if path.is_file() and row.get("sha256") == sha(path.read_bytes()):
            cache[row["url"]] = (path, row.get("retrieved_at_utc"))
    originals = OLD / "official_originals_receipt_private.json"
    if originals.exists():
        for row in json.loads(originals.read_text(encoding="utf-8")).get("documents", []):
            path = OLD / (row.get("local_file") or "")
            if path.is_file() and row.get("sha256") == sha(path.read_bytes()):
                cache[row["url"]] = (path, row.get("retrieved_at_utc"))
    return cache


def sec_announcement_date(meta: dict) -> str:
    accepted = datetime.fromisoformat(meta["published_at_raw"].replace("Z", "+00:00"))
    accepted_date_et = accepted.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    # A filingDate later than the Eastern acceptance date occurs in this
    # metadata. Taking the later official date is a conservative proxy.
    return max(meta["filing_date"], accepted_date_et)


def sec_acceptance_date_et(meta: dict) -> str:
    accepted = datetime.fromisoformat(meta["published_at_raw"].replace("Z", "+00:00"))
    return accepted.astimezone(ZoneInfo("America/New_York")).date().isoformat()


class Fetcher:
    def __init__(self, config: dict):
        self.config = config
        self.cache = existing_cache()
        self.ledger_path = PRIVATE / "request_ledger_private.jsonl"
        self.old_ledger = read_lines(self.ledger_path)
        self.by_url = {r["url"]: r for r in self.old_ledger if r.get("url")}
        self.count = sum(1 for r in self.old_ledger if r.get("attempted"))
        self.source_count = Counter(r.get("source") for r in self.old_ledger if r.get("attempted"))
        self.stopped: set[str] = set()
        self.last_request: dict[str, float] = {}

    def fetch(self, url: str, source: str) -> tuple[bytes | None, dict]:
        if url in self.cache:
            path, original_retrieval = self.cache[url]
            data = path.read_bytes()
            return data, {"url": url, "source": source, "status": "CACHED_VERIFIED", "raw_path": str(path),
                          "sha256": sha(data), "bytes": len(data),
                          "retrieved_at_utc": original_retrieval, "cache_verified_at_utc": now()}
        if url in self.by_url:
            receipt = self.by_url[url]
            if receipt.get("status") == "HTTP_200" and receipt.get("raw_path"):
                path = Path(receipt["raw_path"])
                if path.is_file() and sha(path.read_bytes()) == receipt.get("sha256"):
                    return path.read_bytes(), receipt
            return None, receipt
        limit = self.config["max_new_http_requests"]
        source_limit = self.config["max_sec_requests"] if source == "SEC" else self.config["max_fed_requests"]
        if source in self.stopped or self.count >= limit or self.source_count[source] >= source_limit:
            return None, {"url": url, "source": source, "status": "BUDGET_OR_SOURCE_STOP"}
        if source == "SEC":
            user_agent = os.environ.get("SEC_USER_AGENT", "")
            if not re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", user_agent, re.I):
                return None, {"url": url, "source": source, "status": "MISSING_IDENTIFYING_USER_AGENT"}
        else:
            user_agent = "quant-official-corpus/1.0 research contact via repository"
        wait = self.config["per_source_min_interval_seconds"] - (time.monotonic() - self.last_request.get(source, -1e9))
        if wait > 0:
            time.sleep(wait)
        self.last_request[source] = time.monotonic()
        requested = now()
        start = time.monotonic()
        receipt = {"url": url, "source": source, "attempted": True, "requested_at_utc": requested}
        try:
            request = Request(url, headers={"User-Agent": user_agent, "Accept-Encoding": "identity"})
            with urlopen(request, timeout=self.config["request_timeout_seconds"]) as response:
                data = response.read(self.config["per_document_max_bytes"] + 1)
                receipt["http_status"] = response.status
                receipt["content_type"] = response.headers.get("Content-Type")
                receipt["last_modified_header"] = response.headers.get("Last-Modified")
            if len(data) > self.config["per_document_max_bytes"]:
                receipt["status"] = "TOO_LARGE"
                data = None
            else:
                target = PRIVATE / "raw" / (sha(url.encode()) + ".html")
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists() and target.read_bytes() != data:
                    raise RuntimeError("Existing local raw file differs")
                if not target.exists():
                    target.write_bytes(data)
                receipt.update(status="HTTP_200", raw_path=str(target), sha256=sha(data), bytes=len(data))
        except HTTPError as exc:
            data = None
            receipt.update(status=f"HTTP_{exc.code}", http_status=exc.code, error_type="HTTPError")
            if exc.code in self.config["stop_source_on_status"]:
                self.stopped.add(source)
        except (URLError, TimeoutError, OSError, RuntimeError) as exc:
            data = None
            receipt.update(status="REQUEST_FAILED", error_type=type(exc).__name__, error=str(exc)[:160])
        receipt["completed_at_utc"] = now()
        receipt["elapsed_ms"] = round((time.monotonic() - start) * 1000)
        with self.ledger_path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n")
        self.by_url[url] = receipt
        self.count += 1
        self.source_count[source] += 1
        return data, receipt


def content_record(base: dict, data: bytes | None, receipt: dict) -> dict:
    row = dict(base)
    row.update(
        retrieved_at_utc=receipt.get("completed_at_utc") or receipt.get("retrieved_at_utc"),
        cache_verified_at_utc=receipt.get("cache_verified_at_utc"),
        http_last_modified_header=receipt.get("last_modified_header"),
        raw_body_path=receipt.get("raw_path"),
        raw_body_sha256=receipt.get("sha256"),
        body_path=None,
        body_sha256=None,
        full_body_status="FAILED",
        acquisition_status=receipt.get("status"),
    )
    if data is None:
        return row
    text = html_text(data, base["source_kind"])
    if len(text) < 400 or "request rate threshold exceeded" in text.lower() or "access denied" in text.lower():
        row["full_body_status"] = "INVALID_BODY"
        return row
    extraction_version = ("bs4_fed_article_v2" if base["source_kind"] == "FOMC_STATEMENTS"
                          else "bs4_strip_script_style_ixheader_v1")
    key = base["source_url"] + ("|" + extraction_version if base["source_kind"] == "FOMC_STATEMENTS" else "")
    body_path = PRIVATE / "text" / (sha(key.encode()) + ".txt")
    body_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = text.encode("utf-8")
    if body_path.exists() and body_path.read_bytes() != encoded:
        raise RuntimeError(f"Existing extracted text differs: {body_path}")
    if not body_path.exists():
        body_path.write_bytes(encoded)
    row.update(body_path=str(body_path), body_sha256=sha(encoded),
               full_body_status="FULL_BODY", extracted_characters=len(text),
               extraction_version=extraction_version)
    return row


def archive_dir(url: str) -> str:
    return url.rsplit("/", 1)[0] + "/"


def earnings_exhibit_href(raw: bytes, filing_url: str) -> str | None:
    soup = BeautifulSoup(raw, "html.parser")
    text = soup.get_text(" ", strip=True).lower()
    if "item 2.02" not in text and "results of operations and financial condition" not in text:
        return None
    candidates = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        name = a.get_text(" ", strip=True).lower()
        if href.startswith(("http:", "https:", "#")) or not re.search(r"\.html?$", href, re.I):
            continue
        if re.search(r"press release|earnings release|exhibit 99[.\-]?1|exhibit 99\.01", name) or re.search(r"press|earn|pr\.htm", href, re.I):
            full = urljoin(filing_url, href)
            if urlparse(full).netloc == "www.sec.gov" and full != filing_url:
                candidates.append(full)
    return candidates[0] if candidates else None


def collect_fomc_links(data: bytes, expected: list[str]) -> dict[str, str]:
    soup = BeautifulSoup(data, "html.parser")
    links = {}
    for a in soup.find_all("a", href=True):
        full = urljoin(CALENDAR, a["href"])
        m = re.search(r"monetary(20\d{6})a\.htm$", urlparse(full).path)
        if m and m.group(1) in {d.replace("-", "") for d in expected}:
            date = f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}"
            if "statement" in a.get_text(" ", strip=True).lower() or date not in links:
                links[date] = full
    return links


def months(start: str, end: str) -> list[str]:
    year, month = map(int, start[:7].split("-"))
    ey, em = map(int, end[:7].split("-"))
    result = []
    while (year, month) <= (ey, em):
        result.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return result


def monthly_table(documents: list[dict], config: dict, calendar_verified: bool) -> list[dict]:
    result = []
    for month in months(config["window_start"], config["window_end"]):
        for source_kind, symbol in [("SEC_FILINGS", "NVDA"), ("SEC_FILINGS", "MU"),
                                     ("ISSUER_EARNINGS", "NVDA"), ("ISSUER_EARNINGS", "MU"),
                                     ("FOMC_STATEMENTS", "GLOBAL")]:
            docs = [d for d in documents if d["source_kind"] == source_kind and
                    d["symbol"] == symbol and d["announcement_date"].startswith(month)]
            planned = len(docs)
            full = sum(d["full_body_status"] == "FULL_BODY" for d in docs)
            failed = sum(d["full_body_status"] in {"FAILED", "INVALID_BODY"} for d in docs)
            duplicate = sum(bool(d.get("duplicate_of")) for d in docs)
            unknown = planned - full - failed
            if source_kind == "ISSUER_EARNINGS":
                status = "UNKNOWN"  # SEC exhibits cannot certify the complete issuer IR archive.
            elif source_kind == "FOMC_STATEMENTS" and not calendar_verified:
                status = "UNKNOWN"
            elif failed or unknown:
                status = "INCOMPLETE"
            else:
                status = "COMPLETE"
            result.append(dict(month=month, source_kind=source_kind, symbol=symbol, status=status,
                               planned=planned, full_body=full, failed=failed, duplicate=duplicate,
                               unknown=unknown, version_note=("Current mutable web version unverified" if source_kind == "FOMC_STATEMENTS" else None)))
    return result


def run() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    PRIVATE.mkdir(parents=True, exist_ok=True)
    PUBLIC.mkdir(parents=True, exist_ok=True)
    metadata = [r for r in read_lines(META) if r.get("ticker_candidate") in config["sec_symbols"] and
                r.get("form") in config["sec_forms"] and
                config["window_start"] <= r["filing_date"] <= config["window_end"]]
    metadata.sort(key=lambda r: (r["filing_date"], r["ticker_candidate"], r["event_id"]))
    write_json(PRIVATE / "frozen_plan.json", {
        "config_sha256": sha(CONFIG.read_bytes()), "metadata_sha256": sha(META.read_bytes()),
        "sec_planned": len(metadata), "fomc_expected_dates": config["fomc_expected_dates"],
        "max_new_http_requests": config["max_new_http_requests"],
        "created_at_utc": now() if not (PRIVATE / "frozen_plan.json").exists() else
        json.loads((PRIVATE / "frozen_plan.json").read_text(encoding="utf-8"))["created_at_utc"],
    })
    fetcher = Fetcher(config)
    documents = []
    exhibits = []
    for meta in metadata:
        url = meta["source_url"]
        data, receipt = fetcher.fetch(url, "SEC")
        record = content_record({
            "document_id": meta["event_id"] + ":PRIMARY", "event_id": meta["event_id"],
            "symbol": meta["ticker_candidate"], "source_kind": "SEC_FILINGS",
            "publisher": "US SEC", "form": meta["form"], "announcement_date": sec_announcement_date(meta),
            "announcement_date_basis": "MAX_SEC_FILING_DATE_ACCEPTANCE_ET_DATE",
            "filing_date": meta["filing_date"], "sec_filing_date": meta["filing_date"],
            "sec_acceptance_date_et": sec_acceptance_date_et(meta),
            "sec_accession": meta["event_id"].split(":")[-1],
            "sec_acceptance_at": meta["published_at_raw"], "official_published_at": None,
            "web_updated_at": None, "historical_first_available_at": None,
            "time_zone": "SEC API UTC; SEC archive acceptance context US Eastern",
            "version_status": "SEC_ARCHIVE_HASHED", "source_url": url,
            "content_type": "SEC_PRIMARY_FILING", "event_group_id": meta["event_id"],
        }, data, receipt)
        documents.append(record)
        if data and record["full_body_status"] == "FULL_BODY" and meta["form"] == "8-K":
            exhibit_url = earnings_exhibit_href(data, url)
            if exhibit_url:
                exhibits.append((meta, exhibit_url))
    # Exhibits share the SEC accession event and are not independent announcements.
    seen_exhibits = set()
    for meta, url in exhibits[:config["max_exhibit_requests"]]:
        if url in seen_exhibits:
            continue
        seen_exhibits.add(url)
        data, receipt = fetcher.fetch(url, "SEC")
        record = content_record({
            "document_id": meta["event_id"] + ":EARNINGS_EXHIBIT:" + sha(url.encode())[:12],
            "event_id": meta["event_id"], "symbol": meta["ticker_candidate"],
            "source_kind": "ISSUER_EARNINGS", "publisher": meta["issuer_or_filer"],
            "form": "EXHIBIT", "announcement_date": sec_announcement_date(meta),
            "announcement_date_basis": "MAX_SEC_FILING_DATE_ACCEPTANCE_ET_DATE",
            "filing_date": meta["filing_date"], "sec_filing_date": meta["filing_date"],
            "sec_acceptance_date_et": sec_acceptance_date_et(meta),
            "sec_accession": meta["event_id"].split(":")[-1],
            "sec_acceptance_at": meta["published_at_raw"], "official_published_at": None,
            "web_updated_at": None, "historical_first_available_at": None,
            "time_zone": "SEC API UTC; corporate release time unverified",
            "version_status": "SEC_ARCHIVE_HASHED", "source_url": url,
            "content_type": "SEC_ATTACHED_ISSUER_EARNINGS_RELEASE",
            "event_group_id": meta["event_id"], "same_event_as_filing": True,
        }, data, receipt)
        documents.append(record)
    calendar_data, calendar_receipt = fetcher.fetch(CALENDAR, "FED")
    links = collect_fomc_links(calendar_data, config["fomc_expected_dates"]) if calendar_data else {}
    calendar_verified = len(links) == len(config["fomc_expected_dates"])
    for date in config["fomc_expected_dates"]:
        url = links.get(date)
        if not url:
            documents.append({
                "document_id": f"fed:fomc:{date}", "event_id": f"fed:fomc:{date}", "symbol": "GLOBAL",
                "source_kind": "FOMC_STATEMENTS", "publisher": "Federal Reserve",
                "announcement_date": date, "sec_acceptance_at": None, "official_published_at": None,
                "web_updated_at": None, "historical_first_available_at": None, "version_status": "UNKNOWN",
                "source_url": None, "content_type": "FOMC_STATEMENT", "full_body_status": "FAILED",
                "acquisition_status": "CALENDAR_LINK_UNVERIFIED",
            })
            continue
        data, receipt = fetcher.fetch(url, "FED")
        record = content_record({
            "document_id": f"fed:fomc:{date}", "event_id": f"fed:fomc:{date}", "symbol": "GLOBAL",
            "source_kind": "FOMC_STATEMENTS", "publisher": "Federal Reserve",
            "announcement_date": date, "sec_acceptance_at": None, "official_published_at": None,
            "web_updated_at": None, "historical_first_available_at": None,
            "time_zone": "America/New_York; official release clock to be verified",
            "version_status": "UNKNOWN", "source_url": url, "content_type": "FOMC_STATEMENT",
            "event_group_id": f"fed:fomc:{date}",
        }, data, receipt)
        if data and re.search(r"for release at\s+2:00\s+p\.m\.", html_text(data, "FOMC_STATEMENTS"), re.I):
            record["official_published_at"] = date + "T14:00:00 America/New_York (page release clock)"
        documents.append(record)
    table = monthly_table(documents, config, calendar_verified)
    prior_sec_receipt = OLD / "sec_receipt_private.json"
    index_coverage = []
    if prior_sec_receipt.exists():
        previous = json.loads(prior_sec_receipt.read_text(encoding="utf-8"))
        for source in previous.get("sources", []):
            if source.get("ticker_or_filer") in config["sec_symbols"]:
                index_coverage.append({
                    "symbol": source["ticker_or_filer"],
                    "recent_min_filing_date": source.get("recent_min_filing_date"),
                    "history_files_overlapping_window": source.get("history_files_overlapping_window"),
                    "index_retrieved_at_utc": source.get("fetches", [{}])[0].get("retrieved_at_utc"),
                    "index_source_url": source.get("fetches", [{}])[0].get("url"),
                })
    manifest = {
        "schema_version": "official_corpus_manifest_v1", "scope_config_sha256": sha(CONFIG.read_bytes()),
        "source_metadata_sha256": sha(META.read_bytes()), "generated_at_utc": now(),
        "request_ledger_path": str(fetcher.ledger_path), "new_http_requests_total": fetcher.count,
        "new_http_requests_by_source": dict(fetcher.source_count),
        "fomc_calendar_link_count": len(links), "fomc_calendar_verified": calendar_verified,
        "fomc_calendar_url": CALENDAR, "index_coverage": index_coverage,
        "version_status_definition": {
            "SEC_ARCHIVE_HASHED": "Accession-specific URL and current bytes hashed; no independent proof those same bytes were publicly available on announcement date",
            "UNKNOWN": "Current page body obtained but historical exact version unverified"
        },
        "documents": documents, "monthly_coverage": table,
    }
    write_json(PRIVATE / "corpus_manifest.json", manifest)
    write_json(PRIVATE / "monthly_coverage.json", table)
    summary = {
        "schema_version": "official_corpus_public_v1", "scope_config_sha256": sha(CONFIG.read_bytes()),
        "source_metadata_sha256": sha(META.read_bytes()), "generated_at_utc": manifest["generated_at_utc"],
        "window": [config["window_start"], config["window_end"]],
        "new_http_requests_total": fetcher.count,
        "new_http_requests_by_source": dict(fetcher.source_count),
        "planned_sec_filings": len(metadata), "sec_full_bodies": sum(d["source_kind"] == "SEC_FILINGS" and d["full_body_status"] == "FULL_BODY" for d in documents),
        "earnings_exhibits_discovered": len(exhibits), "earnings_exhibit_full_bodies": sum(d["source_kind"] == "ISSUER_EARNINGS" and d["full_body_status"] == "FULL_BODY" for d in documents),
        "fomc_statements_expected": len(config["fomc_expected_dates"]),
        "fomc_calendar_link_count": len(links),
        "fomc_full_bodies": sum(d["source_kind"] == "FOMC_STATEMENTS" and d["full_body_status"] == "FULL_BODY" for d in documents),
        "historical_first_availability_verified": 0,
        "sec_filing_date_acceptance_et_discrepancies": sum(
            d["sec_filing_date"] != d["sec_acceptance_date_et"] for d in documents
            if d["source_kind"] == "SEC_FILINGS"
        ),
        "issuer_ir_archive_completeness": "UNKNOWN",
        "sec_monthly_complete_scope": "Complete only against frozen SEC submissions index for NVDA/MU 8-K, 10-Q, 10-K and current hashed accession bodies; not all issuer communications or historical EDGAR versions",
        "index_coverage": index_coverage,
        "fomc_calendar_source_url": CALENDAR,
        "fomc_calendar_verified": calendar_verified,
        "fomc_historical_version_status": "UNKNOWN",
        "sec_historical_first_availability_status": "NOT_VERIFIED",
        "standalone_issuer_ir_archive_gaps": [
            "NVDA dated IR release archive completeness not verified",
            "MU dated IR release archive completeness not verified"
        ],
        "documents": [{"document_id": d["document_id"], "symbol": d["symbol"], "source_kind": d["source_kind"],
                       "announcement_date": d["announcement_date"], "source_url": d.get("source_url"),
                       "full_body_status": d["full_body_status"], "version_status": d["version_status"],
                       "acquisition_status": d["acquisition_status"],
                       "event_group_id": d.get("event_group_id")} for d in documents],
        "monthly_coverage": table,
    }
    write_json(PUBLIC / "corpus_coverage_redacted.json", summary)
    print(json.dumps({k: v for k, v in summary.items() if k not in ("documents", "monthly_coverage")}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-plan", action="store_true")
    args = parser.parse_args()
    if args.dry_plan:
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        metadata = [r for r in read_lines(META) if r.get("ticker_candidate") in config["sec_symbols"] and
                    r.get("form") in config["sec_forms"] and config["window_start"] <= r["filing_date"] <= config["window_end"]]
        print(json.dumps({"config_sha256": sha(CONFIG.read_bytes()), "sec_rows": len(metadata),
                          "fomc_dates": len(config["fomc_expected_dates"]),
                          "max_new_http_requests": config["max_new_http_requests"]}))
    else:
        run()
