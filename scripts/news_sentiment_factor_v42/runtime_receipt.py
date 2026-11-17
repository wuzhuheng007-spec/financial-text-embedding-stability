"""Record a single redacted service status readback without private data."""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone

from .worker import PUBLIC, ROOT, status, write_json


def ps_status(script: str) -> list[str]:
    run = subprocess.run(
        ["powershell", "-NoProfile", "-File", str(ROOT / script), "status"],
        cwd=ROOT, capture_output=True, text=True, timeout=20, check=False,
    )
    return run.stdout.splitlines() if run.returncode == 0 else ["STATUS_COMMAND_FAILED"]


def collect() -> dict:
    worker = status()
    portal = ps_status("apps/quant_portal/ops/local.ps1")
    feed = ps_status("apps/quant_portal/ops/feed_personal.ps1")
    providers = []
    if len(feed) > 1:
        try:
            parsed = json.loads(feed[-1])
            providers = [
                {key: row.get(key) for key in
                 ("provider", "state", "worker_alive", "heartbeat_at", "last_success_at", "coverage_gap")}
                for row in parsed.get("states", [])
            ]
        except (ValueError, TypeError):
            pass
    receipt = {
        "schema": "news_sentiment_v42_runtime_readback_1",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "research_worker": {key: worker.get(key) for key in
                            ("run_id", "state", "process_alive_identity", "heartbeat_at_utc",
                             "last_success_at_utc", "daily_attempts", "daily_limit", "backlog",
                             "snapshot_state", "next_cutoff_utc", "quarantined")},
        "portal": {"running": bool(portal and portal[0].startswith("RUNNING ")),
                   "http_200": bool(portal and "ready_http=200" in portal[0]),
                   "loopback_url": "http://127.0.0.1:8765/"},
        "personal_feed": {"running": bool(feed and feed[0].startswith("FEED_RUNNING ")),
                          "providers": providers},
        "observed_at_one_instant_only": True,
        "private_news_prices_scores_credentials_included": False,
    }
    write_json(PUBLIC / "final_runtime_redacted.json", receipt)
    return receipt


if __name__ == "__main__":
    print(json.dumps(collect(), ensure_ascii=False))
