"""Synthetic software tests for the metadata-first 10-Q candidate adapter."""
import hashlib
import json

import pytest

from scripts.m7_discover_10q_candidates import (
    _history_needed, _prior_report, _seed_verified_cache, survey_primary_html,
)


def test_primary_survey_requires_item2_header_and_marks_xbrl_offset_units():
    html = ("<html><head>Item 2. Management's Discussion and Analysis</head>"
            "<body><ix:nonNumeric name='dei:DocumentFiscalPeriodFocus'>Q2</ix:nonNumeric>"
            "<ix:nonNumeric name='dei:DocumentFiscalYearFocus'>2025</ix:nonNumeric>"
            "<xbrli:context id='SyntheticContext'><xbrli:period>"
            "<xbrli:startDate>2024-10-01</xbrli:startDate>"
            "<xbrli:endDate>2024-12-31</xbrli:endDate>"
            "</xbrli:period></xbrli:context>"
            "<p>Item 2. Management's Discussion and Analysis</p></body></html>")
    result = survey_primary_html(html.encode(), "2024-12-31", "2024-09-30")
    assert result["mda_title_occurrences_in_visible_primary"] == 1
    assert result["item2_mda_heading_pair_occurrences_in_parser_text"] == 1
    assert result["mda_title_presence_status"].endswith("BOUNDARY_UNREVIEWED")
    assert result["fiscal_period_focus"] == "Q2"
    assert result["coverage_start_proxy"] == "2024-10-01"
    assert result["xbrl_exact_start_end_duration_context_found"] is True
    evidence = result["matching_xbrl_duration_contexts_first_5"][0]
    assert evidence["context_id_candidate"] == "SyntheticContext"
    exact_span = html[evidence["html_decoded_char_start"]:evidence["html_decoded_char_end_exclusive"]]
    assert hashlib.sha256(exact_span.encode()).hexdigest() == evidence["period_substring_sha256"]
    assert "UTF-8-decoded saved primary HTML" in result["xbrl_offset_units"]


def test_primary_survey_does_not_manufacture_quarter_start_or_body():
    html = b"<html><body>Item 2. Management's Discussion and Analysis</body></html>"
    result = survey_primary_html(html, "2025-03-31", None)
    assert result["coverage_start_proxy"] is None
    assert result["xbrl_exact_start_end_duration_context_found"] is None
    assert result["mda_title_presence_status"].endswith("BOUNDARY_UNREVIEWED")
    no_title = survey_primary_html(b"<html><body>Item 2 Other</body></html>",
                                   "2025-03-31", "2024-12-31")
    assert no_title["mda_title_presence_status"] == "TITLE_NOT_FOUND_NEEDS_REVIEW"
    assert no_title["xbrl_exact_start_end_duration_context_found"] is False


def test_history_required_only_if_recent_does_not_cover_start_of_2025():
    main = {"filings": {"recent": {"filingDate": ["2024-12-31", "2025-05-01"]},
                        "files": []}}
    assert _history_needed(main) == []
    main["filings"]["recent"]["filingDate"] = ["2025-05-01"]
    main["filings"]["files"] = [{"name": "CIK0000320193-submissions-001.json",
                                  "filingFrom": "2024-12-01", "filingTo": "2025-04-30"}]
    assert len(_history_needed(main)) == 1
    main["filings"]["files"] = []
    with pytest.raises(ValueError, match="2025 date range absent"):
        _history_needed(main)


def test_previous_reporting_period_uses_original_sec_row_only():
    rows = [{"form": "10-K", "reportDate": "2024-06-30", "filingDate": "2024-08-01"},
            {"form": "10-Q/A", "reportDate": "2024-09-30", "filingDate": "2024-12-01"},
            {"form": "10-Q", "reportDate": "2024-09-30", "filingDate": "2024-11-01"}]
    assert _prior_report(rows, "2024-12-31")["reportDate"] == "2024-09-30"


def test_prior_cache_is_verified_before_reuse(tmp_path):
    source, target = tmp_path / "old", tmp_path / "new"
    source.mkdir()
    url = "https://data.sec.gov/submissions/CIK0000320193.json"
    key = hashlib.sha256(url.encode()).hexdigest()
    body = b'{"synthetic":true}'
    (source / f"{key}.bin").write_bytes(body)
    (source / f"{key}.json").write_text(json.dumps({"url": url,
        "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body),
        "retrieved_utc": "2026-01-01T00:00:00Z"}), encoding="utf-8")
    assert len(_seed_verified_cache(source, target)) == 1
    assert (target / f"{key}.bin").read_bytes() == body
    (source / f"{key}.bin").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="failed integrity"):
        _seed_verified_cache(source, tmp_path / "fail")
