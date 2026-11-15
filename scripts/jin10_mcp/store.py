"""SQLite ledger for raw MCP pages, item versions and resumable coverage."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS streams (
  stream_key TEXT PRIMARY KEY,
  tool_name TEXT NOT NULL,
  replay_cursor_json TEXT,
  next_cursor_json TEXT,
  coverage_status TEXT NOT NULL DEFAULT 'NEVER_RUN',
  stop_requested INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  last_page_at_utc TEXT,
  last_complete_at_utc TEXT,
  last_run_id INTEGER
);
CREATE TABLE IF NOT EXISTS runs (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT,
  stream_key TEXT NOT NULL,
  started_at_utc TEXT NOT NULL,
  finished_at_utc TEXT,
  status TEXT NOT NULL,
  pages INTEGER NOT NULL DEFAULT 0,
  items_seen INTEGER NOT NULL DEFAULT 0,
  requests INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  FOREIGN KEY (stream_key) REFERENCES streams(stream_key)
);
CREATE TABLE IF NOT EXISTS request_attempts (
  attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  tool_name TEXT NOT NULL,
  arguments_json TEXT NOT NULL,
  attempted_at_utc TEXT NOT NULL,
  elapsed_ms INTEGER NOT NULL,
  http_status INTEGER,
  business_status TEXT,
  outcome TEXT NOT NULL,
  error TEXT,
  FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
CREATE TABLE IF NOT EXISTS raw_responses (
  raw_id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  tool_name TEXT NOT NULL,
  arguments_json TEXT NOT NULL,
  response_json TEXT NOT NULL,
  response_sha256 TEXT NOT NULL,
  received_at_utc TEXT NOT NULL,
  http_status INTEGER,
  business_status TEXT,
  cursor_in_json TEXT,
  cursor_out_json TEXT,
  page_complete INTEGER NOT NULL,
  FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
CREATE TABLE IF NOT EXISTS logical_items (
  logical_key TEXT PRIMARY KEY,
  tool_name TEXT NOT NULL,
  source_name TEXT NOT NULL,
  key_kind TEXT NOT NULL,
  source_key TEXT NOT NULL,
  potential_collision INTEGER NOT NULL,
  source_id TEXT,
  source_url TEXT,
  first_received_at_utc TEXT NOT NULL,
  first_published_raw TEXT,
  UNIQUE (tool_name, key_kind, source_key)
);
CREATE TABLE IF NOT EXISTS item_versions (
  version_id INTEGER PRIMARY KEY AUTOINCREMENT,
  logical_key TEXT NOT NULL,
  version_number INTEGER NOT NULL,
  content_sha256 TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  normalized_json TEXT NOT NULL,
  source_published_raw TEXT,
  published_at_utc TEXT,
  source_timezone TEXT NOT NULL,
  time_precision TEXT NOT NULL,
  first_seen_at_utc TEXT NOT NULL,
  source_url TEXT,
  event_category_candidate TEXT NOT NULL,
  issuer_candidates_json TEXT NOT NULL,
  raw_id INTEGER NOT NULL,
  raw_locator TEXT NOT NULL,
  source_status TEXT NOT NULL,
  UNIQUE (logical_key, payload_sha256),
  UNIQUE (logical_key, version_number),
  FOREIGN KEY (logical_key) REFERENCES logical_items(logical_key),
  FOREIGN KEY (raw_id) REFERENCES raw_responses(raw_id)
);
CREATE TABLE IF NOT EXISTS item_fetches (
  fetch_id INTEGER PRIMARY KEY AUTOINCREMENT,
  logical_key TEXT NOT NULL,
  version_id INTEGER NOT NULL,
  raw_id INTEGER NOT NULL,
  raw_locator TEXT NOT NULL,
  fetched_at_utc TEXT NOT NULL,
  FOREIGN KEY (logical_key) REFERENCES logical_items(logical_key),
  FOREIGN KEY (version_id) REFERENCES item_versions(version_id),
  FOREIGN KEY (raw_id) REFERENCES raw_responses(raw_id)
);
CREATE TABLE IF NOT EXISTS event_clusters (
  cluster_id TEXT NOT NULL,
  logical_key TEXT NOT NULL,
  reviewed_at_utc TEXT,
  review_status TEXT NOT NULL DEFAULT 'UNREVIEWED',
  PRIMARY KEY (cluster_id, logical_key),
  FOREIGN KEY (logical_key) REFERENCES logical_items(logical_key)
);
CREATE INDEX IF NOT EXISTS idx_item_versions_first_seen ON item_versions(first_seen_at_utc);
CREATE INDEX IF NOT EXISTS idx_item_fetches_logical ON item_fetches(logical_key, fetched_at_utc);
CREATE INDEX IF NOT EXISTS idx_raw_response_run ON raw_responses(run_id);
"""


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class Jin10Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(SCHEMA)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Jin10Store":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        else:
            self.connection.execute("COMMIT")

    def ensure_stream(self, stream_key: str, tool_name: str) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO streams(stream_key,tool_name) VALUES(?,?)", (stream_key, tool_name))
        self.connection.commit()

    def stream(self, stream_key: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM streams WHERE stream_key=?", (stream_key,)).fetchone()
        if row is None:
            raise KeyError(stream_key)
        return dict(row)

    def request_stop(self, stream_key: str) -> None:
        changed = self.connection.execute(
            "UPDATE streams SET stop_requested=1 WHERE stream_key=?", (stream_key,)).rowcount
        self.connection.commit()
        if changed != 1:
            raise KeyError(stream_key)

    def clear_stop(self, stream_key: str) -> None:
        self.connection.execute("UPDATE streams SET stop_requested=0 WHERE stream_key=?", (stream_key,))
        self.connection.commit()

    def new_run(self, stream_key: str, started_at_utc: str) -> int:
        cursor = self.connection.execute(
            "INSERT INTO runs(stream_key,started_at_utc,status) VALUES(?,?,?)",
            (stream_key, started_at_utc, "RUNNING"),
        )
        run_id = int(cursor.lastrowid)
        self.connection.execute(
            "UPDATE streams SET coverage_status='RUNNING',last_error=NULL,last_run_id=? WHERE stream_key=?",
            (run_id, stream_key),
        )
        self.connection.commit()
        return run_id

    def record_attempt(self, *, run_id: int, tool_name: str, arguments: dict,
                       attempted_at_utc: str, elapsed_ms: int, http_status: int | None,
                       business_status: str | int | None, outcome: str,
                       error: str | None) -> None:
        self.connection.execute(
            """INSERT INTO request_attempts
               (run_id,tool_name,arguments_json,attempted_at_utc,elapsed_ms,http_status,
                business_status,outcome,error) VALUES(?,?,?,?,?,?,?,?,?)""",
            (run_id, tool_name, canonical(arguments), attempted_at_utc, elapsed_ms,
             http_status, None if business_status is None else str(business_status), outcome, error),
        )
        self.connection.execute("UPDATE runs SET requests=requests+1 WHERE run_id=?", (run_id,))
        self.connection.commit()

    def record_page(self, *, run_id: int, stream_key: str, tool_name: str,
                    arguments: dict, response: dict, received_at_utc: str,
                    http_status: int | None, business_status: str | int | None,
                    cursor_in: Any, cursor_out: Any, has_more: bool,
                    normalized_items: list[dict]) -> dict[str, int]:
        raw_json = canonical(response)
        new_versions = 0
        with self.transaction():
            raw_cursor = self.connection.execute(
                """INSERT INTO raw_responses
                   (run_id,tool_name,arguments_json,response_json,response_sha256,received_at_utc,
                    http_status,business_status,cursor_in_json,cursor_out_json,page_complete)
                   VALUES(?,?,?,?,?,?,?,?,?,?,1)""",
                (run_id, tool_name, canonical(arguments), raw_json, sha256_text(raw_json),
                 received_at_utc, http_status,
                 None if business_status is None else str(business_status),
                 canonical(cursor_in), canonical(cursor_out)),
            )
            raw_id = int(raw_cursor.lastrowid)
            for item in normalized_items:
                self.connection.execute(
                    """INSERT OR IGNORE INTO logical_items
                       (logical_key,tool_name,source_name,key_kind,source_key,potential_collision,
                        source_id,source_url,first_received_at_utc,first_published_raw)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (item["logical_key"], tool_name, "jin10_mcp", item["key_kind"], item["source_key"],
                     int(item["potential_collision"]), item["source_id"], item["source_url"],
                     received_at_utc, item["published_raw"]),
                )
                prior = self.connection.execute(
                    "SELECT version_id FROM item_versions WHERE logical_key=? AND payload_sha256=?",
                    (item["logical_key"], item["payload_sha256"]),
                ).fetchone()
                if prior:
                    version_id = int(prior[0])
                else:
                    previous = self.connection.execute(
                        "SELECT COALESCE(MAX(version_number),0) FROM item_versions WHERE logical_key=?",
                        (item["logical_key"],),
                    ).fetchone()[0]
                    version_cursor = self.connection.execute(
                        """INSERT INTO item_versions
                           (logical_key,version_number,content_sha256,payload_sha256,normalized_json,
                            source_published_raw,published_at_utc,source_timezone,time_precision,
                            first_seen_at_utc,source_url,event_category_candidate,issuer_candidates_json,
                            raw_id,raw_locator,source_status)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (item["logical_key"], previous + 1, item["content_sha256"],
                         item["payload_sha256"], canonical(item["normalized"]), item["published_raw"],
                         item["published_at_utc"], item["source_timezone"], item["time_precision"],
                         received_at_utc, item["source_url"], item["event_category_candidate"],
                         canonical(item["issuer_candidates"]), raw_id, item["raw_locator"], "SOURCE_REPORT"),
                    )
                    version_id = int(version_cursor.lastrowid)
                    new_versions += 1
                self.connection.execute(
                    """INSERT INTO item_fetches
                       (logical_key,version_id,raw_id,raw_locator,fetched_at_utc)
                       VALUES(?,?,?,?,?)""",
                    (item["logical_key"], version_id, raw_id, item["raw_locator"], received_at_utc),
                )
            self.connection.execute(
                "UPDATE runs SET pages=pages+1,items_seen=items_seen+? WHERE run_id=?",
                (len(normalized_items), run_id),
            )
            self.connection.execute(
                """UPDATE streams SET replay_cursor_json=?,next_cursor_json=?,coverage_status=?,
                   last_page_at_utc=?,last_complete_at_utc=CASE WHEN ?=0 THEN ? ELSE last_complete_at_utc END
                   WHERE stream_key=?""",
                (canonical(cursor_in) if has_more else None,
                 canonical(cursor_out) if has_more else None,
                 "PARTIAL_MORE_PAGES" if has_more else "COMPLETE_FOR_REQUESTED_RANGE",
                 received_at_utc, int(has_more), received_at_utc, stream_key),
            )
        return {"raw_id": raw_id, "new_versions": new_versions}

    def record_incomplete_response(self, *, run_id: int, tool_name: str, arguments: dict,
                                   response: dict, received_at_utc: str,
                                   http_status: int | None, business_status: str | int | None,
                                   cursor_in: Any) -> int:
        raw_json = canonical(response)
        cursor = self.connection.execute(
            """INSERT INTO raw_responses
               (run_id,tool_name,arguments_json,response_json,response_sha256,received_at_utc,
                http_status,business_status,cursor_in_json,cursor_out_json,page_complete)
               VALUES(?,?,?,?,?,?,?,?,?,?,0)""",
            (run_id, tool_name, canonical(arguments), raw_json, sha256_text(raw_json),
             received_at_utc, http_status,
             None if business_status is None else str(business_status), canonical(cursor_in), None),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def finish_run(self, run_id: int, stream_key: str, status: str, finished_at_utc: str,
                   error: str | None = None) -> dict[str, Any]:
        self.connection.execute(
            "UPDATE runs SET status=?,finished_at_utc=?,error=? WHERE run_id=?",
            (status, finished_at_utc, error, run_id),
        )
        if status != "COMPLETE_FOR_REQUESTED_RANGE":
            self.connection.execute(
                "UPDATE streams SET coverage_status=?,last_error=? WHERE stream_key=?",
                (status, error, stream_key),
            )
        self.connection.commit()
        row = self.connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(row)

    def status(self, stream_key: str) -> dict[str, Any]:
        stream = self.stream(stream_key)
        latest = self.connection.execute(
            "SELECT * FROM runs WHERE stream_key=? ORDER BY run_id DESC LIMIT 1", (stream_key,)
        ).fetchone()
        return {
            "stream_key": stream_key,
            "tool_name": stream["tool_name"],
            "coverage_status": stream["coverage_status"],
            "stop_requested": bool(stream["stop_requested"]),
            "has_replay_checkpoint": stream["replay_cursor_json"] is not None,
            "last_page_at_utc": stream["last_page_at_utc"],
            "last_complete_at_utc": stream["last_complete_at_utc"],
            "last_error": stream["last_error"],
            "last_run": dict(latest) if latest else None,
        }
