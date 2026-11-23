"""Frozen parameters: loading, structural checks, the list of values that are still null, and the two gates
(paid acquisition, classifier) that stay closed while any of their inputs is missing."""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

LABELS = ("bullish", "bearish", "neutral")
HORIZONS = (1, 3, 5, 10, 20, 30)
KEY_ENV = {"twitterapi_io": "TWITTERAPI_IO_API_KEY"}
DEFAULT_PATH = Path(__file__).resolve().parents[2] / "research" / "x_stance_v1" / "config" / "x_stance_v2_twitterapiio_5000_20261122.json"

# (path into the config, why it matters). A null here is reported, never replaced by a default.
MUST_FREEZE = (
    ("classifier.model_id", "classifier"), ("classifier.immutable_revision", "classifier"),
    ("classifier.prompt_version", "classifier"), ("classifier.temperature", "classifier"),
    ("market.input_version", "market"), ("market.adjustment_semantics", "market"),
    ("validation.holdout_start", "relationship_study"),
)


class ConfigError(ValueError):
    pass


def load(path: str | Path | None = None) -> dict:
    cfg = json.loads(Path(path or DEFAULT_PATH).read_text(encoding="utf-8"))
    validate(cfg)
    return cfg


def config_hash(cfg: dict) -> str:
    """Hash of the config FILE content. Keys starting with "_" are run-time attachments (vocabulary in force,
    panel scope, identities from the private watch list) and are not part of it."""
    cfg = {k: v for k, v in cfg.items() if not k.startswith("_")}
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def get(cfg: dict, dotted: str):
    cur = cfg
    for part in dotted.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else None
    return cur


def validate(cfg: dict) -> None:
    if tuple(cfg.get("stance_labels") or ()) != LABELS:
        raise ConfigError("stance_labels must be exactly bullish, bearish, neutral")
    if tuple(cfg.get("horizons_trading_sessions") or ()) != HORIZONS:
        raise ConfigError("horizons must be exactly 1, 3, 5, 10, 20, 30 trading sessions")
    if cfg.get("automatic_paid_fallback") is not False:
        raise ConfigError("automatic_paid_fallback must be false: one primary provider, no silent paid fallback")
    acq = cfg.get("acquisition") or {}
    if acq.get("sort") != "Latest":
        raise ConfigError("history sample must use Latest ordering")
    if acq.get("historical_min_likes_filter") is not None:
        raise ConfigError("an engagement filter must not select the historical sample")
    if (cfg.get("aggregation") or {}).get("engagement_weighting"):
        raise ConfigError("the primary shares are never weighted by engagement")
    if cfg.get("provider_candidate") not in KEY_ENV:
        raise ConfigError("unknown provider_candidate")
    ids = [t.get("id") for t in cfg.get("topics") or []]
    if not ids or len(ids) != len(set(ids)):
        raise ConfigError("topic ids must be present and unique")
    for t in cfg["topics"]:
        kws = t.get("keywords") or []
        if not kws or len(kws) != len(set(kws)):
            raise ConfigError(f"topic {t.get('id')}: keywords must be non-empty and unique")
    b, al, pf, q = cfg.get("budget") or {}, cfg.get("allocation") or {}, cfg.get("preflight") or {}, cfg.get("quote") or {}
    try:
        day_posts, day_usd = int(b["daily_all_jobs_returned_post_cap"]), Decimal(b["daily_all_jobs_cost_cap_usd"])
        pools_posts = sum(int(al[k]["max_returned_posts_per_day"]) for k in ("recent", "historical"))
        pools_usd = sum(Decimal(al[k]["cost_cap_usd_per_day"]) for k in ("recent", "historical"))
        roll = Decimal(b["rolling_all_jobs_cost_cap_usd"])
        per_post = Decimal(q["credits_per_post"]) / Decimal(q["credits_per_usd"])
    except (KeyError, TypeError, ValueError, ArithmeticError):
        raise ConfigError("budget, allocation and quote must be complete: no budget, no request") from None
    if day_posts <= 0 or day_usd <= 0 or roll <= 0:
        raise ConfigError("budget caps must be positive")
    if pools_posts > day_posts or pools_usd > day_usd:
        raise ConfigError("the recent and historical pools together must fit inside the daily cap")
    if al.get("automatic_reallocation") is not False:
        raise ConfigError("pools do not borrow from each other")
    if per_post != Decimal(q["per_post"]) or Decimal(day_posts) * per_post > day_usd:
        raise ConfigError("quote and daily caps are inconsistent")
    if int(pf.get("max_returned_posts", 0)) > day_posts or pf.get("included_in_daily_and_rolling_caps") is not True:
        raise ConfigError("the preflight allowance is part of the daily caps, never extra")
    if (cfg.get("authorization") or {}).get("auto_topup") is not False or (cfg.get("authorization") or {}).get("purchase_subscription") is not False:
        raise ConfigError("automatic top-up and subscription purchase must stay off")
    if (cfg.get("narratives") or {}).get("max_clusters_to_display", 99) > 5:
        raise ConfigError("at most 5 narratives are displayed")
    for name, rate in (cfg.get("quoted_standard_rates_usd_per_returned_post") or {}).items():
        if not isinstance(rate, str) or Decimal(rate) <= 0:
            raise ConfigError(f"rate for {name} must be a positive decimal string")


def unfrozen(cfg: dict) -> list[dict]:
    out = [{"field": f, "blocks": why} for f, why in MUST_FREEZE if get(cfg, f) is None]
    for t in cfg["topics"]:
        if t.get("comparison_asset") is None:
            out.append({"field": f"topics[{t['id']}].comparison_asset", "blocks": "market_pairing_for_this_topic"})
    return out


def acquisition_gate(cfg: dict, env: dict, *, preflight_passed: bool = False, for_preflight: bool = False) -> dict:
    """Metered requests need the provider key in the environment (looked up by name; its value is never returned
    or logged). The scheduled jobs additionally need a passed preflight. Budgets are part of the validated config."""
    provider = cfg["provider_candidate"]
    reasons = []
    if not (env.get(KEY_ENV[provider]) or "").strip():
        reasons.append("PROVIDER_KEY_MISSING")
    if not for_preflight and cfg["preflight"]["required_for_metered_job_activation"] and not preflight_passed:
        reasons.append("PREFLIGHT_NOT_PASSED")
    return {"allowed": not reasons, "provider": provider, "key_env_name": KEY_ENV[provider], "reasons": reasons}


def classifier_gate(cfg: dict) -> dict:
    c = cfg["classifier"]
    reasons = [f"CLASSIFIER_NOT_PINNED:{f}" for f in ("model_id", "immutable_revision", "prompt_version", "temperature") if c.get(f) is None]
    if not reasons and not isinstance(c.get("generation"), dict):
        reasons.append("CLASSIFIER_NOT_PINNED:generation")
    return {"allowed": not reasons, "reasons": reasons}


def topic(cfg: dict, topic_id: str) -> dict:
    for t in cfg["topics"]:
        if t["id"] == topic_id:
            return t
    raise ConfigError(f"unknown topic {topic_id}")


def targets(t: dict) -> list[str]:
    """A topic without a configured comparison asset is counted as an industry view only."""
    return [t["comparison_asset"]] if t.get("comparison_asset") else [f"industry:{t['id']}"]


# ---------- classifier profiles: independent classification versions one can switch between ----------
PROFILE_META = "active_classifier_profile"


def profiles(cfg: dict) -> dict:
    return cfg.get("classifier_profiles") or {}


def with_profile(cfg: dict, name: str | None) -> dict:
    """The configuration with its classifier block replaced by a named profile. The file's own block is the
    profile called "default". Labels are stored per inference identity, so switching never mixes versions and
    never deletes anything."""
    import copy
    if not name or name == "default":
        return cfg
    if name not in profiles(cfg):
        raise ConfigError(f"unknown classifier profile: {name}")
    out = copy.deepcopy(cfg)
    out["classifier"] = copy.deepcopy(profiles(cfg)[name])
    out["_classifier_profile"] = name
    return out


def active_profile(db) -> dict:
    """{"name", "set_at_utc", "reason"}; name "default" when nothing was chosen in this store."""
    try:
        r = db.execute("SELECT value FROM x_meta WHERE key=?", (PROFILE_META,)).fetchone()
    except Exception:  # noqa: BLE001 - a store without the meta table
        r = None
    return json.loads(r[0]) if r else {"name": "default", "set_at_utc": None, "reason": None}


def apply_active(db, cfg: dict) -> dict:
    name = active_profile(db)["name"]
    return with_profile(cfg, name) if name in profiles(cfg) else cfg
