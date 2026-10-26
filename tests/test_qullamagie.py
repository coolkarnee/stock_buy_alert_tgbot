import unittest
from datetime import timedelta

import pandas as pd

from jobs.live_monitor import fmt_qullamagie_alert
from scanner.indicators import adx
from scanner.qullamagie_daily import build_report
from scanner.live_engine import IST


class QullamagieAlertTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame({
            "timestamp": pd.date_range(
                end=pd.Timestamp.now(tz=IST).normalize() - pd.Timedelta(days=1),
                periods=270, freq="D",
            ),
            "open": [100.0 + index * 0.2 for index in range(270)],
            "high": [105.0 + index * 0.2 for index in range(270)],
            "low": [95.0 + index * 0.2 for index in range(270)],
            "close": [100.0 + index * 0.2 for index in range(270)],
            "volume": [1_000_000.0] * 269 + [3_000_000.0],
        })
        previous_close = self.frame.loc[268, "close"]
        self.frame.loc[269, "open"] = previous_close * 1.06
        self.frame.loc[269, "close"] = previous_close * 1.08
        self.frame.loc[269, "high"] = previous_close * 1.10
        self.frame.loc[269, "low"] = previous_close * 1.04

    def test_adx14_indicates_directional_trend_and_flat_series(self):
        rising = pd.Series(range(40), dtype=float)
        rising_adx = adx(rising + 2, rising, rising + 1)
        self.assertGreater(rising_adx.iloc[-1], 90)

        flat = pd.Series([100.0] * 40)
        flat_adx = adx(flat + 1, flat - 1, flat)
        self.assertEqual(flat_adx.iloc[-1], 0)

    def test_universe_report_shows_periods_atr_adx_and_actual_turnover_sessions(self):
        report = build_report({"TEST": self.frame}, top_fraction=1.0)
        self.assertIn("TEST · EP", report)
        for label in ("Sector: NA", "Top-2% in: 1M, 3M, 6M", "1W", "1M", "3M", "6M", "12M", "ATR14", "ADX14"):
            self.assertIn(label, report)
        self.assertIn("50 sessions", report)

    def test_top_momentum_leader_is_shown_without_completed_setup(self):
        frame = self.frame.copy()
        frame["open"] = frame["close"]
        frame["high"] = frame["close"] * 1.08
        frame["low"] = frame["close"] * 0.92
        frame["volume"] = 2_000_000.0
        report = build_report({"TEST": frame}, top_fraction=1.0)
        self.assertIn("TEST · LEADER", report)
        self.assertIn("Top-2% in: 1M, 3M, 6M", report)
        self.assertIn("No High Tight Flag or EP setup yet", report)

    def test_watchlist_includes_sector_from_lookup(self):
        report = build_report(
            {"TEST": self.frame}, top_fraction=1.0,
            sector_lookup=lambda symbol: "Specialty Chemicals",
        )
        self.assertIn("Sector: Specialty Chemicals", report)

    def test_buy_alert_includes_stop_atr_adx_market_context_and_performance(self):
        result = {
            "symbol": "TEST", "qullamagie_setup": "EP", "orb_windows": [5],
            "price": 110.0, "day_low": 104.0, "qullamagie_sl_pct": 5.45,
            "adr_pct": 6.0, "turnover_cr": 12.5, "atr_pct": 5.8,
            "adx14": 28.4, "swing_moves": {"1W": 3.0, "1M": 12.0,
                                               "3M": 25.0, "6M": 44.0, "1Y": 80.0},
            "sector": "Industrials", "market_context": "Market Context: Bull Market\nNIFTY 500: Uptrending\nStocks above 200 MA: 62.0%",
        }
        message = fmt_qullamagie_alert(result)
        self.assertIn("Entry-day low SL Rs 104.00", message)
        self.assertIn("ATR14 5.80% | ADX14 28.4", message)
        self.assertIn("NIFTY 500: Uptrending", message)
        self.assertIn("12M +80.0%", message)


if __name__ == "__main__":
    unittest.main()
