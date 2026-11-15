"""Private market-observer dashboard from a read-only snapshot.

This renderer makes no provider calls. The public default contains no market
data or provider text; a private snapshot may contain licensed data and must
be written under ignored ``artifacts/`` only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from typing import Any
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")


def _s(value: Any) -> str:
    return "unknown" if value is None or value == "" else escape(str(value), quote=True)


def _time(value: Any) -> str:
    if value is None or value == "":
        return "unknown"
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return f"{_s(value)} · timezone unknown"
        return f"{_s(dt.astimezone(SHANGHAI).strftime('%Y-%m-%d %H:%M:%S'))} CST"
    except (TypeError, ValueError):
        return f"{_s(value)} · precision/zone unverified"


def _rows(items: Any, fields: tuple[tuple[str, str], ...], empty: str) -> str:
    if not isinstance(items, list) or not items:
        return f'<p class="empty">{escape(empty)}</p>'
    head = "".join(f"<th scope='col'>{escape(label)}</th>" for _, label in fields)
    body = "".join(
        "<tr>" + "".join(
            f"<td>{_time(row.get(key)) if key.endswith('_at') or key.endswith('_time') else _s(row.get(key))}</td>"
            for key, _ in fields
        ) + "</tr>" for row in items if isinstance(row, dict)
    )
    return f'<div class="table-scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _news(items: Any) -> str:
    if not isinstance(items, list) or not items:
        return '<p class="empty">No local news item in this view. Check collection status before interpreting the gap.</p>'
    cards = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = item.get("source_url") or item.get("original_url")
        link = (f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">Source link</a>'
                if isinstance(url, str) and url.startswith("https://") else "Source URL unavailable")
        cards.append(
            '<article class="news-card">'
            f'<h3>{_s(item.get("title") or item.get("event_id") or "Untitled source report")}</h3>'
            f'<p class="badge">{_s(item.get("verification_status") or "UNVERIFIED")}</p>'
            f'<p>Publisher: {_s(item.get("publisher") or item.get("source"))} · '
            f'Original publisher: {_s(item.get("original_publisher"))} · {link}</p>'
            f'<p>Source time: {_time(item.get("source_published_at"))} · '
            f'original zone: {_s(item.get("source_timezone"))} · '
            f'precision: {_s(item.get("time_precision"))} · '
            f'first local receipt: {_time(item.get("first_received_at"))} · '
            f'version: {_s(item.get("version"))}</p>'
            '<p class="note">Ticker association is a candidate until source and historical identity checks pass.</p>'
            '</article>'
        )
    return "".join(cards)


def _watchlist_section(snapshot: dict[str, Any], watchlist: dict[str, Any],
                       quote_rows: list[dict[str, Any]],
                       live_status: dict[str, Any] | None = None) -> str:
    """Render the v2 observation scope without promoting candidate associations."""
    from .watchlist import validate_watchlist

    validate_watchlist(watchlist)
    instruments = {row.get("symbol"): row for row in snapshot.get("instruments", [])
                   if isinstance(row, dict) and row.get("symbol")}
    live = live_status if isinstance(live_status, dict) else {}
    last_market_codes = live.get("market_state_codes")
    market_labels = {0: "NOT_OPEN_OR_UNSUPPORTED", 6: "CLOSED", 11: "AFTER_HOURS_END"}
    last_market_signal = ", ".join(
        f"{market} {market_labels.get(code, 'UNCLASSIFIED')} ({code})"
        for market, code in sorted(last_market_codes.items())
        if isinstance(market, str) and isinstance(code, int) and not isinstance(code, bool)
    ) if isinstance(last_market_codes, dict) else ""
    enabled = {row.get("provider_code") for row in live.get("enabled_subscriptions", [])
               if isinstance(row, dict) and row.get("provider_code")}
    skipped = {row.get("provider_code"): row.get("reason")
               for row in live.get("skipped_subscriptions", [])
               if isinstance(row, dict) and row.get("provider_code")}
    by_code: dict[str, dict[str, Any]] = {}
    for row in quote_rows:
        code = row.get("instrument_id")
        if isinstance(code, str) and (code not in by_code or
                                      str(row.get("provider_time") or "") >
                                      str(by_code[code].get("provider_time") or "")):
            by_code[code] = row
    entity_events: dict[str, set[str]] = {}
    for link in snapshot.get("event_entity_links", []):
        if isinstance(link, dict) and link.get("entity_id") and link.get("event_id"):
            entity_events.setdefault(str(link["entity_id"]), set()).add(str(link["event_id"]))
    news_ids_by_event: dict[str, set[Any]] = {}
    for link in snapshot.get("event_news_links", []):
        if isinstance(link, dict) and link.get("event_id") and link.get("news_id") is not None:
            news_ids_by_event.setdefault(str(link["event_id"]), set()).add(link["news_id"])
    news_by_id = {item.get("id"): item for item in snapshot.get("news", []) if isinstance(item, dict)}
    outcomes_by_prediction = {row.get("prediction_id"): row for row in snapshot.get("outcomes", [])
                              if isinstance(row, dict) and row.get("prediction_id")}
    group_names = {
        "priority_etfs": "Priority ETFs",
        "benchmark": "QQQ benchmark ETF",
        "magnificent_seven": "Magnificent Seven",
        "memory": "Memory and storage",
        "space": "SpaceX",
        "related": "Related issuers",
    }
    blocks: list[str] = [
        "<section class='watchlist-intro'><h2>Observation pool v2</h2>"
        "<p>Entity, security and ETF records are separate. Ticker matches suggest a company link only; "
        "a price move does not verify news. The market-data fields below come from this local snapshot.</p>"
        "<p>ETF and constituent returns overlap and are not independent event confirmations. "
        "Dated official holdings are required before weighted exposure analysis.</p>"
        "<p>Push connection: " + _s(live.get("connection_state") or "NOT_TESTED") +
        " · status observed: " + _time(live.get("observed_at_utc")) +
        ". Subscription status is a connection receipt, not proof of live price updates.</p>"
        "<p>Last provider market signal: " + _s(last_market_signal or "UNKNOWN") +
        ". The signal time was not separately recorded; a disconnected receipt is historical.</p></section>"
    ]
    for group in watchlist["display_order"]:
        cards: list[str] = []
        for entity_id in watchlist["groups"][group]:
            entity = watchlist["entities"][entity_id]
            security_parts: list[str] = []
            verified_codes: set[str] = set()
            for security_id in entity["security_ids"]:
                security = watchlist["securities"][security_id]
                ticker = security["requested_ticker"]
                mapping = watchlist["verified_current_mappings"][security_id]
                runtime = instruments.get(ticker) or {}
                runtime_verified = str(runtime.get("resolution_status") or "").startswith("VERIFIED")
                runtime_code = runtime.get("provider_code") if runtime_verified else None
                provider_code = runtime_code or mapping["provider_code"]
                mapping_status = ("VERIFIED_PRIVATE_STORE_CURRENT_ONLY" if runtime_code == mapping["provider_code"]
                                  else "VERIFIED_CONFIG_RECEIPT_CURRENT_ONLY" if not runtime_code
                                  else "CONFLICT_PRIVATE_STORE_VS_CONFIG")
                if runtime_code and runtime_code != mapping["provider_code"]:
                    provider_code = None
                if provider_code:
                    verified_codes.add(str(provider_code))
                quote = by_code.get(str(provider_code)) if provider_code else None
                subscription = ("Enabled in last connection receipt" if provider_code in enabled else
                                "Skipped: " + str(skipped[provider_code]) if provider_code in skipped else
                                "Not confirmed")
                mapping_label = ("Conflicting mappings; quote suppressed" if provider_code is None else
                                 "Current mapping verified")
                role = "share-class alias; not separately watched" if security.get("watch_enabled") is False else \
                    security["type_candidate"].replace("_", " ").lower()
                security_parts.append(
                    "<li><strong>" + _s(ticker) + "</strong> · " + _s(role) +
                    " · observed price/bar: " + _s(quote.get("close") if quote else None) +
                    "<br>Data time: " + _time(quote.get("provider_time") if quote else None) +
                    "<br>Market: " + _s(quote.get("market_session") if quote else None) +
                    " · delay: " + _s(quote.get("delay_status") if quote else None) +
                    " · freshness: " + _s(str(quote.get("freshness")).replace("_", " ").lower()
                                          if quote and quote.get("freshness") else None) +
                    "<br>Push: " + _s(subscription) +
                    "<details><summary>Mapping and receipt details</summary><p>"
                    "Market: " + _s(security["market_hint"]) +
                    " · provider code: " + _s(provider_code) +
                    " · mapping: " + _s(mapping_label) +
                    " (" + _s(mapping_status) + ")" +
                    "<br>Receipt/upper bound: " + _time(quote.get("received_at") if quote else None) +
                    " · receipt precision: " + _s(quote.get("receipt_semantics") if quote else None) +
                    "</p></details></li>"
                )
            if not security_parts:
                security_parts.append("<li>News and earnings watch only; no verified quote security.</li>")
            event_ids = entity_events.get(entity_id, set())
            linked_news_ids = {news_id for event_id in event_ids
                               for news_id in news_ids_by_event.get(event_id, set())}
            linked_news = [news_by_id[news_id] for news_id in linked_news_ids if news_id in news_by_id]
            if linked_news:
                news_parts = []
                for item in sorted(linked_news, key=lambda x: str(x.get("first_received_at") or ""),
                                   reverse=True)[:2]:
                    url = item.get("source_url")
                    link = (f'<a href="{escape(url, quote=True)}" target="_blank" '
                            'rel="noopener noreferrer">source</a>') if isinstance(url, str) and \
                        url.startswith("https://") else "source URL unknown"
                    news_parts.append(_s(item.get("title") or "source report") + " · " +
                                      _s(item.get("verification_status") or "UNVERIFIED") + " · " + link)
                news_text = "<br>".join(news_parts)
            else:
                news_text = "No explicitly linked source report in this local view; coverage unknown."
            predictions = [row for row in snapshot.get("predictions", [])
                           if isinstance(row, dict) and row.get("event_id") in event_ids and
                           row.get("instrument_id") in verified_codes]
            predictions.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
            latest_prediction = predictions[0] if predictions else None
            latest_outcome = outcomes_by_prediction.get(latest_prediction.get("prediction_id")) \
                if latest_prediction else None
            decision_text = ("No pre-window decision linked to this entity in the local view." if
                             latest_prediction is None else
                             "Decision: " + _s(latest_prediction.get("direction")) +
                             " · persisted: " + _time(latest_prediction.get("created_at")) +
                             " · quality: " + _s(latest_prediction.get("data_quality_status")))
            outcome_text = ("No mature result linked to this entity in the local view." if
                            latest_outcome is None else
                            "Outcome: " + _s(latest_outcome.get("status")) +
                            " · actual return: " + _s(latest_outcome.get("actual_return")) +
                            " · QQQ difference: " + _s(latest_outcome.get("qqq_return_difference")))
            extras = ""
            if entity["kind"] == "ETF":
                index = watchlist["indices"][entity["reference_index_id"]]
                extras = "<p>Reference index (separate entity): " + _s(index["name"]) + ". "
                if entity_id == "ETF:TQQQ":
                    extras += "Daily target: 3× index performance before fees; outcome labels use observed TQQQ prices. "
                extras += "No dated official holdings weights are loaded.</p>"
            elif entity_id == "COMPANY:SK_HYNIX":
                extras = "<p>US ADR and Korean ordinary share candidates remain separate price series.</p>"
            elif entity_id == "COMPANY:ALPHABET":
                extras = "<p>GOOGL is the selected watch security. GOOG is a separate share class mapped to the same company.</p>"
            elif entity_id in {"COMPANY:TESLA", "COMPANY:SPACEX"}:
                extras = "<p>Tesla and SpaceX are separate issuers; a shared person does not merge financial events.</p>"
            cards.append(
                "<article class='watch-card' data-entity='" + escape(entity_id, quote=True) + "'>"
                "<h3>" + _s(entity["name"]) + " <span class='badge'>" + _s(entity["kind"]) + "</span></h3>"
                "<ul>" + "".join(security_parts) + "</ul>"
                "<p>Related news / source level: " + news_text + "</p>"
                "<p>" + decision_text + "</p><p>" + outcome_text + "</p>" + extras +
                "</article>"
            )
        blocks.append("<section class='watch-group' data-group='" + escape(group, quote=True) +
                      "'><h2>" + _s(group_names[group]) + "</h2><div class='watch-grid'>" +
                      "".join(cards) + "</div></section>")
    blocks.append(
        "<section><h2>Analysis intake</h2><p>Earnings fields: revenue, net income, EPS, gross margin, "
        "capital spending, cash flow, segments, next-quarter and full-year guidance. Actual, prior actual, "
        "guidance and pre-release consensus require separate sourced fields; missing consensus stays unknown.</p>"
        "<p>Storage topics: DRAM, NAND, HBM, enterprise SSD, price, capacity, inventory, orders and capital "
        "spending. A product-specific price report is not an all-memory price change.</p>"
        "<p>Macro topics: interest rates, CPI, employment, policy, energy and geopolitical conflict. "
        "Transmission is conditional and company-specific.</p></section>"
    )
    return "".join(blocks)


def render_dashboard(snapshot: dict[str, Any] | None = None,
                     *, watchlist: dict[str, Any] | None = None,
                     live_status: dict[str, Any] | None = None) -> str:
    """Render a standalone, escaped HTML page. ``snapshot=None`` is safe to publish."""
    d = snapshot or {}
    service = d.get("service") if isinstance(d.get("service"), dict) else {}
    state = service.get("state") or "NOT_CONNECTED"
    if state == "RUNNING" and not service.get("provider_connected"):
        state = "RUNNING_WITHOUT_PROVIDER_CONNECTION"
    service_info = (f'Service: {_s(state)} · provider: {_s(service.get("provider_state"))} · '
                    f'heartbeat: {_time(service.get("heartbeat_at"))} · '
                    f'last error: {_s(service.get("last_error"))}')
    quote_rows = [dict(row) for row in (d.get("quotes") or []) if isinstance(row, dict)]
    for row in quote_rows:
        if isinstance(row, dict):
            # A recorded price alone never proves that a live stream is updating.
            if row.get("market_session") != "REGULAR_OPEN":
                row["freshness"] = "MARKET_NOT_CONFIRMED_OPEN"
            elif row.get("delay_status") not in {"REAL_TIME", "REALTIME"}:
                row["freshness"] = "DELAYED_OR_UNKNOWN"
            elif not row.get("provider_time") or not row.get("received_at"):
                row["freshness"] = "PROVIDER_OR_RECEIPT_TIME_UNKNOWN"
            else:
                try:
                    parsed = datetime.fromisoformat(str(row["provider_time"]).replace("Z", "+00:00"))
                    received = datetime.fromisoformat(str(row["received_at"]).replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                    provider_age = (now - parsed.astimezone(timezone.utc)).total_seconds()
                    receipt_age = (now - received.astimezone(timezone.utc)).total_seconds()
                    row["freshness"] = ("RECENT_QUOTE" if 0 <= provider_age <= 120 and
                                        0 <= receipt_age <= 120 else "STALE_OR_FUTURE_QUOTE")
                except (TypeError, ValueError):
                    row["freshness"] = "PROVIDER_TIME_UNPARSEABLE"
    instrument_table = _rows(d.get("instruments"), (("symbol", "Requested"), ("provider_code", "Resolved code"),
                        ("market", "Market"), ("resolution_status", "Resolution")),
                        "No instrument has been verified through an official security-information response.")
    quote_table = _rows(quote_rows, (("instrument_id", "Instrument"), ("close", "Price / bar close"),
                        ("provider_time", "Provider time"), ("received_at", "Receipt time / upper bound"),
                        ("receipt_semantics", "Receipt precision"),
                        ("market_session", "Market state"),
                        ("delay_status", "Delay"), ("freshness", "Freshness")),
                        "No quote or completed one-minute bar has been stored. This does not imply a flat market.")
    prediction_table = _rows(d.get("predictions"), (("prediction_id", "ID"), ("event_id", "Event"),
                            ("instrument_id", "Instrument"), ("created_at", "Persisted decision"),
                            ("direction", "Direction / abstain"), ("data_quality_status", "Quality")),
                            "No pre-window prediction has been persisted.")
    outcome_table = _rows(d.get("outcomes"), (("prediction_id", "Prediction"), ("status", "Maturity"),
                         ("actual_return", "Return"), ("qqq_return_difference", "QQQ difference"),
                         ("evaluated_at", "Evaluated")),
                         "No mature outcome. Pending predictions must remain pending.")
    review_table = _rows(d.get("review_queue"), (("reason", "Reason"), ("status", "Status"),
                        ("created_at", "Created"), ("human_verdict", "Human verdict")),
                        "No review item is recorded in this local view.")
    gap_table = _rows(d.get("collection_gaps"), (("provider", "Provider"), ("reason", "Reason"),
                     ("started_at", "Gap start"), ("ended_at", "Gap end"), ("backfilled_at", "Backfill acquired")),
                     "No gap record in this view; coverage is only established by completed source receipts.")
    claims = _rows(d.get("source_claims"), (("claim_id", "Claim"), ("status", "Evidence status"),
                   ("source_locator", "Source locator")),
                   "No claim has been promoted from a source report.")
    watchlist_html = _watchlist_section(d, watchlist, quote_rows, live_status) if watchlist is not None else ""
    css = """
    :root{color-scheme:light;font-family:system-ui,-apple-system,Segoe UI,sans-serif;color:#192431;background:#f4f7fb}
    body{margin:0}header{background:#172b44;color:white;padding:1.1rem max(1rem,calc((100vw - 1180px)/2))}
    h1{font-size:1.55rem;margin:.2rem 0}header p{margin:.3rem 0;color:#d3e1f1}main{max-width:1180px;margin:auto;padding:1rem}
    section{background:white;border:1px solid #d4dfeb;border-radius:12px;padding:1rem;margin:0 0 1rem;min-width:0}
    h2{font-size:1.18rem;margin:0 0 .7rem}.status{border-left:5px solid #b96b18;background:#fff7e9;padding:.8rem;overflow-wrap:anywhere}
    .badge{display:inline-block;background:#ecf2fb;border-radius:20px;padding:.18rem .55rem;font-size:.78rem;font-weight:700}
    .note,.empty{color:#526174}.table-scroll{overflow-x:auto;max-width:100%}table{border-collapse:collapse;width:100%;min-width:580px}
    th,td{text-align:left;border-bottom:1px solid #e1e7ef;padding:.45rem;vertical-align:top;overflow-wrap:anywhere}th{background:#edf3fa}
    .news-card{border:1px solid #e1e7ef;border-radius:8px;padding:.7rem;margin:.6rem 0;overflow-wrap:anywhere}.news-card h3{margin:.1rem 0;font-size:1rem}
    .news-card p{margin:.35rem 0}a{color:#154e91;overflow-wrap:anywhere}footer{color:#526174;padding:1rem;max-width:1180px;margin:auto}
    .watch-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,340px),1fr));gap:.7rem}
    .watch-card{border:1px solid #d7e1eb;border-radius:9px;padding:.8rem;min-width:0;overflow-wrap:anywhere}
    .watch-card h3{margin:.05rem 0 .5rem}.watch-card p{margin:.5rem 0}.watch-card ul{padding-left:1.15rem;margin:.4rem 0}
    .watch-card li{margin:.4rem 0}.watch-card details{margin-top:.35rem}.watch-card summary{cursor:pointer;color:#154e91}
    .watchlist-intro{border-left:5px solid #3b6796}
    @media(max-width:600px){header{padding:1rem}main{padding:.55rem}section{padding:.75rem}h1{font-size:1.32rem}table{font-size:.83rem}}
    """
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Read-only market observer v1</title><style>" + css + "</style></head><body>"
            "<header><h1>Read-only market observer v1</h1><p>Moomoo quotes + Jin10 source reports · no trading</p>"
            "<p>Local time display: Asia/Shanghai; raw provider timezone retained in private records.</p></header>"
            "<main><section class='status' aria-label='Collection status'><strong>" + service_info + "</strong>"
            "<p>Acquisition failure and incomplete pagination are coverage gaps, not evidence that no news occurred.</p></section>"
            + watchlist_html +
            "<section><h2>Instruments</h2>" + instrument_table + "</section>"
            "<section><h2>Market observations</h2>" + quote_table + "</section>"
            "<section><h2>News and source relationships</h2><p class='note'>Source reports, verified claims and inference stay separate. Price movement does not corroborate a report.</p>"
            + _news(d.get("news")) + "<h3>Verified claim layer</h3>" + claims + "</section>"
            "<section><h2>Pre-window decisions</h2>" + prediction_table + "</section>"
            "<section><h2>Mature outcomes</h2>" + outcome_table + "</section>"
            "<section><h2>Collection gaps</h2>" + gap_table + "</section>"
            "<section><h2>Review queue</h2>" + review_table + "</section></main>"
            "<footer>Research observation only. A local decision is not an investment recommendation or an established forecast. "
            "Model quality and formal financial validity remain untested.</footer></body></html>")
