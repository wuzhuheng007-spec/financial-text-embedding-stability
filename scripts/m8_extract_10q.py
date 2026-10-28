"""Extract fixed four original 10-Q Item 2 candidates from verified saved SEC bytes.

This command makes no network request. Every program candidate and table remains
pending researcher review; it never claims a human decision or chapter period.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from html import escape
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

# Keep the existing M7 HTML table parser and safe preview rather than create a
# second table interpretation. The v4 packet and its fixed-AAPL builder stay read-only.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.m7_build_10q_review import FilingHTML, html_byte_offset, source_rendered

from finembed.extract import ExtractionError, primary_text
from finembed.m8_tenq import (boundary_candidates_generic, fiscal_focus,
                              page_and_character_anomalies, period_hints, unit_hints)
from finembed.qpilot import _primary_source, _check_primary_filename, _normalize
from finembed.sec import SecError, raw_filer_cik, raw_header


FIXED = {
    "MSFT": ("0000789019", "0000950170-25-010491"),
    "AMZN": ("0001018724", "0001018724-25-000036"),
    "GOOGL": ("0001652044", "0001652044-25-000043"),
    "META": ("0001326801", "0001326801-25-000054"),
}
PRIOR_NAME = {"MSFT": "msft-20241231.htm", "AMZN": "amzn-20250331.htm",
              "GOOGL": "goog-20250331.htm", "META": "meta-20250331.htm"}
PRIOR_SOURCE_ROOT = (Path(__file__).resolve().parents[1] /
                     "artifacts/m7_review/20261024T235855Z-closeout/candidates/run_v2/sources")


class NeedsVersionReview(Exception):
    """A newly retrieved official snapshot differs from the verified prior HTML."""


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def need(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


class TextNodeAnchors(HTMLParser):
    """Source offsets of text nodes headed Item 2/3; not rendered-text spans."""

    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.nodes: list[tuple[int, str]] = []
        self.line_starts = [0] + [m.end() for m in re.finditer("\n", html)]
        self.suppressed = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.suppressed += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.suppressed:
            self.suppressed -= 1

    def handle_data(self, value: str) -> None:
        if self.suppressed or not value.strip():
            return
        line, column = self.getpos()
        self.nodes.append((self.line_starts[line - 1] + column, _normalize(value)))


def heading_anchors(html: str, raw_html_start: int) -> dict:
    parser = TextNodeAnchors(html)
    parser.feed(html)
    parser.close()

    def collect(number: int, next_word: str) -> list[dict]:
        out: list[dict] = []
        for i, (start, text) in enumerate(parser.nodes):
            if not re.match(rf"^item\s*{number}\s*[.·:\-]?(?:\s|$|(?={next_word}))", text):
                continue
            joined = text + " " + (parser.nodes[i + 1][1] if i + 1 < len(parser.nodes) else "")
            if not re.match(rf"^item\s*{number}\s*[.·:\-]?\s*{next_word}", joined):
                continue
            byte_offset = html_byte_offset(html, start)
            out.append({"html_source_char_start": start,
                        "html_source_byte_start": byte_offset,
                        "complete_submission_byte_start": raw_html_start + byte_offset,
                        "source_context": html[max(0, start - 150):min(len(html), start + 420)],
                        "match_method": "HTMLParser visible text nodes, exact source start anchor; not a chapter span"})
        return out
    return {"item2_candidates": collect(2, "management"),
            "item3_candidates": collect(3, "quantitative"),
            "selected_item2": None, "selected_item3": None}


class AllTablesHTML(FilingHTML):
    """Reuse M7's cell parser, including tables outside direct-body div blocks.

    The M7 packet only indexes a table while a direct-body div is active.
    Microsoft places literal MD&A tables directly in the document flow, so
    relying on that condition omits them. A temporary active block permits
    exactly one otherwise unindexed table while preserving original offsets.
    Nested tables still fail closed in the inherited parser.
    """

    def __init__(self, source: str):
        super().__init__(source)
        self._synthetic_table_active = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table" and self.active is None:
            self.active = {"index": len(self.blocks), "start_char_in_html": self.source_offset(),
                           "end_char_exclusive_in_html": None, "text_parts": []}
            self.active_depth = -1
            self._synthetic_table_active = True
        super().handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        close_synthetic = tag == "table" and self._synthetic_table_active
        super().handle_endtag(tag)
        if close_synthetic:
            need(self.table is None, "Unclosed synthetic-source table")
            self.active = None
            self.active_depth = None
            self._synthetic_table_active = False


def _source_grid(table: dict) -> str:
    return '<table class="source-grid">' + "".join(
        "<tr>" + "".join(
            f'<{cell["tag"]} rowspan="{cell["rowspan"]}" colspan="{cell["colspan"]}">'
            f'{escape(cell["text"])}</{cell["tag"]}>' for cell in row) + "</tr>"
        for row in table["rows"]) + "</table>"


def _footnotes(fragment: str) -> list[dict]:
    out = []
    for match in re.finditer(r"<sup\b[^>]*>.*?</sup>", fragment, re.I | re.S):
        value = source_rendered(match.group(0))
        out.append({"html_fragment_char_span": [match.start(), match.end()],
                    "text": value, "raw_html_sha256": sha(match.group(0).encode("utf-8"))})
    return out


def _table_index(html: str, raw: bytes, section: str, raw_html_start: int,
                 start_html_char: int, end_html_char: int, identity: dict) -> dict:
    parsed = AllTablesHTML(html)
    parsed.feed(html)
    parsed.close()
    need(parsed.table is None and parsed.active is None, "Unclosed HTML table or body block")
    source_tables = [row for row in parsed.tables if
                     start_html_char <= row["start_char_in_html"] and
                     row["end_char_exclusive_in_html"] <= end_html_char]
    boundary_layout_tables = []
    for source in parsed.tables:
        a, b = source["start_char_in_html"], source["end_char_exclusive_in_html"]
        roles = (["CONTAINS_ITEM2_HEADING"] if a <= start_html_char < b else []) + (
                ["CONTAINS_ITEM3_HEADING"] if a <= end_html_char < b else [])
        if roles:
            a_byte, b_byte = html_byte_offset(html, a), html_byte_offset(html, b)
            raw_fragment = raw[raw_html_start + a_byte:raw_html_start + b_byte]
            need(raw_fragment == html[a:b].encode("utf-8"), "Boundary layout table differs from raw source")
            boundary_layout_tables.append({"source_table_index": source["index"],
                                           "roles": roles,
                                           "html_source_char_span": [a, b],
                                           "complete_submission_byte_span": [raw_html_start + a_byte,
                                                                             raw_html_start + b_byte],
                                           "raw_html_sha256": sha(raw_fragment),
                                           "flattened_excerpt": source_rendered(html[a:b])[:350],
                                           "review_status": "BOUNDARY_LAYOUT_TABLE_NEEDS_HUMAN_REVIEW"})
    tables: list[dict] = []
    for ordinal, source in enumerate(source_tables, 1):
        a, b = source["start_char_in_html"], source["end_char_exclusive_in_html"]
        fragment = html[a:b]
        a_byte, b_byte = html_byte_offset(html, a), html_byte_offset(html, b)
        raw_fragment = raw[raw_html_start + a_byte:raw_html_start + b_byte]
        need(raw_fragment == fragment.encode("utf-8"), "Table HTML differs from complete raw submission")
        flat = source_rendered(fragment)
        offset = section.find(flat) if flat else -1
        unique = bool(flat) and offset >= 0 and section.find(flat, offset + 1) < 0
        before = section[max(0, offset - 350):offset] if unique else None
        hints = []
        if unique:
            for hint in period_hints(before or "", offset=max(0, offset - 350)):
                hint["location_kind"] = "PRECEDING_SECTION_TEXT"
                hints.append(hint)
            for hint in period_hints(flat, offset=offset):
                hint["location_kind"] = "FLATTENED_TABLE_SECTION_TEXT"
                hints.append(hint)
        tables.append({
            "table_id": f"MDA-{ordinal}", "source_table_index": source["index"],
            "html_source_char_start": a, "html_source_char_end_exclusive": b,
            "html_source_byte_start": a_byte, "html_source_byte_end_exclusive": b_byte,
            "complete_submission_byte_start": raw_html_start + a_byte,
            "complete_submission_byte_end_exclusive": raw_html_start + b_byte,
            "raw_html_fragment_sha256": sha(raw_fragment),
            "flattened_section_char_start": offset if unique else None,
            "flattened_section_char_end_exclusive": offset + len(flat) if unique else None,
            "flattened_text_sha256": sha(flat.encode("utf-8")),
            "source_grid_html": _source_grid(source), "flattened_text": flat,
            "preceding_context": before,
            "title_hint": " ".join((before or "").splitlines()[-2:])[-180:] if before else None,
            "period_hints": hints,
            "unit_hints": unit_hints((before or "") + "\n" + flat),
            "footnote_hints": _footnotes(fragment),
            "row_count": len(source["rows"]),
            "source_cells_with_spans": sum(cell["colspan"] > 1 or cell["rowspan"] > 1
                                           for row in source["rows"] for cell in row),
            "review_status": "NEEDS_HUMAN_TABLE_REVIEW" if unique else
                             "NEEDS_REVIEW_FLATTENED_SPAN_NOT_UNIQUE",
        })
    previous_html_end = start_html_char
    previous_section_end = 0
    for row in tables:
        table_start = row["html_source_char_start"]
        context_start = max(previous_html_end, table_start - 4000)
        source_context = html[context_start:table_start]
        byte_start, byte_end = html_byte_offset(html, context_start), html_byte_offset(html, table_start)
        raw_context = raw[raw_html_start + byte_start:raw_html_start + byte_end]
        need(raw_context == source_context.encode("utf-8"), "Table heading context differs from raw source")
        row["source_caption_context"] = {
            "html_source_char_span": [context_start, table_start],
            "complete_submission_byte_span": [raw_html_start + byte_start, raw_html_start + byte_end],
            "raw_html_sha256": sha(raw_context),
            "rendered_excerpt": source_rendered(source_context)[-700:] if source_context else "",
            "context_truncated_at_4000_source_chars": context_start > previous_html_end,
            "status": "SOURCE_CONTEXT_ONLY_CAPTION_NOT_CONFIRMED" if source_context else
                      "NO_SEPARATE_SOURCE_CONTEXT"}
        offset = row["flattened_section_char_start"]
        if offset is not None:
            need(offset >= previous_section_end, "Mapped table order differs between HTML and normalized section")
            caption_start = max(previous_section_end, offset - 1200)
            caption_context = section[caption_start:offset]
            lines = [line.strip() for line in caption_context.splitlines() if line.strip()]
            candidate = " ".join(lines[-2:])[-350:] if lines else None
            row["caption_candidate"] = {
                "text": candidate,
                "normalized_section_char_span": [caption_start, offset],
                "normalized_context_sha256": sha(caption_context.encode("utf-8")),
                "status": "BEST_EFFORT_CONTEXT_NOT_CONFIRMED_TITLE" if candidate else
                          "NO_SEPARATE_CAPTION_TEXT",
                "reason": None if candidate else "No separate normalized text between prior indexed table and this table"}
            previous_section_end = row["flattened_section_char_end_exclusive"]
            unit_context = caption_context + "\n" + row["flattened_text"]
        else:
            row["caption_candidate"] = {"text": None, "normalized_section_char_span": None,
                                        "normalized_context_sha256": None,
                                        "status": "NEEDS_REVIEW_FLATTENED_SPAN_NOT_UNIQUE",
                                        "reason": "Table flattened text has no unique section location"}
            unit_context = row["flattened_text"]
        phrases = unit_hints(unit_context)
        symbols = [symbol for symbol in ("$", "%") if symbol in row["flattened_text"]]
        row["unit_evidence"] = {
            "explicit_scale_or_percent_phrases": phrases,
            "symbols_in_flattened_table": symbols,
            "resolved_unit": None,
            "status": "EXPLICIT_CUES_NEED_HUMAN_INTERPRETATION" if phrases or symbols else
                      "NO_EXPLICIT_UNIT_CUE_FOUND",
            "reason": "Symbols and scale phrases do not by themselves establish a single unit for every cell" if
                      phrases or symbols else "No explicit unit cue found in this table and nearby normalized context"}
        for note in row["footnote_hints"]:
            rel_start, rel_end = note["html_fragment_char_span"]
            note["html_source_char_span"] = [table_start + rel_start, table_start + rel_end]
            note["complete_submission_byte_span"] = [
                raw_html_start + html_byte_offset(html, table_start + rel_start),
                raw_html_start + html_byte_offset(html, table_start + rel_end)]
            note_start, note_end = note["complete_submission_byte_span"]
            need(sha(raw[note_start:note_end]) == note["raw_html_sha256"],
                 "Sup element source hash differs from complete raw submission")
        row["footnote_evidence"] = {
            "sup_element_count": len(row["footnote_hints"]),
            "confirmed_complete_footnotes": None,
            "status": "SUP_ELEMENTS_NEED_HUMAN_REVIEW" if row["footnote_hints"] else
                      "NO_SUP_ELEMENT_FOUND_NOT_A_NO_FOOTNOTE_CLAIM"}
        previous_html_end = row["html_source_char_end_exclusive"]
    for i, row in enumerate(tables):
        next_source_start = (tables[i + 1]["html_source_char_start"] if i + 1 < len(tables)
                             else end_html_char)
        source_start = row["html_source_char_end_exclusive"]
        source_end = min(next_source_start, end_html_char, source_start + 12000)
        source_context = html[source_start:source_end]
        byte_start, byte_end = html_byte_offset(html, source_start), html_byte_offset(html, source_end)
        raw_context = raw[raw_html_start + byte_start:raw_html_start + byte_end]
        need(raw_context == source_context.encode("utf-8"), "Following source context differs from raw filing")
        row["following_source_context"] = {
            "html_source_char_span": [source_start, source_end],
            "complete_submission_byte_span": [raw_html_start + byte_start, raw_html_start + byte_end],
            "raw_html_sha256": sha(raw_context),
            "rendered_excerpt": source_rendered(source_context)[:1800] if source_context else "",
            "status": "SOURCE_CONTEXT_ONLY_NOTE_ASSIGNMENT_UNCONFIRMED" if source_context else
                      "NO_FOLLOWING_SOURCE_CONTEXT_BEFORE_NEXT_TABLE_OR_ITEM3"}
        flat_end = row["flattened_section_char_end_exclusive"]
        if flat_end is None:
            row["adjacent_text_note_candidates"] = []
            row["footnote_evidence"]["adjacent_text_note_count"] = 0
            row["footnote_evidence"]["status"] = "FLATTENED_SPAN_UNRESOLVED_NOTES_NEED_REVIEW"
            continue
        next_flat_start = (tables[i + 1]["flattened_section_char_start"] if i + 1 < len(tables)
                           else len(section))
        if next_flat_start is None:
            next_flat_start = len(section)
        context_end = min(next_flat_start, len(section), flat_end + 1600)
        notes = []
        if context_end >= flat_end:
            context = section[flat_end:context_end]
            for match in re.finditer(r"(?mi)^[ \t]*(?P<label>\(\d{1,2}\)|Notes?:)[ \t]*(?P<body>[^\r\n]{1,500})",
                                     context):
                note_start = flat_end + match.start("label")
                note_end = flat_end + match.end("body")
                note_text = section[note_start:note_end]
                notes.append({"label": match.group("label"), "text": note_text,
                              "normalized_section_char_span": [note_start, note_end],
                              "text_sha256": sha(note_text.encode("utf-8")),
                              "distance_from_table_end_chars": note_start - flat_end,
                              "status": "ADJACENT_NOTE_CANDIDATE_NEEDS_HUMAN_REVIEW"})
        row["adjacent_text_note_candidates"] = notes
        row["footnote_evidence"]["adjacent_text_note_count"] = len(notes)
        row["footnote_evidence"]["status"] = (
            "NOTE_CUES_NEED_HUMAN_REVIEW" if notes or row["footnote_hints"] else
            "NO_NOTE_CUE_FOUND_NOT_A_NO_FOOTNOTE_CLAIM")
    layout_risks = []
    numeric_blocks = []
    if not tables:
        layout_risks.append("No literal <table> tags occur between the selected source headings; div/span numeric layouts may still be tabular and require human inspection.")
        for block in parsed.blocks:
            if (start_html_char <= block["start_char_in_html"] and
                    block["end_char_exclusive_in_html"] <= end_html_char and
                    len(re.findall(r"\d", block["text"])) >= 8):
                a, b = block["start_char_in_html"], block["end_char_exclusive_in_html"]
                numeric_blocks.append({"html_source_char_span": [a, b],
                                       "source_html_sha256": sha(html[a:b].encode("utf-8")),
                                       "text_excerpt": block["text"][:280],
                                       "classification": "NUMERIC_LAYOUT_CANDIDATE_NOT_CONFIRMED_TABLE"})
    if any(row["flattened_section_char_start"] is None for row in tables):
        layout_risks.append("At least one source HTML table could not be mapped uniquely into normalized Item 2 text.")
    return {"status": "PROGRAM_SOURCE_SPANS_PASS_HUMAN_PENDING" if
            not any(row["flattened_section_char_start"] is None for row in tables) else "NEEDS_REVIEW",
            "source_identity": {key: identity.get(key) for key in
                                ("ticker", "company", "cik", "accession", "form", "report_period_end", "filing_date")},
            "section_sha256": sha(section.encode("utf-8")),
            "source_html_table_count": len(source_tables),
            "tables": tables, "boundary_layout_tables": boundary_layout_tables,
            "layout_risks": layout_risks,
            "numeric_layout_candidates": numeric_blocks,
            "human_review": "PENDING_HUMAN_REVIEW"}


def _period_ledger(section: str, html: str, identity: dict, tables: list[dict]) -> dict:
    year, quarter = fiscal_focus(html)
    table_spans = [(row["flattened_section_char_start"], row["flattened_section_char_end_exclusive"])
                   for row in tables if row["flattened_section_char_start"] is not None]
    narrative = []
    for hint in period_hints(section):
        start = hint["source_start_char"]
        if any(a <= start < b for a, b in table_spans):
            continue
        hint["classification"] = ("PROGRAM_OUTSIDE_LITERAL_HTML_TABLE" if tables else
                                  "UNRESOLVED_NARRATIVE_OR_DIV_LAYOUT")
        narrative.append(hint)
    return {"status": "PROGRAM_PERIOD_HINTS_ONLY_HUMAN_PENDING",
            "report_fiscal_year": year, "report_fiscal_quarter": quarter,
            "report_period_end": identity["report_period_end"],
            "filing_date": identity["filing_date"],
            "narrative_periods": narrative,
            "table_periods": [{"table_id": row["table_id"],
                               "caption_candidate": row["caption_candidate"],
                               "source_caption_context": row["source_caption_context"],
                               "period_hints": row["period_hints"],
                               "unit_hints": row["unit_hints"],
                               "unit_evidence": row["unit_evidence"],
                               "footnote_hints": row["footnote_hints"],
                               "footnote_evidence": row["footnote_evidence"],
                               "adjacent_text_note_candidates": row["adjacent_text_note_candidates"],
                               "following_source_context": row["following_source_context"],
                               "source_html_char_span": [row["html_source_char_start"],
                                                         row["html_source_char_end_exclusive"]],
                               "complete_submission_byte_span": [row["complete_submission_byte_start"],
                                                                 row["complete_submission_byte_end_exclusive"]],
                               "raw_html_fragment_sha256": row["raw_html_fragment_sha256"],
                               "normalized_section_char_span": [row["flattened_section_char_start"],
                                                                row["flattened_section_char_end_exclusive"]],
                               "status": "NEEDS_HUMAN_PERIOD_REVIEW"} for row in tables],
            "other_periods": [h for h in narrative if
                              re.search(r"(?i)twelve\s+months|year\s+ended|as\s+of", h["phrase"])],
            "chapter_period": None,
            "chapter_period_reason": "Multiple narrative and table periods may coexist; no whole-chapter start/end is inferred.",
            "coverage_start_proxy": identity.get("coverage_start_proxy"),
            "coverage_start_proxy_is_not_chapter_start": True,
            "human_review": "PENDING_HUMAN_REVIEW"}


def _git_head() -> str | None:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def _verify_version_adjudication(path: Path, identity: dict, raw: bytes,
                                 html_bytes: bytes) -> dict:
    """Recompute a narrow structural comparison; never infer script origin."""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise NeedsVersionReview(f"Version adjudication receipt unreadable: {exc}") from exc
    ticker = identity["ticker"]
    cik, accession = FIXED[ticker]
    proof = record.get("equivalence_proof") or {}
    if (record.get("status") != "PROCEED_WITH_NEW_RAW_SNAPSHOT_FOR_UNREVIEWED_PILOT" or
            record.get("ticker") != ticker or record.get("cik") != cik or
            record.get("accession") != accession or
            record.get("new_raw_sha256") != sha(raw) or
            record.get("new_embedded_primary_sha256") != sha(html_bytes) or
            record.get("prior_standalone_primary_sha256") !=
            identity.get("prior_primary_comparison", {}).get("prior_sha256") or
            proof.get("status") != "PASS_STRUCTURAL_EQUIVALENCE" or
            proof.get("rule") != "XBRL_WRAPPER_AND_ONE_STANDALONE_SCRIPT_ELEMENT" or
            proof.get("exact_unwrapped_bytes_equal") is not True or
            not isinstance(proof.get("script_bytes"), int) or proof["script_bytes"] <= 0 or
            not isinstance(proof.get("script_sha256"), str)):
        raise NeedsVersionReview("Version adjudication identity/hash/rule fields do not match fixed raw source")
    prior_path = PRIOR_SOURCE_ROOT / ticker / PRIOR_NAME[ticker]
    if Path(record.get("prior_standalone_path", "")).resolve() != prior_path.resolve():
        raise NeedsVersionReview("Version adjudication references a different prior standalone primary")
    try:
        prior = prior_path.read_bytes()
    except OSError as exc:
        raise NeedsVersionReview(f"Verified prior standalone HTML unavailable: {exc}") from exc
    if sha(prior) != record["prior_standalone_primary_sha256"]:
        raise NeedsVersionReview("Prior standalone HTML SHA differs from adjudication receipt")
    prefix, suffix = b"<XBRL>\n", b"</XBRL>"
    if not html_bytes.startswith(prefix) or not html_bytes.endswith(suffix):
        raise NeedsVersionReview("New raw primary lacks the exact recorded XBRL wrapper")
    unwrapped = html_bytes[len(prefix):-len(suffix)]
    scripts = [m for m in re.finditer(rb"<script\b[^>]*>.*?</script>", prior, re.I | re.S)
               if len(m.group(0)) == proof["script_bytes"] and
               sha(m.group(0)) == proof["script_sha256"]]
    if len(scripts) != 1:
        raise NeedsVersionReview("Exactly one standalone script element must match recorded bytes/hash")
    script = scripts[0]
    if prior[:script.start()] + prior[script.end():] != unwrapped:
        raise NeedsVersionReview("Unwrapped raw primary differs beyond the one recorded script element")
    return {"status": "STRUCTURAL_DIFFERENCE_RECHECKED_FOR_UNREVIEWED_EXTRACTION",
            "adjudication_path": str(path), "adjudication_sha256": sha(path.read_bytes()),
            "prior_standalone_sha256": sha(prior), "new_embedded_primary_sha256": sha(html_bytes),
            "script_element_sha256": sha(script.group(0)), "script_element_bytes": len(script.group(0)),
            "script_element_source_char_position_not_claimed": True,
            "script_origin_or_function": "UNVERIFIED; not executed",
            "historical_snapshot_equivalence": "NOT_ESTABLISHED",
            "human_review": "PENDING_HUMAN_REVIEW"}


def extract_saved_10q(ingest_dir: Path, output_dir: Path,
                      version_adjudication: Path | None = None) -> dict:
    """Make one immutable-by-convention extraction directory from verified inputs."""
    output_dir.mkdir(parents=True, exist_ok=False)
    receipt: dict = {"kind": "M8_ONE_ORIGINAL_10Q_ITEM2_EXTRACTION",
                     "status": "RUNNING", "started_utc": datetime.now(timezone.utc).isoformat(),
                     "source_code_git_head": _git_head(),
                     "source_code_sha256": sha(Path(__file__).read_bytes()),
                     "module_sha256": sha((Path(__file__).resolve().parents[1] /
                                           "src/finembed/m8_tenq.py").read_bytes()),
                     "ingest_dir": str(ingest_dir), "human_review": "PENDING_HUMAN_REVIEW",
                     "financial_experiment": "NOT_RUN", "command_argv": sys.argv}
    try:
        identity_path = ingest_dir / "source_identity.json"
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
        ticker = identity.get("ticker")
        need(ticker in FIXED, "Ticker is not one of the four fixed pilot candidates")
        cik, accession = FIXED[ticker]
        if identity.get("status") == "NEEDS_VERSION_REVIEW" and version_adjudication is None:
            raise NeedsVersionReview("New raw primary HTML differs from the verified prior candidate snapshot; preserve both and review version before extraction")
        need(identity.get("status") in {"VERIFIED_RAW_AND_PRIMARY", "NEEDS_VERSION_REVIEW"},
             "Acquisition raw/primary identity not verified")
        need(identity.get("cik") == cik and identity.get("accession") == accession,
             "Saved identity conflicts with fixed CIK/accession")
        need(identity.get("form") == "10-Q", "Only original 10-Q is permitted")
        raw_path = ingest_dir / "original_submission.txt"
        html_path = ingest_dir / "primary_document_source.html"
        primary_path = ingest_dir / "primary_text.txt"
        raw, html_bytes, primary_bytes = raw_path.read_bytes(), html_path.read_bytes(), primary_path.read_bytes()
        need(sha(raw) == identity.get("original_submission_sha256"), "Complete submission SHA differs from acquisition identity")
        if identity["status"] == "NEEDS_VERSION_REVIEW":
            receipt["version_adjudication"] = _verify_version_adjudication(
                version_adjudication, identity, raw, html_bytes)
        elif version_adjudication is not None:
            raise NeedsVersionReview("Unexpected version adjudication supplied for already verified identical primary")
        header = raw_header(raw, accession, "10-Q")
        need(raw_filer_cik(raw) == cik, "Raw FILER CIK conflicts with fixed candidate")
        need(header["filing_date_raw_header"] == identity.get("filing_date"), "Raw filing date conflicts with SEC row")
        raw_period = re.search(rb"CONFORMED PERIOD OF REPORT:\s*(\d{8})", raw[:raw.find(b"</SEC-HEADER>")], re.I)
        need(raw_period is not None and
             f"{raw_period.group(1)[:4].decode()}-{raw_period.group(1)[4:6].decode()}-{raw_period.group(1)[6:].decode()}" == identity.get("report_period_end"),
             "Raw report period conflicts with SEC row")
        source_html, html_span = _primary_source(raw)
        need(source_html == html_bytes, "Saved primary HTML differs from complete submission")
        need(_check_primary_filename(html_span, {"primaryDocument": identity.get("primary_document")}) == identity.get("primary_document"),
             "Primary filename conflict")
        normalized, render_meta = primary_text(raw, "10-Q")
        need(normalized.encode("utf-8") == primary_bytes, "Saved normalized primary text cannot be reproduced")
        html = html_bytes.decode("utf-8", errors="strict")
        for name in ("primary_document_source.html", "primary_text.txt"):
            shutil.copyfile(ingest_dir / name, output_dir / name)
        boundary = boundary_candidates_generic(normalized)
        anchors = heading_anchors(html, html_span["raw_byte_start"])
        mapped = (len(anchors["item2_candidates"]) == boundary["item2_heading_count"] and
                  len(anchors["item3_candidates"]) == boundary["item3_heading_count"])
        selected = boundary["selected_candidate_index"]
        if selected is not None and mapped:
            source_start = anchors["item2_candidates"][selected]
            following = next((a for a in anchors["item3_candidates"] if
                              a["html_source_char_start"] > source_start["html_source_char_start"]), None)
            mapped = following is not None
            if following:
                anchors["selected_item2"], anchors["selected_item3"] = source_start, following
        if not mapped:
            boundary["status"] = "NEEDS_REVIEW"
            boundary["html_anchor_issue"] = "Text/source heading counts or following Item 3 anchor differ; no selected HTML table interval is asserted"
        boundary["html_heading_anchors"] = anchors
        boundary["coordinates"] = {
            "primary_text_unit": "Python Unicode characters in saved normalized primary_text.txt",
            "primary_html_unit": "UTF-8 bytes in saved primary_document_source.html",
            "complete_submission_unit": "bytes in saved original_submission.txt",
            "primary_html_raw_byte_span": [html_span["raw_byte_start"], html_span["raw_byte_end_exclusive"]],
            "warning": "Heading HTML anchors are source text-node starts, not an HTML byte-equivalent of the normalized section span."}
        boundary["source_identity"] = {key: identity.get(key) for key in
                                       ("ticker", "company", "cik", "accession", "form", "report_period_end", "filing_date", "primary_document")}
        for i, candidate in enumerate(boundary["candidates"]):
            if candidate["end_char_exclusive"] is not None:
                content = normalized[candidate["start_char"]:candidate["end_char_exclusive"]]
                name = f"candidate_{i:02d}.txt"
                (output_dir / name).write_bytes(content.encode("utf-8"))
                need(sha((output_dir / name).read_bytes()) == candidate["content_sha256"], "Candidate save hash mismatch")
                candidate["saved_file"] = name
        section = None
        if boundary["status"] == "PROGRAM_BOUNDARY_PASS" and selected is not None:
            candidate = boundary["candidates"][selected]
            section = normalized[candidate["start_char"]:candidate["end_char_exclusive"]]
            (output_dir / "item2_candidate.txt").write_bytes(section.encode("utf-8"))
            need(sha((output_dir / "item2_candidate.txt").read_bytes()) == candidate["content_sha256"],
                 "Item 2 save hash mismatch")
        boundary["source_diagnostics"] = page_and_character_anomalies(section) if section else None
        write_json(output_dir / "boundary_candidates.json", boundary)
        if section is not None:
            table_index = _table_index(html, raw, section, html_span["raw_byte_start"],
                                       anchors["selected_item2"]["html_source_char_start"],
                                       anchors["selected_item3"]["html_source_char_start"], identity)
            periods = _period_ledger(section, html, identity, table_index["tables"])
        else:
            table_index = {"status": "NEEDS_REVIEW", "source_identity": identity,
                           "section_sha256": None, "source_html_table_count": None,
                           "tables": [], "boundary_layout_tables": [],
                           "layout_risks": ["No unique source-anchored Item 2 body; no table interval asserted"],
                           "numeric_layout_candidates": [], "human_review": "PENDING_HUMAN_REVIEW"}
            periods = {"status": "NEEDS_REVIEW", "report_period_end": identity["report_period_end"],
                       "narrative_periods": [], "table_periods": [], "chapter_period": None,
                       "human_review": "PENDING_HUMAN_REVIEW"}
        write_json(output_dir / "table_index.json", table_index)
        write_json(output_dir / "period_ledger.json", periods)
        receipt.update({"status": "PROGRAM_EXTRACTED_HUMAN_PENDING" if section is not None else "NEEDS_REVIEW",
                        "source_identity": boundary["source_identity"],
                        "source_identity_sha256": sha(identity_path.read_bytes()),
                        "input_hashes": {name: sha((ingest_dir / name).read_bytes()) for name in
                                         ("original_submission.txt", "primary_document_source.html", "primary_text.txt", "source_identity.json")},
                        "raw_header": header,
                        "rendering": render_meta,
                        "table_count": len(table_index["tables"]),
                        "table_source_status": table_index["status"],
                        "period_status": periods["status"],
                        "human_review": "PENDING_HUMAN_REVIEW"})
    except NeedsVersionReview as exc:
        receipt.update({"status": "NEEDS_REVIEW", "reason": f"{type(exc).__name__}: {exc}",
                        "human_review": "PENDING_HUMAN_REVIEW"})
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, SecError, ExtractionError) as exc:
        receipt.update({"status": "FAILED_INPUT_VALIDATION", "reason": f"{type(exc).__name__}: {exc}"})
    receipt["finished_utc"] = datetime.now(timezone.utc).isoformat()
    receipt["outputs"] = {path.name: {"sha256": sha(path.read_bytes()), "bytes": path.stat().st_size}
                          for path in sorted(output_dir.iterdir()) if path.is_file() and path.name != "extraction_receipt.json"}
    write_json(output_dir / "extraction_receipt.json", receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ingest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version-adjudication", type=Path,
                        help="Separate exact-hash structural discrepancy receipt; never human acceptance")
    args = parser.parse_args()
    result = extract_saved_10q(args.ingest, args.output, args.version_adjudication)
    print(json.dumps({"status": result["status"], "output": str(args.output),
                      "table_count": result.get("table_count"), "reason": result.get("reason")}))
    return 0 if result["status"] in {"PROGRAM_EXTRACTED_HUMAN_PENDING", "NEEDS_REVIEW"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
