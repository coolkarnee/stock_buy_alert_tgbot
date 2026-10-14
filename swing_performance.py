"""Build the persistent swing-performance sheet for live alerts."""

import csv
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from scanner.alerts import send, send_document
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

VISIBLE_COLUMNS = [column for column in OUTPUT_COLUMNS if column != "Alert ID"]
VISIBLE_DESCRIPTIONS = [
    description
    for column, description in zip(OUTPUT_COLUMNS, COLUMN_DESCRIPTIONS)
    if column != "Alert ID"
]
VISIBLE_COLUMNS.insert(0, "No.")
VISIBLE_DESCRIPTIONS.insert(0, "Sequence number restarting within each ISO week.")


def _write_excel(frame):
    """Write the dashboard and readable weekly workbook for Telegram/download."""
    report = frame[OUTPUT_COLUMNS].copy()
    report_date = datetime.now(IST).date().isoformat()
    total = len(report)
    open_count = int((report["Status"] == "Trade open").sum()) if total else 0
    partial_count = int((report["Status"] == "Partial exit").sum()) if total else 0
    exited_count = int((report["Status"] == "Full exit").sum()) if total else 0
    valid_performance = pd.to_numeric(report["Performance %"], errors="coerce")
    average_performance = valid_performance.mean()

    workbook = Workbook()
    summary_sheet = workbook.active
    summary_sheet.title = "Dashboard"
    detail_sheet = workbook.create_sheet("Swing Performance")
    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)
    border = Border(bottom=Side(style="thin", color="D9E2F3"))

    # Dashboard: overall trade KPIs and status breakdown.
    numeric_performance = valid_performance.dropna()
    closed = report[report["Status"] == "Full exit"]
    closed_performance = pd.to_numeric(closed["Performance %"], errors="coerce").dropna()
    win_rate = (
        (closed_performance > 0).mean() * 100 if len(closed_performance) else None
    )
    dashboard_rows = [
        ["Performance as of", report_date],
        ["Total trades", total],
        ["Trade open", open_count],
        ["Partial exits", partial_count],
        ["Full exits", exited_count],
        ["Closed-trade win rate", "NA" if win_rate is None else f"{win_rate:.2f}%"],
        ["Max performance", "NA" if numeric_performance.empty else f"{numeric_performance.max():+.2f}%"],
        ["Mean performance", "NA" if numeric_performance.empty else f"{numeric_performance.mean():+.2f}%"],
        ["Min performance", "NA" if numeric_performance.empty else f"{numeric_performance.min():+.2f}%"],
    ]
    summary_sheet.merge_cells("A1:E1")
    summary_sheet["A1"] = "SWING PERFORMANCE DASHBOARD"
    summary_sheet["A1"].fill = header_fill
    summary_sheet["A1"].font = Font(color="FFFFFF", bold=True, size=16)
    summary_sheet["A1"].alignment = Alignment(horizontal="center")
    summary_sheet["A3"] = "Overall trade metrics"
    summary_sheet["D3"] = "Status overview"
    for cell in (summary_sheet["A3"], summary_sheet["D3"]):
        cell.fill = PatternFill("solid", fgColor="D9EAF7")
        cell.font = Font(bold=True, color="1F1F1F")
    for row_number, (metric, value) in enumerate(dashboard_rows, start=4):
        summary_sheet.cell(row_number, 1, metric)
        summary_sheet.cell(row_number, 2, value)
    status_rows = [
        ["Trade open", open_count],
        ["Partial exit", partial_count],
        ["Full exit", exited_count],
    ]
    for row_number, (status, count) in enumerate(status_rows, start=4):
        summary_sheet.cell(row_number, 4, status)
        summary_sheet.cell(row_number, 5, count)
    for row in summary_sheet.iter_rows(min_row=3, max_row=12, min_col=1, max_col=5):
        for cell in row:
            cell.border = border
            cell.alignment = Alignment(vertical="top")
    summary_sheet.freeze_panes = "A4"
    summary_sheet.sheet_properties.tabColor = "1F4E78"
    summary_sheet.column_dimensions["A"].width = 28
    summary_sheet.column_dimensions["B"].width = 22
    summary_sheet.column_dimensions["D"].width = 18
    summary_sheet.column_dimensions["E"].width = 14

    # Weekly detail sections. Alert IDs stay in the CSV but are intentionally
    # not included in the shared workbook.
    report["Alert Date"] = report["Alert Date"].fillna("").astype(str)
    grouped = report.sort_values(["Alert Date", "Alert Time", "Ticker Name"])
    detail_sheet.sheet_properties.tabColor = "70AD47"
    widths = {
        "A": 8, "B": 20, "C": 18, "D": 13, "E": 14, "F": 15,
        "G": 13, "H": 13, "I": 13, "J": 14, "K": 28, "L": 13,
        "M": 18, "N": 18, "O": 18,
    }
    for column, width in widths.items():
        detail_sheet.column_dimensions[column].width = width

    green_fill = PatternFill("solid", fgColor="E2F0D9")
    red_fill = PatternFill("solid", fgColor="FCE4D6")
    yellow_fill = PatternFill("solid", fgColor="FFF2CC")
    current_row = 2  # leave one blank row before the first weekly section
    for (year, week_number), week_frame in grouped.groupby(
        grouped["Alert Date"].map(
            lambda value: (
                pd.to_datetime(value, errors="coerce").isocalendar().year,
                pd.to_datetime(value, errors="coerce").isocalendar().week,
            )
            if pd.notna(pd.to_datetime(value, errors="coerce")) else (0, 0)
        ), sort=False
    ):
        if current_row > 2:
            current_row += 1
        detail_sheet.merge_cells(
            start_row=current_row, start_column=1,
            end_row=current_row, end_column=len(VISIBLE_COLUMNS)
        )
        title = detail_sheet.cell(current_row, 1, f"WEEK {week_number} | {year}")
        title.fill = PatternFill("solid", fgColor="155EEF")
        title.font = Font(color="FFFFFF", bold=True, size=13)
        title.alignment = Alignment(horizontal="center", vertical="center")
        detail_sheet.row_dimensions[current_row].height = 25
        current_row += 1
        for column_number, header in enumerate(VISIBLE_COLUMNS, start=1):
            cell = detail_sheet.cell(current_row, column_number, header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center")
        current_row += 1
        for column_number, description in enumerate(VISIBLE_DESCRIPTIONS, start=1):
            cell = detail_sheet.cell(current_row, column_number, description)
            cell.fill = PatternFill("solid", fgColor="D9EAF7")
            cell.font = Font(italic=True, color="1F1F1F")
            cell.alignment = Alignment(wrap_text=True, vertical="top")
        detail_sheet.row_dimensions[current_row].height = 42
        current_row += 1
        for sequence, (_, source_row) in enumerate(week_frame.iterrows(), start=1):
            values = [sequence] + [source_row.get(column, "") for column in VISIBLE_COLUMNS[1:]]
            for column_number, value in enumerate(values, start=1):
                if pd.isna(value):
                    value = ""
                cell = detail_sheet.cell(current_row, column_number, value)
                cell.border = border
                cell.alignment = Alignment(vertical="top")
            status_cell = detail_sheet.cell(current_row, 14)
            performance_cell = detail_sheet.cell(current_row, 13)
            if status_cell.value == "Trade open":
                status_cell.fill = yellow_fill
            elif status_cell.value == "Partial exit":
                status_cell.fill = PatternFill("solid", fgColor="FCE4D6")
            elif status_cell.value == "Full exit":
                performance_value = pd.to_numeric(performance_cell.value, errors="coerce")
                status_cell.fill = green_fill if performance_value >= 0 else red_fill
            performance_value = pd.to_numeric(performance_cell.value, errors="coerce")
            if pd.notna(performance_value):
                performance_cell.value = float(performance_value)
                performance_cell.number_format = '0.00"%"'
            current_row += 1
    detail_sheet.freeze_panes = "A5"
    detail_sheet.auto_filter.ref = f"A1:{chr(64 + len(VISIBLE_COLUMNS))}{max(1, current_row - 1)}"
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


def _send_exit_alerts(frame, previous_records):
    """Notify Telegram once for today's newly recorded partial/full exits."""
    today = datetime.now(IST).date().isoformat()
    events = []
    for _, row in frame.iterrows():
        status = str(row.get("Status", ""))
        action_date = str(row.get("SL Hit Date", ""))
        if status not in {"Partial exit", "Full exit"} or action_date != today:
            continue
        alert_id = str(row.get("Alert ID", ""))
        previous = previous_records.get(alert_id, {})
        previous_marker = (
            str(previous.get("Status", "")),
            str(previous.get("SL Hit Date", "")),
            str(previous.get("SL Type", "")),
        )
        current_marker = (status, action_date, str(row.get("SL Type", "")))
        if previous_marker == current_marker:
            continue
        performance = str(row.get("Performance %", ""))
        performance_value = _float(performance)
        performance_text = (
            f"{performance_value:+.2f}%"
            if performance_value is not None else performance
        )
        latest_close = str(row.get("Latest Close", ""))
        events.append(
            "\n".join([
                f"{status.upper()} | {row.get('Ticker Name', '')}",
                f"SL Type: {row.get('SL Type', '')} | Latest Close: Rs {latest_close}",
                f"Performance: {performance_text} | Holding Days: {row.get('Holding Days', '')}",
            ])
        )
    if not events:
        return
    message = "📣 SWING EXIT ALERT | " + today + "\n\n" + "\n\n".join(events)
    send(message)


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
    previous_records = _read_existing()
    records = dict(previous_records)
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
    _send_exit_alerts(ordered, previous_records)
    _send_excel_report(ordered[OUTPUT_COLUMNS])
    print(f"Wrote {len(frame)} swing performance rows to {OUTPUT_FILE}.")


if __name__ == "__main__":
    run()
