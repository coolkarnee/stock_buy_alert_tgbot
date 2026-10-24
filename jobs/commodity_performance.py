"""Evaluate commodity alerts and report entry-day stop-loss hits."""

import csv
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from commodity.upstox import current_day_thirty_minute_candles, thirty_minute_candles
from scanner.alerts import send

IST = ZoneInfo("Asia/Kolkata")
ARTIFACT_DIR = Path("artifacts")
ALERT_FILE = ARTIFACT_DIR / "commodity_alerts.csv"
OUTPUT_FILE = ARTIFACT_DIR / "commodity_performance.csv"
SUMMARY_FILE = ARTIFACT_DIR / "commodity_performance_summary.txt"
OUTPUT_COLUMNS = [
    "asset_class", "symbol", "instrument_key", "expiry", "timeframe", "direction",
    "alert_time_ist", "entry_price", "sl_price", "sl_pct", "latest_price",
    "performance_pct", "strategy", "sl_hit_time", "status",
]


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def evaluate_alert(alert, candles):
    """Evaluate one alert against candles after its recorded entry time."""
    entry = _number(alert.get("alert_price"))
    sl = _number(alert.get("stop_price"))
    direction = str(alert.get("direction", "")).lower()
    alert_time = pd.to_datetime(alert.get("alert_time_ist"), utc=True, errors="coerce")
    base = {
        "asset_class": "commodity",
        "symbol": alert.get("symbol", ""),
        "instrument_key": alert.get("instrument_key", ""),
        "expiry": alert.get("expiry", ""),
        "timeframe": alert.get("timeframe", ""),
        "direction": direction,
        "alert_time_ist": alert.get("alert_time_ist", ""),
        "entry_price": entry,
        "sl_price": sl,
        "sl_pct": _number(alert.get("stop_pct")),
        "latest_price": None,
        "performance_pct": None,
        "strategy": alert.get("strategy", "UNKNOWN"),
        "sl_hit_time": "",
        "status": "INVALID ALERT",
    }
    if entry is None or sl is None or pd.isna(alert_time) or direction not in {"long", "short"}:
        return base

    frame = candles.copy()
    if frame.empty:
        base["status"] = "NO DATA AFTER ALERT"
        return base
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    frame = frame.dropna(subset=["timestamp"]).sort_values("timestamp")
    frame = frame[frame["timestamp"] > alert_time]
    if frame.empty:
        base["status"] = "NO DATA AFTER ALERT"
        return base

    latest = float(frame.iloc[-1]["close"])
    base["latest_price"] = latest
    if direction == "long":
        hit_frame = frame[frame["low"] <= sl]
    else:
        hit_frame = frame[frame["high"] >= sl]
    if not hit_frame.empty:
        hit = hit_frame.iloc[0]
        base["latest_price"] = sl
        base["sl_hit_time"] = hit["timestamp"].tz_convert(IST).isoformat()
        base["status"] = "SL HIT"
        mark = sl
    else:
        base["status"] = "OPEN"
        mark = latest
    base["performance_pct"] = round(
        ((mark - entry) / entry if direction == "long" else (entry - mark) / entry) * 100,
        3,
    )
    return base


def _report(rows, today):
    lines = [f"📊 COMMODITY PERFORMANCE | {today}", "", f"{len(rows)} commodity alert events evaluated."]
    for row in rows:
        status = row["status"]
        if status == "SL HIT":
            lines.append(
                f"🔴 {row['symbol']} ({row['direction'].upper()} {row['timeframe']}) | "
                f"{row['strategy']} | "
                f"Entry Rs {row['entry_price']:.2f} | SL HIT Rs {row['sl_price']:.2f} | "
                f"Hit {row['sl_hit_time']} | Return {row['performance_pct']:+.2f}%"
            )
        elif status == "OPEN":
            lines.append(
                f"🟢 {row['symbol']} ({row['direction'].upper()} {row['timeframe']}) | "
                f"{row['strategy']} | "
                f"Entry Rs {row['entry_price']:.2f} | SL Rs {row['sl_price']:.2f} | "
                f"Latest Rs {row['latest_price']:.2f} | Return {row['performance_pct']:+.2f}% | OPEN"
            )
        else:
            lines.append(f"{row['symbol']} | {status}")
    return "\n".join(lines)


def run():
    today = datetime.now(IST).date().isoformat()
    if not ALERT_FILE.exists():
        rows = []
    else:
        with ALERT_FILE.open(newline="", encoding="utf-8") as handle:
            alerts = list(csv.DictReader(handle))
        rows = []
        for alert in alerts:
            key = alert.get("instrument_key")
            if not key:
                rows.append(evaluate_alert(alert, pd.DataFrame()))
                continue
            try:
                candles = thirty_minute_candles(key, days=120)
                current = current_day_thirty_minute_candles(key)
                if not current.empty:
                    candles = pd.concat([candles, current], ignore_index=True).drop_duplicates("timestamp")
                rows.append(evaluate_alert(alert, candles))
            except Exception as exc:
                print(f"[{alert.get('symbol', 'unknown')}] commodity performance lookup failed: {exc}")
                result = evaluate_alert(alert, pd.DataFrame())
                result["status"] = "ERROR"
                rows.append(result)

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=OUTPUT_COLUMNS).to_csv(OUTPUT_FILE, index=False)
    report = _report(rows, today) if rows else f"📊 COMMODITY PERFORMANCE | {today}\nNo commodity alerts recorded."
    print(report)
    send(report)
    SUMMARY_FILE.write_text(
        "No commodity alerts recorded.\n" if not rows else
        f"{sum(row['status'] == 'SL HIT' for row in rows)} commodity alerts hit SL.\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    run()
