"""Score frozen shortfacts responses against a versioned, model-visible reference.

Mechanical validation and same-executor semantic decisions remain separate. The
reference and review are development drafts, never independent human gold.
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
FACT_KEYS = ("entity", "metric", "value_text", "unit_text", "period_text",
             "basis_text", "evidence_id", "quote")
FIELDS = FACT_KEYS[:6]
NULLABLE = {"entity", "unit_text", "period_text", "basis_text"}
FIELD_STATES = {"CORRECT", "WRONG", "UNKNOWN"}
SUPPORT_STATES = {"SUPPORTED", "PARTIAL", "UNSUPPORTED", "UNRESOLVED"}
ERROR_LABELS = {"WRONG_VALUE", "WRONG_UNIT", "WRONG_PERIOD", "WRONG_ENTITY",
                "WRONG_BASIS", "WRONG_METRIC", "UNSUPPORTED", "MISSING_FACT",
                "DUPLICATE", "FORMAT_ERROR", "WRONG_EVIDENCE_ID", "QUOTE_MISMATCH"}
ATTRIBUTIONS = {"MODEL_GENERATION_ERROR", "INPUT_CONTEXT_MISSING", "REFERENCE_ERROR",
                "SCHEMA_TASK_CONFLICT", "UNRESOLVED"}
FENCE = re.compile(r"\A[ \t\r\n]*```(?:json)?[ \t]*\r?\n(?P<body>(?:(?!```)[\s\S])*)\r?\n```[ \t\r\n]*\Z", re.I)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"Non-object JSONL row: {path}")
    return rows


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def strict_json_loads(raw: str) -> Any:
    def reject_constant(value: str) -> Any:
        raise ValueError(f"Non-JSON constant: {value}")

    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        obj: dict[str, Any] = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError(f"Duplicate JSON key: {key}")
            obj[key] = value
        return obj

    return json.loads(raw, parse_constant=reject_constant, object_pairs_hook=unique_pairs)


def parse_response(raw_bytes: bytes) -> dict[str, Any]:
    """Adapt only a complete, single outer JSON fence; never repair JSON."""
    raw_hash = digest(raw_bytes)
    try:
        raw = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        return {"kind": "INVALID", "raw_sha256": raw_hash, "view_sha256": None,
                "view_text": None, "obj": None, "error": f"UTF8: {exc}"}
    try:
        obj = strict_json_loads(raw)
        return {"kind": "RAW_JSON", "raw_sha256": raw_hash,
                "view_sha256": raw_hash, "view_text": raw, "obj": obj, "error": None}
    except (json.JSONDecodeError, ValueError) as raw_exc:
        match = FENCE.fullmatch(raw)
        if match is None:
            return {"kind": "INVALID", "raw_sha256": raw_hash,
                    "view_sha256": None, "view_text": None, "obj": None,
                    "error": f"RAW_JSON: {raw_exc}; no exact single fence"}
        body = match.group("body")
        try:
            obj = strict_json_loads(body)
        except (json.JSONDecodeError, ValueError) as fence_exc:
            return {"kind": "INVALID", "raw_sha256": raw_hash,
                    "view_sha256": digest(body.encode("utf-8")), "view_text": body,
                    "obj": None, "error": f"FENCE_JSON: {fence_exc}"}
        return {"kind": "FENCE_ADAPTED", "raw_sha256": raw_hash,
                "view_sha256": digest(body.encode("utf-8")), "view_text": body,
                "obj": obj, "error": None}


def schema_errors(obj: Any) -> list[str]:
    """Exact v2 eight-field contract. No type coercion or content repair."""
    if not isinstance(obj, dict):
        return ["top_level_not_object"]
    errors = []
    if set(obj) != {"facts"}:
        errors.append("top_level_keys")
    facts = obj.get("facts")
    if not isinstance(facts, list):
        return errors + ["facts_not_array"]
    if len(facts) > 3:
        errors.append("more_than_three_facts")
    for index, fact in enumerate(facts):
        if not isinstance(fact, dict):
            errors.append(f"facts[{index}]_not_object")
            continue
        if set(fact) != set(FACT_KEYS):
            errors.append(f"facts[{index}]_keys")
        for key in FACT_KEYS:
            if key not in fact:
                continue
            value = fact[key]
            if value is None and key in NULLABLE:
                continue
            if not isinstance(value, str) or not value.strip():
                errors.append(f"facts[{index}].{key}_type_or_empty")
    return errors


def norm(value: Any) -> Any:
    return " ".join(value.casefold().split()) if isinstance(value, str) else value


def numeric_tokens(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    return [x.replace(",", "") for x in re.findall(r"(?<!\w)[+-]?\d[\d,]*(?:\.\d+)?", value)]


def verify_frozen_inputs(manifest_path: Path, reference_path: Path) -> dict[str, dict[str, Any]]:
    manifest_bytes = manifest_path.read_bytes()
    manifest, reference = json.loads(manifest_bytes), read_json(reference_path)
    if reference.get("input_manifest_sha256") != digest(manifest_bytes):
        raise ValueError("Reference is not bound to this frozen input manifest")
    if not str(reference.get("status", "")).startswith("CODEX_REFERENCE_DRAFT"):
        raise ValueError("Reference must be labeled CODEX_REFERENCE_DRAFT")
    mtasks, rtasks = manifest.get("tasks"), reference.get("tasks")
    if not isinstance(mtasks, list) or len(mtasks) != 6 or not isinstance(rtasks, list):
        raise ValueError("Expected six frozen input tasks and reference task list")
    ref_by_id = {item.get("task_id"): item for item in rtasks}
    if len(ref_by_id) != len(rtasks):
        raise ValueError("Duplicate reference task")
    tasks = {}
    for item in mtasks:
        task_id = item["task_id"]
        if task_id in tasks or task_id not in ref_by_id:
            raise ValueError(f"Duplicate or missing reference task: {task_id}")
        for path_key, sha_key in (("source_path", "source_sha256"), ("text_path", "text_sha256")):
            path = Path(item[path_key])
            path = path if path.is_absolute() else ROOT / path
            if digest(path.read_bytes()) != item[sha_key]:
                raise ValueError(f"Frozen {path_key} hash mismatch: {task_id}")
        text_path = Path(item["text_path"])
        text = (text_path if text_path.is_absolute() else ROOT / text_path).read_text(encoding="utf-8")
        facts = ref_by_id[task_id].get("facts")
        if not isinstance(facts, list) or len(facts) > 3:
            raise ValueError(f"Reference facts outside three-item cap: {task_id}")
        for fact in facts:
            if schema_errors({"facts": [fact]}) or fact["evidence_id"] != item["evidence_id"] or fact["quote"] not in text:
                raise ValueError(f"Reference fact is invalid or outside model-visible passage: {task_id}")
        tasks[task_id] = {"input": item, "text": text, "references": facts}
    if set(tasks) != set(ref_by_id):
        raise ValueError("Reference has tasks outside the frozen manifest")
    return tasks


def load_decisions(path: Path | None, reference_sha256: str | None = None) -> dict[tuple[str, int], dict[str, Any]]:
    if path is None:
        return {}
    obj = read_json(path)
    if obj.get("reviewer") != "CODEX_REVIEW" or obj.get("reference_sha256") != reference_sha256:
        raise ValueError("Fresh CODEX_REVIEW must bind the exact corrected reference")
    rows = {}
    for decision in obj.get("decisions", []):
        key = (decision.get("task_id"), decision.get("fact_index"))
        if not isinstance(key[0], str) or not isinstance(key[1], int) or key in rows:
            raise ValueError("Duplicate or invalid review key")
        fields = decision.get("field_checks")
        if not isinstance(fields, dict) or set(fields) != set(FIELDS) or set(fields.values()) - FIELD_STATES:
            raise ValueError(f"Invalid six-field decisions: {key}")
        if decision.get("semantic_support") not in SUPPORT_STATES:
            raise ValueError(f"Invalid semantic decision: {key}")
        if decision.get("reference_index") is not None and (not isinstance(decision["reference_index"], int) or isinstance(decision["reference_index"], bool)):
            raise ValueError(f"Invalid reference index: {key}")
        labels = decision.get("error_labels", [])
        if not isinstance(labels, list) or set(labels) - ERROR_LABELS:
            raise ValueError(f"Invalid error labels: {key}")
        attributions = decision.get("error_attribution", {})
        if (not isinstance(attributions, dict) or set(attributions) - set(FIELDS) or
                set(attributions.values()) - ATTRIBUTIONS or
                any(fields[field] != "WRONG" for field in attributions)):
            raise ValueError(f"Invalid field-error attribution: {key}")
        rows[key] = decision
    return rows


def evaluate(manifest_path: Path, reference_path: Path, attempt_ledger: Path,
             output_dir: Path, decisions_path: Path | None = None,
             schema_path: Path | None = None) -> tuple[dict, dict, dict]:
    tasks = verify_frozen_inputs(manifest_path, reference_path)
    ref_hash = digest(reference_path.read_bytes())
    decisions = load_decisions(decisions_path, ref_hash)
    attempts_list = read_jsonl(attempt_ledger)
    attempts = {row.get("task_id"): row for row in attempts_list}
    if len(attempts) != len(attempts_list) or set(attempts) - set(tasks) or len(attempts_list) > 6:
        raise ValueError("Attempt ledger has duplicate, unknown or over-budget task")
    launches = read_jsonl(attempt_ledger.parent / "launch_journal.jsonl")
    launches = [row for row in launches if row.get("event") in (None, "WORKER_LAUNCH_COMMITTED")]
    launched = [row.get("task_id") for row in launches]
    if len(set(launched)) != len(launched) or set(launched) - set(tasks) or len(launched) > 6:
        raise ValueError("Launch journal has duplicate, unknown or over-budget task")
    if schema_path is not None:
        schema = read_json(schema_path)
        if schema.get("properties", {}).get("facts", {}).get("maxItems") != 3:
            raise ValueError("Versioned schema does not state three-fact cap")
    counts = Counter({name: 0 for name in (
        "planned_tasks", "attempted_tasks", "generation_started_tasks", "completed_response_tasks",
        "strict_raw_json_valid_tasks", "fence_adapted_tasks", "json_valid_after_optional_adapter_tasks",
        "strict_raw_schema_valid_tasks", "fence_adapted_schema_valid_tasks",
        "schema_valid_after_optional_adapter_tasks", "empty_fact_response_tasks",
        "model_facts", "valid_evidence_id_facts", "quote_contiguous_facts",
        "semantic_supported_facts", "semantic_partial_facts", "semantic_unsupported_facts",
        "semantic_unresolved_facts", "reviewed_facts",
        "reference_facts", "matched_reference_facts", "matched_with_field_error_facts",
        "fully_correct_facts", "unmatched_reference_facts", "unresolved_output_facts",
        "duplicate_output_facts", "unmatched_output_facts")})
    counts["planned_tasks"] = len(tasks)
    counts["reference_facts"] = sum(len(t["references"]) for t in tasks.values())
    field_counts = {field: Counter({state: 0 for state in FIELD_STATES}) for field in FIELDS}
    mechanical_rows, review_rows, private_error_table = [], [], []
    labels = Counter({label: 0 for label in ERROR_LABELS})
    consumed = set()
    for task_id, task in tasks.items():
        attempt = attempts.get(task_id)
        phases = read_jsonl(attempt_ledger.parent / "phases" / f"{task_id}.jsonl")
        phase_names = {row.get("event") for row in phases}
        started = bool(attempt and attempt.get("generation_started")) or "GENERATION_STARTED" in phase_names
        completed = bool(attempt and attempt.get("response_completed"))
        counts["attempted_tasks"] += bool(attempt or task_id in launched)
        counts["generation_started_tasks"] += started
        counts["completed_response_tasks"] += completed
        row = {"task_id": task_id, "attempt_status": attempt.get("status") if attempt else
               ("LAUNCHED_NO_LEDGER" if task_id in launched else "NOT_ATTEMPTED"),
               "generation_started": started, "response_completed": completed,
               "parse_kind": "NO_RESPONSE", "raw_response_sha256": None,
               "adapted_view_sha256": None, "schema_valid": False,
               "schema_errors": [], "facts": []}
        review_row = {"task_id": task_id, "reference_count": len(task["references"]),
                      "review_status": "NO_SCHEMA_VALID_RESPONSE", "facts": [],
                      "unmatched_reference_indices": []}
        if completed:
            raw_rel = attempt.get("raw_response_path")
            if not isinstance(raw_rel, str):
                raise ValueError(f"Completed attempt lacks raw response path: {task_id}")
            run_dir = attempt_ledger.parent.resolve()
            raw_path = (run_dir / raw_rel).resolve()
            if not raw_path.is_relative_to(run_dir) or not raw_path.is_file():
                raise ValueError(f"Raw response missing or outside run: {task_id}")
            raw_bytes = raw_path.read_bytes()
            if attempt.get("raw_response_sha256") and digest(raw_bytes) != attempt["raw_response_sha256"]:
                raise ValueError(f"Raw response hash mismatch: {task_id}")
            parsed = parse_response(raw_bytes)
            row.update({"parse_kind": parsed["kind"], "raw_response_sha256": parsed["raw_sha256"],
                        "adapted_view_sha256": parsed["view_sha256"], "parse_error": parsed["error"]})
            counts["strict_raw_json_valid_tasks"] += parsed["kind"] == "RAW_JSON"
            counts["fence_adapted_tasks"] += parsed["kind"] == "FENCE_ADAPTED"
            counts["json_valid_after_optional_adapter_tasks"] += parsed["obj"] is not None
            if parsed["kind"] == "FENCE_ADAPTED":
                labels["FORMAT_ERROR"] += 1  # usable view does not erase raw-format failure
                view_path = output_dir / "parsed_views" / f"{task_id}.json"
                view_path.parent.mkdir(parents=True, exist_ok=True)
                view_path.write_text(parsed["view_text"], encoding="utf-8", newline="")
                row["parsed_view_path"] = str(view_path)
                if digest(view_path.read_bytes()) != parsed["view_sha256"]:
                    raise ValueError(f"Adapted view byte mismatch: {task_id}")
            if parsed["obj"] is None:
                labels["FORMAT_ERROR"] += 1
            else:
                errors = schema_errors(parsed["obj"])
                row["schema_errors"] = errors
                row["schema_valid"] = not errors
                counts["schema_valid_after_optional_adapter_tasks"] += not errors
                counts["strict_raw_schema_valid_tasks"] += not errors and parsed["kind"] == "RAW_JSON"
                counts["fence_adapted_schema_valid_tasks"] += not errors and parsed["kind"] == "FENCE_ADAPTED"
                if errors:
                    labels["FORMAT_ERROR"] += parsed["kind"] != "FENCE_ADAPTED"
                else:
                    facts = parsed["obj"]["facts"]
                    counts["empty_fact_response_tasks"] += len(facts) == 0
                    counts["model_facts"] += len(facts)
                    seen_literal = set()
                    used_refs = set()
                    review_row["review_status"] = "CODEX_REVIEW_COMPLETE" if decisions_path else "CODEX_REVIEW_PENDING"
                    for index, fact in enumerate(facts):
                        evidence_valid = fact["evidence_id"] == task["input"]["evidence_id"]
                        quote_contiguous = fact["quote"] in task["text"]
                        value_in_quote = fact["value_text"] in fact["quote"]
                        value_in_passage = fact["value_text"] in task["text"]
                        literal = tuple(norm(fact[key]) for key in FIELDS)
                        duplicate_literal = literal in seen_literal
                        seen_literal.add(literal)
                        mech = {"fact_index": index, "evidence_id_valid": evidence_valid,
                                "quote_contiguous": quote_contiguous,
                                "value_in_quote": value_in_quote,
                                "value_in_passage": value_in_passage,
                                "duplicate_literal": duplicate_literal}
                        row["facts"].append(mech)
                        counts["valid_evidence_id_facts"] += evidence_valid
                        counts["quote_contiguous_facts"] += quote_contiguous
                        decision = decisions.get((task_id, index))
                        if decision is None and decisions_path:
                            raise ValueError(f"Missing fresh semantic decision: {task_id}/{index}")
                        if decision is None:
                            review_row["facts"].append({"fact_index": index, "status": "CODEX_REVIEW_PENDING"})
                            continue
                        consumed.add((task_id, index))
                        counts["reviewed_facts"] += 1
                        ref_idx = decision["reference_index"]
                        if ref_idx is not None and not 0 <= ref_idx < len(task["references"]):
                            raise ValueError(f"Reference index outside task: {task_id}/{index}")
                        duplicate_ref = ref_idx is not None and ref_idx in used_refs
                        if ref_idx is not None and not duplicate_ref:
                            used_refs.add(ref_idx)
                        duplicate = duplicate_literal or duplicate_ref
                        counts["duplicate_output_facts"] += duplicate
                        labels["DUPLICATE"] += duplicate
                        if decision["semantic_support"] == "SUPPORTED" and not (evidence_valid and quote_contiguous):
                            raise ValueError(f"Supported decision contradicts mechanical quote check: {task_id}/{index}")
                        counts["semantic_supported_facts"] += decision["semantic_support"] == "SUPPORTED"
                        counts["semantic_partial_facts"] += decision["semantic_support"] == "PARTIAL"
                        counts["semantic_unsupported_facts"] += decision["semantic_support"] == "UNSUPPORTED"
                        counts["semantic_unresolved_facts"] += decision["semantic_support"] == "UNRESOLVED"
                        for field in FIELDS:
                            state = decision["field_checks"][field]
                            field_counts[field][state] += 1
                            private_error_table.append({"task_id": task_id,
                                "response_sha256": parsed["raw_sha256"], "fact_index": index,
                                "reference_index": ref_idx, "field": field, "raw_value": fact[field],
                                "reference_value": task["references"][ref_idx][field] if ref_idx is not None else None,
                                "evidence_locator": task["input"]["source_locator"],
                                "judgment": state, "reason": decision.get("reason", decision.get("note", "")),
                                "attribution": decision.get("error_attribution", {}).get(field),
                                "reference_sha256": ref_hash})
                        labels.update(decision.get("error_labels", []))
                        if ref_idx is None or duplicate:
                            counts["unmatched_output_facts"] += 1
                        unresolved = (decision["semantic_support"] == "UNRESOLVED" or
                                      any(v == "UNKNOWN" for v in decision["field_checks"].values()))
                        counts["unresolved_output_facts"] += unresolved
                        matched = ref_idx is not None and not duplicate
                        full_correct = (matched and decision["semantic_support"] == "SUPPORTED" and
                                        all(v == "CORRECT" for v in decision["field_checks"].values()) and
                                        evidence_valid and quote_contiguous and value_in_quote and value_in_passage and
                                        numeric_tokens(fact["value_text"]) ==
                                        numeric_tokens(task["references"][ref_idx]["value_text"]))
                        if matched and all(v == "CORRECT" for v in decision["field_checks"].values()) and not full_correct and not unresolved:
                            raise ValueError(f"All-correct review contradicts literal value/quote or support: {task_id}/{index}")
                        counts["fully_correct_facts"] += full_correct
                        counts["matched_with_field_error_facts"] += matched and not full_correct and not unresolved
                        review_row["facts"].append({"fact_index": index, "reference_index": ref_idx,
                            "matched_one_to_one": matched, "duplicate": duplicate,
                            "semantic_support": decision["semantic_support"],
                            "field_checks": decision["field_checks"], "fully_correct": full_correct,
                            "error_labels": decision.get("error_labels", []), "reviewer": "CODEX_REVIEW"})
                    if decisions_path or not facts:
                        missing = [i for i in range(len(task["references"])) if i not in used_refs]
                        review_row["unmatched_reference_indices"] = missing
                        counts["matched_reference_facts"] += len(used_refs)
                        counts["unmatched_reference_facts"] += len(missing)
                        labels["MISSING_FACT"] += len(missing)
                    if not facts:
                        review_row["review_status"] = "EMPTY_FACTS_NOT_SUCCESS"
        mechanical_rows.append(row)
        review_rows.append(review_row)
    if decisions_path and set(decisions) != consumed:
        raise ValueError(f"Semantic decisions refer to absent/schema-invalid facts: {set(decisions) - consumed}")
    if decisions_path:
        # Nonresponse and malformed tasks also leave all their reference items missing.
        for row in review_rows:
            if row["review_status"] == "NO_SCHEMA_VALID_RESPONSE":
                n = row["reference_count"]
                row["unmatched_reference_indices"] = list(range(n))
                counts["unmatched_reference_facts"] += n
                labels["MISSING_FACT"] += n
        if counts["matched_reference_facts"] + counts["unmatched_reference_facts"] != counts["reference_facts"]:
            raise ValueError("One-to-one reference accounting failed")
    mechanical = {"schema": "shortfacts_mechanical_validation_v2",
        "manifest_sha256": digest(manifest_path.read_bytes()), "reference_sha256": ref_hash,
        "schema_sha256": digest(schema_path.read_bytes()) if schema_path else None,
        "attempt_ledger_sha256": digest(attempt_ledger.read_bytes()),
        "counts": dict(counts), "tasks": mechanical_rows}
    review = {"schema": "shortfacts_field_review_v2", "reviewer": "CODEX_REVIEW_NOT_HUMAN_GOLD",
              "decisions_sha256": digest(decisions_path.read_bytes()) if decisions_path else None,
              "reference_sha256": ref_hash, "mechanical_quote_presence_is_not_semantic_support": True,
              "tasks": review_rows}
    summary = {"schema": "shortfacts_error_summary_v2", "counts": dict(counts),
        "fields": {field: {**dict(status), "denominator": sum(status.values())}
                   for field, status in field_counts.items()},
        "error_labels": dict(labels),
        "complete_correct_recall": {"numerator": counts["fully_correct_facts"],
                                    "denominator": counts["reference_facts"]},
        "interpretation": "Six reused development passages; same-executor reference and review are not independent human gold. Strict raw JSON and fence-adapted usability differ."}
    for name, obj in (("mechanical_validation.json", mechanical),
                      ("field_review.json", review), ("error_summary.json", summary),
                      ("private_error_table.json", {"schema": "shortfacts_private_error_table_v2",
                                                    "rows": private_error_table})):
        write_json(output_dir / name, obj)
    return mechanical, review, summary


def write_public_summary(path: Path, mechanical: dict, summary: dict) -> None:
    """Only hashes, counts, and task-level statuses; no source text or model content."""
    obj = {"schema": "shortfacts_public_validation_summary_v2",
           "manifest_sha256": mechanical["manifest_sha256"],
           "reference_sha256": mechanical["reference_sha256"],
           "schema_sha256": mechanical["schema_sha256"],
           "attempt_ledger_sha256": mechanical["attempt_ledger_sha256"],
           "counts": summary["counts"], "fields": summary["fields"],
           "error_labels": summary["error_labels"],
           "complete_correct_recall": summary["complete_correct_recall"],
           "tasks": [{"task_id": row["task_id"], "attempt_status": row["attempt_status"],
                      "generation_started": row["generation_started"],
                      "response_completed": row["response_completed"],
                      "parse_kind": row["parse_kind"], "schema_valid": row["schema_valid"],
                      "model_fact_count": len(row["facts"])} for row in mechanical["tasks"]],
           "interpretation": summary["interpretation"]}
    write_json(path, obj)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("manifest", "reference", "attempt-ledger", "output-dir"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--review-decisions", type=Path)
    parser.add_argument("--schema", type=Path)
    parser.add_argument("--public-summary", type=Path)
    args = parser.parse_args()
    private_root = (ROOT / "artifacts").resolve()
    for name in ("manifest", "reference", "attempt_ledger", "output_dir", "review_decisions"):
        path = getattr(args, name)
        if path is not None and not path.resolve().is_relative_to(private_root):
            parser.error(f"{name} must remain under artifacts/")
    if args.public_summary and not args.public_summary.resolve().is_relative_to((ROOT / "results").resolve()):
        parser.error("public summary must be under results/")
    mechanical, _, summary = evaluate(args.manifest, args.reference, args.attempt_ledger,
                                        args.output_dir, args.review_decisions, args.schema)
    if args.public_summary:
        write_public_summary(args.public_summary, mechanical, summary)
    print(json.dumps(summary["counts"], sort_keys=True))


if __name__ == "__main__":
    main()
