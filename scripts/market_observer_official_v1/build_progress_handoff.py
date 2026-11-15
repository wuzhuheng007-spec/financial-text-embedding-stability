"""Build a static, redacted page handoff from public official-corpus receipts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(*, corpus_path: Path, comparison_path: Path, price_audit_path: Path,
          output_path: Path) -> dict:
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
    price = json.loads(price_audit_path.read_text(encoding="utf-8"))
    if corpus["window"] != ["2025-04-01", "2026-07-31"]:
        raise ValueError("corpus window differs from frozen round")
    if price["status"] != "READ_ONLY_RETROSPECTIVE_AUDIT":
        raise ValueError("price audit status is unexpected")
    methods = {}
    for symbol in ("US.NVDA", "US.MU"):
        row = comparison["symbols"][symbol]
        fitted = row.get("comparison") or {}
        methods[symbol] = {
            "common_train_n": row.get("qualified_train_n"),
            "common_validation_n": row.get("qualified_validation_n"),
            "excluded_count": row.get("excluded_count"),
            "status": row.get("comparison_status"),
            "validation": {method: fitted.get(method, {}).get("validation")
                           for method in ("A", "B", "C")},
        }
    handoff = {
        "schema": "dot_official_corpus_progress_handoff_v1",
        "mode": "STATIC_REDACTED_HANDOFF_NOT_CONNECTED",
        "page_status": "NOT_UPDATED_NO_EXISTING_DEPLOYMENT_BINDING",
        "historical_window": corpus["window"],
        "price": {"scope": "saved TQQQ/NVDA/MU historical daily bars; no new price retrieval",
                  "audit_status": price["status"],
                  "audited_prior_rows": sum(x["audited_prediction_rows"] for x in price["symbols"].values()),
                  "historic_asof_availability": "UNVERIFIED"},
        "official_corpus": {
            "sec_filings_planned": corpus["planned_sec_filings"],
            "sec_full_bodies": corpus["sec_full_bodies"],
            "sec_earnings_exhibit_bodies": corpus["earnings_exhibit_full_bodies"],
            "fomc_statements_expected": corpus["fomc_statements_expected"],
            "fomc_full_bodies": corpus["fomc_full_bodies"],
            "new_http_requests": corpus["new_http_requests_total"],
            "monthly_coverage": corpus["monthly_coverage"],
            "historical_first_availability_verified": corpus["historical_first_availability_verified"],
        },
        "strict_historical_availability": "NOT_ADMITTED_UNCHANGED",
        "retrospective_announcement_date_proxy": {
            "status": "DEVELOPMENT_ONLY",
            "methods": methods,
            "validation_reused": True,
            "june_july_test": "CONTAMINATED_NOT_RESCORED_THIS_ROUND",
        },
        "formal_m5": "NOT_RUN",
        "report_links": [
            "reports/2026-11-15_market_observer_official_v1.en.md",
            "reports/2026-11-15_market_observer_official_v1.zh.md",
            "results/market_observer_official_20261115/plan_execution.csv",
        ],
        "input_sha256": {"corpus": digest(corpus_path), "comparison": digest(comparison_path),
                         "price_audit": digest(price_audit_path)},
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(handoff, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    return handoff


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--price-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(corpus_path=args.corpus, comparison_path=args.comparison,
                   price_audit_path=args.price_audit, output_path=args.output)
    print(json.dumps({"mode": result["mode"], "page_status": result["page_status"],
                      "methods": {k: v["status"] for k, v in
                                  result["retrospective_announcement_date_proxy"]["methods"].items()}}))


if __name__ == "__main__":
    main()
