"""Redacted v4.3 source, correction and runtime audit. No article text is exported."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.news_sentiment_factor_v41.freeze import ROOT, sha
from scripts.news_sentiment_factor_v41.score import DB as SCORE_DB
from scripts.news_sentiment_factor_v42.worker import QUEUE_DB, status, write_json
from .daily import _complete_versions, config, rule_effective, window_for, cutoff_for

PUBLIC = ROOT / "results/news_sentiment_factor_v43_20261117"
OLD_ZIP = Path(r"C:\Users\ROG\Documents\ChatGPT\quant\handoffs\news_sentiment_factor_v42_20261117_final.zip")
OLD_INDEX = ROOT / "results/news_sentiment_factor_v42_20261117/evidence_index_redacted.json"
SH = ZoneInfo("Asia/Shanghai")


def run() -> dict:
    when = datetime.now(timezone.utc)
    with zipfile.ZipFile(OLD_ZIP) as package:
        entries = package.infolist()
        bad = package.testzip()
        names = [x.filename for x in entries]
    index = json.loads(OLD_INDEX.read_text(encoding="utf-8"))
    share = index["redacted_shareable"]
    duplicates = sorted(name for name, number in Counter(x["name"] for x in share).items() if number > 1)
    correction = {"schema": "news_sentiment_v43_v42_zip_count_correction_1",
                  "checked_at_utc": when.isoformat(), "checked_at_asia_shanghai": when.astimezone(SH).isoformat(),
                  "old_zip_sha256": sha(OLD_ZIP), "old_index_sha256": sha(OLD_INDEX),
                  "zip_total_entries": len(entries), "zip_file_entries": sum(not x.is_dir() for x in entries),
                  "zip_unique_file_paths": len(set(names)), "index_rows": len(share),
                  "index_unique_paths": len({x["name"] for x in share}),
                  "duplicated_index_names": duplicates, "zip_crc_first_bad_member": bad,
                  "prior_zip_and_index_unchanged": True}
    write_json(PUBLIC / "v42_zip_count_correction_redacted.json", correction)
    with closing(sqlite3.connect(f"file:{QUEUE_DB.as_posix()}?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        v42 = dict(con.execute("SELECT kind,COUNT(*) FROM snapshots GROUP BY kind").fetchall())
        rule_at = rule_effective(con)
        v43_exists = bool(con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='daily_snapshots'").fetchone())
        v43 = dict(con.execute("SELECT kind,COUNT(*) FROM daily_snapshots GROUP BY kind").fetchall()) if v43_exists else {}
        released = con.execute("""SELECT COUNT(*) FROM daily_snapshot_releases r
            JOIN daily_snapshot_publication_witness w USING(snapshot_id)
            WHERE r.signal_available_at_utc<=?""", (when.isoformat(),)).fetchone()[0] if v43_exists else 0
    complete, excluded = _complete_versions(when, True, rule_at)
    with closing(sqlite3.connect(f"file:{SCORE_DB.as_posix()}?mode=ro", uri=True)) as con:
        scored = con.execute("SELECT COUNT(*) FROM scores WHERE status='DONE'").fetchone()[0]
        prospective = con.execute("SELECT COUNT(*) FROM articles WHERE cohort='PROSPECTIVE_RECEIPT'").fetchone()[0]
    worker = status()
    next_cutoff = cutoff_for(when.astimezone(ZoneInfo("America/New_York")).date())
    redacted = {"schema": "news_sentiment_v43_runtime_audit_1", "checked_at_utc": when.isoformat(),
                "checked_at_asia_shanghai": when.astimezone(SH).isoformat(),
                "head_at_start": config()["baseline_commit"],
                "scoring_config_sha256": sha(ROOT / config()["existing_scoring_config"]),
                "v43_config_sha256": sha(ROOT / "configs/news_sentiment_factor_v43_20261117.json"),
                "mapping_receipt_sha256": sha(ROOT / "results/watchlist_live_20261115/security_mapping_combined_redacted.json"),
                "score_db_snapshot_counts": {"done_segments": scored, "prospective_article_versions": prospective,
                                             "complete_version_candidates_at_read": len(complete),
                                             "excluded_version_counts": excluded},
                "v42_snapshots_by_kind": v42, "v43_snapshots_by_kind": v43,
                "v43_released_at_read": released,
                "worker": {k: worker.get(k) for k in ("state", "run_id", "heartbeat_at_utc", "daily_attempts",
                                                       "daily_limit", "backlog", "daily_snapshot_state",
                                                       "daily_snapshot_rule_version", "daily_next_cutoff_utc")},
                "next_same_day_verified_cutoff_utc": next_cutoff.isoformat(),
                "score_inference_in_this_audit": 0, "return_labels_read": 0, "return_fit": 0}
    write_json(PUBLIC / "runtime_audit_redacted.json", redacted)
    return {"correction": correction, "runtime": redacted}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
