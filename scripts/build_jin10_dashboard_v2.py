"""Build a new local dashboard without changing the frozen earnings page.

Without --private-db this creates a shareable template with an explicit
NOT_ATTEMPTED collection status. With a private database it embeds source
reports only in an ignored local HTML file; never publish that file.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.build_earnings_dashboard import RESULTS as OLD_RESULTS
from scripts.build_earnings_dashboard import h, link, render
from scripts.jin10_mcp.export import export_display


ROOT = Path(__file__).resolve().parents[1]
NEW_RESULTS = ROOT / "results/news_jin10_20261115"
PRIVATE_ROOT = ROOT / "artifacts"


def load(name: str, root: Path = OLD_RESULTS) -> dict:
    return json.loads((root / name).read_text(encoding="utf-8"))


def _status_label(stream: dict) -> str:
    key = stream.get("stream_key") or stream.get("tool_name") or "unknown stream"
    state = stream.get("coverage_status") or "UNKNOWN"
    error = stream.get("last_error")
    error_text = f" · error: {h(error)}" if error else ""
    return f'<li><strong>{h(key)}</strong>: {h(state)}{error_text}</li>'


def _provider_row(row: dict) -> str:
    title = row.get("title") or row.get("content") or "Source report title unavailable"
    detail = row.get("content")
    candidates = ", ".join(row.get("issuer_candidates") or []) or "none"
    url = row.get("source_url")
    source_link = link(url, "Original URL") if isinstance(url, str) and url.startswith("https://") else "URL unavailable"
    calendar_values = row.get("calendar_values")
    if isinstance(calendar_values, dict):
        cells = " · ".join(
            f'{label}: {h(calendar_values.get(field)) if calendar_values.get(field) is not None else "unknown"}'
            for field, label in (("actual", "actual"), ("consensus", "consensus"),
                                 ("previous", "previous"), ("revised", "revised"))
        )
        calendar_detail = (f'<p class="muted">Scheduled: {h(row.get("scheduled_for_raw") or "unknown")} '
                           f'({h(row.get("scheduled_time_precision") or "unknown precision")}); '
                           f'provider theoretical time, not actual release. {cells}. Prerelease consensus snapshot: '
                           f'{h(row.get("pre_release_consensus_status") or "UNKNOWN")}.</p>')
    else:
        calendar_detail = ""
    return (f'<article class="card"><h3>{h(title)}</h3>'
            f'<p class="flag">Jin10 SOURCE_REPORT · unverified; company association is a candidate</p>'
            f'<p>{h(detail) if detail and detail != title else ""}</p>{calendar_detail}'
            f'<p class="muted">Source published: {h(row.get("source_published_raw") or "unknown")} · '
            f'first local receipt: {h(row.get("first_received_at_utc") or "unknown")} · '
            f'last fetched: {h(row.get("last_fetched_at_utc") or "unknown")}</p>'
            f'<p class="muted">Candidate companies: {h(candidates)} · category candidate: '
            f'{h(row.get("event_category_candidate") or "unclassified")} · '
            f'version {h(row.get("version_number") or "unknown")} · {source_link}</p></article>')


def render_v2(feed: dict | None = None) -> str:
    cards = copy.deepcopy(load("company_cards.json"))
    guidance = load("guidance_cards_v2.json", NEW_RESULTS / "guidance_v2")
    mapped = {card["issuer_id"]: card["guidance"] for card in guidance["cards"]}
    if set(mapped) != {card["issuer"] for card in cards["cards"]}:
        raise ValueError("Guidance v2 issuer mismatch")
    for card in cards["cards"]:
        card["guidance"] = mapped[card["issuer"]]
    macro = load("macro_cards.json")
    news = load("news_events.json")
    growth = load("growth_calculations.json")
    drafts = load("transmission_drafts.json")
    page = render(cards, macro, news, growth["calculations"], drafts["drafts"])
    if feed is None:
        feed = {"records": [], "acquisition_status": [], "raw_provider_text_included": False}
        state = "PUBLIC_TEMPLATE_PRIVATE_FEED_OMITTED"
    else:
        stream_states = [row.get("coverage_status") for row in feed.get("acquisition_status", [])]
        if not stream_states:
            state = "LOCAL_COLLECTION_NOT_STARTED"
        elif any(value != "COMPLETE_FOR_REQUESTED_RANGE" for value in stream_states):
            state = "LOCAL_CAPTURE_WITH_COVERAGE_GAP" if feed.get("records") else "ACQUISITION_INCOMPLETE_OR_ERROR"
        else:
            state = "LOCAL_CAPTURE_AVAILABLE" if feed.get("records") else "EMPTY_FOR_REQUESTED_RANGE_ONLY"
    status_rows = "".join(_status_label(row) for row in feed["acquisition_status"])
    if not status_rows:
        status_rows = "<li>No Jin10 stream has been run in this local export.</li>"
    banner = (f'<aside id="collector-status" class="collector"><strong>Jin10 collection: {h(state)}</strong>'
              f'<p>These live/current source reports are separate from the frozen official cards dated '
              f'{h(cards["as_of_utc"])}. An API failure or incomplete page means a coverage gap, not “no news today.” '
              f'Current retrieval cannot prove historical first availability or prerelease expectations.</p>'
              f'<ul>{status_rows}</ul></aside>')
    page = page.replace('</nav>\n<main>', '</nav>\n' + banner + '\n<main>', 1)
    records = feed.get("records") or []
    flash_scanned = [row for row in records if "flash" in str(row.get("tool_name", "")).lower()]
    flashes = [row for row in flash_scanned if row.get("issuer_candidates") or
               row.get("event_category_candidate") not in (None, "UNCLASSIFIED")]
    calendar = [row for row in records if "calendar" in str(row.get("tool_name", "")).lower()]
    if flashes:
        flash_html = "".join(_provider_row(row) for row in flashes)
    else:
        flash_html = '<p class="flag">No related flash candidates in the completed local display window. Collection status above applies.</p>'
    if calendar:
        calendar_html = "".join(_provider_row(row) for row in calendar)
    else:
        calendar_html = '<p class="flag">No completed Jin10 calendar records in this bounded local export. This does not establish no scheduled releases.</p>'
    supplement = (f'<h2>Related Jin10 flash candidates · {len(flashes)} of {len(flash_scanned)} '
                  f'in this display window</h2><p class="flag">Keyword candidates only; not verified company events.</p>{flash_html}'
                  f'<h2>Jin10 calendar source reports · {len(calendar)}</h2>{calendar_html}')
    page = page.replace('</section></main>', supplement + '</section></main>', 1)
    page = page.replace('</style>', '.collector{margin:14px 18px;padding:12px 16px;border:1px solid #b9d0e1;background:#eaf3fa;border-radius:8px}.collector p{margin:5px 0}</style>', 1)
    page = page.replace('Technology earnings + macro development panel', 'Technology earnings + macro + Jin10 local panel', 1)
    return page


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-db", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    output = args.out.resolve()
    if output.exists():
        raise FileExistsError(output)
    feed = None
    if args.private_db is not None:
        database = args.private_db.resolve()
        if not database.is_relative_to(PRIVATE_ROOT) or not output.is_relative_to(PRIVATE_ROOT):
            raise ValueError("Private Jin10 content and HTML must stay in ignored artifacts")
        if not database.is_file():
            raise FileNotFoundError(database)
        feed = export_display(database, limit=50, include_private_content=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_v2(feed), encoding="utf-8", newline="\n")
    records = feed["records"] if feed else []
    flash_records = [row for row in records if "flash" in str(row.get("tool_name", "")).lower()]
    related_flash = [row for row in flash_records if row.get("issuer_candidates") or
                     row.get("event_category_candidate") not in (None, "UNCLASSIFIED")]
    calendar_records = [row for row in records if "calendar" in str(row.get("tool_name", "")).lower()]
    print(json.dumps({"output": output.relative_to(ROOT).as_posix() if output.is_relative_to(ROOT) else "local",
                      "jin10_records_in_display_export": len(records),
                      "related_flash_candidates_shown": len(related_flash),
                      "calendar_rows_shown": len(calendar_records),
                      "private_provider_text_included": bool(feed and feed.get("raw_provider_text_included"))}))


if __name__ == "__main__":
    main()
