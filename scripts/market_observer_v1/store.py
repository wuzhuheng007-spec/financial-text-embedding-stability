"""Private SQLite ledger for versioned observations and immutable judgments.

The store accepts already acquired observations. It never calls a provider and
does not infer that a local receipt is the first public availability time.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "market_observer_v1"
CLAIM_STATUSES = {
    "PRIMARY_CONFIRMED", "INDEPENDENTLY_CORROBORATED", "SAME_ORIGIN_REPRINT",
    "SINGLE_SOURCE", "CONFLICTING", "UNVERIFIED",
}
DELAY_STATUSES = {"REALTIME", "DELAYED", "UNKNOWN", "STALE"}
MARKET_SESSIONS = {"REGULAR", "REGULAR_OPEN", "PRE_MARKET", "AFTER_HOURS", "CLOSED", "UNKNOWN"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def require_utc(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{field} must be an explicit UTC ISO 8601 timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} is not a valid UTC timestamp") from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{field} must be UTC")
    return value


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS sessions (
 session_id TEXT PRIMARY KEY, mode TEXT NOT NULL, started_at TEXT NOT NULL,
 ended_at TEXT, heartbeat_at TEXT NOT NULL, status TEXT NOT NULL,
 stop_requested INTEGER NOT NULL DEFAULT 0, pid INTEGER, error TEXT,
 config_hash TEXT NOT NULL, schema_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS instruments (
 instrument_id TEXT PRIMARY KEY, provider TEXT NOT NULL, requested_symbol TEXT NOT NULL,
 provider_code TEXT, market TEXT, identity_status TEXT NOT NULL,
 verified_at TEXT, metadata_json TEXT NOT NULL DEFAULT '{}', schema_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS instrument_resolutions (
 candidate_id TEXT NOT NULL, verified_id TEXT NOT NULL,
 verified_at TEXT NOT NULL, evidence_hash TEXT NOT NULL,
 source_tool TEXT NOT NULL,
 PRIMARY KEY(candidate_id,verified_id,evidence_hash),
 FOREIGN KEY(candidate_id) REFERENCES instruments(instrument_id),
 FOREIGN KEY(verified_id) REFERENCES instruments(instrument_id)
);
CREATE TABLE IF NOT EXISTS probe_observations (
 probe_id TEXT PRIMARY KEY, provider TEXT NOT NULL, tool_name TEXT NOT NULL,
 instrument_id TEXT NOT NULL, provider_time TEXT NOT NULL,
 receipt_lower_bound TEXT NOT NULL, receipt_upper_bound TEXT NOT NULL,
 delay_status TEXT NOT NULL, market_session TEXT NOT NULL,
 payload_hash TEXT NOT NULL, payload_json TEXT NOT NULL,
 source_hash TEXT NOT NULL, recorded_at TEXT NOT NULL,
 status TEXT NOT NULL,
 FOREIGN KEY(instrument_id) REFERENCES instruments(instrument_id)
);
CREATE TRIGGER IF NOT EXISTS probe_observations_no_update BEFORE UPDATE ON probe_observations
BEGIN SELECT RAISE(ABORT,'probe observations are append-only'); END;
CREATE TRIGGER IF NOT EXISTS probe_observations_no_delete BEFORE DELETE ON probe_observations
BEGIN SELECT RAISE(ABORT,'probe observations are append-only'); END;
CREATE TABLE IF NOT EXISTS quotes_or_bars (
 id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT NOT NULL,
 instrument_id TEXT NOT NULL, kind TEXT NOT NULL,
 provider_time TEXT NOT NULL, received_at TEXT NOT NULL, persisted_at TEXT NOT NULL,
 source_timezone TEXT NOT NULL, market_session TEXT NOT NULL,
 delay_status TEXT NOT NULL, input_hash TEXT NOT NULL,
 schema_version TEXT NOT NULL, version INTEGER NOT NULL,
 revision_of INTEGER, payload_json TEXT NOT NULL,
 UNIQUE(provider,instrument_id,kind,provider_time,input_hash),
 UNIQUE(provider,instrument_id,kind,provider_time,version),
 FOREIGN KEY(instrument_id) REFERENCES instruments(instrument_id),
 FOREIGN KEY(revision_of) REFERENCES quotes_or_bars(id)
);
CREATE INDEX IF NOT EXISTS idx_quotes_instrument_time ON quotes_or_bars(instrument_id,provider_time);
CREATE TABLE IF NOT EXISTS news_items (
 id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT NOT NULL,
 identity_key TEXT NOT NULL, key_kind TEXT NOT NULL, potential_collision INTEGER NOT NULL,
 provider_id TEXT, source_url TEXT, original_publisher TEXT,
 provider_time TEXT, source_timezone TEXT NOT NULL, time_precision TEXT NOT NULL,
 first_received_at TEXT NOT NULL, received_at TEXT NOT NULL, persisted_at TEXT NOT NULL,
 content_hash TEXT NOT NULL, input_hash TEXT NOT NULL, version INTEGER NOT NULL,
 revision_of INTEGER, status TEXT NOT NULL, raw_locator TEXT, payload_json TEXT NOT NULL,
 schema_version TEXT NOT NULL,
 UNIQUE(provider,identity_key,input_hash), UNIQUE(provider,identity_key,version),
 FOREIGN KEY(revision_of) REFERENCES news_items(id)
);
CREATE INDEX IF NOT EXISTS idx_news_identity ON news_items(provider,identity_key,version);
CREATE TABLE IF NOT EXISTS news_identity_audits (
 audit_id INTEGER PRIMARY KEY AUTOINCREMENT, news_id INTEGER NOT NULL,
 source_key_kind TEXT NOT NULL, inspected_key_kind TEXT NOT NULL,
 potential_collision INTEGER NOT NULL, reason TEXT NOT NULL, recorded_at TEXT NOT NULL,
 UNIQUE(news_id,source_key_kind,inspected_key_kind,potential_collision,reason),
 FOREIGN KEY(news_id) REFERENCES news_items(id)
);
CREATE TABLE IF NOT EXISTS news_fetches (
 id INTEGER PRIMARY KEY AUTOINCREMENT, news_id INTEGER NOT NULL,
 fetched_at TEXT NOT NULL, raw_locator TEXT,
 FOREIGN KEY(news_id) REFERENCES news_items(id)
);
CREATE TABLE IF NOT EXISTS observer_audit_notes (
 note_id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT NOT NULL,
 record_ids_json TEXT NOT NULL, description TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS event_clusters (
 event_id TEXT PRIMARY KEY, cluster_key TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 status TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS event_news (
 event_id TEXT NOT NULL, news_id INTEGER NOT NULL, linked_at TEXT NOT NULL,
 PRIMARY KEY(event_id,news_id),
 FOREIGN KEY(event_id) REFERENCES event_clusters(event_id),
 FOREIGN KEY(news_id) REFERENCES news_items(id)
);
CREATE TABLE IF NOT EXISTS source_claims (
 claim_id TEXT PRIMARY KEY, claim_group_id TEXT NOT NULL,
 origin_id TEXT, relation TEXT NOT NULL,
 primary_document_verified INTEGER NOT NULL DEFAULT 0,
 event_id TEXT,
 news_id INTEGER, claim_text TEXT NOT NULL, status TEXT NOT NULL,
 source_url TEXT, source_type TEXT, platform TEXT, provider_time TEXT,
 source_timezone TEXT NOT NULL DEFAULT 'UNKNOWN',
 time_precision TEXT NOT NULL DEFAULT 'UNKNOWN',
 received_at TEXT NOT NULL, persisted_at TEXT NOT NULL,
 evidence_hash TEXT NOT NULL,
 schema_version TEXT NOT NULL,
 FOREIGN KEY(event_id) REFERENCES event_clusters(event_id),
 FOREIGN KEY(news_id) REFERENCES news_items(id)
);
CREATE TABLE IF NOT EXISTS model_versions (
 version_id TEXT PRIMARY KEY, kind TEXT NOT NULL, created_at TEXT NOT NULL,
 source_hash TEXT NOT NULL, config_json TEXT NOT NULL,
 status TEXT NOT NULL, rollback_to TEXT,
 FOREIGN KEY(rollback_to) REFERENCES model_versions(version_id)
);
CREATE TRIGGER IF NOT EXISTS source_claims_no_update BEFORE UPDATE ON source_claims
BEGIN SELECT RAISE(ABORT,'claim evidence is append-only'); END;
CREATE TRIGGER IF NOT EXISTS source_claims_no_delete BEFORE DELETE ON source_claims
BEGIN SELECT RAISE(ABORT,'claim evidence is append-only'); END;
CREATE TRIGGER IF NOT EXISTS model_versions_no_update BEFORE UPDATE ON model_versions
BEGIN SELECT RAISE(ABORT,'model versions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS model_versions_no_delete BEFORE DELETE ON model_versions
BEGIN SELECT RAISE(ABORT,'model versions are append-only'); END;
CREATE TABLE IF NOT EXISTS predictions (
 prediction_id TEXT PRIMARY KEY, event_id TEXT NOT NULL,
 instrument_id TEXT NOT NULL, created_at TEXT NOT NULL,
 persisted_at TEXT NOT NULL, information_cutoff TEXT NOT NULL,
 input_snapshot_hash TEXT NOT NULL, input_manifest_json TEXT NOT NULL,
 model_version TEXT,
 prompt_version TEXT, rule_version TEXT NOT NULL,
 evaluation_protocol_hash TEXT NOT NULL, price_basis TEXT NOT NULL,
 calendar_version TEXT NOT NULL,
 horizon_minutes INTEGER NOT NULL, direction TEXT NOT NULL,
 rationale TEXT NOT NULL, transmission_json TEXT NOT NULL,
 counter_scenario TEXT NOT NULL, data_quality_status TEXT NOT NULL,
 parent_prediction_id TEXT, schema_version TEXT NOT NULL,
 CHECK(horizon_minutes > 0),
 CHECK(direction IN ('UP','DOWN','ABSTAIN')),
 CHECK(julianday(created_at) <= julianday(persisted_at)),
 CHECK(julianday(information_cutoff) <= julianday(created_at)),
 FOREIGN KEY(event_id) REFERENCES event_clusters(event_id),
 FOREIGN KEY(instrument_id) REFERENCES instruments(instrument_id),
 FOREIGN KEY(parent_prediction_id) REFERENCES predictions(prediction_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_prediction_event_once
 ON predictions(event_id,instrument_id,rule_version)
 WHERE parent_prediction_id IS NULL;
CREATE TABLE IF NOT EXISTS outcomes (
 outcome_id TEXT PRIMARY KEY, prediction_id TEXT NOT NULL,
 created_at TEXT NOT NULL, status TEXT NOT NULL,
 start_bar_id INTEGER, end_bar_id INTEGER,
 return_value REAL, qqq_return REAL, excess_return REAL,
 direction_hit INTEGER, baseline_direction TEXT, baseline_hit INTEGER,
 reason TEXT, input_hash TEXT NOT NULL, input_manifest_json TEXT,
 schema_version TEXT NOT NULL,
 FOREIGN KEY(prediction_id) REFERENCES predictions(prediction_id),
 FOREIGN KEY(start_bar_id) REFERENCES quotes_or_bars(id),
 FOREIGN KEY(end_bar_id) REFERENCES quotes_or_bars(id)
);
CREATE TABLE IF NOT EXISTS review_queue (
 review_id TEXT PRIMARY KEY, event_id TEXT, prediction_id TEXT,
 category TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
 evidence_json TEXT NOT NULL, model_prediction_json TEXT,
 objective_outcome_json TEXT, human_ruling_json TEXT,
 FOREIGN KEY(event_id) REFERENCES event_clusters(event_id),
 FOREIGN KEY(prediction_id) REFERENCES predictions(prediction_id)
);
CREATE TABLE IF NOT EXISTS collection_gaps (
 gap_id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL,
 stream_key TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT,
 reason TEXT NOT NULL, coverage_status TEXT NOT NULL,
 detail_json TEXT NOT NULL, recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checkpoints (
 source TEXT NOT NULL, stream_key TEXT NOT NULL,
 complete_cursor_json TEXT, latest_seen_at TEXT,
 coverage_status TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(source,stream_key)
);
CREATE TRIGGER IF NOT EXISTS predictions_no_update BEFORE UPDATE ON predictions
BEGIN SELECT RAISE(ABORT,'predictions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS predictions_no_delete BEFORE DELETE ON predictions
BEGIN SELECT RAISE(ABORT,'predictions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS outcomes_no_update BEFORE UPDATE ON outcomes
BEGIN SELECT RAISE(ABORT,'outcomes are append-only'); END;
CREATE TRIGGER IF NOT EXISTS outcomes_no_delete BEFORE DELETE ON outcomes
BEGIN SELECT RAISE(ABORT,'outcomes are append-only'); END;
CREATE TRIGGER IF NOT EXISTS quote_no_update BEFORE UPDATE ON quotes_or_bars
BEGIN SELECT RAISE(ABORT,'quotes are append-only'); END;
CREATE TRIGGER IF NOT EXISTS quote_no_delete BEFORE DELETE ON quotes_or_bars
BEGIN SELECT RAISE(ABORT,'quotes are append-only'); END;
CREATE TRIGGER IF NOT EXISTS news_no_update BEFORE UPDATE ON news_items
BEGIN SELECT RAISE(ABORT,'news versions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS news_no_delete BEFORE DELETE ON news_items
BEGIN SELECT RAISE(ABORT,'news versions are append-only'); END;
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=30000")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(SCHEMA)
        self._add_compatible_columns()

    def _add_compatible_columns(self) -> None:
        """Additive migration for an earlier private v1 database."""
        additions = {
            "source_claims": {
                "claim_group_id": "TEXT", "origin_id": "TEXT", "relation": "TEXT",
                "primary_document_verified": "INTEGER NOT NULL DEFAULT 0",
                "persisted_at": "TEXT",
                "source_timezone": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
                "time_precision": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
                "platform": "TEXT",
            },
            "outcomes": {"input_manifest_json": "TEXT"},
            "predictions": {"input_manifest_json": "TEXT"},
        }
        for table, columns in additions.items():
            existing = {row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})")}
            for name, declaration in columns.items():
                if name not in existing:
                    self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
        self.connection.commit()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def upsert_instrument(self, instrument_id: str, provider: str, requested_symbol: str,
                          *, provider_code: str | None = None, market: str | None = None,
                          identity_status: str = "UNVERIFIED", verified_at: str | None = None,
                          metadata: dict[str, Any] | None = None) -> None:
        if verified_at:
            require_utc(verified_at, "verified_at")
        with self.connection:
            self.connection.execute("""INSERT INTO instruments
              (instrument_id,provider,requested_symbol,provider_code,market,identity_status,
               verified_at,metadata_json,schema_version) VALUES(?,?,?,?,?,?,?,?,?)
              ON CONFLICT(instrument_id) DO UPDATE SET
               provider_code=excluded.provider_code,market=excluded.market,
               identity_status=excluded.identity_status,verified_at=excluded.verified_at,
               metadata_json=excluded.metadata_json""",
              (instrument_id,provider,requested_symbol,provider_code,market,identity_status,
               verified_at,canonical(metadata or {}),SCHEMA_VERSION))

    def seed_candidate_instruments(self, symbols: list[str]) -> int:
        """Register requested strings only; never downgrade a resolved identity."""
        inserted = 0
        with self.connection:
            for symbol in symbols:
                if not isinstance(symbol, str) or not symbol or not symbol.isascii() or not symbol.isalnum():
                    raise ValueError("candidate symbols must be nonempty ASCII alphanumeric strings")
                cur = self.connection.execute("""INSERT OR IGNORE INTO instruments
                  (instrument_id,provider,requested_symbol,provider_code,market,
                   identity_status,verified_at,metadata_json,schema_version)
                  VALUES(?,?,?,NULL,NULL,'PENDING_OFFICIAL_LOOKUP',NULL,'{}',?)""",
                  (f"CANDIDATE:{symbol}","moomoo_requested",symbol,SCHEMA_VERSION))
                inserted += cur.rowcount
        return inserted

    def record_instrument_resolution(self, *, requested_symbol: str,
                                     verified_id: str, provider_code: str,
                                     market: str, verified_at: str,
                                     evidence_hash: str, source_tool: str,
                                     metadata: dict[str, Any] | None = None) -> None:
        """Retain the requested candidate and add a separately sourced identity."""
        require_utc(verified_at, "verified_at")
        candidate_id = f"CANDIDATE:{requested_symbol}"
        candidate = self.connection.execute("SELECT instrument_id FROM instruments WHERE instrument_id=?",
                                            (candidate_id,)).fetchone()
        if not candidate:
            raise ValueError("candidate string was not registered before resolution")
        if not all((verified_id, provider_code, market, evidence_hash, source_tool)):
            raise ValueError("resolved identity requires official source evidence")
        existing = self.connection.execute("SELECT * FROM instruments WHERE instrument_id=?",
                                           (verified_id,)).fetchone()
        if existing and (existing["provider_code"] != provider_code or existing["market"] != market or
                         existing["requested_symbol"] != requested_symbol):
            raise ValueError("existing verified identity conflicts with new source; review required")
        if not existing:
            self.upsert_instrument(verified_id, "moomoo", requested_symbol,
                                   provider_code=provider_code, market=market,
                                   identity_status="VERIFIED", verified_at=verified_at,
                                   metadata=metadata or {})
        with self.connection:
            self.connection.execute("""INSERT OR IGNORE INTO instrument_resolutions
              (candidate_id,verified_id,verified_at,evidence_hash,source_tool)
              VALUES(?,?,?,?,?)""", (candidate_id,verified_id,verified_at,evidence_hash,source_tool))

    def insert_probe_snapshot(self, *, probe_id: str, provider: str, tool_name: str,
                              instrument_id: str, provider_time: str,
                              receipt_lower_bound: str, receipt_upper_bound: str,
                              delay_status: str, market_session: str,
                              payload: dict[str, Any], source_hash: str) -> bool:
        """Quarantine a quote whose exact receipt/delay/session is unavailable."""
        for field, value in (("provider_time", provider_time),
                             ("receipt_lower_bound", receipt_lower_bound),
                             ("receipt_upper_bound", receipt_upper_bound)):
            require_utc(value, field)
        parse = lambda value: datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parse(receipt_lower_bound) > parse(receipt_upper_bound):
            raise ValueError("probe receipt interval is reversed")
        instrument = self.connection.execute("SELECT identity_status FROM instruments WHERE instrument_id=?",
                                             (instrument_id,)).fetchone()
        if not instrument or instrument["identity_status"] != "VERIFIED":
            raise ValueError("probe snapshot requires a verified provider instrument")
        with self.connection:
            cur = self.connection.execute("""INSERT OR IGNORE INTO probe_observations
              (probe_id,provider,tool_name,instrument_id,provider_time,receipt_lower_bound,
               receipt_upper_bound,delay_status,market_session,payload_hash,payload_json,
               source_hash,recorded_at,status)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (probe_id,provider,tool_name,instrument_id,provider_time,receipt_lower_bound,
               receipt_upper_bound,delay_status,market_session,digest(payload),canonical(payload),
               source_hash,utc_now(),"PROBE_TIME_INTERVAL_ONLY"))
            return cur.rowcount == 1

    def insert_quote(self, *, provider: str, instrument_id: str, kind: str,
                     provider_time: str, received_at: str, source_timezone: str,
                     market_session: str, delay_status: str,
                     payload: dict[str, Any]) -> tuple[int, bool]:
        require_utc(provider_time, "provider_time")
        require_utc(received_at, "received_at")
        if market_session not in MARKET_SESSIONS or delay_status not in DELAY_STATUSES:
            raise ValueError("market_session or delay_status is not in the frozen vocabulary")
        if kind == "BAR_1M":
            required = {"start_at", "end_at", "open", "close", "session_id", "price_basis"}
            if not required.issubset(payload):
                raise ValueError(f"BAR_1M missing fields: {sorted(required - payload.keys())}")
            require_utc(payload["start_at"], "bar.start_at")
            require_utc(payload["end_at"], "bar.end_at")
            if payload["start_at"] >= payload["end_at"]:
                raise ValueError("bar start_at must precede end_at")
        instrument = self.connection.execute("SELECT identity_status FROM instruments WHERE instrument_id=?",
                                             (instrument_id,)).fetchone()
        if not instrument or instrument["identity_status"] != "VERIFIED":
            raise ValueError("quote instrument requires verified official security identity")
        payload_json = canonical(payload)
        input_hash = digest({"payload": payload, "source_timezone": source_timezone,
                             "market_session": market_session, "delay_status": delay_status})
        persisted_at = utc_now()
        with self.connection:
            row = self.connection.execute("""SELECT id FROM quotes_or_bars
              WHERE provider=? AND instrument_id=? AND kind=? AND provider_time=? AND input_hash=?""",
              (provider,instrument_id,kind,provider_time,input_hash)).fetchone()
            if row:
                return int(row["id"]), False
            prev = self.connection.execute("""SELECT id,version FROM quotes_or_bars
              WHERE provider=? AND instrument_id=? AND kind=? AND provider_time=?
              ORDER BY version DESC LIMIT 1""", (provider,instrument_id,kind,provider_time)).fetchone()
            cursor = self.connection.execute("""INSERT INTO quotes_or_bars
              (provider,instrument_id,kind,provider_time,received_at,persisted_at,
               source_timezone,market_session,delay_status,input_hash,schema_version,
               version,revision_of,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (provider,instrument_id,kind,provider_time,received_at,persisted_at,
               source_timezone,market_session,delay_status,input_hash,SCHEMA_VERSION,
               int(prev["version"])+1 if prev else 1,int(prev["id"]) if prev else None,payload_json))
            return int(cursor.lastrowid), True

    def insert_news(self, *, provider: str, identity_key: str, key_kind: str,
                    potential_collision: bool, received_at: str, content: str,
                    payload: dict[str, Any], provider_time: str | None = None,
                    source_timezone: str = "UNKNOWN", time_precision: str = "UNKNOWN",
                    provider_id: str | None = None, source_url: str | None = None,
                    original_publisher: str | None = None, status: str = "ACTIVE",
                    raw_locator: str | None = None) -> tuple[int, bool]:
        require_utc(received_at, "received_at")
        if provider_time:
            require_utc(provider_time, "provider_time")
        if key_kind not in {"PROVIDER_ID", "SOURCE_URL", "COMPOSITE"}:
            raise ValueError("news key_kind must describe the inspected identity basis")
        if key_kind == "SOURCE_URL" and not source_url:
            raise ValueError("SOURCE_URL identity requires a source_url")
        if key_kind == "PROVIDER_ID" and not provider_id:
            raise ValueError("PROVIDER_ID identity requires a provider_id")
        if key_kind == "COMPOSITE" and not potential_collision:
            raise ValueError("composite key must disclose potential collision")
        if status not in {"ACTIVE", "REVISED", "WITHDRAWN", "UNKNOWN"}:
            raise ValueError("unsupported news status")
        input_hash = digest({"content": content, "payload": payload, "status": status})
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        with self.connection:
            prior = self.connection.execute("""SELECT id,version,first_received_at FROM news_items
                WHERE provider=? AND identity_key=? ORDER BY version DESC LIMIT 1""",
                (provider,identity_key)).fetchone()
            same = self.connection.execute("""SELECT id FROM news_items
                WHERE provider=? AND identity_key=? AND input_hash=?""",
                (provider,identity_key,input_hash)).fetchone()
            if same:
                news_id = int(same["id"])
                created = False
            else:
                cur = self.connection.execute("""INSERT INTO news_items
                  (provider,identity_key,key_kind,potential_collision,provider_id,source_url,
                   original_publisher,provider_time,source_timezone,time_precision,
                   first_received_at,received_at,persisted_at,content_hash,input_hash,
                   version,revision_of,status,raw_locator,payload_json,schema_version)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (provider,identity_key,key_kind,int(potential_collision),provider_id,source_url,
                   original_publisher,provider_time,source_timezone,time_precision,
                   prior["first_received_at"] if prior else received_at,received_at,utc_now(),
                   content_hash,input_hash,int(prior["version"])+1 if prior else 1,
                   int(prior["id"]) if prior else None,status,raw_locator,canonical(payload),SCHEMA_VERSION))
                news_id = int(cur.lastrowid)
                created = True
            self.connection.execute("""INSERT INTO news_fetches(news_id,fetched_at,raw_locator)
              SELECT ?,?,? WHERE NOT EXISTS (
                SELECT 1 FROM news_fetches WHERE news_id=? AND fetched_at=?
                AND (raw_locator=? OR (raw_locator IS NULL AND ? IS NULL)))""",
                (news_id,received_at,raw_locator,news_id,received_at,raw_locator,raw_locator))
            return news_id, created

    def record_news_identity_audit(self, news_id: int, *, source_key_kind: str,
                                   inspected_key_kind: str, potential_collision: bool,
                                   reason: str) -> None:
        with self.connection:
            self.connection.execute("""INSERT OR IGNORE INTO news_identity_audits
              (news_id,source_key_kind,inspected_key_kind,potential_collision,reason,recorded_at)
              VALUES(?,?,?,?,?,?)""",
              (news_id,source_key_kind,inspected_key_kind,int(potential_collision),reason,utc_now()))

    def create_session(self, session_id: str, *, mode: str, pid: int | None,
                       config_hash: str, status: str = "STARTING") -> None:
        now = utc_now()
        with self.connection:
            self.connection.execute("""INSERT INTO sessions
              (session_id,mode,started_at,heartbeat_at,status,pid,config_hash,schema_version)
              VALUES(?,?,?,?,?,?,?,?)""", (session_id,mode,now,now,status,pid,config_hash,SCHEMA_VERSION))

    def heartbeat(self, session_id: str, status: str) -> bool:
        with self.connection:
            self.connection.execute("UPDATE sessions SET heartbeat_at=?,status=? WHERE session_id=?",
                                    (utc_now(),status,session_id))
            row = self.connection.execute("SELECT stop_requested FROM sessions WHERE session_id=?",
                                          (session_id,)).fetchone()
        return bool(row and row["stop_requested"])

    def request_stop(self, session_id: str) -> bool:
        with self.connection:
            cur = self.connection.execute("""UPDATE sessions SET stop_requested=1
              WHERE session_id=? AND ended_at IS NULL""", (session_id,))
        return cur.rowcount > 0

    def end_session(self, session_id: str, status: str, error: str | None = None) -> None:
        with self.connection:
            self.connection.execute("""UPDATE sessions SET ended_at=?,heartbeat_at=?,status=?,error=?
              WHERE session_id=?""", (utc_now(),utc_now(),status,error,session_id))

    def latest_session(self) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM sessions ORDER BY started_at DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def record_gap(self, source: str, stream_key: str, reason: str,
                   *, started_at: str | None = None, ended_at: str | None = None,
                   coverage_status: str = "INCOMPLETE",
                   detail: dict[str, Any] | None = None) -> int:
        started_at = require_utc(started_at or utc_now(), "started_at")
        if ended_at:
            require_utc(ended_at, "ended_at")
        with self.connection:
            cur = self.connection.execute("""INSERT INTO collection_gaps
              (source,stream_key,started_at,ended_at,reason,coverage_status,detail_json,recorded_at)
              VALUES(?,?,?,?,?,?,?,?)""",
              (source,stream_key,started_at,ended_at,reason,coverage_status,canonical(detail or {}),utc_now()))
            return int(cur.lastrowid)

    def record_audit_note(self, category: str, record_ids: list[int], description: str) -> int:
        with self.connection:
            cur = self.connection.execute("""INSERT INTO observer_audit_notes
              (category,record_ids_json,description,created_at) VALUES(?,?,?,?)""",
              (category,canonical(record_ids),description,utc_now()))
            return int(cur.lastrowid)

    def checkpoint(self, source: str, stream_key: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM checkpoints WHERE source=? AND stream_key=?",
                                      (source,stream_key)).fetchone()
        return dict(row) if row else None

    def set_checkpoint(self, source: str, stream_key: str, *, complete_cursor: Any,
                       latest_seen_at: str | None = None,
                       coverage_status: str = "COMPLETE") -> None:
        if coverage_status != "COMPLETE":
            raise ValueError("only a fully verified page/window may advance the complete checkpoint")
        if latest_seen_at:
            require_utc(latest_seen_at, "latest_seen_at")
        with self.connection:
            self.connection.execute("""INSERT INTO checkpoints
              (source,stream_key,complete_cursor_json,latest_seen_at,coverage_status,updated_at)
              VALUES(?,?,?,?,?,?) ON CONFLICT(source,stream_key) DO UPDATE SET
               complete_cursor_json=excluded.complete_cursor_json,
               latest_seen_at=excluded.latest_seen_at,
               coverage_status=excluded.coverage_status,updated_at=excluded.updated_at""",
              (source,stream_key,canonical(complete_cursor),latest_seen_at,coverage_status,utc_now()))

    def insert_prediction(self, record: dict[str, Any]) -> str:
        required = {"prediction_id", "event_id", "instrument_id", "created_at",
                    "information_cutoff", "input_snapshot_hash", "input_manifest_json", "rule_version",
                    "evaluation_protocol_hash", "price_basis", "calendar_version",
                    "horizon_minutes", "direction", "rationale", "transmission_json",
                    "counter_scenario", "data_quality_status"}
        missing = required - record.keys()
        if missing:
            raise ValueError(f"prediction missing {sorted(missing)}")
        require_utc(record["created_at"], "created_at")
        require_utc(record["information_cutoff"], "information_cutoff")
        persisted_at = utc_now()
        parse = lambda value: datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parse(record["created_at"]) > parse(persisted_at) or parse(record["information_cutoff"]) > parse(record["created_at"]):
            raise ValueError("prediction time order is invalid")
        manifest = record["input_manifest_json"]
        if isinstance(manifest, str):
            manifest = json.loads(manifest)
        if not isinstance(manifest, dict) or not isinstance(manifest.get("records"), list):
            raise ValueError("input manifest must contain an ordered records list")
        for field in ("event_id", "instrument_id", "rule_version"):
            if manifest.get(field) != record[field]:
                raise ValueError(f"input manifest {field} differs from prediction")
        manifest_cutoff = manifest.get("information_cutoff")
        require_utc(manifest_cutoff, "manifest.information_cutoff")
        parse = lambda value: datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parse(manifest_cutoff) != parse(record["information_cutoff"]):
            raise ValueError("input manifest cutoff differs from prediction")
        identities: set[tuple[str, str]] = set()
        for item in manifest["records"]:
            if not isinstance(item, dict):
                raise ValueError("input manifest record must be an object")
            for field in ("received_at", "persisted_at"):
                require_utc(item.get(field), f"input manifest {field}")
            if parse(item["received_at"]) > parse(item["persisted_at"]) or parse(item["persisted_at"]) > parse(manifest_cutoff):
                raise ValueError("input manifest contains an input unavailable at cutoff")
            identity = (str(item.get("record_kind")), str(item.get("record_id")))
            if identity in identities:
                raise ValueError("input manifest contains a duplicate record")
            identities.add(identity)
        if digest(manifest) != record["input_snapshot_hash"]:
            raise ValueError("input snapshot hash does not match frozen manifest")
        fields = ("prediction_id","event_id","instrument_id","created_at","persisted_at",
                  "information_cutoff","input_snapshot_hash","input_manifest_json",
                  "model_version","prompt_version",
                  "rule_version","evaluation_protocol_hash","price_basis","calendar_version",
                  "horizon_minutes","direction","rationale","transmission_json",
                  "counter_scenario","data_quality_status","parent_prediction_id","schema_version")
        values = {**record, "persisted_at": persisted_at, "schema_version": SCHEMA_VERSION,
                  "input_manifest_json": canonical(manifest)}
        if not isinstance(values["transmission_json"], str):
            values["transmission_json"] = canonical(values["transmission_json"])
        with self.connection:
            self.connection.execute(f"INSERT INTO predictions ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",
                                    tuple(values.get(field) for field in fields))
        return str(record["prediction_id"])

    def get_prediction(self, prediction_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM predictions WHERE prediction_id=?",
                                      (prediction_id,)).fetchone()
        return dict(row) if row else None

    def insert_outcome(self, record: dict[str, Any]) -> str:
        required = {"outcome_id", "prediction_id", "status", "input_hash"}
        missing = required - record.keys()
        if missing:
            raise ValueError(f"outcome missing {sorted(missing)}")
        fields = ("outcome_id","prediction_id","created_at","status","start_bar_id","end_bar_id",
                  "return_value","qqq_return","excess_return","direction_hit",
                  "baseline_direction","baseline_hit","reason","input_hash",
                  "input_manifest_json","schema_version")
        values = {**record, "created_at": record.get("created_at") or utc_now(),
                  "schema_version": SCHEMA_VERSION}
        require_utc(values["created_at"], "created_at")
        if values["status"] not in {"MATURED", "PENDING", "NO_REGULAR_WINDOW",
                                    "CROSSES_CLOSE", "DATA_GAP", "PRICE_BASIS_MISMATCH"}:
            raise ValueError("outcome status is outside the frozen vocabulary")
        if values["status"] == "MATURED" and not values.get("input_manifest_json"):
            raise ValueError("mature outcome requires an auditable bar input manifest")
        if values.get("input_manifest_json") is not None and not isinstance(values["input_manifest_json"], str):
            values["input_manifest_json"] = canonical(values["input_manifest_json"])
        if values["status"] == "MATURED" and digest(json.loads(values["input_manifest_json"])) != values["input_hash"]:
            raise ValueError("outcome input hash does not match bar manifest")
        with self.connection:
            self.connection.execute(f"INSERT INTO outcomes ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",
                                    tuple(values.get(field) for field in fields))
        return str(record["outcome_id"])

    def insert_source_claim(self, record: dict[str, Any]) -> str:
        required = {"claim_id", "claim_group_id", "claim_text", "status", "relation", "evidence_hash"}
        missing = required - record.keys()
        if missing:
            raise ValueError(f"source claim missing {sorted(missing)}")
        if record["status"] not in CLAIM_STATUSES:
            raise ValueError("claim status is outside the frozen vocabulary")
        if record["relation"] not in {"SUPPORTS", "CONTRADICTS", "UNREVIEWED"}:
            raise ValueError("claim relation is outside the frozen vocabulary")
        fields = ("claim_id","claim_group_id","origin_id","relation",
                  "primary_document_verified","event_id","news_id","claim_text","status",
                  "source_url","source_type","platform","provider_time","source_timezone","time_precision",
                  "received_at","persisted_at",
                  "evidence_hash",
                  "schema_version")
        values = {**record, "received_at": record.get("received_at") or utc_now(),
                  "persisted_at": utc_now(),
                  "schema_version": SCHEMA_VERSION,
                  "source_timezone": record.get("source_timezone") or "UNKNOWN",
                  "time_precision": record.get("time_precision") or "UNKNOWN",
                  "primary_document_verified": int(bool(record.get("primary_document_verified", False)))}
        require_utc(values["received_at"], "received_at")
        parse = lambda value: datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parse(values["received_at"]) > parse(values["persisted_at"]):
            raise ValueError("claim local receipt cannot follow durable persistence")
        if values.get("provider_time"):
            require_utc(values["provider_time"], "provider_time")
        if values["time_precision"] not in {"UNKNOWN", "DATE", "MINUTE", "SECOND", "MILLISECOND"}:
            raise ValueError("source claim time precision is outside the frozen vocabulary")
        with self.connection:
            self.connection.execute(f"INSERT INTO source_claims ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",
                                    tuple(values.get(field) for field in fields))
        return str(record["claim_id"])

    def register_model_version(self, *, version_id: str, kind: str, source_hash: str,
                               config: dict[str, Any], status: str = "CANDIDATE",
                               rollback_to: str | None = None) -> str:
        if status not in {"CANDIDATE", "OFFLINE_EVALUATED", "REJECTED", "APPROVED"}:
            raise ValueError("model version status is outside the frozen vocabulary")
        with self.connection:
            self.connection.execute("""INSERT INTO model_versions
              (version_id,kind,created_at,source_hash,config_json,status,rollback_to)
              VALUES(?,?,?,?,?,?,?)""", (version_id,kind,utc_now(),source_hash,
                                           canonical(config),status,rollback_to))
        return version_id

    def get_model_version(self, version_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM model_versions WHERE version_id=?",
                                      (version_id,)).fetchone()
        return dict(row) if row else None

    def enqueue_review(self, record: dict[str, Any]) -> str:
        required = {"review_id", "category", "evidence_json"}
        missing = required - record.keys()
        if missing:
            raise ValueError(f"review item missing {sorted(missing)}")
        fields = ("review_id","event_id","prediction_id","category","status","created_at",
                  "evidence_json","model_prediction_json","objective_outcome_json","human_ruling_json")
        values = {**record, "status": record.get("status", "PENDING"),
                  "created_at": record.get("created_at", utc_now())}
        require_utc(values["created_at"], "created_at")
        for field in ("evidence_json","model_prediction_json","objective_outcome_json","human_ruling_json"):
            if values.get(field) is not None and not isinstance(values[field], str):
                values[field] = canonical(values[field])
        with self.connection:
            self.connection.execute(f"INSERT INTO review_queue ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",
                                    tuple(values.get(field) for field in fields))
        return str(record["review_id"])

    def dashboard_snapshot(self, *, include_private_text: bool = False, limit: int = 25) -> dict[str, Any]:
        """Read local state only. Caller must write snapshots under ignored artifacts.

        Provider news content is omitted by default; no full raw payload is
        exported. A quote's timestamp and market state remain separate so the
        renderer cannot silently relabel an old quote as a live update.
        """
        if not 1 <= limit <= 100:
            raise ValueError("dashboard limit must be 1..100")

        def rows(sql: str) -> list[dict[str, Any]]:
            return [dict(row) for row in self.connection.execute(sql, (limit,))]

        session = self.latest_session()
        live_connected = False
        if session and session["mode"] == "LIVE_PUSH" and session["ended_at"] is None and session["status"].startswith("CONNECTED_"):
            try:
                heartbeat = datetime.fromisoformat(session["heartbeat_at"].replace("Z", "+00:00"))
                live_connected = 0 <= (datetime.now(timezone.utc) - heartbeat).total_seconds() <= 30
            except (TypeError, ValueError):
                pass
        service = {
            "state": session["status"] if session else "NOT_STARTED",
            "mode": session["mode"] if session else None,
            "heartbeat_at": session["heartbeat_at"] if session else None,
            "provider_state": "CONNECTED" if live_connected else "UNVERIFIED_OR_OFFLINE",
            "provider_connected": live_connected,
            "last_error": session["error"] if session else None,
        }
        instruments = rows("""SELECT c.instrument_id,c.requested_symbol AS symbol,
            v.provider_code,v.market,
            CASE WHEN r.verified_id IS NOT NULL THEN 'VERIFIED_BY_OFFICIAL_BASICINFO'
                 ELSE c.identity_status END AS resolution_status
            FROM instruments c
            LEFT JOIN instrument_resolutions r ON r.candidate_id=c.instrument_id
              AND r.rowid=(SELECT MAX(r2.rowid) FROM instrument_resolutions r2
                           WHERE r2.candidate_id=c.instrument_id)
            LEFT JOIN instruments v ON v.instrument_id=r.verified_id
            WHERE c.instrument_id LIKE 'CANDIDATE:%'
            ORDER BY c.requested_symbol LIMIT ?""")
        quotes = rows("""SELECT id,instrument_id,kind,provider_time,received_at,
            market_session,delay_status,payload_json FROM quotes_or_bars ORDER BY id DESC LIMIT ?""")
        for quote in quotes:
            payload = json.loads(quote.pop("payload_json"))
            quote["close"] = payload.get("close") if quote["kind"] == "BAR_1M" else payload.get("price")
        probes = rows("""SELECT probe_id AS id,instrument_id,'PROBE_SNAPSHOT' AS kind,
            provider_time,receipt_upper_bound AS received_at,market_session,
            delay_status,payload_json FROM probe_observations ORDER BY recorded_at DESC LIMIT ?""")
        for probe in probes:
            payload = json.loads(probe.pop("payload_json"))
            probe["close"] = payload.get("last_price")
            probe["receipt_semantics"] = "BOUNDED_INTERVAL_NOT_EXACT"
        quotes.extend(probes)
        news = rows("""SELECT id,provider,source_url,original_publisher,provider_time,
            source_timezone,time_precision,first_received_at,version,status,payload_json
            FROM news_items ORDER BY id DESC LIMIT ?""")
        for item in news:
            payload = json.loads(item.pop("payload_json"))
            display_payload = payload.get("normalized", payload)
            item["title"] = (display_payload.get("title") or
                             str(display_payload.get("content") or "")[:140]) if include_private_text else None
            item["source"] = item["provider"]
            item["source_published_at"] = item["provider_time"]
            item["verification_status"] = "UNVERIFIED"
        claims = rows("""SELECT claim_id,status,source_url AS source_locator
            FROM source_claims ORDER BY received_at DESC LIMIT ?""")
        predictions = rows("""SELECT prediction_id,event_id,instrument_id,persisted_at AS created_at,
            direction,data_quality_status FROM predictions ORDER BY persisted_at DESC LIMIT ?""")
        outcomes = rows("""SELECT prediction_id,status,return_value AS actual_return,
            excess_return AS qqq_return_difference,created_at AS evaluated_at
            FROM outcomes ORDER BY created_at DESC LIMIT ?""")
        review = rows("""SELECT category AS reason,status,created_at,human_ruling_json
            FROM review_queue ORDER BY created_at DESC LIMIT ?""")
        for item in review:
            item["human_verdict"] = "PENDING" if item.pop("human_ruling_json") is None else "RECORDED"
        gaps = rows("""SELECT source AS provider,reason,started_at,ended_at,
            NULL AS backfilled_at FROM collection_gaps ORDER BY gap_id DESC LIMIT ?""")
        return {"service": service, "instruments": instruments, "quotes": quotes,
                "news": news, "source_claims": claims, "predictions": predictions,
                "outcomes": outcomes, "review_queue": review,
                "collection_gaps": gaps, "generated_at_utc": utc_now()}
