"""Derive bounded YoY/QoQ percentages only from matched quarter comparisons."""

from __future__ import annotations

import argparse
import json
import re
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results/news_earnings_macro_20261115"
CORE = ("revenue", "operating_income", "net_income")
PERIOD = re.compile(r"(?:FY)?(\d{4})[-\s]*Q([1-4])", re.IGNORECASE)


def number(value: object) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Nonfinite number")
    return result


def target_period(year: int, quarter: int, relation: str) -> tuple[int, int]:
    if relation == "YOY":
        return year - 1, quarter
    return (year - 1, 4) if quarter == 1 else (year, quarter - 1)


def comparison_period(row: dict) -> tuple[int, int] | None:
    match = PERIOD.search(str(row.get("period") or ""))
    if match:
        return int(match.group(1)), int(match.group(2))
    if row.get("fiscal_year") is not None and row.get("fiscal_quarter") is not None:
        year = int(re.search(r"\d{4}", str(row["fiscal_year"])).group())
        quarter = int(re.search(r"[1-4]", str(row["fiscal_quarter"])).group())
        return year, quarter
    return None


def derive(cards: dict, source_manifest: dict) -> list[dict]:
    captured = {(s["issuer_id"], s["url"], s["sha256"])
                for s in source_manifest["sources"] if s["captured_local_bytes_verified"]}
    output = []
    for card in cards["cards"]:
        issuer = card["issuer"]
        metrics = {row["metric"]: row for row in card["metrics"]}
        for metric in CORE:
            current = metrics[metric]
            year = int(re.search(r"\d{4}", str(current["fiscal_year"])).group())
            quarter = int(re.search(r"[1-4]", str(current["fiscal_quarter"])).group())
            for relation in ("YOY", "QOQ"):
                target = target_period(year, quarter, relation)
                matches = [row for row in card.get("comparison", [])
                           if row.get("metric") == metric
                           and comparison_period(row) == target]
                row = {
                    "issuer_id": issuer, "metric": metric, "relation": relation,
                    "current_row_id": current.get("row_id") or f"{issuer}:{metric}:CURRENT",
                    "prior_row_id": None, "target_period": f"FY{target[0]}-Q{target[1]}",
                    "status": None, "growth_percent": None,
                }
                if len(matches) != 1:
                    row["status"] = "PRIOR_NOT_UNIQUE_OR_NOT_FOUND"
                else:
                    prior = matches[0]
                    row["prior_row_id"] = prior.get("row_id") or f"{issuer}:{metric}:FY{target[0]}Q{target[1]}:PRIOR"
                    if current["raw_value"] is None or prior.get("raw_value") is None:
                        row["status"] = "VALUE_MISSING"
                    elif current["period_basis"] != "QUARTER" or prior.get("period_basis") != "QUARTER":
                        row["status"] = "PERIOD_BASIS_MISMATCH"
                    elif any(current.get(dim) != prior.get(dim) for dim in
                             ("scope", "accounting_standard", "currency")):
                        row["status"] = "BASIS_MISMATCH"
                    elif (issuer, prior.get("source_url"), prior.get("source_sha256")) not in captured:
                        row["status"] = "PRIOR_SOURCE_NOT_CAPTURED"
                    elif (prior.get("adjustment_basis") != current.get("adjustment_basis")
                          and prior.get("source_url") != current.get("source_url")):
                        row["status"] = "ADJUSTMENT_BASIS_UNVERIFIED"
                    else:
                        previous = number(prior["raw_value"]) * number(prior["scale"])
                        present = number(current["normalized_value"])
                        if previous <= 0:
                            row["status"] = "NONPOSITIVE_BASE"
                        else:
                            change = ((present - previous) / previous * 100).quantize(
                                Decimal("0.1"), rounding=ROUND_HALF_UP)
                            row.update({
                                "status": "COMPUTED", "growth_percent": str(change),
                                "formula": "(current_normalized-prior_raw*prior_scale)/(prior_raw*prior_scale)*100",
                                "input_row_ids": [row["current_row_id"], row["prior_row_id"]],
                                "current_source_url": current["source_url"],
                                "current_source_sha256": current["source_sha256"],
                                "prior_source_url": prior["source_url"],
                                "prior_source_sha256": prior["source_sha256"],
                                "basis_check": "EXPLICIT_MATCH" if prior.get("adjustment_basis") == current.get("adjustment_basis")
                                               else "SAME_SOURCE_COMPARISON_TABLE",
                                "limitation": "Source values may be rounded; first availability and market consensus unverified",
                            })
                output.append(row)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=RESULTS / "growth_calculations.json")
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    cards = json.loads((RESULTS / "company_cards.json").read_text(encoding="utf-8"))
    sources = json.loads((RESULTS / "source_manifest.json").read_text(encoding="utf-8"))
    calculations = derive(cards, sources)
    output = {"schema": "earnings_growth_calculations_v1", "as_of_utc": cards["as_of_utc"],
              "scope": "THREE_CORE_METRICS_SAME_BASIS_QUARTERS_ONLY",
              "calculations": calculations,
              "computed": sum(row["status"] == "COMPUTED" for row in calculations),
              "not_computed": sum(row["status"] != "COMPUTED" for row in calculations)}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"computed={output['computed']} not_computed={output['not_computed']}")


if __name__ == "__main__":
    main()
