"""Synthetic software checks only; no fixture is a research filing."""
from __future__ import annotations

import hashlib
import json

import pytest

from finembed.qpilot import (_check_primary_filename, _copy_verified_cache, _primary_source,
                             boundary_candidates, select_earliest_original)
from finembed.sec import SecError


def _row(form, accession, filed, period="2024-12-28"):
    return {"form": form, "accessionNumber": accession, "filingDate": filed,
            "acceptanceDateTime": filed + "T12:00:00.000Z",
            "reportDate": period, "primaryDocument": "synthetic.htm"}


def test_selection_uses_earliest_2025_original_and_retains_amendments():
    a = _row("10-Q", "0000320193-25-000008", "2025-01-31")
    b = _row("10-Q", "0000320193-25-000009", "2025-01-31")
    c = _row("10-Q", "0000320193-25-000057", "2025-05-02")
    amend = _row("10-Q/A", "0000320193-25-000010", "2025-01-30")
    tied, ledger = select_earliest_original([c, amend, b, a,
        _row("10-Q", "0000320193-24-000001", "2024-01-01")])
    assert [r["accessionNumber"] for r in tied] == [a["accessionNumber"], b["accessionNumber"]]
    assert len(ledger) == 4 and any(r["form"] == "10-Q/A" for r in ledger)


def test_selection_rejects_missing_original_or_malformed_identity():
    with pytest.raises(SecError, match="No original"):
        select_earliest_original([_row("10-Q/A", "0000320193-25-000001", "2025-01-01")])
    with pytest.raises(ValueError, match="Invalid"):
        select_earliest_original([_row("10-Q", "not-an-accession", "2025-01-01")])


def _synthetic_primary(body_words=800, include_body=True, end=True):
    toc = ("TABLE OF CONTENTS\nPart I\nItem 1.\nFinancial Statements\n1\n"
           "Item 2.\nManagement’s Discussion and Analysis of Financial Condition and Results of Operations\n12\n"
           "Item 3. Quantitative and Qualitative Disclosures About Market Risk\n17\n"
           "Part II\nItem 1. Legal Proceedings\n")
    if not include_body:
        return toc
    body = ("\nPART I — FINANCIAL INFORMATION\nItem 1. Financial Statements\n" +
            "Synthetic financial text. " * 120 + "\n" +
            "Item 2. Management’s Discussion and Analysis of Financial Condition and Results of Operations\n" +
            "Synthetic MD&A content with a table description. " * body_words + "\n")
    return toc + body + ("Item 3. Quantitative and Qualitative Disclosures About Market Risk\n"
                         "Synthetic market risk text.\nPART II — OTHER INFORMATION\n" +
                         "Synthetic unrelated Part II disclosures. " * 2500 if end else "")


def test_boundary_keeps_toc_but_selects_unique_substantive_body():
    text = _synthetic_primary()
    found = boundary_candidates(text)
    assert found["status"] == "PROGRAM_BOUNDARY_PASS"
    assert found["selected_candidate_index"] == 1
    assert found["candidates"][0]["classification"] == "TOC_OR_SHORT_CANDIDATE"
    chosen = found["candidates"][1]
    assert chosen["classification"] == "PROGRAM_SUBSTANTIVE_BODY_CANDIDATE"
    assert text[chosen["start_char"]:chosen["end_char_exclusive"]].startswith("Item 2. Management")
    assert chosen["end_char_exclusive"] < found["candidates"][1]["following_part_ii_char"]


def test_boundary_never_substitutes_toc_or_unclosed_section():
    assert boundary_candidates(_synthetic_primary(include_body=False))["status"] == "NEEDS_REVIEW"
    assert boundary_candidates(_synthetic_primary(end=False))["status"] == "NEEDS_REVIEW"


def test_primary_source_bytes_and_offsets_are_exact():
    raw = (b"<SEC-DOCUMENT><DOCUMENT><TYPE>10-Q\n<FILENAME>synthetic.htm\n<TEXT><html>synthetic</html></TEXT>"
           b"</DOCUMENT></SEC-DOCUMENT>")
    html, metadata = _primary_source(raw)
    assert html == b"<html>synthetic</html>"
    assert metadata["filename_raw_header"] == "synthetic.htm"
    assert raw[metadata["raw_byte_start"]:metadata["raw_byte_end_exclusive"]] == html
    with pytest.raises(Exception, match="found 2"):
        _primary_source(raw.replace(b"</DOCUMENT>", b"</DOCUMENT><DOCUMENT><TYPE>10-Q\n<FILENAME>second.htm\n<TEXT>x</TEXT></DOCUMENT>"))


def test_primary_source_requires_safe_explicit_raw_filename():
    raw = b"<DOCUMENT><TYPE>10-Q\n<FILENAME>primary.htm\n<TEXT>x</TEXT></DOCUMENT>"
    with pytest.raises(Exception, match="no raw FILENAME"):
        _primary_source(raw.replace(b"<FILENAME>primary.htm\n", b""))
    with pytest.raises(Exception, match="filename is invalid"):
        _primary_source(raw.replace(b"primary.htm", b"../other.htm"))


def test_primary_filename_must_match_submissions_row():
    primary = {"filename_raw_header": "primary.htm"}
    assert _check_primary_filename(primary, {"primaryDocument": "primary.htm"}) == "primary.htm"
    with pytest.raises(SecError, match="differs"):
        _check_primary_filename(primary, {"primaryDocument": "different.htm"})
    with pytest.raises(SecError, match="differs"):
        _check_primary_filename(primary, {})


def test_cached_sec_response_is_hash_checked_before_copy(tmp_path):
    source, target = tmp_path / "old", tmp_path / "new"
    source.mkdir()
    url = "https://www.sec.gov/Archives/edgar/data/320193/000032019325000008/demo.txt"
    key = hashlib.sha256(url.encode()).hexdigest()
    body = b"synthetic software fixture"
    (source / f"{key}.bin").write_bytes(body)
    (source / f"{key}.json").write_text(json.dumps({"url": url,
        "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body),
        "retrieved_utc": "2025-01-01T00:00:00+00:00"}), encoding="utf-8")
    result = _copy_verified_cache(source, target, url)
    assert result["method"] == "COPIED_VERIFIED_CACHE"
    assert (target / f"{key}.bin").read_bytes() == body
    (source / f"{key}.bin").write_bytes(b"tampered")
    with pytest.raises(SecError, match="integrity"):
        _copy_verified_cache(source, tmp_path / "another", url)
