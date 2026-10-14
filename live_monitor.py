import time
import csv
import html
import re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time as dtime, timedelta
from threading import Lock
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import upstox_client
from google.protobuf.json_format import MessageToDict

from config import settings
from scanner.alerts import send
from scanner.live_engine import build_state, evaluate, update_intraday, _completed
from scanner.live_data import intraday_minutes
from scanner.indicators import ema
from scanner.upstox import (
    load_instruments,
    load_nse_equities,
    historical_daily,
    historical_many,
)

IST = ZoneInfo("Asia/Kolkata")
TRADINGVIEW_INDIA_URL = "https://www.tradingview.com/markets/india/#stocks"

MARKET_INDICES = (
    ("Nifty", "NIFTY"),
    ("NIFTY 500", "NIFTY 500"),
    ("NIFTY MIDSML400", "NIFTY MIDSML 400"),
    ("NIFTY MIDCAP 150", "NIFTY MIDCAP 150"),
    ("NIFTY SMLCAP 250", "NIFTY SMLCAP 250"),
    ("NIFTY MICROCAP 250", "NIFTY MICROCAP250"),
)


def find_market_index_keys(instruments):
    """Return exact Upstox NSE_INDEX matches; unavailable indices remain absent."""
    found = {}
    for display_name, trading_symbol in MARKET_INDICES:
        matches = [
            instrument for instrument in instruments
            if instrument.get("segment") == "NSE_INDEX"
            and instrument.get("instrument_type") == "INDEX"
            and str(instrument.get("trading_symbol", "")).upper() == trading_symbol
        ]
        if len(matches) == 1:
            found[display_name] = matches[0]["instrument_key"]
        else:
            print(
                f"{display_name} exact Upstox instrument unavailable; "
                f"expected 1 match for {trading_symbol!r}, found {len(matches)}."
            )
    return found


def classify_index(index_state, quote):
    """Classify an index using the existing live EMA market-context rules."""
    price = quote.get("last_price", 0.0)
    if price <= 0 or index_state["ema10"] <= 0 or index_state["ema20"] <= 0:
        return "Chop", None
    live_ema10 = index_state["ema10"] + 2 / 11 * (price - index_state["ema10"])
    live_ema20 = index_state["ema20"] + 2 / 21 * (price - index_state["ema20"])
    ema10_up = live_ema10 > index_state["prev_ema10"]
    ema20_up = live_ema20 > index_state["prev_ema20"]
    if price > live_ema10 and price > live_ema20 and ema10_up and ema20_up:
        return "Bull", (live_ema10, live_ema20)
    if price < live_ema10 and price < live_ema20:
        return "Bear", (live_ema10, live_ema20)
    return "Chop", (live_ema10, live_ema20)


def market_summary(phase, index_states, index_quotes, previous_conclusion=None, alert_counts=None):
    lines = [f"📈 MARKET {phase} SUMMARY", ""]
    regimes = []
    for name, _ in MARKET_INDICES:
        state = index_states.get(name)
        quote = index_quotes.get(name)
        if not state or not quote or quote.get("last_price", 0) <= 0:
            lines.append(f"{name} NA |")
            continue
        regime, _ = classify_index(state, quote)
        move = (quote["last_price"] / state["prev_close"] - 1) * 100
        lines.append(f"{name} {move:+.2f}% {regime} |")
        regimes.append(regime)

    if len(regimes) < len(MARKET_INDICES):
        lines.extend(["", f"Index data fetched: {len(regimes)}/{len(MARKET_INDICES)}"])

    if regimes:
        counts = {regime: regimes.count(regime) for regime in ("Bull", "Bear", "Chop")}
        leading = max(counts, key=counts.get)
        overall = leading if counts[leading] > len(regimes) / 2 else "Chop"
        lines.extend(["", f"Overall market: {overall.lower()}"])
    else:
        lines.extend(["", "Overall market: unavailable"])
    if previous_conclusion:
        lines.extend(["", "Yesterday's intraday alert performance conclusion:", previous_conclusion])
    if alert_counts is not None:
        lines.extend(["", f"Today's alerts: {alert_counts['buy']} Momentum BUY | {alert_counts['ep']} EP"])
    return "\n".join(lines)


def prior_performance_conclusion(path=Path("performance_summary.txt")):
    try:
        text = path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return "No prior-day performance summary available."
    return text or "No prior-day performance summary available."


def scrape_tradingview_breadth(url=TRADINGVIEW_INDIA_URL):
    """Fetch the three TradingView India market-breadth percentages."""
    values = {"20-day MA": None, "50-day MA": None, "200-day MA": None}
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; market-monitor/1.0)"},
            timeout=20,
        )
        response.raise_for_status()
        text = html.unescape(re.sub(r"<[^>]+>", " ", response.text))
        text = re.sub(r"\s+", " ", text)
        for label in values:
            match = re.search(
                rf"{re.escape(label)}\s+(\d+(?:\.\d+)?)\s*%",
                text,
                flags=re.IGNORECASE,
            )
            if match:
                values[label] = float(match.group(1))
    except Exception as exc:
        return values, str(exc)
    return values, None


def fmt_breadth_alert(values, error=None):
    lines = [
        "📊 TRADINGVIEW MARKET BREADTH",
        "Stocks trading above moving average:",
        *(f"{label}: {value:.0f}%" if value is not None else f"{label}: NA"
          for label, value in values.items()),
    ]
    fetched = sum(value is not None for value in values.values())
    if fetched < len(values):
        lines.extend(["", f"Breadth data fetched: {fetched}/{len(values)}"])
    if error:
        lines.extend(["", f"TradingView breadth fetch error: {error}"])
    lines.append(f"Source: {TRADINGVIEW_INDIA_URL}")
    return "\n".join(lines)

def as_dict(message):
    if isinstance(message, dict):
        return message
    try:
        return MessageToDict(message, preserving_proto_field_name=False)
    except Exception:
        return {}

def extract_quote(feed):
    full = feed.get("ff") or feed.get("fullFeed") or feed.get("full_feed") or {}
    market = (full.get("marketFullFeed") or full.get("market_full_feed") or
              full.get("indexFullFeed") or full.get("index_full_feed") or
              full.get("marketFF") or full.get("market_ff") or {})
    ltpc = market.get("ltpc") or {}
    details = market.get("eFeedDetails") or market.get("e_feed_details") or {}
    ohlc_block = market.get("marketOHLC") or market.get("market_ohlc") or {}
    candles = ohlc_block.get("ohlc") or []
    daily = next((c for c in candles if c.get("interval") == "1d"), {})
    ltp = ltpc.get("ltp")
    volume = market.get("vtt", details.get("vtt"))
    if volume is None:
        volume = daily.get("vol") or daily.get("volume")
    return {
        "last_price": float(ltp) if ltp is not None else 0.0,
        "volume": float(volume) if volume is not None else 0.0,
        "ohlc": {field: float(daily.get(field) or 0) for field in ("open", "high", "low")},
        "timestamp": str(ltpc.get("ltt") or feed.get("currentTs", "")),
    }

def fmt_alert(kind, r):
    market = r.get("market_regime", "Choppy Market")
    benchmark = r.get("market_benchmark", "NIFTY 500")
    ranges = ", ".join(f"{window}m" for window in r["orb_windows"])
    if kind == "EP":
        return (
            f"EPISODIC PIVOT BUY - {r['symbol']}\n"
            f"Price Rs {r['price']:.2f} | Gap {(r['day_open'] / r['prev_close'] - 1) * 100:+.2f}%\n"
            f"Prev Day Low SL Rs {r['prev_low']:.2f}\n"
            f"ATR {r['atr_pct']:.2f}% | Turnover Rs {r['turnover_cr']:.1f} Cr\n"
            f"{benchmark}: {market}"
        )
    volume_confirmations = []
    if r["rvol_condition"]:
        volume_confirmations.append(f"RVOL {r['rvol']:.2f}x")
    if r["pocket_pivot"]:
        volume_confirmations.append("Pocket Pivot")
    if r["first30_surge"]:
        volume_confirmations.append("First 30m surge")
    orb_and_volume = [f"ORB {r['orb_mode']}: {ranges}"] + volume_confirmations
    volume_line = f"\n{' | '.join(orb_and_volume)}" if orb_and_volume else ""
    return (
        f"MOMENTUM BUY - {r['symbol']}\n"
        f"Price Rs {r['price']:.2f} | Prev Day Low SL Rs {r['prev_low']:.2f}\n"
        f"Max Position {r['position_size_pct']:.2f}%\n"
        f"Gap {(r['day_open'] / r['prev_close'] - 1) * 100:+.2f}% | "
        f"Move {r['intraday_move']:+.2f}% from open | {r['intraday_move_from_low']:+.2f}% from low\n"
        f"ATR {r['atr_pct']:.2f}% | Turnover Rs {r['turnover_cr']:.1f} Cr\n"
        f"52W HI {-r['from_high']:+.2f}% | 52W LO {r['swing']:+.2f}%"
        f"{volume_line}\n"
        f"{benchmark}: {market}"
    )

def market_open_close():
    now = datetime.now(IST)
    start = datetime.combine(now.date(), dtime(9, 15), tzinfo=IST)
    end = datetime.combine(now.date(), dtime(15, 20), tzinfo=IST)
    return start, end

def run():
    alert_log = Path("live_alerts.csv")
    with alert_log.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([
            "alert_date", "alert_time_ist", "alert_type", "symbol",
            "alert_price", "sl_price", "market_context",
        ])
    universe = load_nse_equities()
    benchmark_name = "NIFTY 500"
    try:
        index_keys = find_market_index_keys(load_instruments())
    except Exception as exc:
        print(f"Upstox index instrument master unavailable: {exc}")
        index_keys = {}
    index_states = {}
    index_frames = {}
    for name, _ in MARKET_INDICES:
        key = index_keys.get(name)
        if not key:
            continue
        try:
            frame = historical_daily(key, years=2)
            completed = _completed(frame)
            if len(completed) < 20:
                print(f"{name} history unavailable: only {len(completed)} completed candles.")
                continue
            ema10_series = ema(completed.close, 10)
            ema20_series = ema(completed.close, 20)
            index_frames[name] = frame
            index_states[name] = {
                "prev_close": float(completed.close.iloc[-1]),
                "ema10": float(ema10_series.iloc[-1]),
                "ema20": float(ema20_series.iloc[-1]),
                "prev_ema10": float(ema10_series.iloc[-2]),
                "prev_ema20": float(ema20_series.iloc[-2]),
            }
        except Exception as exc:
            print(f"{name} history unavailable: {exc}")

    nifty_key = index_keys.get("NIFTY 500")
    nifty_df = index_frames.get("NIFTY 500", pd.DataFrame())
    benchmark = index_states.get("NIFTY 500")
    benchmark_ema10 = benchmark["ema10"] if benchmark else None
    benchmark_ema20 = benchmark["ema20"] if benchmark else None
    benchmark_prev_ema10 = benchmark["prev_ema10"] if benchmark else None
    benchmark_prev_ema20 = benchmark["prev_ema20"] if benchmark else None
    if benchmark:
        print(f"Using exact {benchmark_name} index as market benchmark.")
    else:
        print("Exact NIFTY 500 market benchmark unavailable; stock alerts continue without it.")

    print(f"Preparing live states for {len(universe)} NSE equities...")
    data = historical_many(universe, years=2, max_workers=settings.UPSTOX_MAX_WORKERS)

    states = {}
    for _, row in universe.iterrows():
        symbol = row.trading_symbol
        _, df, error = data.get(row.instrument_key, (symbol, pd.DataFrame(), "missing"))
        if error or df.empty:
            continue
        try:
            state = build_state(symbol, df, nifty_df)
            if state:
                states[row.instrument_key] = state
        except Exception as exc:
            print(f"[{symbol}] state build failed: {exc}")

    print(f"Live-ready symbols: {len(states)}")
    if not states:
        raise RuntimeError("No symbols have completed historical data for live monitoring.")

    print(f"NSE live scanner started; monitoring {len(states)} symbols.")

    configuration = upstox_client.Configuration()
    configuration.access_token = settings.UPSTOX_ACCESS_TOKEN
    client = upstox_client.ApiClient(configuration)
    keys = list(states.keys())
    keys.extend(key for key in index_keys.values() if key not in keys)
    if nifty_key and nifty_key not in keys:
        keys.append(nifty_key)
    streamer = upstox_client.MarketDataStreamerV3(client, keys, "full")
    streamer.auto_reconnect(True, 10, 20)

    sent_buy = set()
    sent_ep = set()
    latest_quotes = {}
    index_quotes = {}
    index_name_by_key = {key: name for name, key in index_keys.items()}
    quote_lock = Lock()
    pending = {}
    next_refresh = {}
    pool = ThreadPoolExecutor(max_workers=settings.UPSTOX_MAX_WORKERS)
    market_context = {"above_ema": None, "regime": "Choppy Market"}
    last_log = 0.0
    open_summary_sent = False

    def on_open():
        print(f"WebSocket connected; subscribed to {len(keys)} instruments.")

    def on_error(message):
        print(f"WebSocket error: {message}")

    def on_close(*args):
        print(f"WebSocket closed: {args}")

    def on_message(message):
        raw = as_dict(message)
        for instrument_key, feed in (raw.get("feeds") or {}).items():
            try:
                quote = extract_quote(feed)
                if instrument_key in index_name_by_key:
                    with quote_lock:
                        index_quotes[index_name_by_key[instrument_key]] = quote
                if instrument_key == nifty_key:
                    if (benchmark_ema10 is not None and benchmark_ema20 is not None and
                            benchmark_prev_ema10 is not None and benchmark_prev_ema20 is not None and
                            quote["last_price"] > 0):
                        live_ema10 = benchmark_ema10 + 2 / 11 * (quote["last_price"] - benchmark_ema10)
                        live_ema20 = benchmark_ema20 + 2 / 21 * (quote["last_price"] - benchmark_ema20)
                        ema10_up = live_ema10 > benchmark_prev_ema10
                        ema20_up = live_ema20 > benchmark_prev_ema20
                        if quote["last_price"] > live_ema10 and quote["last_price"] > live_ema20 and ema10_up and ema20_up:
                            market_context["regime"] = "Bull Market"
                        elif quote["last_price"] < live_ema10 and quote["last_price"] < live_ema20:
                            market_context["regime"] = "Bear Market"
                        else:
                            market_context["regime"] = "Choppy Market"
                        market_context["above_ema"] = quote["last_price"] > live_ema20
                    continue
                if instrument_key in states:
                    with quote_lock:
                        latest_quotes[instrument_key] = quote
            except Exception as exc:
                print(f"[{instrument_key}] tick processing failed: {exc}")

    def fetch_signal_data(instrument_key):
        return intraday_minutes(instrument_key)

    streamer.on("open", on_open)
    streamer.on("message", on_message)
    streamer.on("error", on_error)
    streamer.on("close", on_close)

    start, end = market_open_close()
    while datetime.now(IST) < start:
        time.sleep(5)

    try:
        streamer.connect()
        while datetime.now(IST) < end:
            for instrument_key, future in list(pending.items()):
                if not future.done():
                    continue
                del pending[instrument_key]
                state = states[instrument_key]
                try:
                    candles = future.result()
                    update_intraday(state, candles)
                except Exception as exc:
                    print(f"[{state['symbol']}] opening candle refresh failed; BUY blocked until ORB known: {exc}")
            with quote_lock:
                quotes = dict(latest_quotes)
                current_index_quotes = dict(index_quotes)
            if not open_summary_sent:
                fetched_index_quotes = {
                    name for name, quote in current_index_quotes.items()
                    if name in index_states and quote.get("last_price", 0) > 0
                }
                quote_timeout = datetime.now(IST) >= start + timedelta(seconds=60)
                if fetched_index_quotes or quote_timeout:
                    send(
                        market_summary(
                            "OPEN",
                            index_states,
                            current_index_quotes,
                            previous_conclusion=prior_performance_conclusion(),
                        )
                    )
                    breadth_values, breadth_error = scrape_tradingview_breadth()
                    send(fmt_breadth_alert(breadth_values, breadth_error))
                    open_summary_sent = True
            if not open_summary_sent:
                time.sleep(1)
                continue
            for instrument_key, quote in quotes.items():
                state = states[instrument_key]
                alert_key = (datetime.now(IST).date(), state["symbol"])
                if market_context["above_ema"] is not None:
                    state["market_above_ema"] = market_context["above_ema"]
                state["market_regime"] = market_context["regime"]
                state["market_benchmark"] = benchmark_name
                result = evaluate(state, quote)
                if not result:
                    continue
                if result["episodic_pivot"] and alert_key not in sent_ep:
                    sent_ep.add(alert_key)
                    with alert_log.open("a", newline="", encoding="utf-8") as f:
                        csv.writer(f).writerow([
                            datetime.now(IST).date().isoformat(),
                            datetime.now(IST).isoformat(),
                            "EP",
                            result["symbol"],
                            f'{result["price"]:.2f}',
                            f'{result["prev_low"]:.2f}',
                            result.get("market_regime", ""),
                        ])
                    send(fmt_alert("EP", result), chart_symbol=result["symbol"])
                if result["buy"] and alert_key not in sent_buy:
                    sent_buy.add(alert_key)
                    with alert_log.open("a", newline="", encoding="utf-8") as f:
                        csv.writer(f).writerow([
                            datetime.now(IST).date().isoformat(),
                            datetime.now(IST).isoformat(),
                            "BUY",
                            result["symbol"],
                            f'{result["price"]:.2f}',
                            f'{result["prev_low"]:.2f}',
                            result.get("market_regime", ""),
                        ])
                    send(fmt_alert("BUY", result), chart_symbol=result["symbol"])
                if result["buy"]:
                    continue
                eligible = all(passed for name, passed in result["checks"].items()
                               if name not in {"orb", "volume_surge"})
                needs_data = (
                    len(state["orb_highs"]) < 5 or
                    (result["orb_mode"] == "close" and not result["orb_windows"])
                )
                if (eligible and needs_data and instrument_key not in pending and
                        time.monotonic() >= next_refresh.get(instrument_key, 0)):
                    pending[instrument_key] = pool.submit(fetch_signal_data, instrument_key)
                    next_refresh[instrument_key] = time.monotonic() + 60
            now = time.time()
            if now - last_log >= 60:
                last_log = now
                print(f"Heartbeat {datetime.now(IST).strftime('%H:%M:%S')} | buy alerts={len(sent_buy)} | pending ORB={len(pending)}")
            time.sleep(1)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        try:
            if open_summary_sent:
                send(
                    market_summary(
                        "CLOSE",
                        index_states,
                        dict(index_quotes),
                        alert_counts={"buy": len(sent_buy), "ep": len(sent_ep)},
                    )
                )
            else:
                print("NSE live scanner stopped before a complete market summary was available.")
        finally:
            try:
                streamer.disconnect()
            except Exception:
                pass

if __name__ == "__main__":
    run()
