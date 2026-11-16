import unittest

from .protocol import label_window_status, news_count_or_missing, proxy_eligible_from


class ProtocolTests(unittest.TestCase):
    def test_announcement_after_friday_waits_through_next_full_session(self):
        days = ["2025-05-23", "2025-05-27", "2025-05-28", "2025-05-29"]
        self.assertEqual(proxy_eligible_from("2025-05-23", days), "2025-05-28")
        self.assertEqual(proxy_eligible_from("2025-05-24", days), "2025-05-28")

    def test_cross_boundary_is_excluded(self):
        days = ["2026-03-30", "2026-03-31", "2026-04-01", "2026-04-02"]
        self.assertEqual(label_window_status("2026-03-30", 2, days), "CROSSES_SPLIT_BOUNDARY")

    def test_unseen_news_is_missing_not_zero(self):
        self.assertIsNone(news_count_or_missing([], coverage_complete=False))
        self.assertEqual(news_count_or_missing([], coverage_complete=True), 0)
        self.assertEqual(news_count_or_missing(["event-1", "event-1"], coverage_complete=True), 1)


if __name__ == "__main__":
    unittest.main()
