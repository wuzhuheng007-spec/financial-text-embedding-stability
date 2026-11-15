"""Focused tests for same-basis growth and untrusted text display."""

from scripts.derive_earnings_growth import derive
from scripts.build_earnings_dashboard import render


def _metric(name, value):
    return {
        "row_id": f"issuer:{name}:current", "metric": name, "raw_value": value,
        "normalized_value": value, "period_basis": "QUARTER", "scope": "CONSOLIDATED",
        "accounting_standard": "US_GAAP", "adjustment_basis": "GAAP",
        "currency": "USD", "fiscal_year": 2026, "fiscal_quarter": 3,
        "source_url": "https://issuer.example/release", "source_sha256": "a" * 64,
    }


def _prior(name, period, value, accounting="US_GAAP"):
    return {
        "metric": name, "period": period, "raw_value": value, "scale": 1,
        "period_basis": "QUARTER", "scope": "CONSOLIDATED",
        "accounting_standard": accounting, "adjustment_basis": "GAAP",
        "currency": "USD", "source_url": "https://issuer.example/release",
        "source_sha256": "a" * 64,
    }


def test_growth_computes_matching_quarters_and_rejects_nonpositive_base():
    card = {
        "issuer": "ISSUER", "metrics": [_metric("revenue", 120), _metric("operating_income", 20),
                                         _metric("net_income", 5)],
        "comparison": [_prior("revenue", "2025-Q3", 100), _prior("revenue", "2026-Q2", 80),
                       _prior("operating_income", "2025-Q3", 0),
                       _prior("net_income", "2025-Q3", 4, accounting="NON_GAAP")],
    }
    sources = {"sources": [{"issuer_id": "ISSUER", "url": "https://issuer.example/release",
                            "sha256": "a" * 64, "captured_local_bytes_verified": True}]}
    rows = derive({"cards": [card]}, sources)
    indexed = {(r["metric"], r["relation"]): r for r in rows}
    assert indexed["revenue", "YOY"]["growth_percent"] == "20.0"
    assert indexed["revenue", "QOQ"]["growth_percent"] == "50.0"
    assert indexed["operating_income", "YOY"]["status"] == "NONPOSITIVE_BASE"
    assert indexed["net_income", "YOY"]["status"] == "BASIS_MISMATCH"


def test_dashboard_escapes_news_and_keeps_financial_boundary():
    page = render({"cards": [], "as_of_utc": "2026-11-14T23:47:54Z"},
                  {"cards": []}, {"events": [{"title": "<script>alert(1)</script>",
                      "issuer_id": "ISSUER", "event_date": "2026-11-10",
                      "timestamp_precision": "DATE_ONLY", "facts": "<b>claim</b>",
                      "source_url": "https://issuer.example/news", "source_locator": "line 1"}]}, [], [])
    assert "<script>" not in page and "&lt;script&gt;" in page
    assert "financial evaluation NOT_RUN" in page
