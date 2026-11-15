"""Render the source-bound earnings, macro and company-news development page.

This file only formats already reviewed local products. It makes no network,
model, price or financial-label calls.
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results/news_earnings_macro_20261115"
METRIC_LABELS = {
    "revenue": "Revenue / 营收", "operating_income": "Operating income / 营业利润",
    "net_income": "Net income / 净利润", "diluted_eps": "Diluted EPS / 摊薄每股收益",
    "gross_margin": "Gross margin / 毛利率", "operating_margin": "Operating margin / 营业利润率",
    "operating_cash_flow": "Operating cash flow / 经营现金流",
    "capex": "CapEx / 资本支出", "free_cash_flow": "Free cash flow / 自由现金流",
    "cash_and_equivalents": "Cash and equivalents / 现金及等价物", "debt": "Debt / 债务",
    "interest_expense": "Interest expense / 利息费用",
}


def h(value: object) -> str:
    return html.escape(str(value), quote=True)


def link(url: str, label: str) -> str:
    if not url.startswith("https://"):
        raise ValueError("Only HTTPS source links are supported")
    return f'<a href="{h(url)}" target="_blank" rel="noopener noreferrer">{h(label)}</a>'


def display_value(row: dict) -> str:
    if row.get("raw_value") is None:
        return f'<span class="missing">Missing: {h(row.get("missing_reason") or "NOT_FOUND")}</span>'
    role = ' <span class="muted">(DERIVED)</span>' if row.get("value_role") == "DERIVED" else ""
    return f'{h(row["raw_value"])} {h(row.get("unit") or "")}{role}'


def source_detail(row: dict) -> str:
    return (f'<details><summary>Source & basis / 来源和口径</summary>'
            f'<p>{link(row["source_url"], "Official source")} · {h(row.get("source_locator") or "locator pending")}</p>'
            f'<p>{h(row.get("period_basis"))} · {h(row.get("scope"))} · '
            f'{h(row.get("accounting_standard"))} · {h(row.get("adjustment_basis"))}</p>'
            f'<p>Published: {h(row.get("published_at") or "unknown")} · {h(row.get("timestamp_precision") or "unknown")} · '
            f'first observed availability: {h(row.get("first_available_at") or "unknown")}</p>'
            f'<p>Role: {h(row.get("value_role"))} · formula: {h(row.get("formula") or "n/a")}</p>'
            f'<p>SHA-256: <code>{h(row.get("source_sha256"))}</code></p></details>')


def guidance_value(item: dict) -> str:
    low, high, point = item.get("low"), item.get("high"), item.get("point_estimate")
    if low is not None and high is not None:
        value = str(low) if low == high else f"{low}–{high}"
    elif point is not None:
        value = str(point)
    else:
        value = item.get("narrative") or "Qualitative / numeric value not disclosed"
    return f"{value} {item.get('unit') or ''}"


def guidance_period(item: dict) -> str:
    start, end = item.get("target_period_start"), item.get("target_period_end")
    return f"{start} to {end}" if start and end else "target dates unknown"


def guidance_period_basis(item: dict) -> str:
    status = item.get("target_period_date_status") or "unreviewed"
    basis = item.get("target_period_date_basis")
    if not isinstance(basis, dict) or not basis.get("not_official_exact_target_dates"):
        return f'period basis: {h(status)}'
    sources = basis.get("input_sources") or []
    source_links = " · ".join(
        f'{link(source["url"], f"official basis {index}")} ({h(source.get("locator") or "locator unknown")})'
        for index, source in enumerate(sources, 1) if isinstance(source, dict) and source.get("url")
    )
    return (f'<span class="flag">Target dates inferred from official fiscal calendar; '
            f'not issuer-stated exact dates ({h(status)}).</span>'
            f'<details><summary>Target-date derivation / 财期推断依据</summary>'
            f'<p>{h(basis.get("formula") or "formula unavailable")}</p>'
            f'<p>{source_links or "Source links unavailable"}</p></details>')


def gap_text(item: object) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return " · ".join(str(item.get(key)) for key in ("field", "reason", "status", "detail") if item.get(key))
    return "Unrecognized gap entry"


def company_panel(card: dict, growth: dict[str, dict]) -> str:
    issuer = card["issuer"]
    rows = []
    for row in card["metrics"]:
        metric = row["metric"]
        derived = growth.get(f"{issuer}/{metric}", {})
        yoy = derived.get("YOY", "—")
        qoq = derived.get("QOQ", "—")
        rows.append(f'<tr><th scope="row">{h(METRIC_LABELS.get(metric, metric))}</th>'
                    f'<td>{display_value(row)}</td><td>{h(yoy)}</td><td>{h(qoq)}</td>'
                    f'<td>{source_detail(row)}</td></tr>')
    guidance = card.get("guidance") or []
    guidance_html = "".join(
        f'<li><strong>{h(item.get("metric"))}</strong> · {h(guidance_period(item))} · '
        f'{h(guidance_value(item))} · {h(item.get("basis"))} · '
        f'{link(item["source_url"], "source")} · '
        f'{guidance_period_basis(item)} · '
        f'published: {h(item.get("published_at") or "unknown")} '
        f'({h(item.get("timestamp_precision") or "unknown precision")}) · '
        f'first available: {h(item.get("first_available_at") or "unknown")} '
        f'({h(item.get("first_available_status") or "status unknown")})</li>'
        for item in guidance)
    if not guidance_html:
        guidance_html = '<li class="missing">No source-bound numeric guidance in this card.</li>'
    other_html = "".join(
        f'<li><strong>{h(item.get("metric"))}</strong>: {display_value(item)} · '
        f'{h(item.get("scope") or "scope unknown")} · {link(item["source_url"], "source")} · '
        f'{h(item.get("source_locator") or "locator pending")}</li>'
        for item in card.get("other_values", []) if item.get("source_url"))
    if not other_html:
        other_html = '<li class="missing">No additional segment or financing figures in this card.</li>'
    gaps = card.get("gaps") or []
    gaps_html = "".join(f'<li>{h(gap_text(item))}</li>' for item in gaps)
    if not gaps_html:
        gaps_html = '<li>See explicit missing reasons in the metric table.</li>'
    return (f'<article class="card"><h3>{h(card.get("issuer_name") or card.get("issuer"))}</h3>'
            f'<p class="muted">{h(card.get("report_period"))} · report date {h(card.get("report_date"))} · '
            f'{h(card.get("report_status") or "issuer report")}</p>'
            f'<p class="flag">Development card. Historical security identity and market consensus are unverified.</p>'
            f'<div class="table-wrap"><table><thead><tr><th>Metric</th><th>Actual</th><th>YoY*</th><th>QoQ*</th><th>Evidence</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>'
            f'<p class="muted">*Calculated only for source-supported matching bases and positive prior values; '
            f'otherwise shown as —. Pre-release consensus: NOT_FOUND; beat/miss: NOT_COMPUTED.</p>'
            f'<details><summary>Management guidance / 管理层指引</summary><ul>{guidance_html}</ul></details>'
            f'<details><summary>Business & financing context / 业务和融资</summary><ul>{other_html}</ul></details>'
            f'<details><summary>Known gaps / 已知缺口</summary><ul>{gaps_html}</ul></details></article>')


def macro_panel(card: dict) -> str:
    values = []
    for value in card["values"]:
        actual = value.get("actual")
        prior = value.get("prior_pre_release")
        revised = value.get("prior_revised_in_current_release")
        values.append(f'<tr><th>{h(value["metric"])}</th><td>{h(actual) if actual is not None else "Missing"}</td>'
                      f'<td>{h(prior) if prior is not None else "—"}</td>'
                      f'<td>{h(revised) if revised is not None else "—"}</td><td>{h(value["unit"])}</td></tr>')
    return (f'<article class="card"><h3>{h(card["type"])} · {h(card["reference_period"])}</h3>'
            f'<p>{h(card["official_time_text"])} · first observed availability: unknown</p>'
            f'<div class="table-wrap"><table><thead><tr><th>Metric</th><th>Actual</th><th>Prior as released</th>'
            f'<th>Prior revised</th><th>Unit</th></tr></thead><tbody>{"".join(values)}</tbody></table></div>'
            f'<p>Pre-release consensus: NOT_FOUND. Surprise and market outcome: NOT_COMPUTED / NOT_RUN.</p>'
            f'<p>{link(card["source_url"], "Official release")} · {h(card["source_locator"])}</p></article>')


def news_panel(event: dict) -> str:
    return (f'<article class="card"><h3>{h(event["title"])}</h3>'
            f'<p class="muted">{h(event["issuer_id"])} · {h(event["event_date"])} · '
            f'{h(event["timestamp_precision"])}</p><p>{h(event["facts"])}</p>'
            f'<p>{link(event["source_url"], "Official source")} · {h(event["source_locator"])}</p>'
            f'<p class="muted">Market observation: NOT_RUN. No price-based selection.</p></article>')


def render(cards: dict, macro: dict, news: dict, growth_rows: list[dict], drafts: list[dict]) -> str:
    growth: dict[str, dict] = {}
    for row in growth_rows:
        if row.get("status") == "COMPUTED":
            growth.setdefault(f'{row["issuer_id"]}/{row["metric"]}', {})[row["relation"]] = f'{row["growth_percent"]}%'
    companies = "".join(company_panel(card, growth) for card in cards["cards"])
    macros = "".join(macro_panel(card) for card in macro["cards"])
    events = "".join(news_panel(event) for event in news["events"])
    draft_html = "".join(
        f'<article class="card"><h3>{h(item["title"])}</h3><p class="flag">AI_ANALYST_DRAFT · '
        f'conditional hypothesis, not model output or observed market effect</p><p>{h(item["conditional_path"])}</p>'
        f'<p>{" · ".join(link(url, "source") for url in item["source_urls"])}</p></article>'
        for item in drafts)
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Technology earnings + macro development panel</title>
<style>
:root{{--ink:#192941;--muted:#52647a;--line:#d6dfe8;--bg:#f5f8fb;--card:#fff;--accent:#145b88}}
*{{box-sizing:border-box}}body{{margin:0;font:15px/1.45 system-ui,sans-serif;color:var(--ink);background:var(--bg)}}
header{{padding:20px 26px;background:#142d4a;color:white}}header h1{{margin:0 0 6px;font-size:25px}}header p{{margin:0;color:#d8e7f5}}
nav{{display:flex;flex-wrap:wrap;gap:15px;padding:12px 26px;background:#e7eef6;position:sticky;top:0;z-index:2}}nav a{{color:var(--accent);font-weight:700}}
main{{padding:18px;display:grid;grid-template-columns:minmax(420px,2.1fr) minmax(320px,1fr) minmax(320px,1fr);gap:16px;align-items:start}}
section{{min-width:0}}h2{{font-size:19px;margin:0 0 12px}}.card{{background:var(--card);border:1px solid var(--line);border-radius:9px;padding:15px;margin-bottom:14px;box-shadow:0 2px 8px #17314c0a}}
.card h3{{margin:0 0 7px;font-size:17px}}.card p{{margin:7px 0}}.card{{overflow-wrap:anywhere}}.muted{{color:var(--muted);font-size:13px}}.flag{{background:#fff6dc;padding:8px;border-radius:5px}}
.missing{{color:#874927}}.table-wrap{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{text-align:left;border-bottom:1px solid var(--line);padding:7px;vertical-align:top}}td details{{min-width:120px}}
details{{margin:8px 0}}summary{{cursor:pointer;color:var(--accent);font-weight:600}}code{{overflow-wrap:anywhere}}a{{color:var(--accent)}}
footer{{padding:20px;color:var(--muted)}}@media(max-width:1100px){{main{{grid-template-columns:1fr}}nav{{position:static}}}}@media(max-width:600px){{header{{padding:16px}}main{{padding:10px}}.card{{padding:12px}}nav{{gap:8px;padding:10px}}.table-wrap table{{min-width:600px}}.table-wrap :is(th,td):nth-child(2),.table-wrap :is(th,td):nth-child(3),.table-wrap :is(th,td):nth-child(4){{white-space:nowrap}}}}
</style></head><body><header><h1>Technology earnings, macro & major news</h1>
<p>Source-bound development intake · frozen cutoff {h(cards["as_of_utc"])} · preliminary/descriptive · financial evaluation NOT_RUN</p></header>
<nav><a href="#earnings">Company earnings / 财报</a><a href="#macro">Macro calendar / 宏观</a><a href="#news">Major events / 新闻</a></nav>
<main><section id="earnings"><h2>Company earnings · {len(cards["cards"])} issuers</h2>{companies}</section>
<section id="macro"><h2>Macro calendar · {len(macro["cards"])} releases</h2>{macros}</section>
<section id="news"><h2>Major events · {len(news["events"])} items</h2>{events}<h2>Conditional transmission drafts</h2>{draft_html}</section></main>
<footer>No new model inference, financial labels, market returns or backtest. Source captures are retained locally; this page contains short source-bound values and self-authored summaries.</footer></body></html>'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    cards = json.loads((RESULTS / "company_cards.json").read_text(encoding="utf-8"))
    macro = json.loads((RESULTS / "macro_cards.json").read_text(encoding="utf-8"))
    news = json.loads((RESULTS / "news_events.json").read_text(encoding="utf-8"))
    growth = json.loads((RESULTS / "growth_calculations.json").read_text(encoding="utf-8"))
    drafts = json.loads((RESULTS / "transmission_drafts.json").read_text(encoding="utf-8"))
    if args.out.exists():
        raise FileExistsError(args.out)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(cards, macro, news, growth["calculations"], drafts["drafts"]), encoding="utf-8", newline="\n")
    print(args.out)


if __name__ == "__main__":
    main()
