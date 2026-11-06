"""Compare saved five-firm price dates with a verified XNYS baseline calendar."""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from audit_m5_horizon_round8 import bound_json, coverage_on_reference_dates, sha256, valid_horizon
from build_nyse_calendar_round9 import CalendarError, verify_calendar_dir


ROUND8_CANDIDATE_SHA256 = "f1fa28949353adea6aa179854441b48658415ef577a006fe76bd01c779ddaa38"
ROUND8_RECEIPT_SHA256 = "27d7027e871996f17e4669d3c040ef53ffc94890f40e98cd012f525c493b0a41"
ROUND7_DATA_AUDIT_SHA256 = "eab463172225170ab935de5599e258f916576de712e5434a1bfa929ebc9d391e"


class ComparisonError(ValueError):
    pass


def compare(candidate_path: Path, round8_path: Path, data_audit_path: Path,
            rules_path: Path, calendar_dir: Path, m4_run: Path) -> dict:
    if sha256(candidate_path) != ROUND8_CANDIDATE_SHA256:
        raise ComparisonError("Round-8 candidate bytes changed")
    if sha256(round8_path) != ROUND8_RECEIPT_SHA256:
        raise ComparisonError("Round-8 date receipt bytes changed")
    if sha256(data_audit_path) != ROUND7_DATA_AUDIT_SHA256:
        raise ComparisonError("Round-7 input audit bytes changed")
    candidate = bound_json(candidate_path)
    if (candidate.get("status"), candidate.get("researcher_accepted"),
            candidate.get("formal_financial_experiment_status")) != ("PROPOSED", False, "NOT_RUN"):
        raise ComparisonError("M5 proposal status changed")
    previous = bound_json(round8_path)
    if previous.get("candidate_sha256") != ROUND8_CANDIDATE_SHA256:
        raise ComparisonError("Round-8 receipt candidate binding differs")
    prior_audit = bound_json(data_audit_path)
    if previous.get("round7_data_audit_sha256") != ROUND7_DATA_AUDIT_SHA256:
        raise ComparisonError("Round-8 receipt input binding differs")
    root = Path(__file__).resolve().parents[1]
    sessions, calendar_manifest = verify_calendar_dir(rules_path, calendar_dir, root)
    if calendar_manifest["mic"] != "XNYS" or calendar_manifest["market_scope"] != "NYSE_EQUITIES_CORE":
        raise ComparisonError("Unexpected calendar market")
    calendar_dates = [row["session_date"] for row in sessions]
    saved_masks_path = m4_run / "common_masks.json"
    masks = bound_json(saved_masks_path, prior_audit["sources"]["m4_common_masks_sha256"])
    saved_dates = masks["date_order"]
    if not isinstance(saved_dates, list) or masks["ticker_order"] != [f["ticker"] for f in prior_audit["firms"]]:
        raise ComparisonError("Saved M4 date or firm axis differs from bound audit")
    year_2025 = {day for day in calendar_dates if day.startswith("2025-")}
    saved_date_set = set(saved_dates)
    missing_saved_dates = sorted(year_2025 - saved_date_set)
    extra_saved_dates = sorted(saved_date_set - year_2025)
    definitions = candidate["decision_and_label"]
    horizons = [valid_horizon(definitions["primary_horizon_subsequent_sessions"]),
                valid_horizon(definitions["alternative_horizon_subsequent_sessions"])]
    prior_count = valid_horizon(candidate["features"]["minimum_prior_closes_including_d"])
    if set(previous["horizons"]) != {str(h) for h in horizons}:
        raise ComparisonError("Round-8 receipt horizons differ from proposal")
    outputs: dict[str, dict] = {}
    for horizon in horizons:
        rows = []
        previous_by_ticker = {row["ticker"]: row for row in previous["horizons"][str(horizon)]["firms"]}
        for firm in prior_audit["firms"]:
            ticker = firm["ticker"]
            bars_path = m4_run / "market" / f"bars_{ticker}.json"
            bars = bound_json(bars_path, firm["saved_price"]["processed_sha256"])
            if bars.get("ticker") != ticker or bars.get("adjusted") is not True:
                raise ComparisonError(f"Saved price identity differs for {ticker}")
            saved_bar_rows = bars.get("bars")
            if not isinstance(saved_bar_rows, list):
                raise ComparisonError(f"Saved price rows absent for {ticker}")
            bar_dates = [row["date_et"] for row in saved_bar_rows]
            if bar_dates != sorted(set(bar_dates)):
                raise ComparisonError(f"Saved price dates unordered or duplicated for {ticker}")
            available = {row["date_et"] for row in saved_bar_rows
                         if type(row.get("c")) in (int, float) and
                         math.isfinite(row["c"]) and row["c"] > 0}
            off_calendar = sorted(set(bar_dates) - set(calendar_dates))
            result = coverage_on_reference_dates(calendar_dates, available & set(calendar_dates),
                                                 firm["official_filing_date"], horizon, prior_count)
            old = previous_by_ticker[ticker]
            fields = ("reference_start_date", "reference_end_date",
                      "structural_pair_complete", "trailing_close_window_complete")
            differences = [{"field": field, "round8_saved_row": old[field],
                            "round9_xnys_baseline": result[field]}
                           for field in fields if old[field] != result[field]]
            if off_calendar:
                differences.append({"field": "saved_bar_dates_outside_xnys",
                                    "round8_saved_row": off_calendar,
                                    "round9_xnys_baseline": []})
            rows.append({"ticker": ticker, "accession": firm["accession"],
                         "official_filing_date": firm["official_filing_date"],
                         "saved_bar_sha256": sha256(bars_path),
                         "xnys_baseline": result,
                         "round8_saved_row_reference": {field: old[field] for field in fields},
                         "differences": differences,
                         "market_applicability": "BASELINE_ONLY_LISTING_MIC_NOT_VERIFIED_AS_OF_EVENT",
                         "security_calendar_confirmed_pair": None,
                         "admitted_financial_observation": None})
        outputs[str(horizon)] = {
            "reference_axis": "XNYS_OFFICIAL_RETROSPECTIVE_BASELINE_ONLY",
            "horizon_subsequent_sessions": horizon,
            "xnys_baseline_complete_pairs": sum(row["xnys_baseline"]["structural_pair_complete"] for row in rows),
            "xnys_baseline_pairs_with_predecision_lookback": sum(
                row["xnys_baseline"]["structural_pair_complete"] and
                row["xnys_baseline"]["trailing_close_window_complete"] for row in rows),
            "difference_rows_vs_round8": sum(bool(row["differences"]) for row in rows),
            "security_calendar_confirmed_pairs": None,
            "admitted_financial_observations": None,
            "firms": rows}
    return {"schema": "m5_round9_xnys_baseline_price_coverage_v1",
            "status": "INDEPENDENT_XNYS_BASELINE_COMPARISON_NO_FINANCIAL_LABELS",
            "candidate_sha256": sha256(candidate_path),
            "round8_receipt_sha256": sha256(round8_path),
            "round7_data_audit_sha256": sha256(data_audit_path),
            "calendar_manifest_sha256": sha256(calendar_dir / "manifest.json"),
            "calendar_sessions_sha256": sha256(calendar_dir / "sessions.csv"),
            "m4_common_masks_sha256": sha256(saved_masks_path),
            "audit_code_sha256": sha256(Path(__file__)),
            "saved_2025_date_count": len(saved_dates),
            "xnys_2025_session_count": len(year_2025),
            "xnys_dates_without_saved_common_bar": missing_saved_dates,
            "saved_common_dates_outside_xnys": extra_saved_dates,
            "market_applicability_status": "BASELINE_ONLY_UNVERIFIED_EVENT_TIME_LISTING_MIC",
            "horizons": outputs,
            "return_labels_or_scores_computed": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--round8-receipt", required=True, type=Path)
    parser.add_argument("--data-audit", required=True, type=Path)
    parser.add_argument("--rules", required=True, type=Path)
    parser.add_argument("--calendar-dir", required=True, type=Path)
    parser.add_argument("--m4-run", required=True, type=Path)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--output", type=Path)
    action.add_argument("--verify-output", type=Path)
    args = parser.parse_args()
    result = compare(args.candidate, args.round8_receipt, args.data_audit,
                     args.rules, args.calendar_dir, args.m4_run)
    if args.verify_output:
        saved = bound_json(args.verify_output)
        saved.pop("recorded_at_utc", None)
        if saved != result:
            raise ComparisonError("Saved coverage output differs from source recomputation")
        print("PASS_XNYS_BASELINE_COVERAGE_RECOMPUTED")
        return
    output = args.output.resolve()
    if output.exists():
        raise ComparisonError("Refusing to overwrite saved coverage output")
    output.parent.mkdir(parents=True, exist_ok=True)
    result["recorded_at_utc"] = datetime.now(timezone.utc).isoformat()
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("XNYS_BASELINE_ONLY " + " ".join(
        f"h={h}:{x['xnys_baseline_complete_pairs']}/{len(x['firms'])}"
        for h, x in result["horizons"].items()))


if __name__ == "__main__":
    main()
