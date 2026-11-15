"""Focused gates for the six-task runner; no model generation in these tests."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/news_shortfacts_v1/run.py"
spec = importlib.util.spec_from_file_location("shortfacts_run", SCRIPT)
assert spec and spec.loader
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_minimal_schema_rejects_fourth_fact_and_extra_key() -> None:
    schema = json.loads((SCRIPT.parent / "output.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    fact = {"entity": "Example", "metric": "revenue", "value_text": "$1 million",
            "unit_text": "million dollars", "period_text": "Q1", "basis_text": None,
            "evidence_id": "A1", "quote": "revenue was $1 million"}
    assert validator.is_valid({"facts": [fact]})
    assert not validator.is_valid({"facts": [fact] * 4})
    assert not validator.is_valid({"facts": [fact], "commentary": "buy"})


def test_prompt_does_not_truncate_evidence_and_rejects_bad_id() -> None:
    template = (SCRIPT.parent / "prompt.txt").read_text(encoding="utf-8")
    passage = "Revenue was $1 million.\n\nFootnote: amounts in USD."
    rendered = runner.render_prompt(template, "A1", passage)
    assert passage in rendered and "A1" in rendered
    assert "{PASSAGE}" not in rendered
    with pytest.raises(ValueError, match="UNSAFE_EVIDENCE_ID"):
        runner.render_prompt(template, "../escape", passage)


def test_manifest_rejects_changed_source_or_passage(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    art = repo / "artifacts"
    inputs = art / "news_shortfacts_20261115/inputs"
    inputs.mkdir(parents=True)
    source = art / "earnings_20261115/source.html"
    source.parent.mkdir(parents=True)
    source.write_text("Source value 1", encoding="utf-8")
    tasks = []
    for task_id in runner.ORDER:
        p = inputs / f"{task_id}.txt"
        p.write_text(f"Source value 1 for {task_id}", encoding="utf-8")
        tasks.append({"task_id": task_id, "evidence_id": task_id,
                      "text_path": p.relative_to(repo).as_posix(),
                      "text_sha256": runner.sha256_file(p),
                      "source_path": source.relative_to(repo).as_posix(),
                      "source_sha256": runner.sha256_file(source),
                      "source_url": "https://example.com/source",
                      "source_locator": "paragraph 1", "extraction_method": "verbatim",
                      "entity": "Example", "event_id": "DEV"})
    manifest = inputs / "input_manifest.json"
    manifest.write_text(json.dumps({"tasks": tasks}), encoding="utf-8")
    monkeypatch.setattr(runner, "REPO", repo)
    monkeypatch.setattr(runner, "ARTIFACT_ROOT", art)
    monkeypatch.setattr(runner, "PRIVATE_ROOT", art / "news_shortfacts_20261115")
    assert len(runner.validate_manifest(manifest)[1]) == 6
    source.write_text("Source value 2", encoding="utf-8")
    with pytest.raises(ValueError, match="SOURCE_HASH_MISMATCH_A1"):
        runner.validate_manifest(manifest)


def test_process_timeout_kills_worker(monkeypatch) -> None:
    worker = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    monkeypatch.setattr(runner, "gpu_pid_absent", lambda _pid: True)
    exited, released = runner.kill_and_confirm(worker)
    assert exited is True and released is True and worker.poll() is not None


def test_stop_reason_keeps_token_budget_distinct_from_eos() -> None:
    assert runner.stop_reason([9, 2], 2, 768) == "EOS"
    assert runner.stop_reason([9] * 768, 2, 768) == "OUTPUT_TOKEN_BUDGET"
    assert runner.stop_reason([9], 2, 768) == "OTHER_STOP"
