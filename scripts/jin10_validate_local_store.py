"""Read-only structural audit of a private Jin10 SQLite probe import."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.jin10_mcp.engine import resolve_raw_locator
from scripts.jin10_mcp.store import Jin10Store


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOT = ROOT / "artifacts"
PUBLIC_ROOT = ROOT / "results/news_jin10_20261115"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--public-output", type=Path, required=True)
    args = parser.parse_args()
    db = args.db.resolve()
    captures = args.capture_dir.resolve()
    public = args.public_output.resolve()
    if not db.is_relative_to(PRIVATE_ROOT) or not captures.is_relative_to(PRIVATE_ROOT):
        raise ValueError("Database and captures must remain ignored private artifacts")
    if not public.is_relative_to(PUBLIC_ROOT) or public.exists():
        raise ValueError("Public receipt must be a new Jin10 results file")
    if not db.is_file():
        raise FileNotFoundError(db)
    with Jin10Store(db) as store:
        counts = {table: store.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for table in ("streams", "runs", "request_attempts", "raw_responses",
                                "logical_items", "item_versions", "item_fetches", "event_clusters")}
        streams = {row["stream_key"]: row["coverage_status"]
                   for row in store.connection.execute("SELECT stream_key,coverage_status FROM streams")}
        per_tool = {row[0]: {"raw_pages": row[1], "logical_items": row[2], "versions": row[3]}
                    for row in store.connection.execute(
                        """SELECT r.tool_name,COUNT(DISTINCT r.raw_id),COUNT(DISTINCT i.logical_key),
                                  COUNT(DISTINCT v.version_id)
                           FROM raw_responses r JOIN item_versions v ON v.raw_id=r.raw_id
                           JOIN logical_items i ON i.logical_key=v.logical_key
                           GROUP BY r.tool_name""")}
        locators_checked = 0
        for row in store.connection.execute(
            "SELECT r.response_json,v.raw_locator FROM raw_responses r JOIN item_versions v ON v.raw_id=r.raw_id"
        ):
            item = resolve_raw_locator(json.loads(row[0]), row[1])
            if not isinstance(item, dict):
                raise AssertionError("Version locator did not resolve to a provider object")
            locators_checked += 1
        raw_hashes_checked = 0
        for row in store.connection.execute("SELECT response_json,response_sha256 FROM raw_responses"):
            if hashlib.sha256(row[0].encode("utf-8")).hexdigest() != row[1]:
                raise AssertionError("Stored raw JSON hash differs")
            raw_hashes_checked += 1
        calendar_time_misused = store.connection.execute(
            """SELECT COUNT(*) FROM item_versions v JOIN logical_items i ON i.logical_key=v.logical_key
               WHERE i.tool_name='list_calendar' AND
                 (v.source_published_raw IS NOT NULL OR v.published_at_utc IS NOT NULL)""").fetchone()[0]
        scheduled_count = store.connection.execute(
            """SELECT COUNT(*) FROM item_versions v JOIN logical_items i ON i.logical_key=v.logical_key
               WHERE i.tool_name='list_calendar' AND
                 json_extract(v.normalized_json,'$.scheduled_for_raw') IS NOT NULL""").fetchone()[0]
        verified_fact_pending = store.connection.execute(
            """SELECT COUNT(*) FROM item_versions
               WHERE json_extract(normalized_json,'$.verified_fact_status')='NOT_VERIFIED'""").fetchone()[0]
        if calendar_time_misused:
            raise AssertionError("Theoretical calendar time was mislabeled as actual publication")
        if verified_fact_pending != counts["item_versions"]:
            raise AssertionError("Verified-fact status is missing on a stored item")
        flash_key_status = {row[0]: row[1] for row in store.connection.execute(
            """SELECT key_kind,COUNT(*) FROM logical_items WHERE tool_name='list_flash'
               GROUP BY key_kind""")}
        calendar_key_status = {row[0]: row[1] for row in store.connection.execute(
            """SELECT key_kind,COUNT(*) FROM logical_items WHERE tool_name='list_calendar'
               GROUP BY key_kind""")}
        flash_status = store.status("flash")
        calendar_status = store.status("calendar")
    receipt = {
        "schema": "jin10_private_store_validation_v1",
        "input_capture_sha256": {
            "flash": sha256(captures / "flash_captures.json"),
            "calendar": sha256(captures / "calendar_captures.json"),
            "tools_list": sha256(captures / "tools_list.json"),
        },
        "database_private_path": db.relative_to(ROOT).as_posix(),
        "counts": counts, "per_tool": per_tool,
        "source_item_locators_reread": locators_checked,
        "raw_response_hashes_reread": raw_hashes_checked,
        "calendar_published_time_mislabels": calendar_time_misused,
        "calendar_scheduled_time_records": scheduled_count,
        "verified_fact_not_verified_records": verified_fact_pending,
        "flash_key_kinds": flash_key_status,
        "calendar_key_kinds": calendar_key_status,
        "stream_status": streams,
        "flash_has_replay_checkpoint": flash_status["has_replay_checkpoint"],
        "calendar_has_replay_checkpoint": calendar_status["has_replay_checkpoint"],
        "network_requests_in_offline_import": counts["request_attempts"],
        "scope": "Read-only audit of the bounded probe import; no new provider call",
    }
    public.parent.mkdir(parents=True, exist_ok=True)
    public.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8", newline="\n")
    print(json.dumps({"locators_checked": locators_checked,
                      "raw_hashes_checked": raw_hashes_checked,
                      "calendar_time_mislabels": calendar_time_misused,
                      "stream_status": streams}, ensure_ascii=False))


if __name__ == "__main__":
    main()
