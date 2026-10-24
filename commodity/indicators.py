"""Pure indicator and signal calculations for commodity candles."""

import pandas as pd

from commodity import commodity_settings as settings


def add_emas(candles):
    frame = candles.copy()
    frame["ema10"] = frame["close"].ewm(span=settings.INTRADAY_EMA_FAST, adjust=False).mean()
    frame["ema20"] = frame["close"].ewm(span=settings.INTRADAY_EMA_SLOW, adjust=False).mean()
    return frame


def add_volatility_and_trend(candles):
    """Add ATR, ADX, Bollinger and participation fields to a candle frame."""
    frame = candles.copy()
    previous_close = frame["close"].shift(1)
    true_ranges = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    period = settings.ADX_PERIOD
    frame["atr"] = true_ranges.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    up_move = frame["high"].diff()
    down_move = -frame["low"].diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    smoothed_range = true_ranges.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / smoothed_range
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / smoothed_range
    directional_sum = (plus_di + minus_di).replace(0, float("nan"))
    frame["adx"] = (100 * (plus_di - minus_di).abs() / directional_sum).ewm(
        alpha=1 / period, adjust=False, min_periods=period
    ).mean()

    basis = frame["close"].rolling(settings.INTRADAY_EMA_SLOW).mean()
    deviation = frame["close"].rolling(settings.INTRADAY_EMA_SLOW).std(ddof=0)
    frame["bb_upper"] = basis + settings.MEAN_REVERSION_BOLLINGER_STD * deviation
    frame["bb_lower"] = basis - settings.MEAN_REVERSION_BOLLINGER_STD * deviation
    frame["volume_average"] = frame["volume"].shift(1).rolling(settings.BREAKOUT_LOOKBACK).mean()
    frame["volume_ratio"] = frame["volume"] / frame["volume_average"]
    frame["oi_change"] = frame["oi"].diff()
    return frame


def daily_context(candles):
    """Return the completed daily trend and whether it just crossed."""
    if len(candles) < settings.DAILY_EMA_SLOW + 1:
        return {"direction": "neutral", "ready": False, "alert": False}

    frame = add_emas(candles.dropna(subset=["close"]))
    previous, current = frame.iloc[-2], frame.iloc[-1]
    long_now = (current["close"] > current["ema10"] > current["ema20"])
    short_now = (current["close"] < current["ema10"] < current["ema20"])
    crossed_long = previous["ema10"] <= previous["ema20"] and current["ema10"] > current["ema20"]
    crossed_short = previous["ema10"] >= previous["ema20"] and current["ema10"] < current["ema20"]
    direction = "long" if long_now else "short" if short_now else "neutral"
    return {
        "direction": direction,
        "ready": True,
        "alert": crossed_long or crossed_short,
        "cross": "long" if crossed_long else "short" if crossed_short else None,
        "timestamp": current["timestamp"],
        "close": float(current["close"]),
        "low": float(current["low"]),
        "high": float(current["high"]),
        "ema10": float(current["ema10"]),
        "ema20": float(current["ema20"]),
    }


def intraday_entry(candles, daily_direction, tolerance_pct=None):
    """Find a completed 30-minute EMA retest aligned with daily direction."""
    if len(candles) < settings.INTRADAY_EMA_SLOW + 1 or daily_direction not in {"long", "short"}:
        return None
    tolerance = (settings.EMA_TOUCH_TOLERANCE_PCT if tolerance_pct is None else tolerance_pct) / 100
    frame = add_emas(candles.dropna(subset=["open", "high", "low", "close"]))
    current = frame.iloc[-1]
    previous = frame.iloc[-2]
    ema_floor = max(current["ema10"], current["ema20"])
    ema_ceiling = min(current["ema10"], current["ema20"])

    if daily_direction == "long":
        retest = current["low"] <= ema_floor * (1 + tolerance)
        confirmed = current["close"] > ema_floor and current["close"] > current["open"]
    else:
        retest = current["high"] >= ema_ceiling * (1 - tolerance)
        confirmed = current["close"] < ema_ceiling and current["close"] < current["open"]

    if not (retest and confirmed):
        return None
    return {
        "direction": daily_direction,
        "timestamp": current["timestamp"],
        "entry": float(current["close"]),
        "ema10": float(current["ema10"]),
        "ema20": float(current["ema20"]),
        "previous_close": float(previous["close"]),
    }


def _participation_confirmation(current):
    """Use volume and OI when Upstox supplies usable derivative fields."""
    volume_ratio = current.get("volume_ratio")
    oi_change = current.get("oi_change")
    volume_ok = pd.notna(volume_ratio) and volume_ratio >= settings.BREAKOUT_VOLUME_MULTIPLIER
    oi_available = pd.notna(current.get("oi")) and pd.notna(oi_change) and current.get("oi", 0) > 0
    return volume_ok and (not oi_available or oi_change > 0), volume_ratio, oi_change, oi_available


def intraday_signal(candles, daily_direction, commodity=None, tolerance_pct=None):
    """Return an EMA, breakout or sideways mean-reversion signal.

    Trend strategies require daily alignment. Mean reversion is only allowed
    when the daily context is neutral, preventing it from fighting a strong
    higher-timeframe trend.
    """
    required = max(settings.INTRADAY_EMA_SLOW + 1, settings.BREAKOUT_LOOKBACK + 2, settings.ADX_PERIOD * 2)
    if len(candles) < required:
        return None
    frame = add_volatility_and_trend(
        candles.dropna(subset=["open", "high", "low", "close"]).copy()
    )
    frame = add_emas(frame).dropna(subset=["atr", "adx"])
    if len(frame) < settings.BREAKOUT_LOOKBACK + 1:
        return None
    current = frame.iloc[-1]
    previous = frame.iloc[-2]
    threshold = settings.ADX_THRESHOLDS.get(commodity, settings.ADX_DEFAULT_THRESHOLD)
    tolerance = (settings.EMA_TOUCH_TOLERANCE_PCT if tolerance_pct is None else tolerance_pct) / 100
    ema_floor = max(current["ema10"], current["ema20"])
    ema_ceiling = min(current["ema10"], current["ema20"])

    if daily_direction in {"long", "short"}:
        if daily_direction == "long":
            retest = current["low"] <= ema_floor * (1 + tolerance)
            confirmed = current["close"] > ema_floor and current["close"] > current["open"]
            breakout_level = frame["high"].iloc[-settings.BREAKOUT_LOOKBACK - 1:-1].max()
            breakout = current["close"] > breakout_level and current["close"] > current["open"]
        else:
            retest = current["high"] >= ema_ceiling * (1 - tolerance)
            confirmed = current["close"] < ema_ceiling and current["close"] < current["open"]
            breakout_level = frame["low"].iloc[-settings.BREAKOUT_LOOKBACK - 1:-1].min()
            breakout = current["close"] < breakout_level and current["close"] < current["open"]

        if current["adx"] >= threshold and breakout:
            participation, volume_ratio, oi_change, oi_available = _participation_confirmation(current)
            if participation:
                return _signal_payload(current, daily_direction, "VOLATILITY_BREAKOUT", volume_ratio, oi_change, oi_available)
        if retest and confirmed:
            return _signal_payload(current, daily_direction, "EMA_PULLBACK", current.get("volume_ratio"), current.get("oi_change"), False)

    if daily_direction == "neutral" and current["adx"] <= threshold + settings.MEAN_REVERSION_ADX_BUFFER:
        long_reversion = (
            current["low"] <= current["bb_lower"]
            and current["close"] > current["open"]
            and current["close"] > previous["close"]
            and current["low"] <= current["ema20"] - settings.MEAN_REVERSION_ATR_MULTIPLIER * current["atr"]
        )
        short_reversion = (
            current["high"] >= current["bb_upper"]
            and current["close"] < current["open"]
            and current["close"] < previous["close"]
            and current["high"] >= current["ema20"] + settings.MEAN_REVERSION_ATR_MULTIPLIER * current["atr"]
        )
        if long_reversion:
            return _signal_payload(current, "long", "MEAN_REVERSION", current.get("volume_ratio"), current.get("oi_change"), False)
        if short_reversion:
            return _signal_payload(current, "short", "MEAN_REVERSION", current.get("volume_ratio"), current.get("oi_change"), False)
    return None


def _signal_payload(current, direction, strategy, volume_ratio, oi_change, oi_available):
    return {
        "direction": direction,
        "strategy": strategy,
        "timestamp": current["timestamp"],
        "entry": float(current["close"]),
        "close": float(current["close"]),
        "ema10": float(current["ema10"]),
        "ema20": float(current["ema20"]),
        "adx": float(current["adx"]),
        "volume_ratio": float(volume_ratio) if pd.notna(volume_ratio) else None,
        "oi_change": float(oi_change) if pd.notna(oi_change) else None,
        "oi_available": bool(oi_available),
        "previous_close": float(current["close"]),
    }


def stop_loss(entry, day_low, day_high, direction):
    """Return the entry-day extreme and its percentage distance."""
    if direction == "long":
        price = float(day_low)
        percentage = (entry - price) / entry * 100
    else:
        price = float(day_high)
        percentage = (price - entry) / entry * 100
    return {"price": price, "percentage": round(max(0.0, percentage), 3)}
