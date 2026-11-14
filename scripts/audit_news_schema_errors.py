"""Offline diagnosis of the immutable first N1 model run's schema failures.

The private receipt contains exact model values and validator details. The
shareable receipt contains only aggregate counts and source hashes. This script
never calls a model, changes an old response, or treats formatting as accuracy.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re

from jsonschema import Draft202012Validator


REPO = Path(__file__).resolve().parents[1]
RUN = REPO / "artifacts/news_first_inference_20261114/qwen3b_aa8e725_run1"
PRIVATE = REPO / "artifacts/news_schema_repair_20261114/old_error_audit"
PUBLIC = REPO / "results/news_schema_repair_20261114/old_error_aggregate.json"
BASELINE = REPO / "results/news_first_inference_20261114/inference_aggregate.json"
SAMPLE = REPO / "artifacts/news_n1_20261114/upstream_nasdaq_bf9189c_run1/raw_fields_with_body.jsonl"
SAMPLE_MANIFEST = REPO / "artifacts/news_n1_20261114/upstream_nasdaq_bf9189c_run1/local_manifest.json"
SCHEMA = REPO / "docs/news_research/company_event_output.schema.json"
PROMPT = REPO / "docs/news_research/company_event_prompt.md"
CONFIG = REPO / "configs/news_n1_smoke_v2.json"
RUNNER = REPO / "scripts/run_news_n1_smoke.py"
VALIDATOR = REPO / "scripts/validate_news_company_event.py"
HOTSPOTS = ("article_disposition", "input_binding", "market_outcome_reported",
            "schema_version", "article_evidence")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def module_at(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pointer(parts) -> str:
    return "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def value_type(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    return type(value).__name__


def missing_name(error) -> str | None:
    if error.validator != "required" or not isinstance(error.instance, dict):
        return None
    match = re.fullmatch(r"'([^']+)' is a required property", error.message)
    if match and match.group(1) in error.validator_value:
        return match.group(1)
    return None


def diagnostic_code(error) -> str:
    parts = list(error.path)
    head = str(parts[0]) if parts else "ROOT"
    actual = error.instance
    key = error.validator
    if head == "article_disposition" and key == "enum":
        allowed = error.validator_value
        if isinstance(actual, str) and any(actual.casefold() == x.casefold() for x in allowed):
            return "DISPOSITION_CASE_ONLY"
        return "DISPOSITION_NONCANONICAL_STRING" if isinstance(actual, str) else "DISPOSITION_WRONG_TYPE"
    if head == "input_binding" and key == "type":
        return "BINDING_STRING_NOT_OBJECT" if isinstance(actual, str) else "BINDING_WRONG_TYPE"
    if head == "market_outcome_reported" and key == "enum":
        return "MARKET_FLAG_BOOLEAN_NOT_TRISTATE" if isinstance(actual, bool) else "MARKET_FLAG_NONCANONICAL"
    if head == "schema_version" and key == "const":
        return "SCHEMA_VERSION_WRONG_CONSTANT"
    if head == "article_evidence" and key == "required":
        name = missing_name(error)
        return "EVIDENCE_SPAN_MISSING_" + name.upper() if name else "EVIDENCE_SPAN_MISSING_FIELD"
    if head == "article_evidence" and key == "additionalProperties":
        return "EVIDENCE_SPAN_EXTRA_FIELD"
    if head == "article_evidence" and key == "type":
        return "EVIDENCE_WRONG_TYPE"
    if head == "ROOT" and key == "required":
        name = missing_name(error)
        return "ROOT_MISSING_" + name.upper() if name else "ROOT_MISSING_FIELD"
    return "OTHER_SCHEMA_ERROR"


def exact_error(error) -> dict:
    missing = missing_name(error)
    extra = None
    if error.validator == "additionalProperties" and isinstance(error.instance, dict):
        extra = {key: error.instance[key] for key in sorted(
            set(error.instance) - set(error.schema.get("properties", {})))}
    return {
        "instance_path": pointer(error.path),
        "schema_path": pointer(error.schema_path),
        "keyword": error.validator,
        "diagnostic_code": diagnostic_code(error),
        "expected_rule": error.validator_value,
        "actual_type": value_type(error.instance),
        "actual_value": error.instance,
        "missing_required_field": missing,
        "unexpected_fields_and_values": extra,
        "validator_message_private": error.message,
    }


def audit() -> tuple[dict, dict]:
    prior = read_json(BASELINE)
    local = read_json(RUN / "local_manifest.json")
    config = read_json(CONFIG)
    source_manifest = read_json(SAMPLE_MANIFEST)
    schema = read_json(SCHEMA)
    Draft202012Validator.check_schema(schema)
    json_validator = Draft202012Validator(schema)
    old_runner = module_at(RUNNER, "n1_runner_for_old_schema_diagnosis")
    old_validator = module_at(VALIDATOR, "n1_validator_for_old_schema_diagnosis")

    bindings = {
        "sample_sha256": sha256(SAMPLE),
        "sample_manifest_sha256": sha256(SAMPLE_MANIFEST),
        "config_sha256": sha256(CONFIG),
        "runner_sha256": sha256(RUNNER),
        "schema_sha256": sha256(SCHEMA),
        "prompt_sha256": sha256(PROMPT),
        "validator_sha256": sha256(VALIDATOR),
    }
    for key, actual in bindings.items():
        if actual != prior[key]:
            raise RuntimeError("PRIOR_BINDING_MISMATCH:" + key)
    for key, prior_key in (("input_sample_sha256", "sample_sha256"),
                           ("input_manifest_sha256", "sample_manifest_sha256"),
                           ("config_sha256", "config_sha256"),
                           ("runner_sha256", "runner_sha256"),
                           ("schema_sha256", "schema_sha256"),
                           ("prompt_sha256", "prompt_sha256"),
                           ("validator_sha256", "validator_sha256")):
        if local[key] != bindings[prior_key]:
            raise RuntimeError("LOCAL_BINDING_MISMATCH:" + key)
    if (local["run_status"] != "SMOKE_RUN_FINISHED"
            or local["counts"]["model_calls_attempted"] != 10
            or local["counts"]["model_calls_completed"] != 9):
        raise RuntimeError("PRIOR_RUN_STATUS_MISMATCH")
    events = [json.loads(line) for line in (RUN / "run_events.jsonl").read_text(
        encoding="utf-8").splitlines()]
    if events[-1].get("event") != "RUN_FINISHED":
        raise RuntimeError("PRIOR_RUN_UNFINISHED")

    rows = old_runner.first_ten_inputs(SAMPLE, SAMPLE_MANIFEST, config["source"]["sample_sha256"])
    prompt = PROMPT.read_text(encoding="utf-8")
    request_files = sorted(RUN.glob("request_*.json"))
    response_files = sorted(RUN.glob("response_*.json"))
    if len(request_files) != 10 or len(response_files) != 9:
        raise RuntimeError("PRIOR_RUN_FILE_COUNT_MISMATCH")
    system_messages = []
    for row, request_path in zip(rows, request_files):
        request = read_json(request_path)
        expected_input = old_runner.build_article_input(row, source_manifest)
        messages = old_runner.render_messages(prompt, expected_input)
        if request["messages"] != messages:
            raise RuntimeError("PRIOR_REQUEST_NOT_FROM_FROZEN_PROMPT")
        system_messages.append(messages[0]["content"])
    if len(set(system_messages)) != 1:
        raise RuntimeError("PRIOR_REQUEST_SYSTEM_CHANGED")
    system = system_messages[0]
    interface = {
        "requests_rechecked": len(request_files),
        "same_system_content_all_requests": True,
        "complete_schema_embedded_in_system": "BEGIN_OUTPUT_JSON_SCHEMA" in system,
        "company_events_found_literal_in_system": "COMPANY_EVENTS_FOUND" in system,
        "schema_link_outside_sent_messages": "company_event_output.schema.json" in prompt and
        "company_event_output.schema.json" not in system,
    }
    if interface["complete_schema_embedded_in_system"] or interface["company_events_found_literal_in_system"]:
        raise RuntimeError("PRIOR_PROMPT_INTERFACE_ASSUMPTION_CHANGED")

    keyword_occurrences = Counter()
    keyword_responses: dict[str, set[int]] = defaultdict(set)
    hotspot_occurrences = Counter()
    hotspot_responses: dict[str, set[int]] = defaultdict(set)
    diagnostic_occurrences = Counter()
    diagnostic_responses: dict[str, set[int]] = defaultdict(set)
    missing_occurrences = Counter()
    detailed = []
    response_hashes = []
    for response_path in response_files:
        ordinal = int(response_path.stem.split("_")[1])
        if not (1 <= ordinal <= 10):
            raise RuntimeError("PRIOR_RESPONSE_ORDINAL_INVALID")
        raw = read_json(response_path)
        output = json.loads(raw["raw_text"])
        row = rows[ordinal - 1]
        expected_input = old_runner.build_article_input(row, source_manifest)
        prior_parsed = read_json(RUN / f"parsed_{ordinal:02d}.json")
        check = old_validator.validate_output(output, expected_input["body"],
                                              expected_input["input_binding"], SCHEMA)
        if prior_parsed != {"output": output, "validation": check}:
            raise RuntimeError("PRIOR_MECHANICAL_RECHECK_MISMATCH")
        errors = sorted(json_validator.iter_errors(output), key=lambda e: tuple(map(str, e.path)))
        codes = [{"path": "/" + "/".join(map(str, error.path)),
                  "keyword": error.validator} for error in errors]
        if codes != check["schema_error_codes"] or not errors:
            raise RuntimeError("PRIOR_SCHEMA_ERROR_RECHECK_MISMATCH")
        response_hashes.append(f"{ordinal}:{sha256(response_path)}")
        item = {"source_row_ordinal": ordinal, "response_sha256": sha256(response_path),
                "original_output_unchanged": True,
                "schema_error_instances": len(errors),
                "errors": [exact_error(error) for error in errors]}
        for error in errors:
            keyword = error.validator.upper()
            hotspot = str(error.path[0]) if error.path else "ROOT"
            if hotspot not in HOTSPOTS:
                hotspot = "ROOT_OR_OTHER"
            diagnostic = diagnostic_code(error)
            keyword_occurrences[keyword] += 1
            keyword_responses[keyword].add(ordinal)
            hotspot_occurrences[hotspot] += 1
            hotspot_responses[hotspot].add(ordinal)
            diagnostic_occurrences[diagnostic] += 1
            diagnostic_responses[diagnostic].add(ordinal)
            if name := missing_name(error):
                missing_occurrences[name] += 1
        detailed.append(item)

    published_keywords = prior["schema_error_keyword_counts"]
    if (dict(sorted(keyword_occurrences.items())) != published_keywords
            or sum(keyword_occurrences.values()) != 87
            or hashlib.sha256("\n".join(response_hashes).encode("ascii")).hexdigest()
            != prior["response_set_sha256"]):
        raise RuntimeError("PRIOR_AGGREGATE_MISMATCH")
    timeout = [item for item in local["article_statuses"] if item["status"] == "CALL_TIMEOUT"]
    if len(timeout) != 1:
        raise RuntimeError("PRIOR_TIMEOUT_STATUS_MISMATCH")
    timeout_item = timeout[0]
    timeout_ordinal = timeout_item["ordinal"]
    phases = [json.loads(line) for line in (RUN / f"phase_{timeout_ordinal:02d}.jsonl").read_text(
        encoding="utf-8").splitlines()]
    phase_names = [item["event"] for item in phases]
    if (phase_names != ["WORKER_PROCESS_STARTED", "MODEL_LOADED", "GENERATION_STARTED"]
            or (RUN / f"response_{timeout_ordinal:02d}.json").exists()):
        raise RuntimeError("PRIOR_TIMEOUT_PHASE_MISMATCH")
    timeout_public = {
        "calls": 1,
        "known_phase_sequence": phase_names,
        "response_saved": False,
        "wall_clock_cap_seconds": config["budget"]["per_call_timeout_seconds"],
        "parent_elapsed_seconds_includes_cleanup": timeout_item.get("seconds"),
        "generated_token_count": None,
        "pure_generation_seconds": None,
        "failure_reason_beyond_wall_timeout": "UNKNOWN",
    }

    private = {
        "schema": "news_old_schema_error_private_v1",
        "date": "2026-11-14", "timezone": "Asia/Shanghai",
        "run_dir": str(RUN), "source_hashes": bindings,
        "request_interface": interface,
        "response_details": detailed,
        "timeout": {"source_row_ordinal": timeout_ordinal,
                    "phase_events_with_times": phases,
                    "status_record": timeout_item,
                    "response_absent": True},
    }
    public = {
        "schema": "news_old_schema_error_aggregate_v1",
        "date": "2026-11-14", "timezone": "Asia/Shanghai",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scope": "Offline revalidation of unchanged first-run raw responses; zero new model calls.",
        "source_commit": "585904f29d03d378b194cc714c84bd5be41db98f",
        "source_hashes": bindings,
        "prior_aggregate_sha256": sha256(BASELINE),
        "response_set_sha256": prior["response_set_sha256"],
        "requests_rechecked": len(request_files),
        "responses_revalidated": len(response_files),
        "schema_invalid_responses": len(response_files),
        "schema_error_instances": sum(keyword_occurrences.values()),
        "schema_error_keyword_occurrences": dict(sorted(keyword_occurrences.items())),
        "schema_error_keyword_unique_responses": {k: len(v) for k, v in sorted(keyword_responses.items())},
        "hotspot_occurrences": dict(sorted(hotspot_occurrences.items())),
        "hotspot_unique_responses": {k: len(v) for k, v in sorted(hotspot_responses.items())},
        "diagnostic_occurrences": dict(sorted(diagnostic_occurrences.items())),
        "diagnostic_unique_responses": {k: len(v) for k, v in sorted(diagnostic_responses.items())},
        "missing_required_field_occurrences": dict(sorted(missing_occurrences.items())),
        "prompt_interface_observation": interface,
        "timeout_observation": timeout_public,
        "interpretation": [
            "Error instances and unique responses are different denominators; one response can have multiple errors.",
            "The sent System message lacked the complete schema and COMPANY_EVENTS_FOUND literal; this is an observed interface gap, not proof that it caused every error.",
            "This audit does not repair model output or compute extraction accuracy; semantic and financial evaluation remain NOT_RUN.",
        ],
        "new_model_calls": 0,
    }
    return private, public


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-dir", type=Path, default=PRIVATE)
    parser.add_argument("--public-output", type=Path, default=PUBLIC)
    args = parser.parse_args()
    if args.private_dir.exists() or args.public_output.exists():
        parser.error("OUTPUT_ALREADY_EXISTS")
    ignored = (REPO / "artifacts").resolve()
    if not args.private_dir.resolve().is_relative_to(ignored):
        parser.error("PRIVATE_OUTPUT_MUST_BE_IGNORED")
    private, public = audit()
    args.private_dir.mkdir(parents=True)
    private_path = args.private_dir / "exact_error_details.json"
    private_path.write_text(json.dumps(private, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    public["private_details_sha256"] = sha256(private_path)
    args.public_output.parent.mkdir(parents=True, exist_ok=True)
    args.public_output.write_text(json.dumps(public, ensure_ascii=False, indent=2) + "\n",
                                  encoding="utf-8")
    print(json.dumps({"responses_revalidated": public["responses_revalidated"],
                      "schema_error_instances": public["schema_error_instances"],
                      "keyword_occurrences": public["schema_error_keyword_occurrences"],
                      "new_model_calls": 0}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
