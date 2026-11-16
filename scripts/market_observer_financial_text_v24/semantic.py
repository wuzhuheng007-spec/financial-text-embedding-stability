"""Source-located table semantics, independent of financial-topic selection.

This module never reads reviewed facts, candidate IDs, scores or market labels.
Its EXPLICIT status means an HTML relationship was found by a stated rule; only
a separate source review can decide whether that relationship is correct.
"""
from __future__ import annotations

from hashlib import sha256
import re
from typing import Any

from bs4 import BeautifulSoup, Tag

from scripts.market_observer_financial_text_v21.parser import dom_path, visible_text
from scripts.market_observer_financial_text_v23.structure import _grid, _topics


SCHEMA = "financial_table_semantics_v24_dev1"
QUARTER = re.compile(r"^(?:FQ[1-4][-/ ]?\d{2,4}|Q[1-4](?:\s*(?:FY\s*)?\d{2,4})?|FY\s*\d{2,4})$", re.I)
DATE = re.compile(r"^(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+20\d{2}$", re.I)
YEAR = re.compile(r"^20\d{2}$")
DURATION = re.compile(r"\b(?:three|six|nine|twelve)\s+months?\s+ended\b|\b(?:quarter|year)\s+ended\b", re.I)
COMPARISON = re.compile(r"^(?:Q\s*/\s*Q|Y\s*/\s*Y|QOQ|YOY|quarter[- ]over[- ]quarter|year[- ]over[- ]year)$", re.I)
BASIS_NON = re.compile(r"\bnon[- ]?GAAP\b|\badjusted\b", re.I)
BASIS_GAAP = re.compile(r"\bGAAP\b|\bas reported\b", re.I)
SCALE = re.compile(r"\b(thousands?|millions?|billions?|trillions?)\b", re.I)
UNIT_DECLARATION = re.compile(
    r"^\s*\(?\s*(?:(?:[$€£]|USD|U\.S\.\s*dollars?)\s+)?"
    r"(?:(?:amounts?|dollars?)\s+)?(?:in\s+)(?:thousands?|millions?|billions?|trillions?)"
    r"(?:\s*(?:,|;)?\s*except\s+(?:for\s+)?(?:earnings\s+)?per[- ]share(?:\s+(?:amounts?|data))?)?\s*\)?\s*$",
    re.I,
)
UNIT_DECLARATION_ALT = re.compile(
    r"^\s*(?:units?\s*[:=]\s*|amounts?\s+in\s+)"
    r"(?:thousands?|millions?|billions?|trillions?)"
    r"(?:\s*(?:,|;)?\s*except\s+(?:for\s+)?(?:earnings\s+)?per[- ]share(?:\s+(?:amounts?|data))?)?\s*$",
    re.I,
)
PER_SHARE_EXCEPTION = re.compile(r"\bexcept\s+(?:for\s+)?(?:earnings\s+)?per[- ]share\b", re.I)
PER_SHARE_METRIC = re.compile(r"\b(?:earnings?\s+per[- ]share|per[- ]share\s+(?:earnings?|amounts?)|EPS)\b", re.I)
SHARE_METRIC = re.compile(r"\b(?:shares?\s+(?:outstanding|used|issued)|weighted[- ]average\s+shares?)\b", re.I)
PERCENT_METRIC = re.compile(r"\b(?:percent(?:age)?\s+of|margin\s*\(?%\)?)\b", re.I)
CURRENCY_MARKER = {"$", "€", "£"}
PERCENT_MARKER = {"%"}
MEASURE = re.compile(
    r"^\s*\(?\s*[−–-]?\s*[$€£]?\s*\d[\d,]*(?:\.\d+)?\s*\)?"
    r"(?:\s*(?:%|pts?|points?|bps|basis\s+points?))?"
    r"(?:\s*(?:\([a-z0-9]{1,2}\)|\*{1,3}))?\s*$", re.I,
)
FOOTNOTE_MARKER = re.compile(r"(?:\w|\d)\s*\(([1-9]|[a-z])\)\s*$", re.I)


def _hash(raw: bytes) -> str:
    return sha256(raw).hexdigest()


def _basis(text: str) -> str | None:
    non = bool(BASIS_NON.search(text))
    remainder = BASIS_NON.sub("", text)
    gaap = bool(BASIS_GAAP.search(remainder))
    if non and gaap:
        return "CONFLICT"
    return "Non-GAAP" if non else "GAAP" if gaap else None


def _period(text: str) -> bool:
    text = text.strip()
    return bool(QUARTER.fullmatch(text) or DATE.fullmatch(text) or YEAR.fullmatch(text)
                or DURATION.search(text))


def _unit_declaration(text: str) -> bool:
    return bool(UNIT_DECLARATION.fullmatch(text) or UNIT_DECLARATION_ALT.fullmatch(text))


def _scale(text: str) -> str | None:
    match = SCALE.search(text)
    if not match:
        return None
    return match.group(1).lower().rstrip("s") + "s"


def _measure(text: str) -> bool:
    text = text.strip()
    return bool(text and not _period(text) and not COMPARISON.fullmatch(text)
                and MEASURE.fullmatch(text))


def _roles(cell: dict) -> list[str]:
    text = cell["text"].strip()
    if not text:
        return ["BLANK"]
    roles = []
    if text in CURRENCY_MARKER:
        roles.append("CURRENCY_MARKER")
    if text in PERCENT_MARKER:
        roles.append("PERCENT_MARKER")
    if _unit_declaration(text):
        roles.append("UNIT_DECLARATION")
    if _basis(text) is not None and not _measure(text):
        roles.append("BASIS_LABEL")
    if _period(text):
        roles.append("PERIOD_LABEL")
    if COMPARISON.fullmatch(text):
        roles.append("COMPARISON_LABEL")
    if _measure(text):
        roles.append("NUMERIC_MEASURE")
    if not roles:
        roles.append("TEXT_LABEL")
    return roles


def _metric_label_cell(cell: dict) -> bool:
    if "TEXT_LABEL" in cell["roles"]:
        return True
    if "BASIS_LABEL" not in cell["roles"]:
        return False
    # A basis-qualified metric is a data-row label, while a bare GAAP label
    # (possibly with a footnote marker) remains a column/subtable heading.
    remainder = BASIS_GAAP.sub("", BASIS_NON.sub("", cell["text"]))
    remainder = FOOTNOTE_MARKER.sub("", remainder)
    return bool(re.search(r"[A-Za-z]{2,}", remainder))


def _relation(value: Any, status: str, sources: list[str], scope: str, reason: str) -> dict:
    return {"value": value, "status": status, "source_dom_paths": list(dict.fromkeys(sources)),
            "scope_id": scope, "reason": reason}


def _overlap(a: dict, b: dict) -> bool:
    return a["column_start"] < b["column_end"] and b["column_start"] < a["column_end"]


def _classify_row(cells: list[dict], inherited_labels: list[dict]) -> str:
    substantive = [c for c in cells if "BLANK" not in c["roles"]]
    if not substantive:
        return "SPACER"
    measures = [c for c in cells if "NUMERIC_MEASURE" in c["roles"]]
    if measures:
        first = min(c["column_start"] for c in measures)
        labels = [c for c in inherited_labels + cells if c["column_start"] < first
                  and _metric_label_cell(c)]
        if labels:
            return "DATA"
    has_unit = any("UNIT_DECLARATION" in c["roles"] for c in cells)
    has_basis = any("BASIS_LABEL" in c["roles"] for c in cells)
    has_period = any("PERIOD_LABEL" in c["roles"] or "COMPARISON_LABEL" in c["roles"]
                     for c in cells)
    if has_unit and has_basis:
        return "UNIT_AND_BASIS_HEADER"
    if has_unit and has_period:
        return "UNIT_AND_COLUMN_HEADER"
    if has_basis:
        return "BASIS_HEADER"
    if has_period:
        return "PERIOD_HEADER"
    if has_unit:
        return "UNIT_HEADER"
    return "TITLE" if not measures else "UNRESOLVED"


def _explicit_preamble(table: Tag) -> list[dict]:
    """Only a structurally adjacent, complete unit declaration may scope a table."""
    found = []
    parent = table.parent
    if not isinstance(parent, Tag):
        return found
    for sibling in reversed(list(table.previous_siblings)):
        if not isinstance(sibling, Tag):
            continue
        if sibling.name in {"table", "h1", "h2", "h3", "h4", "h5", "h6"}:
            break
        text = visible_text(sibling)
        if _unit_declaration(text):
            found.append({"text": text, "dom_path": dom_path(sibling),
                          "roles": ["UNIT_DECLARATION"], "source": "TABLE_PREAMBLE"})
        elif text:
            # A business narrative is not an implicit unit declaration.
            break
    return list(reversed(found))


def _one_semantic(candidates: list[dict], value_fn, scope: str, reason: str) -> dict:
    vals = [(value_fn(c["text"]), c["dom_path"]) for c in candidates]
    vals = [(v, p) for v, p in vals if v is not None]
    unique = {v for v, _ in vals}
    if len(unique) > 1:
        return _relation(None, "CONFLICT", [p for _, p in vals], scope, reason + ": conflicting explicit labels")
    if not vals:
        return _relation(None, "UNKNOWN", [], scope, reason + ": no explicit label")
    return _relation(vals[0][0], "EXPLICIT", [p for _, p in vals], scope, reason)


def _footnote_relation(candidates: list[dict], table: Tag, scope: str) -> dict:
    """A marker needs a direct HTML link before its definition is bound."""
    marked = [c for c in candidates if FOOTNOTE_MARKER.search(c["text"])]
    if not marked:
        return _relation(None, "NOT_APPLICABLE", [], scope, "no footnote marker on applicable value or labels")
    source_paths = [c["dom_path"] for c in marked]
    targets = []
    for cell in marked:
        tag = cell.get("tag_obj")
        if not isinstance(tag, Tag):
            continue
        for anchor in tag.find_all("a", href=True):
            href = anchor.get("href", "")
            if not href.startswith("#") or len(href) == 1:
                continue
            target = table.find(id=href[1:])
            if target is None:
                document = table.find_parent("html") or table
                target = document.find(id=href[1:])
            if target is not None:
                targets.append(dom_path(target))
    if len(targets) == len(marked):
        return _relation("DIRECT_HTML_LINK", "EXPLICIT", source_paths + targets, scope,
                         "footnote marker has resolvable direct HTML anchor")
    return _relation(None, "UNKNOWN", source_paths, scope,
                     "footnote marker present but direct HTML definition link unresolved")


def _table_bindings(table: Tag, document_id: str, source_sha: str) -> dict:
    table_path = dom_path(table)
    table_id = "T" + _hash((document_id + "|" + table_path).encode("utf-8"))[:16]
    rows, physical_cells = _grid(table)
    for c in physical_cells:
        c["roles"] = _roles(c)
    preamble = _explicit_preamble(table)
    caption = table.find("caption", recursive=False)
    if caption is not None:
        text = visible_text(caption)
        if _unit_declaration(text):
            preamble.append({"text": text, "dom_path": dom_path(caption),
                             "roles": ["UNIT_DECLARATION"], "source": "TABLE_CAPTION"})
    output_rows = []
    bindings = []
    headers: list[dict] = []
    unit_headers: list[dict] = list(preamble)
    seen_data = False
    region_index = 0
    region_id = f"{table_id}:region:{region_index}"
    row_regions = {}

    for row in rows:
        cells = row["cells"]
        inherited = [c for c in physical_cells if c["row_start"] < row["row_index"] < c["row_end"]
                     and row_regions.get(c["row_start"]) == region_id]
        role = _classify_row(cells, inherited)
        is_header = role.endswith("HEADER")
        if is_header and seen_data:
            # A period-only repeated heading starts a new data region but does
            # not revoke a table-wide unit or accounting-basis declaration.
            # A new explicit declaration supersedes the prior region instead.
            carries_unit = not any("UNIT_DECLARATION" in c["roles"] for c in cells)
            carries_basis = not any("BASIS_LABEL" in c["roles"] for c in cells)
            prior_units = list(unit_headers) if carries_unit else []
            prior_basis = [h for h in headers if "BASIS_LABEL" in h["roles"]] if carries_basis else []
            region_index += 1
            region_id = f"{table_id}:region:{region_index}"
            headers = prior_basis
            unit_headers = prior_units
            seen_data = False
        row_regions[row["row_index"]] = region_id
        output_rows.append({
            "row_index": row["row_index"], "row_dom_path": dom_path(row["row_tag"]),
            "role": role, "subtable_id": region_id,
            "cells": [{"dom_path": c["dom_path"], "text": c["text"],
                       "row_start": c["row_start"], "row_end": c["row_end"],
                       "column_start": c["column_start"], "column_end": c["column_end"],
                       "tag": c["tag"], "roles": c["roles"],
                       "geometry_status": c["span_status"]} for c in cells],
        })
        if is_header:
            for c in cells:
                if "BLANK" in c["roles"]:
                    continue
                if "UNIT_DECLARATION" in c["roles"]:
                    unit_headers.append(c)
                if any(x in c["roles"] for x in ("PERIOD_LABEL", "COMPARISON_LABEL", "BASIS_LABEL")):
                    headers.append(c)
            continue
        if role != "DATA":
            continue
        seen_data = True
        measures = [c for c in cells if "NUMERIC_MEASURE" in c["roles"]]
        first_col = min(c["column_start"] for c in measures)
        labels = [c for c in inherited + cells if c["column_start"] < first_col
                  and _metric_label_cell(c)]
        for c in measures:
            row_labels = [x for x in labels if x["column_end"] <= c["column_start"]]
            metric = " ".join(x["text"] for x in row_labels).strip()
            row_rel = _relation(metric if len(row_labels) == 1 else None,
                                "EXPLICIT" if len(row_labels) == 1 else "UNKNOWN" if not row_labels else "CONFLICT",
                                [x["dom_path"] for x in row_labels], region_id, "left row label with grid span")
            col_headers = [h for h in headers if _overlap(h, c) and
                           any(k in h["roles"] for k in ("PERIOD_LABEL", "COMPARISON_LABEL"))]
            if col_headers:
                min_width = min(h["column_end"] - h["column_start"] for h in col_headers)
                most_specific = [h for h in col_headers if h["column_end"] - h["column_start"] == min_width]
                chosen_header = max(most_specific, key=lambda h: h["row_start"])
                unique_headers = {h["text"] for h in most_specific}
                col_status = "CONFLICT" if len(unique_headers) > 1 else "EXPLICIT"
                col_rel = _relation(chosen_header["text"] if col_status == "EXPLICIT" else None,
                                    col_status, [h["dom_path"] for h in col_headers], region_id,
                                    "semantic period/comparison header in intersecting logical column")
            else:
                chosen_header = None
                col_rel = _relation(None, "UNKNOWN", [], region_id, "no semantic column header")
            comparison_label = (chosen_header["text"] if chosen_header is not None
                                and "COMPARISON_LABEL" in chosen_header["roles"] else None)
            period_rel = (
                _relation(None, "NOT_APPLICABLE", [chosen_header["dom_path"]], region_id,
                          "comparison header describes a change, not a calendar period")
                if comparison_label is not None and chosen_header is not None else
                _relation(
                    chosen_header["text"] if chosen_header is not None
                    and "PERIOD_LABEL" in chosen_header["roles"] and col_rel["status"] == "EXPLICIT" else None,
                    "EXPLICIT" if chosen_header is not None and "PERIOD_LABEL" in chosen_header["roles"]
                    and col_rel["status"] == "EXPLICIT" else "UNKNOWN" if col_rel["status"] != "CONFLICT" else "CONFLICT",
                    [chosen_header["dom_path"]] if chosen_header is not None and "PERIOD_LABEL" in chosen_header["roles"] else [],
                    region_id, "source quarter/date header"))

            applicable_units = [u for u in unit_headers if u.get("source") in {"TABLE_PREAMBLE", "TABLE_CAPTION"}
                                or u.get("row_start", -1) <= c["row_start"]]
            unit_decl = applicable_units[-1] if applicable_units else None
            scale_rel = _one_semantic(applicable_units, _scale, region_id, "explicit table unit declaration")
            has_exception = any(PER_SHARE_EXCEPTION.search(u["text"]) for u in applicable_units)
            nearby_currency = [x for x in cells if x["column_end"] == c["column_start"]
                               and "CURRENCY_MARKER" in x["roles"]]
            nearby_percent = [x for x in cells if x["column_start"] == c["column_end"]
                              and "PERCENT_MARKER" in x["roles"]]
            direct_currency = next((s for s in CURRENCY_MARKER if s in c["text"]), None)
            currency_sources = [c["dom_path"]] if direct_currency else [x["dom_path"] for x in nearby_currency]
            currency = direct_currency or (nearby_currency[0]["text"] if nearby_currency else None)
            percent = "%" in c["text"] or bool(nearby_percent)
            points = bool(re.search(r"\b(?:pts?|points?|bps|basis\s+points?)\b", c["text"], re.I))
            if points:
                kind = "percentage_points"
            elif comparison_label is not None:
                kind = "percent_change" if percent else "percent_change"
            elif percent or PERCENT_METRIC.search(metric):
                kind = "percentage"
            elif PER_SHARE_METRIC.search(metric) or (has_exception and "per share" in metric.lower()):
                kind = "per_share_amount"
            elif SHARE_METRIC.search(metric):
                kind = "shares"
            else:
                kind = "amount"
            kind_sources = ([x["dom_path"] for x in row_labels]
                            + ([chosen_header["dom_path"]] if comparison_label is not None and chosen_header else [])
                            + ([c["dom_path"]] if percent or points or currency else [])
                            + [x["dom_path"] for x in nearby_percent]
                            + ([unit_decl["dom_path"]] if unit_decl and has_exception else []))
            kind_rel = _relation(kind, "EXPLICIT", kind_sources, region_id,
                                 "row metric, visible value/marker and table exception")
            if kind in {"percentage", "percent_change", "percentage_points", "per_share_amount"}:
                scale_rel = _relation(None, "NOT_APPLICABLE", [unit_decl["dom_path"]] if unit_decl else [],
                                      region_id, "percent/comparison or explicit per-share exception excludes amount scale")
            currency_rel = (_relation(None, "NOT_APPLICABLE", [], region_id, "non-monetary measurement")
                            if kind in {"percentage", "percent_change", "percentage_points", "shares"}
                            else _relation(currency, "EXPLICIT" if currency else "UNKNOWN", currency_sources,
                                           region_id, "direct or adjacent currency symbol only; no table-wide guess"))
            unit_source_paths = [u["dom_path"] for u in applicable_units]
            unit = {"currency": currency_rel, "scale": scale_rel,
                    "measurement_kind": kind_rel,
                    "source_dom_paths": list(dict.fromkeys(unit_source_paths + currency_sources
                                                            + [x["dom_path"] for x in nearby_percent])),
                    "scope_id": region_id,
                    "reason": "explicit table declaration plus cell/row-specific markers"}

            row_basis = [x for x in row_labels if _basis(x["text"]) is not None]
            col_basis = [h for h in headers if "BASIS_LABEL" in h["roles"] and _overlap(h, c)]
            basis_candidates = row_basis + col_basis
            basis_rel = _one_semantic(basis_candidates, _basis, region_id,
                                      "explicit row or spanning column accounting basis")
            basis_path = basis_rel["source_dom_paths"][0] if basis_rel["status"] == "EXPLICIT" else None
            footnote = _footnote_relation([c] + row_labels + col_headers + basis_candidates,
                                          table, region_id)
            statuses = [row_rel["status"], col_rel["status"], kind_rel["status"], basis_rel["status"]]
            if kind == "amount":
                statuses.append(scale_rel["status"])
            if kind in {"amount", "per_share_amount"}:
                statuses.append(currency_rel["status"])
            if footnote["status"] != "NOT_APPLICABLE":
                statuses.append(footnote["status"])
            if comparison_label is None:
                statuses.append(period_rel["status"])
            full_status = ("CONFLICT" if "CONFLICT" in statuses else
                           "MECHANICALLY_COMPLETE" if all(s == "EXPLICIT" for s in statuses)
                           else "PARTIAL_UNKNOWN")
            bindings.append({
                "value_cell_dom_path": c["dom_path"], "value_text": c["text"],
                "document_id": document_id, "table_id": table_id,
                "table_dom_path": table_path, "subtable_id": region_id,
                "row_index": c["row_start"], "column_start": c["column_start"],
                "column_end": c["column_end"], "geometry_status": c["span_status"],
                "row_metric_text": metric if len(row_labels) == 1 else None,
                "row_metric": row_rel,
                "row_label_dom_path": row_labels[0]["dom_path"] if len(row_labels) == 1 else None,
                "row_label": row_rel,
                "column_header_dom_path": chosen_header["dom_path"] if chosen_header else None,
                "column_header_dom_paths": col_rel["source_dom_paths"],
                "column_header": col_rel,
                "period_text": period_rel["value"], "period": period_rel,
                "comparison_label": comparison_label,
                "unit_dom_path": unit_decl["dom_path"] if unit_decl else None,
                "currency": currency_rel["value"], "scale": scale_rel["value"],
                "measurement_kind": kind, "unit": unit,
                "basis": basis_rel["value"], "basis_dom_path": basis_path,
                "basis_relation": basis_rel, "footnote": footnote,
                "topics": _topics(metric),  # late annotation only; never controls row role.
                "full_status": full_status,
                "semantic_review_status": "NOT_REVIEWED_BY_PRODUCTION_PARSER",
            })

    return {"table_id": table_id, "dom_path": table_path,
            "row_count": len(rows), "physical_cell_count": len(physical_cells),
            "rows": output_rows, "bindings": bindings,
            "candidate_not_confirmed": True}


def parse_document(raw_html: bytes, meta: dict, table_dom_paths: list[str]) -> dict:
    """Parse only externally selected tables from a verified full raw document.

    The filter is a source-coordinate selection, not a candidate answer list.
    An empty filter is rejected to prevent accidental full-corpus analysis.
    """
    if not table_dom_paths or len(table_dom_paths) != len(set(table_dom_paths)):
        raise ValueError("nonempty distinct table_dom_paths required")
    document_id = meta["document_id"]
    expected = meta["raw_body_sha256"]
    if _hash(raw_html) != expected:
        raise ValueError("source HTML hash mismatch")
    soup = BeautifulSoup(raw_html, "html.parser")
    available = {dom_path(t): t for t in soup.find_all("table")}
    missing = [p for p in table_dom_paths if p not in available]
    if missing:
        raise ValueError(f"selected table DOM path not found: {len(missing)}")
    tables = [_table_bindings(available[p], document_id, expected) for p in table_dom_paths]
    return {"schema": SCHEMA, "document_id": document_id, "source_sha256": expected,
            "table_dom_paths": list(table_dom_paths), "tables": tables,
            "semantic_review_status": "NOT_REVIEWED_BY_PRODUCTION_PARSER",
            "candidate_not_confirmed": True}
