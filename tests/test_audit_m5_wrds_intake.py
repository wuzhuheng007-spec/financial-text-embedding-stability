"""SYNTHETIC_TEST_ONLY tests for the local M5 WRDS source binder."""

import csv
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import audit_m5_wrds_intake as intake  # noqa: E402


def write_csv(path, header, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        writer.writerows(rows)


def synthetic_sql():
    columns = ",\n    ".join(
        "crsp_q_stock.stksecurityinfohist." + field for field in intake.EXPECTED_COLUMNS)
    return (
        "SELECT\n    " + columns + "\nFROM crsp_q_stock.stksecurityinfohist\n"
        "WHERE secinfostartdt BETWEEN '1925-12-31'::date AND '2020-01-01'::date\n"
        'AND ("crsp_q_stock"."stksecurityinfohist"."secinfoenddt" >= \'2020-01-01\')\n'
    )


def synthetic_row(permno, start, end, cusip):
    row = {field: "" for field in intake.EXPECTED_COLUMNS}
    row.update(permno=permno, secinfostartdt=start, secinfoenddt=end,
               cusip=cusip, cusip9=cusip + "0", permco=permno)
    return row


def history_entry(source, record, row):
    return {"source": source, "source_record": record,
            "start": row["secinfostartdt"], "end": row["secinfoenddt"],
            "historical_cusip": row["cusip"],
            "normalized_row_sha256": intake.row_digest(row)}


@pytest.fixture
def sample(tmp_path):
    """All identifiers and files here are synthetic software fixtures."""
    names = {name: tmp_path / ("SYNTHETIC_TEST_ONLY_" + name) for name in
             ("snapshot", "links", "changes", "sql", "receipt")}
    tool_dir = tmp_path / "SYNTHETIC_TEST_ONLY_v2_tool"
    tool_dir.mkdir()
    tool_hashes = {}
    for name in intake.TOOL_HASHES:
        (tool_dir / name).write_text("SYNTHETIC_TEST_ONLY " + name, encoding="utf-8")
        tool_hashes[name] = intake.sha256_file(tool_dir / name)
    first = synthetic_row("101", "2019-01-01", "2020-01-01", "AAAA0001")
    second = synthetic_row("102", "2019-01-01", "2020-01-01", "AAAA0001")
    changed = synthetic_row("101", "2020-01-02", "2025-12-31", "BBBB0002")
    later_only = synthetic_row("999", "2020-01-02", "2025-12-31", "CCCC0003")
    snapshot_rows = [first, second]
    changes_rows = [changed, later_only]
    write_csv(names["snapshot"], intake.EXPECTED_COLUMNS,
              [[row[field] for field in intake.EXPECTED_COLUMNS] for row in snapshot_rows])
    write_csv(names["changes"], intake.EXPECTED_COLUMNS,
              [[row[field] for field in intake.EXPECTED_COLUMNS] for row in changes_rows])
    link_header = ("cik", "cusip", "cusip_full", "cikdate1", "cikdate2", "validated")
    write_csv(names["links"], link_header, [
        ("1", "AAAA0001", "AAAA00010", "2019-12-31", "2021-01-01", "3"),
        ("2", "AAAA0001", "AAAA00010", "2019-12-31", "2021-01-01", "0"),
        ("3", "BBBB0002", "BBBB00020", "2020-01-02", "2021-01-01", "1"),
        ("3", "BBBB0002", "BBBB00020", "2020-01-02", "2021-01-01", "1"),
        ("4", "CCCC0003", "CCCC00030", "2020-01-02", "2021-01-01", "0"),
    ])
    names["sql"].write_text(synthetic_sql(), encoding="utf-8")
    receipt = {
        "schema_version": 2, "status": "THREE_FILE_HISTORY_AUDIT_COMPLETE_REVIEW_PENDING",
        "formal_cohort_selected": False,
        "security_source": {"source_path": str(names["snapshot"].resolve()),
                            "source_sha256": intake.sha256_file(names["snapshot"]),
                            "checker_sha256": tool_hashes["check_m5_security_asof_20200101.py"],
                            "row_count": 2, "unique_permno_count": 2,
                            "column_count": 36, "column_names": list(intake.EXPECTED_COLUMNS)},
        "link_source_path": str(names["links"].resolve()),
        "link_source_sha256": intake.sha256_file(names["links"]),
        "changes_source_path": str(names["changes"].resolve()),
        "changes_source_sha256": intake.sha256_file(names["changes"]),
        "audit_script_sha256": tool_hashes["check_m5_cik_candidates.py"],
        "history_audit_script_sha256": tool_hashes["check_m5_three_file_history.py"],
        "validated_dictionary_sha256": tool_hashes["validated_dictionary.json"],
        "summary": {"security_records": 2, "link_records": 5,
                    "change_source_records": 2, "changes_for_starting_securities": 1,
                    "changes_for_other_securities_kept_in_raw_source": 1,
                    "starting_securities_with_historical_cusip_changes": 1,
                    "accepted_cik_links": 0},
        "securities": [
            {"permno": "101", "history": [history_entry("STARTING_SNAPSHOT", 1, first),
                                          history_entry("LATER_CHANGES", 1, changed)],
             "historical_cusip_changed": True, "history_interval_issues": [],
             "history_ends_before_2025_12_31": False},
            {"permno": "102", "history": [history_entry("STARTING_SNAPSHOT", 2, second)],
             "historical_cusip_changed": False, "history_interval_issues": [],
             "history_ends_before_2025_12_31": True},
        ],
    }
    names["receipt"].write_text(json.dumps(receipt), encoding="utf-8")
    args = SimpleNamespace(**names, tool_dir=tool_dir,
                           output_dir=tmp_path / "SYNTHETIC_TEST_ONLY_output")
    return args, tool_hashes, receipt


def run_synthetic(args, hashes):
    with patch.object(intake, "check_ignored_output", lambda _path: None):
        return intake.run(args, expected_sql_hash=intake.sha256_file(args.sql),
                          expected_rows=2, tool_hashes=hashes)


def test_each_historical_interval_matches_exact_cusip_and_keeps_all_candidates(sample):
    args, hashes, _ = sample
    summary = run_synthetic(args, hashes)
    index = json.loads((args.output_dir / "m5_wrds_history_candidates_private.json").read_text())
    assert summary["local_snapshot_rows"] == 2
    assert summary["history_interval_count"] == 3
    assert summary["later_only_interval_count"] == 1
    assert summary["intervals_with_multiple_cik_candidates"] == 2
    assert summary["interval_candidate_rows"] == 6  # Two plus two plus two duplicate rows.
    assert len(index["starting_securities"]) == 2
    first = index["starting_securities"][0]["history"]
    assert first[0]["historical_cusip"] == "AAAA0001"
    assert first[1]["historical_cusip"] == "BBBB0002"
    assert [candidate["cik"] for candidate in first[1]["candidates"]] == ["0000000003"] * 2
    assert summary["interval_candidate_validation_code_counts"]["0"] == 2
    assert any("LOW_QUALITY_LINK_ROW_REVIEW" in issue["codes"] for issue in index["exceptions"])
    assert any("DUPLICATE_EXACT_LINK_ROW" in issue["codes"] for issue in index["exceptions"])
    assert all(not candidate["accepted"] for security in index["starting_securities"]
               for interval in security["history"] for candidate in interval["candidates"])
    assert index["later_only_security_intervals"][0]["not_in_starting_snapshot"]
    assert not index["later_only_security_intervals"][0]["admitted"]


@pytest.mark.parametrize("change,code", [
    ("sql_filter", "SQL_FILTER_MISMATCH"),
    ("header", "SNAPSHOT_HEADER_SQL_MISMATCH"),
    ("row_count", "SNAPSHOT_PAGE_ROW_COUNT_MISMATCH"),
    ("receipt_hash", "V2_RECEIPT_INPUT_HASH_MISMATCH"),
    ("receipt_path", "V2_RECEIPT_INPUT_PATH_MISMATCH"),
    ("tool_hash", "V2_TOOL_HASH_MISMATCH"),
])
def test_mismatch_refuses_complete_output(sample, change, code):
    args, hashes, receipt = sample
    if change == "sql_filter":
        args.sql.write_text(synthetic_sql().replace("2020-01-01')", "2019-01-01')"), encoding="utf-8")
    elif change == "header":
        original = args.snapshot.read_text(encoding="utf-8")
        args.snapshot.write_text(original.replace("permno,secinfostartdt", "secinfostartdt,permno", 1), encoding="utf-8")
    elif change == "row_count":
        lines = args.snapshot.read_text(encoding="utf-8").splitlines()
        args.snapshot.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    elif change == "receipt_hash":
        receipt["link_source_sha256"] = "0" * 64
        args.receipt.write_text(json.dumps(receipt), encoding="utf-8")
    elif change == "receipt_path":
        receipt["changes_source_path"] = str(args.changes.with_name("SYNTHETIC_TEST_ONLY_other.csv"))
        args.receipt.write_text(json.dumps(receipt), encoding="utf-8")
    else:
        (args.tool_dir / "check_m5_three_file_history.py").write_text("SYNTHETIC_TEST_ONLY changed", encoding="utf-8")
    with pytest.raises(intake.AuditError, match=code):
        run_synthetic(args, hashes)
    assert not args.output_dir.exists()


def test_sql_hash_is_pinned_before_semantics(sample):
    args, _, _ = sample
    with pytest.raises(intake.AuditError, match="SQL_HASH_MISMATCH"):
        intake.parse_query(args.sql.read_bytes())


def test_reordered_change_columns_are_semantically_identical(sample):
    args, hashes, receipt = sample
    with args.changes.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    reordered = tuple(reversed(intake.EXPECTED_COLUMNS))
    write_csv(args.changes, reordered, [[row[key] for key in reordered] for row in rows])
    receipt["changes_source_sha256"] = intake.sha256_file(args.changes)
    args.receipt.write_text(json.dumps(receipt), encoding="utf-8")
    summary = run_synthetic(args, hashes)
    assert summary["change_rows"] == 2


def test_sql_changed_during_audit_is_rejected(sample):
    args, hashes, _ = sample
    original = intake.build_index

    def change_sql(*params):
        result = original(*params)
        args.sql.write_text("SYNTHETIC_TEST_ONLY changed during audit", encoding="utf-8")
        return result

    with patch.object(intake, "build_index", change_sql), pytest.raises(
            intake.AuditError, match="SQL_CHANGED_DURING_AUDIT"):
        run_synthetic(args, hashes)
    assert not args.output_dir.exists()


def test_rejection_writes_only_private_fixed_code(sample):
    args, _, _ = sample
    with patch.object(intake, "check_ignored_output", lambda _path: None):
        intake.write_rejection(args.output_dir, "SYNTHETIC_TEST_ONLY_BINDING_MISMATCH", args)
    names = [file.name for file in args.output_dir.iterdir()]
    assert names == ["m5_wrds_intake_exceptions_private.json"]
    assert json.loads((args.output_dir / names[0]).read_text())["status"] == "REJECTED_LOCAL_BINDING"


def test_existing_output_is_not_replaced(sample):
    args, hashes, _ = sample
    run_synthetic(args, hashes)
    before = (args.output_dir / "m5_wrds_intake_aggregate_summary.json").read_bytes()
    with pytest.raises(intake.AuditError, match="OUTPUT_ALREADY_EXISTS"):
        run_synthetic(args, hashes)
    assert (args.output_dir / "m5_wrds_intake_aggregate_summary.json").read_bytes() == before
