import os

UPSTOX_ACCESS_TOKEN=os.getenv("UPSTOX_ACCESS_TOKEN","")
TELEGRAM_BOT_TOKEN=os.getenv("TELEGRAM_BOT_TOKEN","")
TELEGRAM_CHAT_ID=os.getenv("TELEGRAM_CHAT_ID","")

MIN_PRICE=20.0
MIN_TURNOVER_CR=20.0
MAX_FROM_52W_HIGH=20.0
EARLY_HISTORY_MAX_FROM_52W_HIGH=15.0
MIN_ATR_PCT=3.49
ATR_REQUIRED_SESSIONS=14
EMA21_REQUIRED_SESSIONS=21
MOMENTUM_STRENGTH_START_SESSIONS=63
MIN_RVOL=1.30
BREAKOUT_BUFFER_PCT=0.50
# EP opening gap must be strictly greater than this percentage.
EP_MIN_GAP_PCT=4.5
# EP alerts require ATR strictly above this percentage after ATR warm-up.
EP_MIN_ATR_PCT=3.0
# Adds IPOs with actual listing dates inside this rolling window to the universe.
IPO_LOOKBACK_MONTHS=12
# Momentum and EP radar opening-range trigger: "close" requires a completed candle close
# above the opening-range high; "spot" triggers when live price crosses it.
OPENING_RANGE_BREAKOUT_MODE="close"
# A previous candle closing more than this percentage below its open uses
# only its high as the next breakout reference.
DARK_RED_CANDLE_DROP_PCT=4.0
LIVE_BLOCK_EXTENDED=False
# Upstox standard limits: 10/s, 500/min, 2,000/30min.
# Smooth requests against the tightest budget, including retries.
UPSTOX_REQUESTS_PER_SECOND=8
UPSTOX_REQUESTS_PER_MINUTE=480
UPSTOX_REQUESTS_PER_30_MINUTES=1950
UPSTOX_MAX_WORKERS=8
UPSTOX_REQUEST_TIMEOUT=15
UPSTOX_MAX_RETRIES=1
