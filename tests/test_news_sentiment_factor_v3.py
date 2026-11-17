"""Focused synthetic checks for the v3 research gates and date folds."""
from __future__ import annotations

import unittest

from scripts.news_sentiment_factor_v3.evaluate import block_interval, fit_candidate, fold_specs


def synthetic_rows(count=40, horizon=1):
    rows = []
    for i in range(count):
        date = f"2025-{5 + i // 28:02d}-{i % 28 + 1:02d}"
        rows.append({"event_id": str(i), "symbol": "SYN", "acceptance_et_date": date,
                     "entry_date": date, f"exit_{horizon}d": date, "_horizon": horizon,
                     f"return_{horizon}d": i / 1000,
                     "prior_session_open_to_close_return": i / 100,
                     "prior_session_log_volume": 10 + i / 100,
                     "finbert_F1": i / 200})
    return rows


class SentimentV3Checks(unittest.TestCase):
    def test_date_blocks_never_split_an_event(self):
        for fold in fold_specs(synthetic_rows()):
            self.assertFalse({x["event_id"] for x in fold["train"]} & {x["event_id"] for x in fold["test"]})
            self.assertLess(max(x["acceptance_et_date"] for x in fold["train"]),
                            min(x["acceptance_et_date"] for x in fold["test"]))

    def test_overlap_purge_uses_exit_before_first_test_entry(self):
        for fold in fold_specs(synthetic_rows()):
            self.assertTrue(all(x["exit_1d"] < fold["first_test_entry"] for x in fold["train"]))

    def test_gate_rejects_twenty_two_events(self):
        result, predictions = fit_candidate(synthetic_rows(22), "finbert_F1", 1, {
            "minimum_pooled_events_for_fit": 24, "minimum_distinct_event_dates_for_fit": 18,
            "minimum_train_events_per_fold": 16, "minimum_test_events_per_fold": 4})
        self.assertEqual(result["status"], "OVERALL_FIT_GATE_NOT_MET")
        self.assertEqual(predictions, [])

    def test_actual_fit_and_pairing_are_executed(self):
        result, predictions = fit_candidate(synthetic_rows(), "finbert_F1", 1, {
            "minimum_pooled_events_for_fit": 24, "minimum_distinct_event_dates_for_fit": 18,
            "minimum_train_events_per_fold": 16, "minimum_test_events_per_fold": 4})
        self.assertEqual(result["fit_calls"], 6)
        self.assertEqual(result["paired_predictions"], len(predictions))
        self.assertTrue(predictions)
        self.assertTrue(all(x["A_price_prediction"] is not None and
                            x["C_price_text_prediction"] is not None for x in predictions))

    def test_too_few_date_blocks_has_no_interval(self):
        rows = [{"acceptance_et_date": f"2025-05-{i + 1:02d}", "value": float(i)} for i in range(7)]
        receipt = block_interval(rows, lambda sample: sum(x["value"] for x in sample) / len(sample), 5, 1729)
        self.assertEqual(receipt["status"], "TOO_FEW_NONOVERLAPPING_DATE_BLOCKS")
        self.assertIsNone(receipt["interval"])


if __name__ == "__main__":
    unittest.main()
