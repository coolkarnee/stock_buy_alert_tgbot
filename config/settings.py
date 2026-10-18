import os

UPSTOX_ACCESS_TOKEN=os.getenv("UPSTOX_ACCESS_TOKEN","")
TELEGRAM_BOT_TOKEN=os.getenv("TELEGRAM_BOT_TOKEN","")
TELEGRAM_CHAT_ID=os.getenv("TELEGRAM_CHAT_ID","")

MIN_PRICE=20.0
MIN_TURNOVER_CR=20.0
MAX_FROM_52W_HIGH=20.0
MIN_ATR_PCT=3.9
MIN_RVOL=1.30
BREAKOUT_BUFFER_PCT=0.50
# EP opening gap must be strictly greater than this percentage.
EP_MIN_GAP_PCT=4.5
# Momentum and EP radar opening-range trigger: "close" requires a completed candle close
# above the opening-range high; "spot" triggers when live price crosses it.
OPENING_RANGE_BREAKOUT_MODE="close"
# A previous candle closing more than this percentage below its open uses
# only its high as the next breakout reference.
DARK_RED_CANDLE_DROP_PCT=4.0
LIVE_BLOCK_EXTENDED=False
CANDIDATE_SCORE_REQUIRED=4
CANDIDATE_NEAR_HIGH_PCT=15.0
CANDIDATE_MIN_SWING=20.0

VCP_SEGMENT=5
VCP_BASE_LEN=30
VCP_RANGE_SHRINK=0.80
VCP_VOLUME_RATIO=0.80
VCP_ATR_RATIO=0.80
VCP_MAX_TIGHTNESS=8.0
VCP_UPPER_BASE_RATIO=0.65
VCP_MIN_QUALITY_SCORE=4
ORIGINAL_VCP_VOLUME_RATIO=1.0

MAX_UNIVERSE=1500

# Upstox standard limits: 10/s, 500/min, 2,000/30min.
# Smooth requests against the tightest budget, including retries.
UPSTOX_REQUESTS_PER_SECOND=8
UPSTOX_REQUESTS_PER_MINUTE=480
UPSTOX_REQUESTS_PER_30_MINUTES=1950
UPSTOX_MAX_WORKERS=8
UPSTOX_REQUEST_TIMEOUT=15
UPSTOX_MAX_RETRIES=1
