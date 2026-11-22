"""Command line for the X stance study. Collection, classification and derived tables are separate steps.

  status | cost-table | usage | plan | migrate          read-only or local bookkeeping, no request
  vocab show | vocab add|disable|enable TOPIC KEYWORD | vocab edit TOPIC OLD NEW
  import-local FILE [--declare ...]                      offline import
  preflight                                              bounded live check (needs TWITTERAPI_IO_API_KEY)
  daily                                                  collection only: recent sample + historical queue
  classify --limit N                                     local model, resumable; items stay PENDING if the worker is down
  smoke-model                                            real local model on hand-written texts, in a throwaway store
  build                                                  derived tables: shares, snapshots, views, episodes, QQQ pairing

`preflight` and `daily` are the only commands that can reach the provider. Both refuse and exit 2 while their gate
is closed, and every request goes through the shared ledger. The key is read from the environment and never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import batch, config as cfgmod, cost, provider, snapshots, stance, store, vocab
from .ledger import Ledger


def _cal(cfg):
    from .sessions import Calendar
    c = cfg["time"]["exchange_calendar"]
    return Calendar.load(store.REPO_ROOT / c["path"], c["sha256"])


def _prices(cfg, override=None) -> tuple[dict, dict]:
    """The frozen moomoo snapshot named in the config; verified by hash before use. No other source is tried."""
    from .returns import Prices
    m = cfg["market"]
    inp = m.get("input") or {}
    path = Path(override) if override else (store.REPO_ROOT / inp["path"] if inp.get("path") else None)
    if path is None or not path.exists():
        return {}, {"state": "NO_PRICE_INPUT", "path_configured": bool(inp.get("path")), "exists": bool(path and path.exists())}
    out = {asset: Prices.from_snapshot(path, sid, inp.get("sha256") or (m.get("candidate_input") or {}).get("snapshot_sha256")) for asset, sid in (inp.get("series") or (m.get("candidate_input") or {}).get("series") or {}).items()}
    return out, {"state": "LOADED", "sha256": next(iter(out.values())).input_sha256 if out else None, "series": {a: p.series_id for a, p in out.items()},
                 "last_dates": {a: p.last for a, p in out.items()}}


def _derive(db, cfg, prices_db=None) -> dict:
    from . import pipeline
    cal = _cal(cfg)
    prices, info = _prices(cfg, prices_db)
    return {"topics_mapped": stance.map_topics(db, cfg), "daily": pipeline.build_daily(db, cfg, cal, mode="AUTO"),
            "price_input": info, "research": pipeline.build_research(db, cfg, cal, prices, mode="AUTO")}


def _llm(cfg):
    from . import llm
    return llm.LlamaServer(generation=cfg["classifier"]["generation"])


def _smoke(cfg) -> dict:
    """Real model, hand-written texts, throwaway store. Counts as 'real inference on synthetic text': an
    engineering check of the worker, the grammar and the validator - not accuracy, not market evidence."""
    from . import llm
    worker = _llm(cfg)
    with tempfile.TemporaryDirectory() as td:
        db = store.connect(Path(td) / "smoke.sqlite3")
        for i, (lang, kind, text) in enumerate(llm.SMOKE_TEXTS, 1):
            store.put_post(db, {"provider": "synthetic_smoke", "post_id": str(i), "author_id": f"s{i}", "created_at_utc": "2026-11-20T15:00:00Z", "first_seen_at_utc": store.utcnow(),
                                "fetched_at_utc": store.utcnow(), "ingest_mode": "SYNTHETIC_SMOKE", "source_url": None, "text": text, "author_text": text, "lang": lang,
                                "text_hash": provider.text_hash(text), "edit_status": "SYNTHETIC", "kind": kind, "parent_available": False if kind == "reply" else None, "raw_batch": "smoke"})
        c = dict(cfg); c["topics"] = [t for t in cfg["topics"] if t["id"] == "qqq"]
        stance.map_topics(db, c)
        res = stance.run(db, c, worker)
        rows = [dict(r) for r in db.execute("SELECT post_id, label, quality_status, is_relevant, (evidence_text IS NOT NULL) has_evidence FROM x_stance_labels ORDER BY CAST(post_id AS INTEGER)")]
        again = stance.run(db, c, lambda p: (_ for _ in ()).throw(RuntimeError("cache missed")))
        db.close()
    return {"kind": "REAL_MODEL_INFERENCE_ON_HAND_WRITTEN_TEXT", "texts": len(llm.SMOKE_TEXTS), "matched_to_topic": sum(1 for _ in rows), "run": res, "labels": rows,
            "second_run_model_calls": again["model_calls"], "worker": worker.props(), "throughput": worker.summary(), "output_schema_sha256": llm.schema_hash(),
            "classification_quality": "UNVALIDATED", "note": "no expected labels are attached; this checks plumbing only"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="x_stance_v1")
    ap.add_argument("--config"); ap.add_argument("--db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("status", "cost-table", "usage", "plan", "preflight", "migrate", "smoke-model", "daily"):
        sub.add_parser(name)
    s = sub.add_parser("import-local"); s.add_argument("file"); s.add_argument("--declare", nargs=5, metavar=("TOPIC", "LANG", "START", "END", "STATUS"))
    s = sub.add_parser("classify"); s.add_argument("--limit", type=int, default=50)
    s = sub.add_parser("build"); s.add_argument("--prices-db"); s.add_argument("--out")
    s = sub.add_parser("vocab"); s.add_argument("action", choices=["show", "add", "disable", "enable", "edit"]); s.add_argument("args", nargs="*")
    a = ap.parse_args(argv)
    base, env, now, code = cfgmod.load(a.config), dict(os.environ), datetime.now(timezone.utc), 0
    if a.cmd == "cost-table":
        out = {"table": cost.cost_table(base), "basis": "quoted standard rates x returned posts x 30 days; not a bill"}
    elif a.cmd == "smoke-model":
        gate = cfgmod.classifier_gate(base)
        out, code = (_smoke(base), 0) if gate["allowed"] else ({"status": "REFUSED_CLASSIFIER_NOT_PINNED", "gate": gate}, 2)
    else:
        db = store.connect(a.db)
        ledger = Ledger(db, base)
        cfg = vocab.with_topics(base, vocab.active(db, base))          # the vocabulary in force now; plans freeze their own
        key_env = cfgmod.KEY_ENV[base["provider_candidate"]]
        if a.cmd == "status":
            pf = batch.preflight_state(db, base, env)
            out = {"config_sha256": cfgmod.config_hash(base), "config_version": base["config_version"], "store": str(Path(a.db or store.db_path())),
                   "unfrozen": cfgmod.unfrozen(base), "acquisition_gate": cfgmod.acquisition_gate(base, env, preflight_passed=pf["recent_unlocked"]), "preflight": pf,
                   "classifier_gate": cfgmod.classifier_gate(base), "vocabulary": {"active": cfg["topic_vocabulary_version"], "pending": (vocab.pending(db) or {}).get("version")},
                   "usage": ledger.usage(), "history": batch.history_progress(db, base), "snapshots": db.execute("SELECT count(*) FROM x_signal_snapshots").fetchone()[0] if _has(db, "x_signal_snapshots") else 0}
        elif a.cmd == "usage":
            out = {"usage": ledger.usage(), "ledger": ledger.summary()}
        elif a.cmd == "migrate":
            out = {"checkpoints": provider.migrate_checkpoints(db), "snapshots": snapshots.migrate_legacy(db),
                   "log": [dict(r) for r in db.execute("SELECT id, name, applied_at_utc, detail_json FROM x_migrations ORDER BY id")]}
        elif a.cmd == "plan":                                    # shows what the next run would ask for; freezes nothing, sends nothing
            d = batch.last_completed_ny_date(now)
            out = {"requests_sent": 0, "recent_ny_date": d.isoformat(), "vocabulary": vocab.overview(db, base), "usage": ledger.usage()}
        elif a.cmd == "vocab":
            if a.action == "show":
                out = vocab.overview(db, base)
            else:
                need = 3 if a.action == "edit" else 2
                if len(a.args) != need:
                    out, code = {"status": "USAGE", "expected": "TOPIC KEYWORD" + (" NEW_KEYWORD" if need == 3 else "")}, 2
                else:
                    ch = {"action": a.action, "topic_id": a.args[0], "keyword": a.args[1], **({"new_keyword": a.args[2]} if need == 3 else {})}
                    try:
                        out = vocab.propose(db, base, [ch], now)
                    except vocab.VocabError as ex:
                        out, code = {"status": "REJECTED", "reason": str(ex)}, 1
        elif a.cmd == "import-local":
            out = provider.import_local(db, a.file)
            if a.declare:
                t, lang, s0, s1, st = a.declare
                out["declared_run"] = provider.declare_import_coverage(db, topic_id=t, lang=lang, vocab_version=cfg["topic_vocabulary_version"], start_utc=s0, end_utc=s1, status=st, batch=out["batch"])
        elif a.cmd == "preflight":
            gate = cfgmod.acquisition_gate(base, env, for_preflight=True)
            if not gate["allowed"]:
                out, code = {"status": "BLOCKED_CREDENTIAL" if "PROVIDER_KEY_MISSING" in gate["reasons"] else "REFUSED_FAIL_CLOSED", "requests_sent": 0, "gate": gate}, 2
            else:
                ledger.recover_open()
                out = batch.run_preflight(db, base, provider.UrllibTransport(env[key_env]), now, env=env, ledger=ledger)
                code = 0 if out["verdict"] == "PASS" else 1
        elif a.cmd == "daily":
            out = batch.run_daily(db, base, env, lambda: provider.UrllibTransport(env[key_env]), now)
            if out["status"] == "REFUSED_FAIL_CLOSED":
                code = 2
            else:
                out["topics_mapped"] = stance.map_topics(db, vocab.with_topics(base, vocab.active(db, base)))
        elif a.cmd == "classify":
            gate = cfgmod.classifier_gate(cfg)
            if not gate["allowed"]:
                out, code = {"status": "REFUSED_CLASSIFIER_NOT_PINNED", "gate": gate, "model_calls": 0}, 2
            else:
                worker = _llm(cfg)
                stance.map_topics(db, cfg)
                res = stance.run(db, cfg, worker, limit=a.limit)
                out = {"status": "RAN" if not res["stopped"] else "WORKER_UNAVAILABLE_ITEMS_LEFT_PENDING", **res, "throughput": worker.summary(),
                       "post_topic_target_pairs": db.execute("SELECT count(*) FROM x_post_topics WHERE topic_version=?", (cfg["topic_vocabulary_version"],)).fetchone()[0],
                       "classification_quality": "UNVALIDATED"}
                code = 0 if not res["stopped"] else 1
        else:
            out = _derive(db, cfg, a.prices_db)
        if getattr(a, "out", None):
            Path(a.out).write_text(json.dumps(out, indent=1, sort_keys=True, ensure_ascii=False, default=str), encoding="utf-8", newline="\n")
        db.close()
    print(provider.redact(json.dumps(out, indent=1, ensure_ascii=False, default=str)))
    return code


def _has(db, table: str) -> bool:
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


if __name__ == "__main__":
    sys.exit(main())
