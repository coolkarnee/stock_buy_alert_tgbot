from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from scanner.indicators import ema

IST = ZoneInfo("Asia/Kolkata")
NIFTY_TRENDS = {"Uptrending", "Downtrending", "Chopping"}
MARKET_CONTEXT_CATEGORIES = (
    "Bull Market", "Bear Market", "Choppy Market", "Mixed Market", "NA", "Unknown",
)


def classify_nifty500(index_state, price):
    if not index_state or price is None or price <= 0:
        return "NA", None
    ema10 = index_state.get("ema10")
    ema20 = index_state.get("ema20")
    prev_ema10 = index_state.get("prev_ema10")
    prev_ema20 = index_state.get("prev_ema20")
    if not all(value is not None and value > 0 for value in
               (ema10, ema20, prev_ema10, prev_ema20)):
        return "NA", None
    live_ema10 = ema10 + 2 / 11 * (price - ema10)
    live_ema20 = ema20 + 2 / 21 * (price - ema20)
    ema10_up = live_ema10 > prev_ema10
    ema20_up = live_ema20 > prev_ema20
    ema10_down = live_ema10 < prev_ema10
    ema20_down = live_ema20 < prev_ema20
    if price > live_ema10 and price > live_ema20 and ema10_up and ema20_up:
        return "Uptrending", (live_ema10, live_ema20)
    if price < live_ema10 and price < live_ema20 and ema10_down and ema20_down:
        return "Downtrending", (live_ema10, live_ema20)
    return "Chopping", (live_ema10, live_ema20)


def classify_nifty500_daily(frame):
    if frame is None or frame.empty or not {"timestamp", "close"}.issubset(frame.columns):
        return "NA"
    timestamps = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce").dt.tz_convert(IST)
    history = frame.loc[timestamps.dt.date < datetime.now(IST).date()].copy()
    closes = pd.to_numeric(history["close"], errors="coerce")
    valid = closes.notna() & closes.gt(0) & closes.lt(float("inf"))
    closes = closes[valid]
    if len(closes) < 21:
        return "NA"
    ema10 = ema(closes, 10)
    ema20 = ema(closes, 20)
    price = float(closes.iloc[-1])
    if (price > ema10.iloc[-1] and price > ema20.iloc[-1]
            and ema10.iloc[-1] > ema10.iloc[-2]
            and ema20.iloc[-1] > ema20.iloc[-2]):
        return "Uptrending"
    if (price < ema10.iloc[-1] and price < ema20.iloc[-1]
            and ema10.iloc[-1] < ema10.iloc[-2]
            and ema20.iloc[-1] < ema20.iloc[-2]):
        return "Downtrending"
    return "Chopping"


def latest_200ma_breadth(path=Path("artifacts/market_breadth.csv")):
    try:
        history = pd.read_csv(path)
    except (FileNotFoundError, pd.errors.EmptyDataError, OSError, pd.errors.ParserError):
        return None, None
    if not {"date", "200-day MA"}.issubset(history.columns):
        return None, None
    dates = pd.to_datetime(history["date"], errors="coerce")
    values = pd.to_numeric(history["200-day MA"], errors="coerce")
    valid = (
        dates.notna()
        & (dates.dt.date <= datetime.now(IST).date())
        & values.between(0, 100)
    )
    if not valid.any():
        return None, None
    latest_index = dates[valid].idxmax()
    return float(values.loc[latest_index]), dates.loc[latest_index].date().isoformat()


def market_context(nifty_trend, stocks_above_200ma, breadth_date=None):
    trend = nifty_trend if nifty_trend in NIFTY_TRENDS else "NA"
    breadth = None
    try:
        value = float(stocks_above_200ma)
        if 0 <= value <= 100 and value < float("inf"):
            breadth = value
    except (TypeError, ValueError):
        pass

    if breadth is None or trend == "NA":
        category = "NA"
    elif trend == "Uptrending" and breadth > 50:
        category = "Bull Market"
    elif trend == "Downtrending" and breadth <= 50:
        category = "Bear Market"
    else:
        category = "Mixed Market"

    lines = [f"Market Context: {category}"]
    trend_descriptions = {
        "Uptrending": "above EMA10 and EMA20; both rising",
        "Downtrending": "below EMA10 and EMA20; both falling",
        "Chopping": "otherwise",
    }
    lines.append(
        f"NIFTY 500: {trend} ({trend_descriptions[trend]})"
        if trend != "NA" else "NIFTY 500: NA"
    )
    if breadth is None:
        lines.append("Stocks above 200 MA: NA")
    else:
        breadth_context = "Broader Bull" if breadth > 50 else "Broader Bear"
        below = 100 - breadth
        line = (
            f"Stocks above 200 MA: {breadth:.1f}% ({breadth_context}; "
            f"{below:.1f}% below 200 MA)"
        )
        if breadth_date:
            line += f" | as of {breadth_date}"
        if breadth <= 20:
            line += " | Oversold"
        elif breadth >= 80:
            line += " | Overbought"
        lines.append(line)
    return category, "\n".join(lines)


def market_context_category(value):
    first_line = str(value or "").strip().splitlines()[0] if value else ""
    if first_line.lower().startswith("market context:"):
        first_line = first_line.split(":", 1)[1].strip()
    elif first_line.upper().startswith("NIFTY 500:"):
        legacy = first_line.split(":", 1)[1].strip().casefold()
        return {
            "bull market": "Bull Market",
            "bear market": "Bear Market",
            "chop market": "Choppy Market",
            "choppy market": "Choppy Market",
        }.get(legacy, "Unknown")
    first_line = first_line.split("(", 1)[0].strip()
    for category in MARKET_CONTEXT_CATEGORIES[:-1]:
        if first_line.casefold() == category.casefold():
            return category
    return "Unknown"