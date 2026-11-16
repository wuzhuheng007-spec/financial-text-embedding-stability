"""Write a redacted, reproducible receipt for the owner-only v1.6 runtime."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .collector import ROOT
from .news_display_v16 import source_title
from .quote_select_v16 import choose_latest, read_latest
from .security_profiles_v16 import read_profiles
from .store import read_personal_cache
from .view_v16 import AUTO_DRAFT_DISPLAY_ENABLED

RESULT = ROOT / "results/quant_portal_personal_v16_20261116/runtime_redacted.json"
CONFIG = ROOT / "configs/quant_portal_personal_v16_20261116.json"
SOURCE_FILES = (
    "scripts/quant_personal_feed/analysis_contract_v16.py",
    "scripts/quant_personal_feed/analysis_v16.py",
    "scripts/quant_personal_feed/view_v16.py",
    "scripts/quant_personal_feed/quote_select_v16.py",
    "scripts/quant_personal_feed/store.py",
    "scripts/quant_personal_feed/timeline.py",
    "apps/quant_portal/core/personal_feed_cache.py",
    "apps/quant_portal/static/core/personal_feed.js",
    "apps/quant_portal/static/core/personal_timeline.js",
    "configs/quant_portal_personal_v16_20261116.json",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run() -> dict:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    owner_name = json.loads((ROOT / "configs/quant_portal_personal_feeds_v11_20261116.json")
                            .read_text(encoding="utf-8"))["owner_username"]
    portal = ROOT / "apps/quant_portal/var/dev.sqlite3"
    feed = ROOT / "apps/quant_portal/var/personal_feeds.sqlite3"
    analysis = ROOT / "apps/quant_portal/var/personal_analysis_v16.sqlite3"
    with sqlite3.connect(portal.resolve().as_uri() + "?mode=ro", uri=True) as db:
        owner = db.execute("SELECT id FROM auth_user WHERE username=?", (owner_name,)).fetchone()
        if not owner:
            raise ValueError("OWNER_NOT_FOUND")
        owner_id = owner[0]
        account = {
            "owner_accounts": db.execute("SELECT COUNT(*) FROM auth_user WHERE id=?", (owner_id,)).fetchone()[0],
            "watchlist_rows": db.execute("SELECT COUNT(*) FROM core_watchlistitem WHERE user_id=?", (owner_id,)).fetchone()[0],
            "settings_rows": db.execute("SELECT COUNT(*) FROM core_userpreferences WHERE user_id=?", (owner_id,)).fetchone()[0],
            "historical_fact_cards": db.execute("SELECT COUNT(*) FROM core_publicfactcard").fetchone()[0],
        }
    data = read_personal_cache(feed, owner_id, news_limit=200, included_only=True)
    profiles = read_profiles()
    with sqlite3.connect(feed.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        # Hold one read transaction while comparing the dashboard and chart
        # selection rules. Two separate live reads can straddle a new push.
        db.execute("BEGIN")
        equality = {}
        source_time_present = {}
        for symbol in profiles:
            sessions = [dict(row) for row in db.execute("""SELECT symbol,session,price_text,
              source_at,received_at FROM session_quotes WHERE owner_id=? AND symbol=?""",
              (owner_id, symbol))]
            legacy = db.execute("""SELECT symbol,provider_code,price_text,currency,provider_time,
              received_at,market_state,delay_status FROM quotes WHERE owner_id=? AND symbol=?
              ORDER BY julianday(provider_time) DESC,received_at DESC,id DESC LIMIT 1""",
              (owner_id, symbol)).fetchone()
            dashboard = choose_latest(symbol, sessions, dict(legacy) if legacy else None)
            chart = read_latest(db, owner_id, symbol)
            equality[symbol] = all((dashboard or {}).get(field) == (chart or {}).get(field)
                                   for field in ("price_text", "session", "source_at"))
            source_time_present[symbol] = bool((dashboard or {}).get("source_at"))
        feed_counts = {
            "news_items": db.execute("SELECT COUNT(*) FROM news_items WHERE owner_id=?", (owner_id,)).fetchone()[0],
            "session_quote_rows": db.execute("SELECT COUNT(*) FROM session_quotes WHERE owner_id=?", (owner_id,)).fetchone()[0],
        }
    jobs: dict[str, dict[str, int]] = {}
    attempts: dict[str, dict[str, int]] = {}
    worker = None
    with sqlite3.connect(analysis.resolve().as_uri() + "?mode=ro", uri=True) as db:
        for prompt, status, count in db.execute("SELECT prompt_version,status,COUNT(*) FROM analysis_jobs GROUP BY 1,2"):
            jobs.setdefault(prompt, {})[status] = count
        for prompt, error, count in db.execute("SELECT prompt_version,error_class,COUNT(*) FROM analysis_attempts GROUP BY 1,2"):
            attempts.setdefault(prompt, {})[error or "MECHANICAL_PASS"] = count
        row = db.execute("SELECT pid,heartbeat_at,state,model_revision,prompt_version,schema_version,last_error_class FROM analysis_worker_state WHERE owner_id=?", (owner_id,)).fetchone()
        if row:
            worker = dict(zip(("pid", "heartbeat_at", "state", "model_revision", "prompt_version", "schema_version", "last_error_class"), row))
    now = datetime.now(timezone.utc)
    receipt = {
        "schema": "quant_portal_personal_v16_runtime_redacted_v1",
        "observed_at_utc": now.isoformat().replace("+00:00", "Z"),
        "observed_at_asia_shanghai": now.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
        "code_and_config_sha256": {rel: sha(ROOT / rel) for rel in SOURCE_FILES},
        "account_preservation": account,
        "security_profiles": {symbol: {"name_present": bool(p.get("name")),
                                         "type": p.get("asset_type"),
                                         "currency_present": bool(p.get("currency")),
                                         "source_response_sha256": p.get("response_sha256")}
                              for symbol, p in profiles.items()},
        "quote_selection": {"checked_symbols": len(equality), "all_cache_selection_equal": all(equality.values()),
                            "comparison_scope": "SAME_SQLITE_READ_TRANSACTION",
                            "per_symbol_equal": equality, "source_time_present": source_time_present},
        "feed": {**feed_counts, "news_title_kinds_in_page_window": dict(Counter(
            source_title(item.get("title"), item.get("content"))[1]
            for item in data.get("news", []))), "provider_states": [
            {"provider": state.get("provider"), "state": state.get("state"),
             "heartbeat_at": state.get("heartbeat_at"), "last_success_at": state.get("last_success_at")}
            for state in data.get("states", [])],
            "included_news_in_page_window": len(data.get("news", []))},
        "analysis": {"worker": worker, "jobs_by_contract": jobs, "attempts_by_contract": attempts,
                     "current_prompt_version": config["prompt_version"],
                     "auto_draft_display_enabled": AUTO_DRAFT_DISPLAY_ENABLED,
                     "mechanical_pass_is_not_source_review": True},
        "limitations": ["No independent source verification of supplier news", "No inference from missing currency or delay fields", "No financial prediction or trading"],
    }
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8", newline="\n")
    return receipt


if __name__ == "__main__":
    result = run()
    print(json.dumps({"receipt": str(RESULT), "quote_selection": result["quote_selection"],
                      "account_preservation": result["account_preservation"]}, ensure_ascii=False))
