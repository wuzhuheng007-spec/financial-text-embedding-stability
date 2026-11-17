"""Focused mechanics for the private retrospective pilot."""
import tempfile
import unittest
from pathlib import Path

from scripts.news_sentiment_factor_v2.evaluate import _first_json_array_objects, correlations
from scripts.news_sentiment_factor_v2.score import lexicon_score


class SentimentV2MechanicsTests(unittest.TestCase):
    def test_price_reader_stops_before_later_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "saved.json"
            path.write_bytes(b'{"kline_list":[{"date":"20260330","open":"10"},'
                             b'{"date":"20260331","open":"11"},INVALID_LATER_BYTES]')
            rows = _first_json_array_objects(path, 2)
            self.assertEqual([r["date"] for r in rows], ["20260330", "20260331"])

    def test_too_few_events_never_produces_correlation(self):
        rows = [{"entry_date": "2026-01-01", "symbol": "A", "lm_F1": 0.1,
                 "return_1d": 0.02}]
        result = correlations(rows, "lm_F1", 1, 6, 2, 10, 1729)
        self.assertEqual(result["status"], "MINIMUM_EVENT_COUNT_NOT_MET")
        self.assertIsNone(result["pearson"])

    def test_dictionary_score_is_text_tone_not_return(self):
        result = lexicon_score("growth risk", {"GROWTH"}, {"RISK"})
        self.assertEqual(result["positive_count"], 1)
        self.assertEqual(result["negative_count"], 1)
        self.assertEqual(result["net"], 0)


if __name__ == "__main__":
    unittest.main()
