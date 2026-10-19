import json
import time
from threading import Lock
from urllib.parse import quote
from pathlib import Path

import requests
from config import settings
from scanner.market_context import market_context

TELEGRAM_SAFE_MESSAGE_LENGTH = 3900


def _text_length(text):
    return sum(2 if ord(character) > 0xFFFF else 1 for character in text)


def _text_chunks(text):
    """Keep each part below Telegram's limit, including UTF-16 emoji units."""
    chunks = []
    start = 0
    length = 0
    for index, character in enumerate(text):
        units = 2 if ord(character) > 0xFFFF else 1
        if length + units > TELEGRAM_SAFE_MESSAGE_LENGTH:
            chunks.append(text[start:index])
            start = index
            length = 0
        length += units
    if start < len(text):
        chunks.append(text[start:])
    return chunks


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
    chunks = _text_chunks(message) or [""]

    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        for chunk in chunks:
            print(chunk)
        if chart_symbol:
            print(f"Open TradingView Chart: {tradingview_chart_url(chart_symbol)}")
        return

    for chat_id in _chat_ids():
        _send_text(chat_id, chunks, chart_symbol)


def _chart_button(chart_symbol):
    return {
        "inline_keyboard": [[{
            "text": "Open TradingView Chart",
            "url": tradingview_chart_url(chart_symbol),
        }]]
    }


def _send_text(chat_id, chunks, chart_symbol=None):
    ok = True
    for chunk_number, chunk in enumerate(chunks):
        payload = {"chat_id": chat_id, "text": chunk}
        # Attach the chart button to the first part of a split alert.
        if chart_symbol and chunk_number == 0:
            payload["reply_markup"] = _chart_button(chart_symbol)
        ok = _post("sendMessage", chat_id, json=payload, timeout=20) and ok
    return ok


# Telegram limits: ~1 msg/s per private chat, 20 msgs/min per group, ~30 msgs/s per bot.
PRIVATE_CHAT_INTERVAL = 1.1
GROUP_CHAT_INTERVAL = 3.1
BOT_SEND_INTERVAL = 1 / 25
_last_sent = {}
_last_bot_sent = 0.0
_send_lock = Lock()


def _throttle(chat_id):
    global _last_bot_sent
    interval = GROUP_CHAT_INTERVAL if str(chat_id).startswith("-") else PRIVATE_CHAT_INTERVAL
    with _send_lock:
        wait = max(
            _last_sent.get(chat_id, 0) + interval,
            _last_bot_sent + BOT_SEND_INTERVAL,
        ) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_sent[chat_id] = time.monotonic()
        _last_bot_sent = _last_sent[chat_id]


def _post(method, chat_id, attempts=3, **kwargs):
    """POST to the Bot API with per-chat pacing and 429/5xx retries; return True on success."""
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/{method}"
    for attempt in range(1, attempts + 1):
        _throttle(chat_id)
        try:
            response = requests.post(url, **kwargs)
        except requests.RequestException as exc:
            print(f"Telegram {method} failed for chat_id={chat_id} (attempt {attempt}): {exc}")
            time.sleep(2)
            continue
        if response.ok:
            return True
        if response.status_code == 429:
            try:
                retry_after = int(response.json().get("parameters", {}).get("retry_after", 5))
            except (ValueError, TypeError, AttributeError):
                retry_after = 5
            retry_after = max(1, retry_after)
            print(f"Telegram rate limit for chat_id={chat_id}; retrying in {retry_after}s.")
            time.sleep(retry_after)
            continue
        if response.status_code >= 500:
            print(f"Telegram {method} server error for chat_id={chat_id}: HTTP {response.status_code}")
            time.sleep(2)
            continue
        print(f"Telegram {method} failed for chat_id={chat_id}: HTTP {response.status_code} | {response.text}")
        return False
    print(f"Telegram {method} delivery exhausted {attempts} attempts for chat_id={chat_id}.")
    return False


def send_document(path, caption=None):
    """Send a local file to every configured Telegram chat."""
    document = Path(path)
    caption = str(caption or "")
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        print(f"Telegram document ready: {document}")
        if caption:
            print(caption)
        return

    try:
        document_bytes = document.read_bytes()
    except OSError as exc:
        print(f"Telegram document read failed for {document}: {exc}")
        return
    fits = _text_length(caption) <= TELEGRAM_CAPTION_LIMIT
    for chat_id in _chat_ids():
        delivered = _post(
            "sendDocument", chat_id,
            data={"chat_id": chat_id, "caption": caption if fits else ""},
            files={"document": (
                document.name, document_bytes,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )},
            timeout=60,
        )
        if caption and (not fits or not delivered):
            _send_text(chat_id, _text_chunks(caption))


TELEGRAM_CAPTION_LIMIT = 1024


def send_photo(image_bytes, caption=None, chart_symbol=None, filename="chart.png"):
    """Send a chart with the text; per chat, fall back to text-only if the photo fails.

    If the caption exceeds Telegram's 1024-char limit, the photo and text go as two
    messages and the chart button stays on the text message.
    """
    caption = str(caption or "")
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        print(f"Telegram photo ready ({len(image_bytes)} bytes)")
        if caption:
            print(caption)
        if chart_symbol:
            print(f"Open TradingView Chart: {tradingview_chart_url(chart_symbol)}")
        return

    fits = _text_length(caption) <= TELEGRAM_CAPTION_LIMIT
    data = {"caption": caption if fits else ""}
    if chart_symbol and fits:
        data["reply_markup"] = json.dumps(_chart_button(chart_symbol))
    chunks = _text_chunks(caption)
    for chat_id in _chat_ids():
        photo_ok = _post(
            "sendPhoto", chat_id,
            data={"chat_id": chat_id, **data},
            files={"photo": (filename, image_bytes, "image/png")},
            timeout=60,
        )
        if chunks and (not photo_ok or not fits):
            if not photo_ok:
                print(f"Photo not delivered to chat_id={chat_id}; sending text alert instead.")
            _send_text(chat_id, chunks, chart_symbol)

def format_result(r,state):
    context = r.get("market_context") or market_context("NA", None)[1]
    return f"{state} | {r['symbol']}\nSector: {r.get('sector', 'NA')}\nPrice ₹{r['price']:.2f} | Score {r['score']}/7\nPivot ₹{r['pivot']:.2f} | ATR {r['atr_pct']:.1f}%\nRS20 {r['rs20']:.1f}% | 52W distance {r['from_52w_high']:.1f}%\nSwing {r['swing_return']:.1f}% | RVOL {r['rvol']:.1f}x\nVCP={r['vcp']} | Breakout={r['vcp_breakout']} | Pocket Pivot={r['pocket_pivot']}\n{context}"
