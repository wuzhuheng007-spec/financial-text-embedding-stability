"""Read-only recheck of the saved v5 failure and GPU stages.

The private output records generated label values; it must live under ignored
``artifacts/``. The public output contains only aggregate field counts, hashes,
timeout stage receipts, and memory snapshots. This script makes no model call.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess

from jsonschema import Draft202012Validator


EXPECTED = {
    "MARKET_OUTCOME_DIRECTION:CONST": 4,
    "RECORDS.[].EVIDENCE_SPANS:MINITEMS": 2,
    "RECORDS.[].UNKNOWN_REASON:ENUM": 2,
    "RECORDS:MAXITEMS": 1,
}
EXPECTED_SHAPES = {
    "MARKET_OUTCOME_DIRECTION:CONST:string": 4,
    "RECORDS.[].EVIDENCE_SPANS:MINITEMS:array_length_1": 2,
    "RECORDS.[].UNKNOWN_REASON:ENUM:json_null": 2,
    "RECORDS:MAXITEMS:array_length_1": 1,
}
WIRE_INVALID = (1, 2, 3, 5, 6, 8)
TIMEOUTS = (4, 7, 9)
SAVED = (1, 2, 3, 5, 6, 8, 10)


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def error_key(error) -> str:
    pieces = ["[]" if isinstance(piece, int) else str(piece).upper()
              for piece in error.absolute_path]
    return ".".join(pieces) + ":" + str(error.validator).upper()


def gpu_now() -> dict:
    result = {"observed_at_local": datetime.now().astimezone().isoformat(),
              "query_available": False}
    try:
        gpu = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total,memory.used,memory.free",
             "--format=csv,noheader,nounits"], capture_output=True, text=True,
            timeout=10, check=True)
        row = [int(x.strip()) for x in gpu.stdout.splitlines()[0].split(",")]
        result.update(query_available=True, total_mib=row[0], used_mib=row[1],
                      free_mib=row[2])
        procs = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,used_gpu_memory",
             "--format=csv,noheader,nounits"], capture_output=True, text=True,
            timeout=10, check=True)
        result["compute_process_rows"] = sum(bool(line.strip()) for line in procs.stdout.splitlines())
    except (OSError, subprocess.SubprocessError, IndexError, ValueError):
        result["caution"] = "Current host GPU query unavailable; historical snapshots remain separate."
    return result


def recheck(run_dir: Path, schema_path: Path, aggregate_path: Path) -> tuple[dict, dict]:
    aggregate = read(aggregate_path)
    manifest_path = run_dir / "local_manifest.json"
    manifest = read(manifest_path)
    if (aggregate["run_status"] != "TARGETED_RUN_FINISHED"
            or aggregate["counts"]["model_calls_completed"] != 7
            or aggregate["counts"]["raw_json_valid"] != 6
            or aggregate["counts"]["wire_schema_valid"] != 0
            or manifest["config_sha256"] != aggregate["config_sha256"]
            or manifest["input_sample_sha256"] != aggregate["sample_sha256"]
            or sha(schema_path) != aggregate["contract_sha256"]["wire_schema"]):
        raise ValueError("Frozen v5 aggregate/input/Schema binding mismatch")

    schema = read(schema_path)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    fields: Counter[str] = Counter()
    shapes: Counter[str] = Counter()
    private_errors = []
    wire_hashes = {}
    for ordinal in WIRE_INVALID:
        path = run_dir / f"wire_{ordinal:02d}.json"
        wire_hashes[str(ordinal)] = sha(path)
        obj = read(path)["output"]
        for error in validator.iter_errors(obj):
            key = error_key(error)
            fields[key] += 1
            value = error.instance
            observed = ({"array_length": len(value)} if isinstance(value, list)
                        else {"type": type(value).__name__, "value": value})
            shape = (f"array_length_{len(value)}" if isinstance(value, list)
                     else "json_null" if value is None
                     else "string" if isinstance(value, str)
                     else type(value).__name__)
            shapes[f"{key}:{shape}"] += 1
            private_errors.append({"ordinal": ordinal, "field_keyword": key,
                                   "observed": observed,
                                   "constraint": error.validator_value})
    if (dict(fields) != EXPECTED or dict(shapes) != EXPECTED_SHAPES
            or fields != aggregate["wire_error_field_keyword_occurrences"]):
        raise ValueError("Saved wire objects do not reconcile to nine audited errors")

    statuses = {item["ordinal"]: item for item in manifest["article_statuses"]}
    timeout_rows = []
    for ordinal in TIMEOUTS:
        status = statuses[ordinal]
        phase_path = run_dir / f"phase_{ordinal:02d}.jsonl"
        phase = events(phase_path)
        before = [item for item in phase if item["event"] == "CUDA_MEMORY"
                  and item.get("stage") == "BEFORE_GENERATION"]
        if (status["status"] != "CALL_TIMEOUT" or len(before) != 1
                or phase[-1]["event"] != "GENERATION_STARTED"):
            raise ValueError(f"Unexpected timeout stage at ordinal {ordinal}")
        recorded_start = datetime.fromisoformat(phase[-1]["at_utc"])
        if recorded_start.tzinfo is None:
            raise ValueError(f"Timezone missing from saved phase at ordinal {ordinal}")
        shanghai_start = recorded_start.astimezone(timezone(timedelta(hours=8)))
        timeout_rows.append({
            "ordinal": ordinal, "wall_seconds": status["seconds"],
            "last_saved_worker_phase": phase[-1]["event"],
            "generation_started_at_asia_shanghai": shanghai_start.isoformat(),
            "before_generation_free_bytes": before[0]["free_bytes"],
            "before_generation_allocated_bytes": before[0]["allocated_bytes"],
            "before_generation_reserved_bytes": before[0]["reserved_bytes"],
            "phase_sha256": sha(phase_path),
            "intermediate_token_timing_saved": False,
        })

    after_values = []
    for ordinal in SAVED:
        phase = events(run_dir / f"phase_{ordinal:02d}.jsonl")
        after = [item for item in phase if item["event"] == "CUDA_MEMORY"
                 and item.get("stage") == "AFTER_GENERATION"]
        if len(after) != 1 or not after[0]["available"]:
            raise ValueError(f"Missing CUDA API snapshot at ordinal {ordinal}")
        after_values.append(after[0]["free_bytes"])
    if after_values != [0] * 7:
        raise ValueError("Observed post-generation GPU free-byte values changed")

    private = {"schema": "v5_failure_private_recheck_v1",
               "error_instances": private_errors,
               "note": "Generated label values only; ignored local artifact. No source article text."}
    public = {
        "schema": "news_decode_v5_evidence_recheck_v1",
        "date": datetime.now().astimezone().date().isoformat(),
        "timezone": "Asia/Shanghai", "model_calls": 0,
        "v5_aggregate_sha256": sha(aggregate_path),
        "v5_local_manifest_sha256": sha(manifest_path),
        "wire_schema_sha256": sha(schema_path),
        "sample_sha256": manifest["input_sample_sha256"],
        "config_sha256": manifest["config_sha256"],
        "wire_input_sha256_by_ordinal": wire_hashes,
        "json_valid_responses_reviewed": len(WIRE_INVALID),
        "schema_error_instances": sum(fields.values()),
        "field_keyword_occurrences": dict(sorted(fields.items())),
        "observed_shape_counts": dict(sorted(shapes.items())),
        "actual_generated_values_reviewed_private": True,
        "timeouts": timeout_rows,
        "post_generation_cuda_mem_get_info": {
            "source": "torch.cuda.mem_get_info in frozen v5 runner",
            "saved_response_snapshots": len(after_values),
            "free_bytes_zero_snapshots": sum(x == 0 for x in after_values),
            "interpretation": "Seven returned CUDA API values of zero free bytes. This is not an OOM or a causal explanation of timeouts.",
        },
        "current_gpu_observation": gpu_now(),
        "limitations": [
            "No model call, old result repair, or human annotation.",
            "Timeouts have no saved intermediate token counts or interval timing after generation started.",
            "Current GPU use is a later host snapshot with other processes; it cannot explain the old timeouts.",
            "Raw generated labels and article/response text remain local.",
        ],
    }
    return public, private


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("run-dir", "schema", "aggregate", "public-out", "private-out"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    if args.public_out.exists() or args.private_out.exists():
        raise ValueError("Use new output paths; preserve old receipts")
    public, private = recheck(args.run_dir, args.schema, args.aggregate)
    private_bytes = (json.dumps(private, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    args.private_out.parent.mkdir(parents=True, exist_ok=True)
    args.public_out.parent.mkdir(parents=True, exist_ok=True)
    args.private_out.write_bytes(private_bytes)
    public["private_details_sha256"] = hashlib.sha256(private_bytes).hexdigest()
    args.public_out.write_text(json.dumps(public, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"public_out": str(args.public_out),
                      "schema_error_instances": public["schema_error_instances"],
                      "timeouts": len(public["timeouts"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
