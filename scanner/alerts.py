import requests
from config import settings

def _chat_ids():
    raw = settings.TELEGRAM_CHAT_ID
    return [x.strip() for x in raw.split(",") if x.strip()]

def send(message):
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        print(message)
        return

    for chat_id in _chat_ids():
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": message},
                timeout=20,
            )
            if not r.ok:
                print(f"Telegram send failed for chat_id={chat_id}: HTTP {r.status_code} | {r.text}")
                continue
        except Exception as exc:
            print(f"Telegram send failed for chat_id={chat_id}: {exc}")
            continue

def format_result(r,state):
    return f"{state} | {r['symbol']}\nPrice ₹{r['price']:.2f} | Score {r['score']}/7\nPivot ₹{r['pivot']:.2f} | ATR {r['atr_pct']:.1f}%\nRS20 {r['rs20']:.1f}% | 52W distance {r['from_52w_high']:.1f}%\nSwing {r['swing_return']:.1f}% | RVOL {r['rvol']:.1f}x\nVCP={r['vcp']} | Breakout={r['vcp_breakout']} | Pocket Pivot={r['pocket_pivot']}"