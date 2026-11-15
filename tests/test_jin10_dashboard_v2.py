"""Static/local display boundaries; provider text is synthetic."""

from scripts.build_jin10_dashboard_v2 import render_v2


def test_template_marks_private_feed_omitted_and_guidance_inference():
    html = render_v2()
    assert "PUBLIC_TEMPLATE_PRIVATE_FEED_OMITTED" in html
    assert "INFERRED_FROM_OFFICIAL_FISCAL_CALENDAR_NOT_GUIDANCE_RELEASE" in html
    assert "2026-11-14T23:47:54.6036273Z" in html
    assert "No related flash candidates" in html
    assert "financial evaluation NOT_RUN" in html


def test_private_provider_text_is_escaped_and_acquisition_failure_visible():
    feed = {
        "records": [{"tool_name": "list_flash", "title": "<script>alert(1)</script>",
                     "content": "<b>source report</b>", "issuer_candidates": ["AMD"],
                     "source_url": "https://example.org/original", "version_number": 1}],
        "acquisition_status": [{"stream_key": "flash", "coverage_status": "INCOMPLETE_ERROR",
                                "last_error": "API unavailable"}],
        "raw_provider_text_included": True,
    }
    html = render_v2(feed)
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<b>source report</b>" not in html and "&lt;b&gt;source report&lt;/b&gt;" in html
    assert "INCOMPLETE_ERROR" in html and "API unavailable" in html
    assert "ACQUISITION_INCOMPLETE_OR_ERROR" not in html
    assert "LOCAL_CAPTURE_WITH_COVERAGE_GAP" in html
    assert "Jin10 SOURCE_REPORT" in html


def test_empty_failed_collection_is_not_called_empty_news():
    html = render_v2({"records": [], "acquisition_status": [
        {"stream_key": "flash", "coverage_status": "AUTH_DENIED_STOP",
         "last_error": "HTTP 401"}], "raw_provider_text_included": False})
    assert "ACQUISITION_INCOMPLETE_OR_ERROR" in html
    assert "EMPTY_FOR_REQUESTED_RANGE_ONLY" not in html
