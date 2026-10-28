"""Software-only tests for a local 10-Q human review packet; no research data."""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

from scripts.m7_build_10q_review import (
    FilingHTML, make_form, safe_preview, source_rendered, table_record,
)


def test_filing_parser_records_distinct_html_and_table_spans():
    source = ("<html><body><div>Item 2. Management’s Discussion and Analysis"
              "<table><tr><th colspan='2'>2025</th></tr><tr><td>A</td><td>(2)%</td></tr></table>"
              "</div><div>Item 3. Quantitative and Qualitative Disclosures About Market Risk</div>"
              "</body></html>")
    parser = FilingHTML(source)
    parser.feed(source)
    parser.close()
    assert len(parser.blocks) == 2 and len(parser.tables) == 1
    table = parser.tables[0]
    assert source[table["start_char_in_html"]:table["end_char_exclusive_in_html"]].startswith("<table>")
    assert table["rows"][0][0]["colspan"] == 2
    assert table["rows"][1][1]["text"] == "(2)%"
    assert parser.blocks[0]["text"].startswith("Item 2.")
    assert parser.blocks[1]["text"].startswith("Item 3.")


def test_source_preview_drops_active_content_and_source_attributes():
    bad = ('<div onclick="steal()"><script>bad_script()</script>'
           '<img src="https://tracker.example/pixel"><a href="https://tracker.example">Safe label</a>'
           '<table><tr><td colspan="2" onmouseover="steal()">$ (11)%</td></tr></table></div>')
    good = safe_preview(bad)
    assert "bad_script" not in good
    assert "tracker.example" not in good
    assert "onclick" not in good and "onmouseover" not in good
    assert "<img" not in good and "href=" not in good
    assert "Safe label" in good and '<td colspan="2">$ (11)%</td>' in good


def test_table_record_marks_flattened_cell_join_for_human_review():
    source = "<table><tr><td>Region</td><td>$2</td></tr></table>"
    parser = FilingHTML("<html><body><div>" + source + "</div></body></html>")
    parser.feed(parser.source)
    parser.close()
    flat = source_rendered(source)
    record = table_record(parser.source, flat, parser.tables[0], html_start_in_raw=50,
                          ordinal=1)
    assert record["table_id"] == "MDA-1"
    assert record["source_table_index"] == 1
    assert record["flattened_text"] == flat
    assert record["review_status"] == "NEEDS_HUMAN_TABLE_REVIEW"
    assert record["indicators"]["possible_flattened_cell_join_count"] >= 1
    assert record["complete_submission_byte_start"] == 50 + record["html_source_byte_start"]


def test_generated_form_starts_without_person_time_or_decision(tmp_path: Path):
    identity = {"company": "Synthetic Co.", "cik": "0000000001", "accession": "0000000001-25-000001",
                "form": "10-Q", "report_period_end": "2024-12-31", "filing_date": "2025-01-30",
                "raw_sha256": "a" * 64, "html_sha256": "b" * 64,
                "primary_text_sha256": "c" * 64, "section_sha256": "d" * 64}
    output = tmp_path / "review_form.html"
    make_form(output, identity, [{"table_id": "MDA-1"}])
    text = output.read_text(encoding="utf-8")
    assert "PENDING_HUMAN_REVIEW" in text
    assert "HUMAN_DECISION_RECORDED" in text  # only after the human fills all fields
    assert "<input name='reviewer' autocomplete='off'>" in text
    assert "<input name='review_time' type='datetime-local'>" in text
    assert "<option value='' selected>— 尚未决定 —</option>" in text
    assert "Synthetic Co." in text


def test_safe_preview_escapes_markup_in_source_text():
    result = safe_preview("<div>5 &lt; 7 &amp; x</div>")
    assert "5 &lt; 7 &amp; x" in result
    assert "<script" not in result
