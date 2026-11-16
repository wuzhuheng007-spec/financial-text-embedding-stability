"""Read the owner's loopback-only sidecar cache; never call a provider here."""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings


REPO_ROOT = Path(__file__).resolve().parents[3]
ALIASES = {
    "NVDA": (r"\bNVDA\b", r"\bNVIDIA\b", "英伟达"),
    "MU": (r"\bMU\b", r"\bMicron\b", "美光"),
    "SNDK": (r"\bSNDK\b", r"\bSanDisk\b", r"\bSandisk\b", "闪迪"),
    "SKHYNIX": (r"\bSK[ -]?hynix\b", "SK海力士", "SK 海力士", "海力士"),
    "SPACEX": (r"\bSpaceX\b", "太空探索技术公司"),
    "AMD": (r"\bAMD\b", "超威"),
    "INTC": (r"\bINTC\b", r"\bIntel\b", "英特尔"),
    "MSFT": (r"\bMSFT\b", r"\bMicrosoft\b", "微软"),
    "AAPL": (r"\bAAPL\b", r"\bApple\b", "苹果公司"),
    "AMZN": (r"\bAMZN\b", r"\bAmazon\b", "亚马逊"),
    "GOOGL": (r"\bGOOGL?\b", r"\bAlphabet\b", "谷歌"),
    "META": (r"\bMETA\b", "脸书"),
    "TSLA": (r"\bTSLA\b", r"\bTesla\b", "特斯拉"),
    "SOXX": (r"\bSOXX\b", "半导体"),
    "TQQQ": (r"\bTQQQ\b", "纳斯达克100"),
}
MACRO = re.compile(r"\bCPI\b|\bPCE\b|\bFOMC\b|美联储|非农|利率决议|通胀|就业数据|货币政策", re.I)


def is_interest(text: str, symbols: set[str]) -> bool:
    if MACRO.search(text):
        return True
    return any(re.search(pattern, text, re.I) for symbol in symbols
               for pattern in ALIASES.get(symbol, (rf"\b{re.escape(symbol)}\b",)))


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
    interests = {row["interest_key"] for row in data.get("news_interests", []) if row["enabled"]}
    interests.update(watch_symbols)
    now = datetime.now(timezone.utc)
    for state in data["states"]:
        heartbeat = _parse_utc(state.get("heartbeat_at"))
        if heartbeat is None or (now-heartbeat).total_seconds() > 20:
            state["state"] = "DISCONNECTED"
            state["heartbeat_stale"] = True
        else:
            state["heartbeat_stale"] = False
    for item in data["news"]:
        item["interest"] = is_interest((item.get("title") or "") + " " +
                                       (item.get("content") or ""),interests)
        item["source_status"] = "金十快讯／原始发布者未知／待交叉核实"
    return data
