"""Synthetic contract checks only; no saved article or model output is loaded."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "docs/news_research/company_event_output.schema.json"
spec = importlib.util.spec_from_file_location(
    "validate_news_company_event", ROOT / "scripts/validate_news_company_event.py"
)
validator = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(validator)


def fixture_output():
    body = "Acme acquired 蓝星. Shares rose."
    binding = {
        "dataset": "synthetic/FNSPID",
        "dataset_revision": "fixed",
        "source_row_ordinal": 1,
        "source_article_version_id": None,
        "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "normalization_applied": False,
        "normalization_mapping_ref": None,
    }

    def span(role, text):
        start = body.index(text)
        return {"role": role, "start": start, "end": start + len(text), "text": text}

    output = {
        "schema_version": "n1_company_event_v1",
        "input_binding": binding,
        "article_disposition": "COMPANY_EVENTS_FOUND",
        "article_unknown_reason": "NONE",
        "market_outcome_reported": "YES",
        "market_outcome_direction": "UP",
        "date_conflict": "NO",
        "identity_conflict": "NO",
        "future_return_status": "NOT_OBSERVED",
        "article_evidence": [span("MARKET_OUTCOME", "Shares rose")],
        "records": [
            {
                "event_description": "Acme acquired another company.",
                "event_type": "ACQUISITION",
                "target_company_text": "Acme",
                "ticker_candidate": None,
                "company_role": "SUBJECT",
                "identity_status": "TEXT_ONLY",
                "business_impact_direction": "NO_CLEAR_IMPACT",
                "impact_path": "DIRECT",
                "statement_status": "EXPLICIT_IN_ARTICLE",
                "inference_premise": None,
                "unknown_reason": "NONE",
                "evidence_spans": [span("EVENT", "acquired 蓝星"), span("TARGET_COMPANY", "Acme")],
                "relationship_evidence_spans": [],
            }
        ],
    }
    return body, binding, output


def test_unicode_code_point_spans_and_binding():
    body, binding, output = fixture_output()
    check = validator.validate_output(output, body, binding, SCHEMA)
    assert check["schema_valid"] is True
    assert check["binding_valid"] is True
    assert check["evidence_span_count"] == 3
    assert check["evidence_span_exact_match_count"] == 3
    assert check["mechanical_valid"] is True
    assert body.index("Shares") != len(body[:body.index("Shares")].encode("utf-8"))


def test_span_slice_mismatch_is_mechanical_failure():
    body, binding, output = fixture_output()
    output["records"][0]["evidence_spans"][0]["start"] += 1
    check = validator.validate_output(output, body, binding, SCHEMA)
    assert check["schema_valid"] is True
    assert check["mechanical_valid"] is False
    assert "SPAN_SLICE_MISMATCH" in check["mechanical_issue_codes"]


def test_indirect_requires_relationship_evidence():
    body, binding, output = fixture_output()
    output["records"][0]["impact_path"] = "INDIRECT"
    check = validator.validate_output(output, body, binding, SCHEMA)
    assert check["schema_valid"] is False
    assert any(x["keyword"] == "minItems" for x in check["schema_error_codes"])


def test_unknown_is_valid_but_extraction_failure_is_not_article_disposition():
    body, binding, output = fixture_output()
    output.update(
        article_disposition="UNDETERMINED",
        article_unknown_reason="AMBIGUOUS_TARGET",
        market_outcome_reported="UNKNOWN",
        market_outcome_direction="UNKNOWN",
        article_evidence=[],
        records=[],
    )
    check = validator.validate_output(output, body, binding, SCHEMA)
    assert check["schema_valid"] is True and check["mechanical_valid"] is True
    output["article_disposition"] = "EXTRACTION_FAILED"
    assert validator.validate_output(output, body, binding, SCHEMA)["schema_valid"] is False


def test_wrong_body_hash_rejected_without_echoing_text():
    body, binding, output = fixture_output()
    changed = dict(binding, body_sha256="0" * 64)
    check = validator.validate_output(output, body, changed, SCHEMA)
    assert check["schema_valid"] is True
    assert check["binding_valid"] is False
    assert check["mechanical_valid"] is False
    assert "EXPECTED_BINDING_BODY_HASH_MISMATCH" in check["mechanical_issue_codes"]
    assert "Acme" not in json.dumps(check)


def test_prompt_has_single_input_placeholder():
    prompt = (ROOT / "docs/news_research/company_event_prompt.md").read_text(encoding="utf-8")
    assert prompt.count("{{ARTICLE_INPUT_JSON}}") == 1
    assert "## Runner notes (not sent to model)" in prompt
    assert "future_return_status" in prompt
