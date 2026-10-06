from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

import pandas as pd

from config import settings
from scanner.indicators import ema

IST = ZoneInfo("Asia/Kolkata")
ORB_WINDOWS = (3, 5, 15, 30, 60)


def _completed(df):
    if df.empty:
        return df
    current = df.copy()
    current["timestamp"] = pd.to_datetime(current["timestamp"], utc=True).dt.tz_convert(IST)
    today = datetime.now(IST).date()
    return current[current["timestamp"].dt.date < today].sort_values("timestamp").reset_index(drop=True)


def build_state(symbol, df, nifty_df):
    history = _completed(df)
    if history.empty:
        return None
    previous = history.iloc[-1]
    previous_close = history.close.shift(1)
    true_ranges = pd.concat([
        history.high - history.low,
        (history.high - previous_close).abs(),
        (history.low - previous_close).abs(),
    ], axis=1).max(axis=1)
    red = history.iloc[-10:]
    red_volume = red.loc[red.close < red.open, "volume"].max()
    benchmark = _completed(nifty_df) if nifty_df is not None else pd.DataFrame()
    market_above_ema = None
    if len(benchmark) >= 21:
        market_above_ema = bool(benchmark.close.iloc[-1] > ema(benchmark.close, 21).iloc[-1])
    return {
        "symbol": symbol,
        "history_days": len(history),
        "is_ipo": len(history) < 252,
        "ema10": float(ema(history.close, 10).iloc[-1]),
        "ema21": float(ema(history.close, 21).iloc[-1]),
        "prior_tr13": float(true_ranges.iloc[-13:].sum()),
        "year_high": float(history.high.iloc[-252:].max()),
        "year_low": float(history.low.iloc[-252:].min()),
        "avg_volume": float(history.volume.iloc[-20:].mean()),
        "avg_volume10": float(history.volume.iloc[-10:].mean()),
        "avg_turnover10_cr": float((history.close.iloc[-10:] * history.volume.iloc[-10:] / 1e7).mean()),
        "dry_up": bool(history.volume.iloc[-3:].mean() < history.volume.iloc[-20:].mean()),
        "red_max_volume": float(red_volume) if pd.notna(red_volume) else 0.0,
        "prev_close": float(previous.close),
        "prev_high": float(previous.high),
        "pivot": float(previous.close) * (1 + settings.BREAKOUT_BUFFER_PCT / 100),
        "market_above_ema": market_above_ema,
        "orb_highs": {},
        "first30_volume": None,
        # RVOL baseline inputs matching the PineScript calculation.
        "rvol_traded_days": len(history),
        "rvol_cum_volume": float(history.volume.sum()),
        "rvol_prev19_volume": float(history.volume.iloc[-19:].sum()),
        # Turnover baseline inputs matching the PineScript calculation.
        "turnover_traded_days": len(history),
        "turnover_cum_volume": float(history.volume.sum()),
        "turnover_cum_close": float(history.close.sum()),
        "turnover_prev49_volume": float(history.volume.iloc[-49:].sum()),
        "turnover_prev49_close": float(history.close.iloc[-49:].sum()),
    }


def update_intraday(state, candles, now=None):
    now = now or datetime.now(IST)
    start = datetime.combine(now.date(), dtime(9, 15), tzinfo=IST)
    bars = candles.copy()
    if bars.empty:
        return
    bars["timestamp"] = pd.to_datetime(bars["timestamp"], utc=True).dt.tz_convert(IST)
    bars = bars[(bars.timestamp >= start) & (bars.timestamp + pd.Timedelta(minutes=1) <= now)]
    bars = bars.drop_duplicates("timestamp").sort_values("timestamp")
    for window in ORB_WINDOWS:
        opening = bars[bars.timestamp < start + pd.Timedelta(minutes=window)]
        expected = pd.date_range(start, periods=window, freq="min")
        if len(opening) == window and opening.timestamp.tolist() == expected.tolist():
            state["orb_highs"][window] = float(opening.high.max())
            if window == 30:
                state["first30_volume"] = float(opening.volume.sum())


def _market_minutes():
    now = datetime.now(IST)
    start = datetime.combine(now.date(), dtime(9, 15), tzinfo=IST)
    end = datetime.combine(now.date(), dtime(15, 30), tzinfo=IST)
    return (now - start).total_seconds() / 60, start, end


def evaluate(state, quote):
    minutes, _, _ = _market_minutes()
    if minutes < 3 or minutes >= 375:
        return None
    price = float(quote.get("last_price") or 0)
    volume = float(quote.get("volume") or 0)
    ohlc = quote.get("ohlc") or {}
    day_open = float(ohlc.get("open") or 0)
    day_high = max(float(ohlc.get("high") or price), price)
    day_low = float(ohlc.get("low") or price)
    if (price <= 0 or volume <= 0 or day_open <= 0 or
            float(ohlc.get("high") or 0) <= 0 or float(ohlc.get("low") or 0) <= 0):
        return None
    current_ema10 = state["ema10"] + 2 / 11 * (price - state["ema10"])
    current_ema21 = state["ema21"] + 2 / 22 * (price - state["ema21"])
    extension10 = (price / current_ema10 - 1) * 100
    extension21 = (price / current_ema21 - 1) * 100
    today_tr = max(day_high - day_low, abs(day_high - state["prev_close"]), abs(day_low - state["prev_close"]))
    atr_pct = (state["prior_tr13"] + today_tr) / 14 / price * 100
    year_high = max(state["year_high"], day_high)
    year_low = min(state["year_low"], day_low)
    from_high = (year_high - price) / year_high * 100
    swing = (price / year_low - 1) * 100

    # PineScript RVOL logic:
    # avgVol20 = tradedDays < inp_rvLen ?
    #     (ta.cum(volume) / math.max(1, tradedDays)) :
    #     ta.sma(volume, inp_rvLen)
    # relVol = volume / avgVol20
    # cond_RVOL = relVol > inp_rvThresh
    #
    # inp_rvLen = 20. The current developing daily volume is included.
    traded_days = state["rvol_traded_days"] + 1
    if traded_days < 20:
        avg_vol20 = (state["rvol_cum_volume"] + volume) / max(1, traded_days)
    else:
        avg_vol20 = (state["rvol_prev19_volume"] + volume) / 20.0
    rvol = volume / avg_vol20 if avg_vol20 > 0 else 0.0
    rvol_condition = rvol > settings.MIN_RVOL

    # PineScript turnover/liquidity logic:
    # if tradedDays < 50:
    #     avgVol50 = ta.cum(volume) / tradedDays
    #     avgPrice50 = ta.cum(close) / tradedDays
    # else:
    #     avgVol50 = ta.sma(volume, 50)
    #     avgPrice50 = ta.sma(close, 50)
    # turnover = avgPrice50 * avgVol50
    # cond12_Liquidity = turnover > inp_minTurnoverRaw
    #
    # The current developing daily volume and close are included.
    turnover_traded_days = state["turnover_traded_days"] + 1
    if turnover_traded_days < 50:
        avg_vol50 = (state["turnover_cum_volume"] + volume) / max(1, turnover_traded_days)
        avg_price50 = (state["turnover_cum_close"] + price) / max(1, turnover_traded_days)
    else:
        avg_vol50 = (state["turnover_prev49_volume"] + volume) / 50.0
        avg_price50 = (state["turnover_prev49_close"] + price) / 50.0
    turnover_raw = avg_price50 * avg_vol50
    turnover_cr = turnover_raw / 1e7
    liquidity_condition = turnover_cr > settings.MIN_TURNOVER_CR

    pocket = day_open > 0 and price > day_open and state["red_max_volume"] > 0 and volume > state["red_max_volume"]
    first30_surge = state["first30_volume"] is not None and state["first30_volume"] > state["avg_volume10"]
    orb_windows = [window for window, high in state["orb_highs"].items() if minutes >= window and price > high]
    breakout = price > min(state["prev_high"], state["prev_close"]) * (1 + settings.BREAKOUT_BUFFER_PCT / 100)
    checks = {
        "trend": state["history_days"] + 1 <= 21 or price > current_ema21,
        "extension": not getattr(settings, "LIVE_BLOCK_EXTENDED", False) or (extension10 <= 10 and extension21 <= 20),
        "volume_surge": rvol_condition or pocket or first30_surge,
        "momentum": price > state["prev_close"],
        "breakout": breakout,
        "strength": state["is_ipo"] or swing >= settings.MIN_SWING_RETURN,
        "near_high": from_high <= (15 if state["is_ipo"] else settings.MAX_FROM_52W_HIGH),
        "volatility": state["is_ipo"] or atr_pct > settings.MIN_ATR_PCT,
        "price_floor": price > settings.MIN_PRICE,
        "liquidity": liquidity_condition,
        "orb": bool(orb_windows),
    }
    return {
        **state,
        "price": price,
        "volume": volume,
        "avg_turnover10_cr": state["avg_turnover10_cr"],
        "turnover_raw": turnover_raw,
        "turnover_cr": turnover_cr,
        "rvol": rvol,
        "rvol_condition": rvol_condition,
        "atr_pct": atr_pct,
        "from_high": from_high,
        "swing": swing,
        "extension10": extension10,
        "extension21": extension21,
        "pocket_pivot": bool(pocket),
        "first30_surge": bool(first30_surge),
        "orb_windows": sorted(orb_windows),
        "breakout": breakout,
        "checks": checks,
        "score": sum(bool(passed) for passed in checks.values()),
        "buy": all(checks.values()),
        "timestamp": quote.get("timestamp", ""),
    }