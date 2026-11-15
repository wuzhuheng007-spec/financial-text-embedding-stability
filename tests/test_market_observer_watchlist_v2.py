"""Focused synthetic contract checks for the versioned observer scope and page."""

from __future__ import annotations

import copy
import sqlite3
from pathlib import Path

import pytest

from scripts.market_observer_v1.dashboard import render_dashboard
from scripts.market_observer_v1.store import Store
from scripts.market_observer_v1.watchlist import (
    candidate_entity_for_news_alias,
    event_entity_projection,
    load_watchlist,
    requested_us_subscription_order,
    validate_watchlist,
)


def test_current_scope_has_user_order_and_distinct_entity_security_index_ids() -> None:
    scope = load_watchlist()
    assert scope["display_order"] == ["priority_etfs", "benchmark", "magnificent_seven",
                                       "memory", "space", "related"]
    assert len(scope["entities"]) == 20
    assert len(scope["verified_current_mappings"]) == len(scope["securities"]) == 21
    assert set(scope["entities"]).isdisjoint(scope["securities"])
    assert set(scope["indices"]).isdisjoint(scope["entities"])
    assert requested_us_subscription_order(scope) == [
        "TQQQ", "SOXX", "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN",
        "GOOGL", "META", "TSLA", "SNDK", "MU", "SKHY", "SPCX",
        "AMD", "INTC", "CRWV", "AVGO",
    ]
    assert "GOOG" not in requested_us_subscription_order(scope)
    assert "000660" not in requested_us_subscription_order(scope)


def test_current_mapping_is_receipt_bound_and_does_not_backfill_history() -> None:
    scope = load_watchlist()
    assert scope["mapping_history_policy"].endswith("DOES_NOT_BACKFILL_HISTORICAL_IDENTITY")
    for security_id, code in (("SEC:SPCX:US", "US.SPCX"),
                              ("SEC:SKHY:US_CANDIDATE", "US.SKHY"),
                              ("SEC:000660:KR_CANDIDATE", "KR.000660")):
        mapping = scope["verified_current_mappings"][security_id]
        assert mapping["provider_code"] == code
        assert mapping["identity_scope"] == "CURRENT_ONLY"
        assert mapping["live_quote_entitlement"] == "NOT_TESTED"
        assert scope["mapping_evidence"][mapping["evidence_id"]]["public_receipt"]
    assert scope["entities"]["COMPANY:SPACEX"]["security_ids"] == ["SEC:SPCX:US"]
    assert scope["entities"]["COMPANY:SK_HYNIX"]["cross_market_price_policy"] == \
        "SEPARATE_SERIES_NO_CONCATENATION"
    assert scope["securities"]["SEC:SKHY:US_CANDIDATE"]["type_candidate"] == \
        "ADR_ISSUER_CROSSCHECKED"
    assert scope["securities"]["SEC:000660:KR_CANDIDATE"]["type_candidate"] == \
        "ORDINARY_SHARE_ISSUER_CROSSCHECKED"
    tampered = copy.deepcopy(scope)
    tampered["verified_current_mappings"]["SEC:SPCX:US"]["provider_code"] = "US.TSLA"
    with pytest.raises(ValueError, match="code/market conflict|current source mappings"):
        validate_watchlist(tampered)


def test_one_alphabet_news_event_keeps_separate_share_classes_and_spacex_distinct() -> None:
    scope = load_watchlist()
    assert candidate_entity_for_news_alias(" goog ", scope) == "COMPANY:ALPHABET"
    assert candidate_entity_for_news_alias("GOOGL", scope) == "COMPANY:ALPHABET"
    assert candidate_entity_for_news_alias("TSLA", scope) != \
        candidate_entity_for_news_alias("SPCX", scope)
    alphabet = event_entity_projection("source-event-1", ["GOOG", "GOOGL", "GOOG"], scope)
    assert alphabet["candidate_entity_ids"] == ["COMPANY:ALPHABET"]
    assert alphabet["candidate_security_ids_by_entity"]["COMPANY:ALPHABET"] == \
        ["SEC:GOOGL:US", "SEC:GOOG:US"]
    hynix = event_entity_projection("source-event-2", ["SKHY", "000660"], scope)
    assert hynix["candidate_entity_ids"] == ["COMPANY:SK_HYNIX"]
    assert len(hynix["candidate_security_ids_by_entity"]["COMPANY:SK_HYNIX"]) == 2


def test_event_candidate_links_are_append_only_and_do_not_duplicate_source_event(tmp_path: Path) -> None:
    scope = load_watchlist()
    projection = event_entity_projection("source-event-1", ["GOOG", "GOOGL"], scope)
    with Store(tmp_path / "observer.sqlite") as store:
        store.connection.execute("""INSERT INTO event_clusters
            (event_id,cluster_key,created_at,updated_at,status)
            VALUES('source-event-1','synthetic','2026-11-15T00:00:00Z',
                   '2026-11-15T00:00:00Z','UNVERIFIED')""")
        store.connection.commit()
        first = store.link_event_watchlist_candidates(projection, evidence_hash="a" * 64)
        again = store.link_event_watchlist_candidates(projection, evidence_hash="a" * 64)
        assert first == {"entity_links_added": 1, "security_links_added": 2}
        assert again == {"entity_links_added": 0, "security_links_added": 0}
        assert store.connection.execute("SELECT COUNT(*) FROM event_clusters").fetchone()[0] == 1
        assert store.connection.execute("SELECT COUNT(*) FROM source_claims").fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store.connection.execute("DELETE FROM event_entity_links")


def test_grouped_dashboard_keeps_missing_values_unknown_and_live_receipt_separate() -> None:
    scope = load_watchlist()
    snapshot = {"service": {"state": "CONNECTED_MARKET_CLOSED", "provider_state": "CONNECTED"},
                "instruments": [{"symbol": "TQQQ", "provider_code": "US.TQQQ",
                                 "resolution_status": "VERIFIED_BY_OFFICIAL_BASICINFO"}],
                "quotes": [{"instrument_id": "US.TQQQ", "close": 42.1,
                            "provider_time": "2026-11-13T21:00:00Z",
                            "received_at": "2026-11-15T04:00:00Z",
                            "market_session": "CLOSED", "delay_status": "UNKNOWN"}]}
    live = {"connection_state": "CONNECTED_MARKET_CLOSED",
            "observed_at_utc": "2026-11-15T04:30:00Z",
            "market_state_codes": {"US": 11},
            "enabled_subscriptions": [{"provider_code": "US.TQQQ", "kind": "QUOTE"}],
            "skipped_subscriptions": [{"provider_code": "US.SPCX", "reason": "NO_ENTITLEMENT"}]}
    page = render_dashboard(snapshot, watchlist=scope, live_status=live)
    assert page.count("class='watch-card'") == 20
    assert page.index("data-group='priority_etfs'") < page.index("data-group='benchmark'") < \
        page.index("data-group='magnificent_seven'") < page.index("data-group='memory'") < \
        page.index("data-group='space'") < page.index("data-group='related'")
    assert "observed price/bar: 42.1" in page
    assert "market not confirmed open" in page
    assert "Enabled in last connection receipt" in page
    assert "Last provider market signal: US AFTER_HOURS_END (11)" in page
    assert "Skipped: NO_ENTITLEMENT" in page
    assert "Data time: unknown" in page
    assert "No explicitly linked source report" in page
    assert "Daily target: 3×" in page
    assert "observed TQQQ prices" in page
    assert "No dated official holdings weights are loaded" in page
    assert "missing consensus stays unknown" in page


def test_dashboard_needs_explicit_event_link_and_escapes_news_and_skip_reasons() -> None:
    scope = load_watchlist()
    news = {"id": 7, "title": "<script>bad</script>", "source_url": "https://example.org/a",
            "verification_status": "SINGLE_SOURCE", "first_received_at": "2026-11-15T04:00:00Z"}
    snapshot = {"news": [news], "event_entity_links": [],
                "event_news_links": [{"event_id": "event-7", "news_id": 7}]}
    page = render_dashboard(snapshot, watchlist=scope)
    card = page.split("data-entity='COMPANY:ALPHABET'", 1)[1].split("</article>", 1)[0]
    assert "<script>" not in page
    assert "&lt;script&gt;bad&lt;/script&gt;" not in card
    snapshot["event_entity_links"] = [{"event_id": "event-7", "entity_id": "COMPANY:ALPHABET"}]
    page = render_dashboard(snapshot, watchlist=scope,
                            live_status={"skipped_subscriptions": [{"provider_code": "US.GOOGL",
                                                                      "reason": "<script>deny</script>"}]})
    card = page.split("data-entity='COMPANY:ALPHABET'", 1)[1].split("</article>", 1)[0]
    assert "&lt;script&gt;bad&lt;/script&gt;" in card
    assert "&lt;script&gt;deny&lt;/script&gt;" in card
    assert "href=\"https://example.org/a\"" in card
    assert "<script>" not in page
