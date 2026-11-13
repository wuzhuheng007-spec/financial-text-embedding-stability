"""Synthetic-only regression checks for the merged distribution intake."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import tempfile
import zipfile
from pathlib import Path

import pytest

from scripts import audit_m5_distribution_multiyear as audit


FIELDS = (
    "distype permno disexdt primaryexch nasdissuno siccd disseqnbr "
    "disordinaryflg disfreqtype dispaymenttype disdetailtype distaxtype "
    "disorigcurtype disdivamt disfacpr disfacshr disdeclaredt disrecorddt "
    "dispaydt dispermno dispermco disamountsourcetype"
).split()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)


def csv_bytes(rows: list[dict[str, str]], *, fields: list[str] = FIELDS) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def event(identifier: str, day: str, seq: str, *, amount: str = "1",
          kind: str = "CD") -> dict[str, str]:
    row = dict.fromkeys(FIELDS, "")
    row.update(permno=identifier, disexdt=day, disseqnbr=seq,
               distype=kind, disdivamt=amount, primaryexch="N")
    return row


def prior_fixture(root: Path) -> Path:
    prior = root / "prior"
    prior.mkdir()
    summary = {
        "scope": "ENGINEERING_ONLY",
        "requested_disexdt_window_inclusive": ["2019-11-01", "2019-12-31"],
        "row_count": 1, "observed_bound_identifier_count": 1,
    }
    (prior / audit.SUMMARY).write_bytes(audit.json_bytes(summary))
    (prior / "event_index_private.jsonl").write_text(
        json.dumps({"permno": "33"}) + "\n", encoding="utf-8")
    audit.write_json_new(prior / audit.OUTPUT_MANIFEST, {
        "saved_file_sha256": {
            "event_index_private.jsonl": audit.sha(prior / "event_index_private.jsonl")}})
    return prior


def mapping_fixture() -> dict:
    items = []
    for index, name in enumerate(FIELDS):
        header = name in audit.HEADER_CONTEXT
        items.append({"ui_index": index, "name": name,
                      "table_id": 273706 if header else 273698,
                      "declared_table": ("crsp_q_stock.stksecurityinfohdr" if header else
                                         "crsp_q_stock.stkdistributions")})
    return {"schema": "m5_distribution_user_transcript_field_mapping_v1",
            "fields_in_ui_index_order": items, "selected_fields_using_273776": []}


def parameters_fixture() -> bytes:
    lines = [f"{name}" for name in FIELDS]
    lines += ["begin_date\t:\t2020-01-01", "end_date\t:\t2026-02-28",
              "crsp_q_stock.stkdistributions.disexdt",
              "crsp_q_stock.stksecurityinfohdr.permno", "file_as_variable_flag",
              "source_saved_query\t:\t7991527", "compress\t:\tzip",
              "format\t:\tcsv"]
    lines.extend("'" + name + "'" for name in sorted(audit.SELECTED_TYPES))
    return ("\n".join(lines) + "\n").encode()


def daily_fixture(root: Path) -> Path:
    segments = []
    for name, _, _ in audit.SEGMENTS:
        year = int(name[:4])
        directory = root / f"daily_{name}"
        directory.mkdir()
        exception = directory / "daily_exceptions_private.jsonl"
        if year == 2020:
            exception.write_text(json.dumps({
                "permno": "11", "dlycaldt": "2020-01-02",
                "codes": ["PERMNO_DATE_DUPLICATE"]}) + "\n", encoding="utf-8")
            group_count = extra_count = 1
        else:
            exception.write_text("", encoding="utf-8")
            group_count = extra_count = 0
        summary = directory / "summary_safe.json"
        manifest = directory / "output_manifest_private.json"
        audit.write_json_new(summary, {
            "scope": "ENGINEERING_ONLY",
            "duplicate_key_group_count": group_count,
            "duplicate_extra_row_count": extra_count})
        audit.write_json_new(manifest, {
            "saved_file_sha256": {exception.name: audit.sha(exception)}})
        segments.append({"year": year,
                         "summary_path": str(summary.relative_to(root)).replace("\\", "/"),
                         "summary_sha256": audit.sha(summary),
                         "manifest_path": str(manifest.relative_to(root)).replace("\\", "/"),
                         "manifest_sha256": audit.sha(manifest),
                         "duplicate_key_group_count": group_count,
                         "duplicate_extra_row_count": extra_count})
    inventory = root / "inventory.json"
    audit.write_json_new(inventory, {
        "schema": "m5_daily_eight_segments_inventory_safe_v1",
        "duplicate_key_definition":
        "PERMNO plus DlyCalDt within non-overlapping saved date sets",
        "segments": segments})
    return inventory


def package_fixture(root: Path, rows: list[dict[str, str]]) -> Path:
    nested = root / "nested.zip"
    write_zip(nested, {audit.CSV_MEMBER: csv_bytes(rows)})
    prior_summary = (root / "prior" / audit.SUMMARY).read_bytes()
    prior_manifest = {
        "files": {"03_MACHINE_SUMMARY_SAFE.json": {
            "bytes": len(prior_summary), "sha256": digest(prior_summary)}}}
    prior_safe = root / "prior_safe.zip"
    write_zip(prior_safe, {
        "03_MACHINE_SUMMARY_SAFE.json": prior_summary,
        "11_FILE_MANIFEST.json": audit.json_bytes(prior_manifest)})
    upload = (root / "upload.txt").read_bytes()
    members = {
        audit.RAW_ZIP_MEMBER: nested.read_bytes(),
        audit.UPLOAD_MEMBER: upload,
        audit.PRIOR_SAFE_MEMBER: prior_safe.read_bytes(),
        audit.FIELD_MAPPING: audit.json_bytes(mapping_fixture()),
        audit.PARAMETERS: parameters_fixture(),
    }
    manifest = {
        "schema": "m5_distribution_merged_handoff_manifest_v1",
        "files": {name: {"bytes": len(data), "sha256": digest(data)}
                  for name, data in members.items()},
    }
    members[audit.PACKAGE_MANIFEST] = audit.json_bytes(manifest)
    handoff = root / "handoff.zip"
    write_zip(handoff, members)
    return handoff


def synthetic_environment(tmp: Path, *, rows: list[dict[str, str]] | None = None):
    prior = prior_fixture(tmp)
    (tmp / "upload.txt").write_bytes(b"11\n22\n")
    inventory = daily_fixture(tmp)
    if rows is None:
        rows = [event("11", "2020-01-02", "1"),
                event("11", "2020-01-02", "2"),
                event("22", "2021-12-31", "1"),
                event("22", "2021-12-31", "1", amount="2"),
                event("11", "2026-02-28", "1", kind="N/A")]
    handoff = package_fixture(tmp, rows)
    return handoff, tmp / "upload.txt", inventory, prior


def test_synthetic_full_run_verify_and_tamper():
    with tempfile.TemporaryDirectory(prefix="m5-dist-merged-test-", dir=audit.ART) as directory:
        root = Path(directory)
        handoff, upload, inventory, prior = synthetic_environment(root)
        output = root / "result"
        summary = audit.run(handoff, output, bound_txt=upload, inventory=inventory,
                            prior_dir=prior, expected_rows=5, expected_ids=2,
                            synthetic_test_only=True, repo=root)
        comparison = audit.read_json(output / audit.COMPARISON)
        annual = audit.read_json(output / audit.ANNUAL)
        assert summary["scope"] == "SYNTHETIC_TEST_ONLY"
        assert summary["row_count"] == annual["row_count_sum"] == 5
        assert summary["candidate_three_field_key_duplicate_groups"] == 1
        assert summary["candidate_duplicate_group_classes"]["non_header_field_conflict"] == 1
        assert summary["collection_window_context"]["union_distinct_identifier_count"] == 3
        assert comparison["all_seven"]["status"] == "COMPLETE_SEVEN_SEGMENT_INDEX_COMPARISON"
        assert comparison["segments"]["2020"]["intersection_group_count"] == 1
        assert comparison["segments"]["2020"]["both_multiplicities_match_count"] == 1
        assert annual["segments"]["2026janfeb"]["row_count"] == 1
        assert audit.verify_output(output)["source_csv_sha256"] == summary["source_csv_sha256"]
        with pytest.raises(audit.IntakeError, match="OUTPUT_ALREADY_EXISTS"):
            audit.run(handoff, output, bound_txt=upload, inventory=inventory,
                      prior_dir=prior, expected_rows=5, expected_ids=2,
                      synthetic_test_only=True, repo=root)
        upload_before = upload.read_bytes()
        upload.write_bytes(b"11\n23\n")
        with pytest.raises(audit.IntakeError, match="INPUT_SOURCE_CHANGED"):
            audit.verify_output(output)
        upload.write_bytes(upload_before)
        assert audit.verify_output(output)["row_count"] == 5
        target = output / audit.ANNUAL
        target.write_bytes(target.read_bytes() + b" ")
        with pytest.raises(audit.IntakeError, match="SAVED_FILE_HASH_OR_PATH_MISMATCH"):
            audit.verify_output(output)


def test_year_boundaries_invalid_dates_and_missing_daily_index():
    with tempfile.TemporaryDirectory(prefix="m5-dist-merged-test-", dir=audit.ART) as directory:
        root = Path(directory)
        rows = [event("11", "2020-02-29", "01"),
                event("11", "2024-02-29", "1"),
                event("11", "2026-02-28", "1"),
                event("11", "2026-02-29", "1"),
                event("11", "2026-03-01", "1")]
        csv_path = root / "events.csv"
        csv_path.write_bytes(csv_bytes(rows))
        summary, annual, data = audit.analyze_csv(
            csv_path, {"11"}, FIELDS, root / "rows.sqlite3",
            root / "exceptions.jsonl", expected_rows=5)
        assert annual["segments"]["2020"]["row_count"] == 1
        assert annual["segments"]["2024"]["row_count"] == 1
        assert annual["segments"]["2026janfeb"]["row_count"] == 1
        assert summary["unassigned_or_outside_window_row_count"] == 2
        assert {"DISEXDT_INVALID", "DISEXDT_OUTSIDE_WINDOW"} <= set(summary["structural_issue_codes"])
        groups = {name: None for name, _, _ in audit.SEGMENTS}
        evidence = {name: {"status": "INDEX_NOT_LOCATED"} for name in groups}
        comparison = audit.compare_seven_segments(root / "rows.sqlite3", data,
                                                   groups, evidence,
                                                   root / "comparison.jsonl")
        assert comparison["all_seven"]["status"] == "PARTIAL_SEVEN_SEGMENT_INDEX_COMPARISON"
        assert comparison["all_seven"]["daily_duplicate_group_denominator"] is None
        assert comparison["segments"]["2020"]["daily_only_group_count"] is None


def test_identifier_key_and_field_identity_failures():
    with tempfile.TemporaryDirectory(prefix="m5-dist-merged-test-", dir=audit.ART) as directory:
        root = Path(directory)
        csv_path = root / "events.csv"
        csv_path.write_bytes(csv_bytes([event("999", "2020-01-01", "1"),
                                        event("11", "2020-01-01", "N/A")]))
        summary, _, _ = audit.analyze_csv(
            csv_path, {"11"}, FIELDS, root / "rows.sqlite3",
            root / "exceptions.jsonl", expected_rows=2)
        assert "PERMNO_OUTSIDE_BOUND_SET" in summary["structural_issue_codes"]
        assert summary["sequence_uncertain_row_count"] == 1
        assert summary["observed_bound_identifier_count"] == 1
        with pytest.raises(audit.IntakeError, match="FIELD_MAPPING_INDEX_INVALID"):
            wrong = mapping_fixture()
            wrong["fields_in_ui_index_order"][1]["ui_index"] = 0
            audit.inspect_mapping(wrong, parameters_fixture().decode())


def test_daily_source_change_never_becomes_zero_denominator():
    with tempfile.TemporaryDirectory(prefix="m5-dist-merged-test-", dir=audit.ART) as directory:
        root = Path(directory)
        inventory = daily_fixture(root)
        changed = root / "daily_2020" / "daily_exceptions_private.jsonl"
        changed.write_bytes(changed.read_bytes() + b" ")
        groups, evidence = audit.read_daily_duplicate_indexes(inventory, repo=root)
        assert groups["2020"] is None
        assert evidence["2020"]["status"] == "INDEX_SOURCE_OR_KEY_REVIEW_REQUIRED"
        assert groups["2021"] == {}


def test_zero_sequence_na_header_variant_and_full_row_repeat():
    with tempfile.TemporaryDirectory(prefix="m5-dist-merged-test-", dir=audit.ART) as directory:
        root = Path(directory)
        first = event("11", "2020-01-02", "0", kind="N/A")
        header_variant = dict(first, primaryexch="Q")
        repeated = event("22", "2020-01-03", "0", kind="N/A")
        csv_path = root / "events.csv"
        csv_path.write_bytes(csv_bytes([first, header_variant, repeated, repeated]))
        summary, annual, _ = audit.analyze_csv(
            csv_path, {"11", "22"}, FIELDS, root / "rows.sqlite3",
            root / "exceptions.jsonl", expected_rows=4)
        assert summary["sequence_uncertain_row_count"] == 0
        assert summary["distype_counts_literal"]["N/A"] == 4
        assert "DISTYPE_OUTSIDE_RECORDED_PREDICATE" not in summary["structural_issue_codes"]
        assert summary["candidate_duplicate_group_classes"] == {
            "parsed_full_row_repeat": 1, "provisional_header_only_variant": 1}
        assert annual["segments"]["2020"]["candidate_three_field_key_extra_rows"] == 2
        private = [json.loads(line) for line in (root / "exceptions.jsonl").read_text(
            encoding="utf-8").splitlines()]
        assert all(item["disseqnbr_normalized"] == "0" for item in private)


def test_empty_csv_and_missing_selected_field_are_structural_review():
    with tempfile.TemporaryDirectory(prefix="m5-dist-merged-test-", dir=audit.ART) as directory:
        root = Path(directory)
        empty = root / "empty.csv"
        empty.write_bytes(b"")
        empty_summary, _, _ = audit.analyze_csv(
            empty, {"11"}, FIELDS, root / "empty.sqlite3",
            root / "empty_exceptions.jsonl", expected_rows=1)
        assert {"HEADER_MISSING_OR_BLANK", "SELECTED_FIELDS_MISSING", "EMPTY_CSV"} <= set(
            empty_summary["structural_issue_codes"])
        assert empty_summary["status"] == "STRUCTURE_REVIEW_REQUIRED"
        missing = root / "missing.csv"
        missing_row = event("11", "2020-01-02", "1")
        del missing_row["dispaydt"]
        missing.write_bytes(csv_bytes([missing_row],
                                      fields=[name for name in FIELDS if name != "dispaydt"]))
        missing_summary, _, _ = audit.analyze_csv(
            missing, {"11"}, FIELDS, root / "missing.sqlite3",
            root / "missing_exceptions.jsonl", expected_rows=1)
        assert missing_summary["missing_selected_fields"] == ["dispaydt"]
        assert "SELECTED_FIELDS_MISSING" in missing_summary["structural_issue_codes"]
