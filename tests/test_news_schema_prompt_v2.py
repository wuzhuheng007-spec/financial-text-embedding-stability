"""Offline regression checks for the complete-contract N1 prompt candidate."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
PROMPT = REPO / "docs/news_research/company_event_prompt_v2.md"
SCHEMA = REPO / "docs/news_research/company_event_output.schema.json"
V2_CONFIG = REPO / "configs/news_n1_smoke_v2.json"
V3_CONFIG = REPO / "configs/news_n1_smoke_v3_prompt_complete.json"


def load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load_script("news_runner_for_prompt_test", REPO / "scripts/run_news_n1_smoke.py")
validator = load_script("news_validator_for_prompt_test", REPO / "scripts/validate_news_company_event.py")


def block(text: str, name: str) -> dict:
    value = text.split(f"BEGIN_{name}\n", 1)[1].split(f"\nEND_{name}", 1)[0].strip()
    return json.loads(value)


def test_candidate_preserves_model_input_and_budget_and_sends_full_schema():
    original = json.loads(V2_CONFIG.read_text(encoding="utf-8"))
    candidate = runner.load_config(V3_CONFIG)
    for key in (
        "schema", "source", "model_preference", "model_path_policy", "device", "dtype",
        "context_cap_tokens", "generation", "budget", "model_input_fields",
        "forbidden_model_inputs", "model_revision", "attention_implementation", "runtime_policy",
    ):
        assert candidate[key] == original[key]
    for key in ("schema_path", "schema_sha256", "validator_path", "validator_sha256"):
        assert candidate["contract"][key] == original["contract"][key]
    assert candidate["contract"]["prompt_path"] == "docs/news_research/company_event_prompt_v2.md"
    assert candidate["contract"]["prompt_sha256"] == hashlib.sha256(PROMPT.read_bytes()).hexdigest()
    runner.validate_hash_binding(candidate, SCHEMA, PROMPT)

    text = PROMPT.read_text(encoding="utf-8")
    embedded = block(text, "OUTPUT_JSON_SCHEMA")
    assert embedded == json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert "COMPANY_EVENTS_FOUND" in embedded["properties"]["article_disposition"]["enum"]
    assert embedded["properties"]["input_binding"]["type"] == "object"
    messages = runner.render_messages(text, {
        "input_binding": {"dataset": "unit/test"}, "source_date": "2020-01-02",
        "source_ticker_candidate": "XYZ", "title": "Unit title", "body": "Meridian launched a device.",
    })
    assert [m["role"] for m in messages] == ["system", "user"]
    assert "BEGIN_OUTPUT_JSON_SCHEMA" in messages[0]["content"]
    assert "COMPANY_EVENTS_FOUND" in messages[0]["content"]
    assert "BEGIN_SYNTHETIC_OUTPUT" in messages[0]["content"]
    assert "BEGIN_OUTPUT_JSON_SCHEMA" not in messages[1]["content"]
    assert "synthetic/FNSPID" not in messages[1]["content"]
    assert "Meridian launched a device." in messages[1]["content"]
    assert "Runner notes" not in messages[0]["content"] + messages[1]["content"]


def test_embedded_synthetic_example_is_mechanically_valid():
    text = PROMPT.read_text(encoding="utf-8")
    example_input = block(text, "SYNTHETIC_INPUT")
    example_output = block(text, "SYNTHETIC_OUTPUT")
    assert example_input["input_binding"]["dataset"].startswith("synthetic/")
    assert example_input["input_binding"]["body_sha256"] == hashlib.sha256(
        example_input["body"].encode("utf-8")
    ).hexdigest()
    check = validator.validate_output(
        example_output, example_input["body"], example_input["input_binding"], SCHEMA,
    )
    assert check["schema_valid"] is True
    assert check["binding_valid"] is True
    assert check["mechanical_valid"] is True
    assert check["evidence_span_count"] == check["evidence_span_exact_match_count"]
