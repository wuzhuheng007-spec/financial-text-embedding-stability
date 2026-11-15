"""Offline gates for the single frozen v2 development run; never load a model."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/news_shortfacts_v2/run.py"
spec = importlib.util.spec_from_file_location("shortfacts_v2_run", SCRIPT)
assert spec and spec.loader
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


@pytest.fixture
def synthetic_frozen_manifest(tmp_path: Path, monkeypatch) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    art = repo / "artifacts"
    inputs = art / "news_shortfacts_20261115/inputs"
    inputs.mkdir(parents=True)
    source = art / "official/source.html"
    source.parent.mkdir(parents=True)
    source.write_text("Official release: revenue $1 million", encoding="utf-8")
    tasks = []
    for task_id in runner.ORDER:
        text_path = inputs / f"{task_id}.txt"
        text_path.write_text(f"Revenue $1 million for {task_id}", encoding="utf-8")
        tasks.append({"task_id": task_id, "evidence_id": task_id,
                      "text_path": text_path.relative_to(repo).as_posix(),
                      "text_sha256": runner.sha256_file(text_path),
                      "source_path": source.relative_to(repo).as_posix(),
                      "source_sha256": runner.sha256_file(source),
                      "source_url": "https://example.org/source", "source_locator": "paragraph 1",
                      "extraction_method": "verbatim", "entity": "Example", "event_id": "DEV"})
    manifest = inputs / "input_manifest.json"
    manifest.write_text(json.dumps({"tasks": tasks}), encoding="utf-8")
    monkeypatch.setattr(runner, "REPO", repo)
    monkeypatch.setattr(runner, "ARTIFACT_ROOT", art)
    monkeypatch.setattr(runner, "FROZEN_INPUT_ROOT", inputs)
    monkeypatch.setattr(runner, "FROZEN_INPUT_MANIFEST_SHA256", runner.sha256_file(manifest))
    return manifest, inputs / "A1.txt", source


def test_frozen_manifest_checks_passage_and_source_bytes(synthetic_frozen_manifest) -> None:
    manifest, text_path, source = synthetic_frozen_manifest
    _, tasks = runner.validate_manifest(manifest)
    assert [task["task_id"] for task in tasks] == runner.ORDER
    text_path.write_text("Changed passage", encoding="utf-8")
    with pytest.raises(ValueError, match="INPUT_TEXT_HASH_MISMATCH_A1"):
        runner.validate_manifest(manifest)
    text_path.write_text("Revenue $1 million for A1", encoding="utf-8")
    source.write_text("Changed source", encoding="utf-8")
    with pytest.raises(ValueError, match="SOURCE_HASH_MISMATCH_A1"):
        runner.validate_manifest(manifest)


def test_modified_manifest_cannot_replace_a_task(synthetic_frozen_manifest) -> None:
    manifest, _, _ = synthetic_frozen_manifest
    modified = json.loads(manifest.read_text(encoding="utf-8"))
    modified["tasks"][-1]["text_sha256"] = "0" * 64
    manifest.write_text(json.dumps(modified), encoding="utf-8")
    with pytest.raises(ValueError, match="FROZEN_INPUT_MANIFEST_HASH_MISMATCH"):
        runner.validate_manifest(manifest)


def test_v2_schema_accepts_unknown_entity_but_keeps_three_fact_cap() -> None:
    schema = json.loads((SCRIPT.parent / "output.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    fact = {"entity": None, "metric": "revenue", "value_text": "$1 million",
            "unit_text": "million dollars", "period_text": None, "basis_text": None,
            "evidence_id": "E1", "quote": "revenue was $1 million"}
    assert validator.is_valid({"facts": [fact]})
    assert validator.is_valid({"facts": []})
    assert not validator.is_valid({"facts": [fact] * 4})
    assert not validator.is_valid({"facts": [fact], "investment_view": "buy"})


def test_prompt_embeds_complete_passage_and_rejects_unsafe_id() -> None:
    template = (SCRIPT.parent / "prompt.txt").read_text(encoding="utf-8")
    passage = "Revenue was $1 million.\n\nFootnote: amounts in USD."
    rendered = runner.render_prompt(template, "E1", passage)
    assert passage in rendered and "{PASSAGE}" not in rendered
    with pytest.raises(ValueError, match="UNSAFE_EVIDENCE_ID"):
        runner.render_prompt(template, "../escape", passage)


def test_existing_run_directory_cannot_be_overwritten(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(runner, "PRIVATE_ROOT", tmp_path)
    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    with pytest.raises(FileExistsError, match="RUN_DIRECTORY_ALREADY_EXISTS_NO_OVERWRITE"):
        runner.preflight(Path("unused"), run_dir, Path("unused"))


def test_worker_rejects_direct_unregistered_generation(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="WORKER_REQUIRES_ONE_UNCONSUMED_ORCHESTRATOR_LAUNCH"):
        runner._worker("A1", tmp_path, Path("unused"))


def test_timeout_kills_worker_and_confirms_resource_check(monkeypatch) -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    monkeypatch.setattr(runner, "gpu_pid_absent", lambda _pid: True)
    exited, gpu_absent = runner.kill_and_confirm(child)
    assert exited and gpu_absent and child.poll() is not None


def test_fixed_generation_budget_and_stop_reasons() -> None:
    generation = runner.expected_generation()
    assert generation["seed"] == 1729 and generation["batch_size"] == 1
    assert generation["do_sample"] is False and generation["num_beams"] == 1
    assert generation["max_input_tokens"] == 2048 and generation["max_new_tokens"] == 768
    assert generation["generation_timeout_seconds"] == 180
    assert generation["model_load_timeout_seconds"] == 240 and generation["retries"] == 0
    assert runner.stop_reason([9, 2], 2, 768) == "EOS"
    assert runner.stop_reason([9] * 768, 2, 768) == "OUTPUT_TOKEN_BUDGET"
