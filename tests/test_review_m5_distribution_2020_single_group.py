"""Synthetic-only checks for targeted saved-index classification."""

from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile

import pytest

from scripts import review_m5_distribution_2020_single_group as review


def fixture_sources(root: Path, *, daily_rows: int) -> tuple[Path, Path, Path, Path]:
    event_path = root / "event_synthetic.sqlite3"
    with closing(sqlite3.connect(event_path)) as db:
        db.execute("CREATE TABLE event_row (row_no INTEGER, permno TEXT, exdate TEXT, "
                   "seq TEXT, row_sha TEXT, event_sha TEXT, header_sha TEXT, "
                   "valid_id INTEGER, valid_date INTEGER, in_window INTEGER)")
        db.executemany("INSERT INTO event_row VALUES (?,?,?,?,?,?,?,?,?,?)", [
            (2, "11", "2020-01-02", "0", "a", "a", "a", 1, 1, 1),
            (3, "11", "2020-01-02", "1", "b", "b", "a", 1, 1, 1),
            (4, "22", "2020-01-03", "0", "c", "c", "a", 1, 1, 1),
        ])
        db.commit()
    comparison = root / "comparison_synthetic.jsonl"
    comparison.write_text(json.dumps({
        "segment": "2020",
        "permno": "11", "session": "2020-01-02",
        "comparison_state": review.TARGET_STATE,
        "distribution_row_count": 2,
        "distribution_distinct_sequence_count": 2,
        "candidate_key_uncertain": False,
    }) + "\n", encoding="utf-8")
    daily_path = root / "daily_synthetic.sqlite3"
    with closing(sqlite3.connect(daily_path)) as db:
        db.execute("CREATE TABLE record_key (permno TEXT, session TEXT, "
                   "first_row INTEGER, first_sha TEXT, has_duplicate INTEGER, "
                   "has_conflict INTEGER)")
        db.execute("INSERT INTO record_key VALUES (?,?,?,?,?,?)",
                   ("22", "2020-01-02", 2, "x", 0, 0))
        db.execute("INSERT INTO record_key VALUES (?,?,?,?,?,?)",
                   ("11", "2020-01-04", 3, "y", 0, 0))
        if daily_rows:
            db.execute("INSERT INTO record_key VALUES (?,?,?,?,?,?)",
                       ("11", "2020-01-02", 4, "z",
                        int(daily_rows > 1), 0))
        db.commit()
    exceptions = root / "daily_exceptions_synthetic.jsonl"
    lines = [{"permno": "11", "dlycaldt": "2020-01-02",
              "codes": ["PERMNO_DATE_DUPLICATE"]}
             for _ in range(max(0, daily_rows - 1))]
    exceptions.write_text("".join(json.dumps(x) + "\n" for x in lines),
                          encoding="utf-8")
    return event_path, comparison, daily_path, exceptions


@pytest.mark.parametrize("daily_rows,classification", [
    (0, "NO_SAVED_DAILY_ROW_FOR_TARGET_KEY"),
    (1, "ONE_SAVED_DAILY_ROW_NO_DUPLICATE"),
    (3, "MULTIPLE_SAVED_DAILY_ROWS_REVIEW_REQUIRED"),
])
def test_target_daily_multiplicity_and_date_sets(daily_rows: int, classification: str):
    with tempfile.TemporaryDirectory(prefix="m5-single-synthetic-") as tmp:
        event, comparison, daily, exceptions = fixture_sources(
            Path(tmp), daily_rows=daily_rows)
        safe, private = review.diagnose_target(
            event, comparison, daily, exceptions,
            expected_dist_dates=2, expected_daily_dates=2)
        assert safe["scope"] == "ENGINEERING_ONLY"
        assert safe["target_group_count"] == 1
        assert safe["distribution_event_row_count"] == 2
        assert safe["distribution_distinct_sequence_count"] == 2
        assert safe["daily_target_key_row_count"] == daily_rows
        assert safe["classification"] == classification
        assert safe["date_intersection_count"] == 1
        assert safe["distribution_only_date_count"] == 1
        assert safe["daily_only_date_count"] == 1
        assert safe["target_date_in_daily_saved_set"] is True
        assert safe["target_security_seen_on_other_daily_date"] is True
        assert safe["target_date_in_distribution_only_set"] is False
        assert private["daily_exception_extra_row_count"] == max(0, daily_rows - 1)


def test_unavailable_full_index_yields_null_not_zero():
    with tempfile.TemporaryDirectory(prefix="m5-single-synthetic-") as tmp:
        event, comparison, _, _ = fixture_sources(Path(tmp), daily_rows=0)
        safe, _ = review.diagnose_target(
            event, comparison, None, None,
            expected_dist_dates=2, expected_daily_dates=2)
        assert safe["classification"] == "NOT_VERIFIED"
        assert safe["daily_target_key_row_count"] is None
        assert safe["date_intersection_count"] is None


@pytest.mark.parametrize("index_status,expected", [
    ("NOT_VERIFIED_INDEX_MISSING", "NOT_VERIFIED"),
    ("SOURCE_MISMATCH_INDEX_HASH", "SOURCE_MISMATCH"),
])
def test_derive_source_unavailable_never_fills_zero(index_status: str, expected: str):
    with tempfile.TemporaryDirectory(prefix="m5-single-synthetic-") as tmp:
        event, comparison, _, _ = fixture_sources(Path(tmp), daily_rows=0)
        sources = {
            "merged_event_db": event,
            "merged_comparison_private": comparison,
            "daily_index": None, "daily_exceptions": None,
            "daily_index_status": index_status,
            "merged_annual": {"segments": {"2020": {"actual_disexdt_count": 2}}},
            "daily_summary": {"distinct_date_count": 2},
            "file_sha256": {}, "query_gaps": {},
        }
        safe, _ = review.derive(sources)
        assert safe["classification"] == expected
        assert safe["daily_target_key_row_count"] is None
        assert safe["date_intersection_count"] is None


def test_saved_derivation_detects_private_or_safe_tamper():
    with tempfile.TemporaryDirectory(prefix="m5-single-synthetic-") as tmp:
        output = Path(tmp)
        safe, private = {"scope": "SYNTHETIC_TEST_ONLY"}, {"target": "synthetic"}
        (output / review.SAFE).write_bytes(review.json_bytes(safe))
        (output / review.PRIVATE).write_bytes(review.json_bytes(private))
        review.verify_saved_derivation(output, safe, private)
        (output / review.PRIVATE).write_bytes(b"{}\n")
        with pytest.raises(review.IntakeError, match="SAVED_DERIVATION_MISMATCH"):
            review.verify_saved_derivation(output, safe, private)
        (output / review.PRIVATE).write_bytes(review.json_bytes(private))
        (output / review.SAFE).write_bytes(b"{}\n")
        with pytest.raises(review.IntakeError, match="SAVED_DERIVATION_MISMATCH"):
            review.verify_saved_derivation(output, safe, private)


def test_duplicate_target_or_changed_date_set_rejected():
    with tempfile.TemporaryDirectory(prefix="m5-single-synthetic-") as tmp:
        event, comparison, daily, exceptions = fixture_sources(
            Path(tmp), daily_rows=0)
        comparison.write_bytes(comparison.read_bytes() * 2)
        with pytest.raises(review.IntakeError, match="RESULT_MISMATCH"):
            review.diagnose_target(
                event, comparison, daily, exceptions,
                expected_dist_dates=2, expected_daily_dates=2)
        comparison.write_bytes(comparison.read_bytes()[:len(comparison.read_bytes()) // 2])
        with pytest.raises(review.IntakeError, match="SAVED_DATE_SET_COUNT_MISMATCH"):
            review.diagnose_target(
                event, comparison, daily, exceptions,
                expected_dist_dates=2, expected_daily_dates=3)


def test_daily_index_exception_disagreement_rejected():
    with tempfile.TemporaryDirectory(prefix="m5-single-synthetic-") as tmp:
        event, comparison, daily, exceptions = fixture_sources(
            Path(tmp), daily_rows=1)
        exceptions.write_text(json.dumps({
            "permno": "11", "dlycaldt": "2020-01-02",
            "codes": ["PERMNO_DATE_DUPLICATE"]}) + "\n", encoding="utf-8")
        with pytest.raises(review.IntakeError, match="DAILY_INDEX_EXCEPTION_MISMATCH"):
            review.diagnose_target(
                event, comparison, daily, exceptions,
                expected_dist_dates=2, expected_daily_dates=2)
