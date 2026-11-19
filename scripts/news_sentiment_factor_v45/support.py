"""Read-only redacted receipt for the independently blocked REST quote route."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from scripts.market_observer_v1.moomoo_webapi_oauth import OAuthError, token_store
from scripts.news_sentiment_factor_v44.price_pilot import TOKEN
from .preflight import PUBLIC, ROOT, dump


def run() -> dict:
    at = datetime.now(timezone.utc)
    feed = ROOT / "apps/quant_portal/var/personal_feeds.sqlite3"
    with closing(sqlite3.connect(f"file:{feed.as_posix()}?mode=ro", uri=True)) as con:
        row = con.execute("""SELECT state,last_success_at,last_error_class,worker_alive
            FROM provider_state WHERE provider='moomoo_snapshot'""").fetchone()
    try:
        auth = token_store(TOKEN).status()
    except OAuthError:
        auth = {"credential_state": "STORE_NOT_CONFIGURED"}
    result = {"schema": "news_sentiment_factor_v45_rest_439_redacted_1",
              "checked_at_utc": at.isoformat(),
              "checked_at_asia_shanghai": at.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
              "route": "POST /api/v1.0/quote/stock-quote",
              "route_state": row[0] if row else "UNKNOWN",
              "last_business_success_at_utc": row[1] if row else None,
              "last_error_class": row[2] if row else None,
              "thread_alive": bool(row[3]) if row else False,
              "provider_error_category": "HTTP_439_SECURITY_POLICY_BLOCKED",
              "provider_numeric_code_from_private_response": "-12009_IF_RESPONSE_MATCHES_DOCUMENTATION; NOT_REEXPOSED_HERE",
              "scope_classes": auth.get("granted_scope_classes", []),
              "credential_state": auth.get("credential_state"),
              "access_expires_at_utc": auth.get("access_expires_at"),
              "official_error_guide": "https://open.moomoo.com/api/overview/getting-started",
              "official_route_guide": "https://open.moomoo.com/api/quote/realtime/stock-quote",
              "root_cause": "NOT_DETERMINED_BY_HTTP_STATUS_ALONE",
              "new_blocked_route_requests_in_this_turn": 0,
              "support_draft": "reports/2026-11-17_moomoo_rest_439_support_draft.zh.md"}
    dump(PUBLIC / "rest_439_redacted.json", result)
    return result


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
