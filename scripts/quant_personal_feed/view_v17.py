"""Owner-only bounded staged output overlay; no provider or model calls."""
from __future__ import annotations

import json
from pathlib import Path

from .analysis_contract_v17 import PROMPT_VERSION


def overlay(data: dict, *, private_dir: Path) -> dict:
    manifest_path = private_dir / "selection_private.json"
    review_path = private_dir / "semantic_review_private.json"
    data["staged_analysis_mode"] = "BOUNDED_DEVELOPMENT_NOT_CONTINUOUS"
    if not manifest_path.is_file() or not review_path.is_file():
        data["staged_analysis_status"] = "NOT_READY"
        return data
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    reviews = json.loads(review_path.read_text(encoding="utf-8"))
    task_map = {(t["item_key"], t["news_version"]): t for t in manifest["tasks"]}
    data["staged_analysis_status"] = "BOUNDED_BATCH_REVIEWED"
    data["staged_analysis_prompt_version"] = PROMPT_VERSION
    for item in data.get("news", []):
        task = task_map.get((item["item_key"], item["version"]))
        if not task or task["source_hash"] != item.get("content_sha256"):
            continue
        if item.get("analysis_display_kind") == "CODEX_REVIEWED":
            continue  # preserve older human-reviewed cards
        review = reviews.get(task["task_id"], {})
        a = review.get("A", {})
        if a.get("source_text_hash") != task["source_text_hash"]:
            continue
        item["staged_task_id"] = task["task_id"]
        item["staged_source_candidate_count"] = task["candidate_count"]
        item["staged_selected_candidate_index"] = task["selected_candidate_index"]
        if task["candidate_count"] > 1:
            phrase = (item.get("screening_v15") or {}).get("evidence_phrase")
            item["staged_relevance_scope"] = (
                "SELECTED_SEGMENT" if isinstance(phrase, str) and phrase.casefold() in task["source_text"].casefold()
                else "FULL_REPORT_ONLY"
            )
        item["staged_extraction_status"] = a.get("decision", "NOT_REVIEWED")
        item["staged_extraction"] = []
        a_path = private_dir / "attempts" / f"{task['task_id']}_A.json"
        if not a_path.is_file():
            continue
        attempt = json.loads(a_path.read_text(encoding="utf-8"))
        if attempt.get("raw_sha256") != a.get("raw_sha256"):
            continue
        parsed = attempt.get("parsed") or {}
        for path in a.get("accepted_field_paths", []):
            bits = path.split(".")
            value = parsed.get(bits[0])
            if len(bits) == 2 and isinstance(value, list) and bits[1].isdigit():
                value = value[int(bits[1])] if int(bits[1]) < len(value) else None
            if isinstance(value, dict) and isinstance(value.get("quote"), str) and value["quote"] in task["source_text"]:
                item["staged_extraction"].append({"field": path, "value": value.get("value"),
                                                   "unit": value.get("unit"), "quote": value["quote"]})
        b_review = review.get("B", {})
        b_path = private_dir / "attempts" / f"{task['task_id']}_B.json"
        if b_review.get("decision") == "ACCEPT" and b_path.is_file():
            b = json.loads(b_path.read_text(encoding="utf-8"))
            if (b.get("validation") or {}).get("mechanical_valid") and b.get("raw_sha256") == b_review.get("raw_sha256"):
                item["staged_interpretation"] = b.get("parsed")
                item["staged_interpretation_status"] = "CODEX_REVIEWED_DEVELOPMENT_DRAFT"
                item["analysis_status"] = "STAGED_REVIEWED"
                t_review = review.get("T", {})
                t_path = private_dir / "attempts" / f"{task['task_id']}_T.json"
                if t_review.get("decision") == "ACCEPT" and t_path.is_file():
                    t = json.loads(t_path.read_text(encoding="utf-8"))
                    if (t.get("validation") or {}).get("mechanical_valid") and t.get("raw_sha256") == t_review.get("raw_sha256"):
                        item["staged_translation"] = t.get("parsed")
                        item["staged_translation_status"] = "CODEX_REVIEWED_MACHINE_TRANSLATION"
        if item["staged_extraction"] and item.get("analysis_status") != "STAGED_REVIEWED":
            item["analysis_status"] = "STAGED_PARTIAL"
    latest = {(x["item_key"], x["version"]): x for x in data.get("news", [])}
    for row in data.get("top10", []):
        item = latest.get((row["item_key"], row["version"]))
        if item and item.get("staged_task_id"):
            row["analysis_status"] = item["analysis_status"]
    return data
