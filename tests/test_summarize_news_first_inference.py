"""Synthetic checks for postrun status evidence binding."""

from __future__ import annotations

import importlib.util
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "summarize_news_first_inference", REPO / "scripts/summarize_news_first_inference.py")
assert spec is not None and spec.loader is not None
auditor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auditor)


def test_oom_terminal_statuses_match_responses_events_and_failure(tmp_path: Path):
    for ordinal in (1, 2):
        (tmp_path / f"response_{ordinal:02d}.json").write_text("{}", encoding="utf-8")
    (tmp_path / "stderr_03.txt").write_text(
        "torch.AcceleratorError: CUDA error: out of memory", encoding="utf-8")
    statuses = {1: "MECHANICAL_INVALID", 2: "MECHANICAL_INVALID", 3: "OOM_STOP"}
    statuses.update({i: "NOT_ATTEMPTED_AFTER_OOM_STOP" for i in range(4, 11)})
    preflight = {i: {"fits_with_output_budget": True} for i in range(1, 11)}
    finished = {1: ["MECHANICAL_INVALID"], 2: ["MECHANICAL_INVALID"], 3: ["OOM_STOP"]}
    derived = {1: "MECHANICAL_INVALID", 2: "MECHANICAL_INVALID"}
    args = (tmp_path, statuses, preflight, {1, 2, 3}, finished, derived, "OOM_STOP")
    assert auditor.status_consistency_issues(*args) == []
    derived[2] = "SCHEMA_INVALID"
    assert "ARTICLE_STATUS_RESPONSE_OR_EVENT_MISMATCH" in auditor.status_consistency_issues(*args)
    derived[2] = "MECHANICAL_INVALID"
    statuses[5] = "SCHEMA_INVALID"
    assert "ARTICLE_STATUS_PREFLIGHT_OR_ATTEMPT_MISMATCH" in auditor.status_consistency_issues(*args)
    statuses[5] = "NOT_ATTEMPTED_AFTER_OOM_STOP"
    statuses[5] = "NOT_ATTEMPTED_AFTER_TOTAL_TIMEOUT"
    assert "ARTICLE_STATUS_PREFLIGHT_OR_ATTEMPT_MISMATCH" in auditor.status_consistency_issues(*args)
    statuses[5] = "NOT_ATTEMPTED_AFTER_OOM_STOP"
    (tmp_path / "stderr_03.txt").write_text("other failure", encoding="utf-8")
    assert "OOM_FAILURE_EVIDENCE_MISMATCH" in auditor.status_consistency_issues(*args)
