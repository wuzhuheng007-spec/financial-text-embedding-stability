"""Which posts belong to a sample, and how much an account's vote counts.

Sample version = vocabulary version + account-rule version. It is the key of x_post_topics, of every derived
series and of the snapshot evidence set, so a post that the vocabulary or the rules in force do not admit can never
reach an aggregate, a view or a return row of that version - whatever labels it may still have from earlier.

Account rules (set by the owner, 2026-11-23):
  * a post whose like count AT FETCH TIME is below `min_likes` is left out of the sample (and is not classified);
  * an account's vote is weighted by its follower tier AT FETCH TIME, or by an explicit weight for a named account.
Both numbers are what the provider reported when the post was first received. They are not historical values: a
post fetched hours after it was written has had less time to collect likes than one fetched months later, so the
filter is not comparable between the daily batch and the historical back-fill. The stance model never sees them.

Standard library only (the web view imports this).
"""
from __future__ import annotations

import json
from datetime import date

from . import store

FILTERED_LOW, FILTERED_UNKNOWN = "FILTERED_LOW_LIKES", "FILTERED_LIKES_UNKNOWN"


def rules(cfg: dict) -> dict | None:
    r = (cfg.get("aggregation") or {}).get("account_rules")
    return r if r and r.get("enabled", True) else None


def rules_version(cfg: dict) -> str | None:
    """Declared name plus a hash of the content: editing any number gives a new version without anyone having to
    remember to rename it."""
    r = rules(cfg)
    if not r:
        return None
    body = {k: r.get(k) for k in ("min_likes", "unknown_likes", "follower_tiers", "named_accounts", "unknown_followers_weight")}
    return f"{r['version']}-{store.sha(json.dumps(body, sort_keys=True, ensure_ascii=False))[:8]}"


def sample_version(cfg: dict) -> str:
    rv = rules_version(cfg)
    return cfg["topic_vocabulary_version"] + (f"@{rv}" if rv else "")


def engagement(row) -> dict:
    try:
        raw = row["engagement_snapshot_json"]
    except (KeyError, IndexError):
        raw = None
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


def like_filter(row, cfg: dict) -> str | None:
    """None = admitted. Otherwise the reason the post is outside the sample."""
    r = rules(cfg)
    if not r or r.get("min_likes") is None:
        return None
    likes = engagement(row).get("likeCount")
    if not isinstance(likes, int):
        return FILTERED_UNKNOWN if r.get("unknown_likes", "exclude") == "exclude" else None
    return FILTERED_LOW if likes < r["min_likes"] else None


def weight(row, cfg: dict) -> tuple[float, str]:
    """(weight, basis) of the account behind a vote row. Without rules every account counts 1."""
    r = rules(cfg)
    if not r:
        return 1.0, "EQUAL"
    e = engagement(row)
    handle = str(e.get("authorUserName") or "").lower()
    for n in r.get("named_accounts") or []:
        if n.get("weight") is None:
            continue
        if (n.get("author_id") and str(n["author_id"]) == str(row["author_id"])) or (n.get("handle") and handle and str(n["handle"]).lstrip("@").lower() == handle):
            return float(n["weight"]), f"NAMED:{n.get('label') or n.get('handle') or n.get('author_id')}"
    followers = e.get("authorFollowers")
    if not isinstance(followers, int):
        return float(r.get("unknown_followers_weight", 1)), "FOLLOWERS_UNKNOWN"
    for t in sorted(r.get("follower_tiers") or [], key=lambda t: -t["min_followers"]):
        if followers >= t["min_followers"]:
            return float(t["weight"]), f"FOLLOWERS>={t['min_followers']}"
    return 1.0, "BELOW_ALL_TIERS"


def summary(cfg: dict) -> dict:
    r = rules(cfg)
    if not r:
        return {"enabled": False, "version": None}
    named = r.get("named_accounts") or []
    return {"enabled": True, "version": rules_version(cfg), "min_likes": r.get("min_likes"), "unknown_likes": r.get("unknown_likes", "exclude"),
            "follower_tiers": r.get("follower_tiers") or [], "unknown_followers_weight": r.get("unknown_followers_weight", 1),
            "named_accounts": [{"label": n.get("label"), "weight": n.get("weight"), "identified": bool(n.get("author_id") or n.get("handle"))} for n in named],
            "named_accounts_not_identified": sum(1 for n in named if not (n.get("author_id") or n.get("handle"))),
            "basis": "like and follower counts as reported when the post was first fetched; not historical values"}


# ---------- which vocabulary a date was sampled under ----------
def config_for_date(db, cfg: dict, d: date) -> dict:
    """Config (topics + vocabulary version) that a New York sample date is derived under: the historical plan's
    frozen vocabulary inside the historical range, otherwise the version in force on that date."""
    from . import vocab
    try:
        row = db.execute("SELECT value FROM x_meta WHERE key='history_range'").fetchone()
        hr = json.loads(row[0]) if row else None
    except Exception:  # noqa: BLE001 - a store without the table simply has no historical plan
        hr = None
    if hr and hr["first_ny_date"] <= d.isoformat() <= hr["last_ny_date"]:
        plan = db.execute("SELECT plan_json FROM x_sample_plans WHERE job='historical' AND plan_key=?", (f"{hr['first_ny_date']}..{hr['last_ny_date']}",)).fetchone()
        ver = json.loads(plan[0]).get("vocab_version") if plan else None
        if ver:
            v = vocab.get(db, cfg, ver)
            if v:
                return vocab.with_topics(cfg, v)
    return vocab.with_topics(cfg, vocab.active(db, cfg, d))


def live_configs(db, cfg: dict) -> list[dict]:
    """Every vocabulary that still has a role: the one in force now, the historical plan's, and those of the
    frozen recent plans. One config per distinct sample version, current first."""
    from . import vocab
    out, seen = [], set()

    def add(c):
        if sample_version(c) not in seen:
            seen.add(sample_version(c)); out.append(c)
    add(vocab.with_topics(cfg, vocab.active(db, cfg)))
    try:
        plans = db.execute("SELECT plan_json FROM x_sample_plans ORDER BY frozen_at_utc DESC").fetchall()
    except Exception:  # noqa: BLE001
        plans = []
    for p in plans:
        ver = json.loads(p[0]).get("vocab_version")
        v = vocab.get(db, cfg, ver) if ver else None
        if v:
            add(vocab.with_topics(cfg, v))
    return out
