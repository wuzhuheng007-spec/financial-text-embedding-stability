"""Synthetic checks for bounded news sampling; no network or article data."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = REPO / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


acquire = load_script("sample_fnspid_news.py")
profile = load_script("profile_fnspid_sample.py")


def small_chunks(payload: bytes, size: int):
    return (payload[i:i + size] for i in range(0, len(payload), size))


def test_quoted_multiline_article_is_one_logical_record(tmp_path: Path):
    payload = (
        'Date,Article_title,Stock_symbol,Url,Article\r\n'
        '2020-01-01,"A, B",AAA,https://example.test/a,"First line\r\nSecond, line"\r\n'
        '2020-01-02,Another,BBB,https://example.test/b,"Next article"\r\n'
    ).encode("utf-8")
    target = tmp_path / "sample.jsonl"
    previous_field_limit = csv.field_size_limit()
    result, meter = acquire.parse_csv_prefix(
        small_chunks(payload, 3), target, max_rows=1000, max_bodies=2,
        max_bytes=len(payload))
    rows = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert result["complete_logical_rows_scanned"] == 2
    assert result["saved_nonempty_body_rows"] == 2
    assert result["stop_reason"] == "SAVED_BODY_LIMIT"
    assert rows[0]["fields"][-1] == "First line\r\nSecond, line"
    assert rows[0]["source_row_ordinal"] == 1
    assert meter["bytes_consumed"] <= len(payload)
    assert csv.field_size_limit() == previous_field_limit


def test_sentinel_bodies_are_skipped_and_row_limit_stops(tmp_path: Path):
    payload = (
        'Article,Article_title\n'
        '0,Failed\n'
        'null,Missing\n'
        '"Useful body",Usable\n'
        '"Later body",Not scanned\n'
    ).encode("utf-8")
    target = tmp_path / "sample.jsonl"
    result, _ = acquire.parse_csv_prefix(
        [payload], target, max_rows=3, max_bodies=100,
        max_bytes=len(payload))
    rows = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert result["complete_logical_rows_scanned"] == 3
    assert result["rows_with_empty_or_sentinel_body"] == 2
    assert result["saved_nonempty_body_rows"] == 1
    assert result["stop_reason"] == "LOGICAL_ROW_LIMIT"
    assert [row["source_row_ordinal"] for row in rows] == [3]


def test_byte_and_server_range_guards():
    meter = {"bytes_consumed": 0, "prefix_sha256": hashlib.sha256(),
             "trailing_incomplete_physical_line_chars": None}
    with pytest.raises(RuntimeError, match="CLIENT_TRANSFER_LIMIT"):
        list(acquire.complete_physical_lines([b"123456"], meter, 5))
    assert meter["bytes_consumed"] == 0
    assert acquire.parse_content_range("bytes 0-4/10", 10, 5) == 5
    with pytest.raises(RuntimeError, match="RANGE_RESPONSE_MISMATCH"):
        acquire.parse_content_range("bytes 1-5/10", 10, 5)


def test_range_end_inside_quoted_record_preserves_completed_rows(tmp_path: Path):
    payload = b'Article,Article_title\n"Complete",First\n"Unfinished\n'
    target = tmp_path / "sample.jsonl"
    state = {}
    result, meter = acquire.parse_csv_prefix(
        small_chunks(payload, 4), target, max_rows=1000, max_bodies=100,
        max_bytes=len(payload), state=state)
    assert result["complete_logical_rows_scanned"] == 1
    assert result["saved_nonempty_body_rows"] == 1
    assert result["stop_reason"] == "RANGE_END_IN_PARTIAL_QUOTED_RECORD"
    assert meter["bytes_consumed"] == len(payload)
    assert len(target.read_text(encoding="utf-8").splitlines()) == 1

    malformed_state = {}
    with pytest.raises(Exception, match="unexpected end of data"):
        acquire.parse_csv_prefix(
            small_chunks(payload, 4), tmp_path / "malformed.jsonl",
            max_rows=1000, max_bodies=100, max_bytes=len(payload),
            range_is_prefix=False,
            state=malformed_state)
    assert malformed_state["result"]["complete_logical_rows_scanned"] == 1
    assert acquire.transfer_record(malformed_state["meter"], len(payload))[
        "main_bytes_consumed"] == len(payload)


def test_run_manifest_keeps_partial_counts_without_network(tmp_path: Path, monkeypatch):
    payload = b'Article,Article_title\n"Complete",First\n"Unfinished\n'
    monkeypatch.setattr(acquire, "__file__", str(tmp_path / "scripts" / "sample_fnspid_news.py"))

    class FakeHead:
        status_code = 302

        def __init__(self, total):
            self.headers = {"X-Repo-Commit": acquire.REVISION,
                            "X-Linked-Size": str(total)}

        def close(self):
            pass

    class FakeResponse:
        status_code = 206
        url = "https://example.test/fake"

        def __init__(self, total):
            self.headers = {"Content-Range": f"bytes 0-{len(payload) - 1}/{total}",
                            "Content-Length": str(len(payload))}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def iter_content(self, chunk_size):
            return small_chunks(payload, 4)

    class FakeSession:
        trust_env = True

        def __init__(self, total):
            self.total = total

        def head(self, *_args, **_kwargs):
            return FakeHead(self.total)

        def get(self, *_args, **_kwargs):
            return FakeResponse(self.total)

    prefix = acquire.run(tmp_path / "artifacts" / "prefix", max_bytes=len(payload),
                         session=FakeSession(len(payload) + 100))
    assert prefix["result"]["stop_reason"] == "RANGE_END_IN_PARTIAL_QUOTED_RECORD"
    assert prefix["result"]["complete_logical_rows_scanned"] == 1
    assert prefix["transfer"]["main_bytes_consumed"] == len(payload)
    assert prefix["sample"]["sha256"]

    full_file = acquire.run(tmp_path / "artifacts" / "full", max_bytes=len(payload),
                            session=FakeSession(len(payload)))
    assert full_file["result"]["stop_reason"] == "ERROR"
    assert full_file["result"]["complete_logical_rows_scanned"] == 1
    assert full_file["transfer"]["main_bytes_consumed"] == len(payload)
    assert full_file["result"]["error_type"] == "Error"


def test_output_must_be_new_and_ignored(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(acquire, "__file__", str(tmp_path / "scripts" / "sample_fnspid_news.py"))
    artifacts = tmp_path / "artifacts"
    existing = artifacts / "existing"
    existing.mkdir(parents=True)
    with pytest.raises(FileExistsError):
        acquire.check_output_dir(existing)
    with pytest.raises(ValueError, match="OUTPUT_MUST_BE_INSIDE_IGNORED_ARTIFACTS"):
        acquire.check_output_dir(tmp_path / "reports" / "raw")
    with pytest.raises(ValueError, match="LIMIT_EXCEEDS_APPROVED_BUDGET"):
        acquire.run(artifacts / "new", max_rows=1001)


def test_profile_has_aggregates_and_no_row_values(tmp_path: Path):
    header = ["Date", "Article_title", "Stock_symbol", "Url", "Article", "Lsa_summary"]
    rows = [
        {"source_row_ordinal": 1, "fields": ["2020-01-01 00:00:00 UTC", "Title A", "AAA",
                                               "https://example.test/a", "Synthetic article A", "Summary A"]},
        {"source_row_ordinal": 3, "fields": ["2020-01-02 00:00:00 UTC", "Title A", "AAA",
                                               "https://example.test/b", "Synthetic article B", "Summary B"]},
    ]
    sample = tmp_path / "sample.jsonl"
    sample.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "source": {"dataset": "test/FNSPID", "revision": "fixed", "target_file": "test.csv"},
        "result": {"header": header, "complete_logical_rows_scanned": 3,
                   "saved_nonempty_body_rows": 2, "stop_reason": "LOGICAL_ROW_LIMIT"},
        "sample": {"sha256": profile.sha256_file(sample)},
    }), encoding="utf-8")
    summary = profile.profile_sample(sample, manifest)
    assert summary["saved_rows"] == 2
    assert summary["complete_logical_rows_scanned"] == 3
    assert summary["duplicate_exact_nonmissing_saved_rows"]["title"]["duplicate_excess"] == 1
    assert summary["date_saved_rows"]["literal_utc_midnight_count"] == 2
    assert summary["date_saved_rows"]["publication_instant_verified"] is False
    output = json.dumps(summary)
    for private_value in ("Synthetic article A", "Title A", "https://example.test/a", "AAA"):
        assert private_value not in output
