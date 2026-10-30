import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

from commodity import contracts, indicators
from commodity.alerts import send_daily
from commodity.monitor import completed_30_minute_candles
from jobs.commodity_performance import evaluate_alert


def candles(closes, lows=None, highs=None, opens=None, start="2026-01-01"):
    closes = list(closes)
    lows = lows or closes
    highs = highs or closes
    opens = opens or closes
    return pd.DataFrame({
        "timestamp": pd.date_range(start, periods=len(closes), freq="D"),
        "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": 1, "oi": 1,
    })


class ContractTests(unittest.TestCase):
    def test_parses_upstox_expiry_epoch_milliseconds_and_fut_type(self):
        instruments = [{
            "segment": "MCX_FO", "instrument_type": "FUT",
            "trading_symbol": "GOLDTEN FUT 25 JAN 24",
            "underlying_symbol": "GOLDTEN", "instrument_key": "MCX_FO|123",
            "expiry": 1706207399000,
        }]
        selected = contracts.select_contract(instruments, "GOLDTEN", date(2024, 1, 5))
        self.assertEqual(selected["expiry"], "2024-01-25")

    def test_rolls_to_next_contract_when_front_has_fifteen_days_or_less(self):
        instruments = [
            {"segment": "MCX_FO", "instrument_type": "FUTCOM", "trading_symbol": "GOLDTEN26JAN", "instrument_key": "front", "expiry": "2026-01-20"},
            {"segment": "MCX_FO", "instrument_type": "FUTCOM", "trading_symbol": "GOLDTEN26FEB", "instrument_key": "next", "expiry": "2026-02-20"},
        ]
        selected = contracts.select_contract(instruments, "GOLDTEN", date(2026, 1, 5))
        self.assertEqual(selected["instrument_key"], "next")

    def test_selects_front_contract_when_more_than_fifteen_days_remain(self):
        instruments = [
            {"segment": "MCX_FO", "instrument_type": "FUTCOM", "trading_symbol": "CRUDEOILM26JAN", "instrument_key": "front", "expiry": "2026-01-25"},
            {"segment": "MCX_FO", "instrument_type": "FUTCOM", "trading_symbol": "CRUDEOILM26FEB", "instrument_key": "next", "expiry": "2026-02-25"},
        ]
        selected = contracts.select_contract(instruments, "CRUDEOILM", date(2026, 1, 5))
        self.assertEqual(selected["instrument_key"], "front")


class SignalTests(unittest.TestCase):
    def test_incomplete_30_minute_candle_is_excluded(self):
        frame = candles([100, 101], start="2026-01-01")
        frame["timestamp"] = pd.to_datetime([
            "2026-01-01 10:00+05:30", "2026-01-01 10:30+05:30"
        ])
        result = completed_30_minute_candles(frame, pd.Timestamp("2026-01-01 10:45+05:30").to_pydatetime())
        self.assertEqual(len(result), 1)

    def test_daily_context_requires_close_above_both_emas_and_reports_cross(self):
        values = [100] * 25 + list(range(100, 130))
        result = indicators.daily_context(candles(values))
        self.assertTrue(result["ready"])
        self.assertEqual(result["direction"], "long")

    def test_long_entry_allows_undercut_then_close_above_ema_zone(self):
        values = [100] * 25
        frame = candles(values, lows=[99] * 24 + [99.7], highs=[101] * 25, opens=[100] * 24 + [99.8])
        frame.loc[24, "close"] = 100.5
        result = indicators.intraday_entry(frame, "long")
        self.assertIsNotNone(result)

    def test_short_entry_rejects_ema_zone(self):
        values = [100] * 25
        frame = candles(values, lows=[99] * 25, highs=[101] * 24 + [100.3], opens=[100] * 24 + [100.2])
        frame.loc[24, "close"] = 99.5
        result = indicators.intraday_entry(frame, "short")
        self.assertIsNotNone(result)

    def test_stop_loss_uses_entry_day_extreme(self):
        self.assertEqual(indicators.stop_loss(110, 105, 115, "long"), {"price": 105.0, "percentage": 4.545})
        self.assertEqual(indicators.stop_loss(110, 105, 115, "short"), {"price": 115.0, "percentage": 4.545})

    def test_directional_signal_can_label_ema_pullback(self):
        values = [100] * 30
        frame = candles(values, lows=[99] * 29 + [99.7], highs=[101] * 30, opens=[100] * 29 + [99.8])
        frame.loc[29, "close"] = 100.5
        result = indicators.intraday_signal(frame, "long", "GOLDTEN")
        self.assertEqual(result["strategy"], "EMA_PULLBACK")

    def test_participation_confirmation_requires_volume_and_rising_oi_when_available(self):
        frame = candles([100] * 30, start="2026-01-01")
        enriched = indicators.add_volatility_and_trend(frame)
        current = enriched.iloc[-1].copy()
        current["volume_ratio"] = 1.3
        current["oi"] = 110
        current["oi_change"] = 10
        self.assertTrue(indicators._participation_confirmation(current)[0])

    def test_directional_breakout_requires_participation_and_is_labeled(self):
        values = [100 + index * 0.1 for index in range(40)]
        highs = [value + 0.2 for value in values]
        lows = [value - 0.2 for value in values]
        opens = values.copy()
        volumes = [100] * 39 + [150]
        oi = list(range(100, 139)) + [150]
        frame = candles(values, lows=lows, highs=highs, opens=opens)
        frame["volume"] = volumes
        frame["oi"] = oi
        frame.loc[39, "open"] = values[38]
        frame.loc[39, "close"] = highs[38] + 1
        result = indicators.intraday_signal(frame, "long", "CRUDEOILM")
        self.assertIsNotNone(result)
        self.assertEqual(result["strategy"], "VOLATILITY_BREAKOUT")

    def test_mean_reversion_is_only_allowed_in_neutral_daily_context(self):
        values = [100 + (i % 2) for i in range(35)]
        frame = candles(values, lows=values, highs=values, opens=values)
        frame.loc[34, "open"] = 95
        frame.loc[34, "low"] = 90
        frame.loc[34, "close"] = 96
        self.assertIsNone(indicators.intraday_signal(frame, "long", "NATGASMINI"))


class AlertTests(unittest.TestCase):
    @patch("commodity.alerts.record_alert")
    @patch("commodity.alerts.send")
    def test_daily_alert_uses_mcx_chart_link_and_context(self, sender, recorder):
        send_daily({"trading_symbol": "GOLDTEN26FEB", "expiry": "2026-02-20"}, {
            "direction": "long", "close": 100, "low": 98, "high": 102,
            "ema10": 99, "ema20": 98,
        })
        sender.assert_called_once()
        self.assertIn("Daily trend: long", sender.call_args.args[0])
        self.assertIn("SL: Rs 98.00", sender.call_args.args[0])
        self.assertEqual(sender.call_args.kwargs["chart_symbol"], "MCX:GOLDTEN26FEB")


class CommodityPerformanceTests(unittest.TestCase):
    def test_long_alert_reports_sl_hit_from_later_candle_low(self):
        alert = {
            "symbol": "GOLDTEN26FEB", "instrument_key": "MCX_FO|1", "expiry": "2026-02-20",
            "timeframe": "30m", "direction": "long", "alert_time_ist": "2026-10-24T10:00:00+05:30",
            "alert_price": "100", "stop_price": "98", "stop_pct": "2",
        }
        data = candles([99, 97], lows=[99, 97], highs=[101, 100], start="2026-10-24")
        data["timestamp"] = pd.to_datetime(["2026-10-24 10:30", "2026-10-24 11:00"]).tz_localize("Asia/Kolkata")
        result = evaluate_alert(alert, data)
        self.assertEqual(result["status"], "SL HIT")
        self.assertEqual(result["sl_price"], 98.0)
        self.assertEqual(result["performance_pct"], -2.0)

    def test_short_alert_remains_open_and_marks_direction_aware_return(self):
        alert = {
            "symbol": "CRUDEOILM26FEB", "instrument_key": "MCX_FO|2", "expiry": "2026-02-20",
            "timeframe": "daily", "direction": "short", "alert_time_ist": "2026-10-24T10:00:00+05:30",
            "alert_price": "100", "stop_price": "102", "stop_pct": "2",
        }
        data = candles([98], lows=[97], highs=[99], start="2026-10-24")
        data["timestamp"] = pd.to_datetime(["2026-10-24 10:30"]).tz_localize("Asia/Kolkata")
        result = evaluate_alert(alert, data)
        self.assertEqual(result["status"], "OPEN")
        self.assertEqual(result["performance_pct"], 2.0)


if __name__ == "__main__":
    unittest.main()
