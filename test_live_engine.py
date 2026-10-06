import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
import pandas as pd

from config import settings
from scanner import live_engine

class MomentumTests(unittest.TestCase):
    def setUp(self):
        # Increased volume to 5,000,000 so at ₹100/share, turnover is 50 Cr (Passes 20 Cr minimum)
        self.history = pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=400, tz=live_engine.IST),
            "open": 101.0, "high": 104.0, "low": 70.0,
            "close": 100.0, "volume": 5000000.0, 
        })
        self.state = live_engine.build_state("TEST", self.history, None)
        self.state["orb_highs"] = {3: 105.0}
        
        # Quote volume increased to 10M to ensure RVOL > 1.3
        self.quote = {
            "last_price": 110.0, "volume": 10000000.0,
            "ohlc": {"open": 100.0, "high": 110.0, "low": 100.0},
        }
        self.market_context = {"idxC": 100, "idxE21": 90, "idxE10": 95, "prev_idxE10": 94}
        
        self.clock = patch.object(live_engine, "_market_minutes", return_value=(30, None, None))
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def test_all_conditions_buy_generates_correct_payload(self):
        result = live_engine.evaluate(self.state, self.quote, self.market_context)
        self.assertEqual(result.get("alert_type"), "BUY")
        self.assertIn("NSE:TEST BUY", result.get("alert_message", ""))
        self.assertIn("Broke 3m ORB", result.get("alert_message", ""))
        self.assertIn("Bull Market", result.get("alert_message", ""))

    def test_orb_is_mandatory_for_buy(self):
        self.state["orb_highs"] = {}
        result = live_engine.evaluate(self.state, self.quote, self.market_context)
        self.assertNotEqual(result.get("alert_type"), "BUY")

    def test_price_floor_is_strict(self):
        self.quote["last_price"] = 19.0
        result = live_engine.evaluate(self.state, self.quote, self.market_context)
        self.assertFalse(result["checks"]["price_floor"])

    def test_gap_up_radar_triggers_on_open(self):
        self.quote["ohlc"]["open"] = 106.0  # 6% gap up from 100
        self.quote["last_price"] = 106.0
        self.quote["volume"] = 100  # Fails liquidity to intentionally block BUY, testing isolated radar
        
        with patch.object(settings, "ALERT_GAP_UP", True):
            result = live_engine.evaluate(self.state, self.quote, self.market_context)
            self.assertEqual(result.get("alert_type"), "RADAR")
            self.assertIn("Gap Up: +6.0%", result.get("alert_message", ""))

    def test_new_high_radar_triggers_when_enabled(self):
        self.quote["ohlc"]["high"] = 200.0  # Above historical 104.0
        self.quote["volume"] = 100  # Fails liquidity to isolate radar
        
        with patch.object(settings, "ALERT_NEW_HIGHS", True):
            result = live_engine.evaluate(self.state, self.quote, self.market_context)
            self.assertEqual(result.get("alert_type"), "RADAR")
            self.assertIn("New 52-Week High", result.get("alert_message", ""))

    def test_ipo_mode_bypasses_trend_and_volatility(self):
        short_history = self.history.iloc[:10]
        state = live_engine.build_state("IPO", short_history, None)
        state["orb_highs"] = {3: 105.0}
        
        result = live_engine.evaluate(state, self.quote, self.market_context)
        self.assertTrue(result["checks"]["trend"])
        self.assertTrue(result["checks"]["volatility"])
        self.assertTrue(result["checks"]["strength"])
        self.assertEqual(result.get("alert_type"), "BUY")

    def test_trade_exit_triggers_after_3_15_pm(self):
        self.state["in_trade"] = True
        self.quote["last_price"] = 90.0  # Below EMA21 (99 approx)
        self.quote["ohlc"]["low"] = 90.0
        
        # Fast forward time to 3:16 PM (361 minutes after 9:15 AM)
        with patch.object(live_engine, "_market_minutes", return_value=(361, None, None)):
            with patch.object(settings, "ENABLE_SELLS", True):
                result = live_engine.evaluate(self.state, self.quote, self.market_context)
                self.assertEqual(result.get("alert_type"), "SELL-21EMA")

if __name__ == "__main__":
    unittest.main()