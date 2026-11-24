"""The material behind one day of one panel: every sample post of that session window with its model judgement,
reason, evidence and link, next to the stored daily summary and the day's main views.

    model judgement (x_stance_labels)  ->  daily summary (x_daily_sentiment / x_panel_sentiment)  ->  main views

The three are compared here on the SAME material: the posts are selected with the function the build uses
(aggregate.rows_for_window, same window, same sample version, same inference identity as the stored row), the
counts are recomputed from them, and the result says whether they equal the stored row. Read-only.

Three times are kept apart for every post: when it was published, when this system first received it, and when
the model finished. A historical window was fetched and classified later; it is marked as such and is never a
signal that was available on that day.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from pathlib import Path

from . import aggregate, config as cfgmod, sample, simple, simple_view, snapshots, stance, store, vocab
from .config import LABELS
from .sessions import Calendar

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
EMPTY = {"NO_SAMPLE": "无样本", "PENDING": "待分类", "NO_VALID_LABEL": "没有有效标签"}


def empty_reason(classification: dict | None, posts_total: int) -> str | None:
    """Why a day has no shares: nothing was sampled, items still wait for the model, or nothing judged was usable."""
    c = classification or {}
    if posts_total:
        return None
    if not c.get("matched_posts"):
        return "NO_SAMPLE"
    if c.get("pending"):
        return "PENDING"
    return "NO_VALID_LABEL"


def _who(r) -> dict:
    """Author handle as stored with the post at fetch time. (Same reading as narratives._who; that module needs numpy,
    which the web process does not load.)"""
    try:
        e = json.loads(r["engagement_snapshot_json"] or "{}")
    except (KeyError, IndexError, ValueError, TypeError):
        e = {}
    return {"author_id": str(r["author_id"]), "author_handle": e.get("authorUserName")}


def _stored(db, scope: str, target_id: str, label: str):
    if scope == "keyword":
        return db.execute("SELECT topic_id, mode, payload_json FROM x_daily_sentiment WHERE window_kind='session' AND lang_scope='all' AND target_id=? AND window_label=?",
                          (target_id, label)).fetchone()
    return db.execute("SELECT topic_id, mode, payload_json FROM x_panel_sentiment WHERE scope=? AND window_kind='session' AND lang_scope='all' AND target_id=? AND window_label=?",
                      (scope, target_id, label)).fetchone()


def _outcome(r) -> str:
    if r["quality_status"] == "OK" and r["label"] in LABELS:
        return r["label"]
    if r["quality_status"] == "IRRELEVANT":
        return "irrelevant"
    return "failed" if r["quality_status"] in simple.FAILED else "not_judged"


def read_day(cfg: dict, db_path: Path | None = None, *, scope: str, target_id: str, window_label: str, max_items: int = 400) -> dict:
    if scope not in sample.SCOPES or not DATE_RE.fullmatch(window_label or "") or not target_id or len(target_id) > 64:
        raise ValueError("bad day request")
    p = Path(db_path or store.db_path())
    out = {"scope": scope, "target_id": target_id, "window_label": window_label, "state": "NO_STORE", "note": simple_view.NOTE, "items": [], "narratives": []}
    if not p.exists():
        return out
    db = store.connect(p, readonly=True)
    try:
        cfg = cfgmod.apply_active(db, cfg)
        cfg = vocab.with_topics(sample.attach_named(db, cfg), vocab.active(db, cfg))
        row = _stored(db, scope, target_id, window_label)
        if row is None:
            out["state"] = "NO_SUMMARY_ROW"
            return out
        stored = json.loads(row["payload_json"])
        sact = stored.get("activation") or {}
        scfg = next((c for c in sample.scoped_configs(db, cfg) if sample.scope_of(c) == scope), None)
        if scfg is None:
            out["state"] = "PANEL_NOT_AVAILABLE"
            return out
        wcfg = sample.config_for_date(db, scfg, date.fromisoformat(window_label))
        act_now = snapshots.activation(wcfg)
        # the stored row's own identity and sample version: the material is the one that row was built from
        act = {**act_now, **{k: sact[k] for k in ("identity", "model_id", "model_revision", "prompt_hash", "sample_version") if sact.get(k)}}
        c = cfg["time"]["exchange_calendar"]
        cal = Calendar.load(store.REPO_ROOT / c["path"], c["sha256"])
        i = cal.index(window_label)
        win = cal.session_window(i) if i is not None else None
        if win is None:
            out["state"] = "NOT_A_SESSION"
            return out
        rows, dropped = aggregate.rows_for_window(db, topic_id=row["topic_id"], target_id=target_id, start=win[0], end=win[1], mode=row["mode"], act=act)
        prog = aggregate.progress(db, topic_id=row["topic_id"], target_id=target_id, start=win[0], end=win[1], vocab_version=act["sample_version"], act=act)
        res = aggregate.aggregate(rows, collection_status=stored.get("collection_status") or "MISSING", threshold=stored.get("low_sample_threshold") or 30,
                                  pending=prog["pending"], cfg=wcfg)
        voters = {(r["provider"], r["post_id"]) for r in aggregate.votes(rows)}
        names = simple.target_names(cfg["classifier"])
        items = []
        for r in sorted(rows, key=lambda r: (r["created_at_utc"], r["post_id"]), reverse=True):
            who = _who(r)
            items.append({"post_id": r["post_id"], "source_url": r["source_url"], "author_handle": who["author_handle"], "outcome": _outcome(r), "quality_status": r["quality_status"],
                          "reason": r["reason_short"], "evidence": r["evidence_text"], "lang": r["lang"] or "und",
                          "counts_as_account_vote": (r["provider"], r["post_id"]) in voters,
                          "published_at_utc": r["created_at_utc"], "first_received_at_utc": r["first_seen_at_utc"], "ingest_mode": r["ingest_mode"],
                          "model_completed_at_utc": r["inference_completed_at_utc"] or r["inferred_at_utc"]})
        same = {k: res[k] == stored.get(k) for k in ("posts", "accounts", "posts_total", "accounts_total", "excluded_or_unjudged")}
        same["vote_posts"] = res["vote_post_ids"] == stored.get("vote_post_ids")
        if scope == "keyword":
            nar = db.execute("SELECT payload_json FROM x_narratives WHERE window_kind='session' AND window_label=? AND target_id=? ORDER BY rank", (window_label, target_id)).fetchall()
        else:
            nar = db.execute("SELECT payload_json FROM x_panel_narratives WHERE scope=? AND window_kind='session' AND window_label=? AND target_id=? ORDER BY rank", (scope, window_label, target_id)).fetchall()
        nars, in_day = [], {x["post_id"]: x for x in items}
        for n in nar:
            n = json.loads(n[0])
            ev = [{**e, "in_this_days_material": e["post_id"] in in_day, "stance_matches_label": (in_day.get(e["post_id"]) or {}).get("outcome") == e.get("stance")} for e in n.get("evidence") or []]
            nars.append({"rank": n["rank"], "title": n["title"], "supporting_accounts": n["supporting_accounts"], "share_of_sample_accounts": n.get("share_of_sample_accounts"),
                         "stance_composition": n["stance_composition"], "evidence": ev})
        nar_ok = all(e["in_this_days_material"] and e["stance_matches_label"] for n in nars for e in n["evidence"])
        mode = row["mode"]
        out.update({
            "state": "READY", "topic_id": row["topic_id"], "target": names.get(target_id) or target_id, "mode": mode,
            "window_start_utc": store.iso(win[0]), "window_end_utc": store.iso(win[1]),
            "backfilled": mode == "RETROSPECTIVE_VISIBLE_NOW",
            "timing_note": ("历史回填：这些帖子是后来取得并分类的，不代表当天可用的信号" if mode == "RETROSPECTIVE_VISIBLE_NOW" else "收盘后批量采集并分类；以实际完成时间为准，不是盘中信号"),
            "identity": act.get("identity"), "model_id": act.get("model_id"), "sample_version": act.get("sample_version"),
            "identity_is_active": act.get("identity") == stance.identity_hash(cfg),
            "posts": res["posts"], "posts_total": res["posts_total"], "shares_by_post": res["shares_by_post"],
            "accounts": res["accounts"], "accounts_total": res["accounts_total"], "shares_by_account": res["shares_equal_weight"], "shares_weighted": res["shares"],
            "excluded": res["excluded_or_unjudged"], "classification": prog, "dropped": dropped,
            "empty_reason": empty_reason(prog, res["posts_total"]),
            "collection_status": stored.get("collection_status"), "summary_state": stored.get("state"), "summary_state_reason": stored.get("state_reason"),
            "summary_committed_at_utc": stored.get("signal_committed_at_utc"), "summary_revision": stored.get("revision"),
            "check": {"labels_equal_summary": all(same.values()), "fields": same, "views_evidence_in_material": nar_ok,
                      "stored_posts": stored.get("posts"), "stored_accounts": stored.get("accounts")},
            "representative": {k: [{"post_id": x["post_id"], "source_url": x["source_url"], "author_handle": x["author_handle"], "reason": x["reason"], "evidence": x["evidence"]}
                                   for x in items if x["outcome"] == k and x["counts_as_account_vote"]][:3] for k in LABELS},
            "items": items[:max_items], "items_total": len(items), "narratives": nars})
        return out
    except sqlite3.OperationalError:
        out["state"] = "STORE_NEEDS_UPGRADE"
        return out
    finally:
        db.close()
