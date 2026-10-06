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
    
    ema5_s = ema(history.close, 5)
    ema10_s = ema(history.close, 10)
    ema21_s = ema(history.close, 21)

    def get_past(col, days, agg="max"):
        if len(history) < days: return 0.0
        return float(getattr(history[col].iloc[-days:], agg)())

    def get_close_ago(days):
        return float(history.close.iloc[-days]) if len(history) >= days else 0.0

    return {
        "symbol": symbol,
        "history_days": len(history),
        
        "ema5": float(ema5_s.iloc[-1]),
        "ema10": float(ema10_s.iloc[-1]),
        "ema21": float(ema21_s.iloc[-1]),
        "prior_tr13": float(true_ranges.iloc[-13:].sum()),
        
        "year_high": get_past("high", 252, "max"),
        "year_low": get_past("low", 252, "min"),
        
        "high_1m": get_past("high", 21, "max"),
        "high_3m": get_past("high", 63, "max"),
        "high_6m": get_past("high", 126, "max"),
        
        "close_1w": get_close_ago(5),
        "close_1m": get_close_ago(21),
        "close_3m": get_close_ago(63),
        "close_6m": get_close_ago(126),
        "close_1y": get_close_ago(252),
        "close_18m": get_close_ago(378),

        "avg_volume": float(history.volume.iloc[-20:].mean()),
        "avg_volume10": float(history.volume.iloc[-10:].mean()),
        "avg_turnover10_cr": float((history.close.iloc[-10:] * history.volume.iloc[-10:] / 1e7).mean()),
        "dry_up": bool(history.volume.iloc[-3:].mean() < history.volume.iloc[-20:].mean()),
        "red_max_volume": float(red_volume) if pd.notna(red_volume) else 0.0,
        
        "prev_close": float(previous.close),
        "prev_high": float(previous.high),
        "lowest_3d_low": get_past("low", 3, "min"),
        
        "orb_highs": {},
        "first30_volume": None,
        
        "rvol_traded_days": len(history),
        "rvol_cum_volume": float(history.volume.sum()),
        "rvol_prev19_volume": float(history.volume.iloc[-19:].sum()),
        
        "turnover_traded_days": len(history),
        "turnover_cum_volume": float(history.volume.sum()),
        "turnover_cum_close": float(history.close.sum()),
        "turnover_prev49_volume": float(history.volume.iloc[-49:].sum()),
        "turnover_prev49_close": float(history.close.iloc[-49:].sum()),
        
        "in_trade": False,
        "sl_price": None,
    }

def update_intraday(state, candles, now=None):
    now = now or datetime.now(IST)
    start = datetime.combine(now.date(), dtime(9, 15), tzinfo=IST)
    bars = candles.copy()
    if bars.empty: return
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

def evaluate(state, quote, market_context):
    minutes, _, _ = _market_minutes()
    if minutes < 3 or minutes >= 375: return None
    
    price = float(quote.get("last_price") or 0)
    volume = float(quote.get("volume") or 0)
    ohlc = quote.get("ohlc") or {}
    day_open = float(ohlc.get("open") or 0)
    day_high = max(float(ohlc.get("high") or price), price)
    day_low = float(ohlc.get("low") or price)
    
    if price <= 0 or volume <= 0 or day_open <= 0 or float(ohlc.get("high") or 0) <= 0:
        return None

    traded_days = state["history_days"] + 1
    is_ipo = traded_days <= 21
    is_vol_bypass = traded_days < 15

    current_ema5 = state["ema5"] + 2 / 6 * (price - state["ema5"])
    current_ema10 = state["ema10"] + 2 / 11 * (price - state["ema10"])
    current_ema21 = state["ema21"] + 2 / 22 * (price - state["ema21"])
    extension10 = (price / current_ema10 - 1) * 100
    extension21 = (price / current_ema21 - 1) * 100
    
    today_tr = max(day_high - day_low, abs(day_high - state["prev_close"]), abs(day_low - state["prev_close"]))
    atr_pct = (state["prior_tr13"] + today_tr) / 14 / price * 100
    
    year_high = max(state["year_high"], day_high)
    year_low = min(state["year_low"], day_low)
    from_high = (year_high - price) / year_high * 100
    swing = (price / year_low - 1) * 100 if year_low > 0 else 0

    if traded_days < 20: avg_vol20 = (state["rvol_cum_volume"] + volume) / max(1, traded_days)
    else: avg_vol20 = (state["rvol_prev19_volume"] + volume) / 20.0
    rvol = volume / avg_vol20 if avg_vol20 > 0 else 0.0

    if traded_days < 50:
        avg_vol50 = (state["turnover_cum_volume"] + volume) / max(1, traded_days)
        avg_price50 = (state["turnover_cum_close"] + price) / max(1, traded_days)
    else:
        avg_vol50 = (state["turnover_prev49_volume"] + volume) / 50.0
        avg_price50 = (state["turnover_prev49_close"] + price) / 50.0
    turnover_cr = (avg_price50 * avg_vol50) / 1e7

    pocket = day_open > 0 and price > day_open and state["red_max_volume"] > 0 and volume > state["red_max_volume"]
    first30_surge = state["first30_volume"] is not None and state["first30_volume"] > state["avg_volume10"]
    abs_vol = volume >= 9000000
    
    stockStrongMomo = (current_ema5 > current_ema10 and current_ema5 > state["ema5"]) if is_ipo else (current_ema10 > current_ema21 and current_ema10 > state["ema10"])
    idxC = market_context.get("idxC", 0)
    idxE21 = market_context.get("idxE21", 0)
    idxE10 = market_context.get("idxE10", 0)
    prev_idxE10 = market_context.get("prev_idxE10", 0)
    idxStrongMomo = (idxE10 > idxE21) and (idxE10 > prev_idxE10)

    orb_windows = [window for window, high in state["orb_highs"].items() if minutes >= window and price > high]
    breakout = price >= min(state["prev_high"], state["prev_close"]) * (1 + settings.BREAKOUT_BUFFER_PCT / 100)

    # Core checks mapping directly to PineScript
    checks = {
        "trend": is_ipo or price > current_ema21,
        "extension": is_ipo or not getattr(settings, "LIVE_BLOCK_EXTENDED", False) or (extension10 <= 10 and extension21 <= 20),
        "volume_surge": is_vol_bypass or (rvol > settings.MIN_RVOL) or pocket or first30_surge or abs_vol,
        "dry_up": state["dry_up"] if getattr(settings, "STRICT_VCP", False) else True,
        "momentum": price > state["prev_close"],
        "breakout": breakout,
        "strength": is_ipo or swing >= settings.MIN_SWING_RETURN,
        "near_high": from_high <= (15 if is_ipo else settings.MAX_FROM_52W_HIGH),
        "volatility": is_ipo or atr_pct > settings.MIN_ATR_PCT,
        "price_floor": price > settings.MIN_PRICE,
        "liquidity": turnover_cr > settings.MIN_TURNOVER_CR,
        "strong_momo": (not getattr(settings, "REQ_STOCK_MOMO", True) or stockStrongMomo) and (not getattr(settings, "REQ_IDX_MOMO", False) or idxStrongMomo),
        "orb": bool(orb_windows),
    }
    trigger_buy = all(checks.values()) and not state.get("in_trade")

    # --- Independent Radars ---
    radar_allowed = is_ipo or price > current_ema21
    gap_pct = (day_open - state["prev_close"]) / state["prev_close"] * 100 if state["prev_close"] > 0 else 0
    trigger_gap = radar_allowed and getattr(settings, "ALERT_GAP_UP", True) and gap_pct >= 5.0
    
    highMsg = None
    if day_high > state["year_high"]: highMsg = "New 52-Week High"
    elif day_high > state["high_6m"]: highMsg = "New 6-Month High"
    elif day_high > state["high_3m"]: highMsg = "New 3-Month High"
    elif day_high > state["high_1m"]: highMsg = "New 1-Month High"
    trigger_high = radar_allowed and getattr(settings, "ALERT_NEW_HIGHS", False) and highMsg
    
    perfMsg = None
    if state["close_18m"] > 0 and price >= state["close_18m"] * 2.5: perfMsg = "18M Return > 150%"
    elif state["close_1y"] > 0 and price >= state["close_1y"] * 2.0: perfMsg = "1Yr Return > 100%"
    elif state["close_6m"] > 0 and price >= state["close_6m"] * 1.5: perfMsg = "6M Return > 50%"
    elif state["close_3m"] > 0 and price >= state["close_3m"] * 1.3: perfMsg = "3M Return > 30%"
    elif state["close_1m"] > 0 and price >= state["close_1m"] * 1.2: perfMsg = "1M Return > 20%"
    elif state["close_1w"] > 0 and price >= state["close_1w"] * 1.19: perfMsg = "1W Return > 19%"
    trigger_perf = radar_allowed and getattr(settings, "ALERT_PERF", False) and perfMsg

    # --- Trade Exits (3:15 PM Closing Basis) ---
    trigger_sell = None
    if state["in_trade"] and getattr(settings, "ENABLE_SELLS", False) and minutes >= 360:
        hitSL = day_low < state["sl_price"] if state["sl_price"] else False
        trail_21 = (traded_days > 21) and (price < current_ema21)
        trail_10 = (traded_days > 10 and traded_days <= 21) and (price < current_ema10)
        trail_3L = (traded_days <= 10) and (price < state["lowest_3d_low"])

        if trail_21: trigger_sell = "SELL-21EMA"
        elif trail_10: trigger_sell = "SELL-10EMA"
        elif trail_3L: trigger_sell = "SELL-3DL"
        elif hitSL:
            shield = (price > current_ema10 and price > current_ema21) if traded_days > 21 else (price > current_ema10)
            if not shield: trigger_sell = "SELL-SLHit"

    if not (trigger_buy or trigger_sell or trigger_gap or trigger_high or trigger_perf):
        return {"checks": checks, "symbol": state["symbol"], "price": price}

    # --- Format TradingView Compatible Message ---
    isBullMarket = (idxC > idxE21) and (idxE10 > idxE21) and (idxE10 > prev_idxE10)
    isBearMarket = (idxC < idxE21) and (idxE10 < idxE21) and (idxE10 < prev_idxE10)
    ctxMsg = f" | Market: {'Bull Market' if isBullMarket else ('Bear Market' if isBearMarket else 'Choppy Market')}"

    alert_type = ""
    alertMsg = ""
    ticker = f"NSE:{state['symbol']}"

    if trigger_buy:
        rawOrbMsg = "ORB not broken yet"
        if 60 in orb_windows: rawOrbMsg = "Broke 60m ORB"
        elif 30 in orb_windows: rawOrbMsg = "Broke 30m ORB"
        elif 15 in orb_windows: rawOrbMsg = "Broke 15m ORB"
        elif 5 in orb_windows: rawOrbMsg = "Broke 5m ORB"
        elif 3 in orb_windows: rawOrbMsg = "Broke 3m ORB"
        
        alertMsg = f"{ticker} BUY | Entry: {price:.2f} | SL: {day_low:.2f} | {rawOrbMsg}{ctxMsg}"
        if stockStrongMomo and idxStrongMomo: alertMsg += " | Strong Momo (Stk+Idx)"
        elif stockStrongMomo: alertMsg += " | Strong Momo (Stk)"
        elif idxStrongMomo: alertMsg += " | Strong Momo (Idx)"
        
        if trigger_high: alertMsg += f" | {highMsg}"
        if trigger_gap: alertMsg += f" | Gap Up: +{gap_pct:.1f}%"
        if trigger_perf: alertMsg += f" | {perfMsg}"
        
        state["in_trade"] = True
        state["sl_price"] = day_low
        alert_type = "BUY"
    elif trigger_sell:
        alertMsg = f"{ticker} {trigger_sell}{ctxMsg}"
        alert_type = trigger_sell
    else:
        radars = []
        if trigger_gap: radars.append(f"Gap Up: +{gap_pct:.1f}%")
        if trigger_high: radars.append(f"Hit {highMsg}")
        if trigger_perf: radars.append(f"Hit {perfMsg}")
        alertMsg = f"{ticker} {' & '.join(radars)}{ctxMsg}"
        alert_type = "RADAR"

    return {
        "checks": checks,
        "symbol": state["symbol"],
        "price": price,
        "alert_type": alert_type,
        "alert_message": alertMsg
    }