"""Command line for the X stance study. Collection, classification and derived tables are separate steps.

  status | cost-table | usage | plan | migrate          read-only or local bookkeeping, no request
  vocab show | vocab add|disable|enable TOPIC KEYWORD | vocab edit TOPIC OLD NEW
  import-local FILE [--declare ...]                      offline import
  preflight                                              bounded live check (needs TWITTERAPI_IO_API_KEY)
  daily                                                  collection only: recent sample + historical queue
  classify --limit N [--queue auto|recent|history] [--max-seconds S]
                                                         local model, resumable; the recent queue goes first and the
                                                         historical queue only uses what is left; items stay PENDING if the worker is down
  queue                                                  backlog of both queues for the active inference identity
  watch import FILE | show | enable ID | disable ID | confirm-handle ID HANDLE
                                                         the watched-author list (platform-side monitoring; nothing is followed on X)
  watch verify-candidate ID                              one bounded, ledgered page per recorded candidate spelling (needs the key)
  export-pending FILE [--queue Q] [--limit N]            hashed work file for an off-server (local GPU) worker
  import-results FILE                                    checked, idempotent import of that worker's answers
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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import time

from . import batch, config as cfgmod, cost, provider, sample, snapshots, stance, store, vocab, watchlist
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
    return {"topics_mapped": stance.map_all(db, cfg), "daily": pipeline.build_daily(db, cfg, cal, mode="AUTO"),
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
                                "text_hash": provider.text_hash(text), "edit_status": "SYNTHETIC", "kind": kind, "parent_available": False if kind == "reply" else None, "raw_batch": "smoke",
                                # made-up counts above the like floor, so the account rules admit the smoke texts and the model is really called
                                "engagement_snapshot": {"likeCount": 1000, "authorFollowers": 1000, "synthetic": True}})
        c = dict(cfg); c["topics"] = [t for t in cfg["topics"] if t["id"] == "qqq"]
        stance.map_topics(db, c)
        res = stance.run(db, c, worker)
        rows = [dict(r) for r in db.execute("SELECT post_id, label, quality_status, is_relevant, (evidence_text IS NOT NULL) has_evidence FROM x_stance_labels ORDER BY CAST(post_id AS INTEGER)")]
        # second pass must find every label already there; skipped when the first pass could not run at all
        again = stance.run(db, c, lambda p: (_ for _ in ()).throw(RuntimeError("cache missed"))) if not res["stopped"] else {"model_calls": None}
        db.close()
    return {"kind": "REAL_MODEL_INFERENCE_ON_HAND_WRITTEN_TEXT", "texts": len(llm.SMOKE_TEXTS), "matched_to_topic": sum(1 for _ in rows), "run": res, "labels": rows,
            "second_run_model_calls": again["model_calls"], "worker": worker.props(), "throughput": worker.summary(), "output_schema_sha256": llm.schema_hash(),
            "classification_quality": "UNVALIDATED", "note": "no expected labels are attached; this checks plumbing only"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="x_stance_v1")
    ap.add_argument("--config"); ap.add_argument("--db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("status", "cost-table", "usage", "plan", "preflight", "migrate", "smoke-model", "daily", "queue"):
        sub.add_parser(name)
    s = sub.add_parser("resume"); s.add_argument("reason")
    s = sub.add_parser("recover"); s.add_argument("--sample-date"); s.add_argument("--plan-id"); s.add_argument("--dry-run", action="store_true")
    s.add_argument("--rebuild-failed-cursors", action="store_true"); s.add_argument("--pending", action="store_true")
    sub.add_parser("health"); s = sub.add_parser("audit"); s.add_argument("out_dir")
    s = sub.add_parser("export-pending"); s.add_argument("file"); s.add_argument("--queue", default="auto", choices=["auto", "recent", "history"]); s.add_argument("--limit", type=int)
    s = sub.add_parser("import-results"); s.add_argument("file")
    s = sub.add_parser("import-local"); s.add_argument("file"); s.add_argument("--declare", nargs=5, metavar=("TOPIC", "LANG", "START", "END", "STATUS"))
    s = sub.add_parser("classify"); s.add_argument("--limit", type=int, default=50); s.add_argument("--queue", default="auto", choices=["auto", "recent", "history"])
    s.add_argument("--max-seconds", type=float)
    s = sub.add_parser("build"); s.add_argument("--prices-db"); s.add_argument("--out")
    s = sub.add_parser("vocab"); s.add_argument("action", choices=["show", "add", "disable", "enable", "edit"]); s.add_argument("args", nargs="*")
    s = sub.add_parser("watch"); s.add_argument("action", choices=["import", "show", "enable", "disable", "confirm-handle", "verify-candidate"]); s.add_argument("args", nargs="*")
    a = ap.parse_args(argv)
    base, env, now, code = cfgmod.load(a.config), dict(os.environ), datetime.now(timezone.utc), 0
    if a.cmd == "cost-table":
        out = {"table": cost.cost_table(base), "basis": "quoted standard rates x returned posts x 30 days; not a bill"}
    elif a.cmd == "smoke-model":
        gate = cfgmod.classifier_gate(base)
        out, code = (_smoke(base), 0) if gate["allowed"] else ({"status": "REFUSED_CLASSIFIER_NOT_PINNED", "gate": gate}, 2)
        if gate["allowed"] and not out["run"]["model_calls"]:        # a smoke test that called nothing has tested nothing
            out["status"], code = "NO_MODEL_CALL_" + str(out["run"]["stopped"] or "NOTHING_ADMITTED"), 1
    else:
        db = store.connect(a.db)
        ledger = Ledger(db, base)
        base = sample.attach_named(db, base)                           # named accounts take their identity from the private watch list
        cfg = vocab.with_topics(base, vocab.active(db, base))          # the vocabulary in force now; plans freeze their own
        key_env = cfgmod.KEY_ENV[base["provider_candidate"]]
        if a.cmd == "status":
            pf = batch.preflight_state(db, base, env)
            out = {"config_sha256": cfgmod.config_hash(base), "config_version": base["config_version"], "store": str(Path(a.db or store.db_path())),
                   "unfrozen": cfgmod.unfrozen({k: v for k, v in base.items() if not k.startswith("_")}), "acquisition_gate": cfgmod.acquisition_gate(base, env, preflight_passed=pf["recent_unlocked"]), "preflight": pf,
                   "classifier_gate": cfgmod.classifier_gate(base), "vocabulary": {"active": cfg["topic_vocabulary_version"], "pending": (vocab.pending(db) or {}).get("version")},
                   "usage": ledger.usage(), "history": batch.history_progress(db, base), "watchlist": watchlist.summary(db, base), "snapshots": db.execute("SELECT count(*) FROM x_signal_snapshots").fetchone()[0] if _has(db, "x_signal_snapshots") else 0}
        elif a.cmd == "resume":
            out = ledger.resume(a.reason)
        elif a.cmd == "health":                                  # run by the collection worker (it holds the key); the page reads the result
            out = batch.write_health(db, base, env, now, source="health command")
        elif a.cmd == "audit":
            from . import audit as auditmod
            s_ = auditmod.write(db, Path(a.out_dir))
            out = {"written_to": a.out_dir, "ledger_totals": s_["ledger"]["totals"], "checks": s_["ledger"]["checks"], "open_units": s_["open_units"]}
        elif a.cmd == "recover":
            if a.pending or not a.sample_date:                   # list what is open; sends nothing
                out = {"status": "PENDING_LIST", "requests_sent": 0, "plans": batch.recovery_pending(db, base, batch.last_completed_ny_date(now) + timedelta(days=1), now)}
            elif a.dry_run:
                out = batch.recovery_preview(db, base, a.sample_date, a.plan_id, now, ledger)
            else:
                state = batch.preflight_state(db, base, env)
                gate = cfgmod.acquisition_gate(base, env, preflight_passed=state["recent_unlocked"])
                if not gate["allowed"]:
                    out, code = {"status": "BLOCKED_CREDENTIAL" if "PROVIDER_KEY_MISSING" in gate["reasons"] else "REFUSED_FAIL_CLOSED", "requests_sent": 0, "gate": gate}, 2
                else:
                    ledger.recover_open()
                    out = batch.run_recover(db, base, provider.live_transport(base, env[key_env]), a.sample_date, a.plan_id, now, ledger=ledger, rebuild=a.rebuild_failed_cursors)
                    if out["status"] == "RAN":
                        out["topics_mapped"] = stance.map_all(db, base)
                        batch.write_health(db, base, env, now, source="recover")
                    else:
                        code = 1
        elif a.cmd == "usage":
            out = {"usage": ledger.usage(), "ledger": ledger.summary()}
        elif a.cmd == "migrate":
            out = {"checkpoints": provider.migrate_checkpoints(db), "snapshots": snapshots.migrate_legacy(db), "inference_identity": stance.migrate_identity(db),
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
        elif a.cmd == "watch":
            out, code = _watch_cmd(db, base, env, a, now, ledger, key_env)
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
                out = batch.run_preflight(db, base, provider.live_transport(base, env[key_env]), now, env=env, ledger=ledger)
                code = 0 if out["verdict"] == "PASS" else 1
                batch.write_health(db, base, env, now, source="preflight")
        elif a.cmd == "daily":
            out = batch.run_daily(db, base, env, lambda: provider.live_transport(base, env[key_env]), now)
            batch.write_health(db, base, env, now, source="daily")
            if out["status"] == "REFUSED_FAIL_CLOSED":
                code = 2
            else:
                out["topics_mapped"] = stance.map_all(db, base)
        elif a.cmd == "classify":
            gate = cfgmod.classifier_gate(cfg)
            if not gate["allowed"]:
                out, code = {"status": "REFUSED_CLASSIFIER_NOT_PINNED", "gate": gate, "model_calls": 0}, 2
            else:
                worker = _llm(cfg)
                stance.map_all(db, base)
                t0 = time.monotonic()
                stop = (lambda: time.monotonic() - t0 >= a.max_seconds) if a.max_seconds else None
                res = stance.run(db, cfg, worker, limit=a.limit, queue=a.queue, versions=_live(db, base), should_stop=stop)   # real clock for every reading
                yielded = res["stopped"] == "YIELDED_TO_RECENT_QUEUE"
                out = {"status": "RAN" if not res["stopped"] else ("HISTORY_YIELDED_TO_RECENT" if yielded else "WORKER_UNAVAILABLE_ITEMS_LEFT_PENDING"), **res, "throughput": worker.summary(),
                       "queues": stance.queue_status(db, cfg, versions=_live(db, base)), "classification_quality": "UNVALIDATED"}
                code = 0 if (not res["stopped"] or yielded) else 1
        elif a.cmd == "queue":
            out = stance.queue_status(db, cfg, versions=_live(db, base)) if cfgmod.classifier_gate(cfg)["allowed"] else {"status": "CLASSIFIER_NOT_PINNED"}
        elif a.cmd == "export-pending":
            from . import offload
            stance.map_all(db, base)
            out = offload.export_pending(db, cfg, Path(a.file), versions=_live(db, base), queue=a.queue, limit=a.limit)
        elif a.cmd == "import-results":
            from . import offload
            try:
                out = offload.import_results(db, cfg, Path(a.file), versions=_live(db, base))
            except offload.OffloadError as ex:
                out, code = {"status": "REJECTED", "reason": str(ex), "labels_written": 0}, 1
        else:
            out = _derive(db, cfg, a.prices_db)
        if getattr(a, "out", None):
            Path(a.out).write_text(json.dumps(out, indent=1, sort_keys=True, ensure_ascii=False, default=str), encoding="utf-8", newline="\n")
        db.close()
    print(provider.redact(json.dumps(out, indent=1, ensure_ascii=False, default=str)))
    return code


def _watch_cmd(db, base, env, a, now, ledger, key_env):
    try:
        if a.action == "show":
            return {"summary": watchlist.summary(db, base), "authors": [{k: x[k] for k in ("watch_id", "display_name", "handle", "aliases", "identity_status", "enabled", "scheduling_priority",
                                                                                           "availability", "source_lists", "handle_candidates")} | {"id_bound": bool(x["stable_author_id"])}
                                                                         for x in watchlist.authors(db)]}, 0
        if a.action == "import" and len(a.args) == 1:
            seed = json.loads(Path(a.args[0]).read_text(encoding="utf-8"))
            before = len(watchlist.authors(db))
            res = watchlist.import_seed(db, seed, now)
            return {"status": "IMPORTED", "authors_before": before, **res, "summary": watchlist.summary(db, base), "requests_sent": 0}, 0
        if a.action in ("enable", "disable") and len(a.args) == 1:
            x = watchlist.set_enabled(db, a.args[0], a.action == "enable", now)
            return {"status": "UPDATED", "watch_id": x["watch_id"], "enabled": x["enabled"], "takes_effect": "next plan that is not frozen yet"}, 0
        if a.action == "confirm-handle" and len(a.args) == 2:
            x = watchlist.confirm_handle(db, a.args[0], a.args[1], now)
            return {"status": "HANDLE_CONFIRMED_BY_OWNER", "watch_id": x["watch_id"], "handle": x["handle"], "identity_status": x["identity_status"]}, 0
        if a.action == "verify-candidate" and len(a.args) == 1:
            pf = batch.preflight_state(db, base, env)
            gate = cfgmod.acquisition_gate(base, env, preflight_passed=pf["recent_unlocked"])
            if not gate["allowed"]:
                return {"status": "BLOCKED_CREDENTIAL" if "PROVIDER_KEY_MISSING" in gate["reasons"] else "REFUSED_FAIL_CLOSED", "requests_sent": 0, "gate": gate}, 2
            return batch.verify_candidates(db, base, provider.live_transport(base, env[key_env]), a.args[0], now, ledger=ledger), 0
    except watchlist.WatchError as ex:
        return {"status": "REJECTED", "reason": str(ex)}, 1
    return {"status": "USAGE", "expected": "import FILE | show | enable ID | disable ID | confirm-handle ID HANDLE | verify-candidate ID"}, 2


def _live(db, base: dict) -> list[str]:
    return [sample.sample_version(c) for c in sample.live_configs(db, base)]


def _has(db, table: str) -> bool:
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


if __name__ == "__main__":
    sys.exit(main())
