"""Synthetic software tests; these are not SEC filing observations."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.m7_acceptance_time_review import (
    compare, timezone_marker, validated_snapshot,
)


def _snapshot(tmp_path: Path, label: str, accepted: str):
    from scripts.m7_acceptance_time_review import ACCESSION, URL

    columns = {"accessionNumber": [ACCESSION], "form": ["10-Q"],
               "filingDate": ["2025-01-31"], "reportDate": ["2024-12-28"],
               "acceptanceDateTime": [accepted],
               "primaryDocument": ["synthetic.htm"]}
    body = json.dumps({"cik": 320193, "filings": {"recent": columns}}).encode()
    body_path, meta_path = tmp_path / f"{label}.bin", tmp_path / f"{label}.json"
    body_path.write_bytes(body)
    meta_path.write_text(json.dumps({"url": URL, "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(), "http_status": 200,
        "retrieved_utc": "2026-01-01T00:00:00Z"}), encoding="utf-8")
    return body_path, meta_path


def _raw() -> bytes:
    return (b"<SEC-DOCUMENT>\n<SEC-HEADER>\n"
            b"ACCESSION NUMBER: 0000320193-25-000008\n"
            b"CONFORMED SUBMISSION TYPE: 10-Q\n"
            b"<ACCEPTANCE-DATETIME>20250131060127\n"
            b"FILED AS OF DATE: 20250131\n"
            b"FILER:\n    CENTRAL INDEX KEY: 0000320193\n"
            b"</SEC-HEADER>\n<DOCUMENT><TYPE>10-Q\n<TEXT>synthetic</TEXT></DOCUMENT>")


def test_saved_string_difference_precedes_timezone_conversion(tmp_path):
    old = validated_snapshot(*_snapshot(tmp_path, "old", "2025-01-31T16:01:27.000Z"))
    new = validated_snapshot(*_snapshot(tmp_path, "new", "2025-01-31T11:01:27.000Z"))
    sec_path = Path(__file__).resolve().parents[1] / "src/finembed/sec.py"
    evidence, rows = compare(old, new, _raw(), sec_path, sec_path)
    assert [r["field"] for r in rows if r["literal_string_changed"]] == ["acceptanceDateTime"]
    assert evidence["converted_values"]["old_minus_new_seconds"] == 18000
    assert evidence["converted_values"]["new_api_equals_raw_header_instant"] is True
    assert evidence["difference_classification"]["cause_of_saved_response_change"] == "UNRESOLVED"
    assert evidence["difference_classification"]["website_first_public_time"] == "UNVERIFIED"


def test_saved_response_hash_failure_is_explicit(tmp_path):
    body, meta = _snapshot(tmp_path, "case", "2025-01-31T11:01:27.000Z")
    body.write_bytes(body.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="integrity"):
        validated_snapshot(body, meta)


def test_timezone_marker_distinguishes_z_offset_and_naive():
    assert timezone_marker("2025-01-31T11:01:27.000Z", "acceptanceDateTime") == "Z_UTC"
    assert timezone_marker("2025-01-31T06:01:27-05:00", "acceptanceDateTime") == "EXPLICIT_NUMERIC_OFFSET"
    assert timezone_marker("20250131060127", "acceptanceDateTime") == "NONE"
