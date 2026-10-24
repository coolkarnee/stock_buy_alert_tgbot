"""Settings isolated from the existing equity scanner."""

from datetime import time

COMMODITIES = ("GOLDTEN", "SILVERMIC", "CRUDEOILM", "NATGASMINI")
DAILY_EMA_FAST = 10
DAILY_EMA_SLOW = 20
INTRADAY_EMA_FAST = 10
INTRADAY_EMA_SLOW = 20
EMA_TOUCH_TOLERANCE_PCT = 0.25
ADX_PERIOD = 14
ADX_DEFAULT_THRESHOLD = 25.0
# MCX starting values. These are deliberately configuration, not code, so
# they can be recalibrated after the first walk-forward sample.
ADX_THRESHOLDS = {
    "GOLDTEN": 22.0,
    "SILVERMIC": 25.0,
    "CRUDEOILM": 25.0,
    "NATGASMINI": 28.0,
}
BREAKOUT_LOOKBACK = 20
BREAKOUT_VOLUME_MULTIPLIER = 1.20
MEAN_REVERSION_ADX_BUFFER = 0.0
MEAN_REVERSION_ATR_MULTIPLIER = 1.5
MEAN_REVERSION_BOLLINGER_STD = 2.0
ROLL_MIN_DAYS_TO_EXPIRY = 15
DAILY_LOOKBACK_DAYS = 180
INTRADAY_LOOKBACK_DAYS = 120
MARKET_OPEN = time(9, 0)
MARKET_CLOSE = time(23, 45)
# The equity scanner runs in a separate process and has its own limiter. Keep
# this process conservative so both workflows remain under the account quota.
POLL_SECONDS = 240
