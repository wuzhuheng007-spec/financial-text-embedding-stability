"""Derived tables from stored posts and labels: daily aggregates for both window kinds, narratives, episodes,
six-horizon pairs and the conditional tables; plus the read model the portal shows and the run manifest."""
from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import aggregate, config as cfgmod, cost, episodes, narratives, returns, snapshots, stance, stats, store
from .sessions import NY, Calendar, calendar_window

LANG_SCOPES = ("all", "en", "zh")


def _range(db, topic_id: str, target_id: str):
    r = db.execute("SELECT min(a), max(b) FROM (SELECT min(p.created_at_utc) a, max(p.created_at_utc) b FROM x_stance_labels l JOIN x_posts p USING(provider, post_id) WHERE l.topic_id=? AND l.target_id=? "
                   "UNION ALL SELECT min(p.created_at_utc), max(p.created_at_utc) FROM x_post_topics t JOIN x_posts p USING(provider, post_id) WHERE t.topic_id=? AND t.target_id=?)",
                   (topic_id, target_id, topic_id, target_id)).fetchone()
    r = {"a": r[0], "b": r[1]}
    return (store.parse(r["a"]), store.parse(r["b"])) if r and r["a"] else None


def build_daily(db, cfg: dict, cal: Calendar, *, mode: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    thr, ver = cfg["aggregation"]["proposed_low_sample_author_threshold"], cfg["topic_vocabulary_version"]
    act = snapshots.activation(cfg)
    snapshots.migrate_legacy(db)
    out = {"session_windows": 0, "calendar_windows": 0, "narratives": 0, "outside_calendar_posts_ranges": []}
    auto, boundary = mode == "AUTO", None
    if auto:                                                 # windows after the frozen historical range were sampled by the daily batch
        try:
            hr = db.execute("SELECT value FROM x_meta WHERE key='history_range'").fetchone()
            boundary = store.parse(json.loads(hr[0])["end_utc_exclusive"]) if hr else None
        except Exception:  # noqa: BLE001
            boundary = None
    pick = lambda end: mode if not auto else ("BATCH_AFTER_CLOSE" if boundary is not None and end > boundary else "RETROSPECTIVE_VISIBLE_NOW")
    for t in cfg["topics"]:
        for target in cfgmod.targets(t):
            rng = _range(db, t["id"], target)
            if not rng:
                continue
            i0, i1 = cal.assign(rng[0]), cal.assign(rng[1])
            if i0 is None or i1 is None:
                out["outside_calendar_posts_ranges"].append({"topic_id": t["id"], "from": store.iso(rng[0]), "to": store.iso(rng[1]), "reason": "CALENDAR_NOT_COVERED"})
            lo = i0 if i0 is not None else (1 if rng[0] < cal.closes[0] else None)
            hi = i1 if i1 is not None else (len(cal.sessions) - 1 if rng[1] >= cal.closes[-1] else None)
            if lo is not None and hi is not None:
                for i in range(lo, hi + 1):
                    if now < cal.closes[i]:                 # the window has not ended: no session signal yet
                        break
                    a, b = cal.session_window(i)
                    wmode = pick(b)
                    rows, dropped = aggregate.rows_for_window(db, topic_id=t["id"], target_id=target, start=a, end=b, mode=wmode, act=act)
                    cov = store.coverage(db, t["id"], store.iso(a), store.iso(b), vocab_version=ver)
                    prog = aggregate.progress(db, topic_id=t["id"], target_id=target, start=a, end=b, vocab_version=ver, act=act)
                    for scope in LANG_SCOPES:
                        res = aggregate.aggregate(rows, collection_status=cov["status"], threshold=thr, lang_scope=scope, pending=prog["pending"])
                        res["dropped"], res["collection_gaps"], res["classification"] = dropped, len(cov["gaps"]), prog
                        aggregate.commit(db, window_kind="session", window_label=cal.dates[i], start=a, end=b, topic_id=t["id"], target_id=target,
                                         mode=wmode, result=res, rows=rows, now=now, act=act)
                    out["session_windows"] += 1
                    db.execute("DELETE FROM x_narratives WHERE window_kind='session' AND window_label=? AND topic_id=? AND target_id=?", (cal.dates[i], t["id"], target))
                    for nar in narratives.build(aggregate.votes(rows), cfg=cfg, window_label=cal.dates[i], window_kind="session", data_quality=cov["status"]):
                        db.execute("INSERT OR REPLACE INTO x_narratives VALUES(?,?,?,?,?,?,?)", ("session", cal.dates[i], t["id"], target, nar["cluster_version"], nar["rank"], json.dumps(nar, ensure_ascii=False, sort_keys=True)))
                        out["narratives"] += 1
            d, last = rng[0].astimezone(NY).date(), rng[1].astimezone(NY).date()
            while d <= last:
                a, b = calendar_window(d)
                if now >= b:
                    wmode = pick(b)
                    rows, dropped = aggregate.rows_for_window(db, topic_id=t["id"], target_id=target, start=a, end=b, mode=wmode, act=act)
                    cov = store.coverage(db, t["id"], store.iso(a), store.iso(b), vocab_version=ver)
                    prog = aggregate.progress(db, topic_id=t["id"], target_id=target, start=a, end=b, vocab_version=ver, act=act)
                    res = aggregate.aggregate(rows, collection_status=cov["status"], threshold=thr, pending=prog["pending"])
                    res["dropped"], res["classification"], res["note"] = dropped, prog, "reading summary of a New York calendar date; not the session signal"
                    aggregate.commit(db, window_kind="calendar_day", window_label=d.isoformat(), start=a, end=b, topic_id=t["id"], target_id=target, mode=wmode, result=res, rows=rows, now=now, act=act)
                    out["calendar_windows"] += 1
                d += timedelta(days=1)
    db.commit()
    return out


def _series(db, topic_id, target_id, mode, vocab_version=None):
    """One row per session: the FIRST revision that carried a usable state (what was actually known first), or,
    if the series never had one, its latest revision. A later revision never replaces an earlier formal one here."""
    out = {}
    q = ("SELECT series_key, window_label, revision, formal, timing_status, state, payload_json FROM x_signal_snapshots WHERE window_kind='session' AND topic_id=? AND target_id=? "
         "AND lang_scope='all' AND (mode=? OR ?='AUTO')" + (" AND vocab_version=?" if vocab_version else "") + " ORDER BY window_label, revision")
    for r in db.execute(q, (topic_id, target_id, mode, mode) + ((vocab_version,) if vocab_version else ())):
        cur = out.get(r["window_label"])
        usable = bool(r["formal"]) and r["timing_status"] == "RECORDED"
        if cur is None or (not cur[3] and (usable or True)):
            if cur is None or not cur[3]:
                out[r["window_label"]] = (r["window_label"], r["state"] if r["timing_status"] == "RECORDED" else "UNKNOWN", json.loads(r["payload_json"]), usable)
    return [(d, s_, p_) for d, s_, p_, _ in (out[k] for k in sorted(out))]


def build_research(db, cfg: dict, cal: Calendar, prices: dict, *, mode: str) -> dict:
    """prices: comparison asset -> returns.Prices. A topic without a comparison asset or price input gets episodes
    but every return cell is None with the reason."""
    v, out = cfg["validation"], {"topics": []}
    for t in cfg["topics"]:
        for target in cfgmod.targets(t):
            ser = _series(db, t["id"], target, mode, cfg["topic_vocabulary_version"])
            if not ser:
                continue
            i0, i1 = cal.index(ser[0][0]), cal.index(ser[-1][0])
            sessions = cal.dates[i0:i1 + 1]
            eps = episodes.build([(d, s) for d, s, _ in ser], all_sessions=sessions, topic_id=t["id"], target_id=target)
            db.execute("DELETE FROM x_sentiment_episodes WHERE topic_id=? AND target_id=?", (t["id"], target))
            for e in eps:
                db.execute("INSERT INTO x_sentiment_episodes VALUES(?,?,?,?)", (e["episode_id"], t["id"], target, json.dumps(e, sort_keys=True)))
            starts, by_date = {e["start_session"]: e["episode_id"] for e in eps}, {d: (s, p) for d, s, p in ser}
            px, rows = prices.get(target), []
            for d in sessions:
                s, p = by_date.get(d, (None, None))
                when = None if p is None or p.get("timing_status") != "RECORDED" else (p.get("assumed_available_at_utc") or p.get("first_committed_at_utc"))
                avail = store.parse(when) if when else None
                row = {"session_date": d, "state": s, "is_episode_start": d in starts, "desc_return": {}, "next_open_return": {},
                       "controls": returns.controls(cal, px, d) if px else {}}
                for h in cfgmod.HORIZONS:
                    pr = returns.pair(cal, px, session_date=d, h=h, available_at=avail, availability=(p or {}).get("availability") or "NO_SIGNAL")
                    row["desc_return"][h], row["next_open_return"][h] = pr["desc_return"], pr["next_open_return"]
                    if p is not None:
                        db.execute("INSERT OR REPLACE INTO x_event_returns VALUES(?,?,?,?,?,?)", (f"{t['id']}|{target}|{d}", "daily", t["id"], target, h, json.dumps({**pr, "state": s, "episode_id": starts.get(d), "snapshot_id": p.get("snapshot_id"), "snapshot_revision": p.get("revision"),
                                                                                                        "signal_first_committed_at_utc": p.get("first_committed_at_utc"), "timing_status": p.get("timing_status")}, sort_keys=True)))
                rows.append(row)
            sp = stats.split(rows, holdout_start=v["holdout_start"], purge_sessions=max(cfgmod.HORIZONS))
            kw = dict(block=v["proposed_block_length_sessions"], reps=v["proposed_bootstrap_repetitions"], seed=v["proposed_seed"], min_state_days=v["proposed_min_state_days"])
            out["topics"].append({"topic_id": t["id"], "target_id": target, "mode": mode, "sessions": len(sessions), "split": {k: sp[k] for k in ("status", "purged", "label")},
                                  "dev_sessions": len(sp["dev"]), "holdout_sessions": len(sp["holdout"]), "episode_summary": episodes.summary(eps),
                                  "alignment": returns.alignment(cal, px) if px else {"status": "NO_PRICE_INPUT"},
                                  "price_input_sha256": px.input_sha256 if px else None, "series_id": px.series_id if px else None,
                                  "conditional_dev": stats.conditional(sp["dev"], **kw),
                                  "controls_dev": [stats.with_controls(sp["dev"], kind=k, h=h, **kw) for k in stats.KINDS for h in cfgmod.HORIZONS],
                                  "holdout": "NOT_OPENED"})
    db.execute("INSERT OR REPLACE INTO x_run_manifest VALUES(?,?,?)", (f"research_summary:{mode}", store.utcnow(), json.dumps(out, sort_keys=True)))
    db.commit()
    return out


from .portal_read import read_for_portal  # noqa: E402,F401  (kept importable from here)


# ---------- manifest ----------
def manifest(cfg: dict, *, command: str, inputs: dict, outputs: dict, status: str, repo: Path | None = None) -> dict:
    repo = repo or store.REPO_ROOT

    def git(*a):
        try:
            return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, timeout=20).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
    import numpy, scipy
    dirty = git("status", "--porcelain")
    return {"created_at_utc": store.utcnow(), "code_commit": git("rev-parse", "HEAD"), "dirty": None if dirty is None else bool(dirty),
            "python": platform.python_version(), "dependencies": {"numpy": numpy.__version__, "scipy": scipy.__version__},
            "contract_version": cfg["contract_version"], "config_version": cfg["config_version"], "config_sha256": cfgmod.config_hash(cfg),
            "vocabulary_version": cfg["topic_vocabulary_version"], "model_id": cfg["classifier"]["model_id"], "model_revision": cfg["classifier"]["immutable_revision"],
            "prompt_version": cfg["classifier"]["prompt_version"],
            "proposed_prompt_sha256": stance.prompt_hash(cfg["classifier"]["proposed_prompt_version"]),
            "seed": cfg["validation"]["proposed_seed"], "split": {"holdout_start": cfg["validation"]["holdout_start"]},
            "command": command, "argv0": Path(sys.argv[0]).name, "input_hashes": inputs, "output_hashes": outputs, "status": status,
            "cost_table": cost.cost_table(cfg)}
