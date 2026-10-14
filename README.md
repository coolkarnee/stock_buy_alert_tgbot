# Stock Pattern Lab

An NSE equity screening and alerting tool. It has an unchanged daily historical VCP scan and an intraday WebSocket momentum BUY monitor using Upstox prices and Yahoo Finance market capitalization.

This repository is an alert-only scanner. It does not place orders or manage positions. The swing-performance report is a retrospective calculation, not an order-management or execution system.

## What Runs Today

| Entry point | Behavior |
| --- | --- |
| `main.py` | Downloads the current universe and about two years of daily history, evaluates each stock, prints a scan summary, sends Telegram alerts for BUY results, and writes `scan_results.csv` if there are results or per-symbol failures to report. |
| `live_monitor.py` | Builds historical reference state, sends a first market-open summary for six indices followed by a TradingView India market-breadth snapshot, connects to the Upstox V3 full-market WebSocket, evaluates live quotes each second, sends a final market-close summary, and sends at most one Momentum BUY and one Episodic Pivot BUY alert per symbol per day within that process. |
| `performance.py` | Reads the live alert CSV, fetches the day's close and post-alert intraday high, sends a Telegram summary, writes `performance.csv`, and saves a concise conclusion for the next market-open summary. |
| `swing_performance.py` | Recomputes the persistent swing-performance sheet for every live alert until the first daily SL/EMA break. |
| `test_live_engine.py` | Offline automated tests for momentum rules, IPO exemptions, ORB candles, feed parsing, and mocked Telegram dispatch. |
| `test_telegram.py` | Sends a fixed Telegram connectivity test message. This is a manual smoke test, not an automated test suite. |

There are no command-line options. The batch function accepts an optional `limit` when called from Python, but its normal script invocation scans the full loaded universe. The configured `MAX_UNIVERSE` value is currently not applied.

## When Will a Telegram BUY Alert Fire? — Share-Market Guide

This section explains the alert as a market signal, without requiring knowledge of the implementation.

### Momentum BUY

A Momentum BUY alert means the stock is showing a broad combination of strength rather than only a short-lived price spike. From a share-market perspective, the alert indicates that:

- The stock is trading strongly compared with its recent trend.
- Buyers are participating through meaningful trading activity or a strong price move.
- The current rise has moved beyond an important recent price range, suggesting a possible breakout.
- The stock is still reasonably close to its recent high instead of being deeply weak.
- The stock has enough liquidity for the alert to be practically observable and tradable.
- The stock's price movement is large enough to be considered meaningful, while the alert still checks broader price context.

In simple terms, this alert is intended for a stock that is already demonstrating sustained market strength and is attempting to continue that move. It is not a prediction that the stock must rise, and it is not a recommendation to buy without checking risk, position size, news, valuation, and the overall market.

### Episodic Pivot BUY

An Episodic Pivot BUY alert means the stock has opened with a sharp positive gap compared with the previous trading day's close and is continuing to hold above an important trend level. From a share-market perspective, this can indicate that new information, a result, an announcement, or a sudden change in investor demand is attracting buyers.

The alert is looking for a stock where:

- The opening gap is substantial rather than ordinary day-to-day movement.
- Buyers are holding the stock above its developing trend level after the gap.
- The stock is not trading below the minimum practical price level used for this watchlist.
- The stock has sufficient daily price movement and liquidity to support meaningful participation.

In simple terms, this alert highlights a possible change in investor sentiment. Gap-up stocks can also be volatile: they may continue higher, move sideways, or quickly give back the gap. The alert should therefore be treated as a prompt for further research and risk planning, not as confirmation of a guaranteed return.

### When to expect the message

The Telegram message can arrive only during the live market-monitoring session after the live workflow has been started and after the market has opened. A stock must meet the complete set of requirements for its alert category at the same time. If the stock loses the required strength before that happens, the message will not be sent.

At most one Momentum BUY and one Episodic Pivot BUY message is sent for the same stock during one monitoring run. No order is placed automatically. Telegram delivery requires the configured Telegram credentials; otherwise the message is available in the workflow log.

## Setup

Use Python 3.12 to match every GitHub Actions workflow. Python 3.9+ is required by the code itself (`zoneinfo` and `ThreadPoolExecutor.cancel_futures`), but Python 3.12 is the tested/automation runtime. Install dependencies in a virtual environment:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

On Windows PowerShell, use:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Set `UPSTOX_ACCESS_TOKEN` for Upstox historical and streaming data. Set both `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` to deliver Telegram messages. `TELEGRAM_CHAT_ID` may contain comma-separated chat IDs. If either Telegram setting is absent, alerts are printed to standard output instead of sent.

Run one of the scripts from the repository root:

```powershell
python main.py
python live_monitor.py
python performance.py
python -m unittest test_live_engine -v
python test_telegram.py
```

`performance.py` expects a `live_alerts.csv` produced by the live monitor. The Telegram test script does not need the Upstox token, though the workflow supplies it. The repository does not include a checked-in `.env` loader; configure environment variables in the shell, IDE, or workflow secrets.

For a local first run, execute the unittest suite before contacting external APIs. The batch scanner, live monitor, performance evaluator, and Telegram smoke test all require network access; the batch and live paths also require a valid Upstox token.

## Data And Universe

The universe is rebuilt each run. `scanner/universe.py` collects the union of constituents from Nifty 500, Nifty MidSmallcap 400, Nifty Microcap 250, and Nifty Smallcap 250 CSVs, plus NSE IPOs with a listing date on or after 2026-01-01. Symbols are uppercased and de-duplicated. `scanner/upstox.py` downloads the Upstox instrument master and retains matching `NSE_EQ` instruments of type `EQ` with normal security type.

Universe sources are external services, not bundled files. Individual source failures are printed as warnings; loading can continue if another source returns symbols. The run stops if no symbols are collected. Instruments not found in Upstox's instrument master are omitted.

Both scanners fetch approximately two years of daily candles for each eligible stock. Batch scanning uses Nifty 500 as its benchmark and six worker threads, and requires 260 daily bars. Live state preparation uses `UPSTOX_MAX_WORKERS` (default eight), removes candles dated today, and requires at least one completed daily bar. Live market context uses Nifty Smallcap 250 and falls back to Nifty 500 if the Smallcap benchmark lookup or history is unavailable. Failed or empty history requests are skipped.

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
| Turnover | Price multiplied by volume at least ₹20 crore |

The normal batch breakout compares the latest close with the previous session's high or close, whichever is higher, plus 0.50%. If the previous candle is dark red—its close is more than 4% below its open—the previous close is ignored and the breakout reference becomes only the previous high, still with the 0.50% buffer:

```text
dark red: latest close >= previous high × 1.005
otherwise: latest close >= max(previous high, previous close) × 1.005
```

## Live Momentum BUY Rules

Live selection does not use VCP or the batch candidate score. Every mandatory condition below must align:

| Condition | Live rule |
| --- | --- |
| Trend | Price above the developing EMA21, except through listing day 21 |
| Volume confirmation | At least one of RVOL > 1.30x, pocket pivot, first-30-minute surge, price up ≥4% from open, or daily volume >9M |
| Momentum | Price above yesterday's close |
| Breakout | Price above the previous-session reference with a 0.50% buffer |
| Swing strength | Any qualifying period in the table below; IPOs skip this condition |
| Near high | Within 20% of the 52-week high, or within 15% for IPOs |
| Volatility | ATR14 >3.9%, except IPOs |
| Price | Strictly above ₹20 |
| Liquidity | Turnover strictly above ₹20 Cr |
| Opening range | Break at least one completed 3m, 5m, 15m, 30m, or 60m opening range |
| SL risk | Previous-day-low distance is >0% and ≤6% of entry price |

### Multi-period swing strength

The old 30% rise-from-52-week-low requirement has been removed. Current price is compared with the closing price from each standard trading-session period. **At least one** move must be strictly greater than its threshold:

| Period | Reference close | Required move |
| --- | ---: | ---: |
| 1 week | 5 sessions ago | >10% |
| 1 month | 21 sessions ago | >19% |
| 3 months | 63 sessions ago | >29% |
| 6 months | 126 sessions ago | >49% |
| 1 year | 252 sessions ago | >99% |
| 18 months | 378 sessions ago | >149% |
| 24 months | 504 sessions ago | >199% |

Only the existing two-year history request is used. A period qualifies only when its reference close is available; no three-year download is performed. IPOs skip this entire condition.

### Previous-day-low risk and position size

Momentum BUY alerts are sent only when the previous-day-low stop is no more than 6% below entry:

```text
SL distance % = ((Buy Price − Previous Day Low) / Buy Price) × 100
Position Size % = min(25%, 1% / SL distance)
```

Examples: a 5% SL distance gives a 20% maximum position; a 4% distance reaches the 25% cap. The Telegram alert shows the previous-day-low SL and the calculated maximum position size. This filter does not apply to EP alerts.

The breakout reference is the lower of yesterday's high and close, with the 0.50% buffer. If yesterday's candle closed more than 4% below its open, only yesterday's high is used. `OPENING_RANGE_BREAKOUT_MODE="close"` requires a completed candle close above the opening-range high; `"spot"` triggers on the live price crossing it.

### Episodic Pivot BUY

An independent EP alert requires all five conditions below:

- The day's open is strictly more than 5% above yesterday's close.
- Current price is above the developing EMA21.
- Price is strictly above ₹20.
- ATR is above 3.9%, except for IPOs.
- Turnover/liquidity is above ₹20 Cr.

EP alerts do not require Momentum BUY volume confirmation, momentum, breakout, rally-strength, near-high, or opening-range conditions.

Stocks with fewer than 252 completed daily candles are treated as IPOs: rally and ATR checks auto-pass, and proximity must be within 15% of the listing high. This uses available history as a listing-age proxy; incomplete provider history may therefore misclassify a stock. A first-day listing without a previous daily candle cannot be evaluated against yesterday's close.

EMA10/EMA21 extension is shown in alerts but is not a blocking filter by default. Set `LIVE_BLOCK_EXTENDED=True` to require at most 10% above EMA10 and 20% above EMA21. Prior three-session average volume below the preceding 20-session average is informational only and never blocks a BUY. The exact NIFTY 500 market context is informational and never blocks alerts. The market summary uses exact Upstox records only; unavailable index values are shown as `NA`, with a fetched count when fewer than six are available.

RVOL projects cumulative volume to a 375-minute session using elapsed session time and the preceding 20-session average. Turnover uses actual cumulative volume, not projected volume. Live EMA and ATR incorporate the developing daily candle, so they may change before the close.

`scanner/live_data.py` contains a Yahoo Finance INR market-cap helper for future use, but the current live monitor does not invoke it, so there is no market-cap lookup, retry, or market-cap rejection in the active signal path. Missing/incomplete opening candles cannot establish an ORB; these requests are retried at most once per minute per stock until all five ranges are available. The first eligible alert can be delayed by provider latency, queues, or rate limits. Starting after the open still reconstructs the full opening ranges from candles, rather than approximating them from observed ticks.

Run the offline regression suite with `python -m unittest test_live_engine -v`. It does not contact Telegram or Upstox. Real-time delivery additionally requires valid Upstox and Telegram credentials and a running monitor each trading day.

## Outputs And Alerts

The batch scan writes `scan_results.csv` in the current working directory when the combined results are non-empty. Successful analyses include the signal flags, score, VCP fields, measurements, failed BUY filters, and data status. Stocks whose history download or analysis failed are included with a failure status and error text. If there are no successful rows and no recorded failures, no CSV is written.

The batch output is sorted with BUYs and candidates first, followed by VCP quality and score. The console prints scan health, counts, and up to 30 candidates. Only BUY results are sent as stock alerts; candidate-only results are not.

The live monitor sends Momentum BUY and Episodic Pivot BUY messages, at most once per category and symbol per trading day within that process. Restart the monitor each trading day; it exits at 15:20 IST. There is no database or file-backed state to suppress repeats across separate runs on the same day.

Telegram delivery uses the Telegram Bot API `sendMessage` endpoint. A non-success response or request exception is printed and processing continues. Missing Telegram credentials cause the message to be printed locally.

Stock Telegram alerts include an `Open TradingView Chart` inline button. The button uses the cross-platform HTTPS URL `https://www.tradingview.com/chart/?symbol=NSE%3ASYMBOL`, which opens the chart in the TradingView app when the device handles the link there, or in the browser otherwise. The exchange-qualified symbol avoids ambiguity for NSE stocks. The same link is used by live Momentum/EP alerts and batch BUY alerts.

## Swing Performance

The live monitor records each Momentum BUY and Episodic Pivot BUY with ticker, buy price, previous-day-low SL, entry market context, alert type, date, and time in `live_alerts.csv`. Momentum BUY alerts are sent only when the previous-day-low distance is positive and no more than 6% below the buy price. Their maximum portfolio allocation for 1% portfolio risk is `min(25%, 1% / SL distance)`. The position-size filter does not apply to EP alerts. The `swing_performance.py` job merges the current day's alerts with the committed history in `artifacts/swing_performance.csv` and recalculates every row.

The report stores the market context at entry and uses the previous trading day's low as the initial SL. In a Bear/Choppy market, a +10% move books 30%; in a Bull market, a +15% move books 20%. The remaining quantity trails at the higher of breakeven and the 10 EMA on daily closing basis. A 10 EMA close break sells 50% of the remaining quantity; a 20 EMA close break or a post-trigger breakeven close break exits the remainder. `Latest Close` is the latest daily close on the `Performance As Of Date`; all SL checks are closing-basis checks. `Performance %` is computed from entry to latest close, with weighted realized and remaining-position return after partial exits. Status values are `Trade open`, `Partial exit`, and `Full exit`. EMA and SL checks use daily bars, so this is a daily-bar retrospective report rather than tick-level execution accounting.

The latest CSV remains committed under `artifacts/swing_performance.csv` as the persistent calculation source. Each run also creates a formatted `artifacts/swing_performance.xlsx` workbook with an overall dashboard, ISO-week sections, merged centered week titles, weekly numbering, repeated headers/descriptions, status colors, and performance formatting. The visible workbook hides the internal Alert ID and shows a simple number restarting within each week; the CSV retains Alert ID for persistence and event tracking. When a Partial exit or Full exit is newly recorded with today's action date, the swing workflow sends a Telegram exit alert. The existing Telegram sender splits long messages automatically. The workbook is uploaded as the `swing-performance-latest` GitHub Actions artifact and sent to Telegram as a document. The swing workflow runs automatically after `NSE Live Alert Performance` completes successfully and can also be started manually.

For a shareable web-format overview, open [`docs/stock-pattern-lab.html`](./docs/stock-pattern-lab.html) in a browser or publish it with any static web host.

## Configuration

Environment variables read in `config/settings.py`:

| Variable | Required for | Default |
| --- | --- | --- |
| `UPSTOX_ACCESS_TOKEN` | Fetching Upstox data and connecting the live feed | Empty; Upstox requests fail without it |
| `TELEGRAM_BOT_TOKEN` | Sending Telegram alerts | Empty; alerts print locally if not configured |
| `TELEGRAM_CHAT_ID` | Selecting Telegram recipient(s) | Empty; alerts print locally if not configured |

`UPSTOX_MAX_WORKERS` is a Python constant used by live historical-data preparation (default eight), not an environment variable. Batch concurrency is fixed at six in `main.py`.

The screening thresholds and VCP parameters are Python constants in `config/settings.py`, not environment variables. They include `MIN_PRICE=20`, `MIN_TURNOVER_CR=20`, `MAX_FROM_52W_HIGH=20`, `MIN_ATR_PCT=3.9`, `MIN_RVOL=1.30`, `BREAKOUT_BUFFER_PCT=0.50`, `DARK_RED_CANDLE_DROP_PCT=4.0`, and `CANDIDATE_SCORE_REQUIRED=4`.

## Automation

There are six GitHub Actions workflows. The live alert, live performance, and swing performance workflows are chained from manual execution; the remaining workflows are manual `workflow_dispatch` jobs:

| Workflow | Schedule | Action |
| --- | --- | --- |
| `.github/workflows/daily-scan.yml` | Manual only | Runs `main.py`, then uploads `scan_results.csv` for seven days. Artifact upload fails if the file was not created. The comment mentioning a daily scan is stale; there is no `schedule` trigger in this file. |
| `.github/workflows/live-alerts.yml` | Manual only | Runs `live_monitor.py`. The process waits until 09:15 IST, monitors until 15:20 IST, and uploads `live_alerts.csv` even when the job fails. A concurrency group cancels an older run on the same branch. |
| `.github/workflows/live-performance.yml` | After successful live alerts and manual | Downloads the exact triggering `live-alerts.yml` artifact, runs `performance.py`, and uploads the daily report plus alert input for 30 days. |
| `.github/workflows/swing-performance.yml` | After successful live performance and manual | Downloads the alert input, recalculates all persistent swing rows until exit or report date, commits `artifacts/swing_performance.csv`, uploads the formatted Excel workbook for 90 days, and sends it to Telegram. |
| `.github/workflows/test-ipo-universe.yml` | Manual only | Checks out the hard-coded `Nilesh_branch_to_add_ipos` branch, installs dependencies, and calls the Upstox IPO API to print listed IPO symbols since 2026-01-01. It is not a main-branch test until its checkout ref is changed. |
| `.github/workflows/test-telegram.yml` | Manual only | Runs the fixed Telegram connectivity message. |

The daily scan and live monitor are separate jobs; the batch run does not feed state to the live process. The live performance and swing performance jobs consume workflow artifacts, while the committed swing CSV provides persistence between runs.

### Main-branch checklist

Before enabling this reference branch in the main branch:

1. Add repository or organization Actions secrets named `UPSTOX_ACCESS_TOKEN`, `TELEGRAM_BOT_TOKEN`, and `TELEGRAM_CHAT_ID`. The chat ID may be comma-separated for multiple recipients.
2. Ensure Actions are enabled and the repository permits artifact read/write. `live-performance.yml` already declares `contents: read` and `actions: read` and uses the built-in `GITHUB_TOKEN` through `GH_TOKEN`.
3. Change `ref: stock_buy_alert_tgbot` in `test-ipo-universe.yml` to the intended main/default branch, or remove the explicit checkout ref so the workflow tests the selected branch. Otherwise that workflow can fail before running project code.
4. Keep the workflow's Python 3.12 and install from the checked-in `requirements.txt`.
5. Treat the manually started live job as a long-running external-data job: Upstox access, Yahoo Finance access, Nifty/NSE source access, and Telegram access must be available from GitHub-hosted runners. The live job waits until 09:15 IST after it is started.
6. Run `test_live_engine.py` manually after merging. It is the only automated Python test file currently wired to project behavior; the Telegram and IPO workflows are integration/smoke checks.

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
| `swing_performance.py` | Persistent alert performance calculation until exit |
| `artifacts/` | Versioned latest swing-performance CSV |
| `docs/stock-pattern-lab.html` | Standalone shareable web-format overview |
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
