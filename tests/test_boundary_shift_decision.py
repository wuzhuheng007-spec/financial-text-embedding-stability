"""Synthetic S3 decision checks; fixtures are not researcher acceptance."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess

import pytest

from finembed import boundary_shift_decision as decision
from finembed.small_chunk_decision import derive_input_manifest_sha256, file_sha256


@pytest.fixture
def decision_case(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("artifacts/\n", encoding="utf-8")
    (tmp_path / "configs").mkdir()
    (tmp_path / "docs").mkdir()
    source_root = Path(__file__).parents[1]
    candidate = tmp_path / "configs" / "boundary_shift_probe_round5.json"
    candidate.write_bytes((source_root / "configs" / candidate.name).read_bytes())
    proposal = json.loads(candidate.read_text(encoding="utf-8"))
    metric = tmp_path / decision.METRIC_CONTRACT
    metric.write_text("# Synthetic S3 metric contract fixture\n", encoding="utf-8")
    legacy = tmp_path / decision.LEGACY_METRIC_CONTRACT
    legacy.write_bytes((source_root / decision.LEGACY_METRIC_CONTRACT).read_bytes())
    versions = copy.deepcopy(proposal["input"]["versions"])
    manifest_hash = derive_input_manifest_sha256(proposal["input"]["corpus_index_sha256"], versions)
    model_identity = {
        "model_id": "sentence-transformers/all-MiniLM-L6-v2",
        "revision": decision.EXPECTED_MODEL_REVISION,
        "tokenizer_revision": decision.EXPECTED_MODEL_REVISION,
        "model_file_sha256": copy.deepcopy(decision.EXPECTED_MODEL_FILE_SHA256),
        "provider": "CPUExecutionProvider",
        "reference_config_sha256": proposal["model_config"]["sha256"],
    }
    private = tmp_path / "artifacts"
    private.mkdir()
    source = private / "source.json"
    source.write_text('{"kind":"SYNTHETIC_TEST_ONLY"}\n', encoding="utf-8")
    record_file = private / "decision.json"
    record = {
        "schema_version": 1,
        "kind": "boundary_shift_decision_record",
        "decision_scope": decision.EXPECTED_SCOPE,
        "decision": "ACCEPT",
        "researcher_name": decision.EXPECTED_RESEARCHER_NAME,
        "source_reference": {"path": "artifacts/source.json", "sha256": file_sha256(source)},
        "original_message_at_utc": None,
        "recorded_at_utc": "2026-10-31T12:34:56Z",
        "candidate_sha256": file_sha256(candidate),
        "input_manifest_sha256": manifest_hash,
        "input_versions": versions,
        "model_identity": model_identity,
        "metric_contract_sha256": file_sha256(metric),
        "company_failure_policy": copy.deepcopy(decision.COMPANY_FAILURE_POLICY),
        "axis": "boundary_shift",
        "reference_body_tokens": 128,
        "reference_shift": 0,
        "boundary_shifts": copy.deepcopy(decision.BOUNDARY_SHIFTS),
        "pooling": copy.deepcopy(decision.POOLING),
        "interpretation_scope": decision.INTERPRETATION_SCOPE,
    }

    def check(record_override=None, **expected_override):
        record_file.write_text(json.dumps(record if record_override is None else record_override),
                               encoding="utf-8")
        expected = {
            "candidate_path": candidate,
            "input_manifest_sha256": manifest_hash,
            "input_versions": versions,
            "model_identity": model_identity,
            "metric_contract_path": metric,
        }
        expected.update(expected_override)
        return decision.validate_boundary_decision(record_file, **expected)

    return {"candidate": candidate, "metric": metric, "legacy": legacy,
            "source": source, "record_file": record_file, "record": record,
            "versions": versions, "manifest_hash": manifest_hash,
            "model_identity": model_identity, "check": check}


def test_exact_s3_synthetic_record_matches_without_authenticating_person(decision_case):
    result = decision_case["check"]()
    assert result["status"] == "ACCEPTED"
    assert result["record_binding_match"] is True
    assert result["source_authenticity"] == "NOT_VERIFIED_BY_SOFTWARE"


def test_missing_rejected_and_wrong_scope_never_authorize(decision_case):
    case = decision_case
    context = {"candidate_path": case["candidate"],
               "input_manifest_sha256": case["manifest_hash"],
               "input_versions": case["versions"],
               "model_identity": case["model_identity"],
               "metric_contract_path": case["metric"]}
    assert decision.validate_boundary_decision(None, **context)["status"] == "MISSING"
    no_decision = copy.deepcopy(case["record"])
    no_decision["decision"] = "NOT_PROVIDED"
    assert case["check"](no_decision)["status"] == "MISSING"
    rejected = copy.deepcopy(case["record"])
    rejected["decision"] = "REJECT"
    assert case["check"](rejected)["status"] == "REJECTED"
    wrong = copy.deepcopy(case["record"])
    wrong["decision_scope"] = "formal_financial_research"
    assert case["check"](wrong)["status"] == "WRONG_SCOPE"
    assert case["check"](expected_scope="small_chunk_engineering_probe_real_inference_five_company_full_grid")["status"] == "WRONG_SCOPE"


def test_round_four_decision_cannot_authorize_s3(decision_case):
    case = decision_case
    old = copy.deepcopy(case["record"])
    old["kind"] = "small_chunk_decision_record"
    old["decision_scope"] = "small_chunk_engineering_probe_real_inference_five_company_full_grid"
    assert case["check"](old)["status"] != "ACCEPTED"


@pytest.mark.parametrize("field,value", [
    ("candidate_sha256", "0" * 64),
    ("input_manifest_sha256", "0" * 64),
    ("metric_contract_sha256", "0" * 64),
    ("axis", "body_length"),
    ("reference_body_tokens", 118),
    ("reference_shift", 5),
    ("boundary_shifts", [-5, 0, 5]),
    ("pooling", ["equal_chunks"]),
    ("interpretation_scope", "financial_experiment"),
])
def test_changed_decision_bindings_fail(decision_case, field, value):
    changed = copy.deepcopy(decision_case["record"])
    changed[field] = value
    assert decision_case["check"](changed)["status"] == "VERSION_MISMATCH"


def test_input_model_and_failure_policy_changes_fail(decision_case):
    case = decision_case
    for field, updater in (
        ("input_versions", lambda x: x[0].update(item1_sha256="0" * 64)),
        ("model_identity", lambda x: x["model_file_sha256"].update({"tokenizer.json": "0" * 64})),
        ("company_failure_policy", lambda x: x.update(missing_or_failed_company="DROP_COMPANY")),
    ):
        changed = copy.deepcopy(case["record"])
        updater(changed[field])
        assert case["check"](changed)["status"] == "VERSION_MISMATCH"
    expected_versions = copy.deepcopy(case["versions"])
    expected_versions[0]["item1_sha256"] = "0" * 64
    assert case["check"](input_versions=expected_versions)["status"] == "VERSION_MISMATCH"
    expected_model = copy.deepcopy(case["model_identity"])
    expected_model["revision"] = "0" * 40
    assert case["check"](model_identity=expected_model)["status"] == "VERSION_MISMATCH"


def test_candidate_and_metric_file_changes_fail(decision_case):
    case = decision_case
    assert case["check"]()["status"] == "ACCEPTED"
    case["metric"].write_text("# Changed synthetic S3 contract\n", encoding="utf-8")
    assert case["check"]()["status"] == "VERSION_MISMATCH"
    case["metric"].write_text("# Synthetic S3 metric contract fixture\n", encoding="utf-8")
    case["legacy"].write_text("# Changed legacy metric definitions\n", encoding="utf-8")
    assert case["check"]()["status"] == "VERSION_MISMATCH"
    case["legacy"].write_bytes((Path(__file__).parents[1] / decision.LEGACY_METRIC_CONTRACT).read_bytes())
    case["candidate"].write_bytes(case["candidate"].read_bytes() + b"\n")
    assert case["check"]()["status"] == "VERSION_MISMATCH"


def test_private_source_record_and_time_precision_fail_closed(decision_case, tmp_path):
    case = decision_case
    changed = copy.deepcopy(case["record"])
    changed["original_message_at_utc"] = "UNKNOWN"
    assert case["check"](changed)["status"] == "MISSING"
    changed = copy.deepcopy(case["record"])
    changed["original_message_at_utc"] = "2026-10-31T13:00:00Z"
    assert case["check"](changed)["status"] == "MISSING"
    changed = copy.deepcopy(case["record"])
    changed["researcher_name"] = "Synthetic Test Researcher"
    assert case["check"](changed)["status"] == "VERSION_MISMATCH"
    case["source"].write_text("Changed synthetic source.\n", encoding="utf-8")
    assert case["check"]()["status"] == "VERSION_MISMATCH"
    outside = tmp_path / "outside-source.json"
    outside.write_text("Synthetic.\n", encoding="utf-8")
    changed = copy.deepcopy(case["record"])
    changed["source_reference"] = {"path": str(outside), "sha256": file_sha256(outside)}
    assert case["check"](changed)["status"] == "MISSING"


def test_tracked_record_and_encoder_construction_are_rejected(decision_case, tmp_path, monkeypatch):
    import finembed.model

    monkeypatch.setattr(finembed.model.Encoder, "__init__",
                        lambda *_args, **_kwargs: pytest.fail("Encoder constructed"))
    case = decision_case
    assert case["check"]()["status"] == "ACCEPTED"
    subprocess.run(["git", "-C", str(tmp_path), "add", "-f", "artifacts/decision.json"], check=True)
    assert case["check"]()["status"] == "MISSING"
