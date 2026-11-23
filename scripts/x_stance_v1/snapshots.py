"""Formal signals as immutable snapshots.

A series is one window x topic x target x language scope x collection mode x vocabulary version. Each time the
evidence behind it really changes, a NEW revision is appended; nothing is overwritten.

  same input rebuilt later      -> the existing revision is returned; its first_committed_at never moves
  new posts / labels / model    -> revision n+1 with observed_at = now, supersedes = revision n, and the reason
  UNKNOWN that later gets labels -> the formal state exists only from the later revision's first_committed_at

x_daily_sentiment is only the current view (latest revision, with materialized_at / last_checked_at for the page).
Anything that asks "what was known then" reads this table: `first_formal` for the first usable state of a series,
`as_of` for the revision that existed at a given time.

Rows that existed before this table (no snapshot id) cannot have their real first commit time recovered. The
migration marks them TIMING_UNVERIFIED and they are excluded from every tradable result; no time is back-filled
from the window end.
"""
from __future__ import annotations

import json
from datetime import datetime

from . import store
from .config import LABELS

SCHEMA = """
CREATE TABLE IF NOT EXISTS x_signal_snapshots(
  snapshot_id TEXT PRIMARY KEY, series_key TEXT NOT NULL, revision INTEGER NOT NULL,
  window_kind TEXT NOT NULL, window_label TEXT NOT NULL, topic_id TEXT NOT NULL, target_id TEXT NOT NULL, lang_scope TEXT NOT NULL,
  mode TEXT NOT NULL, vocab_version TEXT, model_id TEXT, model_revision TEXT, prompt_hash TEXT, config_hash TEXT,
  input_hash TEXT NOT NULL, state TEXT NOT NULL, formal INTEGER NOT NULL, first_committed_at_utc TEXT, observed_at_utc TEXT NOT NULL,
  supersedes TEXT, change_reason TEXT NOT NULL, timing_status TEXT NOT NULL, payload_json TEXT NOT NULL, UNIQUE(series_key, revision));
CREATE INDEX IF NOT EXISTS x_signal_snapshots_series ON x_signal_snapshots(series_key, revision);
CREATE TABLE IF NOT EXISTS x_panel_sentiment(
  scope TEXT NOT NULL, window_kind TEXT NOT NULL, window_label TEXT NOT NULL, window_start_utc TEXT NOT NULL, window_end_utc TEXT NOT NULL,
  topic_id TEXT NOT NULL, target_id TEXT NOT NULL, lang_scope TEXT NOT NULL, mode TEXT NOT NULL, payload_json TEXT NOT NULL,
  state TEXT NOT NULL, signal_committed_at_utc TEXT NOT NULL,
  PRIMARY KEY(scope, window_kind, window_label, topic_id, target_id, lang_scope, mode));
CREATE TABLE IF NOT EXISTS x_panel_narratives(
  scope TEXT NOT NULL, window_kind TEXT NOT NULL, window_label TEXT NOT NULL, topic_id TEXT NOT NULL, target_id TEXT NOT NULL,
  rank INTEGER NOT NULL, payload_json TEXT NOT NULL, PRIMARY KEY(scope, window_kind, window_label, topic_id, target_id, rank));
"""
EMPTY_ACTIVATION = {"model_id": None, "model_revision": None, "prompt_hash": None, "config_hash": None, "vocab_version": None,
                    "identity": None, "sample_version": None, "rules_version": None}
OBSERVED = ("LIVE", "BATCH_AFTER_CLOSE")


def activation(cfg: dict) -> dict:
    """The one classifier configuration whose labels count. Labels from any other model, revision or prompt are
    ignored by aggregation and by the progress figures."""
    from . import sample, stance
    c = cfg["classifier"]
    pv = c.get("prompt_version")
    pinned = bool(c.get("model_id") and c.get("immutable_revision") and pv in stance.PROMPTS)
    act = {"model_id": c.get("model_id"), "model_revision": c.get("immutable_revision"),
           "prompt_hash": stance.prompt_hash(pv) if pv in stance.PROMPTS else None,
           "config_hash": store.sha(json.dumps({k: c.get(k) for k in ("model_id", "immutable_revision", "prompt_version", "temperature", "generation", "schema_version")}, sort_keys=True)),
           "identity": stance.identity_hash(cfg) if pinned else None,          # the complete inference identity; labels are matched on this
           "vocab_version": cfg["topic_vocabulary_version"], "sample_version": sample.sample_version(cfg), "rules_version": sample.rules_version(cfg)}
    if sample.scope_of(cfg) != "keyword":                          # watched-author and combined panels say which panel they are
        act["scope"] = sample.scope_of(cfg)
    return act


def series_key(window_kind, window_label, topic_id, target_id, lang_scope, mode, vocab_version) -> str:
    return store.sha("|".join(str(x) for x in (window_kind, window_label, topic_id, target_id, lang_scope, mode, vocab_version)))[:32]


def _g(row, k):
    try:
        return row[k]
    except (KeyError, IndexError):
        return None


def _latest(db, key: str):
    return db.execute("SELECT * FROM x_signal_snapshots WHERE series_key=? ORDER BY revision DESC LIMIT 1", (key,)).fetchone()


def commit(db, *, window_kind: str, window_label: str, start: datetime, end: datetime, topic_id: str, target_id: str, mode: str,
           result: dict, rows: list, now: datetime, act: dict | None = None) -> dict:
    db.executescript(SCHEMA)
    act = {**EMPTY_ACTIVATION, **(act or {})}
    sv = act["sample_version"] or act["vocab_version"]         # the series is one SAMPLE version: vocabulary + account rules
    key = series_key(window_kind, window_label, topic_id, target_id, result["lang_scope"], mode, sv)
    votes = set(result["vote_post_ids"])
    wts = result.get("vote_weights") or {}
    used = sorted((r["post_id"], r["label"], r["author_id"]) + ((wts[r["post_id"]],) if r["post_id"] in wts else ()) for r in rows if r["post_id"] in votes)
    cls = result.get("classification") or {}
    if not act["identity"] and not act["sample_version"] and not act["rules_version"]:     # callers that predate these fields hash exactly as before
        act = {k: v for k, v in act.items() if k not in ("identity", "sample_version", "rules_version")}
    material = {"activation": act, "votes": used, "accounts": result["accounts"], "posts": result["posts"], "excluded": result["excluded_or_unjudged"],
                "collection_status": result["collection_status"], "state": result["state"], "state_reason": result["state_reason"],
                "pending": cls.get("pending"), "matched_posts": cls.get("matched_posts"), "window": [store.iso(start), store.iso(end)]}
    input_hash = store.sha(json.dumps(material, sort_keys=True, ensure_ascii=False))
    last = _latest(db, key)
    now_iso = store.iso(now)
    if last is not None and last["input_hash"] == input_hash:        # nothing changed: the original stands, only "last checked" moves
        payload = json.loads(last["payload_json"])
        _materialize(db, payload, last, now_iso)
        return {**payload, "snapshot_reused": True, "last_checked_at_utc": now_iso}
    formal = result["state"] in LABELS
    observed = mode in OBSERVED
    # never earlier than the material was received, than the inference behind each vote was completed, or than this commit
    received = [t for r in rows if r["post_id"] in votes for t in (_g(r, "inferred_at_utc"), _g(r, "inference_completed_at_utc"), _g(r, "first_seen_at_utc")) if t]
    committed = max([now_iso, store.iso(end)] + received) if observed else now_iso
    revision = (last["revision"] + 1) if last is not None else 1
    if last is None:
        reason = "FIRST_BUILD"
    elif any(last[k] != act.get(k) for k in ("model_id", "model_revision", "prompt_hash")):
        reason = "MODEL_OR_PROMPT_CHANGED"
    elif (json.loads(last["payload_json"]).get("activation") or {}).get("identity") != act.get("identity"):
        reason = "INFERENCE_IDENTITY_CHANGED"
    elif last["state"] not in LABELS and formal:
        reason = "CLASSIFICATION_OR_SAMPLE_COMPLETED"
    else:
        reason = "EVIDENCE_CHANGED"
    sid = store.sha(f"{key}|{revision}|{input_hash}")[:32]
    payload = {**result, "window_kind": window_kind, "window_label": window_label, "topic_id": topic_id, "target_id": target_id, "window_start_utc": store.iso(start), "window_end_utc": store.iso(end), "source_window_end_utc": store.iso(end), "mode": mode,
               "snapshot_id": sid, "series_key": key, "revision": revision, "supersedes": last["snapshot_id"] if last is not None else None, "change_reason": reason,
               "first_committed_at_utc": committed, "signal_committed_at_utc": committed, "observed_at_utc": now_iso, "timing_status": "RECORDED",
               "assumed_available_at_utc": None if observed else store.iso(end),
               "availability": {"LIVE": "OBSERVED", "BATCH_AFTER_CLOSE": "OBSERVED_AFTER_CLOSE_BATCH"}.get(mode, "RETROSPECTIVE_VISIBLE_NOW_HYPOTHETICAL"),
               "activation": act, "input_hash": input_hash}
    db.execute("INSERT INTO x_signal_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (sid, key, revision, window_kind, window_label, topic_id, target_id, result["lang_scope"], mode, sv, act["model_id"], act["model_revision"],
                act["prompt_hash"], act["config_hash"], input_hash, result["state"], int(formal), committed, now_iso, payload["supersedes"], reason, "RECORDED",
                json.dumps(payload, sort_keys=True, ensure_ascii=False)))
    _materialize(db, payload, None, now_iso)
    return {**payload, "snapshot_reused": False, "last_checked_at_utc": now_iso}


def _materialize(db, payload: dict, row, now_iso: str) -> None:
    view = {**payload, "materialized_at_utc": now_iso, "last_checked_at_utc": now_iso}
    scope = (payload.get("activation") or {}).get("scope")
    if scope:                                                      # the keyword panel keeps x_daily_sentiment to itself
        db.execute("INSERT OR REPLACE INTO x_panel_sentiment VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                   (scope, payload["window_kind"], payload["window_label"], payload["window_start_utc"], payload["window_end_utc"], payload["topic_id"], payload["target_id"],
                    payload["lang_scope"], payload["mode"], json.dumps(view, sort_keys=True, ensure_ascii=False), payload["state"], payload["first_committed_at_utc"] or ""))
        return
    db.execute("INSERT OR REPLACE INTO x_daily_sentiment VALUES(?,?,?,?,?,?,?,?,?,?,?)",
               (payload["window_kind"], payload["window_label"], payload["window_start_utc"], payload["window_end_utc"], payload["topic_id"], payload["target_id"],
                payload["lang_scope"], payload["mode"], json.dumps(view, sort_keys=True, ensure_ascii=False), payload["state"], payload["first_committed_at_utc"] or ""))


def revisions(db, key: str) -> list[dict]:
    return [json.loads(r["payload_json"]) for r in db.execute("SELECT payload_json FROM x_signal_snapshots WHERE series_key=? ORDER BY revision", (key,))]


def first_formal(db, key: str, *, identity: str | None = None) -> dict | None:
    """The first revision of a series that carried a usable state and a recorded commit time.

    Without `identity` this is the production question "what was known first", whichever model produced it.
    A fixed-model study must pass its own inference identity: it then gets the first formal revision OF THAT
    MODEL, and never another model's earlier revision presented as its result."""
    for r in db.execute("SELECT payload_json FROM x_signal_snapshots WHERE series_key=? AND formal=1 AND timing_status='RECORDED' ORDER BY revision", (key,)):
        p = json.loads(r[0])
        if identity is None or (p.get("activation") or {}).get("identity") == identity:
            return p
    return None


def as_of(db, key: str, at_utc: str) -> dict | None:
    r = db.execute("SELECT payload_json FROM x_signal_snapshots WHERE series_key=? AND timing_status='RECORDED' AND first_committed_at_utc<=? ORDER BY revision DESC LIMIT 1", (key, at_utc)).fetchone()
    return json.loads(r[0]) if r else None


def migrate_legacy(db) -> dict:
    """Aggregates written before snapshots existed: kept, marked TIMING_UNVERIFIED, never tradable. Idempotent."""
    db.executescript(SCHEMA)
    db.execute("CREATE TABLE IF NOT EXISTS x_migrations(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, applied_at_utc TEXT NOT NULL, detail_json TEXT NOT NULL)")
    moved = 0
    for r in db.execute("SELECT window_kind, window_label, topic_id, target_id, lang_scope, mode, payload_json, state FROM x_daily_sentiment").fetchall():
        p = json.loads(r["payload_json"])
        if p.get("snapshot_id"):
            continue
        key = series_key(r["window_kind"], r["window_label"], r["topic_id"], r["target_id"], r["lang_scope"], r["mode"], "LEGACY_UNVERSIONED")
        sid = store.sha(f"{key}|legacy")[:32]
        legacy_time = p.get("signal_committed_at_utc")
        p.update({"snapshot_id": sid, "series_key": key, "revision": 0, "supersedes": None, "change_reason": "MIGRATED_LEGACY_ROW", "timing_status": "TIMING_UNVERIFIED",
                  "first_committed_at_utc": None, "signal_committed_at_utc": None, "assumed_available_at_utc": None, "legacy_overwritten_time_utc": legacy_time,
                  "availability": "TIMING_UNVERIFIED_NOT_TRADABLE", "window_kind": r["window_kind"], "window_label": r["window_label"], "topic_id": r["topic_id"], "target_id": r["target_id"]})
        db.execute("INSERT OR IGNORE INTO x_signal_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (sid, key, 0, r["window_kind"], r["window_label"], r["topic_id"], r["target_id"], r["lang_scope"], r["mode"], "LEGACY_UNVERSIONED", None, None, None, None,
                    "LEGACY", r["state"], 0, None, store.utcnow(), None, "MIGRATED_LEGACY_ROW", "TIMING_UNVERIFIED", json.dumps(p, sort_keys=True, ensure_ascii=False)))
        db.execute("UPDATE x_daily_sentiment SET payload_json=?, signal_committed_at_utc='' WHERE window_kind=? AND window_label=? AND topic_id=? AND target_id=? AND lang_scope=? AND mode=?",
                   (json.dumps(p, sort_keys=True, ensure_ascii=False), r["window_kind"], r["window_label"], r["topic_id"], r["target_id"], r["lang_scope"], r["mode"]))
        moved += 1
    done = db.execute("SELECT 1 FROM x_migrations WHERE name='signal-snapshots-v1'").fetchone()
    if moved or not done:
        db.execute("INSERT INTO x_migrations(name, applied_at_utc, detail_json) VALUES(?,?,?)", ("signal-snapshots-v1", store.utcnow(), json.dumps({"legacy_rows_marked": moved})))
    db.commit()
    return {"legacy_rows_marked": moved, "snapshots": db.execute("SELECT count(*) FROM x_signal_snapshots").fetchone()[0]}


def rollback_legacy(db) -> dict:
    """Undo of migrate_legacy for legacy rows only: removes the TIMING_UNVERIFIED snapshot rows and restores the
    recorded (overwritten) time into the view. Snapshots created after the migration are not touched."""
    n = 0
    for r in db.execute("SELECT snapshot_id, window_kind, window_label, topic_id, target_id, lang_scope, mode, payload_json FROM x_signal_snapshots WHERE change_reason='MIGRATED_LEGACY_ROW'").fetchall():
        p = json.loads(r["payload_json"]); t = p.pop("legacy_overwritten_time_utc", None)
        for k in ("snapshot_id", "series_key", "revision", "supersedes", "change_reason", "timing_status", "first_committed_at_utc"):
            p.pop(k, None)
        p["signal_committed_at_utc"] = t
        db.execute("UPDATE x_daily_sentiment SET payload_json=?, signal_committed_at_utc=? WHERE window_kind=? AND window_label=? AND topic_id=? AND target_id=? AND lang_scope=? AND mode=?",
                   (json.dumps(p, sort_keys=True, ensure_ascii=False), t or "", r["window_kind"], r["window_label"], r["topic_id"], r["target_id"], r["lang_scope"], r["mode"]))
        db.execute("DELETE FROM x_signal_snapshots WHERE snapshot_id=?", (r["snapshot_id"],)); n += 1
    db.execute("INSERT INTO x_migrations(name, applied_at_utc, detail_json) VALUES(?,?,?)", ("signal-snapshots-v1-rollback", store.utcnow(), json.dumps({"legacy_rows_restored": n})))
    db.commit()
    return {"legacy_rows_restored": n}
