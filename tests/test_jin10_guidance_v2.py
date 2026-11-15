"""Focused guidance-time and target-period contract checks."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from scripts.jin10_guidance_v2 import validate_product


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results/news_jin10_20261115/guidance_v2"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_real_v2_output_preserves_frozen_core_and_repairs_time_status():
    old_path = ROOT / "results/news_earnings_macro_20261115/company_cards.json"
    old = _read(old_path)
    v2 = _read(RESULTS / "guidance_cards_v2.json")
    coverage = _read(RESULTS / "guidance_coverage_v2.json")
    assert hashlib.sha256(old_path.read_bytes()).hexdigest() == v2["input_company_cards_sha256"]
    assert sum(len(card["metrics"]) for card in old["cards"]) == 72
    rows = {card["issuer_id"]: card["guidance"] for card in v2["cards"]}
    assert coverage["counts"]["guidance_rows"] == 19
    assert coverage["guidance_counts_are_separate_from_core_metrics"]
    assert all(row["first_available_at"] is None for issuer_rows in rows.values() for row in issuer_rows)
    assert all(row["target_period_date_status"].startswith("INFERRED_FROM_OFFICIAL") for row in rows["INTEL"])
    assert all(row["published_at"] == "2026-10-29" and row["timestamp_precision"] == "DATE_ONLY"
               for row in rows["SAMSUNG_ELECTRONICS"])
    assert all(row["published_at"] == "2026-10-28" and row["timestamp_precision"] == "DATE_ONLY"
               for row in rows["SK_HYNIX"])
    assert next(row for row in rows["INTEL"] if row["metric"] == "gross_margin")["currency"] == "NONE"
    assert coverage["counts"]["target_bounds_unknown"] == 1


@pytest.fixture
def valid_product():
    source_url = "https://issuer.example/earnings"
    source_sha = "a" * 64
    row = {
        "guidance_id": "TEST:1", "source_url": source_url, "source_sha256": source_sha,
        "source_locator": "Outlook table", "unit": "USD millions", "currency": "USD",
        "basis": "GAAP", "first_available_at": None,
        "first_available_status": "NOT_INDEPENDENTLY_OBSERVED",
        "published_at": "2026-11-01", "timestamp_precision": "DATE_ONLY",
        "target_period_start": "2026-10-01", "target_period_end": "2026-12-31",
        "target_period_date_status": "RETAINED_V1_BOUNDS_NOT_INDEPENDENTLY_RECHECKED",
        "low": 10, "high": 12, "point_estimate": 11, "formula": None,
        "input_row_ids": None,
    }
    return ({"cards": [{"issuer_id": "TEST", "guidance": [row]}]},
            {("TEST", source_url, source_sha): {"captured_local_bytes_verified": True}})


@pytest.mark.parametrize("change", [
    {"first_available_at": "2026-11-01T12:00:00Z"},
    {"target_period_end": "2026-09-30"},
    {"unit": "percent", "currency": "USD"},
    {"target_period_start": None, "target_period_date_status": "DATES_UNKNOWN"},
    {"formula": "center*1.03", "input_row_ids": None},
])
def test_rejects_time_period_currency_and_formula_errors(valid_product, change):
    product, registered = valid_product
    altered = copy.deepcopy(product)
    altered["cards"][0]["guidance"][0].update(change)
    with pytest.raises(ValueError):
        validate_product(altered, registered)
