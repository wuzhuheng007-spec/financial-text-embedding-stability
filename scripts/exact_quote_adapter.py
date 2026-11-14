"""Exact, unique quote grounding. No normalization, fuzzy repair or semantic edit.

Input binding and character offsets are host-computed fields, never model
performance metrics. The caller must validate the resulting canonical object
with the frozen research validator and retain the raw wire response separately.
"""
from copy import deepcopy
import hashlib
from jsonschema import Draft202012Validator


def locate_quote(body: str, quote: str) -> dict:
    if not isinstance(body, str) or not isinstance(quote, str) or not quote:
        return {"status": "INVALID_QUOTE", "start": None, "end": None}
    first = body.find(quote)
    if first < 0:
        return {"status": "QUOTE_NOT_FOUND", "start": None, "end": None}
    # Include overlapping occurrences; never silently choose the first match.
    if body.find(quote, first + 1) >= 0:
        return {"status": "QUOTE_AMBIGUOUS", "start": None, "end": None}
    return {"status": "UNIQUE_EXACT", "start": first, "end": first + len(quote)}


def span_containers(output):
    yield "/article_evidence", output["article_evidence"]
    for i, record in enumerate(output["records"]):
        yield f"/records/{i}/evidence_spans", record["evidence_spans"]
        yield f"/records/{i}/relationship_evidence_spans", record["relationship_evidence_spans"]


def adapt_wire(wire: dict, body: str, expected_binding: dict,
               wire_schema: dict, canonical_schema: dict) -> dict:
    Draft202012Validator.check_schema(wire_schema)
    Draft202012Validator.check_schema(canonical_schema)
    errors = list(Draft202012Validator(wire_schema).iter_errors(wire))
    result = {"wire_schema_valid": not errors, "host_body_hash_valid": False,
              "grounding_status": "NOT_EVALUATED", "spans": [],
              "canonical_schema_valid": None, "canonical": None}
    if errors:
        # Do not echo raw values or error messages into public receipts.
        result["wire_error_keywords"] = sorted(set(str(e.validator) for e in errors))
        return result
    if not isinstance(body, str) or not isinstance(expected_binding, dict):
        result["grounding_status"] = "INVALID_HOST_INPUT"
        return result
    if expected_binding.get("body_sha256") != hashlib.sha256(body.encode("utf-8")).hexdigest():
        result["grounding_status"] = "HOST_BODY_HASH_MISMATCH"
        return result
    result["host_body_hash_valid"] = True
    canonical = deepcopy(wire)
    canonical["schema_version"] = "n1_company_event_v1"
    canonical["input_binding"] = deepcopy(expected_binding)
    failed = False
    for path, spans in span_containers(canonical):
        for index, span in enumerate(spans):
            located = locate_quote(body, span["text"])
            result["spans"].append({"path": f"{path}/{index}", **located})
            if located["status"] != "UNIQUE_EXACT":
                failed = True
            else:
                span["start"], span["end"] = located["start"], located["end"]
    if failed:
        result["grounding_status"] = "UNRESOLVED_QUOTES"
        return result
    result["grounding_status"] = "ALL_UNIQUE_EXACT" if result["spans"] else "NO_SPANS_REQUESTED"
    valid = Draft202012Validator(canonical_schema).is_valid(canonical)
    result["canonical_schema_valid"] = valid
    # Keep a complete constructed object even if the downstream validator fails;
    # callers must not confuse construction with semantic/mechanical success.
    result["canonical"] = canonical
    return result
