"""Synthetic contract checks for the fixed wire-v2 quote-grounding candidate."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
V3 = ROOT / "configs/news_n1_smoke_v3_prompt_complete.json"
V4 = ROOT / "configs/news_n1_smoke_v4_grounded.json"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


adapter = load("exact_quote_adapter_candidate_test", ROOT / "scripts/exact_quote_adapter.py")
fixture = load("news_contract_fixture_candidate_test", ROOT / "tests/test_news_company_event_contract.py")


def test_v4_contract_hashes_and_fixed_development_budget():
    old = json.loads(V3.read_text(encoding="utf-8"))
    new = json.loads(V4.read_text(encoding="utf-8"))
    assert new["schema"] == "news_n1_smoke_config_v4_grounded"
    assert new["source"]["sample_sha256"] == old["source"]["sample_sha256"]
    assert new["source"]["dataset_revision"] == old["source"]["dataset_revision"]
    assert new["source"]["first_usable_articles"] == old["source"]["first_usable_articles"] == 10
    assert new["model_preference"] == old["model_preference"]
    assert new["model_revision"] == old["model_revision"]
    assert new["dtype"] == old["dtype"] == "float16"
    assert new["context_cap_tokens"] == old["context_cap_tokens"]
    assert new["budget"] == old["budget"] == {
        "max_model_calls": 10,
        "max_new_tokens": 1024,
        "per_call_timeout_seconds": 180,
        "total_timeout_seconds": 1800,
        "retries": 0,
    }
    assert new["attention_implementation"] == "sdpa"
    assert new["generation_output_flags"] == {
        "output_attentions": False,
        "output_hidden_states": False,
        "output_scores": False,
    }
    assert new["generation"]["use_cache"] is True
    assert "input_binding" not in new["model_input_fields"]
    contract = new["contract"]
    for stem in ("schema", "wire_schema", "prompt", "adapter", "validator"):
        path = ROOT / contract[f"{stem}_path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == contract[f"{stem}_sha256"]
    assert contract["schema_sha256"] == old["contract"]["schema_sha256"]
    assert contract["validator_sha256"] == old["contract"]["validator_sha256"]
    assert new["baseline_config_sha256"] == hashlib.sha256(V3.read_bytes()).hexdigest()


def test_adapter_constructs_valid_canonical_without_editing_wire_semantics():
    body, binding, canonical = fixture.fixture_output()
    wire = copy.deepcopy(canonical)
    wire.pop("input_binding")
    wire["schema_version"] = "n1_company_event_wire_v2"
    for _, spans in adapter.span_containers(wire):
        for span in spans:
            span.pop("start")
            span.pop("end")
    prior_wire = copy.deepcopy(wire)
    wire_schema = json.loads(
        (ROOT / "docs/news_research/company_event_wire_v2.schema.json").read_text(encoding="utf-8"))
    canonical_schema = json.loads(
        (ROOT / "docs/news_research/company_event_output.schema.json").read_text(encoding="utf-8"))
    result = adapter.adapt_wire(wire, body, binding, wire_schema, canonical_schema)
    assert result["wire_schema_valid"] is True
    assert result["host_body_hash_valid"] is True
    assert result["grounding_status"] == "ALL_UNIQUE_EXACT"
    assert result["canonical"] == canonical
    assert wire == prior_wire
    checked = fixture.validator.validate_output(
        result["canonical"], body, binding,
        ROOT / "docs/news_research/company_event_output.schema.json")
    assert checked["mechanical_valid"] is True
    wire["records"][0]["evidence_spans"][0]["text"] = "quote absent from the body"
    failed = adapter.adapt_wire(wire, body, binding, wire_schema, canonical_schema)
    assert failed["grounding_status"] == "UNRESOLVED_QUOTES"
    assert failed["canonical"] is None
    assert any(span["status"] == "QUOTE_NOT_FOUND" for span in failed["spans"])
