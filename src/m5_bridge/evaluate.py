"""Event-level paired evaluation of explicit scalar signals.

This module never builds return labels or predicts from embeddings. It validates
already prepared event, scalar-signal and target tables, compares arms only on
their common eligible events within each horizon/target/family, and preserves
the excluded rows. The synthetic mode is for arithmetic and join validation.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
import platform
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


EVENT_COLUMNS = {
    "run_id", "event_id", "issuer_id", "security_id", "text_hash",
    "filing_date", "entry_session", "exit_session", "horizon",
    "eligibility", "exclusion_reason", "calendar_id",
}
SIGNAL_COLUMNS = {
    "run_id", "event_id", "horizon", "arm", "status", "signal",
    "signal_available_at", "failure_reason", "signal_origin", "fit_id",
    "not_applicable_reason",
}
TARGET_COLUMNS = {
    "run_id", "event_id", "horizon", "target_type", "status",
    "target", "failure_reason", "entry_session", "exit_session",
}
SCORE_COLUMNS = [
    "run_id", "synthetic", "event_id", "issuer_id", "security_id", "text_hash",
    "filing_date", "entry_session", "exit_session", "horizon", "target_type",
    "arm_family", "arm", "event_eligible", "event_exclusion_reason",
    "target_status", "target_failure_reason", "signal_status", "signal_failure_reason",
    "signal", "target", "individual_valid", "common_included", "reason",
    "signed_markout", "signal_weighted_markout", "direction_hit",
]
PAIR_COLUMNS = [
    "run_id", "synthetic", "event_id", "issuer_id", "filing_date",
    "entry_session", "exit_session", "horizon", "target_type", "arm_family",
    "reference_arm", "arm", "reference_signal", "arm_signal", "target",
    "signal_delta", "sign_changed", "direct_sign_flip", "signed_markout_delta",
    "signal_weighted_markout_delta",
]
ATTRITION_COLUMNS = ["horizon", "target_type", "arm_family", "arm", "reason", "count"]


class EvaluationError(ValueError):
    """Invalid input or result provenance."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def _csv_read(path: Path, required: set[str]) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise EvaluationError(f"{path.name}: missing columns {sorted(missing)}")
        rows = list(reader)
    for line, row in enumerate(rows, 2):
        if None in row or any(value is None for value in row.values()):
            raise EvaluationError(f"{path.name}:{line}: malformed CSV row")
        for key, value in row.items():
            row[key] = value.strip()
    return rows


def _csv_write(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, columns, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _date(value: str, label: str) -> date:
    """Require a date-only value where the contract is a session/date proxy."""
    if not isinstance(value, str) or len(value) != 10:
        raise EvaluationError(f"{label}: expected ISO date only, got {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise EvaluationError(f"{label}: invalid ISO date {value!r}") from exc


def _aware_datetime(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvaluationError(f"{label}: invalid ISO timestamp {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EvaluationError(f"{label}: naive timestamp has no frozen timezone")
    return parsed.astimezone(timezone.utc)


def _market_date(value: str, market_timezone: str, label: str) -> date:
    if len(value) == 10:
        return _date(value, label)
    try:
        zone = ZoneInfo(market_timezone)
    except ZoneInfoNotFoundError as exc:
        raise EvaluationError(f"{label}: unknown market timezone {market_timezone!r}") from exc
    return _aware_datetime(value, label).astimezone(zone).date()


def _not_after(first: str, second: str, label: str) -> bool:
    """Compare like temporal types, normalizing aware timestamps to UTC first."""
    if len(first) == len(second) == 10:
        return _date(first, label) <= _date(second, label)
    if len(first) == 10 or len(second) == 10:
        raise EvaluationError(f"{label}: date/timestamp order is ambiguous")
    return _aware_datetime(first, label) <= _aware_datetime(second, label)


def _same_temporal(first: str, second: str, label: str) -> bool:
    return _not_after(first, second, label) and _not_after(second, first, label)


def _require_sha256(value: str, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise EvaluationError(f"{label}: lowercase SHA-256 required")
    return value


def _float(value: str, label: str) -> float:
    try:
        number = float(value)
    except (ValueError, TypeError) as exc:
        raise EvaluationError(f"{label}: expected finite scalar, got {value!r}") from exc
    if not math.isfinite(number):
        raise EvaluationError(f"{label}: expected finite scalar, got {value!r}")
    return number


def _horizon(value: str, label: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise EvaluationError(f"{label}: invalid horizon {value!r}") from exc
    if result <= 0:
        raise EvaluationError(f"{label}: horizon must be positive")
    return result


def _boolean(value: str, label: str) -> bool:
    if value.lower() in {"true", "1"}:
        return True
    if value.lower() in {"false", "0"}:
        return False
    raise EvaluationError(f"{label}: expected true/false or 1/0")


def _sign(value: float) -> int:
    return (value > 0) - (value < 0)


def _fraction(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    at = 0
    while at < len(order):
        stop = at + 1
        while stop < len(order) and values[order[stop]] == values[order[at]]:
            stop += 1
        average_rank = (at + 1 + stop) / 2
        for index in order[at:stop]:
            ranks[index] = average_rank
        at = stop
    return ranks


def _spearman(signals: list[float], targets: list[float]) -> tuple[float | None, str | None]:
    if len(signals) < 2:
        return None, "fewer_than_two"
    if len(set(signals)) == 1:
        return None, "constant_signal"
    if len(set(targets)) == 1:
        return None, "constant_target"
    x, y = _ranks(signals), _ranks(targets)
    mx, my = statistics.fmean(x), statistics.fmean(y)
    numerator = sum((a - mx) * (b - my) for a, b in zip(x, y))
    denominator = math.sqrt(sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y))
    return numerator / denominator, None


def _config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("schema_version") != 2:
        raise EvaluationError("config: schema_version must be 2 for the repaired contract")
    if type(config.get("synthetic")) is not bool:
        raise EvaluationError("config: synthetic must be true or false")
    run_id = config.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise EvaluationError("config: nonempty run_id required")
    horizons = config.get("horizons")
    if not isinstance(horizons, list) or not horizons or any(type(h) is not int or h <= 0 for h in horizons) or len(set(horizons)) != len(horizons):
        raise EvaluationError("config: unique positive integer horizons required")
    target_types = config.get("target_types")
    if not isinstance(target_types, list) or not target_types or any(t not in {"raw_return", "total_return", "market_relative"} for t in target_types) or len(set(target_types)) != len(target_types):
        raise EvaluationError("config: target_types must be unique raw_return/total_return/market_relative names")
    families = config.get("arm_families")
    if not isinstance(families, dict) or not families:
        raise EvaluationError("config: arm_families object required")
    for name, family in families.items():
        if not name or not isinstance(family, dict):
            raise EvaluationError("config: invalid arm family")
        arms = family.get("arms")
        if not isinstance(arms, list) or not arms or any(not isinstance(a, str) or not a for a in arms) or len(set(arms)) != len(arms) or family.get("reference") not in arms:
            raise EvaluationError(f"config: family {name!r} needs unique arms and reference")
    all_arms = {arm for family in families.values() for arm in family["arms"]}
    arm_origins = config.get("arm_origins")
    if not isinstance(arm_origins, dict) or set(arm_origins) != all_arms or any(
        origin not in {"supervised", "zero_shot", "unsupervised"} for origin in arm_origins.values()
    ):
        raise EvaluationError("config: arm_origins must declare every arm")
    fit_files = config.get("fit_manifest_files")
    if not isinstance(fit_files, dict) or any(not isinstance(k, str) or not k or not isinstance(v, str) or not v
                                               for k, v in fit_files.items()):
        raise EvaluationError("config: fit_manifest_files object required")
    rules = config.get("target_rules")
    if not isinstance(rules, dict) or set(rules) != set(target_types):
        raise EvaluationError("config: explicit target_rules required for each target_type")
    allowed = {"raw_return": {"provided", "price_ratio"},
               "total_return": {"provided", "price_plus_cash"},
               "market_relative": {"stock_minus_beta_benchmark"}}
    for target_type, rule in rules.items():
        if not isinstance(rule, dict) or rule.get("formula") not in allowed[target_type]:
            raise EvaluationError(f"config: unsupported {target_type} target rule")
        if rule["formula"] == "provided" and (not rule.get("constructor_id") or
                                                  not rule.get("source_manifest_file") or
                                                  not rule.get("source_manifest_sha256")):
            raise EvaluationError("config: provided target needs constructor and source manifest")
        if target_type == "market_relative":
            base = rule.get("base_target_type")
            if base not in {"raw_return", "total_return"} or base not in target_types:
                raise EvaluationError("config: market_relative needs a configured base_target_type")
            if not rule.get("benchmark_series_id") or not rule.get("benchmark_return_convention") or not rule.get("benchmark_input_version"):
                raise EvaluationError("config: market_relative needs benchmark identity, convention and version")
            if rule.get("beta_mode") != "fixed":
                raise EvaluationError("config: estimated beta requires a separate bound fit window; fixed beta only")
            if type(rule.get("beta")) not in {int, float} or not math.isfinite(rule["beta"]):
                raise EvaluationError("config: market_relative fixed beta must be finite")
    metadata = config.get("metadata")
    if not isinstance(metadata, dict):
        raise EvaluationError("config: metadata object required")
    if any(metadata.get(field) for field in ("fit_cutoff", "latest_train_label_end")):
        raise EvaluationError("config: global fit metadata is unbound; use fit_manifest_files per supervised fit")
    required_calendar = {"anchor_rule", "calendar_id", "calendar_version", "calendar_market_timezone",
                         "calendar_sessions_file", "calendar_coverage_file"}
    if any(not metadata.get(key) for key in required_calendar):
        raise EvaluationError(f"config: calendar metadata required {sorted(required_calendar)}")
    if metadata["anchor_rule"] != "first_regular_session_strictly_after_filing":
        raise EvaluationError("config: unsupported anchor_rule")
    try:
        ZoneInfo(str(metadata["calendar_market_timezone"]))
    except ZoneInfoNotFoundError as exc:
        raise EvaluationError("config: invalid calendar_market_timezone") from exc
    if not config["synthetic"]:
        if config.get("researcher_accepted") is not True:
            raise EvaluationError("real evaluation requires researcher_accepted=true")
        required = {"protocol_hash", "protocol_version", "protocol_file", "acceptance_receipt_file",
                    "price_definition", "model_revision"}
        if any(not metadata.get(key) for key in required):
            raise EvaluationError(f"real evaluation requires metadata {sorted(required)}")
    return config


def _external_path(config_path: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    return path if path.is_absolute() else config_path.parent / path


def _external_inputs(config: dict[str, Any], config_path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, str]]:
    """Bind calendar coverage, supervised fits and any full protocol receipt."""
    metadata = config["metadata"]
    hashes: dict[str, str] = {}
    calendar_path = _external_path(config_path, str(metadata["calendar_sessions_file"]))
    calendar_hash = _sha256(calendar_path)
    sessions = [_date(row["session"], "calendar session") for row in _csv_read(calendar_path, {"session"})]
    if not sessions or sessions != sorted(set(sessions)):
        raise EvaluationError("calendar sessions must be nonempty, unique and sorted")
    coverage_path = _external_path(config_path, str(metadata["calendar_coverage_file"]))
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    expected_coverage = {
        "calendar_id": metadata["calendar_id"], "calendar_version": metadata["calendar_version"],
        "market_timezone": metadata["calendar_market_timezone"], "sessions_sha256": calendar_hash,
        "completeness_status": "COMPLETE",
    }
    if not isinstance(coverage, dict) or any(coverage.get(key) != value for key, value in expected_coverage.items()):
        raise EvaluationError("calendar coverage evidence does not bind complete sessions")
    coverage_start = _date(coverage.get("coverage_start", ""), "calendar coverage_start")
    coverage_end = _date(coverage.get("coverage_end", ""), "calendar coverage_end")
    if coverage_start > sessions[0] or coverage_end < sessions[-1]:
        raise EvaluationError("calendar coverage range does not enclose saved sessions")
    calendar = {"sessions": sessions, "index": {session: position for position, session in enumerate(sessions)},
                "id": metadata["calendar_id"], "timezone": metadata["calendar_market_timezone"],
                "coverage_start": coverage_start, "coverage_end": coverage_end}
    hashes["calendar_sessions"] = calendar_hash
    hashes["calendar_coverage"] = _sha256(coverage_path)
    fit_infos: dict[str, dict[str, Any]] = {}
    for fit_id, raw_path in config["fit_manifest_files"].items():
        fit_path = _external_path(config_path, raw_path)
        fit = json.loads(fit_path.read_text(encoding="utf-8"))
        if not isinstance(fit, dict) or fit.get("fit_id") != fit_id or fit.get("signal_origin") != "supervised":
            raise EvaluationError(f"fit {fit_id}: identity or origin mismatch")
        if not fit.get("training_input_manifest_file"):
            raise EvaluationError(f"fit {fit_id}: training input manifest file required")
        training_path = _external_path(fit_path, str(fit.get("training_input_manifest_file", "")))
        expected_training_hash = _require_sha256(fit.get("training_input_manifest_sha256"), f"fit {fit_id} training manifest")
        if _sha256(training_path) != expected_training_hash:
            raise EvaluationError(f"fit {fit_id}: training input manifest hash mismatch")
        if "training_rows" in fit:
            raise EvaluationError(f"fit {fit_id}: training_rows must come from hashed training input manifest")
        training_manifest = json.loads(training_path.read_text(encoding="utf-8"))
        rows = training_manifest.get("training_rows") if isinstance(training_manifest, dict) else None
        if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) or not row.get("label_end") for row in rows):
            raise EvaluationError(f"fit {fit_id}: hashed training input manifest needs training_rows with label_end")
        if fit.get("training_row_count") != len(rows):
            raise EvaluationError(f"fit {fit_id}: declared training_row_count differs from hashed input")
        label_ends = [str(row["label_end"]) for row in rows]
        actual_max = label_ends[0]
        for value in label_ends[1:]:
            if not _not_after(value, actual_max, f"fit {fit_id} training label order"):
                actual_max = value
        declared_max = fit.get("actual_max_label_end")
        if not declared_max or not _same_temporal(str(declared_max), actual_max, f"fit {fit_id} max label"):
            raise EvaluationError(f"fit {fit_id}: declared max label_end does not match training_rows")
        cutoff = str(fit.get("fit_cutoff", ""))
        if not cutoff or not _not_after(actual_max, cutoff, f"fit {fit_id} label/cutoff"):
            raise EvaluationError(f"fit {fit_id}: training label ends after fit_cutoff")
        window = fit.get("transform_fit_window")
        if not isinstance(window, dict) or not window.get("start") or not window.get("end") or not _not_after(
            str(window["start"]), str(window["end"]), f"fit {fit_id} transform window"
        ) or not _not_after(str(window["end"]), cutoff, f"fit {fit_id} transform cutoff"):
            raise EvaluationError(f"fit {fit_id}: invalid transform fit window")
        fit_infos[fit_id] = {"fit_cutoff": cutoff, "actual_max_label_end": actual_max,
                             "training_input_manifest_sha256": expected_training_hash,
                             "transform_fit_window": window}
        hashes[f"fit_manifest:{fit_id}"] = _sha256(fit_path)
        hashes[f"training_input_manifest:{fit_id}"] = expected_training_hash
    for target_type, rule in config["target_rules"].items():
        if rule["formula"] == "provided":
            source_path = _external_path(config_path, str(rule["source_manifest_file"]))
            expected = _require_sha256(rule["source_manifest_sha256"], f"provided {target_type} source")
            if _sha256(source_path) != expected:
                raise EvaluationError(f"provided {target_type}: source manifest hash mismatch")
            hashes[f"provided_target_source:{target_type}"] = expected
    if not config["synthetic"]:
        protocol_path = _external_path(config_path, str(metadata["protocol_file"]))
        protocol_hash = _sha256(protocol_path)
        if protocol_hash != metadata["protocol_hash"]:
            raise EvaluationError("protocol_file hash does not match protocol_hash")
        receipt_path = _external_path(config_path, str(metadata["acceptance_receipt_file"]))
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        required_receipt = {
            "status": "ACCEPTED", "researcher_accepted": True,
            "scope": "financial_evaluation", "protocol_hash": protocol_hash,
            "protocol_version": metadata["protocol_version"],
        }
        if not isinstance(receipt, dict) or any(receipt.get(k) != v for k, v in required_receipt.items()) or not receipt.get("accepted_at") or not receipt.get("accepted_by"):
            raise EvaluationError("acceptance receipt must identify accepted full protocol, scope, date and researcher")
        hashes["protocol_file"] = protocol_hash
        hashes["acceptance_receipt"] = _sha256(receipt_path)
    return calendar, fit_infos, hashes


def _check_run(row: dict[str, str], config: dict[str, Any], label: str) -> None:
    if row["run_id"] != config["run_id"]:
        raise EvaluationError(f"{label}: run_id does not match config")


def _load_events(path: Path, config: dict[str, Any], calendar: dict[str, Any]) -> dict[tuple[str, int], dict[str, str]]:
    result: dict[tuple[str, int], dict[str, str]] = {}
    core_by_id: dict[str, tuple[str, ...]] = {}
    for row in _csv_read(path, EVENT_COLUMNS):
        label = f"events event_id={row['event_id']!r} horizon={row['horizon']!r}"
        _check_run(row, config, label)
        if not all(row[field] for field in ("event_id", "issuer_id")):
            raise EvaluationError(f"{label}: empty event/issuer ID")
        horizon = _horizon(row["horizon"], label)
        if horizon not in config["horizons"]:
            raise EvaluationError(f"{label}: horizon outside config")
        filing = _date(row["filing_date"], label)
        eligible = _boolean(row["eligibility"], label)
        if not eligible and not row["exclusion_reason"]:
            raise EvaluationError(f"{label}: ineligible event needs exclusion_reason")
        if eligible:
            if not row["security_id"] or not row["text_hash"]:
                raise EvaluationError(f"{label}: eligible event needs security_id and text_hash")
            if row["calendar_id"] != calendar["id"]:
                raise EvaluationError(f"{label}: event calendar_id differs from bound single calendar")
            entry = _date(row["entry_session"], label + " entry_session")
            exit_session = _date(row["exit_session"], label + " exit_session")
            if not filing < entry < exit_session:
                raise EvaluationError(f"{label}: require filing_date < entry_session < exit_session")
            if filing < calendar["coverage_start"] or exit_session > calendar["coverage_end"]:
                raise EvaluationError(f"{label}: calendar coverage does not include filing-to-exit window")
            sessions = calendar["sessions"]
            first_after = bisect.bisect_right(sessions, filing)
            if first_after == 0 or first_after >= len(sessions) or sessions[first_after - 1] > filing:
                raise EvaluationError(f"{label}: no observed pre-filing calendar coverage")
            if entry != sessions[first_after]:
                raise EvaluationError(f"{label}: entry is not first regular session strictly after filing")
            if entry not in calendar["index"] or exit_session not in calendar["index"]:
                raise EvaluationError(f"{label}: entry/exit absent from frozen calendar")
            if calendar["index"][exit_session] - calendar["index"][entry] != horizon:
                raise EvaluationError(f"{label}: exit is not exactly entry + {horizon} calendar sessions")
        elif row["entry_session"] or row["exit_session"]:
            # Excluded records can retain supported endpoints, but never require
            # an invented calendar endpoint merely to keep an attrition row.
            if row["entry_session"]:
                _date(row["entry_session"], label + " excluded entry_session")
            if row["exit_session"]:
                _date(row["exit_session"], label + " excluded exit_session")
        row["eligibility"] = "true" if eligible else "false"
        key = row["event_id"], horizon
        if key in result:
            raise EvaluationError(f"{label}: duplicate event-horizon key")
        core = tuple(row[field] for field in ("issuer_id", "security_id", "text_hash", "filing_date", "entry_session"))
        if row["event_id"] in core_by_id and core_by_id[row["event_id"]] != core:
            raise EvaluationError(f"{label}: inconsistent event identity across horizons")
        core_by_id[row["event_id"]] = core
        result[key] = row
    if not result:
        raise EvaluationError("events: no rows")
    return result


def _load_signals(path: Path, config: dict[str, Any], events: dict[tuple[str, int], dict[str, str]],
                  calendar: dict[str, Any], fit_infos: dict[str, dict[str, Any]]) -> dict[tuple[str, int, str], dict[str, str]]:
    result: dict[tuple[str, int, str], dict[str, str]] = {}
    known_arms = {arm for family in config["arm_families"].values() for arm in family["arms"]}
    for row in _csv_read(path, SIGNAL_COLUMNS):
        label = f"signals event_id={row['event_id']!r} horizon={row['horizon']!r} arm={row['arm']!r}"
        _check_run(row, config, label)
        horizon = _horizon(row["horizon"], label)
        event = events.get((row["event_id"], horizon))
        if event is None or row["arm"] not in known_arms:
            raise EvaluationError(f"{label}: event or arm outside configured universe")
        key = row["event_id"], horizon, row["arm"]
        if key in result:
            raise EvaluationError(f"{label}: duplicate event-arm-horizon key")
        origin = row["signal_origin"]
        if origin != config["arm_origins"][row["arm"]]:
            raise EvaluationError(f"{label}: signal_origin differs from configured arm origin")
        if any(row.get(field) for field in ("fit_cutoff", "latest_train_label_end", "training_input_manifest")):
            raise EvaluationError(f"{label}: free fit metadata must be bound through fit_manifest_files")
        if origin in {"zero_shot", "unsupervised"}:
            if row["fit_id"] or not row["not_applicable_reason"]:
                raise EvaluationError(f"{label}: non-supervised origin requires null fit_id and not_applicable_reason")
        elif row["not_applicable_reason"]:
            raise EvaluationError(f"{label}: supervised origin cannot claim fit fields are not applicable")
        elif row["fit_id"] and row["fit_id"] not in fit_infos:
            raise EvaluationError(f"{label}: supervised fit_id is not bound")
        if row["status"] == "ok":
            _float(row["signal"], label + " signal")
            available_raw = row["signal_available_at"]
            available = _market_date(available_raw, calendar["timezone"], label + " signal_available_at")
            if available < _date(event["filing_date"], label) or (
                event["entry_session"] and available >= _date(event["entry_session"], label)
            ):
                raise EvaluationError(f"{label}: signal availability leaks into or precedes filing")
            if origin == "supervised":
                fit = fit_infos.get(row["fit_id"])
                if fit is None:
                    raise EvaluationError(f"{label}: supervised signal needs bound fit_id and training manifest")
                cutoff_raw = fit["fit_cutoff"]
                if not _not_after(cutoff_raw, available_raw, label + " fit_cutoff/signal_available_at") or _same_temporal(
                    cutoff_raw, available_raw, label + " fit_cutoff/signal_available_at"
                ):
                    raise EvaluationError(f"{label}: fit_cutoff must precede signal availability")
                if event["entry_session"] and _market_date(cutoff_raw, calendar["timezone"], label) >= _date(
                    event["entry_session"], label
                ):
                    raise EvaluationError(f"{label}: fit_cutoff must precede entry session")
        elif row["status"] == "failed":
            if row["signal"] or not row["failure_reason"]:
                raise EvaluationError(f"{label}: failed signal needs empty value and failure_reason")
        else:
            raise EvaluationError(f"{label}: status must be ok or failed")
        result[key] = row
    return result


def _load_targets(path: Path, config: dict[str, Any], events: dict[tuple[str, int], dict[str, str]]) -> dict[tuple[str, int, str], dict[str, str]]:
    result: dict[tuple[str, int, str], dict[str, str]] = {}
    for row in _csv_read(path, TARGET_COLUMNS):
        label = f"targets event_id={row['event_id']!r} horizon={row['horizon']!r} target_type={row['target_type']!r}"
        _check_run(row, config, label)
        horizon = _horizon(row["horizon"], label)
        event = events.get((row["event_id"], horizon))
        if event is None or row["target_type"] not in config["target_types"]:
            raise EvaluationError(f"{label}: event or target_type outside configured universe")
        if row["entry_session"] != event["entry_session"] or row["exit_session"] != event["exit_session"]:
            raise EvaluationError(f"{label}: target window differs from event window")
        key = row["event_id"], horizon, row["target_type"]
        if key in result:
            raise EvaluationError(f"{label}: duplicate event-target-horizon key")
        result[key] = row
    for (event_id, horizon, target_type), row in result.items():
        label = f"targets event_id={event_id!r} horizon={horizon} target_type={target_type!r}"
        rule = config["target_rules"][target_type]
        formula = rule["formula"]
        if formula == "stock_minus_beta_benchmark":
            if row.get("base_target_type") != rule["base_target_type"] or row.get("stock_target"):
                raise EvaluationError(f"{label}: market relative must reference saved base target, not free stock_target")
            if any(row.get(key) != expected for key, expected in (
                ("benchmark_series_id", rule["benchmark_series_id"]),
                ("benchmark_return_convention", rule["benchmark_return_convention"]),
                ("benchmark_input_version", rule["benchmark_input_version"]),
                ("benchmark_entry_session", row["entry_session"]),
                ("benchmark_exit_session", row["exit_session"]),
            )):
                raise EvaluationError(f"{label}: benchmark identity/convention/window does not match bound rule")
        if row["status"] == "ok":
            target = _float(row["target"], label + " target")
            if formula in {"price_ratio", "price_plus_cash"}:
                entry = _float(row.get("entry_price", ""), label + " entry_price")
                exit_price = _float(row.get("exit_price", ""), label + " exit_price")
                if entry <= 0 or exit_price < 0:
                    raise EvaluationError(f"{label}: entry_price must be positive; exit_price nonnegative")
                cash = (_float(row.get("cash_distribution", ""), label + " cash_distribution")
                        if formula == "price_plus_cash" else 0.0)
                expected = (exit_price + cash) / entry - 1
            elif formula == "stock_minus_beta_benchmark":
                base_type = rule["base_target_type"]
                base = result.get((event_id, horizon, base_type))
                if base is None or base["status"] != "ok":
                    raise EvaluationError(f"{label}: referenced base target is missing")
                benchmark = _float(row.get("benchmark_target", ""), label + " benchmark_target")
                beta = _float(row.get("beta", ""), label + " beta")
                if beta != rule["beta"]:
                    raise EvaluationError(f"{label}: beta differs from fixed rule")
                expected = _float(base["target"], label + " base target") - beta * benchmark
            else:
                expected = target
            if not math.isclose(target, expected, rel_tol=1e-9, abs_tol=1e-12):
                raise EvaluationError(f"{label}: target does not match configured {formula} formula")
        elif row["status"] == "missing":
            if row["target"] or not row["failure_reason"]:
                raise EvaluationError(f"{label}: missing target needs empty value and failure_reason")
        else:
            raise EvaluationError(f"{label}: status must be ok or missing")
    return result


def _score_rows(config: dict[str, Any], events: dict[tuple[str, int], dict[str, str]], signals: dict[tuple[str, int, str], dict[str, str]], targets: dict[tuple[str, int, str], dict[str, str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for horizon in config["horizons"]:
        horizon_events = [event for (event_id, h), event in sorted(events.items()) if h == horizon]
        for target_type in config["target_types"]:
            for family_name, family in config["arm_families"].items():
                for event in horizon_events:
                    event_id = event["event_id"]
                    target_row = targets.get((event_id, horizon, target_type))
                    candidate_rows = []
                    for arm in family["arms"]:
                        signal_row = signals.get((event_id, horizon, arm))
                        event_eligible = event["eligibility"] == "true"
                        target_ok = target_row is not None and target_row["status"] == "ok"
                        signal_ok = signal_row is not None and signal_row["status"] == "ok"
                        individual = event_eligible and target_ok and signal_ok
                        if not event_eligible:
                            reason = "event_ineligible"
                        elif not target_ok:
                            reason = "target_missing"
                        elif signal_row is None:
                            reason = "signal_missing"
                        elif not signal_ok:
                            reason = "signal_failed"
                        else:
                            reason = "included"
                        candidate_rows.append({
                            "run_id": config["run_id"], "synthetic": str(config["synthetic"]).lower(),
                            "event_id": event_id, "issuer_id": event["issuer_id"],
                            "security_id": event["security_id"], "text_hash": event["text_hash"],
                            "filing_date": event["filing_date"], "entry_session": event["entry_session"],
                            "exit_session": event["exit_session"], "horizon": horizon,
                            "target_type": target_type, "arm_family": family_name, "arm": arm,
                            "event_eligible": event["eligibility"],
                            "event_exclusion_reason": event["exclusion_reason"],
                            "target_status": target_row["status"] if target_row else "absent",
                            "target_failure_reason": target_row["failure_reason"] if target_row else "target_row_absent",
                            "signal_status": signal_row["status"] if signal_row else "absent",
                            "signal_failure_reason": signal_row["failure_reason"] if signal_row else "signal_row_absent",
                            "signal": signal_row["signal"] if signal_ok else "",
                            "target": target_row["target"] if target_ok else "",
                            "individual_valid": str(individual).lower(), "common_included": "false",
                            "reason": reason, "signed_markout": "", "signal_weighted_markout": "",
                            "direction_hit": "",
                        })
                    common = all(row["individual_valid"] == "true" for row in candidate_rows)
                    for row in candidate_rows:
                        if common:
                            signal, target = float(row["signal"]), float(row["target"])
                            row["common_included"] = "true"
                            row["signed_markout"] = _sign(signal) * target
                            row["signal_weighted_markout"] = signal * target
                            row["direction_hit"] = "" if signal == 0 or target == 0 else str(_sign(signal) == _sign(target)).lower()
                        elif row["individual_valid"] == "true":
                            row["reason"] = "peer_missing"
                        rows.append(row)
    return rows


def _pair_rows(config: dict[str, Any], scores: list[dict[str, Any]]) -> list[dict[str, Any]]:
    indexed = {(r["event_id"], int(r["horizon"]), r["target_type"], r["arm_family"], r["arm"]): r for r in scores}
    rows: list[dict[str, Any]] = []
    for score in scores:
        if score["common_included"] != "true":
            continue
        family = config["arm_families"][score["arm_family"]]
        reference = family["reference"]
        if score["arm"] == reference:
            continue
        ref = indexed[(score["event_id"], int(score["horizon"]), score["target_type"], score["arm_family"], reference)]
        s_ref, s_arm, target = float(ref["signal"]), float(score["signal"]), float(score["target"])
        rows.append({
            "run_id": config["run_id"], "synthetic": str(config["synthetic"]).lower(),
            "event_id": score["event_id"], "issuer_id": score["issuer_id"],
            "filing_date": score["filing_date"], "entry_session": score["entry_session"],
            "exit_session": score["exit_session"], "horizon": score["horizon"],
            "target_type": score["target_type"], "arm_family": score["arm_family"],
            "reference_arm": reference, "arm": score["arm"], "reference_signal": s_ref,
            "arm_signal": s_arm, "target": target, "signal_delta": s_arm - s_ref,
            "sign_changed": str(_sign(s_arm) != _sign(s_ref)).lower(),
            "direct_sign_flip": str(s_arm * s_ref < 0).lower(),
            "signed_markout_delta": (_sign(s_arm) - _sign(s_ref)) * target,
            "signal_weighted_markout_delta": (s_arm - s_ref) * target,
        })
    return rows


def _summary(config: dict[str, Any], scores: list[dict[str, Any]], pairs: list[dict[str, Any]]) -> dict[str, Any]:
    by_arm: dict[tuple[int, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in scores:
        by_arm[(int(row["horizon"]), row["target_type"], row["arm_family"], row["arm"])].append(row)
    for horizon in config["horizons"]:
        for target_type in config["target_types"]:
            for family_name, family in config["arm_families"].items():
                for arm in family["arms"]:
                    by_arm[(horizon, target_type, family_name, arm)]
    arm_metrics = []
    for key, group in sorted(by_arm.items()):
        included = [row for row in group if row["common_included"] == "true"]
        signals = [float(row["signal"]) for row in included]
        targets = [float(row["target"]) for row in included]
        spearman, reason = _spearman(signals, targets)
        direction_denom = sum(s != 0 and t != 0 for s, t in zip(signals, targets))
        direction_hits = sum(_sign(s) == _sign(t) for s, t in zip(signals, targets) if s != 0 and t != 0)
        arm_metrics.append({
            "horizon": key[0], "target_type": key[1], "arm_family": key[2], "arm": key[3],
            "candidate_count": len(group),
            "eligible_count": sum(row["event_eligible"] == "true" for row in group),
            "target_available_count": sum(row["target_status"] == "ok" for row in group),
            "signal_available_count": sum(row["signal_status"] == "ok" for row in group),
            "individual_valid_count": sum(row["individual_valid"] == "true" for row in group),
            "common_count": len(included),
            "individual_coverage": _fraction(sum(row["individual_valid"] == "true" for row in group), len(group)),
            "common_coverage": _fraction(len(included), len(group)),
            "spearman_pooled_descriptive": spearman, "spearman_reason": reason,
            "direction_hits": direction_hits, "direction_denominator": direction_denom,
            "direction_hit_rate": _fraction(direction_hits, direction_denom),
            "direction_coverage_of_common": _fraction(direction_denom, len(included)),
            "zero_signal_count": sum(s == 0 for s in signals),
            "zero_target_count": sum(t == 0 for t in targets),
            "mean_signed_markout": _mean([_sign(s) * t for s, t in zip(signals, targets)]),
            "mean_signal_weighted_markout": _mean([s * t for s, t in zip(signals, targets)]),
        })
    by_pair: dict[tuple[int, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in pairs:
        by_pair[(int(row["horizon"]), row["target_type"], row["arm_family"], row["reference_arm"], row["arm"])].append(row)
    pair_metrics = []
    for horizon in config["horizons"]:
        for target_type in config["target_types"]:
            for family_name, family in config["arm_families"].items():
                for arm in family["arms"]:
                    if arm == family["reference"]:
                        continue
                    key = (horizon, target_type, family_name, family["reference"], arm)
                    group = by_pair.get(key, [])
                    nonzero_both = sum(float(row["reference_signal"]) != 0 and float(row["arm_signal"]) != 0 for row in group)
                    sign_changes = sum(row["sign_changed"] == "true" for row in group)
                    direct_flips = sum(row["direct_sign_flip"] == "true" for row in group)
                    pair_metrics.append({
                        "horizon": horizon, "target_type": target_type, "arm_family": family_name,
                        "reference_arm": family["reference"], "arm": arm,
                        "common_count": len(group), "sign_change_count": sign_changes,
                        "sign_change_rate": _fraction(sign_changes, len(group)),
                        "direct_positive_negative_flip_count": direct_flips,
                        "both_nonzero_count": nonzero_both,
                        "direct_positive_negative_flip_rate": _fraction(direct_flips, nonzero_both),
                        "mean_signal_delta": _mean([float(row["signal_delta"]) for row in group]),
                        "mean_signed_markout_delta": _mean([float(row["signed_markout_delta"]) for row in group]),
                        "mean_signal_weighted_markout_delta": _mean([float(row["signal_weighted_markout_delta"]) for row in group]),
                    })
    return {
        "run_id": config["run_id"], "synthetic": config["synthetic"],
        "scope": "synthetic_validation_only" if config["synthetic"] else "event_markout_only",
        "calendar_validation": ("first_strictly_after_filing_and_coverage_checked"
                                if config.get("schema_version") == 2 else
                                "checked" if config["metadata"].get("calendar_sessions_file") else "not_provided"),
        "arm_metrics": arm_metrics, "pair_metrics": pair_metrics,
        "definitions": {
            "common_mask": "same event within each horizon,target_type,arm_family and all family arms individually valid",
            "pooled_spearman": "descriptive average-tie ranks over common events; null for fewer than two or constant input",
            "direction": "sign(signal)==sign(target) only when both nonzero; coverage denominator is common events",
            "signed_markout": "sign(signal)*target; not strategy PnL",
            "signal_weighted_markout": "signal*target; not dollar PnL",
            "sign_change": "sign(arm)!=sign(reference), including zero",
            "direct_flip": "positive to negative or negative to positive; denominator has both nonzero",
        },
        "not_computed": ["daily_IC", "portfolio_returns", "annualized_return", "Sharpe", "bootstrap_uncertainty"],
    }


def _attrition(scores: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = Counter((int(row["horizon"]), row["target_type"], row["arm_family"], row["arm"], row["reason"]) for row in scores)
    return [dict(zip(ATTRITION_COLUMNS[:-1], key), count=count) for key, count in sorted(counts.items())]


def _output_nonempty(path: Path) -> bool:
    return path.exists() and (not path.is_dir() or any(path.iterdir()))


def evaluate(events_path: Path, signals_path: Path, targets_path: Path, config_path: Path, output: Path) -> dict[str, Any]:
    if _output_nonempty(output):
        raise EvaluationError(f"output directory already contains files: {output}")
    config = _config(config_path)
    calendar, fit_infos, external_hashes = _external_inputs(config, config_path)
    events = _load_events(events_path, config, calendar)
    signals = _load_signals(signals_path, config, events, calendar, fit_infos)
    targets = _load_targets(targets_path, config, events)
    scores = _score_rows(config, events, signals, targets)
    pairs = _pair_rows(config, scores)
    summary = _summary(config, scores, pairs)
    attrition = _attrition(scores)
    output.mkdir(parents=True, exist_ok=True)
    _csv_write(output / "event_scores.csv", SCORE_COLUMNS, scores)
    _csv_write(output / "paired_differences.csv", PAIR_COLUMNS, pairs)
    _csv_write(output / "attrition.csv", ATTRITION_COLUMNS, attrition)
    _json_write(output / "summary.json", summary)
    _json_write(output / "run_manifest.json", {
        "schema_version": 2, "run_id": config["run_id"], "synthetic": config["synthetic"],
        "config": config,
        "implementation_sha256": _sha256(Path(__file__)),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "inputs_sha256": {name: _sha256(path) for name, path in (
            ("events", events_path), ("signals", signals_path),
            ("targets", targets_path), ("config", config_path))},
        "external_inputs_sha256": external_hashes,
        "outputs_sha256": {name: _sha256(output / name) for name in (
            "event_scores.csv", "paired_differences.csv", "attrition.csv", "summary.json")},
    })
    return summary


def _same_number(actual: str, expected: float, label: str) -> None:
    if not math.isclose(_float(actual, label), expected, rel_tol=1e-12, abs_tol=1e-12):
        raise EvaluationError(f"{label}: saved detail does not match recomputation")


def _verify_input_join(manifest: dict[str, Any], scores: list[dict[str, str]], inputs_dir: Path) -> None:
    """Independently reconstruct the event/target/signal join from bound inputs.

    This intentionally does not call ``_score_rows`` or ``_summary``. It is a
    join oracle, while ``verify_output`` without inputs is only an internal
    arithmetic/summary consistency check.
    """
    paths = {"events": inputs_dir / "events.csv", "signals": inputs_dir / "signals.csv",
             "targets": inputs_dir / "targets.csv", "config": inputs_dir / "config.json"}
    if {name: _sha256(path) for name, path in paths.items()} != manifest["inputs_sha256"]:
        raise EvaluationError("original input SHA-256 mismatch")
    config = _config(paths["config"])
    if config != manifest["config"]:
        raise EvaluationError("resolved config differs from run manifest")
    calendar, fit_infos, external_hashes = _external_inputs(config, paths["config"])
    if external_hashes != manifest["external_inputs_sha256"]:
        raise EvaluationError("external calendar/fit/source SHA-256 mismatch")
    events = _load_events(paths["events"], config, calendar)
    signals = _load_signals(paths["signals"], config, events, calendar, fit_infos)
    targets = _load_targets(paths["targets"], config, events)
    saved = {(row["event_id"], int(row["horizon"]), row["target_type"], row["arm_family"], row["arm"]): row
             for row in scores}
    expected_keys = set()
    for (event_id, horizon), event in events.items():
        for target_type in config["target_types"]:
            target = targets.get((event_id, horizon, target_type))
            for family_name, family in config["arm_families"].items():
                available = {arm: signals.get((event_id, horizon, arm)) for arm in family["arms"]}
                valid = {arm: event["eligibility"] == "true" and target is not None and target["status"] == "ok"
                         and signal is not None and signal["status"] == "ok" for arm, signal in available.items()}
                common = all(valid.values())
                for arm, signal in available.items():
                    key = event_id, horizon, target_type, family_name, arm
                    expected_keys.add(key)
                    row = saved.get(key)
                    if row is None:
                        raise EvaluationError(f"event_scores.csv missing input-derived row {key}")
                    expected_reason = (
                        "event_ineligible" if event["eligibility"] != "true" else
                        "target_missing" if target is None or target["status"] != "ok" else
                        "signal_missing" if signal is None else
                        "signal_failed" if signal["status"] != "ok" else
                        "included" if common else "peer_missing"
                    )
                    checks = {
                        "issuer_id": event["issuer_id"], "security_id": event["security_id"],
                        "text_hash": event["text_hash"], "filing_date": event["filing_date"],
                        "entry_session": event["entry_session"], "exit_session": event["exit_session"],
                        "event_eligible": event["eligibility"], "event_exclusion_reason": event["exclusion_reason"],
                        "target_status": target["status"] if target else "absent",
                        "target_failure_reason": target["failure_reason"] if target else "target_row_absent",
                        "signal_status": signal["status"] if signal else "absent",
                        "signal_failure_reason": signal["failure_reason"] if signal else "signal_row_absent",
                        "signal": signal["signal"] if signal and signal["status"] == "ok" else "",
                        "target": target["target"] if target and target["status"] == "ok" else "",
                        "individual_valid": str(valid[arm]).lower(), "common_included": str(common).lower(),
                        "reason": expected_reason,
                    }
                    if any(row[field] != str(expected) for field, expected in checks.items()):
                        raise EvaluationError(f"event_scores.csv input join mismatch {key}")
    if set(saved) != expected_keys:
        raise EvaluationError("event_scores.csv has rows outside original event/arm/target universe")


def verify_output(run_dir: Path, inputs_dir: Path | None = None) -> dict[str, Any]:
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    config = manifest["config"]
    for name, expected in manifest["outputs_sha256"].items():
        if _sha256(run_dir / name) != expected:
            raise EvaluationError(f"{name}: output SHA-256 mismatch")
    scores = _csv_read(run_dir / "event_scores.csv", set(SCORE_COLUMNS))
    pairs = _csv_read(run_dir / "paired_differences.csv", set(PAIR_COLUMNS))
    attrition = _csv_read(run_dir / "attrition.csv", set(ATTRITION_COLUMNS))
    by_event_group: dict[tuple[str, int, str, str], list[dict[str, str]]] = defaultdict(list)
    seen_score_keys: set[tuple[str, int, str, str, str]] = set()
    for row in scores:
        if row["run_id"] != config["run_id"] or row["synthetic"] != str(config["synthetic"]).lower():
            raise EvaluationError("event_scores.csv run identity mismatch")
        key = (row["event_id"], int(row["horizon"]), row["target_type"], row["arm_family"], row["arm"])
        if key in seen_score_keys:
            raise EvaluationError("event_scores.csv duplicate key")
        seen_score_keys.add(key)
        by_event_group[key[:4]].append(row)
        if row["common_included"] == "true":
            signal, target = _float(row["signal"], "saved signal"), _float(row["target"], "saved target")
            if row["reason"] != "included" or row["individual_valid"] != "true":
                raise EvaluationError("included detail has inconsistent status")
            _same_number(row["signed_markout"], _sign(signal) * target, "saved signed_markout")
            _same_number(row["signal_weighted_markout"], signal * target, "saved signal_weighted_markout")
            expected_hit = "" if signal == 0 or target == 0 else str(_sign(signal) == _sign(target)).lower()
            if row["direction_hit"] != expected_hit:
                raise EvaluationError("saved direction_hit mismatch")
        elif any(row[key] for key in ("signed_markout", "signal_weighted_markout", "direction_hit")):
            raise EvaluationError("excluded detail contains markout")
    for key, group in by_event_group.items():
        family = config["arm_families"].get(key[3])
        if family is None or {row["arm"] for row in group} != set(family["arms"]):
            raise EvaluationError("event_scores.csv incomplete arm family")
        common = all(row["individual_valid"] == "true" for row in group)
        if any((row["common_included"] == "true") != common for row in group):
            raise EvaluationError("event_scores.csv common mask mismatch")
        for row in group:
            if common and row["reason"] != "included":
                raise EvaluationError("event_scores.csv included reason mismatch")
            if not common and row["individual_valid"] == "true" and row["reason"] != "peer_missing":
                raise EvaluationError("event_scores.csv peer attrition mismatch")
    expected_pairs = _pair_rows(config, scores)
    if len(expected_pairs) != len(pairs):
        raise EvaluationError("paired_differences.csv row count mismatch")
    for actual, expected in zip(pairs, expected_pairs):
        for column in PAIR_COLUMNS:
            if column in {"reference_signal", "arm_signal", "target", "signal_delta", "signed_markout_delta", "signal_weighted_markout_delta"}:
                _same_number(actual[column], float(expected[column]), "pair " + column)
            elif actual[column] != str(expected[column]):
                raise EvaluationError(f"paired_differences.csv {column} mismatch")
    recomputed_attrition = _attrition(scores)
    normalized_attrition = [{key: (int(row[key]) if key in {"horizon", "count"} else row[key]) for key in ATTRITION_COLUMNS} for row in attrition]
    if normalized_attrition != recomputed_attrition:
        raise EvaluationError("attrition.csv does not match event detail")
    saved_summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    recomputed_summary = _summary(config, scores, pairs)
    if saved_summary != recomputed_summary:
        raise EvaluationError("summary.json does not match CSV detail")
    if inputs_dir is not None:
        _verify_input_join(manifest, scores, inputs_dir)
    return {"run_id": config["run_id"], "synthetic": config["synthetic"], "verified": True,
            "verification_level": "input_bound_independent_join" if inputs_dir is not None else "internal_consistency",
            "event_score_rows": len(scores), "pair_rows": len(pairs)}


def main_evaluate(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m m5_bridge evaluate")
    for name in ("events", "signals", "targets", "config", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        summary = evaluate(args.events, args.signals, args.targets, args.config, args.output)
    except (EvaluationError, OSError, json.JSONDecodeError, KeyError) as exc:
        print(f"evaluation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"run_id": summary["run_id"], "synthetic": summary["synthetic"],
                      "output": str(args.output)}, ensure_ascii=False))
    return 0


def main_verify_output(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m m5_bridge verify-output")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--inputs", type=Path, help="directory with original config.json and three CSV inputs")
    args = parser.parse_args(argv)
    try:
        result = verify_output(args.run_dir, args.inputs)
    except (EvaluationError, OSError, json.JSONDecodeError, KeyError) as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0
