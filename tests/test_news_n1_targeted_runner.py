"""No-model checks for the independent v5 targeted-prompt runner."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "configs/news_n1_smoke_v5_targeted.json"
PROMPT = REPO / "docs/news_research/company_event_prompt_v4_targeted.md"
spec = importlib.util.spec_from_file_location(
    "run_news_n1_targeted_synthetic_test",
    REPO / "scripts/run_news_n1_targeted.py",
)
assert spec is not None and spec.loader is not None
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


@pytest.mark.parametrize(
    ("returncode", "stderr", "response_saved", "expected"),
    [
        (1, "RuntimeError: synthetic failure", False, "WORKER_NONZERO_STOP"),
        (-1073741819, "", False, "NATIVE_PROCESS_CRASH_STOP"),
        (0xC0000005, "", False, "NATIVE_PROCESS_CRASH_STOP"),
        (1, "CUDA error: out of memory", False, "OOM_STOP"),
        (0, "", False, "WORKER_NO_RESPONSE_STOP"),
    ],
)
def test_failed_worker_exit_is_classified_without_causal_inference(
        returncode: int, stderr: str, response_saved: bool, expected: str):
    assert runner.classify_failed_worker(returncode, stderr, response_saved) == expected


def test_failure_classifier_rejects_success():
    with pytest.raises(ValueError, match="WORKER_FAILURE_CLASSIFIER_CALLED_ON_SUCCESS"):
        runner.classify_failed_worker(0, "", True)


def test_v5_config_hash_and_contract_are_frozen(tmp_path: Path):
    config = runner.load_config(CONFIG)
    assert runner.sha256_file(CONFIG) == runner.PINNED_V5_CONFIG_SHA256
    assert runner.validate_hash_binding(
        config, REPO / config["contract"]["schema_path"], PROMPT)["prompt_sha256"] == (
            runner.PINNED_PROMPT_SHA256)
    changed = copy.deepcopy(config)
    changed["budget"]["max_model_calls"] = 11
    path = tmp_path / "tampered_v5.json"
    path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="TARGETED_V5_CONFIG_HASH_MISMATCH"):
        runner.load_config(path)
    with pytest.raises(ValueError, match="TARGETED_V5_CONFIG_HASH_MISMATCH"):
        runner.load_config(REPO / "configs/news_n1_smoke_v4_grounded.json")


def test_v5_prompt_embeds_exact_wire_schema_without_host_binding():
    config = runner.load_config(CONFIG)
    body = "Synthetic company announced a merger."
    model_input = {"source_date": "2020-01-01", "source_ticker_candidate": "SYN",
                   "title": "Synthetic title", "body": body}
    messages = runner.render_messages(PROMPT.read_text(encoding="utf-8"), model_input)
    assert len(messages) == 2
    assert "input_binding" not in messages[1]["content"]
    assert body in messages[1]["content"]
    embedded = json.loads(messages[0]["content"].split("BEGIN_WIRE_SCHEMA\n", 1)[1].split(
        "\nEND_WIRE_SCHEMA", 1)[0])
    actual = json.loads((REPO / config["contract"]["wire_schema_path"]).read_text(encoding="utf-8"))
    assert embedded == actual


def test_native_worker_exit_stops_without_retry_or_later_launch(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A stub worker proves the v5 lifecycle without a model call."""
    config = runner.load_config(CONFIG)
    fake_repo = tmp_path / "repo"
    for stem in ("schema", "wire_schema", "prompt", "adapter", "validator"):
        relative = config["contract"][f"{stem}_path"]
        target = fake_repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((REPO / relative).read_bytes())
    sample = tmp_path / "sample.jsonl"
    sample.write_text("", encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"source": {
        "dataset": "synthetic", "revision": "fixed"}}), encoding="utf-8")
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    rows = [{"ordinal": i, "fields": {
        "Date": "2020-01-01", "Stock_symbol": "SYN",
        "Article_title": "Synthetic title", "Article": "Synthetic company announced a merger.",
    }} for i in range(1, 11)]
    monkeypatch.setattr(runner, "REPO", fake_repo)
    monkeypatch.setattr(runner, "first_ten_inputs", lambda *_: rows)
    original_sha = runner.sha256_file
    def synthetic_sha(path: Path) -> str:
        if path == sample:
            return runner.PINNED_SAMPLE_SHA256
        if path == manifest_path:
            return runner.PINNED_SAMPLE_MANIFEST_SHA256
        return original_sha(path)
    monkeypatch.setattr(runner, "sha256_file", synthetic_sha)
    monkeypatch.setattr(runner, "local_model_identity", lambda *_: {
        "snapshot_revision": runner.PINNED_MODEL_REVISION,
        "context_limit_tokens": 32768,
        "tokenizer_chat_template_sha256": "a" * 64,
        "generation_config_sha256": "b" * 64,
        "file_hashes": runner.PINNED_MODEL_FILE_HASHES,
        "weight_sha256": {},
    })
    monkeypatch.setattr(runner, "dependency_versions", lambda: runner.PINNED_RUNTIME)
    monkeypatch.setattr(runner, "host_gpu_snapshot", lambda: {
        "at_utc": runner.utc_now(), "gpu_rows": [],
        "compute_process_query_available": False, "compute_processes": [],
    })
    import torch
    import transformers
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    class FakeTokenizer:
        chat_template = "synthetic chat"
        def apply_chat_template(self, *_args, **_kwargs):
            return list(range(17))
    monkeypatch.setattr(transformers.AutoTokenizer, "from_pretrained",
                        lambda *_args, **_kwargs: FakeTokenizer())
    launches = []
    def fake_worker(command, **kwargs):
        request_path = Path(command[command.index("--request") + 1])
        phase_path = Path(command[command.index("--phase") + 1])
        launches.append(int(request_path.stem.split("_")[1]))
        assert kwargs["timeout"] <= 180
        runner.durable_event(phase_path, "WORKER_PROCESS_STARTED")
        runner.durable_event(
            phase_path, "CUDA_MEMORY", stage="WORKER_CREATED", available=True,
            free_bytes=4_000_000_000, total_bytes=12_000_000_000,
            allocated_bytes=0, reserved_bytes=0, max_allocated_bytes=0,
            max_reserved_bytes=0)
        return subprocess.CompletedProcess(command, -1073741819, "", "")
    monkeypatch.setattr(runner.subprocess, "run", fake_worker)
    output_dir = fake_repo / runner.TARGET_OUTPUT_RELATIVE
    result = runner.run(CONFIG, sample, manifest_path, output_dir, model_dir)
    assert launches == [1]
    assert result["run_status"] == "NATIVE_PROCESS_CRASH_STOP"
    assert result["counts"]["model_calls_attempted"] == 1
    assert result["counts"]["worker_process_started"] == 1
    assert result["counts"]["model_load_completed"] == 0
    assert result["counts"]["generation_started"] == 0
    assert result["counts"]["model_calls_completed"] == 0
    assert result["counts"]["model_call_failures"] == 1
    assert result["article_statuses"][0]["status"] == "NATIVE_PROCESS_CRASH_STOP"
    assert result["article_statuses"][0]["exit_code_unsigned_hex"] == "0xC0000005"
    assert all(item["status"] == "NOT_ATTEMPTED_AFTER_NATIVE_PROCESS_CRASH_STOP"
               for item in result["article_statuses"][1:])
    events = [json.loads(line) for line in (output_dir / "run_events.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert [event["ordinal"] for event in events
            if event["event"] == "WORKER_LAUNCH_COMMITTED"] == [1]
    assert events[-1]["event"] == "RUN_FINISHED"
    with pytest.raises(FileExistsError, match="OUTPUT_ALREADY_EXISTS"):
        runner.run(CONFIG, sample, manifest_path, output_dir, model_dir)
