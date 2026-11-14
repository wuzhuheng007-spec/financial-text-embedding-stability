"""Render the wire-v2 label vocabulary directly from its JSON Schema.

This helper has no model or article dependency.  A prompt test compares its
output with the embedded table so the two cannot silently diverge.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


LABEL_PATHS = (
    ("schema_version", ("properties", "schema_version")),
    ("article_disposition", ("properties", "article_disposition")),
    ("article_unknown_reason", ("properties", "article_unknown_reason")),
    ("market_outcome_reported", ("properties", "market_outcome_reported")),
    ("market_outcome_direction", ("properties", "market_outcome_direction")),
    ("date_conflict", ("properties", "date_conflict")),
    ("identity_conflict", ("properties", "identity_conflict")),
    ("future_return_status", ("properties", "future_return_status")),
    ("article_evidence[].role", ("$defs", "span", "properties", "role")),
    ("records[].company_role", ("$defs", "company_event", "properties", "company_role")),
    ("records[].identity_status", ("$defs", "company_event", "properties", "identity_status")),
    ("records[].business_impact_direction", ("$defs", "company_event", "properties", "business_impact_direction")),
    ("records[].impact_path", ("$defs", "company_event", "properties", "impact_path")),
    ("records[].statement_status", ("$defs", "company_event", "properties", "statement_status")),
    ("records[].unknown_reason", ("$defs", "company_event", "properties", "unknown_reason")),
    ("records[].evidence_spans[].role", ("$defs", "span", "properties", "role")),
    ("records[].relationship_evidence_spans[].role", ("$defs", "span", "properties", "role")),
)


def at_path(schema: dict, path: tuple[str, ...]) -> dict:
    node = schema
    for part in path:
        node = node[part]
    while "$ref" in node:
        ref = node["$ref"]
        if not ref.startswith("#/"):
            raise ValueError(f"external reference is not allowed: {ref}")
        node = at_path(schema, tuple(ref[2:].split("/")))
    return node


def render_label_table(schema: dict) -> str:
    rows = []
    for field, path in LABEL_PATHS:
        node = at_path(schema, path)
        if "enum" in node:
            values = node["enum"]
        elif "const" in node:
            values = [node["const"]]
        else:
            raise ValueError(f"no enum or const at {field}")
        if not values or any(not isinstance(value, str) for value in values):
            raise ValueError(f"non-string or empty vocabulary at {field}")
        rows.append(f"{field}: {' | '.join(values)}")
    return "\n".join(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("schema", type=Path)
    args = parser.parse_args()
    print(render_label_table(json.loads(args.schema.read_text(encoding="utf-8"))))


if __name__ == "__main__":
    main()
