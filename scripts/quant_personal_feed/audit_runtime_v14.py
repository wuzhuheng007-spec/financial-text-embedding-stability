"""Redacted, read-only personal portal and feed runtime receipt."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
OPS = ROOT / "apps/quant_portal/ops"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def status(script: str, action: str) -> str:
    run = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                          "-File", str(OPS / script), action],
                         cwd=ROOT, capture_output=True, text=True, timeout=20)
    return (run.stdout.splitlines() or [f"ERROR_EXIT_{run.returncode}"])[0]


def http_status(path: str) -> int | None:
    try:
        with urlopen("http://127.0.0.1:8765" + path, timeout=5) as response:
            return response.status
    except OSError:
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    feed_config = ROOT / "configs/quant_portal_personal_feeds_v11_20261116.json"
    feed = json.loads(feed_config.read_text(encoding="utf-8"))
    portal_path = ROOT / feed["portal_db"]
    cache_path = ROOT / feed["cache_db"]
    with sqlite3.connect(portal_path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        owner = db.execute("SELECT id FROM auth_user WHERE username=?", (feed["owner_username"],)).fetchone()
        owner_id = owner[0] if owner else None
        portal_counts = {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in
                         ("auth_user", "core_watchlistitem", "core_userpreferences", "core_publicfactcard")}
    with sqlite3.connect(cache_path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        binding = db.execute("SELECT owner_id FROM owner_binding WHERE singleton=1").fetchone()
        selected = [row[0] for row in db.execute("""SELECT symbol FROM quote_selection
          WHERE owner_id=? AND desired=1 ORDER BY position,symbol""", (owner_id,))]
        provider = [{"provider": row[0], "state": row[1], "worker_alive": row[2],
                     "heartbeat_at": row[3], "last_success_at": row[4]} for row in db.execute(
            "SELECT provider,state,worker_alive,heartbeat_at,last_success_at FROM provider_state ORDER BY provider")]
        news_count = db.execute("SELECT COUNT(*) FROM news_items WHERE owner_id=?", (owner_id,)).fetchone()[0]
    now = datetime.now(timezone.utc)
    output = {"schema": "quant_portal_personal_timeline_v14_runtime",
              "observed_at_utc": now.isoformat().replace("+00:00", "Z"),
              "observed_at_asia_shanghai": now.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
              "portal_status": status("local.ps1", "status"),
              "feed_status": status("feed_personal.ps1", "status"),
              "ready_http": http_status("/readyz/"),
              "chart_script_http": http_status("/static/core/personal_timeline.js"),
              "portal_counts": portal_counts,
              "owner_binding_matches": owner_id is not None and bool(binding and binding[0] == owner_id),
              "desired_quote_symbols": selected,
              "news_items": news_count,
              "provider_states": provider,
              "feed_config_sha256": digest(feed_config),
              "timeline_config_sha256": digest(ROOT / "configs/quant_portal_personal_timeline_v14_20261116.json"),
              "supplier_requests_from_audit": 0,
              "scope_note": "Only loopback HTTP and local private SQLite metadata; no account identifier, price or body emitted."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"receipt": str(args.output), "portal": output["portal_status"],
                      "feed": output["feed_status"], "ready": output["ready_http"],
                      "chart_script": output["chart_script_http"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
