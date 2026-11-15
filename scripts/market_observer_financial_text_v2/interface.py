"""Unfitted feature input and conservative per-cell table context helpers."""
from __future__ import annotations

import re


def cell_context(block: dict, cell: dict) -> dict:
    """Expose a column-specific *candidate* context, not a confirmed fact.

    A table-wide hint can mix GAAP and non-GAAP columns. No fact card should
    promote this helper output without checking the raw row and source table.
    """
    if block.get("element_kind") != "table_row":
        raise ValueError("cell_context requires a table row block")
    col = cell["column_start"]
    headers = block.get("column_headers") or []
    header = headers[col] if col < len(headers) else ""
    row_header = block.get("row_header")
    context = f"{row_header or ''} {header}".lower()
    non_gaap = bool(re.search(r"\bnon[- ]?gaap\b", context))
    gaap_without_non = bool(re.search(r"\bgaap\b", re.sub(r"\bnon[- ]?gaap\b", "", context)))
    if non_gaap and gaap_without_non:
        basis = None
        status = "AMBIGUOUS_COLUMN_BASIS"
    elif non_gaap:
        basis = "NON_GAAP_CANDIDATE"
        status = "CANDIDATE_ONLY"
    elif gaap_without_non:
        basis = "GAAP_CANDIDATE"
        status = "CANDIDATE_ONLY"
    else:
        basis = None
        status = "UNRESOLVED_COLUMN_BASIS"
    return {
        "block_id": block["block_id"], "cell_dom_path": cell["dom_path"],
        "column_start": col, "column_header": header or None,
        "row_header": row_header, "period_hint": block.get("period_hint"),
        "units_hint": block.get("units_hint"), "column_basis_candidate": basis,
        "basis_status": status,
        "source_locator_kind": "DOM_PATH_NOT_BYTE_OFFSET",
        "fact_confirmation": "NOT_CONFIRMED",
    }


def feature_record(event_input: dict) -> dict:
    """Return a format-checked future text feature input without fitting."""
    keys = {"event_group_id", "selected_text", "selected_text_sha256",
            "selected_block_ids", "omitted_blocks", "max_characters"}
    if not keys.issubset(event_input):
        raise ValueError("missing v2 feature input field")
    if len(event_input["selected_text"]) > event_input["max_characters"]:
        raise ValueError("feature input exceeds budget")
    if not isinstance(event_input["selected_block_ids"], list) or not isinstance(event_input["omitted_blocks"], list):
        raise TypeError("provenance lists required")
    return {
        "schema": "financial_text_v2_unfitted_feature_input_v1",
        "event_group_id": event_input["event_group_id"],
        "text": event_input["selected_text"],
        "text_sha256": event_input["selected_text_sha256"],
        "source_block_ids": list(event_input["selected_block_ids"]),
        "omitted_block_ids": [r["block_id"] for r in event_input["omitted_blocks"]],
        "fit_status": "NOT_FITTED",
        "text_scope": "TRAINING_ANNOUNCEMENT_ENGINEERING_ONLY",
    }
