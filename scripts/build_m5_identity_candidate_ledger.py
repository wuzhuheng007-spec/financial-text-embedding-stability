"""Build an offline M5 issue/CIK/issuer candidate ledger from saved intakes.

The input indexes already contain the row-level evidence. This module rehashes
their registered source files but does not parse the licensed CSVs or execute a
query. All candidate links remain unaccepted; no formal cohort is selected.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

import audit_m5_wrds_intake as wrds_audit
from audit_m5_wrds_intake import (
    AuditError, check_ignored_output, require, sha256_file, write_directory,
)


ROOT = Path(__file__).resolve().parents[1]
WRDS_FILES = {
    "registry": "m5_wrds_intake_registry.json",
    "index": "m5_wrds_history_candidates_private.json",
    "summary": "m5_wrds_intake_aggregate_summary.json",
}
ISSUER_FILES = {
    "registry": "m5_issuer_history_registry.json",
    "index": "m5_issuer_history_index_private.json",
    "summary": "m5_issuer_history_summary.json",
}
WRDS_HASHES = {
    "registry": "8482df5d3dfccc72e5513179aa1b91f0847321488c0c1c9161f95d8e10cf08ec",
    "index": "de8df60b998d94d76a8452685bb94b27aa68ff864b5850a796b612824d3895bf",
    "summary": "9d33b02743f3d7683462a286e0f25221c957ad00e75788152817091712ef6ebe",
}
ISSUER_HASHES = {
    "registry": "713f7deeb6aa43b5e43b62482e3c36755c49123b5b28c9f63b36c7d822be6194",
    "index": "370318027c222965de32467641c00998e5c3eb985344db41891807c4e6a0a833",
    "summary": "db71f33d7cc662099703100f4f5c38dd68a77234f352b1b7ca5d4febe96fef75",
}
OUTPUT_FILES = {
    "registry": "m5_identity_candidate_registry_private.json",
    "index": "m5_identity_candidate_ledger_private.json",
    "summary": "m5_identity_candidate_summary_safe.json",
    "manifest": "m5_identity_candidate_manifest_private.json",
}
ALLOWED_SOURCE_ROLES = {"STARTING_SNAPSHOT", "LATER_CHANGES"}
M5_CANDIDATE = ROOT / "configs/m5_financial_protocol_round8.json"
M5_CANDIDATE_SHA256 = "f1fa28949353adea6aa179854441b48658415ef577a006fe76bd01c779ddaa38"
LOGICAL_DRAFT_CONTRACT = ROOT / "docs/M5_IDENTITY_LEDGER_CONTRACT_DRAFT.json"
LOGICAL_DRAFT_CONTRACT_SHA256 = "b6fe35194eac5bd74bbe47bbf9f295b5e1f439923dcbc2f796615bc8aa9d38c1"


def check_current_code_and_candidate() -> str:
    require(Path(wrds_audit.__file__).resolve() ==
            (ROOT / "scripts/audit_m5_wrds_intake.py").resolve(),
            "IMPORTED_AUDIT_MODULE_PATH_MISMATCH")
    require(sha256_file(M5_CANDIDATE) == M5_CANDIDATE_SHA256,
            "M5_CANDIDATE_HASH_MISMATCH")
    require(sha256_file(LOGICAL_DRAFT_CONTRACT) == LOGICAL_DRAFT_CONTRACT_SHA256,
            "LOGICAL_DRAFT_CONTRACT_HASH_MISMATCH")
    candidate = json.loads(M5_CANDIDATE.read_text(encoding="utf-8"))
    require(candidate.get("status") == "PROPOSED"
            and candidate.get("researcher_accepted") is False
            and candidate.get("formal_financial_experiment_status") == "NOT_RUN",
            "M5_CANDIDATE_STATUS_MISMATCH")
    return M5_CANDIDATE_SHA256


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _read_pinned(directory: Path, names: dict[str, str], hashes: dict[str, str], code: str) -> dict:
    result = {}
    for role, filename in names.items():
        path = directory / filename
        require(sha256_file(path) == hashes[role], code + "_ARTIFACT_HASH_MISMATCH")
        result[role] = json.loads(path.read_text(encoding="utf-8"))
    return result


def _registered_source_paths(wrds_registry: dict, issuer_registry: dict) -> dict[str, dict]:
    wrds = wrds_registry
    issuer = issuer_registry
    result = {
        "SECURITY_STARTING": {"path": wrds["local_snapshot"]["path"], "sha256": wrds["local_snapshot"]["sha256"]},
        "SECURITY_LATER": {"path": wrds["local_changes"]["path"], "sha256": wrds["local_changes"]["sha256"]},
        "CIK_CUSIP": {"path": wrds["local_links"]["path"], "sha256": wrds["local_links"]["sha256"]},
        "SECURITY_QUERY_SQL": {"path": wrds["query_sql_path"], "sha256": wrds["query_sql_sha256"]},
        "SECURITY_V2_RECEIPT": {"path": wrds["saved_v2_receipt"]["path"],
                                "sha256": wrds["saved_v2_receipt"]["sha256"]},
    }
    for role in ("EARLY", "LATER"):
        source = issuer["issuer_sources"][role]
        result[f"ISSUER_{role}"] = {"path": source["csv_path"], "sha256": source["sha256"]}
        result[f"ISSUER_{role}_SQL"] = {"path": source["sql_path"], "sha256": source["sql_sha256"]}
    return result


def load_saved(
    wrds_dir: Path,
    issuer_dir: Path,
    *,
    wrds_hashes: dict[str, str] = WRDS_HASHES,
    issuer_hashes: dict[str, str] = ISSUER_HASHES,
) -> tuple[dict, dict, dict]:
    """Check saved indexes, code and registered raw-file hashes; parse no CSV."""
    check_current_code_and_candidate()
    wrds_dir = wrds_dir.resolve(strict=True)
    issuer_dir = issuer_dir.resolve(strict=True)
    require(wrds_dir != issuer_dir, "SOURCE_DIRECTORIES_NOT_DISTINCT")
    wrds = _read_pinned(wrds_dir, WRDS_FILES, wrds_hashes, "WRDS")
    issuer = _read_pinned(issuer_dir, ISSUER_FILES, issuer_hashes, "ISSUER")
    wr, wi, ws = wrds["registry"], wrds["index"], wrds["summary"]
    ir, ii, iss = issuer["registry"], issuer["index"], issuer["summary"]
    require(wi.get("schema") == "m5_wrds_history_candidate_index_v1"
            and ii.get("schema") == "m5_issuer_history_candidates_private_v1",
            "PRIOR_INDEX_SCHEMA_INVALID")
    require(wi.get("accepted") is False and ii.get("accepted") is False
            and wi.get("historical_link_validity") == "NOT_ESTABLISHED"
            and ir.get("formal_m5_cohort_selected") is False
            and iss.get("formal_m5_cohort_selected") is False,
            "PRIOR_ACCEPTANCE_STATUS_INVALID")
    require(ir["prior_artifacts"]["sha256"] == wrds_hashes
            and iss["prior_artifact_sha256"] == wrds_hashes,
            "ISSUER_PRIOR_ARTIFACT_BINDING_MISMATCH")
    require(wi["source_bindings"]["snapshot_sha256"] == wr["local_snapshot"]["sha256"]
            and wi["source_bindings"]["changes_sha256"] == wr["local_changes"]["sha256"]
            and wi["source_bindings"]["links_sha256"] == wr["local_links"]["sha256"]
            and wi["source_bindings"]["v2_receipt_sha256"] == wr["saved_v2_receipt"]["sha256"]
            and wi["source_bindings"]["intake_script_sha256"] == wr["intake_script_sha256"],
            "WRDS_INDEX_SOURCE_BINDING_MISMATCH")
    require(ws["snapshot_sha256"] == wr["local_snapshot"]["sha256"]
            and ws["changes_sha256"] == wr["local_changes"]["sha256"]
            and ws["links_sha256"] == wr["local_links"]["sha256"]
            and ws["v2_receipt_sha256"] == wr["saved_v2_receipt"]["sha256"]
            and ws["intake_script_sha256"] == wr["intake_script_sha256"],
            "WRDS_SUMMARY_SOURCE_BINDING_MISMATCH")
    require(iss["issuer_csv_sha256"] == {role: ir["issuer_sources"][role]["sha256"]
                                            for role in ("EARLY", "LATER")}
            and iss["query_sql_sha256"] == {role: ir["issuer_sources"][role]["sql_sha256"]
                                            for role in ("EARLY", "LATER")}
            and iss["issuer_audit_script_sha256"] == ir["issuer_audit_script_sha256"],
            "ISSUER_SUMMARY_SOURCE_BINDING_MISMATCH")
    require(ir["prior_source_sha256"] == {
                "STARTING_SNAPSHOT": wr["local_snapshot"]["sha256"],
                "LATER_CHANGES": wr["local_changes"]["sha256"]}
            and ir["prior_links_sha256"] == wr["local_links"]["sha256"]
            and ir["prior_v2_receipt_sha256"] == wr["saved_v2_receipt"]["sha256"]
            and ir["prior_intake_script_sha256"] == wr["intake_script_sha256"],
            "ISSUER_PRIOR_SOURCE_BINDING_MISMATCH")
    wrds_code = ROOT / "scripts/audit_m5_wrds_intake.py"
    issuer_code = ROOT / "scripts/audit_m5_issuer_history.py"
    require(sha256_file(wrds_code) == wr["intake_script_sha256"]
            and sha256_file(issuer_code) == ir["issuer_audit_script_sha256"],
            "PRIOR_CODE_HASH_MISMATCH")
    raw_sources = _registered_source_paths(wr, ir)
    for source in raw_sources.values():
        require(sha256_file(Path(source["path"])) == source["sha256"],
                "REGISTERED_SOURCE_HASH_MISMATCH")
    for role, filename in WRDS_FILES.items():
        require(sha256_file(wrds_dir / filename) == wrds_hashes[role],
                "WRDS_ARTIFACT_CHANGED_DURING_READ")
    for role, filename in ISSUER_FILES.items():
        require(sha256_file(issuer_dir / filename) == issuer_hashes[role],
                "ISSUER_ARTIFACT_CHANGED_DURING_READ")
    source_manifest = {
        "schema": "m5_identity_source_manifest_private_v1",
        "wrds_artifact_directory": str(wrds_dir),
        "issuer_artifact_directory": str(issuer_dir),
        "wrds_artifact_sha256": dict(wrds_hashes),
        "issuer_artifact_sha256": dict(issuer_hashes),
        "registered_source_files": raw_sources,
        "prior_code_sha256": {
            "wrds_intake": wr["intake_script_sha256"],
            "issuer_intake": ir["issuer_audit_script_sha256"],
        },
        "m5_candidate_sha256": M5_CANDIDATE_SHA256,
        "logical_draft_contract_sha256": LOGICAL_DRAFT_CONTRACT_SHA256,
        "logical_draft_contract_role": "CONCEPTUAL_ENGINEERING_DRAFT_NOT_EXECUTABLE_SCHEMA",
        "prior_receipt_raw_row_counts": {
            "SECURITY_STARTING": wr["local_snapshot"].get("row_count"),
            "SECURITY_LATER": wr["local_changes"].get("row_count"),
            "CIK_CUSIP": wr["local_links"].get("row_count"),
            "ISSUER_EARLY": ir["issuer_sources"]["EARLY"].get("row_count"),
            "ISSUER_LATER": ir["issuer_sources"]["LATER"].get("row_count"),
        },
        "raw_row_count_provenance": "PRIOR_SAVED_RECEIPTS_NOT_REPARSED_IN_THIS_RUN",
        "prior_recorded_at_utc": {
            "wrds_intake": wr.get("recorded_at_utc"),
            "issuer_intake": ir.get("recorded_at_utc"),
        },
        "prior_downloaded_at_utc": {
            "wrds_intake": wr.get("downloaded_at_utc"),
            "issuer_intake": ir.get("downloaded_at_utc"),
        },
        "provider_release_vintage": {
            "wrds_intake": wr.get("provider_release_vintage"),
            "issuer_intake": ir.get("provider_release_vintage"),
        },
        "license_or_export_terms_verified": {
            "wrds_intake": wr.get("license_or_export_terms_verified"),
            "issuer_intake": ir.get("license_or_export_terms_verified"),
        },
        "csv_rows_reparsed": False,
        "raw_source_files_hashed_only": True,
    }
    return wi, ii, source_manifest


def _iso_or_none(value: object) -> bool:
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _time_evidence(start: object, end: object, semantics: str) -> dict:
    require(_iso_or_none(start) and _iso_or_none(end), "CANDIDATE_DATE_FORMAT_INVALID")
    require(start is None or end is None or start <= end,
            "CANDIDATE_DATE_RANGE_REVERSED")
    return {
        "date_semantics": semantics,
        "start": start,
        "end": end,
        "start_boundary": "KNOWN" if start is not None else "UNKNOWN",
        "end_boundary": "KNOWN" if end is not None else "UNKNOWN",
        "end_inclusivity": "UNKNOWN",
        "contains_proposal_asof_or_null": None,
        "historical_link_validity_established": False,
    }


def _observation_evidence(first: object, last: object) -> dict:
    require(_iso_or_none(first) and _iso_or_none(last),
            "CIK_OBSERVATION_DATE_FORMAT_INVALID")
    if first is not None and last is not None and first > last:
        return {
            **_time_evidence(None, None, "OBSERVATION_TIME"),
            "source_first_filing_observation": first,
            "source_last_filing_observation": last,
            "observation_date_order_status": "REVERSED_SOURCE_DATES_REVIEW_REQUIRED",
        }
    return {**_time_evidence(first, last, "OBSERVATION_TIME"),
            "source_first_filing_observation": first,
            "source_last_filing_observation": last,
            "observation_date_order_status": "ORDERED_OR_PARTIAL"}


def build_ledger(wrds_index: dict, issuer_index: dict, source_manifest: dict) -> tuple[dict, dict]:
    """Preserve two parallel candidate edge types on each issue interval."""
    require(wrds_index.get("accepted") is False and issuer_index.get("accepted") is False,
            "PRIOR_CANDIDATES_UNEXPECTEDLY_ACCEPTED")
    issuer_intervals = {}
    for row in issuer_index["security_interval_issuer_candidates"]:
        key = (row["source_role"], row["source_record"])
        require(row["source_role"] in ALLOWED_SOURCE_ROLES and key not in issuer_intervals
                and row.get("accepted") is False
                and row.get("prior_cik_candidates_accepted") is False,
                "ISSUER_SECURITY_INTERVAL_INVALID")
        issuer_intervals[key] = row
    issuer_rows = {}
    for row in issuer_index["related_issuer_records"]:
        key = (row["source_role"], row["source_record"])
        require(row["source_role"] in {"EARLY", "LATER"} and key not in issuer_rows,
                "ISSUER_SOURCE_RECORD_DUPLICATE")
        issuer_rows[key] = row

    sources = source_manifest["registered_source_files"]
    source_records: dict[str, dict] = {}
    issue_evidence = []
    identity_candidates = []
    exceptions = []
    seen_issue_keys = set()
    starting_count = 0
    seen_starting_permnos = set()
    cik_count = 0
    issuer_count = 0
    no_cik_count = 0
    no_issuer_count = 0
    multiple_cik_count = 0
    multiple_issuer_count = 0
    later_only_evidence = []

    def source_record(ref: str, record_type: str, role: str, number: int,
                      source_sha256: str, row_sha256: str | None,
                      identifier_fields: tuple[str, ...]) -> None:
        count_role = {"STARTING_SNAPSHOT": "SECURITY_STARTING",
                      "LATER_CHANGES": "SECURITY_LATER",
                      "CIK_CUSIP": "CIK_CUSIP",
                      "EARLY": "ISSUER_EARLY", "LATER": "ISSUER_LATER"}[role]
        prior_count = source_manifest.get("prior_receipt_raw_row_counts", {}).get(count_role)
        require(isinstance(number, int) and number > 0
                and (prior_count is None or
                     (isinstance(prior_count, int) and number <= prior_count)),
                "SOURCE_RECORD_OUTSIDE_PRIOR_ROW_COUNT")
        raw_ref = {"source_role": role, "source_record_number": number,
                   "source_file_sha256": source_sha256}
        value = {"source_record_id": ref, "record_type": record_type,
                 "source_role": role, "source_record_number": number,
                 "source_file_sha256": source_sha256,
                 "normalized_row_sha256": row_sha256,
                 "raw_record_ref": raw_ref,
                 "raw_identifier_refs": [
                     {"raw_field_name": field, "raw_record_ref": raw_ref}
                     for field in identifier_fields]}
        old = source_records.setdefault(ref, value)
        require(old == value, "SOURCE_RECORD_REFERENCE_CONFLICT")

    for security in wrds_index["starting_securities"]:
        require(security.get("accepted") is False, "SECURITY_UNEXPECTEDLY_ACCEPTED")
        require(security["permno"] not in seen_starting_permnos,
                "STARTING_SECURITY_DUPLICATED")
        seen_starting_permnos.add(security["permno"])
        starting_count += 1
        for interval in security["history"]:
            role, number = interval["source"], interval["source_record"]
            key = (role, number)
            require(role in ALLOWED_SOURCE_ROLES and isinstance(number, int)
                    and number > 0 and key not in seen_issue_keys,
                    "SECURITY_INTERVAL_REFERENCE_INVALID")
            seen_issue_keys.add(key)
            issuer = issuer_intervals.get(key)
            require(issuer is not None and issuer["permno"] == security["permno"]
                    and issuer["start"] == interval["start"]
                    and issuer["end"] == interval["end"]
                    and issuer["historical_cusip"] == interval["historical_cusip"]
                    and issuer["normalized_row_sha256"] == interval["normalized_row_sha256"],
                    "SECURITY_ISSUER_INTERVAL_BINDING_MISMATCH")
            require(interval.get("accepted") is False, "INTERVAL_UNEXPECTEDLY_ACCEPTED")
            issue_ref = f"SECURITY:{role}:{number}"
            source_key = "SECURITY_STARTING" if role == "STARTING_SNAPSHOT" else "SECURITY_LATER"
            require(issuer["source_sha256"] == sources[source_key]["sha256"],
                    "SECURITY_ROW_SOURCE_BINDING_MISMATCH")
            source_record(issue_ref, "SECURITY", role, number,
                          sources[source_key]["sha256"], interval["normalized_row_sha256"],
                          ("permno", "permco", "cusip"))
            observed = _time_evidence(interval["start"], interval["end"], "VALID_TIME")
            issue_evidence.append({
                "issue_evidence_id": issue_ref,
                "security_record_id": issue_ref,
                "raw_record_ref": source_records[issue_ref]["raw_record_ref"],
                "raw_identifier_refs": source_records[issue_ref]["raw_identifier_refs"],
                "permno": security["permno"],
                "permco": issuer["permco"],
                "historical_cusip": interval["historical_cusip"],
                "security_interval": observed,
                "starting_snapshot_membership": role == "STARTING_SNAPSHOT",
                "snapshot_date_semantics": "ASOF_SNAPSHOT" if role == "STARTING_SNAPSHOT" else None,
                "issuer_closed_date_comparison_only": issuer["issuer_date_coverage_under_closed_comparison"],
                "historical_permco_change_candidate": issuer["historical_permco_change_candidate"],
                "primary_security": None,
                "mic": None,
                "eligible": None,
                "accepted": False,
            })
            cik_edges = interval["candidates"]
            issuer_edges = issuer["issuer_matches"]
            if not cik_edges:
                no_cik_count += 1
                exceptions.append({"origin": "CURRENT_COMPOSITION", "issue_evidence_id": issue_ref,
                                   "affected_source_record_refs": [issue_ref],
                                   "codes": ["NO_CIK_OBSERVATION_CANDIDATE"]})
            if not issuer_edges:
                no_issuer_count += 1
                exceptions.append({"origin": "CURRENT_COMPOSITION", "issue_evidence_id": issue_ref,
                                   "affected_source_record_refs": [issue_ref],
                                   "codes": ["NO_ISSUER_INTERVAL_CANDIDATE"]})
            if len({edge["cik"] for edge in cik_edges if edge["cik"] is not None}) > 1:
                multiple_cik_count += 1
            if len(issuer_edges) > 1:
                multiple_issuer_count += 1
            for edge in cik_edges:
                require(edge.get("accepted") is False
                        and edge.get("historical_link_validity") == "NOT_ESTABLISHED"
                        and edge["historical_cusip"] == interval["historical_cusip"],
                        "CIK_OBSERVATION_EDGE_INVALID")
                link_number = edge["link_source_record"]
                require(isinstance(link_number, int) and link_number > 0,
                        "CIK_SOURCE_RECORD_INVALID")
                link_ref = f"CIK_CUSIP:{link_number}"
                source_record(link_ref, "CIK_CUSIP", "CIK_CUSIP", link_number,
                              sources["CIK_CUSIP"]["sha256"], None,
                              ("cik", "cusip"))
                identity_candidates.append({
                    "identity_candidate_id": f"CIK_EDGE:{len(identity_candidates) + 1}",
                    "candidate_kind": "CIK_CUSIP_OBSERVATION",
                    "issue_evidence_id": issue_ref,
                    "source_record_id": link_ref,
                    "raw_record_ref": source_records[link_ref]["raw_record_ref"],
                    "raw_identifier_refs": source_records[link_ref]["raw_identifier_refs"],
                    "cik": edge["cik"],
                    "validation_code": edge["validation_code"],
                    "validation_class": edge["validation_class"],
                    "observed_filing_dates": _observation_evidence(
                        edge["first_filing_observation"], edge["last_filing_observation"]),
                    "historical_valid_from": None,
                    "historical_valid_to": None,
                    "issues": list(edge["issues"]),
                    "accepted": False,
                })
                cik_count += 1
            for edge in issuer_edges:
                require(edge.get("accepted") is False, "ISSUER_EDGE_UNEXPECTEDLY_ACCEPTED")
                issuer_key = (edge["issuer_source_role"], edge["issuer_source_record"])
                issuer_row = issuer_rows.get(issuer_key)
                require(issuer_row is not None
                        and issuer_row["permco"] == issuer["permco"]
                        and issuer_row["normalized_row_sha256"] == edge["issuer_row_sha256"]
                        and issuer_row["source_sha256"] == edge["issuer_source_sha256"]
                        == sources[f"ISSUER_{issuer_key[0]}"]["sha256"],
                        "ISSUER_EDGE_SOURCE_BINDING_MISMATCH")
                issuer_ref = f"ISSUER:{issuer_key[0]}:{issuer_key[1]}"
                source_record(issuer_ref, "ISSUER", issuer_key[0], issuer_key[1],
                              edge["issuer_source_sha256"], edge["issuer_row_sha256"],
                              ("permco", "issinfostartdt", "issinfoenddt"))
                identity_candidates.append({
                    "identity_candidate_id": f"ISSUER_EDGE:{len(identity_candidates) + 1}",
                    "candidate_kind": "SECURITY_ISSUER_CLOSED_DATE_INTERSECTION",
                    "issue_evidence_id": issue_ref,
                    "source_record_id": issuer_ref,
                    "raw_record_ref": source_records[issuer_ref]["raw_record_ref"],
                    "raw_identifier_refs": source_records[issuer_ref]["raw_identifier_refs"],
                    "permco": issuer["permco"],
                    "issuer_source_interval": _time_evidence(
                        issuer_row["start"], issuer_row["end"], "VALID_TIME"),
                    "closed_date_diagnostic_intersection": {
                        **_time_evidence(edge["intersection_start"],
                                         edge["intersection_end"], "UNKNOWN"),
                        "comparison_method": "CLOSED_DATE_DIAGNOSTIC_ONLY",
                    },
                    "endpoint_equality_review": edge["endpoint_equality_review"],
                    "provider_end_inclusivity_verified": False,
                    "accepted": False,
                })
                issuer_count += 1

    require(set(issuer_intervals) == seen_issue_keys,
            "ISSUER_SECURITY_INTERVAL_COVERAGE_MISMATCH")
    seen_later_only_refs = set()
    for interval in wrds_index["later_only_security_intervals"]:
        number = interval["source_record"]
        require(isinstance(number, int) and number > 0
                and ("LATER_CHANGES", number) not in seen_issue_keys
                and number not in seen_later_only_refs
                and interval.get("not_in_starting_snapshot") is True
                and interval.get("admitted") is False,
                "LATER_ONLY_SOURCE_REFERENCE_INVALID")
        seen_later_only_refs.add(number)
        ref = f"SECURITY:LATER_CHANGES:{number}"
        source_record(ref, "SECURITY_LATER_ONLY", "LATER_CHANGES", number,
                      sources["SECURITY_LATER"]["sha256"],
                      interval["normalized_row_sha256"],
                      ("permno", "permco", "cusip"))
        later_only_evidence.append({
            "source_record_id": ref,
            "raw_record_ref": source_records[ref]["raw_record_ref"],
            "raw_identifier_refs": source_records[ref]["raw_identifier_refs"],
            "permno": interval["permno"],
            "historical_cusip": interval["historical_cusip"],
            "source_interval": _time_evidence(interval["start"], interval["end"],
                                               "VALID_TIME"),
            "issues": list(interval["issues"]),
            "starting_snapshot_membership": False,
            "accepted": False,
            "formal_cohort_member": False,
        })

    def prior_refs(prior_exception: dict) -> list[str]:
        refs = []
        role = prior_exception.get("source_role", prior_exception.get("source"))
        number = prior_exception.get("source_record")
        if role == "LATER_ONLY":
            role = "LATER_CHANGES"
        if role in ALLOWED_SOURCE_ROLES and isinstance(number, int) and number > 0:
            refs.append(f"SECURITY:{role}:{number}")
        if role in {"EARLY", "LATER"} and isinstance(number, int) and number > 0:
            refs.append(f"ISSUER:{role}:{number}")
        link_number = prior_exception.get("link_source_record")
        if isinstance(link_number, int) and link_number > 0:
            refs.append(f"CIK_CUSIP:{link_number}")
        for item in prior_exception.get("source_records", []):
            if isinstance(item, list) and len(item) == 2 and item[0] in {"EARLY", "LATER"} \
                    and isinstance(item[1], int) and item[1] > 0:
                refs.append(f"ISSUER:{item[0]}:{item[1]}")
        return sorted(set(refs))

    issue_ids = {item["issue_evidence_id"] for item in issue_evidence}

    def ensure_exception_ref(ref: str, prior_exception: dict) -> None:
        if ref in source_records:
            return
        parts = ref.split(":")
        require(len(parts) in {2, 3} and parts[-1].isdigit(),
                "EXCEPTION_SOURCE_REFERENCE_INVALID")
        kind, number = parts[0], int(parts[-1])
        role = parts[1] if len(parts) == 3 else None
        if kind == "SECURITY":
            source_key = "SECURITY_STARTING" if role == "STARTING_SNAPSHOT" else "SECURITY_LATER"
            source_record(ref, "SECURITY_EXCEPTION_SOURCE", role, number,
                          sources[source_key]["sha256"], None,
                          ("permno", "permco", "cusip"))
        elif kind == "ISSUER":
            source_record(ref, "ISSUER_EXCEPTION_SOURCE", role, number,
                          sources[f"ISSUER_{role}"]["sha256"],
                          prior_exception.get("normalized_row_sha256"),
                          ("permco", "issinfostartdt", "issinfoenddt"))
        else:
            require(kind == "CIK_CUSIP" and role is None,
                    "EXCEPTION_SOURCE_REFERENCE_INVALID")
            source_record(ref, "CIK_CUSIP_EXCEPTION_SOURCE", "CIK_CUSIP", number,
                          sources["CIK_CUSIP"]["sha256"], None, ("cik", "cusip"))

    for origin, prior in (("WRDS_INTAKE", wrds_index), ("ISSUER_INTAKE", issuer_index)):
        for prior_exception in prior["exceptions"]:
            entry = {"origin": origin, "prior_exception": prior_exception,
                     "codes": list(prior_exception["codes"]),
                     "affected_source_record_refs": prior_refs(prior_exception)}
            for ref in entry["affected_source_record_refs"]:
                ensure_exception_ref(ref, prior_exception)
                require(ref in source_records, "EXCEPTION_SOURCE_REFERENCE_UNRESOLVED")
                if ref in issue_ids:
                    entry["issue_evidence_id"] = ref
            exceptions.append(entry)
    exception_codes = Counter(code for item in exceptions for code in item["codes"])
    affected_refs = {ref for item in exceptions for ref in item["affected_source_record_refs"]}
    retained_source_counts = dict(sorted(Counter(
        item["record_type"] for item in source_records.values()).items()))
    retained_source_role_counts = dict(sorted(Counter(
        item["source_role"] for item in source_records.values()).items()))
    ledger = {
        "schema": "m5_identity_candidate_ledger_private_v1",
        "scope": "ENGINEERING_ONLY_CANDIDATE_IDENTITY",
        "source_manifest": source_manifest,
        "source_record": list(source_records.values()),
        "issue_evidence": issue_evidence,
        "later_only_security_evidence": later_only_evidence,
        "identity_candidate": identity_candidates,
        "identity_exception": exceptions,
        "relationship_semantics": "Two parallel candidate edge kinds share each security interval; no CIK-by-issuer Cartesian validity assertion",
        "historical_cik_validity": "NOT_ESTABLISHED",
        "provider_end_inclusivity": "UNKNOWN",
        "accepted": False,
        "formal_m5_cohort_selected": False,
        "formal_m5_cohort_count": None,
        "admitted_financial_observations": None,
        "financial_experiment_run": False,
    }
    summary = {
        "schema": "m5_identity_candidate_summary_safe_v1",
        "scope": "ENGINEERING_ONLY_CANDIDATE_IDENTITY",
        "status": "CANDIDATE_IDENTITY_LEDGER_BUILT_NOT_ADMITTED",
        "starting_security_count": starting_count,
        "issue_interval_count": len(issue_evidence),
        "source_record_locator_count": len(source_records),
        "retained_source_record_counts": retained_source_counts,
        "retained_source_record_role_counts": retained_source_role_counts,
        "prior_receipt_raw_row_counts": source_manifest.get("prior_receipt_raw_row_counts"),
        "raw_row_count_provenance": source_manifest.get("raw_row_count_provenance"),
        "unparsed_or_quarantined_raw_row_count": None,
        "unparsed_or_quarantined_count_reason": "NOT_REASSESSED_FROM_RAW_CSV_IN_THIS_RUN",
        "later_only_interval_count": len(later_only_evidence),
        "later_only_distinct_security_count": len({row["permno"] for row in later_only_evidence}),
        "identity_candidate_edge_count": len(identity_candidates),
        "cik_observation_edge_count": cik_count,
        "issuer_intersection_edge_count": issuer_count,
        "issue_intervals_without_cik_observation": no_cik_count,
        "issue_intervals_without_issuer_intersection": no_issuer_count,
        "issue_intervals_with_multiple_distinct_cik_candidates": multiple_cik_count,
        "issue_intervals_with_multiple_issuer_intersections": multiple_issuer_count,
        "exception_record_count": len(exceptions),
        "exception_code_counts": dict(sorted(exception_codes.items())),
        "distinct_affected_source_record_count": len(affected_refs),
        "exception_records_without_source_record_subject": sum(
            not item["affected_source_record_refs"] for item in exceptions),
        "null_primary_security_count": len(issue_evidence),
        "null_mic_count": len(issue_evidence),
        "null_eligibility_count": len(issue_evidence),
        "accepted_candidate_count": 0,
        "formal_m5_cohort_count": None,
        "admitted_financial_observations": None,
        "raw_csv_rows_reparsed": False,
        "financial_experiment_run": False,
    }
    return ledger, summary


def _manifest(files: dict[str, dict]) -> dict:
    return {"schema": "m5_identity_candidate_output_manifest_private_v1",
            "files": {name: {"sha256": hashlib.sha256(json_bytes(value)).hexdigest(),
                             "bytes": len(json_bytes(value))}
                      for name, value in files.items()}}


def run(wrds_dir: Path, issuer_dir: Path, output_dir: Path,
        *, wrds_hashes: dict[str, str] = WRDS_HASHES,
        issuer_hashes: dict[str, str] = ISSUER_HASHES) -> dict:
    output_dir = output_dir.resolve()
    check_ignored_output(output_dir)
    wrds, issuer, source_manifest = load_saved(
        wrds_dir, issuer_dir, wrds_hashes=wrds_hashes, issuer_hashes=issuer_hashes)
    ledger, summary = build_ledger(wrds, issuer, source_manifest)
    require(summary["starting_security_count"] == len(wrds["starting_securities"])
            and summary["issue_interval_count"] == wrds["summary"]["history_interval_count"]
            and summary["cik_observation_edge_count"] == wrds["summary"]["interval_candidate_rows"]
            and summary["issuer_intersection_edge_count"] == issuer["summary"]["issuer_candidate_rows"],
            "PRIOR_AGGREGATE_COUNT_MISMATCH")
    now = datetime.now(timezone.utc).isoformat()
    code_sha256 = sha256_file(Path(__file__).resolve())
    registry = {
        "schema": "m5_identity_candidate_registry_private_v1",
        "scope": "ENGINEERING_ONLY_CANDIDATE_IDENTITY",
        "created_at_utc": now,
        "script_path": str(Path(__file__).resolve()),
        "script_sha256": code_sha256,
        "python_executable": sys.executable,
        "python_version": sys.version,
        "source_manifest": source_manifest,
        "m5_candidate_sha256": M5_CANDIDATE_SHA256,
        "logical_draft_contract_sha256": LOGICAL_DRAFT_CONTRACT_SHA256,
        "formal_m5_cohort_count": None,
        "researcher_accepted": False,
        "financial_experiment_run": False,
    }
    summary["script_sha256"] = code_sha256
    summary["m5_candidate_sha256"] = M5_CANDIDATE_SHA256
    summary["logical_draft_contract_sha256"] = LOGICAL_DRAFT_CONTRACT_SHA256
    summary["source_artifact_sha256"] = {
        "wrds": source_manifest["wrds_artifact_sha256"],
        "issuer": source_manifest["issuer_artifact_sha256"],
    }
    summary["ledger_sha256"] = hashlib.sha256(json_bytes(ledger)).hexdigest()
    summary["registry_sha256"] = hashlib.sha256(json_bytes(registry)).hexdigest()
    files = {OUTPUT_FILES["registry"]: registry, OUTPUT_FILES["index"]: ledger,
             OUTPUT_FILES["summary"]: summary}
    files[OUTPUT_FILES["manifest"]] = _manifest(files)
    write_directory(output_dir.resolve(), files)
    verify_output(output_dir.resolve(), wrds_hashes=wrds_hashes, issuer_hashes=issuer_hashes)
    return summary


def verify_output(output_dir: Path, *, wrds_hashes: dict[str, str] = WRDS_HASHES,
                  issuer_hashes: dict[str, str] = ISSUER_HASHES) -> dict:
    output_dir = output_dir.resolve(strict=True)
    require(output_dir.is_relative_to(ROOT / "artifacts"), "OUTPUT_NOT_UNDER_LOCAL_ARTIFACTS")
    paths = {role: output_dir / filename for role, filename in OUTPUT_FILES.items()}
    require({path.name for path in output_dir.iterdir()} == set(OUTPUT_FILES.values()),
            "OUTPUT_FILE_SET_MISMATCH")
    manifest_before = sha256_file(paths["manifest"])
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    require(manifest["schema"] == "m5_identity_candidate_output_manifest_private_v1",
            "OUTPUT_MANIFEST_SCHEMA_INVALID")
    require(set(manifest["files"]) == set(OUTPUT_FILES.values()) - {OUTPUT_FILES["manifest"]},
            "OUTPUT_MANIFEST_FILE_SET_MISMATCH")
    for role in ("registry", "index", "summary"):
        item = manifest["files"][OUTPUT_FILES[role]]
        require(sha256_file(paths[role]) == item["sha256"]
                and paths[role].stat().st_size == item["bytes"],
                "SAVED_OUTPUT_HASH_MISMATCH")
    registry = json.loads(paths["registry"].read_text(encoding="utf-8"))
    ledger = json.loads(paths["index"].read_text(encoding="utf-8"))
    summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
    require(Path(registry["script_path"]).resolve() == Path(__file__).resolve()
            and registry["script_sha256"] == sha256_file(Path(__file__).resolve())
            == summary["script_sha256"], "SAVED_CODE_IDENTITY_MISMATCH")
    require(registry["m5_candidate_sha256"] == summary["m5_candidate_sha256"]
            == check_current_code_and_candidate(), "SAVED_M5_CANDIDATE_IDENTITY_MISMATCH")
    require(registry["logical_draft_contract_sha256"]
            == summary["logical_draft_contract_sha256"]
            == LOGICAL_DRAFT_CONTRACT_SHA256,
            "SAVED_LOGICAL_DRAFT_CONTRACT_IDENTITY_MISMATCH")
    require(summary["ledger_sha256"] == sha256_file(paths["index"])
            and summary["registry_sha256"] == sha256_file(paths["registry"]),
            "SAVED_SUMMARY_BINDING_MISMATCH")
    source_manifest = registry["source_manifest"]
    require(source_manifest == ledger["source_manifest"], "SAVED_SOURCE_MANIFEST_MISMATCH")
    require(summary["source_artifact_sha256"] == {
                "wrds": source_manifest["wrds_artifact_sha256"],
                "issuer": source_manifest["issuer_artifact_sha256"]}
            and registry["researcher_accepted"] is False
            and registry["formal_m5_cohort_count"] is None
            and registry["financial_experiment_run"] is False,
            "SAVED_STATUS_OR_SOURCE_BINDING_MISMATCH")
    wrds, issuer, current_source = load_saved(
        Path(source_manifest["wrds_artifact_directory"]),
        Path(source_manifest["issuer_artifact_directory"]),
        wrds_hashes=wrds_hashes, issuer_hashes=issuer_hashes)
    require(current_source == source_manifest, "SAVED_SOURCE_IDENTITY_MISMATCH")
    recomputed, counts = build_ledger(wrds, issuer, current_source)
    require(recomputed == ledger, "SAVED_LEDGER_RECOMPUTATION_MISMATCH")
    for key, value in counts.items():
        require(summary.get(key) == value, "SAVED_AGGREGATE_RECOMPUTATION_MISMATCH")
    require(summary["accepted_candidate_count"] == 0
            and summary["formal_m5_cohort_count"] is None
            and summary["admitted_financial_observations"] is None
            and all(item["accepted"] is False for item in ledger["identity_candidate"])
            and all(item["primary_security"] is None and item["mic"] is None
                    and item["eligible"] is None and item["accepted"] is False
                    for item in ledger["issue_evidence"]),
            "SAVED_ADMISSION_STATUS_INVALID")
    for role in ("registry", "index", "summary", "manifest"):
        require(sha256_file(paths[role]) ==
                (manifest["files"][OUTPUT_FILES[role]]["sha256"]
                 if role != "manifest" else manifest_before),
                "SAVED_OUTPUT_CHANGED_DURING_READ")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--wrds-dir", required=True, type=Path)
    run_parser.add_argument("--issuer-dir", required=True, type=Path)
    run_parser.add_argument("--output-dir", required=True, type=Path)
    verify_parser = sub.add_parser("verify-output")
    verify_parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "run":
            run(args.wrds_dir, args.issuer_dir, args.output_dir)
            print("M5_IDENTITY_CANDIDATE_LEDGER_SAVED_NOT_ADMITTED")
        else:
            verify_output(args.output_dir)
            print("M5_IDENTITY_CANDIDATE_LEDGER_SAVED_OUTPUT_VERIFIED")
    except (AuditError, OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        code = str(exc) if isinstance(exc, AuditError) else "LOCAL_INPUT_OR_OUTPUT_INVALID"
        print(f"M5_IDENTITY_LEDGER_REVIEW_REQUIRED:{code}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
