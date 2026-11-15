"""Preserve visible SEC HTML financial blocks with DOM and table provenance.

This parser produces candidates for review. A table cell is never a confirmed
financial value merely because it was extracted or given a probable header.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from bs4 import BeautifulSoup, NavigableString, Tag


TOPIC_PATTERNS = {
    "revenue": r"\b(revenue|net sales)\b",
    "net_income": r"\b(net income|net loss|net earnings)\b",
    "earnings_per_share": r"\b(earnings|net income|net loss) per (diluted |basic )?share\b|\beps\b",
    "gross_margin": r"\bgross (margin|profit)\b",
    "next_quarter_or_full_year_guidance": r"\b(guidance|outlook|expects?|forecast|next quarter|fiscal year|upcoming quarter)\b",
    "capital_expenditures": r"\b(capital expenditures?|capital spending|capex|purchases? of property)\b",
    "operating_cash_flow": r"\b(operating cash flow|cash flow from operations|net cash provided by operating activities)\b",
    "free_cash_flow": r"\bfree cash flow\b",
    "important_segment_revenue": r"\b(data center|gaming|networking|automotive|dram|nand|hbm|cloud memory|business unit)\b",
}
TOPIC_RE = {key: re.compile(value, re.I) for key, value in TOPIC_PATTERNS.items()}
PERIOD_RE = re.compile(r"\b(20\d{2}|three months ended|six months ended|nine months ended|year ended|fiscal (?:year|quarter)|quarter ended|fq[1-4][- /]?\d{2,4}|q[1-4](?:\s*fy\d{2,4})?)\b", re.I)
MONTH_DAY_RE = re.compile(r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\b", re.I)
UNITS_RE = re.compile(r"\b(?:(?:in|usd|u\.s\. dollars?)\s+(?:millions|billions|thousands)|(?:millions|billions|thousands)\s+(?:of\s+)?(?:u\.s\.\s+)?dollars?|percent|except per share|per share)\b|[%$€£]", re.I)
BASIS_RE = re.compile(r"\b(non[- ]?gaap|gaap|adjusted|as reported)\b", re.I)
NUMERIC_RE = re.compile(r"(?<![A-Za-z])(?:\(?[−–-]?\s*[$€£]?\s*\d[\d,.]*(?:\s*(?:million|billion|trillion|%))?\)?)")
BLOCK_TAGS = {"div", "p", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol", "li"}
SKIP_TAGS = {"script", "style", "noscript", "ix:header"}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def visible_text(tag: Tag | str) -> str:
    # NFC and whitespace collapse preserve minus signs, currency, percentages,
    # ranges, scale words and the displayed digit sequence.
    raw = tag.get_text(" ", strip=True) if isinstance(tag, Tag) else str(tag)
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", raw)).strip()


def dom_path(tag: Tag) -> str:
    parts = []
    current: Tag | None = tag
    while current is not None and current.name and current.name != "[document]":
        name = current.name.lower()
        if isinstance(current.parent, Tag):
            siblings = [s for s in current.parent.find_all(name, recursive=False)]
            index = next((i for i, s in enumerate(siblings, 1) if s is current), 1)
        else:
            index = 1
        parts.append(f"{name}:nth-of-type({index})")
        current = current.parent if isinstance(current.parent, Tag) else None
    return " > ".join(reversed(parts))


def _rows(table: Tag) -> list[Tag]:
    return [r for r in table.find_all("tr") if r.find_parent("table") is table]


def _cells(row: Tag) -> list[Tag]:
    return [c for c in row.find_all(["td", "th"], recursive=False)]


def _span(cell: Tag, name: str) -> int:
    try:
        return max(1, min(100, int(cell.get(name, 1))))
    except (ValueError, TypeError):
        return 1


def table_grid(table: Tag) -> list[dict]:
    """Return display cells with inherited row/column spans and raw row tags."""
    output = []
    pending: dict[int, tuple[str, int]] = {}
    for row in _rows(table):
        grid: dict[int, str] = {}
        next_pending: dict[int, tuple[str, int]] = {}
        for col, (value, remaining) in pending.items():
            grid[col] = value
            if remaining > 1:
                next_pending[col] = (value, remaining - 1)
        entries = []
        col = 0
        for cell in _cells(row):
            while col in grid:
                col += 1
            value = visible_text(cell)
            colspan, rowspan = _span(cell, "colspan"), _span(cell, "rowspan")
            for offset in range(colspan):
                grid[col + offset] = value
                if rowspan > 1:
                    next_pending[col + offset] = (value, rowspan - 1)
            entries.append({"column_start": col, "colspan": colspan,
                            "rowspan": rowspan, "tag": cell.name.lower(), "text": value,
                            "dom_path": dom_path(cell)})
            col += colspan
        pending = next_pending
        width = max(grid, default=-1) + 1
        output.append({"row_tag": row, "grid": [grid.get(i, "") for i in range(width)],
                       "cells": entries})
    return output


def _topic_hits(text: str) -> list[str]:
    return [topic for topic, pattern in TOPIC_RE.items() if pattern.search(text)]


def _role(text: str, heading: str) -> str:
    together = f"{heading} {text}".lower()
    if re.search(r"\b(guidance|outlook|expects?|forecast|next quarter|future|anticipates?)\b", together):
        return "future_guidance"
    if re.search(r"\b(year.over.year|prior year|same period last year|compared (?:with|to))\b", together):
        return "year_over_year_comparison"
    if re.search(r"\b(said|commented|according to|believe|strategy)\b", together):
        return "management_commentary"
    if _topic_hits(together):
        return "historical_result"
    return "other"


def _looks_heading(tag: Tag, text: str) -> bool:
    if tag.name.lower() in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        return True
    if len(text) > 160 or not text:
        return False
    bold = tag.find(["b", "strong"])
    if bold is not None and visible_text(bold) == text:
        return True
    return text.endswith(":") and not NUMERIC_RE.search(text)


def _structural_boilerplate(text: str, source_kind: str) -> str | None:
    """Only exact SEC cover labels; none of the earnings text is discarded."""
    if source_kind != "SEC_FILINGS":
        return None
    fixed = {"united states", "securities and exchange commission", "washington, d.c. 20549",
             "form 8-k", "current report", "pursuant to section 13 or 15(d) of the securities exchange act of 1934"}
    return "EXACT_SEC_COVER_LABEL" if text.lower().strip(" .") in fixed else None


def parse_html(raw: bytes, meta: dict) -> list[dict]:
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup.find_all(SKIP_TAGS):
        tag.decompose()
    root = soup.body or soup
    blocks: list[dict] = []
    heading = ""
    table_index = 0
    source_hash = meta["raw_body_sha256"]
    document_id = meta["document_id"]

    def append_block(tag: Tag, kind: str, raw_text: str, text: str, *,
                     table_index_value: int | None = None, row_index: int | None = None,
                     column_headers: list[str] | None = None, row_header: str | None = None,
                     units_hint: str | None = None, period_hint: str | None = None,
                     basis_hint: str | None = None, table_cells: list[dict] | None = None,
                     header_relation: str | None = None) -> None:
        if not raw_text:
            return
        sequence = len(blocks)
        boilerplate = _structural_boilerplate(raw_text, meta["source_kind"])
        block = {
            "block_id": f"{digest((document_id + '|' + str(sequence) + '|' + dom_path(tag)).encode())[:20]}",
            "document_id": document_id, "event_group_id": meta["event_group_id"],
            "symbol": meta["symbol"], "announcement_date": meta["announcement_date"],
            "source_kind": meta["source_kind"], "source_url": meta["source_url"],
            "source_sha256": source_hash, "sequence": sequence,
            "element_kind": kind, "dom_path": dom_path(tag), "heading": heading,
            "raw_text": raw_text, "raw_text_sha256": digest(raw_text.encode("utf-8")),
            "text": text, "text_sha256": digest(text.encode("utf-8")),
            "topic_hits": _topic_hits(f"{heading} {raw_text}"),
            "content_role": _role(raw_text, heading),
            "selection_status": "STRUCTURAL_BOILERPLATE" if boilerplate else "AVAILABLE",
            "selection_reason": boilerplate,
            "table_index": table_index_value, "row_index": row_index,
            "column_headers": column_headers, "row_header": row_header,
            "units_hint": units_hint, "period_hint": period_hint, "basis_hint": basis_hint,
            "table_cells": table_cells, "header_relation_status": header_relation,
            "footnote_refs": re.findall(r"(?<!\w)(?:\([a-z0-9]{1,2}\)|\*{1,3})(?!\w)", raw_text, re.I),
            "raw_locator_kind": "DOM_PATH_NOT_BYTE_OFFSET",
        }
        blocks.append(block)

    def parse_table(table: Tag) -> None:
        nonlocal table_index
        table_index += 1
        rows = table_grid(table)
        if not rows:
            return
        title = heading
        # SEC tables may place source-backed period rows after title and unit
        # rows.  The v2 first-four-rows guard missed the Micron FQ headers and
        # the NVIDIA cash-flow statement's later date rows.  Stop header
        # discovery at the first actual topic-bearing numeric data row.
        first_data_row = next((i for i, row in enumerate(rows)
                               if _topic_hits(visible_text(row["row_tag"]))
                               and any(NUMERIC_RE.search(e["text"]) for e in row["cells"][1:])),
                              len(rows))
        header_rows: list[list[str]] = []
        source_header_rows: list[str] = []
        for i, row in enumerate(rows):
            cells = row["grid"]
            raw_text = visible_text(row["row_tag"])
            if not raw_text:
                continue
            if i < first_data_row and (PERIOD_RE.search(raw_text) or MONTH_DAY_RE.search(raw_text)
                                       or UNITS_RE.search(raw_text) or BASIS_RE.search(raw_text)):
                header_rows.append(cells)
                source_header_rows.append(raw_text)
            # The full row and its cell locations are retained even when the
            # header relationship cannot be established.
            headers = [" | ".join(dict.fromkeys(h[j] for h in header_rows if j < len(h) and h[j]))
                       for j in range(len(cells))]
            nonempty = [e["text"] for e in row["cells"] if e["text"]]
            row_header = next((x for x in nonempty if not NUMERIC_RE.fullmatch(x)), None)
            period_parts = [x for x in [title] + headers if PERIOD_RE.search(x) or MONTH_DAY_RE.search(x)]
            unit_parts = [x for x in [title, raw_text] + headers if UNITS_RE.search(x)]
            basis_parts = [x for x in [title, raw_text] + headers if BASIS_RE.search(x)]
            period_hint = " | ".join(dict.fromkeys(period_parts)) or None
            units_hint = " | ".join(dict.fromkeys(unit_parts)) or None
            basis_hint = " | ".join(dict.fromkeys(basis_parts)) or None
            relation = "EXPLICIT_CONTEXT" if row_header and period_hint and units_hint else "AMBIGUOUS_CONTEXT"
            cell_parts = []
            for entry in row["cells"]:
                if not entry["text"]:
                    continue
                col = entry["column_start"]
                head = headers[col] if col < len(headers) else ""
                cell_parts.append(f"col {col + 1}" + (f" [{head}]" if head else "") + f": {entry['text']}")
            # Both the normalized column mapping and the exact source strings
            # enter the *actual* adapter text.  Source-row copies are required
            # because a reformatted "col 4: ..." row is not a verbatim quote.
            # Header copies are tied to this same table before its first data
            # row; no period is inferred when the source has none.
            source_context = " | ".join(source_header_rows) if i >= first_data_row else ""
            text = (f"TABLE {table_index}; heading: {title or 'UNKNOWN'}; "
                    f"period: {period_hint or 'UNKNOWN'}; units: {units_hint or 'UNKNOWN'}; "
                    f"basis: {basis_hint or 'UNKNOWN'}; "
                    f"row {i + 1}; " + " | ".join(cell_parts)
                    + (f"; source_header_rows: {source_context}" if source_context else "")
                    + f"; source_row: {raw_text}")
            append_block(row["row_tag"], "table_row", raw_text, text,
                         table_index_value=table_index, row_index=i + 1,
                         column_headers=headers, row_header=row_header,
                         units_hint=units_hint, period_hint=period_hint, basis_hint=basis_hint,
                         table_cells=row["cells"], header_relation=relation)

    def walk(node: Tag) -> None:
        nonlocal heading
        if node.name == "table":
            parse_table(node)
            return
        child_blocks = [c for c in node.children if isinstance(c, Tag) and c.name in BLOCK_TAGS]
        if child_blocks:
            for child in node.children:
                if isinstance(child, Tag):
                    walk(child)
                elif isinstance(child, NavigableString):
                    direct = visible_text(str(child))
                    if direct and len(direct) > 2:
                        append_block(node, "paragraph", direct, direct)
            return
        if node.name in {"head", "meta", "title", "link"}:
            return
        raw_text = visible_text(node)
        if not raw_text:
            return
        if _looks_heading(node, raw_text):
            heading = raw_text
            append_block(node, "heading", raw_text, raw_text)
        else:
            append_block(node, "paragraph", raw_text, raw_text)

    for child in root.children:
        if isinstance(child, Tag):
            walk(child)
        elif isinstance(child, NavigableString):
            direct = visible_text(str(child))
            if direct and len(direct) > 2:
                append_block(root, "paragraph", direct, direct)
    return blocks
