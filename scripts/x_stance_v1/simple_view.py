"""The plain view of model judgements: per target, how many posts were judged bullish / bearish / neutral, and
everything that is NOT one of those three, each under its own name.

  denominator of the three shares = items of that target that were classified successfully AND are relevant
  irrelevant / classification failed / cut by a rule before the model / waiting = counted separately, never as neutral

Two shares side by side, because they answer different questions:
  post share     every judged post counts once (an account that posts ten times counts ten times)
  account share  every account counts once, by the majority of its own judged posts (a tie is neutral)

The like floor and the other sample rules are applied BEFORE this (they decide which items are in the sample); the
model never sees likes, followers or weights. Everything is per inference identity, so two classification versions
can be compared without ever being mixed. Read-only. Standard library only.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict

from . import simple, stance

LABELS = ("bullish", "bearish", "neutral")
NOTE = "模型判断，待人工验证"


def identities(db) -> list[dict]:
    """Every classification version that has labels in this store, newest first."""
    try:
        rows = db.execute("SELECT inference_identity i, COUNT(*) n, MIN(inference_completed_at_utc) a, MAX(inference_completed_at_utc) b, MAX(model_id) m "
                          "FROM x_stance_labels WHERE inference_identity IS NOT NULL GROUP BY 1 ORDER BY b DESC").fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for r in rows:
        d = db.execute("SELECT payload_json FROM x_inference_identities WHERE identity=?", (r["i"],)).fetchone() if _has(db, "x_inference_identities") else None
        dec = (json.loads(d[0]).get("declared") or {}) if d else {}
        out.append({"identity": r["i"], "short": r["i"][:12], "labels": r["n"], "first_completed_at_utc": r["a"], "last_completed_at_utc": r["b"], "model_id": r["m"],
                    "prompt_version": dec.get("prompt_version"), "contract": dec.get("contract") or "v2"})
    return out


def _has(db, table: str) -> bool:
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _outcome(pre: str, lab) -> str:
    if pre != "PENDING":
        return "cut_by_rule"                                     # e.g. the text is longer than the limit: recorded as cut, never judged
    if lab is None:
        return "pending"
    q = lab["quality_status"]
    if q == "OK" and lab["label"] in LABELS:
        return lab["label"]
    if q == "IRRELEVANT":
        return "irrelevant"
    if q in simple.FAILED:
        return "failed"
    return "not_judged"                                          # older contract: context missing, conflicting ...


def rows(db, cfg: dict, identity: str, versions=None) -> list[dict]:
    """One row per (post, topic, target) of the sample, with its outcome under `identity`."""
    labs = {}
    for l in db.execute("SELECT provider, post_id, topic_id, target_id, label, quality_status, evidence_text, reason_short, inference_completed_at_utc FROM x_stance_labels "
                        "WHERE inference_identity=? ORDER BY label_written_at_utc", (identity,)):
        labs[(l["provider"], l["post_id"], l["topic_id"], l["target_id"])] = l
    out = []
    for r in stance.work_items(db, cfg, versions):
        lab = labs.get((r["provider"], r["post_id"], r["topic_id"], r["target_id"]))
        out.append({"provider": r["provider"], "post_id": r["post_id"], "topic_id": r["topic_id"], "target_id": r["target_id"], "author_id": r["author_id"], "lang": r["lang"] or "und",
                    "created_at_utc": r["created_at_utc"], "sample_day": r["sample_day"], "queue": r["queue"], "source_url": r["source_url"], "cut_reason": r["pre"] if r["pre"] != "PENDING" else None,
                    "outcome": _outcome(r["pre"], lab), "reason": lab["reason_short"] if lab else None, "evidence": lab["evidence_text"] if lab else None,
                    "completed_at_utc": lab["inference_completed_at_utc"] if lab else None})
    return out


def _shares(c: Counter) -> dict:
    n = sum(c[k] for k in LABELS)
    return {"n": n, **{k: c[k] for k in LABELS}, **{k + "_share": (round(c[k] / n, 4) if n else None) for k in LABELS}}


def summarize(rs: list[dict], names: dict) -> list[dict]:
    by_target = defaultdict(list)
    for r in rs:
        by_target[r["target_id"]].append(r)
    out = []
    for target, items in sorted(by_target.items()):
        c = Counter(r["outcome"] for r in items)
        votes = defaultdict(Counter)
        for r in items:
            if r["outcome"] in LABELS:
                votes[r["author_id"]][r["outcome"]] += 1
        acc = Counter()
        for a, v in votes.items():
            top = v.most_common()
            acc["neutral" if len(top) > 1 and top[0][1] == top[1][1] else top[0][0]] += 1
        total = len(items)
        processed = total - c["pending"]
        out.append({"target_id": target, "target": names.get(target) or target, "items_in_sample": total,
                    "posts": _shares(c), "accounts": _shares(acc),
                    "irrelevant": c["irrelevant"], "failed": c["failed"], "not_judged": c["not_judged"], "cut_by_rule": c["cut_by_rule"], "pending": c["pending"],
                    "processed": processed, "progress": round(processed / total, 4) if total else None, "complete": c["pending"] == 0,
                    "denominator": "items of this target that were classified successfully and are relevant",
                    "scope_note": None if c["pending"] == 0 else "已分类样本占比：只代表已分类的部分，不是整天或全市场的情绪"})
    return out


def view(db, cfg: dict, identity: str | None = None, versions=None, *, max_items: int = 400) -> dict:
    """What the page shows. `identity` may be a full hash or a prefix; None is the active configuration's."""
    active = stance.identity_hash(cfg)
    ids = identities(db)
    chosen = active
    if identity:
        m = [i["identity"] for i in ids if i["identity"].startswith(identity)]
        chosen = m[0] if len(m) == 1 else active
    names = simple.target_names(cfg["classifier"])
    rs = rows(db, cfg, chosen, versions)
    done = [r for r in rs if r["outcome"] != "pending"]
    done.sort(key=lambda r: (r["completed_at_utc"] or "", r["created_at_utc"]), reverse=True)
    items = [{"post_id": r["post_id"], "target_id": r["target_id"], "target": names.get(r["target_id"]) or r["target_id"], "outcome": r["outcome"], "reason": r["reason"],
              "evidence": r["evidence"], "source_url": r["source_url"], "created_at_utc": r["created_at_utc"], "lang": r["lang"], "cut_reason": r["cut_reason"]} for r in done[:max_items]]
    c = Counter(r["outcome"] for r in rs)
    return {"note": NOTE, "identity": chosen, "identity_is_active": chosen == active, "active_identity": active, "identities": ids,
            "active_profile": cfg.get("_classifier_profile") or "default", "model_id": next((i["model_id"] for i in ids if i["identity"] == chosen), cfg["classifier"].get("model_id") if chosen == active else None),
            "sample_rule": "样本先按既有规则筛选（点赞下限等）；模型看不到点赞数、粉丝数和作者权重",
            "totals": {"items_in_sample": len(rs), **{k: c[k] for k in LABELS}, "irrelevant": c["irrelevant"], "failed": c["failed"], "not_judged": c["not_judged"],
                       "cut_by_rule": c["cut_by_rule"], "pending": c["pending"], "processed": len(rs) - c["pending"],
                       "progress": round((len(rs) - c["pending"]) / len(rs), 4) if rs else None},
            "by_queue": {q: dict(Counter(r["outcome"] for r in rs if r["queue"] == q)) for q in ("recent", "history")},
            "targets": summarize(rs, names), "items": items, "items_shown": len(items), "items_total": len(done)}
