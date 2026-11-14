"""Compare two finished, audited N1 development smokes without publishing rows.

The private run directories contain source text, prompts and model responses.
Only aggregate counts, hashes and denominator-aware mechanical rates leave
ignored ``artifacts/``. This is a paired engineering comparison, not an
independent extraction-quality or financial experiment.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import statistics
import sys


REPO = Path(__file__).resolve().parents[1]
OLD_RUN = REPO / "artifacts/news_first_inference_20261114/qwen3b_aa8e725_run1"
NEW_RUN = REPO / "artifacts/news_schema_repair_20261114/qwen3b_prompt_v2_run1"
OLD_AUDIT = REPO / "results/news_first_inference_20261114/inference_aggregate.json"
NEW_AUDIT = REPO / "results/news_schema_repair_20261114/new_inference_aggregate.json"
OLD_CONFIG = REPO / "configs/news_n1_smoke_v2.json"
NEW_CONFIG = REPO / "configs/news_n1_smoke_v3_prompt_complete.json"
OUTPUT = REPO / "results/news_schema_repair_20261114/paired_comparison.json"
FIXED_ARTICLES = 10


class ComparisonError(RuntimeError):
    """A fixed, public-safe reason to stop rather than publish stale counts."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    if not path.is_file():
        raise ComparisonError("REQUIRED_FILE_MISSING")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ComparisonError("REQUIRED_JSON_INVALID") from exc
    if not isinstance(value, dict):
        raise ComparisonError("REQUIRED_JSON_OBJECT_MISSING")
    return value


def read_events(path: Path) -> list[dict]:
    if not path.is_file():
        raise ComparisonError("RUN_EVENT_LOG_MISSING")
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ComparisonError("RUN_EVENT_LOG_INVALID") from exc


def response_set_sha256(run_dir: Path, events: list[dict]) -> str:
    """Use the auditor's ordinal-and-file-digest response-set algorithm."""
    hashes = []
    for event in events:
        if event.get("event") != "WORKER_LAUNCH_COMMITTED":
            continue
        ordinal = event.get("ordinal")
        if not isinstance(ordinal, int) or not 1 <= ordinal <= FIXED_ARTICLES:
            raise ComparisonError("RESPONSE_EVENT_ORDINAL_INVALID")
        path = run_dir / f"response_{ordinal:02d}.json"
        if path.is_file():
            hashes.append(f"{ordinal}:{sha256(path)}")
    return hashlib.sha256("\n".join(hashes).encode("ascii")).hexdigest()


def finished_run(run_dir: Path, audit_path: Path) -> tuple[dict, dict, dict]:
    if not run_dir.resolve().is_relative_to((REPO / "artifacts").resolve()):
        raise ComparisonError("RUN_OUTSIDE_IGNORED_ARTIFACTS")
    events = read_events(run_dir / "run_events.jsonl")
    if (not events or events[-1].get("event") != "RUN_FINISHED"
            or sum(e.get("event") == "RUN_FINISHED" for e in events) != 1):
        raise ComparisonError("RUN_NOT_FINISHED")
    local = read_json(run_dir / "local_manifest.json")
    shareable = read_json(run_dir / "shareable_summary.json")
    audit = read_json(audit_path)
    hashes = audit.get("private_receipt_hashes", {})
    if (not local.get("run_finished_at_utc") or local.get("run_status") == "PREFLIGHT"
            or shareable.get("run_status") != local.get("run_status")
            or shareable.get("counts") != local.get("counts")
            or audit.get("audit_status") != "PASS"
            or audit.get("run_status") != local.get("run_status")
            or hashes.get("local_manifest_sha256") != sha256(run_dir / "local_manifest.json")
            or hashes.get("shareable_summary_sha256") != sha256(run_dir / "shareable_summary.json")
            or hashes.get("run_events_sha256") != sha256(run_dir / "run_events.jsonl")
            or hashes.get("token_preflight_sha256") != sha256(run_dir / "token_preflight.json")
            or audit.get("response_set_sha256") != response_set_sha256(run_dir, events)):
        raise ComparisonError("FINISHED_RUN_OR_AUDIT_BINDING_MISMATCH")
    return local, shareable, audit


def ordinal_statuses(local: dict) -> dict[int, dict]:
    rows = local.get("article_statuses", [])
    if (len(rows) != FIXED_ARTICLES or
            sorted(row.get("ordinal") for row in rows) != list(range(1, FIXED_ARTICLES + 1))):
        raise ComparisonError("FIXED_TEN_STATUS_COVERAGE_MISMATCH")
    allowed = {"INPUT_TOO_LONG", "CALL_TIMEOUT", "OOM_STOP", "MODEL_CALL_ERROR",
               "JSON_PARSE_ERROR", "SCHEMA_INVALID", "MECHANICAL_INVALID",
               "MECHANICAL_VALID", "ATTEMPTED_UNCLASSIFIED_RUN_ERROR"}
    for row in rows:
        status = row.get("status")
        if status not in allowed and not (isinstance(status, str) and
                                          status.startswith("NOT_ATTEMPTED_AFTER_")):
            raise ComparisonError("UNEXPECTED_ARTICLE_STATUS")
    return {row["ordinal"]: row for row in rows}


def ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def metric_rates(counts: dict) -> dict:
    attempted = counts["model_calls_attempted"]
    completed = counts["model_calls_completed"]
    conditional = {"json_parse_valid": completed,
                   "schema_valid": counts["json_parse_valid"],
                   "binding_valid": counts["schema_valid"],
                   "mechanical_valid": counts["schema_valid"]}
    rates = {}
    for name, denominator in conditional.items():
        numerator = counts[name]
        rates[name] = {
            "numerator": numerator,
            "conditional_denominator": denominator,
            "conditional_rate": ratio(numerator, denominator),
            "attempted_denominator": attempted,
            "attempted_rate": ratio(numerator, attempted),
            "fixed_target_denominator": FIXED_ARTICLES,
            "fixed_target_rate": ratio(numerator, FIXED_ARTICLES),
        }
    spans = counts["evidence_spans_checked"]
    rates["evidence_spans_exact"] = {
        "numerator": counts["evidence_spans_exact"],
        "span_denominator": spans,
        "span_rate": ratio(counts["evidence_spans_exact"], spans),
        "unit": "evidence_spans_not_articles",
    }
    return rates


def number_summary(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "minimum": None, "median": None,
                "maximum": None, "mean": None}
    return {"count": len(values), "minimum": min(values),
            "median": statistics.median(values), "maximum": max(values),
            "mean": sum(values) / len(values)}


def demo_output(prompt_path: Path) -> dict:
    text = prompt_path.read_text(encoding="utf-8")
    try:
        block = text.split("BEGIN_SYNTHETIC_OUTPUT\n", 1)[1].split(
            "\nEND_SYNTHETIC_OUTPUT", 1)[0]
        value = json.loads(block)
    except (IndexError, json.JSONDecodeError) as exc:
        raise ComparisonError("SYNTHETIC_DEMO_NOT_PARSEABLE") from exc
    if not isinstance(value, dict) or not isinstance(value.get("input_binding"), dict):
        raise ComparisonError("SYNTHETIC_DEMO_INVALID")
    return value


def schema_valid_distributions(run_dir: Path, statuses: dict[int, dict],
                               demo: dict | None = None) -> tuple[dict, dict, list[dict]]:
    disposition = Counter()
    record_counts = Counter()
    empty_records = all_unknown_records = 0
    example_exact = example_binding = example_record_marker = 0
    response_count = 0
    private = []
    marker = None
    if demo:
        records = demo.get("records", [])
        if records:
            marker = (records[0].get("target_company_text"), records[0].get("event_type"))
    for ordinal in sorted(statuses):
        status = statuses[ordinal]
        response_path = run_dir / f"response_{ordinal:02d}.json"
        parsed_path = run_dir / f"parsed_{ordinal:02d}.json"
        detail = {"ordinal": ordinal, "status": status["status"],
                  "response_sha256": sha256(response_path) if response_path.is_file() else None}
        output = None
        if response_path.is_file():
            response_count += 1
            response = read_json(response_path)
            try:
                output = json.loads(response["raw_text"])
            except (KeyError, TypeError, json.JSONDecodeError):
                pass
            if demo and isinstance(output, dict):
                exact = output == demo
                binding = output.get("input_binding") == demo["input_binding"]
                output_records = output.get("records") if isinstance(output.get("records"), list) else []
                record_marker = bool(marker and any(
                    isinstance(item, dict) and
                    (item.get("target_company_text"), item.get("event_type")) == marker
                    for item in output_records
                ))
                example_exact += int(exact)
                example_binding += int(binding)
                example_record_marker += int(record_marker)
                detail["synthetic_demo_exact_object"] = exact
                detail["synthetic_demo_binding_reused"] = binding
                detail["synthetic_demo_record_marker"] = record_marker
        if parsed_path.is_file():
            parsed = read_json(parsed_path)
            if parsed.get("output") != output:
                raise ComparisonError("PARSED_RESPONSE_MISMATCH")
            validation = parsed.get("validation", {})
            if validation.get("schema_valid"):
                if not isinstance(output, dict) or not isinstance(output.get("records"), list):
                    raise ComparisonError("SCHEMA_VALID_OUTPUT_SHAPE_INVALID")
                disposition[output["article_disposition"]] += 1
                n_records = len(output["records"])
                record_counts[str(n_records)] += 1
                empty_records += int(n_records == 0)
                all_unknown_records += int(n_records > 0 and all(
                    rec.get("business_impact_direction") == "UNKNOWN" for rec in output["records"]))
                detail["schema_valid_disposition"] = output["article_disposition"]
                detail["schema_valid_record_count"] = n_records
        private.append(detail)
    public_distribution = {
        "schema_valid_response_denominator": sum(disposition.values()),
        "article_disposition_counts": dict(sorted(disposition.items())),
        "records_per_response_counts": dict(sorted(record_counts.items(), key=lambda kv: int(kv[0]))),
        "empty_records_response_count": empty_records,
        "nonempty_all_unknown_direction_response_count": all_unknown_records,
        "limits": "Distributions include schema-valid raw outputs only; no semantic correctness claim.",
    }
    demo_counts = {"response_denominator": response_count,
                   "exact_demo_object_count": example_exact,
                   "demo_input_binding_reused_count": example_binding,
                   "demo_company_and_event_marker_count": example_record_marker,
                   "interpretation": "These are strict copying indicators, not semantic error counts."}
    return public_distribution, demo_counts, private


def elapsed_seconds(local: dict) -> float:
    start = datetime.fromisoformat(local["run_started_at_utc"])
    finish = datetime.fromisoformat(local["run_finished_at_utc"])
    return (finish - start).total_seconds()


def check_new_response_provenance(old_run: Path, new_run: Path,
                                  old_local: dict, new_local: dict) -> dict:
    """Reject copied response files and responses outside the new run window."""
    new_start = datetime.fromisoformat(new_local["run_started_at_utc"])
    new_finish = datetime.fromisoformat(new_local["run_finished_at_utc"])
    old_finish = datetime.fromisoformat(old_local["run_finished_at_utc"])
    if new_start <= old_finish:
        raise ComparisonError("PAIR_RUN_TIME_ORDER_INVALID")
    matched_raw_text = 0
    compared = 0
    for ordinal in range(1, FIXED_ARTICLES + 1):
        old_path = old_run / f"response_{ordinal:02d}.json"
        new_path = new_run / f"response_{ordinal:02d}.json"
        if not new_path.is_file():
            continue
        new_response = read_json(new_path)
        try:
            response_time = datetime.fromisoformat(new_response["finished_at_utc"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ComparisonError("NEW_RESPONSE_TIMESTAMP_INVALID") from exc
        if not new_start <= response_time <= new_finish:
            raise ComparisonError("NEW_RESPONSE_OUTSIDE_RUN_WINDOW")
        if old_path.is_file():
            compared += 1
            if sha256(old_path) == sha256(new_path):
                raise ComparisonError("OLD_RESPONSE_FILE_REUSED")
            matched_raw_text += int(read_json(old_path).get("raw_text") ==
                                    new_response.get("raw_text"))
    return {"new_response_files_checked": len(list(new_run.glob("response_*.json"))),
            "paired_response_text_equal_count": matched_raw_text,
            "paired_response_text_comparison_denominator": compared,
            "interpretation": "File identity and run-window checks reject direct reuse. Equal generated text alone does not prove reuse."}


def compare(old_run: Path, new_run: Path, old_audit_path: Path,
            new_audit_path: Path) -> tuple[dict, dict]:
    old_local, old_share, old_audit = finished_run(old_run, old_audit_path)
    new_local, new_share, new_audit = finished_run(new_run, new_audit_path)
    old_config = read_json(OLD_CONFIG)
    new_config = read_json(NEW_CONFIG)
    common_keys = ("source", "model_preference", "model_path_policy", "device", "dtype",
                   "context_cap_tokens", "generation", "budget", "model_input_fields",
                   "forbidden_model_inputs", "model_revision", "attention_implementation",
                   "runtime_policy")
    if any(old_config.get(key) != new_config.get(key) for key in common_keys):
        raise ComparisonError("FROZEN_RUN_CONFIG_INVARIANT_MISMATCH")
    for kind in ("schema", "validator"):
        for suffix in ("path", "sha256"):
            if old_config["contract"].get(f"{kind}_{suffix}") != new_config["contract"].get(
                    f"{kind}_{suffix}"):
                raise ComparisonError("FROZEN_CONTRACT_MISMATCH")
    if (old_config["contract"].get("prompt_sha256") == new_config["contract"].get("prompt_sha256")
            or old_config["contract"].get("prompt_path") == new_config["contract"].get("prompt_path")):
        raise ComparisonError("PROMPT_NOT_VERSIONED")
    bindings = ("input_sample_sha256", "input_manifest_sha256", "runner_sha256",
                "schema_sha256", "validator_sha256")
    if any(old_local.get(key) != new_local.get(key) for key in bindings):
        raise ComparisonError("RUN_INPUT_OR_CODE_DRIFT")
    if (old_local.get("config_sha256") != sha256(OLD_CONFIG)
            or new_local.get("config_sha256") != sha256(NEW_CONFIG)
            or old_local.get("prompt_sha256") != old_config["contract"]["prompt_sha256"]
            or new_local.get("prompt_sha256") != new_config["contract"]["prompt_sha256"]
            or old_local.get("model", {}).get("file_hashes") != new_local.get("model", {}).get("file_hashes")
            or old_local.get("model", {}).get("generation_config_sha256") != new_local.get(
                "model", {}).get("generation_config_sha256")
            or old_local.get("model", {}).get("tokenizer_chat_template_sha256") != new_local.get(
                "model", {}).get("tokenizer_chat_template_sha256")):
        raise ComparisonError("PROMPT_OR_MODEL_BINDING_MISMATCH")
    if (old_local.get("runtime") != new_local.get("runtime")
            or old_local.get("budgets") != new_local.get("budgets")
            or old_local.get("attention_implementation") != new_local.get("attention_implementation")):
        raise ComparisonError("RUNTIME_OR_BUDGET_DRIFT")
    for local, audit in ((old_local, old_audit), (new_local, new_audit)):
        if (audit.get("config_sha256") != local.get("config_sha256")
                or audit.get("prompt_sha256") != local.get("prompt_sha256")
                or audit.get("sample_sha256") != local.get("input_sample_sha256")
                or audit.get("runner_sha256") != local.get("runner_sha256")):
            raise ComparisonError("AUDIT_BINDING_MISMATCH")
    old_status = ordinal_statuses(old_local)
    new_status = ordinal_statuses(new_local)
    transitions = Counter(f"{old_status[i]['status']}->{new_status[i]['status']}"
                          for i in range(1, FIXED_ARTICLES + 1))
    old_preflight = read_json(old_run / "token_preflight.json")
    new_preflight = read_json(new_run / "token_preflight.json")
    old_tokens = {row["ordinal"]: row["prompt_tokens"] for row in old_preflight["articles"]}
    new_tokens = {row["ordinal"]: row["prompt_tokens"] for row in new_preflight["articles"]}
    if (sorted(old_tokens) != list(range(1, FIXED_ARTICLES + 1))
            or sorted(new_tokens) != list(range(1, FIXED_ARTICLES + 1))):
        raise ComparisonError("PREFLIGHT_PAIR_COVERAGE_MISMATCH")
    deltas = [new_tokens[i] - old_tokens[i] for i in range(1, FIXED_ARTICLES + 1)]
    prompt_path = REPO / new_config["contract"]["prompt_path"]
    demo = demo_output(prompt_path)
    old_distribution, _, old_private = schema_valid_distributions(old_run, old_status)
    new_distribution, demo_counts, new_private = schema_valid_distributions(new_run, new_status, demo)
    provenance = check_new_response_provenance(old_run, new_run, old_local, new_local)
    old_counts, new_counts = old_local["counts"], new_local["counts"]
    if (old_distribution["schema_valid_response_denominator"] != old_counts["schema_valid"]
            or new_distribution["schema_valid_response_denominator"] != new_counts["schema_valid"]):
        raise ComparisonError("SCHEMA_VALID_DISTRIBUTION_COUNT_MISMATCH")
    old_wall = [row["seconds"] for row in old_status.values() if "seconds" in row]
    new_wall = [row["seconds"] for row in new_status.values() if "seconds" in row]
    response_statuses = {"JSON_PARSE_ERROR", "SCHEMA_INVALID", "MECHANICAL_INVALID",
                         "MECHANICAL_VALID"}
    paired_complete_deltas = [new_status[i]["seconds"] - old_status[i]["seconds"]
                              for i in range(1, FIXED_ARTICLES + 1)
                              if old_status[i]["status"] in response_statuses
                              and new_status[i]["status"] in response_statuses
                              and "seconds" in old_status[i] and "seconds" in new_status[i]]
    private_rows = []
    for i in range(1, FIXED_ARTICLES + 1):
        private_rows.append({"ordinal": i, "old": old_private[i - 1], "new": new_private[i - 1],
                             "old_prompt_tokens": old_tokens[i], "new_prompt_tokens": new_tokens[i],
                             "prompt_token_delta": deltas[i - 1],
                             "old_wall_seconds": old_status[i].get("seconds"),
                             "new_wall_seconds": new_status[i].get("seconds")})
    private = {"schema": "news_schema_repair_private_pair_v1", "rows": private_rows,
               "old_run_dir": str(old_run.resolve()), "new_run_dir": str(new_run.resolve())}
    private_bytes = (json.dumps(private, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    public = {
        "schema": "news_schema_repair_paired_comparison_v1",
        "date": "2026-11-14", "timezone": "Asia/Shanghai",
        "comparison_status": "PASS_BOUND_DEVELOPMENT_PAIR",
        "fixed_target_articles": FIXED_ARTICLES,
        "model_revision": new_config["model_revision"],
        "sample_sha256": new_local["input_sample_sha256"],
        "schema_sha256": new_local["schema_sha256"],
        "validator_sha256": new_local["validator_sha256"],
        "runner_sha256": new_local["runner_sha256"],
        "old_config_sha256": old_local["config_sha256"],
        "new_config_sha256": new_local["config_sha256"],
        "old_prompt_sha256": old_local["prompt_sha256"],
        "new_prompt_sha256": new_local["prompt_sha256"],
        "old_audit_sha256": sha256(old_audit_path),
        "new_audit_sha256": sha256(new_audit_path),
        "old_private_manifest_sha256": sha256(old_run / "local_manifest.json"),
        "new_private_manifest_sha256": sha256(new_run / "local_manifest.json"),
        "private_pair_sha256": hashlib.sha256(private_bytes).hexdigest(),
        "old": {"status_counts": dict(sorted(Counter(x["status"] for x in old_status.values()).items())),
                "attempted": old_counts["model_calls_attempted"],
                "completed_responses": old_counts["model_calls_completed"],
                "metric_rates": metric_rates(old_counts),
                "schema_valid_output_distribution": old_distribution},
        "new": {"status_counts": dict(sorted(Counter(x["status"] for x in new_status.values()).items())),
                "attempted": new_counts["model_calls_attempted"],
                "completed_responses": new_counts["model_calls_completed"],
                "metric_rates": metric_rates(new_counts),
                "schema_valid_output_distribution": new_distribution,
                "synthetic_demo_copy_indicators": demo_counts},
        "status_transition_counts": dict(sorted(transitions.items())),
        "paired_status_coverage": FIXED_ARTICLES,
        "new_response_provenance": provenance,
        "prompt_token_change": {**number_summary(deltas),
                                "increased": sum(x > 0 for x in deltas),
                                "unchanged": sum(x == 0 for x in deltas),
                                "decreased": sum(x < 0 for x in deltas)},
        "timing": {"old_run_wall_seconds": elapsed_seconds(old_local),
                   "new_run_wall_seconds": elapsed_seconds(new_local),
                   "old_attempt_wall_seconds": number_summary(old_wall),
                   "new_attempt_wall_seconds": number_summary(new_wall),
                   "paired_completed_attempt_wall_seconds_new_minus_old": number_summary(
                       paired_complete_deltas),
                   "interpretation": "Per-call wall seconds include subprocess startup, cold model load, tokenization and generation; timeout and prompt-length changes affect comparisons. These are not stable throughput or causal quality estimates."},
        "limitations": ["The first ten articles were reused after inspecting prior outputs; this is development material, not a blind test.",
                       "The complete Schema and synthetic example changed together; any difference cannot be attributed to one component.",
                       "Format, binding and span checks are mechanical; independent human extraction-quality labels are absent.",
                       "Historical financial admission, prediction and return evaluation remain NOT_RUN."],
    }
    return public, private


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-run", type=Path, default=OLD_RUN)
    parser.add_argument("--new-run", type=Path, default=NEW_RUN)
    parser.add_argument("--old-audit", type=Path, default=OLD_AUDIT)
    parser.add_argument("--new-audit", type=Path, default=NEW_AUDIT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.output.exists() or (args.new_run / "paired_comparison_private.json").exists():
        print("OUTPUT_ALREADY_EXISTS", file=sys.stderr)
        return 2
    try:
        public, private = compare(args.old_run, args.new_run,
                                  args.old_audit, args.new_audit)
    except ComparisonError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    public_bytes = (json.dumps(public, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if re.search(rb"[A-Za-z]:\\|hf_[A-Za-z0-9]{20,}|raw_text|source_row_ordinal",
                 public_bytes, re.IGNORECASE):
        print("PUBLIC_PRIVACY_PATTERN", file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(public_bytes)
    (args.new_run / "paired_comparison_private.json").write_bytes(
        (json.dumps(private, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(json.dumps({"comparison_status": public["comparison_status"],
                      "fixed_target_articles": FIXED_ARTICLES,
                      "old_attempted": public["old"]["attempted"],
                      "new_attempted": public["new"]["attempted"],
                      "transitions": public["status_transition_counts"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
