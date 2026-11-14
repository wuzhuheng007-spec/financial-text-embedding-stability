"""Synthetic time and unit gates for later numerical-surprise work."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from macro_tech_expectation import calculate_surprise  # noqa: E402


class ExpectationGateTests(unittest.TestCase):
    VERIFIED_BINDING = dict(
        actual_metric="headline_cpi_u_mom_sa",
        consensus_metric="headline_cpi_u_mom_sa",
        actual_period="2026-10",
        consensus_period="2026-10",
        consensus_source_url="https://example.org/synthetic-consensus",
    )

    def test_missing_consensus_stays_uncomputed(self) -> None:
        result = calculate_surprise(
            actual="0.4", actual_unit="percent_mom_sa",
            pre_release_consensus=None, consensus_unit=None,
            consensus_as_of=None, release_at=None,
        )
        self.assertEqual(result["status"], "NOT_COMPUTED_MISSING_ACTUAL_OR_CONSENSUS")
        self.assertIsNone(result["value"])

    def test_pre_release_consensus_is_subtracted(self) -> None:
        result = calculate_surprise(
            actual="0.4", actual_unit="percent_mom_sa",
            pre_release_consensus="0.3", consensus_unit="percent_mom_sa",
            consensus_as_of="2026-11-09T16:00:00-05:00",
            release_at="2026-11-10T08:30:00-05:00",
            **self.VERIFIED_BINDING,
        )
        self.assertEqual(result, {"status": "COMPUTED", "value": "0.1",
                                  "unit": "percent_mom_sa"})

    def test_late_consensus_and_unit_change_are_rejected(self) -> None:
        common = dict(actual="0.4", actual_unit="percent_mom_sa",
                      pre_release_consensus="0.3", consensus_unit="percent_mom_sa",
                      consensus_as_of="2026-11-10T09:00:00-05:00",
                      release_at="2026-11-10T08:30:00-05:00",
                      **self.VERIFIED_BINDING)
        with self.assertRaisesRegex(ValueError, "before the release"):
            calculate_surprise(**common)
        common["consensus_as_of"] = "2026-11-09T16:00:00-05:00"
        common["consensus_unit"] = "percent_yoy_unadjusted"
        with self.assertRaisesRegex(ValueError, "units must match"):
            calculate_surprise(**common)

    def test_nonfinite_values_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "finite numbers"):
            calculate_surprise(
                actual="NaN", actual_unit="percent_mom_sa",
                pre_release_consensus="0.3", consensus_unit="percent_mom_sa",
                consensus_as_of="2026-11-09T16:00:00-05:00",
                release_at="2026-11-10T08:30:00-05:00",
                **self.VERIFIED_BINDING,
            )

    def test_metric_period_and_source_binding_are_required(self) -> None:
        common = dict(actual="0.4", actual_unit="percent_mom_sa",
                      pre_release_consensus="0.3", consensus_unit="percent_mom_sa",
                      consensus_as_of="2026-11-09T16:00:00-05:00",
                      release_at="2026-11-10T08:30:00-05:00",
                      **self.VERIFIED_BINDING)
        for change, expected in (
            ({"consensus_metric": "core_cpi_mom_sa"}, "metrics must match"),
            ({"consensus_period": "2026-09"}, "periods must match"),
            ({"consensus_source_url": None}, "HTTPS source URL"),
        ):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, expected):
                calculate_surprise(**(common | change))


if __name__ == "__main__":
    unittest.main()
