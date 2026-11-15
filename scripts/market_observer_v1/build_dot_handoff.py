"""Build a non-content, static dot handoff from published redacted receipts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(price_path: Path, etf_path: Path, news_path: Path,
          alignment_path: Path, baseline_path: Path, output_path: Path) -> dict:
    price, etf, news, alignment, baseline = map(_read, (
        price_path, etf_path, news_path, alignment_path, baseline_path))
    if alignment["strict_pit_eligible"] != 0 or baseline["C_news_incremental"] != \
            "NOT_RUN_NO_QUALIFIED_COMPLETE_POINT_IN_TIME_NEWS":
        raise ValueError("handoff contract expects unqualified news features")
    input_paths = (price_path, etf_path, news_path, alignment_path, baseline_path)
    result = {
        "schema": "dot_historical_research_progress_v1",
        "date_asia_shanghai": "2026-11-15",
        "mode": "STATIC_REDACTED_HANDOFF_NOT_CONNECTED",
        "historical_window": ["2025-04-01", "2026-07-31"],
        "latest_realtime_quote": None,
        "historical_price_coverage": {
            "pilot_adjusted_and_unadjusted": ["TQQQ", "NVDA", "MU"],
            "expanded_unadjusted": ["QQQ", "SOXX", "SPY"],
            "bars_per_security": 335,
            "missing_official_trading_dates": 0,
            "remaining_account_quota": None,
        },
        "historical_news_coverage": {
            "jin10_in_window_search_candidates": 7,
            "sec_filing_metadata_candidates": 41,
            "official_macro_clocks_aligned": 2,
            "strict_first_availability_eligible": alignment["strict_pit_eligible"],
            "missing_news_is_zero": False,
            "full_window_completeness": "UNKNOWN",
        },
        "experiment": {
            "A_train_majority": "RUN_RETROSPECTIVE",
            "B_price_volume": "RUN_RETROSPECTIVE",
            "C_price_volume_plus_news": "NOT_RUN",
            "formal_m5": "NOT_RUN",
            "independent_test": False,
            "validation_by_security": [
                {"security": symbol.removeprefix("US."),
                 "n": data["splits"]["validation"]["n"],
                 "A_accuracy": data["splits"]["validation"]["A_train_majority"]["accuracy"],
                 "A_balanced_accuracy": data["splits"]["validation"]["A_train_majority"]["balanced_accuracy"],
                 "B_accuracy": data["splits"]["validation"]["B_price_volume"]["accuracy"],
                 "B_balanced_accuracy": data["splits"]["validation"]["B_price_volume"]["balanced_accuracy"]}
                for symbol, data in baseline["symbols"].items()
            ],
        },
        "report_links": [
            "reports/2026-11-15_market_observer_historical_v1.en.md",
            "reports/2026-11-15_market_observer_historical_v1.zh.md",
            "results/market_observer_historical_20261115/plan_execution.csv",
        ],
        "input_receipt_sha256": {path.name: _sha(path) for path in input_paths},
    }
    if len(price["series"]) != 6 or len(etf["series"]) != 3 or \
            alignment["input_events"] != 50:
        raise ValueError("source receipts differ from frozen handoff counts")
    if news.get("schema") is None:
        raise ValueError("news receipt has no schema")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result-dir", default="results/market_observer_historical_20261115",
                        type=Path)
    args = parser.parse_args()
    root = args.result_dir
    result = build(root / "price_probe_redacted.json", root / "etf_expansion_redacted.json",
                   root / "news_official_redacted.json", root / "event_alignment_redacted.json",
                   root / "price_direction_v3_redacted.json",
                   root / "dot_progress_redacted.json")
    print(json.dumps({"mode": result["mode"],
                      "validation_securities": len(result["experiment"]["validation_by_security"])}))


if __name__ == "__main__":
    main()
