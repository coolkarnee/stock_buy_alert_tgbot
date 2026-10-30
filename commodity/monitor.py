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
        mcx_count = sum(1 for row in instruments if (row.get("segment") or row.get("exchange")) == "MCX_FO")
        print(f"[MCX][INSTRUMENTS] Loaded {len(instruments)} instruments; MCX_FO rows={mcx_count}.")
        selected = contracts.select_all(instruments, as_of)
        self.contracts = {}
        self.daily = {}
        self.intraday_history = {}
        for symbol, contract in selected.items():
            key = contract["instrument_key"]
            try:
                # The current session is incomplete during the monitoring window.
                candles = self.api.daily_candles(
                    key, settings.DAILY_LOOKBACK_DAYS, as_of - timedelta(days=1)
                )
                context = indicators.daily_context(candles)
                history = self.api.thirty_minute_candles(
                    key, settings.INTRADAY_LOOKBACK_DAYS, as_of - timedelta(days=1)
                )
            except Exception as exc:
                print(f"[MCX][HISTORY][ERROR] {symbol} key={key}: {type(exc).__name__}: {exc}")
                continue
            self.contracts[symbol] = contract
            self.daily[symbol] = (candles, context)
            self.intraday_history[symbol] = history
            daily_dates = (
                f"{candles.timestamp.iloc[0]}..{candles.timestamp.iloc[-1]}" if not candles.empty else "none"
            )
            intraday_dates = (
                f"{history.timestamp.iloc[0]}..{history.timestamp.iloc[-1]}" if not history.empty else "none"
            )
            print(
                f"[MCX][HISTORY] {symbol}: daily={len(candles)} rows ({daily_dates}), "
                f"ready={context.get('ready')} trend={context.get('direction')} "
                f"cross={context.get('cross') or 'none'} alert={context.get('alert')}; "
                f"30m_history={len(history)} rows ({intraday_dates})."
            )
            alert_key = (symbol, context.get("cross"), context.get("timestamp"))
            if context.get("alert") and alert_key not in self.daily_alerted:
                print(f"[MCX][SIGNAL] {symbol}: daily EMA cross detected ({context['cross']}); sending context alert.")
                send_daily(contract, context)
                self.daily_alerted.add(alert_key)
        if not self.contracts:
            raise RuntimeError("No MCX contracts have usable candle history; see [MCX] diagnostics above.")
        self.context_date = as_of

    def run_once(self, now=None):
        now = now or datetime.now(IST)
        if not in_market_session(now):
            return
        if (
            not self.contracts
            or self.context_date != now.date()
        ):
            self.refresh(now.date())
        try:
            quotes = self.api.full_market_quotes(
                [contract["instrument_key"] for contract in self.contracts.values()]
            )
            print(f"[MCX][QUOTE] Requested {len(self.contracts)} contracts; received {len(quotes)} quotes.")
        except Exception as exc:
            print(f"[MCX][QUOTE][ERROR] {type(exc).__name__}: {exc}")
            quotes = {}
        for symbol, contract in self.contracts.items():
            daily_candles, daily = self.daily[symbol]
            key = contract["instrument_key"]
            try:
                intraday = self.api.current_day_thirty_minute_candles(key)
            except Exception as exc:
                print(f"[MCX][INTRADAY][ERROR] {symbol} key={key}: {type(exc).__name__}: {exc}")
                continue
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
                latest = intraday.timestamp.iloc[-1] if not intraday.empty else "none"
                reason = indicators.signal_diagnostic(
                    intraday, daily.get("direction", "neutral"), symbol,
                )
                print(
                    f"[MCX][SCAN] {symbol}: completed_30m={len(intraday)} latest={latest}; "
                    f"no alert: {reason}."
                )
                continue
            alert_key = (symbol, entry["timestamp"])
            if alert_key in self.intraday_alerted:
                print(f"[MCX][SCAN] {symbol}: signal at {entry['timestamp']} already sent this run.")
                continue
            quote = quotes.get(contract["instrument_key"], {})
            if not quote:
                print(
                    f"[MCX][QUOTE][MISSING] {symbol} expected key={key}; "
                    f"returned keys={list(quotes)[:8]}"
                )
            day_ohlc = quote.get("ohlc") or {}
            if not day_ohlc:
                day_ohlc = {"low": intraday["low"].min(), "high": intraday["high"].max()}
            stop = indicators.stop_loss(entry["entry"], day_ohlc["low"], day_ohlc["high"], entry["direction"])
            print(
                f"[MCX][SIGNAL] {symbol}: {entry['strategy']} {entry['direction']} "
                f"at {entry['timestamp']} entry={entry['entry']:.2f}; sending Telegram alert."
            )
            send_intraday(contract, entry, daily["direction"], stop)
            self.intraday_alerted.add(alert_key)

    def run_forever(self):
        print(
            f"[MCX][START] Monitoring {', '.join(settings.COMMODITIES)} "
            f"from {settings.MARKET_OPEN} to {settings.MARKET_CLOSE}; "
            f"poll interval={settings.POLL_SECONDS}s."
        )
        while True:
            try:
                self.run_once()
            except Exception as exc:
                print(f"[MCX][POLL][ERROR] {type(exc).__name__}: {exc}")
            time_module.sleep(settings.POLL_SECONDS)
