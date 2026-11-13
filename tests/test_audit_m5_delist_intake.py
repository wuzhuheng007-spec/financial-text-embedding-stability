"""SYNTHETIC_TEST_ONLY tests for the local filtered delisting intake."""

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
import audit_m5_delist_intake as audit  # noqa: E402
import audit_m5_wrds_intake as prior  # noqa: E402


def synthetic_sql():
    fields = ",\n".join(("id_table." if name in audit.HEADER_FIELDS else
                         "crsp_q_stock.stkdelists.") + name
                        for name in audit.EXPECTED_COLUMNS)
    return (
        "SELECT\n" + fields + "\nFROM crsp_q_stock.stkdelists\n"
        "LEFT JOIN ( SELECT permno, primaryexch, nasdissuno, siccd "
        "FROM crsp_q_stock.stksecurityinfohdr ) AS id_table\n"
        "ON crsp_q_stock.stkdelists.permno = id_table.permno\n"
        "WHERE crsp_q_stock.stkdelists.delistingdt BETWEEN "
        "'2019-11-01'::date AND '2026-02-28'::date\n"
        'AND ("crsp_q_stock"."stkdelists"."delactiontype" = '
        "ANY (ARRAY['GDR','GEX','GLI','LOS','MER']))\n"
    )


def row(permno="101", day="2020-01-01", action="GDR", **changes):
    fields = {name: "" for name in audit.EXPECTED_COLUMNS}
    fields.update(permno=permno, delistingdt=day, delactiontype=action,
                  primaryexch="N", nasdissuno="1", siccd="1234")
    fields.update(changes)
    return fields


def write_csv(path, rows, columns=audit.EXPECTED_COLUMNS):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(columns)
        writer.writerows([[item[key] for key in columns] for item in rows])


def inspect(tmp_path, rows):
    path = tmp_path / "SYNTHETIC_TEST_ONLY_delists.csv"
    write_csv(path, rows)
    return audit.inspect_export(path)


def test_query_requires_exact_join_fields_date_and_action_filter():
    data = synthetic_sql().encode()
    digest = hashlib.sha256(data).hexdigest()
    assert audit.parse_query(data, digest) == audit.EXPECTED_COLUMNS
    with pytest.raises(prior.AuditError, match="DELIST_SQL_HASH_MISMATCH"):
        audit.parse_query(data)
    for old, new in ((b"LEFT JOIN", b"INNER JOIN"),
                     (b"2026-02-28", b"2026-02-27"),
                     (b"'MER'", b"'DUM'"),
                     (b"stksecurityinfohdr", b"stksecurityinfohist")):
        modified = data.replace(old, new)
        assert modified != data
        with pytest.raises(prior.AuditError, match="DELIST_SQL_JOIN_OR_FILTER_INVALID"):
            audit.parse_query(modified, hashlib.sha256(modified).hexdigest())


def test_five_actions_and_both_date_endpoints(tmp_path):
    rows = [row(str(101 + i), "2019-11-01" if i == 0 else
                "2026-02-28" if i == 4 else "2022-01-01", action)
            for i, action in enumerate(audit.ACTIONS)]
    meta, records, issues = inspect(tmp_path, rows)
    assert meta["row_count"] == 5 and meta["core_issue_count"] == 0
    assert not issues and [item["delactiontype"] for item in records] == list(audit.ACTIONS)
    assert records[0]["delistingdt"] == "2019-11-01"
    assert records[-1]["delistingdt"] == "2026-02-28"


@pytest.mark.parametrize("changed,code", [
    ({"permno": "0"}, "DELIST_PERMNO_INVALID"),
    ({"delistingdt": "not-date"}, "DELIST_DATE_INVALID"),
    ({"delistingdt": "2019-10-31"}, "DELIST_DATE_OUTSIDE_QUERY"),
    ({"delistingdt": "2026-03-01"}, "DELIST_DATE_OUTSIDE_QUERY"),
    ({"delactiontype": "DUM"}, "DELIST_ACTION_OUTSIDE_QUERY"),
])
def test_core_bad_rows_are_retained_for_rejection(tmp_path, changed, code):
    meta, records, issues = inspect(tmp_path, [row(**changed)])
    assert meta["row_count"] == len(records) == 1
    assert meta["core_issue_count"] == 1
    assert code in records[0]["issues"] and code in issues[0]["codes"]


def test_header_and_event_fields_are_separate_and_optional_issues_preserved(tmp_path):
    meta, records, issues = inspect(tmp_path, [row(
        delpermno="BAD", delret="NaN", deldlydt="not-date")])
    assert meta["core_issue_count"] == 0
    assert set(records[0]["header_fields"]) == set(audit.HEADER_FIELDS)
    assert set(records[0]["event_fields"]) == set(audit.EVENT_FIELDS)
    assert records[0]["header_provenance"] == "STKSECURITYINFOHDR_UNDATED_RECENT_VALUES"
    assert {"DELPERMNO_NOT_POSITIVE_JOIN_KEY", "DELDLYDT_UNUSABLE",
            "DELRET_NONFINITE_OR_UNUSABLE"} <= set(issues[0]["codes"])


def test_successor_missing_codes_are_not_treated_as_positive_keys():
    assert audit.successor_key_class("202") == "POSITIVE_JOIN_CANDIDATE"
    assert audit.successor_key_class("0") == "NONPOSITIVE_NUMERIC_NOT_JOINED"
    assert audit.successor_key_class("-5") == "NONPOSITIVE_NUMERIC_NOT_JOINED"
    assert audit.successor_key_class("") == "BLANK"
    assert audit.successor_key_class("UNKNOWN") == "OTHER_UNRESOLVED_NOT_JOINED"


def test_all_rows_and_duplicate_review_categories_survive(tmp_path):
    base = row(delret="0.1")
    header_changed = dict(base, primaryexch="Q")
    event_changed = dict(base, delret="0.2")
    _, records, _ = inspect(tmp_path, [base, dict(base), header_changed, event_changed])
    groups, counts = audit.review_groups(records)
    assert len(records) == 4 and len(groups) == 1
    assert groups[0]["source_records"] == [1, 2, 3, 4]
    assert counts["exact_duplicate_extra_rows"] == 1
    assert counts["REVIEW_KEY_EVENT_PAYLOAD_CONFLICT"] == 1
    assert groups[0]["distinct_header_payload_count"] == 2
    assert not groups[0]["accepted"]
    _, only_header, _ = inspect(tmp_path, [base, header_changed])
    _, only_counts = audit.review_groups(only_header)
    assert only_counts["SAME_EVENT_PAYLOAD_HEADER_VARIANT"] == 1


def test_review_key_is_not_a_unique_event_key(tmp_path):
    _, records, _ = inspect(tmp_path, [row(day="2020-01-01"),
                                      row(day="2021-01-01", action="MER")])
    groups, _ = audit.review_groups(records)
    assert len(groups) == 2
    assert all(not group["accepted"] for group in groups)


def test_starting_scope_and_successor_remain_candidates(tmp_path):
    _, records, _ = inspect(tmp_path, [row("101", delpermno="202", delpermco="303"),
                                      row("999", action="MER")])
    groups, _ = audit.review_groups(records)
    interval = {"source_role": "STARTING_SNAPSHOT", "source_record": 1,
                "start": "2019-01-01", "end": "2020-12-31", "permco": "11"}
    notes, counts = audit.diagnose_starting(
        records, groups, {"101", "102"},
        {"101": {"historical_cusip_changed": True},
         "102": {"historical_cusip_changed": False}},
        {"101": [interval], "102": [interval]}, {"11": "2022-12-31"})
    assert counts["starting_security_count"] == 2
    assert counts["starting_with_one_review_group"] == 1
    assert counts["starting_with_zero_review_groups"] == 1
    assert counts["starting_export_rows"] == counts["outside_starting_export_rows"] == 1
    assert counts["delpermno_candidate_rows"] == counts["delpermco_candidate_rows"] == 1
    assert notes[0]["successor_link_accepted"] is False
    assert notes[0]["formal_cohort_admitted"] is False


def test_event_before_all_security_history_has_no_last_known_issuer_claim(tmp_path):
    _, records, _ = inspect(tmp_path, [row("101", day="2019-11-01")])
    groups, _ = audit.review_groups(records)
    future = {"source_role": "STARTING_SNAPSHOT", "source_record": 1,
              "start": "2020-01-01", "end": "2021-01-01", "permco": "11"}
    notes, counts = audit.diagnose_starting(
        records, groups, {"101"}, {"101": {"historical_cusip_changed": False}},
        {"101": [future]}, {"11": "2021-01-01"})
    assert counts["event_without_preceding_security_history_interval"] == 1
    assert notes[0]["no_preceding_security_interval"] is True
    assert notes[0]["issuer_history_end_relations"] == []


def test_event_in_security_history_gap_uses_only_preceding_identity(tmp_path):
    _, records, _ = inspect(tmp_path, [row("101", day="2020-06-01")])
    groups, _ = audit.review_groups(records)
    earlier = {"source_role": "STARTING_SNAPSHOT", "source_record": 1,
               "start": "2019-01-01", "end": "2020-01-01", "permco": "11"}
    later = {"source_role": "LATER_CHANGES", "source_record": 2,
             "start": "2021-01-01", "end": "2022-01-01", "permco": "22"}
    notes, counts = audit.diagnose_starting(
        records, groups, {"101"}, {"101": {"historical_cusip_changed": False}},
        {"101": [earlier, later]}, {"11": "2020-01-01", "22": "2022-01-01"})
    assert counts["active_security_interval_count_0"] == 1
    assert notes[0]["issuer_history_end_relations"] == [
        {"permco": "11", "last_issinfoenddt": "2020-01-01",
         "relation": "AFTER_LAST_ISSUER_RANGE_END",
         "identity_basis": "LAST_PRECEDING_NOT_EVENT_DATE_VALID"}]


def test_prior_issuer_multi_intersections_are_refined_not_all_errors():
    sequential = {"source_role": "STARTING_SNAPSHOT", "source_record": 1,
                  "issuer_date_coverage_under_closed_comparison": True,
                  "issuer_matches": [
                      {"intersection_start": "2019-01-01", "intersection_end": "2019-03-01"},
                      {"intersection_start": "2019-03-02", "intersection_end": "2019-06-01"}]}
    overlap = {**sequential, "source_record": 2, "issuer_matches": [
        {"intersection_start": "2019-01-01", "intersection_end": "2019-04-01"},
        {"intersection_start": "2019-03-15", "intersection_end": "2019-06-01"}]}
    gaps = {**sequential, "source_record": 3, "issuer_matches": [
        {"intersection_start": "2019-01-01", "intersection_end": "2019-03-01"},
        {"intersection_start": "2019-03-04", "intersection_end": "2019-06-01"}]}
    details, counts = audit.split_issuer_intersections({"101": [sequential, overlap, gaps]})
    assert counts["multiple_intersection_security_intervals"] == 3
    assert counts["SEQUENTIAL_CLOSED_DATE_SEGMENTS"] == 1
    assert counts["overlap"] == counts["internal_gap"] == 1
    assert all(not item["accepted"] for item in details)


def test_source_change_during_read_rejects(tmp_path):
    path = tmp_path / "SYNTHETIC_TEST_ONLY_changed.csv"
    write_csv(path, [row()])
    actual = prior.sha256_file(path)
    with patch.object(audit, "sha256_file", side_effect=[actual, "0" * 64]):
        with pytest.raises(prior.AuditError, match="DELIST_CSV_CHANGED_DURING_READ"):
            audit.inspect_export(path)


def test_core_failure_saves_private_record_and_does_not_join(tmp_path, monkeypatch):
    csv_path = tmp_path / "SYNTHETIC_TEST_ONLY_bad.csv"
    sql_path = tmp_path / "SYNTHETIC_TEST_ONLY_query.sql"
    write_csv(csv_path, [row(permno="0")])
    sql_path.write_text(synthetic_sql(), encoding="utf-8")
    output = tmp_path / "SYNTHETIC_TEST_ONLY_rejected"
    args = SimpleNamespace(csv=csv_path, sql=sql_path, output_dir=output,
                           prior_wrds_dir=tmp_path, prior_issuer_dir=tmp_path)
    monkeypatch.setattr(audit, "check_ignored_output", lambda _path: None)
    monkeypatch.setattr(audit, "load_prior", lambda *_: pytest.fail("prior join must not run"))

    def save(directory, files):
        directory.mkdir()
        for name, data in files.items():
            (directory / name).write_bytes(audit.json_bytes(data))

    monkeypatch.setattr(audit, "write_directory", save)
    result = audit.run(args, expected_sql_hash=hashlib.sha256(sql_path.read_bytes()).hexdigest())
    assert result["status"] == "REJECTED_CORE_STRUCTURE"
    assert not (output / "m5_delist_candidates_private.json").exists()
    private = json.loads((output / "m5_delist_structural_issues_private.json").read_text())
    assert private["issues"][0]["codes"] == ["DELIST_PERMNO_INVALID"]
    assert len(private["raw_export_records"]) == 1


def test_full_synthetic_roundtrip_rejects_output_and_source_tampering(tmp_path, monkeypatch):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "audit_m5_delist_intake.py"
    script.write_text("SYNTHETIC_TEST_ONLY_script", encoding="utf-8")
    for name in ("audit_m5_wrds_intake.py", "audit_m5_issuer_history.py"):
        (scripts / name).write_text("SYNTHETIC_TEST_ONLY_" + name, encoding="utf-8")
    monkeypatch.setattr(audit, "__file__", str(script))
    csv_path = tmp_path / "SYNTHETIC_TEST_ONLY_events.csv"
    sql_path = tmp_path / "SYNTHETIC_TEST_ONLY_query.sql"
    write_csv(csv_path, [row("101", delpermno="202"), row("999", action="MER")])
    sql_path.write_text(synthetic_sql(), encoding="utf-8")
    wrds_dir = tmp_path / "SYNTHETIC_TEST_ONLY_wrds"
    issuer_dir = tmp_path / "SYNTHETIC_TEST_ONLY_issuer"
    wrds_dir.mkdir()
    issuer_dir.mkdir()
    wrds_names = {"registry": "m5_wrds_intake_registry.json",
                  "index": "m5_wrds_history_candidates_private.json",
                  "summary": "m5_wrds_intake_aggregate_summary.json"}
    issuer_names = {"registry": "m5_issuer_history_registry.json",
                    "index": "m5_issuer_history_index_private.json",
                    "summary": "m5_issuer_history_summary.json"}
    for directory, names, constant in ((wrds_dir, wrds_names, "PRIOR_WRDS_HASHES"),
                                       (issuer_dir, issuer_names, "PRIOR_ISSUER_HASHES")):
        hashes = {}
        for key, filename in names.items():
            path = directory / filename
            path.write_text("SYNTHETIC_TEST_ONLY_" + filename, encoding="utf-8")
            hashes[key] = prior.sha256_file(path)
        monkeypatch.setattr(audit, constant, hashes)
    interval = {"source_role": "STARTING_SNAPSHOT", "source_record": 1,
                "start": "2019-01-01", "end": "2022-01-01", "permco": "11",
                "issuer_matches": [{"intersection_start": "2019-01-01",
                                    "intersection_end": "2022-01-01"}],
                "issuer_date_coverage_under_closed_comparison": True}
    prior_tuple = ({"101"}, {"101": {"historical_cusip_changed": False}},
                   {"101": [interval]}, {"11": "2022-01-01"})
    monkeypatch.setattr(audit, "load_prior", lambda *_: prior_tuple)
    monkeypatch.setattr(audit, "verify_issuer_output", lambda _path: None)
    monkeypatch.setattr(audit, "check_ignored_output", lambda _path: None)

    def save(directory, files):
        directory.mkdir()
        for name, data in files.items():
            (directory / name).write_bytes(audit.json_bytes(data))

    monkeypatch.setattr(audit, "write_directory", save)
    output = tmp_path / "artifacts" / "SYNTHETIC_TEST_ONLY_output"
    output.parent.mkdir()
    args = SimpleNamespace(csv=csv_path, sql=sql_path, output_dir=output,
                           prior_wrds_dir=wrds_dir, prior_issuer_dir=issuer_dir)
    summary = audit.run(args, expected_sql_hash=hashlib.sha256(sql_path.read_bytes()).hexdigest())
    assert summary["starting_export_rows"] == 1
    assert summary["outside_starting_export_rows"] == 1
    monkeypatch.setattr(audit, "parse_query", lambda _data, _expected=":": audit.EXPECTED_COLUMNS)
    audit.verify_output(output)
    summary_path = output / "m5_delist_summary.json"
    summary_path.write_text(summary_path.read_text().replace('"export_row_count": 2',
                                                            '"export_row_count": 3'))
    with pytest.raises(prior.AuditError, match="DELIST_MANIFEST_FILE_HASH_MISMATCH"):
        audit.verify_output(output)
    summary_path.write_bytes(audit.json_bytes(summary))
    csv_path.write_text(csv_path.read_text() + "\n", encoding="utf-8")
    with pytest.raises(prior.AuditError, match="DELIST_INPUT_HASH_MISMATCH"):
        audit.verify_output(output)
