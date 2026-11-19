"""Versioned news cache (own SQLite file; never shares a lock with the quote feed).

items     one row per upstream article (upstream, upstream_id): the same Benzinga article arriving via
          the Benzinga stream and via Massive's Benzinga REST is ONE item, not two.
versions  a new version only when the content hash changes; every receipt is counted.
tombstones deletion events are kept; the item is flagged deleted, its versions are not erased.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  upstream TEXT NOT NULL, upstream_id TEXT NOT NULL, first_seen_at TEXT NOT NULL, first_seen_via TEXT NOT NULL,
  first_online_seen_at TEXT, latest_version INTEGER NOT NULL, deleted INTEGER NOT NULL DEFAULT 0, deleted_at TEXT,
  receipts INTEGER NOT NULL DEFAULT 1, PRIMARY KEY (upstream, upstream_id));
CREATE TABLE IF NOT EXISTS versions (
  upstream TEXT NOT NULL, upstream_id TEXT NOT NULL, version INTEGER NOT NULL, content_sha256 TEXT NOT NULL,
  provider TEXT NOT NULL, via TEXT NOT NULL, title TEXT, teaser TEXT, body TEXT, url TEXT, source TEXT, authors TEXT,
  symbols_json TEXT NOT NULL, channels_json TEXT NOT NULL, provider_created_at TEXT, provider_updated_at TEXT,
  received_at TEXT NOT NULL, available_at TEXT NOT NULL, raw_json TEXT NOT NULL,
  PRIMARY KEY (upstream, upstream_id, version));
CREATE TABLE IF NOT EXISTS tombstones (
  upstream TEXT NOT NULL, upstream_id TEXT NOT NULL, provider TEXT NOT NULL, provider_deleted_at TEXT,
  received_at TEXT NOT NULL, raw_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messages_seen (provider TEXT NOT NULL, message_id TEXT NOT NULL, received_at TEXT NOT NULL,
  PRIMARY KEY (provider, message_id));
CREATE TABLE IF NOT EXISTS provider_state (
  provider TEXT PRIMARY KEY, state TEXT NOT NULL, detail TEXT, connected_since TEXT, last_heartbeat_at TEXT,
  last_message_at TEXT, last_news_at TEXT, last_error_class TEXT, last_error_at TEXT, counters_json TEXT NOT NULL,
  updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS versions_received ON versions(received_at);
CREATE INDEX IF NOT EXISTS versions_created ON versions(provider_created_at);
"""
CONTENT_FIELDS = ("title", "teaser", "body", "url", "source", "authors", "symbols", "channels", "provider_updated_at")


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def db_path() -> Path:
    return Path(os.environ.get("QUANT_NEWS_DB", REPO_ROOT / "apps/quant_portal/var/news/news_v1.sqlite3"))


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or db_path(); path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10); db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL"); db.executescript(SCHEMA)
    return db


def content_hash(n: dict) -> str:
    return hashlib.sha256(json.dumps({k: n.get(k) for k in CONTENT_FIELDS}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def seen_message(db, provider: str, message_id) -> bool:
    """True if this provider message id was already processed (stream replays/duplicates)."""
    if message_id is None:
        return False
    try:
        db.execute("INSERT INTO messages_seen VALUES(?,?,?)", (provider, str(message_id), utcnow()))
        return False
    except sqlite3.IntegrityError:
        return True


def _authors(a):
    """Providers send one author string or a list; stored as one comma-joined string."""
    return ", ".join(str(x) for x in a if x) or None if isinstance(a, (list, tuple)) else a


def apply(db, n: dict, *, provider: str, via: str, received_at: str, online: bool = True) -> str:
    """Store one normalized message. Returns NEW | NEW_VERSION | DUPLICATE | TOMBSTONE."""
    key = (n["upstream"], str(n["upstream_id"]))
    raw = json.dumps(n.get("raw"), ensure_ascii=False, default=str)
    if n.get("action") == "deleted":
        db.execute("INSERT INTO tombstones VALUES(?,?,?,?,?,?)", (*key, provider, n.get("provider_updated_at"), received_at, raw))
        db.execute("UPDATE items SET deleted=1, deleted_at=? WHERE upstream=? AND upstream_id=?", (received_at, *key))
        db.commit(); return "TOMBSTONE"
    h = content_hash(n)
    row = db.execute("SELECT latest_version FROM items WHERE upstream=? AND upstream_id=?", key).fetchone()
    if row:
        last = db.execute("SELECT content_sha256 FROM versions WHERE upstream=? AND upstream_id=? AND version=?", (*key, row[0])).fetchone()
        db.execute("UPDATE items SET receipts=receipts+1, first_online_seen_at=COALESCE(first_online_seen_at, ?) WHERE upstream=? AND upstream_id=?",
                   (received_at if online else None, *key))
        if last and last[0] == h:
            db.commit(); return "DUPLICATE"
        version = row[0] + 1
        db.execute("UPDATE items SET latest_version=? WHERE upstream=? AND upstream_id=?", (version, *key))
        result = "NEW_VERSION"
    else:
        version = 1
        db.execute("INSERT INTO items(upstream,upstream_id,first_seen_at,first_seen_via,first_online_seen_at,latest_version) VALUES(?,?,?,?,?,1)",
                   (*key, received_at, via, received_at if online else None))
        result = "NEW"
    db.execute("INSERT INTO versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (*key, version, h, provider, via, n.get("title"), n.get("teaser"), n.get("body"), n.get("url"), n.get("source"),
                _authors(n.get("authors")), json.dumps(n.get("symbols") or []), json.dumps(n.get("channels") or []),
                n.get("provider_created_at"), n.get("provider_updated_at"), received_at, utcnow(), raw))
    db.commit(); return result


def set_state(db, provider: str, state: str, *, detail: str | None = None, error_class: str | None = None, **counters_delta) -> None:
    now = utcnow()
    row = db.execute("SELECT counters_json, connected_since FROM provider_state WHERE provider=?", (provider,)).fetchone()
    counters = json.loads(row[0]) if row else {}
    for k, v in counters_delta.items():
        counters[k] = counters.get(k, 0) + v
    stamp = {"last_heartbeat_at": now}
    if state in ("CONNECTED_NO_MESSAGES_YET", "RECEIVING"):
        stamp["connected_since"] = (row[1] if row and row[1] else now)
    else:
        stamp["connected_since"] = None
    if error_class:
        stamp.update(last_error_class=error_class, last_error_at=now)
    if counters_delta.get("messages"):
        stamp["last_message_at"] = now
    if counters_delta.get("news_new") or counters_delta.get("news_versions"):
        stamp["last_news_at"] = now
    db.execute("""INSERT INTO provider_state(provider,state,detail,counters_json,updated_at) VALUES(?,?,?,?,?)
                  ON CONFLICT(provider) DO UPDATE SET state=excluded.state, detail=excluded.detail,
                  counters_json=excluded.counters_json, updated_at=excluded.updated_at""",
               (provider, state, detail, json.dumps(counters), now))
    sets = ", ".join(f"{k}=?" for k in stamp)
    db.execute(f"UPDATE provider_state SET {sets} WHERE provider=?", (*stamp.values(), provider))
    db.commit()


def read_for_portal(limit: int = 200, path: Path | None = None) -> dict:
    """Read-only view for the web app: latest version per item (deleted items flagged), provider state."""
    path = path or db_path()
    if not path.is_file():
        return {"state": "NOT_STARTED", "items": [], "providers": []}
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("""SELECT i.upstream, i.upstream_id, i.first_seen_at, i.first_seen_via, i.first_online_seen_at, i.deleted,
                i.deleted_at, i.receipts, i.latest_version, v.provider, v.via, v.title, v.teaser, v.body, v.url, v.source, v.authors,
                v.symbols_json, v.channels_json, v.provider_created_at, v.provider_updated_at, v.received_at, v.available_at
              FROM items i JOIN versions v ON v.upstream=i.upstream AND v.upstream_id=i.upstream_id AND v.version=i.latest_version
              ORDER BY COALESCE(v.provider_created_at, i.first_seen_at) DESC LIMIT ?""", (limit,)).fetchall()
        providers = [dict(r) | {"counters": json.loads(r["counters_json"])} for r in db.execute("SELECT * FROM provider_state")]
    items = []
    for r in rows:
        d = dict(r); d["symbols"] = json.loads(d.pop("symbols_json")); d["channels"] = json.loads(d.pop("channels_json"))
        items.append(d)
    for p in providers:
        p.pop("counters_json", None)
    return {"state": "READY", "items": items, "providers": providers}
