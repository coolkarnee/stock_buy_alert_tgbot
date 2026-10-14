"""Build the persistent swing-performance sheet for live alerts."""

import csv
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from scanner.alerts import send_document
from scanner.upstox import current_day_daily, historical_daily, load_nse_equities
from scanner.indicators import ema

ARTIFACT_DIR = Path("artifacts")
OUTPUT_FILE = ARTIFACT_DIR / "swing_performance.csv"
EXCEL_FILE = ARTIFACT_DIR / "swing_performance.xlsx"
ALERT_FILE = Path("live_alerts.csv")
IST = ZoneInfo("Asia/Kolkata")

OUTPUT_COLUMNS = [
    "Alert ID",
    "Performance As Of Date",
    "Ticker Name",
    "Buy Price",
    "Entry Day SL",
    "Latest Close",
    "Alert Type",
    "Alert Date",
    "Alert Time",
    "SL Hit Date",
    "SL Type",
    "Holding Days",
    "Performance %",
    "Status",
    "Market Context",
]

COLUMN_DESCRIPTIONS = [
    "Unique alert identifier.",
    "Date through which the performance was calculated.",
    "NSE ticker symbol.",
    "Alert/buy price used for return calculation.",
    "Previous trading day's low; initial protective stop.",
    "Latest daily closing price as of Performance As Of Date.",
    "BUY or EP alert type.",
    "Date on which the alert was generated.",
    "IST time at which the alert was generated.",
    "Date of the latest exit or partial action.",
    "Prev Day Low SL, Breakeven SL, Profit Booking, 10 DEMA CB, or 20 DEMA CB.",
    "Trading sessions from alert through the latest action/report date.",
    "Weighted realized return plus the marked-to-market return on any remaining size.",
    "Trade open, Partial exit, Full exit, or a data status.",
    "Bull Market, Bear Market, or Choppy Market at entry.",
]


def _write_excel(frame):
    """Write a readable, filtered workbook for Telegram and workflow download."""
    report = frame[OUTPUT_COLUMNS].copy()
    report_date = datetime.now(IST).date().isoformat()
    total = len(report)
    open_count = int((report["Status"] == "Trade open").sum()) if total else 0
    partial_count = int((report["Status"] == "Partial exit").sum()) if total else 0
    exited_count = int((report["Status"] == "Full exit").sum()) if total else 0
    valid_performance = pd.to_numeric(report["Performance %"], errors="coerce")
    average_performance = valid_performance.mean()

    with pd.ExcelWriter(EXCEL_FILE, engine="openpyxl") as writer:
        summary = pd.DataFrame([
            ["Performance as of", report_date],
            ["Total alerts", total],
            ["Trade open", open_count],
            ["Partial exits", partial_count],
            ["Exited alerts", exited_count],
            ["Average performance", "" if pd.isna(average_performance) else f"{average_performance:+.2f}%"],
        ], columns=["Metric", "Value"])
        summary.to_excel(writer, sheet_name="Summary", index=False)
        descriptions = pd.DataFrame([COLUMN_DESCRIPTIONS], columns=OUTPUT_COLUMNS)
        descriptions.to_excel(
            writer, sheet_name="Swing Performance", index=False
        )
        report.to_excel(
            writer, sheet_name="Swing Performance", index=False,
            header=False, startrow=2
        )

    workbook = load_workbook(EXCEL_FILE)
    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)
    border = Border(bottom=Side(style="thin", color="D9E2F3"))

    summary_sheet = workbook["Summary"]
    summary_sheet.sheet_properties.tabColor = "1F4E78"
    detail_sheet = workbook["Swing Performance"]
    detail_sheet.sheet_properties.tabColor = "70AD47"

    for sheet in (summary_sheet, detail_sheet):
        sheet.freeze_panes = "A2"
        for cell in sheet[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center")
        for row in sheet.iter_rows():
            for cell in row:
                cell.border = border
                cell.alignment = Alignment(vertical="top")

    summary_sheet.column_dimensions["A"].width = 25
    summary_sheet.column_dimensions["B"].width = 22
    detail_sheet.auto_filter.ref = detail_sheet.dimensions
    detail_sheet.freeze_panes = "A3"
    for cell in detail_sheet[2]:
        cell.fill = PatternFill("solid", fgColor="D9EAF7")
        cell.font = Font(italic=True, color="1F1F1F")
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    detail_sheet.row_dimensions[2].height = 42
    widths = {
        "A": 26, "B": 20, "C": 18, "D": 13, "E": 14, "F": 15,
        "G": 13, "H": 13, "I": 13, "J": 14, "K": 28, "L": 13,
        "M": 18, "N": 18, "O": 18,
    }
    for column, width in widths.items():
        detail_sheet.column_dimensions[column].width = width

    green_fill = PatternFill("solid", fgColor="E2F0D9")
    red_fill = PatternFill("solid", fgColor="FCE4D6")
    yellow_fill = PatternFill("solid", fgColor="FFF2CC")
    for row in range(3, detail_sheet.max_row + 1):
        status = detail_sheet.cell(row, 14)
        performance = detail_sheet.cell(row, 13)
        if status.value == "Trade open":
            status.fill = yellow_fill
        elif status.value == "Partial exit":
            status.fill = PatternFill("solid", fgColor="FCE4D6")
        elif status.value == "Full exit":
            status.fill = green_fill if float(performance.value or 0) >= 0 else red_fill
        try:
            performance.value = float(performance.value)
            performance.number_format = '0.00"%"'
        except (TypeError, ValueError):
            pass

    workbook.save(EXCEL_FILE)
    return EXCEL_FILE


def _send_excel_report(frame):
    report_date = datetime.now(IST).date().isoformat()
    total = len(frame)
    open_count = int((frame["Status"] == "Trade open").sum()) if total else 0
    partial_count = int((frame["Status"] == "Partial exit").sum()) if total else 0
    exited_count = int((frame["Status"] == "Full exit").sum()) if total else 0
    caption = (
        f"📊 Swing Performance | {report_date}\n"
        f"Alerts: {total} | Open: {open_count} | Partial: {partial_count} | Exited: {exited_count}"
    )
    send_document(EXCEL_FILE, caption)


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
                "Market Context": source.get("market_context", ""),
            }
            row["Alert ID"] = _alert_id(row)
            rows.append(row)
    return rows


def _format_date(value):
    return value.isoformat() if value is not None else ""


def _ist_dates(values):
    """Convert Upstox timestamps to Indian calendar dates."""
    return pd.to_datetime(values, errors="coerce", utc=True).dt.tz_convert(IST).dt.date


def _market_rules(context):
    """Return the profit trigger and booking size for the entry regime."""
    if "BULL" in str(context).upper():
        return 15.0, 0.20
    return 10.0, 0.30


def _weighted_return(realized, remaining, buy_price, mark_price):
    return (realized + remaining * (mark_price / buy_price - 1)) * 100


def _evaluate_alert(alert, key):
    symbol = alert["Ticker Name"]
    buy_price = _float(alert["Buy Price"])
    sl = _float(alert.get("Entry Day SL", alert.get("SL")))
    market_context = alert.get("Market Context", "")
    profit_trigger, booking_size = _market_rules(market_context)
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
            daily = pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume", "oi"]
            )
        daily["date"] = _ist_dates(daily["timestamp"])
        daily = daily.dropna(subset=["date"]).sort_values("date").copy()

        # The historical endpoint can omit the still-recent/current session.
        # Add Upstox's current-day candle so a same-day alert is evaluated
        # against today's close instead of producing blank performance fields.
        current = pd.DataFrame()
        if alert_date == report_date or report_date not in set(daily["date"]):
            try:
                current = current_day_daily(key)
            except Exception as exc:
                print(f"[{symbol}] current-day candle lookup failed: {exc}")
        if not current.empty:
            current["date"] = _ist_dates(current["timestamp"])
            current = current.dropna(subset=["date"])
            current = current[current["date"] >= alert_date]
            if not current.empty:
                known_dates = set(daily["date"])
                daily = pd.concat(
                    [daily, current[~current["date"].isin(known_dates)]],
                    ignore_index=True,
                ).sort_values("date")
        daily["ema10"] = ema(daily["close"], 10)
        daily["ema21"] = ema(daily["close"], 21)
        window = daily[daily["date"] >= alert_date]
        if window.empty:
            return {**base, "Status": "NO DATA AFTER ALERT"}

        remaining = 1.0
        realized = 0.0
        triggered = False
        ten_dema_exit_done = False
        event_date = None
        event_type = "Prev Day Low SL"
        status = "Trade open"
        holding_days = len(window)

        for day_number, (_, day) in enumerate(window.iterrows(), start=1):
            close = float(day["close"])
            high = float(day["high"])
            day_date = day["date"]

            if not triggered:
                if sl is not None and close < sl:
                    event_date = day_date
                    event_type = "Prev Day Low SL"
                    realized = close / buy_price - 1
                    remaining = 0.0
                    holding_days = day_number
                    status = "Full exit"
                    break
                if high >= buy_price * (1 + profit_trigger / 100):
                    sold = booking_size
                    realized += sold * (close / buy_price - 1)
                    remaining -= sold
                    triggered = True
                    event_date = day_date
                    event_type = f"Profit booking +{profit_trigger:.0f}%"
                    status = "Partial exit"
                    holding_days = day_number
                    continue
            else:
                if close < buy_price:
                    realized += remaining * (buy_price / buy_price - 1)
                    remaining = 0.0
                    event_date = day_date
                    event_type = "Breakeven SL"
                    holding_days = day_number
                    status = "Full exit"
                    break
                if close < float(day["ema21"]):
                    realized += remaining * (close / buy_price - 1)
                    remaining = 0.0
                    event_date = day_date
                    event_type = "20 DEMA CB"
                    holding_days = day_number
                    status = "Full exit"
                    break
                if close < float(day["ema10"]) and not ten_dema_exit_done:
                    sold = remaining * 0.50
                    realized += sold * (close / buy_price - 1)
                    remaining -= sold
                    ten_dema_exit_done = True
                    event_date = day_date
                    event_type = "10 DEMA CB"
                    holding_days = day_number
                    status = "Partial exit"

        as_of = window.iloc[-1]
        as_of_date = as_of["date"]
        as_of_price = float(as_of["close"])
        if event_date is None:
            event_date = None
            event_type = "Open; initial SL active"
        performance = _weighted_return(realized, remaining, buy_price, as_of_price)
        return {
            **base,
            "Performance As Of Date": as_of_date.isoformat(),
            "SL Hit Date": _format_date(event_date),
            "SL Type": event_type,
            "Latest Close": f"{as_of_price:.2f}",
            "Holding Days": holding_days,
            "Performance %": f"{performance:.2f}",
            "Status": status,
            "Market Context": market_context,
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
        empty = pd.DataFrame(columns=OUTPUT_COLUMNS)
        empty.to_csv(OUTPUT_FILE, index=False)
        _write_excel(empty)
        _send_excel_report(empty)
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
    sort_columns = [column for column in ("Date Time", "Ticker Name") if column in frame]
    ordered = frame.sort_values(sort_columns) if sort_columns else frame
    ordered[OUTPUT_COLUMNS].to_csv(OUTPUT_FILE, index=False)
    _write_excel(ordered[OUTPUT_COLUMNS])
    _send_excel_report(ordered[OUTPUT_COLUMNS])
    print(f"Wrote {len(frame)} swing performance rows to {OUTPUT_FILE}.")


if __name__ == "__main__":
    run()
