"""Append-only company tone snapshots with explicit as-of and true availability."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.news_sentiment_factor_v41.score import DB as SCORE_DB, MODEL_ID
from scripts.news_sentiment_factor_v41.worker import utc
from scripts.news_sentiment_factor_v41.freeze import FEED_DB, now, sha
from .association import RULES, VERSION, associate
from .worker import CFG, PRIVATE, PUBLIC, ROOT, SEGMENT_RULE, open_queue, write_json

CALENDAR=ROOT/"configs/news_sentiment_factor_v42_us_calendar_2026.json"
SNAPSHOT_RULE=ROOT/"configs/news_sentiment_factor_v42_snapshot_r2_20261117.json"
NY=ZoneInfo("America/New_York")


def calendar() -> dict:
    return json.loads(CALENDAR.read_text(encoding="utf-8"))


def session_day(day: date) -> bool:
    if day.year!=2026:
        return False
    return day.weekday()<5 and day.isoformat() not in calendar()["closed_dates"]


def cutoff_for(day: date) -> datetime:
    if not session_day(day):
        raise ValueError("NOT_VERIFIED_2026_US_REGULAR_SESSION")
    return datetime.combine(day,time(16,15),NY).astimezone(timezone.utc)


def schema(con: sqlite3.Connection) -> None:
    con.executescript("""
      CREATE TABLE IF NOT EXISTS snapshots (
        snapshot_id TEXT PRIMARY KEY,as_of_utc TEXT NOT NULL,generated_at_utc TEXT NOT NULL,
        available_at_utc TEXT NOT NULL,kind TEXT NOT NULL,session_date_ny TEXT,
        model_revision TEXT NOT NULL,scoring_config_sha256 TEXT NOT NULL,
        association_rule_version TEXT NOT NULL,event_group_rule_version TEXT NOT NULL,
        input_manifest_sha256 TEXT NOT NULL,member_count INTEGER NOT NULL,
        coverage_json TEXT NOT NULL,created_run_id TEXT);
      CREATE TABLE IF NOT EXISTS snapshot_company (
        snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id),entity_id TEXT NOT NULL,
        n1_observed_tone REAL,n2_observed_events INTEGER NOT NULL,n2_complete_count INTEGER,
        coverage_state TEXT NOT NULL,excluded_json TEXT NOT NULL,
        PRIMARY KEY(snapshot_id,entity_id));
      CREATE TABLE IF NOT EXISTS snapshot_members (
        snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id),entity_id TEXT NOT NULL,
        provider TEXT NOT NULL,article_id TEXT NOT NULL,version INTEGER NOT NULL,
        group_id TEXT NOT NULL,article_source_hash TEXT NOT NULL,segment_ids_json TEXT NOT NULL,
        tone REAL NOT NULL,received_at_utc TEXT NOT NULL,scored_at_utc TEXT NOT NULL,
        PRIMARY KEY(snapshot_id,entity_id,provider,article_id,version));
      CREATE TABLE IF NOT EXISTS future_label_queue (
        snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id),entity_id TEXT NOT NULL,
        horizon_sessions INTEGER NOT NULL,status TEXT NOT NULL,reason TEXT,
        created_at_utc TEXT NOT NULL,
        PRIMARY KEY(snapshot_id,entity_id,horizon_sessions));
      CREATE TRIGGER IF NOT EXISTS snapshots_no_update BEFORE UPDATE ON snapshots
        BEGIN SELECT RAISE(ABORT,'IMMUTABLE_SNAPSHOT'); END;
      CREATE TRIGGER IF NOT EXISTS snapshots_no_delete BEFORE DELETE ON snapshots
        BEGIN SELECT RAISE(ABORT,'IMMUTABLE_SNAPSHOT'); END;
      CREATE TRIGGER IF NOT EXISTS snapshot_company_no_update BEFORE UPDATE ON snapshot_company
        BEGIN SELECT RAISE(ABORT,'IMMUTABLE_SNAPSHOT_COMPANY'); END;
      CREATE TRIGGER IF NOT EXISTS snapshot_company_no_delete BEFORE DELETE ON snapshot_company
        BEGIN SELECT RAISE(ABORT,'IMMUTABLE_SNAPSHOT_COMPANY'); END;
      CREATE TRIGGER IF NOT EXISTS snapshot_members_no_update BEFORE UPDATE ON snapshot_members
        BEGIN SELECT RAISE(ABORT,'IMMUTABLE_SNAPSHOT_MEMBER'); END;
      CREATE TRIGGER IF NOT EXISTS snapshot_members_no_delete BEFORE DELETE ON snapshot_members
        BEGIN SELECT RAISE(ABORT,'IMMUTABLE_SNAPSHOT_MEMBER'); END;
    """)
    con.commit()


def source_coverage(as_of: datetime) -> dict:
    # The current provider_state is not a historical audit trail. For an older
    # replay, make the missing historical continuity evidence explicit.
    result={"source":"jin10_personal_cache","scope":"latest_page_polling_with_local_relevance_filter",
            "coverage":"HISTORICAL_CONTINUITY_UNKNOWN","last_success_at_utc":None,
            "page_cursor_continuity":"UNKNOWN","restart_gap":"UNKNOWN",
            "cache_ingest_gap":"UNKNOWN","rate_limited":"UNKNOWN"}
    if abs((datetime.now(timezone.utc)-as_of).total_seconds())>120:
        return result
    with sqlite3.connect(f"file:{FEED_DB.as_posix()}?mode=ro",uri=True,timeout=3) as feed:
        feed.row_factory=sqlite3.Row
        row=feed.execute("SELECT state,last_success_at,coverage_gap,last_error_class,business_failure_total FROM provider_state WHERE provider='jin10'").fetchone()
        if row:
            result.update({"last_success_at_utc":row["last_success_at"],
                           "page_cursor_continuity":row["coverage_gap"] or "NOT_PROVEN",
                           "rate_limited":"YES" if row["last_error_class"] in ("HTTP_429","RATE_LIMIT") else "NO_RECENT_FLAG",
                           "coverage":"GAP_POSSIBLE" if row["coverage_gap"] else "SCOPE_CONTINUITY_NOT_PROVEN"})
    return result


def available_rows(as_of: datetime, *, live: bool, rule_effective: datetime) -> tuple[list[dict],dict]:
    skipped=defaultdict(int)
    with sqlite3.connect(f"file:{SCORE_DB.as_posix()}?mode=ro",uri=True,timeout=5) as con:
        con.row_factory=sqlite3.Row
        source=con.execute("""SELECT a.provider,a.article_id,a.version,a.received_at_utc,a.source_text_hash,
          a.cohort,a.event_group_id,s.segment_id,s.kind,s.text,s.input_sha256,s.company_association,
          s.association_status,x.net_score,x.status,x.scored_at_utc,x.model_revision
          FROM articles a JOIN segments s USING(provider,article_id,version)
          JOIN scores x USING(provider,article_id,version,segment_id,segment_rule_version)
          WHERE s.segment_rule_version=? AND x.model_revision=?
          ORDER BY a.provider,a.article_id,a.version,s.segment_id""",(SEGMENT_RULE,MODEL_ID)).fetchall()
    eligible=[]
    for raw in source:
        r=dict(raw)
        if live and r["cohort"]!="PROSPECTIVE_RECEIPT":
            skipped["CACHE_REPLAY_EXCLUDED_FROM_FORWARD"]+=1
            continue
        if utc(r["received_at_utc"])>as_of:
            skipped["RECEIVED_AFTER_AS_OF"]+=1
            continue
        if utc(r["scored_at_utc"])>as_of:
            skipped["SCORED_AFTER_AS_OF"]+=1
            continue
        if r["status"]!="DONE" or r["net_score"] is None:
            skipped["SCORE_FAILED"]+=1
            continue
        if as_of>=rule_effective:
            binding=associate(r["text"])
            r["company_association"]=binding["entity_id"]
            r["association_status"]=binding["status"]
        eligible.append(r)
    return eligible,dict(skipped)


def candidate_members(rows: list[dict]) -> list[dict]:
    # Select the latest *available and scored* revision per source article.
    article_versions=defaultdict(list)
    for r in rows:
        article_versions[(r["provider"],r["article_id"])].append(r)
    selected=[]
    for versions in article_versions.values():
        latest=max((r["version"],utc(r["received_at_utc"])) for r in versions)
        selected.extend(r for r in versions if (r["version"],utc(r["received_at_utc"]))==latest)
    by_article_company=defaultdict(list)
    for r in selected:
        if r["association_status"]!="ONE_EXPLICIT_COMPANY":
            continue
        by_article_company[(r["provider"],r["article_id"],r["version"],r["company_association"])].append(r)
    members=[]
    for (provider,article_id,version,entity),group in by_article_company.items():
        body=[x for x in group if x["kind"]=="BODY_SEGMENT"]
        chosen=body if body else [x for x in group if x["kind"]=="TITLE"]
        if not chosen:
            continue
        first=chosen[0]
        members.append({"entity_id":entity,"provider":provider,"article_id":article_id,"version":version,
                        "group_id":first["event_group_id"],"article_source_hash":first["source_text_hash"],
                        "segment_ids":[x["segment_id"] for x in chosen],
                        "tone":sum(x["net_score"] for x in chosen)/len(chosen),
                        "received_at_utc":first["received_at_utc"],
                        "scored_at_utc":max((x["scored_at_utc"] for x in chosen),key=utc)})
    return sorted(members,key=lambda r:(r["entity_id"],r["provider"],r["group_id"],r["article_id"],r["version"]))


def create_snapshot(as_of_text: str, *, mode: str="DEVELOPMENT_REPLAY", run_id: str | None=None) -> dict:
    as_of=utc(as_of_text)
    generated=datetime.now(timezone.utc)
    if as_of>generated:
        raise ValueError("FUTURE_AS_OF_NOT_ALLOWED")
    local=as_of.astimezone(NY)
    if mode=="SCHEDULED":
        if not session_day(local.date()) or as_of!=cutoff_for(local.date()):
            raise ValueError("NOT_VERIFIED_SCHEDULED_1615_SESSION_CUTOFF")
        if local.date()!=generated.astimezone(NY).date():
            raise ValueError("OLD_CUTOFF_REQUIRES_DEVELOPMENT_REPLAY")
    elif mode!="DEVELOPMENT_REPLAY":
        raise ValueError("UNSUPPORTED_SNAPSHOT_MODE")
    con=open_queue()
    schema(con)
    rule=con.execute("SELECT rule_sha256,created_at_utc FROM association_revisions WHERE rule_version=?",(VERSION,)).fetchone()
    if not rule or rule["rule_sha256"]!=sha(RULES):
        raise ValueError("ASSOCIATION_RULE_NOT_FROZEN_OR_DRIFTED")
    rule_effective=utc(rule["created_at_utc"])
    if mode=="SCHEDULED" and as_of<rule_effective:
        raise ValueError("ASSOCIATION_RULE_NOT_AVAILABLE_AT_CUTOFF")
    kind="DEVELOPMENT_REPLAY" if mode=="DEVELOPMENT_REPLAY" else (
        "LATE_SCHEDULED" if (generated-as_of).total_seconds()>60 else "ON_TIME_SCHEDULED")
    rows,excluded=available_rows(as_of,live=mode=="SCHEDULED",rule_effective=rule_effective)
    members=candidate_members(rows)
    coverage=source_coverage(as_of)
    coverage["scoring_backlog"]=con.execute("SELECT count(*) FROM jobs WHERE state IN ('PENDING','RECOVERY_PENDING','STARTED')").fetchone()[0]
    coverage["unidentified_segment_count"]=sum(r["association_status"]!="ONE_EXPLICIT_COMPANY" for r in rows)
    coverage["model_failure_count"]=excluded.get("SCORE_FAILED",0)
    coverage["excluded_reasons"]=excluded
    manifest=json.dumps(members,sort_keys=True,ensure_ascii=False,separators=(",",":"))
    manifest_hash=hashlib.sha256(manifest.encode("utf-8")).hexdigest()
    identity={"as_of":as_of.isoformat(),"mode":mode,"model":MODEL_ID,"scoring_config":sha(ROOT/"configs/news_sentiment_factor_v41_20261117_r2.json"),
              "association_rule":VERSION if as_of>=rule_effective else "v41_original",
              "group_rule":json.loads(SNAPSHOT_RULE.read_text(encoding="utf-8"))["rule_version"]}
    snapshot_id=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()[:32]
    old=con.execute("SELECT input_manifest_sha256 FROM snapshots WHERE snapshot_id=?",(snapshot_id,)).fetchone()
    if old:
        if old[0]!=manifest_hash:
            raise ValueError("IMMUTABLE_SNAPSHOT_IDENTITY_COLLISION")
        return {"snapshot_id":snapshot_id,"status":"ALREADY_EXISTS_UNCHANGED","kind":kind}
    by_event=defaultdict(list)
    for item in members:
        by_event[(item["entity_id"],item["provider"],item["group_id"])].append(item)
    entity_events=defaultdict(list)
    for (entity,provider,group_id),article_rows in by_event.items():
        entity_events[entity].append(sum(r["tone"] for r in article_rows)/len(article_rows))
    available_at=generated.isoformat() if mode=="SCHEDULED" else generated.isoformat()
    con.execute("BEGIN IMMEDIATE")
    try:
        con.execute("""INSERT INTO snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (snapshot_id,as_of.isoformat(),generated.isoformat(),available_at,kind,
             local.date().isoformat() if session_day(local.date()) else None,MODEL_ID,
             identity["scoring_config"],identity["association_rule"],identity["group_rule"],
             manifest_hash,len(members),json.dumps(coverage,ensure_ascii=False),run_id))
        for member in members:
            con.execute("""INSERT INTO snapshot_members VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (snapshot_id,member["entity_id"],member["provider"],member["article_id"],member["version"],
                 member["group_id"],member["article_source_hash"],json.dumps(member["segment_ids"]),
                 member["tone"],member["received_at_utc"],member["scored_at_utc"]))
        company_scope=json.loads(SNAPSHOT_RULE.read_text(encoding="utf-8"))["company_scope"]
        for entity in company_scope:
            tones=entity_events.get(entity,[])
            complete=coverage["coverage"]=="VERIFIED_CONTINUOUS" and coverage["scoring_backlog"]==0
            con.execute("INSERT INTO snapshot_company VALUES(?,?,?,?,?,?,?)",
                        (snapshot_id,entity,sum(tones)/len(tones) if tones else None,len(tones),
                         len(tones) if complete else None,
                         "NO_EVENT_OBSERVED_COVERAGE_GAP" if not tones and not complete else
                         "OBSERVED_INCOMPLETE" if not complete else "OBSERVED_CONTINUOUS",
                         json.dumps(excluded,sort_keys=True)))
            if mode=="SCHEDULED":
                for horizon in (1,5):
                    con.execute("INSERT INTO future_label_queue VALUES(?,?,?,?,?,?)",
                                (snapshot_id,entity,horizon,"PENDING",
                                 "PRICE_AND_HORIZON_NOT_MATURE" if tones else
                                 "PRICE_AND_HORIZON_NOT_MATURE_NO_DIRECT_EVENT",
                                 generated.isoformat()))
        con.commit()
    except Exception:
        con.rollback()
        raise
    private={"schema":"news_sentiment_v42_snapshot_evidence_1","snapshot_id":snapshot_id,
             "as_of_utc":as_of.isoformat(),"generated_at_utc":generated.isoformat(),"kind":kind,
             "input_manifest_sha256":manifest_hash,"members":members,"coverage":coverage,
             "model_revision":MODEL_ID,"scoring_config_sha256":identity["scoring_config"],
             "association_rule_version":identity["association_rule"],"event_group_rule_version":identity["group_rule"]}
    write_json(PRIVATE/f"snapshot_{snapshot_id}_private.json",private)
    redacted={k:private[k] for k in ("schema","snapshot_id","as_of_utc","generated_at_utc","kind",
                                      "input_manifest_sha256","model_revision","scoring_config_sha256",
                                      "association_rule_version","event_group_rule_version")}
    redacted.update({"member_count":len(members),"company_count":len(entity_events),
                     "coverage_state":coverage["coverage"],"scoring_backlog":coverage["scoring_backlog"],
                     "company_rows":len(company_scope),"companies_with_event":len(entity_events),
                     "future_label_rows":len(company_scope)*2 if mode=="SCHEDULED" else 0,
                     "not_return_prediction":True})
    write_json(PUBLIC/f"snapshot_{snapshot_id}_redacted.json",redacted)
    con.close()
    return redacted


def maybe_due_snapshot(run_id: str | None=None) -> dict:
    present=datetime.now(timezone.utc)
    local=present.astimezone(NY)
    if not session_day(local.date()):
        return {"state":"WAITING_VERIFIED_SESSION"}
    cutoff=cutoff_for(local.date())
    if present<cutoff:
        return {"state":"PENDING_MARKET_CUTOFF","next_cutoff_utc":cutoff.isoformat()}
    return create_snapshot(cutoff.isoformat(),mode="SCHEDULED",run_id=run_id)


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--as-of")
    parser.add_argument("--mode",choices=["DEVELOPMENT_REPLAY","SCHEDULED"],default="DEVELOPMENT_REPLAY")
    args=parser.parse_args()
    result=create_snapshot(args.as_of,mode=args.mode) if args.as_of else maybe_due_snapshot()
    print(json.dumps(result,ensure_ascii=False))
