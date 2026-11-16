"""Freeze owner-only v1.8 material before any v1.8 generation."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .analysis_contract_v18 import digest, entity_candidates, event_spans, sentence_spans
from .analysis_v17_batch import PRIVATE as V17_PRIVATE, PUBLIC as V17_PUBLIC, _rows, write
from .analysis_v16 import PAUSED
from .collector import ROOT

CONFIG = ROOT / "configs/quant_portal_personal_v18_20261117.json"
PRIVATE = ROOT / "artifacts/quant_personal_v18_20261117"
PUBLIC = ROOT / "results/quant_portal_personal_v18_20261117"
RULES = (
    ("SINGLE_EVENT", lambda row: len(event_spans(row["content"])) == 1),
    ("MIXED_EVENT", lambda row: len(event_spans(row["content"])) > 1),
    ("ATTRIBUTION_OR_DENIAL", lambda row: bool(re.search(r"否认|驳斥|指控|指责|声称|传闻|警告称", row["content"]))),
    ("NUMERIC", lambda row: bool(re.search(r"\d+(?:\.\d+)?\s*(?:%|亿美元|亿元|美元|万|亿|基点)", row["content"]))),
)


def freeze() -> dict:
    if not PAUSED.exists():
        raise RuntimeError("OLD_ANALYSIS_WORKER_NOT_PAUSED")
    target = PRIVATE / "selection_private.json"
    if target.exists():
        raise FileExistsError("SELECTION_ALREADY_FROZEN")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    prior = json.loads((V17_PRIVATE / "selection_private.json").read_text(encoding="utf-8"))["tasks"]
    if len(prior) != config["max_existing_items"]:
        raise RuntimeError("V17_COUNT_CHANGED")
    rows = _rows()
    by_key = {row["item_key"]: row for row in rows}
    prior_keys = {row["item_key"] for row in prior}
    selected: list[tuple[str, dict]] = []
    for row in prior:
        current = by_key.get(row["item_key"])
        if current is None or current["version"] != row["news_version"] or current["content_sha256"] != row["source_hash"]:
            raise RuntimeError("V17_SOURCE_VERSION_NO_LONGER_LATEST_USE_VERSIONED_LOOKUP")
        selected.append((row["selection_category"], current))
    cutoff = "2026-11-16T08:05:19Z"
    eligible = [row for row in rows if row["first_received_at"] >= cutoff and row["item_key"] not in prior_keys]
    used = set(prior_keys)
    gaps = []
    for category, predicate in RULES[:config["max_additional_items"]]:
        match = next((row for row in eligible if row["item_key"] not in used and predicate(row)), None)
        if match is None:
            gaps.append(category)
            continue
        selected.append((category, match))
        used.add(match["item_key"])
    frozen = []
    for index, (category, row) in enumerate(selected, 1):
        original = (row["title"].strip() + "\n" if row["title"] else "") + row["content"]
        events = event_spans(original)
        process_limit = config["max_event_segments_per_bulletin"]
        frozen_events = [{**event, "sentences": sentence_spans(event),
                          "candidate_entities": entity_candidates(event["text"]),
                          "run_gate": "READY" if i < process_limit else "NOT_RUN_EVENT_BUDGET"}
                         for i, event in enumerate(events)]
        frozen.append({"task_id": f"V18-{index:02d}", "selection_category": category,
                       "development_only": True, "item_key": row["item_key"], "news_version": row["version"],
                       "source_hash": row["content_sha256"], "source_original": original,
                       "source_original_hash": digest(original), "published_at": row["published_at_utc"],
                       "first_received_at": row["first_received_at"], "source_characters": len(original),
                       "source_gate": "READY" if len(original) <= config["max_source_characters"] else "SOURCE_OVER_BUDGET",
                       "events": frozen_events})
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    manifest = {"schema": "private_v18_frozen_selection_1", "frozen_at_utc": now,
                "selection_rule": "v1.7 eight in original order then earliest post-cutoff unused single, mixed, attributed/denial, numeric bulletin",
                "additional_missing_categories": gaps, "independent_test": False,
                "config_sha256": hashlib.sha256(CONFIG.read_bytes()).hexdigest(), "tasks": frozen}
    write(target, manifest)
    public = {k: v for k, v in manifest.items() if k != "tasks"}
    public["tasks"] = [{k: v for k, v in task.items() if k not in {"source_original", "events"}} |
                       {"event_count": len(task["events"]),
                        "event_run_count": sum(e["run_gate"] == "READY" for e in task["events"]),
                        "event_omitted_count": sum(e["run_gate"] != "READY" for e in task["events"])}
                       for task in frozen]
    public["selection_private_sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    write(PUBLIC / "selection_manifest_redacted.json", public)
    return {"frozen_news": len(frozen), "additional": len(frozen)-len(prior),
            "missing_categories": gaps, "event_tasks": sum(sum(e["run_gate"] == "READY" for e in t["events"]) for t in frozen),
            "source_over_budget": sum(t["source_gate"] != "READY" for t in frozen)}


if __name__ == "__main__":
    print(json.dumps(freeze(), ensure_ascii=False))
