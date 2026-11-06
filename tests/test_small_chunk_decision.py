"""Synthetic decision-contract checks; no model inference or human decision."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from finembed import small_chunk_decision as decision


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@pytest.fixture
def record_case(tmp_path):
    companies = list(decision.COMPANY_ORDER)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("artifacts/\n", encoding="utf-8")
    (tmp_path / "configs").mkdir()
    candidate = tmp_path / "configs" / "candidate.json"
    corpus_hash = _sha("synthetic corpus index")
    candidate.write_text(json.dumps({
        "schema_version": 1, "kind": "new_engineering_probe_candidate",
        "status": "PROPOSED", "researcher_accepted": False,
        "real_inference_status": "NOT_RUN",
        "input": {"corpus_index_sha256": corpus_hash, "company_order": companies,
                  "form": "10-K", "section": "ITEM_1_BUSINESS"},
        "model_config": {"path": "configs/smoke.yaml",
                         "sha256": _sha("synthetic smoke config")},
        "chunking": {"reference_body_tokens": 128,
                     "candidate_body_tokens": [118, 123, 128, 133, 138],
                     "overlap_tokens": 0},
        "pooling": ["equal_chunks", "body_token_weighted"],
        "query_rewriting": False, "generation": False, "training": False,
    }), encoding="utf-8")
    contract = tmp_path / "metrics.json"
    contract.write_text('{"synthetic_metric_contract":true}\n', encoding="utf-8")
    versions = [{"ticker": ticker, "cik": f"{index:010d}",
                 "accession": f"{index:010d}-25-{index:06d}",
                 "form": "10-K", "section": "ITEM_1_BUSINESS",
                 "item1_sha256": _sha(f"synthetic {ticker} Item 1")}
                for index, ticker in enumerate(companies, start=1)]
    model_identity = {
        "model_id": "sentence-transformers/all-MiniLM-L6-v2",
        "revision": "1" * 40, "tokenizer_revision": "1" * 40,
        "model_file_sha256": {name: _sha(f"synthetic {name}")
                              for name in decision.MODEL_FILE_NAMES},
        "provider": "CPUExecutionProvider",
        "reference_config_sha256": _sha("synthetic smoke config"),
    }
    manifest_hash = decision.derive_input_manifest_sha256(corpus_hash, versions)
    source_path = tmp_path / "artifacts" / "decision-source.txt"
    source_path.parent.mkdir()
    source_path.write_text("Synthetic researcher decision source fixture.\n", encoding="utf-8")
    record_data = {
        "schema_version": 1, "kind": "small_chunk_decision_record",
        "decision_scope": decision.EXPECTED_SCOPE,
        "decision": "ACCEPT", "source_reference": {
            "path": "artifacts/decision-source.txt", "sha256": decision.file_sha256(source_path)},
        "original_message_at_utc": None,
        "recorded_at_utc": "2026-10-31T12:34:56Z",
        "candidate_sha256": decision.file_sha256(candidate),
        "input_manifest_sha256": manifest_hash,
        "input_versions": versions,
        "model_identity": model_identity,
        "metric_contract_sha256": decision.file_sha256(contract),
        "company_failure_policy": decision.COMPANY_FAILURE_POLICY,
    }
    record_path = tmp_path / "artifacts" / "record.json"

    def check(record=record_data, **overrides):
        record_path.write_text(json.dumps(record), encoding="utf-8")
        inputs = {"candidate_path": candidate,
                  "input_manifest_sha256": manifest_hash,
                  "input_versions": versions,
                  "model_identity": model_identity,
                  "metric_contract_path": contract}
        inputs.update(overrides)
        return decision.validate_decision(record_path, **inputs)

    return {"candidate": candidate, "contract": contract, "source_path": source_path,
            "record_path": record_path, "record": record_data,
            "versions": versions, "model_identity": model_identity,
            "manifest_hash": manifest_hash, "check": check}


def test_canonical_manifest_hash_binds_order_and_corpus():
    assert decision.canonical_json_hash({"a": 1, "b": 2}) == \
        decision.canonical_json_hash({"b": 2, "a": 1})
    assert decision.derive_input_manifest_sha256(_sha("a"), [{"x": 1}, {"x": 2}]) != \
        decision.derive_input_manifest_sha256(_sha("a"), [{"x": 2}, {"x": 1}])
    assert decision.derive_input_manifest_sha256(_sha("a"), []) != \
        decision.derive_input_manifest_sha256(_sha("b"), [])
    with pytest.raises(ValueError):
        decision.canonical_json_hash({"invalid": float("nan")})


def test_missing_template_wrong_scope_and_rejection(record_case):
    case = record_case
    expected = {"candidate_path": case["candidate"],
                "input_manifest_sha256": case["manifest_hash"],
                "input_versions": case["versions"],
                "model_identity": case["model_identity"],
                "metric_contract_path": case["contract"]}
    assert decision.validate_decision(None, **expected)["status"] == "MISSING"
    template = Path(__file__).parents[1] / "configs" / "small_chunk_decision_template.json"
    assert decision.validate_decision(template, **expected)["status"] == "MISSING"
    wrong_scope = copy.deepcopy(case["record"])
    wrong_scope["decision_scope"] = "formal_finance"
    assert case["check"](wrong_scope)["status"] == "WRONG_SCOPE"
    rejection = copy.deepcopy(case["record"])
    rejection["decision"] = "REJECT"
    outcome = case["check"](rejection)
    assert outcome["status"] == "REJECTED"
    assert outcome["record_binding_match"] is False


def test_unknown_original_time_and_legacy_time_are_distinct_compatible_paths(record_case):
    case = record_case
    assert case["check"]()["status"] == "ACCEPTED"
    known_time = copy.deepcopy(case["record"])
    known_time["original_message_at_utc"] = "2026-10-31T12:00:00Z"
    assert case["check"](known_time)["status"] == "ACCEPTED"
    legacy = copy.deepcopy(case["record"])
    legacy.pop("original_message_at_utc")
    legacy.pop("recorded_at_utc")
    legacy["decided_at_utc"] = "2026-10-31T12:00:00Z"
    assert case["check"](legacy)["status"] == "ACCEPTED"
    legacy["decided_at_utc"] = "NOT_PROVIDED"
    assert case["check"](legacy)["status"] == "MISSING"


def test_new_time_fields_fail_closed_on_missing_invalid_or_conflicting_values(record_case):
    case = record_case
    for key, value in (
        ("recorded_at_utc", "NOT_PROVIDED"),
        ("recorded_at_utc", "2026-10-31T20:34:56+08:00"),
        ("original_message_at_utc", "NOT_PROVIDED"),
        ("original_message_at_utc", "2026-10-31T13:00:00Z"),
    ):
        changed = copy.deepcopy(case["record"])
        changed[key] = value
        assert case["check"](changed)["status"] == "MISSING"
    no_original = copy.deepcopy(case["record"])
    no_original.pop("original_message_at_utc")
    assert case["check"](no_original)["status"] == "MISSING"
    mixed = copy.deepcopy(case["record"])
    mixed["decided_at_utc"] = "2026-10-31T12:34:56Z"
    assert case["check"](mixed)["status"] == "MISSING"


def test_acceptance_requires_all_exact_bindings(record_case):
    case = record_case
    outcome = case["check"]()
    assert outcome["status"] == "ACCEPTED"
    assert outcome["record_binding_match"] is True
    assert outcome["source_authenticity"] == "NOT_VERIFIED_BY_SOFTWARE"
    assert "source authenticity requires independent review" in outcome["reason"]
    for field in ("candidate_sha256", "input_manifest_sha256", "metric_contract_sha256"):
        stale = copy.deepcopy(case["record"])
        stale[field] = _sha("changed " + field)
        assert case["check"](stale)["status"] == "VERSION_MISMATCH"
    stale = copy.deepcopy(case["record"])
    stale["input_versions"][0]["item1_sha256"] = _sha("different text")
    assert case["check"](stale)["status"] == "VERSION_MISMATCH"
    stale = copy.deepcopy(case["record"])
    stale["model_identity"]["model_file_sha256"]["tokenizer.json"] = _sha("new tokenizer")
    assert case["check"](stale)["status"] == "VERSION_MISMATCH"
    stale = copy.deepcopy(case["record"])
    stale["company_failure_policy"]["missing_or_failed_company"] = "DROP_COMPANY"
    assert case["check"](stale)["status"] == "VERSION_MISMATCH"


def test_real_file_change_and_invalid_expected_context_fail_closed(record_case):
    case = record_case
    assert case["check"]()["status"] == "ACCEPTED"
    case["contract"].write_text('{"synthetic_metric_contract":false}\n', encoding="utf-8")
    assert case["check"]()["status"] == "VERSION_MISMATCH"
    case["contract"].write_text('{"synthetic_metric_contract":true}\n', encoding="utf-8")
    case["candidate"].write_text(case["candidate"].read_text(encoding="utf-8") + "\n",
                                 encoding="utf-8")
    assert case["check"]()["status"] == "VERSION_MISMATCH"
    case["candidate"].write_text(case["candidate"].read_text(encoding="utf-8").rstrip("\n"),
                                 encoding="utf-8")
    altered_versions = copy.deepcopy(case["versions"])
    altered_versions[1]["item1_sha256"] = _sha("different source")
    assert case["check"](input_versions=altered_versions)["status"] == "VERSION_MISMATCH"
    altered_model = copy.deepcopy(case["model_identity"])
    altered_model["revision"] = "2" * 40
    assert case["check"](model_identity=altered_model)["status"] == "VERSION_MISMATCH"


def test_decision_source_must_be_ignored_local_nonempty_and_hash_matched(record_case, tmp_path):
    case = record_case
    source = case["source_path"]
    record = copy.deepcopy(case["record"])
    source.write_text("Changed private source.\n", encoding="utf-8")
    assert case["check"](record)["status"] == "VERSION_MISMATCH"
    source.write_text("  \n", encoding="utf-8")
    assert case["check"](record)["status"] == "MISSING"
    source.write_text("Synthetic researcher decision source fixture.\n", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("Outside artifacts.\n", encoding="utf-8")
    record["source_reference"] = {"path": "outside.txt", "sha256": decision.file_sha256(outside)}
    assert case["check"](record)["status"] == "MISSING"
    record["source_reference"] = case["record"]["source_reference"]
    subprocess.run(["git", "-C", str(tmp_path), "add", "-f", "artifacts/decision-source.txt"], check=True)
    assert case["check"](record)["status"] == "MISSING"


def test_tracked_decision_record_cannot_authorize(record_case, tmp_path):
    case = record_case
    assert case["check"]()["status"] == "ACCEPTED"
    subprocess.run(["git", "-C", str(tmp_path), "add", "-f", "artifacts/record.json"], check=True)
    assert case["check"]()["status"] == "MISSING"


@pytest.mark.parametrize("field,value", [
    ("source_reference", "NOT_PROVIDED"),
    ("source_reference", {"path": "NOT_PROVIDED", "sha256": "NOT_PROVIDED"}),
    ("recorded_at_utc", "NOT_PROVIDED"),
    ("recorded_at_utc", "2026-10-31"),
    ("recorded_at_utc", "2026-10-31T12:34:56+08:00"),
    ("decision", "NOT_PROVIDED"),
])
def test_missing_human_decision_fields_never_authorize(record_case, field, value):
    record = copy.deepcopy(record_case["record"])
    record[field] = value
    outcome = record_case["check"](record)
    assert outcome["status"] == "MISSING"
    assert outcome["record_binding_match"] is False


def test_decision_validator_does_not_construct_encoder(record_case, monkeypatch):
    import finembed.model

    monkeypatch.setattr(finembed.model.Encoder, "__init__",
                        lambda *_args, **_kwargs: pytest.fail("Encoder constructed"))
    assert record_case["check"]()["status"] == "ACCEPTED"
