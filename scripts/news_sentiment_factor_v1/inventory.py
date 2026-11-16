"""Read-only historical coverage and factor-input panel; never reads return labels."""
from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .protocol import proxy_eligible_from

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "artifacts/market_observer_historical_20261115"
CORPUS = ROOT / "artifacts/market_observer_official_20261115/corpus_manifest.json"
FEED = ROOT / "apps/quant_portal/var/personal_feeds.sqlite3"
CONFIG = ROOT / "configs/news_sentiment_factor_v1_20261117.json"
PUBLIC = ROOT / "results/news_sentiment_factor_v1_20261117"
PRIVATE = ROOT / "artifacts/news_sentiment_factor_v1_20261117"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    first, last = cfg["window"]
    csv_path = BASE / "normalized_daily_expansion_v1.csv"
    pilot_path = BASE / "normalized_daily_v1.csv"
    calendar_path = BASE / "US_trading_days.raw.json"
    calendar_receipt = json.loads((BASE / "calendar_receipt.json").read_text(encoding="utf-8"))
    if digest(calendar_path) != calendar_receipt["response_sha256"]:
        raise ValueError("CALENDAR_HASH_MISMATCH")
    calendar_raw = json.loads(calendar_path.read_text(encoding="utf-8"))
    if calendar_raw.get("ret_code") != 0:
        raise ValueError("CALENDAR_PROVIDER_FAILURE")
    calendar = sorted({row["time"] for row in calendar_raw["data"]["trading_days"]})
    if len(calendar) != calendar_receipt["day_count"]:
        raise ValueError("CALENDAR_COUNT_MISMATCH")
    price_rows: dict[tuple[str, str], dict] = {}
    for path in (csv_path, pilot_path):
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if row["adjustment"] != "0" or not first <= row["market_date"] <= last:
                    continue
                key = (row["symbol"], row["market_date"])
                if key in price_rows:
                    raise ValueError("DUPLICATE_UNADJUSTED_BAR")
                price_rows[key] = row
    if not calendar or calendar[0] != first or calendar[-1] != last:
        raise ValueError("PRICE_WINDOW_MISMATCH")
    calendar_set = set(calendar)
    if any(day not in calendar_set for _, day in price_rows):
        raise ValueError("BAR_DATE_OUTSIDE_OFFICIAL_CALENDAR")
    months = sorted({day[:7] for day in calendar})
    expected = Counter(day[:7] for day in calendar)
    events = json.loads(CORPUS.read_text(encoding="utf-8"))["documents"]
    event_groups = defaultdict(set)
    timing = defaultdict(Counter)
    source_kinds = defaultdict(Counter)
    event_meta = []
    for doc in events:
        date = doc.get("announcement_date")
        symbol = doc.get("symbol")
        if not date or not first <= date <= last:
            continue
        key = (symbol, date[:7])
        event_groups[key].add(doc.get("event_group_id") or doc.get("document_id"))
        source_kinds[key][doc.get("source_kind") or "UNKNOWN"] += 1
        timing[key]["STRICT_HISTORIC_AVAILABLE"] += bool(doc.get("historical_first_available_at"))
        timing[key]["SEC_ACCEPTANCE_TIME"] += bool(doc.get("sec_acceptance_at"))
        timing[key]["OFFICIAL_DATE_ONLY"] += bool(doc.get("official_published_at") or doc.get("announcement_date"))
        event_meta.append({"symbol": symbol, "month": date[:7], "event_group_id": doc.get("event_group_id"),
                           "source_kind": doc.get("source_kind"), "version_status": doc.get("version_status"),
                           "first_available_proven": bool(doc.get("historical_first_available_at"))})
    # Date-proxy features describe only the saved, hashed issuer-document
    # subset. They do not turn missing historical news into a zero count.
    issuer_groups: dict[tuple[str, str], str] = {}
    for doc in events:
        date, symbol = doc.get("announcement_date"), doc.get("symbol")
        if (symbol not in {"NVDA", "MU"} or not date or not first <= date <= last
                or doc.get("version_status") != "SEC_ARCHIVE_HASHED"):
            continue
        group = doc.get("event_group_id") or doc.get("document_id")
        if group:
            key = (symbol, group)
            issuer_groups[key] = min(date, issuer_groups.get(key, date))
    eligible_groups = defaultdict(list)
    for (symbol, group), date in issuer_groups.items():
        eligible = proxy_eligible_from(date, calendar)
        if eligible:
            eligible_groups[symbol].append((eligible, group))
    symbols = cfg["ordinary_stock_pilot"] + cfg["etf_context_separate"]
    monthly = []
    for symbol in symbols:
        for month in months:
            days = [day for day in calendar if day.startswith(month)]
            got = sum((symbol, day) in price_rows for day in days)
            code = symbol.removeprefix("US.")
            ekey = (code, month)
            monthly.append({"symbol": symbol, "security_class": "STOCK" if symbol in cfg["ordinary_stock_pilot"] else "ETF",
                "month": month, "expected_trading_days": expected[month], "saved_unadjusted_daily_bars": got,
                "missing_price_days": expected[month]-got,
                "price_missing_fraction": round((expected[month]-got)/expected[month], 6),
                "saved_official_document_event_groups": len(event_groups[ekey]),
                "saved_official_document_source_kinds": dict(source_kinds[ekey]),
                "strict_historical_availability_documents": timing[ekey]["STRICT_HISTORIC_AVAILABLE"],
                "sec_acceptance_time_documents": timing[ekey]["SEC_ACCEPTANCE_TIME"],
                "historical_news_archive_coverage": "UNKNOWN_NOT_ARCHIVED",
                "historical_news_count": None, "historical_dedup_news_events": None,
                "corporate_action_coverage": "DATED_REHAB_CHECKED" if symbol in {"US.TQQQ","US.NVDA","US.MU"}
                    else "NOT_CHECKED_IN_PILOT"})
    PUBLIC.mkdir(parents=True, exist_ok=True)
    with (PUBLIC / "coverage_matrix.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(monthly[0]))
        writer.writeheader(); writer.writerows(monthly)
    # Private development panel contains saved prices; no return label or text is read.
    PRIVATE.mkdir(parents=True, exist_ok=True)
    panel_path = PRIVATE / "company_day_panel_private.jsonl"
    with panel_path.open("w", encoding="utf-8") as handle:
        for symbol in cfg["ordinary_stock_pilot"]:
            for day_index, day in enumerate(calendar):
                row = price_rows.get((symbol, day))
                if not row:
                    continue
                opening, closing = float(row["open"]), float(row["close"])
                lookback_start = calendar[max(0, day_index-19)]
                saved_groups = sorted(group for available, group in eligible_groups[symbol]
                                      if lookback_start <= available <= day)
                panel = {"symbol": symbol, "signal_date_et": day,
                    "signal_cutoff_rule": cfg["signal_cutoff"],
                    "price_input_source_sha256": row["source_raw_sha256"],
                    "price_same_session_open_close_ratio": closing/opening if opening else None,
                    "volume_raw": row["volume_raw"],
                    "price_availability": "RETROSPECTIVE_DATE_PROXY",
                    "saved_official_event_count_20td_date_proxy": len(saved_groups),
                    "saved_official_event_group_ids": saved_groups,
                    "official_date_proxy_status": "PARTIAL_SAVED_ISSUER_ONLY_NOT_FULL_NEWS_COVERAGE",
                    "news_count": None, "news_tone": None, "news_uncertainty": None,
                    "news_novelty": None, "news_coverage": "UNKNOWN_INCOMPLETE_ARCHIVE",
                    "label_1d": None, "label_5d": None, "label_20d": None,
                    "label_status": "NOT_COMPUTED_UNQUALIFIED_NEWS_COMPARISON"}
                handle.write(json.dumps(panel, ensure_ascii=False, sort_keys=True) + "\n")
    with sqlite3.connect(FEED.resolve().as_uri() + "?mode=ro", uri=True) as db:
        cache = db.execute("SELECT COUNT(*), MIN(received_at), MAX(received_at) FROM news_versions").fetchone()
        in_window = db.execute("SELECT COUNT(*) FROM news_versions WHERE published_at_utc >= ? AND published_at_utc < ?",
                               (first, "2026-08-01")).fetchone()[0]
    result = {"schema": "news_sentiment_factor_v1_coverage_redacted", "generated_at_utc":
        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "config_sha256": digest(CONFIG), "price_input_sha256": {p.name: digest(p) for p in (csv_path,pilot_path)},
        "saved_official_trading_calendar_sha256": digest(calendar_path),
        "official_manifest_sha256": digest(CORPUS), "calendar_trading_days": len(calendar),
        "monthly_rows": len(monthly), "price_symbols": symbols,
        "stock_panel_rows": sum((symbol,day) in price_rows for symbol in cfg["ordinary_stock_pilot"] for day in calendar),
        "stock_panel_sha256_private": digest(panel_path),
        "official_documents_in_window": len(event_meta),
        "official_event_groups_by_symbol": {s: len({e["event_group_id"] for e in event_meta if e["symbol"] == s})
            for s in ("NVDA", "MU", "GLOBAL")},
        "issuer_event_groups_with_eligible_date_proxy": {s: len(eligible_groups[s]) for s in ("NVDA", "MU")},
        "historical_news_archive": "UNQUALIFIED; current personal cache and search hits are not a full archive",
        "current_personal_cache_version_rows": cache[0], "current_cache_first_received_at": cache[1],
        "current_cache_last_received_at": cache[2], "current_cache_published_in_study_window": in_window,
        "price_action_note": "Dated rehab checked only for TQQQ,NVDA,MU; raw close/open features are descriptive and bar availability is retrospective",
        "A_B_C_D_fit_calls": 0, "IC_calculated": False,
        "stop_reason": "HISTORICAL_NEWS_COVERAGE_AND_POINT_IN_TIME_AVAILABILITY_UNQUALIFIED",
        "prior_exposure": cfg["known_exposure"]}
    save(PUBLIC / "coverage_summary_redacted.json", result)
    save(PUBLIC / "panel_manifest_redacted.json", {"schema": "news_sentiment_factor_v1_panel_manifest",
        "private_panel_path": "artifacts/news_sentiment_factor_v1_20261117/company_day_panel_private.jsonl",
        "private_panel_sha256": digest(panel_path), "rows": result["stock_panel_rows"],
        "full_archive_news_features_missing": True,
        "partial_saved_issuer_event_count_present": True,
        "all_labels_not_computed": True,
        "purpose": "traceable input inventory, not model fit or predictive validation"})
    print(json.dumps({k: result[k] for k in ("calendar_trading_days", "stock_panel_rows", "official_documents_in_window", "A_B_C_D_fit_calls", "stop_reason")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
