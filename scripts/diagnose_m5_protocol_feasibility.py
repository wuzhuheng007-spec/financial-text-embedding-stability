"""Compare proposed M5 identity designs using saved engineering evidence only.

This tool reads two hash-bound private saved views. It does not reparse licensed
source CSVs, establish historical CIK validity, admit a cohort, or calculate a
financial label. Terminal output is restricted to fixed status codes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import audit_m5_wrds_intake as audit
import prepare_m5_candidate_screen as screen
import prepare_m5_existing_attributes as attributes


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/m5_protocol_joint_diagnostic_20261113.json"
ROUND8 = ROOT / "configs/m5_financial_protocol_round8.json"
ATTRIBUTE_DIR = ROOT / "artifacts/m5_existing_attribute_inventory_20261113_v3"
CANDIDATE_DIR = ROOT / "artifacts/m5_preparation_candidate_20261113_v1"
SOURCE_PATHS = {
    "attribute_view": ATTRIBUTE_DIR / "m5_attribute_view_private.json",
    "attribute_manifest": ATTRIBUTE_DIR / "m5_attribute_manifest_private.json",
    "attribute_registry": ATTRIBUTE_DIR / "m5_attribute_registry_private.json",
    "attribute_summary": ATTRIBUTE_DIR / "m5_attribute_summary_safe.json",
    "candidate_view": CANDIDATE_DIR / "m5_preparation_candidate_screen_private.json",
    "candidate_manifest": CANDIDATE_DIR / "m5_preparation_candidate_screen_manifest_private.json",
    "candidate_registry": CANDIDATE_DIR / "m5_preparation_candidate_screen_registry_private.json",
    "candidate_summary": CANDIDATE_DIR / "m5_preparation_candidate_screen_summary_safe.json",
}
OUTPUT_FILES = {
    "view": "m5_protocol_joint_diagnostic_private.json",
    "summary": "m5_protocol_joint_diagnostic_summary_safe.json",
    "registry": "m5_protocol_joint_diagnostic_registry_private.json",
    "manifest": "m5_protocol_joint_diagnostic_manifest_private.json",
}
ARRAY_START = re.compile(r'^  "([a-z_]+)": \[$')
EMPTY_ARRAY = re.compile(r'^  "([a-z_]+)": \[\],?$')
REFERENCE_KEYS = ("securitytype", "securitysubtype", "sharetype", "issuertype")


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def stream_arrays(path: Path, wanted: frozenset[str]):
    """Read the pinned pretty-printed top-level object arrays incrementally."""
    seen: set[str] = set()
    active: str | None = None
    buffer: list[str] = []
    with path.open("r", encoding="utf-8", newline="") as stream:
        for line in stream:
            stripped = line.rstrip("\r\n")
            if active is None:
                empty = EMPTY_ARRAY.fullmatch(stripped)
                if empty and empty.group(1) in wanted:
                    audit.require(empty.group(1) not in seen, "SOURCE_ARRAY_DUPLICATED")
                    seen.add(empty.group(1))
                    continue
                match = ARRAY_START.fullmatch(stripped)
                if match and match.group(1) in wanted:
                    active = match.group(1)
                    audit.require(active not in seen, "SOURCE_ARRAY_DUPLICATED")
                    seen.add(active)
                continue
            if stripped in ("  ],", "  ]"):
                audit.require(not buffer, "SOURCE_ARRAY_TRUNCATED")
                active = None
                continue
            if not buffer:
                audit.require(stripped == "    {", "SOURCE_ARRAY_FORMAT_INVALID")
            buffer.append(line)
            if stripped in ("    },", "    }"):
                record = "".join(buffer).rstrip("\r\n")
                if record.endswith(","):
                    record = record[:-1]
                yield active, json.loads(record)
                buffer = []
    audit.require(active is None and seen == wanted, "SOURCE_ARRAY_COVERAGE_INVALID")


def check_config() -> tuple[dict, str]:
    cfg_hash = audit.sha256_file(CONFIG)
    cfg = load_json(CONFIG)
    audit.require(cfg.get("schema") == "m5_protocol_joint_diagnostic_config_v1"
                  and cfg.get("scope") == "ENGINEERING_ONLY_PROPOSED_DESIGNS"
                  and cfg.get("status") == "PROPOSED_NOT_SELECTED"
                  and cfg.get("researcher_accepted") is False
                  and cfg.get("formal_financial_experiment_status") == "NOT_RUN"
                  and cfg["result_gates"] == {
                      "H_time_valid_cik_primary_admitted_count": None,
                      "R_formal_admitted_count": None,
                      "researcher_selected_design": None,
                      "event_mask": None,
                      "financial_labels": "NOT_RUN",
                  }
                  and cfg["reference_date"] == "2020-01-01"
                  and cfg["reference_date_status"] == "PROPOSED_DIAGNOSTIC_ONLY"
                  and cfg["endpoint_conventions"] == ["CLOSED", "LEFT_CLOSED_RIGHT_OPEN"]
                  and cfg["quality_codes"] == ["2", "3"]
                  and cfg["quality_screen_status"] == "CANDIDATE_SCREEN_PASS",
                  "DIAGNOSTIC_CONFIG_SCOPE_INVALID")
    audit.require(cfg["original_round8_candidate_sha256"] ==
                  audit.sha256_file(ROUND8) == screen.CANDIDATE_SHA256,
                  "ORIGINAL_CANDIDATE_CHANGED")
    audit.require(cfg["code_pattern"] == {
        "securitytype": ["EQTY"], "securitysubtype": ["COM"],
        "sharetype": ["NS"], "issuertype": ["ACOR", "CORP"]},
        "CODE_PATTERN_CHANGED")
    audit.require(cfg["relation_policy"]["historical_validity_established"] is False
                  and cfg["relation_policy"]["primary_security_established"] is False
                  and cfg["relation_policy"]["eligible_cohort_selected"] is False
                  and cfg["strata"]["usincflg_public_values"] == ["Y", "N"]
                  and cfg["strata"]["primaryexch_public_examples"] ==
                  ["A", "B", "I", "N", "Q", "R", "X"]
                  and cfg["strata"]["primaryexch_is_mic_or_company_primary"] is False,
                  "DIAGNOSTIC_RULE_SCOPE_INVALID")
    audit.require(Path(audit.__file__).resolve() == ROOT / "scripts/audit_m5_wrds_intake.py"
                  and Path(screen.__file__).resolve() == ROOT / "scripts/prepare_m5_candidate_screen.py"
                  and Path(attributes.__file__).resolve() == ROOT / "scripts/prepare_m5_existing_attributes.py",
                  "IMPORTED_SOURCE_PATH_MISMATCH")
    audit.require(audit.sha256_file(Path(audit.__file__)) == screen.AUDIT_HELPER_SHA256,
                  "AUDIT_HELPER_HASH_MISMATCH")
    return cfg, cfg_hash


def check_saved_sources(cfg: dict) -> tuple[dict, dict]:
    """Bind saved outputs and their code; do not rerun prior data audits."""
    expected = cfg["source_files"]
    audit.require(set(expected) == set(SOURCE_PATHS), "SOURCE_ROLE_SET_CHANGED")
    for role, path in SOURCE_PATHS.items():
        audit.require(path.is_file() and audit.sha256_file(path) == expected[role],
                      "SAVED_SOURCE_HASH_MISMATCH")
    for prefix, directory, files in (
        ("attribute", ATTRIBUTE_DIR, attributes.FILES),
        ("candidate", CANDIDATE_DIR, screen.OUTPUT_FILES),
    ):
        manifest = load_json(SOURCE_PATHS[f"{prefix}_manifest"])
        view_name = files["view"] if prefix == "attribute" else files["index"]
        audit.require(set(manifest["files"]) ==
                      {view_name, files["registry"], files["summary"]},
                      "SAVED_SOURCE_MANIFEST_INVALID")
        for name in (view_name, files["registry"], files["summary"]):
            role = (f"{prefix}_view" if name == view_name else
                    f"{prefix}_registry" if name == files["registry"] else
                    f"{prefix}_summary")
            audit.require(manifest["files"][name]["sha256"] == expected[role]
                          and manifest["files"][name]["bytes"] ==
                          (directory / name).stat().st_size,
                          "SAVED_SOURCE_MANIFEST_BINDING_MISMATCH")
    attr_registry = load_json(SOURCE_PATHS["attribute_registry"])
    cand_registry = load_json(SOURCE_PATHS["candidate_registry"])
    audit.require(attr_registry.get("schema") == "m5_attribute_registry_private_v1"
                  and attr_registry["script_sha256"] ==
                  audit.sha256_file(Path(attributes.__file__))
                  and attr_registry["researcher_accepted_full_m5"] is False
                  and attr_registry["formal_cohort_count"] is None
                  and attr_registry["financial_experiment_run"] is False
                  and attr_registry["m5_candidate_sha256"] ==
                  cfg["original_round8_candidate_sha256"],
                  "ATTRIBUTE_SOURCE_REGISTRY_INVALID")
    audit.require(cand_registry.get("schema") ==
                  "m5_preparation_candidate_screen_registry_private_v1"
                  and cand_registry["script_sha256"] ==
                  audit.sha256_file(Path(screen.__file__))
                  and cand_registry["full_m5_protocol_researcher_accepted"] is False
                  and cand_registry["formal_m5_cohort_count"] is None
                  and cand_registry["financial_experiment_run"] is False
                  and cand_registry["m5_candidate_sha256"] ==
                  cfg["original_round8_candidate_sha256"],
                  "CANDIDATE_SOURCE_REGISTRY_INVALID")
    attr_summary = load_json(SOURCE_PATHS["attribute_summary"])
    cand_summary = load_json(SOURCE_PATHS["candidate_summary"])
    audit.require(attr_summary["issue_interval_count"] ==
                  cand_summary["issue_interval_count"] == 29404
                  and attr_summary["security_count"] ==
                  cand_summary["starting_security_count"] == 7639
                  and attr_summary["cik_observation_edge_count"] ==
                  cand_summary["cik_observation_edge_count"] == 47125
                  and cand_summary["screened_edge_status_counts"]["CANDIDATE_SCREEN_PASS"] == 16685
                  and cand_summary["distinct_screened_cik_count"] == 3977
                  and attr_summary["distinct_cik_observation_candidate_count"] == 6921,
                  "PRIOR_DENOMINATORS_CHANGED")
    return attr_summary, cand_summary


def project_saved_views() -> tuple[list[dict], list[dict]]:
    """Retain only fields required for the one-time design diagnostic."""
    issues: list[dict] = []
    for section, row in stream_arrays(SOURCE_PATHS["attribute_view"],
                                      frozenset({"issue_interval_attributes"})):
        audit.require(section == "issue_interval_attributes"
                      and row["admitted"] is None
                      and row["primary_security"] is None
                      and row["historical_mic"] is None,
                      "ATTRIBUTE_ROW_SCOPE_INVALID")
        fields = row["security_fields"]
        issue_id = f'SECURITY:{row["security_source_role"]}:{row["security_source_record"]}'
        issues.append({"issue_id": issue_id, "security": row["issue_permno"],
                       "issuer": row["issue_permco"],
                       "start": row["source_interval"]["start"],
                       "end": row["source_interval"]["end"],
                       "fields": {name: fields[name]["raw_value"] for name in
                                  (*REFERENCE_KEYS, "usincflg", "primaryexch")}})
    issue_lookup = {row["issue_id"]: row for row in issues}
    audit.require(len(issues) == len(issue_lookup), "ISSUE_REFERENCE_DUPLICATED")
    edges: list[dict] = []
    candidate_issue_ids: set[str] = set()
    for section, row in stream_arrays(SOURCE_PATHS["candidate_view"],
                                      frozenset({"cik_observation_edge", "issue_evidence"})):
        if section == "issue_evidence":
            issue_id = row["issue_evidence_id"]
            audit.require(issue_id not in candidate_issue_ids,
                          "CANDIDATE_ISSUE_REFERENCE_DUPLICATED")
            candidate_issue_ids.add(issue_id)
            issue = issue_lookup.get(issue_id)
            audit.require(issue is not None
                          and issue["security"] == row["permno"]
                          and issue["issuer"] == row["permco"]
                          and issue["start"] == row["security_interval"]["start"]
                          and issue["end"] == row["security_interval"]["end"]
                          and row["accepted"] is False,
                          "CANDIDATE_ISSUE_SOURCE_MISMATCH")
            continue
        audit.require(row["issue_evidence_id"] in issue_lookup
                      and row["accepted"] is False
                      and row["historical_valid_from"] is None
                      and row["historical_valid_to"] is None
                      and row["observed_filing_dates"]["date_semantics"] == "OBSERVATION_TIME"
                      and row["observed_filing_dates"]["historical_link_validity_established"] is False
                      and row["quality_screen_status"] ==
                      screen._screen_state(row["validation_code"], row["cik"]),
                      "CANDIDATE_EDGE_SOURCE_MISMATCH")
        edges.append({"issue_id": row["issue_evidence_id"],
                      "edge_id": row["identity_candidate_id"],
                      "cik": row["cik"],
                      "screen_status": row["quality_screen_status"],
                      "quality": str(row["validation_code"]) if row["validation_code"] is not None
                      else None})
    audit.require(candidate_issue_ids == set(issue_lookup)
                  and len(edges) == 47125,
                  "SAVED_VIEW_RECORD_COUNTS_MISMATCH")
    return issues, edges


def date_state(start: object, end: object, reference: date, convention: str) -> str:
    """Classify one bounded interval without inventing a missing endpoint."""
    audit.require(convention in ("CLOSED", "LEFT_CLOSED_RIGHT_OPEN"),
                  "ENDPOINT_CONVENTION_INVALID")
    if start in (None, "") or end in (None, ""):
        return "UNKNOWN_ENDPOINT"
    try:
        start_dt = date.fromisoformat(start)
        end_dt = date.fromisoformat(end)
    except (TypeError, ValueError):
        return "INVALID_DATE"
    if start_dt > end_dt:
        return "REVERSED_INTERVAL"
    included = (start_dt <= reference <= end_dt if convention == "CLOSED"
                else start_dt <= reference < end_dt)
    return "ACTIVE" if included else "NOT_ACTIVE"


def code_stage(fields: dict, pattern: dict) -> tuple[int, str]:
    """Return the cumulative stage and a reason, with no eligibility claim."""
    for index, field in enumerate(REFERENCE_KEYS, start=1):
        value = fields.get(field)
        if value in (None, ""):
            return index - 1, f"{field.upper()}_MISSING"
        if value not in pattern[field]:
            return index - 1, f"{field.upper()}_OUTSIDE_PROPOSED_PATTERN"
    return len(REFERENCE_KEYS), "PATTERN_MATCH"


def public_stratum(value: object, public: list[str]) -> str:
    if value in (None, ""):
        return "MISSING"
    return value if value in public else "OTHER_UNMAPPED_NOT_INVALID"


def evaluate(issues: list[dict], edges: list[dict], cfg: dict) -> tuple[dict, dict]:
    """Compute both proposed endpoint scenarios from the same saved records."""
    reference = date.fromisoformat(cfg["reference_date"])
    issue_ids = [item["issue_id"] for item in issues]
    issue_id_set = set(issue_ids)
    audit.require(len(issue_ids) == len(issue_id_set), "ISSUE_REFERENCE_DUPLICATED")
    by_issue: dict[str, list[dict]] = defaultdict(list)
    edge_ids = set()
    for edge in edges:
        audit.require(edge["edge_id"] not in edge_ids and edge["issue_id"] in issue_id_set,
                      "EDGE_REFERENCE_INVALID")
        edge_ids.add(edge["edge_id"])
        by_issue[edge["issue_id"]].append(edge)
    all_pass = [edge for edge in edges if edge["screen_status"] == cfg["quality_screen_status"]]
    audit.require(all(edge["quality"] in cfg["quality_codes"] and edge["cik"] not in (None, "")
                      for edge in all_pass), "PASSED_QUALITY_IDENTITY_INVALID")
    scenarios = {}
    private_cases = []
    for convention in cfg["endpoint_conventions"]:
        date_counts = Counter()
        active_code_reasons = Counter()
        cumulative_stage_counts = Counter()
        cumulative_stage_security: dict[int, set[str]] = defaultdict(set)
        active_issue_ids = set()
        pattern_issues = []
        case_rows = []
        for issue in issues:
            state = date_state(issue["start"], issue["end"], reference, convention)
            date_counts[state] += 1
            stage, reason = code_stage(issue["fields"], cfg["code_pattern"])
            if state == "ACTIVE":
                active_issue_ids.add(issue["issue_id"])
                active_code_reasons[reason] += 1
                for layer in range(1, stage + 1):
                    cumulative_stage_counts[layer] += 1
                    cumulative_stage_security[layer].add(issue["security"])
                if stage == len(REFERENCE_KEYS):
                    pattern_issues.append(issue)
            case_rows.append({"issue_id": issue["issue_id"], "security": issue["security"],
                              "issuer": issue["issuer"], "date_state": state,
                              "code_stage": stage, "code_reason": reason,
                              "screened_candidate_ciks": sorted({edge["cik"] for edge in
                                  by_issue[issue["issue_id"]] if
                                  edge["screen_status"] == cfg["quality_screen_status"]}),
                              "accepted": False})
        chosen = {issue["issue_id"]: issue for issue in pattern_issues}
        selected_edges = [edge for edge in all_pass if edge["issue_id"] in chosen]
        per_issue_ciks: dict[str, set[str]] = defaultdict(set)
        per_issue_edge_counts: Counter[str] = Counter()
        per_pair_edge_counts: Counter[tuple[str, str]] = Counter()
        security_to_cik: dict[str, set[str]] = defaultdict(set)
        cik_to_security: dict[str, set[str]] = defaultdict(set)
        for edge in selected_edges:
            issue = chosen[edge["issue_id"]]
            cik = edge["cik"]
            security = issue["security"]
            per_issue_ciks[edge["issue_id"]].add(cik)
            per_issue_edge_counts[edge["issue_id"]] += 1
            per_pair_edge_counts[(cik, security)] += 1
            security_to_cik[security].add(cik)
            cik_to_security[cik].add(security)
        pairs = set(per_pair_edge_counts)
        active_issues = [issue for issue in issues if issue["issue_id"] in active_issue_ids]
        active_interval_by_security = Counter(issue["security"] for issue in active_issues)
        strict_pairs = {(cik, security) for cik, security in pairs
                        if len(security_to_cik[security]) == 1
                        and len(cik_to_security[cik]) == 1
                        and active_interval_by_security[security] == 1}
        usinc = Counter(public_stratum(issue["fields"].get("usincflg"),
                                      cfg["strata"]["usincflg_public_values"])
                        for issue in pattern_issues)
        exchange = Counter(public_stratum(issue["fields"].get("primaryexch"),
                                         cfg["strata"]["primaryexch_public_examples"])
                           for issue in pattern_issues)
        multiple_active_security = Counter(issue["security"] for issue in active_issues)
        audit.require(sum(date_counts.values()) == len(issues)
                      and sum(active_code_reasons.values()) == len(active_issues)
                      and cumulative_stage_counts[len(REFERENCE_KEYS)] == len(pattern_issues)
                      and sum(usinc.values()) == sum(exchange.values()) == len(pattern_issues)
                      and len(pairs) <= len(selected_edges)
                      and len(strict_pairs) <= len(pairs),
                      "DIAGNOSTIC_DENOMINATOR_MISMATCH")
        scenarios[convention] = {
            "reference_date": cfg["reference_date"],
            "reference_date_status": "PROPOSED_DIAGNOSTIC_ONLY",
            "endpoint_convention": convention,
            "endpoint_semantics_source_verified": False,
            "all_issue_interval_count": len(issues),
            "all_distinct_security_count": len({issue["security"] for issue in issues}),
            "date_state_issue_interval_counts": {k: date_counts[k] for k in
                ("ACTIVE", "NOT_ACTIVE", "UNKNOWN_ENDPOINT", "INVALID_DATE", "REVERSED_INTERVAL")},
            "active_distinct_security_count": len({issue["security"] for issue in active_issues}),
            "active_distinct_issuer_id_count": len({issue["issuer"] for issue in active_issues
                                                    if issue["issuer"] not in (None, "")}),
            "active_securities_with_multiple_intervals_count": sum(
                count > 1 for count in multiple_active_security.values()),
            "code_pattern_stage_issue_interval_counts": {
                REFERENCE_KEYS[layer - 1]: cumulative_stage_counts[layer]
                for layer in range(1, len(REFERENCE_KEYS) + 1)},
            "active_code_pattern_reason_issue_interval_counts": dict(sorted(active_code_reasons.items())),
            "code_pattern_stage_distinct_security_counts": {
                REFERENCE_KEYS[layer - 1]: len(cumulative_stage_security[layer])
                for layer in range(1, len(REFERENCE_KEYS) + 1)},
            "code_pattern_joint_issue_interval_count": len(pattern_issues),
            "code_pattern_joint_distinct_security_count": len({i["security"] for i in pattern_issues}),
            "code_pattern_joint_distinct_issuer_id_count": len({i["issuer"] for i in pattern_issues
                                                               if i["issuer"] not in (None, "")}),
            "usincflg_code_pattern_issue_interval_strata": dict(sorted(usinc.items())),
            "primaryexch_code_pattern_issue_interval_strata": dict(sorted(exchange.items())),
            "quality_2_3_pass_edge_count_on_joint_pattern": len(selected_edges),
            "quality_2_3_pass_issue_interval_count_on_joint_pattern": len(per_issue_ciks),
            "quality_2_3_pass_distinct_security_count_on_joint_pattern": len(security_to_cik),
            "quality_2_3_pass_distinct_cik_count_on_joint_pattern": len(cik_to_security),
            "quality_2_3_pass_distinct_cik_security_pair_count": len(pairs),
            "joint_pattern_issue_intervals_without_pass_edge_count":
                len(pattern_issues) - len(per_issue_ciks),
            "joint_pattern_issue_intervals_with_multiple_distinct_ciks_count": sum(
                len(ciks) > 1 for ciks in per_issue_ciks.values()),
            "joint_pattern_issue_intervals_with_parallel_same_cik_edges_count": sum(
                per_issue_edge_counts[issue_id] > len(ciks)
                for issue_id, ciks in per_issue_ciks.items()),
            "quality_2_3_pass_duplicate_edges_same_cik_security_pair_count": sum(
                count - 1 for count in per_pair_edge_counts.values()),
            "distinct_securities_with_multiple_distinct_ciks_count": sum(
                len(ciks) > 1 for ciks in security_to_cik.values()),
            "distinct_ciks_with_multiple_distinct_securities_count": sum(
                len(securities) > 1 for securities in cik_to_security.values()),
            "strict_one_to_one_structural_pair_count": len(strict_pairs),
            "strict_one_to_one_structural_distinct_security_count": len({s for _, s in strict_pairs}),
            "strict_one_to_one_structural_distinct_cik_count": len({c for c, _ in strict_pairs}),
            "strict_one_to_one_status": "FIXED_SAVED_SNAPSHOT_ASSOCIATION_ONLY_NOT_HISTORICAL_VALIDITY",
            "strict_one_to_one_requires_one_active_source_interval_per_security": True,
            "strict_one_to_one_uses_quality_2_3_screened_graph_only": True,
            "H_time_valid_cik_primary_admitted_count": None,
            "R_formal_admitted_count": None,
            "historical_primary_security_evidence": None,
            "historical_cik_link_validity": None,
            "formal_event_mask": None,
        }
        private_cases.append({"endpoint_convention": convention,
                              "issue_diagnostics": case_rows,
                              "structural_unique_pairs": [
                                  {"cik": c, "security": s, "accepted": False}
                                  for c, s in sorted(strict_pairs)]})
    private_view = {"schema": "m5_protocol_joint_diagnostic_private_v1",
                    "scope": "ENGINEERING_ONLY_PROPOSED_DESIGNS",
                    "selected_design": None, "accepted": False,
                    "cases": private_cases, "financial_experiment_run": False}
    summary = {"schema": "m5_protocol_joint_diagnostic_summary_safe_v1",
               "scope": "ENGINEERING_ONLY_PROPOSED_DESIGNS",
               "status": "JOINT_DESIGN_DIAGNOSTIC_NOT_ADMITTED",
               "selected_design": None, "researcher_accepted": False,
               "formal_financial_experiment_status": "NOT_RUN",
               "source_scope": "SAVED_ATTRIBUTE_AND_CANDIDATE_VIEWS_ONLY_NOT_RAW_CSV_REPLAY",
               "full_source_screen": {
                   "issue_interval_count": len(issues),
                   "distinct_security_count": len({issue["security"] for issue in issues}),
                   "quality_2_3_pass_edge_count": len(all_pass),
                   "quality_2_3_pass_distinct_cik_count": len({edge["cik"] for edge in all_pass}),
                   "all_observation_distinct_cik_count": len({edge["cik"] for edge in edges
                                                              if edge["cik"] not in (None, "")}),
               },
               "count_units": {
                   "issue_interval": "starting-set security history source interval",
                   "security": "distinct stable security identifier within starting set",
                   "issuer_id": "distinct issuer identifier on matched security intervals",
                   "cik": "distinct filing-observation candidate CIK; not accepted company",
                   "edge": "saved quality-screened observation relation row",
                   "pair": "distinct CIK-security association collapsed only for graph-degree diagnostic",
                   "strict_one_to_one": "degree one on both CIK and security sides of fixed snapshot graph",
               },
               "scenarios": scenarios,
               "H_time_valid_cik_primary_admitted_count": None,
               "R_formal_admitted_count": None,
               "financial_labels_created": 0,
               "financial_model_sessions": 0,
               "prediction_evaluations": 0,
               "backtests": 0,
               }
    return private_view, summary


def _payload_hash(value: dict) -> str:
    return hashlib.sha256(json_bytes(value)).hexdigest()


def build_from_saved(cfg: dict, attr_summary: dict, cand_summary: dict) -> tuple[dict, dict]:
    issues, edges = project_saved_views()
    audit.require(len(issues) == attr_summary["issue_interval_count"] ==
                  cand_summary["issue_interval_count"]
                  and len(edges) == cand_summary["cik_observation_edge_count"],
                  "SAVED_PROJECTION_COUNTS_MISMATCH")
    view, summary = evaluate(issues, edges, cfg)
    audit.require(summary["full_source_screen"]["quality_2_3_pass_edge_count"] ==
                  cand_summary["screened_edge_status_counts"]["CANDIDATE_SCREEN_PASS"]
                  and summary["full_source_screen"]["quality_2_3_pass_distinct_cik_count"] ==
                  cand_summary["distinct_screened_cik_count"]
                  and summary["full_source_screen"]["all_observation_distinct_cik_count"] ==
                  attr_summary["distinct_cik_observation_candidate_count"],
                  "SAVED_SCREEN_DENOMINATORS_MISMATCH")
    summary["full_source_screen"]["later_only_interval_count_not_joined"] = (
        cand_summary["later_only_interval_count"])
    summary["full_source_screen"]["later_only_distinct_security_count_not_joined"] = (
        cand_summary["later_only_distinct_security_count"])
    return view, summary


def run(output_dir: Path) -> None:
    cfg, cfg_hash = check_config()
    output_dir = output_dir.resolve()
    audit.check_ignored_output(output_dir)
    attr_summary, cand_summary = check_saved_sources(cfg)
    view, summary = build_from_saved(cfg, attr_summary, cand_summary)
    for role, path in SOURCE_PATHS.items():
        audit.require(audit.sha256_file(path) == cfg["source_files"][role],
                      "SAVED_SOURCE_CHANGED_DURING_BUILD")
    source_hashes = dict(cfg["source_files"])
    code_hash = audit.sha256_file(Path(__file__))
    summary.update({"configuration_sha256": cfg_hash,
                    "execution_code_sha256": code_hash,
                    "source_file_sha256": source_hashes,
                    "private_view_sha256": _payload_hash(view)})
    registry = {"schema": "m5_protocol_joint_diagnostic_registry_private_v1",
                "scope": "ENGINEERING_ONLY_PROPOSED_DESIGNS",
                "configuration_path": str(CONFIG), "configuration_sha256": cfg_hash,
                "code_path": str(Path(__file__).resolve()), "code_sha256": code_hash,
                "saved_source_paths": {role: str(path) for role, path in SOURCE_PATHS.items()},
                "saved_source_sha256": source_hashes,
                "round8_candidate_sha256": cfg["original_round8_candidate_sha256"],
                "selected_design": None, "researcher_accepted": False,
                "H_admitted_count": None, "R_admitted_count": None,
                "formal_financial_experiment_status": "NOT_RUN"}
    files = {OUTPUT_FILES["view"]: view, OUTPUT_FILES["summary"]: summary,
             OUTPUT_FILES["registry"]: registry}
    files[OUTPUT_FILES["manifest"]] = {
        "schema": "m5_protocol_joint_diagnostic_manifest_private_v1",
        "files": {name: {"sha256": _payload_hash(payload),
                         "bytes": len(json_bytes(payload))} for name, payload in files.items()}}
    audit.write_directory(output_dir, files)


def verify_output(output_dir: Path) -> None:
    cfg, cfg_hash = check_config()
    output_dir = output_dir.resolve(strict=True)
    paths = {role: output_dir / name for role, name in OUTPUT_FILES.items()}
    audit.require({p.name for p in output_dir.iterdir()} == set(OUTPUT_FILES.values())
                  and all(path.is_file() for path in paths.values()),
                  "OUTPUT_FILE_SET_INVALID")
    manifest = load_json(paths["manifest"])
    audit.require(manifest.get("schema") == "m5_protocol_joint_diagnostic_manifest_private_v1"
                  and set(manifest["files"]) ==
                  {OUTPUT_FILES[role] for role in ("view", "summary", "registry")},
                  "OUTPUT_MANIFEST_INVALID")
    for role in ("view", "summary", "registry"):
        item = manifest["files"][OUTPUT_FILES[role]]
        audit.require(audit.sha256_file(paths[role]) == item["sha256"]
                      and paths[role].stat().st_size == item["bytes"],
                      "OUTPUT_FILE_HASH_MISMATCH")
    registry = load_json(paths["registry"])
    audit.require(registry.get("schema") == "m5_protocol_joint_diagnostic_registry_private_v1"
                  and registry.get("configuration_sha256") == cfg_hash
                  and registry.get("code_sha256") == audit.sha256_file(Path(__file__))
                  and Path(registry.get("code_path", "")).resolve() == Path(__file__).resolve()
                  and registry.get("selected_design") is None
                  and registry.get("researcher_accepted") is False
                  and registry.get("H_admitted_count") is None
                  and registry.get("R_admitted_count") is None
                  and registry.get("formal_financial_experiment_status") == "NOT_RUN"
                  and registry.get("saved_source_sha256") == cfg["source_files"],
                  "OUTPUT_IDENTITY_OR_SCOPE_INVALID")
    attr_summary, cand_summary = check_saved_sources(cfg)
    rebuilt_view, rebuilt_summary = build_from_saved(cfg, attr_summary, cand_summary)
    rebuilt_summary.update({"configuration_sha256": cfg_hash,
                            "execution_code_sha256": registry["code_sha256"],
                            "source_file_sha256": dict(cfg["source_files"]),
                            "private_view_sha256": _payload_hash(rebuilt_view)})
    audit.require(_payload_hash(rebuilt_view) == audit.sha256_file(paths["view"])
                  and _payload_hash(rebuilt_summary) == audit.sha256_file(paths["summary"]),
                  "OUTPUT_RECOMPUTE_MISMATCH")


def main() -> int:
    class SafeParser(argparse.ArgumentParser):
        def error(self, message: str) -> None:
            raise audit.AuditError("CLI_ARGUMENT_INVALID")

    try:
        parser = SafeParser(description=__doc__)
        sub = parser.add_subparsers(dest="action", required=True)
        for action in ("run", "verify-output"):
            sub.add_parser(action).add_argument("--output-dir", type=Path, required=True)
        args = parser.parse_args()
        if args.action == "run":
            run(args.output_dir)
            print("M5_PROTOCOL_JOINT_DIAGNOSTIC_COMPLETE")
        else:
            verify_output(args.output_dir)
            print("M5_PROTOCOL_JOINT_DIAGNOSTIC_VERIFY_PASS")
        return 0
    except audit.AuditError as exc:
        print("M5_PROTOCOL_JOINT_DIAGNOSTIC_FAIL " + str(exc))
        return 2
    except Exception:
        print("M5_PROTOCOL_JOINT_DIAGNOSTIC_FAIL UNEXPECTED_ERROR")
        return 3


if __name__ == "__main__":
    sys.exit(main())
