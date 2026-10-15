from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

import pandas as pd

from config import settings
from scanner.indicators import ema

IST = ZoneInfo("Asia/Kolkata")
ORB_WINDOWS = (3, 5, 15, 30, 60)
MAX_SL_DISTANCE_PCT = 8.0
PORTFOLIO_RISK_PCT = 1.0
MAX_POSITION_SIZE_PCT = 25.0
SWING_MOVE_RULES = (
    ("1W", 5, 10.0),
    ("1M", 21, 19.0),
    ("3M", 63, 29.0),
    ("6M", 126, 49.0),
    ("1Y", 252, 99.0),
    ("18M", 378, 149.0),
    ("24M", 504, 199.0),
)


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
    dark_red_previous_candle = bool(
        float(previous.close) < float(previous.open) * (
            1 - settings.DARK_RED_CANDLE_DROP_PCT / 100
        )
    )
    breakout_reference = previous.high if dark_red_previous_candle else min(previous.high, previous.close)
    benchmark = _completed(nifty_df) if nifty_df is not None else pd.DataFrame()
    market_above_ema = None
    if len(benchmark) >= 21:
        market_above_ema = bool(benchmark.close.iloc[-1] > ema(benchmark.close, 21).iloc[-1])
    swing_move_references = {
        label: float(history.close.iloc[-period])
        if len(history) >= period else None
        for label, period, _ in SWING_MOVE_RULES
    }
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
        "prev_open": float(previous.open),
        "prev_close": float(previous.close),
        "prev_high": float(previous.high),
        "prev_low": float(previous.low),
        "dark_red_previous_candle": dark_red_previous_candle,
        "pivot": float(breakout_reference) * (
            1 + settings.BREAKOUT_BUFFER_PCT / 100
        ),
        "market_above_ema": market_above_ema,
        "swing_move_references": swing_move_references,
        "orb_highs": {},
        "orb_close_breaks": {},
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
        # Build each ORB from the opening time range itself. A missing or
        # delayed 1-minute candle must not invalidate the entire ORB.
        #
        # 3m  -> 09:15 through 09:17
        # 5m  -> 09:15 through 09:19
        # 15m -> 09:15 through 09:29
        # 30m -> 09:15 through 09:44
        # 60m -> 09:15 through 10:14
        window_end = start + pd.Timedelta(minutes=window)
        if now < window_end:
            continue

        opening = bars[
            (bars.timestamp >= start) &
            (bars.timestamp < window_end)
        ]
        if opening.empty:
            continue

        opening_high = float(opening.high.max())
        state["orb_highs"][window] = opening_high
        after_opening = bars[bars.timestamp >= window_end]
        state["orb_close_breaks"][window] = bool(
            not after_opening.empty and (after_opening.close > opening_high).any()
        )
        if window == 30:
            state["first30_volume"] = float(opening.volume.sum())


def _market_minutes():
    now = datetime.now(IST)
    start = datetime.combine(now.date(), dtime(9, 15), tzinfo=IST)
    end = datetime.combine(now.date(), dtime(15, 30), tzinfo=IST)
    return (now - start).total_seconds() / 60, start, end


def evaluate(state, quote):
    minutes, _, _ = _market_minutes()
    # EP is independent of opening-range data and may be delivered from the
    # opening tick. Momentum BUY still remains blocked until an ORB can qualify
    # it through the existing `checks["orb"]` condition below.
    if minutes >= 375:
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
    intraday_move = (price / day_open - 1) * 100
    intraday_move_from_low = (price / day_low - 1) * 100
    intraday_move_condition = intraday_move >= 4.0
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
    daily_volume_condition = volume > 9_000_000

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
    orb_mode = getattr(settings, "OPENING_RANGE_BREAKOUT_MODE", "close").lower()
    if orb_mode == "spot":
        orb_windows = [
            window for window, high in state["orb_highs"].items()
            if minutes >= window and price > high
        ]
    else:
        orb_mode = "close"
        orb_windows = [
            window for window in state["orb_highs"]
            if minutes >= window and state.get("orb_close_breaks", {}).get(window, False)
        ]
    breakout_reference = (
        state["prev_high"]
        if state["dark_red_previous_candle"]
        else min(state["prev_high"], state["prev_close"])
    )
    breakout = price > breakout_reference * (1 + settings.BREAKOUT_BUFFER_PCT / 100)
    swing_moves = {
        label: (price / reference - 1) * 100
        if reference is not None else None
        for label, reference in state["swing_move_references"].items()
    }
    swing_move_condition = state["is_ipo"] or any(
        reference is not None and (price / reference - 1) * 100 > threshold
        for label, _, threshold in SWING_MOVE_RULES
        for reference in [state["swing_move_references"].get(label)]
    )
    sl_distance_pct = (price - state["prev_low"]) / price * 100
    sl_risk_valid = 0 < sl_distance_pct <= MAX_SL_DISTANCE_PCT
    position_size_pct = (
        min(MAX_POSITION_SIZE_PCT, PORTFOLIO_RISK_PCT * 100 / sl_distance_pct)
        if sl_risk_valid else None
    )
    checks = {
        "trend": state["history_days"] + 1 <= 21 or price > current_ema21,
        "extension": not getattr(settings, "LIVE_BLOCK_EXTENDED", False) or (extension10 <= 10 and extension21 <= 20),
        "volume_surge": rvol_condition or pocket or first30_surge or intraday_move_condition or daily_volume_condition,
        "momentum": price > state["prev_close"],
        "breakout": breakout,
        "strength": swing_move_condition,
        "near_high": from_high <= (15 if state["is_ipo"] else settings.MAX_FROM_52W_HIGH),
        "volatility": state["is_ipo"] or atr_pct > settings.MIN_ATR_PCT,
        "price_floor": price > settings.MIN_PRICE,
        "liquidity": liquidity_condition,
        "orb": bool(orb_windows),
        "sl_risk": sl_risk_valid,
    }
    episodic_pivot = (
        day_open > state["prev_close"] * 1.05 and
        price > current_ema21 and
        price > settings.MIN_PRICE and
        (state["is_ipo"] or atr_pct > settings.MIN_ATR_PCT) and
        liquidity_condition
    )
    return {
        **state,
        "price": price,
        "volume": volume,
        "day_open": day_open,
        "day_low": day_low,
        "sl_distance_pct": sl_distance_pct,
        "position_size_pct": position_size_pct,
        "avg_turnover10_cr": state["avg_turnover10_cr"],
        "turnover_raw": turnover_raw,
        "turnover_cr": turnover_cr,
        "turnover_period_days": min(turnover_traded_days, 50),
        "rvol": rvol,
        "rvol_condition": rvol_condition,
        "daily_volume_condition": daily_volume_condition,
        "intraday_move": intraday_move,
        "intraday_move_from_low": intraday_move_from_low,
        "intraday_move_condition": intraday_move_condition,
        "atr_pct": atr_pct,
        "from_high": from_high,
        "swing": swing,
        "swing_moves": swing_moves,
        "extension10": extension10,
        "extension21": extension21,
        "pocket_pivot": bool(pocket),
        "first30_surge": bool(first30_surge),
        "orb_windows": sorted(orb_windows),
        "orb_mode": orb_mode,
        "breakout": breakout,
        "checks": checks,
        "episodic_pivot": episodic_pivot,
        "score": sum(bool(passed) for passed in checks.values()),
        "buy": all(checks.values()),
        "timestamp": quote.get("timestamp", ""),
    }
