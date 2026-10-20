"""Persistent signal-event counts, separate from first-entry trade history."""

import csv
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
SIGNAL_HISTORY_FILE = Path("artifacts") / "signal_history.csv"
TRADING_DAYS_FILE = Path("artifacts") / "trading_sessions.csv"
FIELDS = ("symbol", "alert_time_ist", "alert_type")
TRADING_DAY_FIELDS = ("date",)


def _timestamp(raw):
    timestamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    return timestamp.replace(tzinfo=IST) if timestamp.tzinfo is None else timestamp.astimezone(IST)


def record_trading_sessions(timestamps, path=TRADING_DAYS_FILE):
    dates = set()
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                try:
                    dates.add(datetime.fromisoformat(row["date"]).date())
                except (KeyError, TypeError, ValueError):
                    continue
    except (FileNotFoundError, OSError):
        pass
    for value in timestamps:
        try:
            dates.add(_timestamp(value).date())
        except (TypeError, ValueError):
            continue
    if not dates:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=TRADING_DAY_FIELDS)
        writer.writeheader()
        writer.writerows({"date": day.isoformat()} for day in sorted(dates)[-750:])


class SignalHistory:
    def __init__(self, path=SIGNAL_HISTORY_FILE, trading_days_path=None):
        self.path = path
        self.trading_days_path = trading_days_path or path.with_name("trading_sessions.csv")
        self.events = []
        if path.exists():
            with path.open(newline="", encoding="utf-8") as handle:
                self.extend(csv.DictReader(handle))

    def record_sessions(self, timestamps):
        record_trading_sessions(timestamps, self.trading_days_path)

    def extend(self, rows):
        seen = {tuple(event[field] for field in FIELDS) for event in self.events}
        for row in rows:
            event = {field: str(row.get(field) or "").strip() for field in FIELDS}
            event["symbol"] = event["symbol"].upper()
            try:
                event["alert_time_ist"] = _timestamp(event["alert_time_ist"]).isoformat()
            except ValueError:
                raise ValueError(f"Invalid signal-history timestamp: {event!r}") from None
            if not event["symbol"] or not event["alert_type"]:
                raise ValueError(f"Incomplete signal-history event: {event!r}")
            key = tuple(event[field] for field in FIELDS)
            if key not in seen:
                self.events.append(event)
                seen.add(key)

    def counts(self, symbol, day):
        symbol = symbol.strip().upper()
        dates = [
            _timestamp(event["alert_time_ist"]).date()
            for event in self.events if event["symbol"] == symbol
        ]
        try:
            with self.trading_days_path.open(newline="", encoding="utf-8") as handle:
                trading_days = {
                    datetime.fromisoformat(row["date"]).date()
                    for row in csv.DictReader(handle)
                    if row.get("date")
                }
        except (FileNotFoundError, OSError, KeyError, TypeError, ValueError):
            trading_days = set()
        if not trading_days:
            return sum(date == day for date in dates), None
        trading_days.add(day)
        recent_sessions = set(sorted(value for value in trading_days if value <= day)[-7:])
        return (
            sum(date == day for date in dates),
            len(set(dates) & recent_sessions),
        )

    def record(self, symbol, kind, timestamp):
        event = {"symbol": symbol, "alert_type": kind, "alert_time_ist": timestamp.isoformat()}
        before = len(self.events)
        self.extend([event])
        if len(self.events) != before:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            needs_header = not self.path.exists() or self.path.stat().st_size == 0
            with self.path.open("a", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=FIELDS)
                if needs_header:
                    writer.writeheader()
                writer.writerow(self.events[-1])
        intraday, alert_days = self.counts(symbol, _timestamp(timestamp.isoformat()).date())
        daily_text = (
            "NA" if alert_days is None
            else f"{alert_days} of last 7 trading days"
        )
        return (
            f"\nIntraday Alert Count: {intraday} today | "
            f"Daily Alert Count: {daily_text}"
        )
