"""Focused checks for the private Qwen short-fact evaluation boundary."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.news_shortfacts_v1.evaluate import (
    evaluate, schema_errors, strict_json_loads, write_public_summary,
)


def make_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    tasks = []
    refs = []
    for i in range(6):
        task_id = f"T{i+1}"
        evidence_id = f"P{i+1}"
        text = f"{task_id} reported revenue of {i+1} dollars for quarter {i+1}."
        source = tmp_path / f"source_{task_id}.txt"
        passage = tmp_path / f"passage_{task_id}.txt"
        source.write_text(text, encoding="utf-8")
        passage.write_text(text, encoding="utf-8")
        digest = hashlib.sha256(text.encode()).hexdigest()
        tasks.append({"task_id": task_id, "evidence_id": evidence_id,
                      "source_path": str(source), "source_sha256": digest,
                      "text_path": str(passage), "text_sha256": digest})
        refs.append({"task_id": task_id, "facts": [{
            "entity": task_id, "metric": "revenue", "value_text": str(i+1),
            "unit_text": "dollars", "period_text": f"quarter {i+1}",
            "basis_text": None, "evidence_id": evidence_id,
            "quote": f"revenue of {i+1} dollars",
        }]})
    manifest = tmp_path / "input_manifest.json"
    reference = tmp_path / "reference_draft_v1.json"
    manifest.write_text(json.dumps({"tasks": tasks}), encoding="utf-8")
    reference.write_text(json.dumps({"status": "CODEX_REFERENCE_DRAFT", "tasks": refs}), encoding="utf-8")
    run_dir = tmp_path / "run"
    (run_dir / "raw_responses").mkdir(parents=True)
    ledger = run_dir / "attempt_ledger.jsonl"
    return manifest, reference, ledger, run_dir


def output_fact(task_id: str, value: str, quote: str) -> dict:
    return {"entity": task_id, "metric": "revenue", "value_text": value,
            "unit_text": "dollars", "period_text": f"quarter {task_id[1:]}",
            "basis_text": None, "evidence_id": "P" + task_id[1:], "quote": quote}


def write_attempt(ledger: Path, task_id: str, raw: str) -> None:
    path = ledger.parent / "raw_responses" / f"{task_id}.txt"
    path.write_text(raw, encoding="utf-8")
    row = {"task_id": task_id, "status": "COMPLETED", "generation_started": True,
           "response_completed": True, "raw_response_path": f"raw_responses/{task_id}.txt"}
    with ledger.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row) + "\n")


def test_strict_json_rejects_nonstandard_and_schema_rejects_extra_fields() -> None:
    with pytest.raises(ValueError, match="Non-JSON constant"):
        strict_json_loads('{"facts": NaN}')
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        strict_json_loads('{"facts": [], "facts": []}')
    assert schema_errors({"facts": [output_fact("T1", "1", "revenue of 1 dollars")], "answer": 1})


def test_fenced_view_is_separate_from_strict_json_and_private_text_not_public(tmp_path: Path) -> None:
    manifest, reference, ledger, run_dir = make_fixture(tmp_path)
    first = output_fact("T1", "1", "revenue of 1 dollars")
    write_attempt(ledger, "T1", json.dumps({"facts": [first]}))
    second = output_fact("T2", "2", "revenue of 2 dollars")
    write_attempt(ledger, "T2", "```json\n" + json.dumps({"facts": [second]}) + "\n```")
    mech, review, summary = evaluate(manifest, reference, ledger, run_dir / "eval")
    assert mech["counts"]["planned_tasks"] == 6
    assert mech["counts"]["attempted_tasks"] == 2
    assert mech["counts"]["strict_json_valid_tasks"] == 1
    assert mech["counts"]["schema_valid_tasks"] == 2
    assert mech["counts"]["strict_schema_valid_tasks"] == 1
    assert mech["counts"]["fenced_view_schema_valid_tasks"] == 1
    assert mech["tasks"][1]["fenced_json_view_valid"] is True
    assert (run_dir / "eval/parsed_views/T2.json").is_file()
    assert review["tasks"][0]["model_facts"][0]["codex_review"]["semantic_support"] == "UNRESOLVED"
    assert summary["semantic_support_denominator"] == 0
    public = tmp_path / "public.json"
    write_public_summary(public, mech, summary)
    content = public.read_text(encoding="utf-8")
    assert "revenue of 1 dollars" not in content
    assert "raw_responses" not in content
    assert "schema_valid_tasks" in content


def test_quote_presence_does_not_mark_semantic_support(tmp_path: Path) -> None:
    manifest, reference, ledger, run_dir = make_fixture(tmp_path)
    wrong = output_fact("T1", "99", "revenue of 1 dollars")
    write_attempt(ledger, "T1", json.dumps({"facts": [wrong]}))
    mech, review, summary = evaluate(manifest, reference, ledger, run_dir / "eval")
    fact = mech["tasks"][0]["facts"][0]
    assert fact["quote_contiguous_in_input"] is True
    assert fact["value_text_contiguous_in_input"] is False
    assert summary["semantic_support_denominator"] == 0
    assert review["tasks"][0]["model_facts"][0]["codex_review"]["semantic_support"] == "UNRESOLVED"


def test_explicit_codex_review_adds_field_error_without_rewriting_response(tmp_path: Path) -> None:
    manifest, reference, ledger, run_dir = make_fixture(tmp_path)
    wrong = output_fact("T1", "99", "revenue of 1 dollars")
    raw = json.dumps({"facts": [wrong]})
    write_attempt(ledger, "T1", raw)
    decisions = tmp_path / "decisions.json"
    decisions.write_text(json.dumps({"reviewer": "CODEX_REVIEW", "decisions": [{
        "task_id": "T1", "fact_index": 0, "reference_index": 0,
        "semantic_support": "UNSUPPORTED",
        "field_checks": {"entity": "CORRECT", "metric": "CORRECT",
                         "value_text": "WRONG", "unit_text": "CORRECT",
                         "period_text": "CORRECT", "basis_text": "CORRECT"},
        "error_labels": ["WRONG_VALUE", "UNSUPPORTED"],
        "note": "99 does not appear in the frozen source passage.",
    }]}), encoding="utf-8")
    _, _, summary = evaluate(manifest, reference, ledger, run_dir / "eval", decisions)
    assert summary["error_labels"]["WRONG_VALUE"] == 1
    assert summary["error_labels"]["UNSUPPORTED"] == 1
    assert summary["semantic_support_denominator"] == 1
    assert summary["field_status_denominator_per_field"] == 1
    assert summary["field_status_counts_codex_review"]["value_text"] == {
        "CORRECT": 0, "WRONG": 1, "UNRESOLVED": 0}
    assert (run_dir / "raw_responses/T1.txt").read_text(encoding="utf-8") == raw


def test_journaled_crash_remains_in_attempt_denominator(tmp_path: Path) -> None:
    manifest, reference, ledger, run_dir = make_fixture(tmp_path)
    ledger.write_text("", encoding="utf-8")
    (run_dir / "launch_journal.jsonl").write_text('{"task_id": "T1"}\n', encoding="utf-8")
    mech, _, _ = evaluate(manifest, reference, ledger, run_dir / "eval")
    assert mech["counts"]["planned_tasks"] == 6
    assert mech["counts"]["attempted_tasks"] == 1
    assert mech["tasks"][0]["attempt_status"] == "LAUNCHED_NO_FINAL_LEDGER"


def test_real_runner_launch_and_finish_journal_do_not_double_count(tmp_path: Path) -> None:
    manifest, reference, ledger, run_dir = make_fixture(tmp_path)
    ledger.write_text("", encoding="utf-8")
    (run_dir / "launch_journal.jsonl").write_text(
        '{"event":"WORKER_LAUNCH_COMMITTED","task_id":"T1"}\n'
        '{"event":"ATTEMPT_FINISHED","task_id":"T1","status":"NATIVE_OR_NONZERO_EXIT_STOP"}\n',
        encoding="utf-8")
    (run_dir / "phases").mkdir()
    (run_dir / "phases/T1.jsonl").write_text(
        '{"event":"WORKER_PROCESS_STARTED"}\n{"event":"GENERATION_STARTED"}\n',
        encoding="utf-8")
    mech, _, _ = evaluate(manifest, reference, ledger, run_dir / "eval")
    assert mech["counts"]["attempted_tasks"] == 1
    assert mech["counts"]["generation_started_tasks"] == 1
    assert mech["tasks"][0]["response_completed"] is False


def test_reference_hash_mismatch_blocks_audit(tmp_path: Path) -> None:
    manifest, reference, ledger, run_dir = make_fixture(tmp_path)
    ledger.write_text("", encoding="utf-8")
    obj = json.loads(manifest.read_text(encoding="utf-8"))
    obj["tasks"][0]["text_sha256"] = "0" * 64
    manifest.write_text(json.dumps(obj), encoding="utf-8")
    with pytest.raises(ValueError, match="Passage hash mismatch"):
        evaluate(manifest, reference, ledger, run_dir / "eval")
