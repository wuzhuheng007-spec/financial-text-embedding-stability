"""SYNTHETIC_TEST_ONLY checks for bounded offline SQL source inspection."""

from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile

import pytest

from scripts import review_m5_distribution_sql_sources as review


def synthetic_sql(*, join: str = "INNER JOIN", codes: tuple[str, ...] = ("11", "22"),
                  extra: str = "", start: str = "2020-01-01",
                  end: str = "2026-02-28") -> bytes:
    fields = [(("id_table" if name in review.HEADER_FIELDS else
                "crsp_q_stock.stkdistributions") + "." + name)
              for name in review.EXPECTED_FIELDS]
    values = ",".join("'" + value + "'" for value in codes)
    types = ",".join("'" + value + "'" for value in review.EXPECTED_TYPES)
    statement = f"""SELECT {', '.join(fields)}
FROM crsp_q_stock.stkdistributions
{join} (
  SELECT permno, primaryexch, nasdissuno, siccd
  FROM crsp_q_stock.stksecurityinfohdr
  WHERE crsp_q_stock.stksecurityinfohdr.permno IN ({values})
) AS id_table
ON crsp_q_stock.stkdistributions.permno = id_table.permno
WHERE crsp_q_stock.stkdistributions.disexdt BETWEEN
'{start}'::date AND '{end}'::date
AND ("crsp_q_stock"."stkdistributions"."distype" = ANY (ARRAY[{types}])){extra}
"""
    return statement.encode("utf-8")


def test_exact_structure_and_literal_na():
    parsed = review.parse_sql(synthetic_sql())
    assert parsed["select_fields"] == list(review.EXPECTED_FIELDS)
    assert parsed["distribution_field_count"] == 19
    assert parsed["header_field_count"] == 3
    assert parsed["join_type"] == "INNER_JOIN_HEADER_SUBQUERY"
    assert parsed["date_start_inclusive"] == "2020-01-01"
    assert parsed["date_end_inclusive"] == "2026-02-28"
    assert parsed["distype_filter_codes"] == list(review.EXPECTED_TYPES)
    assert "N/A" in parsed["distype_filter_codes"]
    assert parsed["inline_list_count"] == 2


@pytest.mark.parametrize("changed", [
    synthetic_sql(join="LEFT JOIN"),
    synthetic_sql(extra=" AND 1=1"),
    synthetic_sql(extra=" LIMIT 1"),
    b"<html>login required</html>",
])
def test_changed_statement_or_html_never_inherits_template(changed: bytes):
    with pytest.raises(review.IntakeError):
        review.parse_sql(changed)


def test_invalid_or_duplicate_inline_code_list_rejected():
    with pytest.raises(review.IntakeError, match="SQL_CODE_LIST_INVALID"):
        review.parse_sql(synthetic_sql(codes=("11", "11")))
    with pytest.raises(review.IntakeError, match="SQL_CODE_LIST_INVALID"):
        review.parse_sql(synthetic_sql(codes=("11", "0")))


def test_upload_and_dat_format_set_order_and_bytes(tmp_path: Path):
    upload = tmp_path / "upload.txt"
    upload.write_bytes(b"11\n22\n")
    values, digest = review.upload_ids(upload)
    assert values == ["11", "22"]
    assert digest == hashlib.sha256(upload.read_bytes()).hexdigest()
    dat = tmp_path / "attachment.dat"
    dat.write_bytes(b"11\r\n22\r\n")
    parsed = review.inspect_dat(dat, values)
    assert parsed["status"] == "PARSED_LOCAL_DAT_UNATTESTED_SERVER_LINK"
    assert parsed["entry_count"] == parsed["unique_count"] == 2
    assert parsed["same_order_as_bound_txt"] is True
    assert parsed["same_set_as_bound_txt"] is True
    assert parsed["line_endings"] == "CRLF"
    dat.write_bytes(b"11\n11\n")
    duplicate = review.inspect_dat(dat, values)
    assert duplicate["status"] == "PARSED_DUPLICATE_CODES_REVIEW_REQUIRED"
    assert duplicate["entry_count"] == 2 and duplicate["unique_count"] == 1
    dat.write_bytes(b"")
    assert review.inspect_dat(dat, values)["status"] == "UNPARSEABLE_CODE_FORMAT"
    dat.write_bytes(b"\xff")
    assert review.inspect_dat(dat, values)["status"] == "UNPARSEABLE_ENCODING"


def test_upload_requires_positive_ascii_decimal_final_lf(tmp_path: Path):
    upload = tmp_path / "upload.txt"
    for invalid in (b"11\n11\n", b"0\n", b"11,22\n", b"11\n", b"\xef\xbb\xbf11\n"):
        upload.write_bytes(invalid)
        if invalid == b"11\n":
            assert review.upload_ids(upload)[0] == ["11"]
        else:
            with pytest.raises(review.IntakeError):
                review.upload_ids(upload)
    upload.write_bytes(b"11")
    with pytest.raises(review.IntakeError):
        review.upload_ids(upload)


def test_parameter_status_detects_missing_scope_evidence(tmp_path: Path):
    parameter = tmp_path / "parameters.txt"
    parsed = {"date_start_inclusive": "2020-01-01",
              "date_end_inclusive": "2026-02-28"}
    parameter.write_text("2020-01-01 2026-02-28 DisExDt DisType", encoding="utf-8")
    status = review.parameter_status(
        parameter, hashlib.sha256(parameter.read_bytes()).hexdigest(), parsed)
    assert status["date_start_present"] and status["date_end_present"]
    assert status["all_selected_field_names_present"] is False
    assert status["all_ten_distype_literals_present"] is False


def test_saved_output_rejects_safe_or_copied_source_tamper(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="m5-sql-source-test-", dir=review.ART) as directory:
        root = Path(directory)
        out = root / "result"
        out.mkdir()
        copies = out / "private_sources"
        copies.mkdir()
        originals = root / "originals"
        originals.mkdir()
        for scope in review.SCOPE:
            (copies / f"{scope}.sql").write_bytes(synthetic_sql())
            (originals / f"{scope}.sql").write_bytes(synthetic_sql())
        safe = {"schema": "synthetic_safe", "scope": "SYNTHETIC_TEST_ONLY"}
        private = {"schema": "synthetic_private", "scope": "SYNTHETIC_TEST_ONLY"}
        review.write_json_new(out / review.SAFE, safe)
        review.write_json_new(out / review.PRIVATE, private)
        review.write_json_new(out / review.REGISTRY, {
            "schema": "m5_distribution_sql_source_registry_private_v1",
            "review_code_sha256": review.sha(Path(review.__file__)),
            "helper_code_sha256": review.HELPER_SHA,
            "base_code_sha256": review.BASE_SHA,
            "sql_paths": {scope: str(originals / f"{scope}.sql") for scope in review.SCOPE},
            "dat_paths": {scope: None for scope in review.SCOPE},
            "handoff_path": str(root / "synthetic_handoff.zip"),
        })
        files = [out / name for name in (review.SAFE, review.PRIVATE, review.REGISTRY)]
        files += list(copies.iterdir())
        review.write_json_new(out / review.MANIFEST, {
            "schema": "m5_distribution_sql_source_manifest_private_v1",
            "review_code_sha256": review.sha(Path(review.__file__)),
            "helper_code_sha256": review.HELPER_SHA,
            "base_code_sha256": review.BASE_SHA,
            "saved_file_sha256": {
                str(path.relative_to(out)).replace("\\", "/"): review.sha(path)
                for path in files},
        })
        monkeypatch.setattr(review, "review_sources", lambda *args, **kwargs: (safe, private))
        assert review.verify_output(out) == safe
        original = (out / review.SAFE).read_bytes()
        (out / review.SAFE).write_bytes(original + b" ")
        with pytest.raises(review.IntakeError, match="SAVED_FILE_CHANGED"):
            review.verify_output(out)
        (out / review.SAFE).write_bytes(original)
        target = copies / "2019.sql"
        target.write_bytes(target.read_bytes() + b"ALTERED")
        with pytest.raises(review.IntakeError, match="SAVED_FILE_CHANGED"):
            review.verify_output(out)
