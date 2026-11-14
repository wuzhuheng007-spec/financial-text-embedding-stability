"""Mechanical checks for N1 company-event JSON; no semantic accuracy claim.

The caller retains model responses and row-level content in ignored local files.
This module returns only counts and error codes, never excerpts or body text.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


DEFAULT_SCHEMA = Path(__file__).resolve().parents[1] / "docs/news_research/company_event_output.schema.json"


def _span_list(output: dict[str, Any]):
    for span in output["article_evidence"]:
        yield span
    for record in output["records"]:
        for span in record["evidence_spans"]:
            yield span
        for span in record["relationship_evidence_spans"]:
            yield span


def validate_output(
    output: dict[str, Any],
    body: str,
    expected_binding: dict[str, Any],
    schema_path: Path = DEFAULT_SCHEMA,
) -> dict[str, Any]:
    """Validate one parsed response against exact original-body code-point spans.

    Returns safe aggregate checks. JSON parse errors/timeouts are runner attempt
    statuses outside this function and must not be called valid unknowns.
    """
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    errors = sorted(Draft202012Validator(schema).iter_errors(output), key=lambda e: tuple(map(str, e.path)))
    result: dict[str, Any] = {
        "schema_valid": not errors,
        "schema_error_codes": [
            {"path": "/" + "/".join(map(str, error.path)), "keyword": error.validator}
            for error in errors
        ],
        "binding_valid": False,
        "evidence_span_count": None,
        "evidence_span_exact_match_count": None,
        "mechanical_issue_codes": [],
        "mechanical_valid": False,
    }
    if errors:
        return result

    issues: list[str] = []
    expected_body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if expected_binding.get("body_sha256") != expected_body_hash:
        issues.append("EXPECTED_BINDING_BODY_HASH_MISMATCH")
    if output["input_binding"] != expected_binding:
        issues.append("OUTPUT_BINDING_MISMATCH")
    result["binding_valid"] = not any(
        code in {"EXPECTED_BINDING_BODY_HASH_MISMATCH", "OUTPUT_BINDING_MISMATCH"}
        for code in issues
    )

    spans = list(_span_list(output))
    exact_matches = 0
    for span in spans:
        start, end = span["start"], span["end"]
        if start >= end or end > len(body):
            issues.append("SPAN_OUT_OF_BOUNDS_OR_EMPTY")
        elif body[start:end] != span["text"]:
            issues.append("SPAN_SLICE_MISMATCH")
        else:
            exact_matches += 1
    result["evidence_span_count"] = len(spans)
    result["evidence_span_exact_match_count"] = exact_matches

    article_roles = {span["role"] for span in output["article_evidence"]}
    for flag, role in (
        ("market_outcome_reported", "MARKET_OUTCOME"),
        ("date_conflict", "DATE_CONFLICT"),
        ("identity_conflict", "IDENTITY_CONFLICT"),
    ):
        if output[flag] == "YES" and role not in article_roles:
            issues.append(f"{flag.upper()}_EVIDENCE_MISSING")

    disposition = output["article_disposition"]
    reason = output["article_unknown_reason"]
    if disposition == "COMPANY_EVENTS_FOUND" and reason != "NONE":
        issues.append("ARTICLE_REASON_INCONSISTENT")
    if disposition == "NO_IDENTIFIABLE_EVENT" and reason != "NO_EVENT":
        issues.append("ARTICLE_REASON_INCONSISTENT")
    if disposition == "NO_SUPPORTED_COMPANY" and reason != "NO_COMPANY_LINK":
        issues.append("ARTICLE_REASON_INCONSISTENT")
    if disposition == "UNDETERMINED" and reason == "NONE":
        issues.append("ARTICLE_REASON_INCONSISTENT")

    for record in output["records"]:
        roles = {span["role"] for span in record["evidence_spans"]}
        if not {"EVENT", "TARGET_COMPANY"}.issubset(roles):
            issues.append("EVENT_OR_COMPANY_EVIDENCE_MISSING")
        if record["impact_path"] == "INDIRECT" and not all(
            span["role"] == "RELATIONSHIP" for span in record["relationship_evidence_spans"]
        ):
            issues.append("INDIRECT_RELATIONSHIP_ROLE_MISMATCH")
        if record["business_impact_direction"] == "UNKNOWN" and record["unknown_reason"] == "NONE":
            issues.append("UNKNOWN_DIRECTION_REASON_MISSING")

    result["mechanical_issue_codes"] = sorted(set(issues))
    result["mechanical_valid"] = not issues
    return result
