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
