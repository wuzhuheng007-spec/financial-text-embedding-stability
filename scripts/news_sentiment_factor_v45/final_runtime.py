"""One-time redacted local-process readback; no supplier or model calls."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.request import urlopen
from zoneinfo import ZoneInfo

import psutil

from .audit import run as audit
from .preflight import PUBLIC, dump


def run() -> dict:
    receipt = audit()
    listener_pids = sorted({connection.pid for connection in psutil.net_connections(kind="tcp")
                            if connection.laddr and connection.laddr.port == 8765
                            and connection.status == psutil.CONN_LISTEN and connection.pid})
    portal_http = None
    try:
        with urlopen("http://127.0.0.1:8765/", timeout=5) as response:
            portal_http = response.status
    except HTTPError as exc:
        portal_http = exc.code
    except Exception:
        portal_http = "UNREACHABLE"
    worker = receipt["daily_price_worker"]
    at = datetime.now(timezone.utc)
    result = {
        "schema": "news_sentiment_factor_v45_final_runtime_redacted_1",
        "checked_at_utc": at.isoformat(),
        "checked_at_asia_shanghai": at.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
        "portal_loopback_listener_count": len(listener_pids),
        "portal_http_status_unauthenticated_readback": portal_http,
        "feed_states": receipt["feed_states"],
        "research_worker": receipt["research_worker"],
        "daily_price_worker": {k: worker.get(k) for k in (
            "run_id", "pid", "started_at_utc", "heartbeat_at_utc", "state",
            "process_alive_identity", "last_supplier_success_at_utc",
            "last_supplier_error_class", "last_request_count", "last_queue_rows",
            "pause_reason", "budget_used_today", "daily_budget",
            "next_conditional_collection_window_utc")},
        "snapshot_batches": receipt["snapshot_counts"]["daily_snapshots"],
        "n1_nonmissing_company_rows": receipt["n1_nonmissing_company_rows"],
        "current_qualified_labels": receipt["current_qualified_labels"],
        "current_qualified_pairs": receipt["current_qualified_pairs"],
        "supplier_request_count": receipt["v45_supplier_requests"],
        "source_audit_receipt": "snapshot_acceptance_redacted.json",
    }
    dump(PUBLIC / "final_runtime_redacted.json", result)
    return result


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
