"""Read-only redacted v4.4 runtime and first-cutoff acceptance receipt."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from scripts.news_sentiment_factor_v41.score import DB as SCORE_DB
from scripts.news_sentiment_factor_v41.worker import utc
from scripts.news_sentiment_factor_v42.worker import QUEUE_DB, status
from scripts.news_sentiment_factor_v43.daily import _complete_versions, rule_effective
from scripts.news_sentiment_factor_v44.preflight import PUBLIC, ROOT
from scripts.news_sentiment_factor_v44.labels import DB as LABEL_DB


def read() -> dict:
    when = datetime.now(timezone.utc)
    with closing(sqlite3.connect(f"file:{QUEUE_DB.as_posix()}?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        rule_at = rule_effective(con)
        batches = [dict(x) for x in con.execute("""SELECT s.snapshot_id,s.kind,s.window_start_utc,
            s.scheduled_cutoff_utc,s.generation_started_at_utc,c.completion_recorded_at_utc,
            r.signal_available_at_utc,w.release_commit_observed_at_utc,s.input_count
            FROM daily_snapshots s
            LEFT JOIN daily_snapshot_completions c USING(snapshot_id)
            LEFT JOIN daily_snapshot_releases r USING(snapshot_id)
            LEFT JOIN daily_snapshot_publication_witness w USING(snapshot_id)
            ORDER BY s.scheduled_cutoff_utc""")]
        real = [x for x in batches if x["kind"] in ("ON_TIME_SCHEDULED", "LATE_SCHEDULED")]
        released = [x for x in real if x["release_commit_observed_at_utc"] and
                    x["signal_available_at_utc"] and
                    utc(x["release_commit_observed_at_utc"]) <= utc(x["signal_available_at_utc"]) <= when]
        real_ids = [x["snapshot_id"] for x in real]
        if real_ids:
            slots = ",".join("?" for _ in real_ids)
            company = con.execute(f"SELECT count(*),sum(n1 IS NOT NULL),sum(n2_observed) FROM daily_snapshot_company WHERE snapshot_id IN ({slots})", real_ids).fetchone()
            labels = con.execute(f"SELECT status,count(*) FROM daily_label_queue WHERE snapshot_id IN ({slots}) GROUP BY status", real_ids).fetchall()
            company_rows, effective_n1, groups, label_status = company[0], company[1] or 0, company[2] or 0, dict(labels)
        else:
            company_rows = effective_n1 = groups = 0
            label_status = {}
        v42_count = con.execute("SELECT count(*) FROM snapshots").fetchone()[0]
    complete, excluded = _complete_versions(when, True, rule_at, SCORE_DB)
    with closing(sqlite3.connect(f"file:{LABEL_DB.as_posix()}?mode=ro", uri=True)) as con:
        pilot = {k: con.execute(f"SELECT count(*) FROM {k}").fetchone()[0]
                 for k in ("price_evidence", "action_evidence", "label_versions")}
        label_keys = con.execute("""SELECT snapshot_id,entity_id FROM label_versions
            GROUP BY snapshot_id,entity_id,horizon_sessions,contract""").fetchall()
    with closing(sqlite3.connect(f"file:{QUEUE_DB.as_posix()}?mode=ro", uri=True)) as con:
        pairs = sum(con.execute("SELECT n1 FROM daily_snapshot_company WHERE snapshot_id=? AND entity_id=?",
                                key).fetchone() is not None and
                    con.execute("SELECT n1 FROM daily_snapshot_company WHERE snapshot_id=? AND entity_id=?",
                                key).fetchone()[0] is not None for key in label_keys)
    feed_path = ROOT / "apps/quant_portal/var/personal_feeds.sqlite3"
    with closing(sqlite3.connect(f"file:{feed_path.as_posix()}?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        feed = {x["provider"]: dict(x) for x in con.execute("""SELECT provider,state,heartbeat_at,
            last_success_at,last_error_class,worker_alive,business_attempts_today,business_success_total,
            business_failure_total FROM provider_state WHERE provider IN ('moomoo','moomoo_snapshot','jin10')""")}
    worker = status()
    result = {"schema": "news_sentiment_v44_runtime_audit_1", "checked_at_utc": when.isoformat(),
              "checked_at_asia_shanghai": when.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
              "first_scheduled_cutoff_utc": "2026-11-17T21:15:00+00:00",
              "first_cutoff_due_at_check": when >= utc("2026-11-17T21:15:00+00:00"),
              "v42_cumulative_development_snapshots": v42_count,
              "v43_real_snapshot_batches": len(real), "v43_released_batches": len(released),
              "v43_company_rows": company_rows, "v43_nonmissing_n1_company_rows": effective_n1,
              "v43_observed_group_contributions": groups,
              "v43_label_queue_statuses": label_status,
              "complete_prospective_article_versions_at_read": len(complete),
              "excluded_version_counts": excluded,
              "research_worker": {k: worker.get(k) for k in
                  ("state", "run_id", "heartbeat_at_utc", "process_alive_identity", "daily_limit",
                   "daily_attempts", "backlog", "daily_snapshot_rule_version", "daily_snapshot_state",
                   "daily_next_cutoff_utc")},
              "feed": feed, "price_private_table_counts": pilot,
              "daily_bar_label_targets": len(label_keys), "daily_bar_label_versions": pilot["label_versions"],
              "daily_bar_factor_return_pairs": pairs,
              "v44_new_model_inference_calls": 0, "return_fit_calls": 0, "return_ic_calls": 0,
              "existing_chinese_scoring_worker_is_separate": True}
    PUBLIC.mkdir(parents=True, exist_ok=True)
    (PUBLIC / "runtime_audit_redacted.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(read(), ensure_ascii=False))
