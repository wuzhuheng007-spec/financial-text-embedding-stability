"""One read-only account subscription-quota GET, with no retries.

The documented public page for this endpoint was previously indexed by the
official site but currently returns 404. The API response is treated as the
only account-specific evidence; an HTTP or business error leaves quota unknown.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .moomoo_webapi_oauth import LocalTokenStore


QUOTA_URL = "https://webapi.moomoo.com/api/v1.0/quote/quotas/subscription"


def probe_subscription_quota(token_store: LocalTokenStore, receipt_path: Path) -> dict:
    """Read only the current user's subscription pool once; never infer zero on error."""
    if receipt_path.exists():
        raise FileExistsError("quota probe receipt already exists; preserve it and use a new version")
    token = token_store.access_token()
    requested_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    receipt: dict = {"schema": "moomoo_subscription_quota_probe_v1",
                     "source": QUOTA_URL, "requested_at_utc": requested_at,
                     "request_count": 1, "retry_count": 0,
                     "quota_state": "UNKNOWN", "token_included": False}
    try:
        request = Request(QUOTA_URL, headers={"Authorization": "Bearer " + token}, method="GET")
        with urlopen(request, timeout=15) as response:
            receipt["http_status"] = response.status
            answer = json.loads(response.read(65536).decode("utf-8"))
    except HTTPError as exc:
        receipt["http_status"] = exc.code
        receipt["error_type"] = "HTTP_ERROR"
        receipt["retry_after_seconds"] = exc.headers.get("Retry-After")
        answer = None
    except (URLError, TimeoutError, ValueError) as exc:
        receipt["error_type"] = type(exc).__name__
        answer = None
    if isinstance(answer, dict):
        receipt["ret_code"] = answer.get("ret_code")
        if answer.get("ret_code") == 0 and isinstance(answer.get("data"), dict):
            data = answer["data"]
            numbers = [data.get(name) for name in ("total", "used", "remaining")]
            if all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in numbers):
                receipt.update({"quota_state": "ACCOUNT_REPORTED",
                                "total": numbers[0], "used": numbers[1], "remaining": numbers[2]})
            else:
                receipt["error_type"] = "INVALID_QUOTA_NUMBERS"
        else:
            receipt["error_type"] = "BUSINESS_ERROR_OR_MISSING_DATA"
    receipt["completed_at_utc"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt
