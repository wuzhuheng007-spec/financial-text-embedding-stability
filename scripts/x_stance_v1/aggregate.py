"""Shares by account. One account = one vote per topic/target/window: its last valid, relevant, labelled post
before the window end (ordered by created_at, then post id). "Account" is an identifiable author id in the retrieved
sample; it is neither a person nor a share of all X users.

A = B + E + N. Shares are B/A, E/A, N/A; A = 0 gives None for all three (not 0 %, not neutral). The state is
UNKNOWN whenever the sample is small, missing or known to be partial; UNKNOWN is never folded into neutral.

Two things decide what may be counted, and both are explicit:
  * the SAMPLE version (vocabulary + account rules): only posts that are members of it in x_post_topics;
  * the INFERENCE identity: only labels produced under the active, complete inference configuration.

With account rules the vote of an account carries a weight (follower tier or a named account). The weighted
shares are then the headline and the state; the equal-weight shares are always reported next to them.
"""
from __future__ import annotations

from datetime import datetime

from . import sample, store
from .config import LABELS


def _order(r) -> tuple:
    pid = r["post_id"]
    return (r["created_at_utc"], (0, int(pid), "") if pid.isdigit() else (1, 0, pid))


def _shares(counts: dict) -> dict:
    a = sum(counts[k] for k in LABELS)
    return {k: (counts[k] / a if a else None) for k in LABELS}


def _state(counts: dict, *, collection_status: str, threshold: int, pending: int = 0, accounts: int | None = None) -> tuple[str, str | None]:
    """`counts` decides the leader (weighted when rules are on); `accounts` is the number of voting accounts and is
    what the low-sample rule looks at, so a few heavy accounts cannot pass for a sample."""
    a = sum(counts.values()) if accounts is None else accounts
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
    """SQL for "labels of the active classifier only". None = no restriction (unit tests of the arithmetic).
    With a full inference identity the match is on that identity; a label without one (legacy) never matches."""
    if act is None:
        return "", ()
    if not (act.get("model_id") and act.get("model_revision") and act.get("prompt_hash")):
        return f" AND {alias}.post_id IS NULL AND 0 ", ()          # nothing is pinned: no label counts
    if act.get("identity"):
        return f" AND {alias}.inference_identity=? ", (act["identity"],)
    return f" AND {alias}.model_id=? AND {alias}.model_revision=? AND {alias}.prompt_hash=? ", (act["model_id"], act["model_revision"], act["prompt_hash"])


def rows_for_window(db, *, topic_id: str, target_id: str, start: datetime, end: datetime, mode: str, model_revision: str | None = None,
                    act: dict | None = None, sample_version: str | None = None) -> tuple[list, dict]:
    """Posts of the window that are MEMBERS of the sample version, with their newest label row from the active
    inference identity. A post that the vocabulary or the account rules of this version do not admit is not
    returned, whatever labels it has. LIVE keeps only posts both created before `end` AND first received by `end`;
    a late arrival is counted and left out. The other modes group by creation time.

    The sample version comes from `sample_version` or from `act`. Only the arithmetic unit tests call this with
    neither, and then every labelled post of the window is returned."""
    s, e = store.iso(start), store.iso(end)
    extra, args = _act_sql(act)
    if act is not None and not args:
        return [], {"late_arrival": 0, "retrospective_rows_in_live": 0}
    sv = sample_version or (act or {}).get("sample_version")
    member = ("JOIN x_post_topics t ON t.provider=l.provider AND t.post_id=l.post_id AND t.topic_id=l.topic_id AND t.target_id=l.target_id "
              "AND t.topic_version=? AND t.quality NOT LIKE 'FILTERED_%' ") if sv else ""
    rows = db.execute(
        "SELECT p.provider, p.post_id, p.author_id, p.created_at_utc, p.first_seen_at_utc, p.ingest_mode, p.lang, p.text_hash, p.kind, p.source_url,"
        " p.author_text, p.engagement_snapshot_json, l.label, l.quality_status, l.inferred_at_utc, l.inference_completed_at_utc, l.label_written_at_utc,"
        " l.inference_identity, l.model_revision, l.reason_short, l.evidence_text "
        "FROM x_stance_labels l JOIN x_posts p USING(provider, post_id) " + member +
        "WHERE l.topic_id=? AND l.target_id=? AND p.created_at_utc>=? AND p.created_at_utc<? "
        + ("AND l.model_revision=? " if model_revision else "") + extra + "ORDER BY l.inferred_at_utc",
        ((sv,) if sv else ()) + (topic_id, target_id, s, e) + ((model_revision,) if model_revision else ()) + args).fetchall()
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
    """How much of the window's sample has a label from the active inference identity (or was settled without a
    model: retweet, link only). `vocab_version` is the sample version, i.e. the key of x_post_topics. Posts the
    account rules left out are counted separately and are not part of the total."""
    s, e = store.iso(start), store.iso(end)
    extra, args = _act_sql(act, "x")
    inner = "SELECT DISTINCT x.provider, x.post_id, x.topic_id, x.target_id FROM x_stance_labels x WHERE 1=1" + (extra if (act is None or args) else " AND 0")
    r = db.execute(
        "SELECT SUM(CASE WHEN t.quality NOT LIKE 'FILTERED_%' THEN 1 ELSE 0 END), COUNT(DISTINCT CASE WHEN t.quality NOT LIKE 'FILTERED_%' THEN p.author_id END), "
        "SUM(CASE WHEN t.quality NOT LIKE 'FILTERED_%' AND (l.post_id IS NOT NULL OR t.quality!='PENDING') THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN t.quality='FILTERED_LOW_LIKES' THEN 1 ELSE 0 END), SUM(CASE WHEN t.quality='FILTERED_LIKES_UNKNOWN' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN t.quality='FILTERED_TARGET_NOT_NAMED' THEN 1 ELSE 0 END) "
        "FROM x_post_topics t JOIN x_posts p USING(provider, post_id) "
        f"LEFT JOIN ({inner}) l ON l.provider=t.provider AND l.post_id=t.post_id AND l.topic_id=t.topic_id AND l.target_id=t.target_id "
        "WHERE t.topic_version=? AND t.topic_id=? AND t.target_id=? AND p.created_at_utc>=? AND p.created_at_utc<?",
        args + (vocab_version, topic_id, target_id, s, e)).fetchone()
    total, done = r[0] or 0, r[2] or 0
    return {"matched_posts": total, "sample_accounts": r[1] or 0, "classified_or_prefiltered": done, "pending": total - done,
            "share_done": (done / total) if total else None, "filtered_low_likes": r[3] or 0, "filtered_likes_unknown": r[4] or 0, "filtered_target_not_named": r[5] or 0, "sample_version": vocab_version}


def aggregate(rows: list, *, collection_status: str, threshold: int, lang_scope: str = "all", pending: int = 0, cfg: dict | None = None) -> dict:
    """`cfg` carries the account rules. Without it (or with rules off) every account counts 1."""
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
    a = sum(by_author.values())
    weighted_on = bool(cfg and sample.rules(cfg))
    w = {r["post_id"]: sample.weight(r, cfg) for r in last.values()} if weighted_on else {}
    by_weight = {k: sum(w[r["post_id"]][0] for r in last.values() if r["label"] == k) for k in LABELS} if weighted_on else None
    lead = by_weight if weighted_on else by_author
    state, why = _state(lead, collection_status=collection_status, threshold=threshold, pending=pending, accounts=a)
    without_top = None
    if weighted_on and len(last) > 1:                          # sensitivity: drop the single heaviest vote and count again
        top = max(sorted(last.values(), key=_order), key=lambda r: w[r["post_id"]][0])
        rest = {k: sum(w[r["post_id"]][0] for r in last.values() if r["label"] == k and r is not top) for k in LABELS}
        lead_rest = [k for k in LABELS if rest[k] == max(rest.values())]
        without_top = {"removed_weight": w[top["post_id"]][0], "removed_vote": top["label"], "weighted_votes": rest, "shares": _shares(rest),
                       "leader": lead_rest[0] if len(lead_rest) == 1 else "TIE", "leader_changes": (lead_rest[0] if len(lead_rest) == 1 else "TIE") != (state if state in LABELS else None) if state in LABELS else None}
    basis = {}
    for wt, b in w.values():
        key = b if b.startswith("NAMED:") or b.startswith("FOLLOWERS") else b
        basis.setdefault(key, {"accounts": 0, "weight": wt})["accounts"] += 1
    out = {"lang_scope": lang_scope, "accounts": by_author, "accounts_total": a, "shares": _shares(lead), "shares_equal_weight": _shares(by_author),
           "posts": by_post, "posts_total": sum(by_post.values()), "shares_by_post": _shares(by_post),
           "share_difference_post_minus_account": {k: (None if not a else _shares(by_post)[k] - _shares(by_author)[k]) for k in LABELS},
           "excluding_copies": {"accounts": no_copy, "shares": _shares(no_copy), "copy_clusters": sum(1 for v in copies.values() if v > 1),
                                "note": "identical text only; not a bot detector"},
           "excluded_or_unjudged": excluded, "unjudged_total": sum(excluded.values()), "language_mix_accounts": langs,
           "low_sample": 0 < a < threshold, "low_sample_threshold": threshold, "collection_status": collection_status,
           "state": state, "state_reason": why, "population": "accounts in the retrieved sample",
           "vote_post_ids": sorted(r["post_id"] for r in last.values()),
           "weighting": {"enabled": weighted_on, "rules_version": sample.rules_version(cfg) if weighted_on else None,
                         "weighted_votes": by_weight, "weight_total": sum(by_weight.values()) if weighted_on else None, "by_basis": basis,
                         "share_difference_weighted_minus_equal": ({k: (None if not a else _shares(by_weight)[k] - _shares(by_author)[k]) for k in LABELS} if weighted_on else None),
                         "largest_single_account_share_of_weight": (max((x[0] for x in w.values()), default=0) / sum(by_weight.values()) if weighted_on and sum(by_weight.values()) else None),
                         "voting_accounts": a, "without_heaviest_account": without_top,
                         "note": "weights use follower counts as reported at fetch time; the equal-weight shares are in shares_equal_weight"},
           "vote_weights": {pid: x[0] for pid, x in sorted(w.items())}}
    return out


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
