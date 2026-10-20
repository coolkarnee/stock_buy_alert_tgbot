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
| Run offline tests (no network) | `.\.venv\Scripts\python.exe -m unittest tests.test_rate_limits tests.test_signal_history tests.test_ep_radar tests.test_live_engine tests.test_sector tests.test_alerts tests.test_universe -v` |
| Preview breadth alerts (live TradingView, prints text; no Telegram without tokens) | `.\.venv\Scripts\python.exe -c "from datetime import date; from scanner.breadth import send_breadth_alerts; send_breadth_alerts(date.today())"` |
| Check Upstox index symbols (needs token) | `$env:UPSTOX_ACCESS_TOKEN="..."; .\.venv\Scripts\python.exe -m tests.test_upstox_indices` |
| Telegram connectivity (needs bot token + chat ID) | `.\.venv\Scripts\python.exe -m tests.test_telegram` |
| Clean generated files | `Get-ChildItem -Recurse -Directory -Filter __pycache__ \| Where-Object FullName -notlike "*\.venv\*" \| Remove-Item -Recurse -Force; Remove-Item artifacts\live_alerts.csv,artifacts\performance.csv,artifacts\performance_summary.txt -ErrorAction SilentlyContinue` |

Notes:
- If emoji printing fails in the Windows console (`UnicodeEncodeError`), set `$env:PYTHONIOENCODING="utf-8"` first; GitHub runners are unaffected.
- The breadth preview writes today's row to `artifacts/market_breadth.csv`; discard it with `git restore artifacts/market_breadth.csv` (or delete it if untracked) unless you want it committed.
- Generated local outputs: `.venv/`, `__pycache__/`, and transient files under `artifacts/`. The current clone excludes these locally via `.git/info/exclude`; do not commit that local exclude file.

Run one of the scripts from the repository root:

```powershell
python -m jobs.live_monitor
python -m jobs.performance
python -m jobs.swing_performance
python -m unittest tests.test_live_engine -v
python -m tests.test_telegram
```

`jobs.performance` expects `artifacts/live_alerts.csv` produced by the live monitor. The Telegram test module does not need the Upstox token, though the workflow supplies it. The repository does not include a checked-in `.env` loader; configure environment variables in the shell, IDE, or workflow secrets.

For a local first run, execute the unittest suite before contacting external APIs. The live monitor, performance evaluator, swing report, and Telegram smoke test require network access; the live monitor also requires a valid Upstox token.

## Data And Universe

The universe is rebuilt each run. `scanner/universe.py` collects the union of constituents from Nifty 500, Nifty MidSmallcap 400, Nifty Microcap 250, and Nifty Smallcap 250 CSVs, plus IPOs whose actual listing date is within `IPO_LOOKBACK_MONTHS` (default 12 months). The rolling cutoff includes the date exactly 12 months before today. Symbols are uppercased and de-duplicated. `scanner/upstox.py` downloads the Upstox instrument master and retains matching `NSE_EQ` instruments of type `EQ` with normal security type.

Universe sources are external services, not bundled files. Individual source failures are printed as warnings; loading can continue if another source returns symbols. The run stops if no symbols are collected. Instruments not found in Upstox's instrument master are omitted.

The live monitor fetches approximately two years of daily candles for each eligible stock. Live state preparation uses `UPSTOX_MAX_WORKERS` (default eight), removes candles dated today, and requires at least one completed daily bar. BUY-alert context uses the exact NIFTY 500 index and the latest available stocks-above-200-MA breadth. Failed or empty history requests are skipped; unavailable context inputs are shown as NA.

Upstox REST requests share `scanner/upstox.py`'s thread-safe limiter across daily history, intraday candles, quote snapshots, company profiles and IPO pagination, including every retry. Safety budgets are `UPSTOX_REQUESTS_PER_SECOND=8`, `UPSTOX_REQUESTS_PER_MINUTE=480` and `UPSTOX_REQUESTS_PER_30_MINUTES=1950`, below the stated broker ceilings of 10/s, 500/min and 2,000/30min. The limiter spaces calls against the tightest budget: **one request approximately every 0.923 seconds at the defaults** (about 65/min sustained), not 8/s or 480/min sustained. Raising the longer-window budgets reduces safety headroom; 8/s alone does not protect the 30-minute quota. Invalid budgets above those ceilings fail explicitly. Most requests use a 15-second timeout and one transient-error retry; IPO pages retain two retries and a 20-second timeout. HTTP 429/selected 5xx responses set a shared cooldown, honoring numeric or HTTP-date `Retry-After` without the previous five-second cap. Exhausted requests still surface errors.

This is a **per-process** REST budget, not an account-wide quota coordinator. Do not overlap live, performance, swing or other applications using the same Upstox account/token; independent processes/runners can exceed the combined broker quota. The live WebSocket streams prices without one REST request per tick; its SDK connection/reconnect and subscription limits are separate. Public Nifty CSVs and the Upstox instrument-master asset download are not calls to `api.upstox.com` and remain outside this limiter.

Budget compliance trades throughput for safety: 900 one-request-per-stock history loads take about 13.8 minutes at minimum, before other calls/network delays. Full-universe startup can therefore delay opening alerts; start sufficiently early or arrange preloading before the session. Roughly 65 REST calls/minute sustained are available across candle refreshes, profiles and other requests. A large EP radar cannot refresh every candidate each minute simultaneously: candle requests queue and close confirmations can be reconstructed later, while spot alerts use the live price available when evaluated. Rate limiting does not guarantee timely provider data or Telegram delivery.

## Live Momentum BUY Rules

Every mandatory live Momentum BUY condition below must align:

| Condition | Live rule |
| --- | --- |
| Trend | Price above developing EMA21 after 21 completed sessions; earlier sessions are warm-up |
| Volume confirmation | At least one of RVOL > 1.30x, pocket pivot, first-30-minute surge, price up ≥4% from open, or daily volume >9M |
| Momentum | Price above yesterday's close |
| Breakout | Price above the previous-session reference with a 0.50% buffer |
| Swing strength | Any qualifying period in the table below after 63 completed sessions (the existing 3M/63-session reference); earlier sessions are warm-up |
| Near high | Within 15% of the 52-week high before 63 completed sessions; within 20% thereafter |
| Volatility | ATR14 >3.49% after 14 ATR inputs are available; earlier sessions are warm-up |
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

Only the existing two-year history request is used. A period qualifies only when its reference close is available; no three-year download is performed. Momentum swing strength is not enforced until 63 completed sessions.

The early-history rules are based on completed trading sessions, not IPO listing status. `IPO_LOOKBACK_MONTHS` (default 12) controls which recent listings are added to the universe; it does not grant signal exemptions. Without a listing date, recent-universe membership is unavailable, but the live signal warm-ups still use available history.

### Live alert format

Alerts show the data window behind ATR and turnover, so short-history stocks (IPOs) are easy to judge:

- `ATR Nd`: N = completed sessions used (max 13) + today's developing range. The value is still `(sum of TRs) / 14`, so N < 14 means fewer real ranges and an understated ATR.
- `Turnover Nd avg`: N = sessions in the turnover average (`turnover_period_days`, max 50, includes today).
- `Strength:` (Momentum BUY only) lists every swing-strength period that passed, with move and threshold (`swing_moves_matched` from `evaluate()`); before 63 completed sessions it shows warm-up progress.
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
- After 21 completed sessions, current price must be above developing EMA21; earlier sessions are EMA warm-up.
- Price is strictly above ₹20.
- ATR is strictly above `EP_MIN_ATR_PCT` (default **3.0%**) after 14 ATR inputs are available; before then ATR is warm-up.
- Turnover/liquidity is above ₹20 Cr.

EP alerts do not require Momentum BUY volume confirmation, momentum, breakout, rally-strength, near-high, or opening-range conditions.

EP BUY and Momentum BUY remain independent: an EP alert never suppresses a qualifying Momentum alert, and Momentum does not require an earlier EP alert. Each is sent at most once per symbol per day within the process.

The monitor connects at 09:00 IST and records Upstox's pre-open indicative equilibrium price (IEP). An IEP more than `EP_MIN_GAP_PCT` above the previous close nominates a candidate; it is indicative, not a traded price or an alert. After 09:15, a fresh regular-session quote must confirm the actual opening gap, developing EMA21, price floor, EP ATR threshold, and turnover before EP BUY is sent. Stocks without a usable pre-open IEP can still be found by the completed one-minute candle refresh after the open. Provider, REST queue, or Telegram delays can postpone delivery.

The EP ATR threshold is separate from Momentum BUY's `MIN_ATR_PCT`: `EP_MIN_ATR_PCT=3.0`. EP enforces developing EMA21 after 21 completed sessions; before that it is warm-up. Price >₹20 and available-history turnover >₹20 Cr apply from day one. EP does not require Momentum volume confirmation, swing strength, near-high, ORB, or stop-distance conditions.

Yesterday's close and indicator baselines still exclude today's unfinished daily candle to avoid double-counting it. Today's candle is included separately through the intraday EP data.

### EP gap-radar opening-range BUY

This is a separate, lighter scan, not a replacement for EP BUY or Momentum BUY:

- **Radar membership:** the actual opening minute's open is strictly more than `EP_MIN_GAP_PCT` (default 4.5%) above the previous completed session's close. A full EP BUY is **not** required. The opening gap defines membership even if the stock fails EMA, ATR, liquidity, price-floor, or Momentum checks.
- **Ranges:** first **3, 5, 15, 30, and 60 minutes** from market open. All one-minute price candles within a range must be available and valid before its high is trusted.
- **ORB confirmation:** uses the same `OPENING_RANGE_BREAKOUT_MODE` in `config/settings.py` as Momentum. `"close"` (default) requires a completed candle close strictly above the opening-range high; `"spot"` requires the current live price strictly above that high once the range has formed. This is the existing close/spot choice, not a new yes/no setting. Touching or equalling the high does not qualify.
- **Minimal quality filters:** after 21 completed sessions, qualifying price must be above developing EMA21; earlier sessions are EMA warm-up. Price must always be strictly above ₹20 and pass the same turnover/liquidity threshold (>₹20 Cr), calculated over available history up to 50 sessions. Close mode uses the confirming candle's close and cumulative volume through that candle; spot mode uses live price and cumulative volume (completed-candle volume is the baseline if the tick omits it). It still does not require Momentum volume surge, breakout-reference, near-high, swing strength, ATR, or SL-distance checks.
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

EP-radar regressions in `tests/test_ep_radar.py` cover both configured modes, strict crossings, completed ranges, live-price dispatch without a breakout close, quality filters, per-range duplicate suppression, and unchanged first-entry performance selection.

`IPO_LOOKBACK_MONTHS` (default 12) controls only which recent listings are added to the universe. Signal warm-ups apply to every stock by completed-session age: ATR gates after 14 inputs, EMA21 after 21 completed sessions, and Momentum swing strength plus the standard 20% near-high limit after 63 completed sessions. Before 63 sessions, Momentum uses a 15% near-high limit and skips swing strength. The 3M threshold uses the existing 63-session reference. Stocks without a previous daily candle cannot be evaluated against yesterday's close.

EMA10/EMA21 extension is shown in alerts but is not a blocking filter by default. Set `LIVE_BLOCK_EXTENDED=True` to require at most 10% above EMA10 and 20% above EMA21. Prior three-session average volume below the preceding 20-session average is informational only and never blocks a BUY. The combined market context is informational and never blocks alerts. For live BUY alerts, NIFTY 500 is Uptrending only when above both live EMA10 and EMA20 with both rising, Downtrending only when below both with both falling, and Chopping otherwise. TradingView's stocks-above-200-day-MA percentage is Broader Bull above 50% and Broader Bear at or below 50%; <=20% adds Oversold and >=80% adds Overbought. Matching bullish/bearish readings yield Bull/Bear Market, disagreement yields Mixed Market, and unavailable readings show NA with the other available value retained.

Market-summary indexes are matched in the Upstox instrument master on either `trading_symbol` or `name` (normalized to uppercase alphanumerics), because some indexes differ between the two fields; for example Nifty 50 has `trading_symbol` `NIFTY` and `name` `Nifty 50`.

RVOL projects cumulative volume to a 375-minute session using elapsed session time and the preceding 20-session average. Turnover uses actual cumulative volume, not projected volume. Live EMA and ATR incorporate the developing daily candle, so they may change before the close.

There is no market-cap lookup or market-cap rejection in the active signal path. The unused external market-cap helper has been removed. Missing/incomplete opening candles cannot establish an ORB; these requests are retried at most once per minute per stock until all five ranges are available. The first eligible alert can be delayed by provider latency, queues, or rate limits. Starting after the open still reconstructs the full opening ranges from candles, rather than approximating them from observed ticks.

Run the offline regression suite with `python -m unittest tests.test_live_engine -v`. It does not contact Telegram or Upstox. Fixtures cover close-mode ORB, 50-day turnover > 20 Cr, RVOL from `rvol_prev19_volume`, swing-move strength, and the configurable EP gap and completed-opening-candle gate. The EP monitor regression covers a pre-open start, incomplete WebSocket OHLC/volume, and once-per-day delivery from completed candle data. Live monitor tests write transient logs under temporary directories. Real-time delivery additionally requires valid Upstox and Telegram credentials and a running monitor each trading day.

To diagnose Upstox index symbols and EMA history, run `UPSTOX_ACCESS_TOKEN=... python -m tests.test_upstox_indices`. It lists every available `NSE_INDEX` instrument, checks the six market-summary indexes, and prints EMA10/EMA21 values. Add `--all-indexes` to calculate EMAs for every available Upstox NSE index.

## Outputs And Alerts

The live monitor sends Momentum BUY and Episodic Pivot BUY messages at most once per category and symbol per trading day within that process, plus up to five independent EP gap-radar ORB messages per symbol. Restart the monitor each trading day; it exits at 15:20 IST. There is no database or file-backed state to suppress repeats across separate runs on the same day.

Telegram delivery uses the Telegram Bot API `sendMessage` endpoint. A non-success response or request exception is printed and processing continues. Missing Telegram credentials cause the message to be printed locally.

Stock Telegram alerts include an `Open TradingView Chart` inline button. The button uses the cross-platform HTTPS URL `https://www.tradingview.com/chart/?symbol=NSE%3ASYMBOL`, which opens the chart in the TradingView app when the device handles the link there, or in the browser otherwise. The exchange-qualified symbol avoids ambiguity for NSE stocks. The same link is used by live Momentum, EP, and EP-radar alerts. Stock alerts are text-only by design (no rendered chart image) to keep the live monitor lightweight; the button gives one-click access to the full chart. Chart images are rendered only for the market breadth alerts.

### Sector tags and daily sector counts

Momentum BUY, EP BUY, and EP-radar BUY messages include `Sector: ...`. The shared lookup in `scanner/sector.py` uses Upstox's [Get Company Profile API](https://upstox.com/developer/api-documentation/get-company-profile/): `GET /v2/fundamentals/{isin}/profile`, reading **`data.sector`** (for example, `Refineries`). The normalized ticker is matched exactly against normal NSE equities in Upstox's instrument master to obtain the stock's `isin`. No IPO lookup or industry field is used. Sector is informational and does not change signal eligibility, stop-loss rules, or position sizing.

The first lookup after an alert qualifies loads the Upstox instrument master and caches its symbol-to-ISIN mapping. Each qualifying stock's company profile is fetched through the existing Upstox authentication, rate limiter, timeout, and retry helper. Per-symbol sector results (including `NA`) are cached per process, so a stock triggering both Momentum and EP does not repeat its profile request. These lookups can add provider/network latency to delivery.

Missing ISINs, absent/invalid `data.sector`, API failures, and instrument-master failures are logged and displayed as **`NA`**, without blocking the alert. Missing sectors are grouped under `NA` in daily counts and shown as `NA` in swing reports. The supplied Upstox sector label is used as returned; this does not guarantee profile coverage for every stock. There is no secondary provider, new credential, or workflow change.

The live alert CSV appends a `sector` column so downstream reports preserve the classification recorded at entry. The end-of-day report displays sector beside each stock and, after stock performances, lists total alerts, Momentum BUY count, EP count, distinct stock count, and EP radar ORB count per sector. Counts include all today's logged signal events, even when price/performance data is missing. A stock can contribute an EP, a Momentum BUY, and up to five radar ORB events, but remains one distinct stock within its sector; these are not counts of winning trades.

### Signal-frequency context

Every live EP, Momentum, and EP-radar BUY message appends two counts, for example `Intraday Alert Count: 3 today | Daily Alert Count: 2 of last 7 trading days`. Intraday count includes every alert event for the stock today; each EP, Momentum, and radar timeframe counts separately and resets on the next IST date. Daily Alert Count counts distinct trading sessions with one or more alerts among the latest seven sessions, so multiple same-day alerts increase only the intraday count. Session dates come from completed NIFTY 500 daily candles and persist in `artifacts/trading_sessions.csv`. If unavailable, Daily Alert Count is `NA`; swing exit notifications are not included.

`scanner/signal_history.py` maintains `artifacts/signal_history.csv` separately from the first-entry trade history. The live workflow commits this history and `artifacts/trading_sessions.csv` alongside market breadth even when the scanner times out, provided the save step completes successfully. Later runs on that branch load them before counting; restarting still does not suppress an otherwise eligible repeat. Counts describe recorded signal events, not independently verified Telegram deliveries or a statistical increase in win probability.

History starts when this feature is deployed; older first-entry swing rows cannot reconstruct all earlier signals. Missing pre-feature events are not invented. Counts can be incomplete if historical files were unavailable, a run was cancelled before persistence, or a history push failed. Swing CSV/Excel show `Intraday Alert Count` and `Daily Alert Count (7 Trading Days)` for the first-entry trade, recalculated from persisted events and session dates. Legacy rows preserve a known entry-day event count; their old calendar-based rolling count is not converted and appears as `NA`. Loading today's artifact for reporting deduplicates events already present in the signal history.

### First-alert trade selection

Daily and swing performance use **one entry per stock per IST trading date**, selected by the earliest recorded alert timestamp, regardless of whether it is EP, Momentum BUY, or EP radar ORB. The first alert's price, time, type, previous-day-low SL, and market context remain the entry inputs. Later signals are confirmations, not new trades or replacement entry prices. The full `artifacts/live_alerts.csv` retains every event, and sector counts still count every event. `artifacts/performance.csv` and the swing CSV/Excel contain only the first-entry trade. Sector counts can therefore exceed the number of evaluated trades.

The shared selector `scanner/alert_history.py` sorts by timestamp, not file order or lowest price, and keeps input order for exactly tied timestamps. Naive legacy timestamps are interpreted as IST. Old date-only logs cannot establish intraday order and use the date at midnight; invalid timestamps are logged and retained for data-status reporting rather than silently discarded. Swing reruns also collapse duplicate same-stock/day rows already in the persistent CSV. Later signals survive in the source live-alert artifact, not as separate swing trades; entries on different dates remain separate trades.

`artifacts/performance.csv` appends `sector` and `alert_type` columns; swing CSV/Excel use `Sector`. Older logs without sector remain readable and are backfilled via the shared Upstox lookup. Legacy `industry`/`Industry` fields are not reinterpreted as sector; these rows fetch the company-profile sector instead. Existing recorded sector labels are preserved; unavailable labels (including legacy `Unavailable` values) may be retried by a later reporting process. Backfilled labels reflect the provider's current classification, not a reconstructed historical classification. Sector examples in this README are illustrative.

Offline regressions in `tests/test_sector.py` cover ISIN resolution, the exact company-profile URL, `data.sector` parsing, caching, missing/invalid fields, request failures, legacy CSV compatibility, daily counts, and Excel sector export. `tests/test_alerts.py` covers message splitting; live-monitor tests mock the sector lookup so they remain offline. Run the suite with the virtual-environment command in the quick reference. These tests use mocked Upstox/Telegram calls, not live credentials.

The offline suite is run with `.\.venv\Scripts\python.exe -m unittest tests.test_rate_limits tests.test_signal_history tests.test_ep_radar tests.test_live_engine tests.test_sector tests.test_alerts tests.test_universe -v`. It covers REST quota/retry behavior, alert logic, IPO listing-date window boundaries, Telegram formatting, rolling signal counts, persistence/reload, IST boundaries, first-entry selection, and swing workbook output. Existing pandas/NumPy deprecation warnings remain; live API availability and actual Telegram delivery are not exercised by these tests.

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

History and trend (`update_breadth_history()`, `breadth_trend()`): only MA breadth is stored. Each run upserts today's row (`date`, `20-day MA`, `50-day MA`, `200-day MA`) into `artifacts/market_breadth.csv`, skips days where all three values failed to fetch, and prunes rows older than 61 days. Trend compares the last seven stored trading sessions per MA; changes of at least 3 percentage points are `going up` or `going down`, otherwise `flat`. Before two sessions are stored it reports trend unavailable and the available-session count; from 2/7 through 6/7 it labels the trend provisional. A full seven-session trend appears after seven successful trading-day records. Thus tomorrow's run can show a provisional 2/7 trend, not a full seven-session trend. Tunables: `BREADTH_HISTORY_DAYS`, `BREADTH_TREND_SESSIONS`, `BREADTH_FLAT_POINTS`.

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

Trend (last 7/7 trading sessions):
20-day MA: 60 > 55 > 50 > 45 > 40 > 38 > 36 (-24 pts, going down)
50-day MA: 48 > 45 > 42 > 39 > 36 > 34 > 32 (-16 pts, going down)
200-day MA: 40 > 39 > 38 > 37 > 37 > 36 > 36 (-4 pts, going down)
Source: https://www.tradingview.com/markets/india/#stocks
```

Missing MA values show as `NA`, followed by `Breadth data fetched: X/3` and any fetch error before the source line. Unparsed A/D or highs/lows sections show `Data unavailable`.

## Swing Performance

The live monitor records every Momentum BUY, Episodic Pivot BUY, and EP radar ORB signal with ticker, buy price, previous-day-low SL, entry market context, alert type, date, time, and sector in `artifacts/live_alerts.csv`. Momentum BUY alerts are sent only when the previous-day-low distance is positive and no more than 8% below the buy price. Their maximum portfolio allocation for 1% portfolio risk is `min(25%, 1% / SL distance)`. The position-size filter does not apply to EP or EP radar ORB alerts. The `jobs/swing_performance.py` job merges the current day's alerts with the committed history in `artifacts/swing_performance.csv`, keeps the earliest alert per stock per IST date, and recalculates those first-entry trades.

The report stores the full NIFTY 500 and 200-day breadth context at entry and uses the previous trading day's low as the initial SL. Only a combined Bull Market entry gets the +15% / 20% profit booking rule; Bear, Choppy, Mixed, NA, and unknown contexts use +10% / 30%. The remaining quantity trails at the higher of breakeven and the 10 EMA on daily closing basis. A 10 EMA close break sells 50% of the remaining quantity; a 20 EMA close break or a post-trigger breakeven close break exits the remainder. `Latest Close` is the latest daily close on the `Performance As Of Date`; all SL checks are closing-basis checks. `Performance %` is computed from entry to latest close, with weighted realized and remaining-position return after partial exits. Status values are `Trade open`, `Partial exit`, and `Full exit`. EMA and SL checks use daily bars, so this is a daily-bar retrospective report rather than tick-level execution accounting.

The latest CSV remains committed under `artifacts/swing_performance.csv` as the persistent calculation source. Each run also creates a formatted `artifacts/swing_performance.xlsx` workbook with an overall dashboard, ISO-week sections, merged centered week titles, weekly numbering, repeated headers/descriptions, status colors, and performance formatting. CSV and workbook rows include `Sector`, `Intraday Alert Count`, and `Daily Alert Count (7 Trading Days)`. Intraday counts preserve all same-day alert events; daily counts count distinct alerted trading sessions. Legacy calendar-based totals are not converted into trading-day values and show `NA` until valid session history is available. The visible workbook hides the internal Alert ID and shows a simple number restarting within each week; the CSV retains Alert ID for persistence and event tracking. When a Partial exit or Full exit is newly recorded with today's action date, the swing workflow sends a Telegram exit alert including sector. The existing Telegram sender splits long messages automatically. The workbook is uploaded as the `swing-performance-latest` GitHub Actions artifact and sent to Telegram as a document. The swing workflow runs automatically after `NSE Live Alert Performance` completes successfully and can also be started manually.

For a shareable web-format overview, open [`docs/stock-pattern-lab.html`](./docs/stock-pattern-lab.html) in a browser or publish it with any static web host.

The Excel Dashboard also groups trades by **entry-time Bull Market, Bear Market, Choppy Market, Mixed Market, and NA**, with an **Unknown** group for legacy/unrecognized context. Each group shows total trades, open trades, partial exits, full exits, closed wins/losses/breakeven, closed-trade win rate, all-trade mean performance, data/other-status count, and the number of full exits with usable returns. Only numeric returns on **Full exit** rows enter win rate (breakeven remains in its denominator); open and partial trades cannot become closed wins. Mean performance includes valid open/partial returns and is an unweighted trade average, not compounded portfolio P&L. Existing overall metrics and the separate `Sector` detail column remain unchanged.

## Configuration

Environment variables read in `config/settings.py`:

| Variable | Required for | Default |
| --- | --- | --- |
| `UPSTOX_ACCESS_TOKEN` | Fetching Upstox data and connecting the live feed | Empty; Upstox requests fail without it |
| `TELEGRAM_BOT_TOKEN` | Sending Telegram alerts | Empty; alerts print locally if not configured |
| `TELEGRAM_CHAT_ID` | Selecting Telegram recipient(s) | Empty; alerts print locally if not configured |

`UPSTOX_MAX_WORKERS` is a Python constant used by live historical-data preparation (default eight), not an environment variable.

The live scanner thresholds are Python constants in `config/settings.py`, not environment variables. They include `MIN_PRICE=20`, `MIN_TURNOVER_CR=20`, `MAX_FROM_52W_HIGH=20`, `EARLY_HISTORY_MAX_FROM_52W_HIGH=15`, `MIN_ATR_PCT=3.49`, `EP_MIN_ATR_PCT=3.0`, `ATR_REQUIRED_SESSIONS=14`, `EMA21_REQUIRED_SESSIONS=21`, `MOMENTUM_STRENGTH_START_SESSIONS=63`, `IPO_LOOKBACK_MONTHS=12` for universe membership only, `MIN_RVOL=1.30`, `BREAKOUT_BUFFER_PCT=0.50`, `EP_MIN_GAP_PCT=4.5`, and `DARK_RED_CANDLE_DROP_PCT=4.0`.

## Automation

There are five GitHub Actions workflows. The live alert, live performance, and swing performance workflows form the alert-to-report lifecycle; the remaining workflows are manual `workflow_dispatch` jobs:

| Workflow | Schedule | Action |
| --- | --- | --- |
| `.github/workflows/live-alerts.yml` | Manual only | Runs `jobs.live_monitor`. The process waits until 09:00 IST to collect pre-open IEP candidates, confirms alerts from regular quotes after 09:15, and normally monitors until 15:20. The 358-minute deadline starts at dispatch, so dispatch around 08:30 IST allows setup before 09:00 but ends around 14:28 IST. It uploads `artifacts/live_alerts.csv` and commits market breadth and signal history. |
| `.github/workflows/live-performance.yml` | After successful live alerts and manual | Downloads the exact triggering live-alert artifact, runs `jobs.performance`, and uploads `artifacts/performance.csv`, `artifacts/live_alerts.csv`, and `artifacts/performance_summary.txt` for 30 days. |
| `.github/workflows/swing-performance.yml` | After successful live performance and manual | Downloads the alert input, runs `jobs.swing_performance`, recalculates all persistent swing rows until exit or report date, commits `artifacts/swing_performance.csv`, uploads the formatted Excel workbook for 90 days, and sends it to Telegram. |
| `.github/workflows/test-ipo-universe.yml` | Manual only | Checks out the selected branch, installs dependencies, and prints IPO symbols with listing dates inside the configured rolling `IPO_LOOKBACK_MONTHS` window. |
| `.github/workflows/test-telegram.yml` | Manual only | Runs the fixed Telegram connectivity message. |

The live alert workflow produces the log consumed by daily performance; successful daily performance triggers swing performance. Committed swing, signal-history and market-breadth CSVs provide persistence between runs.

GitHub-hosted jobs are hard-killed at 6 hours, and the live monitor runs until 15:20 IST, so a live run started before ~09:20 IST is killed and ends as `cancelled`/`failure`. `live-performance.yml` (and therefore `swing-performance.yml`) then shows "This job was skipped". The `Run live scanner` step has `timeout-minutes: 355`, so it is stopped before the 6h job kill and the `if: always()` steps (breadth commit, `live-alerts` upload) still run. Chosen practice: run `NSE Live Alert Performance` manually after the live run; it uses the latest live run's `live-alerts` artifact, and its success triggers swing performance automatically.

### Main-branch checklist

Before enabling this reference branch in the main branch:

1. Add repository or organization Actions secrets named `UPSTOX_ACCESS_TOKEN`, `TELEGRAM_BOT_TOKEN`, and `TELEGRAM_CHAT_ID`. The chat ID may be comma-separated for multiple recipients.
2. Ensure Actions are enabled and the repository permits artifact read/write. `live-performance.yml` already declares `contents: read` and `actions: read` and uses the built-in `GITHUB_TOKEN` through `GH_TOKEN`. `live-alerts.yml` declares `contents: write` to push `artifacts/market_breadth.csv`; branch protection must allow `github-actions[bot]` to push, otherwise only that commit step fails.
3. Ensure the IPO-universe workflow's selected ref is the intended branch before dispatching it.
4. Keep the workflow's Python 3.12 and install from the checked-in `requirements.txt`.
5. Treat the manually started live job as a long-running external-data job: Upstox access, Nifty/NSE source access, TradingView (`tradingview.com/markets/india`) access, and Telegram access must be available from GitHub-hosted runners. The scanner connects at 09:00 IST, so dispatch early enough for setup; its 358-minute deadline starts at workflow dispatch.
6. Run the offline suite with the `tests.*` module names in the quick reference after merging. The Telegram and IPO workflows are integration/smoke checks.

## Repository Map

| File | Responsibility |
| --- | --- |
| `jobs/live_monitor.py` | WebSocket lifecycle, feed parsing, BUY alert dispatch, and market summary |
| `jobs/performance.py` | Daily alert performance report and next-session conclusion |
| `tests/test_live_engine.py` | Offline Momentum, EP, market-context, and live dispatch tests |
| `scanner/breadth.py` | TradingView breadth scraping, three chart+text alerts, MA history/trend |
| `scanner/universe.py` | Nifty constituent and NSE IPO universe construction |
| `scanner/upstox.py` | Instrument lookup, historical requests, rate limiting, and data conversion |
| `tests/test_rate_limits.py` | Offline simulated REST quotas, retry cooldowns, and IPO request routing |
| `scanner/indicators.py` | EMA and ATR calculations |
| `scanner/live_engine.py` | Completed-bar state and per-tick signal evaluation |
| `scanner/signal_history.py` | Persistent live signal events and per-stock today/rolling-seven-day counts |
| `scanner/live_data.py` | Live market-cap lookup, Smallcap 250 instrument lookup, and intraday candle requests |
| `jobs/swing_performance.py` | Persistent alert performance calculation until exit |
| `artifacts/` | Scanner outputs and reports plus versioned swing-performance, signal-event, and market-breadth history |
| `tests/` | Offline unit tests and manual Upstox/Telegram smoke checks |
| `docs/stock-pattern-lab.html` | Standalone shareable web-format overview |
| `docs/telegram-trader-rules.txt` | Copy-paste Telegram rules, signal counts, and report interpretation |
| `scanner/alerts.py` | Telegram dispatch and shared alert utilities |
| `config/settings.py` | Credentials and scanner thresholds |
| `tests/test_telegram.py` | Manual Telegram connectivity check |
| `.github/workflows/` | Scheduled/manual GitHub Actions jobs |

## Current Limitations And Checks Before Replacing Components

- There is no persistent lifecycle state, position tracking, cooldown, defense/exit logic, broker order placement, or backtest. Live momentum behavior has an offline regression suite.
- Live alerts are marked sent before Telegram delivery; the unchanged Telegram transport logs delivery failures but does not retry them for that symbol during the same process.
- External data formats and availability are assumed by the code. Universe loading, instrument master retrieval, benchmark lookup, and history downloads can fail when those services, schemas, credentials, or network access change.
- Live monitoring stops its evaluation loop at 15:20 IST. It has WebSocket auto-reconnect enabled, but does not persist alerts or scanner state after process exit.

Use these behaviors as the baseline when changing or replacing a module: live alerting, daily performance, and swing performance are separate workflow stages connected by artifacts and persistent CSV history.
