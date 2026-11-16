"""Read only the private cache to observe v1.3 quote sessions for at most 15 minutes."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .cli import load_config
from .collector import ROOT, owner_id, under_root
from .session_quotes import SESSIONS
from .store import read_personal_cache, utc_now


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path,
        default=ROOT / "configs/quant_portal_personal_feeds_v11_20261116.json")
    parser.add_argument("--seconds", type=int, default=300)
    args = parser.parse_args()
    if not 5 <= args.seconds <= 900:
        parser.error("Observation must be 5–900 seconds")
    config = load_config(args.config)
    code_paths = (Path(__file__), ROOT / "scripts/quant_personal_feed/store.py",
                  ROOT / "scripts/quant_personal_feed/session_quotes.py", args.config)
    input_sha256 = {str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in code_paths}
    user_id = owner_id(config)
    cache_path = under_root(config["cache_db"])
    counts: dict[str, dict[str, int | str | None]] = {
        f"{symbol}:{session}": {"visible_samples": 0, "source_time_advances": 0,
                                  "first_source_at": None, "last_source_at": None}
        for symbol in ("TQQQ","QQQ","SOXX","SPY","NVDA","MU") for session in SESSIONS}
    states: dict[str, set[str]] = {name:set() for name in ("moomoo","moomoo_snapshot","jin10")}
    start = time.monotonic()
    start_at = utc_now()
    readings = 0
    attempts = []
    while True:
        snapshot = read_personal_cache(cache_path,user_id,news_limit=1)
        readings += 1
        for state in snapshot.get("states",[]):
            if state["provider"] in states:
                states[state["provider"]].add(state["state"])
                if state["provider"] == "moomoo_snapshot":
                    attempts.append(int(state.get("business_attempts_today") or 0))
        for row in snapshot.get("session_quotes",[]):
            key = f"{row['symbol']}:{row['session']}"
            if key not in counts:
                continue
            cell = counts[key]
            cell["visible_samples"] += 1
            stamp = row.get("source_at")
            if stamp:
                parsed = datetime.fromisoformat(stamp.replace("Z","+00:00"))
                prior = cell["last_source_at"]
                if prior and parsed > datetime.fromisoformat(prior.replace("Z","+00:00")):
                    cell["source_time_advances"] += 1
                if cell["first_source_at"] is None:
                    cell["first_source_at"] = stamp
                cell["last_source_at"] = stamp
        elapsed = time.monotonic()-start
        if elapsed >= args.seconds:
            break
        time.sleep(min(5,args.seconds-elapsed))
    ended_at = utc_now()
    receipt = {"schema":"personal_session_acceptance_v13", "start_at_utc":start_at,
               "end_at_utc":ended_at,
               "input_sha256_at_start":input_sha256,
               "start_at_shanghai":datetime.fromisoformat(start_at.replace("Z","+00:00"))
                   .astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
               "elapsed_seconds":round(time.monotonic()-start,3),"cache_reads":readings,
               "provider_states_seen":{k:sorted(v) for k,v in states.items()},
               "snapshot_business_attempt_delta":attempts[-1]-attempts[0] if attempts else None,
               "per_symbol_session":counts,"vendor_requests_from_this_script":0}
    output = ROOT / "apps/quant_portal/var/v13_20261116/session_acceptance_private.json"
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"elapsed_seconds":receipt["elapsed_seconds"],
                      "cache_reads":readings,"snapshot_attempt_delta":receipt["snapshot_business_attempt_delta"],
                      "session_fields_visible":sum(x["visible_samples"]>0 for x in counts.values()),
                      "fields_with_source_time_advances":sum(x["source_time_advances"]>0 for x in counts.values()),
                      "private_receipt":str(output)},ensure_ascii=False))


if __name__ == "__main__":
    main()
