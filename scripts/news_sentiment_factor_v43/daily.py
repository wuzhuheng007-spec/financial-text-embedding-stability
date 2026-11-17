"""Source-scoped daily tone snapshots. No supplier, model or price calls."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.news_sentiment_factor_v41.freeze import ROOT, sha
from scripts.news_sentiment_factor_v41.score import DB as SCORE_DB
from scripts.news_sentiment_factor_v41.worker import utc
from scripts.news_sentiment_factor_v42.association import RULES, VERSION, associate
from scripts.news_sentiment_factor_v42.snapshot import cutoff_for, session_day, source_coverage
from scripts.news_sentiment_factor_v42.snapshot import calendar as verified_calendar
from scripts.news_sentiment_factor_v42.worker import QUEUE_DB, open_queue

CFG_PATH = ROOT / "configs/news_sentiment_factor_v43_20261117.json"
SCOPE_PATH = ROOT / "configs/news_sentiment_factor_v42_snapshot_r2_20261117.json"
MAPPING_PATH = ROOT / "configs/news_sentiment_factor_v43_security_mapping_20261117.json"
NY = ZoneInfo("America/New_York")
UTC = timezone.utc


def config() -> dict:
    return json.loads(CFG_PATH.read_text(encoding="utf-8"))


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def eligible_previous(day: date) -> date:
    candidate = day - timedelta(days=1)
    while candidate.year == 2026:
        if session_day(candidate):
            return candidate
        candidate -= timedelta(days=1)
    raise ValueError("PREVIOUS_VERIFIED_SESSION_UNAVAILABLE")


def eligible_next(day: date) -> date:
    candidate = day + timedelta(days=1)
    while candidate.year == 2026:
        if session_day(candidate):
            return candidate
        candidate += timedelta(days=1)
    raise ValueError("NEXT_VERIFIED_SESSION_UNAVAILABLE")


def regular_session_open(day: date) -> datetime:
    """Verified 2026 regular-session entry time, never an observed auction print."""
    if not session_day(day):
        raise ValueError("NOT_VERIFIED_2026_US_REGULAR_SESSION")
    return datetime.combine(day, time(9, 30), NY).astimezone(UTC)


def regular_session_close(day: date) -> datetime:
    """Exchange schedule close; daily K-line prices still need separate qualification."""
    if not session_day(day):
        raise ValueError("NOT_VERIFIED_2026_US_REGULAR_SESSION")
    early = day.isoformat() in verified_calendar()["early_close_dates"]
    return datetime.combine(day, time(13 if early else 16, 0), NY).astimezone(UTC)


def window_for(cutoff: datetime) -> tuple[datetime, datetime, str]:
    local = cutoff.astimezone(NY)
    if not session_day(local.date()) or cutoff != cutoff_for(local.date()):
        raise ValueError("INVALID_VERIFIED_SESSION_CUTOFF")
    start = cutoff_for(eligible_previous(local.date()))
    origin = utc(config()["research_origin_utc"])
    return start, cutoff, "PARTIAL_INITIAL_WINDOW" if start < origin <= cutoff else "OBSERVED_SOURCE_SCOPE"


def schema(con: sqlite3.Connection) -> None:
    con.executescript("""
    CREATE TABLE IF NOT EXISTS daily_snapshots(
      snapshot_id TEXT PRIMARY KEY,window_start_utc TEXT NOT NULL,scheduled_cutoff_utc TEXT NOT NULL,
      generation_started_at_utc TEXT NOT NULL,kind TEXT NOT NULL,run_id TEXT,
      research_origin_utc TEXT NOT NULL,rule_effective_at_utc TEXT NOT NULL,
      model_revision TEXT NOT NULL,segment_rule_version TEXT NOT NULL,scoring_config_sha256 TEXT NOT NULL,
      association_rule_version TEXT NOT NULL,input_manifest_sha256 TEXT NOT NULL,
      input_count INTEGER NOT NULL,coverage_json TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS daily_snapshot_company(
      snapshot_id TEXT NOT NULL,entity_id TEXT NOT NULL,n1 REAL,n2_observed INTEGER NOT NULL,
      n2_complete INTEGER,update_count INTEGER NOT NULL,coverage_state TEXT NOT NULL,
      PRIMARY KEY(snapshot_id,entity_id));
    CREATE TABLE IF NOT EXISTS daily_snapshot_members(
      snapshot_id TEXT NOT NULL,entity_id TEXT NOT NULL,provider TEXT NOT NULL,
      group_id TEXT NOT NULL,article_id TEXT NOT NULL,version INTEGER NOT NULL,
      source_hash TEXT NOT NULL,first_available_at_utc TEXT NOT NULL,
      version_available_at_utc TEXT NOT NULL,tone REAL NOT NULL,
      member_kind TEXT NOT NULL,PRIMARY KEY(snapshot_id,entity_id,provider,group_id,article_id,version));
    CREATE TABLE IF NOT EXISTS daily_snapshot_completions(
      snapshot_id TEXT PRIMARY KEY,completion_recorded_at_utc TEXT NOT NULL,
      data_manifest_sha256 TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS daily_snapshot_releases(
      snapshot_id TEXT PRIMARY KEY,release_started_at_utc TEXT NOT NULL,
      signal_available_at_utc TEXT NOT NULL,release_rule TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS daily_snapshot_publication_witness(
      snapshot_id TEXT PRIMARY KEY,release_commit_observed_at_utc TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS daily_label_queue(
      snapshot_id TEXT NOT NULL,entity_id TEXT NOT NULL,horizon_sessions INTEGER NOT NULL,
      status TEXT NOT NULL,reason TEXT NOT NULL,security_id TEXT,
      entry_session_ny TEXT,entry_at_utc TEXT,exit_session_ny TEXT,exit_at_utc TEXT,
      maturity_at_utc TEXT,price_requirements_json TEXT NOT NULL,
      PRIMARY KEY(snapshot_id,entity_id,horizon_sessions));
    CREATE TABLE IF NOT EXISTS daily_label_status_events(
      snapshot_id TEXT NOT NULL,entity_id TEXT NOT NULL,horizon_sessions INTEGER NOT NULL,
      status TEXT NOT NULL,reason TEXT NOT NULL,observed_at_utc TEXT NOT NULL,
      PRIMARY KEY(snapshot_id,entity_id,horizon_sessions,status));
    """)
    for table in ("daily_snapshots", "daily_snapshot_company", "daily_snapshot_members",
                  "daily_snapshot_completions", "daily_snapshot_releases",
                  "daily_snapshot_publication_witness", "daily_label_queue", "daily_label_status_events"):
        con.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_immutable_update BEFORE UPDATE ON {table} "
                    f"BEGIN SELECT RAISE(ABORT,'IMMUTABLE_{table}'); END")
        con.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_immutable_delete BEFORE DELETE ON {table} "
                    f"BEGIN SELECT RAISE(ABORT,'IMMUTABLE_{table}'); END")
    con.commit()


def rule_effective(con: sqlite3.Connection) -> datetime:
    row = con.execute("SELECT rule_sha256,created_at_utc FROM association_revisions WHERE rule_version=?",
                      (VERSION,)).fetchone()
    if row is None or row[0] != sha(RULES):
        raise ValueError("ASSOCIATION_RULE_NOT_FROZEN_OR_DRIFTED")
    return utc(row[1])


def _complete_versions(cutoff: datetime, live: bool, rule_at: datetime,
                       score_db: Path = SCORE_DB) -> tuple[list[dict], dict]:
    """A version is eligible only if every frozen segment has an exact DONE score."""
    cfg = config()
    score_cfg = sha(ROOT / cfg["existing_scoring_config"])
    excluded = defaultdict(int)
    versions = []
    with closing(sqlite3.connect(f"file:{score_db.as_posix()}?mode=ro", uri=True, timeout=5)) as con:
        con.row_factory = sqlite3.Row
        articles = con.execute("SELECT * FROM articles ORDER BY provider,article_id,version").fetchall()
        for a in articles:
            a = dict(a)
            if live and a["cohort"] != cfg["forward_article_cohort"]:
                excluded["CACHE_REPLAY_NOT_FORWARD"] += 1
                continue
            if a["provider"] not in cfg["source_scope"]:
                excluded["SOURCE_OUT_OF_SCOPE"] += 1
                continue
            if utc(a["received_at_utc"]) > cutoff:
                excluded["RECEIVED_AFTER_CUTOFF"] += 1
                continue
            segments = [dict(r) for r in con.execute(
                "SELECT * FROM segments WHERE provider=? AND article_id=? AND version=? AND segment_rule_version=? ORDER BY segment_id",
                (a["provider"], a["article_id"], a["version"], cfg["segment_rule_version"]))]
            if not segments:
                excluded["NO_EXPECTED_SEGMENTS"] += 1
                continue
            scored = []
            failure = None
            for s in segments:
                r = con.execute("""SELECT * FROM scores WHERE provider=? AND article_id=? AND version=?
                    AND segment_id=? AND segment_rule_version=? AND model_revision=? AND config_sha256=?""",
                    (a["provider"], a["article_id"], a["version"], s["segment_id"],
                     cfg["segment_rule_version"], cfg["model_revision"], score_cfg)).fetchone()
                if r is None:
                    failure = "PARTIAL_OR_MISSING_SCORE"
                    break
                r = dict(r)
                if r["status"] != "DONE" or r["net_score"] is None or r["input_sha256"] != s["input_sha256"]:
                    failure = "FAILED_OR_INPUT_MISMATCH"
                    break
                if utc(r["scored_at_utc"]) > cutoff:
                    failure = "SCORE_AFTER_CUTOFF"
                    break
                scored.append((s, r))
            if failure:
                excluded[failure] += 1
                continue
            available = max([utc(a["received_at_utc"])] + [utc(r["scored_at_utc"]) for _, r in scored])
            bound = []
            for s, r in scored:
                assoc = associate(s["text"]) if cutoff >= rule_at else {
                    "status": s["association_status"], "entity_id": s["company_association"]}
                bound.append({"segment_id": s["segment_id"], "kind": s["kind"],
                              "status": assoc["status"], "entity_id": assoc["entity_id"],
                              "tone": r["net_score"]})
            versions.append({"provider": a["provider"], "article_id": a["article_id"],
                             "version": a["version"], "group_id": a["event_group_id"] or a["article_id"],
                             "source_hash": a["source_text_hash"], "received": a["received_at_utc"],
                             "available": available.isoformat(), "segments": bound})
    return versions, dict(excluded)


def _members(versions: list[dict], start: datetime, end: datetime) -> tuple[list[dict], dict]:
    # Union revisions of the same source article as observed by end; no future
    # revision or cross-provider grouping can change a published snapshot.
    versions = [x for x in versions if utc(x["received"]) <= end and utc(x["available"]) <= end]
    by_article = defaultdict(list)
    for item in versions:
        by_article[(item["provider"], item["article_id"])].append(item)
    edges = {}
    def root(key):
        edges.setdefault(key, key)
        while edges[key] != key:
            edges[key] = edges[edges[key]]
            key = edges[key]
        return key
    def union(a, b):
        x, y = root(a), root(b)
        edges[max(x, y)] = min(x, y)
    for (provider, _), items in by_article.items():
        group_keys = [(provider, x["group_id"]) for x in items]
        for key in group_keys[1:]:
            union(group_keys[0], key)
    for item in versions:
        item["canonical_group"] = root((item["provider"], item["group_id"]))[1]
    first = {}
    for item in versions:
        key = (item["provider"], item["canonical_group"])
        stamp = utc(item["available"])
        first[key] = min(first.get(key, stamp), stamp)
    latest = [max(items, key=lambda x: (x["version"], utc(x["received"]))) for items in by_article.values()]
    members = []
    updates = defaultdict(int)
    first_versions = [x for x in latest if start < first[(x["provider"], x["canonical_group"])] <= end]
    changed_versions = [x for x in versions if first[(x["provider"], x["canonical_group"])] <= start
                        and start < utc(x["available"]) <= end]
    for item in first_versions + changed_versions:
        key = (item["provider"], item["canonical_group"])
        first_at = first[key]
        if first_at > end:
            continue
        is_first = item in first_versions
        is_update = first_at <= start and start < utc(item["available"]) <= end
        if not is_first and not is_update:
            continue
        by_company = defaultdict(list)
        body = [s for s in item["segments"] if s["kind"] == "BODY_SEGMENT"]
        chosen = body if body else [s for s in item["segments"] if s["kind"] == "TITLE"]
        for s in chosen:
            if s["status"] == "ONE_EXPLICIT_COMPANY" and s["entity_id"]:
                by_company[s["entity_id"]].append(s)
        for entity, scored in by_company.items():
            row = {"entity_id": entity, "provider": item["provider"],
                   "group_id": item["canonical_group"], "article_id": item["article_id"],
                   "version": item["version"], "source_hash": item["source_hash"],
                   "first_available": first_at.isoformat(), "version_available": item["available"],
                   "tone": sum(x["tone"] for x in scored) / len(scored)}
            if is_first:
                row["member_kind"] = "FIRST_EVENT"
                members.append(row)
            elif is_update:
                row["member_kind"] = "CROSS_DAY_REVISION"
                members.append(row)
                updates[entity] += 1
    return sorted(members, key=lambda x: (x["entity_id"], x["provider"], x["group_id"],
                                          x["article_id"], x["version"])), dict(updates)


def _label_rows(entities: list[str], cutoff: datetime, available: datetime,
                mapping: dict, observed_at: datetime | None = None) -> list[dict]:
    observed_at = observed_at or datetime.now(UTC)
    try:
        entry_day = eligible_next(cutoff.astimezone(NY).date())
        entry_at = regular_session_open(entry_day)
    except ValueError:
        entry_day = None
        entry_at = None
    rows = []
    for entity in entities:
        item = mapping.get(entity, {})
        verified = item.get("verification") == "CURRENT_US_PROVIDER_CODE_VERIFIED" and item.get("security_id")
        for horizon in (1, 5):
            exit_day = entry_day
            try:
                if exit_day is not None:
                    for _ in range(horizon - 1):
                        exit_day = eligible_next(exit_day)
                    exit_at = regular_session_close(exit_day)
                else:
                    exit_at = None
            except ValueError:
                exit_day = None
                exit_at = None
            if not verified:
                status, reason = "BLOCKED_SECURITY_MAPPING", "NO_VERIFIED_US_SECURITY_ID"
            elif entry_day is None or exit_day is None:
                status, reason = "BLOCKED_CALENDAR_RANGE", "VERIFIED_2026_SESSION_CALENDAR_EXHAUSTED"
            elif available > entry_at:
                status, reason = "MISSED_ENTRY", "SIGNAL_AVAILABLE_AFTER_PLANNED_OPEN"
            elif observed_at < entry_at:
                status, reason = "WAIT_ENTRY", "PLANNED_OPEN_NOT_REACHED"
            elif observed_at < exit_at:
                status, reason = "WAIT_EXIT", "PLANNED_CLOSE_NOT_REACHED"
            else:
                status, reason = "WAIT_QUALIFIED_PRICE", "VERIFIED_OPEN_CLOSE_AND_ACTIONS_NOT_AVAILABLE"
            rows.append({"entity_id": entity, "horizon": horizon, "status": status, "reason": reason,
                         "security_id": item.get("security_id") if verified else None,
                         "entry_session": entry_day.isoformat() if verified and entry_day else None,
                         "entry_at": entry_at.isoformat() if verified and entry_at else None,
                         "exit_session": exit_day.isoformat() if verified and exit_day else None,
                         "exit_at": exit_at.isoformat() if verified and exit_at else None,
                         "maturity_at": exit_at.isoformat() if verified and exit_at else None,
                         "price_requirements": ["VERIFIED_REGULAR_OPEN", "VERIFIED_REGULAR_CLOSE",
                                                "CORPORATE_ACTIONS", "SOURCE_PROVENANCE"]})
    return rows


def _read_existing(con: sqlite3.Connection, snapshot_id: str) -> dict | None:
    row = con.execute("""SELECT s.snapshot_id,s.kind,s.input_manifest_sha256,s.window_start_utc,
        s.scheduled_cutoff_utc,s.generation_started_at_utc,c.completion_recorded_at_utc,
        r.signal_available_at_utc,w.release_commit_observed_at_utc FROM daily_snapshots s
        LEFT JOIN daily_snapshot_completions c USING(snapshot_id)
        LEFT JOIN daily_snapshot_releases r USING(snapshot_id)
        LEFT JOIN daily_snapshot_publication_witness w USING(snapshot_id)
        WHERE snapshot_id=?""",
        (snapshot_id,)).fetchone()
    return dict(row) if row else None


def create_snapshot(cutoff_text: str, *, mode: str = "DEVELOPMENT_REPLAY",
                    run_id: str | None = None, score_db: Path = SCORE_DB,
                    queue_db: Path = QUEUE_DB, fail_after_data: bool = False) -> dict:
    start_time = datetime.now(UTC)
    cutoff = utc(cutoff_text)
    if cutoff > start_time:
        raise ValueError("FUTURE_CUTOFF")
    if mode not in ("SCHEDULED", "DEVELOPMENT_REPLAY"):
        raise ValueError("INVALID_MODE")
    window_start, _, initial = window_for(cutoff)
    if mode == "SCHEDULED" and cutoff.astimezone(NY).date() != start_time.astimezone(NY).date():
        raise ValueError("PAST_CUTOFF_REQUIRES_DEVELOPMENT_REPLAY")
    con = sqlite3.connect(queue_db, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=10000")
    schema(con)
    rule_at = rule_effective(con)
    if mode == "SCHEDULED" and rule_at > cutoff:
        con.close()
        raise ValueError("RULE_NOT_AVAILABLE_AT_CUTOFF")
    score_cfg = sha(ROOT / config()["existing_scoring_config"])
    identity = {"cutoff": cutoff.isoformat(), "mode": mode,
                "v43_config": sha(CFG_PATH), "scoring_config": score_cfg}
    snapshot_id = digest(identity)[:32]
    old = _read_existing(con, snapshot_id)
    versions, excluded = _complete_versions(cutoff, mode == "SCHEDULED", rule_at, score_db)
    members, updates = _members(versions, window_start, cutoff)
    manifest = [{k: row[k] for k in ("entity_id", "provider", "group_id", "article_id",
                                        "version", "source_hash", "first_available", "version_available",
                                        "tone", "member_kind")} for row in members]
    manifest_hash = digest(manifest)
    if old:
        if old["input_manifest_sha256"] != manifest_hash:
            con.close()
            raise ValueError("IMMUTABLE_SNAPSHOT_INPUT_CONFLICT")
        if (old["release_commit_observed_at_utc"] and old["signal_available_at_utc"]
                and utc(old["release_commit_observed_at_utc"]) > utc(old["signal_available_at_utc"])):
            con.close()
            raise ValueError("PUBLICATION_WITNESS_AFTER_SIGNAL_AVAILABLE")
        reused = bool(old["release_commit_observed_at_utc"])
    else:
        reused = False
        coverage = source_coverage(cutoff) if score_db == SCORE_DB else {"coverage": "SYNTHETIC_UNKNOWN"}
        coverage.update({"initial_window": initial, "excluded_version_counts": excluded,
                         "cross_source_deduplication": "UNVERIFIED",
                         "n2_scope": "OBSERVED_SOURCE_SCOPED_ALGORITHMIC_GROUPS"})
        if score_db == SCORE_DB:
            coverage["scoring_backlog"] = con.execute(
                "SELECT count(*) FROM jobs WHERE state IN ('PENDING','RECOVERY_PENDING','STARTED')").fetchone()[0]
        kind = "DEVELOPMENT_REPLAY" if mode != "SCHEDULED" else (
            "LATE_SCHEDULED" if (start_time - cutoff).total_seconds() > 60 else "ON_TIME_SCHEDULED")
        scope = json.loads(SCOPE_PATH.read_text(encoding="utf-8"))["company_scope"]
        by_event = defaultdict(list)
        for row in members:
            if row["member_kind"] == "FIRST_EVENT":
                by_event[(row["entity_id"], row["provider"], row["group_id"])].append(row["tone"])
        con.execute("BEGIN IMMEDIATE")
        try:
            con.execute("INSERT INTO daily_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (snapshot_id, window_start.isoformat(), cutoff.isoformat(), start_time.isoformat(),
                         kind, run_id, config()["research_origin_utc"], rule_at.isoformat(),
                         config()["model_revision"], config()["segment_rule_version"], score_cfg,
                         VERSION if cutoff >= rule_at else "v41_original", manifest_hash,
                         len(members), json.dumps(coverage, ensure_ascii=False, sort_keys=True)))
            for row in members:
                con.execute("INSERT INTO daily_snapshot_members VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (snapshot_id, row["entity_id"], row["provider"], row["group_id"],
                             row["article_id"], row["version"], row["source_hash"],
                             row["first_available"], row["version_available"], row["tone"], row["member_kind"]))
            event_tones = defaultdict(list)
            for (entity, _, _), tones in by_event.items():
                event_tones[entity].append(sum(tones) / len(tones))
            complete = coverage["coverage"] == "VERIFIED_CONTINUOUS" and initial != "PARTIAL_INITIAL_WINDOW" and coverage.get("scoring_backlog", 0) == 0
            for entity in scope:
                tones = event_tones.get(entity, [])
                con.execute("INSERT INTO daily_snapshot_company VALUES(?,?,?,?,?,?,?)",
                            (snapshot_id, entity, sum(tones) / len(tones) if tones else None,
                             len(tones), len(tones) if complete else None, updates.get(entity, 0),
                             "OBSERVED_CONTINUOUS" if complete else initial if initial == "PARTIAL_INITIAL_WINDOW" else "COVERAGE_GAP_OR_UNKNOWN"))
            con.commit()
        except Exception:
            con.rollback()
            raise
    if fail_after_data:
        con.close()
        raise RuntimeError("SIMULATED_INTERRUPTION_AFTER_DATA_COMMIT")
    # Observe the completed data commit, then persist a separate append-only
    # completion. A release is guarded beyond the actual release commit.
    completion = con.execute("SELECT completion_recorded_at_utc FROM daily_snapshot_completions WHERE snapshot_id=?",
                             (snapshot_id,)).fetchone()
    if not completion:
        observed = datetime.now(UTC)
        con.execute("INSERT INTO daily_snapshot_completions VALUES(?,?,?)",
                    (snapshot_id, observed.isoformat(), manifest_hash))
        con.commit()
    else:
        observed = utc(completion[0])
    release = con.execute("SELECT signal_available_at_utc FROM daily_snapshot_releases WHERE snapshot_id=?",
                          (snapshot_id,)).fetchone()
    if not release:
        guard = datetime.now(UTC) + timedelta(seconds=60)
        guard = max(guard, cutoff, rule_at, observed,
                    *([utc(x["version_available"]) for x in members] or [observed]))
        con.execute("INSERT INTO daily_snapshot_releases VALUES(?,?,?,?)",
                    (snapshot_id, datetime.now(UTC).isoformat(), guard.isoformat(), "POST_COMMIT_PLUS_60S"))
        con.commit()
        if datetime.now(UTC) > guard:
            con.close()
            raise RuntimeError("RELEASE_GUARD_EXPIRED_BEFORE_COMMIT_WITNESS")
    else:
        guard = utc(release[0])
    witness = con.execute("SELECT 1 FROM daily_snapshot_publication_witness WHERE snapshot_id=?",
                          (snapshot_id,)).fetchone()
    if not witness:
        observed_release_commit = datetime.now(UTC)
        con.execute("INSERT INTO daily_snapshot_publication_witness VALUES(?,?)",
                    (snapshot_id, observed_release_commit.isoformat()))
        con.commit()
        if datetime.now(UTC) > guard:
            con.close()
            raise RuntimeError("PUBLICATION_WITNESS_COMMIT_EXCEEDED_RELEASE_GUARD")
    if mode == "SCHEDULED":
        mapping = {x["entity_id"]: x for x in json.loads(MAPPING_PATH.read_text(encoding="utf-8"))["rows"]}
        scope = json.loads(SCOPE_PATH.read_text(encoding="utf-8"))["company_scope"]
        for row in _label_rows(scope, cutoff, guard, mapping):
            con.execute("""INSERT OR IGNORE INTO daily_label_queue VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (snapshot_id, row["entity_id"], row["horizon"], row["status"], row["reason"],
                         row["security_id"], row["entry_session"], row["entry_at"],
                         row["exit_session"], row["exit_at"], row["maturity_at"],
                         json.dumps(row["price_requirements"])))
        con.commit()
    saved = _read_existing(con, snapshot_id)
    con.close()
    return {**saved, "status": "ALREADY_EXISTS_UNCHANGED" if reused else
            "PERSISTED_PENDING_AVAILABILITY" if utc(saved["signal_available_at_utc"]) > datetime.now(UTC) else "RELEASED"}


def refresh_label_states(queue_db: Path = QUEUE_DB, observed_at: datetime | None = None) -> dict:
    """Append maturity states; do not fetch prices or synthesize a return."""
    observed_at = observed_at or datetime.now(UTC)
    con = sqlite3.connect(queue_db, timeout=10)
    con.row_factory = sqlite3.Row
    schema(con)
    rows = con.execute("""SELECT q.* FROM daily_label_queue q
        JOIN daily_snapshot_releases r USING(snapshot_id)
        JOIN daily_snapshot_publication_witness w USING(snapshot_id)
        WHERE r.signal_available_at_utc<=? AND w.release_commit_observed_at_utc<=r.signal_available_at_utc""",
        (observed_at.isoformat(),)).fetchall()
    added = 0
    for row in rows:
        if row["status"] in ("BLOCKED_SECURITY_MAPPING", "MISSED_ENTRY"):
            state, reason = row["status"], row["reason"]
        elif row["entry_at_utc"] and observed_at < utc(row["entry_at_utc"]):
            state, reason = "WAIT_ENTRY", "PLANNED_OPEN_NOT_REACHED"
        elif row["exit_at_utc"] and observed_at < utc(row["exit_at_utc"]):
            state, reason = "WAIT_EXIT", "PLANNED_CLOSE_NOT_REACHED"
        else:
            state, reason = "WAIT_QUALIFIED_PRICE", "VERIFIED_OPEN_CLOSE_AND_ACTIONS_NOT_AVAILABLE"
        before = con.total_changes
        con.execute("INSERT OR IGNORE INTO daily_label_status_events VALUES(?,?,?,?,?,?)",
                    (row["snapshot_id"], row["entity_id"], row["horizon_sessions"],
                     state, reason, observed_at.isoformat()))
        added += con.total_changes - before
    con.commit()
    con.close()
    return {"checked": len(rows), "new_state_receipts": added}


def maybe_due_snapshot(run_id: str | None = None) -> dict:
    now = datetime.now(UTC)
    day = now.astimezone(NY).date()
    if not session_day(day):
        return {"state": "WAITING_VERIFIED_SESSION"}
    cutoff = cutoff_for(day)
    if now < cutoff:
        return {"state": "PENDING_MARKET_CUTOFF", "next_cutoff_utc": cutoff.isoformat()}
    return create_snapshot(cutoff.isoformat(), mode="SCHEDULED", run_id=run_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of")
    parser.add_argument("--mode", choices=["SCHEDULED", "DEVELOPMENT_REPLAY"], default="DEVELOPMENT_REPLAY")
    args = parser.parse_args()
    print(json.dumps(create_snapshot(args.as_of, mode=args.mode) if args.as_of else maybe_due_snapshot(), ensure_ascii=False))
