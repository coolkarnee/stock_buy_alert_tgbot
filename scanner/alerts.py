from urllib.parse import quote

import requests
from config import settings

TELEGRAM_SAFE_MESSAGE_LENGTH = 3900

def _chat_ids():
    raw = settings.TELEGRAM_CHAT_ID
    return [x.strip() for x in raw.split(",") if x.strip()]

def tradingview_chart_url(symbol):
    """Return a cross-platform TradingView chart URL for an NSE symbol."""
    clean_symbol = str(symbol or "").strip().upper()
    if not clean_symbol:
        return None
    tradingview_symbol = clean_symbol if ":" in clean_symbol else f"NSE:{clean_symbol}"
    return f"https://www.tradingview.com/chart/?symbol={quote(tradingview_symbol, safe='')}"


def send(message, chart_symbol=None):
    message = str(message)
    chunks = [
        message[index:index + TELEGRAM_SAFE_MESSAGE_LENGTH]
        for index in range(0, len(message), TELEGRAM_SAFE_MESSAGE_LENGTH)
    ] or [""]

    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        for chunk in chunks:
            print(chunk)
        if chart_symbol:
            print(f"Open TradingView Chart: {tradingview_chart_url(chart_symbol)}")
        return

    reply_markup = None
    if chart_symbol:
        reply_markup = {
            "inline_keyboard": [[{
                "text": "Open TradingView Chart",
                "url": tradingview_chart_url(chart_symbol),
            }]]
        }

    for chat_id in _chat_ids():
        for chunk_number, chunk in enumerate(chunks):
            try:
                payload = {"chat_id": chat_id, "text": chunk}
                # Attach the chart button to the first part of a split alert.
                if reply_markup and chunk_number == 0:
                    payload["reply_markup"] = reply_markup
                r = requests.post(
                    f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage",
                    json=payload,
                    timeout=20,
                )
                if not r.ok:
                    print(f"Telegram send failed for chat_id={chat_id}: HTTP {r.status_code} | {r.text}")
            except Exception as exc:
                print(f"Telegram send failed for chat_id={chat_id}: {exc}")
            continue

def format_result(r,state):
    return f"{state} | {r['symbol']}\nPrice ₹{r['price']:.2f} | Score {r['score']}/7\nPivot ₹{r['pivot']:.2f} | ATR {r['atr_pct']:.1f}%\nRS20 {r['rs20']:.1f}% | 52W distance {r['from_52w_high']:.1f}%\nSwing {r['swing_return']:.1f}% | RVOL {r['rvol']:.1f}x\nVCP={r['vcp']} | Breakout={r['vcp_breakout']} | Pocket Pivot={r['pocket_pivot']}"
