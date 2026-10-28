"""Synthetic software fixtures only; never evidence of an SEC download."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from finembed.sec import SecError
from scripts import m8_ingest_four_10q as ingest


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _snapshot(cik: str, accession: str, primary: str, *, acceptance: str) -> bytes:
    row = {"form": "10-Q", "accessionNumber": accession,
           "filingDate": "2025-01-29", "reportDate": "2024-12-31",
           "acceptanceDateTime": acceptance, "primaryDocument": primary}
    columns = {key: [value] for key, value in row.items()}
    return json.dumps({"cik": int(cik), "filings": {"recent": columns, "files": []}}).encode()


def _raw(cik: str, accession: str, primary: str) -> tuple[bytes, bytes]:
    html = ("<html><body><h1>Item 1. Financial Statements</h1><p>" +
            "Synthetic software text. " * 150 +
            "</p><h1>Item 2. Management's Discussion and Analysis</h1><p>" +
            "Synthetic software text. " * 150 + "</p>" +
            "<h1>Item 3. Market Risk</h1></body></html>").encode()
    header = (f"<SEC-DOCUMENT>\n<SEC-HEADER>\n<ACCEPTANCE-DATETIME>20250129161154\n"
              f"ACCESSION NUMBER: {accession}\nCONFORMED SUBMISSION TYPE: 10-Q\n"
              "CONFORMED PERIOD OF REPORT: 20241231\nFILED AS OF DATE: 20250129\n"
              f"FILER:\n  COMPANY DATA:\n    CENTRAL INDEX KEY: {cik}\n"
              "</SEC-HEADER>\n").encode()
    return header + b"<DOCUMENT><TYPE>10-Q\n<FILENAME>" + primary.encode() + b"\n<TEXT>" + html + b"</TEXT></DOCUMENT>\n", html


class FakeClient:
    def __init__(self, responses: dict[str, bytes]):
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url: str):
        self.calls.append(url)
        body = self.responses[url]
        return body, {"url": url, "sha256": _sha(body), "bytes": len(body),
                      "http_status": 200, "cache_hit": False,
                      "retrieved_utc": "2025-02-01T00:00:00+00:00"}


def _fixture(tmp_path: Path):
    ticker, cik, accession = ingest.FIXED[0]
    primary = "msft-20241231.htm"
    old_snapshot = _snapshot(cik, accession, primary,
                             acceptance="2025-01-29T21:11:54.000Z")
    new_snapshot = _snapshot(cik, accession, primary,
                             acceptance="2025-01-29T22:11:54.000Z")
    raw, html = _raw(cik, accession, primary)
    prior = tmp_path / "prior"
    prior.mkdir()
    submissions_path = prior / "submissions.json"
    primary_path = prior / primary
    submissions_path.write_bytes(old_snapshot)
    primary_path.write_bytes(html)
    submissions_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    primary_url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                   f"{accession.replace('-', '')}/{primary}")
    row = {"ticker": ticker, "cik": cik, "accession": accession,
           "form": "10-Q", "filing_date": "2025-01-29",
           "report_period_end": "2024-12-31", "primary_document": primary,
           "primary_document_url": primary_url,
           "submissions_acceptance_raw_string": "2025-01-29T21:11:54.000Z",
           "primary_html": {"path": str(primary_path), "sha256": _sha(html), "bytes": len(html)},
           "submissions_source_receipts": [{"url": submissions_url,
              "file": {"path": str(submissions_path), "sha256": _sha(old_snapshot),
                       "bytes": len(old_snapshot)}, "http_status": 200,
              "retrieved_utc": "2025-01-30T00:00:00+00:00"}]}
    raw_url = ingest.submission_url(cik, accession)
    index_url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                 f"{accession.replace('-', '')}/index.json")
    responses = {raw_url: raw, submissions_url: new_snapshot,
                 index_url: json.dumps({"directory": {"item": [{"name": primary}]}}).encode()}
    return row, prior, responses


def test_one_saves_two_submissions_versions_raw_index_and_primary(tmp_path):
    row, prior, responses = _fixture(tmp_path)
    client = FakeClient(responses)
    destination = tmp_path / "ingest" / "MSFT"
    destination.mkdir(parents=True)
    result = ingest._one(client, row, destination, prior,
                         *ingest.FIXED[0], "Microsoft Corporation")
    assert result["status"] == "VERIFIED_RAW_AND_PRIMARY"
    assert result["acquisition_status"] == "COMPLETE"
    assert result["original_submission_sha256"] == _sha(responses[ingest.submission_url(
        ingest.FIXED[0][1], ingest.FIXED[0][2])])
    assert result["raw_filer_cik"] == ingest.FIXED[0][1]
    assert result["raw_header"]["report_period_end_raw_header"] == "2024-12-31"
    assert result["current_submissions_comparison"]["acceptance_strings_equal"] is False
    assert result["human_review"] == "PENDING_HUMAN_REVIEW"
    assert (destination / "submissions_response.json").read_bytes() != (
        destination / "submissions_current_response.json").read_bytes()
    assert (destination / "filing_index_response.json").is_file()
    assert len(client.calls) == 3  # One raw, one fresh submissions, one filing index.


def test_tampered_saved_snapshot_fails_before_any_network(tmp_path):
    row, prior, responses = _fixture(tmp_path)
    (prior / "submissions.json").write_bytes(b"tampered")
    client = FakeClient(responses)
    destination = tmp_path / "ingest" / "MSFT"
    destination.mkdir(parents=True)
    result = ingest._one(client, row, destination, prior,
                         *ingest.FIXED[0], "Microsoft Corporation")
    assert result["status"] == "FAILED_SOURCE_VALIDATION"
    assert "hash/size" in result["reason"]
    assert client.calls == []


def test_fixed_accession_conflict_cannot_substitute_filing(tmp_path):
    row, prior, responses = _fixture(tmp_path)
    row["accession"] = "0000950170-25-999999"
    client = FakeClient(responses)
    destination = tmp_path / "ingest" / "MSFT"
    destination.mkdir(parents=True)
    result = ingest._one(client, row, destination, prior,
                         *ingest.FIXED[0], "Microsoft Corporation")
    assert result["status"] == "FAILED_SOURCE_VALIDATION"
    assert "user-fixed" in result["reason"]
    assert client.calls == []


def test_raw_filer_mismatch_retains_raw_and_fails_closed(tmp_path):
    row, prior, responses = _fixture(tmp_path)
    raw_url = ingest.submission_url(ingest.FIXED[0][1], ingest.FIXED[0][2])
    responses[raw_url] = responses[raw_url].replace(b"CENTRAL INDEX KEY: 0000789019",
                                                 b"CENTRAL INDEX KEY: 0000000001")
    client = FakeClient(responses)
    destination = tmp_path / "ingest" / "MSFT"
    destination.mkdir(parents=True)
    result = ingest._one(client, row, destination, prior,
                         *ingest.FIXED[0], "Microsoft Corporation")
    assert result["status"] == "FAILED_SOURCE_VALIDATION"
    assert "FILER CIK" in result["reason"]
    assert (destination / "original_submission.txt").is_file()
    assert not (destination / "primary_text.txt").exists()


def test_current_index_failure_keeps_core_source_with_explicit_partial(tmp_path):
    row, prior, responses = _fixture(tmp_path)
    index_url = next(url for url in responses if url.endswith("index.json"))
    responses[index_url] = b"{\"directory\": {\"item\": []}}"
    client = FakeClient(responses)
    destination = tmp_path / "ingest" / "MSFT"
    destination.mkdir(parents=True)
    result = ingest._one(client, row, destination, prior,
                         *ingest.FIXED[0], "Microsoft Corporation")
    assert result["status"] == "VERIFIED_RAW_AND_PRIMARY"
    assert result["acquisition_status"] == "PARTIAL_AUXILIARY"
    assert result["filing_index_status"] == "FAILED_OR_PRIMARY_NOT_LISTED"
    assert (destination / "filing_index_response.json").is_file()


def test_changed_primary_version_is_not_eligible_for_selected_extraction(tmp_path):
    row, prior, responses = _fixture(tmp_path)
    old = (prior / row["primary_document"]).read_bytes() + b"\n"
    (prior / row["primary_document"]).write_bytes(old)
    row["primary_html"].update({"sha256": _sha(old), "bytes": len(old)})
    client = FakeClient(responses)
    destination = tmp_path / "ingest" / "MSFT"
    destination.mkdir(parents=True)
    result = ingest._one(client, row, destination, prior,
                         *ingest.FIXED[0], "Microsoft Corporation")
    assert result["status"] == "NEEDS_VERSION_REVIEW"
    assert result["acquisition_status"] == "PARTIAL_VERSION_REVIEW"
    assert result["prior_primary_comparison"]["same_bytes"] is False
    assert (destination / "original_submission.txt").is_file()
    assert (destination / "primary_document_source.html").is_file()


def test_failed_index_request_does_not_invent_saved_path(tmp_path):
    row, prior, responses = _fixture(tmp_path)
    index_url = next(url for url in responses if url.endswith("index.json"))
    del responses[index_url]
    client = FakeClient(responses)
    destination = tmp_path / "ingest" / "MSFT"
    destination.mkdir(parents=True)
    result = ingest._one(client, row, destination, prior,
                         *ingest.FIXED[0], "Microsoft Corporation")
    assert result["status"] == "VERIFIED_RAW_AND_PRIMARY"
    assert result["acquisition_status"] == "PARTIAL_AUXILIARY"
    assert "filing_index_response" not in result["source_paths"]


def test_current_submissions_identity_conflict_is_preserved(tmp_path):
    row, prior, responses = _fixture(tmp_path)
    url = f"https://data.sec.gov/submissions/CIK{ingest.FIXED[0][1]}.json"
    current = json.loads(responses[url])
    current["filings"]["recent"]["reportDate"][0] = "2024-09-30"
    responses[url] = json.dumps(current).encode()
    client = FakeClient(responses)
    destination = tmp_path / "ingest" / "MSFT"
    destination.mkdir(parents=True)
    result = ingest._one(client, row, destination, prior,
                         *ingest.FIXED[0], "Microsoft Corporation")
    assert result["status"] == "VERIFIED_RAW_AND_PRIMARY"
    assert result["acquisition_status"] == "PARTIAL_AUXILIARY"
    assert result["current_submissions_status"] == "IDENTITY_CONFLICT_NEEDS_REVIEW"
    assert result["current_submissions_comparison"]["identity_conflicts"]["reportDate"] == {
        "prior": "2024-12-31", "current": "2024-09-30"}


def test_invalid_raw_report_period_fails_explicitly():
    raw, _ = _raw(*ingest.FIXED[0][1:], "msft-20241231.htm")
    with pytest.raises(SecError, match="report period"):
        ingest._raw_report_period(raw.replace(b"CONFORMED PERIOD OF REPORT: 20241231",
                                              b"CONFORMED PERIOD OF REPORT: 20250230"))
