"""Retrospective, non-destructive UNKNOWN evidence adaptation for v1.8.1."""
from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .analysis_contract_v18_1 import validate
from .collector import ROOT

OLD = ROOT / "artifacts/quant_personal_v18_1_20261117"
PRIVATE = ROOT / "artifacts/news_extraction_v182_20261117"
PUBLIC = ROOT / "results/news_extraction_v182_20261117"
ENUM_FIELDS = ("claim_kind", "category", "tone", "uncertainty")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def adapt(fields: dict) -> tuple[dict, list[str]]:
    view = copy.deepcopy(fields)
    converted = []
    for key in ENUM_FIELDS:
        evidence = key.replace("_kind", "") + "_evidence_id" if key == "claim_kind" else key + "_evidence_id"
        if view.get(key) == "UNKNOWN" and view.get(evidence) == "UNKNOWN":
            view[evidence] = None
            converted.append(evidence)
    return view, converted


def run() -> dict:
    target = PRIVATE / "old_adapted_private.jsonl"
    if target.exists():
        raise FileExistsError("ADAPTER_OUTPUT_ALREADY_EXISTS")
    freeze = json.loads((OLD / "frozen_tasks.json").read_text(encoding="utf-8"))
    tasks = {x["task_id"]: x for x in freeze["tasks"]}
    attempts = sorted((OLD / "attempts").glob("*.json"))
    if len(tasks) != 16 or len(attempts) != 16:
        raise ValueError("OLD_TASK_OR_ATTEMPT_COUNT_CHANGED")
    original_pass = adapted_pass = 0
    conversion_counts: Counter = Counter()
    error_counts: Counter = Counter()
    output = []
    for path in attempts:
        old_bytes = path.read_bytes()
        old = json.loads(old_bytes)
        task = tasks[old["task_id"]]
        fields = old["parsed"]
        if not isinstance(fields, dict):
            raise ValueError("OLD_PARSED_VIEW_MISSING")
        original = validate(fields, task["payload"])
        if original["valid"] != old["validation"]["valid"]:
            raise ValueError("ORIGINAL_SCORE_MISMATCH")
        view, converted = adapt(fields)
        outcome = validate(view, task["payload"])
        original_pass += original["valid"]
        adapted_pass += outcome["valid"]
        conversion_counts.update(converted)
        error_counts.update(outcome["errors"])
        output.append({"task_id": old["task_id"], "old_attempt_sha256": sha(old_bytes),
                       "old_raw_sha256": old["raw_sha256"], "original_validation": original,
                       "adapted_fields": view, "adapted_fields_sha256": sha(json.dumps(view,ensure_ascii=False,sort_keys=True).encode()),
                       "conversions": converted, "adapted_validation": outcome,
                       "truth_status": "UNVERIFIED_SUPPLIER_REPORT",
                       "semantic_acceptance": "NOT_GRANTED_BY_FORMAT_ADAPTER"})
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in output), encoding="utf-8")
    receipt = {"schema": "news_extraction_v182_adapter_redacted_1",
               "completed_at_utc": datetime.now(timezone.utc).isoformat(),
               "old_attempts": len(attempts), "raw_mechanical_pass": original_pass,
               "adapted_mechanical_pass": adapted_pass, "converted_evidence_values": sum(conversion_counts.values()),
               "conversions_by_field": dict(conversion_counts), "remaining_error_labels": dict(error_counts),
               "old_files_modified": 0, "new_model_calls": 0,
               "private_adapted_sha256": sha(target.read_bytes()),
               "semantic_acceptance": 0,
               "note": "Mechanical adaptation does not certify field meaning or supplier report truth."}
    PUBLIC.mkdir(parents=True, exist_ok=True)
    (PUBLIC / "old_adapter_redacted.json").write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return {k: receipt[k] for k in ("old_attempts", "raw_mechanical_pass", "adapted_mechanical_pass", "converted_evidence_values", "new_model_calls")}


if __name__ == "__main__":
    print(json.dumps(run()))
