"""Build local English review pages from fixed, checked M8 10-Q evidence.

The builder does not download filings, change source files, or record a human
decision. Each output directory must be new so old review versions remain intact.
"""

from __future__ import annotations

import argparse
import hashlib
from html import escape as html_escape
import json
from pathlib import Path
import re

from scripts.m7_build_10q_review import STYLE, safe_preview
from scripts.m8_build_review import (
    FIXED, anomalies, checked_inputs, display_json, esc, need, sha,
)

VERSION = "M9-English-review-v4"
ROOT = Path(__file__).resolve().parents[1]
BASE_STYLE = STYLE + """
.table-scroll{max-width:100%;overflow:auto;max-height:580px}
.table-scroll table{border-collapse:collapse;font-size:13px;min-width:max-content}
.table-scroll td,.table-scroll th{border:1px solid #9ba8ba;padding:5px;vertical-align:top}
.wide-pre{max-height:760px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere}
.overview-cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.firm-card{padding:16px;border:1px solid #9ba8ba;border-radius:8px;min-width:0;overflow-wrap:anywhere}
.firm-card dl{display:grid;grid-template-columns:minmax(135px,35%) minmax(0,1fr);gap:8px}
.firm-card dt{font-weight:700}.firm-card dd{margin:0;min-width:0;overflow-wrap:anywhere}
@media(max-width:760px){.overview-cards{grid-template-columns:1fr}.firm-card dl{grid-template-columns:1fr}.grid{grid-template-columns:minmax(0,1fr)}}
@media print{.table-scroll,.wide-pre{max-height:none;overflow:visible}}
"""


def page_start(title: str, *, form: bool = False) -> str:
    script = "; script-src 'unsafe-inline'" if form else ""
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; "
            f"style-src 'unsafe-inline'{script}; connect-src 'none'\">"
            f"<title>{esc(title)}</title><style>{BASE_STYLE}</style></head><body>")


def source_anchor_preview(html: str, anchor: dict | None) -> str:
    if not isinstance(anchor, dict):
        return "<p>No matching source HTML heading anchor was recorded.</p>"
    start = anchor.get("html_source_char_start")
    if not isinstance(start, int) or not 0 <= start < len(html):
        return "<p>The source HTML heading anchor is invalid and needs review.</p>"
    tail = html[start:start + 3000]
    end = tail.lower().find("</div>")
    fragment = tail[:end + 6] if end >= 0 else tail
    return "<div class='source-preview'>" + safe_preview(fragment) + "</div>"


def source_preceding_preview(html: str, anchor: dict | None) -> str:
    """Show bounded source HTML before a heading without equating its offsets to normalized text."""
    if not isinstance(anchor, dict):
        return "<p>No source HTML heading anchor was recorded.</p>"
    end = anchor.get("html_source_char_start")
    if not isinstance(end, int) or not 0 < end <= len(html):
        return "<p>The source HTML heading anchor is invalid.</p>"
    search_start = max(0, end - 6000)
    opens = [match.start() + search_start for match in
             re.finditer(r"<p(?=[\s>])", html[search_start:end], re.IGNORECASE)]
    start = opens[-5] if len(opens) >= 5 else search_start
    fragment = html[start:end]
    fragment_sha = hashlib.sha256(fragment.encode("utf-8")).hexdigest()
    return (f"<p><small>Saved source HTML character span {start}–{end} (end exclusive); "
            f"UTF-8 fragment SHA-256: <code>{fragment_sha}</code>. This is a bounded source excerpt.</small></p>"
            "<div class='source-preview'>" + safe_preview(fragment) + "</div>")


def meta_rows(rows: list[tuple[str, object]]) -> str:
    return "".join(f"<tr><th>{esc(name)}</th><td><code>{esc(value)}</code></td></tr>"
                   for name, value in rows)


def render_guide_markdown(source: str) -> str:
    """Render the restricted, checked local guide syntax without external resources."""
    def inline(value: str) -> str:
        value = html_escape(value)
        value = re.sub(r"`([^`]+)`", r"<code>\1</code>", value)
        value = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", value)
        return value

    result: list[str] = []
    paragraph: list[str] = []
    mode: str | None = None
    table_header = False

    def flush_paragraph() -> None:
        if paragraph:
            result.append("<p>" + inline(" ".join(paragraph)) + "</p>")
            paragraph.clear()

    def close_mode() -> None:
        nonlocal mode, table_header
        if mode:
            result.append(f"</{mode}>")
            mode = None
            table_header = False

    for raw_line in source.splitlines():
        line = raw_line.strip()
        if not line:
            flush_paragraph()
            close_mode()
            continue
        heading = re.fullmatch(r"(#{1,3})\s+(.+)", line)
        if heading:
            flush_paragraph()
            close_mode()
            level = len(heading.group(1))
            result.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
            continue
        if line.startswith("|") and line.endswith("|"):
            flush_paragraph()
            if mode != "table":
                close_mode()
                result.append("<table class='meta'>")
                mode = "table"
                table_header = True
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if all(re.fullmatch(r"-+", cell) for cell in cells):
                table_header = False
                continue
            tag = "th" if table_header else "td"
            result.append("<tr>" + "".join(f"<{tag}>{inline(cell)}</{tag}>" for cell in cells) + "</tr>")
            continue
        ordered = re.fullmatch(r"\d+\.\s+(.+)", line)
        bullet = re.fullmatch(r"-\s+(.+)", line)
        if ordered or bullet:
            flush_paragraph()
            intended = "ol" if ordered else "ul"
            if mode != intended:
                close_mode()
                result.append(f"<{intended}>")
                mode = intended
            result.append(f"<li>{inline((ordered or bullet).group(1))}</li>")
            continue
        close_mode()
        paragraph.append(line)
    flush_paragraph()
    close_mode()
    return "".join(result)


def build_browser_guidance(output: Path, guide_source: Path) -> dict:
    """Save browser-readable instructions and guide beside the review entry page."""
    guide_text = guide_source.read_text(encoding="utf-8")
    guide_html = (page_start("Four-company Form 10-Q MD&A researcher review guide") +
                  "<nav><a href='index.html'>Four-company review entry</a> · "
                  "<a href='START_HERE.html'>Opening instructions</a></nav>" +
                  render_guide_markdown(guide_text) + "</body></html>")
    opening_html = (page_start("Open the English Form 10-Q MD&A review packet") +
                    "<h1>Open the four-company English review packet</h1>"
                    "<p>This local delivery covers the four fixed Microsoft, Amazon, Alphabet, and Meta "
                    "Form 10-Q Part I Item 2 Management's Discussion and Analysis sections. "
                    "The saved M8 source and extraction files remain the evidence.</p>"
                    "<ol><li>Open the <a href='index.html'>four-company review entry</a> and check "
                    "each filing identity, source hash, extraction status, and pending researcher status.</li>"
                    "<li>Begin with <a href='MSFT/index.html#start'>Microsoft's Item 2 beginning</a>. "
                    "Compare the source HTML, preceding normalized text, table-of-contents candidate, "
                    "and extracted text. Give an explicit ACCEPT, REVISE, or REJECT for this one item.</li>"
                    "<li>Use the company's blank version-bound review form only for your own decisions. "
                    "Do not prefill reviewer identity, time, or an overall approval.</li>"
                    "<li>Read the <a href='M9_ENGLISH_REVIEW_GUIDE.html'>full English review guide</a> "
                    "for sequence, source and period cautions, and record handling.</li></ol>"
                    "<p>Opening a page or importing an empty software-test record is not researcher acceptance. "
                    "No batch representation analysis or formal financial experiment was run for this delivery.</p>"
                    "</body></html>")
    guide_path = output / "M9_ENGLISH_REVIEW_GUIDE.html"
    opening_path = output / "START_HERE.html"
    guide_path.write_text(guide_html, encoding="utf-8")
    opening_path.write_text(opening_html, encoding="utf-8")
    (output / "M9_ENGLISH_REVIEW_GUIDE.md").write_text(guide_text, encoding="utf-8")
    return {"guide_source_path": str(guide_source), "guide_source_sha256": sha(guide_source),
            "guide_html_sha256": sha(guide_path), "opening_html_sha256": sha(opening_path)}


def make_page(data: dict, index: dict) -> str:
    identity, boundary = data["identity"], data["boundary"]
    ticker, section = identity["ticker"], data["section"]
    selected = boundary.get("selected_span") or {}
    anchors = boundary.get("html_heading_anchors") or {}
    hashes = index["source_hashes"]
    meta = [
        ("Company", identity.get("company")), ("Ticker / Central Index Key", f"{ticker} / {identity['cik']}"),
        ("Accession / form", f"{identity['accession']} / {identity['form']}"),
        ("Report period end / filing date", f"{identity.get('report_period_end')} / {identity.get('filing_date')}"),
        ("Primary document", identity.get("primary_document")),
        ("Complete submission SHA-256", hashes["original_submission_sha256"]),
        ("Source HTML SHA-256", hashes["primary_document_sha256"]),
        ("Normalized primary text SHA-256", hashes["primary_text_sha256"]),
        ("Extracted Item 2 SHA-256", hashes["section_sha256"]),
        ("Complete submission URL", identity.get("original_submission_url")),
        ("Primary document URL", identity.get("primary_document_url")),
        ("Prior standalone HTML SHA-256", (data["version_adjudication"] or {}).get("prior_standalone_primary_sha256")),
        ("Version adjudication", (data["version_adjudication"] or {}).get("adjudication_status")),
        ("Historical snapshot equivalence", (data["version_adjudication"] or {}).get("historical_snapshot_equivalence")),
        ("Version difference assessment", (data["version_adjudication"] or {}).get("interpretation")),
    ]
    parts = [page_start(f"{ticker} 10-Q Item 2 Management's Discussion and Analysis review"),
             f"<h1>{esc(ticker)}: Form 10-Q, Part I Item 2 Management's Discussion and Analysis (MD&amp;A)</h1>",
             "<p class='warn'>Researcher status: <strong>PENDING_HUMAN_REVIEW</strong>. "
             "A programmatic extraction and this page are not researcher acceptance. "
             f"Boundary status: {esc(boundary['status'])}. "
             "Use the <a href='review_form.html'>blank review form</a> after checking each item.</p>",
             "<nav><a href='../index.html'>Four-company entry</a> · <a href='#identity'>Identity</a> · "
             "<a href='#start'>Beginning</a> · <a href='#end'>Ending</a> · <a href='#tables'>Tables</a> · "
             "<a href='#periods'>Periods</a> · <a href='#anomalies'>Exceptions</a> · "
             "<a href='#full-text'>Full extracted text</a></nav>",
             "<section id='identity'><h2>Filing identity and source versions</h2><table class='meta'>",
             meta_rows(meta),
             "</table><p class='warn'>Normalized primary text offsets count Python Unicode characters. "
             "Source HTML and complete-submission offsets count bytes. These coordinate systems are not interchangeable. "
             "HTML heading anchors locate source material; they are not normalized section boundaries.</p></section>",
             "<section id='start'><h2>Item 2 beginning and table-of-contents candidates</h2>",
             "<table class='meta'><tr><th>Candidate</th><th>Classification</th><th>Normalized text character range</th><th>Heading</th></tr>"]
    for number, candidate in enumerate(boundary["candidates"]):
        parts.append(f"<tr><td>{number}</td><td>{esc(candidate.get('classification'))}</td>"
                     f"<td>{esc(candidate.get('start_char'))}–{esc(candidate.get('end_char_exclusive'))}</td>"
                     f"<td>{esc(candidate.get('heading'))}</td></tr>")
    a = selected.get("start_char")
    parts += ["</table><div class='grid'><div class='panel'><h3>Sanitized source HTML near the selected heading</h3>",
              source_anchor_preview(data["html"], anchors.get("selected_item2")),
              "<details><summary>Preceding saved source HTML excerpt</summary>",
              source_preceding_preview(data["html"], anchors.get("selected_item2")),
              "</details>",
              "<p><small>This bounded visual preview comes from saved HTML; compare its context with the complete filing.</small></p></div>",
              "<div class='panel'><h3>Corresponding normalized primary text and preceding context</h3><pre>",
              esc(data["primary"][max(0, a - 450):a + 750] if isinstance(a, int) else
                  "No unique Item 2 beginning was selected."),
              "</pre></div><div class='panel'><h3>Actual extracted Item 2 beginning</h3><pre>",
              esc(section[:900] if section else "No unique extraction was generated; NEEDS_REVIEW."),
              "</pre></div></div></section>"]
    b = selected.get("end_char_exclusive")
    parts += ["<section id='end'><h2>Item 2 ending and Item 3 boundary</h2><div class='grid'>",
              "<div class='panel'><h3>Extracted Item 2 ending</h3><pre>",
              esc(section[-900:] if section else "No unique extraction was generated."),
              "</pre></div><div class='panel'><h3>Following normalized primary text</h3><pre>",
              esc(data["primary"][max(0, b - 400):b + 750] if isinstance(b, int) else
                  "No ending boundary was selected."),
              "</pre></div><div class='panel'><h3>Sanitized source HTML near the following heading</h3>",
              source_anchor_preview(data["html"], anchors.get("selected_item3")),
              "<details><summary>Saved source HTML before the following heading</summary>",
              source_preceding_preview(data["html"], anchors.get("selected_item3")),
              "</details>",
              "</div></div></section>",
              f"<section id='tables'><h2>Literal HTML table elements inside Item 2: {len(data['tables'])}</h2>",
              "<p>This count is not a count of semantic tables. Each source table has a sanitized visual preview "
              "alongside its exact programmatically flattened text. Source scripts, external resources, and event handlers "
              "are not executed. The researcher must check dates, units, negative signs, percentages, notes, and row/column meaning.</p>"]
    if data["boundary_layout_tables"]:
        parts.append("<h3>Heading and boundary layout tables (excluded from substantive table count)</h3>")
        for row in data["boundary_layout_tables"]:
            x, y = row["html_source_char_span"]
            label = f"BOUNDARY-{row['source_table_index']}"
            parts += [f"<details class='card'><summary>{esc(label)} · {esc(', '.join(row['roles']))} · "
                      f"{esc(row['review_status'])}</summary>",
                      "<p>This source table encloses an Item 2 or Item 3 heading. Check the neighboring section boundary. "
                      "It is not counted as an Item 2 substantive data table.</p>",
                      "<div class='table-scroll'>", safe_preview(data["html"][x:y]), "</div>",
                      "<p>Recorded normalized text excerpt, possibly truncated:</p><pre>", esc(row["flattened_excerpt"]),
                      "</pre><pre>", display_json({key: row.get(key) for key in
                          ("html_source_char_span", "complete_submission_byte_span", "raw_html_sha256")}),
                      "</pre></details>"]
    for row in data["tables"]:
        h0, h1 = row["html_source_byte_start"], row["html_source_byte_end_exclusive"]
        source_table = data["html"].encode("utf-8")[h0:h1].decode("utf-8")
        locations = {
            "source_table_index": row.get("source_table_index"),
            "source_html_byte_span": [h0, h1],
            "complete_submission_byte_span": [row["complete_submission_byte_start"],
                                              row["complete_submission_byte_end_exclusive"]],
            "extracted_section_unicode_char_span": [row["flattened_section_char_start"],
                                                   row["flattened_section_char_end_exclusive"]],
            "source_html_fragment_sha256": row["raw_html_fragment_sha256"],
            "flattened_text_sha256": row["flattened_text_sha256"],
        }
        clues = {key: row.get(key) for key in
                 ("source_caption_context", "caption_candidate", "period_hints", "unit_hints",
                  "unit_evidence", "footnote_hints", "footnote_evidence",
                  "following_source_context", "adjacent_text_note_candidates")}
        parts += [f"<details class='card' id='{esc(row['table_id'])}'><summary>{esc(row['table_id'])} · "
                  f"source table #{esc(row.get('source_table_index'))} · {esc(row.get('review_status'))}</summary>",
                  "<div class='grid'><div class='panel'><h3>Source HTML rows and columns</h3>",
                  "<div class='table-scroll'>", safe_preview(source_table), "</div></div>",
                  "<div class='panel'><h3>Exact flattened text</h3><pre>", esc(row["flattened_text"]),
                  "</pre></div></div><p>Preceding context:</p><pre>", esc(row.get("preceding_context", "")),
                  "</pre><p>Source caption, period, unit, and note clues (program records only; interpretation and completeness "
                  "remain unconfirmed):</p><pre>", display_json(clues), "</pre>"]
        if row["adjacent_text_note_candidates"]:
            f0, f1 = row["following_source_context"]["html_source_char_span"]
            parts += ["<p class='warn'>The text after this table may include a note. The program has not proved that it belongs "
                      "to this table; compare the location and source wording.</p>",
                      "<div class='grid'><div class='panel'><h3>Sanitized source HTML after table</h3>",
                      "<div class='source-preview'>", safe_preview(data["html"][f0:f1]), "</div></div>",
                      "<div class='panel'><h3>Adjacent normalized text candidates</h3><pre>",
                      display_json(row["adjacent_text_note_candidates"]), "</pre></div></div>"]
        parts += ["<p>Source coordinates and hashes:</p><pre>", display_json(locations), "</pre></details>"]
    if not data["tables"]:
        parts.append("<p class='warn'>No literal HTML table was indexed in the selected section, or the boundary is "
                     "not selected. This does not establish that the disclosure contains no semantic table.</p>")
    if data["numeric_layout_candidates"]:
        parts.append("<h3>Numeric layout candidates outside table elements</h3><p>These are not confirmed "
                     "semantic tables. Compare each with the source layout.</p>")
        for number, row in enumerate(data["numeric_layout_candidates"], 1):
            x, y = row["html_source_char_span"]
            parts += [f"<details class='card'><summary>LAYOUT-{number} · {esc(row.get('classification'))}</summary>",
                      "<div class='table-scroll'>", safe_preview(data["html"][x:y]), "</div><pre>",
                      esc(row.get("text_excerpt")), "</pre><pre>",
                      display_json({key: row.get(key) for key in ("html_source_char_span", "source_html_sha256")}),
                      "</pre></details>"]
    periods = data["periods"]
    parts += ["</section><section id='periods'><h2>Narrative and table reporting periods</h2>",
              "<p class='warn'>The day after a previous filing's period end is only a proxy, not a verified start "
              "for the whole chapter. Quarterly, year-to-date, trailing, annual-reference, and point-in-time "
              "references may coexist. Unsupported values remain null. These are program-detected clues, "
              "not researcher-confirmed assignments.</p><table class='meta'>"]
    for label, key in (("Period clue status", "status"), ("Reported fiscal year", "report_fiscal_year"),
                       ("Reported fiscal quarter", "report_fiscal_quarter"),
                       ("Report period end", "report_period_end"), ("Whole-chapter period", "chapter_period"),
                       ("Reason whole-chapter period is undefined", "chapter_period_reason"),
                       ("Prior-period-end-plus-one-day proxy", "coverage_start_proxy")):
        parts.append(f"<tr><th>{esc(label)}</th><td>{esc(periods.get(key))}</td></tr>")
    parts.append("</table>")
    for label, key in (("Narrative period clues", "narrative_periods"),
                       ("Table period clues", "table_periods"), ("Other period clues", "other_periods")):
        records = periods.get(key) or []
        parts += [f"<details class='card'><summary>{esc(label)}: {len(records)} records "
                  "(source spans count Unicode characters in the extracted section)</summary>",
                  "<table class='meta'><tr><th>Source location / table identifier</th><th>Source wording and dates</th>"
                  "<th>Status and complete record</th></tr>"]
        for record in records:
            loc = (str(record.get("table_id")) if record.get("table_id") is not None else
                   f"{record.get('source_start_char')}–{record.get('source_end_char_exclusive')}")
            phrase = record.get("source_excerpt") or record.get("phrase") or record.get("title") or ""
            parts.append(f"<tr><td>{esc(loc)}</td><td>{esc(str(phrase)[:350])}</td>"
                         f"<td><details><summary>{esc(record.get('status'))}</summary><pre>"
                         f"{display_json(record)}</pre></details></td></tr>")
        parts.append("</table></details>")
    parts += ["</section><section id='anomalies'><h2>Exceptions and researcher checks</h2>",
              "<table class='meta'><tr><th>Category</th><th>Evidence clue</th><th>Status</th></tr>"]
    for item in index["anomalies"]:
        parts.append(f"<tr><td>{esc(item['type'])}</td><td>{esc(item['evidence'])}</td>"
                     f"<td>{esc(item['status'])}</td></tr>")
    parts += ["</table><p>Record explicit item-level decisions in the <a href='review_form.html'>blank review form</a>. "
              "Program passage, opening a page, or saying continue is not acceptance.</p></section>",
              "<details class='card' id='full-text'><summary>Full exact extracted Item 2 text</summary>",
              "<p>This text is the exact saved normalized section slice. It is not a substitute for reviewing "
              "the source layout and tables.</p><pre class='wide-pre'>", esc(section or "No unique section extraction exists."),
              "</pre></details></body></html>"]
    return "".join(parts)


FORM_JS = r"""
<script>
'use strict';
const fixed = __FIXED__;
const checkIds = __CHECK_IDS__;
const fieldNames = [...checkIds,'issues','reviewer','review_time','decision'];
const required = [...checkIds,'reviewer','review_time','decision'];
let previousRecord = null;
function statusFor(fields) {
  return required.every(key => String(fields[key] || '').trim())
    ? 'HUMAN_DECISION_RECORDED' : 'PENDING_HUMAN_REVIEW';
}
function gateFor(fields) {
  if (fixed.program_boundary_status !== 'PROGRAM_BOUNDARY_PASS') return 'NOT_MET';
  return statusFor(fields) === 'HUMAN_DECISION_RECORDED' &&
    fields.decision === 'ACCEPT' && checkIds.every(key => fields[key] === 'ACCEPT')
    ? 'ELIGIBLE_FOR_SEPARATE_PROJECT_VALIDATION' : 'NOT_MET';
}
function validateRecord(record) {
  if (!record || record.schema_version !== 5 || !record.file_and_section || !record.fields) {
    throw Error('This is not an M9 schema-version-5 review record.');
  }
  for (const [key, value] of Object.entries(fixed)) {
    if (record.file_and_section[key] !== value) throw Error('Filing or page version mismatch: ' + key);
  }
  const fields = record.fields;
  if (Object.keys(fields).sort().join('|') !== [...fieldNames].sort().join('|')) {
    throw Error('Review field set does not match this form.');
  }
  for (const key of checkIds) {
    if (!['','ACCEPT','REVISE','REJECT'].includes(fields[key])) throw Error('Invalid item decision: ' + key);
  }
  if (!['','ACCEPT','REVISE','REJECT'].includes(fields.decision)) throw Error('Invalid overall decision.');
  for (const key of ['issues','reviewer','review_time']) {
    if (typeof fields[key] !== 'string') throw Error('Invalid text field: ' + key);
  }
  if (fields.issues.length > 10000 || fields.reviewer.length > 200) throw Error('A text field is too long.');
  if (fields.review_time && Number.isNaN(Date.parse(fields.review_time))) throw Error('Invalid review time.');
  if (record.status !== statusFor(fields) || record.acceptance_gate !== gateFor(fields)) {
    throw Error('Recorded status or gate disagrees with item assessments.');
  }
  if (typeof record.exported_at_utc !== 'string' || Number.isNaN(Date.parse(record.exported_at_utc))) {
    throw Error('Export time is missing or invalid.');
  }
  return fields;
}
document.getElementById('save').addEventListener('click', () => {
  const fields = Object.fromEntries(new FormData(document.getElementById('review')).entries());
  const now = new Date();
  const payload = {
    schema_version: 5, file_and_section: fixed, fields,
    status: statusFor(fields), acceptance_gate: gateFor(fields),
    reviewer_entered_local_time: fields.review_time || null,
    browser_time_zone: Intl.DateTimeFormat().resolvedOptions().timeZone || null,
    exported_at_utc: now.toISOString(), previous_record: previousRecord
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], {type: 'application/json'});
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = __EXPORT_PREFIX__ + now.toISOString().replace(/[:.]/g, '-') + '.json';
  document.body.appendChild(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
  document.getElementById('save-status').textContent =
    'Download requested: ' + link.download + '. Confirm the file in Downloads, then import it below. Project acceptance is unchanged.';
});
document.getElementById('load').addEventListener('click', async () => {
  const message = document.getElementById('load-status');
  const selected = document.getElementById('import-file').files[0];
  if (!selected) { message.textContent = 'Select an exported JSON file first.'; return; }
  if (selected.size > 250000) { message.textContent = 'The file exceeds 250 KB and was rejected.'; return; }
  try {
    const record = JSON.parse(await selected.text());
    const fields = validateRecord(record);
    const form = document.getElementById('review');
    for (const key of fieldNames) form.elements.namedItem(key).value = fields[key];
    previousRecord = record;
    message.textContent = 'Source, section, and page versions matched. Fields were reloaded; project acceptance was not changed.';
  } catch (error) {
    message.textContent = 'Import rejected: ' + String(error.message || error);
  }
});
</script>
"""


def make_form(fixed: dict, table_ids: list[str], ticker: str) -> str:
    checks = ["start", "end", "body", *table_ids]
    parts = [page_start(f"{ticker} Management's Discussion and Analysis review form", form=True),
             f"<h1>{esc(ticker)} researcher review form</h1>",
             "<p class='warn'>Initial state: <strong>PENDING_HUMAN_REVIEW</strong>. "
             "Reviewer, review time, and decisions are blank. First inspect the "
             "<a href='index.html'>evidence page</a>. Export and import run only in this local browser.</p>",
             "<div class='card'><h2>Bound filing and page versions</h2><table class='meta'>",
             meta_rows(list(fixed.items())),
             "</table></div><form id='review'><div class='card'><h2>Item-level assessments</h2>"]
    labels = {
        "start": "Item 2 beginning and exclusion of table-of-contents headings",
        "end": "Item 2 ending and Item 3 boundary",
        "body": "Body completeness, page breaks, cross-references, and exceptions",
    }
    for key in checks:
        if key == "PERIODS":
            label = "Narrative, table, and other reporting periods"
        elif key.startswith("BOUNDARY-"):
            label = f"{key}: heading layout table and section boundary"
        elif key.startswith("LAYOUT-"):
            label = f"{key}: numeric layout and body-text mapping"
        else:
            label = labels.get(key, f"{key}: rows, columns, dates, units, negative signs, percentages, and notes")
        parts += [f"<label>{esc(label)}<select name='{esc(key)}'>",
                  "<option value='' selected>— Not assessed —</option>",
                  "<option value='ACCEPT'>ACCEPT</option><option value='REVISE'>REVISE</option>",
                  "<option value='REJECT'>REJECT</option></select></label>"]
    parts += ["</div><div class='card'><h2>Comments and researcher identity (researcher enters these)</h2>",
              "<label>Exceptions and proposed handling<textarea name='issues'></textarea></label>",
              "<label>Reviewer<input name='reviewer' autocomplete='off'></label>",
              "<label>Review time<input name='review_time' type='datetime-local'></label>",
              "<label>Overall decision<select name='decision'><option value='' selected>— No decision —</option>",
              "<option value='ACCEPT'>ACCEPT</option><option value='REVISE'>REVISE</option>",
              "<option value='REJECT'>REJECT</option></select></label>",
              "<button type='button' id='save'>Download version-bound JSON</button>",
              "<label>Import previously exported JSON<input id='import-file' type='file' "
              "accept='.json,application/json'></label>",
              "<button type='button' id='load'>Validate and reload JSON</button>",
              "<p id='save-status' aria-live='polite'></p><p id='load-status' aria-live='polite'></p>",
              "<p><small>Export does not change project acceptance. Browser export/import requires separate testing. "
              "Completing this form cannot bypass a failed program boundary check.</small></p></div></form>"]
    script = (FORM_JS.replace("__FIXED__", json.dumps(fixed, ensure_ascii=True, sort_keys=True))
             .replace("__CHECK_IDS__", json.dumps(checks, ensure_ascii=True))
             .replace("__EXPORT_PREFIX__", json.dumps(f"{ticker}_10Q_MDA_review_M9_")))
    return "".join(parts) + script + "</body></html>"


def build_packet(ingest: Path, extraction: Path, output: Path) -> dict:
    need(not output.exists(), "Review packet path exists; choose a new version")
    data = checked_inputs(ingest, extraction)
    identity, boundary = data["identity"], data["boundary"]
    ticker = identity["ticker"]
    section_sha = sha(extraction / "item2_candidate.txt") if data["section"] is not None else None
    additional_ids = ["PERIODS"]
    additional_ids += [f"BOUNDARY-{row['source_table_index']}" for row in data["boundary_layout_tables"]]
    additional_ids += [f"LAYOUT-{number}" for number, _ in enumerate(data["numeric_layout_candidates"], 1)]
    index = {
        "kind": "M9_ENGLISH_10Q_MDA_REVIEW_INDEX",
        "status": "PENDING_HUMAN_REVIEW",
        "program_boundary_status": boundary["status"],
        "identity": {key: identity.get(key) for key in
                     ("ticker", "company", "cik", "accession", "form", "report_period_end",
                      "filing_date", "primary_document", "original_submission_url", "primary_document_url")},
        "source_hashes": {**data["hashes"], "section_sha256": section_sha,
                          "boundary_candidates_sha256": sha(extraction / "boundary_candidates.json"),
                          "table_index_sha256": sha(extraction / "table_index.json"),
                          "period_ledger_sha256": sha(extraction / "period_ledger.json")},
        "version_adjudication": data["version_adjudication"],
        "coordinates": boundary["coordinates"],
        "selected_span": boundary.get("selected_span"),
        "table_count": len(data["tables"]),
        "table_count_meaning": "literal HTML table elements in the selected Item 2 source span",
        "boundary_layout_table_count": len(data["boundary_layout_tables"]),
        "numeric_layout_candidate_count": len(data["numeric_layout_candidates"]),
        "table_ids": [row["table_id"] for row in data["tables"]],
        "additional_review_item_ids": additional_ids,
        "period_status": data["periods"].get("status"),
        "anomalies": anomalies(boundary, data["section"], data["tables"],
                               data["table_index"], identity, data["version_adjudication"]),
        "human_reviewer": None, "human_review_time": None, "human_decision": None,
        "browser_render_status": "NOT_VERIFIED", "browser_export_import_status": "NOT_VERIFIED",
    }
    output.mkdir(parents=True)
    (output / "index.html").write_text(make_page(data, index), encoding="utf-8")
    page_sha = sha(output / "index.html")
    fixed = {
        "company": identity.get("company"), "cik": identity["cik"], "accession": identity["accession"],
        "form": identity["form"], "report_period_end": identity.get("report_period_end"),
        "filing_date": identity.get("filing_date"),
        "raw_sha256": data["hashes"]["original_submission_sha256"],
        "html_sha256": data["hashes"]["primary_document_sha256"],
        "primary_text_sha256": data["hashes"]["primary_text_sha256"],
        "section_sha256": section_sha, "review_page_sha256": page_sha,
        "review_packet_version": VERSION, "program_boundary_status": boundary["status"],
    }
    (output / "review_form.html").write_text(
        make_form(fixed, index["table_ids"] + additional_ids, ticker), encoding="utf-8")
    (output / "review_index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    checklist = [f"# {ticker} Form 10-Q Item 2 MD&A Review Checklist", "",
                 "Status: PENDING_HUMAN_REVIEW", "",
                 f"Complete submission SHA-256: `{fixed['raw_sha256']}`",
                 f"Extracted section SHA-256: `{section_sha}`",
                 f"Review page SHA-256: `{page_sha}`", "",
                 "Beginning: [ ] ACCEPT [ ] REVISE [ ] REJECT", "",
                 "Ending: [ ] ACCEPT [ ] REVISE [ ] REJECT", ""]
    checklist += [f"{item}: [ ] ACCEPT [ ] REVISE [ ] REJECT\n" for item in
                  index["table_ids"] + additional_ids]
    checklist += ["Body and exceptions: [ ] ACCEPT [ ] REVISE [ ] REJECT", "",
                  "Reviewer: ", "", "Review time: ", "", "Comments and source locations: ", "",
                  "Overall decision: ", ""]
    (output / "review_form.md").write_text("\n".join(checklist), encoding="utf-8")
    (output / "START_HERE.md").write_text(
        f"# {ticker} English Review Handoff\n\n"
        "Open `index.html` and inspect filing identity, beginning, ending, all source and flattened tables, "
        "reporting periods, exceptions, and full extracted text. Enter explicit assessments in the blank "
        "`review_form.html`. Export JSON, then import that file to verify it. The form binds the exact "
        "source, section, and page versions. Opening a page or exporting an empty software-test record "
        "does not constitute researcher acceptance. No network requests are made by this packet.\n",
        encoding="utf-8")
    files = [{"path": path.name, "bytes": path.stat().st_size, "sha256": sha(path)}
             for path in sorted(output.iterdir()) if path.is_file()]
    receipt = {
        "kind": "M9_LOCAL_ENGLISH_10Q_MDA_REVIEW_PACKET", "ticker": ticker,
        "status": "PENDING_HUMAN_REVIEW", "program_boundary_status": boundary["status"],
        "raw_sha256": fixed["raw_sha256"], "section_sha256": section_sha,
        "review_page_sha256": page_sha, "form_version": VERSION,
        "version_adjudication": data["version_adjudication"],
        "source_code_sha256": sha(Path(__file__)),
        "inputs_rehashed": {**data["hashes"],
                          "source_identity_sha256": sha(ingest / "source_identity.json"),
                          "extraction_receipt_sha256": sha(extraction / "extraction_receipt.json")},
        "outputs": files, "table_count": len(data["tables"]),
        "boundary_layout_table_count": len(data["boundary_layout_tables"]),
        "human_reviewer": None, "human_review_time": None, "human_decision": None,
        "browser_render_status": "NOT_VERIFIED", "browser_export_import_status": "NOT_VERIFIED",
        "network_calls": "NONE",
    }
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return receipt


def build_overview(run_dir: Path, output: Path) -> dict:
    need(not output.exists(), "Overview exists; choose a new version")
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for ticker, (cik, accession) in FIXED.items():
        ingest = run_dir / "ingest" / ticker
        extraction = run_dir / "extraction_v4" / ticker
        packet = output.parent / ticker / "receipt.json"
        need(packet.is_file(), f"{ticker}: English packet is missing")
        item = json.loads(packet.read_text(encoding="utf-8"))
        index = json.loads((packet.parent / "review_index.json").read_text(encoding="utf-8"))
        need(item["raw_sha256"] == index["source_hashes"]["original_submission_sha256"] and
             item["section_sha256"] == index["source_hashes"]["section_sha256"] and
             sha(packet.parent / "index.html") == item["review_page_sha256"],
             f"{ticker}: packet and page source versions disagree")
        identity = json.loads((ingest / "source_identity.json").read_text(encoding="utf-8"))
        need(identity["cik"] == cik and identity["accession"] == accession,
             f"{ticker}: fixed candidate identity conflict")
        ex = json.loads((extraction / "extraction_receipt.json").read_text(encoding="utf-8"))
        rows.append({
            "ticker": ticker, "company": identity.get("company"), "cik": cik,
            "accession": accession, "form": identity.get("form"),
            "report_period_end": identity.get("report_period_end"),
            "filing_date": identity.get("filing_date"),
            "raw_sha256": item["raw_sha256"], "section_sha256": item["section_sha256"],
            "review_page_sha256": item["review_page_sha256"],
            "acquisition_status": identity.get("status"),
            "program_extraction_status": ex.get("status"),
            "substantive_html_table_count": item["table_count"],
            "heading_layout_table_count": item["boundary_layout_table_count"],
            "period_clue_status": index["period_status"],
            "researcher_review_status": "PENDING_HUMAN_REVIEW",
            "open_exceptions": [entry["type"] for entry in index["anomalies"]
                                if entry["status"] not in {"PROGRAM_CHECK_ONLY", "SOURCE_SCOPE_RECORDED"}],
            "review_page_href": f"{ticker}/index.html", "review_form_href": f"{ticker}/review_form.html",
        })
    overview = {
        "kind": "M9_FOUR_COMPANY_ENGLISH_REVIEW_OVERVIEW", "review_packet_version": VERSION,
        "source_run": str(run_dir), "rows": rows,
        "batch_representation": "NOT_RUN", "formal_financial_experiment": "NOT_RUN",
    }
    cards = []
    for row in rows:
        details = [
            ("Central Index Key / accession", f"{row['cik']} / {row['accession']}"),
            ("Form / period end / filing date", f"{row['form']} / {row['report_period_end']} / {row['filing_date']}"),
            ("Complete submission SHA-256", row["raw_sha256"]),
            ("Extracted section SHA-256", row["section_sha256"]),
            ("Review page SHA-256", row["review_page_sha256"]),
            ("Source acquisition", row["acquisition_status"]),
            ("Programmatic extraction", row["program_extraction_status"]),
            ("Substantive / heading-layout tables",
             f"{row['substantive_html_table_count']} / {row['heading_layout_table_count']}"),
            ("Period clues", row["period_clue_status"]),
            ("Researcher review", row["researcher_review_status"]),
            ("Open exception categories", ", ".join(row["open_exceptions"]) or "None recorded"),
        ]
        cards.append(f"<section class='firm-card'><h2>{esc(row['ticker'])}: {esc(row['company'])}</h2>"
                     f"<p><a href='{esc(row['review_page_href'])}'>Evidence page</a> · "
                     f"<a href='{esc(row['review_form_href'])}'>Blank review form</a></p><dl>" +
                     "".join(f"<dt>{esc(key)}</dt><dd>{esc(value)}</dd>" for key, value in details) +
                     "</dl></section>")
    html = (page_start("Four-company English 10-Q MD&A review entry") +
            "<h1>Four-company Form 10-Q Item 2 MD&A review</h1>"
            "<p class='warn'>These four accession numbers are fixed. Source acquisition and programmatic "
            "extraction are distinct from researcher acceptance. All four researcher decisions are pending. "
            "No batch representation analysis or formal financial experiment was run for this delivery.</p>"
            "<p><a href='START_HERE.html'>English opening instructions</a> · "
            "<a href='M9_ENGLISH_REVIEW_GUIDE.html'>English review guide</a></p>"
            "<div class='overview-cards'>" + "".join(cards) + "</div></body></html>")
    output.write_text(html, encoding="utf-8")
    metadata = output.parent / "overview.json"
    need(not metadata.exists(), "Overview metadata exists")
    metadata.write_text(json.dumps(overview, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    receipt = {
        "kind": "M9_ENGLISH_REVIEW_OVERVIEW_RECEIPT", "status": "PENDING_HUMAN_REVIEW",
        "page_sha256": sha(output), "overview_sha256": sha(metadata),
        "source_code_sha256": sha(Path(__file__)), "rows": len(rows), "network_calls": "NONE",
        "opening_html_sha256": sha(output.parent / "START_HERE.html"),
        "guide_html_sha256": sha(output.parent / "M9_ENGLISH_REVIEW_GUIDE.html"),
    }
    (output.parent / "overview_receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run_dir, output = args.run_dir.resolve(), args.output.resolve()
    need(not output.exists(), "Output directory exists; choose a new version")
    output.mkdir(parents=True)
    for ticker in FIXED:
        receipt = build_packet(run_dir / "ingest" / ticker,
                               run_dir / "extraction_v4" / ticker, output / ticker)
        print(json.dumps({"ticker": ticker, "status": receipt["status"],
                          "page_sha256": receipt["review_page_sha256"]}))
    guidance = build_browser_guidance(output, ROOT / "docs/M9_ENGLISH_REVIEW_GUIDE.md")
    overview = build_overview(run_dir, output / "index.html")
    (output / "START_HERE.md").write_text(
        "# English Review Delivery: Four Fixed Form 10-Q Item 2 MD&A Sections\n\n"
        "Open `index.html`. For each company, read the source and extraction versions, then open the "
        "evidence page and blank form. Begin researcher review with Microsoft's Item 2 opening. "
        "Make an explicit ACCEPT, REVISE, or REJECT decision for each review item. Do not treat opening "
        "a page, an empty export, or a program check as acceptance. Source filings remain in the saved M8 "
        "run directory; this delivery includes rendered excerpts and full extracted sections for local review. "
        "Do not distribute source text or researcher statements without a separate sharing check.\n",
        encoding="utf-8")
    print(json.dumps({"overview_status": overview["status"],
                      "overview_page_sha256": overview["page_sha256"],
                      "browser_guidance": guidance}))


if __name__ == "__main__":
    main()
