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


def render_dashboard(snapshot: dict[str, Any] | None = None) -> str:
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
    @media(max-width:600px){header{padding:1rem}main{padding:.55rem}section{padding:.75rem}h1{font-size:1.32rem}table{font-size:.83rem}}
    """
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Read-only market observer v1</title><style>" + css + "</style></head><body>"
            "<header><h1>Read-only market observer v1</h1><p>Moomoo quotes + Jin10 source reports · no trading</p>"
            "<p>Local time display: Asia/Shanghai; raw provider timezone retained in private records.</p></header>"
            "<main><section class='status' aria-label='Collection status'><strong>" + service_info + "</strong>"
            "<p>Acquisition failure and incomplete pagination are coverage gaps, not evidence that no news occurred.</p></section>"
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
