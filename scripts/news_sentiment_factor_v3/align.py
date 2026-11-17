"""Training-window price/action alignment with explicit half-day and time rules."""
from __future__ import annotations

import json
import math
from bisect import bisect_right
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.news_sentiment_factor_v2.evaluate import _first_json_array_objects
from .source import CONFIG, PRIVATE, PUBLIC, digest, now, write_json

ROOT = Path(__file__).resolve().parents[2]
OLD = ROOT / "artifacts/market_observer_historical_20261115"
CALENDAR = OLD / "US_trading_days.raw.json"
FACTOR = PRIVATE / "event_factors_private.jsonl"
ALIGNMENT = PRIVATE / "event_alignment_private.jsonl"
NEW = {"AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "AMD", "AVGO", "INTC", "SNDK"}
WINDOW_END = "2026-03-31"


def calendar_days():
    raw = json.loads(CALENDAR.read_text(encoding="utf-8"))
    entries = [r for r in raw["data"]["trading_days"] if "2025-04-01" <= r["time"] <= WINDOW_END]
    dates = [r["time"] for r in entries]
    if dates != sorted(set(dates)) or not dates:
        raise ValueError("UNUSABLE_TRADING_CALENDAR")
    if any(r["trade_date_type"] not in ("WHOLE", "MORNING") for r in entries):
        raise ValueError("UNRECOGNIZED_TRADING_SESSION_TYPE")
    return entries


def raw_paths(symbol):
    if symbol in NEW:
        stem = PRIVATE / "moomoo_raw"
        return (stem / f"{symbol}_history_kline.json", stem / f"{symbol}_corporate_actions_rehab.json",
                stem / f"{symbol}_corporate_actions_dividends.json")
    return (OLD / f"US_{symbol}_unadjusted_page1.raw.json",
            OLD / f"US_{symbol}_rehab.raw.json", OLD / f"US_{symbol}_dividends.raw.json")


def price_bars(symbol, calendar):
    path, _, _ = raw_paths(symbol)
    if symbol in NEW:
        response = json.loads(path.read_text(encoding="utf-8"))
        if response.get("ret_code") != 0 or response.get("pagination", {}).get("has_more"):
            raise ValueError("HISTORICAL_PRICE_INCOMPLETE:" + symbol)
        rows = response["data"]["kline_list"]
    else:
        # Existing response continues to July 2026; parse only the first 251
        # training-window objects, never the April-July outcome bars.
        rows = _first_json_array_objects(path, len(calendar))
    by_date = {}
    for row in rows:
        date = str(row["date"])
        if len(date) != 8 or date > "20260331" or date < "20250401":
            raise ValueError("PRICE_DATE_OUTSIDE_FROZEN_TRAIN_WINDOW:" + symbol)
        formatted = f"{date[:4]}-{date[4:6]}-{date[6:]}"
        if formatted in by_date:
            raise ValueError("DUPLICATE_DAILY_BAR:" + symbol)
        by_date[formatted] = row
    return by_date


def action_dates(symbol):
    _, rehab_path, dividend_path = raw_paths(symbol)
    dates = set()
    paths = [(rehab_path, "rehabs", "ex_div_date"), (dividend_path, "dividend_list", "ex_date")]
    for path, field, date_field in paths:
        response = json.loads(path.read_text(encoding="utf-8"))
        if response.get("ret_code") != 0 or not isinstance(response.get("data", {}).get(field), list):
            return None
        for item in response["data"][field]:
            if not isinstance(item, dict):
                return None
            value = item.get(date_field)
            if not isinstance(value, str):
                return None
            date = value.replace("/", "-")
            try:
                datetime.strptime(date, "%Y-%m-%d")
            except ValueError:
                return None
            if "2025-04-01" <= date <= WINDOW_END:
                dates.add(date)
    return dates


def preflight():
    if ALIGNMENT.exists():
        raise FileExistsError("ALIGNED_LABELS_ALREADY_EXIST")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    factors = [json.loads(line) for line in FACTOR.read_text(encoding="utf-8").splitlines()]
    if len(factors) != 46 or any(x["filing_date"] > WINDOW_END for x in factors):
        raise ValueError("FACTOR_SCOPE_DRIFT")
    calendar = calendar_days()
    hashes, counts, action_status = {}, {}, {}
    for symbol in config["issuer_order"]:
        paths = raw_paths(symbol)
        hashes[symbol] = {p.name: digest(p) for p in paths}
        bars = price_bars(symbol, calendar)
        counts[symbol] = {"bars_in_training_window": len(bars),
                          "missing_calendar_dates": len(set(x["time"] for x in calendar) - set(bars))}
        action = action_dates(symbol)
        action_status[symbol] = {"status": "SOURCE_RESPONSE_PARSED" if action is not None else "UNKNOWN",
                                 "action_dates_in_window": len(action) if action is not None else None}
    receipt = {"schema": "sentiment_v3_price_preflight_redacted_1", "checked_at_utc": now(),
               "config_sha256": digest(CONFIG), "align_code_sha256": digest(Path(__file__)),
               "factors_sha256": digest(FACTOR), "calendar_sha256": digest(CALENDAR),
               "source_raw_hashes": hashes, "price_coverage": counts, "action_status": action_status,
               "trading_sessions": len(calendar), "half_sessions": [x["time"] for x in calendar if x["trade_date_type"] == "MORNING"],
               "labels_computed": False, "no_post_training_price_objects_parsed_for_NVDA_MU": True}
    write_json(PUBLIC / "price_preflight_redacted.json", receipt)
    return {"trading_sessions": len(calendar), "half_sessions": len(receipt["half_sessions"]),
            "symbols_complete_price": sum(x["missing_calendar_dates"] == 0 for x in counts.values()),
            "symbols_action_response_parsed": sum(x["status"] == "SOURCE_RESPONSE_PARSED" for x in action_status.values())}


def align():
    if ALIGNMENT.exists():
        raise FileExistsError("ALIGNED_LABELS_ALREADY_EXIST")
    pre = json.loads((PUBLIC / "price_preflight_redacted.json").read_text(encoding="utf-8"))
    if pre["config_sha256"] != digest(CONFIG) or pre["factors_sha256"] != digest(FACTOR) or pre["align_code_sha256"] != digest(Path(__file__)):
        raise ValueError("PRICE_PREFLIGHT_DRIFT")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    calendar = calendar_days()
    days = [x["time"] for x in calendar]
    whole = [x["time"] for x in calendar if x["trade_date_type"] == "WHOLE"]
    day_index = {d: i for i, d in enumerate(days)}
    bars = {s: price_bars(s, calendar) for s in config["issuer_order"]}
    actions = {s: action_dates(s) for s in config["issuer_order"]}
    factors = [json.loads(line) for line in FACTOR.read_text(encoding="utf-8").splitlines()]
    output = []
    exclusions = Counter()
    for factor in factors:
        row = {k: factor[k] for k in ("event_id", "symbol", "acceptance_utc", "filing_date", "source_sha256",
                                        "time_evidence", "lm_F1", "finbert_F1", "lm_F2", "finbert_F2", "F2_prior_event_ids", "document_count")}
        symbol = row["symbol"]
        acceptance_et = datetime.fromisoformat(row["acceptance_utc"]).astimezone(ZoneInfo("America/New_York"))
        row["acceptance_et_date"] = acceptance_et.date().isoformat()
        wait_pos = bisect_right(whole, row["acceptance_et_date"])
        row["full_wait_session"] = whole[wait_pos] if wait_pos < len(whole) else None
        entry_pos = day_index[row["full_wait_session"]] + 1 if row["full_wait_session"] else len(days)
        row["entry_date"] = days[entry_pos] if entry_pos < len(days) else None
        row["entry_session_type"] = calendar[entry_pos]["trade_date_type"] if row["entry_date"] else None
        prior = bars[symbol].get(row["full_wait_session"]) if row["full_wait_session"] else None
        if prior and float(prior["open"]) > 0 and float(prior["close"]) > 0 and float(prior["volume"]) > 0:
            row["prior_session_open_to_close_return"] = float(prior["close"]) / float(prior["open"]) - 1
            row["prior_session_log_volume"] = math.log(float(prior["volume"]))
        else:
            row["prior_session_open_to_close_return"] = None
            row["prior_session_log_volume"] = None
        for horizon in (1, 5):
            reason = None
            exit_pos = entry_pos + horizon
            exit_date = days[exit_pos] if exit_pos < len(days) else None
            if not row["entry_date"] or not exit_date:
                reason = "CROSSES_TRAIN_WINDOW_END_OR_NO_FULL_WAIT_SESSION"
            elif actions[symbol] is None:
                reason = "CORPORATE_ACTION_SOURCE_UNKNOWN"
            elif any(row["entry_date"] <= d <= exit_date for d in actions[symbol]):
                reason = "CORPORATE_ACTION_IN_RETURN_WINDOW"
            elif row["prior_session_open_to_close_return"] is None:
                reason = "PRICE_FEATURE_MISSING"
            else:
                entry_bar = bars[symbol].get(row["entry_date"])
                exit_bar = bars[symbol].get(exit_date)
                if not entry_bar or not exit_bar:
                    reason = "ENTRY_OR_EXIT_PRICE_MISSING"
                elif float(entry_bar["open"]) <= 0 or float(exit_bar["open"]) <= 0:
                    reason = "INVALID_OPEN_PRICE"
            row[f"exit_{horizon}d"] = exit_date
            row[f"return_{horizon}d"] = float(bars[symbol][exit_date]["open"]) / float(bars[symbol][row["entry_date"]]["open"]) - 1 if not reason else None
            row[f"exclusion_{horizon}d"] = reason
            if reason:
                exclusions[f"{horizon}d:{reason}"] += 1
        output.append(row)
    ALIGNMENT.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in output), encoding="utf-8")
    receipt = {"schema": "sentiment_v3_alignment_redacted_1", "completed_at_utc": now(),
               "config_sha256": digest(CONFIG), "align_code_sha256": digest(Path(__file__)),
               "price_preflight_sha256": digest(PUBLIC / "price_preflight_redacted.json"),
               "event_alignment_sha256": digest(ALIGNMENT), "events": len(output),
               "eligible_1d": sum(x["return_1d"] is not None for x in output),
               "eligible_5d": sum(x["return_5d"] is not None for x in output),
               "exclusions": dict(exclusions), "half_session_step_rule": "MORNING counts as one actual session",
               "strict_first_visibility_proven": False}
    write_json(PUBLIC / "alignment_redacted.json", receipt)
    return {k: receipt[k] for k in ("events", "eligible_1d", "eligible_5d", "exclusions")}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("preflight", "align"))
    args = parser.parse_args()
    print(json.dumps(preflight() if args.mode == "preflight" else align()))
