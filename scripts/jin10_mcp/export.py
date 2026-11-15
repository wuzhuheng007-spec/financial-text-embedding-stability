"""Sanitized local display export; never returns raw response or flash body."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .store import Jin10Store


def export_display(db_path: Path, limit: int = 50,
                   include_private_content: bool = False) -> dict[str, Any]:
    """Return at most ``limit`` rows per tool; private text is opt-in."""
    if not (1 <= limit <= 500):
        raise ValueError("Display limit must be between 1 and 500")
    if not Path(db_path).is_file():
        return {"schema": "jin10_display_export_v1",
                "raw_provider_text_included": include_private_content,
                "records": [],
                "acquisition_status": [{"coverage_status": "NOT_STARTED",
                                        "last_error": "No local Jin10 response database exists"}],
                "interpretation": "No provider call is recorded; this is not evidence of an empty news feed"}
    with Jin10Store(db_path) as store:
        streams = [store.status(row[0]) for row in store.connection.execute(
            "SELECT stream_key FROM streams ORDER BY stream_key")]
        rows = store.connection.execute(
            """WITH ranked AS (
               SELECT i.logical_key,i.tool_name,i.key_kind,i.potential_collision,
                      v.source_url,i.first_received_at_utc,v.version_number,
                      v.content_sha256,v.source_published_raw,v.published_at_utc,
                      v.source_timezone,v.time_precision,v.event_category_candidate,
                      v.issuer_candidates_json,v.source_status,v.normalized_json,
                      (SELECT MAX(fetched_at_utc) FROM item_fetches f WHERE f.logical_key=i.logical_key)
                        AS last_fetched_at_utc,
                      ROW_NUMBER() OVER (PARTITION BY i.tool_name
                                         ORDER BY i.first_received_at_utc DESC,i.logical_key) AS tool_rank
               FROM logical_items i JOIN item_versions v ON v.logical_key=i.logical_key
               WHERE v.version_number=(SELECT MAX(v2.version_number) FROM item_versions v2
                                       WHERE v2.logical_key=i.logical_key))
               SELECT * FROM ranked WHERE tool_rank<=?
               ORDER BY tool_name,first_received_at_utc DESC,logical_key""", (limit,)
        ).fetchall()
        records = [{
            "source": "jin10_mcp", "tool_name": row["tool_name"],
            "logical_key": row["logical_key"], "key_kind": row["key_kind"],
            "potential_collision": bool(row["potential_collision"]),
            "source_url": row["source_url"],
            "source_published_raw": row["source_published_raw"],
            "published_at_utc": row["published_at_utc"],
            "source_timezone": row["source_timezone"],
            "time_precision": row["time_precision"],
            "first_received_at_utc": row["first_received_at_utc"],
            "last_fetched_at_utc": row["last_fetched_at_utc"],
            "version_number": row["version_number"],
            "content_sha256": row["content_sha256"],
            "event_category_candidate": row["event_category_candidate"],
            "issuer_candidates": json.loads(row["issuer_candidates_json"]),
            "source_status": row["source_status"],
            **({"title": json.loads(row["normalized_json"]).get("title"),
                "content": json.loads(row["normalized_json"]).get("content"),
                "calendar_values": json.loads(row["normalized_json"]).get("calendar_values"),
                "scheduled_for_raw": json.loads(row["normalized_json"]).get("scheduled_for_raw"),
                "scheduled_time_precision": json.loads(row["normalized_json"]).get("scheduled_time_precision"),
                "scheduled_time_status": json.loads(row["normalized_json"]).get("scheduled_time_status"),
                "consensus_snapshot_received_at_utc": json.loads(row["normalized_json"]).get("consensus_snapshot_received_at_utc"),
                "pre_release_consensus_status": json.loads(row["normalized_json"]).get("pre_release_consensus_status"),
                "private_provider_text": True} if include_private_content else {}),
        } for row in rows]
        return {
            "schema": "jin10_display_export_v1",
            "raw_provider_text_included": include_private_content,
            "records": records,
            "acquisition_status": streams,
            "interpretation": "Current local fetches only; issuer links and categories are candidates, not verified facts",
        }
