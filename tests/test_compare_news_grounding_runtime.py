"""Synthetic-only checks for version-aware N1 development comparison."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "compare_news_grounding_runtime", ROOT / "scripts/compare_news_grounding_runtime.py")
assert spec is not None and spec.loader is not None
comparison = importlib.util.module_from_spec(spec)
spec.loader.exec_module(comparison)


def test_ten_status_positions_required():
    rows = [{"ordinal": i, "status": "NOT_ATTEMPTED_AFTER_OOM_STOP"}
            for i in range(1, 11)]
    assert len(comparison.statuses({"article_statuses": rows})) == 10
    with pytest.raises(comparison.GroundingComparisonError,
                       match="TEN_STATUS_COVERAGE_MISMATCH"):
        comparison.statuses({"article_statuses": rows[:-1]})


def test_prompt_token_summary_has_explicit_denominator():
    assert comparison.number_summary([]) == {
        "count": 0, "minimum": None, "median": None,
        "maximum": None, "mean": None}
    assert comparison.number_summary([-5, 0, 2])["median"] == 0


def test_unfinished_run_cannot_be_published(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(comparison, "REPO", tmp_path)
    run = tmp_path / "artifacts" / "new"
    run.mkdir(parents=True)
    (run / "run_events.jsonl").write_text(
        json.dumps({"event": "RUN_STARTED"}) + "\n", encoding="utf-8")
    with pytest.raises(comparison.GroundingComparisonError, match="RUN_NOT_FINISHED"):
        comparison.read_finished(run, tmp_path / "missing_audit.json")
