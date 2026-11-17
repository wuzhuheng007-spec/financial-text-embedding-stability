"""Read-only, redacted source/queue/runtime acceptance for the v4.5 pilot."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from scripts.news_sentiment_factor_v44.labels import DB, QUEUE
from .metadata import PILOT, _verified_sources, resolve_currency
from .preflight import PUBLIC, ROOT, dump, sha
from .worker import status as worker_status


def _count(con: sqlite3.Connection, table: str) -> int:
    return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def run() -> dict:
    at = datetime.now(timezone.utc)
    source_hashes = _verified_sources()
    pilot = {}
    with closing(sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=10)) as con:
        con.row_factory = sqlite3.Row
        for symbol in ("NVDA", "MU"):
            raw = next(PILOT.glob(f"{symbol}_history_*.json"))
            source = json.loads(raw.read_text(encoding="utf-8"))
            bars = {str(x["date"]): x for x in source["data"]["kline_list"]}
            rows = con.execute("""SELECT * FROM price_evidence WHERE security_id=?
                AND trade_date IN ('2026-11-13','2026-11-16') ORDER BY trade_date,field""",
                (f"SEC:{symbol}:US",)).fetchall()
            field_matches = 0
            currency_links = 0
            details = []
            for row in rows:
                bar = bars[row["trade_date"].replace("-", "")]
                field = row["field"].lower()
                same = (row["price_text"] == str(bar[field]) and row["raw_sha256"] == sha(raw)
                        and row["provider_code"] == f"US.{symbol}" and row["session"] == "REGULAR"
                        and row["adjustment"] == "UNADJUSTED_AUTYPE_0")
                if not same:
                    raise ValueError(f"PILOT_PRICE_SOURCE_MISMATCH_{symbol}_{row['trade_date']}_{field}")
                field_matches += 1
                currency, metadata_id, basis = resolve_currency(con, row["evidence_id"], at)
                if currency == "USD" and metadata_id and basis == "EXTERNAL_EVIDENCE_CHAIN_NOT_PROVIDER_FIELD":
                    currency_links += 1
                details.append({"trade_date": row["trade_date"], "field": row["field"],
                                "raw_sha256": row["raw_sha256"], "source_match": same,
                                "currency_link_state": basis, "metadata_id": metadata_id})
            pilot[symbol] = {"completed_regular_daily_bars": len({x["trade_date"] for x in rows}),
                             "open_close_fields_source_matched": field_matches,
                             "open_close_fields_currency_linked": currency_links,
                             "verified_auction_open_bars": 0, "source_checks": details}
        metadata = [dict(x) for x in con.execute("""SELECT m.security_id,m.provider_code,m.quote_currency,
            m.valid_from,m.valid_to AS recorded_valid_to,
            COALESCE(c.effective_valid_to,m.valid_to) AS effective_valid_to,
            m.observed_at_utc,m.metadata_id,m.verification_method
            FROM v45_security_metadata m LEFT JOIN v45_metadata_scope_corrections c
            ON c.metadata_id=m.metadata_id ORDER BY m.security_id,m.observed_at_utc""")]
        request_count = _count(con, "v45_supplier_requests")
        new_price_observations = _count(con, "v45_price_observations")
        new_action_intervals = _count(con, "v45_action_coverage")
        total_label_versions = _count(con, "label_versions")
        current_qualified_keys = [tuple(row) for row in con.execute("""SELECT snapshot_id,entity_id,horizon_sessions FROM (
            SELECT snapshot_id,entity_id,horizon_sessions,state,
              ROW_NUMBER() OVER (PARTITION BY snapshot_id,entity_id,horizon_sessions
              ORDER BY decision_at_utc DESC,decision_id DESC) AS rn FROM v45_label_decisions)
            WHERE rn=1 AND state='CURRENT_QUALIFIED'""")]
        current_qualified = len(current_qualified_keys)
    with closing(sqlite3.connect(f"file:{QUEUE.as_posix()}?mode=ro", uri=True, timeout=10)) as con:
        queue_counts = {table: _count(con, table) for table in (
            "daily_snapshots", "daily_snapshot_completions", "daily_snapshot_releases",
            "daily_snapshot_publication_witness", "daily_snapshot_company", "daily_label_queue")}
        n1_nonmissing = con.execute("SELECT COUNT(*) FROM daily_snapshot_company WHERE n1 IS NOT NULL").fetchone()[0]
        factor_keys = {tuple(row) for row in con.execute(
            "SELECT snapshot_id,entity_id FROM daily_snapshot_company WHERE n1 IS NOT NULL")}
        current_pairs = sum((sid, entity) in factor_keys for sid, entity, _ in current_qualified_keys)
        event_group_contributions = con.execute("SELECT COALESCE(SUM(n2_observed),0) FROM daily_snapshot_company").fetchone()[0]
    feed = ROOT / "apps/quant_portal/var/personal_feeds.sqlite3"
    with closing(sqlite3.connect(f"file:{feed.as_posix()}?mode=ro", uri=True, timeout=10)) as con:
        con.row_factory = sqlite3.Row
        feeds = [dict(row) for row in con.execute("""SELECT provider,state,last_success_at,
            last_error_class,worker_alive FROM provider_state
            WHERE provider IN ('jin10','moomoo','moomoo_snapshot') ORDER BY provider""")]
    research_path = ROOT / "results/news_sentiment_factor_v42_20261117/worker_status_redacted.json"
    research = json.loads(research_path.read_text(encoding="utf-8")) if research_path.is_file() else {}
    from scripts.news_sentiment_factor_v43.daily import regular_session_close
    from datetime import date, timedelta
    first_cutoff = regular_session_close(date(2026, 11, 17)) + timedelta(minutes=15)
    result = {"schema": "news_sentiment_factor_v45_acceptance_redacted_1",
              "as_of_utc": at.isoformat(),
              "as_of_asia_shanghai": at.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
              "first_planned_daily_cutoff_utc": first_cutoff.isoformat(),
              "first_cutoff_due_at_check": at >= first_cutoff,
              "source_hashes": source_hashes,
              "metadata": metadata, "pilot": pilot,
              "v45_supplier_requests": request_count,
              "v45_new_price_observations": new_price_observations,
              "v45_new_action_intervals": new_action_intervals,
              "snapshot_counts": queue_counts,
              "n1_nonmissing_company_rows": n1_nonmissing,
              "observed_event_group_contributions": event_group_contributions,
              "label_versions_all": total_label_versions,
              "current_qualified_labels": current_qualified,
              "current_qualified_pairs": current_pairs,
              "feed_states": feeds,
              "research_worker": {k: research.get(k) for k in (
                  "state", "heartbeat_at_utc", "last_success_at_utc", "daily_snapshot_state",
                  "daily_next_cutoff_utc", "daily_snapshot_rule_version", "pause_reason")},
              "daily_price_worker": worker_status(),
              "no_return_fit_ic_backtest_or_trade": True}
    dump(PUBLIC / "snapshot_acceptance_redacted.json", result)
    return result


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
