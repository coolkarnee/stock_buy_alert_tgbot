"""Build the persistent swing-performance sheet for live alerts."""

import csv
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from scanner.upstox import historical_daily, load_nse_equities
from scanner.indicators import ema

ARTIFACT_DIR = Path("artifacts")
OUTPUT_FILE = ARTIFACT_DIR / "swing_performance.csv"
ALERT_FILE = Path("live_alerts.csv")
IST = ZoneInfo("Asia/Kolkata")

OUTPUT_COLUMNS = [
    "Alert ID",
    "Performance As Of Date",
    "Ticker Name",
    "Buy Price",
    "Entry Day SL",
    "Alert Type",
    "Alert Date",
    "Alert Time",
    "SL Hit Date",
    "SL Type",
    "Holding Days",
    "Performance %",
    "Status",
]


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _alert_id(row):
    existing = str(row.get("Alert ID", "")).strip()
    if existing and existing != "nan":
        return existing
    return "|".join([
        str(row.get("Alert Type", "BUY")),
        str(row.get("Ticker Name", "")),
        str(row.get("Date Time", "")),
        str(row.get("Buy Price", "")),
    ])


def _read_existing():
    if not OUTPUT_FILE.exists():
        return {}
    rows = {}
    with OUTPUT_FILE.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if not row.get("Date Time"):
                row["Date Time"] = " ".join([
                    row.get("Alert Date", ""), row.get("Alert Time", "")
                ]).strip()
            if not row.get("Entry Day SL"):
                row["Entry Day SL"] = row.get("SL", "")
            row["Alert ID"] = _alert_id(row)
            rows[row["Alert ID"]] = row
    return rows


def _read_current_alerts():
    if not ALERT_FILE.exists():
        return []
    rows = []
    with ALERT_FILE.open(newline="", encoding="utf-8") as f:
        for source in csv.DictReader(f):
            symbol = source.get("symbol", "").strip()
            price = _float(source.get("alert_price"))
            if not symbol or price is None:
                continue
            row = {
                "Ticker Name": symbol,
                "Buy Price": f"{price:.2f}",
                "Entry Day SL": source.get("sl_price", ""),
                "Date Time": source.get("alert_time_ist", source.get("alert_date", "")),
                "Alert Type": source.get("alert_type", "BUY") or "BUY",
            }
            row["Alert ID"] = _alert_id(row)
            rows.append(row)
    return rows


def _format_date(value):
    return value.isoformat() if value is not None else ""


def _evaluate_alert(alert, key):
    symbol = alert["Ticker Name"]
    buy_price = _float(alert["Buy Price"])
    sl = _float(alert.get("Entry Day SL", alert.get("SL")))
    alert_ts = pd.to_datetime(alert["Date Time"], errors="coerce")
    if pd.isna(alert_ts) or buy_price is None:
        return {**alert, "Status": "INVALID ALERT"}
    alert_date = alert_ts.date()
    report_date = datetime.now(IST).date()
    base = {
        **alert,
        "Performance As Of Date": report_date.isoformat(),
        "Alert Date": alert_date.isoformat(),
        "Alert Time": alert_ts.strftime("%H:%M:%S"),
    }

    try:
        daily = historical_daily(key, years=2)
        if daily.empty:
            return {**base, "Status": "NO DATA"}
        daily["date"] = pd.to_datetime(daily["timestamp"], errors="coerce").dt.date
        daily = daily.dropna(subset=["date"]).sort_values("date").copy()
        daily["ema10"] = ema(daily["close"], 10)
        daily["ema21"] = ema(daily["close"], 21)
        window = daily[daily["date"] >= alert_date]
        if window.empty:
            return {**base, "Status": "NO DATA AFTER ALERT"}

        exit_event = None
        for day_number, (_, day) in enumerate(window.iterrows(), start=1):
            close = float(day["close"])
            if sl is not None and float(day["low"]) <= sl:
                exit_event = (day["date"], "Entry day SL", sl, day_number)
            elif close < float(day["ema10"]):
                exit_event = (day["date"], "10 EMA break", close, day_number)
            elif close < float(day["ema21"]):
                exit_event = (day["date"], "21 EMA break", close, day_number)
            if exit_event:
                break

        as_of = window.iloc[-1]
        as_of_date = as_of["date"]
        as_of_price = float(as_of["close"])

        if exit_event:
            event_date, event_type, event_price, holding_days = exit_event
            status = "EXITED"
        else:
            event_date = None
            event_type = "Open as of report date"
            event_price = as_of_price
            holding_days = len(window)
            status = "OPEN"
        performance = (event_price / buy_price - 1) * 100
        return {
            **base,
            "SL Hit Date": _format_date(event_date),
            "SL Type": event_type,
            "Holding Days": holding_days,
            "Performance %": f"{performance:.2f}",
            "Status": status,
        }
    except Exception as exc:
        print(f"[{symbol}] swing performance lookup failed: {exc}")
        return {**base, "Status": "ERROR"}


def run():
    records = _read_existing()
    for alert in _read_current_alerts():
        records[alert["Alert ID"]] = {**records.get(alert["Alert ID"], {}), **alert}

    if not records:
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(columns=OUTPUT_COLUMNS).to_csv(OUTPUT_FILE, index=False)
        print("No live alerts found; wrote an empty swing performance sheet.")
        return

    universe = load_nse_equities()
    key_by_symbol = dict(zip(universe.trading_symbol, universe.instrument_key))
    output = []
    for alert in records.values():
        key = key_by_symbol.get(alert["Ticker Name"])
        if not key:
            output.append({**alert, "Status": "NO INSTRUMENT"})
        else:
            output.append(_evaluate_alert(alert, key))

    frame = pd.DataFrame(output)
    for column in OUTPUT_COLUMNS:
        if column not in frame:
            frame[column] = ""
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    frame[OUTPUT_COLUMNS].sort_values(["Date Time", "Ticker Name"]).to_csv(
        OUTPUT_FILE, index=False
    )
    print(f"Wrote {len(frame)} swing performance rows to {OUTPUT_FILE}.")


if __name__ == "__main__":
    run()
