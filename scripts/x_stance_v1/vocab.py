"""Editable keyword vocabulary as immutable versions.

An edit never changes a version: it creates a new one with an effective New York date. The default effective date
is the first sample date that has no frozen plan yet ("next batch"). A plan that is already frozen keeps the
vocabulary it was frozen with, and the historical plan keeps its own. Shares computed under different vocabulary
versions have different coverage and are never joined into one series.

Standard library only (the web view imports this).
"""
from __future__ import annotations

import copy
import json
import re
import sqlite3
from datetime import date, datetime, timedelta

from . import store

SCHEMA = """
CREATE TABLE IF NOT EXISTS x_vocab_versions(
  version TEXT PRIMARY KEY, parent TEXT, created_at_utc TEXT NOT NULL, effective_from_ny_date TEXT NOT NULL, status TEXT NOT NULL,
  topics_json TEXT NOT NULL, change_json TEXT NOT NULL, activated_at_utc TEXT);
"""
MAX_KEYWORD_CHARS, MAX_QUERY_CHARS = 60, 512
_FORBIDDEN = re.compile(r"[\"'()\\\r\n\t]|(?i:\b(since_time|until_time|since_id|lang|filter|from|to|min_faves|min_retweets)\s*:)|(?<!\S)(OR|AND)(?!\S)")
ACTIONS = ("add", "disable", "enable", "edit")


class VocabError(ValueError):
    pass


def _ensure(db) -> None:
    db.executescript(SCHEMA)


def _rows(db, where: str, args=()) -> list:
    try:
        return db.execute(f"SELECT version, parent, created_at_utc, effective_from_ny_date, status, topics_json, change_json, activated_at_utc FROM x_vocab_versions WHERE {where}", args).fetchall()
    except sqlite3.OperationalError:
        return []


def _as_dict(r) -> dict:
    return {"version": r[0], "parent": r[1], "created_at_utc": r[2], "effective_from_ny_date": r[3], "status": r[4], "topics": json.loads(r[5]),
            "change": json.loads(r[6]), "activated_at_utc": r[7]}


def base(cfg: dict) -> dict:
    """The vocabulary written in the config file. A config that already had another version applied to it
    (with_topics) still carries this one, so "the base version" never silently becomes the current one."""
    if cfg.get("_base_vocabulary"):
        return copy.deepcopy(cfg["_base_vocabulary"])
    return {"version": cfg["topic_vocabulary_version"], "parent": None, "effective_from_ny_date": None, "status": "BASE_CONFIG",
            "topics": [{"id": t["id"], "keywords": list(t["keywords"]), "disabled_keywords": list(t.get("disabled_keywords") or [])} for t in cfg["topics"]]}


def active(db, cfg: dict, on: date | None = None) -> dict:
    """The version in force on a sample date: the ACTIVE version with the latest effective date not after it."""
    rows = _rows(db, "status='ACTIVE'" + (" AND effective_from_ny_date<=?" if on else "") + " ORDER BY effective_from_ny_date DESC, created_at_utc DESC LIMIT 1", (on.isoformat(),) if on else ())
    return _as_dict(rows[0]) if rows else base(cfg)


def get(db, cfg: dict, version: str) -> dict | None:
    """One version by name (any status), or the base config's own."""
    rows = _rows(db, "version=?", (version,))
    if rows:
        return _as_dict(rows[0])
    return base(cfg) if version == base(cfg)["version"] else None


def pending(db) -> dict | None:
    rows = _rows(db, "status='PENDING' ORDER BY created_at_utc DESC LIMIT 1")
    return _as_dict(rows[0]) if rows else None


def with_topics(cfg: dict, v: dict) -> dict:
    out = copy.deepcopy(cfg)
    out["_base_vocabulary"] = base(cfg)
    by_id = {t["id"]: t for t in v["topics"]}
    for t in out["topics"]:
        if t["id"] in by_id:
            t["keywords"], t["disabled_keywords"] = list(by_id[t["id"]]["keywords"]), list(by_id[t["id"]].get("disabled_keywords") or [])
    out["topic_vocabulary_version"] = v["version"]
    return out


def apply(db, cfg: dict, sample_date: date, now: datetime | None = None) -> dict:
    """Config for one sample date. A pending version whose effective date has come is activated here (once)."""
    p = pending(db)
    if p and p["effective_from_ny_date"] <= sample_date.isoformat():
        _ensure(db)
        db.execute("UPDATE x_vocab_versions SET status='ACTIVE', activated_at_utc=? WHERE version=? AND status='PENDING'", (store.iso(now) if now else store.utcnow(), p["version"]))
        db.commit()
    return with_topics(cfg, active(db, cfg, sample_date))


def _lang(k: str) -> str:
    return "zh" if re.search(r"[\u3400-\u9fff]", k) else "en"


def _term(k: str) -> str:
    return f'"{k}"' if re.search(r"[\s\-&]", k) else k


def impact(cfg: dict, topics: list[dict]) -> dict:
    """Queries, their lengths, units and page depth under a vocabulary; the budget itself never changes."""
    reserve = len(" since_time:9999999999 until_time:9999999999")
    queries = []
    for t in topics:
        off = set(t.get("disabled_keywords") or [])
        for lang in cfg["languages"]:
            kws = [k for k in t["keywords"] if k not in off and _lang(k) == lang]
            tail, cur, room = f" lang:{lang}", [], MAX_QUERY_CHARS - reserve - len(f" lang:{lang}") - 2
            groups = []
            for k in kws:
                term = _term(k)
                if len(term) > room:
                    raise VocabError("KEYWORD_LONGER_THAN_QUERY_LIMIT")
                if cur and len(" OR ".join(cur + [term])) > room:
                    groups.append(cur); cur = []
                cur.append(term)
            if cur:
                groups.append(cur)
            queries += [{"topic_id": t["id"], "lang": lang, "chars": len(f"({' OR '.join(g)}){tail}") + reserve} for g in groups]
    page = cfg["quote"]["documented_max_posts_per_page"]
    units = len(queries) * cfg["acquisition"]["proposed_recent_slices_per_day"]
    depth = max(1, cfg["allocation"]["recent"]["max_returned_posts_per_day"] // page // max(1, units)) if units else 0
    return {"queries": len(queries), "max_query_chars_with_time_operators": max((q["chars"] for q in queries), default=0), "query_char_limit": MAX_QUERY_CHARS,
            "recent_units_per_day": units, "recent_pages_per_unit": depth, "recent_posts_if_every_page_is_full": units * depth * page,
            "recent_pool_cap_posts": cfg["allocation"]["recent"]["max_returned_posts_per_day"], "daily_cap_posts": cfg["budget"]["daily_all_jobs_returned_post_cap"],
            "daily_cap_usd": cfg["budget"]["daily_all_jobs_cost_cap_usd"], "budget_changed": False,
            "over_pool": units * page > cfg["allocation"]["recent"]["max_returned_posts_per_day"]}


def _clean(k) -> str:
    k = " ".join(str(k or "").split())
    if not (1 <= len(k) <= MAX_KEYWORD_CHARS):
        raise VocabError("KEYWORD_LENGTH")
    if _FORBIDDEN.search(k):
        raise VocabError("KEYWORD_HAS_QUERY_SYNTAX")
    return k


def next_effective_date(db, now: datetime) -> date:
    """The first New York sample date without a frozen plan."""
    from .batch import last_completed_ny_date
    d = last_completed_ny_date(now)
    try:
        r = db.execute("SELECT max(plan_key) FROM x_sample_plans WHERE job='recent'").fetchone()
    except sqlite3.OperationalError:
        r = None
    if r and r[0]:
        d = max(d, date.fromisoformat(r[0])) + timedelta(days=1) if date.fromisoformat(r[0]) >= d else d
    return d


def propose(db, cfg: dict, changes: list[dict], now: datetime) -> dict:
    """Builds a new PENDING version on top of the pending one (if any) or the active one. Returns the version with
    its impact. Raises VocabError and writes nothing if any change is invalid."""
    _ensure(db)
    if not changes:
        raise VocabError("NO_CHANGES")
    p = pending(db)
    parent = p or active(db, cfg)
    topics = copy.deepcopy(parent["topics"])
    by_id = {t["id"]: t for t in topics}
    for c in changes:
        if c.get("action") not in ACTIONS or c.get("topic_id") not in by_id:
            raise VocabError("UNKNOWN_ACTION_OR_TOPIC")
        t = by_id[c["topic_id"]]
        t.setdefault("disabled_keywords", [])
        k = _clean(c.get("keyword"))
        if c["action"] == "add":
            if k.lower() in {x.lower() for x in t["keywords"]}:
                raise VocabError("KEYWORD_EXISTS")
            t["keywords"].append(k)
        elif k not in t["keywords"]:
            raise VocabError("KEYWORD_NOT_FOUND")
        elif c["action"] == "disable":
            if k not in t["disabled_keywords"]:
                t["disabled_keywords"].append(k)
        elif c["action"] == "enable":
            t["disabled_keywords"] = [x for x in t["disabled_keywords"] if x != k]
        else:
            new = _clean(c.get("new_keyword"))
            if new.lower() in {x.lower() for x in t["keywords"] if x != k}:
                raise VocabError("KEYWORD_EXISTS")
            t["keywords"] = [new if x == k else x for x in t["keywords"]]
            t["disabled_keywords"] = [new if x == k else x for x in t["disabled_keywords"]]
    for t in topics:
        if not [k for k in t["keywords"] if k not in set(t["disabled_keywords"])]:
            raise VocabError("TOPIC_WOULD_HAVE_NO_KEYWORD")
    imp = impact(cfg, topics)
    if imp["over_pool"]:
        raise VocabError("TOO_MANY_QUERIES_FOR_THE_DAILY_POOL")
    body = json.dumps(topics, sort_keys=True, ensure_ascii=False)
    version = "vocab-" + store.sha(body)[:12]
    if version == parent["version"] or _rows(db, "version=?", (version,)):
        raise VocabError("NO_EFFECTIVE_CHANGE")
    eff = next_effective_date(db, now)
    if p:                                                      # a pending version that never took effect is kept for the record
        db.execute("UPDATE x_vocab_versions SET status='REPLACED_BEFORE_ACTIVATION' WHERE version=?", (p["version"],))
    db.execute("INSERT INTO x_vocab_versions VALUES(?,?,?,?,?,?,?,?)", (version, parent["version"], store.iso(now), eff.isoformat(), "PENDING", body,
                                                                    json.dumps(changes, sort_keys=True, ensure_ascii=False), None))
    db.commit()
    return {"version": version, "parent": parent["version"], "effective_from_ny_date": eff.isoformat(), "status": "PENDING", "topics": topics, "impact": imp}


def overview(db, cfg: dict) -> dict:
    a, p = active(db, cfg), pending(db)
    return {"active": {**a, "impact": impact(cfg, a["topics"])}, "pending": ({**p, "impact": impact(cfg, p["topics"])} if p else None),
            "history": [{"version": r[0], "parent": r[1], "created_at_utc": r[2], "effective_from_ny_date": r[3], "status": r[4]} for r in _rows(db, "1=1 ORDER BY created_at_utc")],
            "limits": {"max_keyword_chars": MAX_KEYWORD_CHARS, "max_query_chars": MAX_QUERY_CHARS}}
