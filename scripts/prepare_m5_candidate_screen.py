"""Screen saved M5 identity candidates without admitting a financial cohort.

This tool reads the already bound private identity ledger. It never joins a
CIK observation to an issuer intersection as a certified three-way identity.
Only aggregate counts are printed; row-level results stay under ignored
``artifacts/``. The original ledger and raw files are never modified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import audit_m5_wrds_intake as audit_module
from audit_m5_wrds_intake import (
    AuditError, check_ignored_output, require, sha256_file, write_directory,
)


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "configs/m5_preparation_rules_accepted_20261113.json"
POLICY_SHA256 = "14f22ae821d61d44848832fc5e73b5b27360f5947cafb4189ceb27603a38970e"
CANDIDATE = ROOT / "configs/m5_financial_protocol_round8.json"
CANDIDATE_SHA256 = "f1fa28949353adea6aa179854441b48658415ef577a006fe76bd01c779ddaa38"
DICTIONARY = ROOT / "configs/m5_cik_cusip_dictionary_evidence_20261113.json"
DICTIONARY_SHA256 = "ace590c91af118a2e836feae76c8f61d58b14e5134b78d4cb8843e384da8b537"
DECISION = ROOT / "artifacts/m5_three_preparation_rules_accepted_20261113_v1/preparation_decision_receipt_private.json"
DECISION_SHA256 = "cf701cb379b6849ceb5f89def2c7e38f3e9b2d16f6a1ac7971b032201bb8c56c"
OLD_BUILDER = ROOT / "scripts/build_m5_identity_candidate_ledger.py"
AUDIT_HELPER = ROOT / "scripts/audit_m5_wrds_intake.py"
AUDIT_HELPER_SHA256 = "97ded6befe05d0759d1c289618a31d1cf4eab6f965d34fb01768fef1b23cae1c"
SOURCE_FILES = {
    "ledger": "m5_identity_candidate_ledger_private.json",
    "registry": "m5_identity_candidate_registry_private.json",
    "summary": "m5_identity_candidate_summary_safe.json",
    "manifest": "m5_identity_candidate_manifest_private.json",
}
PINNED_SOURCE_SHA256 = {
    "ledger": "49f00380da610f85637ea77178a2eda9f784e0ffe1ac9db2dee79a40dac4337f",
    "registry": "607e29f7dd22a81c7765ff24f1f493b9a0ec796425632a353abb94f615c79f79",
    "summary": "797510fcd085fe6f817387ce5791f33f5757900510cfc6af9b53017e7613c0e7",
    "manifest": "53e01b49ca603c61fee45485f9a8f87a93514caa20a712bef206c0e6287e9452",
}
OUTPUT_FILES = {
    "index": "m5_preparation_candidate_screen_private.json",
    "registry": "m5_preparation_candidate_screen_registry_private.json",
    "summary": "m5_preparation_candidate_screen_summary_safe.json",
    "manifest": "m5_preparation_candidate_screen_manifest_private.json",
}
SECTIONS = {"identity_candidate", "issue_evidence", "later_only_security_evidence"}
ARRAY_START = re.compile(r'^  "([a-z_]+)": \[$')
EMPTY_ARRAY = re.compile(r'^  "([a-z_]+)": \[\],?$')


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def check_rules() -> None:
    require(Path(audit_module.__file__).resolve() == AUDIT_HELPER.resolve(),
            "IMPORTED_AUDIT_MODULE_PATH_MISMATCH")
    require(sha256_file(AUDIT_HELPER) == AUDIT_HELPER_SHA256,
            "IMPORTED_AUDIT_MODULE_HASH_MISMATCH")
    require(sha256_file(POLICY) == POLICY_SHA256, "PREPARATION_POLICY_HASH_MISMATCH")
    require(sha256_file(CANDIDATE) == CANDIDATE_SHA256, "M5_CANDIDATE_HASH_MISMATCH")
    require(sha256_file(DICTIONARY) == DICTIONARY_SHA256, "DICTIONARY_HASH_MISMATCH")
    require(sha256_file(DECISION) == DECISION_SHA256, "PRIVATE_DECISION_HASH_MISMATCH")
    p = json.loads(POLICY.read_text(encoding="utf-8"))
    c = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    d = json.loads(DECISION.read_text(encoding="utf-8"))
    require(p.get("schema") == "m5_accepted_preparation_policy_v1"
            and p.get("preparation_rules_researcher_accepted") is True
            and p.get("full_m5_protocol_researcher_accepted") is False
            and p["candidate_link_screening"]["provider_quality_codes_for_candidate_screen"] == [2, 3]
            and p["candidate_link_screening"]["existing_cusip8_exact_join_preserved"] is True
            and c.get("status") == "PROPOSED"
            and c.get("researcher_accepted") is False
            and c.get("formal_financial_experiment_status") == "NOT_RUN",
            "PREPARATION_OR_CANDIDATE_STATUS_INVALID")
    require(d.get("accepted_policy_sha256") == POLICY_SHA256
            and d.get("original_round8_candidate_sha256") == CANDIDATE_SHA256
            and d.get("decision_scope") == "ACCEPTED_ENGINEERING_PREPARATION_ONLY"
            and d.get("accepted_rule_ids") == p.get("rule_ids")
            and d.get("preparation_rules_researcher_accepted") is True
            and d.get("full_m5_protocol_researcher_accepted") is False,
            "PRIVATE_DECISION_SCOPE_OR_VERSION_MISMATCH")


def check_saved_source(source_dir: Path) -> tuple[dict, dict, dict[str, str]]:
    """Bind the old ledger, its builder and registered raw files; parse no CSV."""
    source_dir = source_dir.resolve(strict=True)
    require(source_dir.is_relative_to(ROOT / "artifacts"), "SOURCE_NOT_LOCAL_ARTIFACT")
    paths = {role: source_dir / name for role, name in SOURCE_FILES.items()}
    require(all(path.is_file() for path in paths.values()), "SOURCE_ARTIFACT_MISSING")
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    require(manifest.get("schema") == "m5_identity_candidate_output_manifest_private_v1"
            and set(manifest.get("files", {})) ==
            {SOURCE_FILES[role] for role in ("ledger", "registry", "summary")},
            "SOURCE_MANIFEST_INVALID")
    hashes = {}
    for role, path in paths.items():
        hashes[role] = sha256_file(path)
        if role != "manifest":
            item = manifest["files"][path.name]
            require(item["sha256"] == hashes[role] and item["bytes"] == path.stat().st_size,
                    "SOURCE_ARTIFACT_HASH_MISMATCH")
    require(hashes == PINNED_SOURCE_SHA256, "PINNED_SOURCE_LEDGER_HASH_MISMATCH")
    registry = json.loads(paths["registry"].read_text(encoding="utf-8"))
    summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
    require(registry.get("schema") == "m5_identity_candidate_registry_private_v1"
            and summary.get("schema") == "m5_identity_candidate_summary_safe_v1"
            and summary.get("ledger_sha256") == hashes["ledger"]
            and summary.get("registry_sha256") == hashes["registry"]
            and registry.get("script_sha256") == sha256_file(OLD_BUILDER)
            and Path(registry.get("script_path", "")).resolve() == OLD_BUILDER.resolve()
            and registry.get("m5_candidate_sha256") == summary.get("m5_candidate_sha256")
            == CANDIDATE_SHA256 and registry.get("researcher_accepted") is False
            and summary.get("accepted_candidate_count") == 0
            and summary.get("formal_m5_cohort_count") is None,
            "SOURCE_REGISTRY_OR_SUMMARY_MISMATCH")
    sources = registry["source_manifest"]["registered_source_files"]
    for source in sources.values():
        require(sha256_file(Path(source["path"])) == source["sha256"],
                "REGISTERED_RAW_SOURCE_HASH_MISMATCH")
    return registry, summary, hashes


def stream_source_arrays(path: Path):
    """Parse only required object arrays in the pinned pretty-printed JSON."""
    seen: set[str] = set()
    active: str | None = None
    buffer: list[str] = []
    with path.open("r", encoding="utf-8", newline="") as stream:
        for line in stream:
            stripped = line.rstrip("\r\n")
            if active is None:
                empty = EMPTY_ARRAY.fullmatch(stripped)
                if empty and empty.group(1) in SECTIONS:
                    require(empty.group(1) not in seen, "SOURCE_ARRAY_DUPLICATED")
                    seen.add(empty.group(1))
                    continue
                match = ARRAY_START.fullmatch(stripped)
                if match and match.group(1) in SECTIONS:
                    active = match.group(1)
                    require(active not in seen, "SOURCE_ARRAY_DUPLICATED")
                    seen.add(active)
                continue
            if stripped in ("  ],", "  ]"):
                require(not buffer, "SOURCE_ARRAY_TRUNCATED")
                active = None
                continue
            if not buffer:
                require(stripped == "    {", "SOURCE_ARRAY_FORMAT_INVALID")
            buffer.append(line)
            if stripped in ("    },", "    }"):
                record = "".join(buffer).rstrip("\r\n")
                if record.endswith(","):
                    record = record[:-1]
                yield active, json.loads(record)
                buffer = []
    require(active is None and seen == SECTIONS, "SOURCE_ARRAY_COVERAGE_INVALID")


def _quality_state(code: object) -> str:
    if isinstance(code, bool):
        return "QUALITY_UNRESOLVED"
    if (isinstance(code, int) and code in (2, 3)) or (isinstance(code, str) and code in ("2", "3")):
        return "CANDIDATE_SCREEN_PASS"
    if (isinstance(code, int) and code in (0, 1)) or (isinstance(code, str) and code in ("0", "1")):
        return "CANDIDATE_SCREEN_REJECT_QUALITY"
    return "QUALITY_UNRESOLVED"


def _screen_state(code: object, cik: object) -> str:
    if cik is None:
        return "CIK_UNUSABLE"
    # The pinned intake applies normalize_id(..., 10).zfill(10) before saving.
    require(isinstance(cik, str) and re.fullmatch(r"[0-9]{10}", cik) is not None
            and cik != "0000000000",
            "SOURCE_NORMALIZED_CIK_INVALID")
    return _quality_state(code)


def _code_bucket(code: object) -> str:
    if code is None:
        return "MISSING"
    if not isinstance(code, bool) and ((isinstance(code, int) and code in (0, 1, 2, 3))
                                       or (isinstance(code, str) and code in ("0", "1", "2", "3"))):
        return str(code)
    return "UNKNOWN"


def build_view(records, old_summary: dict) -> tuple[dict, dict]:
    """Build a private edge screen and recomputable safe denominators."""
    edges = []
    issues = []
    later_only_count = 0
    later_only_ids = set()
    for section, row in records:
        if section == "identity_candidate":
            if row["candidate_kind"] != "CIK_CUSIP_OBSERVATION":
                require(row["candidate_kind"] == "SECURITY_ISSUER_CLOSED_DATE_INTERSECTION",
                        "SOURCE_CANDIDATE_KIND_INVALID")
                continue
            observed = row["observed_filing_dates"]
            require(row["accepted"] is False
                    and row["historical_valid_from"] is None
                    and row["historical_valid_to"] is None
                    and observed["date_semantics"] == "OBSERVATION_TIME"
                    and observed["historical_link_validity_established"] is False
                    and isinstance(row["issues"], list)
                    and all(isinstance(code, str) for code in row["issues"]),
                    "SOURCE_CIK_EDGE_STATUS_INVALID")
            edges.append({
                "identity_candidate_id": row["identity_candidate_id"],
                "issue_evidence_id": row["issue_evidence_id"],
                "source_record_id": row["source_record_id"],
                "cik": row["cik"],
                "validation_code": row["validation_code"],
                "quality_screen_status": _screen_state(row["validation_code"], row["cik"]),
                "source_issue_codes": list(row["issues"]),
                "match_kind": "EXACT_HISTORICAL_CUSIP8_FROM_PINNED_LEDGER",
                "observed_filing_dates": observed,
                "historical_valid_from": None,
                "historical_valid_to": None,
                "accepted": False,
            })
        elif section == "issue_evidence":
            require(row["accepted"] is False
                    and row["primary_security"] is None
                    and row["mic"] is None
                    and row["eligible"] is None,
                    "SOURCE_ISSUE_STATUS_INVALID")
            issues.append({
                "issue_evidence_id": row["issue_evidence_id"],
                "security_record_id": row["security_record_id"],
                "permno": row["permno"],
                "permco": row["permco"],
                "security_interval": row["security_interval"],
                "starting_snapshot_membership": row["starting_snapshot_membership"],
                "ordinary_share_eligibility": None,
                "market_mic": None,
                "primary_security": None,
                "eligibility_evidence_status": "UNKNOWN_NO_ACCEPTED_SOURCE_RULE",
                "market_evidence_status": "UNKNOWN_NO_ACCEPTED_SOURCE_RULE",
                "primary_evidence_status": "UNKNOWN_NO_ACCEPTED_SOURCE_RULE",
                "accepted": False,
            })
        elif section == "later_only_security_evidence":
            later_only_count += 1
            later_only_ids.add(row["permno"])
    require(len(edges) == old_summary["cik_observation_edge_count"]
            and len(issues) == old_summary["issue_interval_count"]
            and later_only_count == old_summary["later_only_interval_count"]
            and len(later_only_ids) == old_summary["later_only_distinct_security_count"],
            "SOURCE_AGGREGATE_COVERAGE_MISMATCH")
    view = {
        "schema": "m5_preparation_candidate_screen_private_v1",
        "scope": "ENGINEERING_ONLY_PREPARATION",
        "screening_match_kind": "EXACT_HISTORICAL_CUSIP8_FROM_PINNED_LEDGER",
        "issue_evidence": issues,
        "cik_observation_edge": edges,
        "later_only_distinct_security_count": len(later_only_ids),
        "later_only_interval_count": later_only_count,
        "historical_cik_validity": "NOT_ESTABLISHED",
        "formal_m5_cohort_count": None,
        "admitted_observations": None,
        "accepted_candidate_count": 0,
        "financial_experiment_run": False,
    }
    return view, summarize_view(view, old_summary)


def summarize_view(view: dict, old_summary: dict) -> dict:
    """Recompute all screen counts from the saved private rows."""
    edges = view["cik_observation_edge"]
    issues = view["issue_evidence"]
    issue_ids = {item["issue_evidence_id"] for item in issues}
    require(len(issue_ids) == len(issues), "ISSUE_ID_DUPLICATED")
    by_issue: dict[str, list[dict]] = defaultdict(list)
    edge_ids = set()
    source_edge_refs = set()
    statuses = Counter()
    code_buckets = Counter()
    source_issue_codes = Counter()
    source_issue_affected = set()
    for edge in edges:
        require(edge["identity_candidate_id"] not in edge_ids
                and edge["issue_evidence_id"] in issue_ids
                and edge["accepted"] is False
                and edge["historical_valid_from"] is None
                and edge["historical_valid_to"] is None
                and edge["match_kind"] == "EXACT_HISTORICAL_CUSIP8_FROM_PINNED_LEDGER"
                and edge["observed_filing_dates"]["date_semantics"] == "OBSERVATION_TIME"
                and edge["quality_screen_status"] ==
                _screen_state(edge["validation_code"], edge["cik"])
                and isinstance(edge["source_issue_codes"], list)
                and all(isinstance(code, str) for code in edge["source_issue_codes"]),
                "SCREENED_EDGE_INVALID")
        edge_ids.add(edge["identity_candidate_id"])
        source_edge_refs.add(edge["source_record_id"])
        statuses[edge["quality_screen_status"]] += 1
        code_buckets[_code_bucket(edge["validation_code"])] += 1
        source_issue_codes.update(edge["source_issue_codes"])
        if edge["source_issue_codes"]:
            source_issue_affected.add(edge["issue_evidence_id"])
        by_issue[edge["issue_evidence_id"]].append(edge)
    issue_statuses = Counter()
    affected = set()
    distinct_screened_ciks = set()
    starting_security_ids = set()
    starting_issuer_to_securities: dict[str, set[str]] = defaultdict(set)
    starting_without_issuer_id = set()
    for issue in issues:
        require(issue["accepted"] is False
                and issue["primary_security"] is None
                and issue["market_mic"] is None
                and issue["ordinary_share_eligibility"] is None,
                "PREPARATION_ISSUE_STATUS_INVALID")
        if issue["starting_snapshot_membership"]:
            starting_security_ids.add(issue["permno"])
            if issue["permco"] is None:
                starting_without_issuer_id.add(issue["permno"])
            else:
                starting_issuer_to_securities[issue["permco"]].add(issue["permno"])
        issue_id = issue["issue_evidence_id"]
        candidates = by_issue[issue_id]
        passed = [edge for edge in candidates
                  if edge["quality_screen_status"] == "CANDIDATE_SCREEN_PASS"]
        if not candidates:
            issue_statuses["no_cik_observation_edge"] += 1
            affected.add(issue_id)
        if not passed:
            issue_statuses["no_screened_cik_candidate"] += 1
            affected.add(issue_id)
        else:
            issue_statuses["has_screened_cik_candidate"] += 1
        if any(edge["quality_screen_status"] == "QUALITY_UNRESOLVED" for edge in candidates):
            issue_statuses["quality_unresolved"] += 1
            affected.add(issue_id)
        if any(edge["quality_screen_status"] == "CIK_UNUSABLE" for edge in candidates):
            issue_statuses["cik_unusable"] += 1
            affected.add(issue_id)
        if any(edge["quality_screen_status"] == "CANDIDATE_SCREEN_REJECT_QUALITY" for edge in candidates):
            issue_statuses["quality_rejected"] += 1
            affected.add(issue_id)
        ciks = [edge["cik"] for edge in passed if edge["cik"] is not None]
        distinct = set(ciks)
        distinct_screened_ciks.update(distinct)
        if len(ciks) != len(distinct):
            issue_statuses["parallel_edges_same_cik"] += 1
            affected.add(issue_id)
        if len(distinct) > 1:
            issue_statuses["multiple_distinct_screened_ciks"] += 1
            affected.add(issue_id)
    require(len(starting_security_ids) == old_summary["starting_security_count"],
            "STARTING_SCOPE_MISMATCH")
    require(sum(statuses.values()) == len(edges)
            and statuses["CANDIDATE_SCREEN_PASS"] + statuses["CANDIDATE_SCREEN_REJECT_QUALITY"]
            + statuses["QUALITY_UNRESOLVED"] + statuses["CIK_UNUSABLE"] == len(edges),
            "SCREEN_STATE_ACCOUNTING_MISMATCH")
    all_edge_states = ("CANDIDATE_SCREEN_PASS", "CANDIDATE_SCREEN_REJECT_QUALITY",
                       "QUALITY_UNRESOLVED", "CIK_UNUSABLE")
    all_issue_states = ("no_cik_observation_edge", "no_screened_cik_candidate",
                        "has_screened_cik_candidate", "quality_unresolved",
                        "quality_rejected", "cik_unusable", "parallel_edges_same_cik",
                        "multiple_distinct_screened_ciks")
    return {
        "schema": "m5_preparation_candidate_screen_summary_safe_v1",
        "scope": "ENGINEERING_ONLY_PREPARATION",
        "status": "CANDIDATE_SCREEN_BUILT_NOT_ADMITTED",
        "old_export_predicate_verified": False,
        "quality_bucket_coverage": "PINNED_EXACT_CUSIP8_LEDGER_EDGES_ONLY_NOT_FULL_PROVIDER_EXPORT",
        "count_units": {
            "source_cik_link_rows": "prior receipt raw rows; not reparsed here",
            "cik_observation_edges": "candidate edges on issue intervals",
            "issue_intervals": "starting-security history intervals",
            "starting_securities": "distinct starting-set securities",
            "later_only_securities": "distinct later-only securities",
            "affected_issue_intervals": "distinct issue intervals in overlapping exception categories",
            "source_issue_codes": "incidences on CIK observation edges; may overlap screening statuses",
            "prior_source_exception_records": "prior saved identity ledger summary; not rebuilt or replaced here",
        },
        "prior_receipt_raw_cik_link_rows": old_summary["prior_receipt_raw_row_counts"]["CIK_CUSIP"],
        "prior_source_exception_record_count": old_summary["exception_record_count"],
        "prior_source_exceptions_retained_in_pinned_ledger": True,
        "cik_observation_edge_count": len(edges),
        "cik_source_record_ref_count": len(source_edge_refs),
        "screened_edge_status_counts": {key: statuses[key] for key in all_edge_states},
        "original_validation_code_bucket_counts": {
            key: code_buckets[key] for key in ("0", "1", "2", "3", "MISSING", "UNKNOWN")},
        "source_issue_code_incidence_counts": dict(sorted(source_issue_codes.items())),
        "source_issue_distinct_affected_issue_interval_count": len(source_issue_affected),
        "issue_interval_count": len(issues),
        "issue_interval_status_counts": {key: issue_statuses[key] for key in all_issue_states},
        "distinct_screened_cik_count": len(distinct_screened_ciks),
        "distinct_affected_issue_interval_count": len(affected),
        "starting_security_count": len(starting_security_ids),
        "distinct_starting_issuer_id_count": len(starting_issuer_to_securities),
        "starting_securities_without_issuer_id_count": len(starting_without_issuer_id),
        "starting_issuer_ids_with_multiple_securities_count": sum(
            len(securities) > 1 for securities in starting_issuer_to_securities.values()),
        "later_only_interval_count": view["later_only_interval_count"],
        "later_only_distinct_security_count": view["later_only_distinct_security_count"],
        "ordinary_share_source_supported_issue_count": 0,
        "ordinary_share_unknown_issue_count": len(issues),
        "market_mic_source_supported_issue_count": 0,
        "market_mic_unknown_issue_count": len(issues),
        "primary_security_source_supported_issue_count": 0,
        "primary_security_unknown_issue_count": len(issues),
        "formal_m5_cohort_count": None,
        "admitted_observations": None,
        "historical_cik_validity": "NOT_ESTABLISHED",
        "financial_experiment_run": False,
    }


def _output_manifest(files: dict[str, dict]) -> dict:
    return {"schema": "m5_preparation_candidate_screen_manifest_private_v1",
            "files": {name: {"sha256": hashlib.sha256(json_bytes(data)).hexdigest(),
                             "bytes": len(json_bytes(data))}
                      for name, data in files.items()}}


def run(source_dir: Path, output_dir: Path) -> dict:
    check_rules()
    output_dir = output_dir.resolve()
    check_ignored_output(output_dir)
    source_dir = source_dir.resolve(strict=True)
    require(source_dir != output_dir, "SOURCE_OUTPUT_SAME_DIRECTORY")
    source_registry, old_summary, source_hashes = check_saved_source(source_dir)
    view, summary = build_view(
        stream_source_arrays(source_dir / SOURCE_FILES["ledger"]), old_summary)
    require(sha256_file(source_dir / SOURCE_FILES["ledger"]) == source_hashes["ledger"],
            "SOURCE_CHANGED_DURING_READ")
    script_hash = sha256_file(Path(__file__).resolve())
    registry = {
        "schema": "m5_preparation_candidate_screen_registry_private_v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_directory": str(source_dir),
        "source_file_sha256": source_hashes,
        "source_raw_file_sha256": {
            role: source["sha256"] for role, source in
            source_registry["source_manifest"]["registered_source_files"].items()},
        "script_path": str(Path(__file__).resolve()),
        "script_sha256": script_hash,
        "policy_sha256": POLICY_SHA256,
        "private_decision_sha256": DECISION_SHA256,
        "dictionary_sha256": DICTIONARY_SHA256,
        "m5_candidate_sha256": CANDIDATE_SHA256,
        "preparation_rules_researcher_accepted": True,
        "full_m5_protocol_researcher_accepted": False,
        "formal_m5_cohort_count": None,
        "admitted_observations": None,
        "financial_experiment_run": False,
    }
    summary.update({"source_ledger_sha256": source_hashes["ledger"],
                    "policy_sha256": POLICY_SHA256,
                    "script_sha256": script_hash,
                    "private_index_sha256": hashlib.sha256(json_bytes(view)).hexdigest()})
    registry["audit_helper_sha256"] = AUDIT_HELPER_SHA256
    files = {OUTPUT_FILES["index"]: view, OUTPUT_FILES["registry"]: registry,
             OUTPUT_FILES["summary"]: summary}
    files[OUTPUT_FILES["manifest"]] = _output_manifest(files)
    check_rules()
    write_directory(output_dir, files)
    return verify_output(output_dir)


def verify_output(output_dir: Path) -> dict:
    check_rules()
    output_dir = output_dir.resolve(strict=True)
    require(output_dir.is_relative_to(ROOT / "artifacts"), "OUTPUT_NOT_LOCAL_ARTIFACT")
    paths = {role: output_dir / name for role, name in OUTPUT_FILES.items()}
    require({p.name for p in output_dir.iterdir()} == set(OUTPUT_FILES.values()),
            "OUTPUT_FILE_SET_MISMATCH")
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    require(manifest.get("schema") == "m5_preparation_candidate_screen_manifest_private_v1"
            and set(manifest.get("files", {})) ==
            {OUTPUT_FILES[role] for role in ("index", "registry", "summary")},
            "OUTPUT_MANIFEST_INVALID")
    for role in ("index", "registry", "summary"):
        item = manifest["files"][paths[role].name]
        require(sha256_file(paths[role]) == item["sha256"]
                and paths[role].stat().st_size == item["bytes"],
                "OUTPUT_FILE_HASH_MISMATCH")
    view = json.loads(paths["index"].read_text(encoding="utf-8"))
    registry = json.loads(paths["registry"].read_text(encoding="utf-8"))
    summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
    require(view.get("schema") == "m5_preparation_candidate_screen_private_v1"
            and registry.get("schema") == "m5_preparation_candidate_screen_registry_private_v1"
            and registry.get("policy_sha256") == POLICY_SHA256
            and registry.get("private_decision_sha256") == DECISION_SHA256
            and registry.get("audit_helper_sha256") == AUDIT_HELPER_SHA256
            and registry.get("dictionary_sha256") == DICTIONARY_SHA256
            and registry.get("m5_candidate_sha256") == CANDIDATE_SHA256
            and registry.get("script_sha256") == sha256_file(Path(__file__).resolve())
            and Path(registry.get("script_path", "")).resolve() == Path(__file__).resolve()
            and registry.get("preparation_rules_researcher_accepted") is True
            and registry.get("full_m5_protocol_researcher_accepted") is False
            and registry.get("formal_m5_cohort_count") is None
            and registry.get("admitted_observations") is None
            and registry.get("financial_experiment_run") is False
            and view.get("formal_m5_cohort_count") is None
            and view.get("admitted_observations") is None
            and view.get("accepted_candidate_count") == 0
            and view.get("financial_experiment_run") is False,
            "OUTPUT_IDENTITY_OR_STATUS_MISMATCH")
    source_dir = Path(registry["source_directory"])
    source_registry, old_summary, source_hashes = check_saved_source(source_dir)
    expected_raw_hashes = {
        role: source["sha256"] for role, source in
        source_registry["source_manifest"]["registered_source_files"].items()}
    require(source_hashes == registry["source_file_sha256"]
            and registry.get("source_raw_file_sha256") == expected_raw_hashes
            and summary.get("source_ledger_sha256") == source_hashes["ledger"],
            "OUTPUT_SOURCE_BINDING_MISMATCH")
    recomputed_from_saved = summarize_view(view, old_summary)
    for key, value in recomputed_from_saved.items():
        require(summary.get(key) == value, "OUTPUT_AGGREGATE_RECOMPUTATION_MISMATCH")
    rebuilt_view, rebuilt_summary = build_view(
        stream_source_arrays(source_dir / SOURCE_FILES["ledger"]), old_summary)
    require(rebuilt_view == view, "OUTPUT_SOURCE_RECOMPUTATION_MISMATCH")
    for key, value in rebuilt_summary.items():
        require(summary.get(key) == value, "OUTPUT_SOURCE_AGGREGATE_MISMATCH")
    require(summary.get("private_index_sha256") == sha256_file(paths["index"])
            and summary.get("policy_sha256") == POLICY_SHA256
            and summary.get("script_sha256") == registry["script_sha256"],
            "OUTPUT_SUMMARY_BINDING_MISMATCH")
    for role in ("index", "registry", "summary"):
        require(sha256_file(paths[role]) == manifest["files"][paths[role].name]["sha256"],
                "OUTPUT_CHANGED_DURING_VERIFY")
    check_rules()
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--source-dir", required=True, type=Path)
    run_parser.add_argument("--output-dir", required=True, type=Path)
    verify_parser = sub.add_parser("verify-output")
    verify_parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "run":
            run(args.source_dir, args.output_dir)
            print("M5_PREPARATION_CANDIDATE_SCREEN_SAVED_NOT_ADMITTED")
        else:
            verify_output(args.output_dir)
            print("M5_PREPARATION_CANDIDATE_SCREEN_OUTPUT_VERIFIED")
        return 0
    except (AuditError, OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        code = str(exc) if isinstance(exc, AuditError) else "LOCAL_INPUT_OR_OUTPUT_INVALID"
        print(f"M5_PREPARATION_CANDIDATE_REVIEW_REQUIRED:{code}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
