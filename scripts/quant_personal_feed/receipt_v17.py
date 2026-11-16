"""Write aggregate v1.7 runtime evidence without provider text or prices."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "apps/quant_portal/var/personal_feeds.sqlite3"
OUTPUT = ROOT / "results/quant_portal_personal_v17_20261117/runtime_redacted.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    now = datetime.now(timezone.utc)
    with sqlite3.connect(DB.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        rows = [dict(row) for row in db.execute("""SELECT provider,state,heartbeat_at,
          last_success_at,last_error_class,worker_alive,business_attempts_today,
          business_success_total,duplicate_quote_packets FROM provider_state
          WHERE provider IN ('jin10','moomoo','moomoo_snapshot') ORDER BY provider""")]
        quote_symbols = [row[0] for row in db.execute("SELECT DISTINCT symbol FROM quotes ORDER BY symbol")]
        news_versions = db.execute("SELECT COUNT(*) FROM news_versions").fetchone()[0]
        db.execute("ROLLBACK")
    try:
        with urlopen(Request("http://127.0.0.1:8765/accounts/login/", method="GET"), timeout=3) as page:
            portal_http = page.status
    except (HTTPError, URLError, TimeoutError) as error:
        portal_http = type(error).__name__
    result_root = ROOT / "results/quant_portal_personal_v17_20261117"
    a_run = json.loads((result_root / "run_a_redacted.json").read_text(encoding="utf-8"))
    b_run = json.loads((result_root / "run_b_redacted.json").read_text(encoding="utf-8"))
    result = {"schema": "personal_v17_runtime_aggregate_v1",
              "captured_at_utc": now.isoformat().replace("+00:00", "Z"),
              "captured_at_asia_shanghai": now.astimezone(timezone(timedelta(hours=8))).isoformat(),
              "portal_login_http": portal_http,
              "owner_page_visual_acceptance": "PENDING_OWNER_LOGIN",
              "providers": rows, "quote_symbols": quote_symbols, "news_version_rows": news_versions,
              "old_continuous_analysis": "PAUSED_FOR_V17" if
                  (ROOT / "apps/quant_portal/var/personal_analysis_v16.paused").is_file() else "NOT_PAUSED",
              "v17_bounded_A_calls": a_run["counts"]["attempted"],
              "v17_bounded_B_calls": b_run["counts"]["attempted"],
              "v17_bounded_T_calls": len(list((ROOT / "artifacts/quant_personal_v17_20261117/attempts")
                                                  .glob("*_T.json"))),
              "code_sha256": {str(path.relative_to(ROOT)).replace('\\', '/'): sha(path)
                              for path in (ROOT / "scripts/quant_personal_feed/store.py",
                                           ROOT / "scripts/quant_personal_feed/view_v17.py",
                                           ROOT / "apps/quant_portal/core/personal_feed_cache.py",
                                           ROOT / "apps/quant_portal/static/core/personal_feed.js")}}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes((json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    print(json.dumps({"portal_login_http": portal_http,
                      "providers": [(x["provider"], x["state"], x["worker_alive"]) for x in rows],
                      "news_version_rows": news_versions}, ensure_ascii=False))


if __name__ == "__main__":
    main()
