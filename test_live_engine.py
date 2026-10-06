import unittest
from concurrent.futures import Future
from datetime import datetime, timedelta
from unittest.mock import patch

import pandas as pd

import live_monitor
from config import settings
from scanner import live_data, live_engine


class MomentumTests(unittest.TestCase):
    def setUp(self):
        self.history = pd.DataFrame({
            "timestamp": pd.date_range("2025-01-01", periods=270, tz=live_engine.IST),
            "open": 101.0, "high": 104.0, "low": 70.0,
            "close": 100.0, "volume": 1000000.0,
        })
        self.state = live_engine.build_state("TEST", self.history, None)
        self.state["orb_highs"] = {3: 105.0}
        self.quote = {
            "last_price": 110.0, "volume": 4000000.0,
            "ohlc": {"open": 100.0, "high": 110.0, "low": 100.0},
        }
        self.clock = patch.object(live_engine, "_market_minutes", return_value=(30, None, None))
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def test_all_conditions_buy_without_dry_up(self):
        result = live_engine.evaluate(self.state, self.quote)
        self.assertTrue(result["buy"], result["checks"])
        self.assertFalse(result["dry_up"])
        self.assertEqual(result["orb_windows"], [3])

    def test_orb_is_mandatory(self):
        self.state["orb_highs"] = {}
        self.assertFalse(live_engine.evaluate(self.state, self.quote)["buy"])

    def test_orb_requires_strict_break(self):
        self.state["orb_highs"] = {3: 110.0}
        self.assertFalse(live_engine.evaluate(self.state, self.quote)["checks"]["orb"])

    def test_any_orb_window_can_qualify(self):
        with patch.object(live_engine, "_market_minutes", return_value=(60, None, None)):
            for window in live_engine.ORB_WINDOWS:
                with self.subTest(window=window):
                    self.state["orb_highs"] = {window: 105.0}
                    self.assertTrue(live_engine.evaluate(self.state, self.quote)["buy"])

    def test_pdc_breakout_does_not_require_pdh(self):
        self.state["prev_high"] = 120.0
        self.assertTrue(live_engine.evaluate(self.state, self.quote)["buy"])

    def test_breakout_buffer_is_strict(self):
        self.quote["last_price"] = self.state["prev_close"] * 1.005
        self.assertFalse(live_engine.evaluate(self.state, self.quote)["checks"]["breakout"])

    def test_turnover_boundary_is_strict(self):
        self.quote["volume"] = 30 * 1e7 / self.quote["last_price"]
        self.assertFalse(live_engine.evaluate(self.state, self.quote)["checks"]["liquidity"])

    def test_price_floor_is_strict(self):
        self.quote["last_price"] = 20.0
        self.assertFalse(live_engine.evaluate(self.state, self.quote)["checks"]["price_floor"])

    def test_green_day_is_required(self):
        self.quote["last_price"] = self.state["prev_close"]
        self.assertFalse(live_engine.evaluate(self.state, self.quote)["checks"]["momentum"])

    def test_first30_volume_can_be_only_volume_confirmation(self):
        self.state["avg_volume"] = 1e9
        self.state["red_max_volume"] = 1e9
        self.state["first30_volume"] = self.state["avg_volume10"] + 1
        result = live_engine.evaluate(self.state, self.quote)
        self.assertLess(result["rvol"], 1.3)
        self.assertFalse(result["pocket_pivot"])
        self.assertTrue(result["buy"])

    def test_pocket_pivot_can_be_only_volume_confirmation(self):
        self.state["avg_volume"] = 1e9
        self.assertTrue(live_engine.evaluate(self.state, self.quote)["buy"])

    def test_rvol_threshold_is_strict(self):
        self.state["avg_volume"] = self.quote["volume"] / (1.3 * 30 / 375)
        self.state["red_max_volume"] = 1e9
        self.assertFalse(live_engine.evaluate(self.state, self.quote)["checks"]["volume_surge"])

    def test_ipo_exemptions_and_listing_high(self):
        self.state = live_engine.build_state("IPO", self.history.iloc[:10], None)
        self.state.update({"orb_highs": {3: 105}, "year_low": 100, "prior_tr13": 0, "ema21": 200})
        result = live_engine.evaluate(self.state, self.quote)
        self.assertTrue(result["buy"], result["checks"])
        self.state["year_high"] = 130
        self.assertFalse(live_engine.evaluate(self.state, self.quote)["checks"]["near_high"])

    def test_ema_exemption_ends_after_listing_day21(self):
        self.state.update({"history_days": 20, "is_ipo": True, "ema21": 200})
        self.assertTrue(live_engine.evaluate(self.state, self.quote)["checks"]["trend"])
        self.state["history_days"] = 21
        self.assertFalse(live_engine.evaluate(self.state, self.quote)["checks"]["trend"])

    def test_non_ipo_strength_atr_and_high_filters(self):
        for field, value, check in [("year_low", 100, "strength"), ("prior_tr13", 0, "volatility"),
                                     ("year_high", 140, "near_high")]:
            with self.subTest(check=check):
                state = dict(self.state, **{field: value})
                self.assertFalse(live_engine.evaluate(state, self.quote)["checks"][check])

    def test_extension_optional_and_market_context_informational(self):
        self.state.update({"ema10": 50, "market_above_ema": False})
        self.assertTrue(live_engine.evaluate(self.state, self.quote)["buy"])
        with patch.object(settings, "LIVE_BLOCK_EXTENDED", True):
            self.assertFalse(live_engine.evaluate(self.state, self.quote)["buy"])

    def test_no_evaluation_outside_session(self):
        for minutes in (2, 375, 400):
            with patch.object(live_engine, "_market_minutes", return_value=(minutes, None, None)):
                self.assertIsNone(live_engine.evaluate(self.state, self.quote))

    def test_invalid_daily_ohlc_blocked(self):
        self.quote["ohlc"]["low"] = 0
        self.assertIsNone(live_engine.evaluate(self.state, self.quote))

    def test_completed_opening_candles_and_first30_volume(self):
        start = datetime.now(live_engine.IST).replace(hour=9, minute=15, second=0, microsecond=0)
        candles = pd.DataFrame({
            "timestamp": pd.date_range(start, periods=60, freq="min"),
            "high": list(range(100, 160)), "volume": 100,
        })
        self.state["orb_highs"] = {}
        live_engine.update_intraday(self.state, candles, start + timedelta(minutes=2))
        self.assertFalse(self.state["orb_highs"])
        live_engine.update_intraday(self.state, candles, start + timedelta(minutes=30))
        self.assertEqual(self.state["orb_highs"], {3: 102, 5: 104, 15: 114, 30: 129})
        self.assertEqual(self.state["first30_volume"], 3000)
        live_engine.update_intraday(self.state, candles, start + timedelta(minutes=60))
        self.assertEqual(self.state["orb_highs"][60], 159)

    def test_missing_opening_candle_cannot_establish_orb(self):
        start = datetime.now(live_engine.IST).replace(hour=9, minute=15, second=0, microsecond=0)
        candles = pd.DataFrame({"timestamp": pd.date_range(start + timedelta(minutes=1), periods=60, freq="min"),
                                "high": 100, "volume": 100})
        self.state["orb_highs"] = {}
        live_engine.update_intraday(self.state, candles, start + timedelta(minutes=61))
        self.assertFalse(self.state["orb_highs"])

    def test_completed_history_excludes_today(self):
        today = datetime.now(live_engine.IST).replace(hour=0, minute=0, second=0, microsecond=0)
        candles = pd.DataFrame({"timestamp": [today - timedelta(days=1), today],
                                "open": 100, "high": 104, "low": 90, "close": [100, 999], "volume": 100})
        self.assertEqual(live_engine.build_state("IPO", candles, None)["prev_close"], 100)

    def test_v3_feed_parsing_and_alert_format(self):
        feed = {"fullFeed": {"marketFullFeed": {
            "ltpc": {"ltp": 110, "ltt": "12345"}, "vtt": "4000000",
            "marketOHLC": {"ohlc": [{"interval": "1d", "open": 100, "high": 110, "low": 100}]},
        }}}
        quote = live_monitor.extract_quote(feed)
        self.assertEqual(quote["last_price"], 110)
        self.assertEqual(quote["volume"], 4000000)
        result = live_engine.evaluate(self.state, quote)
        self.assertTrue(result["buy"])
        message = live_monitor.fmt_alert("BUY", result)
        self.assertIn("MOMENTUM BUY", message)
        self.assertIn("ORB break: 3m", message)
        self.assertIn("warning only", message)
        self.assertIn("NIFTYSMLCAP250", message)

    def test_intraday_api_endpoint_and_candle_parsing(self):
        with patch.object(live_data, "_request_json", return_value={"data": {"candles": [
                ["2026-10-02T09:15:00+05:30", 100, 105, 99, 101, 200, 0]]}}) as request:
            candles = live_data.intraday_minutes("NSE_EQ|TEST")
            self.assertEqual(candles.high.iloc[0], 105)
            self.assertIn("NSE_EQ%7CTEST/minutes/1", request.call_args.args[0])

    def test_monitor_dispatches_once_after_background_data_is_ready(self):
        start = datetime(2026, 10, 2, 9, 15, tzinfo=live_engine.IST)
        candles = pd.DataFrame({"timestamp": pd.date_range(start, periods=60, freq="min"),
                                "high": 105, "volume": 100})
        universe = pd.DataFrame([{"instrument_key": "NSE_EQ|TEST", "trading_symbol": "TEST"}])
        feed = {"feeds": {"NSE_EQ|TEST": {"fullFeed": {"marketFullFeed": {
            "ltpc": {"ltp": 110}, "vtt": 4000000,
            "marketOHLC": {"ohlc": [{"interval": "1d", "open": 100, "high": 110, "low": 100}]},
        }}}}}

        class Clock(datetime):
            current = start + timedelta(minutes=60)

            @classmethod
            def now(cls, timezone=None):
                return cls.current

        class ImmediateExecutor:
            def __init__(self, **kwargs):
                pass

            def submit(self, function, *args):
                future = Future()
                future.set_result(function(*args))
                return future

            def shutdown(self, **kwargs):
                pass

        def advance(seconds):
            Clock.current += timedelta(hours=2)

        with (patch.object(live_monitor, "datetime", Clock),
              patch.object(live_engine, "datetime", Clock),
              patch.object(live_monitor.time, "sleep", side_effect=advance),
              patch.object(live_monitor, "ThreadPoolExecutor", ImmediateExecutor),
              patch.object(live_monitor, "load_nse_equities", return_value=universe),
              patch.object(live_monitor, "find_smallcap250_key", side_effect=RuntimeError("unavailable")),
              patch.object(live_monitor, "historical_many", return_value={"NSE_EQ|TEST": ("TEST", self.history, None)}),
              patch.object(live_monitor, "intraday_minutes", return_value=candles) as intraday,
              patch.object(live_monitor.upstox_client, "MarketDataStreamerV3") as streamer,
              patch.object(live_monitor, "send") as send):
            callbacks = {}
            streamer.return_value.on.side_effect = lambda event, callback: callbacks.update({event: callback})
            streamer.return_value.connect.side_effect = lambda: callbacks["message"](feed)
            live_monitor.run()
            send.assert_called_once()
            self.assertIn("MOMENTUM BUY - TEST", send.call_args.args[0])
            intraday.assert_called_once_with("NSE_EQ|TEST")
            streamer.return_value.disconnect.assert_called_once()


if __name__ == "__main__":
    unittest.main()