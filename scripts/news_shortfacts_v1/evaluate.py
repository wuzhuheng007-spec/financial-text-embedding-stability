"""Audit a bounded short-evidence Qwen run without changing its raw responses.

Mechanical checks establish format and literal evidence presence. Semantic support
and field correctness require separate, explicit CODEX_REVIEW decisions; an
exactly found quote alone never establishes either.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
FACT_KEYS = (
    "entity", "metric", "value_text", "unit_text", "period_text",
    "basis_text", "evidence_id", "quote",
)
NULLABLE = {"unit_text", "period_text", "basis_text"}
ERROR_LABELS = {
    "WRONG_VALUE", "WRONG_UNIT", "WRONG_PERIOD", "WRONG_ENTITY",
    "WRONG_BASIS", "UNSUPPORTED", "MISSING_FACT", "DUPLICATE",
    "FORMAT_ERROR", "WRONG_METRIC", "WRONG_EVIDENCE_ID", "QUOTE_MISMATCH",
}
FIELD_NAMES = ("entity", "metric", "value_text", "unit_text", "period_text", "basis_text")
FIELD_DECISIONS = {"CORRECT", "WRONG", "UNRESOLVED"}
SEMANTIC_DECISIONS = {"SUPPORTED", "PARTIAL", "UNSUPPORTED", "UNRESOLVED"}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip():
            obj = json.loads(line)
            if not isinstance(obj, dict):
                raise ValueError(f"{path}:{line_number} must be an object")
            rows.append(obj)
    return rows


def resolve_input_path(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else ROOT / p


def verify_inputs(manifest: dict[str, Any], reference: dict[str, Any]) -> dict[str, dict[str, Any]]:
    tasks = manifest.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 6:
        raise ValueError("The frozen development manifest must contain exactly six tasks")
    if not str(reference.get("status", "")).startswith("CODEX_REFERENCE_DRAFT"):
        raise ValueError("Reference must be explicitly marked CODEX_REFERENCE_DRAFT")
    ref_tasks = reference.get("tasks")
    if not isinstance(ref_tasks, list):
        raise ValueError("Reference tasks are missing")
    refs = {r.get("task_id"): r for r in ref_tasks}
    if len(refs) != len(ref_tasks):
        raise ValueError("Duplicate reference task ID")
    seen_ids: set[str] = set()
    seen_evidence: set[str] = set()
    result: dict[str, dict[str, Any]] = {}
    for task in tasks:
        task_id = task.get("task_id")
        evidence_id = task.get("evidence_id")
        if not isinstance(task_id, str) or not isinstance(evidence_id, str):
            raise ValueError("Task and evidence IDs must be strings")
        if task_id in seen_ids or evidence_id in seen_evidence:
            raise ValueError("Duplicate task or evidence ID")
        seen_ids.add(task_id)
        seen_evidence.add(evidence_id)
        if task_id not in refs:
            raise ValueError(f"Missing frozen reference: {task_id}")
        source = resolve_input_path(task["source_path"])
        passage = resolve_input_path(task["text_path"])
        if sha256(source.read_bytes()) != task["source_sha256"]:
            raise ValueError(f"Source hash mismatch: {task_id}")
        raw_passage = passage.read_bytes()
        if sha256(raw_passage) != task["text_sha256"]:
            raise ValueError(f"Passage hash mismatch: {task_id}")
        text = raw_passage.decode("utf-8")
        frozen_facts = refs[task_id].get("facts")
        if not isinstance(frozen_facts, list) or len(frozen_facts) > 3:
            raise ValueError(f"Reference facts out of bounded range: {task_id}")
        for fact in frozen_facts:
            if fact.get("evidence_id") != evidence_id or fact.get("quote") not in text:
                raise ValueError(f"Frozen reference quote or evidence ID mismatch: {task_id}")
        result[task_id] = {"manifest": task, "text": text, "reference_facts": frozen_facts}
    if set(refs) != seen_ids:
        raise ValueError("Reference contains tasks outside the frozen manifest")
    return result


def schema_errors(obj: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(obj, dict):
        return ["Top-level response must be one JSON object"]
    if set(obj) != {"facts"}:
        errors.append("Top-level keys must be exactly ['facts']")
    facts = obj.get("facts")
    if not isinstance(facts, list):
        return errors + ["facts must be an array"]
    if len(facts) > 3:
        errors.append("facts must contain at most three objects")
    for index, fact in enumerate(facts):
        if not isinstance(fact, dict):
            errors.append(f"facts[{index}] must be an object")
            continue
        if set(fact) != set(FACT_KEYS):
            errors.append(f"facts[{index}] keys differ from the eight-field contract")
        for key in FACT_KEYS:
            if key not in fact:
                continue
            value = fact[key]
            if key in NULLABLE and value is None:
                continue
            if not isinstance(value, str) or not value.strip():
                errors.append(f"facts[{index}].{key} must be a nonempty string" +
                              (" or null" if key in NULLABLE else ""))
    return errors


def strict_json_loads(raw: str) -> Any:
    def reject_constant(value: str) -> Any:
        raise ValueError(f"Non-JSON constant: {value}")

    def unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        obj: dict[str, Any] = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError(f"Duplicate JSON key: {key}")
            obj[key] = value
        return obj

    return json.loads(raw, parse_constant=reject_constant, object_pairs_hook=unique_keys)


def fenced_view(raw: str) -> Any | None:
    match = re.fullmatch(r"\s*```(?:json)?\s*\r?\n(?P<body>.*?)\r?\n```\s*",
                         raw, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    try:
        return strict_json_loads(match.group("body"))
    except (json.JSONDecodeError, ValueError):
        return None


def normalize_text(value: Any) -> Any:
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    return " ".join(value.casefold().split())


def numeric_literals(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    return [piece.replace(",", "") for piece in re.findall(r"(?<!\w)[+-]?\d[\d,]*(?:\.\d+)?", value)]


def review_decisions(path: Path | None) -> dict[tuple[str, int], dict[str, Any]]:
    if path is None:
        return {}
    obj = load_json(path)
    if obj.get("reviewer") != "CODEX_REVIEW":
        raise ValueError("Decision file must identify reviewer as CODEX_REVIEW")
    decisions: dict[tuple[str, int], dict[str, Any]] = {}
    for item in obj.get("decisions", []):
        key = (item.get("task_id"), item.get("fact_index"))
        if not isinstance(key[0], str) or not isinstance(key[1], int) or key in decisions:
            raise ValueError("Duplicate or invalid review decision key")
        if item.get("semantic_support") not in SEMANTIC_DECISIONS:
            raise ValueError(f"Invalid semantic decision: {key}")
        field_checks = item.get("field_checks")
        if not isinstance(field_checks, dict) or set(field_checks) != set(FIELD_NAMES):
            raise ValueError(f"Incomplete field checks: {key}")
        if any(value not in FIELD_DECISIONS for value in field_checks.values()):
            raise ValueError(f"Invalid field decision: {key}")
        labels = item.get("error_labels", [])
        if not isinstance(labels, list) or any(label not in ERROR_LABELS for label in labels):
            raise ValueError(f"Invalid error labels: {key}")
        if item.get("reference_index") is not None and not isinstance(item["reference_index"], int):
            raise ValueError(f"Invalid reference index: {key}")
        decisions[key] = item
    return decisions


def evaluate(manifest_path: Path, reference_path: Path, attempt_ledger: Path,
             output_dir: Path, decisions_path: Path | None = None) -> tuple[dict, dict, dict]:
    manifest_bytes = manifest_path.read_bytes()
    reference_bytes = reference_path.read_bytes()
    manifest_obj = json.loads(manifest_bytes)
    reference_obj = json.loads(reference_bytes)
    if (reference_obj.get("input_manifest_sha256") is not None and
            reference_obj["input_manifest_sha256"] != sha256(manifest_bytes)):
        raise ValueError("Reference was frozen against a different input manifest")
    tasks = verify_inputs(manifest_obj, reference_obj)
    attempt_rows = load_jsonl(attempt_ledger)
    attempts = {row.get("task_id"): row for row in attempt_rows}
    if len(attempts) != len(attempt_rows) or any(task_id not in tasks for task_id in attempts):
        raise ValueError("Duplicate or unknown attempt task ID")
    journal = load_jsonl(attempt_ledger.parent / "launch_journal.jsonl")
    launch_rows = [row for row in journal if row.get("event") in
                   (None, "WORKER_LAUNCH_COMMITTED")]
    launched = {row.get("task_id") for row in launch_rows}
    if any(task_id not in tasks for task_id in launched):
        raise ValueError("Unknown launch-journal task ID")
    if len(attempts) > 6 or len(launch_rows) > 6 or len(launched) != len(launch_rows):
        raise ValueError("Generation-call budget exceeded")
    decisions = review_decisions(decisions_path)
    if any(task_id not in tasks for task_id, _ in decisions):
        raise ValueError("Review decision references unknown task")

    output_dir.mkdir(parents=True, exist_ok=True)
    parsed_dir = output_dir / "parsed_views"
    mechanical_rows = []
    review_rows = []
    label_counts: Counter[str] = Counter()
    count = Counter({key: 0 for key in (
        "planned_tasks", "attempted_tasks", "generation_started_tasks",
        "completed_response_tasks", "strict_json_valid_tasks", "schema_valid_tasks",
        "strict_schema_valid_tasks", "fenced_view_json_valid_tasks",
        "fenced_view_schema_valid_tasks", "facts_from_fenced_views",
        "model_facts_in_schema_valid_responses", "quote_present_facts",
        "valid_evidence_id_facts", "tasks_with_all_quotes_and_evidence_ids",
        "quote_applicable_tasks", "semantic_reviewed_facts",
        "semantic_supported_facts", "all_fields_correct_facts",
        "reference_facts", "reference_facts_matched_after_review",
    )})
    count["planned_tasks"] = len(tasks)
    count["reference_facts"] = sum(len(task["reference_facts"]) for task in tasks.values())

    for task_id, task in tasks.items():
        attempt = attempts.get(task_id)
        journaled = task_id in launched
        count["attempted_tasks"] += bool(attempt or journaled)
        phases = load_jsonl(attempt_ledger.parent / "phases" / f"{task_id}.jsonl")
        phase_names = {entry.get("event") for entry in phases}
        generation_started = bool(attempt and attempt.get("generation_started")) or "GENERATION_STARTED" in phase_names
        if attempt:
            count["completed_response_tasks"] += bool(attempt.get("response_completed"))
        count["generation_started_tasks"] += generation_started
        row: dict[str, Any] = {
            "task_id": task_id,
            "attempt_status": attempt.get("status") if attempt else
                ("LAUNCHED_NO_FINAL_LEDGER" if journaled else "NOT_ATTEMPTED"),
            "journaled_launch": journaled,
            "generation_started": generation_started,
            "generation_phase_ledger_mismatch": bool(attempt and
                bool(attempt.get("generation_started")) != ("GENERATION_STARTED" in phase_names)),
            "response_completed": bool(attempt and attempt.get("response_completed")),
            "strict_raw_json_valid": False,
            "fenced_json_view_valid": False,
            "json_view_kind": None,
            "schema_valid": False,
            "strict_schema_valid": False,
            "fenced_view_schema_valid": False,
            "schema_errors": [],
            "raw_response_path": attempt.get("raw_response_path") if attempt else None,
            "facts": [],
        }
        review_task: dict[str, Any] = {"task_id": task_id,
            "reviewer_status": "CODEX_REVIEW_PENDING",
            "reference_status": "CODEX_REFERENCE_DRAFT_NOT_HUMAN_GOLD",
            "reference_fact_count": len(task["reference_facts"]),
            "model_facts": [], "reference_missing_candidates": []}
        if row["response_completed"]:
            raw_rel = row["raw_response_path"]
            if not isinstance(raw_rel, str):
                row["schema_errors"].append("Completed response has no raw_response_path")
            else:
                raw_path = (attempt_ledger.parent / raw_rel).resolve()
                if not raw_path.is_relative_to(attempt_ledger.parent.resolve()) or not raw_path.is_file():
                    row["schema_errors"].append("Raw response missing or outside run directory")
                else:
                    raw = raw_path.read_text(encoding="utf-8")
                    row["raw_response_sha256"] = sha256(raw_path.read_bytes())
                    try:
                        obj = strict_json_loads(raw)
                        row["strict_raw_json_valid"] = True
                        row["json_view_kind"] = "strict_raw"
                        view = obj
                        count["strict_json_valid_tasks"] += 1
                    except (json.JSONDecodeError, ValueError) as exc:
                        row["strict_json_error"] = str(exc)
                        obj = None
                        view = fenced_view(raw)
                        if view is not None:
                            row["fenced_json_view_valid"] = True
                            row["json_view_kind"] = "fenced_json"
                            count["fenced_view_json_valid_tasks"] += 1
                            parsed_dir.mkdir(exist_ok=True)
                            view_path = parsed_dir / f"{task_id}.json"
                            view_path.write_text(json.dumps(view, ensure_ascii=False, indent=2) + "\n",
                                                 encoding="utf-8", newline="\n")
                            row["parsed_view_path"] = str(view_path)
                    if view is not None:
                        row["schema_errors"].extend(schema_errors(view))
                        row["schema_valid"] = not row["schema_errors"]
                        count["schema_valid_tasks"] += row["schema_valid"]
                        if row["schema_valid"]:
                            row["strict_schema_valid"] = row["strict_raw_json_valid"]
                            row["fenced_view_schema_valid"] = row["fenced_json_view_valid"]
                            count["strict_schema_valid_tasks"] += row["strict_schema_valid"]
                            count["fenced_view_schema_valid_tasks"] += row["fenced_view_schema_valid"]
                            obj = view
                            count["model_facts_in_schema_valid_responses"] += len(obj["facts"])
                            if row["fenced_view_schema_valid"]:
                                count["facts_from_fenced_views"] += len(obj["facts"])
                            all_quotes = True
                            used_references: set[int] = set()
                            reviewed_all = True
                            seen_facts: set[tuple[Any, ...]] = set()
                            for index, fact in enumerate(obj["facts"]):
                                quote_present = fact["quote"] in task["text"]
                                value_in_passage = fact["value_text"] in task["text"]
                                value_in_quote = fact["value_text"] in fact["quote"]
                                evidence_valid = fact["evidence_id"] == task["manifest"]["evidence_id"]
                                count["quote_present_facts"] += quote_present
                                count["valid_evidence_id_facts"] += evidence_valid
                                all_quotes = all_quotes and quote_present and evidence_valid
                                fact_key = tuple(normalize_text(fact[field]) for field in FIELD_NAMES)
                                duplicate = fact_key in seen_facts
                                seen_facts.add(fact_key)
                                if duplicate:
                                    label_counts["DUPLICATE"] += 1
                                ref_comparisons = []
                                for r_index, ref in enumerate(task["reference_facts"]):
                                    ref_comparisons.append({
                                        "reference_index": r_index,
                                        "literal_field_equal": {field: normalize_text(fact[field]) ==
                                            normalize_text(ref.get(field)) for field in FIELD_NAMES},
                                        "value_numeric_literals_equal": numeric_literals(fact["value_text"]) ==
                                            numeric_literals(ref.get("value_text")),
                                    })
                                row["facts"].append({"fact_index": index,
                                    "evidence_id_valid": evidence_valid,
                                    "quote_contiguous_in_input": quote_present,
                                    "value_text_contiguous_in_input": value_in_passage,
                                    "value_text_contiguous_in_quote": value_in_quote,
                                    "duplicate_literal_fact": duplicate})
                                decision = decisions.get((task_id, index))
                                if decision is None:
                                    reviewed_all = False
                                else:
                                    ref_index = decision.get("reference_index")
                                    if ref_index is not None:
                                        if not (0 <= ref_index < len(task["reference_facts"])) or ref_index in used_references:
                                            raise ValueError(f"Invalid or duplicate matched reference for {task_id}")
                                        used_references.add(ref_index)
                                    if decision["semantic_support"] == "SUPPORTED" and not (quote_present and evidence_valid):
                                        raise ValueError(f"Supported review requires matching quote and evidence ID: {task_id}/{index}")
                                    count["semantic_reviewed_facts"] += 1
                                    count["semantic_supported_facts"] += decision["semantic_support"] == "SUPPORTED"
                                    all_correct = (decision["semantic_support"] == "SUPPORTED" and
                                        all(value == "CORRECT" for value in decision["field_checks"].values()))
                                    count["all_fields_correct_facts"] += all_correct
                                    label_counts.update(decision.get("error_labels", []))
                                review_task["model_facts"].append({
                                    "fact_index": index, "model_fact": fact,
                                    "mechanical": row["facts"][-1],
                                    "reference_text_comparisons_not_semantic_gold": ref_comparisons,
                                    "codex_review": decision if decision is not None else
                                        {"semantic_support": "UNRESOLVED", "field_checks":
                                         {field: "UNRESOLVED" for field in FIELD_NAMES},
                                         "error_labels": [], "reference_index": None},
                                })
                            count["tasks_with_all_quotes_and_evidence_ids"] += bool(obj["facts"]) and all_quotes
                            count["quote_applicable_tasks"] += bool(obj["facts"])
                            if reviewed_all:
                                review_task["reviewer_status"] = "CODEX_REVIEW_COMPLETE"
                                missing = [i for i in range(len(task["reference_facts"])) if i not in used_references]
                                review_task["reference_missing_candidates"] = missing
                                if missing:
                                    label_counts["MISSING_FACT"] += len(missing)
                                count["reference_facts_matched_after_review"] += len(used_references)
                            elif not obj["facts"] and task["reference_facts"]:
                                review_task["reviewer_status"] = "CODEX_REVIEW_COMPLETE_EMPTY_RESPONSE"
                                review_task["reference_missing_candidates"] = list(range(len(task["reference_facts"])))
                                label_counts["MISSING_FACT"] += len(task["reference_facts"])
                        else:
                            label_counts["FORMAT_ERROR"] += 1
                    else:
                        label_counts["FORMAT_ERROR"] += 1
                    if row["fenced_view_schema_valid"]:
                        label_counts["FORMAT_ERROR"] += 1
        mechanical_rows.append(row)
        review_rows.append(review_task)

    unknown_decisions = set(decisions) - {(task["task_id"], f["fact_index"])
        for task in review_rows for f in task["model_facts"]}
    if unknown_decisions:
        raise ValueError(f"Review decisions without schema-valid model facts: {unknown_decisions}")
    count["strict_json_failed_completed_tasks"] = (count["completed_response_tasks"] -
                                                   count["strict_json_valid_tasks"])
    count["schema_failed_strict_tasks"] = (count["strict_json_valid_tasks"] -
                                          count["strict_schema_valid_tasks"])
    count["quote_missing_facts"] = (count["model_facts_in_schema_valid_responses"] -
                                    count["quote_present_facts"])
    count["invalid_evidence_id_facts"] = (count["model_facts_in_schema_valid_responses"] -
                                          count["valid_evidence_id_facts"])
    count["value_text_absent_from_passage_facts"] = sum(
        not fact["value_text_contiguous_in_input"]
        for row in mechanical_rows for fact in row["facts"])
    status_counts = Counter(row["attempt_status"] for row in mechanical_rows)
    field_status_counts = {
        field: {status: sum(decision["field_checks"][field] == status
                            for decision in decisions.values())
                for status in ("CORRECT", "WRONG", "UNRESOLVED")}
        for field in FIELD_NAMES
    }
    mechanical = {"schema": "shortfacts_mechanical_validation_v1",
        "manifest_sha256": sha256(manifest_bytes), "reference_sha256": sha256(reference_bytes),
        "attempt_ledger_sha256": sha256(attempt_ledger.read_bytes()),
        "strict_raw_json_is_not_fenced_view": True,
        "schema_valid_tasks_include_separately_saved_fenced_views": True,
        "counts": dict(count), "attempt_status_counts": dict(status_counts),
        "tasks": mechanical_rows}
    review = {"schema": "shortfacts_field_review_v1",
        "reviewer": "CODEX_REVIEW_NOT_INDEPENDENT_HUMAN_GOLD",
        "reference_status": "CODEX_REFERENCE_DRAFT",
        "mechanical_quote_match_is_not_semantic_support": True,
        "decisions_file_sha256": sha256(decisions_path.read_bytes()) if decisions_path else None,
        "tasks": review_rows}
    summary = {"schema": "shortfacts_error_summary_v1",
        "counts": dict(count), "attempt_status_counts": dict(status_counts),
        "field_status_counts_codex_review": field_status_counts,
        "field_status_denominator_per_field": count["semantic_reviewed_facts"],
        "error_labels": {label: label_counts[label] for label in sorted(ERROR_LABELS)},
        "semantic_support_denominator": count["semantic_reviewed_facts"],
        "field_correct_denominator": count["semantic_reviewed_facts"],
        "reference_completeness_denominator": count["reference_facts"],
        "interpretation": "Six development tasks are a call budget, not an accuracy sample or independent gold; lexical quote presence and semantic support are distinct."}
    for name, obj in (("mechanical_validation.json", mechanical),
                      ("field_review.json", review), ("error_summary.json", summary)):
        (output_dir / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
                                       encoding="utf-8", newline="\n")
    return mechanical, review, summary


def write_public_summary(path: Path, mechanical: dict, summary: dict) -> None:
    """Emit no passages, model text, private paths, quotes or reference facts."""
    public = {
        "schema": "shortfacts_public_validation_summary_v1",
        "manifest_sha256": mechanical["manifest_sha256"],
        "reference_sha256": mechanical["reference_sha256"],
        "attempt_ledger_sha256": mechanical["attempt_ledger_sha256"],
        "counts": mechanical["counts"],
        "attempt_status_counts": mechanical["attempt_status_counts"],
        "field_status_counts_codex_review": summary["field_status_counts_codex_review"],
        "field_status_denominator_per_field": summary["field_status_denominator_per_field"],
        "error_labels": summary["error_labels"],
        "tasks": [{
            "task_id": row["task_id"],
            "attempt_status": row["attempt_status"],
            "generation_started": row["generation_started"],
            "response_completed": row["response_completed"],
            "strict_raw_json_valid": row["strict_raw_json_valid"],
            "fenced_json_view_valid": row["fenced_json_view_valid"],
            "json_view_kind": row["json_view_kind"],
            "schema_valid": row["schema_valid"],
            "model_fact_count": len(row["facts"]),
            "quote_present_count": sum(f["quote_contiguous_in_input"] for f in row["facts"]),
        } for row in mechanical["tasks"]],
        "interpretation": summary["interpretation"],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(public, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--attempt-ledger", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--review-decisions", type=Path)
    parser.add_argument("--public-summary", type=Path)
    args = parser.parse_args()
    private_root = (ROOT / "artifacts").resolve()
    for name, path in (("manifest", args.manifest), ("reference", args.reference),
                       ("attempt ledger", args.attempt_ledger), ("output directory", args.output_dir),
                       ("review decisions", args.review_decisions)):
        if path is not None and not path.resolve().is_relative_to(private_root):
            parser.error(f"{name} must stay under ignored artifacts/")
    if args.public_summary and not args.public_summary.resolve().is_relative_to((ROOT / "results").resolve()):
        parser.error("Sanitized public summary must be under results/")
    mechanical, _, summary = evaluate(args.manifest, args.reference, args.attempt_ledger,
                                  args.output_dir, args.review_decisions)
    if args.public_summary:
        write_public_summary(args.public_summary, mechanical, summary)
    print(json.dumps(mechanical["counts"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
