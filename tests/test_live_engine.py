import csv
import unittest
from concurrent.futures import Future
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from jobs import live_monitor
from config import settings
from scanner import live_data, live_engine
from scanner.market_context import classify_nifty500, market_context
from scanner.alerts import tradingview_chart_url


class MomentumTests(unittest.TestCase):
    def setUp(self):
        sector_lookup = patch.object(live_monitor, "get_sector", return_value="Specialty Chemicals")
        self.sector_lookup = sector_lookup.start()
        self.addCleanup(sector_lookup.stop)
        self.history = pd.DataFrame({
            "timestamp": pd.date_range("2025-01-01", periods=270, tz=live_engine.IST),
            "open": 101.0, "high": 104.0, "low": 70.0,
            # 2.5M shares at ~Rs 100 keeps 50-day turnover above MIN_TURNOVER_CR (20 Cr).
            "close": 100.0, "volume": 2500000.0,
        })
        self.state = live_engine.build_state("TEST", self.history, None)
        self.state["prev_low"] = 109.0
        self.state["swing_move_references"]["1W"] = 90.0
        self.state["orb_highs"] = {3: 105.0}
        self.state["orb_close_breaks"] = {3: True}
        self.quote = {
            "last_price": 110.0, "volume": 4000000.0,
            "ohlc": {"open": 100.0, "high": 110.0, "low": 100.0},
        }
        self.ep_start = datetime.now(live_engine.IST).replace(hour=9, minute=15, second=0, microsecond=0)
        self.ep_candles = pd.DataFrame({
            "timestamp": [self.ep_start],
            "open": [104.6], "high": [110.0], "low": [104.6],
            "close": [110.0], "volume": [3000000.0],
        })
        self.clock = patch.object(live_engine, "_market_minutes", return_value=(30, None, None))
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def test_nifty_context_requires_price_and_both_emas_to_agree_with_slope(self):
        rising = {"ema10": 100.0, "ema20": 98.0, "prev_ema10": 99.0, "prev_ema20": 97.0}
        falling = {"ema10": 100.0, "ema20": 102.0, "prev_ema10": 101.0, "prev_ema20": 103.0}
        self.assertEqual(classify_nifty500(rising, 110.0)[0], "Uptrending")
        self.assertEqual(classify_nifty500(falling, 90.0)[0], "Downtrending")
        self.assertEqual(classify_nifty500(rising, 99.0)[0], "Chopping")
        self.assertEqual(classify_nifty500(None, 110.0)[0], "NA")

    def test_combined_market_context_uses_breadth_thresholds_and_na(self):
        category, text = market_context("Uptrending", 50.1, "2026-10-19")
        self.assertEqual(category, "Bull Market")
        self.assertIn("Stocks above 200 MA: 50.1%", text)
        self.assertIn("Stocks above 200 MA", text)

        category, text = market_context("Downtrending", 50.0)
        self.assertEqual(category, "Bear Market")
        self.assertIn("50.0% below 200 MA", text)

        category, text = market_context("Uptrending", 50.0)
        self.assertEqual(category, "Mixed Market")
        self.assertIn("Broader Bear", text)
        self.assertEqual(market_context("Chopping", 60.0)[0], "Mixed Market")

        self.assertIn("Oversold", market_context("Chopping", 20.0)[1])
        self.assertIn("Overbought", market_context("Chopping", 80.0)[1])
        category, text = market_context("NA", None)
        self.assertEqual(category, "NA")
        self.assertIn("NIFTY 500: NA", text)
        self.assertIn("Stocks above 200 MA: NA", text)
        _, text = market_context("Uptrending", None)
        self.assertIn("NIFTY 500: Uptrending", text)
        self.assertIn("Stocks above 200 MA: NA", text)
        _, text = market_context("NA", 61.2)
        self.assertIn("NIFTY 500: NA", text)
        self.assertIn("Stocks above 200 MA: 61.2%", text)

    def test_delayed_market_conclusions_are_kept_for_future_alert_context(self):
        details = live_monitor.append_market_conclusions(
            "Market Context: NA\nNIFTY 500: NA\nStocks above 200 MA: NA",
            "bear",
            "decliners lead",
            "Read (1Y): more new lows than highs",
            "200-day MA: broader bear market",
        )
        self.assertEqual(
            details,
            "Market Context: NA\n"
            "NIFTY 500: NA\n"
            "Stocks above 200 MA: NA\n"
            "Overall market: bear\n"
            "Read: decliners lead\n"
            "Read (1Y): more new lows than highs\n"
            "200-day MA: broader bear market",
        )

    def test_all_conditions_buy_without_dry_up(self):
        details = live_monitor.append_market_conclusions(
            "Market Context: Bear Market\n"
            "NIFTY 500: Downtrending (below EMA10 and EMA20; both falling)\n"
            "Stocks above 200 MA: 30.4% (Broader Bear; 69.6% below 200 MA) | as of 2026-10-23",
            "bear",
            "decliners lead",
            "Read (1Y): more new lows than highs",
            "200-day MA: broader bear market",
        )
        self.assertEqual(
            live_monitor.alert_market_context(details),
            "Market Context: Bear Market\n"
            "NIFTY 500: Downtrending (below EMA10 and EMA20; both falling)\n"
            "Stocks above 200 MA: 30.4% (Broader Bear; 69.6% below 200 MA) | as of 2026-10-23\n"
            "Today decliners lead\n"
            "Read (1Y): more new lows than highs",
        )
        self.assertIn("Overall market: bear", details)
        self.assertIn("200-day MA: broader bear market", details)

        result = live_engine.evaluate(self.state, self.quote)
        self.assertTrue(result["buy"], result["checks"])
        self.assertFalse(result["dry_up"])
        self.assertEqual(result["orb_windows"], [3])

    def test_orb_is_mandatory(self):
        self.state["orb_highs"] = {}
        self.assertFalse(live_engine.evaluate(self.state, self.quote)["buy"])

    def test_orb_requires_strict_break(self):
        self.state["orb_close_breaks"] = {3: False}
        self.assertFalse(live_engine.evaluate(self.state, self.quote)["checks"]["orb"])
        with patch.object(settings, "OPENING_RANGE_BREAKOUT_MODE", "spot"):
            self.state["orb_highs"] = {3: 110.0}
            self.assertFalse(live_engine.evaluate(self.state, self.quote)["checks"]["orb"])
            self.state["orb_highs"] = {3: 109.99}
            self.assertTrue(live_engine.evaluate(self.state, self.quote)["checks"]["orb"])

    def test_any_orb_window_can_qualify(self):
        with patch.object(live_engine, "_market_minutes", return_value=(60, None, None)):
            for window in live_engine.ORB_WINDOWS:
                with self.subTest(window=window):
                    self.state["orb_highs"] = {window: 105.0}
                    self.state["orb_close_breaks"] = {window: True}
                    self.assertTrue(live_engine.evaluate(self.state, self.quote)["buy"])

    def test_pdc_breakout_does_not_require_pdh(self):
        self.state["prev_high"] = 120.0
        self.assertTrue(live_engine.evaluate(self.state, self.quote)["buy"])

    def test_dark_red_previous_candle_uses_previous_high_for_breakout(self):
        self.state.update({
            "prev_open": 110.0,
            "prev_close": 100.0,
            "prev_high": 120.0,
            "dark_red_previous_candle": True,
        })
        result = live_engine.evaluate(self.state, self.quote)
        self.assertFalse(result["checks"]["breakout"])

        self.quote["last_price"] = 121.0
        result = live_engine.evaluate(self.state, self.quote)
        self.assertTrue(result["checks"]["breakout"])

    def test_breakout_buffer_is_strict(self):
        self.quote["last_price"] = self.state["prev_close"] * 1.005
        self.assertFalse(live_engine.evaluate(self.state, self.quote)["checks"]["breakout"])

    def test_turnover_boundary_is_strict(self):
        # Price 125 and avg volume 1.6M give exactly 20 Cr 50-day turnover.
        self.quote.update({"last_price": 125.0, "ohlc": {"open": 100.0, "high": 125.0, "low": 100.0}})
        volume = self.quote["volume"]
        self.state["turnover_prev49_close"] = 49 * 125.0
        self.state["turnover_prev49_volume"] = 50 * 1.6e6 - volume
        self.assertFalse(live_engine.evaluate(self.state, self.quote)["checks"]["liquidity"])
        self.state["turnover_prev49_volume"] += 50
        self.assertTrue(live_engine.evaluate(self.state, self.quote)["checks"]["liquidity"])

    def test_price_floor_is_strict(self):
        self.quote["last_price"] = 20.0
        self.assertFalse(live_engine.evaluate(self.state, self.quote)["checks"]["price_floor"])

    def test_green_day_is_required(self):
        self.quote["last_price"] = self.state["prev_close"]
        self.assertFalse(live_engine.evaluate(self.state, self.quote)["checks"]["momentum"])

    def _disable_other_volume_confirmations(self):
        # Open at 107 keeps the intraday move below 4%; 4M volume is below the 9M daily-volume rule.
        self.quote["ohlc"] = {"open": 107.0, "high": 110.0, "low": 107.0}
        self.state["rvol_prev19_volume"] = 1e12
        self.state["red_max_volume"] = 1e9
        self.state["first30_volume"] = None

    def test_first30_volume_can_be_only_volume_confirmation(self):
        self._disable_other_volume_confirmations()
        self.state["first30_volume"] = self.state["avg_volume10"] + 1
        result = live_engine.evaluate(self.state, self.quote)
        self.assertLess(result["rvol"], 1.3)
        self.assertFalse(result["pocket_pivot"])
        self.assertFalse(result["intraday_move_condition"])
        self.assertTrue(result["buy"], result["checks"])

    def test_pocket_pivot_can_be_only_volume_confirmation(self):
        self._disable_other_volume_confirmations()
        self.state["red_max_volume"] = self.quote["volume"] - 1
        result = live_engine.evaluate(self.state, self.quote)
        self.assertLess(result["rvol"], 1.3)
        self.assertTrue(result["pocket_pivot"])
        self.assertTrue(result["buy"], result["checks"])

    def test_rvol_threshold_is_strict(self):
        self._disable_other_volume_confirmations()
        volume = self.quote["volume"]
        with patch.object(settings, "MIN_RVOL", 2.0):
            # 20-day average = (prev19 + today) / 20 = volume / 2, so RVOL is exactly 2.0.
            self.state["rvol_prev19_volume"] = 9 * volume
            result = live_engine.evaluate(self.state, self.quote)
            self.assertEqual(result["rvol"], 2.0)
            self.assertFalse(result["checks"]["volume_surge"])
            self.state["rvol_prev19_volume"] = 9 * volume - 1e6
            self.assertTrue(live_engine.evaluate(self.state, self.quote)["checks"]["volume_surge"])

    def test_short_history_warmups_and_listing_high(self):
        self.state = live_engine.build_state("RECENT", self.history.iloc[:10], None)
        self.state.update({"orb_highs": {3: 105}, "orb_close_breaks": {3: True}, "year_low": 100,
                           "prior_tr13": 0, "ema21": 200, "prev_low": 109.0})
        result = live_engine.evaluate(self.state, self.quote)
        self.assertTrue(result["buy"], result["checks"])
        # 10 completed sessions: ATR uses 10 + today, turnover averages 11 sessions.
        self.assertEqual(result["atr_days"], 11)
        self.assertEqual(result["turnover_period_days"], 11)
        message = live_monitor.fmt_alert("BUY", result)
        self.assertIn("ATR 11d ", message)
        self.assertIn("Turnover 11d avg Rs ", message)
        self.assertIn("Strength: warm-up (10/63 completed sessions)", message)
        self.assertIn("ATR 11d ", live_monitor.fmt_alert("EP", result))
        self.assertIn("Perf: 1W +10.0%", live_monitor.fmt_alert("EP", result))
        short = live_engine.evaluate(live_engine.build_state("NEW", self.history.iloc[:3], None), self.quote)
        self.assertIn("Perf: NA (3 sessions)", live_monitor.fmt_alert("EP", short))
        self.assertIn("ADX14 N/A", live_monitor.fmt_alert("EP", short))
        self.state["year_high"] = 130
        self.assertFalse(live_engine.evaluate(self.state, self.quote)["checks"]["near_high"])

    def test_session_warmups_for_ema_atr_strength_and_near_high(self):
        self.state.update({
            "history_days": 20, "ema21": 200, "prior_tr13": 0,
            "year_high": 130,
            "swing_move_references": dict.fromkeys(self.state["swing_move_references"]),
        })
        self.assertTrue(live_engine.evaluate(self.state, self.quote)["checks"]["trend"])
        self.state["history_days"] = 21
        self.assertFalse(live_engine.evaluate(self.state, self.quote)["checks"]["trend"])

        self.state["history_days"] = 12
        with patch.object(settings, "MIN_ATR_PCT", 100.0):
            self.assertTrue(live_engine.evaluate(self.state, self.quote)["checks"]["volatility"])
        self.state["history_days"] = 13
        with patch.object(settings, "MIN_ATR_PCT", 100.0):
            self.assertFalse(live_engine.evaluate(self.state, self.quote)["checks"]["volatility"])

        self.state["history_days"] = 62
        result = live_engine.evaluate(self.state, self.quote)
        self.assertTrue(result["checks"]["strength"])
        self.assertFalse(result["checks"]["near_high"])
        self.state["history_days"] = 63
        result = live_engine.evaluate(self.state, self.quote)
        self.assertFalse(result["checks"]["strength"])
        self.assertTrue(result["checks"]["near_high"])

    def test_non_ipo_strength_atr_and_high_filters(self):
        no_swing_moves = dict.fromkeys(self.state["swing_move_references"])
        for field, value, check in [("swing_move_references", no_swing_moves, "strength"),
                                     ("prior_tr13", 0, "volatility"),
                                     ("year_high", 140, "near_high")]:
            with self.subTest(check=check):
                state = dict(self.state, **{field: value})
                self.assertFalse(live_engine.evaluate(state, self.quote)["checks"][check])

    def test_extension_optional_and_market_context_informational(self):
        self.state.update({"ema10": 50, "market_above_ema": False})
        self.assertTrue(live_engine.evaluate(self.state, self.quote)["buy"])
        with patch.object(settings, "LIVE_BLOCK_EXTENDED", True):
            self.assertFalse(live_engine.evaluate(self.state, self.quote)["buy"])

    def test_momentum_stock_tag(self):
        # Flat fixture closes -> flat EMAs -> no tag.
        result = live_engine.evaluate(self.state, self.quote)
        self.assertFalse(result["momentum_stock"])
        self.assertNotIn("Momentum Stock", live_monitor.fmt_alert("BUY", result))

        self.state.update({"ema10": 100.0, "ema10_prev": 99.0, "ema21": 98.0, "ema21_prev": 97.0})
        result = live_engine.evaluate(self.state, self.quote)
        self.assertTrue(result["momentum_stock"])
        self.assertEqual(live_monitor.fmt_alert("BUY", result).splitlines()[2], "Momentum Stock")
        self.assertEqual(live_monitor.fmt_alert("EP", result).splitlines()[2], "Momentum Stock")
        self.assertTrue(result["buy"], "tag must not change BUY rules")

        for update in ({"ema21_prev": 99.0},                     # EMA21 sloping down
                       {"ema10": 120.0, "ema10_prev": 119.0}):    # price below live EMA10
            with self.subTest(update=update):
                state = dict(self.state, **update)
                self.assertFalse(live_engine.evaluate(state, self.quote)["momentum_stock"])

        one_day = live_engine.build_state("NEW", self.history.iloc[:1], None)
        self.assertIsNone(one_day["ema10_prev"])
        self.assertFalse(live_engine.evaluate(one_day, self.quote)["momentum_stock"])

    def test_no_evaluation_outside_session(self):
        with patch.object(live_engine, "_market_minutes", return_value=(2, None, None)):
            result = live_engine.evaluate(self.state, self.quote)
            self.assertIsNotNone(result)
            self.assertFalse(result["buy"])
        for minutes in (375, 400):
            with patch.object(live_engine, "_market_minutes", return_value=(minutes, None, None)):
                self.assertIsNone(live_engine.evaluate(self.state, self.quote))

    def test_ep_can_evaluate_after_first_completed_minute_without_orb(self):
        self.state["orb_highs"] = {}
        live_engine.update_intraday(self.state, self.ep_candles, self.ep_start + timedelta(minutes=1))
        with patch.object(live_engine, "_market_minutes", return_value=(1, None, None)):
            result = live_engine.evaluate(self.state, self.state["ep_quote"])
        self.assertTrue(result["episodic_pivot"])
        self.assertFalse(result["buy"])
        self.assertFalse(result["checks"]["orb"])

    def test_ep_ema_and_atr_gates_activate_after_their_session_warmups(self):
        live_engine.update_intraday(self.state, self.ep_candles, self.ep_start + timedelta(minutes=1))
        self.state.update({"history_days": 12, "ema21": 200.0, "prior_tr13": 0.0})
        with patch.object(settings, "EP_MIN_ATR_PCT", 100.0):
            early = live_engine.evaluate(self.state, self.state["ep_quote"])
        self.assertEqual(early["atr_days"], 13)
        self.assertTrue(early["episodic_pivot"])

        self.state["history_days"] = 13
        with patch.object(settings, "EP_MIN_ATR_PCT", 100.0):
            atr_ready = live_engine.evaluate(self.state, self.state["ep_quote"])
        self.assertEqual(atr_ready["atr_days"], 14)
        self.assertFalse(atr_ready["episodic_pivot"])

        self.state.update({"history_days": 21, "ema21": 200.0})
        with patch.object(settings, "EP_MIN_ATR_PCT", -1.0):
            ema_ready = live_engine.evaluate(self.state, self.state["ep_quote"])
        self.assertTrue(ema_ready["ema21_ready"])
        self.assertFalse(ema_ready["episodic_pivot"])

    def test_ep_waits_for_actual_completed_opening_candle(self):
        self.quote["ohlc"]["open"] = 106.0
        self.assertFalse(live_engine.evaluate(self.state, self.quote)["episodic_pivot"])
        live_engine.update_intraday(self.state, self.ep_candles, self.ep_start + timedelta(seconds=59))
        self.assertIsNone(self.state["ep_quote"])
        self.assertFalse(live_engine.evaluate(self.state, self.quote)["episodic_pivot"])
        live_engine.update_intraday(self.state, self.ep_candles, self.ep_start + timedelta(minutes=1))
        self.assertTrue(live_engine.evaluate(self.state, self.state["ep_quote"])["episodic_pivot"])

    def test_ep_gap_threshold_is_configurable_and_strict(self):
        for threshold, opening, expected in (
            (4.5, 104.49, False), (4.5, 104.50, False), (4.5, 104.51, True),
            (6.0, 104.60, False), (6.0, 106.10, True),
        ):
            with self.subTest(threshold=threshold, opening=opening):
                candles = self.ep_candles.assign(open=opening, low=opening)
                live_engine.update_intraday(self.state, candles, self.ep_start + timedelta(minutes=1))
                with patch.object(settings, "EP_MIN_GAP_PCT", threshold):
                    result = live_engine.evaluate(self.state, self.state["ep_quote"])
                self.assertEqual(result["episodic_pivot"], expected)

    def test_ep_rejects_previous_day_missing_or_invalid_opening_candle(self):
        for candles in (
            self.ep_candles.assign(timestamp=self.ep_start - timedelta(days=1)),
            self.ep_candles.assign(timestamp=self.ep_start + timedelta(minutes=1)),
            self.ep_candles.assign(low=0),
            self.ep_candles.assign(close=float("nan")),
            self.ep_candles.assign(volume=0),
        ):
            with self.subTest(candles=candles.to_dict()):
                self.state["ep_quote"] = None
                live_engine.update_intraday(self.state, candles, self.ep_start + timedelta(minutes=2))
                self.assertIsNone(self.state["ep_quote"])

    def test_ep_keeps_existing_trend_price_and_liquidity_filters(self):
        live_engine.update_intraday(self.state, self.ep_candles, self.ep_start + timedelta(minutes=1))
        self.assertTrue(live_engine.evaluate(self.state, self.state["ep_quote"])["episodic_pivot"])
        below_ema = dict(self.state, ema21=200.0)
        self.assertFalse(live_engine.evaluate(below_ema, below_ema["ep_quote"])["episodic_pivot"])
        with patch.object(settings, "EP_MIN_ATR_PCT", 100.0):
            self.assertFalse(live_engine.evaluate(self.state, self.state["ep_quote"])["episodic_pivot"])
        with patch.object(settings, "MIN_ATR_PCT", 100.0):
            self.assertTrue(live_engine.evaluate(self.state, self.state["ep_quote"])["episodic_pivot"])
        for setting, threshold in (("MIN_PRICE", 110.0), ("MIN_TURNOVER_CR", 100.0)):
            with self.subTest(setting=setting), patch.object(settings, setting, threshold):
                self.assertFalse(live_engine.evaluate(self.state, self.state["ep_quote"])["episodic_pivot"])

    def test_invalid_daily_ohlc_blocked(self):
        self.quote["ohlc"]["low"] = 0
        self.assertIsNone(live_engine.evaluate(self.state, self.quote))

    def test_completed_opening_candles_and_first30_volume(self):
        start = datetime.now(live_engine.IST).replace(hour=9, minute=15, second=0, microsecond=0)
        candles = pd.DataFrame({
            "timestamp": pd.date_range(start, periods=60, freq="min"),
            "high": list(range(100, 160)), "close": list(range(99, 159)), "volume": 100,
        })
        self.state["orb_highs"] = {}
        live_engine.update_intraday(self.state, candles, start + timedelta(minutes=2))
        self.assertFalse(self.state["orb_highs"])
        live_engine.update_intraday(self.state, candles, start + timedelta(minutes=30))
        self.assertEqual(self.state["orb_highs"], {3: 102, 5: 104, 15: 114, 30: 129})
        self.assertTrue(self.state["orb_close_breaks"][3])
        self.assertEqual(self.state["first30_volume"], 3000)
        live_engine.update_intraday(self.state, candles, start + timedelta(minutes=60))
        self.assertEqual(self.state["orb_highs"][60], 159)

    def test_missing_first_candle_still_builds_orb_from_available_candles(self):
        start = datetime.now(live_engine.IST).replace(hour=9, minute=15, second=0, microsecond=0)
        candles = pd.DataFrame({"timestamp": pd.date_range(start + timedelta(minutes=1), periods=60, freq="min"),
                                "high": 100, "close": 99, "volume": 100})
        self.state["orb_highs"] = {}
        live_engine.update_intraday(self.state, candles, start + timedelta(minutes=61))
        self.assertEqual(self.state["orb_highs"], {3: 100, 5: 100, 15: 100, 30: 100, 60: 100})
        self.assertFalse(any(self.state["orb_close_breaks"].values()))

    def test_no_opening_candles_cannot_establish_orb(self):
        start = datetime.now(live_engine.IST).replace(hour=9, minute=15, second=0, microsecond=0)
        candles = pd.DataFrame({"timestamp": pd.date_range(start + timedelta(minutes=61), periods=5, freq="min"),
                                "high": 100, "close": 99, "volume": 100})
        self.state["orb_highs"] = {}
        live_engine.update_intraday(self.state, candles, start + timedelta(minutes=70))
        self.assertFalse(self.state["orb_highs"])

    def test_completed_history_excludes_today(self):
        today = datetime.now(live_engine.IST).replace(hour=0, minute=0, second=0, microsecond=0)
        candles = pd.DataFrame({"timestamp": [today - timedelta(days=1), today],
                                "open": 100, "high": 104, "low": 90, "close": [100, 999], "volume": 100})
        self.assertEqual(live_engine.build_state("IPO", candles, None)["prev_close"], 100)

    def test_v3_feed_parsing_and_alert_format(self):
        feed = {"fullFeed": {"marketFullFeed": {
            "ltpc": {"ltp": 110, "ltt": "12345", "iep": 105.5}, "vtt": "4000000",
            "marketOHLC": {"ohlc": [{"interval": "1d", "open": 100, "high": 110, "low": 100}]},
        }}}
        quote = live_monitor.extract_quote(feed)
        self.assertEqual(quote["last_price"], 110)
        self.assertEqual(quote["iep"], 105.5)
        self.assertEqual(quote["volume"], 4000000)
        result = live_engine.evaluate(self.state, quote)
        self.assertTrue(result["buy"])
        result["market_context"] = market_context("Uptrending", 62.5, "2026-10-19")[1]
        message = live_monitor.fmt_alert("BUY", result)
        self.assertIn("MOMENTUM BUY", message)
        self.assertIn("ORB close: 3m", message)
        self.assertIn("52W HI -", message)
        self.assertIn("ATR 14d ", message)
        expected_adx = self.state["adx14"]
        self.assertIn(f"ADX14 {expected_adx:.1f}", message)
        self.assertIn(f"ADX14 {expected_adx:.1f}", live_monitor.fmt_alert("EP", result))
        self.assertIn("Turnover 50d avg Rs ", message)
        # 1W reference 90 -> +22.2% passes the >10% 1W rule; 100-based references fail 1M+.
        self.assertIn("Strength: 1W +22.2% (>10%)", message)
        self.assertNotIn("1M +", message)
        # EP lists every available period (270 sessions: up to 1Y); ✓ only where the threshold passed.
        self.assertIn("Perf: 1W +22.2% ✓ | 1M +10.0% | 3M +10.0% | 6M +10.0% | 1Y +10.0%\n",
                      live_monitor.fmt_alert("EP", result))
        self.assertNotIn("warning only", message)
        self.assertNotIn("Live scanner only", message)
        self.assertIn("NIFTY 500", message)
        self.assertIn("Market Context: Bull Market", message)
        self.assertIn("NIFTY 500: Uptrending", message)
        self.assertIn("Stocks above 200 MA: 62.5%", message)
        self.assertIn("Stocks above 200 MA: 62.5%", live_monitor.fmt_alert("EP", result))
        radar_state = {
            "symbol": "TEST", "ep_radar_open": 106.0, "prev_close": 100.0,
            "sector": "Refineries", "prev_low": 95.0,
            "adx14": expected_adx,
            "market_context": result["market_context"],
        }
        radar_message = live_monitor.fmt_ep_orb_alert(radar_state, 3, {
            "price": 111.0, "range_high": 110.0, "orb_mode": "close",
        })
        self.assertIn("Stocks above 200 MA: 62.5%", radar_message)
        self.assertIn(f"ADX14 {expected_adx:.1f}", radar_message)
        result["sector"] = "Specialty Chemicals"
        for kind in ("BUY", "EP"):
            self.assertIn("Sector: Specialty Chemicals", live_monitor.fmt_alert(kind, result))

    def test_tradingview_chart_url_uses_nse_symbol(self):
        self.assertEqual(
            tradingview_chart_url("POONAWALLA"),
            "https://www.tradingview.com/chart/?symbol=NSE%3APOONAWALLA",
        )

    def test_intraday_api_endpoint_and_candle_parsing(self):
        with patch.object(live_data, "_request_json", return_value={"data": {"candles": [
                ["2026-10-02T09:15:00+05:30", 100, 105, 99, 101, 200, 0]]}}) as request:
            candles = live_data.intraday_minutes("NSE_EQ|TEST")
            self.assertEqual(candles.high.iloc[0], 105)
            self.assertIn("NSE_EQ%7CTEST/minutes/1", request.call_args.args[0])

    def test_monitor_dispatches_once_after_background_data_is_ready(self):
        start = datetime(2026, 10, 2, 9, 15, tzinfo=live_engine.IST)
        # First 3 minutes set a 105 opening high; later candles close above it.
        candles = pd.DataFrame({"timestamp": pd.date_range(start, periods=60, freq="min"),
                                "high": [105] * 3 + [107] * 57, "close": [104] * 3 + [106] * 57,
                                "volume": 100})
        history = self.history.copy()
        # Previous day near the entry (SL within 8%) and a 1W reference low enough for strength.
        history.loc[history.index[-1], ["open", "high", "low", "close"]] = [102.0, 104.0, 102.0, 103.0]
        history.loc[history.index[-5], "close"] = 90.0
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

        with (TemporaryDirectory() as history_directory,
              patch.object(live_monitor, "SIGNAL_HISTORY_FILE", Path(history_directory) / "history.csv"),
              patch.object(live_monitor, "datetime", Clock),
              patch.object(live_engine, "datetime", Clock),
              patch.object(live_monitor.time, "sleep", side_effect=advance),
              patch.object(live_monitor, "ThreadPoolExecutor", ImmediateExecutor),
              patch.object(live_monitor, "load_nse_equities", return_value=universe),
              patch.object(live_monitor, "load_instruments", return_value=[]),
              patch.object(live_monitor, "send_breadth_alerts") as breadth,
              patch.object(live_monitor, "historical_many", return_value={"NSE_EQ|TEST": ("TEST", history, None)}),
              patch.object(live_monitor, "intraday_minutes", return_value=candles) as intraday,
              patch.object(live_monitor.upstox_client, "MarketDataStreamerV3") as streamer,
              patch.object(live_monitor, "send") as send):
            callbacks = {}
            streamer.return_value.on.side_effect = lambda event, callback: callbacks.update({event: callback})
            streamer.return_value.connect.side_effect = lambda: callbacks["message"](feed)
            live_monitor.run()
            messages = [call.args[0] for call in send.call_args_list]
            buy_messages = [m for m in messages if "MOMENTUM BUY - TEST" in m]
            self.assertEqual(len(buy_messages), 1, messages)
            self.assertTrue(any("MARKET OPEN SUMMARY" in m for m in messages))
            self.assertTrue(any("MARKET CLOSE SUMMARY" in m for m in messages))
            breadth.assert_called_once()
            intraday.assert_called_once_with("NSE_EQ|TEST")
            streamer.return_value.disconnect.assert_called_once()

    def test_monitor_ep_uses_completed_candle_with_incomplete_websocket_quote(self):
        start = self.ep_start
        universe = pd.DataFrame([{"instrument_key": "NSE_EQ|TEST", "trading_symbol": "TEST"}])
        feed = {"feeds": {"NSE_EQ|TEST": {"fullFeed": {"marketFullFeed": {
            "ltpc": {"ltp": 110},
        }}}}}

        class Clock(datetime):
            current = start - timedelta(minutes=5)

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
            Clock.current += timedelta(seconds=seconds)
            if Clock.current >= start + timedelta(minutes=1, seconds=3):
                Clock.current = start.replace(hour=15, minute=20)

        delivered = []
        with TemporaryDirectory() as directory:
            alert_log = Path(directory) / "live_alerts.csv"
            with (patch.object(live_monitor, "datetime", Clock),
                  patch.object(live_engine, "datetime", Clock),
                  patch.object(live_monitor.time, "sleep", side_effect=advance),
                  patch.object(live_monitor, "Path", return_value=alert_log),
                  patch.object(live_monitor, "SIGNAL_HISTORY_FILE", Path(directory) / "history.csv"),
                  patch.object(live_monitor, "ThreadPoolExecutor", ImmediateExecutor),
                  patch.object(live_monitor, "load_nse_equities", return_value=universe),
                  patch.object(live_monitor, "load_instruments", return_value=[]),
                  patch.object(live_monitor, "historical_many",
                               return_value={"NSE_EQ|TEST": ("TEST", self.history, None)}),
                  patch.object(live_monitor, "intraday_minutes", return_value=self.ep_candles) as intraday,
                  patch.object(live_monitor.upstox_client, "MarketDataStreamerV3") as streamer,
                  patch.object(live_monitor, "send",
                               side_effect=lambda message, **kwargs: delivered.append((Clock.current, message)))):
                callbacks = {}
                streamer.return_value.on.side_effect = lambda event, callback: callbacks.update({event: callback})
                def connect():
                    callbacks["message"](feed)
                    Clock.current = start
                    callbacks["message"](feed)

                streamer.return_value.connect.side_effect = connect
                live_monitor.run()
                intraday.assert_called_once_with("NSE_EQ|TEST")
                streamer.return_value.disconnect.assert_called_once()
            ep_alerts = [(when, message) for when, message in delivered if "EPISODIC PIVOT BUY - TEST" in message]
            self.assertEqual(len(ep_alerts), 1, delivered)
            self.assertGreaterEqual(ep_alerts[0][0], start + timedelta(minutes=1))
            self.assertLess(ep_alerts[0][0], start + timedelta(minutes=2))
            self.assertIn("Gap +4.60%", ep_alerts[0][1])
            self.assertIn("Sector: Specialty Chemicals", ep_alerts[0][1])
            self.assertFalse(any("MOMENTUM BUY" in message for _, message in delivered))
            with alert_log.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual([row["alert_type"] for row in rows], ["EP"])
            self.assertEqual(rows[0]["sector"], "Specialty Chemicals")
            self.sector_lookup.assert_called_once_with("TEST")

    def test_monitor_confirms_preopen_ep_candidate_after_open(self):
        start = self.ep_start
        universe = pd.DataFrame([{"instrument_key": "NSE_EQ|TEST", "trading_symbol": "TEST"}])
        preopen_feed = {"feeds": {"NSE_EQ|TEST": {"fullFeed": {"marketFullFeed": {
            "ltpc": {"ltp": 100, "iep": 110},
        }}}}}
        regular_feed = {"feeds": {"NSE_EQ|TEST": {"fullFeed": {"marketFullFeed": {
            "ltpc": {"ltp": 110}, "vtt": 4000000,
            "marketOHLC": {"ohlc": [{"interval": "1d", "open": 105, "high": 110, "low": 105}]},
        }}}}}

        class Clock(datetime):
            current = start - timedelta(minutes=20)

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

        def connect():
            callbacks["message"](preopen_feed)
            Clock.current = start
            callbacks["message"](regular_feed)

        def advance(seconds):
            if Clock.current < start.replace(hour=9, minute=0):
                Clock.current = start.replace(hour=9, minute=0)
            else:
                Clock.current = start.replace(hour=15, minute=20)

        delivered = []
        with TemporaryDirectory() as directory:
            with (patch.object(live_monitor, "datetime", Clock),
                  patch.object(live_engine, "datetime", Clock),
                  patch.object(live_monitor.time, "sleep", side_effect=advance),
                  patch.object(live_monitor, "Path", return_value=Path(directory) / "live_alerts.csv"),
                  patch.object(live_monitor, "SIGNAL_HISTORY_FILE", Path(directory) / "history.csv"),
                  patch.object(live_monitor, "ThreadPoolExecutor", ImmediateExecutor),
                  patch.object(live_monitor, "load_nse_equities", return_value=universe),
                  patch.object(live_monitor, "load_instruments", return_value=[]),
                  patch.object(live_monitor, "historical_many",
                               return_value={"NSE_EQ|TEST": ("TEST", self.history, None)}),
                  patch.object(live_monitor, "intraday_minutes", return_value=self.ep_candles),
                  patch.object(live_monitor.upstox_client, "MarketDataStreamerV3") as streamer,
                  patch.object(live_monitor, "send",
                               side_effect=lambda message, **kwargs: delivered.append((Clock.current, message)))):
                callbacks = {}
                streamer.return_value.on.side_effect = lambda event, callback: callbacks.update({event: callback})
                streamer.return_value.connect.side_effect = connect
                live_monitor.run()

        ep_alerts = [(when, message) for when, message in delivered
                     if "EPISODIC PIVOT BUY - TEST" in message]
        self.assertEqual(len(ep_alerts), 1, delivered)
        self.assertEqual(ep_alerts[0][0], start)
        self.assertIn("Gap +5.00%", ep_alerts[0][1])


if __name__ == "__main__":
    unittest.main()
