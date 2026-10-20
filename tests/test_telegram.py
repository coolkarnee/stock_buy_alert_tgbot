import requests
from config import settings
from scanner.alerts import send

if __name__ == "__main__":
    send("✅ Telegram test successful — NSE live scanner alert channel is connected.")
    print("Telegram test completed.")
