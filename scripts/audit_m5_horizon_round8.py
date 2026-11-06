"""Candidate-driven saved-price date coverage; no return labels or model calls."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path


class CoverageError(ValueError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def bound_json(path: Path, expected_sha256: str | None = None) -> dict:
    if expected_sha256 is not None and sha256(path) != expected_sha256:
        raise CoverageError(f"Source SHA-256 mismatch: {path.name}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CoverageError(f"Expected JSON object: {path.name}")
    return value


def valid_horizon(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise CoverageError("Horizon must be a positive integer number of subsequent sessions")
    return value


def valid_dates(values: list[str], label: str) -> list[str]:
    if not isinstance(values, list) or not values:
        raise CoverageError(f"{label} must be a nonempty date list")
    try:
        for value in values:
            if type(value) is not str or date.fromisoformat(value).isoformat() != value:
                raise ValueError("Noncanonical ISO date")
    except (TypeError, ValueError) as exc:
        raise CoverageError(f"{label} contains an invalid ISO date") from exc
    if values != sorted(set(values)):
        raise CoverageError(f"{label} is not strictly increasing and unique")
    return values


def coverage_on_reference_dates(reference_dates: list[str], available_dates: set[str],
                                filing_date: str, horizon: int,
                                prior_close_count: int = 21) -> dict:
    """Fix d+h on the reference axis; a missing price never shifts its endpoint."""
    valid_horizon(horizon)
    valid_dates(reference_dates, "Reference dates")
    try:
        if type(filing_date) is not str:
            raise ValueError("Filing date must be a string")
        if date.fromisoformat(filing_date).isoformat() != filing_date:
            raise ValueError("Noncanonical ISO date")
    except ValueError as exc:
        raise CoverageError("Filing date must be a valid ISO date") from exc
    if type(prior_close_count) is not int or prior_close_count <= 0:
        raise CoverageError("Prior close count must be positive integer")
    if not available_dates.issubset(set(reference_dates)):
        raise CoverageError("A saved price date is outside the reference date axis")
    first = next((i for i, value in enumerate(reference_dates) if value > filing_date), None)
    if first is None:
        return {"reference_start_date": None, "reference_end_date": None,
                "structural_pair_complete": False, "reason": "NO_REFERENCE_DATE_AFTER_FILING",
                "missing_window_price_dates": [], "trailing_close_window_complete": False}
    prior_start = first - prior_close_count + 1
    trailing = (prior_start >= 0 and
                all(value in available_dates for value in reference_dates[prior_start:first + 1]))
    end = first + horizon
    if end >= len(reference_dates):
        return {"reference_start_date": reference_dates[first], "reference_end_date": None,
                "structural_pair_complete": False, "reason": "REFERENCE_AXIS_END_BEFORE_HORIZON",
                "missing_window_price_dates": [], "trailing_close_window_complete": trailing}
    window = reference_dates[first:end + 1]
    missing = [value for value in window if value not in available_dates]
    return {"reference_start_date": reference_dates[first],
            "reference_end_date": reference_dates[end],
            "structural_pair_complete": not missing,
            "reason": "SAVED_ROWS_PRESENT_ONLY" if not missing else "MISSING_PRICE_ON_REFERENCE_AXIS",
            "missing_window_price_dates": missing,
            "trailing_close_window_complete": trailing}


def audit(candidate_path: Path, data_audit_path: Path, m4_run: Path) -> dict:
    candidate = bound_json(candidate_path)
    if (candidate.get("status") != "PROPOSED" or
            candidate.get("researcher_accepted") is not False or
            candidate.get("formal_financial_experiment_status") != "NOT_RUN"):
        raise CoverageError("Expected unchanged, unaccepted M5 proposal")
    definitions = candidate.get("decision_and_label", {})
    primary = valid_horizon(definitions.get("primary_horizon_subsequent_sessions"))
    alternative = valid_horizon(definitions.get("alternative_horizon_subsequent_sessions"))
    if primary == alternative:
        raise CoverageError("Primary and alternative horizons must differ")
    prior_count = valid_horizon(candidate.get("features", {}).get("minimum_prior_closes_including_d"))
    prior_audit = bound_json(data_audit_path)
    if prior_audit.get("status") != "LOCAL_AVAILABILITY_AUDIT_ONLY_NO_FINANCIAL_EXPERIMENT":
        raise CoverageError("Expected local availability audit, not a financial result")
    masks_path = m4_run / "common_masks.json"
    masks = bound_json(masks_path, prior_audit["sources"]["m4_common_masks_sha256"])
    reference_dates = valid_dates(masks["date_order"], "Saved common date axis")
    tickers = [row["ticker"] for row in prior_audit["firms"]]
    if masks["ticker_order"] != tickers:
        raise CoverageError("Saved price axis ticker order differs from bound audit")

    by_horizon: dict[str, dict] = {}
    for horizon in (primary, alternative):
        rows = []
        for firm in prior_audit["firms"]:
            ticker = firm["ticker"]
            bars_path = m4_run / "market" / f"bars_{ticker}.json"
            bars = bound_json(bars_path, firm["saved_price"]["processed_sha256"])
            if bars.get("ticker") != ticker or bars.get("adjusted") is not True:
                raise CoverageError(f"Saved price identity changed for {ticker}")
            saved_rows = bars.get("bars")
            if not isinstance(saved_rows, list):
                raise CoverageError(f"Missing saved bar rows for {ticker}")
            saved_dates = [row.get("date_et") for row in saved_rows]
            if saved_dates != sorted(set(saved_dates)):
                raise CoverageError(f"Saved bar dates are unordered or duplicated for {ticker}")
            available = {row["date_et"] for row in saved_rows
                         if type(row.get("c")) in (int, float)
                         and math.isfinite(row["c"]) and row["c"] > 0}
            result = coverage_on_reference_dates(reference_dates, available,
                                                 firm["official_filing_date"], horizon,
                                                 prior_count)
            rows.append({"ticker": ticker, "accession": firm["accession"],
                         "official_filing_date": firm["official_filing_date"],
                         "saved_bar_sha256": firm["saved_price"]["processed_sha256"],
                         **result,
                         "independent_calendar_confirmed_pair": None,
                         "independent_calendar_confirmed_pair_reason":
                             "No independent version-bound exchange calendar was supplied",
                         "admitted_financial_observation": None,
                         "admitted_financial_observation_reason":
                             "Public time, text vintage, universe and label policy are not accepted"})
        by_horizon[str(horizon)] = {
            "horizon_subsequent_saved_rows": horizon,
            "reference_axis": "SAVED_M4_COMMON_DATE_ORDER_NOT_INDEPENDENT_EXCHANGE_CALENDAR",
            "structural_complete_pairs": sum(row["structural_pair_complete"] for row in rows),
            "structural_pairs_with_predecision_lookback": sum(
                row["structural_pair_complete"] and row["trailing_close_window_complete"]
                for row in rows),
            "independent_calendar_confirmed_pairs": None,
            "admitted_financial_observations": None,
            "firms": rows}

    return {"schema": "m5_round8_candidate_horizon_coverage_v1",
            "status": "SAVED_ROW_STRUCTURAL_COVERAGE_ONLY_NO_LABELS",
            "audit_code_sha256": sha256(Path(__file__)),
            "candidate_sha256": sha256(candidate_path),
            "round7_data_audit_sha256": sha256(data_audit_path),
            "m4_common_masks_sha256": sha256(masks_path),
            "saved_common_date_count": len(reference_dates),
            "saved_common_date_start": reference_dates[0],
            "saved_common_date_end": reference_dates[-1],
            "independent_calendar_sha256": None,
            "independent_calendar_reason": "No independent calendar file is bound to this audit",
            "horizons": by_horizon,
            "returns_or_scores_computed": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--data-audit", type=Path, required=True)
    parser.add_argument("--m4-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.candidate, args.data_audit, args.m4_run)
    output = args.output.resolve()
    if output.exists():
        raise CoverageError("Refusing to overwrite existing coverage audit")
    output.parent.mkdir(parents=True, exist_ok=True)
    result["recorded_at_utc"] = datetime.now(timezone.utc).isoformat()
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("SAVED_ROW_COVERAGE_ONLY " + " ".join(
        f"h={key}:{value['structural_complete_pairs']}/{len(value['firms'])}"
        for key, value in result["horizons"].items()))


if __name__ == "__main__":
    main()
