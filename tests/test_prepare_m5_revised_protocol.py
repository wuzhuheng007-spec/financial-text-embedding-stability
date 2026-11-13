"""Static and synthetic checks for an unaccepted M5 protocol preparation."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
ROUND8 = ROOT / "configs/m5_financial_protocol_round8.json"
H = ROOT / "configs/m5_protocol_design_h_20261113.json"
R = ROOT / "configs/m5_protocol_design_r_20261113.json"
PATCH = ROOT / "configs/m5_protocol_h_to_company_proxy_patch_20261113.json"
NEW = ROOT / "configs/m5_protocol_company_proxy_conditional_20261113.json"
DECISION = ROOT / "configs/m5_protocol_design_choices_accepted_20261113.json"
EVENT_SCHEMA = ROOT / "configs/m5_company_proxy_event_attrition_schema_20261113.json"
EVENT_TEMPLATE = ROOT / "configs/m5_company_proxy_event_attrition_template_20261113.json"
DIFF = ROOT / "docs/M5_COMPANY_PROXY_H_TO_NEW_DIFF_20261113.md"
TOOL = ROOT / "scripts/apply_checked_protocol_patch.py"
ROUND8_HASH = "f1fa28949353adea6aa179854441b48658415ef577a006fe76bd01c779ddaa38"
H_HASH = "ce1526228ece9d5c1442273a5ef2116a8df0fda75077346ea31c3c21a99c3f00"
R_HASH = "8c54dd49a8336315632dffabadbcfd803debbe466864826febe76a5d108b2016"
NEW_HASH = "bb5649176ea913990b11082e9c5214bc5e3ffe96a98a3acd53f0e8d8f906ee51"


spec = importlib.util.spec_from_file_location("m5_checked_patch", TOOL)
assert spec is not None and spec.loader is not None
checked = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = checked
spec.loader.exec_module(checked)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path: Path) -> dict:
    return checked.decode(path.read_bytes())


def test_source_bytes_and_checked_replay() -> None:
    assert digest(ROUND8) == ROUND8_HASH
    assert digest(H) == H_HASH
    assert digest(R) == R_HASH
    assert digest(NEW) == NEW_HASH
    original = H.read_bytes()
    replay = checked.apply_checked(original, PATCH.read_bytes(), "repository")
    assert replay == read(NEW)
    assert H.read_bytes() == original


def test_preparation_scope_and_inherited_contracts() -> None:
    h, new, decision = read(H), read(NEW), read(DECISION)
    checked.validate(new)
    assert new["status"] == "PROPOSED"
    assert new["researcher_accepted"] is False
    assert new["formal_financial_experiment_status"] == "NOT_RUN"
    assert new["selected_design_scope"] == "DESIGN_DIRECTION_FOR_PREPARATION_ONLY"
    assert decision["observed_directly_in_local_codex_conversation"] is False
    assert decision["full_protocol_accepted"] is False
    assert decision["financial_execution_authorized"] is False
    assert decision["original_message_exact_utc"] is None
    for key in ("features", "metrics", "development_evidence_only"):
        assert new[key] == h[key]
    for key in ("training_decision_years", "validation_decision_years",
                "test_decision_years", "split_unit", "random_shuffle"):
        assert new["chronology"][key] == h["chronology"][key]
    assert new["sample"]["development_cik_exclusions"] == h["sample"]["development_cik_exclusions"]
    assert new["sample"]["planning_cohort_hash_rule"] == h["sample"]["planning_cohort_hash_rule"]
    assert new["decision_and_label"]["security_join"]["unit"] == h["decision_and_label"]["security_join"]["unit"]
    assert new["decision_and_label"]["primary_horizon_subsequent_sessions"] == 5
    assert new["decision_and_label"]["alternative_horizon_subsequent_sessions"] == 21
    assert "18:00" not in new["decision_and_label"]["recommended_decision"]
    assert new["design_review"]["clock"]["intraday_anchor_time"] is None
    assert new["design_review"]["horizon_mask_choice"]["selected"] == "SEPARATE_HORIZON_SPECIFIC_MASKS_COMMON_TO_F0_F1_F2"
    assert new["prediction_arms"]["common_sample_scope"] == "WITHIN_EACH_HORIZON_ACROSS_F0_F1_F2_NOT_ACROSS_HORIZONS"
    assert "d-20 through d+h inclusive" in new["design_review"]["required_price_window"]
    assert new["sample"]["starting_cohort_price_coverage_reselection"] is False
    assert new["design_review"]["split_adjustment_formula"] is None
    assert new["design_review"]["nonordinary_distribution_rule"] is None
    assert new["design_review"]["exit_terminal_value_rule"] is None
    assert new["design_review"]["admitted_observation_count"] is None


def test_reject_wrong_source_and_old_value() -> None:
    source = H.read_bytes()
    patch = PATCH.read_bytes()
    with pytest.raises(checked.ContractError, match="SOURCE_HASH_MISMATCH"):
        checked.apply_checked(source + b" ", patch, "repository")
    altered = checked.decode(patch)
    operation = next(item for item in altered["operations"] if item["op"] == "replace")
    operation["expected"] = "SYNTHETIC_WRONG_OLD_VALUE"
    with pytest.raises(checked.ContractError, match="PATCH_OLD_VALUE_MISMATCH"):
        checked.apply_checked(source, json.dumps(altered).encode("utf-8"), "repository")


def test_reject_private_exclusion_patch_and_duplicate_json_key() -> None:
    altered = read(PATCH)
    altered["operations"].insert(0, {
        "op": "replace", "path": "/sample/development_cik_exclusions",
        "expected": [], "value": [],
    })
    with pytest.raises(checked.ContractError, match="FORBIDDEN_PATCH_PATH"):
        checked.apply_checked(H.read_bytes(), json.dumps(altered).encode("utf-8"), "repository")
    with pytest.raises(checked.ContractError, match="DUPLICATE_JSON_KEY"):
        checked.decode(b'{"synthetic": 1, "synthetic": 2}')


def test_static_gate_rejects_premature_scientific_claims() -> None:
    new = read(NEW)
    for field, value in (
        ("researcher_accepted", True),
        ("formal_financial_experiment_status", "COMPLETE"),
    ):
        mutated = copy.deepcopy(new)
        mutated[field] = value
        with pytest.raises(checked.ContractError):
            checked.validate(mutated)
    for path, value in (
        (("design_review", "split_adjustment_formula"), "SYNTHETIC_UNSUPPORTED"),
        (("design_review", "identity_gate", "event_time_cik_security_evidence"), "SYNTHETIC_UNSUPPORTED"),
        (("design_review", "clock", "first_public_time_proven"), True),
        (("sample", "starting_cohort_price_coverage_reselection"), True),
    ):
        mutated = copy.deepcopy(new)
        target = mutated
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        with pytest.raises(checked.ContractError):
            checked.validate(mutated)


def test_only_declared_redaction_may_differ() -> None:
    new = read(NEW)
    redacted = copy.deepcopy(new)
    redacted["sample"]["development_cik_exclusions"] = [
        f"REDACTED_DEVELOPMENT_CIK_{i}" for i in range(1, 6)
    ]
    checked.compare_review(new, redacted)
    redacted["sample"]["planning_cohort_master_as_of"] = "SYNTHETIC_WRONG_DATE"
    with pytest.raises(checked.ContractError, match="REVIEW_SEMANTIC_MISMATCH"):
        checked.compare_review(new, redacted)


def test_existing_output_is_not_overwritten(tmp_path: Path) -> None:
    output = tmp_path / "SYNTHETIC_TEST_ONLY.json"
    marker = b"SYNTHETIC_ORIGINAL_CONTENT"
    output.write_bytes(marker)
    completed = subprocess.run([
        sys.executable, str(TOOL), "apply", "--source-kind", "repository",
        "--base", str(H), "--patch", str(PATCH), "--output", str(output),
    ], capture_output=True, text=True, check=False)
    assert completed.returncode == 2
    assert json.loads(completed.stdout)["reason"] == "OUTPUT_ALREADY_EXISTS"
    assert output.read_bytes() == marker


def test_empty_event_template_and_year_boundary_capacity() -> None:
    schema, template = read(EVENT_SCHEMA), read(EVENT_TEMPLATE)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    validator.validate(template)
    assert template["events"] == template["annual_attrition"] == []
    assert template["starting_company_denominator"] is None
    assert template["potential_original_filing_event_denominator"] is None
    assert template["formal_admitted_count"] is None
    assert [row["h"] for row in template["horizon_attrition"]] == [5, 21]
    row = {
        "h": 5, "year_axis": "PROXY_DECISION_SESSION_YEAR",
        "calendar_year": 2026, "potential_original_filing_event_count": None,
        "scored_event_count": None, "unscored_event_count": None,
        "exclusive_first_failure_counts": {}, "overlapping_reason_counts": {},
        "reconciliation_status": "NOT_EVALUATED",
    }
    synthetic = copy.deepcopy(template)
    synthetic["annual_attrition"] = [row]
    validator.validate(synthetic)
    synthetic["annual_attrition"][0]["calendar_year"] = 2019
    validator.validate(synthetic)
    for bad_year in (2018, 2027):
        synthetic["annual_attrition"][0]["calendar_year"] = bad_year
        assert not validator.is_valid(synthetic)
    synthetic = copy.deepcopy(template)
    synthetic["formal_admitted_count"] = 0
    assert not validator.is_valid(synthetic)


def test_h_to_new_path_table_matches_checked_operations() -> None:
    operations = read(PATCH)["operations"]
    documented = re.findall(r"^\| `([^`]+)` \| (add|replace|remove) \|$",
                            DIFF.read_text(encoding="utf-8"), flags=re.M)
    assert len(operations) == len(documented) == 62
    assert {(item["path"], item["op"]) for item in operations} == set(documented)
