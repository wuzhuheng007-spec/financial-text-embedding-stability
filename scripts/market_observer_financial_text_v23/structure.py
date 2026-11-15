"""Recover source-visible table geometry and conservative evidence relations.

This module reads raw saved HTML and source metadata only. A returned relation
is an engineering candidate, never a confirmed accounting fact. No reviewed
candidate, outcome, model score or later-period document is used for selection.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict

from bs4 import BeautifulSoup, NavigableString, Tag

from scripts.market_observer_financial_text_v21.parser import (
    BASIS_RE, MONTH_DAY_RE, NUMERIC_RE, PERIOD_RE, TOPIC_RE, UNITS_RE,
    dom_path, visible_text,
)


DATE_RE = re.compile(r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+20\d{2}\b", re.I)
YEAR_RE = re.compile(r"\b20\d{2}\b")
RESULTS_INTRO_RE = re.compile(r"\b(?:results?\s+for|quarter\s+ended|year\s+ended|fiscal\s+(?:first|second|third|fourth|q[1-4])\b.{0,40}\bresults?)\b", re.I)
GUIDANCE_RE = re.compile(r"\b(?:guidance|outlook|expect(?:s|ed)?|forecast|project(?:s|ed)?|anticipat(?:e|es|ed)|future|next[- ]quarter|upcoming quarter)\b", re.I)
TOPIC_ORDER = (
    "revenue", "net_income", "earnings_per_share", "gross_margin",
    "next_quarter_or_full_year_guidance", "capital_expenditures",
    "operating_cash_flow", "free_cash_flow", "important_segment_revenue",
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _id(prefix: str, *parts: object) -> str:
    return prefix + _sha("|".join(map(str, parts)).encode("utf-8"))[:16]


def _without_nested_table_text(tag: Tag) -> str:
    pieces = []
    for child in tag.descendants:
        if not isinstance(child, NavigableString):
            continue
        ancestor = child.parent
        inside_nested_table = False
        while ancestor is not None and ancestor is not tag:
            if isinstance(ancestor, Tag) and ancestor.name == "table":
                inside_nested_table = True
                break
            ancestor = ancestor.parent
        if not inside_nested_table:
            pieces.append(str(child))
    return visible_text(" ".join(pieces))


def _measurement_without_linked_sup(tag: Tag) -> str:
    """Remove only superscript text inside a source-linked footnote marker."""
    pieces = []
    for child in tag.descendants:
        if not isinstance(child, NavigableString):
            continue
        ancestor = child.parent
        skip = False
        while ancestor is not None and ancestor is not tag:
            if isinstance(ancestor, Tag) and ancestor.name == "sup":
                parent = ancestor.parent
                if isinstance(parent, Tag) and parent.name == "a" and str(parent.get("href", "")).startswith("#"):
                    skip = True
                    break
            if isinstance(ancestor, Tag) and ancestor.name == "table":
                skip = True
                break
            ancestor = ancestor.parent
        if not skip:
            pieces.append(str(child))
    return visible_text(" ".join(pieces))


def _span(cell: Tag, name: str, remaining_rows: int) -> tuple[int, bool]:
    raw = cell.get(name)
    if raw is None:
        return 1, True
    try:
        value = int(raw)
    except (ValueError, TypeError):
        return 1, False
    if name == "rowspan" and value == 0:
        return remaining_rows, False  # preserve geometry, abstain on relation
    if value < 1 or value > 1000:
        return 1, False
    return value, True


def _direct_rows(table: Tag) -> list[Tag]:
    return [r for r in table.find_all("tr") if r.find_parent("table") is table]


def _direct_cells(row: Tag) -> list[Tag]:
    return row.find_all(["td", "th"], recursive=False)


def _grid(table: Tag) -> tuple[list[dict], list[dict]]:
    """Return physical cells and rows in a 0-based half-open logical grid."""
    rows = _direct_rows(table)
    output: list[dict] = []
    all_cells: list[dict] = []
    occupied_until: dict[int, int] = {}
    for row_index, row in enumerate(rows):
        physical: list[dict] = []
        col = 0
        for tag in _direct_cells(row):
            colspan, good_col = _span(tag, "colspan", len(rows) - row_index)
            rowspan, good_row = _span(tag, "rowspan", len(rows) - row_index)
            # HTML table placement skips occupied columns, including every
            # column covered by a spanning cell. Do not overwrite a rowspan.
            while any(occupied_until.get(c, 0) > row_index for c in range(col, col + colspan)):
                col += 1
            end_col = col + colspan
            end_row = min(len(rows), row_index + rowspan)
            cell = {
                "row_start": row_index, "row_end": end_row,
                "column_start": col, "column_end": end_col,
                "dom_path": dom_path(tag), "row_dom_path": dom_path(row),
                "text": _without_nested_table_text(tag), "tag": tag.name.lower(),
                "span_status": "EXACT" if good_col and good_row else "UNRESOLVED",
                "tag_obj": tag,
            }
            physical.append(cell)
            all_cells.append(cell)
            for c in range(col, end_col):
                occupied_until[c] = end_row
            col = end_col
        output.append({"row_tag": row, "row_index": row_index, "cells": physical})
    return output, all_cells


def _period_key(text: str) -> tuple[str, ...]:
    dates = tuple(x.group(0).lower() for x in DATE_RE.finditer(text))
    if dates:
        return dates
    return tuple(x.group(0) for x in YEAR_RE.finditer(text))


def _basis_key(text: str) -> str | None:
    low = text.lower()
    non = bool(re.search(r"\bnon[- ]?gaap\b|\badjusted\b", low))
    gaap = bool(re.search(r"(?<!non[- ])\bgaap\b|\bas reported\b", low))
    if non and gaap:
        return "CONFLICT"
    if non:
        return "NON_GAAP"
    if gaap:
        return "GAAP"
    return None


def _unit_key(text: str) -> set[str]:
    low = text.lower()
    out: set[str] = set()
    for scale in ("thousand", "million", "billion", "trillion"):
        if re.search(rf"\b{scale}s?\b", low):
            out.add(scale)
    if "%" in text or "percent" in low:
        out.add("percent")
    if "$" in text or "dollar" in low or "usd" in low:
        out.add("USD")
    if "€" in text:
        out.add("EUR")
    if "£" in text:
        out.add("GBP")
    if "per share" in low:
        out.add("per_share")
    return out


def _measurement(text: str) -> bool:
    if not text or not NUMERIC_RE.search(text):
        return False
    # A numeric footnote marker on a descriptive row label is not itself a
    # measured value. Keep a genuine numeric value with a trailing marker.
    if not re.fullmatch(r"[$€£]?\s*\(\d+\)", text.strip()):
        without_trailing_note = re.sub(r"\s*(?:\([a-z0-9]{1,2}\)|\*{1,3})\s*$", "", text, flags=re.I)
        if without_trailing_note != text and not NUMERIC_RE.search(without_trailing_note):
            return False
    if DATE_RE.fullmatch(text.strip()) or re.fullmatch(r"20\d{2}", text.strip()):
        return False
    return True


def _footnote_only_cell(cell: dict) -> bool:
    tag = cell["tag_obj"]
    if not tag.find("sup") or not re.fullmatch(r"\(?[a-z0-9*]{1,3}\)?", cell["text"], re.I):
        return False
    return all(isinstance(child, Tag) and child.name in {"sup", "a"}
               or isinstance(child, NavigableString) and not str(child).strip()
               for child in tag.contents)


def _unlinked_note_marker(tag: Tag) -> bool:
    text = _without_nested_table_text(tag)
    if re.fullmatch(r"[$€£]?\s*\(\d+\)", text.strip()):
        return False  # an entire parenthetical amount may be negative
    # Only a trailing marker is plausible without an explicit HTML link.
    # An asterisk used in a formula or prose is not a footnote relation.
    return bool(re.search(r"(?:\([a-z0-9]{1,2}\)|(?<=\S)\*{1,3})\s*$", text, re.I))


def _paragraph_measurements(text: str) -> list[tuple[int, int, str]]:
    dates = [(m.start(), m.end()) for m in DATE_RE.finditer(text)]
    found = []
    for match in NUMERIC_RE.finditer(text):
        if any(a <= match.start() < b for a, b in dates):
            continue
        raw_value = match.group(0)
        left_trim = len(raw_value) - len(raw_value.lstrip())
        right_trim = len(raw_value) - len(raw_value.rstrip())
        start, end = match.start() + left_trim, match.end() - right_trim
        value = text[start:end]
        if not value or YEAR_RE.fullmatch(value):
            continue
        found.append((start, end, value))
    return found


def _topics(text: str) -> list[str]:
    hits = [topic for topic in TOPIC_ORDER if TOPIC_RE[topic].search(text)]
    if re.search(r"\bcost\s+of\s+(?:revenue|sales)\b", text, re.I):
        hits = [x for x in hits if x != "revenue"]
    if re.search(r"\bweighted\s+average\s+shares\b|\bshares\s+used\s+in\b", text, re.I):
        hits = [x for x in hits if x not in {"net_income", "earnings_per_share"}]
    if GUIDANCE_RE.search(text) and "next_quarter_or_full_year_guidance" not in hits:
        hits.append("next_quarter_or_full_year_guidance")
    return hits


def _content_role(text: str) -> str:
    if GUIDANCE_RE.search(text):
        return "FUTURE_GUIDANCE"
    if re.search(r"\b(?:year over year|compared (?:with|to)|same period last year)\b", text, re.I):
        return "COMPARISON"
    return "HISTORICAL_ACTUAL"


def _heading(tag: Tag) -> bool:
    if tag.name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        return True
    if tag.name not in {"p", "div"}:
        return False
    text = visible_text(tag)
    if not text or len(text) > 160:
        return False
    bold = tag.find(["b", "strong"], recursive=False)
    return bool(bold and visible_text(bold) == text)


def _source_events(root: Tag) -> list[tuple[str, Tag, str]]:
    """Collect chapter headings, leaf paragraphs and outer tables in DOM order."""
    events = []
    for tag in root.descendants:
        if not isinstance(tag, Tag) or tag.name in {"script", "style", "noscript"}:
            continue
        if tag.name == "table":
            # A nested table is a distinct structural scope; its rows must
            # never be folded into the outer grid.
            events.append(("TABLE", tag, ""))
            continue
        if tag.find_parent("table") is not None:
            continue
        if tag.name not in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "div"}:
            continue
        if tag.find("table") is not None or tag.find(["p", "div", "h1", "h2", "h3", "h4", "h5", "h6"]) is not None:
            continue
        text = visible_text(tag)
        if text:
            events.append(("HEADING" if _heading(tag) else "PARAGRAPH", tag, text))
    return events


def parse_document(raw_html: bytes, meta: dict) -> dict:
    """Parse one saved training document without looking at reviewed answers."""
    expected = meta["raw_body_sha256"]
    if _sha(raw_html) != expected:
        raise ValueError("saved raw HTML hash differs from manifest")
    document_id = meta["document_id"]
    source_sha = expected
    soup = BeautifulSoup(raw_html, "html.parser")
    root = soup.body or soup
    fragments: list[dict] = []
    fragment_by_path: dict[str, dict] = {}
    numeric_cells: list[dict] = []
    groups: list[dict] = []
    relations: list[dict] = []
    table_summaries: list[dict] = []
    section_id = _id("S", document_id, "root")
    section_intros: list[dict] = []
    table_index = 0

    def fragment(tag: Tag, text: str, kind: str, *, table_id: str | None = None,
                 subtable_id: str | None = None, spans: dict | None = None) -> dict:
        path = dom_path(tag)
        existing = fragment_by_path.get(path)
        if existing is not None:
            return existing
        f = {
            "fragment_id": _id("F", document_id, path), "document_id": document_id,
            "source_sha256": source_sha, "dom_path": path, "text": text,
            "text_sha256": _sha(text.encode("utf-8")), "kind": kind,
            "table_id": table_id, "subtable_id": subtable_id,
            "section_id": section_id,
            "row_start": spans.get("row_start") if spans else None,
            "row_end": spans.get("row_end") if spans else None,
            "column_start": spans.get("column_start") if spans else None,
            "column_end": spans.get("column_end") if spans else None,
        }
        fragments.append(f)
        fragment_by_path[path] = f
        return f

    def add_relation(cell: dict, kind: str, targets: list[dict], method: str,
                     scope_id: str, status: str) -> dict:
        ids = [f["fragment_id"] for f in targets]
        relation = {
            "relation_id": _id("R", cell["cell_id"], kind, method, ",".join(ids)),
            "from_id": cell["cell_id"], "kind": kind, "to_ids": ids,
            "method": method, "scope_id": scope_id,
            "source_dom_paths": [f["dom_path"] for f in targets],
            "status": status,
        }
        relations.append(relation)
        return relation

    for event_kind, node, text in _source_events(root):
        if event_kind == "HEADING":
            section_id = _id("S", document_id, dom_path(node))
            section_intros = []
            if RESULTS_INTRO_RE.search(text) and (PERIOD_RE.search(text) or MONTH_DAY_RE.search(text)):
                section_intros = [fragment(node, text, "SECTION_PERIOD")]
            continue
        if event_kind == "PARAGRAPH":
            if RESULTS_INTRO_RE.search(text) and (PERIOD_RE.search(text) or MONTH_DAY_RE.search(text)):
                # An explicit later declaration replaces the earlier scope in
                # the same chapter; a single conflicting declaration remains
                # unresolved when evaluated below.
                section_intros = [fragment(node, text, "SECTION_PERIOD")]
            if not _topics(text):
                continue
            found_values = _paragraph_measurements(text)
            if not found_values:
                continue
            p_context = fragment(node, text, "PARAGRAPH_CONTEXT")
            p_path = dom_path(node)
            period_keys = _period_key(text)
            period_status = "CONFLICT" if len(period_keys) > 1 else (
                "EXPLICIT" if period_keys or PERIOD_RE.search(text) else "UNKNOWN")
            unit_keys = _unit_key(text)
            unit_status = "EXPLICIT" if unit_keys and len(found_values) == 1 else "UNKNOWN"
            basis_key = _basis_key(text)
            basis_status = "CONFLICT" if basis_key == "CONFLICT" else (
                "EXPLICIT" if basis_key else "UNKNOWN")
            mapping_status = "EXPLICIT" if len(found_values) == 1 and len(_topics(text)) == 1 else "UNKNOWN"
            for start, end, value in found_values:
                cell = {
                    "cell_id": _id("V", document_id, p_path, start, end),
                    "document_id": document_id, "source_sha256": source_sha,
                    "table_id": None, "subtable_id": section_id, "section_id": section_id,
                    "row_index": None, "column_index": None,
                    "row_start": None, "row_end": None,
                    "column_start": None, "column_end": None,
                    "cell_dom_path": p_path, "row_dom_path": p_path,
                    "text": value, "measurement_text": value,
                    "text_sha256": _sha(value.encode("utf-8")),
                    "text_start": start, "text_end": end,
                    "anchor_kind": "PARAGRAPH", "element_kind": "PARAGRAPH_VALUE",
                    "geometry_status": "NOT_APPLICABLE",
                }
                numeric_cells.append(cell)
                specs = [
                    ("ROW_HEADER", mapping_status), ("COLUMN_HEADER", mapping_status),
                    ("PERIOD", period_status), ("UNIT", unit_status),
                    ("BASIS", basis_status), ("FOOTNOTE", "NOT_APPLICABLE"),
                ]
                rels = [add_relation(cell, kind, [p_context] if status in ("EXPLICIT", "CONFLICT") else [],
                                     "SINGLE_PARAGRAPH_EXPLICIT_TEXT", section_id, status)
                        for kind, status in specs]
                statuses = {r["kind"].lower(): r["status"] for r in rels}
                if any(x == "CONFLICT" for x in statuses.values()):
                    eligibility = "RELATION_CONFLICT"
                elif any(statuses[k] != "EXPLICIT" for k in ("row_header", "column_header", "period", "unit")):
                    eligibility = "RELATION_UNRESOLVED"
                else:
                    eligibility = "ELIGIBLE_KNOWN_FIELDS" if basis_status == "UNKNOWN" else "ELIGIBLE_FULL_FIELDS"
                groups.append({
                    "group_id": _id("G", cell["cell_id"]), "document_id": document_id,
                    "event_group_id": meta["event_group_id"], "symbol": meta["symbol"],
                    "source_kind": meta["source_kind"], "announcement_date": meta["announcement_date"],
                    "anchor_cell_id": cell["cell_id"], "topics": _topics(text),
                    "content_role": _content_role(text), "relation_ids": [r["relation_id"] for r in rels],
                    "relation_refs": {r["kind"].lower(): r["to_ids"] for r in rels},
                    "relation_status": statuses, "eligibility": eligibility,
                    "candidate_not_confirmed": True,
                })
            continue
        table_index += 1
        table_id = _id("T", document_id, dom_path(node))
        rows, physical_cells = _grid(node)
        table_width = max((c["column_end"] for c in physical_cells), default=0)
        region_index = 0
        seen_data = False
        current_region = _id("U", table_id, region_index)
        caption_tag = node.find("caption", recursive=False)
        caption_headers: list[dict] = []
        if caption_tag is not None and visible_text(caption_tag):
            caption_headers.append(fragment(caption_tag, visible_text(caption_tag), "TABLE_CAPTION",
                                            table_id=table_id,
                                            spans={"row_start": None, "row_end": None,
                                                   "column_start": 0, "column_end": table_width}))
        active_headers: list[dict] = caption_headers[:]
        known_header_signatures: set[tuple[str, ...]] = set()
        region_by_row: dict[int, str] = {}
        table_summaries.append({"table_id": table_id, "document_id": document_id,
                                "dom_path": dom_path(node), "source_order": table_index,
                                "row_count": len(rows), "physical_cell_count": len(physical_cells)})
        for row in rows:
            cells = row["cells"]
            inherited_labels = [c for c in physical_cells
                                if c["row_start"] < row["row_index"] < c["row_end"]
                                and region_by_row.get(c["row_start"]) == current_region
                                and c["text"] and not _measurement(c["text"])]
            full_text = " ".join(c["text"] for c in inherited_labels + cells if c["text"])
            row_topics = _topics(full_text)
            measures = [c for c in cells if _measurement(c["text"]) and not _footnote_only_cell(c)]
            same_row_period_cells = [c for c in cells if c["text"]
                                     and (DATE_RE.fullmatch(c["text"].strip())
                                          or YEAR_RE.fullmatch(c["text"].strip()))]
            row_signature = tuple(c["text"].strip().lower() for c in cells if c["text"])
            repeated_header = bool(seen_data and row_signature and row_signature in known_header_signatures)
            label_cells = [c for c in inherited_labels + cells if c["text"] and c not in measures
                           and c not in same_row_period_cells
                           and not re.fullmatch(r"[$€£*()\s]+", c["text"])]
            all_header_tags = bool(cells) and all(c["tag"] == "th" for c in cells if c["text"])
            data_row = bool(not all_header_tags and not repeated_header and row_topics and measures and label_cells)
            header_row = all_header_tags or repeated_header or (not data_row and bool(
                any(c["tag"] == "th" for c in cells)
                or PERIOD_RE.search(full_text) or MONTH_DAY_RE.search(full_text)
                or UNITS_RE.search(full_text) or BASIS_RE.search(full_text)
                or (len([c for c in cells if c["text"]]) == 1 and cells
                    and cells[0]["column_end"] > cells[0]["column_start"] + 1)))
            if header_row and seen_data:
                region_index += 1
                current_region = _id("U", table_id, region_index)
                active_headers = caption_headers[:]
                seen_data = False
            region_by_row[row["row_index"]] = current_region
            if header_row:
                if row_signature:
                    known_header_signatures.add(row_signature)
                for c in cells:
                    if c["text"]:
                        active_headers.append(fragment(c["tag_obj"], c["text"], "TABLE_HEADER",
                                                       table_id=table_id, subtable_id=current_region, spans=c))
                continue
            if not data_row:
                continue
            seen_data = True
            row_label = [c for c in label_cells if c["column_start"] < min(v["column_start"] for v in measures)]
            row_label_fragments = [fragment(c["tag_obj"], c["text"], "ROW_HEADER", table_id=table_id,
                                            subtable_id=current_region, spans=c) for c in row_label]
            for c in measures:
                if c["span_status"] != "EXACT":
                    geometry_status = "CONFLICT"
                else:
                    geometry_status = "EXPLICIT"
                cell = {
                    "cell_id": _id("V", document_id, c["dom_path"]),
                    "document_id": document_id, "source_sha256": source_sha,
                    "table_id": table_id, "subtable_id": current_region,
                    "section_id": section_id, "row_index": c["row_start"],
                    "column_index": c["column_start"],
                    "row_start": c["row_start"], "row_end": c["row_end"],
                    "column_start": c["column_start"], "column_end": c["column_end"],
                    "cell_dom_path": c["dom_path"], "row_dom_path": c["row_dom_path"],
                    "text": c["text"], "text_sha256": _sha(c["text"].encode("utf-8")),
                    "measurement_text": _measurement_without_linked_sup(c["tag_obj"]),
                    "anchor_kind": "TABLE_CELL", "element_kind": "TABLE_VALUE",
                    "geometry_status": geometry_status,
                }
                numeric_cells.append(cell)
                scoped_headers = [f for f in active_headers if
                                  (f["subtable_id"] == current_region
                                   or (f["kind"] == "TABLE_CAPTION" and f["table_id"] == table_id))
                                  and f["column_start"] is not None
                                  and f["column_start"] < c["column_end"]
                                  and c["column_start"] < f["column_end"]]
                period_headers = [f for f in scoped_headers if
                                  PERIOD_RE.search(f["text"]) or MONTH_DAY_RE.search(f["text"])
                                  or DATE_RE.search(f["text"])]
                specific_period = [f for f in period_headers if _period_key(f["text"])]
                min_width = min((f["column_end"] - f["column_start"] for f in specific_period), default=None)
                precise_period = [f for f in specific_period
                                  if f["column_end"] - f["column_start"] == min_width]
                duration_header = [f for f in period_headers if not _period_key(f["text"])]
                period_sources = precise_period + duration_header
                period_method = "COLUMN_GRID_OVERLAP"
                if not precise_period:
                    row_period = [fragment(x["tag_obj"], x["text"], "ROW_PERIOD",
                                           table_id=table_id, subtable_id=current_region, spans=x)
                                  for x in same_row_period_cells if x["column_start"] < c["column_start"]]
                    if len(row_period) == 1:
                        period_sources = row_period + duration_header
                        period_method = "SAME_ROW_PERIOD_CELL"
                if not period_sources and section_intros:
                    keys = {_period_key(f["text"]) for f in section_intros}
                    if len(keys) == 1:
                        period_sources = section_intros[:]
                        period_method = "EXPLICIT_SECTION_RESULTS_INTRO"
                period_keys = {k for f in precise_period if (k := _period_key(f["text"]))}
                if period_method in ("EXPLICIT_SECTION_RESULTS_INTRO", "SAME_ROW_PERIOD_CELL"):
                    period_keys = {k for f in period_sources if (k := _period_key(f["text"]))}
                period_status = "CONFLICT" if len(period_keys) > 1 or any(len(k) > 1 for k in period_keys) else (
                    "EXPLICIT" if period_keys else "UNKNOWN")

                col_header_sources = scoped_headers
                row_status = "EXPLICIT" if len(row_label_fragments) == 1 else ("UNKNOWN" if not row_label_fragments else "CONFLICT")
                column_status = ("EXPLICIT" if any(
                    table_width == 1 or f["column_end"] - f["column_start"] < table_width
                    for f in col_header_sources) else "UNKNOWN")
                direct_unit = []
                if _unit_key(c["text"]):
                    direct_unit = [fragment(c["tag_obj"], c["text"], "VALUE_CELL", table_id=table_id,
                                            subtable_id=current_region, spans=c)]
                adjacent_currency = [x for x in cells if x["column_end"] == c["column_start"]
                                     and x["text"] in ("$", "€", "£")]
                currency_sources = [fragment(x["tag_obj"], x["text"], "CURRENCY_SYMBOL",
                                             table_id=table_id, subtable_id=current_region, spans=x)
                                    for x in adjacent_currency]
                row_unit = [f for f in row_label_fragments if _unit_key(f["text"])]
                header_unit = [f for f in scoped_headers if _unit_key(f["text"])]
                section_unit = [f for f in section_intros if _unit_key(f["text"])]
                explicit_percent = any("percent" in _unit_key(f["text"])
                                       for f in direct_unit + row_unit)
                if explicit_percent:
                    header_unit = [f for f in header_unit if "percent" in _unit_key(f["text"])]
                    section_unit = [f for f in section_unit if "percent" in _unit_key(f["text"])]
                per_share_exception = (
                    "earnings_per_share" in _topics(" ".join(f["text"] for f in row_label_fragments))
                    and any(re.search(r"\bexcept\s+(?:for\s+)?per[- ]share\b", f["text"], re.I)
                            for f in caption_headers))
                if per_share_exception:
                    header_unit = [f for f in header_unit if f["kind"] != "TABLE_CAPTION"]
                unit_sources = list(dict.fromkeys(f["fragment_id"] for f in (
                    direct_unit + currency_sources + row_unit + header_unit + section_unit)))
                unit_sources = [next(f for f in fragments if f["fragment_id"] == fid) for fid in unit_sources]
                unit_keys = set().union(*(_unit_key(f["text"]) for f in unit_sources)) if unit_sources else set()
                unit_conflict = len(unit_keys & {"thousand", "million", "billion", "trillion"}) > 1 or len(unit_keys & {"USD", "EUR", "GBP"}) > 1
                unit_status = "CONFLICT" if unit_conflict else ("EXPLICIT" if unit_sources else "UNKNOWN")
                if per_share_exception and "per_share" not in unit_keys:
                    unit_status = "UNKNOWN"

                row_basis = [f for f in row_label_fragments if _basis_key(f["text"])]
                col_basis_all = [f for f in scoped_headers if _basis_key(f["text"])]
                basis_width = min((f["column_end"] - f["column_start"] for f in col_basis_all), default=None)
                col_basis = [f for f in col_basis_all if f["column_end"] - f["column_start"] == basis_width]
                basis_sources = row_basis + col_basis
                basis_keys = {_basis_key(f["text"]) for f in basis_sources}
                basis_status = "CONFLICT" if "CONFLICT" in basis_keys or len(basis_keys) > 1 else ("EXPLICIT" if basis_sources else "UNKNOWN")
                basis_method = "ROW_AND_COLUMN_AGREEMENT" if row_basis and col_basis else (
                    "ROW_LABEL" if row_basis else "COLUMN_GRID_OVERLAP")

                footnote_nodes = [c["tag_obj"]] + [x["tag_obj"] for x in row_label]
                footnote_nodes += [soup.select_one(f["dom_path"]) for f in scoped_headers]
                footnote_nodes = [x for x in footnote_nodes if x is not None]
                footnote_links = [a.get("href", "")[1:] for node_with_refs in footnote_nodes
                                  for a in node_with_refs.find_all("a", href=True)
                                  if a.get("href", "").startswith("#")]
                footnotes = []
                unresolved_note = False
                for target in footnote_links:
                    targets = soup.find_all(id=target)
                    if len(targets) != 1:
                        unresolved_note = True
                    else:
                        footnotes.append(fragment(targets[0], visible_text(targets[0]), "HTML_LINKED_FOOTNOTE"))
                if any(x.find("sup") or _unlinked_note_marker(x) for x in footnote_nodes) and not footnote_links:
                    unresolved_note = True
                foot_status = "UNKNOWN" if unresolved_note else ("EXPLICIT" if footnotes else "NOT_APPLICABLE")

                rels = [
                    add_relation(cell, "ROW_HEADER", row_label_fragments, "SAME_ROW_LEFT_LABEL", current_region, row_status),
                    add_relation(cell, "COLUMN_HEADER", col_header_sources, "COLUMN_GRID_OVERLAP", current_region, column_status),
                    add_relation(cell, "PERIOD", period_sources, period_method,
                                 current_region if period_method == "COLUMN_GRID_OVERLAP" else section_id, period_status),
                    add_relation(cell, "UNIT", unit_sources, "CELL_ROW_OR_COLUMN_DECLARATION", current_region, unit_status),
                    add_relation(cell, "BASIS", basis_sources, basis_method, current_region, basis_status),
                    add_relation(cell, "FOOTNOTE", footnotes, "HTML_HREF_TARGET", current_region, foot_status),
                ]
                statuses = {r["kind"].lower(): r["status"] for r in rels}
                if geometry_status == "CONFLICT" or any(s == "CONFLICT" for s in statuses.values()):
                    eligibility = "RELATION_CONFLICT"
                elif any(statuses[k] != "EXPLICIT" for k in ("row_header", "column_header", "period", "unit")):
                    eligibility = "RELATION_UNRESOLVED"
                elif foot_status == "UNKNOWN":
                    eligibility = "FOOTNOTE_UNRESOLVED"
                else:
                    eligibility = "ELIGIBLE_KNOWN_FIELDS" if basis_status == "UNKNOWN" else "ELIGIBLE_FULL_FIELDS"
                group_topics = _topics(" ".join(f["text"] for f in row_label_fragments))
                role_text = " ".join(f["text"] for f in row_label_fragments + scoped_headers)
                groups.append({
                    "group_id": _id("G", cell["cell_id"]), "document_id": document_id,
                    "event_group_id": meta["event_group_id"], "symbol": meta["symbol"],
                    "source_kind": meta["source_kind"], "announcement_date": meta["announcement_date"],
                    "anchor_cell_id": cell["cell_id"], "topics": group_topics,
                    "content_role": _content_role(role_text), "relation_ids": [r["relation_id"] for r in rels],
                    "relation_refs": {r["kind"].lower(): r["to_ids"] for r in rels},
                    "relation_status": statuses, "eligibility": eligibility,
                    "candidate_not_confirmed": True,
                })
    # A comparative value is a separate cell. Only an explicit source label
    # naming the prior quarter can identify it here; table position alone is
    # insufficient, because many disclosures put prior-year and prior-quarter
    # values side by side.
    cell_by_id = {c["cell_id"]: c for c in numeric_cells}
    group_by_cell = {g["anchor_cell_id"]: g for g in groups}
    frag_by_id = {f["fragment_id"]: f for f in fragments}
    cells_by_row: dict[tuple[str, str, int], list[dict]] = defaultdict(list)
    for cell in numeric_cells:
        if cell["table_id"] is not None:
            cells_by_row[(cell["table_id"], cell["subtable_id"], cell["row_index"])].append(cell)
    for group in groups:
        cell = cell_by_id[group["anchor_cell_id"]]
        peers = ([other for other in cells_by_row[(cell["table_id"], cell["subtable_id"], cell["row_index"])]
                  if other["cell_id"] != cell["cell_id"]] if cell["table_id"] is not None else [])
        prior_quarter_peers = []
        for other in peers:
            peer_group = group_by_cell[other["cell_id"]]
            header_ids = peer_group["relation_refs"].get("column_header", [])
            header_text = " ".join(frag_by_id[fid]["text"] for fid in header_ids)
            if re.search(r"\b(?:prior|previous|preceding|last)\s+(?:fiscal\s+)?quarter\b", header_text, re.I):
                prior_quarter_peers.append(other)
        companion = prior_quarter_peers[0] if len(prior_quarter_peers) == 1 else None
        companion_group = group_by_cell[companion["cell_id"]] if companion else None
        comparison_specs = [
            ("COMPARISON_VALUE", [companion["cell_id"]] if companion else [],
             "SAME_ROW_EXPLICIT_PRIOR_QUARTER_HEADER"),
            ("COMPARISON_PERIOD", companion_group["relation_refs"]["period"] if companion_group else [],
             "COMPANION_COLUMN_PERIOD"),
            ("COMPARISON_UNIT", companion_group["relation_refs"]["unit"] if companion_group else [],
             "COMPANION_COLUMN_UNIT"),
            ("COMPARISON_BASIS", companion_group["relation_refs"]["basis"] if companion_group else [],
             "COMPANION_COLUMN_BASIS"),
        ]
        group["comparison_cell_id"] = companion["cell_id"] if companion else None
        for kind, ids, method in comparison_specs:
            if kind == "COMPARISON_VALUE":
                status = "CONFLICT" if len(prior_quarter_peers) > 1 else ("EXPLICIT" if companion else "UNKNOWN")
                paths = [companion["cell_dom_path"]] if companion else []
            else:
                key = kind.removeprefix("COMPARISON_").lower()
                status = companion_group["relation_status"][key] if companion_group else "UNKNOWN"
                paths = [frag_by_id[fid]["dom_path"] for fid in ids]
            relation = {
                "relation_id": _id("R", cell["cell_id"], kind, method, ",".join(ids)),
                "from_id": cell["cell_id"], "kind": kind, "to_ids": ids,
                "method": method, "scope_id": cell["subtable_id"],
                "source_dom_paths": paths, "status": status,
            }
            relations.append(relation)
            group["relation_ids"].append(relation["relation_id"])
            group["relation_refs"][kind.lower()] = ids
            group["relation_status"][kind.lower()] = status
    return {
        "schema": "financial_text_v23_parsed_document_v1", "document_id": document_id,
        "event_group_id": meta["event_group_id"], "symbol": meta["symbol"],
        "source_kind": meta["source_kind"], "announcement_date": meta["announcement_date"],
        "source_sha256": source_sha, "source_url": meta["source_url"],
        "source_fragments": fragments, "numeric_cells": numeric_cells,
        "evidence_groups": groups, "relations": relations,
        "tables": table_summaries,
    }
