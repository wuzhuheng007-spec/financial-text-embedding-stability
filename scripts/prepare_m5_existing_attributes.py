"""Inventory bound M5 identity attributes without admitting a financial cohort.

Only the two registered security CSVs are reparsed for attributes omitted from
the saved projection. Issuer attributes come from the hash-bound saved issuer
projection. This tool does not read price files or interpret CIK observations
as valid-time links. Its terminal output contains fixed diagnostic codes only.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import audit_m5_wrds_intake as audit
import prepare_m5_candidate_screen as preparation


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/m5_existing_attribute_inventory_20261113.json"
DEFINITION_NOTES = ROOT / "artifacts/m5_existing_attribute_evidence_20261113_v1/handoff/05_PRIMARY_SOURCE_NOTES.md"
SOURCE_DIR = ROOT / "artifacts/m5_identity_candidate_ledger_20261113_v1"
ISSUER_INDEX_NAME = "m5_issuer_history_index_private.json"
FILES = {
    "view": "m5_attribute_view_private.json",
    "registry": "m5_attribute_registry_private.json",
    "summary": "m5_attribute_summary_safe.json",
    "manifest": "m5_attribute_manifest_private.json",
}
SOURCE_KEY = {"STARTING_SNAPSHOT": "SECURITY_STARTING", "LATER_CHANGES": "SECURITY_LATER"}
FIELD_REFERENCE_CODES = {
    "securitytype": frozenset({"EQTY"}),
    "securitysubtype": frozenset({"COM"}),
    "sharetype": frozenset({"NS"}),
    "issuertype": frozenset({"ACOR", "CORP"}),
    "usincflg": frozenset({"Y", "N"}),
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _check_config() -> tuple[dict, str]:
    config_hash = audit.sha256_file(CONFIG)
    cfg = _load_json(CONFIG)
    audit.require(cfg.get("schema") == "m5_existing_attribute_inventory_config_v1"
                  and cfg.get("scope") == "ENGINEERING_ONLY_ATTRIBUTE_INVENTORY"
                  and cfg["research_rules"] == {
                      "ordinary_share_eligibility": None,
                      "historical_mic": None,
                      "primary_security": None,
                      "formal_cohort": None,
                      "full_m5_protocol_researcher_accepted": False,
                  }, "CONFIG_SCOPE_INVALID")
    audit.require(Path(preparation.__file__).resolve() == ROOT / "scripts/prepare_m5_candidate_screen.py"
                  and Path(audit.__file__).resolve() == ROOT / "scripts/audit_m5_wrds_intake.py",
                  "IMPORTED_CODE_PATH_MISMATCH")
    expected = cfg["source_identity"]
    audit.require(audit.sha256_file(Path(preparation.__file__)) ==
                  expected["preparation_screen_code_sha256"] and
                  audit.sha256_file(DEFINITION_NOTES) == expected["definition_notes_sha256"],
                  "DEPENDENCY_HASH_MISMATCH")
    for role in ("security", "issuer"):
        audit.require(isinstance(cfg["fields"][role], list)
                      and len(cfg["fields"][role]) == len(set(cfg["fields"][role]))
                      and all(isinstance(field, str) and field.islower()
                              for field in cfg["fields"][role]), "CONFIG_FIELDS_INVALID")
    return cfg, config_hash


def _bind_security_headers(manifest: dict, source: dict) -> dict:
    """Recover the header identity omitted from the thin source manifest."""
    wrds_registry_path = (Path(manifest["wrds_artifact_directory"]) /
                          "m5_wrds_intake_registry.json")
    audit.require(audit.sha256_file(wrds_registry_path) ==
                  manifest["wrds_artifact_sha256"]["registry"],
                  "WRDS_REGISTRY_BINDING_MISMATCH")
    wrds_registry = _load_json(wrds_registry_path)
    audit.require(wrds_registry["local_snapshot"]["sha256"] ==
                  source["SECURITY_STARTING"]["sha256"]
                  and wrds_registry["local_changes"]["sha256"] ==
                  source["SECURITY_LATER"]["sha256"],
                  "WRDS_REGISTRY_SOURCE_MISMATCH")
    snapshot_columns = wrds_registry["local_snapshot"]["column_names"]
    later_columns = wrds_registry["local_changes"].get("column_names")
    if later_columns is None:
        later_columns = snapshot_columns
        source["SECURITY_LATER"]["column_names_provenance"] = (
            "SNAPSHOT_COLUMNS_AND_PRIOR_LATER_HEADER_SET_CHECK")
    else:
        source["SECURITY_LATER"]["column_names_provenance"] = "LATER_REGISTRY_COLUMNS"
    source["SECURITY_STARTING"]["column_names"] = snapshot_columns
    source["SECURITY_LATER"]["column_names"] = later_columns
    return source


def _load_bound_sources(cfg: dict) -> tuple[dict, dict, dict, dict]:
    preparation.check_rules()
    identity_registry, identity_summary, hashes = preparation.check_saved_source(SOURCE_DIR)
    expected = cfg["source_identity"]
    audit.require(hashes["ledger"] == expected["identity_ledger_sha256"]
                  and identity_summary["issue_interval_count"] == 29404,
                  "IDENTITY_LEDGER_BINDING_MISMATCH")
    manifest = identity_registry["source_manifest"]
    source = _bind_security_headers(manifest, manifest["registered_source_files"])
    for role, key in (("SECURITY_STARTING", "security_starting_sha256"),
                      ("SECURITY_LATER", "security_later_sha256"),
                      ("ISSUER_EARLY", "issuer_early_sha256"),
                      ("ISSUER_LATER", "issuer_later_sha256")):
        audit.require(source[role]["sha256"] == expected[key], "RAW_SOURCE_BINDING_MISMATCH")
    issuer_path = Path(manifest["issuer_artifact_directory"]) / ISSUER_INDEX_NAME
    audit.require(audit.sha256_file(issuer_path) == expected["issuer_index_sha256"]
                  == manifest["issuer_artifact_sha256"]["index"],
                  "ISSUER_INDEX_BINDING_MISMATCH")
    issuer_index = _load_json(issuer_path)
    audit.require(issuer_index.get("schema") == "m5_issuer_history_candidates_private_v1"
                  and issuer_index.get("accepted") is False,
                  "ISSUER_INDEX_SCOPE_INVALID")
    return identity_registry, identity_summary, issuer_index, source


def _scan_security_csvs(issue_rows: list[dict], source: dict, fields: list[str]) -> tuple[dict, dict]:
    expected = defaultdict(dict)
    for issue in issue_rows:
        role = issue["source_role"]
        number = issue["source_record"]
        audit.require(role in SOURCE_KEY and isinstance(number, int) and number > 0
                      and number not in expected[role], "ISSUE_SOURCE_REFERENCE_INVALID")
        expected[role][number] = issue["normalized_row_sha256"]
    projected = {}
    header_status = {}
    for role, source_key in SOURCE_KEY.items():
        binding = source[source_key]
        path = Path(binding["path"])
        audit.require(audit.sha256_file(path) == binding["sha256"], "SECURITY_SOURCE_HASH_MISMATCH")
        header = None
        seen = set()
        for current_header, number, row in audit.read_csv(path):
            if number is None:
                header = current_header
                audit.require(set(header) == set(binding["column_names"]),
                              "SECURITY_HEADER_BINDING_MISMATCH")
                continue
            if number not in expected[role]:
                continue
            audit.require(audit.row_digest(row) == expected[role][number],
                          "SECURITY_ROW_DIGEST_MISMATCH")
            seen.add(number)
            projected[(role, number)] = {field: row.get(field) for field in fields}
        audit.require(seen == set(expected[role]), "SECURITY_SOURCE_ROW_MISSING")
        audit.require(audit.sha256_file(path) == binding["sha256"],
                      "SECURITY_SOURCE_CHANGED_DURING_READ")
        header_status[source_key] = {
            "source_field_present": {field: field in header for field in fields},
            "saved_identity_projection_field_state": {
                field: ("DERIVED_INTERVAL_MAPPING_PRESENT" if field in
                        ("secinfostartdt", "secinfoenddt") else "DIRECT_RAW_FIELD_ABSENT")
                for field in fields},
            "source_rows_reparsed_for_issue_intervals": len(seen),
        }
    return projected, header_status


def _issuer_projection(index: dict, issue_rows: list[dict], source: dict,
                       fields: list[str]) -> tuple[dict, dict]:
    records = {}
    for record in index["related_issuer_records"]:
        key = (record["source_role"], record["source_record"])
        audit.require(key not in records and key[0] in ("EARLY", "LATER")
                      and audit.row_digest(record["raw_fields"]) ==
                      record["normalized_row_sha256"]
                      and record["source_sha256"] == source[f"ISSUER_{key[0]}"]["sha256"],
                      "ISSUER_PROJECTION_INVALID")
        records[key] = record
    needed = {(edge["issuer_source_role"], edge["issuer_source_record"])
              for issue in issue_rows for edge in issue["issuer_matches"]}
    audit.require(needed.issubset(records), "ISSUER_MATCH_PROJECTION_MISSING")
    header_status = {}
    for role, source_key in (("EARLY", "ISSUER_EARLY"), ("LATER", "ISSUER_LATER")):
        path = Path(source[source_key]["path"])
        audit.require(audit.sha256_file(path) == source[source_key]["sha256"],
                      "ISSUER_SOURCE_HASH_MISMATCH")
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            import csv
            reader = csv.reader(stream, strict=True)
            header = tuple(value.strip().lower() for value in next(reader))
        audit.require(len(header) == len(set(header)),
                      "ISSUER_SOURCE_HEADER_INVALID")
        header_status[source_key] = {
            "source_field_present": {field: field in header for field in fields},
            "saved_issuer_projection_field_state": {
                field: ("DIRECT_RAW_FIELD_PRESENT" if all(
                    field in records[key]["raw_fields"] for key in needed if key[0] == role)
                    else "DIRECT_RAW_FIELD_ABSENT") for field in fields},
            "issuer_source_rows_reparsed": 0,
            "issuer_projection_rows_used": len({key for key in needed if key[0] == role}),
        }
        for key in needed:
            if key[0] == role:
                records[key]["_source_field_present"] = {field: field in header for field in fields}
    return records, header_status


def lexical_state(field: str, value: str | None) -> str:
    """Classify a literal without claiming bound extract code semantics."""
    if value is None:
        return "MISSING_FROM_BOUND_SOURCE"
    if value == "":
        return "VALUE_MISSING"
    if field in ("secinfostartdt", "secinfoenddt", "issinfostartdt", "issinfoenddt"):
        return "INTERVAL_APPLICABILITY_UNRESOLVED"
    if field in FIELD_REFERENCE_CODES:
        if value in FIELD_REFERENCE_CODES[field]:
            return "CURRENT_PUBLIC_REFERENCE_CODE_ONLY"
        return "OUTSIDE_SELECTED_PUBLIC_REFERENCE_SET"
    return "DEFINITION_UNVERIFIED"


def build_view(issue_rows: list[dict], security: dict, issuer: dict,
               security_fields: list[str], issuer_fields: list[str]) -> tuple[dict, dict]:
    """Pure attribute projection; no admission or source-code interpretation."""
    rows = []
    sec_states = defaultdict(Counter)
    iss_states = defaultdict(Counter)
    sec_reference_codes = defaultdict(Counter)
    iss_reference_codes = defaultdict(Counter)
    literal_disagreements = Counter()
    overlap_disagreements = Counter()
    literal_affected = set()
    overlap_affected = set()
    securities = set()
    issuers = set()
    issuer_edge_count = 0
    issuer_with_matches = 0
    distinct_issuer_source_records = set()
    endpoint_equality_edges = 0
    endpoint_equality_issues = set()
    for issue in issue_rows:
        key = (issue["source_role"], issue["source_record"])
        audit.require(key in security, "ISSUE_ATTRIBUTE_SOURCE_MISSING")
        securities.add(issue["permno"])
        if issue.get("permco") not in (None, ""):
            issuers.add(issue["permco"])
        values = security[key]
        sec_values = {field: {"raw_value": values[field],
                              "capability_state": lexical_state(field, values[field])}
                      for field in security_fields}
        for field, entry in sec_values.items():
            sec_states[field][entry["capability_state"]] += 1
            if entry["capability_state"] == "CURRENT_PUBLIC_REFERENCE_CODE_ONLY":
                sec_reference_codes[field][entry["raw_value"]] += 1
        matches = []
        for edge in issue["issuer_matches"]:
            ikey = (edge["issuer_source_role"], edge["issuer_source_record"])
            record = issuer.get(ikey)
            audit.require(record is not None
                          and record["normalized_row_sha256"] == edge["issuer_row_sha256"]
                          and record["source_sha256"] == edge["issuer_source_sha256"],
                          "ISSUER_MATCH_SOURCE_BINDING_MISMATCH")
            ivals = {field: {"raw_value": record["raw_fields"].get(field),
                              "capability_state": (lexical_state(field,
                                                              record["raw_fields"][field])
                                                   if field in record["raw_fields"] else
                                                   ("MISSING_FROM_SAVED_PROJECTION" if
                                                    record.get("_source_field_present", {}).get(field, True)
                                                    else "MISSING_FROM_BOUND_SOURCE"))}
                     for field in issuer_fields}
            for field, entry in ivals.items():
                iss_states[field][entry["capability_state"]] += 1
                if entry["capability_state"] == "CURRENT_PUBLIC_REFERENCE_CODE_ONLY":
                    iss_reference_codes[field][entry["raw_value"]] += 1
            distinct_issuer_source_records.add(ikey)
            if edge["endpoint_equality_review"]:
                endpoint_equality_edges += 1
                endpoint_equality_issues.add(key)
            matches.append({"source_role": ikey[0], "source_record": ikey[1],
                            "source_sha256": record["source_sha256"],
                            "normalized_row_sha256": record["normalized_row_sha256"],
                            "fields": ivals,
                            "closed_date_diagnostic_intersection": {
                                "start": edge["intersection_start"],
                                "end": edge["intersection_end"]},
                            "endpoint_equality_review": edge["endpoint_equality_review"],
                            "date_applicability": (
                                "ENDPOINT_EQUALITY_UNRESOLVED" if edge["endpoint_equality_review"]
                                else "INTERVAL_APPLICABILITY_UNRESOLVED")})
        issuer_edge_count += len(matches)
        issuer_with_matches += bool(matches)
        disagreements = []
        overlap_fields = []
        for field in ("issuertype", "usincflg"):
            concrete = {values[field]} if values.get(field) else set()
            concrete.update(match["fields"][field]["raw_value"] for match in matches
                            if match["fields"][field]["raw_value"])
            if len(concrete) > 1:
                disagreements.append(field)
                literal_disagreements[field] += 1
                literal_affected.add(key)
            candidate_overlap = any(
                values.get(field) not in (None, "")
                and match["fields"][field]["raw_value"] not in (None, "", values[field])
                and match["closed_date_diagnostic_intersection"]["start"] <
                    match["closed_date_diagnostic_intersection"]["end"]
                for match in matches)
            candidate_overlap |= any(
                left["fields"][field]["raw_value"] not in (None, "")
                and right["fields"][field]["raw_value"] not in
                    (None, "", left["fields"][field]["raw_value"])
                and max(left["closed_date_diagnostic_intersection"]["start"],
                        right["closed_date_diagnostic_intersection"]["start"]) <
                    min(left["closed_date_diagnostic_intersection"]["end"],
                        right["closed_date_diagnostic_intersection"]["end"])
                for position, left in enumerate(matches)
                for right in matches[position + 1:])
            if candidate_overlap:
                overlap_fields.append(field)
                overlap_disagreements[field] += 1
                overlap_affected.add(key)
        rows.append({"security_source_role": key[0], "security_source_record": key[1],
                     "security_row_sha256": issue["normalized_row_sha256"],
                     "issue_permno": issue["permno"], "issue_permco": issue["permco"],
                     "source_interval": {"start": issue["start"], "end": issue["end"],
                                         "endpoint_inclusivity": "UNKNOWN"},
                     "issuer_closed_date_coverage_diagnostic":
                     issue["issuer_date_coverage_under_closed_comparison"],
                     "issuer_date_applicability": (
                         "UNRESOLVED_ENDPOINT_SEMANTICS" if matches else
                         "NO_CLOSED_DATE_INTERSECTION_CANDIDATE"),
                     "security_fields": sec_values, "issuer_candidates": matches,
                     "literal_disagreement_within_issue_interval_fields": disagreements,
                     "closed_date_candidate_overlap_disagreement_fields": overlap_fields,
                     "confirmed_source_conflict": None,
                     "historical_cik_validity": "NOT_ESTABLISHED",
                     "ordinary_share_eligibility": None, "primary_security": None,
                     "historical_mic": None, "admitted": None,
                     "admission_status": "NOT_ASSESSED"})
    audit.require(len(rows) == len({(row["security_source_role"], row["security_source_record"])
                                    for row in rows}), "ISSUE_ATTRIBUTE_DUPLICATED")
    view = {"schema": "m5_attribute_view_private_v1", "scope": "ENGINEERING_ONLY",
            "issue_interval_attributes": rows, "accepted": False,
            "formal_cohort_count": None, "financial_experiment_run": False}
    summary = {
        "schema": "m5_attribute_summary_safe_v1", "scope": "ENGINEERING_ONLY",
        "status": "ATTRIBUTE_SOURCE_FACTS_INVENTORIED_NOT_ADMITTED",
        "count_units": {"issue_interval_count": "starting-set issue intervals",
                        "security_count": "distinct starting-set securities",
                        "issuer_count": "distinct issuer identifiers in issue intervals",
                        "issuer_candidate_edge_count": "closed-date diagnostic issuer edges, not accepted links",
                        "conflict_issue_count": "distinct issue intervals with cross-source literal disagreement"},
        "issue_interval_count": len(rows), "security_count": len(securities),
        "issuer_count": len(issuers), "issuer_candidate_edge_count": issuer_edge_count,
        "issues_with_issuer_candidate_count": issuer_with_matches,
        "issues_without_issuer_candidate_count": len(rows) - issuer_with_matches,
        "distinct_issuer_source_record_count": len(distinct_issuer_source_records),
        "issuer_endpoint_equality_review_edge_count": endpoint_equality_edges,
        "issuer_endpoint_equality_review_distinct_issue_count": len(endpoint_equality_issues),
        "security_field_lexical_status_counts": {k: dict(sorted(v.items())) for k, v in sorted(sec_states.items())},
        "issuer_edge_field_lexical_status_counts": {k: dict(sorted(v.items())) for k, v in sorted(iss_states.items())},
        "security_public_reference_code_counts": {
            k: dict(sorted(v.items())) for k, v in sorted(sec_reference_codes.items())},
        "issuer_edge_public_reference_code_counts": {
            k: dict(sorted(v.items())) for k, v in sorted(iss_reference_codes.items())},
        "literal_disagreement_issue_count_by_field": dict(sorted(literal_disagreements.items())),
        "literal_disagreement_distinct_issue_count": len(literal_affected),
        "closed_date_candidate_overlap_disagreement_issue_count_by_field":
            dict(sorted(overlap_disagreements.items())),
        "closed_date_candidate_overlap_disagreement_distinct_issue_count": len(overlap_affected),
        "confirmed_source_conflict_count": None,
        "source_definition_status": "CURRENT_PUBLIC_REFERENCE_ONLY_NOT_BOUND_EXTRACT_REVISION",
        "issuer_interval_applicability": "UNRESOLVED_ENDPOINT_SEMANTICS",
        "historical_cik_validity": "NOT_ESTABLISHED",
        "ordinary_share_eligibility": None, "historical_mic": None,
        "primary_security": None, "formal_cohort_count": None,
        "admitted_observations": None, "financial_experiment_run": False,
    }
    return view, summary


def _candidate_reference_counts(ledger_path: Path) -> tuple[int, int, set[str]]:
    """Count saved CIK observation identities; do not reconstruct or accept links."""
    distinct_cik = set()
    observed_issue_refs = set()
    edges = 0
    for section, entry in preparation.stream_source_arrays(ledger_path):
        if section != "identity_candidate" or entry.get("candidate_kind") != "CIK_CUSIP_OBSERVATION":
            continue
        edges += 1
        if entry.get("cik") not in (None, ""):
            distinct_cik.add(entry["cik"])
        observed_issue_refs.add(entry["issue_evidence_id"])
    return len(distinct_cik), edges, observed_issue_refs


def _payload_hash(value: dict) -> str:
    import hashlib
    return hashlib.sha256(preparation.json_bytes(value)).hexdigest()


def _build(cfg: dict, identity_registry: dict, identity_summary: dict,
           issuer_index: dict, source: dict) -> tuple[dict, dict, dict]:
    issues = issuer_index["security_interval_issuer_candidates"]
    audit.require(len(issues) == identity_summary["issue_interval_count"],
                  "ISSUE_INTERVAL_DENOMINATOR_MISMATCH")
    security, security_headers = _scan_security_csvs(issues, source, cfg["fields"]["security"])
    issuer, issuer_headers = _issuer_projection(issuer_index, issues, source,
                                               cfg["fields"]["issuer"])
    view, summary = build_view(issues, security, issuer,
                               cfg["fields"]["security"], cfg["fields"]["issuer"])
    audit.require(summary["security_count"] == identity_summary["starting_security_count"]
                  and summary["issue_interval_count"] == identity_summary["issue_interval_count"]
                  and summary["issuer_candidate_edge_count"] ==
                  identity_summary["issuer_intersection_edge_count"],
                  "PRIOR_SUMMARY_DENOMINATOR_MISMATCH")
    distinct_cik, cik_edges, observed_issue_refs = _candidate_reference_counts(
        SOURCE_DIR / preparation.SOURCE_FILES["ledger"])
    audit.require(cik_edges == identity_summary["cik_observation_edge_count"],
                  "CIK_OBSERVATION_EDGE_DENOMINATOR_MISMATCH")
    securities_without_cik = {
        row["issue_permno"] for row in view["issue_interval_attributes"]
        if f'SECURITY:{row["security_source_role"]}:{row["security_source_record"]}'
        not in observed_issue_refs
    }
    securities_without_issuer = {
        row["issue_permno"] for row in view["issue_interval_attributes"]
        if not row["issuer_candidates"]
    }
    summary["source_record_count_by_role"] = identity_summary["retained_source_record_role_counts"]
    summary["source_record_count_by_type"] = identity_summary["retained_source_record_counts"]
    summary["prior_receipt_raw_row_counts"] = identity_summary["prior_receipt_raw_row_counts"]
    summary["distinct_cik_observation_candidate_count"] = distinct_cik
    summary["cik_observation_edge_count"] = cik_edges
    summary["issue_intervals_without_cik_observation_count"] = (
        identity_summary["issue_intervals_without_cik_observation"])
    summary["distinct_starting_securities_without_cik_observation_count"] = len(securities_without_cik)
    summary["distinct_starting_securities_without_issuer_candidate_count"] = len(securities_without_issuer)
    audit.require(summary["issue_intervals_without_cik_observation_count"] ==
                  sum(f'SECURITY:{row["security_source_role"]}:{row["security_source_record"]}'
                      not in observed_issue_refs for row in view["issue_interval_attributes"]),
                  "CIK_OBSERVATION_COVERAGE_MISMATCH")
    summary["count_units"].update({
        "source_record_count_by_role": "saved ledger source-record locators by source role",
        "source_record_count_by_type": "saved ledger source-record locators by record type",
        "prior_receipt_raw_row_counts": "raw source rows from prior bound receipts; not newly reparsed here",
        "distinct_cik_observation_candidate_count": "distinct nonblank CIK values on saved observation edges",
        "cik_observation_edge_count": "saved observation edges, not effective-dated links",
        "distinct_starting_securities_without_cik_observation_count":
            "distinct starting-set securities with at least one issue interval lacking an observation edge",
        "distinct_starting_securities_without_issuer_candidate_count":
            "distinct starting-set securities with at least one issue interval lacking an issuer candidate",
    })
    summary["field_value_capability"] = {
        role: {field: {
            "denominator": summary[denominator],
            "nonempty_count": summary[denominator] - sum(
                summary[state_key][field].get(state, 0) for state in
                ("VALUE_MISSING", "MISSING_FROM_BOUND_SOURCE", "MISSING_FROM_SAVED_PROJECTION")),
            "blank_count": summary[state_key][field].get("VALUE_MISSING", 0),
            "source_absent_count": summary[state_key][field].get("MISSING_FROM_BOUND_SOURCE", 0),
            "projection_absent_count": summary[state_key][field].get("MISSING_FROM_SAVED_PROJECTION", 0),
            "public_reference_code_count": sum(summary[code_key].get(field, {}).values()),
            "outside_selected_reference_set_count": summary[state_key][field].get(
                "OUTSIDE_SELECTED_PUBLIC_REFERENCE_SET", 0),
            "date_applicability_assessed": False,
            "semantic_eligibility_assessed": False,
        } for field in cfg["fields"][role]}
        for role, denominator, state_key, code_key in (
            ("security", "issue_interval_count", "security_field_lexical_status_counts",
             "security_public_reference_code_counts"),
            ("issuer", "issuer_candidate_edge_count", "issuer_edge_field_lexical_status_counts",
             "issuer_edge_public_reference_code_counts"))}
    summary["field_capability"] = {**security_headers, **issuer_headers}
    summary["projection_limit"] = {
        "security": "RAW_BOUND_SOURCE_REPARSED_FOR_REFERENCED_ISSUES",
        "issuer": "SAVED_BOUND_PROJECTION_RECOMPUTED_NOT_RAW_ISSUER_CSV_REPARSED",
        "cik": "OBSERVATION_EDGES_NOT_VALID_TIME",
        "daily_prices": "NOT_READ_IN_THIS_TOOL",
    }
    return view, summary, {"security": security_headers, "issuer": issuer_headers}


def run(output_dir: Path) -> None:
    cfg, cfg_hash = _check_config()
    identity_registry, identity_summary, issuer_index, source = _load_bound_sources(cfg)
    output_dir = output_dir.resolve()
    audit.check_ignored_output(output_dir)
    view, summary, _ = _build(cfg, identity_registry, identity_summary, issuer_index, source)
    source_hashes = {key: value["sha256"] for key, value in source.items()
                     if key in ("SECURITY_STARTING", "SECURITY_LATER", "ISSUER_EARLY", "ISSUER_LATER")}
    registry = {
        "schema": "m5_attribute_registry_private_v1", "scope": "ENGINEERING_ONLY",
        "config_path": str(CONFIG), "config_sha256": cfg_hash,
        "script_path": str(Path(__file__).resolve()),
        "script_sha256": audit.sha256_file(Path(__file__)),
        "identity_source_dir": str(SOURCE_DIR),
        "identity_ledger_sha256": cfg["source_identity"]["identity_ledger_sha256"],
        "issuer_index_sha256": cfg["source_identity"]["issuer_index_sha256"],
        "raw_source_sha256": source_hashes,
        "preparation_policy_sha256": preparation.POLICY_SHA256,
        "m5_candidate_sha256": preparation.CANDIDATE_SHA256,
        "researcher_accepted_full_m5": False,
        "formal_cohort_count": None, "financial_experiment_run": False,
    }
    summary.update({"config_sha256": cfg_hash,
                    "script_sha256": registry["script_sha256"],
                    "identity_ledger_sha256": registry["identity_ledger_sha256"],
                    "issuer_index_sha256": registry["issuer_index_sha256"],
                    "private_view_sha256": _payload_hash(view)})
    files = {FILES["view"]: view, FILES["registry"]: registry, FILES["summary"]: summary}
    manifest = {"schema": "m5_attribute_manifest_private_v1",
                "files": {name: {"sha256": _payload_hash(value),
                                 "bytes": len(preparation.json_bytes(value))}
                          for name, value in files.items()}}
    files[FILES["manifest"]] = manifest
    audit.write_directory(output_dir, files)


def _check_saved_payloads(output_dir: Path) -> tuple[dict, dict]:
    paths = {role: output_dir / name for role, name in FILES.items()}
    audit.require(all(path.is_file() for path in paths.values())
                  and {p.name for p in output_dir.iterdir()} == set(FILES.values()),
                  "SAVED_FILES_INVALID")
    manifest = _load_json(paths["manifest"])
    audit.require(manifest.get("schema") == "m5_attribute_manifest_private_v1"
                  and set(manifest["files"]) == {FILES[k] for k in ("view", "registry", "summary")},
                  "SAVED_MANIFEST_INVALID")
    for role in ("view", "registry", "summary"):
        item = manifest["files"][FILES[role]]
        audit.require(audit.sha256_file(paths[role]) == item["sha256"]
                      and paths[role].stat().st_size == item["bytes"],
                      "SAVED_FILE_HASH_MISMATCH")
    registry = _load_json(paths["registry"])
    summary = _load_json(paths["summary"])
    return registry, summary


def verify_output(output_dir: Path) -> None:
    cfg, cfg_hash = _check_config()
    paths = {role: output_dir / name for role, name in FILES.items()}
    registry, summary = _check_saved_payloads(output_dir)
    audit.require(registry.get("schema") == "m5_attribute_registry_private_v1"
                  and registry.get("config_sha256") == cfg_hash
                  and registry.get("script_sha256") == audit.sha256_file(Path(__file__))
                  and Path(registry.get("script_path", "")).resolve() == Path(__file__).resolve()
                  and Path(registry.get("identity_source_dir", "")).resolve() == SOURCE_DIR.resolve()
                  and Path(registry.get("config_path", "")).resolve() == CONFIG.resolve()
                  and registry.get("preparation_policy_sha256") == preparation.POLICY_SHA256
                  and registry.get("m5_candidate_sha256") == preparation.CANDIDATE_SHA256
                  and registry.get("researcher_accepted_full_m5") is False
                  and registry.get("formal_cohort_count") is None
                  and registry.get("financial_experiment_run") is False,
                  "SAVED_IDENTITY_OR_STATUS_MISMATCH")
    identity_registry, identity_summary, issuer_index, source = _load_bound_sources(cfg)
    audit.require(registry["identity_ledger_sha256"] == cfg["source_identity"]["identity_ledger_sha256"]
                  and registry["issuer_index_sha256"] == cfg["source_identity"]["issuer_index_sha256"]
                  and registry["raw_source_sha256"] ==
                  {key: value["sha256"] for key, value in source.items()
                   if key in ("SECURITY_STARTING", "SECURITY_LATER", "ISSUER_EARLY", "ISSUER_LATER")},
                  "SAVED_SOURCE_BINDING_MISMATCH")
    rebuilt_view, rebuilt_summary, _ = _build(cfg, identity_registry, identity_summary,
                                              issuer_index, source)
    rebuilt_summary.update({"config_sha256": cfg_hash,
                            "script_sha256": registry["script_sha256"],
                            "identity_ledger_sha256": registry["identity_ledger_sha256"],
                            "issuer_index_sha256": registry["issuer_index_sha256"],
                            "private_view_sha256": _payload_hash(rebuilt_view)})
    audit.require(_payload_hash(rebuilt_view) == audit.sha256_file(paths["view"])
                  and _payload_hash(rebuilt_summary) == audit.sha256_file(paths["summary"])
                  and summary == rebuilt_summary,
                  "SAVED_ATTRIBUTE_RECOMPUTE_MISMATCH")


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise audit.AuditError("CLI_ARGUMENT_INVALID")


def main() -> int:
    try:
        parser = SafeArgumentParser(description=__doc__)
        sub = parser.add_subparsers(dest="action", required=True,
                                    parser_class=SafeArgumentParser)
        sub.add_parser("run").add_argument("--output-dir", type=Path, required=True)
        sub.add_parser("verify-output").add_argument("--output-dir", type=Path, required=True)
        args = parser.parse_args()
        if args.action == "run":
            run(args.output_dir)
            print("M5_EXISTING_ATTRIBUTE_INVENTORY_COMPLETE")
        else:
            verify_output(args.output_dir.resolve(strict=True))
            print("M5_EXISTING_ATTRIBUTE_VERIFY_PASS")
        return 0
    except audit.AuditError as exc:
        print("M5_EXISTING_ATTRIBUTE_FAIL " + str(exc))
        return 2
    except Exception:
        print("M5_EXISTING_ATTRIBUTE_FAIL UNEXPECTED_ERROR")
        return 3


if __name__ == "__main__":
    sys.exit(main())
