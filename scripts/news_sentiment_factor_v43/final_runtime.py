"""Read-only redacted final status; never exports source text or security prices."""
from __future__ import annotations

import json
import sqlite3
import subprocess
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.news_sentiment_factor_v41.freeze import ROOT
from scripts.news_sentiment_factor_v41.score import DB as SCORE_DB
from scripts.news_sentiment_factor_v42.worker import QUEUE_DB, status
from .daily import _complete_versions, rule_effective

OUT = ROOT / "results/news_sentiment_factor_v43_20261117/final_runtime_redacted.json"


def ops(script: str) -> str:
    result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                             "-File", str(ROOT / script), "status"],
                            cwd=ROOT, text=True, encoding="utf-8", errors="replace",
                            capture_output=True, timeout=30, check=True)
    return result.stdout


def run() -> dict:
    when = datetime.now(timezone.utc)
    portal = ops("apps/quant_portal/ops/local.ps1")
    feed = ops("apps/quant_portal/ops/feed_personal.ps1")
    feed_data = json.loads(next(x for x in feed.splitlines() if x.startswith("{")))
    worker = status()
    with closing(sqlite3.connect(f"file:{QUEUE_DB.as_posix()}?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        rule_at = rule_effective(con)
        v42 = dict(con.execute("SELECT kind,COUNT(*) FROM snapshots GROUP BY kind"))
        v43 = dict(con.execute("SELECT kind,COUNT(*) FROM daily_snapshots GROUP BY kind"))
        released = con.execute("""SELECT COUNT(*) FROM daily_snapshot_releases r
            JOIN daily_snapshot_publication_witness w USING(snapshot_id)
            WHERE r.signal_available_at_utc<=?
              AND w.release_commit_observed_at_utc<=r.signal_available_at_utc""",
            (when.isoformat(),)).fetchone()[0]
        labels = con.execute("SELECT COUNT(*) FROM daily_label_queue").fetchone()[0]
        failures = [dict(x) for x in con.execute("""SELECT error_class,COUNT(*) AS attempts
            FROM attempts WHERE started_at>=? AND state='FAILED'
            GROUP BY error_class ORDER BY error_class""",
            ("2026-11-17T08:24:48+00:00",))]
    complete, excluded = _complete_versions(when, True, rule_at, SCORE_DB)
    with closing(sqlite3.connect(f"file:{SCORE_DB.as_posix()}?mode=ro", uri=True)) as con:
        prospective_versions = con.execute(
            "SELECT COUNT(*) FROM articles WHERE cohort='PROSPECTIVE_RECEIPT'").fetchone()[0]
        done_units = con.execute("SELECT COUNT(*) FROM scores WHERE status='DONE'").fetchone()[0]
    portal_db = ROOT / "apps/quant_portal/var/dev.sqlite3"
    with closing(sqlite3.connect(f"file:{portal_db.as_posix()}?mode=ro", uri=True)) as con:
        personal_counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                           for t in ("auth_user", "core_watchlistitem", "core_publicfactcard", "core_userpreferences")}
    completed = datetime.now(timezone.utc)
    receipt = {"schema": "news_sentiment_v43_final_runtime_redacted_1",
               "audit_started_at_utc": when.isoformat(),
               "audit_completed_at_utc": completed.isoformat(),
               "audit_completed_at_asia_shanghai": completed.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
               "score_candidate_cutoff_utc": when.isoformat(),
               "research_worker": {k: worker.get(k) for k in
                   ("state", "run_id", "heartbeat_at_utc", "process_alive_identity", "stage", "daily_limit",
                    "daily_attempts", "backlog", "research_origin_utc", "daily_snapshot_rule_version",
                    "daily_snapshot_state", "daily_next_cutoff_utc")},
               "scoring_attempt_failures_since_lock": failures,
               "portal_running": portal.lstrip().startswith("RUNNING"),
               "feed_process_running": feed.lstrip().startswith("FEED_RUNNING"),
               "feed_provider_states": {x["provider"]: {"state": x["state"], "worker_alive": x["worker_alive"],
                       "heartbeat_at": x["heartbeat_at"], "last_success_at": x["last_success_at"],
                       "coverage_gap": x["coverage_gap"]} for x in feed_data["states"]},
               "personal_db_counts": personal_counts,
               "v42_snapshots_by_kind": v42, "v43_snapshots_by_kind": v43,
               "v43_released_at_read": released, "v43_real_label_queue_rows": labels,
               "chinese_score_db": {"prospective_article_versions": prospective_versions,
                                    "complete_prospective_versions_at_read": len(complete),
                                    "done_segment_units_all_cohorts": done_units,
                                    "excluded_version_counts": excluded},
               "v43_model_inference": 0, "return_labels_computed": 0,
               "return_fit": 0, "return_ic": 0,
               "first_real_daily_cutoff": "2026-11-17T21:15:00+00:00"}
    OUT.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return receipt


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
