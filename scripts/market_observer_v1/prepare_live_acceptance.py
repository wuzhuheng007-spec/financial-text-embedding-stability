"""Prepare a capped 19-US watchlist WebSocket acceptance config offline.

Requires an actual successful account-quota GET and combined current official
symbol receipt. This selects in frozen watchlist order; it cannot add symbols
or infer quote rights. The later server subscription acknowledgements decide
which keys are actually enabled.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .moomoo_ws import validate_live_config
from .service import load_config
from .store import Store
from .watchlist import load_watchlist, requested_us_subscription_order


REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = REPO_ROOT / "configs/market_observer_v1.live.template.json"
QUOTA = REPO_ROOT / "artifacts/market_observer_live/quota_probe_20261115.json"
DEFAULT_MAPPING = REPO_ROOT / "results/watchlist_live_20261115/security_mapping_combined_redacted.json"
OUTPUT = REPO_ROOT / "artifacts/market_observer_live/acceptance_19us.json"


def prepare(mapping_path: Path = DEFAULT_MAPPING, quota_path: Path = QUOTA,
            output_path: Path = OUTPUT) -> dict:
    quota = json.loads(quota_path.read_text(encoding="utf-8"))
    if (quota.get("quota_state") != "ACCOUNT_REPORTED" or
            not isinstance(quota.get("remaining"), int) or quota["remaining"] < 0):
        raise ValueError("actual account quota GET did not report a usable remaining count")
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    config = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    watchlist_path = REPO_ROOT / config["watchlist_path"]
    watchlist = load_watchlist(watchlist_path)
    if (mapping.get("schema") != "market_observer_combined_current_mapping_v1" or
            mapping.get("watchlist_version") != watchlist["version"] or
            mapping.get("watchlist_config_sha256") != hashlib.sha256(watchlist_path.read_bytes()).hexdigest()):
        raise ValueError("combined mapping is not bound to this frozen watchlist version")
    expected = {
        (security_id, watchlist["securities"][security_id]["requested_ticker"],
         item["provider_code"], item["provider_market"], item["identity_scope"],
         item["evidence_id"])
        for security_id, item in watchlist["verified_current_mappings"].items()
    }
    actual = {
        (row.get("security_id"), row.get("requested_symbol"),
         row.get("provider_code"), row.get("provider_market"),
         row.get("identity_scope"), row.get("evidence_id"))
        for row in mapping.get("rows", []) if isinstance(row, dict)
    }
    if len(mapping.get("rows", [])) != len(actual) or actual != expected:
        raise ValueError("combined mapping rows differ from frozen source-bound identities")
    requested = requested_us_subscription_order(watchlist)
    if len(requested) != 19:
        raise ValueError("frozen US watchlist does not contain 19 requested symbols")
    exact: dict[str, dict] = {}
    for row in mapping.get("rows", []):
        if (row.get("identity_scope") == "CURRENT_ONLY" and
                row.get("provider_market") == "US" and
                row.get("provider_code")):
            symbol = row.get("requested_symbol")
            if symbol in exact and exact[symbol]["provider_code"] != row["provider_code"]:
                raise ValueError("conflicting provider mapping for a requested symbol")
            exact[symbol] = row
    config["observation_pool"] = requested
    live = config["live_push"]
    live.update({"entitlement_state": "BOUNDED_ACCOUNT_QUOTA_PROBE",
                 "entitlement_evidence": str(quota_path.relative_to(REPO_ROOT)).replace("\\", "/"),
                 "mapping_receipt_path": str(mapping_path.relative_to(REPO_ROOT)).replace("\\", "/"),
                 "mapping_receipt_sha256": hashlib.sha256(mapping_path.read_bytes()).hexdigest()})
    remaining = quota["remaining"]
    selected: list[dict] = []
    unserved: list[dict] = []
    db_path = REPO_ROOT / config["db_path"]
    with Store(db_path) as store:
        for symbol in requested:
            row = exact.get(symbol)
            if row is None:
                unserved.append({"requested_symbol": symbol, "kind": "QUOTE",
                                 "reason": "NO_CURRENT_OFFICIAL_MAPPING_IN_RECEIPT"})
                continue
            code = row["provider_code"]
            db_row = store.connection.execute(
                "SELECT identity_status FROM instruments WHERE instrument_id=?", (code,)).fetchone()
            if not db_row or db_row["identity_status"] != "VERIFIED":
                unserved.append({"requested_symbol": symbol, "provider_code": code,
                                 "kind": "QUOTE", "reason": "PRIVATE_DB_IDENTITY_NOT_VERIFIED"})
                continue
            if len(selected) >= remaining:
                unserved.append({"requested_symbol": symbol, "provider_code": code,
                                 "kind": "QUOTE", "reason": "ACCOUNT_REPORTED_REMAINING_CAP"})
                continue
            selected.append({"requested_symbol": symbol, "provider_code": code,
                             "instrument_id": code, "market": "US", "kind": "QUOTE",
                             "verification_status": "VERIFIED_EXACT_PROVIDER_CODE_MARKET"})
    if not selected:
        return {"status": "NO_SUBSCRIPTIONS_PREPARED", "requested_count": len(requested),
                "account_reported_remaining": remaining, "unserved": unserved,
                "provider_calls": 0}
    live["subscriptions"] = selected
    live["subscription_key_budget"] = len(selected)
    live["unserved_subscriptions"] = unserved
    validate_live_config(live, repo_root=REPO_ROOT)
    if output_path.exists():
        raise FileExistsError("private acceptance config exists; inspect it before creating a new version")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    load_config(output_path)
    return {"status": "BOUNDED_CONFIG_PREPARED_NOT_CONNECTED",
            "config_path": str(output_path), "requested_count": len(requested),
            "configured_count": len(selected), "unserved_count": len(unserved),
            "configured_provider_codes": [row["provider_code"] for row in selected],
            "max_runtime_minutes": config["max_runtime_minutes"], "provider_calls": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    args = parser.parse_args()
    print(json.dumps(prepare(mapping_path=args.mapping), ensure_ascii=False, sort_keys=True))
