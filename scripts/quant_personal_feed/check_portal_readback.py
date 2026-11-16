"""Check owner-only portal rendering against the current private cache without publishing data."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .collector import ROOT


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT / "apps/quant_portal"))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "portal.settings")
    os.environ.setdefault("SECRET_KEY", "readback-only-no-session-secret-00000000")
    os.environ["DJANGO_TESTING"] = "1"
    os.environ["DEBUG"] = "1"
    os.environ["ALLOWED_HOSTS"] = "127.0.0.1,localhost,testserver"
    os.environ["PERSONAL_MODE"] = "1"
    os.environ["PUBLIC_MARKET_DATA_ENABLED"] = "0"
    import django
    django.setup()
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import AnonymousUser
    from django.test import RequestFactory
    from core.views import dashboard, personal_feed_json

    factory = RequestFactory()
    owner = get_user_model().objects.get(username="owner@local.invalid")
    request = factory.get("/personal-feed.json")
    request.user = owner
    response = personal_feed_json(request)
    data = json.loads(response.content)
    anonymous = factory.get("/personal-feed.json")
    anonymous.user = AnonymousUser()
    unauthenticated = personal_feed_json(anonymous)
    page_request = factory.get("/")
    page_request.user = owner
    page_request.display_timezone = owner.preferences.display_timezone
    page = dashboard(page_request)
    body = page.content.decode("utf-8")
    receipt = {
        "schema": "quant_portal_personal_readback_redacted_v1",
        "owner_feed_http": response.status_code,
        "anonymous_feed_http": unauthenticated.status_code,
        "cache_control": response.get("Cache-Control"),
        "quote_symbols": sorted(row["symbol"] for row in data.get("quotes", [])),
        "cached_news_items": data.get("news_total"),
        "returned_news_items": len(data.get("news", [])),
        "page_http": page.status_code,
        "page_has_private_quote_root": 'id="personal-quotes"' in body,
        "page_has_private_news_root": 'id="personal-news"' in body,
        "page_has_local_cache_js": "personal_feed.js" in body,
        "page_has_public_approval_wait_notice": "等待公共展示批准" in body,
        "browser_visual_acceptance": "ISOLATED_BROWSER_QA_SEPARATE_RECEIPT",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes((json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print("OWNER_PAGE_READBACK_REDACTED")


if __name__ == "__main__":
    main()
