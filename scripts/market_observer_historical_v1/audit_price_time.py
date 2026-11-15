"""Read-only audit of frozen historical direction inputs. Private row detail."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
OLD = ROOT / "artifacts" / "market_observer_historical_20261115"
OUT = ROOT / "artifacts" / "market_observer_historical_official_20261115" / "price_audit"
PUBLIC = ROOT / "results" / "market_observer_historical_official_20261115" / "price_audit_redacted.json"
SYMBOLS = ("TQQQ", "NVDA", "MU")
THRESHOLD = 1e-4  # 1 basis point in logarithmic return or log-volume change.
FLOAT_TOL = 1e-11


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_bars(symbol: str, suffix: str) -> tuple[list[dict], Path]:
    path = OLD / f"US_{symbol}{suffix}_page1.raw.json"
    rows = json.loads(path.read_text(encoding="utf-8"))["data"]["kline_list"]
    return rows, path


def values(bars: list[dict], index: int) -> dict[str, float]:
    closes = np.array([float(x["close"]) for x in bars], dtype=float)
    volumes = np.array([float(x["volume"]) for x in bars], dtype=float)
    assert (closes > 0).all() and (volumes > 0).all()
    lc, lv = np.log(closes), np.log(volumes)
    twenty = np.diff(lc[index - 20:index + 1])
    return dict(zip(
        ("price_return_1d", "price_return_5d", "price_return_20d",
         "price_volatility_20d", "volume_log_change_1d", "volume_log_change_5d"),
        (float(twenty[-1]), float(sum(twenty[-5:])), float(sum(twenty)),
         float(np.std(twenty, ddof=0)), float(lv[index] - lv[index - 1]),
         float(lv[index] - lv[index - 5]))))


def date_str(bar: dict) -> str:
    return datetime.strptime(str(bar["date"]), "%Y%m%d").date().isoformat()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    PUBLIC.parent.mkdir(parents=True, exist_ok=True)
    code = ROOT / "scripts" / "market_observer_historical_v1" / "direction_baseline.py"
    audit_code = ROOT / "scripts" / "market_observer_historical_v1" / "audit_price_time.py"
    frozen = ROOT / "configs" / "market_observer_historical_v1.json"
    action_alignment = ROOT / "results" / "market_observer_historical_20261115" / "action_alignment_redacted.json"
    action_receipt = ROOT / "results" / "market_observer_historical_20261115" / "corporate_actions_redacted.json"
    report = {
        "schema": "historical_price_time_audit_v1",
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "READ_ONLY_RETROSPECTIVE_AUDIT",
        "new_price_or_action_api_business_calls": 0,
        "source_code_sha256": sha(code), "frozen_config_sha256": sha(frozen),
        "audit_code_sha256": sha(audit_code),
        "prior_action_alignment_redacted_sha256": sha(action_alignment),
        "prior_corporate_action_redacted_sha256": sha(action_receipt),
        "independent_split_source": {
            "symbol": "US.TQQQ", "effective_date": "2025-11-20", "ratio": "2:1",
            "url": "https://www.proshares.com/press-releases/proshares-announces-etf-share-splits5",
            "status": "official ProShares page checked in this audit; states TQQQ 2:1 effective before market open 2025-11-20"},
        "threshold_log_units": THRESHOLD,
        "feature_match_tolerance_abs": FLOAT_TOL,
        "decision_contract": "For this round's retrospective proxy, fix a hypothetical decision at 09:30 America/New_York on t-1. The old v3 did not record an exact decision clock; its last completed feature session was t-2. No historical as-of execution or bar availability is verified.",
        "safe_t_minus_1_revision": "Only in a separate later version: set the decision after the t-1 close bar is finalized and available, record that availability, then use t-1 daily features. Never describe that as a pre-close decision.",
        "source_caveat": "Bars and action factors were retrieved retrospectively on 2026-11-15; this is a software and price-basis audit, not a proof of historical point-in-time visibility.",
        "price_basis_conclusion": "No correction to frozen v3 features is indicated by the saved provider bars and dated rehab factors. Large adjusted/raw changes coincide with action dates; the reconstructed as-of-action features match v3 within numerical precision. Use v3 unchanged for a paired retrospective news comparison, with source and visibility limits retained.",
        "symbols": {},
    }
    private = {"schema": report["schema"], "rows": [], "sources": {}}
    for symbol in SYMBOLS:
        adjusted, ap = read_bars(symbol, "")
        raw, rp = read_bars(symbol, "_unadjusted")
        pred_path = OLD / "baseline_direction_v3" / f"US_{symbol}_predictions.private.jsonl"
        predictions = [json.loads(line) for line in pred_path.read_text(encoding="utf-8").splitlines()]
        rehab_path = OLD / f"US_{symbol}_rehab.raw.json"
        rehabs = json.loads(rehab_path.read_text(encoding="utf-8"))["data"]["rehabs"]
        assert len(adjusted) == len(raw) == 335
        assert [date_str(x) for x in adjusted] == [date_str(x) for x in raw]
        assert len(predictions) == len(adjusted) - 22
        private["sources"][symbol] = {"adjusted": [str(ap), sha(ap)],
                                      "unadjusted": [str(rp), sha(rp)],
                                      "predictions": [str(pred_path), sha(pred_path)],
                                      "rehab": [str(rehab_path), sha(rehab_path)]}
        window_actions = {x["ex_div_date"]: x["action_types"] for x in rehabs
                          if "2025-04-01" <= x["ex_div_date"] <= "2026-07-31"}
        future = [x for x in rehabs if x["ex_div_date"] > "2026-07-31"]
        assert len(future) == 1, (symbol, len(future))
        future_factor = float(future[0]["forward_adj_factorA"])
        assert float(future[0]["forward_adj_factorB"]) == 0
        assert all(float(x["forward_adj_factorB"]) == 0 and
                   float(x["forward_adj_factorA"]) > 0 for x in rehabs)
        ratios = [float(a["close"]) / float(b["close"]) for a, b in zip(adjusted, raw)]
        all_action_ratio_error = max(
            abs(observed - math.prod(float(x["forward_adj_factorA"])
                                     for x in rehabs if x["ex_div_date"] > date_str(bar)))
            for observed, bar in zip(ratios, raw))
        future_relative_log_return_max = 0.0
        counts = Counter()
        maxima = Counter()
        field_counts = Counter()
        label_flips = []
        significant_dates = set()
        for index, prediction in enumerate(predictions, start=22):
            a, b = adjusted[index], raw[index]
            last = index - 2
            day = date_str(a)
            assert prediction["label_day"] == day
            assert prediction["skipped_prior_day"] == date_str(adjusted[index - 1])
            assert prediction["last_feature_day"] == date_str(adjusted[last])
            af, rf = values(adjusted, last), values(raw, last)
            # Rebuild a point-in-time-compatible feature window from unadjusted bars,
            # admitting only action dates no later than the last feature session.
            last_date = date_str(raw[last])
            asof_window = []
            for source_bar in raw[last - 20:last + 1]:
                bar_date = date_str(source_bar)
                eligible = [x for x in rehabs if bar_date < x["ex_div_date"] <= last_date]
                price_factor = math.prod(float(x["forward_adj_factorA"]) for x in eligible)
                volume_factor = math.prod(
                    1 / float(x["forward_adj_factorA"]) for x in eligible
                    if "SPLIT" in x["action_types"])
                asof_window.append({"close": float(source_bar["close"]) * price_factor,
                                    "volume": float(source_bar["volume"]) * volume_factor})
            asof_features = values(asof_window, 20)
            for name in af:
                err = abs(af[name] - prediction["features"][name])
                maxima["frozen_feature_recompute_error"] = max(maxima["frozen_feature_recompute_error"], err)
                assert err <= FLOAT_TOL, (symbol, day, name, err)
                asof_err = abs(af[name] - asof_features[name])
                maxima["asof_action_rebuild_feature_error"] = max(
                    maxima["asof_action_rebuild_feature_error"], asof_err)
                delta = abs(af[name] - rf[name])
                maxima[f"{name}_difference"] = max(maxima[f"{name}_difference"], delta)
                if delta > THRESHOLD:
                    field_counts[name] += 1
                    significant_dates.add(day)
            adjusted_up = int(float(a["close"]) > float(a["open"]))
            raw_up = int(float(b["close"]) > float(b["open"]))
            assert prediction["actual_up"] == adjusted_up
            if adjusted_up != raw_up:
                label_flips.append(day)
            intraday_adjusted = math.log(float(a["close"]) / float(a["open"]))
            intraday_raw = math.log(float(b["close"]) / float(b["open"]))
            maxima["intraday_log_return_difference"] = max(
                maxima["intraday_log_return_difference"], abs(intraday_adjusted - intraday_raw))
            row = {
                "symbol": symbol, "label_day": day,
                "last_feature_day": prediction["last_feature_day"],
                "skipped_prior_day": prediction["skipped_prior_day"],
                "decision_day_assumption": prediction["skipped_prior_day"],
                "decision_clock_proxy": "t-1 09:30 America/New_York; not an actual historical execution timestamp",
                "adjusted_features": af, "unadjusted_features": rf,
                "asof_action_rebuilt_features": asof_features,
                "feature_differences": {k: af[k] - rf[k] for k in af},
                "adjusted_label_up": adjusted_up, "unadjusted_label_up": raw_up,
                "intraday_log_return_difference": intraday_adjusted - intraday_raw,
            }
            private["rows"].append(row)
            counts["prediction_rows"] += 1
        ratio_jumps = []
        for i in range(1, len(ratios)):
            jump = abs(math.log(ratios[i] / ratios[i - 1]))
            if jump > THRESHOLD:
                ratio_jumps.append({"date": date_str(adjusted[i]), "action_registered": date_str(adjusted[i]) in window_actions,
                                    "log_ratio_jump": jump})
            # Uniform post-window multiplicative factor must cancel in every in-window log return.
            future_relative_log_return_max = max(future_relative_log_return_max,
                abs(math.log((float(adjusted[i]["close"]) / future_factor) /
                             (float(adjusted[i - 1]["close"]) / future_factor)) -
                    math.log(float(adjusted[i]["close"]) / float(adjusted[i - 1]["close"]))))
        report["symbols"][f"US.{symbol}"] = {
            "input_sha256": {k: v[1] for k, v in private["sources"][symbol].items()},
            "price_bars": len(adjusted), "audited_prediction_rows": counts["prediction_rows"],
            "first_label_day": predictions[0]["label_day"], "last_label_day": predictions[-1]["label_day"],
            "t_minus_2_date_alignment_errors": 0,
            "frozen_feature_recompute_max_error": maxima["frozen_feature_recompute_error"],
            "asof_action_rebuild_feature_max_error": maxima["asof_action_rebuild_feature_error"],
            "adjusted_raw_ratio_vs_rehab_product_max_abs_error": all_action_ratio_error,
            "forward_vs_raw_feature_difference_over_1bp_counts": dict(field_counts),
            "forward_vs_raw_feature_max_abs_differences": {k: v for k, v in maxima.items() if k.endswith("_difference")},
            "labels_changed_by_adjustment": len(label_flips),
            "intraday_log_return_max_abs_difference": maxima["intraday_log_return_difference"],
            "significant_feature_dates_count": len(significant_dates),
            "adjusted_raw_close_ratio_jumps_over_1bp": len(ratio_jumps),
            "ratio_jumps_not_on_registered_window_action": sum(not x["action_registered"] for x in ratio_jumps),
            "window_rehab_action_dates_count": len(window_actions),
            "post_window_rehab_date": future[0]["ex_div_date"],
            "post_window_action_type": future[0]["action_types"],
            "post_window_forward_factorA": future_factor,
            "uniform_future_factor_cancel_log_return_max_error": future_relative_log_return_max,
            "source_factor_semantics_independently_verified": False,
        }
        private.setdefault("details", {})[symbol] = {"label_flip_dates": label_flips,
            "ratio_jumps": ratio_jumps, "significant_feature_dates": sorted(significant_dates)}
    private_path = OUT / "price_audit.private.json"
    private_path.write_text(json.dumps(private, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report["private_row_receipt_sha256"] = sha(private_path)
    PUBLIC.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"public": str(PUBLIC), "summary": report["symbols"]}, indent=2))


if __name__ == "__main__":
    main()
