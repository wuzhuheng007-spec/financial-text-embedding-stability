"""Targeted isolated checks. Synthetic rows never enter the private research DB."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.news_sentiment_factor_v41.freeze import sha
from scripts.news_sentiment_factor_v41.score import init as score_init
from scripts.news_sentiment_factor_v42.association import RULES, VERSION
from .daily import (_complete_versions, _label_rows, _members, config, create_snapshot,
                    cutoff_for, eligible_next, schema, window_for)


def version(article: str, revision: int, group: str, received: str, scored: str,
            tone: float, entity: str = "NVDA") -> dict:
    return {"provider": "jin10", "article_id": article, "version": revision,
            "group_id": group, "source_hash": f"hash-{article}-{revision}",
            "received": received, "available": scored,
            "segments": [{"segment_id": "body", "kind": "BODY_SEGMENT",
                          "status": "ONE_EXPLICIT_COMPANY", "entity_id": entity, "tone": tone}]}


def run() -> dict:
    checks = {}
    start = datetime(2026, 11, 16, 21, 15, tzinfo=timezone.utc)
    end = cutoff_for(datetime(2026, 11, 17).date())
    exact = start.isoformat()
    within = (start + timedelta(minutes=1)).isoformat()
    late = (start + timedelta(hours=2)).isoformat()
    rows = [version("old", 1, "old-group", "2026-11-16T20:00:00+00:00",
                    "2026-11-16T20:02:00+00:00", 0.2),
            version("boundary", 1, "boundary-group", exact, exact, 0.0),
            version("new", 1, "new-group", within, late, -0.3)]
    selected, _ = _members(rows, start, end)
    first_ids = {r["article_id"] for r in selected if r["member_kind"] == "FIRST_EVENT"}
    checks["old_news_excluded"] = "old" not in first_ids
    checks["exclusive_boundary"] = "boundary" not in first_ids and "new" in first_ids
    before_score, _ = _members(rows, start, start + timedelta(hours=1))
    checks["late_scoring"] = "new" not in {x["article_id"] for x in before_score}
    revised = rows + [version("new", 2, "new-group-v2", within,
                              (start + timedelta(hours=3)).isoformat(), 0.4)]
    selected, updates = _members(revised, start, end)
    checks["revision_no_double_first"] = sum(x["member_kind"] == "FIRST_EVENT" and
                                              x["article_id"] == "new" for x in selected) == 1
    friday = cutoff_for(datetime(2026, 11, 13).date())
    monday = cutoff_for(datetime(2026, 11, 16).date())
    wstart, _, _ = window_for(monday)
    _, _, first_state = window_for(end)
    checks["weekend_holiday_partial"] = (wstart == friday and first_state == "PARTIAL_INITIAL_WINDOW"
                                          and eligible_next(datetime(2026, 11, 25).date()).isoformat() == "2026-11-27")
    mapping = {"NVDA": {"security_id": "SEC:NVDA:US", "verification": "CURRENT_US_PROVIDER_CODE_VERIFIED"}}
    planned_entry = datetime(2026, 11, 18, 14, 30, tzinfo=timezone.utc)
    label = _label_rows(["NVDA", "SKHYNIX"], end, end + timedelta(minutes=1), mapping,
                        observed_at=end)
    checks["unverified_security_block"] = all(x["status"] == "BLOCKED_SECURITY_MAPPING"
                                                  and x["entry_at"] is None for x in label if x["entity_id"] == "SKHYNIX")
    missed = _label_rows(["NVDA"], end, planned_entry + timedelta(seconds=1), mapping,
                         observed_at=planned_entry + timedelta(seconds=1))
    checks["late_signal_missed_entry"] = all(x["status"] == "MISSED_ENTRY" for x in missed)
    no_event, _ = _members(rows[:1], start, end)
    neutral, _ = _members(rows[1:2], start - timedelta(seconds=1), end)
    checks["no_news_vs_neutral"] = not no_event and len(neutral) == 1 and neutral[0]["tone"] == 0.0
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        score_db, queue_db = root / "score.sqlite3", root / "queue.sqlite3"
        score_init(score_db)
        with closing(sqlite3.connect(queue_db)) as con, con:
            con.execute("CREATE TABLE association_revisions(rule_version TEXT,rule_sha256 TEXT,created_at_utc TEXT)")
            con.execute("INSERT INTO association_revisions VALUES(?,?,?)",
                        (VERSION, sha(RULES), "2026-11-16T00:00:00+00:00"))
        cutoff = cutoff_for(datetime(2026, 11, 16).date())
        try:
            create_snapshot(cutoff.isoformat(), score_db=score_db, queue_db=queue_db, fail_after_data=True)
        except RuntimeError as exc:
            interrupted = str(exc) == "SIMULATED_INTERRUPTION_AFTER_DATA_COMMIT"
        else:
            interrupted = False
        with closing(sqlite3.connect(queue_db)) as con, con:
            data_count = con.execute("SELECT COUNT(*) FROM daily_snapshots").fetchone()[0]
            release_count = con.execute("SELECT COUNT(*) FROM daily_snapshot_releases").fetchone()[0]
        checks["interrupted_no_release"] = interrupted and data_count == 1 and release_count == 0
        first = create_snapshot(cutoff.isoformat(), score_db=score_db, queue_db=queue_db)
        second = create_snapshot(cutoff.isoformat(), score_db=score_db, queue_db=queue_db)
        checks["duplicate_keeps_kind_time"] = (first["kind"] == second["kind"] and
             first["generation_started_at_utc"] == second["generation_started_at_utc"] and
             first["signal_available_at_utc"] == second["signal_available_at_utc"] and
             second["status"] == "ALREADY_EXISTS_UNCHANGED")
        # A witness committed after the promised availability time must not
        # make a snapshot readable, even if all append-only rows exist.
        with closing(sqlite3.connect(queue_db)) as con, con:
            con.execute("INSERT INTO daily_snapshot_releases VALUES(?,?,?,?)",
                        ("late-witness", "2026-11-16T00:00:00+00:00",
                         "2026-11-16T00:00:01+00:00", "SYNTHETIC"))
            con.execute("INSERT INTO daily_snapshot_publication_witness VALUES(?,?)",
                        ("late-witness", "2026-11-16T00:00:02+00:00"))
            visible = con.execute("""SELECT COUNT(*) FROM daily_snapshot_releases r
                JOIN daily_snapshot_publication_witness w USING(snapshot_id)
                WHERE snapshot_id='late-witness'
                  AND w.release_commit_observed_at_utc<=r.signal_available_at_utc""").fetchone()[0]
        checks["late_witness_hidden"] = visible == 0
        # A mismatched score configuration cannot create an eligible article.
        with closing(sqlite3.connect(score_db)) as con, con:
            con.execute("""INSERT INTO articles VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        ("jin10", "synthetic", 1, None, None, "英伟达", "英伟达发布消息。",
                         None, "2026-11-16T01:00:00+00:00", "2026-11-16T01:00:00+00:00",
                         "source-hash", "raw-hash", "synthetic", "PROSPECTIVE_RECEIPT", "event", "synthetic", "ORIGINAL"))
            con.execute("""INSERT INTO segments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        ("jin10", "synthetic", 1, "s1", "BODY_SEGMENT", "英伟达发布消息。", "input-hash",
                         8, "body", 0, 9, "SYNTHETIC", "[]", "NVDA", "ONE_EXPLICIT_COMPANY",
                         "REPORT", "event", config()["segment_rule_version"]))
            con.execute("""INSERT INTO scores VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        ("jin10", "synthetic", 1, "s1", config()["segment_rule_version"],
                         config()["model_revision"], "wrong-config", "input-hash", 8, .2, .5, .3, .2,
                         "DONE", None, "cpu", "2026-11-16T01:01:00+00:00",
                         "2026-11-16T01:02:00+00:00", "2026-11-16T01:00:00+00:00",
                         "2026-11-16T01:02:00+00:00", "PROSPECTIVE_RECEIPT", "synthetic-run"))
        eligible, excluded = _complete_versions(cutoff, True, datetime(2026, 11, 16, tzinfo=timezone.utc), score_db)
        checks["scoring_config_isolation"] = not eligible and excluded.get("PARTIAL_OR_MISSING_SCORE") == 1
        with closing(sqlite3.connect(score_db)) as con, con:
            con.execute("""INSERT INTO segments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        ("jin10", "synthetic", 1, "s2", "BODY_SEGMENT", "英伟达补充说明。", "input-hash-2",
                         8, "body", 9, 18, "SYNTHETIC", "[]", "NVDA", "ONE_EXPLICIT_COMPANY",
                         "REPORT", "event", config()["segment_rule_version"]))
            con.execute("""INSERT INTO scores VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        ("jin10", "synthetic", 1, "s1", config()["segment_rule_version"],
                         config()["model_revision"], sha(Path(config()["existing_scoring_config"])),
                         "input-hash", 8, .2, .5, .3, .2, "DONE", None, "cpu",
                         "2026-11-16T01:01:00+00:00", "2026-11-16T01:02:00+00:00",
                         "2026-11-16T01:00:00+00:00", "2026-11-16T01:02:00+00:00",
                         "PROSPECTIVE_RECEIPT", "synthetic-run"))
        eligible, excluded = _complete_versions(cutoff, True, datetime(2026, 11, 16, tzinfo=timezone.utc), score_db)
        checks["partial_article_excluded"] = not eligible and excluded.get("PARTIAL_OR_MISSING_SCORE") == 1
    result = {"schema": "news_sentiment_v43_targeted_checks_1", "checks": checks,
              "passed": sum(checks.values()), "total": len(checks),
              "kind": "SYNTHETIC_ISOLATED_NO_REAL_SCORE_OR_RETURN_LABEL"}
    if not all(checks.values()):
        raise AssertionError(json.dumps(result, ensure_ascii=False))
    return result


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
