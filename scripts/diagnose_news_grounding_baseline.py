"""Read-only diagnosis of saved v3 quote offsets and the terminal OOM.

Writes exact per-response values only to the private output. The public output
contains aggregate categories and hashes, never article or response text.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_once(path: Path, value: dict) -> None:
    data = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise FileExistsError(f"Existing diagnosis differs: {path}")
        return
    path.write_bytes(data)


def user_input(run_dir: Path, ordinal: int) -> dict:
    request = read_json(run_dir / f"request_{ordinal:02d}.json")
    users = [m["content"] for m in request["messages"] if m["role"] == "user"]
    if len(users) != 1:
        raise ValueError("Expected exactly one User message")
    prefix_end = users[0].find("{")
    if prefix_end < 0:
        raise ValueError("User message lacks input JSON")
    return json.loads(users[0][prefix_end:])


def source_body(rows: dict[int, dict], user: dict) -> str:
    binding = user["input_binding"]
    ordinal = binding["source_row_ordinal"]
    row = rows[ordinal]
    matching = [field for field in row["fields"] if isinstance(field, str)
                and hashlib.sha256(field.encode("utf-8")).hexdigest()
                == binding["body_sha256"]]
    if len(matching) != 1 or matching[0] != user["body"]:
        raise ValueError("Saved request body is not uniquely bound to source sample")
    return matching[0]


def parsed_raw_output(run_dir: Path, ordinal: int) -> tuple[dict, dict]:
    raw = read_json(run_dir / f"response_{ordinal:02d}.json")
    parsed = read_json(run_dir / f"parsed_{ordinal:02d}.json")
    if json.loads(raw["raw_text"]) != parsed["output"]:
        raise ValueError("Parsed output differs from unchanged raw response")
    return parsed["output"], parsed["validation"]


def overlapping_positions(body: str, quote: str) -> list[int]:
    if not quote:
        return []
    positions = []
    start = 0
    while (found := body.find(quote, start)) >= 0:
        positions.append(found)
        start = found + 1
    return positions


def all_spans(output: dict) -> list[tuple[str, dict]]:
    spans = [("article_evidence", item) for item in output["article_evidence"]]
    for record in output["records"]:
        for field in ("evidence_spans", "relationship_evidence_spans"):
            spans.extend((field, item) for item in record[field])
    return spans


def diagnose(run_dir: Path, sample: Path, config: Path, prior_aggregate: Path,
             public_output: Path, private_output: Path) -> None:
    manifest = read_json(run_dir / "local_manifest.json")
    aggregate = read_json(prior_aggregate)
    if (sha(sample) != manifest["input_sample_sha256"]
            or sha(sample) != aggregate["sample_sha256"]
            or sha(config) != manifest["config_sha256"]
            or sha(config) != aggregate["config_sha256"]
            or aggregate["audit_status"] != "PASS"
            or manifest["run_status"] != "OOM_STOP"):
        raise ValueError("Baseline identity or audited terminal state differs")
    rows_list = [json.loads(line) for line in sample.read_text(encoding="utf-8").splitlines()]
    rows = {r["source_row_ordinal"]: r for r in rows_list}
    if len(rows) != len(rows_list):
        raise ValueError("Duplicate source ordinals")

    first_user = user_input(run_dir, 1)
    first_body = source_body(rows, first_user)
    first_output, first_validation = parsed_raw_output(run_dir, 1)
    if ("ARTICLE_REASON_INCONSISTENT" not in first_validation["mechanical_issue_codes"]
            or first_output["input_binding"] != first_user["input_binding"]):
        raise ValueError("First response mismatch")

    second_user = user_input(run_dir, 2)
    second_body = source_body(rows, second_user)
    second_output, second_validation = parsed_raw_output(run_dir, 2)
    if ("SPAN_SLICE_MISMATCH" not in second_validation["mechanical_issue_codes"]
            or second_output["input_binding"] != second_user["input_binding"]):
        raise ValueError("Second response mismatch")
    details = []
    classes = Counter()
    exact_offsets = 0
    for field, span in all_spans(second_output):
        quote = span["text"]
        positions = overlapping_positions(second_body, quote)
        category = "MISSING" if not positions else ("UNIQUE" if len(positions) == 1 else "MULTIPLE")
        classes[category] += 1
        offset_exact = second_body[span["start"]:span["end"]] == quote
        exact_offsets += int(offset_exact)
        details.append({
            "field": field, "role": span["role"], "quote": quote,
            "reported_start": span["start"], "reported_end": span["end"],
            "reported_slice": second_body[span["start"]:span["end"]],
            "exact_match_positions": positions, "match_category": category,
            "reported_offset_exact": offset_exact,
        })
    if len(details) != second_validation["evidence_span_count"] or exact_offsets != second_validation["evidence_span_exact_match_count"]:
        raise ValueError("Exact-search result disagrees with frozen validator count")

    phase = [json.loads(line)["event"] for line in (run_dir / "phase_03.jsonl").read_text(encoding="utf-8").splitlines()]
    stderr = (run_dir / "stderr_03.txt").read_text(encoding="utf-8", errors="replace")
    if (phase != ["WORKER_PROCESS_STARTED", "MODEL_LOADED", "GENERATION_STARTED"]
            or "torch.AcceleratorError: CUDA error: out of memory" not in stderr
            or (run_dir / "response_03.json").exists()):
        raise ValueError("OOM evidence differs from audited terminal phase")

    private = {
        "schema": "news_grounding_old_failure_private_v1",
        "first_response": {
            "source_row_ordinal": first_user["input_binding"]["source_row_ordinal"],
            "body_sha256": hashlib.sha256(first_body.encode("utf-8")).hexdigest(),
            "article_disposition": first_output["article_disposition"],
            "article_unknown_reason": first_output["article_unknown_reason"],
            "mechanical_issue_codes": first_validation["mechanical_issue_codes"],
        },
        "second_response": {
            "source_row_ordinal": second_user["input_binding"]["source_row_ordinal"],
            "body_sha256": hashlib.sha256(second_body.encode("utf-8")).hexdigest(),
            "quotes": details,
        },
        "oom": {"phase_sequence": phase, "stderr_sha256": sha(run_dir / "stderr_03.txt"),
                "stderr_text": stderr},
    }
    write_once(private_output, private)
    public = {
        "schema": "news_grounding_old_failure_aggregate_v1",
        "date": "2026-11-14", "timezone": "Asia/Shanghai",
        "scope": "Two saved v3 development responses and the third attempted worker; no new model calls",
        "source_hashes": {
            "sample_sha256": sha(sample), "config_sha256": sha(config),
            "prior_audit_sha256": sha(prior_aggregate),
            "prior_manifest_sha256": sha(run_dir / "local_manifest.json"),
            "failed_phase_sha256": sha(run_dir / "phase_03.jsonl"),
            "failed_stderr_sha256": sha(run_dir / "stderr_03.txt"),
            "private_details_sha256": sha(private_output),
        },
        "old_quote_diagnosis": {
            "quotes_checked": len(details), "unique_exact_text": classes["UNIQUE"],
            "multiple_exact_text": classes["MULTIPLE"],
            "missing_exact_text": classes["MISSING"],
            "original_reported_offsets_exact": exact_offsets,
            "method": "Unchanged Unicode code-point substring search, including overlapping occurrences; no normalization or repair",
        },
        "old_reason_diagnosis": {
            "article_reason_inconsistent_confirmed": 1,
            "actual_disposition_and_reason": "PRIVATE_ROW_LEVEL_DETAIL",
        },
        "old_oom_diagnosis": {
            "failed_attempt": 3, "worker_phases": phase,
            "raw_response_saved": False,
            "exception": "torch.AcceleratorError: CUDA error: out of memory",
            "gpu_memory_at_failure": None,
            "cause_beyond_reported_allocation_failure": "UNKNOWN",
            "limitations": "No old per-phase GPU telemetry; neither the exact allocation pressure nor a causal effect of prompt length can be isolated.",
        },
        "old_output_unchanged": True,
    }
    write_once(public_output, public)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--sample", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--prior-aggregate", type=Path, required=True)
    parser.add_argument("--public-output", type=Path, required=True)
    parser.add_argument("--private-output", type=Path, required=True)
    args = parser.parse_args()
    diagnose(args.run_dir, args.sample, args.config, args.prior_aggregate,
             args.public_output, args.private_output)
    print("Old quote and OOM diagnosis: PASS (public aggregate and private detail saved)")


if __name__ == "__main__":
    main()
