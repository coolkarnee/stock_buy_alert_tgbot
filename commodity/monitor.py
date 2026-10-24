"""Polling monitor for the isolated commodity workflow."""

import time as time_module
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from commodity import commodity_settings as settings
from commodity import contracts, indicators, upstox
from commodity.alerts import send_daily, send_intraday

IST = ZoneInfo("Asia/Kolkata")


def in_market_session(now=None):
    now = now or datetime.now(IST)
    return settings.MARKET_OPEN <= now.time() <= settings.MARKET_CLOSE


def completed_30_minute_candles(candles, now):
    """Exclude the still-forming 30-minute candle from signal decisions."""
    if candles.empty or "timestamp" not in candles:
        return candles
    timestamps = pd.to_datetime(candles["timestamp"], errors="coerce")
    if timestamps.dt.tz is None:
        timestamps = timestamps.dt.tz_localize(IST)
    else:
        timestamps = timestamps.dt.tz_convert(IST)
    cutoff = pd.Timestamp(now).tz_convert(IST) if pd.Timestamp(now).tzinfo else pd.Timestamp(now, tz=IST)
    return candles.loc[timestamps + pd.Timedelta(minutes=30) <= cutoff].copy()


class CommodityMonitor:
    def __init__(self, api=upstox):
        self.api = api
        self.contracts = {}
        self.daily = {}
        self.intraday_history = {}
        self.context_date = None
        self.daily_alerted = set()
        self.intraday_alerted = set()

    def refresh(self, as_of=None):
        as_of = as_of or datetime.now(IST).date()
        instruments = self.api.load_instruments()
        self.contracts = contracts.select_all(instruments, as_of)
        for symbol, contract in self.contracts.items():
            # The current session is incomplete during the monitoring window.
            candles = self.api.daily_candles(
                contract["instrument_key"], settings.DAILY_LOOKBACK_DAYS, as_of - timedelta(days=1)
            )
            context = indicators.daily_context(candles)
            self.daily[symbol] = (candles, context)
            self.intraday_history[symbol] = self.api.thirty_minute_candles(
                contract["instrument_key"], settings.INTRADAY_LOOKBACK_DAYS, as_of - timedelta(days=1)
            )
            alert_key = (symbol, context.get("cross"), context.get("timestamp"))
            if context.get("alert") and alert_key not in self.daily_alerted:
                send_daily(contract, context)
                self.daily_alerted.add(alert_key)
        self.context_date = as_of

    def run_once(self, now=None):
        now = now or datetime.now(IST)
        if not in_market_session(now):
            return
        if (
            not self.contracts
            or self.context_date != now.date()
            or any(
                contract["expiry"] != self.contracts[symbol]["expiry"]
                for symbol, contract in contracts.select_all(self.api.load_instruments(), now.date()).items()
            )
        ):
            self.refresh(now.date())
        quotes = self.api.full_market_quotes([contract["instrument_key"] for contract in self.contracts.values()])
        for symbol, contract in self.contracts.items():
            daily_candles, daily = self.daily[symbol]
            intraday = self.api.current_day_thirty_minute_candles(contract["instrument_key"])
            history = self.intraday_history.get(symbol)
            if history is not None and not history.empty:
                intraday = (
                    pd.concat([history, intraday], ignore_index=True)
                    .drop_duplicates("timestamp")
                    .sort_values("timestamp")
                    .reset_index(drop=True)
                )
            intraday = completed_30_minute_candles(intraday, now)
            entry = indicators.intraday_signal(intraday, daily.get("direction", "neutral"), symbol)
            if entry is None:
                continue
            alert_key = (symbol, entry["timestamp"])
            if alert_key in self.intraday_alerted:
                continue
            quote = quotes.get(contract["instrument_key"], {})
            day_ohlc = quote.get("ohlc") or {}
            if not day_ohlc:
                day_ohlc = {"low": intraday["low"].min(), "high": intraday["high"].max()}
            stop = indicators.stop_loss(entry["entry"], day_ohlc["low"], day_ohlc["high"], entry["direction"])
            send_intraday(contract, entry, daily["direction"], stop)
            self.intraday_alerted.add(alert_key)

    def run_forever(self):
        while True:
            self.run_once()
            time_module.sleep(settings.POLL_SECONDS)
