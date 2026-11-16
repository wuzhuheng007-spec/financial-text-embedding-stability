"""Build a price-free, owner-identifier-free v1.3 session acceptance receipt."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from zoneinfo import ZoneInfo
from datetime import datetime, timezone

from .cli import load_config
from .collector import ROOT, owner_id, under_root
from .session_quotes import SESSIONS
from .store import read_personal_cache

SYMBOLS = ("TQQQ","QQQ","SOXX","SPY","NVDA","MU")
PRIVATE = ROOT / "apps/quant_portal/var/v13_20261116"
OUTPUT = ROOT / "results/quant_portal_personal_sessions_v13_20261116/runtime_redacted.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    config_path = ROOT / "configs/quant_portal_personal_feeds_v11_20261116.json"
    config = load_config(config_path)
    owner = owner_id(config)
    view = read_personal_cache(under_root(config["cache_db"]), owner, news_limit=1)
    observation_path = PRIVATE / "session_acceptance_private.json"
    observation = json.loads(observation_path.read_text(encoding="utf-8"))
    probe_path = PRIVATE / "six_stock_quote_probe.json"
    probe = json.loads(probe_path.read_text(encoding="utf-8"))
    by_row = {(x["symbol"],x["session"]):x for x in view["session_quotes"]}
    cells = []
    for symbol in SYMBOLS:
        for session in SESSIONS:
            row = by_row.get((symbol,session))
            observed = observation["per_symbol_session"][f"{symbol}:{session}"]
            cells.append({"symbol":symbol,"session":session,
                          "snapshot_observed":row is not None,
                          "source_time_known":bool(row and row["source_at"]),
                          "source_time_advances_in_window":observed["source_time_advances"],
                          "source_channel":row["source_channel"] if row else None,
                          "trade_date_known":bool(row and row["trade_date"]),
                          "currency_known":bool(row and row["currency"]),
                          "delay_known":bool(row and row["delay_status"] != "UNKNOWN")})
    states = {x["provider"]:{"state":x["state"],"worker_alive":x["worker_alive"],
                "last_success_at":x["last_success_at"]} for x in view["states"]
              if x["provider"] in {"moomoo","moomoo_snapshot","jin10"}}
    with closing(sqlite3.connect(f"file:{under_root(config['portal_db']).as_posix()}?mode=ro",uri=True)) as db:
        account_counts = {"accounts":db.execute("SELECT COUNT(*) FROM auth_user").fetchone()[0],
                          "watchlist":db.execute("SELECT COUNT(*) FROM core_watchlistitem").fetchone()[0],
                          "settings":db.execute("SELECT COUNT(*) FROM core_userpreferences").fetchone()[0],
                          "historical_fact_cards":db.execute("SELECT COUNT(*) FROM core_publicfactcard").fetchone()[0]}
    with closing(sqlite3.connect(f"file:{under_root(config['cache_db']).as_posix()}?mode=ro",uri=True)) as db:
        current_raw = db.execute("""SELECT COUNT(*),SUM(CASE WHEN r.sha256 IS NOT NULL THEN 1 ELSE 0 END)
          FROM session_quotes q LEFT JOIN session_raw_responses r ON r.sha256=q.raw_sha256
          WHERE q.owner_id=? AND q.id=(SELECT q2.id FROM session_quotes q2
            WHERE q2.owner_id=q.owner_id AND q2.symbol=q.symbol AND q2.session=q.session
            ORDER BY (q2.source_at IS NOT NULL) DESC,q2.source_at DESC,
              q2.parent_source_at_hint DESC,q2.received_at DESC,q2.id DESC LIMIT 1)""",(owner,)).fetchone()
    code_files = [
        "scripts/quant_personal_feed/session_quotes.py",
        "scripts/quant_personal_feed/session_poll.py",
        "scripts/quant_personal_feed/session_acceptance.py",
        "scripts/quant_personal_feed/store.py",
        "scripts/quant_personal_feed/collector.py",
        "scripts/quant_personal_feed/cli.py",
        "scripts/market_observer_v1/moomoo_ws.py",
        "apps/quant_portal/static/core/personal_feed.js",
        "configs/quant_portal_personal_sessions_v13_20261116.json",
        "configs/quant_portal_personal_feeds_v11_20261116.json",
    ]
    now = datetime.now(timezone.utc)
    receipt={"schema":"quant_portal_personal_sessions_v13_redacted",
             "checked_at_utc":now.isoformat(),
             "checked_at_shanghai":now.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
             "baseline_commit":"30d315263db784e5c9bacadfcc0fd2ea1323e33e",
             "six_symbol_rest_probe":{"http_status":probe["http_status"],
                                      "business_code":probe["business_code"],
                                      "rows":len(probe["rows"]),
                                      "raw_response_sha256":digest(PRIVATE/"six_stock_quote_raw.json"),
                                      "timestamp_note":"Private probe UTC label used local wall time; not used as UTC evidence"},
             "observation":{"start_at_utc":observation["start_at_utc"],
                            "end_at_utc":observation["end_at_utc"],
                            "elapsed_seconds":observation["elapsed_seconds"],
                            "cache_reads":observation["cache_reads"],
                            "snapshot_business_attempt_delta":observation["snapshot_business_attempt_delta"],
                            "provider_states_seen":observation["provider_states_seen"],
                            "private_receipt_sha256":digest(observation_path),
                            "input_sha256_at_start":observation.get("input_sha256_at_start")},
             "cells":cells,
             "totals":{"session_cells":len(cells),
                       "cells_with_values":sum(x["snapshot_observed"] for x in cells),
                       "cells_with_source_time":sum(x["source_time_known"] for x in cells),
                       "cells_with_source_time_advances":sum(x["source_time_advances_in_window"]>0 for x in cells),
                       "cells_with_official_trade_date":sum(x["trade_date_known"] for x in cells)},
             "latest_source_time_by_session_utc":{
                 session:max((x["source_at"] for x in view["session_quotes"]
                              if x["session"]==session and x["source_at"]),default=None)
                 for session in SESSIONS},
             "owner_data_counts":account_counts,
             "visible_session_raw_provenance":{"visible_cells":current_raw[0],
                                               "raw_response_present":current_raw[1]},
             "post_observation_change":"RAW_RESPONSE_RETENTION_ONLY; SOURCE_TIME_COMPARISON_UNCHANGED",
             "news_cached_items":view["news_total"],
             "desired_symbols":[x["symbol"] for x in view["selection"] if x["desired"]],
             "provider_states_at_receipt":states,
             "code_config_sha256":{name:digest(ROOT/name) for name in code_files},
             "private_db_uploaded":False,"raw_prices_uploaded":False,
             "model_fit_calls":0,"model_inference_calls":0,"trade_calls":0}
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    OUTPUT.write_bytes((json.dumps(receipt,ensure_ascii=False,indent=2)+"\n").encode("utf-8"))
    print(json.dumps({"receipt":str(OUTPUT.relative_to(ROOT)),"cells":len(cells),
                      "source_time_advancing_cells":receipt["totals"]["cells_with_source_time_advances"],
                      "provider_states":{k:v["state"] for k,v in states.items()}},ensure_ascii=False))


if __name__ == "__main__":
    main()
