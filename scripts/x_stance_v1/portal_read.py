"""What the portal shows. Standard library only (the web image has no numpy), read-only, no network, no model.
With no database it returns the settings and the list of inputs that are missing - never placeholder rows."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import config as cfgmod, store


def read_for_portal(cfg: dict, db_path: Path | None = None, *, env: dict | None = None) -> dict:
    env = env if env is not None else {}
    gate, cgate = cfgmod.acquisition_gate(cfg, env), cfgmod.classifier_gate(cfg)
    missing = [{"input": "PROVIDER_KEY", "detail": gate["key_env_name"]}] if "PROVIDER_KEY_MISSING" in gate["reasons"] else []
    missing += [{"input": u["field"], "detail": u["blocks"]} for u in cfgmod.unfrozen(cfg)]
    out = {"contract_version": cfg["contract_version"], "config_version": cfg["config_version"], "config_sha256": cfgmod.config_hash(cfg),
           "settings": {"provider": cfg["provider_candidate"], "provider_fallback_candidate": cfg["provider_fallback_candidate"], "automatic_paid_fallback": False,
                        "languages": cfg["languages"], "vocabulary_version": cfg["topic_vocabulary_version"],
                        "topics": [{"id": t["id"], "keywords": t["keywords"], "comparison_asset": t.get("comparison_asset"), "targets": cfgmod.targets(t)} for t in cfg["topics"]],
                        "budget": {"daily_usd": cfg["acquisition"]["daily_budget_usd"], "total_usd": cfg["acquisition"]["total_budget_usd"],
                                   "proposed_daily_post_cap": cfg["acquisition"]["proposed_total_returned_post_cap_per_day"]},
                        "acquisition_gate": gate, "classifier_gate": cgate, "editing": "READ_ONLY_VERSIONED_FILE"},
           "collection": {"state": "NOT_STARTED", "runs": 0, "posts": 0, "estimated_cost_usd": "0", "billing_source": "NONE", "by_status": {}, "disk_free_gib": None},
           "missing_inputs": missing, "daily": [], "narratives": [], "episodes": [], "returns": [], "conditional": [], "has_data": False,
           "data_kinds": {"live": 0, "retrospective": 0},
           "limits": ["accounts in the retrieved sample only", "not a prediction", "labels not validated by human review" ]}
    p = Path(db_path or store.db_path())
    try:
        out["collection"]["disk_free_gib"] = round(shutil.disk_usage(p.parent if p.parent.exists() else Path.cwd()).free / 2**30, 1)
    except OSError:
        pass
    if not p.exists():
        return out
    db = store.connect(p, readonly=True)
    try:
        c = out["collection"]
        c["posts"] = db.execute("SELECT count(*) FROM x_posts").fetchone()[0]
        for r in db.execute("SELECT status, count(*) n, sum(CAST(cost_estimate_usd AS REAL)) c FROM x_query_runs GROUP BY status"):
            c["by_status"][r["status"]] = r["n"]; c["runs"] += r["n"]
        c["estimated_cost_usd"] = str(sum((store_dec(r[0]) for r in db.execute("SELECT cost_estimate_usd FROM x_query_runs")), start=store_dec("0")))
        c["billing_source"] = "ESTIMATE_FROM_QUOTED_RATE" if c["runs"] else "NONE"
        c["state"] = "NOT_STARTED" if not c["posts"] else ("HAS_GAPS" if any(k in c["by_status"] for k in ("PARTIAL", "NOT_RUN")) else "COLLECTED")
        for r in db.execute("SELECT ingest_mode, count(*) n FROM x_posts GROUP BY ingest_mode"):
            out["data_kinds"]["live" if r["ingest_mode"] == "LIVE" else "retrospective"] += r["n"]
        for r in db.execute("SELECT window_kind, window_label, topic_id, target_id, lang_scope, mode, payload_json FROM x_daily_sentiment ORDER BY window_label DESC LIMIT 600"):
            p_ = json.loads(r["payload_json"]); p_.pop("vote_post_ids", None)
            out["daily"].append({"window_kind": r["window_kind"], "window_label": r["window_label"], "topic_id": r["topic_id"], "target_id": r["target_id"], **p_})
        for r in db.execute("SELECT topic_id, target_id, payload_json FROM x_narratives ORDER BY window_label DESC, rank LIMIT 100"):
            out["narratives"].append({"topic_id": r["topic_id"], "target_id": r["target_id"], **json.loads(r["payload_json"])})
        for r in db.execute("SELECT payload_json FROM x_sentiment_episodes"):
            out["episodes"].append(json.loads(r["payload_json"]))
        out["episodes"].sort(key=lambda e: e["start_session"], reverse=True)
        for r in db.execute("SELECT topic_id, target_id, h, payload_json FROM x_event_returns ORDER BY event_id DESC LIMIT 1200"):
            out["returns"].append({"topic_id": r["topic_id"], "target_id": r["target_id"], **json.loads(r["payload_json"])})
        for r in db.execute("SELECT payload_json FROM x_run_manifest WHERE manifest_id LIKE 'research_summary:%'"):
            for t in json.loads(r["payload_json"])["topics"]:
                out["conditional"] += [{"topic_id": t["topic_id"], "target_id": t["target_id"], "mode": t["mode"], "split": t["split"]["label"], **c} for c in t["conditional_dev"]]
        out["has_data"] = bool(out["daily"])
    finally:
        db.close()
    return out


def store_dec(v):
    from decimal import Decimal
    return Decimal(str(v or "0"))
