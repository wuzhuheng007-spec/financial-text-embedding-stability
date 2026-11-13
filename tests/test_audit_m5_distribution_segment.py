"""Synthetic-only software checks for the offline distribution intake."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
import tempfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_m5_distribution_segment.py"
SPEC = importlib.util.spec_from_file_location("distribution_intake", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

FIELDS = [
    "siccd", "permno", "disexdt", "distype", "disfacpr", "dispaydt",
    "disdivamt", "disfacshr", "dispermco", "dispermno", "disseqnbr",
    "distaxtype", "nasdissuno", "disfreqtype", "disrecorddt",
    "primaryexch", "disdeclaredt", "disdetailtype", "disordinaryflg",
    "disorigcurtype", "dispaymenttype", "disamountsourcetype",
]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def row(**values: str) -> dict[str, str]:
    result = {name: "" for name in FIELDS}
    result.update({"permno": "1", "disexdt": "2019-11-01", "disseqnbr": "1",
                   "distype": "CD", "siccd": "1000", "primaryexch": "N",
                   "nasdissuno": "X", "disdivamt": "1"})
    result.update(values)
    return result


def csv_bytes(rows: list[dict[str, str]], *, header: list[str] = FIELDS,
              add_blank_physical_line: bool = False) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=header, lineterminator="\n",
                            extrasaction="ignore")
    writer.writeheader()
    for record in rows:
        writer.writerow(record)
        if add_blank_physical_line:
            stream.write("\n")
    return stream.getvalue().encode("utf-8")


def fixture_package(base: Path, rows: list[dict[str, str]],
                    *, header: list[str] = FIELDS,
                    blank_line: bool = False) -> tuple[Path, Path, Path]:
    base.mkdir(parents=True, exist_ok=True)
    bound = base / "bound.txt"
    bound.write_bytes(b"1\n2\n")
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as inner:
        inner.writestr(module.CSV_MEMBER,
                       csv_bytes(rows, header=header,
                                 add_blank_physical_line=blank_line))
    members = {
        module.PACKAGED_ZIP: raw.getvalue(),
        module.PACKAGED_TXT: b"1\n2\n",
        module.FIELD_REVIEW: json.dumps({"expected_selected_field_names": FIELDS})
        .encode("utf-8"),
        "02_PARAMETERS_USER_PROVIDED.txt": b"SYNTHETIC_TEST_ONLY",
    }
    transport = {
        "schema": "transport_manifest_v1", "listed_member_count": len(members),
        "files": [{"path": name, "bytes": len(data),
                   "sha256_transport_only": digest(data)}
                  for name, data in members.items()],
    }
    package = base / "synthetic_handoff.zip"
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as outer:
        for name, data in members.items():
            outer.writestr(name, data)
        outer.writestr(module.PACKAGE_MANIFEST, json.dumps(transport))
    daily = base / "daily"
    daily.mkdir()
    exceptions = {"records": [{"permno": "1", "dlycaldt": "2019-11-01",
                                "codes": ["PERMNO_DATE_DUPLICATE"]}]}
    summary = {"scope": "ENGINEERING_ONLY", "duplicate_key_group_count": 1,
               "duplicate_extra_row_count": 1}
    exception_path = daily / "m5_daily_segment_exceptions_private.json"
    summary_path = daily / "m5_daily_segment_summary_safe.json"
    exception_path.write_text(json.dumps(exceptions), encoding="utf-8")
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    manifest = {"saved_file_sha256": {
        exception_path.name: module.sha(exception_path),
        summary_path.name: module.sha(summary_path),
    }}
    (daily / "m5_daily_segment_manifest_private.json").write_text(
        json.dumps(manifest), encoding="utf-8")
    return package, bound, daily


def test_synthetic_full_intake_saved_recompute_and_tamper() -> None:
    rows = [
        row(disseqnbr="1", disdeclaredt="2019-10-30", siccd="multi\nline"),
        row(disseqnbr="2"),
        row(disseqnbr="2", siccd="2000"),
        row(disseqnbr="2", siccd="2000"),
        row(permno="2", disexdt="2019-11-02", disseqnbr="0", distype="N/A",
            dispermno="N/A", disdivamt="2"),
        row(permno="2", disexdt="2019-11-02", disseqnbr="0", distype="N/A",
            dispermno="N/A", disdivamt="3"),
    ]
    with tempfile.TemporaryDirectory(dir=ROOT / "artifacts",
                                     prefix="m5-dist-synthetic-") as temp:
        base = Path(temp)
        package, bound, daily = fixture_package(base, rows, blank_line=True)
        out = base / "output"
        result = module.run(package, out, bound, daily, expected_rows=len(rows),
                            expected_ids=2, synthetic_test_only=True)
        assert result["scope"] == "SYNTHETIC_TEST_ONLY"
        assert result["structural_issue_codes"] == []
        assert result["status"] == "PASS_STRUCTURE_ONLY"
        assert result["event_key_review_status"] == "REVIEW_REQUIRED"
        assert result["row_count"] == 6
        assert result["permno_disexdt_groups_with_multiple_sequence_numbers"] == 1
        assert result["candidate_three_field_key_duplicate_groups"] == 2
        assert result["candidate_three_field_key_extra_rows"] == 3
        assert result["candidate_duplicate_group_classes"] == {
            "non_header_field_conflict": 1,
            "provisional_header_only_variant": 1,
        }
        assert result["distype_counts_literal"] == {"CD": 4, "N/A": 2}
        assert result["other_date_outside_exdate_window_counts"] == {"disdeclaredt": 1}
        assert result["dispermno_non_decimal_or_special_row_count"] == 2
        comparison = result["limited_daily_duplicate_comparison"]
        assert comparison["daily_duplicate_group_denominator"] == 1
        assert comparison["matched_candidate_event_key_groups"] == 0
        assert comparison["matched_uncertain_key_groups"] == 1
        assert comparison["distinct_candidate_sequence_count_equal_daily_multiplicity_groups"] == 1
        assert module.verify_output(out) == result
        with pytest.raises(module.IntakeError):
            module.run(package, out, bound, daily, expected_rows=len(rows),
                       expected_ids=2, synthetic_test_only=True)
        with (out / module.INDEX).open("ab") as stream:
            stream.write(b" ")
        with pytest.raises(module.IntakeError):
            module.verify_output(out)


def test_synthetic_missing_field_outside_id_bad_date_and_unknown_sequence() -> None:
    fields = [x for x in FIELDS if x != "disdivamt"]
    rows = [row(permno="3", disexdt="2019-12-31", disseqnbr="X"),
            row(disexdt="2020-01-02", diswhat="unused")]
    with tempfile.TemporaryDirectory(dir=ROOT / "artifacts",
                                     prefix="m5-dist-synthetic-") as temp:
        base = Path(temp)
        package, bound, daily = fixture_package(base, rows, header=fields)
        out = base / "output"
        result = module.run(package, out, bound, daily, expected_rows=len(rows),
                            expected_ids=2, synthetic_test_only=True)
        assert result["status"] == "STRUCTURE_REVIEW_REQUIRED"
        assert {"SELECTED_FIELDS_MISSING", "PERMNO_OUTSIDE_BOUND_SET",
                "DISEXDT_OUTSIDE_WINDOW"}.issubset(result["structural_issue_codes"])
        assert result["sequence_uncertain_row_count"] == 1
        assert module.verify_output(out) == result


def test_synthetic_empty_csv_and_upload_mismatch() -> None:
    with tempfile.TemporaryDirectory(dir=ROOT / "artifacts",
                                     prefix="m5-dist-synthetic-") as temp:
        base = Path(temp)
        package, bound, daily = fixture_package(base, [])
        out = base / "output"
        result = module.run(package, out, bound, daily, expected_rows=0,
                            expected_ids=2, synthetic_test_only=True)
        assert result["status"] == "STRUCTURE_REVIEW_REQUIRED"
        assert "EMPTY_CSV" in result["structural_issue_codes"]
        assert module.verify_output(out) == result
        bound.write_bytes(b"1\n3\n")
        with pytest.raises(module.IntakeError):
            module.verify_output(out)


def test_synthetic_zip_traversal_rejected() -> None:
    with tempfile.TemporaryDirectory(dir=ROOT / "artifacts",
                                     prefix="m5-dist-synthetic-") as temp:
        bad = Path(temp) / "bad.zip"
        with zipfile.ZipFile(bad, "w") as archive:
            archive.writestr("../escape.csv", "SYNTHETIC_TEST_ONLY")
        with pytest.raises(module.IntakeError):
            module.checked_zip(bad)


def test_synthetic_sequence_normalization_keeps_zero_and_literal_na() -> None:
    with tempfile.TemporaryDirectory(dir=ROOT / "artifacts",
                                     prefix="m5-dist-synthetic-") as temp:
        base = Path(temp)
        data = base / "sample.csv"
        data.write_bytes(csv_bytes([row(disseqnbr="000", distype="N/A")]))
        index = base / "index.jsonl"
        summary, grouping = module.analyze_csv(data, {"1"}, FIELDS, index,
                                               expected_rows=1)
        assert summary["status"] == "PASS_STRUCTURE_ONLY"
        assert summary["distype_counts_literal"] == {"N/A": 1}
        assert next(iter(grouping["three_key_groups"]))[2] == "0"


def test_synthetic_real_identity_gate_and_saved_path_escape() -> None:
    with tempfile.TemporaryDirectory(dir=ROOT / "artifacts",
                                     prefix="m5-dist-synthetic-") as temp:
        base = Path(temp)
        package, bound, daily = fixture_package(base, [row()])
        out = base / "real_rejected"
        with pytest.raises(module.IntakeError, match="REAL_SOURCE_BINDING_MISMATCH"):
            module.run(package, out, bound, daily, expected_rows=1,
                       expected_ids=2, synthetic_test_only=False)
        assert not out.exists()
        out = base / "synthetic"
        module.run(package, out, bound, daily, expected_rows=1,
                   expected_ids=2, synthetic_test_only=True)
        manifest_path = out / module.MANIFEST
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["saved_file_sha256"]["../escape"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with pytest.raises(module.IntakeError, match="OUTPUT_FILE_PATH_INVALID"):
            module.verify_output(out)


def test_synthetic_upload_format_differences_and_set_failure() -> None:
    local = module.upload_format(b"1\n2\n")
    other = module.upload_format(b"2\r\n1\r\n")
    assert set(local["ids"]) == set(other["ids"])
    assert local["sha256"] != other["sha256"]
    assert local["line_endings"] == "LF" and other["line_endings"] == "CRLF"
    with pytest.raises(module.IntakeError, match="UPLOAD_IDENTIFIER_DUPLICATED"):
        module.upload_format(b"1\n1\n")
