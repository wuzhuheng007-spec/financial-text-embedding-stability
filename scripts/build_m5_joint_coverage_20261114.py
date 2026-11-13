"""Join frozen SEC discovery and CCM structural evidence without admitting events.

The row-level output contains source identifiers and stays in an ignored local
artifact directory. Only aggregate counts are suitable for the handoff ZIP.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PREV = ROOT / "artifacts" / "m5_next_20261114_0325"
DEFAULT_OUT = ROOT / "artifacts" / "m5_source_closure_20261114_0400"
EXPECTED_LEDGER = "eea1a6470e250438d30a0f3d63101e15d2ea973ef4b1999dbe85fcd5295320ea"
EXPECTED_R3_PRIVATE = "2374a22fb567676ab3df55e6da43c52328d393b1907be5704ac6637f8b8f4085"
EXPECTED_R3_SAFE = "e11b527ae63914bfbf8a8b83cfbaba9cba079a65761102b5283a0e9f695c13ed"
EXPECTED_SCOPE = "b3b157e77dc52e8d65568682504a9762e6455ee88d5b43a6a12794ca101f1276"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(name: str, expected: str) -> dict:
    path = PREV / name
    actual = sha(path)
    if actual != expected:
        raise ValueError(f"frozen input drift: {name}: {actual}")
    return json.loads(path.read_text(encoding="utf-8"))


def key(row: dict) -> tuple[str, str]:
    return str(row["cik"]), str(row["accession"])


def status_for_original(row: dict) -> str:
    return "supported" if row["raw_submission_sha256"] else "not_checked"


def status_for_header(row: dict) -> str:
    raw = row["raw_header_check"]
    if raw is None:
        return "not_checked"
    if raw.get("status") not in {"supported", "ambiguous", "unsupported"}:
        raise ValueError("unexpected header status")
    return raw["status"]


def status_for_finite(r3: dict, has_candidate_ids: bool) -> str:
    if not has_candidate_ids:
        return "not_applicable"
    if r3["finite_source_rows_bracketing_filing"] > 0:
        return "literal_bracket_candidate_id_only"
    if r3["matched_finite_end_source_rows"] > 0:
        return "finite_source_hit_not_bracketing"
    return "no_finite_source_hit_in_candidate_ids"


def status_for_unresolved(r3: dict, has_candidate_ids: bool) -> str:
    if not has_candidate_ids:
        return "unknown_no_candidate_id"
    return ("candidate_id_hit" if r3["matched_end_source_rows"] > 0
            else "candidate_id_nonhit")


def main(out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    ledger = load("candidate_events_private.json", EXPECTED_LEDGER)
    r3 = load("r3_candidate_intersection_private.json", EXPECTED_R3_PRIVATE)
    r3_safe = load("r3_candidate_intersection_safe.json", EXPECTED_R3_SAFE)
    scope_path = PREV / "discovery_scope_manifest_private.json"
    if sha(scope_path) != EXPECTED_SCOPE:
        raise ValueError("frozen scope manifest drift")
    if ledger["scope_manifest_sha256"] != EXPECTED_SCOPE:
        raise ValueError("ledger scope binding changed")
    if r3["input_sha256"]["frozen_event_ledger"] != EXPECTED_LEDGER:
        raise ValueError("R3 row-level ledger binding changed")
    if r3_safe["input_sha256"]["frozen_event_ledger"] != EXPECTED_LEDGER:
        raise ValueError("R3 aggregate ledger binding changed")

    events = [("nondevelopment", x) for x in ledger["nondevelopment_candidate_rows"]]
    events += [("development_only", x) for x in ledger["development_only_rows"]]
    matches: dict[tuple[str, str], dict] = {}
    for row in r3["event_rows"]:
        k = key(row)
        if k in matches:
            raise ValueError("duplicate R3 event key")
        matches[k] = row
    if {key(row) for _, row in events} != set(matches):
        raise ValueError("SEC and R3 event keys differ")
    if len(events) != len({key(row) for _, row in events}):
        raise ValueError("duplicate SEC event key")

    private_rows: list[dict] = []
    for group, row in events:
        linked = matches[key(row)]
        has_candidate_ids = bool(row["candidate_security_ids"])
        if row["candidate_security_ids"] != linked["candidate_security_ids"]:
            raise ValueError("candidate security ID sets differ")
        if group == "nondevelopment" and not has_candidate_ids:
            raise ValueError("unexpected missing nondevelopment candidate ID")
        original = status_for_original(row)
        header = status_for_header(row)
        item1 = row["text_status"]
        if item1 not in {"supported", "ambiguous", "unsupported", "not_checked"}:
            raise ValueError("unexpected Item 1 status")
        if item1 == "supported" and (not row["item1_hash"] or original != "supported" or header != "supported"):
            raise ValueError("Item 1 support without a bound original/header")
        if row["security_id"] is not None or row["formal_admitted"]:
            raise ValueError("formal security/admission unexpectedly set")
        finite = status_for_finite(linked, has_candidate_ids)
        unresolved = status_for_unresolved(linked, has_candidate_ids)
        private_rows.append({
            "group": group, "cik": row["cik"], "accession": row["accession"],
            "filing_date": row["filing_date"], "candidate_security_ids": row["candidate_security_ids"],
            "filing_metadata_status": row["filing_metadata_status"],
            "original_body_status": original, "original_body_sha256": row["raw_submission_sha256"],
            "raw_header_status": header, "item1_status": item1, "item1_sha256": row["item1_hash"],
            "candidate_finite_interval_status": finite,
            "candidate_finite_interval_bracketing_rows": linked["finite_source_rows_bracketing_filing"],
            "unresolved_endpoint_candidate_status": unresolved,
            "unresolved_endpoint_matched_source_rows": linked["matched_end_source_rows"],
            "historical_cik_security_status": "unsupported" if has_candidate_ids else "not_applicable",
            "primary_security_rule_status": "unsupported" if has_candidate_ids else "not_applicable",
            "calendar_status": row["calendar_status"], "price_status": row["price_status"],
            "exit_status": row["exit_status"], "security_id": row["security_id"],
            "formal_admitted": row["formal_admitted"], "actual_endpoint_impact": "unknown",
            "sec_source_evidence_sha256": row["source_evidence_hash"],
        })

    private_rows.sort(key=lambda x: (x["filing_date"], x["cik"], x["accession"]))
    fields = list(private_rows[0])
    private_path = out / "event_readiness_private.csv"
    with private_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows({**row, "candidate_security_ids": "|".join(row["candidate_security_ids"])}
                         for row in private_rows)

    nondev = [x for x in private_rows if x["group"] == "nondevelopment"]
    dev = [x for x in private_rows if x["group"] == "development_only"]
    eligible = [x for x in nondev if x["item1_status"] == "supported" and
                x["candidate_finite_interval_status"] == "literal_bracket_candidate_id_only" and
                x["unresolved_endpoint_candidate_status"] == "candidate_id_nonhit"]
    selected = eligible[:3]
    selection_path = out / "selected_identity_review_private.json"
    selection = {
        "schema": "m5_source_closure_identity_selection_private_v1",
        "source_ledger_sha256": EXPECTED_LEDGER,
        "readiness_private_sha256": sha(private_path),
        "selection_rule": "Item1 supported AND literal finite bracket AND no unresolved endpoint hit within frozen candidate IDs; first three sorted filing_date/CIK/accession",
        "eligible_event_count": len(eligible),
        "selected_rows": [{"selection_rank": i + 1, "cik": x["cik"], "accession": x["accession"],
                           "filing_date": x["filing_date"], "candidate_security_ids": x["candidate_security_ids"],
                           "original_body_sha256": x["original_body_sha256"], "item1_sha256": x["item1_sha256"]}
                          for i, x in enumerate(selected)],
    }
    selection_path.write_text(json.dumps(selection, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    joint = Counter((x["item1_status"] == "supported",
                     x["candidate_finite_interval_status"] == "literal_bracket_candidate_id_only" and
                     x["unresolved_endpoint_candidate_status"] == "candidate_id_nonhit") for x in nondev)
    safe = {
        "schema": "m5_source_closure_joint_coverage_safe_v1",
        "status": "SOURCE_ROW_AND_SEC_CANDIDATE_COVERAGE_ONLY",
        "input_sha256": {"candidate_ledger": EXPECTED_LEDGER, "r3_private": EXPECTED_R3_PRIVATE,
                         "r3_safe": EXPECTED_R3_SAFE, "scope_manifest": EXPECTED_SCOPE},
        "private_output_sha256": {"readiness": sha(private_path), "selection": sha(selection_path)},
        "denominators": {"nondevelopment_events": len(nondev), "development_only_events": len(dev)},
        "nondevelopment_set_counts": {
            "original_body_available": sum(x["original_body_status"] == "supported" for x in nondev),
            "header_supported": sum(x["raw_header_status"] == "supported" for x in nondev),
            "item1_supported": sum(x["item1_status"] == "supported" for x in nondev),
            "candidate_finite_interval_literal_bracket_and_no_unresolved_hit":
                sum(x["candidate_finite_interval_status"] == "literal_bracket_candidate_id_only" and
                    x["unresolved_endpoint_candidate_status"] == "candidate_id_nonhit" for x in nondev),
            "unresolved_endpoint_candidate_id_hit":
                sum(x["unresolved_endpoint_candidate_status"] == "candidate_id_hit" for x in nondev),
        },
        "item1_by_finite_no_unresolved_cross_tab": {
            "item1_supported__finite_no_unresolved": joint[(True, True)],
            "item1_supported__other": joint[(True, False)],
            "item1_other__finite_no_unresolved": joint[(False, True)],
            "item1_other__other": joint[(False, False)],
        },
        "review_selection": {"eligible_events": len(eligible), "selected_events": len(selected),
                             "selection_rule": selection["selection_rule"],
                             "selection_max_is_workload_cap_not_sufficiency": 3},
        "status_counts": {
            "nondevelopment_original_body": dict(Counter(x["original_body_status"] for x in nondev)),
            "nondevelopment_header": dict(Counter(x["raw_header_status"] for x in nondev)),
            "nondevelopment_item1": dict(Counter(x["item1_status"] for x in nondev)),
            "nondevelopment_finite_candidate_interval": dict(Counter(x["candidate_finite_interval_status"] for x in nondev)),
            "nondevelopment_unresolved_endpoint_candidate": dict(Counter(x["unresolved_endpoint_candidate_status"] for x in nondev)),
            "nondevelopment_historical_identity": dict(Counter(x["historical_cik_security_status"] for x in nondev)),
            "nondevelopment_primary_policy": dict(Counter(x["primary_security_rule_status"] for x in nondev)),
            "nondevelopment_calendar": dict(Counter(x["calendar_status"] for x in nondev)),
            "nondevelopment_price": dict(Counter(x["price_status"] for x in nondev)),
            "nondevelopment_exit": dict(Counter(x["exit_status"] for x in nondev)),
            "development_no_candidate_id": sum(not x["candidate_security_ids"] for x in dev),
        },
        "identity_caveat": "Finite brackets and endpoint nonhits are only for frozen candidate PERMNO IDs; historical issuer/security validity and primary security remain unsupported for all 133.",
        "formal_admitted_count": 0,
        "financial_experiment": "NOT_RUN",
    }
    if (len(nondev), len(dev), safe["nondevelopment_set_counts"]["item1_supported"],
        safe["nondevelopment_set_counts"]["candidate_finite_interval_literal_bracket_and_no_unresolved_hit"],
        safe["nondevelopment_set_counts"]["unresolved_endpoint_candidate_id_hit"]) != (133, 30, 16, 20, 113):
        raise ValueError("joined aggregate disagrees with frozen source summaries")
    if sum(joint.values()) != 133:
        raise ValueError("joint coverage does not partition nondevelopment events")
    safe_path = out / "joint_coverage_safe.json"
    safe_path.write_text(json.dumps(safe, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {"status": "PASS", "safe_summary_sha256": sha(safe_path),
            "private_readiness_sha256": sha(private_path), "private_selection_sha256": sha(selection_path),
            "eligible": len(eligible), "selected": len(selected)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    print(json.dumps(main(args.out), ensure_ascii=False))
