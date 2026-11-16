"""Manual read-only cache observation during a real US market session."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .cli import load_config
from .collector import ROOT, owner_id, under_root
from .store import read_personal_cache, utc_now


def snapshot(cache: Path, user_id: int) -> dict:
    data = read_personal_cache(cache,user_id,news_limit=1)
    moomoo = next((x for x in data["states"] if x["provider"] == "moomoo"),{})
    return {"observed_at_utc":utc_now(),"state":moomoo.get("state"),
            "worker_alive":moomoo.get("worker_alive"),
            "heartbeat_at":moomoo.get("heartbeat_at"),
            "last_push_at":moomoo.get("last_push_at"),
            "last_quote_saved_at":moomoo.get("last_quote_saved_at"),
            "duplicate_quote_packets":moomoo.get("duplicate_quote_packets"),
            "quotes":{x["symbol"]:{"provider_time":x["provider_time"],
                                   "received_at":x["received_at"],
                                   "market_state":x["market_state"]} for x in data["quotes"]}}


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
    before=snapshot(cache,user_id)
    time.sleep(args.seconds)
    after=snapshot(cache,user_id)
    symbols=sorted(set(before["quotes"]) | set(after["quotes"]))
    result={"schema":"personal_market_open_acceptance_v11",
            "mode":"READ_ONLY_REAL_CACHE_OBSERVATION",
            "started_at_utc":before["observed_at_utc"],
            "ended_at_utc":after["observed_at_utc"],
            "market_session_assessment":"REVIEW_PROVIDER_MARKET_STATE_WITH_OFFICIAL_ENUM",
            "before":{key:value for key,value in before.items() if key != "quotes"},
            "after":{key:value for key,value in after.items() if key != "quotes"},
            "per_symbol":{symbol:{"source_time_before":before["quotes"].get(symbol,{}).get("provider_time"),
                                  "source_time_after":after["quotes"].get(symbol,{}).get("provider_time"),
                                  "source_time_advanced":bool(before["quotes"].get(symbol) and after["quotes"].get(symbol)
                                      and after["quotes"][symbol]["provider_time"] > before["quotes"][symbol]["provider_time"]),
                                  "market_state_after":after["quotes"].get(symbol,{}).get("market_state")}
                          for symbol in symbols}}
    path=ROOT/'apps/quant_portal/var'/('market_open_acceptance_'+utc_now().replace(':','').replace('-','')+'.json')
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({"private_receipt":str(path),"market_session_assessment":result["market_session_assessment"],
                      "symbols":symbols,"source_times_advanced":sum(x["source_time_advanced"]
                      for x in result["per_symbol"].values())},ensure_ascii=False))


if __name__ == "__main__":
    main()
