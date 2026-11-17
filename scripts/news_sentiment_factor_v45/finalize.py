"""Redacted postrun manifests derived from saved receipts and actual files."""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .preflight import CONFIG, PUBLIC, ROOT, dump, sha

DEPENDENCIES = [
    "configs/news_sentiment_factor_v45_20261117.json",
    "configs/news_sentiment_factor_v42_us_calendar_2026.json",
    "configs/news_sentiment_factor_v43_security_mapping_20261117.json",
    "scripts/news_sentiment_factor_v45/preflight.py",
    "scripts/news_sentiment_factor_v45/metadata.py",
    "scripts/news_sentiment_factor_v45/daily_prices.py",
    "scripts/news_sentiment_factor_v45/labels.py",
    "scripts/news_sentiment_factor_v45/worker.py",
    "scripts/news_sentiment_factor_v45/audit.py",
    "scripts/news_sentiment_factor_v45/support.py",
    "scripts/news_sentiment_factor_v45/test_v45.py",
    "scripts/news_sentiment_factor_v45/finalize.py",
    "scripts/news_sentiment_factor_v45/validate.py",
    "scripts/news_sentiment_factor_v45/final_runtime.py",
    "scripts/news_sentiment_factor_v45/package.py",
    "scripts/news_sentiment_factor_v44/labels.py",
    "scripts/news_sentiment_factor_v43/daily.py",
    "apps/quant_portal/core/research_chinese_cache.py",
    "apps/quant_portal/static/core/research_sentiment_v4.js",
    "apps/quant_portal/templates/core/dashboard.html",
]


def run() -> dict:
    at = datetime.now(timezone.utc)
    files = [{"path": p, "sha256": sha(ROOT / p)} for p in DEPENDENCIES]
    manifest = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    audit = json.loads((PUBLIC / "snapshot_acceptance_redacted.json").read_text(encoding="utf-8"))
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    result = {"schema": "news_sentiment_factor_v45_postrun_hashes_redacted_1",
              "generated_at_utc": at.isoformat(),
              "generated_at_asia_shanghai": at.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
              "baseline_head_before_commit": head,
              "dependency_count": len(files), "dependency_manifest_sha256": manifest,
              "dependencies": files,
              "frozen_input_receipt": "results/news_sentiment_factor_v45_20261117/preflight_redacted.json",
              "source_readback_receipt_sha256": sha(PUBLIC / "snapshot_acceptance_redacted.json")}
    dump(PUBLIC / "postrun_hashes_redacted.json", result)
    price = {"schema": "news_sentiment_factor_v45_price_qualification_redacted_1",
             "as_of_utc": audit["as_of_utc"],
             "pilot_source_matched_field_count": sum(x["open_close_fields_source_matched"] for x in audit["pilot"].values()),
             "pilot_currency_linked_field_count": sum(x["open_close_fields_currency_linked"] for x in audit["pilot"].values()),
             "by_symbol": {symbol: {k: row[k] for k in (
                 "completed_regular_daily_bars", "open_close_fields_source_matched",
                 "open_close_fields_currency_linked", "verified_auction_open_bars")}
                 for symbol, row in audit["pilot"].items()},
             "v45_new_collected_daily_bars": audit["v45_new_price_observations"] // 2,
             "pilot_price_dates_are_not_forward_signal_labels": True,
             "current_qualified_labels": audit["current_qualified_labels"],
             "current_qualified_pairs": audit["current_qualified_pairs"],
             "currency_method": "external cross-source USD inference; original moomoo currency field remains UNKNOWN",
             "effective_initial_metadata_end": "2026-11-17",
             "future_days": "require per-day archived bar and exact security-name match"}
    dump(PUBLIC / "price_qualification_redacted.json", price)
    label = {"schema": "news_sentiment_factor_v45_label_gate_redacted_1",
             "as_of_utc": audit["as_of_utc"], "snapshot_batches": audit["snapshot_counts"]["daily_snapshots"],
             "released_batches": audit["snapshot_counts"]["daily_snapshot_releases"],
             "label_queue_rows": audit["snapshot_counts"]["daily_label_queue"],
             "current_qualified_labels": audit["current_qualified_labels"],
             "current_factor_return_pairs": audit["current_qualified_pairs"],
             "new_v45_action_intervals": audit["v45_new_action_intervals"],
             "supplier_requests": audit["v45_supplier_requests"],
             "old_pilot_action_lists_not_upgraded_to_future_holdings": True,
             "no_fit_ic_backtest_or_trade": True}
    dump(PUBLIC / "label_gate_redacted.json", label)
    return {"postrun": result["dependency_manifest_sha256"], "price": price, "label": label}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
