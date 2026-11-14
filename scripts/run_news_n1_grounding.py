"""Bounded wire-v2 quote grounding with offline Qwen and CUDA telemetry.

This is one development-only version. Raw inputs, responses, quotes, and memory
phase logs stay in ignored artifacts/. Host-computed offsets are not model
offset accuracy, and the run does not evaluate extraction or financial quality.
"""

from __future__ import annotations

import argparse
import copy
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

from jsonschema import Draft202012Validator

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from exact_quote_adapter import adapt_wire
from validate_news_company_event import validate_output


REPO = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO / "configs" / "news_n1_smoke_v4_grounded.json"
OFFLINE_ENV = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"}
REQUIRED_MODEL_FILES = ("config.json", "tokenizer_config.json")
PINNED_MODEL_REVISION = "aa8e72537993ba99e69dfaafa59ed015b17504d1"
PINNED_SAMPLE_SHA256 = "77ef3cbc244dd0f6f4c5e544db3df87606359e04922ae4e7b2365c3f8fd486af"
PINNED_SAMPLE_MANIFEST_SHA256 = "a94297c02bf2a272e215bee509610a5b27f4f61f4a189dc4ee89706d4209250e"
INTERRUPTED_PRIOR_RUNNER_SHA256 = "df563dea6878e0373d2ee512f197b45337cefcec2c065e03ce63b51904829071"


def durable_json(path: Path, payload: dict) -> None:
    """Atomically persist a private run checkpoint before another model call."""
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def durable_event(path: Path, event: str, **fields) -> None:
    """Append an fsynced phase event; survives a crash or worker timeout."""
    with path.open("a", encoding="utf-8") as handle:
        json.dump({"at_utc": utc_now(), "event": event, **fields}, handle, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def phase_events(path: Path) -> list[str]:
    if not path.is_file():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line)["event"])
        except (json.JSONDecodeError, KeyError):
            continue  # A killed worker may leave a partial final journal line.
    return events


def record_worker_phases(counts: dict, phase_path: Path) -> list[str]:
    events = phase_events(phase_path)
    for event, field in (("WORKER_PROCESS_STARTED", "worker_process_started"),
                         ("MODEL_LOADED", "model_load_completed"),
                         ("GENERATION_STARTED", "generation_started")):
        if event in events:
            counts[field] += 1
    return events


def stop_reason(generated_ids: list[int], eos_token_id: int | list[int] | None,
                max_new_tokens: int) -> str:
    eos_ids = ({eos_token_id} if isinstance(eos_token_id, int)
               else set(eos_token_id or []))
    if generated_ids and generated_ids[-1] in eos_ids:
        return "EOS"
    if len(generated_ids) >= max_new_tokens:
        return "OUTPUT_TOKEN_BUDGET_POSSIBLY_TRUNCATED"
    return "OTHER_STOP"


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def elapsed_from_original_start(started_at: str, now: datetime | None = None) -> float:
    """Conservative wall-clock budget across the original and recovery process."""
    try:
        started = datetime.fromisoformat(started_at)
    except (ValueError, TypeError) as exc:
        raise ValueError("INTERRUPTED_START_TIME_INVALID") from exc
    if started.tzinfo is None:
        raise ValueError("INTERRUPTED_START_TIME_NAIVE")
    elapsed = ((now or datetime.now(timezone.utc)) - started).total_seconds()
    if elapsed < 0:
        raise ValueError("INTERRUPTED_START_TIME_FUTURE")
    return elapsed


def dependency_versions() -> dict[str, str | None]:
    versions = {}
    for name in ("python", "torch", "transformers", "tokenizers", "safetensors", "jsonschema"):
        if name == "python":
            versions[name] = sys.version.split()[0]
            continue
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("schema") != "news_n1_smoke_config_v4_grounded":
        raise ValueError("REQUIRES_GROUNDED_V4_CONFIG")
    expected_budget = {"max_model_calls": 10, "max_new_tokens": 1024,
                       "per_call_timeout_seconds": 180,
                       "total_timeout_seconds": 1800, "retries": 0}
    expected_generation = {"do_sample": False, "num_beams": 1,
                           "seed": 1729, "use_cache": True}
    expected_flags = {"output_attentions": False,
                      "output_hidden_states": False, "output_scores": False}
    if (config.get("budget") != expected_budget
            or any(config.get("generation", {}).get(k) != v for k, v in expected_generation.items())
            or config.get("generation_output_flags") != expected_flags
            or config.get("source", {}).get("first_usable_articles") != 10
            or config.get("source", {}).get("dataset") != "Zihan1004/FNSPID"
            or config.get("source", {}).get("dataset_revision") != "bf9189c41527198897d1af3e17b1a0095279fc45"
            or config.get("source", {}).get("target_file") != "Stock_news/nasdaq_exteral_data.csv"
            or config.get("source", {}).get("sample_sha256") != PINNED_SAMPLE_SHA256
            or config.get("source", {}).get("sample_manifest_sha256") != PINNED_SAMPLE_MANIFEST_SHA256
            or config.get("model_input_fields") != ["source_date", "source_ticker_candidate", "title", "body"]
            or config.get("device") != "cuda" or config.get("dtype") != "float16"
            or config.get("attention_implementation") != "sdpa"
            or config.get("context_cap_tokens") != 32768
            or config.get("model_revision") != PINNED_MODEL_REVISION
            or config.get("model_preference") != "Qwen/Qwen2.5-3B-Instruct"
            or config.get("pipeline_version") != "wire_v2_exact_quote_host_binding_v1"
            or config.get("baseline_commit") != "bbfa7741e231ab44226b4726a73993f58a468ea4"
            or config.get("baseline_config_sha256") != "163ff6d63d68ca03feac687f9aa493a5d724f7369125200ce3f4d1bed8ed783f"):
        raise ValueError("UNAPPROVED_GROUNDED_V4_CONFIG")
    contract = config.get("contract", {})
    required_paths = ("schema_path", "schema_sha256", "wire_schema_path", "wire_schema_sha256",
                      "prompt_path", "prompt_sha256", "adapter_path", "adapter_sha256",
                      "validator_path", "validator_sha256")
    if any(not contract.get(name) for name in required_paths):
        raise ValueError("GROUNDED_V4_CONTRACT_INCOMPLETE")
    return config


def validate_hash_binding(config: dict, schema_path: Path, prompt_path: Path) -> dict:
    contract = config["contract"]
    paths = {"schema": schema_path, "wire_schema": REPO / contract["wire_schema_path"],
             "prompt": prompt_path, "adapter": REPO / contract["adapter_path"],
             "validator": REPO / contract["validator_path"]}
    actual = {f"{name}_sha256": sha256_file(path) for name, path in paths.items()}
    for name, path in paths.items():
        if path.resolve() != (REPO / contract[f"{name}_path"]).resolve():
            raise ValueError("CONTRACT_PATH_MISMATCH")
        if actual[f"{name}_sha256"] != contract[f"{name}_sha256"]:
            raise ValueError(name.upper() + "_HASH_MISMATCH")
    return actual


def first_ten_inputs(sample_path: Path, manifest_path: Path, expected_sample_sha: str) -> list[dict]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sample_hash = sha256_file(sample_path)
    if sample_hash != expected_sample_sha or sample_hash != manifest["sample"]["sha256"]:
        raise ValueError("SAMPLE_HASH_MISMATCH")
    header = manifest["result"]["header"]
    needed = {"Date", "Article_title", "Stock_symbol", "Article"}
    if not needed.issubset(header) or len(set(header)) != len(header):
        raise ValueError("SOURCE_COLUMNS_MISSING_OR_DUPLICATED")
    rows = []
    with sample_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            item = json.loads(line)
            if len(item["fields"]) != len(header):
                raise ValueError("SOURCE_FIELD_WIDTH_MISMATCH")
            fields = dict(zip(header, item["fields"]))
            if not fields["Article"].strip():
                raise ValueError("EMPTY_SAVED_BODY")
            rows.append({"ordinal": item["source_row_ordinal"], "fields": fields})
            if len(rows) == 10:
                break
    if len(rows) != 10 or [row["ordinal"] for row in rows] != list(range(1, 11)):
        raise ValueError("FIRST_TEN_DEVELOPMENT_ROWS_UNAVAILABLE")
    return rows


def build_article_input(row: dict, manifest: dict) -> dict:
    fields = row["fields"]
    body = fields["Article"]
    binding = {
        "dataset": manifest["source"]["dataset"],
        "dataset_revision": manifest["source"]["revision"],
        "source_row_ordinal": row["ordinal"],
        "source_article_version_id": None,
        "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "normalization_applied": False,
        "normalization_mapping_ref": None,
    }
    return {
        "input_binding": binding,
        "source_date": fields["Date"],
        "source_ticker_candidate": fields["Stock_symbol"],
        "title": fields["Article_title"],
        "body": body,
    }


def render_messages(prompt_text: str, article_input: dict) -> list[dict[str, str]]:
    if set(article_input) != {"source_date", "source_ticker_candidate", "title", "body"}:
        raise ValueError("GROUNDED_MODEL_INPUT_FIELDS_CHANGED")
    prompt_text = prompt_text.replace("\r\n", "\n")
    if prompt_text.count("{{ARTICLE_INPUT_JSON}}") != 1:
        raise ValueError("PROMPT_PLACEHOLDER_COUNT")
    try:
        system = prompt_text.split("## System message\n", 1)[1].split("\n## User message template", 1)[0].strip()
        user_template = prompt_text.split("## User message template\n", 1)[1].split(
            "\n## Runner notes (not sent to model)", 1
        )[0].strip()
    except IndexError as exc:
        raise ValueError("PROMPT_SECTION_MISSING") from exc
    payload = json.dumps(article_input, ensure_ascii=False, separators=(",", ":"))
    user = user_template.replace("`{{ARTICLE_INPUT_JSON}}`", payload)
    if "{{ARTICLE_INPUT_JSON}}" in user:
        user = user.replace("{{ARTICLE_INPUT_JSON}}", payload)
    if not system or not user or "{{" in user:
        raise ValueError("PROMPT_RENDER_FAILED")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def local_model_identity(model_dir: Path, expected_revision: str | None = None) -> dict:
    """Fingerprint a local Transformers snapshot, never resolving a hub ID."""
    model_dir = model_dir.resolve()
    if not model_dir.is_dir():
        raise FileNotFoundError("LOCAL_MODEL_DIRECTORY_MISSING")
    if not (model_dir.parent.name == "snapshots"
            and model_dir.parent.parent.name == "models--Qwen--Qwen2.5-3B-Instruct"
            and re.fullmatch(r"[0-9a-f]{40}", model_dir.name)):
        raise ValueError("MODEL_SOURCE_OR_REVISION_NOT_VERIFIED")
    if expected_revision is not None and model_dir.name != expected_revision:
        raise ValueError("MODEL_REVISION_MISMATCH")
    for name in REQUIRED_MODEL_FILES:
        if not (model_dir / name).is_file():
            raise FileNotFoundError("MODEL_FILE_MISSING_" + name)
    generation_config_path = model_dir / "generation_config.json"
    if not generation_config_path.is_file():
        raise FileNotFoundError("GENERATION_CONFIG_MISSING")
    tokenizer_files = [name for name in ("tokenizer.json", "vocab.json", "merges.txt") if (model_dir / name).is_file()]
    if not tokenizer_files:
        raise FileNotFoundError("TOKENIZER_FILES_MISSING")
    tokenizer_meta = json.loads((model_dir / "tokenizer_config.json").read_text(encoding="utf-8"))
    if not tokenizer_meta.get("chat_template") and not (model_dir / "chat_template.jinja").is_file():
        raise ValueError("CHAT_TEMPLATE_MISSING")
    weights = sorted(model_dir.glob("*.safetensors")) + sorted(model_dir.glob("pytorch_model*.bin"))
    if not weights:
        raise FileNotFoundError("MODEL_WEIGHTS_MISSING")
    index_path = model_dir / "model.safetensors.index.json"
    if index_path.is_file():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        missing = {name for name in index.get("weight_map", {}).values() if not (model_dir / name).is_file()}
        if missing:
            raise FileNotFoundError("MODEL_WEIGHT_SHARD_MISSING")
    model_config = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
    context_limit = model_config.get("max_position_embeddings")
    if not isinstance(context_limit, int) or context_limit < 1024:
        raise ValueError("MODEL_CONTEXT_LIMIT_UNKNOWN")
    files = [model_dir / name for name in REQUIRED_MODEL_FILES + tuple(tokenizer_files)]
    files += [generation_config_path] + weights
    if (model_dir / "chat_template.jinja").is_file():
        files.append(model_dir / "chat_template.jinja")
    if index_path.is_file():
        files.append(index_path)
    file_hashes = {path.name: sha256_file(path) for path in files}
    snapshot_revision = model_dir.name if re.fullmatch(r"[0-9a-f]{40}", model_dir.name) else None
    return {
        "local_directory": str(model_dir),
        "snapshot_revision": snapshot_revision,
        "config_model_type": model_config.get("model_type"),
        "config_name_or_path": model_config.get("_name_or_path"),
        "context_limit_tokens": context_limit,
        "tokenizer_chat_template_sha256": hashlib.sha256(
            str(tokenizer_meta.get("chat_template") or (model_dir / "chat_template.jinja").read_text(encoding="utf-8")).encode("utf-8")
        ).hexdigest(),
        "file_hashes": file_hashes,
        "weight_sha256": {path.name: file_hashes[path.name] for path in weights},
        "weight_files": len(weights),
        "generation_config_sha256": file_hashes["generation_config.json"],
    }


def cuda_snapshot(torch_module, stage: str) -> dict:
    """Best-effort byte counts; unmeasurable values remain null, never zero."""
    result = {"stage": stage, "at_utc": utc_now(), "available": False}
    try:
        free, total = torch_module.cuda.mem_get_info()
        result.update({
            "available": True, "free_bytes": int(free), "total_bytes": int(total),
            "allocated_bytes": int(torch_module.cuda.memory_allocated()),
            "reserved_bytes": int(torch_module.cuda.memory_reserved()),
            "max_allocated_bytes": int(torch_module.cuda.max_memory_allocated()),
            "max_reserved_bytes": int(torch_module.cuda.max_memory_reserved()),
        })
    except Exception as exc:
        result["measurement_error_type"] = type(exc).__name__
    return result


def host_gpu_snapshot() -> dict:
    """Private contemporaneous nvidia-smi observation; never kills processes."""
    result = {"at_utc": utc_now(), "gpu_query_available": False,
              "compute_process_query_available": False, "gpu_rows": [],
              "compute_processes": []}
    try:
        query = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.used,memory.free",
             "--format=csv,noheader,nounits"], capture_output=True, text=True,
            timeout=10, check=False)
        if query.returncode == 0:
            for line in query.stdout.splitlines():
                fields = [part.strip() for part in line.split(",")]
                if len(fields) == 5:
                    result["gpu_rows"].append({
                        "index": int(fields[0]), "name": fields[1],
                        "total_mib": int(fields[2]), "used_mib": int(fields[3]),
                        "free_mib": int(fields[4])})
            result["gpu_query_available"] = bool(result["gpu_rows"])
        else:
            result["gpu_query_error_type"] = "NONZERO_EXIT"
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        result["gpu_query_error_type"] = type(exc).__name__
    try:
        processes = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,used_gpu_memory",
             "--format=csv,noheader,nounits"], capture_output=True, text=True,
            timeout=10, check=False)
        if processes.returncode == 0:
            result["compute_process_query_available"] = True
            for line in processes.stdout.splitlines():
                fields = [part.strip() for part in line.split(",")]
                if len(fields) == 2 and fields[0].isdigit():
                    result["compute_processes"].append({"pid": int(fields[0]),
                        "used_mib": int(fields[1]) if fields[1].isdigit() else None})
        else:
            result["compute_process_query_error_type"] = "NONZERO_EXIT"
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        result["compute_process_query_error_type"] = type(exc).__name__
    return result


def phase_memory_summary(path: Path) -> dict:
    events = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    memory = [event for event in events if event.get("event") == "CUDA_MEMORY"]
    available = [event for event in memory if event.get("available")]
    def largest(key: str) -> int | None:
        values = [item[key] for item in available if isinstance(item.get(key), int)]
        return max(values) if values else None
    free = [item["free_bytes"] for item in available if isinstance(item.get("free_bytes"), int)]
    return {"memory_snapshot_count": len(memory), "memory_snapshot_available": len(available),
            "stages": [item.get("stage") for item in memory],
            "peak_reset_before_model_load": any(item.get("event") == "CUDA_PEAK_RESET"
                                                 and item.get("stage") == "BEFORE_MODEL_LOAD"
                                                 for item in events),
            "minimum_free_bytes": min(free) if free else None,
            "maximum_allocated_bytes": largest("max_allocated_bytes"),
            "maximum_reserved_bytes": largest("max_reserved_bytes"),
            "worker_exception_codes": [item.get("error_code") for item in events
                                       if item.get("event") == "WORKER_EXCEPTION"]}


def _worker(request_path: Path, response_path: Path, phase_path: Path,
            model_dir: Path) -> int:
    """One isolated call; CUDA snapshots survive a failed generation."""
    durable_event(phase_path, "WORKER_PROCESS_STARTED")
    for name, value in OFFLINE_ENV.items():
        os.environ[name] = value
    request = json.loads(request_path.read_text(encoding="utf-8"))
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    if (request["device"] != "cuda" or request["dtype"] != "float16"
            or request["attention_implementation"] != "sdpa"
            or request["seed"] != 1729 or request["max_new_tokens"] != 1024
            or request["use_cache"] is not True
            or request["generation_output_flags"] != {"output_attentions": False,
                                                      "output_hidden_states": False,
                                                      "output_scores": False}):
        raise ValueError("GROUNDED_WORKER_GENERATION_CONTRACT_CHANGED")
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA_UNAVAILABLE")
    torch.cuda.init()
    torch.cuda.reset_peak_memory_stats()
    durable_event(phase_path, "CUDA_PEAK_RESET", stage="BEFORE_MODEL_LOAD")
    durable_event(phase_path, "CUDA_MEMORY", **cuda_snapshot(torch, "WORKER_CREATED"))
    torch.manual_seed(request["seed"])
    torch.cuda.manual_seed_all(request["seed"])
    try:
        tokenizer = AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True,
                                                   trust_remote_code=False)
        model = AutoModelForCausalLM.from_pretrained(
            str(model_dir), local_files_only=True, trust_remote_code=False,
            torch_dtype=torch.float16, attn_implementation="sdpa",
        ).to("cuda")
        model.eval()
        torch.cuda.synchronize()
        durable_event(phase_path, "MODEL_LOADED")
        durable_event(phase_path, "CUDA_MEMORY", **cuda_snapshot(torch, "AFTER_MODEL_LOAD"))
        input_ids = tokenizer.apply_chat_template(
            request["messages"], tokenize=True, add_generation_prompt=True,
            return_tensors="pt").to("cuda")
        if input_ids.shape[0] != 1 or input_ids.shape[-1] != request["prompt_tokens"]:
            raise RuntimeError("PROMPT_TOKEN_COUNT_CHANGED")
        effective_generation = copy.deepcopy(model.generation_config)
        effective_generation.do_sample = False
        effective_generation.num_beams = 1
        effective_generation.max_new_tokens = request["max_new_tokens"]
        effective_generation.use_cache = request["use_cache"]
        for name, value in request["generation_output_flags"].items():
            setattr(effective_generation, name, value)
        effective_generation.pad_token_id = (
            tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id
        )
        effective_config = json.loads(json.dumps(effective_generation.to_dict(), default=str))
        effective_config_bytes = json.dumps(effective_config, sort_keys=True,
                                            ensure_ascii=False, default=str).encode("utf-8")
        special_ids = {"tokenizer_bos_token_id": tokenizer.bos_token_id,
                       "tokenizer_eos_token_id": tokenizer.eos_token_id,
                       "tokenizer_pad_token_id": tokenizer.pad_token_id,
                       "effective_eos_token_id": effective_generation.eos_token_id,
                       "effective_pad_token_id": effective_generation.pad_token_id}
        durable_event(phase_path, "CUDA_MEMORY", **cuda_snapshot(torch, "BEFORE_GENERATION"))
        durable_event(phase_path, "GENERATION_STARTED", prompt_tokens=int(input_ids.shape[-1]))
        generate_started = time.monotonic()
        with torch.inference_mode():
            output_ids = model.generate(input_ids, generation_config=effective_generation)
        torch.cuda.synchronize()
        generation_seconds = time.monotonic() - generate_started
        durable_event(phase_path, "GENERATION_FINISHED")
        durable_event(phase_path, "CUDA_MEMORY", **cuda_snapshot(torch, "AFTER_GENERATION"))
        generated_ids = output_ids[0][input_ids.shape[-1]:]
        token_ids = [int(x) for x in generated_ids.tolist()]
        durable_json(response_path, {
            "raw_text": tokenizer.decode(generated_ids, skip_special_tokens=True),
            "prompt_tokens": int(input_ids.shape[-1]),
            "batch_size": int(input_ids.shape[0]),
            "generated_tokens": int(generated_ids.shape[-1]),
            "generated_token_ids": token_ids,
            "stop_reason": stop_reason(token_ids, effective_generation.eos_token_id,
                                       request["max_new_tokens"]),
            "generation_seconds_synced": generation_seconds,
            "effective_generation_config": effective_config,
            "effective_generation_config_sha256": hashlib.sha256(effective_config_bytes).hexdigest(),
            "generation_config_source_sha256": sha256_file(model_dir / "generation_config.json"),
            "tokenizer_special_ids": special_ids,
            "requested_attention_implementation": request["attention_implementation"],
            "model_config_attention_implementation": getattr(model.config, "_attn_implementation", None),
            "sdpa_backend_enabled_flags": {
                "flash": bool(torch.backends.cuda.flash_sdp_enabled()),
                "memory_efficient": bool(torch.backends.cuda.mem_efficient_sdp_enabled()),
                "math": bool(torch.backends.cuda.math_sdp_enabled()),
            },
            "actual_sdpa_kernel": "UNKNOWN",
            "finished_at_utc": utc_now(),
        })
        durable_event(phase_path, "RESPONSE_SAVED", generated_tokens=len(token_ids))
        return 0
    except Exception as exc:
        code = "CUDA_OUT_OF_MEMORY" if "out of memory" in str(exc).lower() else "WORKER_EXCEPTION"
        durable_event(phase_path, "WORKER_EXCEPTION", error_code=code,
                      exception_type=type(exc).__name__)
        raise
    finally:
        durable_event(phase_path, "CUDA_MEMORY", **cuda_snapshot(torch, "WORKER_FINALLY"))


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _public_summary(local: dict) -> dict:
    public_model = local.get("model", {})
    counts = local["counts"]
    attempted = counts["model_calls_attempted"]
    conditional = {
        "json_parse_valid": counts["model_calls_completed"],
        "wire_schema_valid": counts["json_parse_valid"],
        "canonical_constructible": counts["wire_schema_valid"],
        "canonical_schema_valid": counts["canonical_constructible"],
        "canonical_mechanical_valid": counts["canonical_constructible"],
    }
    rates = {
        name: {"conditional_numerator": counts[name], "conditional_denominator": denominator,
               "conditional_rate": _rate(counts[name], denominator),
               "attempted_denominator": attempted,
               "attempted_rate": _rate(counts[name], attempted),
               "fixed_target_denominator": 10,
               "fixed_target_rate": _rate(counts[name], 10)}
        for name, denominator in conditional.items()
    }
    return {
        "schema": "news_n1_grounding_public_receipt_v1",
        "pipeline_version": local["pipeline_version"],
        "run_started_at_utc": local["run_started_at_utc"],
        "run_finished_at_utc": local["run_finished_at_utc"],
        "run_status": local["run_status"],
        "development_articles": local["development_articles"],
        "input_sample_sha256": local["input_sample_sha256"],
        "input_manifest_sha256": local["input_manifest_sha256"],
        "config_sha256": local["config_sha256"],
        "schema_sha256": local["schema_sha256"],
        "wire_schema_sha256": local["wire_schema_sha256"],
        "prompt_sha256": local["prompt_sha256"],
        "adapter_sha256": local["adapter_sha256"],
        "validator_sha256": local["validator_sha256"],
        "model_preference": local["model_preference"],
        "model_snapshot_revision": public_model.get("snapshot_revision"),
        "model_weight_sha256": public_model.get("weight_sha256"),
        "model_generation_config_source_sha256": public_model.get("generation_config_sha256"),
        "effective_generation_config_sha256": local.get("effective_generation_config_sha256"),
        "tokenizer_special_ids": local.get("tokenizer_special_ids"),
        "attention_implementation": local.get("attention_implementation"),
        "runner_sha256": local.get("runner_sha256"),
        "recovery": ({
            "version": local["recovery"]["version"],
            "original_runner_sha256": local["recovery"]["original_runner_sha256"],
            "original_runner_source_sha256": local["recovery"]["original_runner_source_sha256"],
            "recovery_runner_sha256": local["recovery"]["recovery_runner_sha256"],
            "interrupted_ordinal": local["recovery"]["interrupted_ordinal"],
            "interrupted_status": local["recovery"]["interrupted_status"],
            "prestate_sha256": local["recovery"]["prestate_sha256"],
            "phase_02_sha256": local["recovery"]["phase_02_sha256"],
            "original_wall_budget_seconds": local["recovery"]["original_wall_budget_seconds"],
            "wall_seconds_elapsed_at_recovery": local["recovery"]["wall_seconds_elapsed_at_recovery"],
            "interpretation": "A prior committed call was externally interrupted before a response; it was not retried or classified as OOM.",
        } if local.get("recovery") else None),
        "model_context_limit_tokens": public_model.get("context_limit_tokens"),
        "model_runtime": local["runtime"],
        "budgets": local["budgets"],
        "counts": local["counts"],
        "stage_rates": rates,
        "quote_rate": {"unique_numerator": counts["quote_unique"],
                       "quote_denominator": counts["quotes_checked"],
                       "unique_rate": _rate(counts["quote_unique"], counts["quotes_checked"]),
                       "zero_quote_outputs_are_grounding_success": False},
        "gpu_memory_summary": local.get("gpu_memory_summary", {}),
        "metric_denominators": {
            "input_too_long": "context_preflight_checked_articles",
            "json_parse_valid": "model_calls_completed",
            "wire_schema_valid": "json_parse_valid",
            "canonical_constructible": "wire_schema_valid",
            "canonical_schema_valid": "canonical_constructible",
            "canonical_mechanical_valid": "canonical_constructible",
            "quote_unique": "quotes_checked (quotes, not articles)",
            "host_body_hash_valid": "wire_schema_valid (host integrity, not model ability)",
        },
        "limitations": [
            "First ten source-order articles were already used for field design; development set only.",
            "Per-article wall seconds include subprocess start, cold model load and tokenization; synchronized generation seconds are recorded separately.",
            "Host-computed input binding and quote offsets are not model performance metrics.",
            "Exact quote presence does not establish that the quote supports the event or impact claim.",
            "SDPA is configured; the actual selected PyTorch attention kernel is UNKNOWN without a reliable trace.",
            "No independent human gold labels or extraction accuracy metrics.",
            "No financial labels, prediction or return evaluation.",
        ],
    }


def verify_interrupted_resume(config: dict, config_path: Path, sample_path: Path, manifest_path: Path,
                              output_dir: Path, prompt_text: str, hashes: dict,
                              inputs: list[dict], manifest: dict,
                              expected_prior_runner_sha: str) -> tuple[dict, dict]:
    """Read-only, narrowly scoped recovery gate for the interrupted fixed v4 run."""
    if expected_prior_runner_sha != INTERRUPTED_PRIOR_RUNNER_SHA256:
        raise ValueError("PRIOR_RUNNER_SHA_NOT_APPROVED")
    original_source = output_dir.parent / "original_v4_runner_df563dea6878.py"
    if not original_source.is_file() or sha256_file(original_source) != expected_prior_runner_sha:
        raise ValueError("ORIGINAL_RUNNER_SOURCE_MISSING_OR_CHANGED")
    if not output_dir.is_dir():
        raise FileNotFoundError("INTERRUPTED_OUTPUT_DIR_MISSING")
    required = ("local_manifest.json", "shareable_summary.json", "run_events.jsonl",
                "token_preflight.json", "request_01.json", "request_02.json",
                "response_01.json", "wire_01.json", "grounding_01.json",
                "phase_01.jsonl", "phase_02.jsonl")
    if any(not (output_dir / name).is_file() for name in required):
        raise ValueError("INTERRUPTED_REQUIRED_RECEIPT_MISSING")
    forbidden = ("response_02.json", "wire_02.json", "grounding_02.json",
                 "canonical_02.json", "stderr_02.txt", "gpu_worker_02.json",
                 "gpu_host_after_02.json", "gpu_host_final.json",
                 "recovery_prestate_local_manifest.json",
                 "recovery_prestate_shareable_summary.json",
                 "recovery_prestate_run_events.jsonl")
    if any((output_dir / name).exists() for name in forbidden):
        raise ValueError("INTERRUPTED_UNEXPECTED_PRIOR_RECEIPT")
    if any((output_dir / f"request_{ordinal:02d}.json").exists()
           for ordinal in range(3, 11)):
        raise ValueError("INTERRUPTED_LATER_ATTEMPT_EXISTS")
    local = json.loads((output_dir / "local_manifest.json").read_text(encoding="utf-8"))
    if (local.get("run_status") != "PREFLIGHT"
            or local.get("run_finished_at_utc") is not None
            or local.get("recovery") is not None
            or local.get("runner_sha256") != expected_prior_runner_sha
            or config_path.resolve() != DEFAULT_CONFIG.resolve()
            or local.get("config_sha256") != sha256_file(config_path)
            or local.get("input_sample_sha256") != sha256_file(sample_path)
            or local.get("input_manifest_sha256") != sha256_file(manifest_path)
            or local.get("pipeline_version") != config["pipeline_version"]
            or local.get("model_preference") != config["model_preference"]
            or local.get("attention_implementation") != "sdpa"
            or local.get("budgets") != config["budget"]
            or any(local.get(key) != value for key, value in hashes.items())):
        raise ValueError("INTERRUPTED_RUN_BINDING_MISMATCH")
    counts = local.get("counts", {})
    expected_counts = {"articles_considered": 10, "context_preflight_checked_articles": 10,
                       "input_too_long": 0, "model_calls_attempted": 2,
                       "worker_process_started": 1, "model_load_completed": 1,
                       "generation_started": 1, "model_calls_completed": 1,
                       "model_call_failures": 0, "json_parse_valid": 1,
                       "wire_schema_valid": 0, "canonical_constructible": 0}
    if any(counts.get(key) != value for key, value in expected_counts.items()):
        raise ValueError("INTERRUPTED_PRIOR_COUNTS_UNEXPECTED")
    statuses = local.get("article_statuses", [])
    if len(statuses) != 1 or statuses[0].get("ordinal") != 1 or statuses[0].get(
            "status") != "WIRE_SCHEMA_INVALID":
        raise ValueError("INTERRUPTED_PRIOR_STATUS_UNEXPECTED")
    event_lines = (output_dir / "run_events.jsonl").read_text(encoding="utf-8").splitlines()
    try:
        events = [json.loads(line) for line in event_lines]
    except json.JSONDecodeError as exc:
        raise ValueError("INTERRUPTED_RUN_EVENTS_INVALID") from exc
    expected_events = ("RUN_STARTED", "TOKEN_PREFLIGHT_FINISHED",
                       "WORKER_LAUNCH_COMMITTED", "RESPONSE_READ_BACK",
                       "ATTEMPT_FINISHED", "WORKER_LAUNCH_COMMITTED")
    if tuple(event.get("event") for event in events) != expected_events:
        raise ValueError("INTERRUPTED_EVENT_SEQUENCE_UNEXPECTED")
    if ([event.get("ordinal") for event in events if event.get("event") ==
         "WORKER_LAUNCH_COMMITTED"] != [1, 2]
            or events[4].get("status") != "WIRE_SCHEMA_INVALID"
            or events[5].get("attempt_count") != 2):
        raise ValueError("INTERRUPTED_LAUNCH_BINDING_MISMATCH")
    phase_names = phase_events(output_dir / "phase_02.jsonl")
    if (not phase_names or phase_names[0] != "WORKER_PROCESS_STARTED"
            or "MODEL_LOADED" in phase_names or "GENERATION_STARTED" in phase_names
            or "RESPONSE_SAVED" in phase_names or "WORKER_EXCEPTION" in phase_names):
        raise ValueError("INTERRUPTED_SECOND_PHASE_UNEXPECTED")
    preflight = json.loads((output_dir / "token_preflight.json").read_text(encoding="utf-8"))
    if [item.get("ordinal") for item in preflight.get("articles", [])] != list(range(1, 11)):
        raise ValueError("INTERRUPTED_PREFLIGHT_UNEXPECTED")
    for ordinal in (1, 2):
        request = json.loads((output_dir / f"request_{ordinal:02d}.json").read_text(
            encoding="utf-8"))
        article = build_article_input(inputs[ordinal - 1], manifest)
        model_input = {key: article[key] for key in config["model_input_fields"]}
        if (request.get("model_input") != model_input
                or request.get("host_input_binding") != article["input_binding"]
                or request.get("messages") != render_messages(prompt_text, model_input)
                or request.get("prompt_tokens") != preflight["articles"][ordinal - 1][
                    "prompt_tokens"]
                or request.get("max_new_tokens") != config["budget"]["max_new_tokens"]
                or request.get("seed") != config["generation"]["seed"]
                or request.get("attention_implementation") != "sdpa"
                or request.get("generation_output_flags") != config[
                    "generation_output_flags"]):
            raise ValueError("INTERRUPTED_REQUEST_BINDING_MISMATCH")
    elapsed = elapsed_from_original_start(local["run_started_at_utc"])
    snapshots = {
        "local_manifest": (output_dir / "local_manifest.json").read_bytes(),
        "shareable_summary": (output_dir / "shareable_summary.json").read_bytes(),
        "run_events": (output_dir / "run_events.jsonl").read_bytes(),
    }
    provenance = {
        "version": "external_interruption_resume_v1",
        "original_runner_sha256": expected_prior_runner_sha,
        "original_runner_source_sha256": sha256_file(original_source),
        "recovery_runner_sha256": sha256_file(Path(__file__)),
        "interrupted_ordinal": 2,
        "interrupted_status": "INTERRUPTED_PROCESS_UNCLASSIFIED",
        "interruption_cause": "UNKNOWN",
        "worker_exit_observed_by_original_parent": False,
        "original_wall_budget_seconds": config["budget"]["total_timeout_seconds"],
        "wall_seconds_elapsed_at_recovery": elapsed,
        "prestate_sha256": {key: hashlib.sha256(value).hexdigest()
                            for key, value in snapshots.items()},
        "phase_02_sha256": sha256_file(output_dir / "phase_02.jsonl"),
        "request_01_sha256": sha256_file(output_dir / "request_01.json"),
        "request_02_sha256": sha256_file(output_dir / "request_02.json"),
        "response_01_sha256": sha256_file(output_dir / "response_01.json"),
    }
    return local, {"provenance": provenance, "snapshots": snapshots,
                   "preflight": preflight}


def run(config_path: Path, sample_path: Path, manifest_path: Path, output_dir: Path,
        model_dir: Path | None, *, resume_interrupted: bool = False,
        expected_prior_runner_sha: str | None = None) -> dict:
    config = load_config(config_path)
    schema_path = REPO / config["contract"]["schema_path"]
    wire_schema_path = REPO / config["contract"]["wire_schema_path"]
    prompt_path = REPO / config["contract"]["prompt_path"]
    hashes = validate_hash_binding(config, schema_path, prompt_path)
    canonical_schema = json.loads(schema_path.read_text(encoding="utf-8"))
    wire_schema = json.loads(wire_schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(canonical_schema)
    Draft202012Validator.check_schema(wire_schema)
    prompt_text = prompt_path.read_text(encoding="utf-8").replace("\r\n", "\n")
    try:
        embedded_schema = json.loads(prompt_text.split("BEGIN_WIRE_SCHEMA\n", 1)[1].split(
            "\nEND_WIRE_SCHEMA", 1)[0])
    except (IndexError, json.JSONDecodeError) as exc:
        raise ValueError("WIRE_SCHEMA_NOT_EMBEDDED_IN_PROMPT") from exc
    if embedded_schema != wire_schema:
        raise ValueError("PROMPT_WIRE_SCHEMA_MISMATCH")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    inputs = first_ten_inputs(sample_path, manifest_path, config["source"]["sample_sha256"])
    if sha256_file(manifest_path) != config["source"]["sample_manifest_sha256"]:
        raise ValueError("SAMPLE_MANIFEST_HASH_MISMATCH")
    artifacts_root = (REPO / "artifacts").resolve()
    output_dir = output_dir.resolve()
    if output_dir == artifacts_root or not output_dir.is_relative_to(artifacts_root):
        raise ValueError("OUTPUT_MUST_BE_INSIDE_IGNORED_ARTIFACTS")
    resume_bundle = None
    if resume_interrupted:
        local_prior, resume_bundle = verify_interrupted_resume(
            config, config_path, sample_path, manifest_path, output_dir, prompt_text, hashes,
            inputs, manifest, expected_prior_runner_sha or "")
    else:
        if output_dir.exists():
            raise FileExistsError("OUTPUT_ALREADY_EXISTS")
        output_dir.mkdir(parents=True)
    event_path = output_dir / "run_events.jsonl"
    started = utc_now()
    local = {
        "schema": "news_n1_grounding_local_manifest_v1",
        "pipeline_version": config["pipeline_version"],
        "run_started_at_utc": started,
        "run_finished_at_utc": None,
        "run_status": "PREFLIGHT",
        "development_articles": len(inputs),
        "input_sample_sha256": sha256_file(sample_path),
        "input_manifest_sha256": sha256_file(manifest_path),
        "config_sha256": sha256_file(config_path),
        "runner_sha256": sha256_file(Path(__file__)),
        **hashes,
        "model_preference": config["model_preference"],
        "attention_implementation": config.get("attention_implementation", "eager"),
        "runtime": dependency_versions(),
        "budgets": config["budget"],
        "counts": {"articles_considered": len(inputs), "context_preflight_checked_articles": 0,
                   "input_too_long": 0,
                   "model_calls_attempted": 0, "worker_process_started": 0,
                   "model_load_completed": 0, "generation_started": 0,
                   "model_calls_completed": 0,
                   "model_call_failures": 0, "json_parse_valid": 0,
                   "wire_schema_valid": 0, "host_body_hash_valid": 0,
                   "quotes_checked": 0, "quote_unique": 0, "quote_ambiguous": 0,
                   "quote_not_found": 0, "quote_invalid": 0,
                   "canonical_constructible": 0, "canonical_schema_valid": 0,
                   "canonical_mechanical_valid": 0,
                   "no_spans_requested": 0, "empty_records_response": 0},
        "article_statuses": [],
        "model": {},
        "gpu_memory_summary": {"actual_sdpa_kernel": "UNKNOWN",
                               "worker_snapshot_count": 0,
                               "worker_snapshot_available": 0,
                               "maximum_allocated_bytes": None,
                               "maximum_reserved_bytes": None,
                               "minimum_free_bytes": None},
        "local_input_paths": {"sample": str(sample_path.resolve()),
                              "manifest": str(manifest_path.resolve())},
    }
    def checkpoint() -> None:
        durable_json(output_dir / "local_manifest.json", local)
        durable_json(output_dir / "shareable_summary.json", _public_summary(local))

    if resume_bundle is not None:
        if local["runtime"] != local_prior.get("runtime"):
            raise ValueError("RECOVERY_RUNTIME_DEPENDENCIES_CHANGED")
        local = local_prior
        snapshots = resume_bundle["snapshots"]
        for key, filename in (("local_manifest", "recovery_prestate_local_manifest.json"),
                              ("shareable_summary", "recovery_prestate_shareable_summary.json"),
                              ("run_events", "recovery_prestate_run_events.jsonl")):
            (output_dir / filename).write_bytes(snapshots[key])
        local["recovery"] = resume_bundle["provenance"]
        local["recovery"]["recovery_started_at_utc"] = utc_now()
        local["runner_sha256"] = local["recovery"]["recovery_runner_sha256"]
        durable_event(event_path, "RUN_RECOVERY_STARTED",
                      original_runner_sha256=local["recovery"]["original_runner_sha256"],
                      recovery_runner_sha256=local["recovery"]["recovery_runner_sha256"],
                      interrupted_ordinal=2,
                      original_wall_budget_seconds=config["budget"]["total_timeout_seconds"])
        phases = record_worker_phases(local["counts"], output_dir / "phase_02.jsonl")
        partial_gpu = phase_memory_summary(output_dir / "phase_02.jsonl")
        gpu = local["gpu_memory_summary"]
        gpu["worker_snapshot_count"] += partial_gpu["memory_snapshot_count"]
        gpu["worker_snapshot_available"] += partial_gpu["memory_snapshot_available"]
        for key, combine in (("maximum_allocated_bytes", max),
                             ("maximum_reserved_bytes", max),
                             ("minimum_free_bytes", min)):
            value = partial_gpu[key]
            if value is not None:
                previous = gpu[key]
                gpu[key] = value if previous is None else combine(previous, value)
        local["counts"]["model_call_failures"] += 1
        local["article_statuses"].append({
            "ordinal": 2, "status": "INTERRUPTED_PROCESS_UNCLASSIFIED",
            "worker_phases": phases, "worker_exit_observed": False,
            "interpretation": "Original parent was interrupted; no response or terminal worker phase was saved.",
        })
        durable_event(event_path, "ATTEMPT_FINISHED", ordinal=2,
                      status="INTERRUPTED_PROCESS_UNCLASSIFIED")
        checkpoint()
    else:
        durable_event(event_path, "RUN_STARTED", config_sha256=local["config_sha256"],
                      max_model_calls=config["budget"]["max_model_calls"])
        checkpoint()
    try:
        if model_dir is None:
            local["run_status"] = "NOT_RUN_NO_VERSION_BOUND_LOCAL_MODEL"
            return local
        if resume_bundle is not None:
            original_elapsed = elapsed_from_original_start(local["run_started_at_utc"])
            run_clock = time.monotonic() - original_elapsed
        else:
            run_clock = time.monotonic()
        model = local_model_identity(model_dir, config.get("model_revision"))
        if resume_bundle is not None:
            prior_model = local["model"]
            if any(key not in prior_model or prior_model[key] != value
                   for key, value in model.items()):
                raise ValueError("RECOVERY_MODEL_IDENTITY_CHANGED")
            model = prior_model  # retain prior derived context/device metadata
        local["model"] = model
        checkpoint()
        if not all(local["runtime"].get(name) for name in ("torch", "transformers", "jsonschema")):
            local["run_status"] = "NOT_RUN_RUNTIME_DEPENDENCY_MISSING"
            return local
        import torch
        from transformers import AutoTokenizer

        if config["device"] == "cuda" and not torch.cuda.is_available():
            local["run_status"] = "NOT_RUN_CUDA_UNAVAILABLE"
            return local
        gpu_before = host_gpu_snapshot()
        durable_json(output_dir / ("gpu_host_resume_preflight.json"
                                  if resume_bundle is not None else "gpu_host_preflight.json"),
                     gpu_before)
        if gpu_before["gpu_rows"]:
            first_gpu = gpu_before["gpu_rows"][0]
            if resume_bundle is not None:
                local["gpu_memory_summary"].update({
                    "host_recovery_free_mib": first_gpu["free_mib"],
                    "host_recovery_used_mib": first_gpu["used_mib"],
                    "host_recovery_observed_at_utc": gpu_before["at_utc"],
                })
            else:
                local["gpu_memory_summary"].update({
                    "host_before_free_mib": first_gpu["free_mib"],
                    "host_before_used_mib": first_gpu["used_mib"],
                    "host_total_mib": first_gpu["total_mib"],
                    "host_before_active_compute_process_count": (
                        len(gpu_before["compute_processes"])
                        if gpu_before["compute_process_query_available"] else None),
                })
        checkpoint()
        for name, value in OFFLINE_ENV.items():
            os.environ[name] = value
        tokenizer = AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True, trust_remote_code=False)
        if not tokenizer.chat_template:
            raise ValueError("CHAT_TEMPLATE_UNAVAILABLE")
        context_limit = min(model["context_limit_tokens"], config["context_cap_tokens"])
        if config["budget"]["max_new_tokens"] > context_limit:
            raise ValueError("CONTEXT_LIMIT_TOO_SMALL")
        model["effective_context_limit_tokens"] = context_limit
        model["device"] = config["device"]
        model["dtype"] = config["dtype"]
        prepared = []
        preflight = []
        for row in inputs:
            article_input = build_article_input(row, manifest)
            model_input = {key: article_input[key] for key in config["model_input_fields"]}
            if "input_binding" in model_input:
                raise ValueError("HOST_BINDING_LEAKED_TO_MODEL_INPUT")
            messages = render_messages(prompt_text, model_input)
            ids = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True)
            prompt_tokens = len(ids)
            if resume_bundle is None:
                local["counts"]["context_preflight_checked_articles"] += 1
            prepared.append((row, article_input, model_input, messages, prompt_tokens))
            preflight.append({"ordinal": row["ordinal"], "prompt_tokens": prompt_tokens,
                              "effective_context_limit_tokens": context_limit,
                              "fits_with_output_budget": prompt_tokens + config["budget"]["max_new_tokens"] <= context_limit})
        preflight_receipt = {"articles": preflight,
                             "chat_template_sha256": model["tokenizer_chat_template_sha256"],
                             "context_limit_tokens": context_limit}
        if resume_bundle is not None:
            if preflight_receipt != resume_bundle["preflight"]:
                raise ValueError("RECOVERY_TOKEN_PREFLIGHT_CHANGED")
            durable_event(event_path, "TOKEN_PREFLIGHT_REPLAY_MATCHED", articles=len(preflight))
        else:
            durable_json(output_dir / "token_preflight.json", preflight_receipt)
            durable_event(event_path, "TOKEN_PREFLIGHT_FINISHED", articles=len(preflight))
        checkpoint()
        del tokenizer
        def record_worker_telemetry(index: int, phase_path: Path) -> None:
            observation = phase_memory_summary(phase_path)
            durable_json(output_dir / f"gpu_worker_{index:02d}.json", observation)
            summary = local["gpu_memory_summary"]
            summary["worker_snapshot_count"] += observation["memory_snapshot_count"]
            summary["worker_snapshot_available"] += observation["memory_snapshot_available"]
            for source, target, combine in (
                ("maximum_allocated_bytes", "maximum_allocated_bytes", max),
                ("maximum_reserved_bytes", "maximum_reserved_bytes", max),
                ("minimum_free_bytes", "minimum_free_bytes", min),
            ):
                value = observation[source]
                if value is not None:
                    prior = summary[target]
                    summary[target] = value if prior is None else combine(prior, value)
            after = host_gpu_snapshot()
            durable_json(output_dir / f"gpu_host_after_{index:02d}.json", after)
            if after["gpu_rows"]:
                summary["host_after_last_free_mib"] = after["gpu_rows"][0]["free_mib"]
                summary["host_after_last_used_mib"] = after["gpu_rows"][0]["used_mib"]
            summary["host_after_last_observed_at_utc"] = after["at_utc"]

        launched_ordinals: set[int] = set()
        for index, (row, article_input, model_input, messages, prompt_tokens) in enumerate(prepared, start=1):
            if resume_bundle is not None and index <= 2:
                continue
            if local["counts"]["model_calls_attempted"] >= config["budget"]["max_model_calls"]:
                break
            if prompt_tokens + config["budget"]["max_new_tokens"] > context_limit:
                local["counts"]["input_too_long"] += 1
                local["article_statuses"].append({"ordinal": row["ordinal"], "status": "INPUT_TOO_LONG",
                                                  "prompt_tokens": prompt_tokens})
                checkpoint()
                continue
            remaining = config["budget"]["total_timeout_seconds"] - (time.monotonic() - run_clock)
            if remaining <= 0:
                local["run_status"] = "TOTAL_TIMEOUT"
                break
            request_path = output_dir / f"request_{index:02d}.json"
            response_path = output_dir / f"response_{index:02d}.json"
            phase_path = output_dir / f"phase_{index:02d}.jsonl"
            request = {"messages": messages, "model_input": model_input,
                       "host_input_binding": article_input["input_binding"],
                       "prompt_tokens": prompt_tokens,
                       "max_new_tokens": config["budget"]["max_new_tokens"],
                       "seed": config["generation"]["seed"], "dtype": config["dtype"],
                       "device": config["device"],
                       "attention_implementation": config["attention_implementation"],
                       "use_cache": config["generation"]["use_cache"],
                       "generation_output_flags": config["generation_output_flags"]}
            durable_json(request_path, request)
            local["counts"]["model_calls_attempted"] += 1
            durable_event(event_path, "WORKER_LAUNCH_COMMITTED", ordinal=row["ordinal"],
                          attempt_count=local["counts"]["model_calls_attempted"])
            launched_ordinals.add(row["ordinal"])
            checkpoint()
            call_started = time.monotonic()
            env = os.environ.copy()
            env.update(OFFLINE_ENV)
            try:
                completed = subprocess.run(
                    [sys.executable, str(Path(__file__).resolve()), "--worker",
                     "--request", str(request_path), "--response", str(response_path),
                     "--phase", str(phase_path),
                     "--model-dir", str(model_dir.resolve())],
                    capture_output=True, text=True, timeout=min(
                        config["budget"]["per_call_timeout_seconds"], remaining), env=env,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                phases = record_worker_phases(local["counts"], phase_path)
                record_worker_telemetry(index, phase_path)
                if exc.stderr:
                    stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr
                    (output_dir / f"stderr_{index:02d}.txt").write_text(stderr, encoding="utf-8")
                if exc.stdout:
                    stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout
                    (output_dir / f"stdout_{index:02d}.txt").write_text(stdout, encoding="utf-8")
                local["counts"]["model_call_failures"] += 1
                local["article_statuses"].append({"ordinal": row["ordinal"], "status": "CALL_TIMEOUT",
                                                  "seconds": time.monotonic() - call_started,
                                                  "worker_phases": phases,
                                                  "worker_exit_observed": True})
                durable_event(event_path, "ATTEMPT_FINISHED", ordinal=row["ordinal"], status="CALL_TIMEOUT")
                checkpoint()
                continue
            elapsed = time.monotonic() - call_started
            phases = record_worker_phases(local["counts"], phase_path)
            record_worker_telemetry(index, phase_path)
            if completed.stderr:
                (output_dir / f"stderr_{index:02d}.txt").write_text(completed.stderr, encoding="utf-8")
            if completed.stdout:
                (output_dir / f"stdout_{index:02d}.txt").write_text(completed.stdout, encoding="utf-8")
            if completed.returncode != 0 or not response_path.is_file():
                local["counts"]["model_call_failures"] += 1
                error_text = completed.stderr or ""
                if not completed.stderr:
                    (output_dir / f"stderr_{index:02d}.txt").write_text("", encoding="utf-8")
                failure = "OOM_STOP" if "out of memory" in error_text.lower() else "MODEL_CALL_ERROR"
                local["article_statuses"].append({"ordinal": row["ordinal"], "status": failure,
                                                  "seconds": elapsed, "returncode": completed.returncode,
                                                  "worker_phases": phases,
                                                  "worker_exit_observed": True})
                durable_event(event_path, "ATTEMPT_FINISHED", ordinal=row["ordinal"], status=failure)
                checkpoint()
                if failure == "OOM_STOP":
                    local["run_status"] = "OOM_STOP"
                    break
                continue
            local["counts"]["model_calls_completed"] += 1
            raw = json.loads(response_path.read_text(encoding="utf-8"))
            if raw["generation_config_source_sha256"] != model["generation_config_sha256"]:
                raise RuntimeError("GENERATION_CONFIG_CHANGED_DURING_RUN")
            effective = raw["effective_generation_config"]
            if (effective.get("do_sample") is not False
                    or effective.get("num_beams") != 1
                    or effective.get("max_new_tokens") != config["budget"]["max_new_tokens"]
                    or effective.get("use_cache") is not True
                    or any(effective.get(name) is not False for name in config["generation_output_flags"])):
                raise RuntimeError("EFFECTIVE_GENERATION_BUDGET_CHANGED")
            local["effective_generation_config"] = raw["effective_generation_config"]
            local["effective_generation_config_sha256"] = raw["effective_generation_config_sha256"]
            local["tokenizer_special_ids"] = raw["tokenizer_special_ids"]
            article_status = {"ordinal": row["ordinal"], "status": "RESPONSE_SAVED",
                              "seconds": elapsed, "prompt_tokens": raw["prompt_tokens"],
                              "generated_tokens": raw["generated_tokens"],
                              "stop_reason": raw["stop_reason"], "worker_phases": phases,
                              "generation_seconds_synced": raw["generation_seconds_synced"]}
            durable_event(event_path, "RESPONSE_READ_BACK", ordinal=row["ordinal"],
                          generated_tokens=raw["generated_tokens"], stop_reason=raw["stop_reason"])
            checkpoint()
            try:
                output = json.loads(raw["raw_text"])
                local["counts"]["json_parse_valid"] += 1
            except (json.JSONDecodeError, TypeError):
                article_status["status"] = "JSON_PARSE_ERROR"
                local["article_statuses"].append(article_status)
                durable_event(event_path, "ATTEMPT_FINISHED", ordinal=row["ordinal"], status="JSON_PARSE_ERROR")
                checkpoint()
                continue
            wire_errors = list(Draft202012Validator(wire_schema).iter_errors(output))
            wire_validation = {"wire_schema_valid": not wire_errors,
                               "wire_error_keywords": sorted(set(str(error.validator)
                                                                 for error in wire_errors))}
            durable_json(output_dir / f"wire_{index:02d}.json",
                         {"output": output, "validation": wire_validation})
            adapted = adapt_wire(output, article_input["body"],
                                 article_input["input_binding"], wire_schema, canonical_schema)
            durable_json(output_dir / f"grounding_{index:02d}.json", adapted)
            if adapted["wire_schema_valid"] != wire_validation["wire_schema_valid"]:
                raise RuntimeError("WIRE_VALIDATION_DISAGREEMENT")
            if not wire_validation["wire_schema_valid"]:
                article_status["status"] = "WIRE_SCHEMA_INVALID"
            else:
                local["counts"]["wire_schema_valid"] += 1
                local["counts"]["empty_records_response"] += int(len(output["records"]) == 0)
                if not adapted["host_body_hash_valid"]:
                    article_status["status"] = "HOST_BINDING_ERROR"
                else:
                    local["counts"]["host_body_hash_valid"] += 1
                    statuses = [span["status"] for span in adapted["spans"]]
                    local["counts"]["quotes_checked"] += len(statuses)
                    for status, field in (("UNIQUE_EXACT", "quote_unique"),
                                          ("QUOTE_AMBIGUOUS", "quote_ambiguous"),
                                          ("QUOTE_NOT_FOUND", "quote_not_found"),
                                          ("INVALID_QUOTE", "quote_invalid")):
                        local["counts"][field] += statuses.count(status)
                    if adapted["grounding_status"] == "NO_SPANS_REQUESTED":
                        local["counts"]["no_spans_requested"] += 1
                    if adapted["canonical"] is None:
                        article_status["status"] = "QUOTE_UNRESOLVED"
                    else:
                        local["counts"]["canonical_constructible"] += 1
                        validation = validate_output(adapted["canonical"], article_input["body"],
                                                     article_input["input_binding"], schema_path)
                        durable_json(output_dir / f"canonical_{index:02d}.json",
                                     {"output": adapted["canonical"], "validation": validation})
                        if validation["schema_valid"] != adapted["canonical_schema_valid"]:
                            raise RuntimeError("CANONICAL_SCHEMA_VALIDATION_DISAGREEMENT")
                        local["counts"]["canonical_schema_valid"] += int(validation["schema_valid"])
                        local["counts"]["canonical_mechanical_valid"] += int(validation["mechanical_valid"])
                        article_status["status"] = (
                            "CANONICAL_VALID" if validation["mechanical_valid"]
                            else "CANONICAL_INVALID")
            local["article_statuses"].append(article_status)
            durable_event(event_path, "ATTEMPT_FINISHED", ordinal=row["ordinal"],
                          status=article_status["status"])
            checkpoint()
        completed_ordinals = {item["ordinal"] for item in local["article_statuses"]}
        for row, _, _, _, _ in prepared:
            if row["ordinal"] not in completed_ordinals:
                local["article_statuses"].append({"ordinal": row["ordinal"],
                                                  "status": "NOT_ATTEMPTED_AFTER_" + local["run_status"]})
        if local["run_status"] == "PREFLIGHT":
            local["run_status"] = ("GROUNDED_RUN_FINISHED_WITH_INTERRUPTION"
                                   if resume_bundle is not None else "GROUNDED_RUN_FINISHED")
        return local
    except Exception as exc:
        local["run_status"] = "PREFLIGHT_OR_RUN_ERROR"
        local["error_type"] = type(exc).__name__
        local["error_message"] = str(exc)[:300]
        if "prepared" in locals():
            completed_ordinals = {item["ordinal"] for item in local["article_statuses"]}
            for row, _, _, _, _ in prepared:
                if row["ordinal"] not in completed_ordinals:
                    local["article_statuses"].append({"ordinal": row["ordinal"],
                                                      "status": ("ATTEMPTED_UNCLASSIFIED_RUN_ERROR"
                                                                 if row["ordinal"] in locals().get("launched_ordinals", set())
                                                                 else "NOT_ATTEMPTED_AFTER_RUN_ERROR")})
        return local
    finally:
        gpu_final = host_gpu_snapshot()
        durable_json(output_dir / "gpu_host_final.json", gpu_final)
        if gpu_final["gpu_rows"]:
            local["gpu_memory_summary"]["host_final_free_mib"] = gpu_final["gpu_rows"][0]["free_mib"]
            local["gpu_memory_summary"]["host_final_used_mib"] = gpu_final["gpu_rows"][0]["used_mib"]
        local["gpu_memory_summary"]["host_final_observed_at_utc"] = gpu_final["at_utc"]
        local["run_finished_at_utc"] = utc_now()
        durable_event(event_path, "RUN_FINISHED", status=local["run_status"])
        checkpoint()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--request", type=Path)
    parser.add_argument("--response", type=Path)
    parser.add_argument("--phase", type=Path)
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--sample", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--resume-interrupted", action="store_true",
                        help="Recover only the verified externally interrupted v4 run; never retry a launch")
    parser.add_argument("--expected-prior-runner-sha",
                        help="Required original runner SHA when --resume-interrupted is used")
    args = parser.parse_args()
    if args.worker:
        if args.request is None or args.response is None or args.phase is None or args.model_dir is None:
            parser.error("worker requires --request --response --phase --model-dir")
        return _worker(args.request, args.response, args.phase, args.model_dir)
    if args.sample is None or args.manifest is None or args.output_dir is None:
        parser.error("smoke run requires --sample --manifest --output-dir")
    if args.resume_interrupted and not args.expected_prior_runner_sha:
        parser.error("--resume-interrupted requires --expected-prior-runner-sha")
    if args.expected_prior_runner_sha and not args.resume_interrupted:
        parser.error("--expected-prior-runner-sha requires --resume-interrupted")
    result = run(args.config, args.sample, args.manifest, args.output_dir, args.model_dir,
                 resume_interrupted=args.resume_interrupted,
                 expected_prior_runner_sha=args.expected_prior_runner_sha)
    print(json.dumps({"run_status": result["run_status"], "counts": result["counts"]}, ensure_ascii=False))
    return 0 if result["run_status"] in {"GROUNDED_RUN_FINISHED",
                                       "GROUNDED_RUN_FINISHED_WITH_INTERRUPTION",
                                       "NOT_RUN_NO_VERSION_BOUND_LOCAL_MODEL",
                                       "NOT_RUN_RUNTIME_DEPENDENCY_MISSING", "NOT_RUN_CUDA_UNAVAILABLE"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
