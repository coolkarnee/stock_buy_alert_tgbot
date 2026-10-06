# Stock Pattern Lab

An NSE equity screening and alerting tool. It has an unchanged daily historical VCP scan and an intraday WebSocket momentum BUY monitor using Upstox prices and Yahoo Finance market capitalization.

This repository is an alert-only scanner. It does not place orders, manage positions, persist signal/lifecycle state between runs, or implement defense and exit rules. The word “lifecycle” in the workflow name is not evidence that those stages are implemented.

## What Runs Today

| Entry point | Behavior |
| --- | --- |
| `main.py` | Downloads the current universe and about two years of daily history, evaluates each stock, prints a scan summary, sends Telegram alerts for BUY results, and writes `scan_results.csv` if there are results or per-symbol failures to report. |
| `live_monitor.py` | Builds historical reference state, connects to the Upstox V3 full-market WebSocket, evaluates live quotes each second, and sends at most one momentum BUY alert per symbol per day within that process. |
| `test_live_engine.py` | Offline automated tests for momentum rules, IPO exemptions, ORB candles, feed parsing, and mocked Telegram dispatch. |
| `test_telegram.py` | Sends a fixed Telegram connectivity test message. This is a manual smoke test, not an automated test suite. |

There are no command-line options. The batch function accepts an optional `limit` when called from Python, but its normal script invocation scans the full loaded universe. The configured `MAX_UNIVERSE` value is currently not applied.

## Setup

Requires Python 3.12 in the checked-in GitHub Actions workflows. Install dependencies in a virtual environment:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Set `UPSTOX_ACCESS_TOKEN` for Upstox historical and streaming data. Set both `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` to deliver Telegram messages. `TELEGRAM_CHAT_ID` may contain comma-separated chat IDs. If either Telegram setting is absent, alerts are printed to standard output instead of sent.

Run one of the scripts from the repository root:

```powershell
python main.py
python live_monitor.py
python test_telegram.py
```

The Telegram test script does not need the Upstox token, though the workflow supplies it. The repository does not include a checked-in `.env` loader; configure environment variables in the shell, IDE, or workflow secrets.

## Data And Universe

The universe is rebuilt each run. `scanner/universe.py` collects the union of constituents from Nifty 500, Nifty MidSmallcap 400, Nifty Microcap 250, and Nifty Smallcap 250 CSVs, plus NSE IPOs with a listing date on or after 2026-01-01. Symbols are uppercased and de-duplicated. `scanner/upstox.py` downloads the Upstox instrument master and retains matching `NSE_EQ` instruments of type `EQ` with normal security type.

Universe sources are external services, not bundled files. Individual source failures are printed as warnings; loading can continue if another source returns symbols. The run stops if no symbols are collected. Instruments not found in Upstox's instrument master are omitted.

Both scanners fetch approximately two years of daily candles for each eligible stock. Batch scanning uses Nifty 500 as its benchmark and six worker threads, and requires 260 daily bars. Live state preparation uses `UPSTOX_MAX_WORKERS` (default eight), removes candles dated today, and requires at least one completed daily bar. Live market context uses Nifty Smallcap 250. Failed or empty history requests are skipped.

Upstox historical requests use bearer-token authentication, a shared rate limiter configured for 450 requests per minute, a 15-second default timeout, and one retry for selected transient HTTP statuses or request exceptions. Universe CSVs, the NSE IPO tracker, and the instrument master use separate request paths and do not share all of those retry/rate-limit behaviors.

## Batch Analysis Rules

For a stock with enough history, `scanner/engine.py` calculates:

- EMA-21 from closing prices and ATR-14 as a 14-bar rolling simple average of true range.
- The 52-week high and low as the maximum daily high and minimum daily low over the most recent 252 bars.
- Distance below the 52-week high and percentage rise from the 52-week low.
- Relative volume (`rvol`) as the latest daily volume divided by the average volume over the preceding 20 sessions.
- A pocket-pivot flag when the latest candle closes above its open and its volume exceeds the largest volume on a red candle in the previous ten sessions.
- Twenty-session relative performance (`rs20`) versus Nifty 500, when enough benchmark history is available. This is reported, but is not a candidate or BUY filter.

### VCP

`scanner/vcp.py` evaluates two definitions independently:

- **Original VCP:** the average volume of the three sessions before the latest bar is below the average for the preceding 20 sessions.
- **Optimized VCP:** scores six checks: two successive range contractions, range shrinkage, ATR contraction, volume dry-up, a tight recent range, and a close in the upper part of the preceding base. At least four checks pass to qualify.

Either definition sets `true_vcp`. The optimized VCP's pivot is the highest high in the preceding 30 bars, excluding the latest bar. A VCP breakout requires the relevant VCP condition and a close/price at least 0.50% above its pivot. The VCP flag alone is not a BUY signal.

### Candidate Score

The candidate score counts these six conditions: price above EMA-21, within 15% of the 52-week high, true VCP, at least a 20% rise from the 52-week low, minimum turnover, and positive momentum versus the previous close. Four of six makes `candidate` true. Candidate status is reported in the CSV and console, but does not by itself send an alert.

Alert text currently prints the score as `/7`, although the score has six components. This is a display-label defect; the score itself is a count out of six.

### BUY Filters

The unchanged batch BUY signals require every condition below:

| Filter | Default rule |
| --- | --- |
| Minimum price | Price at least ₹20 |
| Trend | Price above EMA-21 |
| Momentum | Price above the previous close |
| Volume confirmation | Relative volume at least 1.30x, or pocket pivot is true |
| Breakout | Normal breakout or VCP breakout is true |
| Volatility | ATR-14 at least 3.9% of price |
| Swing | At least 30% above the 52-week low |
| Proximity to high | No more than 20% below the 52-week high |
| Turnover | Price multiplied by volume at least ₹30 crore |

The normal batch breakout compares the latest close with the previous session's high or close, whichever is higher, plus 0.50%.

## Live Momentum BUY Rules

Live selection does not use VCP or the batch candidate score. All mandatory conditions must align:

- Current price is above the developing daily EMA21, except through listing day 21.
- Volume confirmation is projected RVOL strictly above 1.3x, a pocket pivot, or completed first-30-minute volume above the preceding 10-session average daily volume.
- Price is above yesterday's close and strictly above either yesterday's high or close plus 0.5%.
- Rally from the 52-week low is at least 30%; distance from the 52-week high is at most 20%.
- Developing daily ATR14 is strictly above 3.9% of current price.
- Price is strictly above Rs 20, total company market cap is strictly above Rs 1,000 Cr, and price times cumulative daily volume is strictly above Rs 30 Cr.
- Price is strictly above at least one completed 3m, 5m, 15m, 30m, or 60m opening-range high, measured from 09:15 IST.

Stocks with fewer than 252 completed daily candles are treated as IPOs: rally and ATR checks auto-pass, and proximity must be within 15% of the listing high. This uses available history as a listing-age proxy; incomplete provider history may therefore misclassify a stock. A first-day listing without a previous daily candle cannot be evaluated against yesterday's close.

EMA10/EMA21 extension is shown in alerts but is not a blocking filter by default. Set `LIVE_BLOCK_EXTENDED=True` to require at most 10% above EMA10 and 20% above EMA21. Prior three-session average volume below the preceding 20-session average is informational only and never blocks a BUY. NIFTYSMLCAP250 versus its developing EMA21 is also informational; an unavailable benchmark does not block alerts.

RVOL projects cumulative volume to a 375-minute session using elapsed session time and the preceding 20-session average. Turnover uses actual cumulative volume, not projected volume. Live EMA and ATR incorporate the developing daily candle, so they may change before the close.

Yahoo Finance supplies INR total market cap through `yfinance` for the NSE symbol. Market cap and current-day Upstox one-minute candles are fetched in background workers only after the other price filters pass. Unknown market cap blocks BUY and is retried after five minutes. Missing/incomplete opening candles cannot establish an ORB; these requests are retried at most once per minute per stock until all five ranges are available. The first eligible alert can be delayed by provider latency, queues, or rate limits. Starting after the open still reconstructs the full opening ranges from candles, rather than approximating them from observed ticks.

Run the offline regression suite with `python -m unittest test_live_engine -v`. It does not contact Telegram or Upstox. Real-time delivery additionally requires valid Upstox and Telegram credentials and a running monitor each trading day.

## Outputs And Alerts

The batch scan writes `scan_results.csv` in the current working directory when the combined results are non-empty. Successful analyses include the signal flags, score, VCP fields, measurements, failed BUY filters, and data status. Stocks whose history download or analysis failed are included with a failure status and error text. If there are no successful rows and no recorded failures, no CSV is written.

The batch output is sorted with BUYs and candidates first, followed by VCP quality and score. The console prints scan health, counts, and up to 30 candidates. Only BUY results are sent as stock alerts; candidate-only results are not.

The live monitor sends only momentum BUY messages, at most once per symbol per trading day within that process. Restart the monitor each trading day; it exits at 15:20 IST. There is no database or file-backed state to suppress repeats across separate runs on the same day.

Telegram delivery uses the Telegram Bot API `sendMessage` endpoint. A non-success response or request exception is printed and processing continues. Missing Telegram credentials cause the message to be printed locally.

## Configuration

Environment variables read in `config/settings.py`:

| Variable | Required for | Default |
| --- | --- | --- |
| `UPSTOX_ACCESS_TOKEN` | Fetching Upstox data and connecting the live feed | Empty; Upstox requests fail without it |
| `TELEGRAM_BOT_TOKEN` | Sending Telegram alerts | Empty; alerts print locally if not configured |
| `TELEGRAM_CHAT_ID` | Selecting Telegram recipient(s) | Empty; alerts print locally if not configured |

`UPSTOX_MAX_WORKERS` is a Python constant used by live historical-data preparation (default eight), not an environment variable. Batch concurrency is fixed at six in `main.py`.

The screening thresholds and VCP parameters are Python constants in `config/settings.py`, not environment variables. They include `MIN_PRICE=20`, `MIN_TURNOVER_CR=30`, `MIN_SWING_RETURN=30`, `MAX_FROM_52W_HIGH=20`, `MIN_ATR_PCT=3.9`, `MIN_RVOL=1.30`, `BREAKOUT_BUFFER_PCT=0.50`, and `CANDIDATE_SCORE_REQUIRED=4`.

## Automation

GitHub Actions workflows run on weekdays using UTC schedules, and can also be started manually:

| Workflow | Schedule | Action |
| --- | --- | --- |
| `.github/workflows/daily-scan.yml` | 03:30 UTC (09:00 IST) | Runs `main.py`, then uploads `scan_results.csv` for seven days. It fails artifact upload if the file was not created. |
| `.github/workflows/live-alerts.yml` | 03:40 UTC (09:10 IST) | Runs `live_monitor.py`; the process waits until 09:15 IST before connecting/monitoring. The job timeout is six hours. |
| `.github/workflows/test-telegram.yml` | Manual only | Runs the fixed Telegram smoke-test message. |

The workflows expect `UPSTOX_ACCESS_TOKEN`, `TELEGRAM_BOT_TOKEN`, and `TELEGRAM_CHAT_ID` to be configured as repository or organization secrets. The daily scan and live monitor run as separate jobs; the batch run does not feed state to the live process.

## Repository Map

| File | Responsibility |
| --- | --- |
| `main.py` | Batch scan orchestration and CSV/console reporting |
| `live_monitor.py` | WebSocket lifecycle, feed parsing, and BUY alert dispatch |
| `scanner/universe.py` | Nifty constituent and NSE IPO universe construction |
| `scanner/upstox.py` | Instrument lookup, historical requests, rate limiting, and data conversion |
| `scanner/indicators.py` | EMA and ATR calculations |
| `scanner/vcp.py` | Original and optimized VCP evaluation |
| `scanner/engine.py` | Historical per-stock metrics, candidate score, and BUY filters |
| `scanner/live_engine.py` | Completed-bar state and per-tick signal evaluation |
| `scanner/live_data.py` | Live market-cap lookup, Smallcap 250 instrument lookup, and intraday candle requests |
| `scanner/alerts.py` | Telegram dispatch and batch alert formatting |
| `config/settings.py` | Credentials and scanner thresholds |
| `test_telegram.py` | Manual Telegram connectivity check |
| `.github/workflows/` | Scheduled/manual GitHub Actions jobs |

## Current Limitations And Checks Before Replacing Components

- There is no persistent lifecycle state, position tracking, cooldown, defense/exit logic, broker order placement, or backtest. Live momentum behavior has an offline regression suite.
- The batch original-VCP breakout comparison uses the latest candle's high as part of its pivot, then requires that candle's close to exceed that pivot by 0.50%. For ordinary OHLC data this route cannot pass. The optimized prior-base breakout path is separate. Live VCP breakouts use a current tick against pivots from completed history, so they do not have that same same-candle constraint.
- Live alerts are marked sent before Telegram delivery; the unchanged Telegram transport logs delivery failures but does not retry them for that symbol during the same process.
- Candidate and alert formatting says `/7` even though only six candidate components are counted.
- `MAX_UNIVERSE=1500` is defined but unused, and scheduled batch execution does not pass a symbol limit.
- The daily workflow requires a CSV artifact, but the batch scanner creates no CSV when there are no output or failure rows.
- External data formats and availability are assumed by the code. Universe loading, instrument master retrieval, benchmark lookup, and history downloads can fail when those services, schemas, credentials, or network access change.
- Live monitoring stops its evaluation loop at 15:20 IST. It has WebSocket auto-reconnect enabled, but does not persist alerts or scanner state after process exit.

Use these behaviors as the baseline when changing or replacing a module: the batch and live paths share thresholds and some analysis, but have separate orchestration, timing, signal state, and alert formatting.
