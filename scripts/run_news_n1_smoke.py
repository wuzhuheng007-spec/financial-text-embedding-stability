"""Offline, bounded retrospective company-event extraction smoke runner.

This runner never downloads a model or news. Raw prompts, responses and row-level
checks stay under ignored artifacts/. The shareable receipt contains counts and
hashes only. A smoke run is not extraction accuracy or financial validation.
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


REPO = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO / "configs" / "news_n1_smoke_v2.json"
OFFLINE_ENV = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"}
REQUIRED_MODEL_FILES = ("config.json", "tokenizer_config.json")
PINNED_MODEL_REVISION = "aa8e72537993ba99e69dfaafa59ed015b17504d1"


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
    budget = config["budget"]
    if not (
        config["source"]["first_usable_articles"] == 10
        and 1 <= budget["max_model_calls"] <= 10
        and 1 <= budget["max_new_tokens"] <= 1024
        and 1 <= budget["per_call_timeout_seconds"] <= 1800
        and 1 <= budget["total_timeout_seconds"] <= 1800
        and budget["retries"] == 0
        and config["generation"]["do_sample"] is False
        and config["generation"]["num_beams"] == 1
        and config["model_input_fields"] == ["input_binding", "source_date", "source_ticker_candidate", "title", "body"]
        and config["device"] == "cuda"
        and config["dtype"] == "float16"
        and config["generation"]["seed"] == 1729
    ):
        raise ValueError("UNAPPROVED_SMOKE_BUDGET")
    if config.get("schema") == "news_n1_smoke_config_v2":
        if (config.get("model_revision") != PINNED_MODEL_REVISION
                or config.get("model_preference") != "Qwen/Qwen2.5-3B-Instruct"
                or config.get("attention_implementation") not in {"eager", "sdpa"}
                or budget["max_model_calls"] != 10
                or budget["max_new_tokens"] != 1024
                or budget["per_call_timeout_seconds"] != 180
                or budget["total_timeout_seconds"] != 1800):
            raise ValueError("UNAPPROVED_PINNED_V2_CONFIG")
    return config


def validate_hash_binding(config: dict, schema_path: Path, prompt_path: Path) -> dict:
    validator_path = REPO / config["contract"]["validator_path"]
    actual = {"schema_sha256": sha256_file(schema_path), "prompt_sha256": sha256_file(prompt_path),
              "validator_sha256": sha256_file(validator_path)}
    if actual["schema_sha256"] != config["contract"]["schema_sha256"]:
        raise ValueError("SCHEMA_HASH_MISMATCH")
    if actual["prompt_sha256"] != config["contract"]["prompt_sha256"]:
        raise ValueError("PROMPT_HASH_MISMATCH")
    if actual["validator_sha256"] != config["contract"]["validator_sha256"]:
        raise ValueError("VALIDATOR_HASH_MISMATCH")
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


def _worker(request_path: Path, response_path: Path, phase_path: Path,
            model_dir: Path) -> int:
    """One isolated generation; parent enforces a hard wall-clock timeout."""
    durable_event(phase_path, "WORKER_PROCESS_STARTED")
    for name, value in OFFLINE_ENV.items():
        os.environ[name] = value
    request = json.loads(request_path.read_text(encoding="utf-8"))
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    dtype = {"float16": torch.float16, "bfloat16": torch.bfloat16, "float32": torch.float32}[request["dtype"]]
    torch.manual_seed(request["seed"])
    if request["device"] == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA_UNAVAILABLE")
        torch.cuda.manual_seed_all(request["seed"])
    tokenizer = AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True, trust_remote_code=False)
    model = AutoModelForCausalLM.from_pretrained(
        str(model_dir), local_files_only=True, trust_remote_code=False,
        torch_dtype=dtype, attn_implementation=request["attention_implementation"],
    ).to(request["device"])
    model.eval()
    durable_event(phase_path, "MODEL_LOADED")
    input_ids = tokenizer.apply_chat_template(request["messages"], tokenize=True,
                                               add_generation_prompt=True, return_tensors="pt").to(request["device"])
    if input_ids.shape[-1] != request["prompt_tokens"]:
        raise RuntimeError("PROMPT_TOKEN_COUNT_CHANGED")
    effective_generation = copy.deepcopy(model.generation_config)
    effective_generation.do_sample = False
    effective_generation.num_beams = 1
    effective_generation.max_new_tokens = request["max_new_tokens"]
    effective_generation.pad_token_id = (
        tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id
    )
    effective_config = json.loads(json.dumps(effective_generation.to_dict(), default=str))
    effective_config_bytes = json.dumps(effective_config, sort_keys=True, ensure_ascii=False,
                                        default=str).encode("utf-8")
    special_ids = {"tokenizer_bos_token_id": tokenizer.bos_token_id,
                   "tokenizer_eos_token_id": tokenizer.eos_token_id,
                   "tokenizer_pad_token_id": tokenizer.pad_token_id,
                   "effective_eos_token_id": effective_generation.eos_token_id,
                   "effective_pad_token_id": effective_generation.pad_token_id}
    durable_event(phase_path, "GENERATION_STARTED", prompt_tokens=int(input_ids.shape[-1]))
    generate_started = time.monotonic()
    with torch.inference_mode():
        output_ids = model.generate(
            input_ids, generation_config=effective_generation,
        )
    if request["device"] == "cuda":
        torch.cuda.synchronize()
    generation_seconds = time.monotonic() - generate_started
    generated_ids = output_ids[0][input_ids.shape[-1]:]
    token_ids = [int(x) for x in generated_ids.tolist()]
    durable_json(response_path, {
        "raw_text": tokenizer.decode(generated_ids, skip_special_tokens=True),
        "prompt_tokens": int(input_ids.shape[-1]),
        "generated_tokens": int(generated_ids.shape[-1]),
        "generated_token_ids": token_ids,
        "stop_reason": stop_reason(token_ids, effective_generation.eos_token_id,
                                   request["max_new_tokens"]),
        "generation_seconds_synced": generation_seconds,
        "effective_generation_config": effective_config,
        "effective_generation_config_sha256": hashlib.sha256(effective_config_bytes).hexdigest(),
        "generation_config_source_sha256": sha256_file(model_dir / "generation_config.json"),
        "tokenizer_special_ids": special_ids,
        "finished_at_utc": utc_now(),
    })
    durable_event(phase_path, "RESPONSE_SAVED", generated_tokens=len(token_ids))
    return 0


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
        "schema_valid": counts["json_parse_valid"],
        "binding_valid": counts["schema_valid"],
        "mechanical_valid": counts["schema_valid"],
    }
    rates = {
        name: {"conditional_numerator": counts[name], "conditional_denominator": denominator,
               "conditional_rate": _rate(counts[name], denominator),
               "attempted_denominator": attempted,
               "attempted_rate": _rate(counts[name], attempted)}
        for name, denominator in conditional.items()
    }
    return {
        "schema": "news_n1_smoke_public_receipt_v1",
        "run_started_at_utc": local["run_started_at_utc"],
        "run_finished_at_utc": local["run_finished_at_utc"],
        "run_status": local["run_status"],
        "development_articles": local["development_articles"],
        "input_sample_sha256": local["input_sample_sha256"],
        "input_manifest_sha256": local["input_manifest_sha256"],
        "config_sha256": local["config_sha256"],
        "schema_sha256": local["schema_sha256"],
        "prompt_sha256": local["prompt_sha256"],
        "validator_sha256": local["validator_sha256"],
        "model_preference": local["model_preference"],
        "model_snapshot_revision": public_model.get("snapshot_revision"),
        "model_weight_sha256": public_model.get("weight_sha256"),
        "model_generation_config_source_sha256": public_model.get("generation_config_sha256"),
        "effective_generation_config_sha256": local.get("effective_generation_config_sha256"),
        "tokenizer_special_ids": local.get("tokenizer_special_ids"),
        "attention_implementation": local.get("attention_implementation"),
        "runner_sha256": local.get("runner_sha256"),
        "model_context_limit_tokens": public_model.get("context_limit_tokens"),
        "model_runtime": local["runtime"],
        "budgets": local["budgets"],
        "counts": local["counts"],
        "mechanical_rates": rates,
        "metric_denominators": {
            "input_too_long": "context_preflight_checked_articles",
            "json_parse_valid": "model_calls_completed",
            "schema_valid": "json_parse_valid",
            "binding_valid": "schema_valid",
            "mechanical_valid": "schema_valid",
            "evidence_spans_exact": "evidence_spans_checked",
        },
        "limitations": [
            "First ten source-order articles were already used for field design; development set only.",
            "Per-article wall seconds include subprocess start, cold model load and tokenization; synchronized generation seconds are recorded separately.",
            "No independent human gold labels or extraction accuracy metrics.",
            "No financial labels, prediction or return evaluation.",
        ],
    }


def run(config_path: Path, sample_path: Path, manifest_path: Path, output_dir: Path,
        model_dir: Path | None) -> dict:
    config = load_config(config_path)
    if model_dir is not None and config.get("schema") != "news_n1_smoke_config_v2":
        raise ValueError("REAL_MODEL_REQUIRES_PINNED_V2_CONFIG")
    schema_path = REPO / config["contract"]["schema_path"]
    prompt_path = REPO / config["contract"]["prompt_path"]
    hashes = validate_hash_binding(config, schema_path, prompt_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    inputs = first_ten_inputs(sample_path, manifest_path, config["source"]["sample_sha256"])
    artifacts_root = (REPO / "artifacts").resolve()
    output_dir = output_dir.resolve()
    if output_dir == artifacts_root or not output_dir.is_relative_to(artifacts_root):
        raise ValueError("OUTPUT_MUST_BE_INSIDE_IGNORED_ARTIFACTS")
    if output_dir.exists():
        raise FileExistsError("OUTPUT_ALREADY_EXISTS")
    output_dir.mkdir(parents=True)
    event_path = output_dir / "run_events.jsonl"
    started = utc_now()
    local = {
        "schema": "news_n1_smoke_local_manifest_v1",
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
                   "schema_valid": 0, "binding_valid": 0, "mechanical_valid": 0,
                   "evidence_spans_checked": 0, "evidence_spans_exact": 0},
        "article_statuses": [],
        "model": {},
        "local_input_paths": {"sample": str(sample_path.resolve()),
                              "manifest": str(manifest_path.resolve())},
    }
    def checkpoint() -> None:
        durable_json(output_dir / "local_manifest.json", local)
        durable_json(output_dir / "shareable_summary.json", _public_summary(local))

    durable_event(event_path, "RUN_STARTED", config_sha256=local["config_sha256"],
                  max_model_calls=config["budget"]["max_model_calls"])
    checkpoint()
    try:
        if model_dir is None:
            local["run_status"] = "NOT_RUN_NO_VERSION_BOUND_LOCAL_MODEL"
            return local
        run_clock = time.monotonic()
        model = local_model_identity(model_dir, config.get("model_revision"))
        local["model"] = model
        checkpoint()
        if not all(local["runtime"].get(name) for name in ("torch", "transformers", "jsonschema")):
            local["run_status"] = "NOT_RUN_RUNTIME_DEPENDENCY_MISSING"
            return local
        import torch
        from transformers import AutoTokenizer
        from validate_news_company_event import validate_output

        if config["device"] == "cuda" and not torch.cuda.is_available():
            local["run_status"] = "NOT_RUN_CUDA_UNAVAILABLE"
            return local
        for name, value in OFFLINE_ENV.items():
            os.environ[name] = value
        tokenizer = AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True, trust_remote_code=False)
        if not tokenizer.chat_template:
            raise ValueError("CHAT_TEMPLATE_UNAVAILABLE")
        context_limit = min(model["context_limit_tokens"], config["context_cap_tokens"])
        if 1024 > context_limit:
            raise ValueError("CONTEXT_LIMIT_TOO_SMALL")
        model["effective_context_limit_tokens"] = context_limit
        model["device"] = config["device"]
        model["dtype"] = config["dtype"]
        prompt_text = prompt_path.read_text(encoding="utf-8")
        prepared = []
        preflight = []
        for row in inputs:
            article_input = build_article_input(row, manifest)
            messages = render_messages(prompt_text, article_input)
            ids = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True)
            prompt_tokens = len(ids)
            local["counts"]["context_preflight_checked_articles"] += 1
            prepared.append((row, article_input, messages, prompt_tokens))
            preflight.append({"ordinal": row["ordinal"], "prompt_tokens": prompt_tokens,
                              "effective_context_limit_tokens": context_limit,
                              "fits_with_output_budget": prompt_tokens + config["budget"]["max_new_tokens"] <= context_limit})
        durable_json(output_dir / "token_preflight.json", {"articles": preflight,
                     "chat_template_sha256": model["tokenizer_chat_template_sha256"],
                     "context_limit_tokens": context_limit})
        durable_event(event_path, "TOKEN_PREFLIGHT_FINISHED", articles=len(preflight))
        checkpoint()
        del tokenizer
        launched_ordinals: set[int] = set()
        for index, (row, article_input, messages, prompt_tokens) in enumerate(prepared, start=1):
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
            request = {"messages": messages, "prompt_tokens": prompt_tokens,
                       "max_new_tokens": config["budget"]["max_new_tokens"],
                       "seed": config["generation"]["seed"], "dtype": config["dtype"],
                       "device": config["device"],
                       "attention_implementation": config.get("attention_implementation", "eager")}
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
                if exc.stderr:
                    stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr
                    (output_dir / f"stderr_{index:02d}.txt").write_text(stderr[-3000:], encoding="utf-8")
                local["counts"]["model_call_failures"] += 1
                local["article_statuses"].append({"ordinal": row["ordinal"], "status": "CALL_TIMEOUT",
                                                  "seconds": time.monotonic() - call_started,
                                                  "worker_phases": phases})
                durable_event(event_path, "ATTEMPT_FINISHED", ordinal=row["ordinal"], status="CALL_TIMEOUT")
                checkpoint()
                continue
            elapsed = time.monotonic() - call_started
            phases = record_worker_phases(local["counts"], phase_path)
            if completed.returncode != 0 or not response_path.is_file():
                local["counts"]["model_call_failures"] += 1
                error_text = (completed.stderr or "")[-3000:]
                (output_dir / f"stderr_{index:02d}.txt").write_text(error_text, encoding="utf-8")
                failure = "OOM_STOP" if "out of memory" in error_text.lower() else "MODEL_CALL_ERROR"
                local["article_statuses"].append({"ordinal": row["ordinal"], "status": failure,
                                                  "seconds": elapsed, "returncode": completed.returncode,
                                                  "worker_phases": phases})
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
            except json.JSONDecodeError:
                article_status["status"] = "JSON_PARSE_ERROR"
                local["article_statuses"].append(article_status)
                durable_event(event_path, "ATTEMPT_FINISHED", ordinal=row["ordinal"], status="JSON_PARSE_ERROR")
                checkpoint()
                continue
            validation = validate_output(output, article_input["body"],
                                         article_input["input_binding"], schema_path)
            durable_json(output_dir / f"parsed_{index:02d}.json",
                         {"output": output, "validation": validation})
            if validation["schema_valid"]:
                local["counts"]["schema_valid"] += 1
            if validation["binding_valid"]:
                local["counts"]["binding_valid"] += 1
            if validation["mechanical_valid"]:
                local["counts"]["mechanical_valid"] += 1
            local["counts"]["evidence_spans_checked"] += validation["evidence_span_count"] or 0
            local["counts"]["evidence_spans_exact"] += validation["evidence_span_exact_match_count"] or 0
            article_status["status"] = (
                "SCHEMA_INVALID" if not validation["schema_valid"] else
                "MECHANICAL_INVALID" if not validation["mechanical_valid"] else
                "MECHANICAL_VALID"
            )
            local["article_statuses"].append(article_status)
            durable_event(event_path, "ATTEMPT_FINISHED", ordinal=row["ordinal"],
                          status=article_status["status"])
            checkpoint()
        completed_ordinals = {item["ordinal"] for item in local["article_statuses"]}
        for row, _, _, _ in prepared:
            if row["ordinal"] not in completed_ordinals:
                local["article_statuses"].append({"ordinal": row["ordinal"],
                                                  "status": "NOT_ATTEMPTED_AFTER_" + local["run_status"]})
        if local["run_status"] == "PREFLIGHT":
            local["run_status"] = "SMOKE_RUN_FINISHED"
        return local
    except Exception as exc:
        local["run_status"] = "PREFLIGHT_OR_RUN_ERROR"
        local["error_type"] = type(exc).__name__
        local["error_message"] = str(exc)[:300]
        if "prepared" in locals():
            completed_ordinals = {item["ordinal"] for item in local["article_statuses"]}
            for row, _, _, _ in prepared:
                if row["ordinal"] not in completed_ordinals:
                    local["article_statuses"].append({"ordinal": row["ordinal"],
                                                      "status": ("ATTEMPTED_UNCLASSIFIED_RUN_ERROR"
                                                                 if row["ordinal"] in locals().get("launched_ordinals", set())
                                                                 else "NOT_ATTEMPTED_AFTER_RUN_ERROR")})
        return local
    finally:
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
    args = parser.parse_args()
    if args.worker:
        if args.request is None or args.response is None or args.phase is None or args.model_dir is None:
            parser.error("worker requires --request --response --phase --model-dir")
        return _worker(args.request, args.response, args.phase, args.model_dir)
    if args.sample is None or args.manifest is None or args.output_dir is None:
        parser.error("smoke run requires --sample --manifest --output-dir")
    result = run(args.config, args.sample, args.manifest, args.output_dir, args.model_dir)
    print(json.dumps({"run_status": result["run_status"], "counts": result["counts"]}, ensure_ascii=False))
    return 0 if result["run_status"] in {"SMOKE_RUN_FINISHED", "NOT_RUN_NO_VERSION_BOUND_LOCAL_MODEL",
                                       "NOT_RUN_RUNTIME_DEPENDENCY_MISSING", "NOT_RUN_CUDA_UNAVAILABLE"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
