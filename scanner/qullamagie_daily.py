"""Build a once-daily India-focused Qullamaggie leaders and setup report."""

import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo

from config import settings
from scanner.indicators import adx

IST = ZoneInfo("Asia/Kolkata")


def _move(frame, sessions):
    if len(frame) <= sessions:
        return None
    return float(frame.close.iloc[-1] / frame.close.iloc[-sessions - 1] - 1) * 100


def analyze_history(symbol, frame):
    """Return a ranked momentum candidate and optional daily setup label."""
    if frame is None or len(frame) < 21:
        return None
    data = frame.sort_values("timestamp").reset_index(drop=True)
    timestamps = pd.to_datetime(data.timestamp, utc=True).dt.tz_convert(IST)
    data = data.loc[timestamps.dt.date < datetime.now(IST).date()].reset_index(drop=True)
    if len(data) < 21:
        return None
    price = float(data.close.iloc[-1])
    moves = {
        "1W": _move(data, 5), "1M": _move(data, 21),
        "3M": _move(data, 63), "6M": _move(data, 126),
        "12M": _move(data, 252),
    }
    adr = float((data.high - data.low).iloc[-14:].mean() / price * 100)
    previous_close = data.close.shift(1)
    true_range = pd.concat([
        data.high - data.low,
        (data.high - previous_close).abs(),
        (data.low - previous_close).abs(),
    ], axis=1).max(axis=1)
    atr14_pct = float(true_range.iloc[-14:].mean() / price * 100)
    adx_value = adx(data.high, data.low, data.close, 14).iloc[-1]
    adx14 = float(adx_value) if pd.notna(adx_value) else None
    turnover_days = min(50, len(data))
    turnover_cr = float(data.close.iloc[-turnover_days:].mean() *
                         data.volume.iloc[-turnover_days:].mean() / 1e7)
    eligible = (price > settings.MIN_PRICE
                and turnover_cr >= getattr(settings, "QULLAMAGGIE_MIN_TURNOVER_CR", 10.0)
                and adr > 3.5)

    close = data.close
    ema10 = close.ewm(span=10, adjust=False).mean()
    ema20 = close.ewm(span=20, adjust=False).mean()
    lows = data.low.iloc[-5:]
    recent = data.iloc[-10:]
    range_pct = float((recent.high.max() - recent.low.min()) / recent.low.min() * 100)
    flag = (
        ((moves["1M"] or -999) >= 20 or (moves["3M"] or -999) >= 20)
        and range_pct <= 15
        and bool((lows.diff().dropna() >= 0).sum() >= 3)
        and price >= float(ema10.iloc[-1]) >= float(ema20.iloc[-1])
        and ema10.iloc[-1] > ema10.iloc[-2] and ema20.iloc[-1] > ema20.iloc[-2]
    )
    gap = float(data.open.iloc[-1] / data.close.iloc[-2] - 1) * 100
    volume_days = min(20, len(data) - 1)
    volume_multiple = float(data.volume.iloc[-1] /
                            max(1, data.volume.iloc[-volume_days - 1:-1].mean()))
    episodic_pivot = gap >= 5 and volume_multiple >= 2
    return {
        "symbol": symbol, "price": price, "moves": moves, "adr": adr,
        "atr14_pct": atr14_pct, "adx14": adx14,
        "turnover_cr": turnover_cr, "eligible": eligible,
        "turnover_days": turnover_days,
        "flag": bool(eligible and flag), "ep": bool(eligible and episodic_pivot),
        "gap": gap, "volume_multiple": volume_multiple,
    }


def build_report(histories, top_fraction=0.02, max_symbols=30):
    rows = [candidate for symbol, frame in histories.items()
            if (candidate := analyze_history(symbol, frame)) is not None]
    if not rows:
        return "Qullamagie Style Strong Stocks Universe\nNo stocks passed the liquidity, ADR (>3.5%), history, and price filters."
    leaders = set()
    for period in ("1M", "3M", "6M"):
        ranked = sorted((row for row in rows if row["moves"][period] is not None),
                        key=lambda row: row["moves"][period], reverse=True)
        leaders.update(row["symbol"] for row in ranked[:max(1, int(len(rows) * top_fraction))])
    selected = [row for row in rows if row["symbol"] in leaders and row["eligible"]
                and (row["flag"] or row["ep"])]
    selected.sort(key=lambda row: max(
        value for value in row["moves"].values() if value is not None
    ), reverse=True)
    selected = selected[:max_symbols]
    lines = ["Qullamagie Style Strong Stocks Universe", "Top 2% leaders by 1M/3M/6M; completed daily candles", ""]
    if not selected:
        lines.append("No top-2% momentum leader currently matches the High Tight Flag or EP filters.")
    for row in selected:
        setup = "HIGH TIGHT FLAG" if row["flag"] else "EP"
        if row["flag"] and row["ep"]:
            setup = "HIGH TIGHT FLAG + EP"
        perf = " | ".join(f"{key} {value:+.1f}%" if value is not None else f"{key} N/A"
                           for key, value in row["moves"].items())
        details = f"EP gap {row['gap']:+.1f}% / {row['volume_multiple']:.1f}x vol" if row["ep"] else f"10d range tightening"
        adx_text = f"{row['adx14']:.1f}" if row["adx14"] is not None else "N/A"
        lines.append(f"{row['symbol']} · {setup}\n₹{row['price']:.2f} | {perf}\nATR14 {row['atr14_pct']:.1f}% | ADX14 {adx_text} | ADR {row['adr']:.1f}%\nTurnover ₹{row['turnover_cr']:.1f} Cr avg ({row['turnover_days']} sessions) | {details}")
    lines.extend(["", "Watchlist only; wait for an opening-range breakout. No orders are placed."])
    return "\n".join(lines)
