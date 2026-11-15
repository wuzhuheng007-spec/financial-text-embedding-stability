"""Prepare one-key, at-most-15-minute private moomoo WebAPI probe config.

Offline only: reads the official basicinfo receipt and existing local SQLite.
It never registers OAuth, contacts moomoo, or claims an account entitlement.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .moomoo_ws import validate_live_config
from .service import load_config
from .store import Store
from .watchlist import load_watchlist, requested_us_subscription_order


REPO_ROOT = Path(__file__).resolve().parents[2]
MAPPING = REPO_ROOT / "results/watchlist_live_20261115/security_mapping_redacted.json"
TEMPLATE = REPO_ROOT / "configs/market_observer_v1.live.template.json"
OUTPUT = REPO_ROOT / "artifacts/market_observer_live/probe_tqqq.json"


def prepare() -> dict:
    mapping = json.loads(MAPPING.read_text(encoding="utf-8"))
    rows = [row for row in mapping.get("rows", [])
            if row.get("requested_symbol") == "TQQQ"
            and row.get("provider_code") == "US.TQQQ"
            and row.get("provider_market") == "US"
            and row.get("identity_scope") == "CURRENT_ONLY"]
    if len(rows) != 1:
        raise ValueError("one exact current US.TQQQ official basicinfo row is required")
    config = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    if config.get("feed_mode") != "LIVE_PUSH" or config.get("quote_push_verified") is not False:
        raise ValueError("probe template must remain unverified LIVE_PUSH")
    config["observation_pool"] = requested_us_subscription_order(load_watchlist(
        REPO_ROOT / config["watchlist_path"]))
    live = config["live_push"]
    live["mapping_receipt_path"] = str(MAPPING.relative_to(REPO_ROOT)).replace("\\", "/")
    live["mapping_receipt_sha256"] = hashlib.sha256(MAPPING.read_bytes()).hexdigest()
    live["subscriptions"] = [{"requested_symbol": "TQQQ", "provider_code": "US.TQQQ",
                              "instrument_id": "US.TQQQ", "market": "US", "kind": "QUOTE",
                              "verification_status": "VERIFIED_EXACT_PROVIDER_CODE_MARKET"}]
    validate_live_config(live, repo_root=REPO_ROOT)
    db_path = REPO_ROOT / config["db_path"]
    with Store(db_path) as store:
        row = store.connection.execute(
            "SELECT identity_status FROM instruments WHERE instrument_id=?", ("US.TQQQ",)).fetchone()
        if not row or row["identity_status"] != "VERIFIED":
            raise ValueError("US.TQQQ official mapping is not imported into private observer database")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    if OUTPUT.exists():
        raise FileExistsError("existing private probe config is preserved; inspect before creating a new version")
    OUTPUT.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    load_config(OUTPUT)
    return {"config_path": str(OUTPUT), "subscription_key_cap": 1,
            "requested_symbol": "TQQQ", "provider_code": "US.TQQQ",
            "max_runtime_minutes": config["max_runtime_minutes"],
            "entitlement_state": "BOUNDED_PROBE_NOT_YET_VERIFIED",
            "provider_calls": 0}


if __name__ == "__main__":
    print(json.dumps(prepare(), ensure_ascii=False, sort_keys=True))
