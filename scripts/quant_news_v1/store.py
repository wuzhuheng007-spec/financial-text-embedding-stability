"""Versioned news cache (own SQLite file; never shares a lock with the quote feed).

items     one row per upstream article (upstream, upstream_id): the same Benzinga article arriving via
          the Benzinga stream and via Massive's Benzinga REST is ONE item, not two.
versions  every distinct content ever received (arrival history; a hash seen before is a DUPLICATE).
tombstones every deletion/removal event; the item is flagged deleted, its versions are never erased.

Which version is CURRENT (items.latest_version) is decided by the provider's update time, not arrival order:
  R1 newer provider_updated_at than current            -> becomes current (NEW_VERSION)
  R2 older provider_updated_at                         -> kept as history only (HISTORY_VERSION)
  R3 same provider_updated_at, different content       -> current kept (first arrival wins), CONFLICT_KEPT
  R4 incoming has no update time, current has one      -> history only (HISTORY_VERSION)
  R5 neither has an update time                        -> arrival order: incoming becomes current
Deletions (stream action deleted, REST /news-removed) use the provider's removal time (else receipt time):
  D1 a deletion marks the item deleted (also for an id never seen: a deleted stub, no visible version)
  D2 content with update time <= removal time, or no update time, arriving after a deletion is stored as
     history and the item STAYS deleted (STALE_AFTER_DELETE) - old backfill cannot resurrect it
  D3 content whose provider update time is strictly later than the removal time is treated as the provider
     publishing it again: the item is restored (RESTORED) and that content becomes current
  D4 repeated deletions keep the latest removal time
  D5 a deletion whose removal time is not later than the provider update time of the currently VISIBLE
     content (e.g. an old removal replayed after a republish) is kept as a tombstone record but does not
     hide that content (STALE_TOMBSTONE)
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
  receipts INTEGER NOT NULL DEFAULT 1, deleted_provider_at TEXT, restored_at TEXT, versions_n INTEGER NOT NULL DEFAULT 0,
  conflicts INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (upstream, upstream_id));
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


def _ts(v):
    """UTC ISO (with or without fractions) -> datetime for comparison; None stays None."""
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def apply(db, n: dict, *, provider: str, via: str, received_at: str, online: bool = True) -> str:
    """Store one normalized message under rules R1-R5 / D1-D4 (module docstring). Returns NEW | NEW_VERSION |
    HISTORY_VERSION | CONFLICT_KEPT | DUPLICATE | TOMBSTONE | STALE_TOMBSTONE | STALE_AFTER_DELETE | RESTORED."""
    key = (n["upstream"], str(n["upstream_id"]))
    raw = json.dumps(n.get("raw"), ensure_ascii=False, default=str)
    row = db.execute("SELECT latest_version, deleted, deleted_provider_at, versions_n FROM items WHERE upstream=? AND upstream_id=?", key).fetchone()
    if n.get("action") == "deleted":
        removed_at = n.get("provider_updated_at") or received_at
        db.execute("INSERT INTO tombstones VALUES(?,?,?,?,?,?)", (*key, provider, n.get("provider_updated_at"), received_at, raw))
        if row is None:  # D1: deletion before (or without) any content
            db.execute("""INSERT INTO items(upstream,upstream_id,first_seen_at,first_seen_via,first_online_seen_at,latest_version,deleted,deleted_at,
                          deleted_provider_at,versions_n) VALUES(?,?,?,?,NULL,0,1,?,?,0)""", (*key, received_at, via, received_at, removed_at))
        else:
            if not row["deleted"] and row["latest_version"]:
                cur = db.execute("SELECT provider_updated_at FROM versions WHERE upstream=? AND upstream_id=? AND version=?",
                                 (*key, row["latest_version"])).fetchone()
                cur_t, rem_t = _ts(cur[0]) if cur else None, _ts(removed_at)
                if cur_t is not None and rem_t is not None and rem_t <= cur_t:  # D5
                    db.execute("UPDATE items SET receipts=receipts+1 WHERE upstream=? AND upstream_id=?", key)
                    db.commit(); return "STALE_TOMBSTONE"
            prev = _ts(row["deleted_provider_at"])
            keep = removed_at if prev is None or (_ts(removed_at) or prev) >= prev else row["deleted_provider_at"]  # D4
            db.execute("UPDATE items SET deleted=1, deleted_at=COALESCE(deleted_at, ?), deleted_provider_at=?, receipts=receipts+1 WHERE upstream=? AND upstream_id=?",
                       (received_at, keep, *key))
        db.commit(); return "TOMBSTONE"
    h = content_hash(n)
    if row is None:
        db.execute("""INSERT INTO items(upstream,upstream_id,first_seen_at,first_seen_via,first_online_seen_at,latest_version,versions_n)
                      VALUES(?,?,?,?,?,1,1)""", (*key, received_at, via, received_at if online else None))
        _insert_version(db, key, 1, h, n, provider, via, received_at, raw)
        db.commit(); return "NEW"
    db.execute("UPDATE items SET receipts=receipts+1, first_online_seen_at=COALESCE(first_online_seen_at, ?) WHERE upstream=? AND upstream_id=?",
               (received_at if online else None, *key))
    if db.execute("SELECT 1 FROM versions WHERE upstream=? AND upstream_id=? AND content_sha256=?", (*key, h)).fetchone():
        db.commit(); return "DUPLICATE"  # this exact content was already received (current or history)
    version = row["versions_n"] + 1
    _insert_version(db, key, version, h, n, provider, via, received_at, raw)
    db.execute("UPDATE items SET versions_n=? WHERE upstream=? AND upstream_id=?", (version, *key))
    inc = _ts(n.get("provider_updated_at"))
    if row["deleted"]:
        removed = _ts(row["deleted_provider_at"])
        if inc is not None and removed is not None and inc > removed:  # D3
            db.execute("UPDATE items SET deleted=0, restored_at=?, latest_version=? WHERE upstream=? AND upstream_id=?", (received_at, version, *key))
            db.commit(); return "RESTORED"
        db.commit(); return "STALE_AFTER_DELETE"  # D2
    cur = db.execute("SELECT provider_updated_at FROM versions WHERE upstream=? AND upstream_id=? AND version=?", (*key, row["latest_version"])).fetchone()
    cur_t = _ts(cur[0]) if cur else None
    if cur is None or (inc is not None and (cur_t is None or inc > cur_t)) or (inc is None and cur_t is None):  # R1 / R5
        db.execute("UPDATE items SET latest_version=? WHERE upstream=? AND upstream_id=?", (version, *key))
        db.commit(); return "NEW_VERSION"
    if inc is not None and cur_t is not None and inc == cur_t:  # R3
        db.execute("UPDATE items SET conflicts=conflicts+1 WHERE upstream=? AND upstream_id=?", key)
        db.commit(); return "CONFLICT_KEPT"
    db.commit(); return "HISTORY_VERSION"  # R2 / R4


def _insert_version(db, key, version, h, n, provider, via, received_at, raw):
    db.execute("INSERT INTO versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (*key, version, h, provider, via, n.get("title"), n.get("teaser"), n.get("body"), n.get("url"), n.get("source"),
                _authors(n.get("authors")), json.dumps(n.get("symbols") or []), json.dumps(n.get("channels") or []),
                n.get("provider_created_at"), n.get("provider_updated_at"), received_at, utcnow(), raw))


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


def read_sync_state(path: Path | None = None) -> dict:
    """Read-only: REST gap-fill completion watermarks and unfinished windows per provider/channel."""
    path = path or db_path()
    if not path.is_file():
        return {"state": "NOT_STARTED", "channels": []}
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='cursors'").fetchone():
            return {"state": "NO_REST_SYNC_YET", "channels": []}
        rows = db.execute("SELECT name, value, updated_at FROM cursors ORDER BY name").fetchall()
    chans = {}
    for name, value, updated in rows:
        parts = name.split(":")
        prov, ch, kind = (parts[0], "articles", "done") if parts[-1] == "updated_since" else (parts[0], parts[1], parts[2]) if len(parts) == 3 else (parts[0], "?", "?")
        c = chans.setdefault((prov, ch), {"provider": prov, "channel": ch, "complete_through_utc": None, "unfinished_window": None})
        if kind == "done":
            c["complete_through_utc"] = datetime.fromtimestamp(float(value), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"); c["updated_at"] = updated
        elif kind == "pending":
            c["unfinished_window"] = json.loads(value)
    return {"state": "READY", "channels": list(chans.values())}


def read_for_portal(limit: int = 200, path: Path | None = None, *, english_only: bool = False) -> dict:
    """Read-only view for the web app: latest version per item (deleted items flagged), provider state."""
    path = path or db_path()
    if not path.is_file():
        return {"state": "NOT_STARTED", "items": [], "providers": []}
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
        db.row_factory = sqlite3.Row
        if english_only:
            return _read_english(db, limit)
        rows = db.execute("""SELECT i.upstream, i.upstream_id, i.first_seen_at, i.first_seen_via, i.first_online_seen_at, i.deleted,
                i.deleted_at, i.receipts, i.latest_version, i.versions_n, i.conflicts, i.restored_at, v.provider, v.via, v.title, v.teaser, v.body, v.url, v.source, v.authors,
                v.symbols_json, v.channels_json, v.provider_created_at, v.provider_updated_at, v.received_at, v.available_at
              FROM items i JOIN versions v ON v.upstream=i.upstream AND v.upstream_id=i.upstream_id AND v.version=i.latest_version
              ORDER BY COALESCE(v.provider_created_at, i.first_seen_at) DESC LIMIT ?""",
                          (limit,))
        items = [_portal_item(row) for row in rows]
        providers = _providers(db)
    return {"state": "READY", "items": items, "providers": providers}


_ITEM_COLUMNS = """i.upstream, i.upstream_id, i.first_seen_at, i.first_seen_via, i.first_online_seen_at, i.deleted,
    i.deleted_at, i.receipts, i.latest_version, i.versions_n, i.conflicts, i.restored_at, v.provider, v.via, v.title, v.teaser, v.body, v.url, v.source, v.authors,
    v.symbols_json, v.channels_json, v.provider_created_at, v.provider_updated_at, v.received_at, v.available_at"""
_JOIN = "FROM items i JOIN versions v ON v.upstream=i.upstream AND v.upstream_id=i.upstream_id AND v.version=i.latest_version"
# Language verdict per content hash. The same content always gets the same verdict, so it is computed once per
# process; the page polls this list and must not run the detector over the whole store on every poll.
_ENGLISH_VERDICT: dict[str, bool] = {}
_ENGLISH_VERDICT_MAX = 200_000


def _portal_item(row) -> dict:
    item = dict(row)
    item["symbols"] = json.loads(item.pop("symbols_json"))
    item["channels"] = json.loads(item.pop("channels_json"))
    return item


def _providers(db) -> list[dict]:
    providers = [dict(r) | {"counters": json.loads(r["counters_json"])} for r in db.execute("SELECT * FROM provider_state")]
    for p in providers:
        p.pop("counters_json", None)
    return providers


def _read_english(db, limit: int) -> dict:
    """The newest `limit` visible English items. The filter runs before the limit, newest first, and stops as soon as
    the list is full; text is read only for versions without a verdict yet and for the items that are returned."""
    from .language import is_english_news
    if len(_ENGLISH_VERDICT) > _ENGLISH_VERDICT_MAX:
        _ENGLISH_VERDICT.clear()
    keys = db.execute(f"SELECT i.upstream, i.upstream_id, i.latest_version, i.deleted, v.content_sha256 {_JOIN} "
                      "ORDER BY COALESCE(v.provider_created_at, i.first_seen_at) DESC").fetchall()
    chosen = []
    for k in keys:
        if len(chosen) >= limit:
            break
        if k["deleted"]:
            continue
        key = (k["upstream"], k["upstream_id"], k["latest_version"])
        ok = _ENGLISH_VERDICT.get(k["content_sha256"])
        if ok is None:
            t = db.execute("SELECT title, teaser, body FROM versions WHERE upstream=? AND upstream_id=? AND version=?", key).fetchone()
            ok = _ENGLISH_VERDICT[k["content_sha256"]] = bool(t) and is_english_news(t["title"], t["teaser"], t["body"])
        if ok:
            chosen.append(key)
    items = []
    for key in chosen:
        row = db.execute(f"SELECT {_ITEM_COLUMNS} {_JOIN} WHERE i.upstream=? AND i.upstream_id=? AND i.latest_version=?", key).fetchone()
        if row is not None:
            items.append(_portal_item(row))
    return {"state": "READY", "items": items, "providers": _providers(db)}
