"""Read-only v5 integrity, context, and current-resource gate; no model forward."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from run_news_n1_targeted import (
    DEFAULT_CONFIG,
    PINNED_MODEL_FILE_HASHES,
    PINNED_RUNTIME,
    REPO,
    TARGET_OUTPUT_RELATIVE,
    dependency_versions,
    first_ten_inputs,
    host_gpu_snapshot,
    load_config,
    local_model_identity,
    render_messages,
    sha256_file,
    validate_hash_binding,
    build_article_input,
    durable_json,
)


PRIVATE_OUT = REPO / "artifacts/news_targeted_repair_20261115/prerun_gate_private.json"
PUBLIC_OUT = REPO / "results/news_targeted_repair_20261115/prerun_gate_receipt.json"


def own_news_python_process_ids() -> list[int]:
    """Fail closed if Windows cannot enumerate potentially active news runners."""
    if os.name != "nt":
        raise RuntimeError("OWN_WORKER_ENUMERATION_REQUIRES_WINDOWS")
    script = (
        "$ErrorActionPreference='Stop'; "
        "@(Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | "
        "Where-Object { $_.CommandLine -match "
        "'run_news_n1_targeted[.]py|run_news_n1_grounding[.]py|run_news_n1_smoke[.]py' } | "
        "Select-Object -ExpandProperty ProcessId) | ConvertTo-Json -Compress"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=15, check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("OWN_WORKER_ENUMERATION_FAILED")
    raw = completed.stdout.strip()
    ids = json.loads(raw) if raw else []
    if isinstance(ids, int):
        ids = [ids]
    if not isinstance(ids, list) or any(not isinstance(value, int) for value in ids):
        raise RuntimeError("OWN_WORKER_ENUMERATION_INVALID")
    return sorted(set(value for value in ids if value != os.getpid()))


def gate(sample_path: Path, manifest_path: Path, model_dir: Path) -> tuple[dict, dict]:
    config = load_config(DEFAULT_CONFIG)
    runtime = dependency_versions()
    if runtime != PINNED_RUNTIME:
        raise RuntimeError("RUNTIME_VERSION_MISMATCH")
    if (REPO / TARGET_OUTPUT_RELATIVE).exists():
        raise FileExistsError("TARGETED_V5_OUTPUT_ALREADY_EXISTS")
    contract = config["contract"]
    schema_path = REPO / contract["schema_path"]
    prompt_path = REPO / contract["prompt_path"]
    hashes = validate_hash_binding(config, schema_path, prompt_path)
    prompt = prompt_path.read_text(encoding="utf-8").replace("\r\n", "\n")
    wire_schema = json.loads((REPO / contract["wire_schema_path"]).read_text(encoding="utf-8"))
    try:
        embedded = json.loads(prompt.split("BEGIN_WIRE_SCHEMA\n", 1)[1].split(
            "\nEND_WIRE_SCHEMA", 1)[0])
    except (IndexError, json.JSONDecodeError) as exc:
        raise ValueError("WIRE_SCHEMA_NOT_EMBEDDED_IN_PROMPT") from exc
    if embedded != wire_schema:
        raise ValueError("PROMPT_WIRE_SCHEMA_MISMATCH")
    if sha256_file(manifest_path) != config["source"]["sample_manifest_sha256"]:
        raise ValueError("SAMPLE_MANIFEST_HASH_MISMATCH")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = first_ten_inputs(sample_path, manifest_path, config["source"]["sample_sha256"])
    model = local_model_identity(model_dir, config["model_revision"])
    if model["file_hashes"] != PINNED_MODEL_FILE_HASHES:
        raise ValueError("PINNED_MODEL_FILE_HASH_MISMATCH")
    own_ids = own_news_python_process_ids()
    if own_ids:
        raise RuntimeError("OWN_NEWS_PYTHON_PROCESS_STILL_ACTIVE")
    import torch
    from transformers import AutoTokenizer

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA_UNAVAILABLE")
    for key, value in {
        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
    }.items():
        os.environ[key] = value
    tokenizer = AutoTokenizer.from_pretrained(
        str(model_dir), local_files_only=True, trust_remote_code=False)
    if not tokenizer.chat_template:
        raise ValueError("CHAT_TEMPLATE_UNAVAILABLE")
    context_limit = min(model["context_limit_tokens"], config["context_cap_tokens"])
    token_rows = []
    for row in rows:
        article = build_article_input(row, manifest)
        model_input = {key: article[key] for key in config["model_input_fields"]}
        messages = render_messages(prompt, model_input)
        tokens = len(tokenizer.apply_chat_template(
            messages, tokenize=True, add_generation_prompt=True))
        token_rows.append({
            "ordinal": row["ordinal"], "prompt_tokens": tokens,
            "fits_with_output_budget": tokens + config["budget"]["max_new_tokens"] <= context_limit,
        })
    if not all(item["fits_with_output_budget"] for item in token_rows):
        raise ValueError("FULL_TEXT_CONTEXT_LIMIT_EXCEEDED")
    gpu = host_gpu_snapshot()
    gpu_row = gpu["gpu_rows"][0] if gpu["gpu_rows"] else {}
    at_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
    public = {
        "schema": "news_n1_targeted_prerun_gate_v1",
        "observed_at_utc": at_utc,
        "timezone": "Asia/Shanghai",
        "status": "PASS_INTEGRITY_AND_CONTEXT_ONLY",
        "model_calls": 0,
        "full_text_articles_checked": len(token_rows),
        "all_fit_with_output_budget": True,
        "context_limit_tokens": context_limit,
        "reserved_new_tokens": config["budget"]["max_new_tokens"],
        "prompt_tokens": token_rows,
        "prompt_tokens_min": min(item["prompt_tokens"] for item in token_rows),
        "prompt_tokens_median": statistics.median(item["prompt_tokens"] for item in token_rows),
        "prompt_tokens_max": max(item["prompt_tokens"] for item in token_rows),
        "config_sha256": sha256_file(DEFAULT_CONFIG),
        "runner_sha256": sha256_file(REPO / "scripts/run_news_n1_targeted.py"),
        "preflight_script_sha256": sha256_file(Path(__file__)),
        "sample_sha256": sha256_file(sample_path),
        "sample_manifest_sha256": sha256_file(manifest_path),
        **hashes,
        "model_revision": model["snapshot_revision"],
        "model_file_hashes_sha256": hashlib.sha256(json.dumps(
            model["file_hashes"], sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "tokenizer_chat_template_sha256": model["tokenizer_chat_template_sha256"],
        "runtime": runtime,
        "attention_requested": config["attention_implementation"],
        "actual_attention_kernel": "UNKNOWN",
        "gpu_free_mib": gpu_row.get("free_mib"),
        "gpu_used_mib": gpu_row.get("used_mib"),
        "gpu_total_mib": gpu_row.get("total_mib"),
        "active_compute_process_count": (
            len(gpu["compute_processes"]) if gpu["compute_process_query_available"] else None),
        "own_news_python_process_count": len(own_ids),
        "future_gpu_fit_guaranteed": False,
        "model_forward_or_generation_executed": False,
    }
    private = {
        "schema": "news_n1_targeted_prerun_private_v1",
        "public_receipt": public,
        "sample_path": str(sample_path.resolve()),
        "sample_manifest_path": str(manifest_path.resolve()),
        "model_directory": str(model_dir.resolve()),
        "model_file_hashes": model["file_hashes"],
        "own_news_python_process_ids": own_ids,
        "host_gpu_snapshot": gpu,
    }
    return public, private


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    args = parser.parse_args()
    if PRIVATE_OUT.exists() or PUBLIC_OUT.exists():
        parser.error("preflight receipt already exists; preserve the original gate")
    public, private = gate(args.sample, args.manifest, args.model_dir)
    PRIVATE_OUT.parent.mkdir(parents=True, exist_ok=True)
    PUBLIC_OUT.parent.mkdir(parents=True, exist_ok=True)
    durable_json(PRIVATE_OUT, private)
    durable_json(PUBLIC_OUT, public)
    print(json.dumps({
        "status": public["status"], "articles": public["full_text_articles_checked"],
        "prompt_tokens_min": public["prompt_tokens_min"],
        "prompt_tokens_max": public["prompt_tokens_max"],
        "gpu_free_mib": public["gpu_free_mib"],
        "own_news_python_process_count": public["own_news_python_process_count"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
