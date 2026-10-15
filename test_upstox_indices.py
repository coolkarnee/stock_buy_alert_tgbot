"""Diagnostic check for Upstox NSE index symbols and EMA history.

Run with:
    UPSTOX_ACCESS_TOKEN=... python test_upstox_indices.py

Add --all-indexes to fetch EMA values for every NSE_INDEX instrument in the
Upstox instrument master. The default checks the six indexes used by the live
market summary while still listing every available NSE index symbol.
"""

import argparse
import re

import pandas as pd

from config import settings
from scanner.indicators import ema
from scanner.live_engine import _completed
from scanner.upstox import historical_many, load_instruments


TARGETS = (
    ("Nifty", "Nifty 50"),
    ("NIFTY 500", "Nifty 500"),
    ("NIFTY MIDSML400", "Nifty Midsml 400"),
    ("NIFTY MIDCAP 150", "Nifty Midcap 150"),
    ("NIFTY SMLCAP 250", "Nifty Smlcap 250"),
    ("NIFTY MICROCAP 250", "Nifty Microcap250"),
)


def normalize(value):
    return re.sub(r"[^A-Z0-9]", "", str(value).upper())


def index_instruments(instruments):
    return sorted(
        (
            str(item.get("trading_symbol", "")),
            item.get("instrument_key", ""),
        )
        for item in instruments
        if item.get("segment") == "NSE_INDEX"
        and item.get("instrument_type") == "INDEX"
    )


def select_targets(available, all_indexes=False):
    if all_indexes:
        return available
    selected = []
    for display_name, expected_symbol in TARGETS:
        matches = [
            item for item in available
            if normalize(item[0]) == normalize(expected_symbol)
        ]
        if len(matches) != 1:
            print(
                f"NOT FOUND: {display_name} expected {expected_symbol!r}; "
                f"matches={matches or 'none'}"
            )
            continue
        selected.append(matches[0])
    return selected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--all-indexes",
        action="store_true",
        help="fetch EMA values for every Upstox NSE_INDEX instrument",
    )
    args = parser.parse_args()

    available = index_instruments(load_instruments())
    print(f"Upstox NSE_INDEX instruments found: {len(available)}")
    for symbol, key in available:
        print(f"AVAILABLE | {symbol} | {key}")

    selected = select_targets(available, all_indexes=args.all_indexes)
    if not settings.UPSTOX_ACCESS_TOKEN:
        print("EMA SKIPPED: UPSTOX_ACCESS_TOKEN is not configured.")
        return 0

    rows = pd.DataFrame(selected, columns=["trading_symbol", "instrument_key"])
    results = historical_many(rows, years=2, max_workers=settings.UPSTOX_MAX_WORKERS)
    print("\nEMA RESULTS")
    for symbol, key in selected:
        _, frame, error = results.get(key, (symbol, pd.DataFrame(), "missing"))
        if error or frame.empty:
            print(f"HISTORY FAILED | {symbol} | {key} | {error or 'empty'}")
            continue
        completed = _completed(frame)
        if len(completed) < 21:
            print(f"HISTORY TOO SHORT | {symbol} | {key} | candles={len(completed)}")
            continue
        ema10 = float(ema(completed.close, 10).iloc[-1])
        ema21 = float(ema(completed.close, 21).iloc[-1])
        print(
            f"OK | {symbol} | {key} | candles={len(completed)} "
            f"EMA10={ema10:.2f} EMA21={ema21:.2f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
