"""Synthetic-only tests for the independent calendar builder."""

from __future__ import annotations

import hashlib
import importlib.util
import importlib.metadata
import json
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_nyse_calendar_round9.py"
SPEC = importlib.util.spec_from_file_location("build_nyse_calendar_round9", SCRIPT)
assert SPEC and SPEC.loader
calendar = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(calendar)


def synthetic_rules(tmp_path: Path) -> dict:
    source_path = tmp_path / "synthetic_official_page.txt"
    source_path.write_text("SYNTHETIC_TEST_ONLY: calendar source fixture\n", encoding="utf-8")
    digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
    source = {"id": "SYNTHETIC_TEST_ONLY", "url": "https://example.test/calendar",
              "announcement_date": "2019-01-01", "retrieved_at_utc": "2026-11-01T00:00:00Z",
              "sha256": digest, "evidence_kind": "SYNTHETIC_TEST_ONLY",
              "local_evidence_relative_path": source_path.name}
    return {"schema": "nyse_round9_official_explicit_dates_v1", "mic": "XNYS",
            "market_scope": "NYSE_EQUITIES_CORE", "timezone": "America/New_York",
            "start_date": "2019-11-01", "end_date": "2026-02-28",
            "normal_open_local": "09:30", "normal_close_local": "16:00",
            "early_close_local": "13:00", "hours_source_id": source["id"],
            "year_source_ids": {str(year): source["id"] for year in range(2019, 2027)},
            "sources": [source],
            "closures": [{"date": "2025-01-09", "reason": "synthetic temporary closure",
                          "kind": "TEMPORARY_CLOSURE", "source_id": source["id"]}],
            "early_closes": [{"date": "2025-11-28", "reason": "synthetic early close",
                              "source_id": source["id"]}]}


def test_weekend_temporary_closure_early_close_and_dst_conversion(tmp_path: Path):
    sessions, closures, summary = calendar.build(synthetic_rules(tmp_path), tmp_path)
    by_date = {row["session_date"]: row for row in sessions}
    assert "2025-01-04" not in by_date  # Saturday
    assert "2025-01-09" not in by_date
    assert any(row["date"] == "2025-01-09" and row["kind"] == "TEMPORARY_CLOSURE"
               for row in closures)
    assert by_date["2025-11-28"]["session_type"] == "EARLY_CLOSE"
    assert by_date["2025-11-28"]["close_et"].startswith("2025-11-28T13:00:00-05:00")
    assert by_date["2025-11-28"]["close_utc"].startswith("2025-11-28T18:00:00+00:00")
    assert by_date["2025-03-07"]["open_utc"].startswith("2025-03-07T14:30:00+00:00")
    assert by_date["2025-03-10"]["open_utc"].startswith("2025-03-10T13:30:00+00:00")
    assert summary["prior_sessions_available_at_first_2020_decision"] >= 21
    assert summary["future_sessions_available_after_last_2025_filing"] >= 21
    assert summary["finance_observations_admitted"] is None


def test_curated_rules_keep_official_exception_and_historical_changes():
    rules_path = Path(__file__).resolve().parents[1] / "configs/nyse_calendar_rules_round9.json"
    rules = json.loads(rules_path.read_text(encoding="utf-8"))
    closures = {row["date"]: row for row in rules["closures"]}
    early = {row["date"]: row for row in rules["early_closes"]}
    assert rules["mic"] == "XNYS"
    assert rules["start_date"] == "2019-11-01"
    assert rules["end_date"] == "2026-02-28"
    assert "2021-06-18" not in closures  # Juneteenth was not a 2021 NYSE closure.
    assert closures["2022-06-20"]["source_id"] == "ice_2021_2022_2024_announcement"
    assert "2021-12-31" not in closures  # Saturday New Year was not observed that Friday.
    assert closures["2025-01-09"] == {
        "date": "2025-01-09", "kind": "TEMPORARY_CLOSURE",
        "reason": "National Day of Mourning for former President Jimmy Carter",
        "source_id": "ice_2024_carter_closure"}
    assert early["2025-07-03"]["source_id"] == "ice_2024_2025_2027_announcement"
    assert len(rules["closures"]) == 63 and len(rules["early_closes"]) == 14


def test_source_change_is_rejected(tmp_path: Path):
    rules = synthetic_rules(tmp_path)
    (tmp_path / "synthetic_official_page.txt").write_text("changed fixture\n", encoding="utf-8")
    with pytest.raises(calendar.CalendarError, match="SHA-256 mismatch"):
        calendar.build(rules, tmp_path)


def test_duplicate_and_conflicting_calendar_exceptions_rejected(tmp_path: Path):
    rules = synthetic_rules(tmp_path)
    rules["early_closes"].append({"date": "2025-01-09", "reason": "impossible",
                                  "source_id": "SYNTHETIC_TEST_ONLY"})
    with pytest.raises(calendar.CalendarError, match="closed or unbound"):
        calendar.build(rules, tmp_path)
    rules = synthetic_rules(tmp_path)
    rules["closures"].append(rules["closures"][0].copy())
    with pytest.raises(calendar.CalendarError, match="Duplicate"):
        calendar.build(rules, tmp_path)
    rules = synthetic_rules(tmp_path)
    rules["closures"][0]["kind"] = "UNKNOWN"
    with pytest.raises(calendar.CalendarError, match="Unknown closure rule kind"):
        calendar.build(rules, tmp_path)


def test_missing_year_or_invalid_buffer_rejected(tmp_path: Path):
    rules = synthetic_rules(tmp_path)
    del rules["year_source_ids"]["2026"]
    with pytest.raises(calendar.CalendarError, match="Every 2019-2026 year"):
        calendar.build(rules, tmp_path)
    rules = synthetic_rules(tmp_path)
    rules["start_date"] = "2019-12-20"
    with pytest.raises(calendar.CalendarError, match="Boundary buffer"):
        calendar.build(rules, tmp_path)


def test_unsafe_source_path_rejected(tmp_path: Path):
    rules = synthetic_rules(tmp_path)
    rules["sources"][0]["local_evidence_relative_path"] = "../outside.txt"
    with pytest.raises(calendar.CalendarError, match="Unsafe source path"):
        calendar.build(rules, tmp_path)


def test_saved_calendar_row_and_source_manifest_tamper_rejected(tmp_path: Path):
    rules = synthetic_rules(tmp_path)
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps(rules) + "\n", encoding="utf-8")
    sessions, closures, summary = calendar.build(rules, tmp_path)
    output = tmp_path / "calendar"
    output.mkdir()
    calendar.write_csv(output / "sessions.csv", sessions)
    calendar.write_csv(output / "closures.csv", closures)
    manifest = {**summary, "rules_sha256": calendar.sha256(rules_path),
                "builder_sha256": calendar.sha256(SCRIPT),
                "sessions_csv_sha256": calendar.sha256(output / "sessions.csv"),
                "closures_csv_sha256": calendar.sha256(output / "closures.csv"),
                "tzdata_version": importlib.metadata.version("tzdata"),
                "python_version": sys.version.split()[0],
                "source_evidence": [{key: rules["sources"][0].get(key) for key in
                                     ("id", "url", "announcement_date", "announcement_date_reason",
                                      "retrieved_at_utc", "sha256", "evidence_kind")}],
                "return_labels_computed": False}
    (output / "manifest.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    calendar.verify_calendar_dir(rules_path, output, tmp_path)
    with (output / "sessions.csv").open("a", encoding="utf-8") as handle:
        handle.write("tampered\n")
    with pytest.raises(calendar.CalendarError, match="SHA-256 mismatch"):
        calendar.verify_calendar_dir(rules_path, output, tmp_path)
    calendar.write_csv(output / "sessions.csv", sessions)
    manifest["source_evidence"][0]["url"] = "https://example.test/changed"
    (output / "manifest.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    with pytest.raises(calendar.CalendarError, match="source manifest changed"):
        calendar.verify_calendar_dir(rules_path, output, tmp_path)
