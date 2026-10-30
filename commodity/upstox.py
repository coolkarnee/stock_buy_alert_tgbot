"""Upstox market-data access used only by the commodity workflow."""

import gzip
import json
from datetime import date, timedelta
from urllib.parse import quote

import pandas as pd
import requests

from config import settings as app_settings

BASE = "https://api.upstox.com"
INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"


def _headers():
    if not app_settings.UPSTOX_ACCESS_TOKEN:
        raise RuntimeError("UPSTOX_ACCESS_TOKEN is not configured.")
    return {"Authorization": "Bearer " + app_settings.UPSTOX_ACCESS_TOKEN, "Accept": "application/json"}


def _get_json(url, params=None):
    try:
        response = requests.get(url, headers=_headers(), params=params, timeout=15)
        response.raise_for_status()
    except requests.HTTPError as exc:
        failed_response = exc.response
        request = failed_response.request if failed_response is not None else None
        print(
            f"[MCX][UPSTOX][HTTP] {request.method if request else 'GET'} "
            f"{failed_response.url if failed_response is not None else url} failed: HTTP "
            f"{failed_response.status_code if failed_response is not None else 'unknown'} | "
            f"{failed_response.text[:1000] if failed_response is not None else str(exc)}"
        )
        raise
    except requests.RequestException as exc:
        print(f"[MCX][UPSTOX][REQUEST] {url} failed: {type(exc).__name__}: {exc}")
        raise
    return response.json()


def load_instruments():
    try:
        response = requests.get(INSTRUMENTS_URL, timeout=60)
        response.raise_for_status()
        instruments = json.loads(gzip.decompress(response.content))
        if not isinstance(instruments, list):
            raise ValueError("Upstox complete instrument file did not contain a JSON list.")
        return instruments
    except Exception as exc:
        print(f"[MCX][UPSTOX][INSTRUMENTS][ERROR] {type(exc).__name__}: {exc}")
        raise


def _frame(payload):
    candles = payload.get("data", {}).get("candles", [])
    frame = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])
    if frame.empty:
        return frame
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    for column in ["open", "high", "low", "close", "volume", "oi"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)


def historical_candles(instrument_key, unit, interval, to_date, from_date=None):
    key = quote(instrument_key, safe="")
    url = f"{BASE}/v3/historical-candle/{key}/{unit}/{interval}/{to_date}"
    if from_date:
        url += f"/{from_date}"
    return _frame(_get_json(url))


def daily_candles(instrument_key, days=180, as_of=None):
    end = as_of or date.today()
    start = end - timedelta(days=days)
    return historical_candles(instrument_key, "days", 1, end, start)


def thirty_minute_candles(instrument_key, days=120, as_of=None):
    """Fetch 30-minute history in <= one-quarter V3 request windows."""
    end = as_of or date.today()
    start = end - timedelta(days=days)
    frames = []
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(chunk_start + timedelta(days=89), end)
        frames.append(historical_candles(instrument_key, "minutes", 30, chunk_end, chunk_start))
        chunk_start = chunk_end + timedelta(days=1)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)


def current_day_thirty_minute_candles(instrument_key):
    key = quote(instrument_key, safe="")
    return _frame(_get_json(f"{BASE}/v3/historical-candle/intraday/{key}/minutes/30"))


def full_market_quotes(instrument_keys):
    if not instrument_keys:
        return {}
    payload = _get_json(f"{BASE}/v3/market-quote/quotes", {"instrument_key": ",".join(instrument_keys)})
    quotes = {}
    for key, value in payload.get("data", {}).items():
        normalized_key = value.get("instrument_token") or key.replace(":", "|", 1)
        quotes[normalized_key] = value
    return quotes
