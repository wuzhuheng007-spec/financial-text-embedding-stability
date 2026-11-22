"""Command line for the X stance study.

  status | cost-table | usage | plan          read-only / no request
  import-local FILE [--declare ...]           offline import
  preflight                                   bounded live check (needs TWITTERAPI_IO_API_KEY; <= 200 posts / USD 0.03)
  daily                                       scheduled job: recent sample + historical queue + derived tables
  build [--prices-db PATH] [--out FILE]       derived tables only (no request)

`preflight` and `daily` are the only commands that can reach the provider. Both refuse and exit 2 while their gate
is closed, and every request they send goes through the shared ledger. The key is read from the environment and
is never printed. There is no classify command: no stance model is pinned, so posts stay PENDING.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import batch, config as cfgmod, cost, provider, stance, store
from .ledger import Ledger


def _cal(cfg):
    from .sessions import Calendar
    c = cfg["time"]["exchange_calendar"]
    return Calendar.load(store.REPO_ROOT / c["path"], c["sha256"])


def _derive(db, cfg, prices_db=None) -> dict:
    from . import pipeline
    from .returns import Prices
    cal, prices = _cal(cfg), {}
    if prices_db:
        cand = cfg["market"]["candidate_input"]
        for asset, sid in cand["series"].items():
            prices[asset] = Prices.from_snapshot(prices_db, sid, cand["snapshot_sha256"])
    return {"topics_mapped": stance.map_topics(db, cfg), "daily": pipeline.build_daily(db, cfg, cal, mode="AUTO"),
            "research": pipeline.build_research(db, cfg, cal, prices, mode="AUTO")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="x_stance_v1")
    ap.add_argument("--config"); ap.add_argument("--db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("status", "cost-table", "usage", "plan", "preflight"):
        sub.add_parser(name)
    s = sub.add_parser("import-local"); s.add_argument("file"); s.add_argument("--declare", nargs=5, metavar=("TOPIC", "LANG", "START", "END", "STATUS"))
    for name in ("daily", "build"):
        s = sub.add_parser(name); s.add_argument("--prices-db"); s.add_argument("--out")
    a = ap.parse_args(argv)
    cfg, env, now, code = cfgmod.load(a.config), dict(os.environ), datetime.now(timezone.utc), 0
    if a.cmd == "cost-table":
        out = {"table": cost.cost_table(cfg), "basis": "quoted standard rates x returned posts x 30 days; not a bill"}
    else:
        db = store.connect(a.db)
        ledger = Ledger(db, cfg)
        key_env = cfgmod.KEY_ENV[cfg["provider_candidate"]]
        if a.cmd == "status":
            out = {"config_sha256": cfgmod.config_hash(cfg), "config_version": cfg["config_version"], "store": str(Path(a.db or store.db_path())),
                   "unfrozen": cfgmod.unfrozen(cfg), "acquisition_gate": cfgmod.acquisition_gate(cfg, env, preflight_passed=batch.preflight_passed(db)),
                   "classifier_gate": cfgmod.classifier_gate(cfg), "usage": ledger.usage(), "history": batch.history_progress(db, cfg)}
        elif a.cmd == "usage":
            out = {"usage": ledger.usage(), "ledger": ledger.summary()}
        elif a.cmd == "plan":                                    # shows what the next run would ask for; freezes nothing, sends nothing
            d, q = batch.last_completed_ny_date(now), provider.register_queries(db, cfg)
            page, units = cfg["quote"]["documented_max_posts_per_page"], len(q) * cfg["acquisition"]["proposed_recent_slices_per_day"]
            out = {"requests_sent": 0, "recent_ny_date": d.isoformat(), "queries": len(q), "recent_units": units,
                   "recent_pages_per_unit": max(1, cfg["allocation"]["recent"]["max_returned_posts_per_day"] // page // units),
                   "history_units_per_depth": len(q) * cfg["acquisition"]["proposed_history_slices_per_day"] * cfg["acquisition"]["initial_history_lookback_calendar_days"],
                   "history_pages_per_day_at_cap": cfg["allocation"]["historical"]["max_returned_posts_per_day"] // page, "usage": ledger.usage()}
        elif a.cmd == "import-local":
            out = provider.import_local(db, a.file)
            if a.declare:
                t, lang, s0, s1, st = a.declare
                out["declared_run"] = provider.declare_import_coverage(db, topic_id=t, lang=lang, vocab_version=cfg["topic_vocabulary_version"], start_utc=s0, end_utc=s1, status=st, batch=out["batch"])
        elif a.cmd == "preflight":
            gate = cfgmod.acquisition_gate(cfg, env, for_preflight=True)
            if not gate["allowed"]:
                out, code = {"status": "REFUSED_FAIL_CLOSED", "requests_sent": 0, "gate": gate}, 2
            else:
                ledger.recover_open()
                out = batch.run_preflight(db, cfg, provider.UrllibTransport(env[key_env]), now, ledger=ledger)
                code = 0 if out["verdict"] == "PASS" else 1
        elif a.cmd == "daily":
            out = batch.run_daily(db, cfg, env, lambda: provider.UrllibTransport(env[key_env]), now)
            if out["status"] == "REFUSED_FAIL_CLOSED":
                code = 2
            else:
                out["derived"] = _derive(db, cfg, a.prices_db)
        else:
            out = _derive(db, cfg, a.prices_db)
        if getattr(a, "out", None):
            Path(a.out).write_text(json.dumps(out, indent=1, sort_keys=True, ensure_ascii=False, default=str), encoding="utf-8", newline="\n")
        db.close()
    print(provider.redact(json.dumps(out, indent=1, ensure_ascii=False, default=str)))
    return code


if __name__ == "__main__":
    sys.exit(main())
