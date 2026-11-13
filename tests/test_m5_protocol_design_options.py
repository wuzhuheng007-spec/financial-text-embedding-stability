"""Static checks for unaccepted H/R M5 design candidates; no private data."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "configs/m5_financial_protocol_round8.json"
H = ROOT / "configs/m5_protocol_design_h_20261113.json"
R = ROOT / "configs/m5_protocol_design_r_20261113.json"
DIFF = ROOT / "docs/M5_PROTOCOL_H_R_DIFF_20261113.md"
BASE_SHA256 = "f1fa28949353adea6aa179854441b48658415ef577a006fe76bd01c779ddaa38"
MISSING = object()


def no_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=no_duplicate_pairs)


def differing_leaves(left: object, right: object, prefix: str = ""):
    """Preserve missing-key versus explicit-null differences."""
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            yield from differing_leaves(left.get(key, MISSING), right.get(key, MISSING),
                                        f"{prefix}.{key}" if prefix else key)
    elif left is MISSING or right is MISSING or left != right:
        yield prefix


def test_original_bytes_and_duplicate_key_rejection() -> None:
    assert hashlib.sha256(BASE.read_bytes()).hexdigest() == BASE_SHA256
    base, h, r = (read_json(path) for path in (BASE, H, R))
    assert base["schema_version"] == 2
    assert {h["design_id"], r["design_id"]} == {
        "H_DATED_COMPANY_PRIMARY_SECURITY",
        "R_FIXED_SNAPSHOT_UNIQUE_ISSUE_OBSERVATION_ASSOCIATION",
    }
    with pytest.raises(ValueError, match="DUPLICATE_JSON_KEY"):
        json.loads('{"x":1,"x":2}', object_pairs_hook=no_duplicate_pairs)


def test_unchanged_numeric_and_development_contracts() -> None:
    base = read_json(BASE)
    for candidate in (read_json(H), read_json(R)):
        assert candidate["status"] == "PROPOSED"
        assert candidate["researcher_accepted"] is False
        assert candidate["selected_design"] is None
        assert candidate["formal_financial_experiment_status"] == "NOT_RUN"
        assert candidate["source_round8_candidate_sha256"] == BASE_SHA256
        assert candidate["design_review"]["admitted_observation_count"] is None
        assert candidate["design_review"]["financial_labels_computed"] is False
        assert candidate["design_review"]["financial_training_or_scoring_run"] is False
        for key in ("development_evidence_only", "features", "prediction_arms",
                    "chronology", "metrics"):
            assert candidate[key] == base[key], key
        for key in ("planning_cohort_hash_rule", "development_cik_exclusions",
                    "filing_decision_years"):
            assert candidate["sample"][key] == base["sample"][key]
        for key in ("primary_horizon_subsequent_sessions",
                    "alternative_horizon_subsequent_sessions", "primary_label",
                    "label_start", "label_end"):
            assert candidate["decision_and_label"][key] == base["decision_and_label"][key]
        assert candidate["decision_and_label"]["primary_horizon_subsequent_sessions"] == 5
        assert candidate["decision_and_label"]["alternative_horizon_subsequent_sessions"] == 21


def test_evidence_reasons_are_current_and_are_not_extra_keys() -> None:
    base = read_json(BASE)
    old_keys = set(base["current_missing_evidence"])
    for candidate in (read_json(H), read_json(R)):
        assert set(candidate["current_missing_evidence"]) == old_keys
        text = json.dumps(candidate, ensure_ascii=False)
        for stale in ("Only a 2025 five-firm price year",
                      "No historical exit and identifier-chain outcome source",
                      "No local dividend cashflow series",
                      "No version-bound point-in-time exchange session file"):
            assert stale not in text
        evidence = candidate["current_engineering_evidence"]
        assert evidence["formal_cohort_count"] is None
        assert evidence["admitted_financial_observations"] is None
        assert candidate["current_missing_evidence"]["complete_feature_and_label_price_manifest_sha256"] is None


def test_h_strict_clock_and_r_explicit_proxy_are_distinct() -> None:
    base, h, r = (read_json(path) for path in (BASE, H, R))
    assert h["decision_and_label"]["recommended_decision"] == base["decision_and_label"]["recommended_decision"]
    assert h["decision_and_label"]["close_availability_rule"] == base["decision_and_label"]["close_availability_rule"]
    assert h["design_review"]["clock"]["exact_item1_public_or_no_later_than_by_18_et"] is None
    assert h["design_review"]["identity_gate"]["event_time_cik_security_evidence"] is None
    assert h["design_review"]["identity_gate"]["dated_company_primary_issue_evidence"] is None
    assert r["decision_and_label"]["recommended_decision"] != base["decision_and_label"]["recommended_decision"]
    assert r["design_review"]["clock"]["proxy_researcher_accepted"] is False
    assert r["design_review"]["identity_gate"]["historical_validity_claim"] is False
    assert r["design_review"]["identity_gate"]["company_primary_issue_claim"] is False
    assert r["decision_and_label"]["security_join"]["alternative_rule"] is None
    assert h["design_review"]["horizon_mask_choice"]["selected"] is None
    assert r["design_review"]["horizon_mask_choice"]["selected"] is None


def test_diff_table_is_exact_including_missing_vs_null() -> None:
    h, r = read_json(H), read_json(R)
    actual = set(differing_leaves(h, r))
    section = DIFF.read_text(encoding="utf-8").split(
        "## H versus R: every differing JSON leaf path\n", 1)[1].split(
            "## Evidence roles and interpretation boundaries\n", 1)[0]
    documented = set(re.findall(r"^\| `([^`]+)` \|", section, flags=re.M))
    assert actual == documented
    assert len(actual) == 36
    assert set(differing_leaves({"x": None}, {})) == {"x"}
    assert "design_review.identity_gate.event_time_cik_security_evidence" in actual
