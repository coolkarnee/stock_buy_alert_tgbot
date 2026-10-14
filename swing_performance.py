"""Build the persistent swing-performance sheet for live alerts."""

import csv
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from scanner.alerts import send_document
from scanner.upstox import historical_daily, load_nse_equities
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
    "Alert Type",
    "Alert Date",
    "Alert Time",
    "SL Hit Date",
    "SL Type",
    "Holding Days",
    "Performance %",
    "Status",
]


def _write_excel(frame):
    """Write a readable, filtered workbook for Telegram and workflow download."""
    report = frame[OUTPUT_COLUMNS].copy()
    report_date = datetime.now(IST).date().isoformat()
    total = len(report)
    open_count = int((report["Status"] == "OPEN").sum()) if total else 0
    exited_count = int((report["Status"] == "EXITED").sum()) if total else 0
    valid_performance = pd.to_numeric(report["Performance %"], errors="coerce")
    average_performance = valid_performance.mean()

    with pd.ExcelWriter(EXCEL_FILE, engine="openpyxl") as writer:
        summary = pd.DataFrame([
            ["Performance as of", report_date],
            ["Total alerts", total],
            ["Open alerts", open_count],
            ["Exited alerts", exited_count],
            ["Average performance", "" if pd.isna(average_performance) else f"{average_performance:+.2f}%"],
        ], columns=["Metric", "Value"])
        summary.to_excel(writer, sheet_name="Summary", index=False)
        report.to_excel(writer, sheet_name="Swing Performance", index=False)

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
    detail_sheet.freeze_panes = "A2"
    widths = {
        "A": 26, "B": 20, "C": 18, "D": 13, "E": 14, "F": 13,
        "G": 13, "H": 13, "I": 14, "J": 22, "K": 13, "L": 15, "M": 15,
    }
    for column, width in widths.items():
        detail_sheet.column_dimensions[column].width = width

    green_fill = PatternFill("solid", fgColor="E2F0D9")
    red_fill = PatternFill("solid", fgColor="FCE4D6")
    yellow_fill = PatternFill("solid", fgColor="FFF2CC")
    for row in range(2, detail_sheet.max_row + 1):
        status = detail_sheet.cell(row, 13)
        performance = detail_sheet.cell(row, 12)
        if status.value == "OPEN":
            status.fill = yellow_fill
        elif status.value == "EXITED":
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
    open_count = int((frame["Status"] == "OPEN").sum()) if total else 0
    exited_count = int((frame["Status"] == "EXITED").sum()) if total else 0
    caption = (
        f"📊 Swing Performance | {report_date}\n"
        f"Alerts: {total} | Open: {open_count} | Exited: {exited_count}"
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
