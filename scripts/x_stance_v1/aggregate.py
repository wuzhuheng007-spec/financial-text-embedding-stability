"""Shares by account. One account = one vote per topic/target/window: its last valid, relevant, labelled post
before the window end (ordered by created_at, then post id). "Account" is an identifiable author id in the retrieved
sample; it is neither a person nor a share of all X users.

A = B + E + N. Shares are B/A, E/A, N/A; A = 0 gives None for all three (not 0 %, not neutral). The state is
UNKNOWN whenever the sample is small, missing or known to be partial; UNKNOWN is never folded into neutral.
"""
from __future__ import annotations

from datetime import datetime

from . import store
from .config import LABELS


def _order(r) -> tuple:
    pid = r["post_id"]
    return (r["created_at_utc"], (0, int(pid), "") if pid.isdigit() else (1, 0, pid))


def _shares(counts: dict) -> dict:
    a = sum(counts[k] for k in LABELS)
    return {k: (counts[k] / a if a else None) for k in LABELS}


def _state(counts: dict, *, collection_status: str, threshold: int, pending: int = 0) -> tuple[str, str | None]:
    a = sum(counts.values())
    if pending:                                              # a day that is not fully classified has no formal state
        return "UNKNOWN", "CLASSIFICATION_INCOMPLETE"
    if collection_status not in ("COMPLETE", "COMPLETE_DECLARED", "PLANNED_SAMPLE"):
        return "UNKNOWN", f"COLLECTION_{collection_status}"
    if a == 0:
        return "UNKNOWN", "NO_VALID_ACCOUNTS"
    if a < threshold:
        return "UNKNOWN", "LOW_SAMPLE"
    top = max(counts.values())
    leaders = [k for k in LABELS if counts[k] == top]
    return (leaders[0] if len(leaders) == 1 else "neutral"), (None if len(leaders) == 1 else "TIE_RESOLVED_TO_NEUTRAL")


def _act_sql(act: dict | None, alias: str = "l") -> tuple[str, tuple]:
    """SQL for "labels of the active classifier only". None = no restriction (unit tests of the arithmetic)."""
    if act is None:
        return "", ()
    if not (act.get("model_id") and act.get("model_revision") and act.get("prompt_hash")):
        return f" AND {alias}.post_id IS NULL AND 0 ", ()          # nothing is pinned: no label counts
    return f" AND {alias}.model_id=? AND {alias}.model_revision=? AND {alias}.prompt_hash=? ", (act["model_id"], act["model_revision"], act["prompt_hash"])


def rows_for_window(db, *, topic_id: str, target_id: str, start: datetime, end: datetime, mode: str, model_revision: str | None = None, act: dict | None = None) -> tuple[list, dict]:
    """Posts of the window with their newest label row FROM THE ACTIVE CLASSIFIER (model, revision and prompt all
    match `act`); labels of any other model are not mixed in. LIVE keeps only posts both created before `end` AND
    first received by `end`; a late arrival is counted and left out. The other modes group by creation time."""
    s, e = store.iso(start), store.iso(end)
    extra, args = _act_sql(act)
    if act is not None and not args:
        return [], {"late_arrival": 0, "retrospective_rows_in_live": 0}
    rows = db.execute(
        "SELECT p.provider, p.post_id, p.author_id, p.created_at_utc, p.first_seen_at_utc, p.ingest_mode, p.lang, p.text_hash, p.kind, p.source_url,"
        " p.author_text, l.label, l.quality_status, l.inferred_at_utc, l.model_revision, l.reason_short, l.evidence_text "
        "FROM x_stance_labels l JOIN x_posts p USING(provider, post_id) "
        "WHERE l.topic_id=? AND l.target_id=? AND p.created_at_utc>=? AND p.created_at_utc<? "
        + ("AND l.model_revision=? " if model_revision else "") + extra + "ORDER BY l.inferred_at_utc",
        (topic_id, target_id, s, e) + ((model_revision,) if model_revision else ()) + args).fetchall()
    latest = {}
    for r in rows:                                          # one label row per post: the most recently inferred
        latest[(r["provider"], r["post_id"])] = r
    kept, dropped = [], {"late_arrival": 0, "retrospective_rows_in_live": 0}
    for r in latest.values():
        if mode == "LIVE":
            if r["ingest_mode"] != "LIVE":
                dropped["retrospective_rows_in_live"] += 1; continue
            if r["first_seen_at_utc"] > e:
                dropped["late_arrival"] += 1; continue
        kept.append(r)
    return kept, dropped


def progress(db, *, topic_id: str, target_id: str, start: datetime, end: datetime, vocab_version: str, act: dict | None = None) -> dict:
    """How much of the window's matched sample has a label from the active classifier (or was settled without a
    model: retweet, link only). Counted on post/topic pairs and on accounts."""
    s, e = store.iso(start), store.iso(end)
    extra, args = _act_sql(act, "x")
    inner = "SELECT DISTINCT x.provider, x.post_id, x.topic_id, x.target_id FROM x_stance_labels x WHERE 1=1" + (extra if (act is None or args) else " AND 0")
    r = db.execute(
        "SELECT COUNT(*), COUNT(DISTINCT p.author_id), SUM(CASE WHEN l.post_id IS NOT NULL OR t.quality!='PENDING' THEN 1 ELSE 0 END) "
        "FROM x_post_topics t JOIN x_posts p USING(provider, post_id) "
        f"LEFT JOIN ({inner}) l ON l.provider=t.provider AND l.post_id=t.post_id AND l.topic_id=t.topic_id AND l.target_id=t.target_id "
        "WHERE t.topic_version=? AND t.topic_id=? AND t.target_id=? AND p.created_at_utc>=? AND p.created_at_utc<?",
        args + (vocab_version, topic_id, target_id, s, e)).fetchone()
    total, done = r[0] or 0, r[2] or 0
    return {"matched_posts": total, "sample_accounts": r[1] or 0, "classified_or_prefiltered": done, "pending": total - done,
            "share_done": (done / total) if total else None}


def aggregate(rows: list, *, collection_status: str, threshold: int, lang_scope: str = "all", pending: int = 0) -> dict:
    rows = [r for r in rows if lang_scope == "all" or (r["lang"] or "") == lang_scope]
    valid = [r for r in rows if r["label"] in LABELS and r["quality_status"] == "OK"]
    excluded, ok = {}, {(r["provider"], r["post_id"]) for r in valid}
    for r in rows:
        if (r["provider"], r["post_id"]) not in ok:
            excluded[r["quality_status"]] = excluded.get(r["quality_status"], 0) + 1
    last = {}
    for r in sorted(valid, key=_order):
        last[r["author_id"]] = r
    by_author = {k: sum(1 for r in last.values() if r["label"] == k) for k in LABELS}
    by_post = {k: sum(1 for r in valid if r["label"] == k) for k in LABELS}
    # copies: identical text from several accounts. Sensitivity keeps the earliest account of each copy cluster.
    first_of_text, copies = {}, {}
    for r in sorted(last.values(), key=_order):
        first_of_text.setdefault(r["text_hash"], r["author_id"])
        copies[r["text_hash"]] = copies.get(r["text_hash"], 0) + 1
    no_copy = {k: sum(1 for r in last.values() if r["label"] == k and first_of_text[r["text_hash"]] == r["author_id"]) for k in LABELS}
    langs = {}
    for r in last.values():
        langs[r["lang"] or "und"] = langs.get(r["lang"] or "und", 0) + 1
    state, why = _state(by_author, collection_status=collection_status, threshold=threshold, pending=pending)
    a = sum(by_author.values())
    return {"lang_scope": lang_scope, "accounts": by_author, "accounts_total": a, "shares": _shares(by_author),
            "posts": by_post, "posts_total": sum(by_post.values()), "shares_by_post": _shares(by_post),
            "share_difference_post_minus_account": {k: (None if not a else _shares(by_post)[k] - _shares(by_author)[k]) for k in LABELS},
            "excluding_copies": {"accounts": no_copy, "shares": _shares(no_copy), "copy_clusters": sum(1 for v in copies.values() if v > 1),
                                 "note": "identical text only; not a bot detector"},
            "excluded_or_unjudged": excluded, "unjudged_total": sum(excluded.values()), "language_mix_accounts": langs,
            "low_sample": 0 < a < threshold, "low_sample_threshold": threshold, "collection_status": collection_status,
            "state": state, "state_reason": why, "population": "accounts in the retrieved sample",
            "vote_post_ids": sorted(r["post_id"] for r in last.values())}


def votes(rows: list, lang_scope: str = "all") -> list:
    """The vote posts themselves (one per account), for the narrative step."""
    rows = [r for r in rows if (lang_scope == "all" or (r["lang"] or "") == lang_scope) and r["label"] in LABELS and r["quality_status"] == "OK"]
    last = {}
    for r in sorted(rows, key=_order):
        last[r["author_id"]] = r
    return sorted(last.values(), key=_order)


def commit(db, *, window_kind: str, window_label: str, start: datetime, end: datetime, topic_id: str, target_id: str, mode: str,
           result: dict, rows: list, now: datetime, act: dict | None = None) -> dict:
    """Appends an immutable snapshot revision when the evidence changed, or returns the existing one unchanged.
    The commit time of a revision is set once and is never moved by a rebuild. See snapshots.py."""
    from . import snapshots
    return snapshots.commit(db, window_kind=window_kind, window_label=window_label, start=start, end=end, topic_id=topic_id, target_id=target_id,
                            mode=mode, result=result, rows=rows, now=now, act=act)
