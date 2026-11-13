"""Independent source-to-output readback for the private S2 readiness table."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PREV = ROOT / "artifacts" / "m5_next_20261114_0325"
DEFAULT_OUT = ROOT / "artifacts" / "m5_source_closure_20261114_0400"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run(out: Path) -> dict:
    safe = read_json(out / "joint_coverage_safe.json")
    previous = {
        "candidate_ledger": PREV / "candidate_events_private.json",
        "r3_private": PREV / "r3_candidate_intersection_private.json",
        "r3_safe": PREV / "r3_candidate_intersection_safe.json",
        "scope_manifest": PREV / "discovery_scope_manifest_private.json",
    }
    for label, path in previous.items():
        if digest(path) != safe["input_sha256"][label]:
            raise ValueError(f"input hash no longer agrees: {label}")
    ledger = read_json(previous["candidate_ledger"])
    r3 = read_json(previous["r3_private"])
    if ledger["formal_admitted_count"] != 0:
        raise ValueError("unexpected formal admission")
    lookup = {}
    for source in r3["event_rows"]:
        k = (source["cik"], source["accession"])
        if k in lookup:
            raise ValueError("duplicate CCM intersection key")
        lookup[k] = source
    nondev = ledger["nondevelopment_candidate_rows"]
    dev = ledger["development_only_rows"]
    if len(nondev) != 133 or len(dev) != 30:
        raise ValueError("SEC event denominators changed")
    if len(lookup) != len(nondev) + len(dev):
        raise ValueError("R3 event denominator changed")

    # Raw input predicates, intentionally separate from the builder's status helpers.
    raw_text = {(x["cik"], x["accession"]) for x in nondev
                if x["text_status"] == "supported" and bool(x["item1_hash"])}
    raw_finite = {(x["cik"], x["accession"]) for x in nondev
                  if lookup[(x["cik"], x["accession"])]["finite_source_rows_bracketing_filing"] > 0
                  and lookup[(x["cik"], x["accession"])]["matched_end_source_rows"] == 0}
    raw_unknown = {(x["cik"], x["accession"]) for x in nondev
                   if lookup[(x["cik"], x["accession"])]["matched_end_source_rows"] > 0}
    expected_all = {(x["cik"], x["accession"]) for x in nondev + dev}
    readiness_path = out / "event_readiness_private.csv"
    selected_path = out / "selected_identity_review_private.json"
    if digest(readiness_path) != safe["private_output_sha256"]["readiness"]:
        raise ValueError("private readiness hash changed")
    if digest(selected_path) != safe["private_output_sha256"]["selection"]:
        raise ValueError("private selection hash changed")
    with readiness_path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    output_keys = [(x["cik"], x["accession"]) for x in rows]
    if len(output_keys) != len(set(output_keys)) or set(output_keys) != expected_all:
        raise ValueError("private readiness row keys do not match frozen source")
    if output_keys != sorted(output_keys, key=lambda k: (
            next(x["filing_date"] for x in rows if (x["cik"], x["accession"]) == k), k[0], k[1])):
        raise ValueError("readiness sort order changed")
    by_key = {(x["cik"], x["accession"]): x for x in rows}
    for source in nondev:
        k = (source["cik"], source["accession"])
        observed = by_key[k]
        if observed["group"] != "nondevelopment" or observed["security_id"] or observed["formal_admitted"] != "False":
            raise ValueError("nondevelopment event was unexpectedly promoted")
        if (observed["item1_status"] == "supported") != (k in raw_text):
            raise ValueError("Item 1 predicate disagrees with source")
        if (observed["candidate_finite_interval_status"] == "literal_bracket_candidate_id_only"
            and observed["unresolved_endpoint_candidate_status"] == "candidate_id_nonhit") != (k in raw_finite):
            raise ValueError("finite/no-unresolved predicate disagrees with source")
        if (observed["unresolved_endpoint_candidate_status"] == "candidate_id_hit") != (k in raw_unknown):
            raise ValueError("unresolved endpoint predicate disagrees with source")
        for field in ("calendar_status", "price_status", "exit_status"):
            if observed[field] != source[field]:
                raise ValueError(f"{field} drift")
    for source in dev:
        k = (source["cik"], source["accession"])
        if by_key[k]["group"] != "development_only" or by_key[k]["historical_cik_security_status"] != "not_applicable":
            raise ValueError("development group misclassified")

    selected = read_json(selected_path)
    expected_selected = sorted(raw_text & raw_finite, key=lambda k: (
        next(x["filing_date"] for x in nondev if (x["cik"], x["accession"]) == k), k[0], k[1]))[:3]
    actual_selected = [(x["cik"], x["accession"]) for x in selected["selected_rows"]]
    if actual_selected != expected_selected or selected["eligible_event_count"] != len(raw_text & raw_finite):
        raise ValueError("selection no longer follows frozen source predicate/order")
    observed_cross = safe["item1_by_finite_no_unresolved_cross_tab"]
    expected_cross = {
        "item1_supported__finite_no_unresolved": len(raw_text & raw_finite),
        "item1_supported__other": len(raw_text - raw_finite),
        "item1_other__finite_no_unresolved": len(raw_finite - raw_text),
        "item1_other__other": len(expected_all - { (x["cik"],x["accession"]) for x in dev } - (raw_text | raw_finite)),
    }
    if observed_cross != expected_cross:
        raise ValueError("safe cross-tab differs from source sets")
    report = {
        "schema": "m5_source_closure_joint_coverage_readback_safe_v1",
        "status": "PASS", "input_sha256": safe["input_sha256"],
        "private_output_sha256": safe["private_output_sha256"],
        "nondevelopment_events_revalidated": len(nondev),
        "development_events_revalidated": len(dev),
        "readiness_rows_revalidated": len(rows),
        "item1_supported_events": len(raw_text),
        "finite_no_unresolved_candidate_id_events": len(raw_finite),
        "unresolved_end_candidate_id_hit_events": len(raw_unknown),
        "item1_and_finite_no_unresolved_intersection": len(raw_text & raw_finite),
        "selected_events_revalidated": len(actual_selected),
        "formal_admitted_count": 0,
        "scope_warning": "All finite and endpoint conditions are candidate-ID structural predicates, not historical identity claims.",
    }
    path = out / "joint_coverage_readback_safe.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    print(json.dumps(run(args.out), ensure_ascii=False))
