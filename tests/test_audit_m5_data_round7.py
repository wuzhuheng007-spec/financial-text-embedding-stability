"""Synthetic current-file and temporal integrity checks for the M5 audit."""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_m5_data_round7.py"
SPEC = importlib.util.spec_from_file_location("audit_m5_data_round7", SCRIPT)
assert SPEC and SPEC.loader
audit_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit_module)


def write_json(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) + "\n", encoding="utf-8")
    return path


def bar(day: str) -> dict:
    stamp = datetime.fromisoformat(day).replace(tzinfo=ZoneInfo("America/New_York"))
    return {"date_et": day, "t": int(stamp.timestamp() * 1000),
            "o": 10, "h": 11, "l": 9, "c": 10.5, "v": 100,
            "vw": 10.4, "n": 2}


def fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    corpus_dir = tmp_path / "m1"
    filing_dir = corpus_dir / "filings" / "TEST"
    filing_dir.mkdir(parents=True)
    item = filing_dir / "item1_business.txt"
    item.write_text("Synthetic business text", encoding="utf-8")
    raw = filing_dir / "original_submission.txt"
    raw.write_bytes(b"<ACCEPTANCE-DATETIME>20250101090000\nSynthetic SEC fixture")
    item_hash, raw_hash = audit_module.file_sha256(item), audit_module.file_sha256(raw)
    write_json(filing_dir / "filing_metadata.json", {
        "ticker": "TEST", "cik": "0000000001", "accession": "0000000001-25-000001",
        "form": "10-K", "report_date": "2024-12-31", "filing_date": "2025-01-01",
        "raw_sec_header": {"acceptance_datetime_raw_header": "20250101090000",
                           "acceptance_datetime_eastern": "2025-01-01T09:00:00-05:00"},
        "acceptance_datetime_sec_source": "2025-01-01T14:00:00Z",
        "api_minus_raw_header_seconds": 0,
        "raw_retrieval": {"retrieved_utc": "2026-01-01T00:00:00Z"},
    })
    write_json(filing_dir / "item1_metadata.json", {
        "item1_sha256": item_hash, "source_raw_sha256": raw_hash})
    corpus = write_json(corpus_dir / "corpus_index.json", {"entries": [{
        "ticker": "TEST", "cik": "0000000001", "accession": "0000000001-25-000001",
        "status": "INCLUDED", "filing_dir": str(filing_dir),
        "item1_path": str(item), "item1_sha256": item_hash, "raw_sha256": raw_hash,
    }]})

    m4 = tmp_path / "m4"
    dates = ["2025-01-02", "2025-01-03"]
    write_json(m4 / "common_masks.json", {
        "date_order": dates, "ticker_order": ["TEST"],
        "company_price_available": [True], "complete_date_mask": [False, True],
        "observed_return_mask_TxN": [[False], [True]],
    })
    bars_path = write_json(m4 / "market" / "bars_TEST.json", {
        "ticker": "TEST", "adjusted": True,
        "adjustment_scope": "splits only; excludes dividend total return",
        "price_field": "c", "bars": [bar(day) for day in dates],
    })
    raw_price = write_json(m4 / "market" / "raw" / "bars_TEST" /
                           "page_001_attempt_01.json", {"results": []})
    write_json(raw_price.with_suffix(".receipt.json"), {
        "raw_path": str(raw_price), "provider": "Massive REST",
        "http_status": 200, "sha256": audit_module.file_sha256(raw_price),
        "retrieved_utc": "2026-01-01T00:00:00Z",
    })
    write_json(m4 / "market" / "identity_TEST.json", {
        "sec_cik": "0000000001", "as_of": "2025-12-31",
        "massive": {"cik": "0000000001", "ticker": "TEST"},
    })
    config = tmp_path / "m4_demo.yaml"
    config.write_text("synthetic: true\n", encoding="utf-8")
    write_json(m4 / "manifest.json", {
        "m4_config_sha256": audit_module.file_sha256(config),
        "inputs": [{"sha256": audit_module.file_sha256(corpus)}],
        "outputs": [{"path": str(bars_path),
                     "sha256": audit_module.file_sha256(bars_path)}],
    })
    return corpus, m4, config


def test_audit_counts_price_rows_without_treating_them_as_independent_texts(tmp_path: Path):
    corpus, m4, config = fixture(tmp_path)
    result = audit_module.audit(corpus, m4, config, text_inventory_root=tmp_path)
    assert result["counts"]["independent_item1_texts"] == 1
    assert result["counts"]["available_stock_date_price_rows"] == 2
    assert result["counts"]["financial_prediction_observations_approved"] is None
    assert result["firms"][0]["five_session_close_pair"]["close_pair_exists"] is False
    assert "Users" not in json.dumps(result)


def test_item1_byte_change_rejected(tmp_path: Path):
    corpus, m4, config = fixture(tmp_path)
    (corpus.parent / "filings" / "TEST" / "item1_business.txt").write_text(
        "altered synthetic text", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        audit_module.audit(corpus, m4, config)


def test_price_date_order_and_missing_close_rejected(tmp_path: Path):
    corpus, m4, config = fixture(tmp_path)
    bars_path = m4 / "market" / "bars_TEST.json"
    bars = json.loads(bars_path.read_text())
    bars["bars"].reverse()
    write_json(bars_path, bars)
    manifest_path = m4 / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["outputs"][0]["sha256"] = audit_module.file_sha256(bars_path)
    write_json(manifest_path, manifest)
    with pytest.raises(ValueError, match="Price dates"):
        audit_module.audit(corpus, m4, config)
    bars["bars"].reverse()
    del bars["bars"][0]["c"]
    write_json(bars_path, bars)
    manifest["outputs"][0]["sha256"] = audit_module.file_sha256(bars_path)
    write_json(manifest_path, manifest)
    with pytest.raises(ValueError, match="Missing daily-bar fields"):
        audit_module.audit(corpus, m4, config)


def test_five_session_event_coverage_is_date_only():
    dates = [f"2025-01-{day:02d}" for day in range(2, 10)]
    covered = audit_module.event_coverage(dates, "2025-01-01", 5)
    assert covered["first_post_filing_bar_date"] == "2025-01-02"
    assert covered["future_close_date"] == "2025-01-07"
    assert covered["close_pair_exists"] is True
    assert audit_module.event_coverage(dates, "2025-01-07", 5)["close_pair_exists"] is False
    assert audit_module.event_coverage(dates, "2025-01-10", 5)["close_pair_exists"] is False


def test_saved_submission_versions_check_source_hash_and_amendment(tmp_path: Path):
    cache = tmp_path / "sec"
    payload_path = write_json(cache / "submissions.bin", {
        "filings": {"recent": {
            "accessionNumber": ["0000000001-25-000001", "0000000001-25-000002"],
            "form": ["10-K", "10-K/A"],
            "reportDate": ["2024-12-31", "2024-12-31"],
            "filingDate": ["2025-01-01", "2025-02-01"],
        }}
    })
    digest = audit_module.file_sha256(payload_path)
    write_json(cache / "submissions.json", {"url": "https://data.sec.gov/submissions/test",
                                           "sha256": digest})
    filing = {"accession": "0000000001-25-000001", "report_date": "2024-12-31",
              "submissions_sources": [{"url": "https://data.sec.gov/submissions/test",
                                       "sha256": digest,
                                       "retrieved_utc": "2026-01-01T00:00:00Z"}]}
    result = audit_module.saved_submission_versions(filing, cache)
    assert result["same_period_10ka_count"] == 1
    assert len(result["same_period_rows"]) == 2
    payload_path.write_text("corrupted", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        audit_module.saved_submission_versions(filing, cache)


def test_quarterly_sources_and_derivatives_remain_separate_from_annual_items(tmp_path: Path):
    m8, m9 = tmp_path / "m8", tmp_path / "m9"
    m7 = tmp_path / "m7"
    raw = m8 / "ingest" / "TEST" / "original_submission.txt"
    raw.parent.mkdir(parents=True)
    raw.write_text("Synthetic original 10-Q", encoding="utf-8")
    write_json(m8 / "receipt.json", {"status": "PARTIAL"})
    write_json(raw.parent / "source_identity.json", {
        "ticker": "TEST", "accession": "0000000001-25-000002",
        "report_period_end": "2025-03-31", "filing_date": "2025-05-01",
        "original_submission_sha256": audit_module.file_sha256(raw),
        "status": "NEEDS_VERSION_REVIEW",
    })
    for version in ("v1", "v2"):
        path = m9 / version / "test_item2_structured_text_v1.txt"
        path.parent.mkdir(parents=True)
        path.write_text("Synthetic derivative", encoding="utf-8")
    pilot = m7 / "tenq" / "pilot_v3"
    pilot.mkdir(parents=True)
    pilot_raw = pilot / "original_submission.txt"
    pilot_raw.write_text("Synthetic AAPL 10-Q", encoding="utf-8")
    write_json(pilot / "receipt.json", {"status": "DONE_BOUNDARY_PROGRAM_PASS_HUMAN_PENDING"})
    write_json(pilot / "selection_ledger.json", {"selected": {
        "response": {"sha256": audit_module.file_sha256(pilot_raw)},
        "raw_header": {"accession": "0000000002-25-000001"},
        "submission_row": {"accessionNumber": "0000000002-25-000001",
                           "reportDate": "2024-12-31", "filingDate": "2025-01-31"},
    }})
    found = audit_module.other_local_texts(m8, m9, m7)
    assert found["m7_original_10q_count"] == 1
    assert found["m7_original_10q"][0]["physical_pilot_copies"] == 1
    assert found["m8_original_10q_count"] == 1
    assert found["m9_structured_item2_distinct_derivative_count"] == 1
    assert found["m9_structured_item2_physical_copy_count"] == 2
    assert found["m9_structured_item2_derivatives"][0]["source_accession"] == "0000000001-25-000002"
    raw.write_text("Changed source bytes", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        audit_module.other_local_texts(m8, m9, m7)
