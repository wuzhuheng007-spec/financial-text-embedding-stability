"""Command line for the X stance study.

  python -m scripts.x_stance_v1.cli status            what is frozen, what is missing, both gates
  python -m scripts.x_stance_v1.cli cost-table
  python -m scripts.x_stance_v1.cli plan --start 2026-01-05T00:00:00Z --end 2026-01-06T00:00:00Z   (sends nothing)
  python -m scripts.x_stance_v1.cli import-local FILE [--declare TOPIC LANG START END STATUS]
  python -m scripts.x_stance_v1.cli acquire --start ... --end ...   refuses unless the acquisition gate passes
  python -m scripts.x_stance_v1.cli map-topics | build --mode RETROSPECTIVE_VISIBLE_NOW [--prices-db PATH]

There is no `classify` command here on purpose: no classifier is pinned, and the interface (`stance.run`) takes the
model as an argument once one is.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import config as cfgmod, cost, pipeline, provider, stance, store
from .returns import Prices
from .sessions import Calendar


def _cal(cfg):
    c = cfg["time"]["exchange_calendar"]
    return Calendar.load(store.REPO_ROOT / c["path"], c["sha256"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="x_stance_v1")
    ap.add_argument("--config"); ap.add_argument("--db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status"); sub.add_parser("cost-table"); sub.add_parser("map-topics")
    for name in ("plan", "acquire"):
        s = sub.add_parser(name); s.add_argument("--start", required=True); s.add_argument("--end", required=True)
    s = sub.add_parser("import-local"); s.add_argument("file"); s.add_argument("--declare", nargs=5, metavar=("TOPIC", "LANG", "START", "END", "STATUS"))
    s = sub.add_parser("build"); s.add_argument("--mode", default=provider.RETRO, choices=[provider.RETRO, provider.LIVE]); s.add_argument("--prices-db"); s.add_argument("--out")
    a = ap.parse_args(argv)
    cfg = cfgmod.load(a.config)
    out: dict
    if a.cmd == "status":
        out = {"config_sha256": cfgmod.config_hash(cfg), "unfrozen": cfgmod.unfrozen(cfg), "acquisition_gate": cfgmod.acquisition_gate(cfg, dict(os.environ)),
               "classifier_gate": cfgmod.classifier_gate(cfg)}
    elif a.cmd == "cost-table":
        out = {"table": cost.cost_table(cfg), "basis": "quoted standard rates x returned posts x 30 days; not a bill"}
    else:
        db = store.connect(a.db)
        if a.cmd == "plan":
            out = provider.plan(cfg, provider.register_queries(db, cfg), store.parse(a.start), store.parse(a.end))
        elif a.cmd == "import-local":
            out = provider.import_local(db, a.file)
            if a.declare:
                t, lang, s0, s1, st = a.declare
                out["declared_run"] = provider.declare_import_coverage(db, topic_id=t, lang=lang, vocab_version=cfg["topic_vocabulary_version"], start_utc=s0, end_utc=s1, status=st, batch=out["batch"])
        elif a.cmd == "map-topics":
            out = stance.map_topics(db, cfg)
        elif a.cmd == "acquire":
            gate = cfgmod.acquisition_gate(cfg, dict(os.environ))
            if not gate["allowed"]:
                print(json.dumps({"status": "REFUSED_FAIL_CLOSED", "requests_sent": 0, "gate": gate}, indent=1)); return 2
            acq, p = cfg["acquisition"], cfg["provider_candidate"]
            budget = cost.Budget(rate=cfg["quoted_standard_rates_usd_per_returned_post"][p], minimum=cfg["minimum_charge_usd_per_request"][p],
                                 page_max=acq["provider_documented"][p]["max_posts_per_page"], total_usd=acq["total_budget_usd"], daily_usd=acq["daily_budget_usd"],
                                 daily_post_cap=acq["proposed_total_returned_post_cap_per_day"])
            col = provider.Collector(db, cfg, budget=budget, transport=provider.UrllibTransport(os.environ[gate["key_env_name"]]))
            out = {"runs": [r for q in provider.register_queries(db, cfg) for r in col.collect(q, store.parse(a.start), store.parse(a.end))],
                   "estimated_spend_usd": str(budget.spent_total), "requests": budget.requests}
        else:
            cal, prices = _cal(cfg), {}
            if a.prices_db:
                cand = cfg["market"]["candidate_input"]
                for asset, sid in cand["series"].items():
                    prices[asset] = Prices.from_snapshot(a.prices_db, sid, cand["snapshot_sha256"])
            out = {"daily": pipeline.build_daily(db, cfg, cal, mode=a.mode), "research": pipeline.build_research(db, cfg, cal, prices, mode=a.mode)}
            if a.out:
                Path(a.out).write_text(json.dumps(out, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8", newline="\n")
        db.close()
    print(provider.redact(json.dumps(out, indent=1, ensure_ascii=False, default=str)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
