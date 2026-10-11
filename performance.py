import csv
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

import pandas as pd

from scanner.alerts import send
from scanner.live_data import intraday_minutes
from scanner.upstox import load_nse_equities, current_day_daily

IST = ZoneInfo("Asia/Kolkata")


def _move_pct(price, reference):
    if reference is None or price <= 0:
        return None
    return (reference - price) / price * 100.0


def run():
    alert_file = Path("live_alerts.csv")
    if not alert_file.exists():
        send("📊 LIVE ALERT PERFORMANCE\nNo live BUY alert log was found for today.")
        return

    today_date = datetime.now(IST).date()
    today = today_date.isoformat()

    alerts = []
    with alert_file.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["alert_date"] == today:
                alerts.append(row)

    if not alerts:
        send(f"📊 LIVE ALERT PERFORMANCE | {today}\nNo BUY alerts today.")
        return

    universe = load_nse_equities()
    key_by_symbol = dict(zip(universe.trading_symbol, universe.instrument_key))

    rows = []

    for alert in alerts:
        symbol = alert["symbol"]
        alert_price = float(alert["alert_price"])
        alert_time_raw = alert.get("alert_time_ist", "")
        key = key_by_symbol.get(symbol)

        if not key:
            rows.append(
                (symbol, alert_price, None, None, None, None, "NO INSTRUMENT")
            )
            continue

        try:
            # 1) Today's completed close.
            daily = current_day_daily(key)
            if daily.empty:
                rows.append(
                    (symbol, alert_price, None, None, None, None, "NO CLOSE")
                )
                continue

            daily["timestamp"] = pd.to_datetime(
                daily["timestamp"], utc=True, errors="coerce"
            ).dt.tz_convert(IST)
            day = daily[daily["timestamp"].dt.date == today_date]

            if day.empty:
                rows.append(
                    (symbol, alert_price, None, None, None, None, "NO CLOSE")
                )
                continue

            close = float(day.iloc[-1]["close"])
            close_move_pct = _move_pct(alert_price, close)

            # 2) Highest price after the alert.
            #
            # Upstox historical intraday data is minute-level. To avoid using
            # the part of the alert minute that may have happened before the
            # alert itself, start from the next full minute after the alert.
            high_after_alert = None
            high_move_pct = None

            if alert_time_raw:
                alert_ts = pd.to_datetime(alert_time_raw, utc=True, errors="coerce")
                if pd.notna(alert_ts):
                    alert_ts = alert_ts.tz_convert(IST)
                    intraday = intraday_minutes(key)

                    if not intraday.empty:
                        intraday["timestamp"] = pd.to_datetime(
                            intraday["timestamp"], utc=True, errors="coerce"
                        ).dt.tz_convert(IST)

                        after_alert = intraday[
                            (intraday["timestamp"].dt.date == today_date)
                            & (intraday["timestamp"] > alert_ts.replace(second=0, microsecond=0))
                        ]

                        if not after_alert.empty:
                            high_after_alert = float(after_alert["high"].max())
                            high_move_pct = _move_pct(
                                alert_price, high_after_alert
                            )

            if high_after_alert is None:
                rows.append(
                    (
                        symbol,
                        alert_price,
                        close,
                        close_move_pct,
                        None,
                        None,
                        "NO HIGH DATA",
                    )
                )
                continue

            rows.append(
                (
                    symbol,
                    alert_price,
                    close,
                    close_move_pct,
                    high_after_alert,
                    high_move_pct,
                    "OK",
                )
            )

        except Exception as exc:
            print(f"[{symbol}] performance lookup failed: {exc}")
            rows.append(
                (symbol, alert_price, None, None, None, None, "ERROR")
            )

    lines = [f"📊 LIVE ALERT PERFORMANCE | {today}", ""]

    for (
        symbol,
        alert_price,
        close,
        close_move,
        high_after_alert,
        high_move,
        status,
    ) in rows:
        if status != "OK":
            if close is not None and status == "NO HIGH DATA":
                close_text = (
                    f" | Close ₹{close:.2f} | Move {close_move:+.2f}%"
                )
                lines.append(
                    f"{symbol} | Alert ₹{alert_price:.2f}{close_text} | "
                    f"High after alert: unavailable"
                )
            else:
                lines.append(
                    f"{symbol} | Alert ₹{alert_price:.2f} | {status}"
                )
            continue

        close_emoji = "🟢" if close_move >= 0 else "🔴"
        lines.append(
            f"{close_emoji} {symbol} | Alert ₹{alert_price:.2f} | "
            f"Close ₹{close:.2f} | Close Move {close_move:+.2f}% | "
            f"Post-alert High ₹{high_after_alert:.2f} | "
            f"Max Move {high_move:+.2f}%"
        )

    close_moves = [r[3] for r in rows if r[6] == "OK" and r[3] is not None]
    high_moves = [r[5] for r in rows if r[6] == "OK" and r[5] is not None]

    summary = [""]

    if close_moves:
        close_green = sum(x >= 0 for x in close_moves)
        close_red = sum(x < 0 for x in close_moves)
        close_avg = sum(close_moves) / len(close_moves)
        summary.append(
            f"Close: {len(close_moves)} evaluated | 🟢 {close_green} | "
            f"🔴 {close_red} | Avg {close_avg:+.2f}%"
        )

    if high_moves:
        high_avg = sum(high_moves) / len(high_moves)
        summary.append(
            f"Post-alert High: {len(high_moves)} evaluated | Avg Max Move {high_avg:+.2f}%"
        )

    if len(summary) > 1:
        lines.extend(summary)

    send("\n".join(lines))

    with Path("performance.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "date",
                "symbol",
                "alert_price",
                "close_price",
                "close_move_pct",
                "post_alert_high_price",
                "post_alert_high_move_pct",
                "status",
            ]
        )

        for (
            symbol,
            alert_price,
            close,
            close_move,
            high_after_alert,
            high_move,
            status,
        ) in rows:
            writer.writerow(
                [
                    today,
                    symbol,
                    f"{alert_price:.2f}",
                    "" if close is None else f"{close:.2f}",
                    "" if close_move is None else f"{close_move:.2f}",
                    "" if high_after_alert is None else f"{high_after_alert:.2f}",
                    "" if high_move is None else f"{high_move:.2f}",
                    status,
                ]
            )


if __name__ == "__main__":
    run()
