from scripts.market_observer_v1.dashboard import render_dashboard


def test_empty_view_does_not_claim_no_news_or_realtime():
    page = render_dashboard()
    assert "NOT_CONNECTED" in page
    assert "No local news item" in page
    assert "not evidence that no news occurred" in page
    assert "RECENT_QUOTE" not in page


def test_source_text_escaped_and_unknown_not_zero():
    page = render_dashboard({
        "service": {"state": "RUNNING", "provider_connected": False},
        "news": [{"title": "<script>alert(1)</script>", "source_url": "javascript:alert(2)",
                  "verification_status": "SINGLE_SOURCE", "first_received_at": None}],
        "quotes": [{"instrument_id": "NVDA", "close": None, "market_session": "CLOSED",
                    "delay_status": "REAL_TIME", "provider_time": "2026-11-13T21:00:00Z"}],
    })
    assert "<script>" not in page
    assert "&lt;script&gt;" in page
    assert "javascript:alert" not in page
    assert "RUNNING_WITHOUT_PROVIDER_CONNECTION" in page
    assert "MARKET_NOT_CONFIRMED_OPEN" in page
    assert "<td>unknown</td>" in page
    assert "first local receipt: unknown" in page


def test_explicit_provider_timezone_and_local_display():
    page = render_dashboard({"service": {"state": "STOPPED", "heartbeat_at": "2026-11-15T03:00:00Z"}})
    assert "2026-11-15 11:00:00 CST" in page
    assert "raw provider timezone retained" in page


def test_probe_receipt_bound_is_not_displayed_as_exact_receipt():
    page = render_dashboard({"quotes": [{
        "instrument_id": "US.NVDA", "close": None,
        "provider_time": "2026-11-13T21:00:00Z",
        "received_at": "2026-11-15T04:03:16Z",
        "receipt_semantics": "BOUNDED_INTERVAL_NOT_EXACT",
        "market_session": "UNKNOWN", "delay_status": "UNKNOWN",
    }]})
    assert "Receipt time / upper bound" in page
    assert "BOUNDED_INTERVAL_NOT_EXACT" in page
    assert "DELAYED_OR_UNKNOWN" not in page
    assert "MARKET_NOT_CONFIRMED_OPEN" in page
