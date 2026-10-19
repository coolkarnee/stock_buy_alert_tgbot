# Stock Pattern Lab

An NSE equity screening and alerting tool. It has an unchanged daily historical VCP scan and an intraday WebSocket momentum BUY monitor using Upstox prices and Upstox company-profile sector metadata.

This repository is an alert-only scanner. It does not place orders or manage positions. The swing-performance report is a retrospective calculation, not an order-management or execution system.

## What Runs Today

| Entry point | Behavior |
| --- | --- |
| `main.py` | Downloads the current universe and about two years of daily history, evaluates each stock, prints a scan summary, sends Telegram alerts for BUY results, and writes `scan_results.csv` if there are results or per-symbol failures to report. |
| `live_monitor.py` | Builds historical reference state, connects to the Upstox V3 full-market WebSocket at 09:00 IST for pre-open EP gap candidates, confirms them with regular-session quotes, evaluates Momentum BUY and EP-radar opening-range alerts, sends the delayed market/breadth summaries, and sends a final market-close summary. |
| `performance.py` | Reads the live alert CSV, fetches the day's close and post-alert intraday high, sends a Telegram summary followed by an sector alert-count breakdown, writes `performance.csv`, and saves a concise conclusion for the next market-open summary. |
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

If `py` is not recognized (launcher not on PATH), use the full interpreter path instead, e.g. `& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m venv .venv`. If winget's `msstore` source fails with a certificate error, install with `winget install --id Python.Python.3.12 --source winget --scope user`. The repo has no `.gitignore`; hide local files per clone (see the quick reference below).

Set `UPSTOX_ACCESS_TOKEN` for Upstox historical and streaming data. Set both `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` to deliver Telegram messages. `TELEGRAM_CHAT_ID` may contain comma-separated chat IDs. If either Telegram setting is absent, alerts are printed to standard output instead of sent.

### Local quick reference (Windows)

Run from the repo root. Uses the venv interpreter directly, so activation is not required.

| Task | Command |
| --- | --- |
| Create venv (one-time) | `& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m venv .venv` |
| Install / update deps | `.\.venv\Scripts\python.exe -m pip install -r requirements.txt` |
| Run offline tests (no network) | `.\.venv\Scripts\python.exe -m unittest test_live_engine test_sector test_alerts test_ep_radar test_signal_history test_rate_limits -v` |
| Preview breadth alerts (live TradingView, prints text; no Telegram without tokens) | `.\.venv\Scripts\python.exe -c "from datetime import date; from scanner.breadth import send_breadth_alerts; send_breadth_alerts(date.today())"` |
| Check Upstox index symbols (needs token) | `$env:UPSTOX_ACCESS_TOKEN="..."; .\.venv\Scripts\python.exe test_upstox_indices.py` |
| Telegram connectivity (needs bot token + chat ID) | `.\.venv\Scripts\python.exe test_telegram.py` |
| Clean generated files | `Get-ChildItem -Recurse -Directory -Filter __pycache__ \| Where-Object FullName -notlike "*\.venv\*" \| Remove-Item -Recurse -Force; Remove-Item live_alerts.csv -ErrorAction SilentlyContinue` |

Notes:
- If emoji printing fails in the Windows console (`UnicodeEncodeError`), set `$env:PYTHONIOENCODING="utf-8"` first; GitHub runners are unaffected.
- The breadth preview writes today's row to `artifacts/market_breadth.csv`; discard it with `git restore artifacts/market_breadth.csv` (or delete it if untracked) unless you want it committed.
- Generated local outputs: `.venv/`, `__pycache__/`, `live_alerts.csv`. Hide them from git locally (one-time, never committed): `Add-Content .git\info\exclude "`n.venv/`n__pycache__/`nlive_alerts.csv"`.

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

Both scanners fetch approximately two years of daily candles for each eligible stock. Batch scanning uses Nifty 500 as its benchmark and six worker threads, and requires 260 daily bars. Live state preparation uses `UPSTOX_MAX_WORKERS` (default eight), removes candles dated today, and requires at least one completed daily bar. BUY-alert context uses the exact NIFTY 500 index and the latest available stocks-above-200-MA breadth. Failed or empty history requests are skipped; unavailable context inputs are shown as NA.

Upstox REST requests share `scanner/upstox.py`'s thread-safe limiter across daily history, intraday candles, quote snapshots, company profiles and IPO pagination, including every retry. Safety budgets are `UPSTOX_REQUESTS_PER_SECOND=8`, `UPSTOX_REQUESTS_PER_MINUTE=480` and `UPSTOX_REQUESTS_PER_30_MINUTES=1950`, below the stated broker ceilings of 10/s, 500/min and 2,000/30min. The limiter spaces calls against the tightest budget: **one request approximately every 0.923 seconds at the defaults** (about 65/min sustained), not 8/s or 480/min sustained. Raising the longer-window budgets reduces safety headroom; 8/s alone does not protect the 30-minute quota. Invalid budgets above those ceilings fail explicitly. Most requests use a 15-second timeout and one transient-error retry; IPO pages retain two retries and a 20-second timeout. HTTP 429/selected 5xx responses set a shared cooldown, honoring numeric or HTTP-date `Retry-After` without the previous five-second cap. Exhausted requests still surface errors.

This is a **per-process** REST budget, not an account-wide quota coordinator. Do not overlap batch, live, performance, swing or other applications using the same Upstox account/token; independent processes/runners can exceed the combined broker quota. The live WebSocket streams prices without one REST request per tick; its SDK connection/reconnect and subscription limits are separate. Public Nifty CSVs and the Upstox instrument-master asset download are not calls to `api.upstox.com` and remain outside this limiter.

Budget compliance trades throughput for safety: 900 one-request-per-stock history loads take about 13.8 minutes at minimum, before other calls/network delays. Full-universe startup can therefore delay opening alerts; start sufficiently early or arrange preloading before the session. Roughly 65 REST calls/minute sustained are available across candle refreshes, profiles and other requests. A large EP radar cannot refresh every candidate each minute simultaneously: candle requests queue and close confirmations can be reconstructed later, while spot alerts use the live price available when evaluated. Rate limiting does not guarantee timely provider data or Telegram delivery.

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
| Volatility | ATR-14 at least 3.49% of price |
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
| Volatility | ATR14 >3.49%, except IPOs |
| Price | Strictly above ₹20 |
| Liquidity | Turnover strictly above ₹20 Cr |
| Opening range | Break at least one completed 3m, 5m, 15m, 30m, or 60m opening range |
| SL risk | Previous-day-low distance is >0% and ≤8% of entry price |

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

### Live alert format

Alerts show the data window behind ATR and turnover, so short-history stocks (IPOs) are easy to judge:

- `ATR Nd`: N = completed sessions used (max 13) + today's developing range. The value is still `(sum of TRs) / 14`, so N < 14 means fewer real ranges and an understated ATR.
- `Turnover Nd avg`: N = sessions in the turnover average (`turnover_period_days`, max 50, includes today).
- `Strength:` (Momentum BUY only) lists every swing-strength period that passed, with move and threshold (`swing_moves_matched` from `evaluate()`); IPOs show `IPO exempt`.
- `Perf:` (EP only, informational; EP does not require swing strength) lists the move for every period with an available reference close (1W, 1M, 3M, 6M, 1Y, 18M, 24M); `✓` marks a move above its swing-strength threshold. With fewer than 5 completed sessions it shows `Perf: NA (N sessions)`.
- `Momentum Stock` (Momentum BUY and EP, after the sector line, informational; does not change BUY/EP rules): shown when price is above both live EMA10 and EMA21 **and** both EMAs are rising on completed bars (yesterday's EMA10 > the day before's, same for EMA21). Uses `ema10_prev`/`ema21_prev` from `build_state()`; needs at least 2 completed sessions. Exposed as `momentum_stock` in `evaluate()`.

```text
MOMENTUM BUY - ELECON
Sector: Specialty Industrial Machinery
Momentum Stock
Price Rs 110.00 | Prev Day Low SL Rs 104.50
Max Position 20.00%
Gap +0.00% | Move +10.00% from open | +10.00% from low
ATR 14d 29.35% | Turnover 50d avg Rs 25.4 Cr
52W HI -0.00% | 52W LO +57.14%
Strength: 1W +22.2% (>10%), 1M +25.0% (>19%), 3M +37.5% (>29%)
ORB close: 3m, 5m | RVOL 1.55x | Pocket Pivot
Market Context: Bull Market
NIFTY 500: Uptrending (above EMA10 and EMA20; both rising)
Stocks above 200 MA: 62.5% (Broader Bull; 37.5% below 200 MA) | as of 2026-10-19
```

```text
EPISODIC PIVOT BUY - ELECON
Sector: Specialty Industrial Machinery
Price Rs 110.00 | Gap +6.00%
Prev Day Low SL Rs 104.50
ATR 14d 29.35% | Turnover 50d avg Rs 25.4 Cr
Perf: 1W +22.2% ✓ | 1M +5.8% | 3M -8.3% | 6M +15.8% | 1Y +120.0% ✓ | 18M +83.3%
Market Context: Bear Market
NIFTY 500: Downtrending (below EMA10 and EMA20; both falling)
Stocks above 200 MA: 42.0% (Broader Bear; 58.0% below 200 MA) | as of 2026-10-19
```

```text
EPISODIC PIVOT BUY - KANOHAR
Sector: NA
Price Rs 110.00 | Gap +6.00%
Prev Day Low SL Rs 104.50
ATR 4d 7.27% | Turnover 4d avg Rs 29.5 Cr
Perf: NA (3 sessions)
Market Context: NA
NIFTY 500: NA
Stocks above 200 MA: NA
```

### Previous-day-low risk and position size

Momentum BUY alerts are sent only when the previous-day-low stop is no more than 8% below entry:

```text
SL distance % = ((Buy Price − Previous Day Low) / Buy Price) × 100
Position Size % = min(25%, 1% / SL distance)
```

Examples: a 5% SL distance gives a 20% maximum position; a 4% distance reaches the 25% cap. The Telegram alert shows the previous-day-low SL and the calculated maximum position size. This filter does not apply to EP alerts.

The breakout reference is the lower of yesterday's high and close, with the 0.50% buffer. If yesterday's candle closed more than 4% below its open, only yesterday's high is used. `OPENING_RANGE_BREAKOUT_MODE="close"` requires a completed candle close above the opening-range high; `"spot"` triggers on the live price crossing it.

### Episodic Pivot BUY

An independent EP alert requires all five conditions below:

- The day's actual open is strictly more than `EP_MIN_GAP_PCT` above yesterday's close (default **4.5%**, configured in `config/settings.py`).
- Current price is above the developing EMA21.
- Price is strictly above ₹20.
- ATR is strictly above `EP_MIN_ATR_PCT` (default **3.0%**), except for IPOs.
- Turnover/liquidity is above ₹20 Cr.

EP alerts do not require Momentum BUY volume confirmation, momentum, breakout, rally-strength, near-high, or opening-range conditions.

EP BUY and Momentum BUY remain independent: an EP alert never suppresses a qualifying Momentum alert, and Momentum does not require an earlier EP alert. Each is sent at most once per symbol per day within the process.

The monitor connects at 09:00 IST and records Upstox's pre-open indicative equilibrium price (IEP). An IEP more than `EP_MIN_GAP_PCT` above the previous close nominates a candidate; it is indicative, not a traded price or an alert. After 09:15, a fresh regular-session quote must confirm the actual opening gap, developing EMA21, price floor, EP ATR threshold, and turnover before EP BUY is sent. Stocks without a usable pre-open IEP can still be found by the completed one-minute candle refresh after the open. Provider, REST queue, or Telegram delays can postpone delivery.

The EP ATR threshold is separate from Momentum BUY's `MIN_ATR_PCT`: `EP_MIN_ATR_PCT=3.0`. IPOs retain the existing EP ATR exemption. EP does not require Momentum volume confirmation, swing strength, near-high, ORB, or stop-distance conditions.

Yesterday's close and indicator baselines still exclude today's unfinished daily candle to avoid double-counting it. Today's candle is included separately through the intraday EP data.

### EP gap-radar opening-range BUY

This is a separate, lighter scan, not a replacement for EP BUY or Momentum BUY:

- **Radar membership:** the actual opening minute's open is strictly more than `EP_MIN_GAP_PCT` (default 4.5%) above the previous completed session's close. A full EP BUY is **not** required. The opening gap defines membership even if the stock fails EMA, ATR, liquidity, price-floor, or Momentum checks.
- **Ranges:** first **3, 5, 15, 30, and 60 minutes** from market open. All one-minute price candles within a range must be available and valid before its high is trusted.
- **ORB confirmation:** uses the same `OPENING_RANGE_BREAKOUT_MODE` in `config/settings.py` as Momentum. `"close"` (default) requires a completed candle close strictly above the opening-range high; `"spot"` requires the current live price strictly above that high once the range has formed. This is the existing close/spot choice, not a new yes/no setting. Touching or equalling the high does not qualify.
- **Minimal quality filters:** qualifying price must be above the developing EMA21, strictly above ₹20, and pass the same turnover/liquidity threshold (>₹20 Cr). Close mode uses the confirming candle's close and cumulative volume through that candle; spot mode uses live price and cumulative volume (completed-candle volume is the baseline if the tick omits it). It still does not require Momentum volume surge, breakout-reference, near-high, strength, ATR, or SL-distance checks.
- **Frequency:** once per stock per range per day within the process, up to five radar messages. Normal EP and Momentum messages remain independently eligible. The monitor continues its shared, at-most-once-per-minute candle refresh for radar stocks after an EP or Momentum alert until all five radar categories have alerted or monitoring ends.

Close mode retains the existing completed one-minute confirmation: for the 3-minute range (09:15–09:17), the earliest confirming candle completes at 09:19; for the 60-minute range it completes at 10:16. Spot mode can qualify after the respective range finishes (09:18 and 10:15), without waiting for a breakout candle to close. These follow range/candle durations, not an exact-time trigger; data/API/Telegram latency may delay delivery. A later monitor start reconstructs opening ranges; close mode can reconstruct qualifying candle breakouts, while spot mode checks the current quote, not historic wicks.

```text
EP RADAR ORB BUY - EXAMPLE | 3m
Sector: Refineries
Breakout Close Rs 110.00 | Opening Gap +4.60%
Opening 3m High Rs 105.00
ORB confirmation: close
Prev Day Low SL Rs 102.00
```

The log types are `EP_ORB_3M`, `EP_ORB_5M`, `EP_ORB_15M`, `EP_ORB_30M`, and `EP_ORB_60M`. Alert price is the first qualifying completed candle's close in close mode or the first qualifying observed live price in spot mode; log time is when the monitor emits the alert. Messages show `ORB confirmation: close` with `Breakout Close`, or `ORB confirmation: spot` with `Spot Price` and quote time. Delayed/reconstructed alerts are not a claim that an order was executable at an earlier price.

The 30-minute range covers 09:15 through 09:44 IST. Spot mode can qualify once that range completes at 09:45; close mode can first qualify when the following one-minute candle completes at 09:46, subject to data availability and the unchanged quality filters.

The copy-paste Telegram trader rules sheet is saved in [docs/telegram-trader-rules.txt](docs/telegram-trader-rules.txt).

EP-radar regressions in `test_ep_radar.py` cover both configured modes, strict crossings, completed ranges, live-price dispatch without a breakout close, quality filters, per-range duplicate suppression, and unchanged first-entry performance selection.

Stocks with fewer than 252 completed daily candles are treated as IPOs: rally and ATR checks auto-pass, and proximity must be within 15% of the listing high. This uses available history as a listing-age proxy; incomplete provider history may therefore misclassify a stock. A first-day listing without a previous daily candle cannot be evaluated against yesterday's close.

EMA10/EMA21 extension is shown in alerts but is not a blocking filter by default. Set `LIVE_BLOCK_EXTENDED=True` to require at most 10% above EMA10 and 20% above EMA21. Prior three-session average volume below the preceding 20-session average is informational only and never blocks a BUY. The combined market context is informational and never blocks alerts. For live BUY alerts, NIFTY 500 is Uptrending only when above both live EMA10 and EMA20 with both rising, Downtrending only when below both with both falling, and Chopping otherwise. TradingView's stocks-above-200-day-MA percentage is Broader Bull above 50% and Broader Bear at or below 50%; <=20% adds Oversold and >=80% adds Overbought. Matching bullish/bearish readings yield Bull/Bear Market, disagreement yields Mixed Market, and unavailable readings show NA with the other available value retained.

Market-summary indexes are matched in the Upstox instrument master on either `trading_symbol` or `name` (normalized to uppercase alphanumerics), because some indexes differ between the two fields; for example Nifty 50 has `trading_symbol` `NIFTY` and `name` `Nifty 50`.

RVOL projects cumulative volume to a 375-minute session using elapsed session time and the preceding 20-session average. Turnover uses actual cumulative volume, not projected volume. Live EMA and ATR incorporate the developing daily candle, so they may change before the close.

There is no market-cap lookup or market-cap rejection in the active signal path. The unused external market-cap helper has been removed. Missing/incomplete opening candles cannot establish an ORB; these requests are retried at most once per minute per stock until all five ranges are available. The first eligible alert can be delayed by provider latency, queues, or rate limits. Starting after the open still reconstructs the full opening ranges from candles, rather than approximating them from observed ticks.

Run the offline regression suite with `python -m unittest test_live_engine -v`. It does not contact Telegram or Upstox. Fixtures cover close-mode ORB, 50-day turnover > 20 Cr, RVOL from `rvol_prev19_volume`, swing-move strength, and the configurable EP gap and completed-opening-candle gate. The EP monitor regression covers a pre-open start, incomplete WebSocket OHLC/volume, and once-per-day delivery from completed candle data. The Momentum monitor test writes `live_alerts.csv` in the working directory (hidden via `.git/info/exclude`; see the quick reference); the EP monitor test uses a temporary directory. Real-time delivery additionally requires valid Upstox and Telegram credentials and a running monitor each trading day.

To diagnose Upstox index symbols and EMA history, run `UPSTOX_ACCESS_TOKEN=... python test_upstox_indices.py`. It lists every available `NSE_INDEX` instrument, checks the six market-summary indexes, and prints EMA10/EMA21 values. Add `--all-indexes` to calculate EMAs for every available Upstox NSE index.

## Outputs And Alerts

The batch scan writes `scan_results.csv` in the current working directory when the combined results are non-empty. Successful analyses include the signal flags, score, VCP fields, measurements, failed BUY filters, and data status. Stocks whose history download or analysis failed are included with a failure status and error text. If there are no successful rows and no recorded failures, no CSV is written.

The batch output is sorted with BUYs and candidates first, followed by VCP quality and score. The console prints scan health, counts, and up to 30 candidates. Only BUY results are sent as stock alerts; candidate-only results are not.

The live monitor sends Momentum BUY and Episodic Pivot BUY messages at most once per category and symbol per trading day within that process, plus up to five independent EP gap-radar ORB messages per symbol. Restart the monitor each trading day; it exits at 15:20 IST. There is no database or file-backed state to suppress repeats across separate runs on the same day.

Telegram delivery uses the Telegram Bot API `sendMessage` endpoint. A non-success response or request exception is printed and processing continues. Missing Telegram credentials cause the message to be printed locally.

Stock Telegram alerts include an `Open TradingView Chart` inline button. The button uses the cross-platform HTTPS URL `https://www.tradingview.com/chart/?symbol=NSE%3ASYMBOL`, which opens the chart in the TradingView app when the device handles the link there, or in the browser otherwise. The exchange-qualified symbol avoids ambiguity for NSE stocks. The same link is used by live Momentum/EP alerts and batch BUY alerts. Stock alerts are text-only by design (no rendered chart image) to keep the live monitor lightweight; the button gives one-click access to the full chart. Chart images are rendered only for the market breadth alerts.

### Sector tags and daily sector counts

Momentum BUY, EP BUY, and batch BUY messages include `Sector: ...`. The shared lookup in `scanner/sector.py` uses Upstox's [Get Company Profile API](https://upstox.com/developer/api-documentation/get-company-profile/): `GET /v2/fundamentals/{isin}/profile`, reading **`data.sector`** (for example, `Refineries`). The normalized ticker is matched exactly against normal NSE equities in Upstox's instrument master to obtain the stock's `isin`. No IPO lookup or industry field is used. Sector is informational and does not change signal eligibility, stop-loss rules, or position sizing.

The first lookup after an alert qualifies loads the Upstox instrument master and caches its symbol-to-ISIN mapping. Each qualifying stock's company profile is fetched through the existing Upstox authentication, rate limiter, timeout, and retry helper. Per-symbol sector results (including `NA`) are cached per process, so a stock triggering both Momentum and EP does not repeat its profile request. These lookups can add provider/network latency to delivery.

Missing ISINs, absent/invalid `data.sector`, API failures, and instrument-master failures are logged and displayed as **`NA`**, without blocking the alert. Missing sectors are grouped under `NA` in daily counts and shown as `NA` in swing reports. The supplied Upstox sector label is used as returned; this does not guarantee profile coverage for every stock. There is no secondary provider, new credential, or workflow change.

The live alert CSV appends a `sector` column so downstream reports preserve the classification recorded at entry. The end-of-day report displays sector beside each stock and, after stock performances, lists total alerts, Momentum BUY count, EP count, distinct stock count, and EP radar ORB count per sector. Counts include all today's logged signal events, even when price/performance data is missing. A stock can contribute an EP, a Momentum BUY, and up to five radar ORB events, but remains one distinct stock within its sector; these are not counts of winning trades.

### Signal-frequency context

Every live EP, Momentum, and EP-radar BUY message appends a line such as `Alert History: 2 today | 3 in last 7 days`. Counts combine all live signal types for the same stock, including each radar timeframe separately. "Last 7 days" means the current IST calendar date plus the preceding six dates, not Monday-to-Sunday or seven trading sessions. The current signal is included; events for other stocks and future dates are excluded. Batch scan alerts and swing exit notifications are not included.

`scanner/signal_history.py` maintains `artifacts/signal_history.csv` separately from the first-entry trade history. The live workflow commits this history alongside market breadth even when the scanner times out, provided the save step completes successfully. Later runs on that branch load it before counting; restarting still does not suppress an otherwise eligible repeat. Counts describe recorded signal events, not independently verified Telegram deliveries or a statistical increase in win probability.

History starts when this feature is deployed; older first-entry swing rows cannot reconstruct all earlier signals. Missing pre-feature events are not invented. Counts can be incomplete if historical files were unavailable, a run was cancelled before persistence, or a history push failed. Swing CSV/Excel append `Signals On Entry Day` and `Signals In Last 7 Days`, covering recorded events through the end of the entry date; these counts can be higher than the first alert's contemporaneous count. Legacy entries with no available event coverage retain prior counts or show `NA`. Loading today's artifact for reporting deduplicates events already present in the signal history.

### First-alert trade selection

Daily and swing performance use **one entry per stock per IST trading date**, selected by the earliest recorded alert timestamp, regardless of whether it is EP, Momentum BUY, or EP radar ORB. The first alert's price, time, type, previous-day-low SL, and market context remain the entry inputs. Later signals are confirmations, not new trades or replacement entry prices. The full `live_alerts.csv` retains every event, and sector counts still count every event. Daily `performance.csv` and the swing CSV/Excel contain only the first-entry trade. Sector counts can therefore exceed the number of evaluated trades.

The shared selector `scanner/alert_history.py` sorts by timestamp, not file order or lowest price, and keeps input order for exactly tied timestamps. Naive legacy timestamps are interpreted as IST. Old date-only logs cannot establish intraday order and use the date at midnight; invalid timestamps are logged and retained for data-status reporting rather than silently discarded. Swing reruns also collapse duplicate same-stock/day rows already in the persistent CSV. Later signals survive in the source live-alert artifact, not as separate swing trades; entries on different dates remain separate trades.

`performance.csv` appends `sector` and `alert_type` columns; swing CSV/Excel use `Sector`. Older logs without sector remain readable and are backfilled via the shared Upstox lookup. Legacy `industry`/`Industry` fields are not reinterpreted as sector; these rows fetch the company-profile sector instead. Existing recorded sector labels are preserved; unavailable labels (including legacy `Unavailable` values) may be retried by a later reporting process. Backfilled labels reflect the provider's current classification, not a reconstructed historical classification. Sector examples in this README are illustrative.

Offline regressions in `test_sector.py` cover ISIN resolution, the exact company-profile URL, `data.sector` parsing, caching, missing/invalid fields, request failures, legacy CSV compatibility, daily counts, and Excel sector export. `test_alerts.py` covers message splitting; live-monitor tests mock the sector lookup so they remain offline. Run all three suites with the virtual-environment command in the quick reference. These tests use mocked Upstox/Telegram calls, not live credentials.

Validated on 2026-10-18: **94 tests passed** with `$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python.exe -W ignore::DeprecationWarning -m unittest test_rate_limits test_signal_history test_ep_radar test_live_engine test_sector test_alerts -q`. Tests cover 4,001 simulated request admissions against all three sliding quota windows, parallel limiter use, uncapped Retry-After/cooldowns, IPO pagination through the shared limiter, Telegram text/photo/document Unicode boundaries and retries, rolling signal counts, persistence/reload, IST boundaries, first-entry preservation, market-regime workbook statistics, and existing alert/sector behavior. Deprecation warnings from existing pandas/NumPy date arithmetic are suppressed in this command. Live API availability and actual Telegram delivery are not exercised by these offline tests.

Telegram delivery (`_post()` in `scanner/alerts.py`) paces messages per chat (1.1 s for private chats, 3.1 s for group chats whose ID starts with `-`) and across the process at no more than 25 sends/s. It honors the full HTTP 429 `retry_after`, and retries network/5xx errors up to 3 attempts, logging exhausted delivery explicitly. Text, photos and documents share that path; document retries reuse the complete file bytes, not a consumed file handle. The Bot API limits are approximately 1 msg/s per chat, 20 msgs/min per group and 30 msgs/s per bot; pacing does not coordinate separate processes using the same bot.

Text is split losslessly into parts of at most **3,900 UTF-16 units**, below Telegram's 4,096 text limit, including emoji and the new signal-frequency line. The chart button stays on the first text part. Photo and document captions are checked against **1,024 UTF-16 units**; longer captions are sent as separate safely split text rather than rejected or truncated. Normal BUY alerts do not need shortening. Large summaries and unusually long sector labels automatically split. File size, credentials, connectivity and provider-side failures can still prevent delivery.

### TradingView Market Breadth Alerts

Sent right after the open market summary (20 minutes after 09:15 IST) by `send_breadth_alerts()` in `scanner/breadth.py`. One fetch of `https://www.tradingview.com/markets/india/#stocks` is parsed from the server-rendered HTML via `data-qa-id` anchors (`market-breadth-advance-decline`, `market-breadth-highs-lows`, `market-breadth-above-ma`). Three separate Telegram messages are sent, in this order, each as a dark TradingView-style PNG chart (matplotlib, `send_photo()` in `scanner/alerts.py`) with the text as caption. Captions over 1024 chars are sent as a follow-up text; if a chart fails to render or a section fails to parse, the text is sent alone.

1. **Advancing & declining** (`parse_advance_decline()`): advancing/declining counts and %, net advances, A/D ratio, up/down volume. Chart: green/red split bar plus stat tiles. Read: A/D ratio > 1 `advancers lead`, < 1 `decliners lead`.
2. **New highs & lows** (`parse_highs_lows()`): highs, lows, and net for 1W/1M/3M/1Y. TradingView shows no numeric labels here, so counts are rebuilt from bar heights: `height% / 50 * max(count-axis label)`. Chart: diverging bars (highs up, lows down) with orange net-highs markers. Read uses the longest period (1Y).
3. **Above moving averages** (`parse_moving_averages()`, exact `aria-valuenow`): % of stocks above 20/50/200-day MA. Chart: progress bars with 20/50/80 guide lines, plus a history line chart with oversold/overbought bands once two sessions are stored.

MA judgement rules (`breadth_judgement()`):

| Indicator | Condition | Judgement |
| --- | --- | --- |
| 200-day MA | > 50% | broader bull market |
| 200-day MA | <= 50% | broader bear market |
| 50-day MA and 200-day MA | <= 20% | highly oversold, bounce may come soon |
| 50-day MA and 200-day MA | >= 80% | highly overbought, correction may come soon |

History and trend (`update_breadth_history()`, `breadth_trend()`): only MA breadth is stored. Each run upserts today's row (`date`, `20-day MA`, `50-day MA`, `200-day MA`) into `artifacts/market_breadth.csv`, skips days where all three values failed to fetch, and prunes rows older than 61 days. The trend compares the last 6 stored sessions per MA: a change of at least 3 points is `going up` or `going down`, otherwise `flat`. The trend section is omitted until two sessions are stored. Tunables: `BREADTH_HISTORY_DAYS`, `BREADTH_TREND_SESSIONS`, `BREADTH_FLAT_POINTS`.

Text formats (each is the caption of its chart image):

```
📈 MARKET BREADTH - ADVANCING & DECLINING
Advancing: 2.65K (49%)
Declining: 2.8K (51%)
Net advances: -149
A/D ratio: 0.95
Up / down volume: 4.2B / 1.8B

Read: decliners lead
Source: https://www.tradingview.com/markets/india/#stocks
```

```
🆕 MARKET BREADTH - NEW HIGHS & LOWS
1W: Highs 1530 | Lows 1726 | Net -196
1M: Highs 550 | Lows 816 | Net -266
3M: Highs 295 | Lows 470 | Net -175
1Y: Highs 187 | Lows 220 | Net -33

Read (1Y): more new lows than highs
Source: https://www.tradingview.com/markets/india/#stocks
```

```
📊 MARKET BREADTH - ABOVE MOVING AVERAGES
Stocks trading above moving average:
20-day MA: 36%
50-day MA: 32%
200-day MA: 36%

Judgement:
200-day MA: broader bear market

Trend (last 6 sessions):
20-day MA: 48 > 45 > 41 > 40 > 38 > 36 (-12 pts, going down)
50-day MA: 40 > 38 > 36 > 35 > 33 > 32 (-8 pts, going down)
200-day MA: 37 > 37 > 36 > 36 > 36 > 36 (-1 pts, flat)
Source: https://www.tradingview.com/markets/india/#stocks
```

Missing MA values show as `NA`, followed by `Breadth data fetched: X/3` and any fetch error before the source line. Unparsed A/D or highs/lows sections show `Data unavailable`.

## Swing Performance

The live monitor records every Momentum BUY, Episodic Pivot BUY, and EP radar ORB signal with ticker, buy price, previous-day-low SL, entry market context, alert type, date, time, and sector in `live_alerts.csv`. Momentum BUY alerts are sent only when the previous-day-low distance is positive and no more than 8% below the buy price. Their maximum portfolio allocation for 1% portfolio risk is `min(25%, 1% / SL distance)`. The position-size filter does not apply to EP or EP radar ORB alerts. The `swing_performance.py` job merges the current day's alerts with the committed history in `artifacts/swing_performance.csv`, keeps the earliest alert per stock per IST date, and recalculates those first-entry trades.

The report stores the full NIFTY 500 and 200-day breadth context at entry and uses the previous trading day's low as the initial SL. Only a combined Bull Market entry gets the +15% / 20% profit booking rule; Bear, Choppy, Mixed, NA, and unknown contexts use +10% / 30%. The remaining quantity trails at the higher of breakeven and the 10 EMA on daily closing basis. A 10 EMA close break sells 50% of the remaining quantity; a 20 EMA close break or a post-trigger breakeven close break exits the remainder. `Latest Close` is the latest daily close on the `Performance As Of Date`; all SL checks are closing-basis checks. `Performance %` is computed from entry to latest close, with weighted realized and remaining-position return after partial exits. Status values are `Trade open`, `Partial exit`, and `Full exit`. EMA and SL checks use daily bars, so this is a daily-bar retrospective report rather than tick-level execution accounting.

The latest CSV remains committed under `artifacts/swing_performance.csv` as the persistent calculation source. Each run also creates a formatted `artifacts/swing_performance.xlsx` workbook with an overall dashboard, ISO-week sections, merged centered week titles, weekly numbering, repeated headers/descriptions, status colors, and performance formatting. Both CSV and weekly Excel detail rows append an `Sector` column, preserving the alert's recorded sector and backfilling older missing values where available. This column is appended so existing performance/status columns retain their positions and formatting. The visible workbook hides the internal Alert ID and shows a simple number restarting within each week; the CSV retains Alert ID for persistence and event tracking. When a Partial exit or Full exit is newly recorded with today's action date, the swing workflow sends a Telegram exit alert including sector. The existing Telegram sender splits long messages automatically. The workbook is uploaded as the `swing-performance-latest` GitHub Actions artifact and sent to Telegram as a document. The swing workflow runs automatically after `NSE Live Alert Performance` completes successfully and can also be started manually.

For a shareable web-format overview, open [`docs/stock-pattern-lab.html`](./docs/stock-pattern-lab.html) in a browser or publish it with any static web host.

The Excel Dashboard also groups trades by **entry-time Bull Market, Bear Market, Choppy Market, Mixed Market, and NA**, with an **Unknown** group for legacy/unrecognized context. Each group shows total trades, open trades, partial exits, full exits, closed wins/losses/breakeven, closed-trade win rate, all-trade mean performance, data/other-status count, and the number of full exits with usable returns. Only numeric returns on **Full exit** rows enter win rate (breakeven remains in its denominator); open and partial trades cannot become closed wins. Mean performance includes valid open/partial returns and is an unweighted trade average, not compounded portfolio P&L. Existing overall metrics and the separate `Sector` detail column remain unchanged.

## Configuration

Environment variables read in `config/settings.py`:

| Variable | Required for | Default |
| --- | --- | --- |
| `UPSTOX_ACCESS_TOKEN` | Fetching Upstox data and connecting the live feed | Empty; Upstox requests fail without it |
| `TELEGRAM_BOT_TOKEN` | Sending Telegram alerts | Empty; alerts print locally if not configured |
| `TELEGRAM_CHAT_ID` | Selecting Telegram recipient(s) | Empty; alerts print locally if not configured |

`UPSTOX_MAX_WORKERS` is a Python constant used by live historical-data preparation (default eight), not an environment variable. Batch concurrency is fixed at six in `main.py`.

The screening thresholds and VCP parameters are Python constants in `config/settings.py`, not environment variables. They include `MIN_PRICE=20`, `MIN_TURNOVER_CR=20`, `MAX_FROM_52W_HIGH=20`, `MIN_ATR_PCT=3.49` for Momentum/batch rules, `EP_MIN_ATR_PCT=3.0` for non-IPO EP alerts, `MIN_RVOL=1.30`, `BREAKOUT_BUFFER_PCT=0.50`, `EP_MIN_GAP_PCT=4.5`, `DARK_RED_CANDLE_DROP_PCT=4.0`, and `CANDIDATE_SCORE_REQUIRED=4`.

## Automation

There are six GitHub Actions workflows. The live alert, live performance, and swing performance workflows are chained from manual execution; the remaining workflows are manual `workflow_dispatch` jobs:

| Workflow | Schedule | Action |
| --- | --- | --- |
| `.github/workflows/daily-scan.yml` | Manual only | Runs `main.py`, then uploads `scan_results.csv` for seven days. Artifact upload fails if the file was not created. The comment mentioning a daily scan is stale; there is no `schedule` trigger in this file. |
| `.github/workflows/live-alerts.yml` | Manual only | Runs `live_monitor.py`. The process waits until 09:00 IST to collect pre-open IEP candidates, confirms alerts from regular quotes after 09:15, and normally monitors until 15:20. The 358-minute deadline starts at dispatch, so dispatch around 08:30 IST allows setup before 09:00 but ends around 14:28 IST. It uploads `live_alerts.csv` and commits market breadth and signal history. |
| `.github/workflows/live-performance.yml` | After successful live alerts and manual | Downloads the exact triggering `live-alerts.yml` artifact, runs `performance.py`, and uploads the daily report plus alert input for 30 days. |
| `.github/workflows/swing-performance.yml` | After successful live performance and manual | Downloads the alert input, recalculates all persistent swing rows until exit or report date, commits `artifacts/swing_performance.csv`, uploads the formatted Excel workbook for 90 days, and sends it to Telegram. |
| `.github/workflows/test-ipo-universe.yml` | Manual only | Checks out the hard-coded `Nilesh_branch_to_add_ipos` branch, installs dependencies, and calls the Upstox IPO API to print listed IPO symbols since 2026-01-01. It is not a main-branch test until its checkout ref is changed. |
| `.github/workflows/test-telegram.yml` | Manual only | Runs the fixed Telegram connectivity message. |

The daily scan and live monitor are separate jobs; the batch run does not feed state to the live process. The live performance and swing performance jobs consume workflow artifacts, while the committed swing, signal-history and market-breadth CSVs provide persistence between runs.

GitHub-hosted jobs are hard-killed at 6 hours, and the live monitor runs until 15:20 IST, so a live run started before ~09:20 IST is killed and ends as `cancelled`/`failure`. `live-performance.yml` (and therefore `swing-performance.yml`) then shows "This job was skipped". The `Run live scanner` step has `timeout-minutes: 355`, so it is stopped before the 6h job kill and the `if: always()` steps (breadth commit, `live-alerts` upload) still run. Chosen practice: run `NSE Live Alert Performance` manually after the live run; it uses the latest live run's `live-alerts` artifact, and its success triggers swing performance automatically.

### Main-branch checklist

Before enabling this reference branch in the main branch:

1. Add repository or organization Actions secrets named `UPSTOX_ACCESS_TOKEN`, `TELEGRAM_BOT_TOKEN`, and `TELEGRAM_CHAT_ID`. The chat ID may be comma-separated for multiple recipients.
2. Ensure Actions are enabled and the repository permits artifact read/write. `live-performance.yml` already declares `contents: read` and `actions: read` and uses the built-in `GITHUB_TOKEN` through `GH_TOKEN`. `live-alerts.yml` declares `contents: write` to push `artifacts/market_breadth.csv`; branch protection must allow `github-actions[bot]` to push, otherwise only that commit step fails.
3. Change `ref: stock_buy_alert_tgbot` in `test-ipo-universe.yml` to the intended main/default branch, or remove the explicit checkout ref so the workflow tests the selected branch. Otherwise that workflow can fail before running project code.
4. Keep the workflow's Python 3.12 and install from the checked-in `requirements.txt`.
5. Treat the manually started live job as a long-running external-data job: Upstox access, Nifty/NSE source access, TradingView (`tradingview.com/markets/india`) access, and Telegram access must be available from GitHub-hosted runners. The scanner connects at 09:00 IST, so dispatch early enough for setup; its 358-minute deadline starts at workflow dispatch.
6. Run `test_live_engine.py` manually after merging. It is the only automated Python test file currently wired to project behavior; the Telegram and IPO workflows are integration/smoke checks.

## Repository Map

| File | Responsibility |
| --- | --- |
| `main.py` | Batch scan orchestration and CSV/console reporting |
| `live_monitor.py` | WebSocket lifecycle, feed parsing, BUY alert dispatch, and market summary |
| `scanner/breadth.py` | TradingView breadth scraping, three chart+text alerts, MA history/trend |
| `scanner/universe.py` | Nifty constituent and NSE IPO universe construction |
| `scanner/upstox.py` | Instrument lookup, historical requests, rate limiting, and data conversion |
| `test_rate_limits.py` | Offline simulated REST quotas, retry cooldowns, and IPO request routing |
| `scanner/indicators.py` | EMA and ATR calculations |
| `scanner/vcp.py` | Original and optimized VCP evaluation |
| `scanner/engine.py` | Historical per-stock metrics, candidate score, and BUY filters |
| `scanner/live_engine.py` | Completed-bar state and per-tick signal evaluation |
| `scanner/signal_history.py` | Persistent live signal events and per-stock today/rolling-seven-day counts |
| `scanner/live_data.py` | Live market-cap lookup, Smallcap 250 instrument lookup, and intraday candle requests |
| `swing_performance.py` | Persistent alert performance calculation until exit |
| `artifacts/` | Versioned swing-performance CSV, signal-event history (`signal_history.csv`), and ~2-month market-breadth history (`market_breadth.csv`) |
| `docs/stock-pattern-lab.html` | Standalone shareable web-format overview |
| `docs/telegram-trader-rules.txt` | Copy-paste Telegram rules, signal counts, and report interpretation |
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
