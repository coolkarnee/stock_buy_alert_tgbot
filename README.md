# Stock Pattern Lab - Live Intraday Monitor

An automated NSE equity screening and alerting tool. This repository is a Python port of the **Momentum Alert Pro (Pine Script v6)** institutional screener. It connects to the Upstox V3 full-market WebSocket to monitor live intraday momentum breakouts across the entire NSE equity universe, dispatching TradingView-formatted alerts directly to Telegram.

This repository is an alert-only scanner. It does not place orders or manage broker positions. 

## System Architecture

The engine asynchronously evaluates three distinct categories on every live market tick:

### 1. Momentum Buy (Core Screener)
Triggers when all strict momentum and volume conditions align during the intraday session:
* **Trend:** Price > developing daily 21 EMA. (IPOs <= 21 days old auto-pass).
* **Momentum:** Price > Previous Close (Green Day).
* **Breakout:** Price crosses min(Previous High, Previous Close) + 0.5% buffer.
* **Intraday ORB:** Live price clears the high of at least one completed Opening Range (3m, 5m, 15m, 30m, or 60m).
* **Volume Surge:** Requires 1 of 4: RVOL > 1.3x, Pocket Pivot, 1st 30m Vol Surge, or Absolute Vol > 9M.
* **Volatility & Strength:** 14-Day ATR > 3.9%, and Rally >= 30% from 52W Low (IPOs auto-pass).
* **Proximity:** Within 20% of 52W High (or 15% of listing high for IPOs).
* **Liquidity:** Daily Turnover (Price × Volume) > ₹20 Cr and Price > ₹20.

### 2. Independent Radars (Trend-Filtered)
Tracks milestones only if the stock is in an uptrend (Close > 21 EMA):
* **Gap Up:** Fires on the opening tick if Open is >= 5% above Previous Close.
* **New Highs:** Alerts on new 1M, 3M, 6M, or 52W highs.
* **Performance Milestones:** Exact-day crossovers of high returns (1W>19%, 1M>20%, 3M>30%, 6M>50%, 1Y>100%).

### 3. Trade Management (Time-Gated Sells)
If enabled, runs only between 15:15 and 15:30 IST to evaluate moving average violations on a daily closing basis:
* **Stop Loss (S-EDL):** Live price drops below the Entry Day's Low.
* **Phase 1 Exit (IPO Days 1-10):** Closes below the lowest low of the prior 3 days (S-3DL).
* **Phase 2 Exit (IPO Days 11-21):** Closes below the 10 EMA (S-10E).
* **Phase 3 Exit (Standard Days 22+):** Closes below the 21 EMA (S-21E).

## Repository Map

| File | Primary Responsibility |
| :--- | :--- |
| **`live_monitor.py`** | Main entry point. Manages the WebSocket lifecycle, quote ingestion, and alert dispatch. |
| **`scanner/live_engine.py`** | The core state machine. Calculates historical baselines, updates ORB, and evaluates tick logic. |
| **`scanner/upstox.py`** | Handles API authentication, rate limiting, historical downloads, and builds the full NSE EQ universe. |
| **`scanner/live_data.py`** | Fetches 1-minute intraday candles to construct Opening Range breakouts. |
| **`scanner/indicators.py`** | Mathematical utility functions for EMA and ATR calculations. |
| **`scanner/alerts.py`** | Telegram HTTP POST dispatcher. |
| **`config/settings.py`** | Centralized configuration for API keys, thresholds, and module toggles. |
| **`test_live_engine.py`** | Offline regression test suite for core buy, sell, and radar logic. |
| **`test_upstox.py`** | Offline regression test suite for universe extraction filtering. |
| **`.github/workflows/live-monitor.yml`** | Automated GitHub Actions execution schedule. |

## Execution Flow

1. **Pre-Market State Building (09:05 IST):** The script fetches the entire active NSE Equity universe directly from Upstox (~2,400 symbols). It uses a thread pool to download up to 2 years of daily history for each stock, calculating reference baselines (EMA, ATR, Prior Lows, Volume averages) to keep the live loop lightweight.
2. **Live Monitoring (09:15 to 15:20 IST):** Connects to the Upstox `MarketDataStreamerV3`. For every tick, it updates developing daily metrics and evaluates the Momentum, Radar, and Exit rules. Missing ORB data triggers asynchronous 1-minute candle fetches.
3. **Dispatch:** Alerts are logged locally to `live_alerts.csv` and sent immediately via Telegram to prevent duplicate firing.

## Setup & Configuration

**1. Requirements**
* Python 3.12+
* Install dependencies: `python -m pip install -r requirements.txt`

**2. Environment Variables**
Configure these in your shell or GitHub Actions repository secrets:
* `UPSTOX_ACCESS_TOKEN`: Your 1-Year Upstox Analytics Token.
* `TELEGRAM_BOT_TOKEN`: Provided by @BotFather.
* `TELEGRAM_CHAT_ID`: Your target user ID or group ID.

*(Note: Toggles for Radars, Trade Exits, and Momentum filters are located in `config/settings.py`)*

**3. Execution Commands**
Run one of the following scripts from the repository root:
```powershell
# Start the intraday live monitor (requires market hours)
python live_monitor.py

# Run unit tests to verify signal logic locally
python -m unittest test_live_engine -v
python -m unittest test_upstox -v