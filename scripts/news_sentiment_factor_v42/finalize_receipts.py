"""Create aggregate v4.2 receipts; never export news, scores or account identifiers."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.news_sentiment_factor_v41.score import DB as SCORE_DB
from .worker import PRIVATE, PUBLIC, QUEUE_DB, ROOT, status, write_json


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect() -> dict:
    current = datetime.now(timezone.utc)
    observed = [json.loads(line) for line in
                (PRIVATE / "observation_15m_private.jsonl").read_text(encoding="utf-8").splitlines()]
    times = [datetime.fromisoformat(row["sampled_at_utc"]) for row in observed]
    monitoring = {"samples": len(observed), "first_utc": times[0].isoformat(),
                  "last_utc": times[-1].isoformat(),
                  "duration_seconds": round((times[-1] - times[0]).total_seconds(), 3),
                  "maximum_sample_interval_seconds": round(max((b-a).total_seconds()
                        for a,b in zip(times,times[1:])),3),
                  "states": dict(Counter(row["state"] for row in observed)),
                  "last_backlog": observed[-1]["backlog"],
                  "last_daily_attempts": observed[-1]["daily_attempts"]}
    with sqlite3.connect(f"file:{SCORE_DB.as_posix()}?mode=ro",uri=True) as con:
        con.row_factory=sqlite3.Row
        cohort={row["cohort"]:row["n"] for row in
                con.execute("SELECT cohort,count(*) n FROM articles GROUP BY cohort")}
        scored_versions=con.execute("""SELECT count(DISTINCT provider||':'||article_id||':'||version)
            FROM scores WHERE score_origin='PROSPECTIVE_RECEIPT' AND status='DONE'""").fetchone()[0]
        scored_units=con.execute("""SELECT count(*) FROM scores WHERE score_origin='PROSPECTIVE_RECEIPT'
            AND status='DONE'""").fetchone()[0]
    with sqlite3.connect(f"file:{QUEUE_DB.as_posix()}?mode=ro",uri=True) as con:
        con.row_factory=sqlite3.Row
        jobs={row["state"]:row["n"] for row in con.execute("SELECT state,count(*) n FROM jobs GROUP BY state")}
        attempts=con.execute("SELECT count(*) FROM attempts").fetchone()[0]
        delayed=con.execute("SELECT count(*) FROM jobs WHERE recovery_delayed=1 AND state='DONE'").fetchone()[0]
        snapshots={row["kind"]:row["n"] for row in
                   con.execute("SELECT kind,count(*) n FROM snapshots GROUP BY kind")}
        labels={row["status"]:row["n"] for row in
                con.execute("SELECT status,count(*) n FROM future_label_queue GROUP BY status")}
    selected=[
      "configs/news_sentiment_factor_v42_20261117.json",
      "configs/news_sentiment_factor_v42_aliases_20261117.json",
      "configs/news_sentiment_factor_v42_snapshot_r2_20261117.json",
      "configs/news_sentiment_factor_v42_us_calendar_2026.json",
      "configs/news_sentiment_factor_v42_future_evaluation_20261117.json",
      "configs/news_sentiment_factor_v41_20261117_r2.json",
      "scripts/news_sentiment_factor_v42/worker.py",
      "scripts/news_sentiment_factor_v42/association.py",
      "scripts/news_sentiment_factor_v42/snapshot.py",
      "scripts/news_sentiment_factor_v42/verify_snapshot.py",
      "scripts/news_sentiment_factor_v42/fault_drills.py",
      "apps/quant_portal/core/research_chinese_cache.py",
      "apps/quant_portal/static/core/research_sentiment_v4.js",
    ]
    receipt={"schema":"news_sentiment_factor_v42_final_counts_1",
             "checked_at_utc":current.isoformat(),
             "checked_at_asia_shanghai":current.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
             "earlier_observations":[
               {"as_of_utc":"2026-11-17T07:15:44Z","source":"v41_prospective_score_observation_redacted.json",
                "distinct_new_versions":1,"run_id":"v41_observe_20261117T071145Z"},
               {"as_of_utc":"2026-11-17T07:19:23Z","source":"v41_final_runtime_readback_local.json",
                "distinct_new_versions":2,"run_id":"v41_observe_20261117T071145Z"}],
             "current_score_db":{"cohort_article_versions":cohort,
                                 "prospective_scored_article_versions":scored_versions,
                                 "prospective_scored_units":scored_units},
             "v42_queue":{"job_states":jobs,"attempt_rows":attempts,
                          "completed_delayed_recovery_jobs":delayed,
                          "current_status":{k:v for k,v in status().items()
                              if k in {"state","run_id","started_at_utc","heartbeat_at_utc",
                                       "last_success_at_utc","backlog","daily_attempts","daily_limit",
                                       "process_alive_identity","snapshot_state","next_cutoff_utc",
                                       "quarantined"}}},
             "snapshots_by_kind":snapshots,"future_label_queue_states":labels,
             "observation_15_minutes":monitoring,
             "code_config_sha256":{name:digest(ROOT/name) for name in selected},
             "private_backup_integrity":"THREE_SQLITE_BACKUPS_PRAGMA_OK",
             "no_return_labels_read":True,"no_return_model_fit":True,
             "not_a_prospective_snapshot_if_before_true_cutoff":True}
    write_json(PUBLIC/"final_counts_redacted.json",receipt)
    return receipt


if __name__=="__main__":
    print(json.dumps(collect(),ensure_ascii=False))
