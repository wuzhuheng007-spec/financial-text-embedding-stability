"""Focused synthetic checks for the private-to-public paired comparison."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "compare_news_schema_repair_runs", REPO / "scripts/compare_news_schema_repair_runs.py")
assert spec is not None and spec.loader is not None
compare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare)


def test_zero_conditional_denominators_remain_null():
    counts = {"model_calls_attempted": 2, "model_calls_completed": 1,
              "json_parse_valid": 0, "schema_valid": 0,
              "binding_valid": 0, "mechanical_valid": 0,
              "evidence_spans_checked": 0, "evidence_spans_exact": 0}
    rates = compare.metric_rates(counts)
    assert rates["schema_valid"]["conditional_denominator"] == 0
    assert rates["schema_valid"]["conditional_rate"] is None
    assert rates["schema_valid"]["attempted_rate"] == 0
    assert rates["schema_valid"]["fixed_target_denominator"] == 10
    assert rates["evidence_spans_exact"]["span_rate"] is None


def test_schema_valid_distributions_and_demo_copy_are_separate(tmp_path: Path):
    demo = {"input_binding": {"body_sha256": "synthetic"},
            "article_disposition": "COMPANY_EVENTS_FOUND",
            "records": [{"target_company_text": "Invented Co", "event_type": "MERGER",
                         "business_impact_direction": "UNKNOWN"}]}
    first = {"raw_text": json.dumps(demo)}
    (tmp_path / "response_01.json").write_text(json.dumps(first), encoding="utf-8")
    (tmp_path / "parsed_01.json").write_text(json.dumps(
        {"output": demo, "validation": {"schema_valid": True}}), encoding="utf-8")
    second_output = {"input_binding": demo["input_binding"], "records": []}
    (tmp_path / "response_02.json").write_text(json.dumps(
        {"raw_text": json.dumps(second_output)}), encoding="utf-8")
    (tmp_path / "parsed_02.json").write_text(json.dumps(
        {"output": second_output, "validation": {"schema_valid": False}}), encoding="utf-8")
    distribution, copying, private = compare.schema_valid_distributions(
        tmp_path, {1: {"status": "MECHANICAL_VALID"},
                   2: {"status": "SCHEMA_INVALID"}}, demo)
    assert distribution["schema_valid_response_denominator"] == 1
    assert distribution["records_per_response_counts"] == {"1": 1}
    assert distribution["nonempty_all_unknown_direction_response_count"] == 1
    assert copying["response_denominator"] == 2
    assert copying["exact_demo_object_count"] == 1
    assert copying["demo_input_binding_reused_count"] == 2
    assert len(private) == 2


def test_unfinished_run_is_rejected_before_result_publication(tmp_path: Path,
                                                               monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(compare, "REPO", tmp_path)
    run = tmp_path / "artifacts" / "new_run"
    run.mkdir(parents=True)
    (run / "run_events.jsonl").write_text(
        json.dumps({"event": "RUN_STARTED"}) + "\n", encoding="utf-8")
    with pytest.raises(compare.ComparisonError, match="RUN_NOT_FINISHED"):
        compare.finished_run(run, tmp_path / "missing_audit.json")


def test_new_response_reuse_and_run_window_are_checked(tmp_path: Path):
    old_run, new_run = tmp_path / "old", tmp_path / "new"
    old_run.mkdir()
    new_run.mkdir()
    old_local = {"run_finished_at_utc": "2026-11-14T06:00:00+00:00"}
    new_local = {"run_started_at_utc": "2026-11-14T07:00:00+00:00",
                 "run_finished_at_utc": "2026-11-14T08:00:00+00:00"}
    response = {"raw_text": "{}", "finished_at_utc": "2026-11-14T07:30:00+00:00"}
    for path in (old_run / "response_01.json", new_run / "response_01.json"):
        path.write_text(json.dumps(response), encoding="utf-8")
    with pytest.raises(compare.ComparisonError, match="OLD_RESPONSE_FILE_REUSED"):
        compare.check_new_response_provenance(old_run, new_run, old_local, new_local)
    response["finished_at_utc"] = "2026-11-14T06:30:00+00:00"
    (new_run / "response_01.json").write_text(json.dumps(response), encoding="utf-8")
    with pytest.raises(compare.ComparisonError, match="NEW_RESPONSE_OUTSIDE_RUN_WINDOW"):
        compare.check_new_response_provenance(old_run, new_run, old_local, new_local)


def test_response_set_digest_changes_after_private_response_mutation(tmp_path: Path):
    path = tmp_path / "response_01.json"
    events = [{"event": "WORKER_LAUNCH_COMMITTED", "ordinal": 1}]
    path.write_text('{"raw_text":"first"}', encoding="utf-8")
    before = compare.response_set_sha256(tmp_path, events)
    path.write_text('{"raw_text":"second"}', encoding="utf-8")
    after = compare.response_set_sha256(tmp_path, events)
    assert before != after
