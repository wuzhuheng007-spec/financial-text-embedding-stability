"""SEC-only HTTP, content-checked cache, and as-of filing discovery."""
from __future__ import annotations

from datetime import datetime, date, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import hashlib
import json
import os
import re
import time
from zoneinfo import ZoneInfo

from .config import Config
from .run import write_json, sha256


class SecError(RuntimeError):
    pass


class MissingContact(SecError):
    pass


def contact_from_environment() -> str:
    value = os.environ.get("SEC_USER_AGENT", "").strip()
    email = re.search(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", value, re.I)
    if not email or len(value) < len(email.group()) + 2 or "example.com" in value.lower():
        raise MissingContact("Set SEC_USER_AGENT to your real identifying name and contact email in the process environment")
    return value


class SecClient:
    def __init__(self, config: Config, user_agent: str, sleep=time.sleep, opener=urlopen):
        self.config = config
        self.user_agent = user_agent
        self.sleep = sleep
        self.opener = opener
        self.last_request = None

    def get(self, url: str) -> tuple[bytes, dict]:
        host = urlparse(url).hostname
        if urlparse(url).scheme != "https" or host not in {"www.sec.gov", "data.sec.gov"}:
            raise SecError("Only official SEC HTTPS URLs are allowed")
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        body_path = self.config.cache / f"{key}.bin"
        meta_path = self.config.cache / f"{key}.json"
        if body_path.exists() and meta_path.exists():
            body = body_path.read_bytes()
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("url") == url and meta.get("sha256") == sha256(body) and meta.get("bytes") == len(body):
                return body, {**meta, "cache_hit": True}
            raise SecError(f"Cache integrity failed for {url}")

        retry_codes = {429, 500, 502, 503, 504}
        for attempt in range(1, self.config.max_attempts + 1):
            if self.last_request is not None:
                self.sleep(max(0.0, self.config.min_interval_seconds - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            request = Request(url, headers={"User-Agent": self.user_agent, "Accept-Encoding": "identity"})
            try:
                with self.opener(request, timeout=self.config.timeout_seconds) as response:
                    if urlparse(response.geturl()).hostname not in {"www.sec.gov", "data.sec.gov"}:
                        raise SecError("SEC request redirected off official host")
                    body = response.read(self.config.max_response_bytes + 1)
                    if len(body) > self.config.max_response_bytes:
                        raise SecError("SEC response exceeded configured byte limit")
                    meta = {"url": url, "retrieved_utc": datetime.now(timezone.utc).isoformat(),
                            "sha256": sha256(body), "bytes": len(body), "cache_hit": False,
                            "http_status": response.status}
                    self.config.cache.mkdir(parents=True, exist_ok=True)
                    body_path.write_bytes(body)
                    write_json(meta_path, meta)
                    return body, meta
            except HTTPError as exc:
                if exc.code not in retry_codes or attempt == self.config.max_attempts:
                    raise SecError(f"SEC HTTP {exc.code} for {url}; attempt {attempt}") from exc
                delay = _retry_delay(exc.headers.get("Retry-After"), attempt)
            except (URLError, TimeoutError) as exc:
                if attempt == self.config.max_attempts:
                    raise SecError(f"SEC network failure for {url}; attempt {attempt}: {type(exc).__name__}") from exc
                delay = min(2 ** (attempt - 1), 8)
            self.sleep(delay)
        raise AssertionError("unreachable")

    def json(self, url: str) -> tuple[dict, dict]:
        body, meta = self.get(url)
        try:
            value = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SecError(f"Invalid SEC JSON at {url}") from exc
        if not isinstance(value, dict):
            raise SecError(f"SEC JSON root is not an object at {url}")
        return value, meta


def _retry_delay(header: str | None, attempt: int) -> float:
    if header:
        try:
            return min(max(float(header), 0.0), 30.0)
        except ValueError:
            try:
                return min(max((parsedate_to_datetime(header) - datetime.now(timezone.utc)).total_seconds(), 0.0), 30.0)
            except (TypeError, ValueError, OverflowError):
                pass
    return min(2 ** (attempt - 1), 8)


def discover_cik(client: SecClient, ticker: str) -> tuple[str, dict]:
    url = "https://www.sec.gov/files/company_tickers.json"
    data, meta = client.json(url)
    matches = [row for row in data.values() if isinstance(row, dict) and str(row.get("ticker", "")).upper() == ticker]
    if len(matches) != 1:
        raise SecError(f"Ticker {ticker} has {len(matches)} exact matches in SEC company_tickers.json")
    return f"{int(matches[0]['cik_str']):010d}", meta


def _rows(columns: dict) -> list[dict]:
    if not isinstance(columns, dict) or not isinstance(columns.get("accessionNumber"), list):
        raise SecError("SEC submissions array missing accessionNumber")
    n = len(columns["accessionNumber"])
    required = ("form", "acceptanceDateTime", "filingDate", "reportDate")
    if any(not isinstance(columns.get(k), list) or len(columns[k]) != n for k in required):
        raise SecError("SEC submissions columns are incomplete or unequal length")
    return [{k: v[i] for k, v in columns.items() if isinstance(v, list) and len(v) == n} for i in range(n)]


def _acceptance(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise SecError(f"Invalid SEC acceptanceDateTime: {value}") from exc
    eastern = ZoneInfo("America/New_York")
    # Current SEC submissions JSON can carry a UTC "Z" timestamp; older
    # fixtures/documentation use a naive Eastern wall clock value.
    return parsed.replace(tzinfo=eastern) if parsed.tzinfo is None else parsed.astimezone(eastern)


def discover_filing(client: SecClient, cik: str, as_of: date) -> tuple[dict, list[dict], list[dict]]:
    if not re.fullmatch(r"\d{10}", cik):
        raise SecError("CIK must be 10 digits")
    base = f"https://data.sec.gov/submissions/CIK{cik}.json"
    main, meta = client.json(base)
    if str(main.get("cik", "")).zfill(10) != cik:
        raise SecError("SEC submissions CIK differs from ticker mapping")
    submissions = main.get("filings", {})
    rows = _rows(submissions.get("recent", {}))
    sources = [meta]
    for prior in submissions.get("files", []):
        name = prior.get("name", "")
        if not re.fullmatch(r"CIK\d{10}-submissions-\d{3}\.json", name):
            raise SecError("Unexpected SEC historical submissions filename")
        old, old_meta = client.json(f"https://data.sec.gov/submissions/{name}")
        rows.extend(_rows(old))
        sources.append(old_meta)
    eligible = [r for r in rows if r.get("form") in {"10-K", "10-K/A"} and _acceptance(r["acceptanceDateTime"]).date() <= as_of]
    originals = [r for r in eligible if r["form"] == "10-K"]
    if not originals:
        raise SecError(f"No original 10-K accepted by {as_of.isoformat()} for CIK {cik}")
    selected = max(originals, key=lambda r: (_acceptance(r["acceptanceDateTime"]), r["accessionNumber"]))
    if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", selected["accessionNumber"]):
        raise SecError("Invalid SEC accession number")
    report_date = selected.get("reportDate")
    amendments = sorted((r for r in eligible if r["form"] == "10-K/A" and r.get("reportDate") == report_date),
                        key=lambda r: _acceptance(r["acceptanceDateTime"]))
    return selected, amendments, sources


def submission_url(cik: str, accession: str) -> str:
    if not re.fullmatch(r"\d{10}", cik) or not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
        raise SecError("Invalid CIK or accession for archive URL")
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{accession}.txt"


def raw_header(raw: bytes, expected_accession: str, expected_form: str = "10-K") -> dict:
    if expected_form not in {"10-K", "10-Q"}:
        raise SecError("Only original 10-K and 10-Q forms are supported")
    header_end = raw.find(b"</SEC-HEADER>")
    if header_end < 0:
        raise SecError("Complete SEC submission lacks a closed SEC-HEADER")
    header = raw[:header_end]
    accepted = re.search(rb"<ACCEPTANCE-DATETIME>(\d{14})", header)
    accession = re.search(rb"ACCESSION NUMBER:\s*([0-9-]+)", header)
    form = re.search(rb"CONFORMED SUBMISSION TYPE:\s*([^\r\n]+)", header)
    filed = re.search(rb"FILED AS OF DATE:\s*(\d{8})", header)
    if not all((accepted, accession, form, filed)):
        raise SecError("Complete SEC submission header lacks required identity/time fields")
    raw_accession = accession.group(1).decode("ascii")
    if raw_accession != expected_accession or form.group(1).strip() != expected_form.encode("ascii"):
        raise SecError(f"Raw SEC header accession or form differs from selected original {expected_form}")
    try:
        local = datetime.strptime(accepted.group(1).decode("ascii"), "%Y%m%d%H%M%S").replace(
            tzinfo=ZoneInfo("America/New_York"))
        filing_date = datetime.strptime(filed.group(1).decode("ascii"), "%Y%m%d").date()
    except ValueError as exc:
        raise SecError("Invalid raw SEC header date/time") from exc
    return {"accession": raw_accession, "form": expected_form, "acceptance_datetime_raw_header": accepted.group(1).decode("ascii"),
            "acceptance_datetime_eastern": local.isoformat(), "filing_date_raw_header": filing_date.isoformat()}


def raw_filer_cik(raw: bytes) -> str:
    """Read the issuer CIK from the SEC-HEADER FILER block, not the accession prefix.

    Multi-filer or missing-FILER submissions need separate identity review and
    therefore fail closed here. The raw_header return schema remains unchanged.
    """
    end = raw.find(b"</SEC-HEADER>")
    if end < 0:
        raise SecError("Complete SEC submission lacks a closed SEC-HEADER")
    header = raw[:end]
    starts = list(re.finditer(rb"(?m)^FILER:[ \t]*\r?$", header))
    if len(starts) != 1:
        raise SecError(f"Expected one SEC-HEADER FILER block; found {len(starts)}")
    tail = header[starts[0].end():]
    next_block = re.search(rb"(?m)^[A-Z][A-Z /-]*:[ \t]*\r?$", tail)
    filer = tail[:next_block.start()] if next_block else tail
    ciks = re.findall(rb"(?m)^[ \t]+CENTRAL INDEX KEY:[ \t]*(\d{1,10})[ \t]*\r?$", filer)
    if len(ciks) != 1:
        raise SecError(f"Expected one FILER CENTRAL INDEX KEY; found {len(ciks)}")
    return ciks[0].decode("ascii").zfill(10)
