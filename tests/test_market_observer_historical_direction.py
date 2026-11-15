"""Synthetic checks for the frozen t-2 historical direction protocol."""

from datetime import date, timedelta

import numpy as np
import pytest

from scripts.market_observer_historical_v1 import direction_baseline as module
from scripts.market_observer_historical_v1.direction_baseline import (
    Ohlcv, choose_regularization, classification_metrics, fit_logistic,
    fit_and_select, make_direction_examples, predict_selected_once_per_split,
    predict_up, split_name,
)


def sample_bars(n: int = 40) -> list[Ohlcv]:
    return [Ohlcv(date(2025, 4, 1) + timedelta(days=i),
                  100 + i / 10, 100 + i / 10 + (0.01 if i % 2 else -0.01),
                  1_000_000 + i * 10) for i in range(n)]


def test_label_and_prior_session_cannot_change_features():
    bars = sample_bars()
    original = make_direction_examples("US.TQQQ", bars)
    changed = list(bars)
    changed[-1] = Ohlcv(changed[-1].day, 90, 50, 50_000_000)
    changed[-2] = Ohlcv(changed[-2].day, 80, 120, 40_000_000)
    rerun = make_direction_examples("US.TQQQ", changed)
    assert original[-1].features == rerun[-1].features
    assert original[-1].label_up != rerun[-1].label_up
    assert original[-1].last_feature_day == bars[-3].day
    assert original[-1].skipped_prior_day == bars[-2].day


def test_split_uses_label_session_date():
    assert split_name(date(2026, 3, 31)) == "train"
    assert split_name(date(2026, 4, 1)) == "validation"
    assert split_name(date(2026, 5, 31)) == "validation"
    assert split_name(date(2026, 6, 1)) == "test"


def test_classification_metrics_balanced_accuracy_and_one_class():
    actual = np.asarray([0, 0, 0, 1])
    predicted = np.asarray([0, 1, 0, 1])
    m = classification_metrics(actual, predicted)
    assert m["accuracy"] == 0.75
    assert m["balanced_accuracy"] == pytest.approx((2 / 3 + 1) / 2)
    one = classification_metrics(np.asarray([1, 1]), np.asarray([1, 0]))
    assert one["balanced_accuracy"] is None
    assert one["balanced_accuracy_status"] == "UNDEFINED_ONE_CLASS"


def test_validation_tie_prefers_stronger_regularization():
    scores = {x: {"balanced_accuracy": 0.5} for x in (0.1, 1.0, 10.0)}
    assert choose_regularization(scores) == 10.0
    scores[1.0]["balanced_accuracy"] = 0.7
    assert choose_regularization(scores) == 1.0


def test_logistic_fit_scales_training_data_only_and_predicts_binary():
    train_x = np.asarray([[float(i % 7), float(i % 5)] for i in range(100)])
    train_y = np.asarray([i % 2 for i in range(100)])
    beta, mean, std = fit_logistic(train_x, train_y, l2=1.0)
    assert np.allclose(mean, train_x.mean(axis=0))
    assert np.allclose(std, train_x.std(axis=0))
    preds, probabilities = predict_up(np.asarray([[10_000.0, -10_000.0]]),
                                      beta, mean, std)
    assert int(preds[0]) in {0, 1}
    assert 0 <= float(probabilities[0]) <= 1


def test_exact_intraday_tie_is_down():
    bars = sample_bars()
    last = bars[-1]
    bars[-1] = Ohlcv(last.day, 101.0, 101.0, last.volume)
    example = make_direction_examples("US.TQQQ", bars)[-1]
    assert example.intraday_zero is True
    assert example.label_up == 0


def test_grid_never_sees_test_features_and_selected_model_sees_them_once(monkeypatch):
    train_x = np.asarray([[float(i % 7), float(i % 5)] for i in range(100)])
    train_y = np.asarray([i % 2 for i in range(100)])
    validation_x = np.asarray([[float(i % 7), float(i % 5)] for i in range(10)])
    validation_y = np.asarray([i % 2 for i in range(10)])
    test_x = np.full((7, 2), 99_999.0)
    seen: list[tuple[int, bool]] = []
    original = module.predict_up

    def spy(x, *fitted):
        seen.append((len(x), bool(np.any(x == 99_999.0))))
        return original(x, *fitted)

    monkeypatch.setattr(module, "predict_up", spy)
    selected, fitted, _ = fit_and_select(train_x, train_y,
                                         validation_x, validation_y)
    assert selected in (0.1, 1.0, 10.0)
    assert seen == [(10, False), (10, False), (10, False)]
    all_x = np.concatenate((train_x, validation_x, test_x))
    groups = {"train": np.arange(100), "validation": np.arange(100, 110),
              "test": np.arange(110, 117)}
    predicted, probability = predict_selected_once_per_split(all_x, groups, fitted)
    assert len(predicted) == len(probability) == 117
    assert seen == [(10, False)] * 3 + [(100, False), (10, False), (7, True)]
