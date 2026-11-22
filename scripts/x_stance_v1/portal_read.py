"""What the portal shows. Standard library only, read-only, no network, no model.
With no database it returns the settings, the budget caps with zero use, and the list of inputs that are missing -
never placeholder rows."""
from __future__ import annotations

import json
import shutil
import sqlite3
from decimal import Decimal
from pathlib import Path

from . import config as cfgmod, store
from .ledger import Ledger


def _empty_usage(cfg: dict) -> dict:
    mem = sqlite3.connect(":memory:")
    try:
        return Ledger(mem, cfg).usage()
    finally:
        mem.close()


def _meta(db, key: str):
    try:
        r = db.execute("SELECT value FROM x_meta WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else None
    except (sqlite3.OperationalError, ValueError):
        return None


def read_for_portal(cfg: dict, db_path: Path | None = None, *, env: dict | None = None) -> dict:
    env = env if env is not None else {}
    cgate = cfgmod.classifier_gate(cfg)
    out = {"contract_version": cfg["contract_version"], "config_version": cfg["config_version"], "config_sha256": cfgmod.config_hash(cfg),
           "settings": {"provider": cfg["provider_candidate"], "automatic_paid_fallback": False, "auto_topup": False,
                        "languages": cfg["languages"], "vocabulary_version": cfg["topic_vocabulary_version"],
                        "topics": [{"id": t["id"], "keywords": t["keywords"], "comparison_asset": t.get("comparison_asset"), "targets": cfgmod.targets(t)} for t in cfg["topics"]],
                        "update_mode": cfg["acquisition"]["update_mode"], "sampling_mode": cfg["acquisition"]["collection_mode"],
                        "key_env_name": cfgmod.KEY_ENV[cfg["provider_candidate"]], "classifier_gate": cgate, "editing": "READ_ONLY_VERSIONED_FILE"},
           "budget": _empty_usage(cfg), "preflight": {"state": "NOT_RUN", "verdict": None, "at_utc": None, "returned_total": None},
           "history_queue": {"state": "NOT_STARTED", "range": None},
           "collection": {"state": "NOT_STARTED", "runs": 0, "posts": 0, "sample_accounts": 0, "last_post_created_at_utc": None, "last_fetch_at_utc": None,
                          "by_status": {}, "disk_free_gib": None},
           "classification": {"matched_pairs": 0, "classified_or_prefiltered": 0, "pending": 0, "share_done": None},
           "daily": [], "narratives": [], "episodes": [], "returns": [], "conditional": [], "has_data": False,
           "data_kinds": {"live": 0, "batch": 0, "retrospective": 0},
           "limits": ["accounts in the retrieved sample only", "planned sample, not all of X", "not a prediction", "labels not validated by human review"]}
    p = Path(db_path or store.db_path())
    try:
        out["collection"]["disk_free_gib"] = round(shutil.disk_usage(p.parent if p.parent.exists() else Path.cwd()).free / 2**30, 1)
    except OSError:
        pass
    passed = False
    if p.exists():
        db = store.connect(p, readonly=True)
        try:
            passed = bool(_meta(db, "preflight_passed"))
            _fill(db, cfg, out)
        finally:
            db.close()
    gate = cfgmod.acquisition_gate(cfg, env, preflight_passed=passed)
    out["settings"]["acquisition_gate"] = gate
    missing = [{"input": "PROVIDER_KEY", "detail": gate["key_env_name"]}] if "PROVIDER_KEY_MISSING" in gate["reasons"] else []
    if "PREFLIGHT_NOT_PASSED" in gate["reasons"]:
        missing.append({"input": "PREFLIGHT", "detail": "bounded live check"})
    out["missing_inputs"] = missing + [{"input": u["field"], "detail": u["blocks"]} for u in cfgmod.unfrozen(cfg)]
    return out


def _fill(db, cfg: dict, out: dict) -> None:
    try:
        out["budget"] = Ledger(db, cfg, readonly=True).usage()
    except sqlite3.OperationalError:
        pass                                                 # no ledger table yet: nothing was ever reserved
    last = _meta(db, "preflight_last")
    if last:
        out["preflight"] = {"state": "RAN", "verdict": last.get("verdict"), "at_utc": last.get("started_at_utc"), "returned_total": last.get("returned_total"),
                            "requests": len(last.get("requests") or []), "receipt_sha256": last.get("sha256")}
    hr = _meta(db, "history_range")
    if hr:
        r = db.execute("SELECT COUNT(DISTINCT substr(window_start_utc,1,10)), COUNT(*), COALESCE(SUM(returned),0) FROM x_query_runs WHERE window_start_utc>=? AND window_end_utc<=? AND pages>0",
                       (hr["start_utc"], hr["end_utc_exclusive"])).fetchone()
        out["history_queue"] = {"state": "IN_PROGRESS" if r[1] else "QUEUED", "range": hr, "utc_dates_with_any_page": r[0], "units_with_any_page": r[1], "returned": r[2]}
    c = out["collection"]
    c["posts"] = db.execute("SELECT count(*) FROM x_posts").fetchone()[0]
    row = db.execute("SELECT max(created_at_utc), max(fetched_at_utc) FROM x_posts").fetchone()
    c["last_post_created_at_utc"], c["last_fetch_at_utc"] = row[0], row[1]
    for r in db.execute("SELECT status, count(*) n FROM x_query_runs GROUP BY status"):
        c["by_status"][r["status"]] = r["n"]; c["runs"] += r["n"]
    c["state"] = "NOT_STARTED" if not c["posts"] else ("HAS_GAPS" if any(k in c["by_status"] for k in ("PARTIAL", "LIMIT_REACHED")) else "COLLECTED")
    for r in db.execute("SELECT ingest_mode, count(*) n FROM x_posts GROUP BY ingest_mode"):
        out["data_kinds"]["live" if r["ingest_mode"] == "LIVE" else "batch" if r["ingest_mode"] == "BATCH_AFTER_CLOSE" else "retrospective"] += r["n"]
    ver = cfg["topic_vocabulary_version"]
    r = db.execute("SELECT COUNT(*), COUNT(DISTINCT p.author_id), SUM(CASE WHEN t.quality!='PENDING' OR EXISTS(SELECT 1 FROM x_stance_labels l WHERE l.provider=t.provider AND l.post_id=t.post_id "
                   "AND l.topic_id=t.topic_id AND l.target_id=t.target_id) THEN 1 ELSE 0 END) FROM x_post_topics t JOIN x_posts p USING(provider, post_id) WHERE t.topic_version=?", (ver,)).fetchone()
    total, done = r[0] or 0, r[2] or 0
    c["sample_accounts"] = r[1] or 0
    out["classification"] = {"matched_pairs": total, "classified_or_prefiltered": done, "pending": total - done, "share_done": (done / total) if total else None}
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
            out["conditional"] += [{"topic_id": t["topic_id"], "target_id": t["target_id"], "mode": t["mode"], "split": t["split"]["label"], **c_} for c_ in t["conditional_dev"]]
    out["has_data"] = bool(out["daily"])


def store_dec(v):
    return Decimal(str(v or "0"))
