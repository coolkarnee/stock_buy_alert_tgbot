from urllib.parse import quote

import pandas as pd

from scanner.upstox import BASE, _request_json, load_instruments


def find_smallcap250_key():
    matches = [
        instrument for instrument in load_instruments()
        if instrument.get("segment") == "NSE_INDEX"
        and instrument.get("instrument_type") == "INDEX"
        and str(instrument.get("trading_symbol", "")).upper() == "NIFTY SMLCAP 250"
    ]
    if len(matches) != 1:
        raise RuntimeError(
            "Expected exactly one Upstox NSE_INDEX NIFTY SMLCAP 250; "
            f"found {len(matches)}."
        )
    return matches[0]["instrument_key"]


def intraday_minutes(instrument_key):
    key = quote(instrument_key, safe="")
    payload = _request_json(f"{BASE}/v3/historical-candle/intraday/{key}/minutes/1")
    candles = payload.get("data", {}).get("candles", [])
    frame = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["timestamp", "high", "volume"])
