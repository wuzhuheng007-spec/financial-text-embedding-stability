"""What the portal shows. Standard library only, read-only, no network, no model.
With no database it returns the settings, the budget caps with zero use, and the list of inputs that are missing -
never placeholder rows."""
from __future__ import annotations

import json
from datetime import datetime, timezone
import shutil
import sqlite3
from decimal import Decimal
from pathlib import Path

from . import batch, config as cfgmod, sample, store, vocab, watchlist
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


def read_for_portal(cfg: dict, db_path: Path | None = None, *, env: dict | None = None, simple_identity: str | None = None) -> dict:
    env = env if env is not None else {}
    try:                                                         # the classification version chosen in this store, before anything is derived from the config
        p0 = Path(db_path) if db_path else store.db_path()
        if p0.exists():
            d0 = store.connect(p0, readonly=True)
            try:
                cfg = cfgmod.apply_active(d0, cfg)
            finally:
                d0.close()
    except (OSError, sqlite3.Error):
        pass
    cgate = cfgmod.classifier_gate(cfg)
    out = {"contract_version": cfg["contract_version"], "config_version": cfg["config_version"], "config_sha256": cfgmod.config_hash(cfg),
           "settings": {"provider": cfg["provider_candidate"], "automatic_paid_fallback": False, "auto_topup": False,
                        "languages": cfg["languages"], "vocabulary_version": cfg["topic_vocabulary_version"],
                        "topics": [{"id": t["id"], "keywords": t["keywords"], "comparison_asset": t.get("comparison_asset"), "targets": cfgmod.targets(t)} for t in cfg["topics"]],
                        "update_mode": cfg["acquisition"]["update_mode"], "sampling_mode": cfg["acquisition"]["collection_mode"],
                        "key_env_name": cfgmod.KEY_ENV[cfg["provider_candidate"]], "classifier_gate": cgate, "editing": "VERSIONED_EDITS_EFFECTIVE_NEXT_BATCH"},
           "classifier": {"model_id": cfg["classifier"].get("model_id"), "revision": cfg["classifier"].get("immutable_revision"), "prompt_version": cfg["classifier"].get("prompt_version"),
                          "quality": cfg["classifier"].get("classification_quality") or "UNVALIDATED", "runs_in": "separate CPU worker, not in the web process"},
           "market": {"input_version": cfg["market"].get("input_version"), "series": (cfg["market"].get("input") or {}).get("series"), "last_date": (cfg["market"].get("input") or {}).get("last_date"),
                      "branch": "market-regime description, not the alpha target"},
           "vocabulary": None,
           "budget": _empty_usage(cfg), "preflight": {"state": "NOT_RUN", "verdict": None, "at_utc": None, "returned_total": None, "reasons": [], "history_unlocked": False, "continuation": None},
           "history_queue": {"state": "NOT_STARTED", "range": None},
           "collection": {"state": "NOT_STARTED", "runs": 0, "posts": 0, "sample_accounts": 0, "last_post_created_at_utc": None, "last_fetch_at_utc": None,
                          "by_status": {}, "disk_free_gib": None},
           "classification": {"matched_pairs": 0, "classified_or_prefiltered": 0, "pending": 0, "share_done": None, "filtered_low_likes": 0, "filtered_likes_unknown": 0},
           "account_rules": sample.summary(cfg), "queues": None, "other_sample_versions": [],
           "watchlist": {"summary": None, "authors": [], "reading": [], "plan": None},
           "panels": {s: {"sample_version": None, "available": s == "keyword", "daily": [], "narratives": [], "classification": None} for s in sample.SCOPES},
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
            try:
                st = batch.preflight_state(db, cfg, env, check_credential=bool(batch.key_fingerprint(env, cfg)))
            except sqlite3.OperationalError:
                st = {"state": "NOT_RUN", "recent_unlocked": False, "history_unlocked": False, "reasons": [], "at_utc": None}
            passed = st["recent_unlocked"]
            rec = _meta(db, batch.RECEIPT_KEY) or {}
            out["preflight"] = {"state": st["state"], "verdict": rec.get("verdict"), "at_utc": st.get("at_utc"), "returned_total": rec.get("returned_total"),
                                "in_window_total": rec.get("in_window_total"), "reasons": st["reasons"], "history_unlocked": st["history_unlocked"],
                                "continuation": (rec.get("continuation") or {}).get("state"), "receipt_sha256": st.get("receipt_sha256")}
            try:
                out["worker_health"] = batch.read_health(db, cfg, datetime.now(timezone.utc))
                from . import incremental
                out["updates"] = incremental.read_receipt(db, datetime.now(timezone.utc))
                todo = _meta(db, "recovery_todo")
                out["recovery_todo"] = {"saved_at_utc": todo.get("saved_at_utc"), "plans": todo.get("plans") or []} if todo else None
            except sqlite3.OperationalError:
                pass
            out["vocabulary"] = vocab.overview(db, cfg)
            cfg = sample.attach_named(db, cfg)
            out["account_rules"] = sample.summary(cfg)
            cfg = vocab.with_topics(cfg, vocab.active(db, cfg))
            out["settings"]["vocabulary_version"] = cfg["topic_vocabulary_version"]
            out["settings"]["topics"] = [{"id": t["id"], "keywords": t["keywords"], "disabled_keywords": t.get("disabled_keywords") or [], "comparison_asset": t.get("comparison_asset"), "targets": cfgmod.targets(t)} for t in cfg["topics"]]
            _fill(db, cfg, out)
            try:
                from . import simple_view
                vs = [sample.sample_version(c_) for c_ in sample.live_configs(db, cfg)]
                out["simple"] = simple_view.view(db, cfg, simple_identity, vs)
            except sqlite3.OperationalError:
                out["simple"] = None
        finally:
            db.close()
    if out["vocabulary"] is None:
        mem = sqlite3.connect(":memory:")
        out["vocabulary"] = vocab.overview(mem, cfg); mem.close()
    if out["watchlist"]["summary"] is None:
        mem = sqlite3.connect(":memory:"); mem.row_factory = sqlite3.Row
        out["watchlist"]["summary"] = watchlist.summary(mem, cfg); mem.close()
    # the keyword panel's rows are the top-level `daily` / `narratives` / `classification`; they are not sent twice
    out["panels"]["keyword"] = {"sample_version": out["settings"].get("sample_version") or sample.sample_version(cfg), "available": True, "rows": "top-level daily / narratives / classification"}
    gate = cfgmod.acquisition_gate(cfg, env, preflight_passed=passed)
    out.setdefault("worker_health", {"state": "NOT_REPORTED"}); out.setdefault("updates", {"state": "NOT_REPORTED", "credit_hold": None}); out.setdefault("recovery_todo", None); out.setdefault("simple", None)
    out["classifier"]["profile"] = cfg.get("_classifier_profile") or "default"
    if not batch.key_fingerprint(env, cfg):
        # This process (the web page) holds no key by design. What it shows about the paid collection comes from
        # the receipt the collection worker wrote, never from the absence of a key here. Too old: unknown.
        h = out["worker_health"]
        if h["state"] == "FRESH":
            gate = {**gate, "allowed": bool(h["gate"]["allowed"]), "reasons": list(h["gate"]["reasons"]), "source": "WORKER_HEALTH", "checked_at_utc": h["checked_at_utc"]}
        else:
            gate = {**gate, "allowed": None, "reasons": ["WORKER_HEALTH_" + h["state"]], "source": "WORKER_HEALTH", "checked_at_utc": h.get("checked_at_utc")}
    out["settings"]["acquisition_gate"] = gate
    missing = [{"input": "PROVIDER_KEY", "detail": gate["key_env_name"]}] if "PROVIDER_KEY_MISSING" in gate["reasons"] else []
    if "PREFLIGHT_NOT_PASSED" in gate["reasons"]:
        missing.append({"input": "PREFLIGHT", "detail": "bounded live check"})
    if any(r.startswith("WORKER_HEALTH_") for r in gate["reasons"]):
        missing.append({"input": "WORKER_HEALTH", "detail": "the collection worker's self-check (quant-x-stance.sh health)"})
    out["missing_inputs"] = missing + [{"input": u["field"], "detail": u["blocks"]} for u in cfgmod.unfrozen(cfg)]
    return out


def _fill(db, cfg: dict, out: dict) -> None:
    try:
        out["budget"] = Ledger(db, cfg, readonly=True).usage()
    except sqlite3.OperationalError:
        pass                                                 # no ledger table yet: nothing was ever reserved
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
    ver = sample.sample_version(cfg)                             # membership key: vocabulary + account rules
    from . import snapshots, stance
    act = snapshots.activation(cfg)                              # only labels of the inference identity that is active now count as "classified"
    out["classifier"]["inference_identity"] = act["identity"]
    out["settings"]["sample_version"] = ver
    try:
        r = db.execute("SELECT SUM(CASE WHEN t.quality NOT LIKE 'FILTERED_%' THEN 1 ELSE 0 END), COUNT(DISTINCT CASE WHEN t.quality NOT LIKE 'FILTERED_%' THEN p.author_id END), "
                       "SUM(CASE WHEN t.quality NOT LIKE 'FILTERED_%' AND (t.quality!='PENDING' OR EXISTS(SELECT 1 FROM x_stance_labels l WHERE l.provider=t.provider AND l.post_id=t.post_id "
                       "AND l.topic_id=t.topic_id AND l.target_id=t.target_id AND l.inference_identity IS ?)) THEN 1 ELSE 0 END), "
                       "SUM(CASE WHEN t.quality='FILTERED_LOW_LIKES' THEN 1 ELSE 0 END), SUM(CASE WHEN t.quality='FILTERED_LIKES_UNKNOWN' THEN 1 ELSE 0 END) "
                       "FROM x_post_topics t JOIN x_posts p USING(provider, post_id) WHERE t.topic_version=?", (act["identity"] or "", ver)).fetchone()
        legacy = db.execute("SELECT count(*) FROM x_stance_labels WHERE inference_identity IS NULL").fetchone()[0]
        out["queues"] = stance.queue_status(db, cfg, versions=[sample.sample_version(c_) for c_ in sample.live_configs(db, cfg)]) if act["identity"] else None
    except sqlite3.OperationalError:                             # a store written by an earlier release that no job has upgraded yet
        r, legacy = (0, 0, 0, 0, 0), None
        out["store_needs_upgrade"] = True
    total, done = r[0] or 0, r[2] or 0
    c["sample_accounts"] = r[1] or 0
    out["classification"] = {"matched_pairs": total, "classified_or_prefiltered": done, "pending": total - done, "share_done": (done / total) if total else None,
                             "filtered_low_likes": r[3] or 0, "filtered_likes_unknown": r[4] or 0, "legacy_identity_labels": legacy}
    try:
        _watch(db, cfg, out, act)
    except sqlite3.OperationalError:
        out["store_needs_upgrade"] = True
    # the rows the page draws (session windows, all languages) come first, so the row limit never cuts a charted date
    for r in db.execute("SELECT window_kind, window_label, topic_id, target_id, lang_scope, mode, payload_json FROM x_daily_sentiment "
                        "ORDER BY (window_kind='session' AND lang_scope='all') DESC, window_label DESC LIMIT 600"):
        out["daily"].append(_daily_row(r))
    out["daily_summary"] = _summary_identity(out["daily"], act)
    for r in db.execute("SELECT topic_id, target_id, payload_json FROM x_narratives ORDER BY window_label DESC, rank LIMIT 100"):
        out["narratives"].append({"topic_id": r["topic_id"], "target_id": r["target_id"], **json.loads(r["payload_json"])})
    # research rows of the sample version in force now; other versions are their own series and are only counted here
    mine, others = (lambda p_: p_.get("sample_version") in (None, ver)), {}
    for r in db.execute("SELECT payload_json FROM x_sentiment_episodes"):
        e = json.loads(r["payload_json"])
        if mine(e):
            out["episodes"].append(e)
        else:
            others.setdefault(e["sample_version"], {"episodes": 0, "sessions_with_a_signal": 0})["episodes"] += 1
    out["episodes"].sort(key=lambda e: e["start_session"], reverse=True)
    for r in db.execute("SELECT topic_id, target_id, h, payload_json FROM x_event_returns ORDER BY event_id DESC LIMIT 2400"):
        p_ = json.loads(r["payload_json"])
        if mine(p_) and len(out["returns"]) < 1200:
            out["returns"].append({"topic_id": r["topic_id"], "target_id": r["target_id"], **p_})
    for r in db.execute("SELECT payload_json FROM x_run_manifest WHERE manifest_id LIKE 'research_summary:%'"):
        for t in json.loads(r["payload_json"])["topics"]:
            if mine(t):
                out["conditional"] += [{"topic_id": t["topic_id"], "target_id": t["target_id"], "mode": t["mode"], "split": t["split"]["label"], **c_} for c_ in t["conditional_dev"]]
            else:
                others.setdefault(t["sample_version"], {"episodes": 0, "sessions_with_a_signal": 0})["sessions_with_a_signal"] += t["sessions"]
    out["other_sample_versions"] = [{"sample_version": k, **v_} for k, v_ in sorted(others.items())]
    out["has_data"] = bool(out["daily"])


def _daily_row(r) -> dict:
    from .day_view import empty_reason
    p_ = json.loads(r["payload_json"]); p_.pop("vote_post_ids", None); p_.pop("vote_weights", None)
    p_["empty_reason"] = empty_reason(p_.get("classification"), p_.get("posts_total") or 0)
    return {"window_kind": r["window_kind"], "window_label": r["window_label"], "topic_id": r["topic_id"], "target_id": r["target_id"], **p_}


def _summary_identity(rows: list, act: dict) -> dict:
    """Which classification version the stored daily rows were built from, next to the version in force now."""
    from collections import Counter
    from .simple_view import NOTE
    ids = Counter((r.get("activation") or {}).get("identity") or "" for r in rows)
    models = sorted({(r.get("activation") or {}).get("model_id") or "" for r in rows} - {""})
    return {"note": NOTE, "active_identity": act.get("identity"), "active_model_id": act.get("model_id"), "rows_by_identity": {k[:12] or "none": v for k, v in ids.items()},
            "models": models, "all_rows_from_active_identity": bool(rows) and set(ids) == {act.get("identity")}}


def _progress(db, ver: str, identity) -> dict:
    r = db.execute("SELECT SUM(CASE WHEN t.quality NOT LIKE 'FILTERED_%' THEN 1 ELSE 0 END), COUNT(DISTINCT CASE WHEN t.quality NOT LIKE 'FILTERED_%' THEN p.author_id END), "
                   "SUM(CASE WHEN t.quality NOT LIKE 'FILTERED_%' AND (t.quality!='PENDING' OR EXISTS(SELECT 1 FROM x_stance_labels l WHERE l.provider=t.provider AND l.post_id=t.post_id "
                   "AND l.topic_id=t.topic_id AND l.target_id=t.target_id AND l.inference_identity IS ?)) THEN 1 ELSE 0 END), "
                   "SUM(CASE WHEN t.quality='FILTERED_LOW_LIKES' THEN 1 ELSE 0 END), SUM(CASE WHEN t.quality='FILTERED_LIKES_UNKNOWN' THEN 1 ELSE 0 END) "
                   "FROM x_post_topics t JOIN x_posts p USING(provider, post_id) WHERE t.topic_version=?", (identity or "", ver)).fetchone()
    total, done = r[0] or 0, r[2] or 0
    return {"matched_pairs": total, "sample_accounts": r[1] or 0, "classified_or_prefiltered": done, "pending": total - done, "share_done": (done / total) if total else None,
            "filtered_low_likes": r[3] or 0, "filtered_likes_unknown": r[4] or 0}


WHY_NOT = {"FILTERED_LOW_LIKES": "BELOW_LIKE_FLOOR", "FILTERED_LIKES_UNKNOWN": "LIKE_COUNT_UNKNOWN"}


def _watch(db, cfg: dict, out: dict, act: dict) -> None:
    """The watch list, what each author query last did, the raw posts of watched authors with the reason each one
    is or is not in the analysis, and the two extra panels. Raw material is shown whether or not it is counted."""
    w = out["watchlist"]
    w["summary"] = watchlist.summary(db, cfg)
    if not watchlist.exists(db):
        return
    rows = watchlist.authors(db)
    plan = db.execute("SELECT plan_key, plan_json, frozen_at_utc FROM x_sample_plans WHERE job='recent' ORDER BY plan_key DESC LIMIT 1").fetchone()
    if plan:
        pw = json.loads(plan["plan_json"]).get("watch")
        w["plan"] = None if not pw else {"ny_date": plan["plan_key"], "frozen_at_utc": plan["frozen_at_utc"], "authors": len(pw["units"]), "not_asked": pw["not_asked"],
                                         "max_pages_per_author": pw["max_pages_per_author"], "posts_budget": pw["posts_budget"], "first_round_max_posts": pw["first_round_max_posts"]}
    scoped = {sample.scope_of(c_): c_ for c_ in sample.scoped_configs(db, cfg)}
    wver = sample.sample_version(scoped["watchlist"]) if "watchlist" in scoped else None
    by_id = watchlist.bound_ids(db)
    for a in rows:
        n = db.execute("SELECT count(*), max(created_at_utc) FROM x_posts WHERE author_id=?", (a["stable_author_id"],)).fetchone() if a["stable_author_id"] else (0, None)
        w["authors"].append({k: a[k] for k in ("watch_id", "display_name", "handle", "profile_url", "aliases", "source_lists", "scheduling_priority", "enabled", "identity_status",
                                               "availability", "last_success_at_utc", "last_post_created_at_utc", "last_checked_ny_date", "handle_candidates", "handle_history")}
                            | {"id_bound": bool(a["stable_author_id"]), "ready_for_queries": watchlist.ready(a), "handle_text_in_image": (a["provenance"] or {}).get("handle_text_in_image"),
                               "stored_posts": n[0], "weight_override": None})
    if by_id:
        marks = ",".join("?" * len(by_id))
        for p in db.execute(f"SELECT provider, post_id, author_id, created_at_utc, first_seen_at_utc, author_text, kind, lang, source_url, engagement_snapshot_json FROM x_posts WHERE author_id IN ({marks}) "
                            "ORDER BY created_at_utc DESC LIMIT 150", list(by_id)):
            e = sample.engagement(p)
            pairs = db.execute("SELECT topic_id, target_id, quality FROM x_post_topics WHERE provider=? AND post_id=? AND topic_version=?", (p["provider"], p["post_id"], wver)).fetchall() if wver else []
            analysis = []
            for t in pairs:
                lab = db.execute("SELECT label, quality_status FROM x_stance_labels WHERE provider=? AND post_id=? AND topic_id=? AND target_id=? AND inference_identity IS ? ORDER BY label_written_at_utc DESC LIMIT 1",
                                 (p["provider"], p["post_id"], t["topic_id"], t["target_id"], act["identity"] or "")).fetchone()
                if t["quality"].startswith("FILTERED_"):
                    state, why = "NOT_IN_ANALYSIS", WHY_NOT[t["quality"]]
                elif lab is None:
                    state, why = ("NOT_IN_ANALYSIS", t["quality"]) if t["quality"] != "PENDING" else ("WAITING_FOR_CLASSIFICATION", None)
                elif lab["label"]:
                    state, why = "IN_ANALYSIS", None
                else:
                    state, why = "NOT_IN_ANALYSIS", lab["quality_status"]
                analysis.append({"topic_id": t["topic_id"], "target_id": t["target_id"], "state": state, "reason": why, "stance": lab["label"] if lab else None})
            if not pairs:
                analysis = [{"topic_id": None, "target_id": None, "state": "NOT_IN_ANALYSIS", "reason": "NO_TARGET_KEYWORD_IN_TEXT", "stance": None}]
            w["reading"].append({"watch_id": by_id[p["author_id"]], "post_id": p["post_id"], "created_at_utc": p["created_at_utc"], "first_seen_at_utc": p["first_seen_at_utc"], "kind": p["kind"],
                                 "lang": p["lang"], "text": p["author_text"], "url": p["source_url"], "likes_at_fetch": e.get("likeCount"), "sources": watchlist.post_sources(db, p["provider"], p["post_id"]),
                                 "analysis": analysis})
    for scope in ("watchlist", "combined"):
        if scope not in scoped:
            continue
        ver = sample.sample_version(scoped[scope])
        pn = out["panels"][scope]
        pn.update({"sample_version": ver, "available": True, "classification": _progress(db, ver, act["identity"])})
        for r in db.execute("SELECT window_kind, window_label, topic_id, target_id, lang_scope, mode, payload_json FROM x_panel_sentiment WHERE scope=? "
                            "ORDER BY (window_kind='session' AND lang_scope='all') DESC, window_label DESC LIMIT 600", (scope,)):
            pn["daily"].append(_daily_row(r))
        pn["daily_summary"] = _summary_identity(pn["daily"], act)
        for r in db.execute("SELECT topic_id, target_id, payload_json FROM x_panel_narratives WHERE scope=? ORDER BY window_label DESC, rank LIMIT 100", (scope,)):
            pn["narratives"].append({"topic_id": r["topic_id"], "target_id": r["target_id"], **json.loads(r["payload_json"])})


def store_dec(v):
    return Decimal(str(v or "0"))
