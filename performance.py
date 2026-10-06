import csv
from datetime import date
from pathlib import Path

from scanner.alerts import send
from scanner.upstox import load_nse_equities, historical_daily

def run():
    alert_file = Path("live_alerts.csv")
    if not alert_file.exists():
        send("📊 LIVE ALERT PERFORMANCE\nNo live BUY alert log was found for today.")
        return

    today = date.today().isoformat()
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
        key = key_by_symbol.get(symbol)
        if not key:
            rows.append((symbol, alert_price, None, None, "NO INSTRUMENT"))
            continue

        try:
            df = historical_daily(key, years=1)
            if df.empty:
                rows.append((symbol, alert_price, None, None, "NO DATA"))
                continue

            day = df[df["timestamp"].dt.date.astype(str) == today]
            if day.empty:
                rows.append((symbol, alert_price, None, None, "NO CLOSE"))
                continue

            close = float(day.iloc[-1]["close"])
            move = (close - alert_price) / alert_price * 100.0
            rows.append((symbol, alert_price, close, move, "OK"))
        except Exception as exc:
            print(f"[{symbol}] performance lookup failed: {exc}")
            rows.append((symbol, alert_price, None, None, "ERROR"))

    lines = [f"📊 LIVE ALERT PERFORMANCE | {today}", ""]
    for symbol, alert_price, close, move, status in rows:
        if status != "OK":
            lines.append(f"{symbol} | Alert ₹{alert_price:.2f} | {status}")
            continue
        emoji = "🟢" if move >= 0 else "🔴"
        lines.append(
            f"{emoji} {symbol} | Alert ₹{alert_price:.2f} | "
            f"Close ₹{close:.2f} | Move {move:+.2f}%"
        )

    valid = [r[3] for r in rows if r[4] == "OK"]
    if valid:
        green = sum(x >= 0 for x in valid)
        red = sum(x < 0 for x in valid)
        avg = sum(valid) / len(valid)
        lines += ["", f"Summary: {len(valid)} evaluated | 🟢 {green} | 🔴 {red} | Avg {avg:+.2f}%"]

    send("\n".join(lines))

    with Path("performance.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "symbol", "alert_price", "close_price", "move_pct", "status"])
        for symbol, alert_price, close, move, status in rows:
            writer.writerow([today, symbol, f"{alert_price:.2f}", "" if close is None else f"{close:.2f}", "" if move is None else f"{move:.2f}", status])

if __name__ == "__main__":
    run()
