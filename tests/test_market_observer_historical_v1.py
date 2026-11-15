"""Focused synthetic checks for the historical price development contract."""

from datetime import date, timedelta, datetime, timezone
import json
from pathlib import Path

import numpy as np
import pytest

from scripts.market_observer_historical_v1.price_baseline import (
    DailyBar, Example, fit_ridge, load_pit_events, load_raw_bars, make_examples,
    metric_set, predict, source_config, split_name,
)


def test_features_never_read_next_day_close():
    bars = [DailyBar(date(2025, 4, 1) + timedelta(days=i),
                     100 * (1.001 ** i)) for i in range(35)]
    original = make_examples("US.TQQQ", bars)
    revised = list(bars)
    revised[-1] = DailyBar(revised[-1].day, revised[-1].close * 1.2)
    changed = make_examples("US.TQQQ", revised)
    assert original[-1].features == changed[-1].features
    assert original[-1].label_bps != changed[-1].label_bps
    assert original[-1].label_day == bars[-1].day


def test_split_is_assigned_by_label_date():
    assert split_name(date(2025, 12, 31)) == "train"
    assert split_name(date(2026, 1, 1)) == "validation"
    assert split_name(date(2026, 3, 31)) == "validation"
    assert split_name(date(2026, 4, 1)) == "test"
    with pytest.raises(ValueError):
        split_name(date(2026, 8, 1))


def test_training_transform_is_fitted_on_training_rows_only():
    train_x = np.asarray([[float(i), float(i % 3)] for i in range(40)])
    train_y = np.asarray([float(i % 5 - 2) for i in range(40)])
    coefficients, mean, std = fit_ridge(train_x, train_y)
    outlier = np.asarray([[1_000_000.0, -1_000_000.0]])
    assert np.allclose(mean, train_x.mean(axis=0))
    assert np.allclose(std, train_x.std(axis=0))
    assert np.isfinite(predict(outlier, coefficients, mean, std)).all()


def test_zero_return_comparator_has_no_direction_calls():
    result = metric_set(np.asarray([1.0, -1.0]), np.zeros(2))
    assert result["n"] == 2
    assert result["direction_n"] == 0
    assert result["direction_hit_rate"] is None


def test_raw_page_must_be_complete_and_unique(tmp_path: Path):
    path = tmp_path / "US_TQQQ_page1.raw.json"
    base = {"ret_code": 0, "data": {"kline_list": []},
            "pagination": {"has_more": True}}
    path.write_text(json.dumps(base), encoding="utf-8")
    with pytest.raises(ValueError, match="pagination incomplete"):
        load_raw_bars(tmp_path, "US.TQQQ")
    base["pagination"]["has_more"] = False
    first = {"date": 20250401, "time_key": 1743480000000, "close": 10}
    last = {"date": 20260731, "time_key": 1785470400000, "close": 12}
    base["data"]["kline_list"] = [first, first, last]
    path.write_text(json.dumps(base), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate daily date"):
        load_raw_bars(tmp_path, "US.TQQQ")


def test_source_request_must_have_frozen_adjustment(tmp_path: Path):
    config = {"start": "2025-04-01", "end": "2026-07-31", "ktype": 2,
              "autype": 0, "extended_time": 0,
              "symbols": ["US.TQQQ", "US.NVDA", "US.MU"]}
    (tmp_path / "run_config.json").write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match="source request"):
        source_config(tmp_path)


def test_event_count_requires_coverage_and_respects_availability(tmp_path: Path):
    examples = [Example("US.TQQQ", date(2025, 4, 8), date(2025, 4, 9),
                        (0.0, 0.0, 0.0, 0.0), 0.0, 0.0),
                Example("US.TQQQ", date(2025, 4, 10), date(2025, 4, 11),
                        (0.0, 0.0, 0.0, 0.0), 0.0, 0.0)]
    manifest = {"schema": "historical_event_availability_v1",
                "coverage_by_symbol": {"US.TQQQ": {"complete": False,
                    "start": "2025-04-01T00:00:00+00:00",
                    "end": "2025-04-30T00:00:00+00:00"}},
                "events": [{"event_id": "one", "symbols": ["US.TQQQ"],
                    "availability_basis": "SOURCE_PUBLISHED",
                    "source_published_at": "2025-04-09T18:00:00+00:00",
                    "available_at": "2025-04-09T18:00:00+00:00"}]}
    path = tmp_path / "events.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="not marked complete"):
        load_pit_events(path, symbol="US.TQQQ", examples=examples)
    manifest["coverage_by_symbol"]["US.TQQQ"]["complete"] = True
    path.write_text(json.dumps(manifest), encoding="utf-8")
    result = load_pit_events(path, symbol="US.TQQQ", examples=examples)
    assert result[date(2025, 4, 8)] == 0
    assert result[date(2025, 4, 10)] == 1
