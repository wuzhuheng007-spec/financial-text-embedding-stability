"""Synthetic evaluator contracts; no historical security or price data."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from m5_bridge.evaluate import EvaluationError, _not_after, evaluate, verify_output


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    columns = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, columns)
        writer.writeheader()
        writer.writerows(rows)


def _fixture(tmp_path: Path):
    sessions = []
    current = date(2018, 12, 31)
    synthetic_holidays = {date(2019, 1, 1), date(2019, 1, 7), date(2019, 1, 21)}
    while len(sessions) < 31:
        if current.weekday() < 5 and current not in synthetic_holidays:
            sessions.append({"session": current.isoformat()})
        current += timedelta(days=1)
    calendar_path = tmp_path / "synthetic_calendar_sessions.csv"
    _write_csv(calendar_path, sessions)
    calendar_hash = hashlib.sha256(calendar_path.read_bytes()).hexdigest()
    (tmp_path / "synthetic_calendar_coverage.json").write_text(json.dumps({
        "calendar_id": "SYN_XNYS", "calendar_version": "synthetic-v2",
        "market_timezone": "America/New_York", "sessions_sha256": calendar_hash,
        "coverage_start": "2018-12-31", "coverage_end": "2019-03-01",
        "completeness_status": "COMPLETE", "source": "explicit synthetic fixture",
    }), encoding="utf-8")
    training_path = tmp_path / "synthetic_training_input_manifest.json"
    training_path.write_text(json.dumps({
        "schema": 1, "source": "synthetic training only",
        "training_rows": [{"row_id": "T1", "label_end": "2018-12-18"},
                          {"row_id": "T2", "label_end": "2018-12-19"}],
    }), encoding="utf-8")
    training_hash = hashlib.sha256(training_path.read_bytes()).hexdigest()
    (tmp_path / "fit_base.json").write_text(json.dumps({
        "fit_id": "FIT_BASE", "signal_origin": "supervised", "fit_cutoff": "2018-12-20",
        "training_input_manifest_file": training_path.name,
        "training_input_manifest_sha256": training_hash,
        "training_row_count": 2,
        "actual_max_label_end": "2018-12-19",
        "transform_fit_window": {"start": "2018-10-01", "end": "2018-12-19"},
    }), encoding="utf-8")
    entry = sessions[1]["session"]  # first synthetic session strictly after Jan 1 filing
    exit_5, exit_21 = sessions[6]["session"], sessions[22]["session"]
    config = {
        "schema_version": 2, "run_id": "synthetic-contract-2", "synthetic": True,
        "horizons": [5, 21], "target_types": ["raw_return", "market_relative"],
        "arm_families": {"minilm": {"reference": "base", "arms": ["base", "shift"]}},
        "arm_origins": {"base": "supervised", "shift": "zero_shot"},
        "fit_manifest_files": {"FIT_BASE": "fit_base.json"},
        "target_rules": {"raw_return": {"formula": "price_ratio"},
                         "market_relative": {"formula": "stock_minus_beta_benchmark",
                                             "base_target_type": "raw_return",
                                             "benchmark_series_id": "SYN_BM",
                                             "benchmark_return_convention": "simple_price_return",
                                             "benchmark_input_version": "synthetic-v1",
                                             "beta_mode": "fixed", "beta": 1}},
        "metadata": {"protocol_hash": "synthetic-only", "calendar_version": "synthetic-v2",
                     "calendar_sessions_file": calendar_path.name,
                     "calendar_coverage_file": "synthetic_calendar_coverage.json",
                     "calendar_id": "SYN_XNYS", "calendar_market_timezone": "America/New_York",
                     "anchor_rule": "first_regular_session_strictly_after_filing",
                     "price_definition": "synthetic simple close ratio", "model_revision": "synthetic-only"},
    }
    events = []
    for event_id in ("E1", "E2", "E3", "E4"):
        for horizon in ([5, 21] if event_id in {"E1", "E2"} else [5]):
            events.append({
                "run_id": config["run_id"], "event_id": event_id, "issuer_id": "issuer-" + event_id,
                "security_id": "security-" + event_id, "text_hash": "synthetic-" + event_id,
                "filing_date": "2019-01-01", "entry_session": entry,
                "exit_session": exit_5 if horizon == 5 else exit_21,
                "horizon": horizon, "eligibility": "true", "exclusion_reason": "", "calendar_id": "SYN_XNYS",
            })
    signal_values = {
        ("E1", 5): (1.0, -1.0), ("E2", 5): (-1.0, None),
        ("E3", 5): (0.0, 1.0), ("E4", 5): (1.0, "failed"),
        ("E1", 21): (1.0, 1.0), ("E2", 21): (-1.0, 1.0),
    }
    signals = []
    for (event_id, horizon), (base, shift) in signal_values.items():
        for arm, value in (("base", base), ("shift", shift)):
            if value is None:
                continue
            signals.append({
                "run_id": config["run_id"], "event_id": event_id, "horizon": horizon,
                "arm": arm, "status": "failed" if value == "failed" else "ok",
                "signal": "" if value == "failed" else value,
                "signal_available_at": "" if value == "failed" else "2019-01-01",
                "failure_reason": "synthetic_encoder_failure" if value == "failed" else "",
                "signal_origin": config["arm_origins"][arm],
                "fit_id": "FIT_BASE" if arm == "base" else "",
                "not_applicable_reason": "" if arm == "base" else "zero_shot_no_supervised_fit",
            })
    raw = {("E1", 5): 0.1, ("E2", 5): -0.2, ("E3", 5): 0.0,
           ("E4", 5): 0.05, ("E1", 21): 0.2, ("E2", 21): -0.1}
    benchmark = {("E1", 5): None, ("E2", 5): 0.1, ("E3", 5): 0.0,
                 ("E4", 5): 0.03, ("E1", 21): 0.05, ("E2", 21): -0.05}
    targets = []
    for (event_id, horizon), stock in raw.items():
        targets.append({
            "run_id": config["run_id"], "event_id": event_id, "horizon": horizon,
            "target_type": "raw_return", "status": "ok", "target": stock,
            "entry_session": entry, "exit_session": exit_5 if horizon == 5 else exit_21,
            "failure_reason": "", "entry_price": 100, "exit_price": 100 * (1 + stock),
            "stock_target": "", "benchmark_target": "", "beta": "",
        })
        bench = benchmark[(event_id, horizon)]
        targets.append({
            "run_id": config["run_id"], "event_id": event_id, "horizon": horizon,
            "target_type": "market_relative", "status": "missing" if bench is None else "ok",
            "target": "" if bench is None else stock - bench,
            "entry_session": entry, "exit_session": exit_5 if horizon == 5 else exit_21,
            "failure_reason": "benchmark_missing" if bench is None else "",
            "entry_price": "", "exit_price": "", "stock_target": "",
            "base_target_type": "raw_return", "benchmark_series_id": "SYN_BM",
            "benchmark_return_convention": "simple_price_return",
            "benchmark_input_version": "synthetic-v1",
            "benchmark_entry_session": entry, "benchmark_exit_session": exit_5 if horizon == 5 else exit_21,
            "benchmark_target": "" if bench is None else bench, "beta": 1,
        })
    paths = {name: tmp_path / (name + (".json" if name == "config" else ".csv"))
             for name in ("events", "signals", "targets", "config")}
    return config, events, signals, targets, paths


def _run_fixture(tmp_path: Path, edits=None):
    config, events, signals, targets, paths = _fixture(tmp_path)
    if edits:
        edits(config, events, signals, targets)
    paths["config"].write_text(json.dumps(config), encoding="utf-8")
    for name, rows in (("events", events), ("signals", signals), ("targets", targets)):
        _write_csv(paths[name], rows)
    output = tmp_path / "synthetic_evaluation"
    return config, paths, output


def _metric(summary, horizon, target_type, arm):
    return next(row for row in summary["arm_metrics"]
                if row["horizon"] == horizon and row["target_type"] == target_type and row["arm"] == arm)


def _pair(summary, horizon, target_type, arm):
    return next(row for row in summary["pair_metrics"]
                if row["horizon"] == horizon and row["target_type"] == target_type and row["arm"] == arm)


def test_common_mask_attrition_and_markout_are_recomputed_from_detail(tmp_path):
    _, paths, output = _run_fixture(tmp_path)
    summary = evaluate(paths["events"], paths["signals"], paths["targets"], paths["config"], output)
    assert summary["synthetic"] is True
    assert summary["scope"] == "synthetic_validation_only"
    assert summary["calendar_validation"] == "first_strictly_after_filing_and_coverage_checked"
    base = _metric(summary, 5, "raw_return", "base")
    shift = _metric(summary, 5, "raw_return", "shift")
    assert base["candidate_count"] == 4
    assert base["individual_valid_count"] == 4
    assert shift["individual_valid_count"] == 2
    assert base["common_count"] == shift["common_count"] == 2
    assert base["mean_signed_markout"] == pytest.approx(0.05)
    assert shift["mean_signed_markout"] == pytest.approx(-0.05)
    assert base["direction_denominator"] == 1  # E3 has zero signal and target.
    assert base["direction_coverage_of_common"] == 0.5
    assert base["spearman_pooled_descriptive"] == 1.0
    assert shift["spearman_pooled_descriptive"] == -1.0
    assert _pair(summary, 5, "raw_return", "shift")["sign_change_count"] == 2
    assert _pair(summary, 5, "raw_return", "shift")["direct_positive_negative_flip_count"] == 1
    assert _pair(summary, 5, "raw_return", "shift")["direct_positive_negative_flip_rate"] == 1.0
    assert _metric(summary, 21, "raw_return", "base")["common_count"] == 2
    assert _metric(summary, 5, "market_relative", "base")["common_count"] == 1
    assert _metric(summary, 5, "market_relative", "base")["spearman_reason"] == "fewer_than_two"
    with (output / "event_scores.csv").open(encoding="utf-8", newline="") as stream:
        scores = list(csv.DictReader(stream))
    by_key = {(r["event_id"], r["horizon"], r["target_type"], r["arm"]): r for r in scores}
    assert by_key["E1", "5", "raw_return", "base"]["common_included"] == "true"
    assert by_key["E1", "5", "market_relative", "base"]["reason"] == "target_missing"
    assert by_key["E2", "5", "raw_return", "base"]["reason"] == "peer_missing"
    assert by_key["E2", "5", "raw_return", "shift"]["reason"] == "signal_missing"
    assert by_key["E4", "5", "raw_return", "shift"]["reason"] == "signal_failed"
    assert verify_output(output)["verified"] is True
    assert "Sharpe" in summary["not_computed"]
    assert "annualized_return" in summary["not_computed"]


def test_duplicate_signal_rejected(tmp_path):
    def edit(config, events, signals, targets):
        signals.append(signals[0].copy())
    _, paths, output = _run_fixture(tmp_path, edit)
    with pytest.raises(EvaluationError, match="duplicate event-arm-horizon"):
        evaluate(paths["events"], paths["signals"], paths["targets"], paths["config"], output)


def test_duplicate_target_rejected(tmp_path):
    def edit(config, events, signals, targets):
        targets.append(targets[0].copy())
    _, paths, output = _run_fixture(tmp_path, edit)
    with pytest.raises(EvaluationError, match="duplicate event-target-horizon"):
        evaluate(paths["events"], paths["signals"], paths["targets"], paths["config"], output)


def test_embedding_vector_cannot_be_used_as_scalar_signal(tmp_path):
    def edit(config, events, signals, targets):
        signals[0]["signal"] = "[0.1, 0.2]"
    _, paths, output = _run_fixture(tmp_path, edit)
    with pytest.raises(EvaluationError, match="expected finite scalar"):
        evaluate(paths["events"], paths["signals"], paths["targets"], paths["config"], output)


@pytest.mark.parametrize("change,needle", [
    ("availability", "availability leaks"),
    ("training_label", "training label ends after fit_cutoff"),
    ("intraday_training_label", "training label ends after fit_cutoff"),
])
def test_known_time_leakage_rejected(tmp_path, change, needle):
    def edit(config, events, signals, targets):
        if change == "availability":
            signals[0]["signal_available_at"] = "2019-01-02"
    _, paths, output = _run_fixture(tmp_path, edit)
    if change in {"training_label", "intraday_training_label"}:
        fit_path = tmp_path / "fit_base.json"
        fit = json.loads(fit_path.read_text(encoding="utf-8"))
        if change == "training_label":
            fit["fit_cutoff"] = "2018-12-18"
        else:
            fit["fit_cutoff"] = "2018-12-20T09:00:00-05:00"
            training_path = tmp_path / "synthetic_training_input_manifest.json"
            training = json.loads(training_path.read_text(encoding="utf-8"))
            training["training_rows"] = [
                {"row_id": "T1", "label_end": "2018-12-20T08:00:00-05:00"},
                {"row_id": "T2", "label_end": "2018-12-20T12:00:00-05:00"},
            ]
            training_path.write_text(json.dumps(training), encoding="utf-8")
            fit["training_input_manifest_sha256"] = hashlib.sha256(training_path.read_bytes()).hexdigest()
            fit["actual_max_label_end"] = "2018-12-20T12:00:00-05:00"
            fit["transform_fit_window"] = {"start": "2018-12-19T10:00:00-05:00",
                                           "end": "2018-12-20T08:00:00-05:00"}
        fit_path.write_text(json.dumps(fit), encoding="utf-8")
    with pytest.raises(EvaluationError, match=needle):
        evaluate(paths["events"], paths["signals"], paths["targets"], paths["config"], output)


def test_real_mode_cannot_rely_on_config_claim_alone(tmp_path):
    def edit(config, events, signals, targets):
        config["synthetic"] = False
        config["researcher_accepted"] = True
    _, paths, output = _run_fixture(tmp_path, edit)
    with pytest.raises(EvaluationError, match="real evaluation requires metadata"):
        evaluate(paths["events"], paths["signals"], paths["targets"], paths["config"], output)
    assert not output.exists()


def test_price_ratio_arithmetic_rejects_mismatch(tmp_path):
    def edit(config, events, signals, targets):
        targets[0]["target"] = 0.2
    _, paths, output = _run_fixture(tmp_path, edit)
    with pytest.raises(EvaluationError, match="does not match configured price_ratio"):
        evaluate(paths["events"], paths["signals"], paths["targets"], paths["config"], output)


def test_calendar_detects_weekend_holiday_and_off_by_one(tmp_path):
    _, paths, output = _run_fixture(tmp_path)
    summary = evaluate(paths["events"], paths["signals"], paths["targets"], paths["config"], output)
    assert summary["calendar_validation"] == "first_strictly_after_filing_and_coverage_checked"
    manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["external_inputs_sha256"]["calendar_sessions"]) == 64

    def bad(config, events, signals, targets):
        events[0]["exit_session"] = "2019-01-09"  # one session early
    bad_dir = tmp_path / "off_by_one"
    bad_dir.mkdir()
    _, bad_paths, bad_output = _run_fixture(bad_dir, bad)
    with pytest.raises(EvaluationError, match="exactly entry \\+ 5 calendar sessions"):
        evaluate(bad_paths["events"], bad_paths["signals"], bad_paths["targets"], bad_paths["config"], bad_output)


def test_synthetic_split_and_cash_return_remain_distinct(tmp_path):
    def edit(config, events, signals, targets):
        config["target_types"].append("total_return")
        config["target_rules"]["total_return"] = {"formula": "price_plus_cash"}
        for row in targets:
            if row["event_id"] == "E1" and row["horizon"] == 5 and row["target_type"] == "raw_return":
                # Two-for-one split: adjusted entry is 50, exit is 47.5 after a
                # 2.5 cash distribution. The unadjusted 100->47.5 ratio is invalid.
                row["entry_price"] = 50
                row["exit_price"] = 47.5
                row["target"] = -0.05
        for event in events:
            event_id, horizon = event["event_id"], event["horizon"]
            raw = next(row for row in targets if row["event_id"] == event_id
                       and row["horizon"] == horizon and row["target_type"] == "raw_return")
            cash = 2.5 if event_id == "E1" and horizon == 5 else 0.0
            entry, exit_price = float(raw["entry_price"]), float(raw["exit_price"])
            targets.append({
                "run_id": config["run_id"], "event_id": event_id, "horizon": horizon,
                "target_type": "total_return", "status": "ok",
                "target": (exit_price + cash) / entry - 1, "failure_reason": "",
                "entry_session": event["entry_session"], "exit_session": event["exit_session"],
                "entry_price": entry, "exit_price": exit_price, "cash_distribution": cash,
            })
    _, paths, output = _run_fixture(tmp_path, edit)
    evaluate(paths["events"], paths["signals"], paths["targets"], paths["config"], output)
    with (output / "event_scores.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    observed = {row["target_type"]: float(row["target"]) for row in rows
                if row["event_id"] == "E1" and row["horizon"] == "5" and row["arm"] == "base"
                and row["common_included"] == "true"}
    assert observed["raw_return"] == pytest.approx(-0.05)
    assert observed["total_return"] == pytest.approx(0.0)
    assert verify_output(output)["verified"] is True


def test_negative_one_hundred_percent_simple_return_is_retained(tmp_path):
    def edit(config, events, signals, targets):
        for row in targets:
            if row["event_id"] == "E1" and row["horizon"] == 5:
                if row["target_type"] == "raw_return":
                    row["target"] = -1
                    row["exit_price"] = 0
                else:
                    row["status"] = "missing"
                    row["target"] = ""
                    row["failure_reason"] = "benchmark_missing"
                    row["benchmark_target"] = ""
    _, paths, output = _run_fixture(tmp_path, edit)
    summary = evaluate(paths["events"], paths["signals"], paths["targets"], paths["config"], output)
    assert _metric(summary, 5, "raw_return", "base")["common_count"] == 2
    with (output / "event_scores.csv").open(encoding="utf-8", newline="") as stream:
        scores = list(csv.DictReader(stream))
    row = next(r for r in scores if r["event_id"] == "E1" and r["horizon"] == "5"
               and r["target_type"] == "raw_return" and r["arm"] == "base")
    assert float(row["target"]) == -1
    assert float(row["signed_markout"]) == -1


def test_constant_signal_returns_null_with_reason(tmp_path):
    def edit(config, events, signals, targets):
        for row in signals:
            if row["arm"] == "base" and row["horizon"] == 21:
                row["signal"] = 1
    _, paths, output = _run_fixture(tmp_path, edit)
    summary = evaluate(paths["events"], paths["signals"], paths["targets"], paths["config"], output)
    metric = _metric(summary, 21, "raw_return", "base")
    assert metric["spearman_pooled_descriptive"] is None
    assert metric["spearman_reason"] == "constant_signal"


def test_output_readback_detects_detail_tampering_even_if_hash_updated(tmp_path):
    _, paths, output = _run_fixture(tmp_path)
    evaluate(paths["events"], paths["signals"], paths["targets"], paths["config"], output)
    score_path = output / "event_scores.csv"
    with score_path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
        columns = list(rows[0])
    next(row for row in rows if row["common_included"] == "true")["signed_markout"] = "999"
    _write_csv(score_path, rows)
    manifest_path = output / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    import hashlib
    manifest["outputs_sha256"]["event_scores.csv"] = hashlib.sha256(score_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(EvaluationError, match="saved signed_markout"):
        verify_output(output)


def test_cli_evaluate_and_verify_output(tmp_path):
    _, paths, output = _run_fixture(tmp_path)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    command = [sys.executable, "-m", "m5_bridge", "evaluate",
               "--events", str(paths["events"]), "--signals", str(paths["signals"]),
               "--targets", str(paths["targets"]), "--config", str(paths["config"]),
               "--output", str(output)]
    first = subprocess.run(command, capture_output=True, text=True, env=env)
    assert first.returncode == 0, first.stderr
    second = subprocess.run([sys.executable, "-m", "m5_bridge", "verify-output",
                             "--run-dir", str(output)], capture_output=True, text=True, env=env)
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["verified"] is True
    third = subprocess.run([sys.executable, "-m", "m5_bridge", "verify-output",
                            "--run-dir", str(output), "--inputs", str(tmp_path)],
                           capture_output=True, text=True, env=env)
    assert third.returncode == 0, third.stderr
    assert json.loads(third.stdout)["verification_level"] == "input_bound_independent_join"


@pytest.mark.parametrize("first,second,expected", [
    ("2020-01-01T23:30:00-08:00", "2020-01-02T00:00:00+00:00", False),
    ("2020-01-02T00:00:00+00:00", "2020-01-01T19:00:00-05:00", True),
    ("2020-03-08T01:30:00-05:00", "2020-03-08T03:00:00-04:00", True),
])
def test_aware_time_order_uses_utc_before_comparison(first, second, expected):
    assert _not_after(first, second, "test") is expected


@pytest.mark.parametrize("first,second", [
    ("2018-12-20", "2018-12-20T10:00:00-05:00"),
    ("2018-12-20T09:00:00", "2018-12-20T10:00:00-05:00"),
    ("2018-12-20T09:00:00", "2018-12-20T10:00:00"),
])
def test_ambiguous_or_naive_time_order_rejected(first, second):
    with pytest.raises(EvaluationError, match="date/timestamp|naive timestamp"):
        _not_after(first, second, "test")


def test_delayed_anchor_rejected_even_when_horizon_is_preserved(tmp_path):
    def edit(config, events, signals, targets):
        with (tmp_path / "synthetic_calendar_sessions.csv").open(encoding="utf-8", newline="") as stream:
            sessions = [row["session"] for row in csv.DictReader(stream)]
        for event in events:
            event["entry_session"] = sessions[sessions.index(event["entry_session"]) + 1]
            event["exit_session"] = sessions[sessions.index(event["exit_session"]) + 1]
        for target in targets:
            target["entry_session"] = sessions[sessions.index(target["entry_session"]) + 1]
            target["exit_session"] = sessions[sessions.index(target["exit_session"]) + 1]
            if target["target_type"] == "market_relative":
                target["benchmark_entry_session"] = target["entry_session"]
                target["benchmark_exit_session"] = target["exit_session"]
    _, paths, output = _run_fixture(tmp_path, edit)
    with pytest.raises(EvaluationError, match="first regular session strictly after filing"):
        evaluate(paths["events"], paths["signals"], paths["targets"], paths["config"], output)


def test_truncated_calendar_cannot_fake_first_session(tmp_path):
    _, paths, output = _run_fixture(tmp_path)
    calendar_path = tmp_path / "synthetic_calendar_sessions.csv"
    with calendar_path.open(encoding="utf-8", newline="") as stream:
        sessions = list(csv.DictReader(stream))
    _write_csv(calendar_path, sessions[1:])
    coverage_path = tmp_path / "synthetic_calendar_coverage.json"
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    coverage["sessions_sha256"] = hashlib.sha256(calendar_path.read_bytes()).hexdigest()
    coverage_path.write_text(json.dumps(coverage), encoding="utf-8")
    with pytest.raises(EvaluationError, match="no observed pre-filing calendar coverage"):
        evaluate(paths["events"], paths["signals"], paths["targets"], paths["config"], output)


def test_eligible_event_cannot_use_unbound_calendar_id(tmp_path):
    def edit(config, events, signals, targets):
        events[0]["calendar_id"] = "ANOTHER_MARKET"
    _, paths, output = _run_fixture(tmp_path, edit)
    with pytest.raises(EvaluationError, match="calendar_id differs"):
        evaluate(paths["events"], paths["signals"], paths["targets"], paths["config"], output)


def test_excluded_calendar_unknown_row_is_retained(tmp_path):
    def edit(config, events, signals, targets):
        event = next(row for row in events if row["event_id"] == "E4")
        event.update({"eligibility": "false", "exclusion_reason": "calendar_unknown",
                      "security_id": "", "text_hash": "", "calendar_id": "",
                      "entry_session": "", "exit_session": ""})
        for row in targets:
            if row["event_id"] == "E4":
                row.update({"entry_session": "", "exit_session": "", "status": "missing",
                            "target": "", "failure_reason": "calendar_unknown"})
                if row["target_type"] == "market_relative":
                    row["benchmark_entry_session"] = ""
                    row["benchmark_exit_session"] = ""
    _, paths, output = _run_fixture(tmp_path, edit)
    evaluate(paths["events"], paths["signals"], paths["targets"], paths["config"], output)
    with (output / "event_scores.csv").open(encoding="utf-8", newline="") as stream:
        e4 = [row for row in csv.DictReader(stream) if row["event_id"] == "E4"]
    assert len(e4) == 4
    assert all(row["reason"] == "event_ineligible" and row["entry_session"] == "" for row in e4)
    assert verify_output(output, tmp_path)["verification_level"] == "input_bound_independent_join"


def test_filing_timestamp_cannot_be_silently_truncated(tmp_path):
    def edit(config, events, signals, targets):
        for event in events:
            event["filing_date"] = "2019-01-01T23:00:00-08:00"
    _, paths, output = _run_fixture(tmp_path, edit)
    with pytest.raises(EvaluationError, match="expected ISO date only"):
        evaluate(paths["events"], paths["signals"], paths["targets"], paths["config"], output)


def test_supervised_fit_requires_training_rows_and_declared_max(tmp_path):
    _, paths, output = _run_fixture(tmp_path)
    fit_path = tmp_path / "fit_base.json"
    fit = json.loads(fit_path.read_text(encoding="utf-8"))
    training_path = tmp_path / "synthetic_training_input_manifest.json"
    training = json.loads(training_path.read_text(encoding="utf-8"))
    training.pop("training_rows")
    training_path.write_text(json.dumps(training), encoding="utf-8")
    fit["training_input_manifest_sha256"] = hashlib.sha256(training_path.read_bytes()).hexdigest()
    fit_path.write_text(json.dumps(fit), encoding="utf-8")
    with pytest.raises(EvaluationError, match="training_rows"):
        evaluate(paths["events"], paths["signals"], paths["targets"], paths["config"], output)
    training["training_rows"] = [{"row_id": "T1", "label_end": "2018-12-18"}]
    training_path.write_text(json.dumps(training), encoding="utf-8")
    fit["training_input_manifest_sha256"] = hashlib.sha256(training_path.read_bytes()).hexdigest()
    fit["training_row_count"] = 1
    fit_path.write_text(json.dumps(fit), encoding="utf-8")
    with pytest.raises(EvaluationError, match="declared max label_end"):
        evaluate(paths["events"], paths["signals"], paths["targets"], paths["config"], output)


def test_supervised_fit_training_input_hash_and_fit_id_bound(tmp_path):
    _, paths, output = _run_fixture(tmp_path)
    training_path = tmp_path / "synthetic_training_input_manifest.json"
    training_path.write_text("changed", encoding="utf-8")
    with pytest.raises(EvaluationError, match="training input manifest hash mismatch"):
        evaluate(paths["events"], paths["signals"], paths["targets"], paths["config"], output)


def test_fit_declaration_cannot_override_hashed_training_rows(tmp_path):
    _, paths, output = _run_fixture(tmp_path)
    fit_path = tmp_path / "fit_base.json"
    fit = json.loads(fit_path.read_text(encoding="utf-8"))
    fit["actual_max_label_end"] = "2018-12-18"
    fit_path.write_text(json.dumps(fit), encoding="utf-8")
    with pytest.raises(EvaluationError, match="declared max label_end"):
        evaluate(paths["events"], paths["signals"], paths["targets"], paths["config"], output)


def test_fit_manifest_cannot_supply_unhashed_training_rows(tmp_path):
    _, paths, output = _run_fixture(tmp_path)
    fit_path = tmp_path / "fit_base.json"
    fit = json.loads(fit_path.read_text(encoding="utf-8"))
    fit["training_rows"] = [{"row_id": "forged", "label_end": "2018-12-19"}]
    fit_path.write_text(json.dumps(fit), encoding="utf-8")
    with pytest.raises(EvaluationError, match="training_rows must come from hashed"):
        evaluate(paths["events"], paths["signals"], paths["targets"], paths["config"], output)


def test_zero_shot_origin_rejects_free_fit_metadata(tmp_path):
    _, paths, output = _run_fixture(tmp_path)
    with paths["signals"].open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    next(row for row in rows if row["arm"] == "shift")["fit_cutoff"] = "2018-12-20"
    _write_csv(paths["signals"], rows)
    with pytest.raises(EvaluationError, match="free fit metadata"):
        evaluate(paths["events"], paths["signals"], paths["targets"], paths["config"], output)


def test_supervised_signal_rejects_unbound_fit_id(tmp_path):
    _, paths, output = _run_fixture(tmp_path)
    with paths["signals"].open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    next(row for row in rows if row["arm"] == "base")["fit_id"] = "NOT_BOUND"
    _write_csv(paths["signals"], rows)
    with pytest.raises(EvaluationError, match="fit_id is not bound"):
        evaluate(paths["events"], paths["signals"], paths["targets"], paths["config"], output)


def test_unsupervised_origin_valid_with_explicit_not_applicable_reason(tmp_path):
    def edit(config, events, signals, targets):
        config["arm_origins"]["shift"] = "unsupervised"
        for row in signals:
            if row["arm"] == "shift":
                row["signal_origin"] = "unsupervised"
                row["not_applicable_reason"] = "no_supervised_labels_for_similarity_score"
    _, paths, output = _run_fixture(tmp_path, edit)
    summary = evaluate(paths["events"], paths["signals"], paths["targets"], paths["config"], output)
    assert summary["synthetic"] is True


def test_valid_aware_supervised_fit_and_signal_availability(tmp_path):
    _, paths, output = _run_fixture(tmp_path)
    fit_path = tmp_path / "fit_base.json"
    fit = json.loads(fit_path.read_text(encoding="utf-8"))
    fit["fit_cutoff"] = "2018-12-20T10:00:00-05:00"
    training_path = tmp_path / "synthetic_training_input_manifest.json"
    training = json.loads(training_path.read_text(encoding="utf-8"))
    training["training_rows"] = [{"row_id": "T1", "label_end": "2018-12-18T12:00:00-05:00"},
                                 {"row_id": "T2", "label_end": "2018-12-19T12:00:00-05:00"}]
    training_path.write_text(json.dumps(training), encoding="utf-8")
    fit["training_input_manifest_sha256"] = hashlib.sha256(training_path.read_bytes()).hexdigest()
    fit["actual_max_label_end"] = "2018-12-19T17:00:00+00:00"
    fit["transform_fit_window"] = {"start": "2018-10-01T10:00:00-04:00",
                                   "end": "2018-12-19T12:00:00-05:00"}
    fit_path.write_text(json.dumps(fit), encoding="utf-8")
    with paths["signals"].open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        if row["arm"] == "base" and row["status"] == "ok":
            row["signal_available_at"] = "2019-01-01T16:00:00-05:00"
    _write_csv(paths["signals"], rows)
    summary = evaluate(paths["events"], paths["signals"], paths["targets"], paths["config"], output)
    assert summary["synthetic"] is True


def test_market_relative_rejects_free_stock_value_and_target_mismatch(tmp_path):
    def edit(config, events, signals, targets):
        row = next(row for row in targets if row["target_type"] == "market_relative" and row["status"] == "ok")
        row["stock_target"] = 9
        row["target"] = 9 - float(row["benchmark_target"])
    _, paths, output = _run_fixture(tmp_path, edit)
    with pytest.raises(EvaluationError, match="saved base target"):
        evaluate(paths["events"], paths["signals"], paths["targets"], paths["config"], output)


def test_market_relative_can_reference_distinct_total_return_estimand(tmp_path):
    def edit(config, events, signals, targets):
        config["target_types"].append("total_return")
        config["target_rules"]["total_return"] = {"formula": "price_plus_cash"}
        config["target_rules"]["market_relative"]["base_target_type"] = "total_return"
        for event in events:
            raw = next(row for row in targets if row["event_id"] == event["event_id"]
                       and row["horizon"] == event["horizon"] and row["target_type"] == "raw_return")
            targets.append({"run_id": config["run_id"], "event_id": event["event_id"],
                            "horizon": event["horizon"], "target_type": "total_return",
                            "status": "ok", "target": float(raw["target"]) + 0.01,
                            "failure_reason": "", "entry_session": event["entry_session"],
                            "exit_session": event["exit_session"], "entry_price": 100,
                            "exit_price": raw["exit_price"], "cash_distribution": 1})
        for row in targets:
            if row["target_type"] == "market_relative":
                row["base_target_type"] = "total_return"
                if row["status"] == "ok":
                    row["target"] = float(row["target"]) + 0.01
    _, paths, output = _run_fixture(tmp_path, edit)
    summary = evaluate(paths["events"], paths["signals"], paths["targets"], paths["config"], output)
    assert any(row["target_type"] == "market_relative" and row["common_count"] > 0
               for row in summary["arm_metrics"])


def test_market_relative_window_and_base_value_bound(tmp_path):
    def edit(config, events, signals, targets):
        row = next(row for row in targets if row["target_type"] == "market_relative" and row["status"] == "ok")
        row["benchmark_exit_session"] = "2019-01-30"
    _, paths, output = _run_fixture(tmp_path, edit)
    with pytest.raises(EvaluationError, match="benchmark identity/convention/window"):
        evaluate(paths["events"], paths["signals"], paths["targets"], paths["config"], output)


def test_market_relative_cannot_relabel_base_value(tmp_path):
    def edit(config, events, signals, targets):
        row = next(row for row in targets if row["target_type"] == "market_relative" and row["status"] == "ok")
        row["target"] = 9 - float(row["benchmark_target"])
    _, paths, output = _run_fixture(tmp_path, edit)
    with pytest.raises(EvaluationError, match="does not match configured stock_minus_beta_benchmark"):
        evaluate(paths["events"], paths["signals"], paths["targets"], paths["config"], output)


def test_provided_target_needs_versioned_constructor_source(tmp_path):
    source_path = tmp_path / "provided_target_source.json"
    source_path.write_text(json.dumps({"source": "synthetic external constructor"}), encoding="utf-8")
    source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()

    def edit(config, events, signals, targets):
        config["target_rules"]["raw_return"] = {"formula": "provided", "constructor_id": "synthetic_ratio_v1",
                                                "source_manifest_file": source_path.name,
                                                "source_manifest_sha256": source_sha}
    _, paths, output = _run_fixture(tmp_path, edit)
    evaluate(paths["events"], paths["signals"], paths["targets"], paths["config"], output)
    assert verify_output(output, tmp_path)["verified"] is True
    source_path.write_text("changed", encoding="utf-8")
    with pytest.raises(EvaluationError, match="source manifest hash mismatch"):
        evaluate(paths["events"], paths["signals"], paths["targets"], paths["config"], tmp_path / "new_run")


def test_input_bound_readback_catches_issuer_tamper_after_hash_update(tmp_path):
    _, paths, output = _run_fixture(tmp_path)
    evaluate(paths["events"], paths["signals"], paths["targets"], paths["config"], output)
    score_path = output / "event_scores.csv"
    with score_path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    excluded = next(row for row in rows if row["common_included"] == "false" and row["event_id"] == "E4")
    excluded["issuer_id"] = "tampered"
    _write_csv(score_path, rows)
    manifest_path = output / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["outputs_sha256"]["event_scores.csv"] = hashlib.sha256(score_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert verify_output(output)["verification_level"] == "internal_consistency"
    with pytest.raises(EvaluationError, match="input join mismatch"):
        verify_output(output, tmp_path)


def test_input_bound_readback_rejects_changed_original_input(tmp_path):
    _, paths, output = _run_fixture(tmp_path)
    evaluate(paths["events"], paths["signals"], paths["targets"], paths["config"], output)
    paths["events"].write_bytes(paths["events"].read_bytes() + b"\n")
    assert verify_output(output)["verified"] is True
    with pytest.raises(EvaluationError, match="original input SHA-256 mismatch"):
        verify_output(output, tmp_path)
