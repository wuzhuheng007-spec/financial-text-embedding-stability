"""Clearly synthetic SGML/HTML fixtures; no claims about a real filing."""
import pytest

from finembed.extract import primary_text, extract_item1, comparison, ExtractionError


def _synthetic_submission(body: str) -> bytes:
    return ("<SEC-DOCUMENT>SYNTHETIC\n<DOCUMENT><TYPE>10-K\n<TEXT><html><body>" + body +
            "</body></html></TEXT></DOCUMENT></SEC-DOCUMENT>").encode()


def test_toc_is_skipped_and_boundary_is_exact():
    intro = "<p>Cover page " + "cover " * 400 + "</p>"
    toc = "<p>TABLE OF CONTENTS</p><p>Item 1. Business</p><p>Item 1A. Risk Factors</p>"
    body = "<h2>Item 1. Business</h2><p>" + ("Synthetic business description. " * 120) + "</p>"
    next_section = "<h2>Item 1A. Risk Factors</h2><p>" + ("Synthetic risk text. " * 100) + "</p>"
    text, _ = primary_text(_synthetic_submission(intro + toc + body + next_section))
    item, offsets = extract_item1(text)
    assert item.startswith("Item 1. Business")
    assert "Synthetic business description" in item
    assert "Synthetic risk text" not in item
    assert offsets["start_heading_count"] == 2
    assert comparison(text, item, offsets)["exact_substring"]


def test_missing_boundary_fails_without_full_text_fallback():
    text = "Cover\n" + "cover " * 500 + "\nItem 1. Business\n" + "text " * 500
    with pytest.raises(ExtractionError, match="following section boundary"):
        extract_item1(text)


def test_duplicate_substantive_sections_fail():
    chunk = "Item 1. Business\n" + "Synthetic business sentence. " * 100 + "\nItem 1A. Risk Factors\n"
    with pytest.raises(ExtractionError, match="Multiple substantive"):
        extract_item1(chunk + "other\n" + chunk)


def test_missing_primary_document_fails():
    with pytest.raises(ExtractionError, match="Expected one primary"):
        primary_text(b"<DOCUMENT><TYPE>EX-1<TEXT>synthetic</TEXT></DOCUMENT>")

