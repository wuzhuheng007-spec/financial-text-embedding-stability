"""Bounded five-second observation of the owner's local cache; no supplier calls.

Run manually during a US market session. A closed-market invocation is only a
short precheck and must never be described as open-market acceptance.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .cli import load_config
from .collector import ROOT, owner_id, under_root
from .store import read_personal_cache, utc_now


MARKET_PHASE = {0: "UNKNOWN", 3: "REGULAR", 5: "REGULAR", 6: "CLOSED",
                8: "PREMARKET", 9: "PREMARKET", 10: "AFTER_HOURS",
                11: "AFTER_HOURS_END", 12: "UNKNOWN", 33: "OVERNIGHT",
                34: "OVERNIGHT", 37: "OVERNIGHT"}
OFFICIAL_ENUM_URL = "https://open.moomoo.com/api/quote/naming-dictionary"


def aware_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except ValueError:
        return None


def market_phase(status_path: Path, observed_at: str) -> tuple[str, int | None, str]:
    now = aware_time(observed_at)
    # The US equity regular session never runs on a local Saturday or Sunday.
    # This clock fallback avoids treating a stale last market-state push as live.
    if now and now.astimezone(ZoneInfo("America/New_York")).weekday() >= 5:
        return "CLOSED", None, "US_EASTERN_WEEKEND_CLOCK_NOT_PROVIDER_PUSH"
    try:
        state = json.loads(status_path.read_text(encoding="utf-8"))
        stamp = aware_time(state.get("observed_at_utc"))
        if not stamp or not now or abs((now-stamp).total_seconds()) > 30:
            return "UNKNOWN", None, "MARKET_STATUS_STALE"
        code = state.get("market_state_codes", {}).get("US")
        if type(code) is not int:
            return "UNKNOWN", None, "US_STATE_MISSING"
        return MARKET_PHASE.get(code,"UNKNOWN"), code, "CACHED_OFFICIAL_ENUM"
    except (OSError, ValueError, TypeError, AttributeError):
        return "UNKNOWN", None, "MARKET_STATUS_UNAVAILABLE"


def snapshot(cache: Path, user_id: int, status_path: Path) -> dict:
    data = read_personal_cache(cache,user_id,news_limit=1)
    state = next((x for x in data["states"] if x["provider"] == "moomoo"),{})
    observed = utc_now()
    phase, code, phase_basis = market_phase(status_path,observed)
    return {"observed_at_utc":observed,"state":state.get("state"),
            "worker_alive":state.get("worker_alive"),"heartbeat_at":state.get("heartbeat_at"),
            "last_push_at":state.get("last_push_at"),
            "last_quote_saved_at":state.get("last_quote_saved_at"),
            "duplicate_quote_packets":state.get("duplicate_quote_packets"),
            "market_phase":phase,"market_code":code,"market_phase_basis":phase_basis,
            "quotes":{x["symbol"]:{"provider_time":x["provider_time"],
                                   "received_at":x["received_at"]} for x in data["quotes"]}}


def summarize(samples: list[dict]) -> dict:
    if not samples:
        raise ValueError("At least one cache sample is required")
    symbols = sorted({symbol for sample in samples for symbol in sample["quotes"]})
    per_symbol = {}
    for symbol in symbols:
        advances = 0
        last_received = None
        prior = None
        for sample in samples:
            quote = sample["quotes"].get(symbol)
            if not quote:
                continue
            stamp = aware_time(quote.get("provider_time"))
            if prior and stamp and stamp > prior:
                advances += 1
            if stamp and (prior is None or stamp > prior):
                prior = stamp
            received = aware_time(quote.get("received_at"))
            if received and (not last_received or received > last_received):
                last_received = received
        per_symbol[symbol] = {"observed_source_time_advances":advances,
                              "latest_received_at":last_received.isoformat() if last_received else None,
                              "latest_source_at":prior.isoformat() if prior else None,
                              "delay_status":"UNKNOWN"}
    heartbeat_intervals = []
    state_changes = []
    disconnections = []
    disconnect_start = None
    prior_state = None
    prior_heartbeat = None
    for sample in samples:
        state = sample.get("state")
        if state != prior_state:
            state_changes.append({"at":sample["observed_at_utc"],"from":prior_state,"to":state})
        disconnected = state in {"DISCONNECTED","AUTH_EXPIRED"} or sample.get("worker_alive") == 0
        if disconnected and disconnect_start is None:
            disconnect_start = sample["observed_at_utc"]
        if not disconnected and disconnect_start is not None:
            disconnections.append({"from":disconnect_start,"to":sample["observed_at_utc"]})
            disconnect_start = None
        heartbeat = aware_time(sample.get("heartbeat_at"))
        if heartbeat and prior_heartbeat and heartbeat > prior_heartbeat:
            heartbeat_intervals.append((heartbeat-prior_heartbeat).total_seconds())
        if heartbeat and (prior_heartbeat is None or heartbeat > prior_heartbeat):
            prior_heartbeat=heartbeat
        prior_state=state
    if disconnect_start is not None:
        disconnections.append({"from":disconnect_start,"to":samples[-1]["observed_at_utc"],"right_censored":True})
    first, last=samples[0],samples[-1]
    phases=sorted({x["market_phase"] for x in samples})
    regular=any(x["market_phase"] == "REGULAR" for x in samples)
    before=first.get("duplicate_quote_packets")
    after=last.get("duplicate_quote_packets")
    return {"schema":"personal_market_open_acceptance_v12",
            "mode":"READ_ONLY_REAL_CACHE_OBSERVATION",
            "started_at_utc":first["observed_at_utc"],"ended_at_utc":last["observed_at_utc"],
            "sample_interval_seconds":5,"sample_count":len(samples),
            "market_phases_observed":phases,"market_state_reference":OFFICIAL_ENUM_URL,
            "open_market_acceptance":"OBSERVED_REGULAR_SESSION" if regular else "NOT_RUN_NO_REGULAR_SESSION",
            "source_time_progress_is_not_zero_latency":True,
            "per_symbol":per_symbol,"connection_state_changes":state_changes,
            "heartbeat_intervals_seconds":heartbeat_intervals,"disconnection_intervals":disconnections,
            "duplicate_packet_delta":after-before if isinstance(before,int) and isinstance(after,int) else None,
            "samples":samples}


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",type=Path,default=ROOT/'configs/quant_portal_personal_feeds_v11_20261116.json')
    parser.add_argument("--seconds",type=int,default=300)
    args=parser.parse_args()
    if not 30 <= args.seconds <= 900:
        parser.error("Observation must be 30 to 900 seconds")
    config=load_config(args.config)
    cache=under_root(config["cache_db"])
    user_id=owner_id(config)
    status_path=ROOT/'apps/quant_portal/var/moomoo_personal_status.json'
    samples=[]
    deadline=time.monotonic()+args.seconds
    while True:
        samples.append(snapshot(cache,user_id,status_path))
        remaining=deadline-time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(5,remaining))
    result=summarize(samples)
    path=ROOT/'apps/quant_portal/var'/('market_open_acceptance_v12_'+utc_now().replace(':','').replace('-','')+'.json')
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({"private_receipt":str(path),"open_market_acceptance":result["open_market_acceptance"],
                      "phases":result["market_phases_observed"],"sample_count":len(samples),
                      "symbols":len(result["per_symbol"]),"source_times_advanced":sum(x["observed_source_time_advances"]
                      for x in result["per_symbol"].values()),"duplicate_packet_delta":result["duplicate_packet_delta"]},ensure_ascii=False))


if __name__ == "__main__":
    main()
