"""Read the owner's loopback-only sidecar cache; never call a provider here."""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings


REPO_ROOT = Path(__file__).resolve().parents[3]
def _parse_utc(value: str | None):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo else None
    except ValueError:
        return None


def for_owner(user, watch_symbols: set[str]) -> dict:
    if not settings.PERSONAL_MODE or user.username != settings.PERSONAL_FEED_OWNER_USERNAME:
        return {"cache_state": "NOT_AUTHORIZED", "quotes": [], "news": [],
                "states": [], "symbols": []}
    if not (REPO_ROOT / "scripts/quant_personal_feed/store.py").is_file():
        return {"cache_state": "NOT_INSTALLED", "quotes": [], "news": [],
                "states": [], "symbols": []}
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from scripts.quant_personal_feed.store import read_personal_cache
    from scripts.quant_personal_feed.collector import verified_current_symbols, ROOT
    from scripts.quant_personal_feed.event_rules import (
        ENTITY_RULES, CATEGORY_RULES, RULE_VERSION, TECH_PRESET_ENTITIES,
        TECH_PRESET_CATEGORIES, category_choices, classify, entity_choices, focus)
    data = read_personal_cache(Path(settings.PERSONAL_FEED_DB_PATH),user.pk,news_limit=500)
    try:
        import json
        config = json.loads((ROOT / "configs/quant_portal_personal_feeds_v11_20261116.json").read_text(encoding="utf-8"))
        data["verified_symbols"] = verified_current_symbols(config)
        data["subscription_budget"] = config["moomoo"]["local_subscription_key_budget"]
    except (OSError, ValueError, KeyError):
        data["verified_symbols"] = {}
        data["subscription_budget"] = 6
        data["mapping_error"] = "VERIFIED_MAPPING_UNAVAILABLE"
    existing_interests = {row["interest_key"] for row in data.get("news_interests", []) if row["enabled"]}
    default_entities = {key for key in existing_interests | watch_symbols if key in ENTITY_RULES}
    # This reproduces the former macro-interest behavior without selecting all
    # technology companies or all event categories on the owner's behalf.
    default_categories = {"MACRO_RATES"}
    saved = data.get("event_preferences")
    entities = set(saved["entities"]) & set(ENTITY_RULES) if saved else default_entities
    categories = set(saved["categories"]) & set(CATEGORY_RULES) if saved else default_categories
    data["event_preferences_effective"] = {"entities": sorted(entities),
        "categories": sorted(categories), "explicit": bool(saved)}
    data["event_choices"] = {"entities": entity_choices(), "categories": category_choices(),
        "tech_preset": {"entities": TECH_PRESET_ENTITIES, "categories": TECH_PRESET_CATEGORIES}}
    data["event_rule_version"] = RULE_VERSION
    now = datetime.now(timezone.utc)
    for state in data["states"]:
        heartbeat = _parse_utc(state.get("heartbeat_at"))
        if heartbeat is None or (now-heartbeat).total_seconds() > 20:
            state["state"] = "DISCONNECTED"
            state["heartbeat_stale"] = True
        else:
            state["heartbeat_stale"] = False
    for item in data["news"]:
        try:
            labels = classify(item)
            item["event_labels"] = labels
            item.update(focus(item, labels, entities, categories))
            item["interest"] = bool({e["id"] for e in labels["entities"]} & (existing_interests | watch_symbols)
                                    or {c["id"] for c in labels["categories"]} & default_categories)
        except (ValueError, TypeError, KeyError, re.error) as exc:
            # A bad news record must not take down the independent quote cards.
            item["event_labels"] = {"entities":[],"categories":[{"id":"OTHER","label":"其他","phrase":None}],
                                    "rule_version":RULE_VERSION,"event_group_id":item.get("item_key")}
            item.update({"interest":False,"focus_eligible":False,"focus_reason":None,
                         "rule_priority":0,"event_rule_error":type(exc).__name__})
        item["source_status"] = "金十快讯／原始发布者未知／待交叉核实"
    return data
