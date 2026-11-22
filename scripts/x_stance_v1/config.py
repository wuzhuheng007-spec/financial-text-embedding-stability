"""Frozen parameters: loading, structural checks, the list of values that are still null, and the two gates
(paid acquisition, classifier) that stay closed while any of their inputs is missing."""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

LABELS = ("bullish", "bearish", "neutral")
HORIZONS = (1, 3, 5, 10, 20, 30)
KEY_ENV = {"twitterapi_io": "X_STANCE_TWITTERAPI_IO_KEY", "socialdata": "X_STANCE_SOCIALDATA_KEY"}
DEFAULT_PATH = Path(__file__).resolve().parents[2] / "research" / "x_stance_v1" / "config" / "x_stance_v1_frozen_20261122.json"

# (path into the config, why it matters). A null here is reported, never replaced by a default.
MUST_FREEZE = (
    ("acquisition.history_start", "acquisition"), ("acquisition.history_end_exclusive", "acquisition"),
    ("acquisition.daily_budget_usd", "acquisition"), ("acquisition.total_budget_usd", "acquisition"),
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


def acquisition_gate(cfg: dict, env: dict) -> dict:
    """Paid requests are allowed only when every reason list entry is absent. The key is looked up by name;
    its value is never returned or logged."""
    acq, provider = cfg["acquisition"], cfg["provider_candidate"]
    reasons = []
    if acq.get("dry_run") is not False:
        reasons.append("DRY_RUN")
    if acq.get("live_enabled") is not True:
        reasons.append("LIVE_NOT_ENABLED")
    if not (env.get(KEY_ENV[provider]) or "").strip():
        reasons.append("PROVIDER_KEY_MISSING")
    for f in ("daily_budget_usd", "total_budget_usd"):
        v = acq.get(f)
        try:
            ok = v is not None and Decimal(str(v)) > 0
        except Exception:  # noqa: BLE001
            ok = False
        if not ok:
            reasons.append(f"BUDGET_MISSING:{f}")
    for f in ("history_start", "history_end_exclusive"):
        if acq.get(f) is None:
            reasons.append(f"WINDOW_NOT_FROZEN:{f}")
    return {"allowed": not reasons, "provider": provider, "key_env_name": KEY_ENV[provider], "reasons": reasons}


def classifier_gate(cfg: dict) -> dict:
    c = cfg["classifier"]
    reasons = [f"CLASSIFIER_NOT_PINNED:{f}" for f in ("model_id", "immutable_revision", "prompt_version", "temperature") if c.get(f) is None]
    return {"allowed": not reasons, "reasons": reasons}


def topic(cfg: dict, topic_id: str) -> dict:
    for t in cfg["topics"]:
        if t["id"] == topic_id:
            return t
    raise ConfigError(f"unknown topic {topic_id}")


def targets(t: dict) -> list[str]:
    """A topic without a configured comparison asset is counted as an industry view only."""
    return [t["comparison_asset"]] if t.get("comparison_asset") else [f"industry:{t['id']}"]
