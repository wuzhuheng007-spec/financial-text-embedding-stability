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

from . import aggregate, config as cfgmod, cost, episodes, narratives, returns, sample, snapshots, stance, stats, store
from .sessions import NY, Calendar, calendar_window

LANG_SCOPES = ("all", "en", "zh")


def _range(db, topic_id: str, target_id: str):
    r = db.execute("SELECT min(a), max(b) FROM (SELECT min(p.created_at_utc) a, max(p.created_at_utc) b FROM x_stance_labels l JOIN x_posts p USING(provider, post_id) WHERE l.topic_id=? AND l.target_id=? "
                   "UNION ALL SELECT min(p.created_at_utc), max(p.created_at_utc) FROM x_post_topics t JOIN x_posts p USING(provider, post_id) WHERE t.topic_id=? AND t.target_id=?)",
                   (topic_id, target_id, topic_id, target_id)).fetchone()
    r = {"a": r[0], "b": r[1]}
    return (store.parse(r["a"]), store.parse(r["b"])) if r and r["a"] else None


def build_daily(db, cfg: dict, cal: Calendar, *, mode: str, now: datetime | None = None) -> dict:
    """Keyword panel (as before), and - while a watch list exists - the watched-author panel and the combined,
    de-duplicated panel. Each is its own sample version and series; none changes how another is counted."""
    now = now or datetime.now(timezone.utc)
    snapshots.migrate_legacy(db)
    out = None
    for scfg in sample.scoped_configs(db, cfg):
        res = _build_daily_scope(db, scfg, cal, mode=mode, now=now)
        if out is None:
            out = res                                              # the keyword panel's figures stay at the top level
            out["panels"] = {}
        else:
            out["panels"][sample.scope_of(scfg)] = {k: res[k] for k in ("session_windows", "group_windows", "calendar_windows", "narratives", "sample_versions")}
    return out


def _coverage(db, cfg: dict, topic_id: str, a, b, ver: str) -> dict:
    """Collection status behind a window, per panel: keyword queries, author queries, or both."""
    scope = sample.scope_of(cfg)
    kw = store.coverage(db, topic_id, store.iso(a), store.iso(b), vocab_version=ver) if scope != "watchlist" else None
    if scope == "keyword":
        return kw
    from . import watchlist
    w = watchlist.coverage(db, cfg, store.iso(a), store.iso(b))
    if scope == "watchlist":
        return w
    rank = {"MISSING": 0, "PARTIAL": 1, "LIMIT_REACHED": 1, "PLANNED_SAMPLE": 2, "COMPLETE_DECLARED": 3, "COMPLETE": 4}
    worst = min((kw, w), key=lambda c: rank.get(c["status"], 1))
    return {"status": worst["status"], "reason": worst["reason"], "gaps": kw["gaps"] + w["gaps"], "parts": {"keyword": kw["status"], "watchlist": w["status"]}}


def group_settings(cfg: dict, cal: Calendar) -> tuple[int, int] | None:
    """(sessions per group, index of the anchor session) or None when grouping is off."""
    a = cfg.get("aggregation") or {}
    n, anchor = a.get("group_sessions"), a.get("group_anchor_session")
    if not n or not anchor:
        return None
    i = cal.index(anchor)
    if i is None:
        raise ValueError("group_anchor_session is not a session of the calendar")
    return int(n), i


def _build_groups(db, cfg, cal, t, target, lo, hi, *, now, panel, for_date, pick, thr, out) -> int:
    """Complete groups of N sessions that overlap [lo, hi]. The posts of the whole group are counted at once, so an
    account that posted on two days of the group still has one vote (its last valid post). A group is written only
    once its last session has closed."""
    from .sessions import GROUP_KIND, group_of, group_window
    gs = group_settings(cfg, cal)
    if not gs:
        return 0
    n, anchor, made, seen = gs[0], gs[1], 0, set()
    for i in range(lo, hi + 1):
        idx = group_of(cal, i, n, anchor)
        if idx[0] in seen or len(idx) < n:
            continue
        seen.add(idx[0])
        w = group_window(cal, idx)
        if w is None or now < w[1]:
            continue
        a, b = w
        label, dates = cal.dates[idx[-1]], [cal.dates[j] for j in idx]
        wmode = pick(b)
        wcfg, act, ver, sver = for_date(dates[0])
        rows, dropped = aggregate.rows_for_window(db, topic_id=t["id"], target_id=target, start=a, end=b, mode=wmode, act=act)
        cov = _coverage(db, wcfg, t["id"], a, b, ver)
        prog = aggregate.progress(db, topic_id=t["id"], target_id=target, start=a, end=b, vocab_version=sver, act=act)
        res = aggregate.aggregate(rows, collection_status=cov["status"], threshold=thr, pending=prog["pending"], cfg=wcfg)
        res["dropped"], res["collection_gaps"], res["classification"] = dropped, len(cov["gaps"]), prog
        res["group_sessions"], res["group_size"] = dates, n
        aggregate.commit(db, window_kind=GROUP_KIND, window_label=label, start=a, end=b, topic_id=t["id"], target_id=target, mode=wmode, result=res, rows=rows, now=now, act=act)
        made += 1
        if panel == "keyword":
            db.execute("DELETE FROM x_narratives WHERE window_kind=? AND window_label=? AND topic_id=? AND target_id=?", (GROUP_KIND, label, t["id"], target))
        else:
            db.execute("DELETE FROM x_panel_narratives WHERE scope=? AND window_kind=? AND window_label=? AND topic_id=? AND target_id=?", (panel, GROUP_KIND, label, t["id"], target))
        for nar in narratives.build(aggregate.votes(rows), cfg=cfg, window_label=label, window_kind=GROUP_KIND, data_quality=cov["status"]):
            nar["sample_version"], nar["group_sessions"] = sver, dates
            if panel != "keyword":
                nar["panel"] = panel
                db.execute("INSERT OR REPLACE INTO x_panel_narratives VALUES(?,?,?,?,?,?,?)", (panel, GROUP_KIND, label, t["id"], target, nar["rank"], json.dumps(nar, ensure_ascii=False, sort_keys=True)))
            else:
                db.execute("INSERT OR REPLACE INTO x_narratives VALUES(?,?,?,?,?,?,?)", (GROUP_KIND, label, t["id"], target, nar["cluster_version"], nar["rank"], json.dumps(nar, ensure_ascii=False, sort_keys=True)))
            out["narratives"] += 1
    return made


def _build_daily_scope(db, cfg: dict, cal: Calendar, *, mode: str, now: datetime) -> dict:
    thr = cfg["aggregation"]["proposed_low_sample_author_threshold"]
    panel = sample.scope_of(cfg)
    out = {"session_windows": 0, "calendar_windows": 0, "group_windows": 0, "narratives": 0, "outside_calendar_posts_ranges": [], "sample_versions": {}}
    per_date = {}

    def for_date(label: str):
        """Config, activation, vocabulary and sample version a window is derived under: the vocabulary that was in
        force for that sample date (the historical plan's inside its range) plus the account rules of this config."""
        if label not in per_date:
            c = sample.config_for_date(db, cfg, date.fromisoformat(label))
            per_date[label] = (c, snapshots.activation(c), c["topic_vocabulary_version"], sample.sample_version(c))
            out["sample_versions"][per_date[label][3]] = out["sample_versions"].get(per_date[label][3], 0) + 1
        return per_date[label]
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
                    wcfg, act, ver, sver = for_date(cal.dates[i])
                    rows, dropped = aggregate.rows_for_window(db, topic_id=t["id"], target_id=target, start=a, end=b, mode=wmode, act=act)
                    cov = _coverage(db, wcfg, t["id"], a, b, ver)
                    prog = aggregate.progress(db, topic_id=t["id"], target_id=target, start=a, end=b, vocab_version=sver, act=act)
                    for scope in LANG_SCOPES:
                        res = aggregate.aggregate(rows, collection_status=cov["status"], threshold=thr, lang_scope=scope, pending=prog["pending"], cfg=wcfg)
                        res["dropped"], res["collection_gaps"], res["classification"] = dropped, len(cov["gaps"]), prog
                        aggregate.commit(db, window_kind="session", window_label=cal.dates[i], start=a, end=b, topic_id=t["id"], target_id=target,
                                         mode=wmode, result=res, rows=rows, now=now, act=act)
                    out["session_windows"] += 1
                    if panel == "keyword":
                        db.execute("DELETE FROM x_narratives WHERE window_kind='session' AND window_label=? AND topic_id=? AND target_id=?", (cal.dates[i], t["id"], target))
                    else:
                        db.execute("DELETE FROM x_panel_narratives WHERE scope=? AND window_kind='session' AND window_label=? AND topic_id=? AND target_id=?", (panel, cal.dates[i], t["id"], target))
                    for nar in narratives.build(aggregate.votes(rows), cfg=cfg, window_label=cal.dates[i], window_kind="session", data_quality=cov["status"]):
                        nar["sample_version"] = sver
                        if panel != "keyword":
                            nar["panel"] = panel
                            db.execute("INSERT OR REPLACE INTO x_panel_narratives VALUES(?,?,?,?,?,?,?)", (panel, "session", cal.dates[i], t["id"], target, nar["rank"], json.dumps(nar, ensure_ascii=False, sort_keys=True)))
                            out["narratives"] += 1
                            continue
                        db.execute("INSERT OR REPLACE INTO x_narratives VALUES(?,?,?,?,?,?,?)", ("session", cal.dates[i], t["id"], target, nar["cluster_version"], nar["rank"], json.dumps(nar, ensure_ascii=False, sort_keys=True)))
                        out["narratives"] += 1
            if lo is not None and hi is not None:
                out["group_windows"] += _build_groups(db, cfg, cal, t, target, lo, hi, now=now, panel=panel, for_date=for_date, pick=pick, thr=thr, out=out)
            d, last = rng[0].astimezone(NY).date(), rng[1].astimezone(NY).date()
            while d <= last:
                a, b = calendar_window(d)
                if now >= b:
                    wmode = pick(b)
                    wcfg, act, ver, sver = for_date(d.isoformat())
                    rows, dropped = aggregate.rows_for_window(db, topic_id=t["id"], target_id=target, start=a, end=b, mode=wmode, act=act)
                    cov = _coverage(db, wcfg, t["id"], a, b, ver)
                    prog = aggregate.progress(db, topic_id=t["id"], target_id=target, start=a, end=b, vocab_version=sver, act=act)
                    res = aggregate.aggregate(rows, collection_status=cov["status"], threshold=thr, pending=prog["pending"], cfg=wcfg)
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
    v, out = cfg["validation"], {"topics": [], "current_sample_version": sample.sample_version(cfg)}
    current = sample.sample_version(cfg)
    db.executescript(snapshots.SCHEMA)
    for t in cfg["topics"]:
        for target in cfgmod.targets(t):
            db.execute("DELETE FROM x_sentiment_episodes WHERE topic_id=? AND target_id=?", (t["id"], target))
            versions = [r[0] for r in db.execute("SELECT DISTINCT vocab_version FROM x_signal_snapshots WHERE window_kind='session' AND topic_id=? AND target_id=? AND vocab_version IS NOT NULL "
                                                 "AND vocab_version!='LEGACY_UNVERSIONED' ORDER BY vocab_version", (t["id"], target))]
            for sver in sorted(versions, key=lambda x: (x != current, x)):          # each sample version is its own series; they are never joined
                _research_one(db, cfg, cal, prices, mode, t, target, sver, current, v, out)
    db.execute("INSERT OR REPLACE INTO x_run_manifest VALUES(?,?,?)", (f"research_summary:{mode}", store.utcnow(), json.dumps(out, sort_keys=True)))
    db.commit()
    return out


def _research_one(db, cfg, cal, prices, mode, t, target, sver, current, v, out) -> None:
    tag = "" if sver == current else f"|{sver}"
    if True:
        if True:
            ser = _series(db, t["id"], target, mode, sver)
            if not ser:
                return
            i0, i1 = cal.index(ser[0][0]), cal.index(ser[-1][0])
            sessions = cal.dates[i0:i1 + 1]
            eps = episodes.build([(d, s) for d, s, _ in ser], all_sessions=sessions, topic_id=t["id"], target_id=target)
            for e in eps:
                e["sample_version"] = sver
                if tag:
                    e["episode_id"] = store.sha(e["episode_id"] + tag)[:20]
                db.execute("INSERT OR REPLACE INTO x_sentiment_episodes VALUES(?,?,?,?)", (e["episode_id"], t["id"], target, json.dumps(e, sort_keys=True)))
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
                        db.execute("INSERT OR REPLACE INTO x_event_returns VALUES(?,?,?,?,?,?)", (f"{t['id']}|{target}|{d}{tag}", "daily", t["id"], target, h, json.dumps({**pr, "sample_version": sver, "state": s, "episode_id": starts.get(d), "snapshot_id": p.get("snapshot_id"), "snapshot_revision": p.get("revision"),
                                                                                                        "signal_first_committed_at_utc": p.get("first_committed_at_utc"), "timing_status": p.get("timing_status")}, sort_keys=True)))
                rows.append(row)
            sp = stats.split(rows, holdout_start=v["holdout_start"], purge_sessions=max(cfgmod.HORIZONS))
            kw = dict(block=v["proposed_block_length_sessions"], reps=v["proposed_bootstrap_repetitions"], seed=v["proposed_seed"], min_state_days=v["proposed_min_state_days"])
            out["topics"].append({"topic_id": t["id"], "target_id": target, "mode": mode, "sample_version": sver, "is_current_sample_version": sver == current, "sessions": len(sessions), "split": {k: sp[k] for k in ("status", "purged", "label")},
                                  "dev_sessions": len(sp["dev"]), "holdout_sessions": len(sp["holdout"]), "episode_summary": episodes.summary(eps),
                                  "alignment": returns.alignment(cal, px) if px else {"status": "NO_PRICE_INPUT"},
                                  "price_input_sha256": px.input_sha256 if px else None, "series_id": px.series_id if px else None,
                                  "conditional_dev": stats.conditional(sp["dev"], **kw),
                                  "controls_dev": [stats.with_controls(sp["dev"], kind=k, h=h, **kw) for k in stats.KINDS for h in cfgmod.HORIZONS],
                                  "holdout": "NOT_OPENED"})


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
