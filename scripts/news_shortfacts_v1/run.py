"""One-pass offline Qwen smoke for six frozen official short passages.

Preflight reads the pinned tokenizer but makes no generation call. Run starts
one disposable worker per task and kills the worker process on timeout. All
prompts, raw responses, phase journals and errors stay under ignored artifacts/.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent
PRIVATE_ROOT = REPO / "artifacts/news_shortfacts_20261115"
ARTIFACT_ROOT = REPO / "artifacts"
PINNED_REVISION = "aa8e72537993ba99e69dfaafa59ed015b17504d1"
PINNED_REPO_ID = "Qwen/Qwen2.5-3B-Instruct"
ORDER = ["A1", "A2", "B1", "B2", "C1", "C2"]
SEED = 1729
MAX_INPUT_TOKENS = 2048
MAX_NEW_TOKENS = 768
GENERATION_TIMEOUT_SECONDS = 180
MODEL_LOAD_TIMEOUT_SECONDS = 240
ATTENTION_REQUEST = "sdpa"
OFFLINE = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def write_json_new(path: Path, obj: dict) -> None:
    write_new(path, (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def append_event(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps({"at_utc": utc_now(), **obj}, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def require_private(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(PRIVATE_ROOT.resolve()):
        raise ValueError(f"Private run path outside {PRIVATE_ROOT}")
    return resolved


def require_artifact(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(ARTIFACT_ROOT.resolve()):
        raise ValueError("Official source path outside ignored artifacts")
    return resolved


def pinned_snapshot(model_dir: Path) -> dict:
    directory = model_dir.resolve()
    if (directory.name != PINNED_REVISION or directory.parent.name != "snapshots"
            or directory.parent.parent.name != "models--Qwen--Qwen2.5-3B-Instruct"):
        raise ValueError("MODEL_SNAPSHOT_REVISION_OR_REPO_MISMATCH")
    needed = ["config.json", "generation_config.json", "tokenizer_config.json", "tokenizer.json",
              "model.safetensors.index.json", "model-00001-of-00002.safetensors",
              "model-00002-of-00002.safetensors"]
    sizes = {}
    for name in needed:
        file = directory / name
        if not file.is_file() or file.stat().st_size <= 0:
            raise FileNotFoundError(f"Missing/empty pinned model file: {name}")
        sizes[name] = file.stat().st_size
    index = json.loads((directory / "model.safetensors.index.json").read_text(encoding="utf-8"))
    if set(index.get("weight_map", {}).values()) != set(needed[-2:]):
        raise ValueError("MODEL_WEIGHT_INDEX_SHARDS_MISMATCH")
    config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
    if config.get("model_type") != "qwen2":
        raise ValueError("MODEL_ARCHITECTURE_MISMATCH")
    return {"repo_id": PINNED_REPO_ID, "revision": PINNED_REVISION,
            "local_path": str(directory), "required_file_sizes": sizes,
            "config_sha256": sha256_file(directory / "config.json"),
            "tokenizer_sha256": sha256_file(directory / "tokenizer.json"),
            "generation_config_sha256": sha256_file(directory / "generation_config.json")}


def dependencies() -> dict:
    result = {"python": sys.version.split()[0]}
    for package in ("torch", "transformers", "tokenizers", "safetensors"):
        try:
            result[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result[package] = None
    return result


def render_prompt(template: str, evidence_id: str, passage: str) -> str:
    if template.count("{EVIDENCE_ID}") != 1 or template.count("{PASSAGE}") != 1:
        raise ValueError("PROMPT_TEMPLATE_PLACEHOLDERS_CHANGED")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", evidence_id):
        raise ValueError("UNSAFE_EVIDENCE_ID")
    return template.replace("{EVIDENCE_ID}", evidence_id).replace("{PASSAGE}", passage)


def validate_manifest(manifest_path: Path) -> tuple[dict, list[dict]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    tasks = manifest.get("tasks")
    if not isinstance(tasks, list) or [item.get("task_id") for item in tasks] != ORDER:
        raise ValueError("SIX_FROZEN_TASKS_REQUIRED_IN_A1_A2_B1_B2_C1_C2_ORDER")
    if len({item.get("evidence_id") for item in tasks}) != 6:
        raise ValueError("DUPLICATE_EVIDENCE_ID")
    for item in tasks:
        for field in ("task_id", "evidence_id", "text_path", "text_sha256", "source_path",
                      "source_sha256", "source_url", "source_locator", "extraction_method", "entity", "event_id"):
            if not item.get(field):
                raise ValueError(f"MISSING_TASK_FIELD_{field}")
        text_path = require_private(REPO / item["text_path"])
        source_path = require_artifact(REPO / item["source_path"])
        if sha256_file(text_path) != item["text_sha256"]:
            raise ValueError(f"INPUT_TEXT_HASH_MISMATCH_{item['task_id']}")
        if sha256_file(source_path) != item["source_sha256"]:
            raise ValueError(f"SOURCE_HASH_MISMATCH_{item['task_id']}")
        if not item["source_url"].startswith("https://"):
            raise ValueError(f"SOURCE_URL_NOT_HTTPS_{item['task_id']}")
    return manifest, tasks


def _tokenize_prompt(tokenizer, rendered: str) -> list[int]:
    chat = tokenizer.apply_chat_template([{"role": "user", "content": rendered}],
                                         tokenize=False, add_generation_prompt=True)
    return tokenizer(chat, add_special_tokens=False).input_ids, chat


def preflight(manifest_path: Path, run_dir: Path, model_dir: Path) -> dict:
    run_dir = require_private(run_dir)
    if run_dir.exists():
        raise FileExistsError("RUN_DIRECTORY_ALREADY_EXISTS_NO_OVERWRITE")
    model = pinned_snapshot(model_dir)
    manifest, tasks = validate_manifest(manifest_path)
    for name, value in OFFLINE.items():
        os.environ[name] = value
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True,
                                              trust_remote_code=False)
    template_path = SCRIPT_DIR / "prompt.txt"
    schema_path = SCRIPT_DIR / "output.schema.json"
    template = template_path.read_text(encoding="utf-8")
    json.loads(schema_path.read_text(encoding="utf-8"))
    planned = []
    requests = []
    for item in tasks:
        passage = (REPO / item["text_path"]).read_text(encoding="utf-8")
        rendered = render_prompt(template, item["evidence_id"], passage)
        ids, chat = _tokenize_prompt(tokenizer, rendered)
        if len(ids) > MAX_INPUT_TOKENS:
            raise ValueError(f"INPUT_TOKEN_BUDGET_EXCEEDED_{item['task_id']}_{len(ids)}")
        requests.append((item["task_id"], chat.encode("utf-8")))
        planned.append({"task_id": item["task_id"], "evidence_id": item["evidence_id"],
                        "text_sha256": item["text_sha256"], "prompt_tokens": len(ids),
                        "rendered_prompt_sha256": sha256_bytes(chat.encode("utf-8"))})
    config = {"schema": "news_shortfacts_run_config_v1", "created_at_utc": utc_now(),
              "development_only": True, "planned_tasks": 6, "manifest_path": str(manifest_path.resolve()),
              "input_manifest_sha256": sha256_file(manifest_path),
              "model": model, "dependencies": dependencies(),
              "prompt_sha256": sha256_file(template_path), "schema_sha256": sha256_file(schema_path),
              "runner_sha256": sha256_file(Path(__file__)),
              "generation": {"batch_size": 1, "do_sample": False, "num_beams": 1, "seed": SEED,
                             "dtype": "float16", "device": "cuda", "attention_requested": ATTENTION_REQUEST,
                             "max_input_tokens": MAX_INPUT_TOKENS, "max_new_tokens": MAX_NEW_TOKENS,
                             "generation_timeout_seconds": GENERATION_TIMEOUT_SECONDS,
                             "model_load_timeout_seconds": MODEL_LOAD_TIMEOUT_SECONDS,
                             "retries": 0}, "tasks": planned,
              "constraint_decoder": "NONE_NORMAL_GENERATION",
              "historical_prediction_or_financial_evaluation": "NOT_RUN"}
    run_dir.mkdir(parents=True)
    write_new(run_dir / "input_manifest.json", manifest_path.read_bytes())
    write_new(run_dir / "prompt.txt", template_path.read_bytes())
    write_new(run_dir / "output.schema.json", schema_path.read_bytes())
    for task_id, data in requests:
        write_new(run_dir / "requests" / f"{task_id}.txt", data)
    write_json_new(run_dir / "run_config.json", config)
    return config


def phase_names(path: Path) -> list[str]:
    if not path.is_file():
        return []
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            names.append(json.loads(line)["event"])
        except (json.JSONDecodeError, KeyError):
            continue
    return names


def stop_reason(ids: list[int], eos_id: int | list[int] | None, maximum: int) -> str:
    endings = {eos_id} if isinstance(eos_id, int) else set(eos_id or [])
    if ids and ids[-1] in endings:
        return "EOS"
    if len(ids) >= maximum:
        return "OUTPUT_TOKEN_BUDGET"
    return "OTHER_STOP"


def _worker(task_id: str, run_dir: Path, model_dir: Path) -> int:
    phase_path = run_dir / "phases" / f"{task_id}.jsonl"
    append_event(phase_path, {"event": "WORKER_PROCESS_STARTED", "pid": os.getpid()})
    for name, value in OFFLINE.items():
        os.environ[name] = value
    stage = "MODEL_LOAD"
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA_UNAVAILABLE")
        torch.manual_seed(SEED)
        torch.cuda.manual_seed_all(SEED)
        append_event(phase_path, {"event": "MODEL_LOAD_STARTED"})
        tokenizer = AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True,
                                                   trust_remote_code=False)
        model = AutoModelForCausalLM.from_pretrained(
            str(model_dir), local_files_only=True, trust_remote_code=False,
            torch_dtype=torch.float16, attn_implementation=ATTENTION_REQUEST).to("cuda")
        model.eval()
        torch.cuda.synchronize()
        append_event(phase_path, {"event": "MODEL_LOADED"})
        config = json.loads((run_dir / "run_config.json").read_text(encoding="utf-8"))
        expected = next(item for item in config["tasks"] if item["task_id"] == task_id)
        request_bytes = (run_dir / "requests" / f"{task_id}.txt").read_bytes()
        if sha256_bytes(request_bytes) != expected["rendered_prompt_sha256"]:
            raise RuntimeError("PROMPT_CHANGED_AFTER_PREFLIGHT")
        ids = tokenizer(request_bytes.decode("utf-8"), add_special_tokens=False,
                        return_tensors="pt").input_ids.to("cuda")
        if ids.shape != (1, expected["prompt_tokens"]):
            raise RuntimeError("PROMPT_TOKEN_COUNT_CHANGED")
        stage = "GENERATION"
        append_event(phase_path, {"event": "GENERATION_STARTED", "input_tokens": int(ids.shape[-1])})
        begun = time.monotonic()
        with torch.inference_mode():
            output = model.generate(ids, do_sample=False, num_beams=1,
                                    max_new_tokens=MAX_NEW_TOKENS, use_cache=True,
                                    pad_token_id=tokenizer.eos_token_id,
                                    output_scores=False, output_attentions=False,
                                    output_hidden_states=False)
        torch.cuda.synchronize()
        duration = time.monotonic() - begun
        generated = output[0][ids.shape[-1]:]
        generated_ids = [int(value) for value in generated.tolist()]
        raw_text = tokenizer.decode(generated, skip_special_tokens=True,
                                    clean_up_tokenization_spaces=False)
        raw_path = run_dir / "raw_responses" / f"{task_id}.txt"
        write_new(raw_path, raw_text.encode("utf-8"))
        append_event(phase_path, {"event": "RAW_RESPONSE_SAVED", "generated_tokens": len(generated_ids)})
        write_json_new(run_dir / "worker_metadata" / f"{task_id}.json", {
            "task_id": task_id, "generated_token_ids": generated_ids,
            "generated_tokens": len(generated_ids), "input_tokens": int(ids.shape[-1]),
            "stop_reason": stop_reason(generated_ids, model.generation_config.eos_token_id,
                                       MAX_NEW_TOKENS),
            "generation_seconds_synchronized": duration,
            "raw_response_sha256": sha256_file(raw_path),
            "decode_policy": "skip_special_tokens=True;clean_up_tokenization_spaces=False;no_postprocessing",
            "actual_dtype": str(next(model.parameters()).dtype),
            "actual_device": str(next(model.parameters()).device),
            "attention_requested": ATTENTION_REQUEST,
            "model_config_attention": getattr(model.config, "_attn_implementation", None),
            "actual_sdpa_kernel": "UNKNOWN",
            "completed_at_utc": utc_now(),
        })
        append_event(phase_path, {"event": "GENERATION_COMPLETED"})
        return 0
    except Exception as exc:
        name = type(exc).__name__
        error_class = "OOM" if "out of memory" in str(exc).lower() else f"{stage}_ERROR"
        write_json_new(run_dir / "worker_errors" / f"{task_id}.json", {
            "task_id": task_id, "stage": stage, "error_class": error_class,
            "exception_type": name, "message": str(exc)[:1000], "at_utc": utc_now()})
        append_event(phase_path, {"event": "WORKER_ERROR", "stage": stage,
                                  "error_class": error_class, "exception_type": name})
        return 3


def gpu_pid_absent(pid: int) -> bool | None:
    try:
        result = subprocess.run(["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader"],
                                capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return str(pid) not in {line.strip() for line in result.stdout.splitlines()}


def kill_and_confirm(proc: subprocess.Popen) -> tuple[bool, bool | None]:
    if proc.poll() is None:
        if os.name == "nt":
            # Exact PID, process tree, no shell; the worker owns the CUDA context.
            try:
                subprocess.run(["taskkill.exe", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True, timeout=10, check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass
        if proc.poll() is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        return False, None
    # A terminated worker can briefly remain in the driver's process list.
    for _ in range(5):
        absent = gpu_pid_absent(proc.pid)
        if absent is True:
            return True, True
        if absent is None:
            return True, None
        time.sleep(1)
    return True, False


def _record_attempt(run_dir: Path, record: dict) -> None:
    append_event(run_dir / "attempt_ledger.jsonl", record)


def run_frozen(run_dir: Path, model_dir: Path) -> dict:
    run_dir = require_private(run_dir)
    config_path = run_dir / "run_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("schema") != "news_shortfacts_run_config_v1":
        raise ValueError("RUN_CONFIG_SCHEMA_MISMATCH")
    if (run_dir / "attempt_ledger.jsonl").exists() or (run_dir / "launch_journal.jsonl").exists():
        raise FileExistsError("RUN_ALREADY_LAUNCHED_NO_RETRY_OR_RESUME")
    if config["runner_sha256"] != sha256_file(Path(__file__)):
        raise ValueError("RUNNER_CHANGED_AFTER_PREFLIGHT")
    if config["prompt_sha256"] != sha256_file(SCRIPT_DIR / "prompt.txt"):
        raise ValueError("PROMPT_CHANGED_AFTER_PREFLIGHT")
    if config["schema_sha256"] != sha256_file(SCRIPT_DIR / "output.schema.json"):
        raise ValueError("SCHEMA_CHANGED_AFTER_PREFLIGHT")
    if config["input_manifest_sha256"] != sha256_file(run_dir / "input_manifest.json"):
        raise ValueError("INPUT_MANIFEST_CHANGED_AFTER_PREFLIGHT")
    snapshot = pinned_snapshot(model_dir)
    if any(snapshot[key] != config["model"][key] for key in
           ("revision", "config_sha256", "tokenizer_sha256", "generation_config_sha256")):
        raise ValueError("MODEL_CHANGED_AFTER_PREFLIGHT")
    _, tasks = validate_manifest(run_dir / "input_manifest.json")
    if dependencies() != config["dependencies"]:
        raise ValueError("RUNTIME_DEPENDENCIES_CHANGED_AFTER_PREFLIGHT")
    for item in config["tasks"]:
        request = run_dir / "requests" / f"{item['task_id']}.txt"
        if sha256_file(request) != item["rendered_prompt_sha256"]:
            raise ValueError(f"REQUEST_CHANGED_AFTER_PREFLIGHT_{item['task_id']}")
    summary = {"schema": "news_shortfacts_run_summary_v1", "started_at_utc": utc_now(),
               "planned_tasks": 6, "attempted": 0, "generation_started": 0,
               "completed_responses": 0, "run_status": "RUNNING", "stop_reason": None}
    for index, task in enumerate(tasks, 1):
        task_id = task["task_id"]
        phase_path = run_dir / "phases" / f"{task_id}.jsonl"
        append_event(run_dir / "launch_journal.jsonl", {
            "event": "WORKER_LAUNCH_COMMITTED", "task_id": task_id, "attempt_index": index})
        started = time.monotonic()
        env = os.environ.copy()
        env.update(OFFLINE)
        out_path, err_path = (run_dir / "worker_logs" / f"{task_id}.{suffix}.txt"
                              for suffix in ("stdout", "stderr"))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        process = None
        try:
            with out_path.open("xb") as out_stream, err_path.open("xb") as err_stream:
                process = subprocess.Popen(
                    [sys.executable, str(Path(__file__)), "worker", "--run-dir", str(run_dir),
                     "--model-dir", str(model_dir), "--task-id", task_id],
                    stdout=out_stream, stderr=err_stream, env=env,
                    creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
                generation_seen_at = None
                timed_out = None
                while process.poll() is None:
                    names = phase_names(phase_path)
                    if "GENERATION_STARTED" in names and generation_seen_at is None:
                        generation_seen_at = time.monotonic()
                    if generation_seen_at is not None:
                        if time.monotonic() - generation_seen_at >= GENERATION_TIMEOUT_SECONDS:
                            timed_out = "GENERATION_TIMEOUT"
                            break
                    elif time.monotonic() - started >= MODEL_LOAD_TIMEOUT_SECONDS:
                        timed_out = "MODEL_LOAD_TIMEOUT"
                        break
                    time.sleep(0.25)
                worker_exited = process.poll() is not None
                resources_released = None
                if timed_out:
                    worker_exited, resources_released = kill_and_confirm(process)
                else:
                    process.wait()
        except OSError as exc:
            timed_out = None
            worker_exited = process is not None and process.poll() is not None
            resources_released = None
            launch_error = type(exc).__name__
        else:
            launch_error = None
        names = phase_names(phase_path)
        metadata_path = run_dir / "worker_metadata" / f"{task_id}.json"
        raw_path = run_dir / "raw_responses" / f"{task_id}.txt"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.is_file() else None
        error_path = run_dir / "worker_errors" / f"{task_id}.json"
        worker_error = json.loads(error_path.read_text(encoding="utf-8")) if error_path.is_file() else None
        completed = bool(raw_path.is_file() and metadata is not None and process is not None
                         and process.returncode == 0 and timed_out is None)
        if launch_error:
            status, error_class = "LAUNCH_ERROR_STOP", launch_error
        elif timed_out:
            status, error_class = timed_out, timed_out
        elif completed:
            status, error_class = "RESPONSE_COMPLETED", None
        elif worker_error:
            error_class = worker_error["error_class"]
            status = "OOM_STOP" if error_class == "OOM" else error_class + "_STOP"
        elif process is not None and process.returncode is not None:
            status, error_class = "NATIVE_OR_NONZERO_EXIT_STOP", "UNCLASSIFIED_WORKER_EXIT"
        else:
            status, error_class = "NO_RESPONSE_STOP", "NO_RESPONSE"
        attempt = {"task_id": task_id, "attempt_index": index, "status": status,
                   "load_started": "MODEL_LOAD_STARTED" in names,
                   "generation_started": "GENERATION_STARTED" in names,
                   "response_completed": completed,
                   "raw_response_path": f"raw_responses/{task_id}.txt" if completed else None,
                   "raw_response_sha256": sha256_file(raw_path) if completed else None,
                   "input_tokens": next(item["prompt_tokens"] for item in config["tasks"]
                                        if item["task_id"] == task_id),
                   "generated_tokens": metadata["generated_tokens"] if metadata else None,
                   "stop_reason": metadata["stop_reason"] if metadata else timed_out,
                   "duration_seconds": round(time.monotonic() - started, 3),
                   "error_class": error_class,
                   "worker_exit_code": process.returncode if process else None,
                   "worker_exit_confirmed": worker_exited,
                   "gpu_process_absent_after_timeout": resources_released,
                   "phase_journal_path": f"phases/{task_id}.jsonl",
                   "completed_at_utc": utc_now()}
        _record_attempt(run_dir, attempt)
        summary["attempted"] += 1
        summary["generation_started"] += int(attempt["generation_started"])
        summary["completed_responses"] += int(completed)
        append_event(run_dir / "launch_journal.jsonl", {
            "event": "ATTEMPT_FINISHED", "task_id": task_id, "status": status})
        (run_dir / "run_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if timed_out:
            if not worker_exited or resources_released is not True:
                summary["run_status"] = "STOP_RESOURCE_RELEASE_UNCONFIRMED"
                summary["stop_reason"] = task_id
                break
            continue
        if not completed:
            summary["run_status"] = status
            summary["stop_reason"] = task_id
            break
    else:
        summary["run_status"] = "SIX_TASKS_PROCESSED"
    summary["finished_at_utc"] = utc_now()
    (run_dir / "run_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    gate = sub.add_parser("preflight")
    gate.add_argument("--manifest", type=Path, required=True)
    gate.add_argument("--run-dir", type=Path, required=True)
    gate.add_argument("--model-dir", type=Path, required=True)
    run = sub.add_parser("run")
    run.add_argument("--run-dir", type=Path, required=True)
    run.add_argument("--model-dir", type=Path, required=True)
    worker = sub.add_parser("worker")
    worker.add_argument("--run-dir", type=Path, required=True)
    worker.add_argument("--model-dir", type=Path, required=True)
    worker.add_argument("--task-id", required=True)
    args = parser.parse_args()
    if args.command == "preflight":
        result = preflight(args.manifest, args.run_dir, args.model_dir)
        print(json.dumps({"status": "PREFLIGHT_PASS_NO_GENERATION", "tasks": len(result["tasks"]),
                          "input_tokens": [item["prompt_tokens"] for item in result["tasks"]]}))
        return 0
    if args.command == "run":
        result = run_frozen(args.run_dir, args.model_dir)
        print(json.dumps(result))
        return 0 if result["run_status"] == "SIX_TASKS_PROCESSED" else 2
    return _worker(args.task_id, require_private(args.run_dir), args.model_dir)


if __name__ == "__main__":
    raise SystemExit(main())
