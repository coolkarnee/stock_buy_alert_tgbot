import csv
import unittest
from concurrent.futures import Future
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from jobs import live_monitor, performance, swing_performance
from config import settings
from scanner import live_engine
from scanner.alert_history import first_daily_alerts


class EpRadarTests(unittest.TestCase):
    def setUp(self):
        mode = patch.object(settings, "OPENING_RANGE_BREAKOUT_MODE", "close")
        mode.start()
        self.addCleanup(mode.stop)
        self.start = datetime.now(live_engine.IST).replace(hour=9, minute=15, second=0, microsecond=0)
        self.history = pd.DataFrame({
            "timestamp": pd.date_range(self.start - timedelta(days=300), periods=270),
            "open": 101.0, "high": 104.0, "low": 70.0, "close": 100.0,
            "volume": 2500000.0,
        })
        self.history.loc[self.history.index[-1], "low"] = 102.0
        self.history.loc[self.history.index[-5], "close"] = 90.0
        self.state = live_engine.build_state("TEST", self.history, None)

    def candles(self, count=62, volume=0):
        return pd.DataFrame({
            "timestamp": pd.date_range(self.start, periods=count, freq="min"),
            "open": 104.6, "high": [105.0] * 60 + [111.0] * (count - 60),
            "low": 104.0, "close": [104.0] * 60 + [110.0] * (count - 60),
            "volume": volume,
        })

    def test_all_five_ranges_trigger_on_completed_close_when_radar_filters_pass(self):
        live_engine.update_intraday(self.state, self.candles(), self.start + timedelta(minutes=61))
        self.assertEqual(set(self.state["ep_orb_breakouts"]), {3, 5, 15, 30, 60})
        self.assertIsNone(self.state["ep_quote"])
        for breakout in self.state["ep_orb_breakouts"].values():
            self.assertEqual(breakout["price"], 110)
            self.assertEqual(breakout["range_high"], 105)
            self.assertEqual(breakout["candle_time"], (self.start + timedelta(minutes=60)).isoformat())

    def test_radar_requires_price_above_ema21_price_floor_and_turnover(self):
        with self.subTest("ema21_gate"):
            state = live_engine.build_state("TEST", self.history, None)
            state["ema21"] = 200.0
            live_engine.update_intraday(state, self.candles(), self.start + timedelta(minutes=61))
            self.assertEqual(state["ep_orb_breakouts"], {})

        with self.subTest("price_floor_gate"):
            state = live_engine.build_state("TEST", self.history, None)
            bars = self.candles()
            bars["high"] = [20.0] * 60 + [21.0] * 2
            bars["close"] = [19.0] * 60 + [20.0] * 2
            live_engine.update_intraday(state, bars, self.start + timedelta(minutes=61))
            self.assertEqual(state["ep_orb_breakouts"], {})

        with self.subTest("turnover_gate"):
            state = live_engine.build_state("TEST", self.history, None)
            with patch.object(settings, "MIN_TURNOVER_CR", 1e9):
                live_engine.update_intraday(state, self.candles(), self.start + timedelta(minutes=61))
            self.assertEqual(state["ep_orb_breakouts"], {})

        with self.subTest("turnover_gate_can_pass_later"):
            state = live_engine.build_state("TEST", self.history, None)
            bars = self.candles()
            bars.loc[60, "close"] = 110.0
            bars.loc[60, "volume"] = 0.0
            bars.loc[61, ["high", "close"]] = [112.0, 111.0]
            bars.loc[61, "volume"] = 2_000_000.0
            with patch.object(settings, "MIN_TURNOVER_CR", 24.8):
                live_engine.update_intraday(state, bars, self.start + timedelta(minutes=62))
            self.assertEqual(set(state["ep_orb_breakouts"]), {3, 5, 15, 30, 60})
            self.assertEqual(state["ep_orb_breakouts"][3]["price"], 111.0)
            self.assertEqual(
                state["ep_orb_breakouts"][3]["candle_time"],
                (self.start + timedelta(minutes=61)).isoformat(),
            )

    def test_not_before_breakout_minute_completes_and_no_duplicate_breakouts(self):
        bars = self.candles()
        live_engine.update_intraday(self.state, bars, self.start + timedelta(minutes=60, seconds=59))
        self.assertEqual(self.state["ep_orb_breakouts"], {})
        live_engine.update_intraday(self.state, bars, self.start + timedelta(minutes=61))
        self.assertIn(30, self.state["ep_orb_breakouts"])
        first = dict(self.state["ep_orb_breakouts"])
        live_engine.update_intraday(self.state, bars, self.start + timedelta(minutes=62))
        self.assertEqual(self.state["ep_orb_breakouts"], first)

    def test_each_range_can_break_independently_on_one_minute_close(self):
        for window in live_engine.EP_ORB_WINDOWS:
            with self.subTest(window=window):
                state = live_engine.build_state("TEST", self.history, None)
                bars = self.candles()
                bars["high"] = 105.0
                bars["close"] = 104.0
                bars.loc[window, ["high", "close"]] = [111.0, 110.0]
                live_engine.update_intraday(state, bars, self.start + timedelta(minutes=window + 1))
                self.assertIn(window, state["ep_orb_breakouts"])
                self.assertEqual(state["ep_orb_breakouts"][window]["price"], 110.0)

    def test_gap_boundary_wick_touch_prior_day_and_missing_opening_data_do_not_trigger(self):
        for mode in ("gap_equal", "wick_only", "close_equal", "prior_day", "missing_first", "missing_range"):
            with self.subTest(mode=mode):
                state = live_engine.build_state("TEST", self.history, None)
                bars = self.candles()
                if mode == "gap_equal":
                    bars["open"] = 104.5
                elif mode == "wick_only":
                    bars["close"] = 104.0
                elif mode == "close_equal":
                    bars["close"] = 105.0
                elif mode == "prior_day":
                    bars["timestamp"] -= timedelta(days=1)
                elif mode == "missing_first":
                    bars = bars.iloc[1:]
                elif mode == "missing_range":
                    bars = bars.drop(index=1)
                live_engine.update_intraday(state, bars, self.start + timedelta(minutes=62))
                self.assertEqual(state["ep_orb_breakouts"], {})

    def test_spot_mode_uses_live_price_without_waiting_for_breakout_close(self):
        bars = self.candles()
        bars["close"] = 104.0
        now = self.start + timedelta(minutes=60)
        with patch.object(settings, "OPENING_RANGE_BREAKOUT_MODE", "spot"):
            live_engine.update_intraday(self.state, bars, now)
            self.assertEqual(self.state["ep_orb_breakouts"], {})
            for price in (104.0, 105.0):
                live_engine.update_ep_radar_spot(self.state, {"last_price": price}, now)
                self.assertEqual(self.state["ep_orb_breakouts"], {})
            live_engine.update_ep_radar_spot(self.state, {"last_price": 110.0}, now)
            self.assertEqual(set(self.state["ep_orb_breakouts"]), {3, 5, 15, 30, 60})
            first = dict(self.state["ep_orb_breakouts"])
            live_engine.update_ep_radar_spot(self.state, {"last_price": 112.0}, now)
            self.assertEqual(self.state["ep_orb_breakouts"], first)
        for breakout in first.values():
            self.assertEqual(breakout["orb_mode"], "spot")
            self.assertEqual(breakout["price"], 110.0)
            self.assertNotIn("candle_time", breakout)
        text = live_monitor.fmt_ep_orb_alert(self.state, 3, first[3])
        self.assertIn("Spot Price Rs 110.00", text)
        self.assertIn("ORB confirmation: spot", text)
        self.assertNotIn("Breakout Close", text)
        self.assertNotIn("1m candle start", text)

    def test_close_mode_ignores_live_cross_and_labels_completed_close(self):
        bars = self.candles()
        live_engine.update_intraday(self.state, bars, self.start + timedelta(minutes=60))
        live_engine.update_ep_radar_spot(
            self.state, {"last_price": 110.0}, self.start + timedelta(minutes=60),
        )
        self.assertEqual(self.state["ep_orb_breakouts"], {})
        live_engine.update_intraday(self.state, bars, self.start + timedelta(minutes=61))
        text = live_monitor.fmt_ep_orb_alert(self.state, 3, self.state["ep_orb_breakouts"][3])
        self.assertIn("Breakout Close Rs 110.00", text)
        self.assertIn("ORB confirmation: close", text)
        self.assertNotIn("1m candle start:", text)

    def test_spot_mode_waits_for_completed_range_and_verified_gap(self):
        with patch.object(settings, "OPENING_RANGE_BREAKOUT_MODE", "spot"):
            live_engine.update_ep_radar_spot(
                self.state, {"last_price": 110.0}, self.start + timedelta(minutes=3),
            )
            self.assertEqual(self.state["ep_orb_breakouts"], {})
            bars = self.candles()
            live_engine.update_intraday(self.state, bars, self.start + timedelta(minutes=2, seconds=59))
            live_engine.update_ep_radar_spot(
                self.state, {"last_price": 110.0}, self.start + timedelta(minutes=2, seconds=59),
            )
            self.assertEqual(self.state["ep_orb_breakouts"], {})
            live_engine.update_intraday(self.state, bars, self.start + timedelta(minutes=3))
            live_engine.update_ep_radar_spot(
                self.state, {"last_price": 110.0}, self.start + timedelta(minutes=3),
            )
            self.assertEqual(set(self.state["ep_orb_breakouts"]), {3})

    def test_spot_mode_preserves_quality_filters_and_uses_live_volume(self):
        for gate in ("ema21", "price", "turnover", "invalid_price", "invalid_volume"):
            with self.subTest(gate=gate), patch.object(settings, "OPENING_RANGE_BREAKOUT_MODE", "spot"):
                state = live_engine.build_state("TEST", self.history, None)
                now = self.start + timedelta(minutes=60)
                live_engine.update_intraday(state, self.candles(), now)
                quote = {"last_price": 110.0, "volume": 0.0}
                if gate == "ema21":
                    state["ema21"] = 110.0
                elif gate == "price":
                    state["ep_radar_highs"] = {3: 19.0}
                    state["ema21"] = 10.0
                    quote["last_price"] = settings.MIN_PRICE
                elif gate == "turnover":
                    state["turnover_prev49_volume"] = 0.0
                elif gate == "invalid_price":
                    quote["last_price"] = float("nan")
                else:
                    quote["volume"] = float("inf")
                live_engine.update_ep_radar_spot(state, quote, now)
                self.assertEqual(state["ep_orb_breakouts"], {})
                if gate == "turnover":
                    quote["volume"] = 200_000_000.0
                    live_engine.update_ep_radar_spot(state, quote, now)
                    self.assertEqual(set(state["ep_orb_breakouts"]), {3, 5, 15, 30, 60})

    def test_30m_range_respects_completion_and_confirmation_mode(self):
        for mode in ("close", "spot"):
            with self.subTest(mode=mode), patch.object(settings, "OPENING_RANGE_BREAKOUT_MODE", mode):
                state = live_engine.build_state("TEST", self.history, None)
                bars = self.candles()
                bars.loc[30, ["high", "close"]] = [111.0, 110.0]
                before = self.start + timedelta(minutes=29, seconds=59)
                live_engine.update_intraday(state, bars, before)
                live_engine.update_ep_radar_spot(state, {"last_price": 110.0}, before)
                self.assertNotIn(30, state["ep_orb_breakouts"])
                formed = self.start + timedelta(minutes=30)
                live_engine.update_intraday(state, bars, formed)
                live_engine.update_ep_radar_spot(state, {"last_price": 110.0}, formed)
                if mode == "close":
                    self.assertNotIn(30, state["ep_orb_breakouts"])
                    live_engine.update_intraday(state, bars, formed + timedelta(minutes=1))
                breakout = state["ep_orb_breakouts"][30]
                self.assertEqual(breakout["price"], 110.0)
                self.assertEqual(breakout["range_high"], 105.0)
                self.assertEqual(breakout["orb_mode"], mode)
                self.assertIn("| 30m", live_monitor.fmt_ep_orb_alert(state, 30, breakout))

    def run_monitor(self, gap_only, orb_mode="close", wick_only=False):
        bars = self.candles(volume=0 if gap_only else 100000)
        if wick_only:
            bars["close"] = 104.0
        universe = pd.DataFrame([{"instrument_key": "NSE_EQ|TEST", "trading_symbol": "TEST"}])
        market_feed = {"ltpc": {"ltp": 110}}
        if not gap_only:
            market_feed.update({
                "vtt": 4000000,
                "marketOHLC": {"ohlc": [{"interval": "1d", "open": 104.6, "high": 110, "low": 104.6}]},
            })
        feed = {"feeds": {"NSE_EQ|TEST": {"fullFeed": {"marketFullFeed": market_feed}}}}
        start = self.start

        class Clock(datetime):
            current = start
            reads = 0

            @classmethod
            def now(cls, timezone=None):
                cls.reads += 1
                return cls.current + timedelta(microseconds=cls.reads)

        class Executor:
            def __init__(self, **kwargs):
                pass

            def submit(self, function, *args):
                future = Future()
                future.set_result(function(*args))
                return future

            def shutdown(self, **kwargs):
                pass

        def advance(seconds):
            Clock.current += timedelta(minutes=1)
            if Clock.current >= start + timedelta(minutes=65):
                Clock.current = start.replace(hour=15, minute=20)

        with TemporaryDirectory() as directory:
            log = Path(directory) / "alerts.csv"
            with (patch.object(settings, "OPENING_RANGE_BREAKOUT_MODE", orb_mode),
                  patch.object(live_monitor, "datetime", Clock),
                  patch.object(live_engine, "datetime", Clock),
                  patch.object(live_monitor.time, "sleep", side_effect=advance),
                  patch.object(live_monitor.time, "monotonic", side_effect=lambda: Clock.current.timestamp()),
                  patch.object(live_monitor, "Path", return_value=log),
                  patch.object(live_monitor, "SIGNAL_HISTORY_FILE", Path(directory) / "history.csv"),
                  patch.object(live_monitor, "ThreadPoolExecutor", Executor),
                  patch.object(live_monitor, "load_nse_equities", return_value=universe),
                  patch.object(live_monitor, "load_instruments", return_value=[]),
                  patch.object(live_monitor, "full_market_quotes", return_value={}),
                  patch.object(live_monitor, "send_breadth_alerts"),
                  patch.object(live_monitor, "historical_many",
                               return_value={"NSE_EQ|TEST": ("TEST", self.history, None)}),
                  patch.object(live_monitor, "intraday_minutes", return_value=bars),
                  patch.object(live_monitor, "get_sector", return_value="Refineries"),
                  patch.object(live_monitor.upstox_client, "MarketDataStreamerV3") as streamer,
                  patch.object(live_monitor, "send") as send,
                  patch("builtins.print")):
                callbacks = {}
                streamer.return_value.on.side_effect = lambda event, callback: callbacks.update({event: callback})
                streamer.return_value.connect.side_effect = lambda: callbacks["message"](feed)
                live_monitor.run()
                messages = [call.args[0] for call in send.call_args_list]
            with log.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            history = live_monitor.SignalHistory(Path(directory) / "history.csv")
            history.extend(rows)
            self.assertEqual(len(history.events), len(rows))
        return rows, messages

    def test_monitor_spot_mode_delivers_each_range_once_without_breakout_close(self):
        rows, messages = self.run_monitor(gap_only=True, orb_mode="spot", wick_only=True)
        self.assertEqual([row["alert_type"] for row in rows],
                         ["EP_ORB_3M", "EP_ORB_5M", "EP_ORB_15M", "EP_ORB_30M", "EP_ORB_60M"])
        radar = [text for text in messages if "EP RADAR ORB BUY" in text]
        self.assertEqual(len(radar), 5)
        self.assertTrue(all("ORB confirmation: spot" in text for text in radar))
        self.assertTrue(all("Spot Price Rs 110.00" in text for text in radar))
        self.assertTrue(all(row["alert_price"] == "110.00" for row in rows))

    def test_gap_only_monitor_delivers_each_timeframe_once_despite_failing_ep_and_momentum(self):
        rows, messages = self.run_monitor(gap_only=True)
        self.assertEqual([row["alert_type"] for row in rows],
                         ["EP_ORB_3M", "EP_ORB_5M", "EP_ORB_15M", "EP_ORB_30M", "EP_ORB_60M"])
        self.assertEqual(sum("EP RADAR ORB BUY" in text for text in messages), 5)
        self.assertTrue(all(row["alert_price"] == "110.00" for row in rows))
        self.assertTrue(all(row["sector"] == "Refineries" for row in rows))
        radar = [text for text in messages if "EP RADAR ORB BUY" in text]
        for count, text in enumerate(radar, start=1):
            self.assertIn(f"Alert History: {count} today | {count} in last 7 days", text)

    def test_ep_momentum_and_radar_alerts_remain_independent_and_first_ep_is_entry(self):
        rows, messages = self.run_monitor(gap_only=False)
        types = [row["alert_type"] for row in rows]
        self.assertEqual(types.count("EP"), 1)
        self.assertEqual(types.count("BUY"), 1)
        self.assertEqual(sum(kind.startswith("EP_ORB_") for kind in types), 5)
        self.assertTrue(any("MOMENTUM BUY - TEST" in text for text in messages))
        self.assertTrue(any("EPISODIC PIVOT BUY - TEST" in text for text in messages))
        selected = first_daily_alerts(rows)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["alert_type"], "EP")
        self.assertEqual(selected[0]["alert_price"], "104.00")


class FirstEntryPerformanceTests(unittest.TestCase):
    def test_first_entry_uses_timestamp_not_price_type_or_file_order(self):
        rows = [
            {"symbol": "TEST", "alert_time_ist": "2026-10-16T10:00:00+05:30", "alert_price": "90", "alert_type": "BUY"},
            {"symbol": "TEST", "alert_time_ist": "2026-10-16T09:16:00+05:30", "alert_price": "106", "alert_type": "EP"},
            {"symbol": "TEST", "alert_time_ist": "2026-10-17T09:19:00+05:30", "alert_price": "110", "alert_type": "EP_ORB_3M"},
        ]
        self.assertEqual([row["alert_price"] for row in first_daily_alerts(rows)], ["106", "110"])
        rows[0]["alert_time_ist"] = "2026-10-16T09:15:00+05:30"
        self.assertEqual(first_daily_alerts(rows)[0]["alert_type"], "BUY")

    def test_ties_are_stable_and_timezone_is_indian_trading_day(self):
        rows = [
            {"symbol": "TEST", "alert_time_ist": "2026-10-16T20:00:00Z", "alert_price": "100"},
            {"symbol": "TEST", "alert_time_ist": "2026-10-17T01:30:00+05:30", "alert_price": "110"},
        ]
        self.assertEqual(first_daily_alerts(rows), rows[:1])

    def test_daily_report_uses_first_price_but_counts_all_events(self):
        today = datetime.now(live_engine.IST).date().isoformat()
        rows = [
            {"alert_date": today, "symbol": "TEST", "alert_time_ist": f"{today}T10:00:00+05:30",
             "alert_price": "120", "alert_type": "BUY", "sector": "Refineries"},
            {"alert_date": today, "symbol": "TEST", "alert_time_ist": f"{today}T09:16:00+05:30",
             "alert_price": "100", "alert_type": "EP", "sector": "Refineries"},
            {"alert_date": today, "symbol": "TEST", "alert_time_ist": f"{today}T09:20:00+05:30",
             "alert_price": "115", "alert_type": "EP_ORB_3M", "sector": "Refineries"},
        ]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            artifact_dir = root / "artifacts"
            artifact_dir.mkdir()
            with (artifact_dir / "live_alerts.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            with (patch.object(performance, "ARTIFACT_DIR", artifact_dir),
                  patch.object(performance, "load_nse_equities", return_value=pd.DataFrame({
                      "trading_symbol": ["TEST"], "instrument_key": ["KEY"]})),
                  patch.object(performance, "current_day_daily", return_value=pd.DataFrame({
                      "timestamp": [today], "close": [110]})) as daily,
                  patch.object(performance, "intraday_minutes", return_value=pd.DataFrame({
                      "timestamp": [f"{today}T09:17:00+05:30"], "high": [125]})),
                  patch.object(performance, "send") as send,
                  patch("builtins.print")):
                performance.run()
            with (artifact_dir / "performance.csv").open(newline="", encoding="utf-8") as handle:
                result = list(csv.DictReader(handle))
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["alert_price"], "100.00")
            self.assertEqual(result[0]["close_move_pct"], "10.00")
            self.assertEqual(result[0]["post_alert_high_move_pct"], "25.00")
            daily.assert_called_once()
            report = send.call_args.args[0]
            self.assertIn("3 signal events | 1 first-entry stock trades", report)
            self.assertIn("3 alerts | 1 Momentum BUY | 1 EP | 1 distinct stocks | 1 EP radar ORB", report)

    def test_swing_merges_existing_and_new_signals_using_earliest_entry(self):
        def record(kind, time, price):
            return {"Alert ID": kind, "Ticker Name": "TEST", "Date Time": f"2026-10-16T{time}+05:30",
                    "Buy Price": price, "Entry Day SL": "95", "Alert Type": kind, "Sector": "Refineries"}
        existing = record("BUY", "10:00:00", "120")
        earliest = record("EP", "09:16:00", "100")
        orb = record("EP_ORB_3M", "09:20:00", "115")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with (patch.object(swing_performance, "_read_existing", return_value={"BUY": existing}),
                  patch.object(swing_performance, "_read_current_alerts", return_value=[orb, earliest]),
                  patch.object(swing_performance, "ARTIFACT_DIR", root),
                  patch.object(swing_performance, "OUTPUT_FILE", root / "swing.csv"),
                  patch.object(swing_performance, "load_nse_equities", return_value=pd.DataFrame({
                      "trading_symbol": ["TEST"], "instrument_key": ["KEY"]})),
                  patch.object(swing_performance, "_evaluate_alert",
                               side_effect=lambda alert, key: {**alert, "Status": "Trade open"}) as evaluate,
                  patch.object(swing_performance, "_write_excel") as excel,
                  patch.object(swing_performance, "_send_exit_alerts"),
                  patch.object(swing_performance, "_send_excel_report"),
                  patch("builtins.print")):
                swing_performance.run()
            evaluate.assert_called_once()
            self.assertEqual(evaluate.call_args.args[0]["Buy Price"], "100")
            frame = excel.call_args.args[0]
            self.assertEqual(len(frame), 1)
            self.assertEqual(frame.iloc[0]["Alert Type"], "EP")


if __name__ == "__main__":
    unittest.main()
