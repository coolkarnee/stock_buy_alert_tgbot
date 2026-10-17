"""Select one performance entry per stock and Indian trading date."""

import pandas as pd


def first_daily_alerts(rows, symbol_field="symbol", time_field="alert_time_ist",
                       date_field="alert_date"):
    ordered = []
    invalid = []
    for index, row in enumerate(rows):
        raw_time = row.get(time_field) or row.get(date_field)
        timestamp = pd.to_datetime(raw_time, errors="coerce")
        if pd.isna(timestamp):
            print(f"[{row.get(symbol_field, '')}] invalid alert time {raw_time!r}; retained for data-status reporting.")
            invalid.append(row)
            continue
        timestamp = (
            timestamp.tz_localize("Asia/Kolkata") if timestamp.tzinfo is None
            else timestamp.tz_convert("Asia/Kolkata")
        )
        ordered.append((timestamp, index, row))
    seen = set()
    selected = []
    for timestamp, _, row in sorted(ordered, key=lambda item: (item[0], item[1])):
        key = (str(row[symbol_field]).strip().upper(), timestamp.date())
        if key not in seen:
            seen.add(key)
            selected.append(row)
    return selected + invalid
