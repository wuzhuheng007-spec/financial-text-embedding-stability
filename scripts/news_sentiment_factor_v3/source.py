"""Bounded SEC identity and submissions feasibility, with no price or labels.

All raw official responses remain in the local ignored artifacts directory.
This module never reads v2 event scores or return labels.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs/news_sentiment_factor_v3_20261117.json"
PRIVATE = ROOT / "artifacts/news_sentiment_factor_v3_20261117"
PUBLIC = ROOT / "results/news_sentiment_factor_v3_20261117"
LEDGER = PRIVATE / "sec_request_ledger.jsonl"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def checked_user_agent() -> str:
    agent = os.environ.get("SEC_USER_AGENT", "")
    if not re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", agent):
        raise RuntimeError("SEC_USER_AGENT_CONTACT_REQUIRED")
    return agent


class SecReader:
    def __init__(self, config: dict):
        self.config = config
        self.agent = checked_user_agent()
        self.requests = 0
        self.bytes = 0
        self.last_at = 0.0
        self.stopped = False

    def fetch(self, url: str, stem: str, kind: str) -> tuple[dict, bytes | None]:
        budget = self.config["source_request_budget"]
        assert kind in ("ticker", "submissions", "archive")
        if self.stopped or self.requests >= budget["sec_total"]:
            raise RuntimeError("SEC_SOURCE_STOPPED_OR_BUDGET_EXHAUSTED")
        existing = PRIVATE / "raw" / (stem + ".bin")
        if existing.exists():
            raise FileExistsError(existing)
        wait = budget["sec_min_interval_seconds"] - (time.monotonic() - self.last_at)
        if wait > 0:
            time.sleep(wait)
        self.last_at = time.monotonic()
        self.requests += 1
        row = {"kind": kind, "url": url, "started_at_utc": now(), "attempt": 1,
               "retry": 0, "raw_path": None, "sha256": None, "bytes": 0}
        body = None
        try:
            req = Request(url, headers={"User-Agent": self.agent, "Accept-Encoding": "identity",
                                        "Accept": "application/json,text/plain,text/html"})
            with urlopen(req, timeout=budget["timeout_seconds"]) as response:
                row["http_status"] = response.status
                body = response.read(budget["per_response_max_bytes"] + 1)
                if len(body) > budget["per_response_max_bytes"]:
                    raise ValueError("RESPONSE_BYTE_CAP")
                if self.bytes + len(body) > budget["aggregate_max_bytes"]:
                    raise ValueError("AGGREGATE_BYTE_CAP")
                self.bytes += len(body)
                existing.parent.mkdir(parents=True, exist_ok=True)
                existing.write_bytes(body)
                row["raw_path"] = str(existing)
                row["sha256"] = digest(existing)
                row["bytes"] = len(body)
        except HTTPError as error:
            row["http_status"] = error.code
            row["error_class"] = "HTTPError"
            row["retry_after_present"] = error.headers.get("Retry-After") is not None
        except (URLError, TimeoutError, ValueError) as error:
            row["error_class"] = type(error).__name__
        row["completed_at_utc"] = now()
        if row.get("http_status") in budget["stop_source_on_http"]:
            self.stopped = True
        with LEDGER.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row, body


def tickers_by_symbol(value: dict) -> dict[str, dict]:
    fields = value.get("fields")
    data = value.get("data")
    if not isinstance(fields, list) or not isinstance(data, list):
        raise ValueError("TICKER_INDEX_SHAPE")
    result = {}
    for record in data:
        row = dict(zip(fields, record))
        if isinstance(row.get("ticker"), str):
            result[row["ticker"].upper()] = row
    return result


def recent_rows(value: dict) -> list[dict]:
    recent = value.get("filings", {}).get("recent", {})
    fields = ("accessionNumber", "acceptanceDateTime", "filingDate", "form", "items",
              "primaryDocument", "reportDate", "isXBRL")
    lengths = {len(recent[k]) for k in fields if isinstance(recent.get(k), list)}
    if len(lengths) != 1:
        raise ValueError("SEC_RECENT_COLUMN_LENGTHS")
    n = next(iter(lengths), 0)
    if not n:
        raise ValueError("SEC_RECENT_EMPTY")
    return [{k: recent[k][i] if isinstance(recent.get(k), list) else None for k in fields}
            for i in range(n)]


def select_earnings(rows: list[dict], begin: str, end: str) -> tuple[list[dict], Counter]:
    excluded = Counter()
    selected = []
    for row in rows:
        date = str(row.get("filingDate") or "")
        if not begin <= date <= end:
            continue
        if row.get("form") not in ("8-K", "8-K/A"):
            excluded["OTHER_FORM"] += 1
            continue
        items = str(row.get("items") or "")
        if not re.search(r"(?:^|[,;\s])2\.02(?:$|[,;\s])", items):
            excluded["NO_ITEM_2_02_OR_UNKNOWN"] += 1
            continue
        selected.append(row)
    selected.sort(key=lambda x: (str(x.get("acceptanceDateTime")), str(x.get("accessionNumber"))))
    return selected, excluded


def metadata_feasibility() -> dict:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if LEDGER.exists() or (PRIVATE / "metadata_private.json").exists():
        raise FileExistsError("V3_SOURCE_METADATA_RUN_EXISTS")
    PRIVATE.mkdir(parents=True, exist_ok=True)
    reader = SecReader(config)
    index_url = "https://www.sec.gov/files/company_tickers_exchange.json"
    index_result, body = reader.fetch(index_url, "company_tickers_exchange", "ticker")
    if not body or index_result.get("http_status") != 200:
        summary = {"schema": "sentiment_v3_feasibility_redacted_1", "checked_at_utc": now(),
                   "config_sha256": digest(CONFIG), "source_status": "SEC_TICKER_INDEX_FAILED",
                   "requests": reader.requests, "http_status": index_result.get("http_status"),
                   "no_returns_read": True}
        write_json(PUBLIC / "feasibility_redacted.json", summary)
        return summary
    ticker_map = tickers_by_symbol(json.loads(body))
    identity = []
    issuer_entries = []
    for symbol in config["issuer_order"]:
        official = ticker_map.get(symbol)
        entry = {"symbol": symbol, "official_current_identity": official,
                 "historical_symbol_validity": "NOT_PROVEN_BY_CURRENT_TICKER_INDEX"}
        identity.append(entry)
        if official is None:
            entry["status"] = "CURRENT_TICKER_MAPPING_ABSENT"
            continue
        cik = int(official["cik"])
        url = f"https://data.sec.gov/submissions/CIK{cik:010d}.json"
        row, raw = reader.fetch(url, f"submissions_CIK{cik:010d}", "submissions")
        entry["submissions_http_status"] = row.get("http_status")
        entry["submissions_sha256"] = row.get("sha256")
        if raw is None or row.get("http_status") != 200:
            entry["status"] = "SUBMISSIONS_UNAVAILABLE"
            if reader.stopped:
                break
            continue
        value = json.loads(raw)
        if int(value.get("cik")) != cik:
            entry["status"] = "CIK_MISMATCH"
            continue
        rows = recent_rows(value)
        candidates, excluded = select_earnings(rows, "2025-04-01", "2026-03-31")
        first_four = candidates[:4]
        entry.update({"status": "SUBMISSIONS_READ", "issuer_name_in_submissions": value.get("name"),
                      "all_item_2_02_count": len(candidates), "selected_max_four": len(first_four),
                      "independent_acceptance_dates": len({str(x.get("acceptanceDateTime"))[:10] for x in first_four}),
                      "filtered_counts": dict(excluded),
                      "submissions_recent_min_filing_date": min((str(x.get("filingDate")) for x in rows), default=None),
                      "selection_status": "METADATA_ONLY_EXHIBIT_NOT_YET_VERIFIED"})
        issuer_entries.extend([{**x, "symbol": symbol, "cik": cik,
                                "submissions_sha256": row["sha256"]} for x in first_four])
    write_json(PRIVATE / "metadata_private.json", {"schema": "sentiment_v3_metadata_private_1",
               "config_sha256": digest(CONFIG), "ticker_index_sha256": index_result["sha256"],
               "identities": identity, "selected_metadata_events": issuer_entries})
    selected = sorted(issuer_entries, key=lambda x: (str(x.get("acceptanceDateTime")), x["symbol"], x["accessionNumber"]))
    dates = sorted({str(x.get("acceptanceDateTime"))[:10] for x in selected})
    summary = {"schema": "sentiment_v3_feasibility_redacted_1", "checked_at_utc": now(),
               "config_sha256": digest(CONFIG), "ticker_index_sha256": index_result["sha256"],
               "metadata_private_sha256": digest(PRIVATE / "metadata_private.json"),
               "sec_request_ledger_sha256": digest(LEDGER), "source_status": "METADATA_ONLY",
               "requests": reader.requests, "downloaded_bytes": reader.bytes,
               "independent_metadata_events": len(selected), "distinct_acceptance_dates": len(dates),
               "potential_F2_after_two_prior_per_issuer": sum(max(0, n - 2) for n in Counter(x["symbol"] for x in selected).values()),
               "issuer_rows": [{k: x.get(k) for k in ("symbol", "status", "all_item_2_02_count",
                               "selected_max_four", "independent_acceptance_dates",
                               "submissions_http_status", "historical_symbol_validity",
                               "submissions_recent_min_filing_date")} for x in identity],
               "events_not_yet_source_verified": len(selected), "no_returns_read": True}
    write_json(PUBLIC / "feasibility_redacted.json", summary)
    return {k: summary[k] for k in ("source_status", "requests", "independent_metadata_events",
                                    "distinct_acceptance_dates", "events_not_yet_source_verified")}


if __name__ == "__main__":
    print(json.dumps(metadata_feasibility()))
