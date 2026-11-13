"""SYNTHETIC_TEST_ONLY checks for the local issuer-history binder."""

import csv
import hashlib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import audit_m5_issuer_history as audit  # noqa: E402
import audit_m5_wrds_intake as prior  # noqa: E402


def write_csv(path, columns, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(columns)
        writer.writerows(rows)


def sql(first, last):
    fields = ",\n".join("crsp_q_stock.stkissuerinfohist." + name
                         for name in audit.EXPECTED_COLUMNS)
    return ("SELECT\n" + fields + "\nFROM crsp_q_stock.stkissuerinfohist\n"
            f"WHERE issinfostartdt BETWEEN '{first}'::date AND '{last}'::date\n")


def issuer(permco, start, end, *, role="EARLY", number=1, name="SYNTHETIC_TEST_ONLY"):
    fields = {name: "" for name in audit.EXPECTED_COLUMNS}
    fields.update(permco=permco, issinfostartdt=start, issinfoenddt=end,
                  issuernm=name)
    return {"source_role": role, "source_record": number, "source_sha256": "a" * 64,
            "normalized_row_sha256": prior.row_digest(fields), "permco": permco,
            "start": start, "end": end, "raw_fields": fields}


def security(permno, permco, start, end, *, role="STARTING_SNAPSHOT", number=1):
    return {"permno": permno, "permco": permco, "source_role": role,
            "source_record": number, "source_sha256": "b" * 64,
            "normalized_row_sha256": "c" * 64, "start": start, "end": end,
            "historical_cusip": "ABCD1234", "prior_cik_candidates_accepted": False,
            "historical_permco_change_candidate": False}


def test_sql_segments_and_exact_columns_are_bound():
    for role, (expected_hash, first, last) in audit.SPECS.items():
        data = sql(first, last).encode()
        assert audit.parse_query(data, role,
                                 expected_hash=hashlib.sha256(data).hexdigest()) == audit.EXPECTED_COLUMNS
        with pytest.raises(prior.AuditError, match="ISSUER_SQL_HASH_MISMATCH"):
            audit.parse_query(data, role)
        bad = data.replace(b"stkissuerinfohist", b"stksecurityinfohist")
        with pytest.raises(prior.AuditError, match="ISSUER_SQL_TABLE_MISMATCH"):
            audit.parse_query(bad, role, expected_hash=hashlib.sha256(bad).hexdigest())


def test_start_date_split_preserves_early_row_crossing_2020(tmp_path):
    early = tmp_path / "SYNTHETIC_TEST_ONLY_early.csv"
    late = tmp_path / "SYNTHETIC_TEST_ONLY_late.csv"
    first = issuer("11.0", "2020-01-01", "2022-05-01")
    second = issuer("11", "2020-01-02", "2023-01-01", role="LATER")
    write_csv(early, audit.EXPECTED_COLUMNS,
              [[first["raw_fields"][key] for key in audit.EXPECTED_COLUMNS]])
    write_csv(late, audit.EXPECTED_COLUMNS,
              [[second["raw_fields"][key] for key in audit.EXPECTED_COLUMNS]])
    left, left_rows, left_issues = audit.inspect_issuer_csv(early, "EARLY")
    right, right_rows, right_issues = audit.inspect_issuer_csv(late, "LATER")
    assert (left["row_count"], right["row_count"]) == (1, 1)
    assert not left_issues and not right_issues
    assert left_rows[0]["end"] == "2022-05-01"
    assert left_rows[0]["permco"] == right_rows[0]["permco"] == "11"


@pytest.mark.parametrize("field,value,code", [
    ("permco", "0", "ISSUER_PERMCO_INVALID"),
    ("issinfostartdt", "not-a-date", "ISSUER_INTERVAL_DATE_INVALID"),
    ("issinfostartdt", "2020-01-02", "ISSUER_START_OUTSIDE_QUERY"),
    ("issinfoenddt", "2018-01-01", "ISSUER_INTERVAL_REVERSED"),
])
def test_invalid_rows_are_retained_as_issues_and_block_joins(tmp_path, field, value, code):
    path = tmp_path / "SYNTHETIC_TEST_ONLY_bad.csv"
    row = issuer("11", "2019-01-01", "2021-01-01")["raw_fields"]
    row[field] = value
    write_csv(path, audit.EXPECTED_COLUMNS, [[row[key] for key in audit.EXPECTED_COLUMNS]])
    meta, records, issues = audit.inspect_issuer_csv(path, "EARLY")
    assert meta["row_count"] == 1 and not records
    assert code in issues[0]["codes"]


def test_wrong_header_and_row_width_are_not_silently_dropped(tmp_path):
    path = tmp_path / "SYNTHETIC_TEST_ONLY_width.csv"
    write_csv(path, audit.EXPECTED_COLUMNS, [["1"]])
    meta, records, issues = audit.inspect_issuer_csv(path, "EARLY")
    assert meta["row_count"] == 1 and not records
    assert issues[0]["codes"] == ["ISSUER_CSV_ROW_WIDTH_OR_HEADER_INVALID"]


def test_malformed_csv_has_structural_rejection_record(tmp_path):
    path = tmp_path / "SYNTHETIC_TEST_ONLY_malformed.csv"
    path.write_text(",".join(audit.EXPECTED_COLUMNS) + '\n"unterminated', encoding="utf-8")
    meta, records, issues = audit.inspect_issuer_csv(path, "EARLY")
    assert meta["row_count"] is None and not records
    assert issues[0]["codes"] == ["ISSUER_CSV_PARSE_OR_ENCODING_ERROR"]


def test_duplicate_conflict_overlap_gap_and_endpoint_touch():
    rows = [
        issuer("11", "2019-01-01", "2019-03-01", number=1),
        issuer("11", "2019-01-01", "2019-03-01", number=2),
        issuer("11", "2019-01-01", "2019-03-01", number=3, name="DIFFERENT_SYNTHETIC"),
        issuer("11", "2019-02-01", "2019-04-01", number=4),
        issuer("11", "2019-04-01", "2019-05-01", number=5),
        issuer("11", "2019-05-03", "2019-06-01", number=6),
    ]
    codes = {code for issue in audit.issuer_interval_issues(rows)
             for code in issue["codes"]}
    assert {"EXACT_DUPLICATE_ISSUER_ROW", "SAME_INTERVAL_ATTRIBUTE_CONFLICT",
            "ISSUER_INTERVAL_OVERLAP", "ISSUER_ENDPOINT_TOUCH_SEMANTICS_PENDING",
            "ISSUER_INTERVAL_GAP"} <= codes


def test_multiple_securities_share_permco_without_becoming_distinct_issuers():
    securities = [security("101", "11", "2019-01-01", "2019-12-31", number=1),
                  security("102", "11", "2019-01-01", "2019-12-31", number=2)]
    rows = [issuer("11", "2018-01-01", "2020-01-01")]
    joined, issues, counts = audit.join_candidates(securities, rows)
    assert not issues
    assert counts["one"] == 2 and counts["issuer_candidate_rows"] == 2
    assert counts["zero"] == counts["multiple"] == counts["not_fully_covered"] == 0
    assert all(item["issuer_date_coverage_under_closed_comparison"] for item in joined)
    assert all(not item["accepted"] for item in joined)


def test_multi_no_match_and_incomplete_interval_are_separate():
    securities = [security("101", "11", "2019-01-01", "2019-12-31"),
                  security("102", "99", "2019-01-01", "2019-12-31", number=2)]
    rows = [issuer("11", "2019-02-01", "2019-05-01", number=1),
            issuer("11", "2019-04-01", "2019-08-01", number=2)]
    joined, issues, counts = audit.join_candidates(securities, rows)
    assert counts["multiple"] == 1 and counts["zero"] == 1
    assert counts["not_fully_covered"] == 2
    assert [item["issuer_match_count"] for item in joined] == [2, 0]
    codes = {code for issue in issues for code in issue["codes"]}
    assert {"MULTIPLE_ISSUER_INTERVAL_INTERSECTIONS", "NO_ISSUER_INTERVAL_INTERSECTION",
            "ISSUER_DATES_DO_NOT_COVER_SECURITY_INTERVAL"} <= codes


def test_last_possible_calendar_date_does_not_overflow():
    item = security("101", "11", "9999-01-01", "9999-12-31")
    linked, issues, counts = audit.join_candidates(
        [item], [issuer("11", "9999-01-01", "9999-12-31")])
    assert not issues and counts["one"] == 1
    assert linked[0]["issuer_date_coverage_under_closed_comparison"]


def test_endpoint_equality_is_flagged_as_unverified_semantics():
    item = security("101", "11", "2020-01-01", "2020-01-01")
    linked, _, _ = audit.join_candidates([item], [issuer("11", "2019-01-01", "2020-01-01")])
    assert linked[0]["issuer_matches"][0]["endpoint_equality_review"]


def test_security_permco_change_is_a_candidate_not_cohort_expansion(tmp_path):
    columns = prior.EXPECTED_COLUMNS
    snapshot = {field: "" for field in columns}
    snapshot.update(permno="101", permco="11", secinfostartdt="2019-01-01",
                    secinfoenddt="2020-01-01", cusip="AAAA0001")
    change = dict(snapshot, permco="22", secinfostartdt="2020-01-02",
                  secinfoenddt="2022-01-01", cusip="BBBB0002")
    paths = {}
    for role, row in (("STARTING_SNAPSHOT", snapshot), ("LATER_CHANGES", change)):
        path = tmp_path / ("SYNTHETIC_TEST_ONLY_" + role + ".csv")
        write_csv(path, columns, [[row[key] for key in columns]])
        paths[role] = {"path": str(path), "sha256": prior.sha256_file(path),
                       "column_names": list(columns)}
    index = {"starting_securities": [{"permno": "101", "history": [
        {"source": role, "source_record": 1,
         "normalized_row_sha256": prior.row_digest(row),
         "start": row["secinfostartdt"], "end": row["secinfoenddt"],
         "historical_cusip": row["cusip"]}
        for role, row in (("STARTING_SNAPSHOT", snapshot), ("LATER_CHANGES", change))]}]}
    intervals, initial, changed, count, issues = audit.load_security_intervals(index, paths)
    assert not issues and count == 1
    assert initial == {"11"} and changed == {"22"}
    assert [item["historical_permco_change_candidate"] for item in intervals] == [False, True]
    changed_row = dict(change, issuernm="SYNTHETIC_TEST_ONLY_changed")
    write_csv(Path(paths["LATER_CHANGES"]["path"]), columns,
              [[changed_row[key] for key in columns]])
    with pytest.raises(prior.AuditError, match="PRIOR_RAW_CHANGED_DURING_READ"):
        audit.load_security_intervals(index, paths)


def test_changed_source_hash_is_rejected(tmp_path):
    path = tmp_path / "SYNTHETIC_TEST_ONLY_source.csv"
    row = issuer("11", "2019-01-01", "2021-01-01")["raw_fields"]
    write_csv(path, audit.EXPECTED_COLUMNS, [[row[key] for key in audit.EXPECTED_COLUMNS]])
    original_hash = prior.sha256_file(path)
    with patch.object(audit, "sha256_file", side_effect=[original_hash, "0" * 64]):
        with pytest.raises(prior.AuditError, match="ISSUER_CSV_CHANGED_DURING_READ"):
            audit.inspect_issuer_csv(path, "EARLY")


def test_saved_output_rejects_tampered_summary_and_nested_acceptance(tmp_path, monkeypatch):
    script = tmp_path / "scripts" / "SYNTHETIC_TEST_ONLY_script.py"
    script.parent.mkdir()
    script.write_text("SYNTHETIC_TEST_ONLY", encoding="utf-8")
    monkeypatch.setattr(audit, "__file__", str(script))
    monkeypatch.setattr(audit, "parse_query", lambda _data, _role: audit.EXPECTED_COLUMNS)
    output = tmp_path / "artifacts" / "SYNTHETIC_TEST_ONLY_output"
    output.mkdir(parents=True)
    sources = {}
    for role in ("EARLY", "LATER"):
        csv_path = tmp_path / ("SYNTHETIC_TEST_ONLY_" + role + ".csv")
        sql_path = tmp_path / ("SYNTHETIC_TEST_ONLY_" + role + ".sql")
        csv_path.write_text("SYNTHETIC_TEST_ONLY_csv", encoding="utf-8")
        sql_path.write_text("SYNTHETIC_TEST_ONLY_sql", encoding="utf-8")
        sources[role] = {"csv_path": str(csv_path), "sql_path": str(sql_path),
                         "sha256": prior.sha256_file(csv_path),
                         "sql_sha256": prior.sha256_file(sql_path)}
    prior_dir = tmp_path / "SYNTHETIC_TEST_ONLY_prior"
    prior_dir.mkdir()
    prior_names = {"registry": "m5_wrds_intake_registry.json",
                   "index": "m5_wrds_history_candidates_private.json",
                   "summary": "m5_wrds_intake_aggregate_summary.json"}
    prior_hashes = {}
    for key, filename in prior_names.items():
        path = prior_dir / filename
        path.write_text("SYNTHETIC_TEST_ONLY_" + key, encoding="utf-8")
        prior_hashes[key] = prior.sha256_file(path)
    old_paths = {}
    for role in ("STARTING_SNAPSHOT", "LATER_CHANGES"):
        path = tmp_path / ("SYNTHETIC_TEST_ONLY_" + role)
        path.write_text(role, encoding="utf-8")
        old_paths[role] = str(path)
    link = tmp_path / "SYNTHETIC_TEST_ONLY_link"
    receipt = tmp_path / "SYNTHETIC_TEST_ONLY_receipt"
    link.write_text("link", encoding="utf-8")
    receipt.write_text("receipt", encoding="utf-8")
    registry = {
        "issuer_audit_script_path": str(script),
        "issuer_audit_script_sha256": prior.sha256_file(script),
        "issuer_sources": sources,
        "prior_artifacts": {"directory": str(prior_dir), "sha256": prior_hashes},
        "prior_source_paths": old_paths,
        "prior_source_sha256": {role: prior.sha256_file(Path(path))
                                for role, path in old_paths.items()},
        "prior_links_path": str(link), "prior_links_sha256": prior.sha256_file(link),
        "prior_v2_receipt_path": str(receipt),
        "prior_v2_receipt_sha256": prior.sha256_file(receipt),
        "local_csv_binding_status": "LOCAL_ISSUER_CSV_STRUCTURE_AND_HASH_BOUND",
    }
    candidate = {"accepted": False, "prior_cik_candidates_accepted": False,
                 "issuer_matches": [{"accepted": False}]}
    index = {"accepted": False, "formal_m5_cohort_selected": False,
             "summary": {"issuer_total_rows": 1, "security_interval_count": 1,
                         "related_issuer_rows": 1, "exception_record_count": 0},
             "security_interval_issuer_candidates": [candidate],
             "related_issuer_records": [{}], "exceptions": []}
    summary = {**index["summary"], "formal_m5_cohort_selected": False,
               "status": registry["local_csv_binding_status"],
               "query_sql_sha256": {role: item["sql_sha256"] for role, item in sources.items()},
               "issuer_csv_sha256": {role: item["sha256"] for role, item in sources.items()},
               "prior_artifact_sha256": prior_hashes}

    def save():
        summary["registry_sha256"] = hashlib.sha256(audit.json_bytes(registry)).hexdigest()
        summary["index_sha256"] = hashlib.sha256(audit.json_bytes(index)).hexdigest()
        for filename, value in (("m5_issuer_history_registry.json", registry),
                                ("m5_issuer_history_index_private.json", index),
                                ("m5_issuer_history_summary.json", summary)):
            (output / filename).write_bytes(audit.json_bytes(value))

    save()
    audit.verify_output(output)
    summary["issuer_total_rows"] = 2
    save()
    with pytest.raises(prior.AuditError, match="ISSUER_OUTPUT_COUNT_MISMATCH"):
        audit.verify_output(output)
    summary["issuer_total_rows"] = 1
    candidate["issuer_matches"][0]["accepted"] = True
    save()
    with pytest.raises(prior.AuditError, match="ISSUER_CANDIDATE_ACCEPTANCE_INVALID"):
        audit.verify_output(output)
