"""Bounded, offline import from the existing private Jin10 SQLite ledger."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .store import Store, utc_now


def replay_jin10(source_db: Path, target_db: Path, *, limit: int = 100) -> dict[str, Any]:
    if not 1 <= limit <= 500:
        raise ValueError("offline replay limit must be 1..500")
    source_db = Path(source_db)
    if not source_db.is_file():
        raise FileNotFoundError(source_db)
    if source_db.resolve() == Path(target_db).resolve():
        raise ValueError("source and target database must be separate")
    source = sqlite3.connect(f"file:{source_db.as_posix()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        rows = source.execute("""SELECT i.logical_key,i.tool_name,i.key_kind,i.potential_collision,
              i.source_id,i.source_url,i.first_received_at_utc,
              v.version_id,v.version_number,v.normalized_json,v.published_at_utc,
              v.source_timezone,v.time_precision,v.first_seen_at_utc,v.source_status,v.raw_id
              FROM logical_items i JOIN item_versions v ON v.logical_key=i.logical_key
              WHERE lower(i.tool_name) LIKE '%flash%'
              ORDER BY i.first_received_at_utc,i.logical_key,v.version_number LIMIT ?""", (limit,)).fetchall()
        streams = [dict(row) for row in source.execute(
            "SELECT stream_key,coverage_status,last_error FROM streams WHERE lower(tool_name) LIKE '%flash%' ORDER BY stream_key")]
    finally:
        source.close()
    imported = duplicates = 0
    with Store(target_db) as target:
        for row in rows:
            normalized = json.loads(row["normalized_json"])
            key_kind = "PROVIDER_ID" if row["key_kind"] in {"SOURCE_ID", "PROVIDER_ID"} and row["source_id"] else (
                "SOURCE_URL" if row["key_kind"] in {"CHECKED_URL", "URL", "SOURCE_URL"} and row["source_url"] else "COMPOSITE")
            content = str(normalized.get("content") or normalized.get("title") or "")
            received_at = row["first_received_at_utc"] if row["version_number"] == 1 else row["first_seen_at_utc"]
            news_id, created = target.insert_news(
                provider="jin10_mcp", identity_key=row["logical_key"], key_kind=key_kind,
                potential_collision=bool(row["potential_collision"]) or key_kind == "COMPOSITE",
                provider_id=row["source_id"], source_url=row["source_url"],
                received_at=received_at, content=content,
                payload={"normalized": normalized, "source_version": row["version_number"],
                         "offline_replay": True},
                provider_time=row["published_at_utc"],
                source_timezone=row["source_timezone"], time_precision=row["time_precision"],
                status="WITHDRAWN" if row["source_status"] == "WITHDRAWN" else "ACTIVE",
                raw_locator=f"jin10_private_db:item_version={row['version_id']}:raw={row['raw_id']}")
            target.record_news_identity_audit(news_id, source_key_kind=row["key_kind"],
                                              inspected_key_kind=key_kind,
                                              potential_collision=bool(row["potential_collision"]),
                                              reason="source ledger key kind read during bounded offline replay")
            imported += int(created)
            duplicates += int(not created)
        for stream in streams:
            if stream["coverage_status"] != "COMPLETE":
                existing = target.connection.execute("""SELECT 1 FROM collection_gaps WHERE
                  source='jin10_mcp' AND stream_key=? AND reason='OFFLINE_SOURCE_COVERAGE_INCOMPLETE'
                  LIMIT 1""", (stream["stream_key"],)).fetchone()
                if not existing:
                    target.record_gap("jin10_mcp", stream["stream_key"], "OFFLINE_SOURCE_COVERAGE_INCOMPLETE",
                                      detail={"source_coverage_status": stream["coverage_status"]})
        if len(rows) >= limit:
            existing = target.connection.execute("""SELECT 1 FROM collection_gaps WHERE
              source='jin10_mcp' AND stream_key='offline_replay' AND reason='REPLAY_ROW_BUDGET_REACHED'
              AND detail_json=? LIMIT 1""", (json.dumps({"limit": limit}, sort_keys=True, separators=(",", ":")),)).fetchone()
            if not existing:
                target.record_gap("jin10_mcp", "offline_replay", "REPLAY_ROW_BUDGET_REACHED",
                                  detail={"limit": limit})
    return {"mode": "OFFLINE_REPLAY", "provider_calls": 0, "received_at_utc": utc_now(),
            "source_rows_read": len(rows), "versions_inserted": imported,
            "duplicates": duplicates, "source_stream_statuses": streams,
            "limit": limit, "coverage": "BOUNDED_SOURCE_REPLAY_NOT_NEW_ACQUISITION"}
