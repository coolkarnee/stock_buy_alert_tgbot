import csv
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

import pandas as pd

from scanner.alerts import send
from scanner.live_data import intraday_minutes
from scanner.sector import resolve_sector
from scanner.alert_history import first_daily_alerts
from scanner.upstox import load_nse_equities, current_day_daily

IST = ZoneInfo("Asia/Kolkata")
ARTIFACT_DIR = Path("artifacts")


def write_summary(text):
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    (ARTIFACT_DIR / "performance_summary.txt").write_text(
        text.strip() + "\n", encoding="utf-8",
    )


def _move_pct(price, reference):
    if reference is None or price <= 0:
        return None
    return (reference - price) / price * 100.0


def sector_summary(alerts):
    groups = {}
    for alert in alerts:
        sector = alert["sector"]
        group = groups.setdefault(sector, {"types": Counter(), "symbols": set()})
        group["types"][alert.get("alert_type") or "BUY"] += 1
        group["symbols"].add(alert["symbol"])
    lines = ["", "SECTOR ALERT BREAKDOWN"]
    for sector, group in sorted(
        groups.items(), key=lambda item: (-sum(item[1]["types"].values()), item[0])
    ):
        counts = group["types"]
        lines.append(
            f"{sector}: {sum(counts.values())} alerts | "
            f"{counts['BUY']} Momentum BUY | {counts['EP']} EP | "
            f"{len(group['symbols'])} distinct stocks"
            f" | {sum(count for kind, count in counts.items() if kind.startswith('EP_ORB_'))} EP radar ORB"
        )
    lines.append("Counts are signal events, not winning trades; one stock can have multiple alert types.")
    return lines


def run():
    alert_file = ARTIFACT_DIR / "live_alerts.csv"
    if not alert_file.exists():
        report = "📊 LIVE ALERT PERFORMANCE\nNo live BUY alert log was found for today."
        print(report)
        send(report)
        write_summary("No BUY alerts were recorded, so there is no intraday performance conclusion.")
        return

    today_date = datetime.now(IST).date()
    today = today_date.isoformat()

    alerts = []
    with alert_file.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["alert_date"] == today:
                alerts.append(row)

    if not alerts:
        report = f"📊 LIVE ALERT PERFORMANCE | {today}\nNo BUY alerts today."
        print(report)
        send(report)
        write_summary("No BUY alerts were recorded, so there is no intraday performance conclusion.")
        return

    for alert in alerts:
        alert["sector"] = resolve_sector(alert["symbol"], alert.get("sector"))

    entries = first_daily_alerts(alerts)
    universe = load_nse_equities()
    key_by_symbol = dict(zip(universe.trading_symbol, universe.instrument_key))

    rows = []

    for alert in entries:
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

    rows = [
        (*row, alert["sector"], alert.get("alert_type") or "BUY")
        for row, alert in zip(rows, entries)
    ]
    lines = [f"📊 LIVE ALERT PERFORMANCE | {today}", ""]
    lines.append(f"{len(alerts)} signal events | {len(entries)} first-entry stock trades; later signals are confirmations.")

    for (
        symbol,
        alert_price,
        close,
        close_move,
        high_after_alert,
        high_move,
        status,
        sector,
        alert_type,
    ) in rows:
        symbol = f"{symbol} ({alert_type}) | Sector: {sector}"
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

    lines.extend(sector_summary(alerts))

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

    if close_moves:
        close_green = sum(x >= 0 for x in close_moves)
        close_red = sum(x < 0 for x in close_moves)
        conclusion = (
            f"{close_green}/{len(close_moves)} alerts closed positive, "
            f"{close_red} negative; average close move {sum(close_moves) / len(close_moves):+.2f}%."
        )
    else:
        conclusion = "No alerts had usable completed-close data for an intraday performance conclusion."
    lines.extend(["", f"Conclusion: {conclusion}"])

    report = "\n".join(lines)
    # Keep the result visible in the GitHub Actions log even when Telegram
    # delivery is unavailable or rejected by the API.
    print(report)
    send(report)
    write_summary(conclusion)

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    with (ARTIFACT_DIR / "performance.csv").open("w", newline="", encoding="utf-8") as f:
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
                "sector",
                "alert_type",
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
            sector,
            alert_type,
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
                    sector,
                    alert_type,
                ]
            )


if __name__ == "__main__":
    run()
