"""Sanitised result files for a hand-off: derived numbers only, never post text, links, author ids or keys.
A table with no rows is written with its header and an entry in MISSING.json saying why; no row is made up.

  python -m scripts.x_stance_v1.export OUT_DIR [--db PATH]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

from . import batch, config as cfgmod, store
from .ledger import Ledger

DAILY = ["window_kind", "window_label", "topic_id", "target_id", "lang_scope", "mode", "availability", "window_start_utc", "window_end_utc", "signal_committed_at_utc",
         "state", "state_reason", "accounts_total", "share_bullish", "share_bearish", "share_neutral", "posts_total", "unjudged_total", "sample_accounts",
         "classification_share_done", "collection_status", "low_sample"]
EPISODES = ["episode_id", "topic_id", "target_id", "state", "start_session", "end_session", "sessions", "calendar_days", "left_censored", "left_censor_reason",
            "right_censored", "right_censor_reason", "length_is_lower_bound"]
RETURNS = ["topic_id", "target_id", "session_date", "state", "episode_id", "h", "anchor_date", "horizon_date", "desc_return", "desc_missing_reason", "entry_date", "exit_date",
           "next_open_return", "next_open_missing_reason", "entry_availability", "series_id", "price_input_sha256", "return_kind"]


def _csv(path: Path, fields: list[str], rows: list[dict]) -> int:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fields})
    return len(rows)


def export(db, cfg: dict, out: Path, env: dict) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    daily = []
    for r in db.execute("SELECT window_kind, window_label, topic_id, target_id, payload_json FROM x_daily_sentiment ORDER BY window_kind, window_label, topic_id, lang_scope"):
        p = json.loads(r["payload_json"]); c = p.get("classification") or {}
        daily.append({**p, "window_kind": r["window_kind"], "window_label": r["window_label"], "topic_id": r["topic_id"], "target_id": r["target_id"],
                      "share_bullish": p["shares"]["bullish"], "share_bearish": p["shares"]["bearish"], "share_neutral": p["shares"]["neutral"],
                      "sample_accounts": c.get("sample_accounts"), "classification_share_done": c.get("share_done")})
    eps = [json.loads(r[0]) for r in db.execute("SELECT payload_json FROM x_sentiment_episodes ORDER BY episode_id")]
    rets = [{"topic_id": r["topic_id"], "target_id": r["target_id"], **json.loads(r["payload_json"])} for r in db.execute("SELECT topic_id, target_id, payload_json FROM x_event_returns ORDER BY event_id, h")]
    # narratives: counts and composition only; titles are post excerpts and stay in the private store
    nars = []
    for r in db.execute("SELECT topic_id, target_id, payload_json FROM x_narratives ORDER BY window_label, rank"):
        p = json.loads(r["payload_json"])
        nars.append({"topic_id": r["topic_id"], "target_id": r["target_id"], **{k: p.get(k) for k in ("window_kind", "window_label", "rank", "supporting_accounts", "share_of_sample_accounts", "stance_composition", "data_quality", "fact_check_status", "cluster_version")},
                     "evidence_post_count": len(p.get("evidence") or []), "title": "WITHHELD_POST_EXCERPT_IN_PRIVATE_STORE"})
    n = {"daily_sentiment.csv": _csv(out / "daily_sentiment.csv", DAILY, daily), "episodes.csv": _csv(out / "episodes.csv", EPISODES, eps),
         "forward_returns.csv": _csv(out / "forward_returns.csv", RETURNS, rets), "narratives.json": len(nars)}
    (out / "narratives.json").write_text(json.dumps(nars, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    posts = db.execute("SELECT count(*) FROM x_posts").fetchone()[0]
    labels = db.execute("SELECT count(*) FROM x_stance_labels WHERE label IS NOT NULL").fetchone()[0]
    why = {}
    if not posts:
        base = "NO_POSTS_COLLECTED: no provider key is configured, so no request has been sent"
        why = {k: base for k in n}
    else:
        if not labels:
            for k in ("narratives.json", "episodes.csv"):
                why[k] = "NO_LABELS: no stance model is pinned, posts are PENDING"
        if not rets:
            why["forward_returns.csv"] = "NO_SIGNAL_OR_NO_PRICE_INPUT"
    missing = {k: why.get(k) for k in n if n[k] == 0}
    (out / "MISSING.json").write_text(json.dumps({"rows": n, "why_empty": missing}, indent=1) + "\n", encoding="utf-8", newline="\n")
    ledger = Ledger(db, cfg)
    gate = cfgmod.acquisition_gate(cfg, env, preflight_passed=batch.preflight_passed(db))
    (out / "cost_ledger_summary.json").write_text(json.dumps({"usage": ledger.usage(), "ledger": ledger.summary()}, indent=1) + "\n", encoding="utf-8", newline="\n")
    runs = [dict(r) for r in db.execute("SELECT status, stop_reason, COUNT(*) runs, COALESCE(SUM(pages),0) pages, COALESCE(SUM(requests),0) requests, COALESCE(SUM(returned),0) returned, "
                                        "COALESCE(SUM(in_window),0) in_window, COALESCE(SUM(out_of_window),0) out_of_window, COALESCE(SUM(duplicates),0) duplicates, COALESCE(SUM(new_posts),0) new_posts "
                                        "FROM x_query_runs GROUP BY status, stop_reason ORDER BY 1, 2")]
    pre = db.execute("SELECT value FROM x_meta WHERE key='preflight_last'").fetchone()
    receipt = {"provider": "twitterapi_io", "acquisition_gate": gate, "requests_sent_total": ledger.summary()["requests_total"], "posts_stored": posts,
               "distinct_authors_stored": db.execute("SELECT count(DISTINCT author_id) FROM x_posts").fetchone()[0], "runs_by_status": runs,
               "preflight": json.loads(pre[0]) if pre else {"state": "NOT_RUN", "reason": ", ".join(cfgmod.acquisition_gate(cfg, env, for_preflight=True)["reasons"]) or None},
               "history": batch.history_progress(db, cfg), "raw_store": "private SQLite store on the machine that ran the job; not included here"}
    (out / "fetch_receipt.json").write_text(json.dumps(receipt, indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8", newline="\n")
    return {"rows": n, "why_empty": missing, "posts": posts, "labels": labels}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir"); ap.add_argument("--db"); ap.add_argument("--config")
    a = ap.parse_args(argv)
    db = store.connect(a.db)
    try:
        print(json.dumps(export(db, cfgmod.load(a.config), Path(a.out_dir), dict(os.environ)), indent=1))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
