"""Synthetic v4 runner gates. No model, GPU forward pass, or news download."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
from datetime import datetime, timedelta, timezone

import pytest


REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "configs/news_n1_smoke_v4_grounded.json"
PROMPT = REPO / "docs/news_research/company_event_prompt_v3_quotes.md"
spec = importlib.util.spec_from_file_location(
    "run_news_n1_grounding_synthetic_test", REPO / "scripts/run_news_n1_grounding.py")
assert spec is not None and spec.loader is not None
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_v4_loader_rejects_wrong_version_budget_source_and_generation(tmp_path: Path):
    approved = json.loads(CONFIG.read_text(encoding="utf-8"))
    assert runner.load_config(CONFIG) == approved
    changes = [
        ("schema", "news_n1_smoke_config_v2"),
        ("budget.max_model_calls", 11),
        ("budget.max_new_tokens", 2048),
        ("budget.per_call_timeout_seconds", 181),
        ("budget.total_timeout_seconds", 1801),
        ("budget.retries", 1),
        ("source.sample_sha256", "0" * 64),
        ("source.sample_manifest_sha256", "0" * 64),
        ("model_revision", "not-the-pinned-revision"),
        ("attention_implementation", "eager"),
        ("generation.seed", 999),
        ("generation.use_cache", False),
        ("generation_output_flags.output_scores", True),
        ("model_input_fields", ["input_binding", "body"]),
    ]
    for key, value in changes:
        changed = copy.deepcopy(approved)
        target = changed
        parts = key.split(".")
        for part in parts[:-1]:
            target = target[part]
        target[parts[-1]] = value
        path = tmp_path / f"changed_{len(list(tmp_path.iterdir()))}.json"
        path.write_text(json.dumps(changed), encoding="utf-8")
        with pytest.raises(ValueError):
            runner.load_config(path)


def test_v4_contract_hashes_fail_closed_if_prompt_changes():
    config = runner.load_config(CONFIG)
    canonical_path = REPO / config["contract"]["schema_path"]
    assert runner.validate_hash_binding(config, canonical_path, PROMPT)["prompt_sha256"] == (
        hashlib.sha256(PROMPT.read_bytes()).hexdigest())
    changed = copy.deepcopy(config)
    changed["contract"]["prompt_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="PROMPT_HASH_MISMATCH"):
        runner.validate_hash_binding(changed, canonical_path, PROMPT)


def test_model_message_omits_host_binding_and_preserves_source_text():
    config = runner.load_config(CONFIG)
    body = "甲🙂Acme acquired 蓝星."
    row = {"ordinal": 1, "fields": {"Date": "2020-01-01",
                                    "Stock_symbol": "ACM",
                                    "Article_title": "Invented title",
                                    "Article": body}}
    manifest = {"source": {"dataset": "synthetic/FNSPID", "revision": "fixed"}}
    article = runner.build_article_input(row, manifest)
    assert article["input_binding"]["body_sha256"] == hashlib.sha256(body.encode()).hexdigest()
    model_input = {name: article[name] for name in config["model_input_fields"]}
    assert "input_binding" not in model_input
    messages = runner.render_messages(PROMPT.read_text(encoding="utf-8"), model_input)
    assert len(messages) == 2
    assert body in messages[1]["content"]
    assert article["input_binding"]["body_sha256"] not in json.dumps(messages)
    assert json.loads(messages[0]["content"].split("BEGIN_WIRE_SCHEMA\n", 1)[1].split(
        "\nEND_WIRE_SCHEMA", 1)[0]) == json.loads(
            (REPO / config["contract"]["wire_schema_path"]).read_text(encoding="utf-8"))


def test_cuda_snapshot_failure_is_unknown_not_zero():
    class FailingCuda:
        @staticmethod
        def mem_get_info():
            raise RuntimeError("unavailable")

    class FakeTorch:
        cuda = FailingCuda()

    snapshot = runner.cuda_snapshot(FakeTorch(), "WORKER_CREATED")
    assert snapshot["available"] is False
    assert snapshot["measurement_error_type"] == "RuntimeError"
    assert "free_bytes" not in snapshot
    assert "max_allocated_bytes" not in snapshot


def test_zero_quotes_is_not_reported_as_perfect_grounding():
    counts = {
        "model_calls_attempted": 1, "model_calls_completed": 1,
        "json_parse_valid": 1, "wire_schema_valid": 1,
        "canonical_constructible": 1, "canonical_schema_valid": 1,
        "canonical_mechanical_valid": 1, "host_body_hash_valid": 1,
        "quotes_checked": 0, "quote_unique": 0, "quote_ambiguous": 0,
        "quote_not_found": 0, "quote_invalid": 0,
        "no_spans_requested": 1, "empty_records_response": 1,
    }
    local = {
        "pipeline_version": "wire_v2_exact_quote_host_binding_v1",
        "run_started_at_utc": "2026-11-14T00:00:00+00:00",
        "run_finished_at_utc": "2026-11-14T00:01:00+00:00",
        "run_status": "GROUNDED_RUN_FINISHED",
        "development_articles": 10,
        "input_sample_sha256": "a" * 64, "input_manifest_sha256": "b" * 64,
        "config_sha256": "c" * 64, "schema_sha256": "d" * 64,
        "wire_schema_sha256": "e" * 64, "prompt_sha256": "f" * 64,
        "adapter_sha256": "1" * 64, "validator_sha256": "2" * 64,
        "model_preference": "Qwen/Qwen2.5-3B-Instruct", "model": {},
        "attention_implementation": "sdpa", "runner_sha256": "3" * 64,
        "runtime": {}, "budgets": {}, "counts": counts,
    }
    public = runner._public_summary(local)
    assert public["quote_rate"]["quote_denominator"] == 0
    assert public["quote_rate"]["unique_rate"] is None
    assert public["quote_rate"]["zero_quote_outputs_are_grounding_success"] is False
    assert public["counts"]["no_spans_requested"] == 1


def test_host_hash_ambiguity_and_original_validator_remain_separate():
    fixture_spec = importlib.util.spec_from_file_location(
        "news_contract_fixture_grounded_boundaries_test",
        REPO / "tests/test_news_company_event_contract.py")
    assert fixture_spec is not None and fixture_spec.loader is not None
    fixture = importlib.util.module_from_spec(fixture_spec)
    fixture_spec.loader.exec_module(fixture)
    body, binding, canonical = fixture.fixture_output()
    wire = copy.deepcopy(canonical)
    wire.pop("input_binding")
    wire["schema_version"] = "n1_company_event_wire_v2"
    for _, spans in runner.adapt_wire.__globals__["span_containers"](wire):
        for span in spans:
            span.pop("start")
            span.pop("end")
    wire_schema = json.loads(
        (REPO / "docs/news_research/company_event_wire_v2.schema.json").read_text(encoding="utf-8"))
    canonical_schema = json.loads(
        (REPO / "docs/news_research/company_event_output.schema.json").read_text(encoding="utf-8"))
    wrong_binding = copy.deepcopy(binding)
    wrong_binding["body_sha256"] = "0" * 64
    wrong = runner.adapt_wire(wire, body, wrong_binding, wire_schema, canonical_schema)
    assert wrong["wire_schema_valid"] is True
    assert wrong["grounding_status"] == "HOST_BODY_HASH_MISMATCH"
    assert wrong["canonical"] is None
    repeated = body + "\n" + body
    repeated_binding = copy.deepcopy(binding)
    repeated_binding["body_sha256"] = hashlib.sha256(repeated.encode()).hexdigest()
    ambiguous = runner.adapt_wire(
        wire, repeated, repeated_binding, wire_schema, canonical_schema)
    assert ambiguous["grounding_status"] == "UNRESOLVED_QUOTES"
    assert ambiguous["canonical"] is None
    assert ambiguous["spans"]
    assert any(span["status"] == "QUOTE_AMBIGUOUS" for span in ambiguous["spans"])
    no_event_role = copy.deepcopy(wire)
    no_event_role["records"][0]["evidence_spans"][0]["role"] = "BUSINESS_IMPACT"
    built = runner.adapt_wire(no_event_role, body, binding, wire_schema, canonical_schema)
    assert built["canonical_schema_valid"] is True
    assert built["canonical"] is not None
    checked = runner.validate_output(
        built["canonical"], body, binding,
        REPO / "docs/news_research/company_event_output.schema.json")
    assert checked["mechanical_valid"] is False


def test_synthetic_worker_stages_are_saved_without_real_model_calls(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Exercise four ordinal outcomes using a stub worker; never load a model."""
    fixture_spec = importlib.util.spec_from_file_location(
        "news_contract_fixture_grounded_runner_test",
        REPO / "tests/test_news_company_event_contract.py")
    assert fixture_spec is not None and fixture_spec.loader is not None
    fixture = importlib.util.module_from_spec(fixture_spec)
    fixture_spec.loader.exec_module(fixture)
    body, _, canonical = fixture.fixture_output()
    wire = copy.deepcopy(canonical)
    wire.pop("input_binding")
    wire["schema_version"] = "n1_company_event_wire_v2"
    for _, spans in runner.adapt_wire.__globals__["span_containers"](wire):
        for span in spans:
            span.pop("start")
            span.pop("end")

    fake_repo = tmp_path / "repo"
    config = runner.load_config(CONFIG)
    for stem in ("schema", "wire_schema", "prompt", "adapter", "validator"):
        relative = config["contract"][f"{stem}_path"]
        target = fake_repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((REPO / relative).read_bytes())
    sample = tmp_path / "synthetic_sample.jsonl"
    sample.write_text("", encoding="utf-8")
    manifest = tmp_path / "synthetic_manifest.json"
    manifest.write_text(json.dumps({"source": {
        "dataset": "Zihan1004/FNSPID",
        "revision": config["source"]["dataset_revision"]}}), encoding="utf-8")
    model_dir = tmp_path / "fake_model_snapshot"
    model_dir.mkdir()
    rows = [{"ordinal": i, "fields": {
        "Date": "2020-01-01", "Stock_symbol": "ACM",
        "Article_title": "Invented title", "Article": body,
    }} for i in range(1, 11)]
    monkeypatch.setattr(runner, "REPO", fake_repo)
    monkeypatch.setattr(runner, "first_ten_inputs", lambda *_: rows)
    real_sha = runner.sha256_file
    def sha_for_test(path: Path) -> str:
        if path == sample:
            return runner.PINNED_SAMPLE_SHA256
        if path == manifest:
            return runner.PINNED_SAMPLE_MANIFEST_SHA256
        return real_sha(path)
    monkeypatch.setattr(runner, "sha256_file", sha_for_test)
    generation_source_sha = "a" * 64
    monkeypatch.setattr(runner, "local_model_identity", lambda *_: {
        "snapshot_revision": runner.PINNED_MODEL_REVISION,
        "context_limit_tokens": 32768,
        "tokenizer_chat_template_sha256": "b" * 64,
        "generation_config_sha256": generation_source_sha,
        "file_hashes": {"generation_config.json": generation_source_sha},
        "weight_sha256": {},
    })
    monkeypatch.setattr(runner, "dependency_versions", lambda: {
        "python": "synthetic", "torch": "synthetic",
        "transformers": "synthetic", "jsonschema": "synthetic",
    })
    monkeypatch.setattr(runner, "host_gpu_snapshot", lambda: {
        "at_utc": runner.utc_now(), "gpu_rows": [],
        "compute_process_query_available": False, "compute_processes": [],
    })
    import torch
    import transformers
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    class FakeTokenizer:
        chat_template = "synthetic template"
        def apply_chat_template(self, *_args, **_kwargs):
            return list(range(17))
    monkeypatch.setattr(transformers.AutoTokenizer, "from_pretrained",
                        lambda *_args, **_kwargs: FakeTokenizer())

    seen_attempts = []
    def fake_worker(command, **_kwargs):
        request_path = Path(command[command.index("--request") + 1])
        response_path = Path(command[command.index("--response") + 1])
        phase_path = Path(command[command.index("--phase") + 1])
        ordinal = int(request_path.stem.split("_")[1])
        seen_attempts.append(ordinal)
        request = json.loads(request_path.read_text(encoding="utf-8"))
        runner.durable_event(phase_path, "WORKER_PROCESS_STARTED")
        runner.durable_event(phase_path, "CUDA_PEAK_RESET", stage="BEFORE_MODEL_LOAD")
        for stage, event in (("WORKER_CREATED", None),
                             ("AFTER_MODEL_LOAD", "MODEL_LOADED"),
                             ("BEFORE_GENERATION", "GENERATION_STARTED")):
            if event:
                runner.durable_event(phase_path, event)
            runner.durable_event(
                phase_path, "CUDA_MEMORY", stage=stage, available=True,
                free_bytes=4_000_000_000, total_bytes=12_000_000_000,
                allocated_bytes=2_000_000_000, reserved_bytes=2_500_000_000,
                max_allocated_bytes=2_000_000_000,
                max_reserved_bytes=2_500_000_000)
        if ordinal == 4:
            runner.durable_event(phase_path, "WORKER_EXCEPTION",
                                 error_code="CUDA_OUT_OF_MEMORY",
                                 exception_type="SyntheticCudaError")
            runner.durable_event(
                phase_path, "CUDA_MEMORY", stage="WORKER_FINALLY", available=True,
                free_bytes=3_000_000_000, total_bytes=12_000_000_000,
                allocated_bytes=3_000_000_000, reserved_bytes=3_500_000_000,
                max_allocated_bytes=3_000_000_000,
                max_reserved_bytes=3_500_000_000)
            return subprocess.CompletedProcess(command, 1, "", "CUDA error: out of memory")
        output = copy.deepcopy(wire)
        if ordinal == 2:
            output["article_unknown_reason"] = "NO_EVENT"
        elif ordinal == 3:
            output["records"][0]["evidence_spans"][0]["text"] = "absent synthetic quotation"
        effective = {
            "do_sample": False, "num_beams": 1, "max_new_tokens": 1024,
            "use_cache": True, "output_attentions": False,
            "output_hidden_states": False, "output_scores": False,
        }
        effective_hash = hashlib.sha256(json.dumps(
            effective, sort_keys=True, ensure_ascii=False,
            default=str).encode("utf-8")).hexdigest()
        runner.durable_json(response_path, {
            "raw_text": json.dumps(output, ensure_ascii=False),
            "prompt_tokens": request["prompt_tokens"],
            "generated_tokens": 1, "generated_token_ids": [2],
            "stop_reason": "EOS", "generation_seconds_synced": 0.01,
            "effective_generation_config": effective,
            "effective_generation_config_sha256": effective_hash,
            "generation_config_source_sha256": generation_source_sha,
            "tokenizer_special_ids": {"effective_eos_token_id": 2},
            "requested_attention_implementation": "sdpa",
            "model_config_attention_implementation": "sdpa",
            "actual_sdpa_kernel": "UNKNOWN",
            "batch_size": 1,
            "finished_at_utc": runner.utc_now(),
        })
        runner.durable_event(phase_path, "GENERATION_FINISHED")
        runner.durable_event(
            phase_path, "CUDA_MEMORY", stage="AFTER_GENERATION", available=True,
            free_bytes=3_500_000_000, total_bytes=12_000_000_000,
            allocated_bytes=2_500_000_000, reserved_bytes=3_000_000_000,
            max_allocated_bytes=2_500_000_000,
            max_reserved_bytes=3_000_000_000)
        runner.durable_event(phase_path, "RESPONSE_SAVED", generated_tokens=1)
        runner.durable_event(
            phase_path, "CUDA_MEMORY", stage="WORKER_FINALLY", available=True,
            free_bytes=4_000_000_000, total_bytes=12_000_000_000,
            allocated_bytes=1_000_000_000, reserved_bytes=1_500_000_000,
            max_allocated_bytes=2_500_000_000,
            max_reserved_bytes=3_000_000_000)
        return subprocess.CompletedProcess(command, 0, "", "")
    monkeypatch.setattr(runner.subprocess, "run", fake_worker)
    output_dir = fake_repo / "artifacts" / "synthetic_run"
    result = runner.run(CONFIG, sample, manifest, output_dir, model_dir)
    assert seen_attempts == [1, 2, 3, 4]
    assert result["run_status"] == "OOM_STOP"
    assert result["counts"]["model_calls_attempted"] == 4
    assert result["counts"]["model_calls_completed"] == 3
    assert result["counts"]["json_parse_valid"] == 3
    assert result["counts"]["wire_schema_valid"] == 2
    assert result["counts"]["canonical_constructible"] == 1
    assert result["counts"]["canonical_mechanical_valid"] == 1
    assert result["counts"]["quote_not_found"] == 1
    assert result["counts"]["quote_unique"] > 0
    assert [item["status"] for item in result["article_statuses"]] == [
        "CANONICAL_VALID", "WIRE_SCHEMA_INVALID", "QUOTE_UNRESOLVED",
        "OOM_STOP", *(["NOT_ATTEMPTED_AFTER_OOM_STOP"] * 6),
    ]
    first_request = json.loads((output_dir / "request_01.json").read_text(encoding="utf-8"))
    assert "input_binding" not in first_request["model_input"]
    assert "host_input_binding" in first_request
    assert (output_dir / "response_01.json").is_file()
    assert (output_dir / "wire_02.json").is_file()
    assert json.loads((output_dir / "grounding_03.json").read_text(encoding="utf-8"))["canonical"] is None
    assert (output_dir / "canonical_01.json").is_file()

    # Recast the completed synthetic run as an interrupted prior process.
    # The same stub worker must then be invoked only for ordinal 3 onward.
    prior_source = output_dir.parent / "original_v4_runner_df563dea6878.py"
    prior_source.write_bytes((REPO / "results/news_grounding_runtime_20261114/original_v4_runner.py").read_bytes())
    (output_dir / "response_01.json").write_bytes((output_dir / "response_02.json").read_bytes())
    (output_dir / "wire_01.json").write_bytes((output_dir / "wire_02.json").read_bytes())
    invalid_wire = json.loads((output_dir / "wire_01.json").read_text(encoding="utf-8"))["output"]
    first_binding = runner.build_article_input(rows[0],
        json.loads(manifest.read_text(encoding="utf-8")))["input_binding"]
    invalid_grounding = runner.adapt_wire(
        invalid_wire, body, first_binding,
        json.loads((fake_repo / config["contract"]["wire_schema_path"]).read_text(encoding="utf-8")),
        json.loads((fake_repo / config["contract"]["schema_path"]).read_text(encoding="utf-8")))
    runner.durable_json(output_dir / "grounding_01.json", invalid_grounding)
    (output_dir / "canonical_01.json").unlink()
    for path in output_dir.iterdir():
        if (path.name == "gpu_host_final.json"
                or any(path.name.endswith(f"_{i:02d}.json")
                       or path.name.endswith(f"_{i:02d}.jsonl")
                       or path.name.endswith(f"_{i:02d}.txt")
                       for i in range(3, 11))
                or any(path.name.startswith(prefix) for prefix in (
                    "response_02.", "wire_02.", "grounding_02.",
                    "canonical_02.", "gpu_worker_02.", "gpu_host_after_02.",
                    "stderr_02."))):
            path.unlink()
    (output_dir / "phase_02.jsonl").write_text("", encoding="utf-8")
    runner.durable_event(output_dir / "phase_02.jsonl", "WORKER_PROCESS_STARTED")
    runner.durable_event(output_dir / "phase_02.jsonl", "CUDA_PEAK_RESET",
                         stage="BEFORE_MODEL_LOAD")
    runner.durable_event(output_dir / "phase_02.jsonl", "CUDA_MEMORY",
                         stage="WORKER_CREATED", available=True,
                         allocated_bytes=0, reserved_bytes=0,
                         max_allocated_bytes=0, max_reserved_bytes=0,
                         free_bytes=8_000_000_000, total_bytes=12_000_000_000)
    result["runner_sha256"] = runner.INTERRUPTED_PRIOR_RUNNER_SHA256
    result["run_status"] = "PREFLIGHT"
    result["run_finished_at_utc"] = None
    first_status = copy.deepcopy(result["article_statuses"][1])
    first_status["ordinal"] = 1
    result["article_statuses"] = [first_status]
    result["counts"].update({
        "articles_considered": 10, "context_preflight_checked_articles": 10,
        "input_too_long": 0, "model_calls_attempted": 2,
        "worker_process_started": 1, "model_load_completed": 1,
        "generation_started": 1, "model_calls_completed": 1,
        "model_call_failures": 0, "json_parse_valid": 1,
        "wire_schema_valid": 0, "canonical_constructible": 0,
        "host_body_hash_valid": 0, "quotes_checked": 0,
        "quote_unique": 0, "quote_ambiguous": 0,
        "quote_not_found": 0, "quote_invalid": 0,
        "canonical_schema_valid": 0, "canonical_mechanical_valid": 0,
        "no_spans_requested": 0, "empty_records_response": 0,
    })
    gpu_first = json.loads((output_dir / "gpu_worker_01.json").read_text(encoding="utf-8"))
    result["gpu_memory_summary"].update({
        "worker_snapshot_count": gpu_first["memory_snapshot_count"],
        "worker_snapshot_available": gpu_first["memory_snapshot_available"],
        "maximum_allocated_bytes": gpu_first["maximum_allocated_bytes"],
        "maximum_reserved_bytes": gpu_first["maximum_reserved_bytes"],
        "minimum_free_bytes": gpu_first["minimum_free_bytes"],
    })
    runner.durable_json(output_dir / "local_manifest.json", result)
    runner.durable_json(output_dir / "shareable_summary.json", runner._public_summary(result))
    events = output_dir / "run_events.jsonl"
    events.write_text("", encoding="utf-8")
    for event, fields in (
        ("RUN_STARTED", {}), ("TOKEN_PREFLIGHT_FINISHED", {}),
        ("WORKER_LAUNCH_COMMITTED", {"ordinal": 1, "attempt_count": 1}),
        ("RESPONSE_READ_BACK", {"ordinal": 1}),
        ("ATTEMPT_FINISHED", {"ordinal": 1, "status": "WIRE_SCHEMA_INVALID"}),
        ("WORKER_LAUNCH_COMMITTED", {"ordinal": 2, "attempt_count": 2}),
    ):
        runner.durable_event(events, event, **fields)
    prior_event_prefix = events.read_bytes()
    replay = runner.run(CONFIG, sample, manifest, output_dir, model_dir,
                        resume_interrupted=True,
                        expected_prior_runner_sha=runner.INTERRUPTED_PRIOR_RUNNER_SHA256)
    assert seen_attempts == [1, 2, 3, 4, 3, 4]
    assert replay["run_status"] == "OOM_STOP"
    assert replay["counts"]["model_calls_attempted"] == 4
    assert replay["counts"]["model_call_failures"] == 2  # interrupted 2 + OOM 4
    assert [item["ordinal"] for item in replay["article_statuses"][:4]] == [1, 2, 3, 4]
    assert replay["article_statuses"][1]["status"] == "INTERRUPTED_PROCESS_UNCLASSIFIED"
    assert replay["recovery"]["original_wall_budget_seconds"] == 1800
    assert (output_dir / "recovery_prestate_run_events.jsonl").read_bytes() == prior_event_prefix
    assert events.read_bytes().startswith(prior_event_prefix)
    assert not (output_dir / "response_02.json").exists()


def test_interrupted_resume_gate_preserves_prior_bytes_and_rejects_changed_config(
        tmp_path: Path):
    """Synthetic interrupted receipt: inspect only, with zero worker launches."""
    config = runner.load_config(CONFIG)
    sample = tmp_path / "sample.jsonl"
    manifest_path = tmp_path / "manifest.json"
    sample.write_text("synthetic", encoding="utf-8")
    manifest = {"source": {"dataset": "synthetic", "revision": "fixed"}}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    out = tmp_path / "interrupted"
    out.mkdir()
    original = tmp_path / "original_v4_runner_df563dea6878.py"
    original.write_bytes((REPO / "results/news_grounding_runtime_20261114/original_v4_runner.py").read_bytes())
    rows = [{"ordinal": i, "fields": {"Date": "2020-01-01", "Stock_symbol": "ACM",
              "Article_title": "Synthetic", "Article": "Acme announced a merger."}}
            for i in range(1, 11)]
    prompt = PROMPT.read_text(encoding="utf-8").replace("\r\n", "\n")
    preflight = {"articles": [{"ordinal": i, "prompt_tokens": 17}
                              for i in range(1, 11)]}
    runner.durable_json(out / "token_preflight.json", preflight)
    for ordinal in (1, 2):
        article = runner.build_article_input(rows[ordinal - 1], manifest)
        model_input = {key: article[key] for key in config["model_input_fields"]}
        runner.durable_json(out / f"request_{ordinal:02d}.json", {
            "model_input": model_input, "host_input_binding": article["input_binding"],
            "messages": runner.render_messages(prompt, model_input), "prompt_tokens": 17,
            "max_new_tokens": 1024, "seed": 1729,
            "attention_implementation": "sdpa",
            "generation_output_flags": config["generation_output_flags"],
        })
    for name in ("response_01.json", "wire_01.json", "grounding_01.json",
                 "phase_01.jsonl"):
        (out / name).write_text("{}\n", encoding="utf-8")
    runner.durable_event(out / "phase_02.jsonl", "WORKER_PROCESS_STARTED")
    counts = {"articles_considered": 10, "context_preflight_checked_articles": 10,
              "input_too_long": 0, "model_calls_attempted": 2,
              "worker_process_started": 1, "model_load_completed": 1,
              "generation_started": 1, "model_calls_completed": 1,
              "model_call_failures": 0, "json_parse_valid": 1,
              "wire_schema_valid": 0, "canonical_constructible": 0}
    started = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    local = {"run_status": "PREFLIGHT", "run_finished_at_utc": None,
             "run_started_at_utc": started, "runner_sha256": runner.INTERRUPTED_PRIOR_RUNNER_SHA256,
             "config_sha256": runner.sha256_file(CONFIG),
             "input_sample_sha256": runner.sha256_file(sample),
             "input_manifest_sha256": runner.sha256_file(manifest_path),
             "pipeline_version": config["pipeline_version"],
             "model_preference": config["model_preference"],
             "attention_implementation": "sdpa", "budgets": config["budget"],
             "counts": counts, "article_statuses": [{"ordinal": 1, "status": "WIRE_SCHEMA_INVALID"}]}
    runner.durable_json(out / "local_manifest.json", local)
    runner.durable_json(out / "shareable_summary.json", {"synthetic": True})
    for event, fields in (
        ("RUN_STARTED", {}), ("TOKEN_PREFLIGHT_FINISHED", {}),
        ("WORKER_LAUNCH_COMMITTED", {"ordinal": 1}),
        ("RESPONSE_READ_BACK", {"ordinal": 1}),
        ("ATTEMPT_FINISHED", {"ordinal": 1, "status": "WIRE_SCHEMA_INVALID"}),
        ("WORKER_LAUNCH_COMMITTED", {"ordinal": 2, "attempt_count": 2}),
    ):
        runner.durable_event(out / "run_events.jsonl", event, **fields)
    prefix = (out / "run_events.jsonl").read_bytes()
    prior, bundle = runner.verify_interrupted_resume(
        config, CONFIG, sample, manifest_path, out, prompt, {}, rows, manifest,
        runner.INTERRUPTED_PRIOR_RUNNER_SHA256)
    assert prior["counts"]["model_calls_attempted"] == 2
    assert bundle["provenance"]["original_runner_source_sha256"] == runner.INTERRUPTED_PRIOR_RUNNER_SHA256
    assert 100 <= bundle["provenance"]["wall_seconds_elapsed_at_recovery"] < 1800
    assert (out / "run_events.jsonl").read_bytes() == prefix
    changed = tmp_path / "changed_config.json"
    changed.write_bytes(CONFIG.read_bytes())
    with pytest.raises(ValueError, match="INTERRUPTED_RUN_BINDING_MISMATCH"):
        runner.verify_interrupted_resume(config, changed, sample, manifest_path,
                                         out, prompt, {}, rows, manifest,
                                         runner.INTERRUPTED_PRIOR_RUNNER_SHA256)
