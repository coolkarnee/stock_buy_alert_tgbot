# Stock Pattern Lab - Live Intraday Monitor

An NSE equity alerting tool that connects to the Upstox V3 full-market WebSocket to monitor live intraday momentum breakouts. It screens stocks in real-time and sends Telegram alerts when specific technical conditions are met. This repository is an alert-only scanner. It does not place orders, manage positions, or implement defense and exit rules.

## System Architecture & File Dependencies

The codebase is strictly modular, separating API communication, signal logic, and execution orchestration.

| File | Primary Responsibility | Dependencies |
| :--- | :--- | :--- |
| **`live_monitor.py`** | Main entry point. Manages the WebSocket lifecycle, background threads, and orchestrates the live evaluation loop. | `config.settings`, `scanner.live_engine`, `scanner.live_data`, `scanner.universe`, `scanner.upstox`, `scanner.alerts`. |
| **`scanner/live_engine.py`** | The core logic engine. Builds the historical reference state for each stock, processes 1m candles for opening ranges, and evaluates live ticks against BUY rules. | `config.settings`, `scanner.indicators`. |
| **`scanner/live_data.py`** | Fetches supplementary intraday data (1-minute candles for ORB) and Yahoo Finance total market capitalization. | `scanner.upstox`, `yfinance`. |
| **`scanner/upstox.py`** | Handles Upstox API communication, including instrument master lookups, rate limiting, and multi-threaded historical daily candle downloads. | `config.settings`. |
| **`scanner/universe.py`** | Dynamically builds the stock universe by downloading Nifty constituent CSVs and the NSE IPO tracker. | External HTTP requests only. |
| **`scanner/indicators.py`** | Provides mathematical utility functions for Exponential Moving Averages (EMA) and Average True Range (ATR). | `pandas`. |
| **`scanner/alerts.py`** | Handles HTTP POST requests to the Telegram Bot API to dispatch BUY alerts. | `config.settings`, `requests`. |
| **`config/settings.py`** | Centralized configuration holding environment variables, API keys, and hardcoded technical thresholds. | `os`. |
| **`performance.py`** | After-market evaluation script that checks the closing prices of alerted stocks to calculate the alert-to-close percentage move. | `scanner.alerts`, `scanner.upstox`. |

## Execution Flow & Sequence of Calls

When you run `python live_monitor.py`, the program executes in three distinct phases:

### Phase 1: Pre-Market Initialization (State Building)
1. **Universe Loading:** `live_monitor.run()` calls `load_nse_equities()`, which delegates to `universe.py` to scrape Nifty indices and recent IPOs. It maps these symbols to Upstox instrument keys.
2. **Historical Data Fetching:** `historical_many()` executes a ThreadPool to download ~2 years of daily historical candles for every eligible stock. 
3. **Reference State Construction:** For each stock, `live_engine.build_state()` is called. It calculates historical baseline metrics (EMA21, ATR14, 52-week high/low, prior true ranges, and average volume) and stores them in a memory dictionary to minimize live-tick calculation overhead.

### Phase 2: Live Market Monitoring
1. **WebSocket Connection:** At 09:15 IST, the Upstox `MarketDataStreamerV3` connects and subscribes to all eligible instrument keys.
2. **Tick Ingestion:** As data flows in, the `on_message` callback uses `extract_quote()` to parse the tick and update a thread-safe dictionary.
3. **Evaluation Loop:** A continuous loop runs every 1 second until 15:20 IST:
   * It iterates through the latest quotes.
   * It calls `live_engine.evaluate(state, quote)` for each stock.
   * The engine dynamically updates the daily developing EMA and ATR with the live tick.
   * The engine checks all momentum rules. 
4. **Intraday Data Injection:** If a stock passes all preliminary checks but lacks its Opening Range Breakout (ORB) levels, the monitor pushes a task to a background thread pool to fetch 1-minute intraday candles via `live_data.intraday_minutes()`. Once fetched, `live_engine.update_intraday()` calculates the 3m, 5m, 15m, 30m, and 60m high levels and injects them into the state.
5. **Alert Dispatch:** If `evaluate()` returns a definitive `buy` flag, the symbol is added to a local set to prevent duplicate alerts, logged to `live_alerts.csv`, and sent to Telegram via `alerts.send()`.

### Phase 3: Session End
1. At 15:20 IST, the evaluation loop terminates, the thread pool shuts down, and the WebSocket gracefully disconnects. 
2. A final summary message is sent to Telegram.

## Live Momentum BUY Conditions

An alert is strictly generated when **all** of the following evaluate to true during the live session:
* **Trend:** Price > developing daily EMA21 (IPOs < 21 days old auto-pass).
* **Momentum:** Price > Previous Close.
* **Breakout:** Price > min(Previous High, Previous Close) + 0.5% buffer.
* **Opening Range:** Price > at least one completed time-window high (3m, 5m, 15m, 30m, or 60m).
* **Volume Surge:** Projected RVOL > 1.3x, OR a Pocket Pivot occurs, OR First 30m volume > 10-day average daily volume.
* **Volatility:** Developing ATR14 > 3.9% of price (IPOs auto-pass).
* **Strength & Proximity:** Stock is >= 30% above its 52-week low and <= 20% below its 52-week high (IPOs use 15% from listing high).
* **Liquidity:** Traded turnover > ₹30 Crores, total market cap > ₹1,000 Crores, and price > ₹20.

## Setup & Configuration

**1. Requirements**
* Python 3.12+
* Install dependencies: `python -m pip install -r requirements.txt`

**2. Environment Variables**
Configure these in your shell or workflow environment:
* `UPSTOX_ACCESS_TOKEN`: Required to fetch Upstox historical data and connect the live WebSocket.
* `TELEGRAM_BOT_TOKEN`: Required for sending Telegram alerts.
* `TELEGRAM_CHAT_ID`: The destination chat ID(s). (May contain comma-separated IDs).
*(If Telegram variables are missing, alerts will print to standard output).*

**3. Execution Commands**
Run one of the following scripts from the repository root:
```powershell
# Start the intraday live monitor (must run during NSE market hours)
python live_monitor.py

# Manually verify Telegram connectivity
python test_telegram.py

# Evaluate intraday alert performance (run after 15:30 IST)
python performance.py
```

**4. Testing**
Run the offline regression suite for the live engine to verify momentum logic without contacting Upstox or Telegram:
```powershell
python -m unittest test_live_engine -v
```