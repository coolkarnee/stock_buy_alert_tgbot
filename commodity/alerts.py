"""Commodity-specific alert formatting with existing Telegram delivery."""

import csv
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scanner.alerts import send
from commodity.indicators import stop_loss

COMMODITY_ALERT_FILE = Path("artifacts/commodity_alerts.csv")
IST = ZoneInfo("Asia/Kolkata")
ALERT_FIELDS = (
    "asset_class", "symbol", "instrument_key", "expiry", "timeframe", "direction", "alert_time_ist",
    "candle_time", "alert_price", "ema10", "ema20", "daily_context", "stop_price",
    "stop_pct", "strategy", "adx", "volume_ratio", "oi_change", "oi_available", "chart_symbol",
)


def ensure_alert_file():
    COMMODITY_ALERT_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not COMMODITY_ALERT_FILE.exists() or COMMODITY_ALERT_FILE.stat().st_size == 0:
        with COMMODITY_ALERT_FILE.open("w", newline="", encoding="utf-8") as handle:
            csv.DictWriter(handle, fieldnames=ALERT_FIELDS).writeheader()
        return
    with COMMODITY_ALERT_FILE.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames == list(ALERT_FIELDS):
            return
        rows = list(reader)
    with COMMODITY_ALERT_FILE.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ALERT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def record_alert(contract, timeframe, direction, context, daily_context=None, stop=None):
    ensure_alert_file()
    symbol = contract.get("trading_symbol") or contract.get("tradingsymbol")
    row = {
        "asset_class": "commodity",
        "symbol": symbol,
        "instrument_key": contract.get("instrument_key", ""),
        "expiry": contract["expiry"],
        "timeframe": timeframe,
        "direction": direction,
        "alert_time_ist": datetime.now(IST).isoformat(),
        "candle_time": str(context.get("timestamp", "")),
        "alert_price": context.get("close", context.get("entry", "")),
        "ema10": context.get("ema10", ""),
        "ema20": context.get("ema20", ""),
        "daily_context": daily_context or direction,
        "stop_price": (stop or {}).get("price", ""),
        "stop_pct": (stop or {}).get("percentage", ""),
        "strategy": context.get("strategy", "DAILY_CONTEXT"),
        "adx": context.get("adx", ""),
        "volume_ratio": context.get("volume_ratio", ""),
        "oi_change": context.get("oi_change", ""),
        "oi_available": context.get("oi_available", ""),
        "chart_symbol": _chart(contract),
    }
    with COMMODITY_ALERT_FILE.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ALERT_FIELDS)
        writer.writerow(row)


def _chart(contract):
    symbol = contract.get("trading_symbol") or contract.get("tradingsymbol")
    return "MCX:" + str(symbol)


def send_daily(contract, context):
    direction = context["direction"].upper()
    stop = stop_loss(context["close"], context["low"], context["high"], context["direction"])
    record_alert(contract, "daily", context["direction"], context, stop=stop)
    message = (
        f"COMMODITY DAILY CONTEXT - {direction}\n"
        f"Instrument: {contract.get('trading_symbol') or contract.get('tradingsymbol')}\n"
        f"Expiry: {contract['expiry']}\n"
        f"Close Rs {context['close']:.2f} | EMA10 Rs {context['ema10']:.2f} | EMA20 Rs {context['ema20']:.2f}\n"
        f"Daily trend: {context['direction']}\n"
        f"SL: Rs {stop['price']:.2f} ({stop['percentage']:.3f}% from entry)\n"
        "This larger timeframe context takes priority over intraday signals."
    )
    send(message, chart_symbol=_chart(contract))


def send_intraday(contract, context, daily_direction, stop):
    direction = context["direction"].upper()
    adx = context.get("adx")
    adx_text = "NA" if adx is None else f"{adx:.2f}"
    record_alert(contract, "30m", context["direction"], context, daily_direction, stop)
    message = (
        f"COMMODITY 30M {direction} ALERT\n"
        f"Instrument: {contract.get('trading_symbol') or contract.get('tradingsymbol')}\n"
        f"Expiry: {contract['expiry']}\n"
        f"Entry Rs {context['entry']:.2f} | EMA10 Rs {context['ema10']:.2f} | EMA20 Rs {context['ema20']:.2f}\n"
        f"Daily context: {daily_direction}\n"
        f"Strategy: {context.get('strategy', 'EMA_PULLBACK')} | ADX: {adx_text}\n"
        f"Volume ratio: {context.get('volume_ratio') or 'NA'} | OI change: {context.get('oi_change') or 'NA'}\n"
        f"SL: Rs {stop['price']:.2f} ({stop['percentage']:.3f}% from entry)\n"
        f"30M candle: {context['timestamp']}"
    )
    send(message, chart_symbol=_chart(contract))
