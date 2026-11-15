"""Failure-sensitive checks for the reproduction gate comparison primitive."""

from scripts.market_observer_official_repro_v1.reproduce import first_difference


def test_nested_date_or_class_change_is_not_masked_by_float_tolerance():
    old = {"rows": [{"day": "2026-04-01", "class": 1, "score": 0.6}]}
    new = {"rows": [{"day": "2026-04-02", "class": 1, "score": 0.6}]}
    issue = first_difference(old, new, atol=1e-10)
    assert issue == {"path": "rows[0].day", "reason": "VALUE",
                     "old": "2026-04-01", "new": "2026-04-02"}


def test_absolute_tolerance_has_no_relative_escape():
    old = {"metric": 1_000_000.0}
    assert first_difference(old, {"metric": 1_000_000.0 + 1e-6}, atol=1e-10)
    assert first_difference({"metric": 1.0}, {"metric": 1.0 + 5e-11}, atol=1e-10) is None


def test_missing_grid_candidate_fails_before_metric_comparison():
    old = {"candidates": {"0.1": {"balanced_accuracy": 0.5}, "1.0": {"balanced_accuracy": 0.5}}}
    new = {"candidates": {"0.1": {"balanced_accuracy": 0.5}}}
    issue = first_difference(old, new, atol=1e-12)
    assert issue["reason"] == "KEY_SET"
