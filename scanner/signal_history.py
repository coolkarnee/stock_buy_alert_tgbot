"""Persistent signal-event counts, separate from first-entry trade history."""

import csv
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
SIGNAL_HISTORY_FILE = Path("artifacts") / "signal_history.csv"
FIELDS = ("symbol", "alert_time_ist", "alert_type")


def _timestamp(raw):
    timestamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    return timestamp.replace(tzinfo=IST) if timestamp.tzinfo is None else timestamp.astimezone(IST)


class SignalHistory:
    def __init__(self, path=SIGNAL_HISTORY_FILE):
        self.path = path
        self.events = []
        if path.exists():
            with path.open(newline="", encoding="utf-8") as handle:
                self.extend(csv.DictReader(handle))

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
        return (
            sum(date == day for date in dates),
            sum(day - timedelta(days=6) <= date <= day for date in dates),
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
        today, rolling = self.counts(symbol, _timestamp(timestamp.isoformat()).date())
        return (
            f"\nSignal frequency: {today} today | {rolling} in last 7 days "
            "(all live signal types, including this alert)."
        )
