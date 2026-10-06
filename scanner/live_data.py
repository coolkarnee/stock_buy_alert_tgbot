import math
from urllib.parse import quote

import pandas as pd
import yfinance as yf

from scanner.upstox import BASE, _request_json, load_instruments


def find_smallcap250_key():
    for instrument in load_instruments():
        name = str(instrument.get("trading_symbol", "")).upper().replace(" ", "")
        if instrument.get("segment") == "NSE_INDEX" and name in {"NIFTYSMLCAP250", "NIFTYSMALLCAP250"}:
            return instrument["instrument_key"]
    raise RuntimeError("Nifty Smallcap 250 was not found in the Upstox instrument master.")


def market_cap_cr(symbol):
    ticker = yf.Ticker(f"{symbol}.NS")
    info = ticker.get_info()
    value = info.get("marketCap")
    if info.get("currency") != "INR" or value is None:
        raise ValueError("INR total market capitalization is unavailable")
    value = float(value) / 1e7
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Invalid total market capitalization")
    return value


def intraday_minutes(instrument_key):
    key = quote(instrument_key, safe="")
    payload = _request_json(f"{BASE}/v3/historical-candle/intraday/{key}/minutes/1")
    candles = payload.get("data", {}).get("candles", [])
    frame = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["timestamp", "high", "volume"])