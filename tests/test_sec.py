"""Synthetic fixtures only; these tests never assert that SEC was downloaded."""
from datetime import date
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
import json

import pytest

from finembed.config import load_config
from finembed.sec import SecClient, SecError, discover_cik, discover_filing, submission_url, _acceptance, raw_header


class FakeResponse:
    status = 200

    def __init__(self, url: str, body: bytes):
        self.url = url
        self.file = BytesIO(body)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def geturl(self):
        return self.url

    def read(self, n):
        return self.file.read(n)


def test_cache_sha_and_official_host(tmp_path):
    config = load_config(Path(__file__).parents[1] / "configs" / "smoke.yaml")
    config = SimpleNamespace(**{**config.__dict__, "cache": tmp_path})
    calls = []
    url = "https://data.sec.gov/submissions/CIK0000320193.json"

    def opener(request, timeout):
        calls.append((request.full_url, timeout))
        return FakeResponse(request.full_url, b'{"cik":320193}')

    client = SecClient(config, "Synthetic Test synthetic@test.invalid", sleep=lambda _: None, opener=opener)
    body, first = client.get(url)
    assert body == b'{"cik":320193}' and not first["cache_hit"]
    assert client.get(url)[1]["cache_hit"] and len(calls) == 1
    next(tmp_path.glob("*.bin")).write_bytes(b"corrupt")
    with pytest.raises(SecError, match="integrity"):
        client.get(url)
    with pytest.raises(SecError, match="official SEC"):
        client.get("https://example.org/filing")


def test_429_retries_with_backoff_and_request_spacing(tmp_path):
    config = load_config(Path(__file__).parents[1] / "configs" / "smoke.yaml")
    config = SimpleNamespace(**{**config.__dict__, "cache": tmp_path})
    calls, sleeps = [], []
    url = "https://data.sec.gov/submissions/CIK0000320193.json"

    def opener(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise HTTPError(url, 429, "synthetic rate limit", {"Retry-After": "1"}, None)
        return FakeResponse(request.full_url, b"{}")

    client = SecClient(config, "Synthetic Test synthetic@test.invalid", sleep=sleeps.append, opener=opener)
    assert client.get(url)[0] == b"{}"
    assert len(calls) == 2
    assert any(delay >= 1 for delay in sleeps)


class SyntheticClient:
    def __init__(self, data):
        self.data = data

    def json(self, url):
        return self.data[url], {"url": url, "sha256": "synthetic", "retrieved_utc": "synthetic"}


def _columns(rows):
    return {key: [r[key] for r in rows] for key in ("form", "accessionNumber", "acceptanceDateTime", "filingDate", "reportDate")}


def test_discovery_respects_acceptance_asof_and_records_amendments():
    cik = "0000320193"
    base = f"https://data.sec.gov/submissions/CIK{cik}.json"
    rows = [
        {"form": "10-K", "accessionNumber": "0000320193-24-000001", "acceptanceDateTime": "2024-11-01T18:00:00", "filingDate": "2024-11-01", "reportDate": "2024-09-28"},
        {"form": "10-K/A", "accessionNumber": "0000320193-24-000002", "acceptanceDateTime": "2024-11-05T17:00:00", "filingDate": "2024-11-05", "reportDate": "2024-09-28"},
        {"form": "10-K", "accessionNumber": "0000320193-25-000001", "acceptanceDateTime": "2025-11-01T18:00:00", "filingDate": "2025-11-01", "reportDate": "2025-09-27"},
        {"form": "10-K/A", "accessionNumber": "0000320193-25-000002", "acceptanceDateTime": "2026-01-03T17:00:00", "filingDate": "2026-01-03", "reportDate": "2025-09-27"},
        {"form": "10-K", "accessionNumber": "0000320193-26-000001", "acceptanceDateTime": "2026-11-01T18:00:00", "filingDate": "2026-11-01", "reportDate": "2026-09-26"},
    ]
    client = SyntheticClient({"https://www.sec.gov/files/company_tickers.json": {"0": {"ticker": "AAPL", "cik_str": 320193}},
                              base: {"cik": 320193, "filings": {"recent": _columns(rows), "files": []}}})
    mapped, _ = discover_cik(client, "AAPL")
    assert mapped == cik
    selected, amendments, _ = discover_filing(client, mapped, date(2025, 12, 31))
    assert selected["accessionNumber"] == "0000320193-25-000001"
    assert amendments == []
    assert submission_url(cik, selected["accessionNumber"]) == "https://www.sec.gov/Archives/edgar/data/320193/000032019325000001/0000320193-25-000001.txt"
    selected, amendments, _ = discover_filing(client, mapped, date(2026, 1, 3))
    assert [a["accessionNumber"] for a in amendments] == ["0000320193-25-000002"]


def test_historical_submissions_are_considered():
    cik = "0000320193"
    base = f"https://data.sec.gov/submissions/CIK{cik}.json"
    name = "CIK0000320193-submissions-001.json"
    row = {"form": "10-K", "accessionNumber": "0000320193-20-000001", "acceptanceDateTime": "2020-11-01T18:00:00", "filingDate": "2020-11-01", "reportDate": "2020-09-26"}
    client = SyntheticClient({base: {"cik": 320193, "filings": {"recent": _columns([]), "files": [{"name": name}]}},
                              f"https://data.sec.gov/submissions/{name}": _columns([row])})
    selected, amendments, sources = discover_filing(client, cik, date(2020, 12, 31))
    assert selected == row and amendments == [] and len(sources) == 2


def test_utc_sec_acceptance_is_compared_in_eastern_calendar_date():
    assert _acceptance("2026-01-01T04:59:00.000Z").date() == date(2025, 12, 31)
    assert _acceptance("2026-01-01T05:01:00.000Z").date() == date(2026, 1, 1)


def test_raw_header_controls_final_identity_and_acceptance_date():
    accession = "0000320193-25-000001"
    raw = (f"<SEC-DOCUMENT>{accession}.txt\n<SEC-HEADER>\n<ACCEPTANCE-DATETIME>20251231235900\n"
           f"ACCESSION NUMBER: {accession}\nCONFORMED SUBMISSION TYPE: 10-K\n"
           "FILED AS OF DATE: 20251231\n</SEC-HEADER>\n<DOCUMENT>").encode()
    parsed = raw_header(raw, accession)
    assert parsed["acceptance_datetime_eastern"].startswith("2025-12-31")
    with pytest.raises(SecError, match="accession or form"):
        raw_header(raw, "0000320193-25-000002")

