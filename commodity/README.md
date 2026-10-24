# Commodity futures workflow

This folder is intentionally separate from the equity scanner. It monitors the
nearest active MCX futures contract for `GOLDTEN`, `SILVERMIC`, `CRUDEOILM`, and
`NATGASMINI`, rolling to the next contract when the front contract has 15 days
or fewer remaining.

Signals use EMA10 and EMA20 on both timeframes:

- Daily context is calculated from completed daily candles and takes priority.
- A 30-minute long setup retests the EMA zone within the configurable 0.25%
  tolerance, can undercut it, and closes back above the zone.
- A 30-minute short setup is the inverse rejection below the EMA zone.
- Stop reference is the entry-day low for long signals and entry-day high for
  short signals. Position sizing and targets are intentionally omitted.

The technical strategy layer also includes:

- `EMA_PULLBACK`: daily-aligned 30-minute EMA10/EMA20 retest.
- `VOLATILITY_BREAKOUT`: daily-aligned 20-candle breakout with commodity-
  specific ADX threshold and volume confirmation. Rising open interest is
  required when Upstox provides usable OI values.
- `MEAN_REVERSION`: neutral-daily-context setup after a 1.5 ATR extension
  beyond the EMA20/Bollinger extreme and a reversal candle.

Starting ADX thresholds are 22 for GOLDTEN, 25 for SILVERMIC, 25 for
CRUDEOILM, and 28 for NATGASMINI. These values are configuration starting
points for later walk-forward calibration. News data and time-of-day filters
are intentionally not used yet.

Alerts are delivered through the existing Telegram sender and include an MCX
TradingView chart link. No orders are placed.

Daily and 30-minute alerts explicitly show `SL` and its percentage distance,
plus the intraday strategy, ADX, volume ratio, and OI change when available.
Long SL uses the entry-day low; short SL uses the entry-day high. The separate
commodity performance job evaluates later 30-minute candles and reports `SL
HIT`, hit time, SL price, and direction-aware return when the level is reached.

The combined performance artifact contains separate Stock Performance and
Commodity Performance Excel sheets, a combined CSV with `asset_class`, and the
supporting `commodity_alerts.csv` and `commodity_performance.csv` files.

Run from the repository root after configuring `UPSTOX_ACCESS_TOKEN` and the
existing Telegram variables:

```bash
python -m jobs.commodity_monitor
```

The V3 30-minute history API is queried in sub-quarter chunks because Upstox
limits historical requests above 15 minutes to one quarter per request.
