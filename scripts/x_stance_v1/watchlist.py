"""Watched authors: a platform-side, read-only monitoring list. Nothing here follows anyone on X, needs an X
password or reads private content.

A row is one LOGICAL author. The handle is what the owner supplied (a screenshot transcription); the stable author
id is only ever taken from a provider response in which the returned `author.userName` equals that handle. Until
then the id stays empty - it is never guessed. After it is bound the author is tracked by id, so a later rename is
recorded in the handle history instead of losing the author.

  SCREENSHOT_HANDLE_PENDING_PROVIDER_ID   handle supplied, no provider response has confirmed it yet
  RESOLVED_FROM_PROVIDER                  id bound from a response whose userName matched the handle
  OWNER_CONFIRMED_HANDLE_PENDING_PROVIDER_ID   the owner settled an ambiguous handle; id still to come
  AMBIGUOUS_HANDLE_BLOCKED                more than one spelling is plausible: NO query is sent for this author
  AUTHOR_MISMATCH                         the query came back with a different account: not counted as watched

Selection time matters: the list was chosen on its import date. Anything derived for earlier dates is a panel of
authors selected afterwards, and follower counts or ranks seen today are never written back onto earlier dates.

Scheduling priority (who is asked first) and statistical weight (how much a vote counts) are separate fields; the
import sets neither weight. Standard library only (the web view imports this).
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from . import store

SCHEMA = """
CREATE TABLE IF NOT EXISTS x_watch_versions(
  version TEXT NOT NULL, imported_at_utc TEXT NOT NULL, seed_sha256 TEXT NOT NULL, accounts INTEGER NOT NULL, added INTEGER NOT NULL,
  merged INTEGER NOT NULL, note TEXT, PRIMARY KEY(version, seed_sha256));
CREATE TABLE IF NOT EXISTS x_watch_authors(
  watch_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, handle TEXT, handle_normalized TEXT, stable_author_id TEXT, identity_status TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1, scheduling_priority INTEGER NOT NULL DEFAULT 2, source_lists_json TEXT NOT NULL, aliases_json TEXT NOT NULL,
  handle_candidates_json TEXT NOT NULL, handle_history_json TEXT NOT NULL, provenance_json TEXT NOT NULL, watchlist_version TEXT NOT NULL,
  first_imported_at_utc TEXT NOT NULL, updated_at_utc TEXT NOT NULL, resolved_at_utc TEXT, availability TEXT NOT NULL DEFAULT 'WAITING_FOR_FIRST_COLLECTION',
  last_success_at_utc TEXT, last_post_created_at_utc TEXT, last_checked_ny_date TEXT, mismatch_json TEXT, candidate_evidence_json TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS x_watch_authors_handle ON x_watch_authors(handle_normalized) WHERE handle_normalized IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS x_watch_authors_id ON x_watch_authors(stable_author_id) WHERE stable_author_id IS NOT NULL;
"""
BLOCKED = ("AMBIGUOUS_HANDLE_BLOCKED",)
NOT_COUNTED = ("AMBIGUOUS_HANDLE_BLOCKED", "AUTHOR_MISMATCH")
_HANDLE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
TOPIC_PREFIX = "watch:"


class WatchError(ValueError):
    pass


def norm(handle) -> str | None:
    h = str(handle or "").strip().lstrip("@")
    return h.lower() if h else None


def ensure(db) -> None:
    db.executescript(SCHEMA)


def exists(db) -> bool:
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='x_watch_authors'").fetchone())


def _row(r) -> dict:
    d = dict(r)
    for k in ("source_lists", "aliases", "handle_candidates", "handle_history", "provenance", "mismatch", "candidate_evidence"):
        v = d.pop(k + "_json", None)
        d[k] = json.loads(v) if v else ([] if k in ("source_lists", "aliases", "handle_candidates", "handle_history") else None)
    d["enabled"] = bool(d["enabled"])
    d["profile_url"] = f"https://x.com/{d['handle']}" if d["handle"] else None
    return d


def authors(db) -> list[dict]:
    if not exists(db):
        return []
    return [_row(r) for r in db.execute("SELECT * FROM x_watch_authors ORDER BY scheduling_priority, watch_id")]


def get(db, watch_id: str) -> dict:
    r = db.execute("SELECT * FROM x_watch_authors WHERE watch_id=?", (watch_id,)).fetchone() if exists(db) else None
    if r is None:
        raise WatchError("UNKNOWN_WATCH_ID")
    return _row(r)


def ready(a: dict) -> bool:
    """May a query be sent for this author?"""
    return bool(a["enabled"] and a["handle"] and a["identity_status"] not in NOT_COUNTED)


def _merge(old: list, new: list) -> list:
    return old + [x for x in new if x not in old]


def import_seed(db, seed: dict, now: datetime, *, seed_sha256: str | None = None) -> dict:
    """Idempotent. One logical author per normalised handle (or bound id, or - for a blocked row without a handle -
    per watch id). A second appearance only adds source lists and aliases; it never creates a second person, never
    changes a bound id, an enabled flag the owner set, or any weight."""
    ensure(db)
    if seed.get("schema_version") != "x-platform-watchlist-seed-v1" or not isinstance(seed.get("accounts"), list):
        raise WatchError("SEED_FORMAT")
    ver, t = seed["watchlist_version"], store.iso(now)
    sha = seed_sha256 or seed.get("content_sha256") or store.sha(json.dumps(seed["accounts"], sort_keys=True, ensure_ascii=False))
    out = {"version": ver, "seed_sha256": sha, "in_seed": len(seed["accounts"]), "added": 0, "merged": 0, "unchanged": 0, "rejected": []}
    for a in seed["accounts"]:
        handle, hn = a.get("handle"), norm(a.get("handle"))
        if handle is not None and not _HANDLE.match(str(handle)):
            out["rejected"].append({"watch_id": a.get("watch_id"), "reason": "HANDLE_SYNTAX"}); continue
        if handle is None and a.get("identity_status") != "AMBIGUOUS_HANDLE_BLOCKED":
            out["rejected"].append({"watch_id": a.get("watch_id"), "reason": "NO_HANDLE_AND_NOT_MARKED_AMBIGUOUS"}); continue
        if a.get("stable_author_id"):                              # a seed cannot assert an id; only a provider response can
            out["rejected"].append({"watch_id": a.get("watch_id"), "reason": "SEED_MAY_NOT_SUPPLY_A_STABLE_ID"}); continue
        cur = None
        if hn:
            cur = db.execute("SELECT * FROM x_watch_authors WHERE handle_normalized=?", (hn,)).fetchone()
            if cur is None:                                         # an author who was renamed since: the old handle is in the history
                for r in db.execute("SELECT * FROM x_watch_authors WHERE handle_history_json LIKE ?", (f'%"{hn}"%',)):
                    if any(norm(h.get("handle")) == hn for h in json.loads(r["handle_history_json"])):
                        cur = r; break
        if cur is None:
            cur = db.execute("SELECT * FROM x_watch_authors WHERE watch_id=?", (a["watch_id"],)).fetchone()
            if cur is not None and hn and cur["handle_normalized"] and cur["handle_normalized"] != hn and cur["identity_status"] not in BLOCKED:
                out["rejected"].append({"watch_id": a["watch_id"], "reason": "WATCH_ID_ALREADY_USED_BY_ANOTHER_HANDLE"}); continue
        prov = {"source_type": a.get("source_type"), "watchlist_version": ver, "handle_text_in_image": a.get("handle_text_in_image"), "resolution_note": a.get("resolution_note"),
                "display_name_from_image": a.get("display_name_from_image")}
        if cur is None:
            db.execute("INSERT INTO x_watch_authors(watch_id, display_name, handle, handle_normalized, stable_author_id, identity_status, enabled, scheduling_priority, source_lists_json,"
                       " aliases_json, handle_candidates_json, handle_history_json, provenance_json, watchlist_version, first_imported_at_utc, updated_at_utc) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (a["watch_id"], a["display_name_from_image"], handle, hn, None, a["identity_status"], int(bool(a.get("requested_monitoring", True))), int(a.get("scheduling_priority") or 2),
                        json.dumps(a.get("source_lists") or [], ensure_ascii=False), json.dumps(a.get("aliases") or [], ensure_ascii=False),
                        json.dumps(a.get("handle_candidates") or [], ensure_ascii=False),
                        json.dumps([{"handle": handle, "from_utc": t, "basis": "IMPORTED_FROM_OWNER_SCREENSHOT"}] if handle else [], ensure_ascii=False),
                        json.dumps(prov, ensure_ascii=False), ver, t, t))
            out["added"] += 1
            continue
        lists = _merge(json.loads(cur["source_lists_json"]), a.get("source_lists") or [])
        aliases = _merge(json.loads(cur["aliases_json"]), a.get("aliases") or [])
        cands = _merge(json.loads(cur["handle_candidates_json"]), a.get("handle_candidates") or [])
        if lists == json.loads(cur["source_lists_json"]) and aliases == json.loads(cur["aliases_json"]) and cands == json.loads(cur["handle_candidates_json"]):
            out["unchanged"] += 1
            continue
        db.execute("UPDATE x_watch_authors SET source_lists_json=?, aliases_json=?, handle_candidates_json=?, updated_at_utc=? WHERE watch_id=?",
                   (json.dumps(lists, ensure_ascii=False), json.dumps(aliases, ensure_ascii=False), json.dumps(cands, ensure_ascii=False), t, cur["watch_id"]))
        out["merged"] += 1
    db.execute("INSERT OR IGNORE INTO x_watch_versions VALUES(?,?,?,?,?,?,?)", (ver, t, sha, len(seed["accounts"]), out["added"], out["merged"], seed.get("scope")))
    db.commit()
    out["authors_in_store"] = db.execute("SELECT count(*) FROM x_watch_authors").fetchone()[0]
    out["imported_at_utc"] = db.execute("SELECT min(imported_at_utc) FROM x_watch_versions WHERE version=?", (ver,)).fetchone()[0]
    return out


def set_enabled(db, watch_id: str, enabled: bool, now: datetime) -> dict:
    get(db, watch_id)
    db.execute("UPDATE x_watch_authors SET enabled=?, updated_at_utc=? WHERE watch_id=?", (int(bool(enabled)), store.iso(now), watch_id))
    db.commit()
    return get(db, watch_id)


def confirm_handle(db, watch_id: str, handle: str, now: datetime, *, basis: str = "OWNER_CONFIRMED") -> dict:
    """The owner settles an ambiguous handle. It must be the image text or one of the recorded candidates - this is
    a choice between known spellings, not a place to type a new account."""
    a = get(db, watch_id)
    if a["identity_status"] not in BLOCKED:
        raise WatchError("NOT_AMBIGUOUS")
    if not _HANDLE.match(handle or ""):
        raise WatchError("HANDLE_SYNTAX")
    known = {norm(c.get("handle")) for c in a["handle_candidates"]} | {norm((a["provenance"] or {}).get("handle_text_in_image"))}
    if norm(handle) not in known:
        raise WatchError("NOT_ONE_OF_THE_RECORDED_SPELLINGS")
    if db.execute("SELECT 1 FROM x_watch_authors WHERE handle_normalized=? AND watch_id!=?", (norm(handle), watch_id)).fetchone():
        raise WatchError("HANDLE_ALREADY_WATCHED")
    t = store.iso(now)
    db.execute("UPDATE x_watch_authors SET handle=?, handle_normalized=?, identity_status='OWNER_CONFIRMED_HANDLE_PENDING_PROVIDER_ID', handle_history_json=?, updated_at_utc=? WHERE watch_id=?",
               (handle, norm(handle), json.dumps(a["handle_history"] + [{"handle": handle, "from_utc": t, "basis": basis}], ensure_ascii=False), t, watch_id))
    db.commit()
    return get(db, watch_id)


# ---------- queries ----------
def version_tag(db) -> str | None:
    """Names the set of logical authors that is watched now (enabled, not blocked). It keys the watched-author and
    combined samples, so a changed list is a new series and never silently re-defines an old one."""
    rows = [a for a in authors(db) if a["enabled"] and a["identity_status"] not in NOT_COUNTED]
    if not rows:
        return None
    ver = sorted({a["watchlist_version"] for a in authors(db)})[-1]
    return f"{ver}-{store.sha('|'.join(sorted(a['watch_id'] for a in rows)))[:8]}"


def register_queries(db, cfg: dict) -> list[dict]:
    """One `from:handle` query per author that may be asked, in scheduling order (priority, then watch id). The
    blocked author gets none. Registered like any other query so runs, pages and costs share the same tables."""
    provider, out = cfg["provider_candidate"], []
    for a in authors(db):
        if not ready(a):
            continue
        q = f"from:{a['handle']}"
        qh = store.sha(f"{provider}|watchlist|{a['watch_id']}|{q}")
        db.execute("INSERT OR IGNORE INTO x_queries VALUES(?,?,?,?,?,?)", (qh, provider, TOPIC_PREFIX + a["watch_id"], "any", "watchlist:" + a["watchlist_version"], q))
        out.append({"query_hash": qh, "topic_id": TOPIC_PREFIX + a["watch_id"], "lang": "any", "query": q, "watch_id": a["watch_id"], "handle": a["handle"],
                    "scheduling_priority": a["scheduling_priority"]})
    db.commit()
    return out


def bound_ids(db) -> dict[str, str]:
    """stable author id -> watch id, for authors that count (enabled, id bound, not blocked or mismatched)."""
    return {a["stable_author_id"]: a["watch_id"] for a in authors(db) if a["enabled"] and a["stable_author_id"] and a["identity_status"] not in NOT_COUNTED}


def watch_only_posts(db) -> set[tuple[str, str]]:
    """Posts that are in the store ONLY because an author query returned them. They are not part of the keyword
    sample, whose definition (collected by keyword queries) must not change because a list was added."""
    rows = db.execute("SELECT rp.provider, rp.post_id, SUM(CASE WHEN q.topic_id LIKE 'watch%' THEN 0 ELSE 1 END) other FROM x_run_posts rp "
                      "JOIN x_query_runs r ON r.run_id=rp.run_id LEFT JOIN x_queries q ON q.query_hash=r.query_hash GROUP BY rp.provider, rp.post_id").fetchall()
    return {(r[0], r[1]) for r in rows if not r[2]}


def post_sources(db, provider: str, post_id: str) -> list[str]:
    """How a stored post got here: 'keyword:<topic>' and / or 'watch:<watch id>'. One stored copy, many sources."""
    rows = db.execute("SELECT DISTINCT q.topic_id FROM x_run_posts rp JOIN x_query_runs r ON r.run_id=rp.run_id JOIN x_queries q ON q.query_hash=r.query_hash WHERE rp.provider=? AND rp.post_id=?",
                      (provider, post_id)).fetchall()
    return sorted(t[0] if t[0].startswith(TOPIC_PREFIX) else "keyword:" + t[0] for t in rows)


def _engagement(row) -> dict:
    try:
        return json.loads(row["engagement_snapshot_json"] or "{}")
    except (ValueError, TypeError):
        return {}


def resolve(db, plan_units: list[dict], now: datetime) -> dict:
    """After the author queries of one plan have run: bind ids, notice renames and mismatches, and set each
    author's availability from what its own query actually did. `plan_units`: watch units with run_id."""
    t, out = store.iso(now), {"bound": [], "renamed": [], "mismatch": [], "no_visible_posts": [], "failed": [], "not_reached": []}
    for u in plan_units:
        a = get(db, u["watch_id"])
        run = db.execute("SELECT status, stop_reason, error_class, pages, returned, in_window FROM x_query_runs WHERE run_id=?", (u["run_id"],)).fetchone()
        if run is None or (run["pages"] or 0) == 0:
            if run is not None and run["status"] == "PARTIAL":
                avail = "QUERY_FAILED:" + str(run["error_class"] or run["stop_reason"])
                out["failed"].append(a["watch_id"])
            else:
                avail = "NOT_REACHED:" + str((run["stop_reason"] if run else None) or "NOT_RUN")
                out["not_reached"].append(a["watch_id"])
            db.execute("UPDATE x_watch_authors SET availability=?, last_checked_ny_date=?, updated_at_utc=? WHERE watch_id=?", (avail, u.get("ny_date"), t, a["watch_id"]))
            continue
        posts = db.execute("SELECT p.author_id, p.created_at_utc, p.engagement_snapshot_json FROM x_run_posts rp JOIN x_posts p USING(provider, post_id) WHERE rp.run_id=?", (u["run_id"],)).fetchall()
        seen = {}
        for p in posts:
            name = _engagement(p).get("authorUserName")
            s = seen.setdefault(p["author_id"], {"names": set(), "n": 0, "latest": None})
            s["n"] += 1; s["latest"] = max(s["latest"] or p["created_at_utc"], p["created_at_utc"])
            if name:
                s["names"].add(name)
        hn, sid = a["handle_normalized"], a["stable_author_id"]
        avail, extra = None, {}
        if not posts:
            avail = "NO_VISIBLE_POSTS_IN_WINDOW" if run["status"] in ("COMPLETE", "PLANNED_SAMPLE") else "QUERY_FAILED:" + str(run["error_class"] or run["stop_reason"])
            out["no_visible_posts" if avail.startswith("NO_") else "failed"].append(a["watch_id"])
        elif sid:
            mine = seen.get(sid)
            if mine is None:
                avail, extra = "AUTHOR_MISMATCH_IN_THIS_WINDOW", {"mismatch_json": json.dumps({"at_utc": t, "expected_id": sid, "returned_ids": sorted(seen)}, ensure_ascii=False)}
                out["mismatch"].append(a["watch_id"])
            else:
                avail, extra = "COLLECTED", {"last_success_at_utc": t, "last_post_created_at_utc": max(a["last_post_created_at_utc"] or mine["latest"], mine["latest"])}
                new = next((n for n in sorted(mine["names"]) if norm(n) != hn), None)
                if new and not db.execute("SELECT 1 FROM x_watch_authors WHERE handle_normalized=? AND watch_id!=?", (norm(new), a["watch_id"])).fetchone():
                    extra.update({"handle": new, "handle_normalized": norm(new),
                                  "handle_history_json": json.dumps(a["handle_history"] + [{"handle": new, "from_utc": t, "basis": "PROVIDER_REPORTED_NEW_USERNAME_FOR_THE_SAME_ID"}], ensure_ascii=False)})
                    out["renamed"].append({"watch_id": a["watch_id"], "from": a["handle"], "to": new})
        else:
            match = [aid for aid, s in seen.items() if any(norm(n) == hn for n in s["names"])]
            if len(match) == 1 and len(seen) == 1 and not db.execute("SELECT 1 FROM x_watch_authors WHERE stable_author_id=?", (match[0],)).fetchone():
                avail = "COLLECTED"
                extra = {"stable_author_id": match[0], "identity_status": "RESOLVED_FROM_PROVIDER", "resolved_at_utc": t, "last_success_at_utc": t, "last_post_created_at_utc": seen[match[0]]["latest"]}
                out["bound"].append({"watch_id": a["watch_id"], "handle": a["handle"]})
            elif not any(s["names"] for s in seen.values()):
                avail = "RETURNED_WITHOUT_USERNAME_ID_NOT_BOUND"         # nothing to compare the handle with: the id is not taken on trust
                out["failed"].append(a["watch_id"])
            else:
                avail = "AUTHOR_MISMATCH"
                extra = {"identity_status": "AUTHOR_MISMATCH", "mismatch_json": json.dumps({"at_utc": t, "asked_for": a["handle"],
                         "returned": [{"author_id": aid, "user_names": sorted(s["names"]), "posts": s["n"]} for aid, s in sorted(seen.items())]}, ensure_ascii=False)}
                out["mismatch"].append(a["watch_id"])
        sets = {"availability": avail, "last_checked_ny_date": u.get("ny_date"), "updated_at_utc": t, **extra}
        db.execute(f"UPDATE x_watch_authors SET {','.join(k + '=?' for k in sets)} WHERE watch_id=?", list(sets.values()) + [a["watch_id"]])
    db.commit()
    return out


def coverage(db, cfg: dict, start_utc: str, end_utc: str) -> dict:
    """Collection status of [start, end) for the watched authors: every author that counts must have author-query
    runs that jointly contain the interval. It describes the collection programme, not how much each author wrote."""
    provider, gaps, kinds, n = cfg["provider_candidate"], [], set(), 0
    for a in authors(db):
        if not ready(a):
            continue
        n += 1
        qh = store.sha(f"{provider}|watchlist|{a['watch_id']}|from:{a['handle']}")
        cursor = start_utc
        for r in db.execute("SELECT window_start_utc s, window_end_utc e, status FROM x_query_runs WHERE query_hash=? AND window_end_utc>? AND window_start_utc<? ORDER BY window_start_utc", (qh, start_utc, end_utc)):
            if r["s"] > cursor:
                gaps.append({"watch_id": a["watch_id"], "from": cursor, "to": r["s"], "status": "NOT_RUN"})
            if r["status"] not in ("COMPLETE", "COMPLETE_DECLARED", "PLANNED_SAMPLE"):
                gaps.append({"watch_id": a["watch_id"], "from": r["s"], "to": r["e"], "status": r["status"]})
            else:
                kinds.add(r["status"])
            cursor = max(cursor, r["e"])
        if cursor < end_utc:
            gaps.append({"watch_id": a["watch_id"], "from": cursor, "to": end_utc, "status": "NOT_RUN"})
    if not n:
        return {"status": "MISSING", "reason": "NO_WATCHED_AUTHOR_READY", "gaps": []}
    if gaps:
        whole = all(g["status"] == "NOT_RUN" and g["from"] <= start_utc and g["to"] >= end_utc for g in gaps) and len(gaps) == n
        return {"status": "MISSING" if whole else "PARTIAL", "reason": "GAPS", "gaps": gaps}
    return {"status": "PLANNED_SAMPLE", "reason": None, "gaps": []}                 # one page, or a few, per author: always a planned sample


def summary(db, cfg: dict) -> dict:
    """Counts for the page and the status command. Never says 'followed' or 'collected' for something that is not."""
    rows = authors(db)
    alloc = ((cfg.get("allocation") or {}).get("recent") or {}).get("sub_pools") or {}
    vers = [dict(zip(("version", "imported_at_utc", "seed_sha256", "accounts", "added", "merged"), r)) for r in
            (db.execute("SELECT version, imported_at_utc, seed_sha256, accounts, added, merged FROM x_watch_versions ORDER BY imported_at_utc") if exists(db) else [])]
    return {"imported": bool(rows), "versions": vers, "selected_at_utc": vers[0]["imported_at_utc"] if vers else None, "version_tag": version_tag(db) if rows else None,
            "logical_authors": len(rows), "with_handle": sum(1 for a in rows if a["handle"]), "blocked_ambiguous": sum(1 for a in rows if a["identity_status"] in BLOCKED),
            "mismatch": sum(1 for a in rows if a["identity_status"] == "AUTHOR_MISMATCH"), "enabled": sum(1 for a in rows if a["enabled"]),
            "ready_for_queries": sum(1 for a in rows if ready(a)), "stable_ids_bound": sum(1 for a in rows if a["stable_author_id"]),
            "collected_at_least_once": sum(1 for a in rows if a["last_success_at_utc"]),
            "scope": "PLATFORM_READ_ONLY_MONITORING: nothing is followed on X; no X password is used",
            "selection_note": "the list was chosen on its import date; results for earlier dates are a panel of authors selected afterwards",
            "suballocation": alloc}
