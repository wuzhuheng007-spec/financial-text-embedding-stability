"""Append-only private article versions for the v4 research pilot."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .preflight import CONFIG, PRIVATE, PUBLIC, save, sha
from .probe import LEDGER

DB = PRIVATE / "news_research.sqlite3"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def english_candidate(text: str) -> bool:
    """Conservative script gate; still requires semantic/language review."""
    letters = [c for c in text if c.isalpha()]
    return bool(text) and len(letters) >= 8 and all(c.isascii() for c in letters)


def from_millis(value: object) -> str | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not 1_500_000_000_000 <= value <= 2_000_000_000_000:
        return None
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()


def frozen_window_bounds_utc() -> list[tuple[str, str]]:
    """Convert frozen NY calendar-day windows to half-open UTC intervals."""
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    try:
        zone = ZoneInfo("America/New_York")
    except ZoneInfoNotFoundError:
        # The isolated v3 research venv has no tzdata package. Reuse the
        # machine's installed IANA file; never substitute a fixed UTC offset.
        zone_file = Path(sys.base_prefix) / "Lib/site-packages/tzdata/zoneinfo/America/New_York"
        with zone_file.open("rb") as handle:
            zone = ZoneInfo.from_file(handle, key="America/New_York")
    return [(datetime.fromisoformat(start).replace(tzinfo=zone).astimezone(timezone.utc).isoformat(),
             datetime.fromisoformat(end).replace(tzinfo=zone).astimezone(timezone.utc).isoformat())
            for start, end in config["historical_windows_in_provider_market_timezone"]]


def provider_response_time(path: Path) -> str | None:
    """Local response-read completion, distinct from later SQLite ingestion."""
    if not LEDGER.is_file():
        return None
    resolved = str(path.resolve())
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        if line:
            row = json.loads(line)
            if row.get("raw_path") == resolved and row.get("raw_sha256") == sha(path):
                return row.get("completed_at_utc")
    return None


def init(db: Path = DB) -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db)) as con:
        con.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS articles (
          provider TEXT NOT NULL, article_id TEXT NOT NULL, version INTEGER NOT NULL,
          publisher TEXT, source_url TEXT, title TEXT, abstract TEXT, body TEXT,
          language TEXT NOT NULL, tickers_json TEXT NOT NULL, content_type TEXT NOT NULL,
          published_raw TEXT, published_at_utc TEXT, updated_raw TEXT, updated_at_utc TEXT,
          first_received_at_utc TEXT NOT NULL, text_sha256 TEXT NOT NULL,
          raw_response_sha256 TEXT NOT NULL, raw_response_path TEXT NOT NULL,
          time_evidence TEXT NOT NULL, observation_mode TEXT NOT NULL,
          event_cluster_id TEXT NOT NULL, related_report_status TEXT NOT NULL,
          response_received_at_utc TEXT,
          PRIMARY KEY(provider,article_id,version));
        CREATE TABLE IF NOT EXISTS scores (
          provider TEXT NOT NULL, article_id TEXT NOT NULL, version INTEGER NOT NULL,
          input_kind TEXT NOT NULL, model_revision TEXT NOT NULL, config_sha256 TEXT NOT NULL,
          input_sha256 TEXT NOT NULL, token_count INTEGER, truncated INTEGER NOT NULL,
          lm_json TEXT, probabilities_json TEXT, net_score REAL, status TEXT NOT NULL,
          error_class TEXT, scored_at_utc TEXT, signal_available_at_utc TEXT,
          score_origin TEXT NOT NULL,
          PRIMARY KEY(provider,article_id,version,input_kind,model_revision,config_sha256));
        CREATE TABLE IF NOT EXISTS observations (
          observed_at_utc TEXT PRIMARY KEY, provider TEXT NOT NULL, request_count INTEGER NOT NULL,
          new_versions INTEGER NOT NULL, duplicate_versions INTEGER NOT NULL,
          english_candidates INTEGER NOT NULL, notes TEXT);
        """)
        if "response_received_at_utc" not in {r[1] for r in con.execute("PRAGMA table_info(articles)")}:
            con.execute("ALTER TABLE articles ADD COLUMN response_received_at_utc TEXT")
            con.commit()


def ingest_moomoo_probe(path: Path, *, mode: str, observation_start_utc: str | None = None,
                        db: Path = DB) -> dict:
    if mode not in {"BASELINE", "PROSPECTIVE"}:
        raise ValueError("BAD_MODE")
    if mode == "PROSPECTIVE" and not observation_start_utc:
        raise ValueError("PROSPECTIVE_REQUIRES_OBSERVATION_START")
    init(db)
    raw = path.read_bytes()
    payload = json.loads(raw)
    if payload.get("ret_code") != 0 or not isinstance(payload.get("data", {}).get("items"), list):
        raise ValueError("MOOMOO_NO_NEWS_ITEMS")
    received = utc_now()
    response_received = provider_response_time(path)
    raw_sha = hashlib.sha256(raw).hexdigest()
    counts = {"response_items": 0, "new_versions": 0, "duplicate_versions": 0,
              "english_title_candidates": 0, "historical_frozen_window_items": 0,
              "time_unknown": 0}
    windows = frozen_window_bounds_utc()
    with closing(sqlite3.connect(db)) as con:
        for item in payload["data"]["items"]:
            counts["response_items"] += 1
            if not isinstance(item, dict) or not isinstance(item.get("news_id"), str):
                counts["time_unknown"] += 1
                continue
            aid = item["news_id"]
            title = item.get("title") if isinstance(item.get("title"), str) else ""
            abstract = item.get("abstract") if isinstance(item.get("abstract"), str) else ""
            body = item.get("content") if isinstance(item.get("content"), str) else ""
            published = from_millis(item.get("publish_time"))
            if published is None:
                counts["time_unknown"] += 1
            else:
                if any(a <= published < b for a, b in windows):
                    counts["historical_frozen_window_items"] += 1
            content = title + "\n" + abstract + "\n" + body
            content_sha = text_hash(content)
            old = con.execute("SELECT version,text_sha256 FROM articles WHERE provider='moomoo_webapi' AND article_id=? ORDER BY version DESC LIMIT 1", (aid,)).fetchone()
            if old and old[1] == content_sha:
                counts["duplicate_versions"] += 1
                continue
            version = old[0] + 1 if old else 1
            language = "ENGLISH_CANDIDATE" if english_candidate(title) else "NON_ENGLISH_OR_UNRESOLVED"
            counts["english_title_candidates"] += language == "ENGLISH_CANDIDATE"
            publisher = item.get("source") if isinstance(item.get("source"), str) else None
            source_url = item.get("source_url") or item.get("url")
            # A matching title is merely a possible reprint, never source corroboration.
            normalized_title = re.sub(r"\s+", " ", title.strip().casefold())
            cluster = text_hash(f"moomoo_webapi|{aid}")
            suspected = con.execute("SELECT COUNT(*) FROM articles WHERE provider='moomoo_webapi' AND article_id<>? AND title=?",
                                    (aid, title)).fetchone()[0]
            effective_mode = mode
            if mode == "PROSPECTIVE" and (published is None or published < observation_start_utc or published > received):
                effective_mode = "LATE_FIRST_RECEIPT"
            con.execute("""INSERT INTO articles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        ("moomoo_webapi", aid, version, publisher, source_url, title, abstract, body,
                         language, "[]", "BODY" if body else "ABSTRACT" if abstract else "TITLE",
                         str(item.get("publish_time")) if item.get("publish_time") is not None else None,
                         published, None, None, received, content_sha, raw_sha, str(path),
                         "PUBLISH_OR_UPDATE_PROXY" if published else "UNKNOWN", effective_mode,
                         cluster, "SUSPECTED_SAME_TITLE" if suspected and normalized_title else "NOT_CHECKED",
                         response_received))
            counts["new_versions"] += 1
        con.execute("INSERT INTO observations VALUES (?,?,?,?,?,?,?)",
                    (received, "moomoo_webapi", 1, counts["new_versions"],
                     counts["duplicate_versions"], counts["english_title_candidates"], mode))
        con.commit()
    return counts


def summarize(db: Path = DB) -> dict:
    init(db)
    windows = frozen_window_bounds_utc()
    predicate = " OR ".join("(published_at_utc >= ? AND published_at_utc < ?)" for _ in windows)
    params = tuple(x for pair in windows for x in pair)
    with closing(sqlite3.connect(db)) as con:
        q = lambda sql: con.execute(sql).fetchone()[0]
        result = {"schema": "news_sentiment_v4_coverage_redacted_1", "generated_at_utc": utc_now(),
                  "article_versions": q("SELECT COUNT(*) FROM articles"),
                  "distinct_provider_articles": q("SELECT COUNT(DISTINCT provider||':'||article_id) FROM articles"),
                  "candidate_english_headlines": q("SELECT COUNT(*) FROM articles WHERE language='ENGLISH_CANDIDATE'"),
                  "historical_window_versions": con.execute("SELECT COUNT(*) FROM articles WHERE " + predicate, params).fetchone()[0],
                  "prospective_versions": q("SELECT COUNT(*) FROM articles WHERE observation_mode='PROSPECTIVE'"),
                  "late_first_receipt_versions": q("SELECT COUNT(*) FROM articles WHERE observation_mode='LATE_FIRST_RECEIPT'"),
                  "completed_scores": q("SELECT COUNT(*) FROM scores WHERE status='DONE'"),
                  "failed_scores": q("SELECT COUNT(*) FROM scores WHERE status='FAILED'"),
                  "prospective_completed_scores": q("SELECT COUNT(*) FROM scores WHERE status='DONE' AND score_origin='PROSPECTIVE_RECEIPT'"),
                  "verified_distinct_event_clusters": 0,
                  "unresolved_article_id_proxy_groups": q("SELECT COUNT(DISTINCT event_cluster_id) FROM articles"),
                  "valid_company_days": 0,
                  "independent_historical_dates": 0,
                  "company_day_reason": "NO_ELIGIBLE_HISTORICAL_ENGLISH_NEWS_OR_VERIFIED_COMPANY_ASSOCIATION",
                  "source_concentration": {"moomoo_webapi": q("SELECT COUNT(*) FROM articles WHERE provider='moomoo_webapi'")},
                  "historical_archive_complete": False,
                  "historical_no_news_days_eligible": False}
    save(PUBLIC / "coverage_redacted.json", result)
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("ingest", "summary"))
    parser.add_argument("--path", type=Path)
    parser.add_argument("--observation-mode", choices=("BASELINE", "PROSPECTIVE"), default="BASELINE")
    parser.add_argument("--observation-start-utc")
    args = parser.parse_args()
    result = ingest_moomoo_probe(args.path, mode=args.observation_mode,
                                 observation_start_utc=args.observation_start_utc) if args.mode == "ingest" else summarize()
    print(json.dumps(result, ensure_ascii=False))
