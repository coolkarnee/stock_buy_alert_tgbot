import unittest

import pandas as pd

from scanner.breadth import BREADTH_TREND_SESSIONS, breadth_trend, highs_lows_read


class BreadthTrendTests(unittest.TestCase):
    def test_highs_lows_read_uses_longest_period(self):
        self.assertEqual(
            highs_lows_read({
                "1W": {"highs": 1218, "lows": 5049},
                "1Y": {"highs": 144, "lows": 854},
            }),
            "Read (1Y): more new lows than highs",
        )

    def test_trend_waits_for_two_sessions_and_labels_one_session_unavailable(self):
        history = pd.DataFrame({"20-day MA": [36.7]})
        self.assertEqual(BREADTH_TREND_SESSIONS, 7)
        self.assertEqual(
            breadth_trend(history, ["20-day MA"]),
            ["Trend unavailable (1/7 trading sessions recorded; need at least 2)."],
        )

    def test_partial_history_is_explicitly_provisional(self):
        history = pd.DataFrame({"20-day MA": [30.0, 36.0]})
        lines = breadth_trend(history, ["20-day MA"])
        self.assertEqual(lines[0], "Trend (last 2/7 trading sessions; provisional):")
        self.assertEqual(lines[1], "20-day MA: 30 > 36 (+6 pts, going up)")

    def test_full_trend_uses_latest_seven_sessions(self):
        history = pd.DataFrame({"20-day MA": list(range(20, 29))})
        lines = breadth_trend(history, ["20-day MA"])
        self.assertEqual(lines[0], "Trend (last 7/7 trading sessions):")
        self.assertEqual(lines[1], "20-day MA: 22 > 23 > 24 > 25 > 26 > 27 > 28 (+6 pts, going up)")


if __name__ == "__main__":
    unittest.main()
