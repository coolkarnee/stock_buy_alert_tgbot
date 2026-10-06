import os

UPSTOX_ACCESS_TOKEN = os.getenv("UPSTOX_ACCESS_TOKEN", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# --- Pine Script Evaluator Thresholds ---
MIN_PRICE = 20.0
MIN_TURNOVER_CR = 20.0  # Dropped from 30 to 20 to match PineScript
MIN_SWING_RETURN = 30.0
MAX_FROM_52W_HIGH = 20.0
MIN_ATR_PCT = 3.9
MIN_RVOL = 1.30
BREAKOUT_BUFFER_PCT = 0.50

# --- Advanced Toggles ---
LIVE_BLOCK_EXTENDED = False  # If True, enforces 10E(<=10%) and 21E(<=20%) bounds
REQ_STOCK_MOMO = True        # Enforces Stock 10E > 21E Sloping Up (5E > 10E for IPOs)
REQ_IDX_MOMO = False         # Enforces Index 10E > 21E Sloping Up
STRICT_VCP = False           # If True, enforces Vol Dry-Up to buy

# --- Independent Radars & Trade Management ---
ALERT_GAP_UP = True
ALERT_NEW_HIGHS = False
ALERT_PERF = False
ENABLE_SELLS = False         # If True, runs closing-basis sells between 15:15 - 15:30 IST

# --- System API Configs ---
UPSTOX_REQUESTS_PER_MINUTE = 450
UPSTOX_MAX_WORKERS = 8
UPSTOX_REQUEST_TIMEOUT = 15
UPSTOX_MAX_RETRIES = 1