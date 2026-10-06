# NSE Live Momentum Scanner

An intraday WebSocket-based NSE equity alerting tool using Upstox real-time prices and Yahoo Finance market capitalization data.

This repository is a **live-only, alert-only scanner**. It does not place orders, manage positions, or persist state between runs. It monitors the NSE market during trading hours (09:15–15:20 IST) and sends Telegram alerts when momentum conditions align.

## What It Does

| Entry point | Behavior |
| --- | --- |
| `live_monitor.py` | Connects to the Upstox V3 WebSocket, builds intraday state for ~1,400 NSE equities, evaluates live quotes every second, and sends at most one MOMENTUM BUY alert per symbol per trading day |
| `test_live_engine.py` | Offline automated tests for momentum rules, IPO exemptions, ORB candles, feed parsing, and mocked Telegram dispatch |
| `test_telegram.py` | Manual Telegram connectivity smoke test |

## Setup

Requires Python 3.12. Install dependencies in a virtual environment:

```powershell
py -3.12 -m venv .venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Set environment variables before running:

- `UPSTOX_ACCESS_TOKEN` — Required. Upstox historical and streaming data access token
- `TELEGRAM_BOT_TOKEN` — Required. Telegram Bot API token for sending alerts
- `TELEGRAM_CHAT_ID` — Required. Telegram chat ID (comma-separated for multiple recipients)

Run from the repository root:

```powershell
python live_monitor.py
python test_telegram.py
```

## Data & Universe

The universe is rebuilt each run from the union of:
- Nifty 500 constituents
- Nifty MidSmallcap 400 constituents
- Nifty Microcap 250 constituents
- Nifty Smallcap 250 constituents
- NSE IPOs listed since 2026-01-01

The scanner downloads ~2 years of daily history and ~2 years of intraday 1-minute candles for all universe stocks. Upstox requests use bearer-token auth, a shared rate limiter (450 req/min), 15-second timeout, and one retry for transient errors (429, 500, 502, 503, 504).

Universe sources are external (Nifty indices CSVs, NSE IPO API). Individual source failures print warnings; loading continues if at least one source succeeds. The run fails only if the final universe is empty.

## Live Momentum BUY Criteria

A stock receives a Telegram MOMENTUM BUY alert when **ALL 11 checks pass**:

| # | Check | Rule | Notes |
|---|-------|------|-------|
| 1 | **Trend** | `price > EMA21` | Exempted for IPOs (first 21 trading days) |
| 2 | **Extension** | EMA10 ≤ 10% above EMA10, EMA21 ≤ 20% above EMA21 | Optional (disabled by default; enable via `LIVE_BLOCK_EXTENDED=True`) |
| 3 | **Volume Surge** | RVOL > 1.30x **OR** pocket pivot **OR** first-30m volume > avg 10-day volume | At least one must trigger |
| 4 | **Momentum** | `price > prev_close` | Green day from previous close |
| 5 | **Breakout** | `price > min(prev_high, prev_close) × 1.005` | Prior-day breakout buffer |
| 6 | **Strength** | Rally ≥ 30% from 52-week low | Auto-pass for IPOs |
| 7 | **Proximity** | ≤ 20% below 52-week high (≤ 15% for IPOs) | Distance from 52-week high |
| 8 | **Volatility** | ATR-14 > 3.9% of price | Auto-pass for IPOs |
| 9 | **Price Floor** | `price > ₹20` | Minimum rupee value |
| 10 | **Liquidity** | 50-session avg price × avg volume > ₹30 Cr | Turnover depth |
| 11 | **ORB** | `price > at least one of {3m, 5m, 15m, 30m, 60m} opening-range high` | **MANDATORY** — must break an ORB window |

**Key calculations:**
- **RVOL** = current volume ÷ 20-session average volume (projected to 375-min session)
- **Pocket pivot** = today closes above open AND volume > max red-candle volume in prior 10 days
- **Rally** = (current_price ÷ 52w_low - 1) × 100
- **Distance from high** = (52w_high - current_price) ÷ 52w_high × 100
- **ATR-14** = 14-bar rolling average of true range, as % of price
- **ORB high** = highest high in the opening N minutes (3, 5, 15, 30, or 60), measured from 09:15 IST

**IPO Treatment:**
Stocks with < 252 completed daily candles are treated as IPOs. For IPOs:
- Trend check auto-passes through day 21
- Strength (rally) check auto-passes
- Volatility (ATR) check auto-passes
- Proximity threshold is 15% instead of 20%

EMA10 and EMA21 are updated intraday using the formula: `new_ema = old_ema + α × (price - old_ema)` where α = 2/(n+1).

Turnover uses actual cumulative daily volume (not projected). Market cap is fetched from Yahoo Finance only after all other checks pass.

## Alert Format

When all conditions align, a Telegram MOMENTUM BUY alert is sent (max once per symbol per trading day):

```
MOMENTUM BUY - SYMBOL
Price Rs 123.45 | PDC buffer Rs 124.56
RVOL 1.52x | ATR 4.23% | IPO False
From high 12.34% | Rally 45.67%
Turnover Rs 125.4 Cr
ORB break: 3m, 15m | Pocket pivot False | First 30m surge True
Extension EMA10 8.5% / EMA21 15.3%
Prior volume dry-up: yes
NIFTYSMLCAP250: above EMA21 (informational)
Live scanner only - no order placed.
```

## Configuration

Edit `config/settings.py` to adjust thresholds (all as Python constants, not env vars):

| Setting | Default | Purpose |
|---------|---------|----------|
| `MIN_PRICE` | 20.0 | Minimum stock price (₹) |
| `MIN_TURNOVER_CR` | 30.0 | Minimum 50-session avg turnover (₹ Cr) |
| `MIN_SWING_RETURN` | 30.0 | Minimum rally from 52-week low (%) |
| `MAX_FROM_52W_HIGH` | 20.0 | Maximum distance from 52-week high (%) |
| `MIN_ATR_PCT` | 3.9 | Minimum ATR-14 as % of price |
| `MIN_RVOL` | 1.30 | Minimum relative volume multiplier |
| `BREAKOUT_BUFFER_PCT` | 0.50 | Breakout margin above prior high/close (%) |
| `LIVE_BLOCK_EXTENDED` | False | If True, enforce EMA extension limits |

Upstox API settings:

| Setting | Default | Purpose |
|---------|---------|----------|
| `UPSTOX_REQUESTS_PER_MINUTE` | 450 | Rate limit (Upstox allows 500/min) |
| `UPSTOX_MAX_WORKERS` | 8 | Parallel workers for historical data |
| `UPSTOX_REQUEST_TIMEOUT` | 15 | Request timeout (seconds) |
| `UPSTOX_MAX_RETRIES` | 1 | Retries for transient errors |

## Workflows

GitHub Actions can run the monitor on a schedule. Create `.github/workflows/live-alerts.yml`:

```yaml
name: NSE Live Scanner
on:
  schedule:
    - cron: '10 3 * * 1-5'  # 03:40 UTC (09:10 IST), weekdays
jobs:
  monitor:
    runs-on: ubuntu-latest
    timeout-minutes: 360
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v4
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt
      - env:
          UPSTOX_ACCESS_TOKEN: ${{ secrets.UPSTOX_ACCESS_TOKEN }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: python live_monitor.py
```

The job will wait until 09:15 IST before starting live monitoring and exit at 15:20 IST.

## Repository Map

| File | Purpose |
|------|----------|
| `live_monitor.py` | WebSocket lifecycle, feed parsing, alert dispatch |
| `scanner/live_engine.py` | Per-tick signal evaluation and state updates |
| `scanner/live_data.py` | Market-cap lookup, Smallcap 250 instrument lookup, intraday candle requests |
| `scanner/upstox.py` | Instrument master, historical data, rate limiting |
| `scanner/universe.py` | Nifty constituent and NSE IPO collection |
| `scanner/indicators.py` | EMA and ATR calculations |
| `scanner/alerts.py` | Telegram delivery |
| `config/settings.py` | API credentials and scanner thresholds |
| `test_live_engine.py` | Offline momentum rule tests |
| `test_telegram.py` | Manual Telegram connectivity check |

## Limitations

- **No order placement or position management** — alerts only; manual execution required
- **No persistent state** — restart the monitor each trading day for a fresh scan
- **WebSocket only** — uses Upstox V3 full-market feed; no fallback to REST polling
- **One alert per symbol per day** — alert marked sent before Telegram delivery; failures are logged but not retried
- **Market hours only** — evaluation stops at 15:20 IST; exits cleanly
- **External data dependencies** — failures in Nifty indices CSVs, NSE IPO API, or Upstox will cause partial/full run failure
- **No backtesting** — live rules only; use `test_live_engine.py` offline for validation

## Testing

Run offline momentum tests:

```powershell
python -m unittest test_live_engine -v
```

Test Telegram connectivity:

```powershell
python test_telegram.py
```
