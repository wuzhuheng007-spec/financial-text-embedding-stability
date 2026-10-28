"""Synthetic software checks; these strings are never research filings."""
from __future__ import annotations

import hashlib
import json
import pytest

from finembed.m8_tenq import boundary_candidates_generic, fiscal_focus, period_hints
from scripts.m8_extract_10q import _period_ledger, _table_index, extract_saved_10q, heading_anchors
from scripts.m8_check_extraction import source_table_spans, visible_item_prefix
from scripts import m8_extract_10q as m8
from scripts.m7_build_10q_review import source_rendered


def _synthetic_section() -> str:
    return ("Item 2.Management's Discussion and Analysis of Financial Condition and Results of Operations\n"
            + "Synthetic quarterly financial discussion. " * 800 + "\n")


def _synthetic_document(*, duplicate_body: bool = False) -> str:
    toc = ("CONTENTS\nItem 2.Management's Discussion and Analysis of Financial Condition and Results of Operations\n"
           "23\nItem 3.Quantitative and Qualitative Disclosures About Market Risk\n24\n")
    front = "Synthetic statements. " * 800 + "\n"
    body = _synthetic_section()
    end = "Item 3.Quantitative and Qualitative Disclosures About Market Risk\n"
    extra = body + end if duplicate_body else ""
    tail = "Synthetic unrelated disclosures. " * 2200
    return toc + front + body + end + extra + tail


def test_no_space_after_item_number_dot_and_no_part_markers_still_retains_toc():
    doc = _synthetic_document()
    found = boundary_candidates_generic(doc)
    assert found["status"] == "PROGRAM_BOUNDARY_PASS"
    assert found["item2_heading_count"] == found["item3_heading_count"] == 2
    assert found["selected_candidate_index"] == 1
    assert found["candidates"][0]["classification"] == "TOC_OR_SHORT_CANDIDATE"
    span = found["selected_span"]
    assert doc[span["start_char"]:span["end_char_exclusive"]] == _synthetic_section()
    assert span["section_sha256"] == hashlib.sha256(_synthetic_section().encode()).hexdigest()
    assert found["part_i_standalone_heading_count"] == 0


def test_multiple_substantive_bodies_fail_closed():
    found = boundary_candidates_generic(_synthetic_document(duplicate_body=True))
    assert found["status"] == "NEEDS_REVIEW"
    assert found["selected_candidate_index"] is None
    assert found["selected_span"] is None
    assert found["substantive_candidate_count"] == 2


def test_html_heading_anchors_keep_html_and_complete_submission_byte_units_separate():
    html = ("<html><body><div>Item 2.</div><div>Management's Discussion and Analysis</div>"
            "<div>Item 3.</div><div>Quantitative and Qualitative Disclosures</div></body></html>")
    found = heading_anchors(html, 245)
    assert len(found["item2_candidates"]) == len(found["item3_candidates"]) == 1
    a = found["item2_candidates"][0]
    assert html[a["html_source_char_start"]:].startswith("Item 2.")
    assert a["complete_submission_byte_start"] == 245 + a["html_source_byte_start"]
    assert found["selected_item2"] is None


def test_period_hints_retain_multiple_durations_and_fiscal_focus():
    text = ("Three months ended March 31, 2025 and March 31, 2024. "
            "Six months ended March 31, 2025. Rolling twelve months ended March 31, 2025. "
            "As of March 31, 2025, amounts are in millions of dollars.")
    found = period_hints(text, offset=90)
    assert len(found) == 4
    assert all(90 <= row["source_start_char"] < row["source_end_char_exclusive"] <= 90 + len(text)
               for row in found)
    assert any("2025" in " ".join(row["date_mentions_in_excerpt"]) for row in found)
    html = ('<ix:nonNumeric name="dei:DocumentFiscalYearFocus">2025</ix:nonNumeric>'
            '<ix:nonNumeric name="dei:DocumentFiscalPeriodFocus">Q2</ix:nonNumeric>')
    assert fiscal_focus(html) == ("2025", "Q2")


def test_literal_html_table_preserves_source_span_and_flattened_period_offset():
    html = ("<html><body><div>Item 2. Management's Discussion and Analysis</div>"
            "<div>Three months ended March 31, 2025 (in millions):</div>"
            "<div><table><tr><td>Period</td><td>2025</td></tr>"
            "<tr><td>Revenue<sup>1</sup></td><td>$12</td></tr></table></div>"
            "<div>(1) Rounded values may differ from sums.</div>"
            "<div>Item 3. Quantitative and Qualitative Disclosures</div></body></html>")
    section = source_rendered(html)
    result = _table_index(html, html.encode(), section, 0, 0, len(html),
                          {"ticker": "TEST", "cik": "0000000000", "accession": "synthetic"})
    assert result["source_html_table_count"] == len(result["tables"]) == 1
    table = result["tables"][0]
    a, b = table["complete_submission_byte_start"], table["complete_submission_byte_end_exclusive"]
    assert hashlib.sha256(html.encode()[a:b]).hexdigest() == table["raw_html_fragment_sha256"]
    c, d = table["flattened_section_char_start"], table["flattened_section_char_end_exclusive"]
    assert section[c:d] == table["flattened_text"]
    assert table["footnote_hints"][0]["text"] == "1"
    assert "in millions" in [hint.lower() for hint in table["unit_evidence"]["explicit_scale_or_percent_phrases"]]
    assert table["unit_evidence"]["resolved_unit"] is None
    assert table["caption_candidate"]["status"] == "BEST_EFFORT_CONTEXT_NOT_CONFIRMED_TITLE"
    assert table["source_caption_context"]["complete_submission_byte_span"][1] == a
    assert table["footnote_hints"][0]["complete_submission_byte_span"][0] >= a
    assert len(table["adjacent_text_note_candidates"]) == 1
    adjacent = table["adjacent_text_note_candidates"][0]
    nx, ny = adjacent["normalized_section_char_span"]
    assert section[nx:ny] == adjacent["text"]
    assert adjacent["text"].startswith("(1) Rounded")
    assert table["footnote_evidence"]["adjacent_text_note_count"] == 1
    assert table["following_source_context"]["complete_submission_byte_span"][0] == b
    ledger = _period_ledger(section, html, {"report_period_end": "2025-03-31", "filing_date": "2025-04-01"},
                            result["tables"])
    assert ledger["table_periods"][0]["unit_evidence"] == table["unit_evidence"]
    assert ledger["table_periods"][0]["caption_candidate"] == table["caption_candidate"]
    assert ledger["table_periods"][0]["adjacent_text_note_candidates"] == table["adjacent_text_note_candidates"]
    assert ledger["table_periods"][0]["status"] == "NEEDS_HUMAN_PERIOD_REVIEW"


def test_direct_body_literal_table_is_indexed_without_direct_body_div():
    html = ("<html><body><p>Item 2. Management's Discussion and Analysis</p>"
            "<p>SUMMARY RESULTS OF OPERATIONS</p>"
            "<table><tr><th>In millions</th><th>Three Months Ended</th>"
            "<th>Six Months Ended</th></tr>"
            "<tr><td>Revenue</td><td>69,632</td><td>136,764</td></tr></table>"
            "<p>Item 3. Quantitative and Qualitative Disclosures</p></body></html>")
    section = source_rendered(html)
    start, end = html.index("Item 2."), html.index("Item 3.")
    result = _table_index(html, html.encode(), section, 0, start, end,
                          {"ticker": "TEST", "cik": "0000000000", "accession": "synthetic"})
    assert result["source_html_table_count"] == len(result["tables"]) == 1
    assert "Three Months Ended" in result["tables"][0]["flattened_text"]
    assert result["tables"][0]["review_status"] == "NEEDS_HUMAN_TABLE_REVIEW"


def test_heading_containing_layout_tables_are_preserved_but_not_data_tables():
    html = ("<html><body>"
            "<table><tr><td>Item 2. Management's Discussion and Analysis</td></tr></table>"
            "<table><tr><td>Revenue</td><td>12</td></tr></table>"
            "<table><tr><td>Item 3. Quantitative and Qualitative Disclosures</td></tr></table>"
            "</body></html>")
    start, end = html.index("Item 2."), html.index("Item 3.")
    result = _table_index(html, html.encode(), source_rendered(html), 0, start, end,
                          {"ticker": "TEST", "cik": "0000000000", "accession": "synthetic"})
    assert result["source_html_table_count"] == len(result["tables"]) == 1
    assert [row["roles"] for row in result["boundary_layout_tables"]] == [
        ["CONTAINS_ITEM2_HEADING"], ["CONTAINS_ITEM3_HEADING"]]
    assert all(row["review_status"] == "BOUNDARY_LAYOUT_TABLE_NEEDS_HUMAN_REVIEW"
               for row in result["boundary_layout_tables"])


def test_self_check_handles_source_entities_and_independent_table_tag_scan():
    html = "<body>Item&#160;2.<table><tr><td>12</td></tr></table>Item&#160;3.</body>"
    assert visible_item_prefix(html, html.index("Item"), 2)
    assert visible_item_prefix(html, html.rindex("Item"), 3)
    spans = source_table_spans(html)
    assert len(spans) == 1
    assert html[slice(*spans[0])].startswith("<table>")
    with pytest.raises(ValueError, match="Nested"):
        source_table_spans("<table><table></table></table>")


def test_new_raw_version_conflict_never_extracts_unreviewed_body(tmp_path):
    ingest, output = tmp_path / "ingest", tmp_path / "extraction"
    ingest.mkdir()
    (ingest / "source_identity.json").write_text(json.dumps({
        "ticker": "MSFT", "cik": "0000789019", "accession": "0000950170-25-010491",
        "status": "NEEDS_VERSION_REVIEW"}), encoding="utf-8")
    result = extract_saved_10q(ingest, output)
    assert result["status"] == "NEEDS_REVIEW"
    assert not (output / "item2_candidate.txt").exists()
    assert json.loads((output / "extraction_receipt.json").read_text())["human_review"] == "PENDING_HUMAN_REVIEW"


def test_malformed_saved_raw_fails_with_receipt_and_no_section(tmp_path):
    ingest, output = tmp_path / "ingest", tmp_path / "extraction"
    ingest.mkdir()
    raw = b"synthetic incomplete SEC software fixture"
    (ingest / "original_submission.txt").write_bytes(raw)
    (ingest / "primary_document_source.html").write_bytes(b"")
    (ingest / "primary_text.txt").write_bytes(b"")
    (ingest / "source_identity.json").write_text(json.dumps({
        "ticker": "MSFT", "cik": "0000789019", "accession": "0000950170-25-010491",
        "status": "VERIFIED_RAW_AND_PRIMARY", "form": "10-Q",
        "original_submission_sha256": hashlib.sha256(raw).hexdigest()}), encoding="utf-8")
    result = extract_saved_10q(ingest, output)
    assert result["status"] == "FAILED_INPUT_VALIDATION"
    assert "SecError" in result["reason"]
    assert not (output / "item2_candidate.txt").exists()
    assert (output / "extraction_receipt.json").is_file()


def test_version_adjudication_rechecks_exact_wrapper_and_one_script(tmp_path, monkeypatch):
    source_root = tmp_path / "prior"
    prior_path = source_root / "MSFT" / "msft-20241231.htm"
    prior_path.parent.mkdir(parents=True)
    script = b'<script src="/synthetic"></script>'
    prior = b"<html>synthetic body" + script + b"</html>"
    prior_path.write_bytes(prior)
    embedded = b"<XBRL>\n<html>synthetic body</html></XBRL>"
    raw = b"synthetic complete submission"
    monkeypatch.setattr(m8, "PRIOR_SOURCE_ROOT", source_root)
    identity = {"ticker": "MSFT", "prior_primary_comparison": {"prior_sha256": hashlib.sha256(prior).hexdigest()}}
    record = {"status": "PROCEED_WITH_NEW_RAW_SNAPSHOT_FOR_UNREVIEWED_PILOT",
              "ticker": "MSFT", "cik": "0000789019", "accession": "0000950170-25-010491",
              "new_raw_sha256": hashlib.sha256(raw).hexdigest(),
              "new_embedded_primary_sha256": hashlib.sha256(embedded).hexdigest(),
              "prior_standalone_primary_sha256": hashlib.sha256(prior).hexdigest(),
              "prior_standalone_path": str(prior_path),
              "equivalence_proof": {"status": "PASS_STRUCTURAL_EQUIVALENCE",
                                    "rule": "XBRL_WRAPPER_AND_ONE_STANDALONE_SCRIPT_ELEMENT",
                                    "script_sha256": hashlib.sha256(script).hexdigest(),
                                    "script_bytes": len(script),
                                    "exact_unwrapped_bytes_equal": True}}
    path = tmp_path / "version.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    result = m8._verify_version_adjudication(path, identity, raw, embedded)
    assert result["status"] == "STRUCTURAL_DIFFERENCE_RECHECKED_FOR_UNREVIEWED_EXTRACTION"
    assert result["script_origin_or_function"].startswith("UNVERIFIED")
    assert result["human_review"] == "PENDING_HUMAN_REVIEW"
    record["equivalence_proof"]["script_sha256"] = "0" * 64
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(m8.NeedsVersionReview, match="script element"):
        m8._verify_version_adjudication(path, identity, raw, embedded)
