"""Private sidecar SQLite cache, bound to exactly one portal owner ID.

The public Django market tables and the owner's account database are not used
as provider caches. Provider text and quote values never enter public receipts.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def published_utc(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_source_url(value: object) -> str | None:
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        return None
    try:
        parts = urlsplit(value)
        if parts.scheme == "https" and parts.hostname and not parts.username and not parts.password:
            return value
    except ValueError:
        pass
    return None


class PersonalFeedStore:
    def __init__(self, path: Path, owner_id: int):
        if owner_id <= 0:
            raise ValueError("A real portal owner ID is required")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=10000")
        timeline_config = Path(__file__).resolve().parents[2] / "configs/quant_portal_personal_timeline_v14_20261116.json"
        retention = json.loads(timeline_config.read_text(encoding="utf-8")) if timeline_config.is_file() else {}
        self.raw_response_recent_hours = int(retention.get("raw_response_recent_hours", 48))
        self.raw_response_extra_count = int(retention.get("raw_response_extra_count", 100))
        if not 1 <= self.raw_response_recent_hours <= 168 or not 0 <= self.raw_response_extra_count <= 1000:
            raise ValueError("Invalid personal raw-response retention limits")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS owner_binding (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), owner_id INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS provider_state (
            provider TEXT PRIMARY KEY, state TEXT NOT NULL, heartbeat_at TEXT NOT NULL,
            last_success_at TEXT, last_received_at TEXT, last_source_at TEXT,
            last_error_class TEXT, requests_total INTEGER NOT NULL DEFAULT 0,
            business_calls_today INTEGER NOT NULL DEFAULT 0, business_day_utc TEXT);
          CREATE TABLE IF NOT EXISTS provider_state_events (
            id INTEGER PRIMARY KEY, provider TEXT NOT NULL, state TEXT NOT NULL, at_utc TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS provider_state_events_time ON provider_state_events(provider,at_utc);
          CREATE TABLE IF NOT EXISTS quotes (
            id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL, provider TEXT NOT NULL,
            symbol TEXT NOT NULL, provider_code TEXT NOT NULL, display_name TEXT,
            price_text TEXT NOT NULL, currency TEXT, provider_time TEXT NOT NULL,
            received_at TEXT NOT NULL, market_state TEXT NOT NULL,
            delay_status TEXT NOT NULL, content_sha256 TEXT NOT NULL,
            UNIQUE(owner_id,provider,symbol,provider_time,content_sha256));
          CREATE INDEX IF NOT EXISTS quotes_latest ON quotes(owner_id,symbol,received_at DESC,id DESC);
          CREATE INDEX IF NOT EXISTS quotes_source_latest ON quotes(owner_id,symbol,provider_time DESC,received_at DESC,id DESC);
          CREATE TABLE IF NOT EXISTS session_quotes (
            id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL, symbol TEXT NOT NULL,
            provider_code TEXT NOT NULL, session TEXT NOT NULL, price_text TEXT NOT NULL,
            currency TEXT, source_at TEXT, received_at TEXT NOT NULL,
            provider_field_path TEXT NOT NULL, source_time_field_path TEXT,
            trade_date TEXT, date_basis TEXT NOT NULL,
            parent_date_hint TEXT, parent_source_at_hint TEXT, delay_status TEXT NOT NULL,
            source_channel TEXT NOT NULL, raw_sha256 TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS session_quotes_latest ON session_quotes
            (owner_id,symbol,session,source_at DESC,received_at DESC,id DESC);
          CREATE INDEX IF NOT EXISTS session_quotes_source_range ON session_quotes
            (owner_id,symbol,source_at);
          CREATE TABLE IF NOT EXISTS session_capabilities (
            owner_id INTEGER NOT NULL, symbol TEXT NOT NULL, session TEXT NOT NULL,
            state TEXT NOT NULL, checked_at TEXT NOT NULL, source_channel TEXT NOT NULL,
            PRIMARY KEY(owner_id,symbol,session));
          CREATE TABLE IF NOT EXISTS session_raw_responses (
            sha256 TEXT PRIMARY KEY, received_at TEXT NOT NULL,
            source_channel TEXT NOT NULL, raw_json TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS symbol_state (
            symbol TEXT PRIMARY KEY, owner_id INTEGER NOT NULL, state TEXT NOT NULL,
            reason TEXT, updated_at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS news_items (
            item_key TEXT PRIMARY KEY, owner_id INTEGER NOT NULL,
            source_key_kind TEXT NOT NULL, first_received_at TEXT NOT NULL,
            latest_received_at TEXT NOT NULL, latest_version INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS news_versions (
            id INTEGER PRIMARY KEY, item_key TEXT NOT NULL REFERENCES news_items(item_key),
            owner_id INTEGER NOT NULL, version INTEGER NOT NULL, provider_id TEXT,
            title TEXT, content TEXT NOT NULL, source_url TEXT,
            published_raw TEXT, published_at_utc TEXT, received_at TEXT NOT NULL,
            content_sha256 TEXT NOT NULL, raw_item_json TEXT NOT NULL,
            UNIQUE(item_key,content_sha256));
          CREATE INDEX IF NOT EXISTS news_latest ON news_versions(owner_id,received_at DESC,id DESC);
          CREATE TABLE IF NOT EXISTS quote_selection (
            symbol TEXT PRIMARY KEY, owner_id INTEGER NOT NULL, desired INTEGER NOT NULL,
            position INTEGER NOT NULL, updated_at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS selection_meta (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), generation INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS news_interest (
            interest_key TEXT PRIMARY KEY, owner_id INTEGER NOT NULL,
            enabled INTEGER NOT NULL, updated_at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS event_preferences (
            owner_id INTEGER PRIMARY KEY, entities_json TEXT NOT NULL,
            categories_json TEXT NOT NULL, updated_at TEXT NOT NULL);
        """)
        row = self.db.execute("SELECT owner_id FROM owner_binding WHERE singleton=1").fetchone()
        if row is None:
            self.db.execute("INSERT INTO owner_binding(singleton,owner_id) VALUES(1,?)", (owner_id,))
        elif row["owner_id"] != owner_id:
            self.db.close()
            raise ValueError("Personal feed cache is bound to a different owner; refuse access")
        self.owner_id = owner_id
        additions = {
            "last_push_at": "TEXT", "last_quote_saved_at": "TEXT",
            "duplicate_quote_packets": "INTEGER NOT NULL DEFAULT 0",
            "business_attempts_today": "INTEGER NOT NULL DEFAULT 0",
            "business_success_total": "INTEGER NOT NULL DEFAULT 0",
            "business_failure_total": "INTEGER NOT NULL DEFAULT 0",
            "handshake_attempts_total": "INTEGER NOT NULL DEFAULT 0",
            "handshake_success_total": "INTEGER NOT NULL DEFAULT 0",
            "worker_alive": "INTEGER NOT NULL DEFAULT 0",
            "coverage_gap": "TEXT",
            "last_page_fingerprints": "TEXT",
        }
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(provider_state)")}
        for name, definition in additions.items():
            if name not in columns:
                self.db.execute(f"ALTER TABLE provider_state ADD COLUMN {name} {definition}")
        session_columns = {row[1] for row in self.db.execute("PRAGMA table_info(session_quotes)")}
        if "parent_source_at_hint" not in session_columns:
            self.db.execute("ALTER TABLE session_quotes ADD COLUMN parent_source_at_hint TEXT")
        if "source_time_field_path" not in session_columns:
            self.db.execute("ALTER TABLE session_quotes ADD COLUMN source_time_field_path TEXT")
        # Legacy counter is only a lower bound: older failures were not counted.
        self.db.execute("""UPDATE provider_state SET business_attempts_today=business_calls_today
          WHERE business_day_utc=? AND business_attempts_today<business_calls_today""", (utc_now()[:10],))

    def close(self) -> None:
        self.db.close()

    def state(self, provider: str, state: str, *, success: bool = False,
              source_at: str | None = None, received_at: str | None = None,
              error_class: str | None = None, request_increment: int = 0,
              business_increment: int = 0) -> None:
        now = utc_now()
        day = now[:10]
        prior = self.db.execute("SELECT business_day_utc,state FROM provider_state WHERE provider=?", (provider,)).fetchone()
        self.db.execute("""
          INSERT INTO provider_state(provider,state,heartbeat_at,last_success_at,
            last_received_at,last_source_at,last_error_class,requests_total,
            business_calls_today,business_day_utc)
          VALUES(?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(provider) DO UPDATE SET
            state=excluded.state, heartbeat_at=excluded.heartbeat_at,
            last_success_at=COALESCE(excluded.last_success_at,provider_state.last_success_at),
            last_received_at=COALESCE(excluded.last_received_at,provider_state.last_received_at),
            last_source_at=CASE WHEN excluded.last_source_at IS NULL THEN provider_state.last_source_at
              WHEN provider_state.last_source_at IS NULL OR excluded.last_source_at>provider_state.last_source_at
              THEN excluded.last_source_at ELSE provider_state.last_source_at END,
            last_error_class=excluded.last_error_class,
            requests_total=provider_state.requests_total+excluded.requests_total,
            business_calls_today=CASE WHEN provider_state.business_day_utc=excluded.business_day_utc
              THEN provider_state.business_calls_today+excluded.business_calls_today
              ELSE excluded.business_calls_today END,
            business_day_utc=excluded.business_day_utc
        """, (provider,state,now,now if success else None,received_at,source_at,
              error_class,request_increment,business_increment,day))
        if prior is None or prior["state"] != state:
            self.db.execute("INSERT INTO provider_state_events(provider,state,at_utc) VALUES(?,?,?)",
                            (provider,state,now))
            self.db.execute("DELETE FROM provider_state_events WHERE at_utc<?",
                            ((datetime.now(timezone.utc)-timedelta(days=7)).isoformat().replace("+00:00","Z"),))
        if prior and prior[0] != day:
            self.db.execute("UPDATE provider_state SET business_attempts_today=0 WHERE provider=?", (provider,))

    def daily_business_calls(self, provider: str) -> int:
        row = self.db.execute("SELECT business_calls_today,business_day_utc FROM provider_state WHERE provider=?",
                              (provider,)).fetchone()
        return row["business_calls_today"] if row and row["business_day_utc"] == utc_now()[:10] else 0

    def daily_business_attempts(self, provider: str) -> int:
        row = self.db.execute("SELECT business_attempts_today,business_day_utc FROM provider_state WHERE provider=?",
                              (provider,)).fetchone()
        return row["business_attempts_today"] if row and row["business_day_utc"] == utc_now()[:10] else 0

    def provider_metric(self, provider: str, **changes: object) -> None:
        allowed = {"last_push_at", "last_quote_saved_at", "duplicate_quote_packets",
                   "business_attempts_today", "business_success_total", "business_failure_total",
                   "handshake_attempts_total", "handshake_success_total", "worker_alive", "coverage_gap",
                   "last_page_fingerprints"}
        if not changes or set(changes) - allowed:
            raise ValueError("Unknown provider metric")
        row = self.db.execute("SELECT state FROM provider_state WHERE provider=?", (provider,)).fetchone()
        if row is None:
            self.state(provider, "CONNECTING")
        increments = {"duplicate_quote_packets", "business_attempts_today", "business_success_total",
                      "business_failure_total", "handshake_attempts_total", "handshake_success_total"}
        assignments = [f"{key}={key}+?" if key in increments else f"{key}=?" for key in changes]
        self.db.execute(f"UPDATE provider_state SET {', '.join(assignments)} WHERE provider=?",
                        (*changes.values(), provider))

    def ensure_selection(self, symbols: list[str]) -> None:
        self.db.execute("INSERT OR IGNORE INTO selection_meta(singleton,generation) VALUES(1,0)")
        if self.db.execute("SELECT COUNT(*) FROM quote_selection WHERE owner_id=?", (self.owner_id,)).fetchone()[0] == 0:
            for position, symbol in enumerate(symbols):
                self.db.execute("INSERT INTO quote_selection VALUES(?,?,?,?,?)",
                                (symbol,self.owner_id,1,position,utc_now()))
        for key in ("SNDK", "SKHYNIX", "SPACEX"):
            self.db.execute("INSERT OR IGNORE INTO news_interest VALUES(?,?,1,?)",
                            (key,self.owner_id,utc_now()))

    def selection(self) -> tuple[int, list[str]]:
        row = self.db.execute("SELECT generation FROM selection_meta WHERE singleton=1").fetchone()
        generation = row[0] if row else 0
        rows = self.db.execute("SELECT symbol FROM quote_selection WHERE owner_id=? AND desired=1 ORDER BY position,symbol",
                               (self.owner_id,)).fetchall()
        return generation, [r[0] for r in rows]

    def change_selection(self, symbol: str, action: str, verified_symbols: set[str],
                         budget: int, replace_symbol: str | None = None) -> str:
        if action not in {"subscribe", "unsubscribe"}:
            return "INVALID_ACTION"
        if action == "subscribe" and symbol not in verified_symbols:
            return "UNVERIFIED_CODE"
        self.db.execute("BEGIN IMMEDIATE")
        try:
            _, desired = self.selection()
            if action == "unsubscribe":
                if symbol not in desired:
                    result = "ALREADY_STOPPED"
                else:
                    self.db.execute("UPDATE quote_selection SET desired=0,updated_at=? WHERE symbol=? AND owner_id=?",
                                    (utc_now(),symbol,self.owner_id))
                    result = "PENDING_STOP"
            elif symbol in desired:
                result = "ALREADY_SELECTED"
            elif len(desired) >= budget and (not replace_symbol or replace_symbol not in desired or replace_symbol == symbol):
                result = "BUDGET_FULL"
            else:
                if replace_symbol:
                    self.db.execute("UPDATE quote_selection SET desired=0,updated_at=? WHERE symbol=? AND owner_id=?",
                                    (utc_now(),replace_symbol,self.owner_id))
                position = self.db.execute("SELECT COALESCE(MAX(position),-1)+1 FROM quote_selection").fetchone()[0]
                self.db.execute("""INSERT INTO quote_selection(symbol,owner_id,desired,position,updated_at)
                  VALUES(?,?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET desired=1,position=excluded.position,
                  updated_at=excluded.updated_at""", (symbol,self.owner_id,1,position,utc_now()))
                result = "PENDING_SUBSCRIBE"
            if result in {"PENDING_STOP", "PENDING_SUBSCRIBE"}:
                self.db.execute("UPDATE selection_meta SET generation=generation+1 WHERE singleton=1")
            self.db.execute("COMMIT")
            return result
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def set_news_interest(self, key: str, enabled: bool) -> None:
        if key not in {"SNDK", "SKHYNIX", "SPACEX"}:
            raise ValueError("Unknown news interest")
        self.db.execute("""INSERT INTO news_interest VALUES(?,?,?,?) ON CONFLICT(interest_key)
          DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at""",
          (key,self.owner_id,int(enabled),utc_now()))

    def set_event_preferences(self, entities: list[str], categories: list[str]) -> None:
        from .event_rules import ENTITY_RULES, CATEGORY_RULES
        if len(entities) != len(set(entities)) or len(categories) != len(set(categories)):
            raise ValueError("Duplicate event preference")
        if not set(entities) <= set(ENTITY_RULES) or not set(categories) <= set(CATEGORY_RULES):
            raise ValueError("Unknown event preference")
        self.db.execute("""INSERT INTO event_preferences(owner_id,entities_json,categories_json,updated_at)
          VALUES(?,?,?,?) ON CONFLICT(owner_id) DO UPDATE SET
          entities_json=excluded.entities_json,categories_json=excluded.categories_json,
          updated_at=excluded.updated_at""",
          (self.owner_id,json.dumps(entities),json.dumps(categories),utc_now()))

    def symbol_state(self, owner_id: int, symbol: str, state: str, reason: str | None = None) -> None:
        if owner_id != self.owner_id:
            raise ValueError("Owner ID mismatch")
        self.db.execute("""INSERT INTO symbol_state(symbol,owner_id,state,reason,updated_at)
          VALUES(?,?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET owner_id=excluded.owner_id,
          state=excluded.state,reason=excluded.reason,updated_at=excluded.updated_at""",
          (symbol,owner_id,state,reason,utc_now()))

    def put_quote(self, owner_id: int, quote: dict, *, symbol: str, display_name: str,
                  market_state: str, limit: int) -> bool:
        if owner_id != self.owner_id:
            raise ValueError("Owner ID mismatch")
        price = quote.get("payload", {}).get("price")
        if isinstance(price, bool) or not isinstance(price, (int,float,str)) or str(price).strip() == "":
            return False
        # The personal card displays last price. Other push fields can change
        # while provider_time and last price stay fixed during a closed market;
        # those repeated packets must not fill the bounded quote history.
        digest = hashlib.sha256(str(price).encode("utf-8")).hexdigest()
        before = self.db.total_changes
        self.db.execute("""INSERT OR IGNORE INTO quotes
          (owner_id,provider,symbol,provider_code,display_name,price_text,currency,
           provider_time,received_at,market_state,delay_status,content_sha256)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
          (owner_id,"MOOMOO_WEBAPI_WS",symbol,quote["instrument_id"],display_name,
           str(price),None,quote["provider_time"],quote["received_at"],market_state,
           quote.get("delay_status") or "UNKNOWN",digest))
        inserted = self.db.total_changes > before
        self.db.execute("""DELETE FROM quotes WHERE id IN (
          SELECT id FROM quotes WHERE owner_id=? AND symbol=? ORDER BY provider_time DESC,received_at DESC,id DESC
          LIMIT -1 OFFSET ?)""", (owner_id,symbol,limit))
        return inserted

    def put_session_quotes(self, owner_id: int, rows: list[dict], *,
                           states: dict[str, dict[str, str]], checked_at: str,
                           raw_json: str | None = None, raw_sha256: str | None = None,
                           source_channel: str) -> dict[str, int]:
        """Append changed session values; never overwrite prior quote evidence."""
        from .session_quotes import SESSIONS
        if owner_id != self.owner_id:
            raise ValueError("Owner ID mismatch")
        inserted = duplicate = 0
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if raw_json is not None and raw_sha256 is not None:
                if hashlib.sha256(raw_json.encode("utf-8")).hexdigest() != raw_sha256:
                    raise ValueError("Raw response hash mismatch")
                self.db.execute("""INSERT OR IGNORE INTO session_raw_responses
                  (sha256,received_at,source_channel,raw_json) VALUES(?,?,?,?)""",
                  (raw_sha256,checked_at,source_channel,raw_json))
            for symbol, by_session in states.items():
                for session, state in by_session.items():
                    if session not in SESSIONS or state not in {"HAS_SOURCE_TIME", "SNAPSHOT_TIME_UNKNOWN",
                       "NO_OBSERVED_VALUE", "NO_OBSERVED_VALUE_OR_TIME", "DATE_CONFLICT", "NOT_YET_VERIFIED"}:
                        raise ValueError("Invalid session state")
                    self.db.execute("""INSERT INTO session_capabilities
                      (owner_id,symbol,session,state,checked_at,source_channel) VALUES(?,?,?,?,?,?)
                      ON CONFLICT(owner_id,symbol,session) DO UPDATE SET
                      state=excluded.state,checked_at=excluded.checked_at,
                      source_channel=excluded.source_channel""",
                      (owner_id,symbol,session,state,checked_at,source_channel))
            for row in rows:
                if row["session"] not in SESSIONS:
                    raise ValueError("Invalid session")
                latest = self.db.execute("""SELECT price_text,source_at,parent_date_hint,
                  source_time_field_path,date_basis,raw_sha256 FROM session_quotes
                  WHERE owner_id=? AND symbol=? AND session=? AND source_channel=?
                  ORDER BY id DESC LIMIT 1""",
                  (owner_id,row["symbol"],row["session"],source_channel)).fetchone()
                if (latest and latest["price_text"] == row["price_text"] and
                    latest["source_at"] == row["source_at"] and
                    latest["parent_date_hint"] == row["parent_date_hint"] and
                    latest["source_time_field_path"] == row.get("source_time_field_path") and
                    latest["date_basis"] == row["date_basis"] and
                    self.db.execute("SELECT 1 FROM session_raw_responses WHERE sha256=?",
                                    (latest["raw_sha256"],)).fetchone()):
                    duplicate += 1
                    continue
                self.db.execute("""INSERT INTO session_quotes
                  (owner_id,symbol,provider_code,session,price_text,currency,source_at,
                   received_at,provider_field_path,source_time_field_path,trade_date,date_basis,parent_date_hint,
                   parent_source_at_hint,delay_status,source_channel,raw_sha256)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (owner_id,row["symbol"],row["provider_code"],row["session"],row["price_text"],
                   row["currency"],row["source_at"],row["received_at"],
                   row["provider_field_path"],row.get("source_time_field_path"),
                   row["trade_date"],row["date_basis"],
                   row["parent_date_hint"],row.get("parent_source_at_hint"),row["delay_status"],source_channel,row["raw_sha256"]))
                inserted += 1
            # Keep current visible cells and every response supporting a quote
            # received in the configured recent chart window. The pre-v1.4
            # policy already pruned some older originals; it cannot recreate
            # those missing responses. Older unreferenced extras stay bounded.
            cutoff = (datetime.now(timezone.utc)-timedelta(hours=self.raw_response_recent_hours)
                      ).isoformat().replace("+00:00","Z")
            self.db.execute("""DELETE FROM session_raw_responses WHERE sha256 IN (
              SELECT sha256 FROM session_raw_responses WHERE sha256 NOT IN (
                SELECT q.raw_sha256 FROM session_quotes q WHERE q.received_at>=? OR q.id=(
                  SELECT q2.id FROM session_quotes q2 WHERE q2.owner_id=q.owner_id
                    AND q2.symbol=q.symbol AND q2.session=q.session
                  ORDER BY (q2.source_at IS NOT NULL) DESC,q2.source_at DESC,
                    q2.parent_source_at_hint DESC,q2.received_at DESC,q2.id DESC LIMIT 1)
              ) ORDER BY received_at DESC,sha256 DESC LIMIT -1 OFFSET ?
            )""", (cutoff,self.raw_response_extra_count))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return {"inserted": inserted, "duplicate": duplicate}

    def put_news(self, owner_id: int, item: dict, *, received_at: str,
                 max_items: int, max_versions: int) -> str:
        if owner_id != self.owner_id:
            raise ValueError("Owner ID mismatch")
        title = str(item.get("title") or "")
        content = str(item.get("content") or "")
        published_raw = str(item.get("time") or "")
        url = safe_source_url(item.get("url"))
        provider_id = str(item.get("id")) if item.get("id") not in (None, "") else None
        if provider_id:
            key_kind, source_key = "PROVIDER_ID_CANDIDATE", provider_id
        elif url:
            key_kind, source_key = "SOURCE_URL_CANDIDATE", url
        else:
            key_kind, source_key = "TIME_TEXT_FALLBACK", published_raw + "\n" + (title or content[:120])
        item_key = hashlib.sha256((key_kind + "\n" + source_key).encode("utf-8")).hexdigest()
        digest = hashlib.sha256((title + "\n" + content).encode("utf-8")).hexdigest()
        current = self.db.execute("SELECT latest_version FROM news_items WHERE item_key=?", (item_key,)).fetchone()
        if current is None:
            self.db.execute("""INSERT INTO news_items
              (item_key,owner_id,source_key_kind,first_received_at,latest_received_at,latest_version)
              VALUES(?,?,?,?,?,1)""", (item_key,owner_id,key_kind,received_at,received_at))
            version = 1
        else:
            existing = self.db.execute("SELECT 1 FROM news_versions WHERE item_key=? AND content_sha256=?",
                                       (item_key,digest)).fetchone()
            if existing:
                self.db.execute("UPDATE news_items SET latest_received_at=? WHERE item_key=?",
                                (received_at,item_key))
                return "DUPLICATE"
            version = current["latest_version"]+1
            self.db.execute("UPDATE news_items SET latest_received_at=?,latest_version=? WHERE item_key=?",
                            (received_at,version,item_key))
        self.db.execute("""INSERT INTO news_versions
          (item_key,owner_id,version,provider_id,title,content,source_url,published_raw,
           published_at_utc,received_at,content_sha256,raw_item_json)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
          (item_key,owner_id,version,provider_id,title,content,url,published_raw,
           published_utc(published_raw),received_at,digest,json.dumps(item,ensure_ascii=False)))
        self.db.execute("""DELETE FROM news_versions WHERE id IN (
          SELECT id FROM news_versions WHERE item_key=? ORDER BY version DESC LIMIT -1 OFFSET ?)""",
          (item_key,max_versions))
        self.db.execute("""DELETE FROM news_items WHERE item_key IN (
          SELECT item_key FROM news_items WHERE owner_id=? ORDER BY latest_received_at DESC
          LIMIT -1 OFFSET ?)""", (owner_id,max_items))
        self.db.execute("DELETE FROM news_versions WHERE item_key NOT IN (SELECT item_key FROM news_items)")
        return "NEW" if version == 1 else "REVISION"


def read_personal_cache(path: Path, owner_id: int, *, news_limit: int = 40) -> dict:
    """Return owner-only values to an already authenticated loopback view."""
    if not path.is_file():
        return {"quotes": [], "news": [], "news_total": 0, "states": [], "symbols": [], "cache_state": "NOT_STARTED"}
    uri = path.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
        db.row_factory = sqlite3.Row
        binding = db.execute("SELECT owner_id FROM owner_binding WHERE singleton=1").fetchone()
        if binding is None or binding["owner_id"] != owner_id:
            return {"quotes": [], "news": [], "news_total": 0, "states": [], "symbols": [], "cache_state": "OWNER_MISMATCH"}
        quotes = db.execute("""SELECT q.symbol,q.provider_code,q.display_name,q.price_text,
          q.currency,q.provider_time,q.received_at,q.market_state,q.delay_status FROM quotes q
          WHERE q.owner_id=? AND q.id=(SELECT q2.id FROM quotes q2 WHERE q2.owner_id=q.owner_id
            AND q2.symbol=q.symbol ORDER BY q2.provider_time DESC,q2.received_at DESC,q2.id DESC LIMIT 1)
          ORDER BY q.symbol""", (owner_id,)).fetchall()
        news = db.execute("""SELECT v.provider_id,v.title,v.content,v.source_url,
          v.published_raw,v.published_at_utc,v.received_at,v.version,
          i.source_key_kind,i.item_key FROM news_versions v JOIN news_items i ON i.item_key=v.item_key
          WHERE v.owner_id=? AND v.version=i.latest_version
          ORDER BY v.received_at DESC,v.id DESC LIMIT ?""", (owner_id,news_limit)).fetchall()
        states = db.execute("SELECT * FROM provider_state ORDER BY provider").fetchall()
        symbols = db.execute("SELECT symbol,state,reason,updated_at FROM symbol_state WHERE owner_id=?",
                             (owner_id,)).fetchall()
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        selection = db.execute("SELECT symbol,desired,updated_at FROM quote_selection WHERE owner_id=? ORDER BY position,symbol",
                               (owner_id,)).fetchall() if "quote_selection" in tables else []
        interests = db.execute("SELECT interest_key,enabled FROM news_interest WHERE owner_id=? ORDER BY interest_key",
                               (owner_id,)).fetchall() if "news_interest" in tables else []
        event_prefs = db.execute("SELECT entities_json,categories_json,updated_at FROM event_preferences WHERE owner_id=?",
                                 (owner_id,)).fetchone() if "event_preferences" in tables else None
        news_total = db.execute("SELECT COUNT(*) FROM news_items WHERE owner_id=?",(owner_id,)).fetchone()[0]
        session_quotes = db.execute("""SELECT q.symbol,q.provider_code,q.session,q.price_text,q.currency,
          q.source_at,q.received_at,q.provider_field_path,q.source_time_field_path,
          q.trade_date,q.date_basis,
          q.parent_date_hint,q.parent_source_at_hint,q.delay_status,q.source_channel FROM session_quotes q
          WHERE q.owner_id=? AND q.id=(SELECT q2.id FROM session_quotes q2
            WHERE q2.owner_id=q.owner_id AND q2.symbol=q.symbol AND q2.session=q.session
            ORDER BY (q2.source_at IS NOT NULL) DESC,q2.source_at DESC,
              q2.parent_source_at_hint DESC,q2.received_at DESC,q2.id DESC LIMIT 1)
          ORDER BY q.symbol,q.session""", (owner_id,)).fetchall() if "session_quotes" in tables else []
        session_capabilities = db.execute("""SELECT symbol,session,state,checked_at,source_channel
          FROM session_capabilities WHERE owner_id=? ORDER BY symbol,session""",
          (owner_id,)).fetchall() if "session_capabilities" in tables else []
        return {"quotes": [dict(row) for row in quotes],
                "session_quotes": [dict(row) for row in session_quotes],
                "session_capabilities": [dict(row) for row in session_capabilities],
                "news": [dict(row) for row in news],
                "news_total": news_total,
                "states": [dict(row) for row in states],
                "symbols": [dict(row) for row in symbols],
                "selection": [dict(row) for row in selection],
                "news_interests": [dict(row) for row in interests],
                "event_preferences": {"entities":json.loads(event_prefs["entities_json"]),
                                      "categories":json.loads(event_prefs["categories_json"]),
                                      "updated_at":event_prefs["updated_at"],"explicit":True} if event_prefs else None,
                "cache_state": "READY"}
