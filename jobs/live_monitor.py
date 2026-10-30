import time
import csv
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
from scanner.breadth import send_breadth_alerts
from scanner.live_engine import build_state, evaluate, update_intraday, update_ep_radar_spot, _completed, EP_ORB_WINDOWS
from scanner.live_data import intraday_minutes
from scanner.indicators import ema
from scanner.market_context import classify_nifty500, market_context as format_market_context
from scanner.sector import get_sector, UNAVAILABLE_SECTOR
from scanner.signal_history import SignalHistory, SIGNAL_HISTORY_FILE
from scanner.qullamagie_daily import build_report as build_qullamagie_report
from scanner.upstox import (
    load_instruments,
    load_nse_equities,
    historical_daily,
    historical_many,
    full_market_quotes,
)

IST = ZoneInfo("Asia/Kolkata")

MARKET_INDICES = (
    ("Nifty", "Nifty 50"),
    ("NIFTY 500", "Nifty 500"),
    ("NIFTY MIDSML400", "Nifty Midsml 400"),
    ("NIFTY MIDCAP 150", "Nifty Midcap 150"),
    ("NIFTY SMLCAP 250", "Nifty Smlcap 250"),
    ("NIFTY MICROCAP 250", "Nifty Microcap250"),
)

MARKET_CONTEXT_DELAY = timedelta(minutes=20)
CLOSING_INDEX_REFRESH_BEFORE = timedelta(minutes=10)


def overall_market_conclusion(index_states, index_quotes):
    """Return the persisted overall market conclusion from the index summary."""
    regimes = []
    for name, _ in MARKET_INDICES:
        state = index_states.get(name)
        quote = index_quotes.get(name)
        if not state or not quote or quote.get("last_price", 0) <= 0:
            continue
        regimes.append(classify_index(state, quote)[0])
    if not regimes:
        return None
    counts = {regime: regimes.count(regime) for regime in ("Bull", "Bear", "Chop")}
    leading = max(counts, key=counts.get)
    return (leading if counts[leading] > len(regimes) / 2 else "Chop").lower()


def append_market_conclusions(market_details, overall_market=None, breadth_read=None,
                              highs_lows_read=None, ma_judgement=None,
                              nifty_summary_trend=None):
    """Add delayed session conclusions to the context stored with an alert."""
    lines = [line for line in str(market_details or "").splitlines() if line]
    if overall_market:
        lines.append(f"Overall market: {overall_market}")
    if breadth_read:
        lines.append(f"Read: {breadth_read}")
    if highs_lows_read:
        lines.append(str(highs_lows_read))
    if ma_judgement:
        lines.append(str(ma_judgement))
    if nifty_summary_trend:
        lines.append(f"NIFTY 500 summary trend: {nifty_summary_trend}")
    return "\n".join(lines)


def alert_market_context(market_details):
    """Return the concise market context shown in alerts.

    The full set of conclusions remains in the CSV context.
    """
    lines = []
    overall_market = None
    nifty_summary_trend = None
    for line in str(market_details or "").splitlines():
        if line.startswith("Overall market:"):
            overall_market = line.split(":", 1)[1].strip()
            continue
        if line.startswith("NIFTY 500 summary trend:"):
            nifty_summary_trend = line.split(":", 1)[1].strip()
            continue
        if line.startswith("Overall market:") or line.startswith("200-day MA:"):
            continue
        if line.startswith("Read: "):
            line = f"Today {line.split(':', 1)[1].strip()}"
        lines.append(line)
    if overall_market and overall_market.lower() != "unavailable":
        normalized = overall_market.strip().title()
        lines = [
            f"Market Context: {normalized}" if line.startswith("Market Context:") else line
            for line in lines
        ]
    if nifty_summary_trend:
        lines = [
            f"NIFTY 500: {nifty_summary_trend}" if line.startswith("NIFTY 500:") else line
            for line in lines
        ]
    return "\n".join(lines)


def update_alert_log_context(path, alert_date, market_details, overall_market=None,
                             breadth_read=None, highs_lows_read=None, ma_judgement=None,
                             nifty_summary_trend=None):
    """Backfill delayed context on alerts already written earlier today."""
    if not path.exists() or not (
        overall_market or breadth_read or highs_lows_read or ma_judgement or nifty_summary_trend
    ):
        return
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        fieldnames = list(rows[0]) if rows else []
        if "market_context" not in fieldnames:
            return
        updated = False
        for row in rows:
            if row.get("alert_date") != alert_date.isoformat():
                continue
            row["market_context"] = append_market_conclusions(
                market_details, overall_market, breadth_read,
                highs_lows_read, ma_judgement, nifty_summary_trend,
            )
            updated = True
        if updated:
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
    except (OSError, csv.Error) as exc:
        print(f"Alert market context backfill failed: {exc}")


def find_market_index_keys(instruments):
    """Return exact normalized Upstox NSE_INDEX matches."""
    def normalize(symbol):
        return re.sub(r"[^A-Z0-9]", "", str(symbol).upper())

    found = {}
    for display_name, trading_symbol in MARKET_INDICES:
        expected = normalize(trading_symbol)
        matches = [
            instrument for instrument in instruments
            if instrument.get("segment") == "NSE_INDEX"
            and instrument.get("instrument_type") == "INDEX"
            # Upstox lists Nifty 50 as trading_symbol "NIFTY" with name "Nifty 50".
            and expected in (
                normalize(instrument.get("trading_symbol", "")),
                normalize(instrument.get("name", "")),
            )
        ]
        if len(matches) == 1:
            found[display_name] = matches[0]["instrument_key"]
        else:
            available = sorted(
                str(instrument.get("trading_symbol", ""))
                for instrument in instruments
                if instrument.get("segment") == "NSE_INDEX"
                and instrument.get("instrument_type") == "INDEX"
            )
            nearby = [
                symbol for symbol in available
                if expected in normalize(symbol) or normalize(symbol) in expected
            ][:10]
            print(
                f"{display_name} exact Upstox instrument unavailable; "
                f"expected 1 match for {trading_symbol!r}, found {len(matches)}. "
                f"Nearby available symbols: {nearby or 'none'}."
            )
    return found


def classify_index(index_state, quote):
    """Classify an index for the existing multi-index market summary."""
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


def build_index_state(frame):
    completed = _completed(frame)
    if len(completed) < 20:
        return None
    ema10_series = ema(completed.close, 10)
    ema20_series = ema(completed.close, 20)
    return {
        "prev_close": float(completed.close.iloc[-1]),
        "ema10": float(ema10_series.iloc[-1]),
        "ema20": float(ema20_series.iloc[-1]),
        "prev_ema10": float(ema10_series.iloc[-2]),
        "prev_ema20": float(ema20_series.iloc[-2]),
    }


def quote_from_snapshot(snapshot):
    ohlc = snapshot.get("ohlc") or {}
    return {
        "last_price": float(snapshot.get("last_price") or 0),
        "volume": float(snapshot.get("volume") or ohlc.get("volume") or 0),
        "ohlc": {
            field: float(ohlc.get(field) or 0)
            for field in ("open", "high", "low")
        },
        "timestamp": str(snapshot.get("timestamp", "")),
    }


def refresh_index_quotes(index_keys, index_name_by_key, target_quotes):
    """Refresh available index quotes from REST and update the supplied mapping."""
    keys = list(dict.fromkeys(index_keys.values()))
    if not keys:
        return 0
    snapshots = full_market_quotes(keys)
    refreshed = 0
    for key, snapshot in snapshots.items():
        name = index_name_by_key.get(key)
        if name and snapshot.get("last_price") is not None:
            target_quotes[name] = quote_from_snapshot(snapshot)
            refreshed += 1
    return refreshed


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
        overall = overall_market_conclusion(index_states, index_quotes)
        lines.extend(["", f"Overall market: {overall}"])
    else:
        lines.extend(["", "Overall market: unavailable"])
    if previous_conclusion:
        lines.extend(["", "Yesterday's intraday alert performance conclusion:", previous_conclusion])
    if alert_counts is not None:
        lines.extend(["", f"Today's alerts: {alert_counts['buy']} Momentum BUY | {alert_counts['ep']} EP"])
        lines.append(f"EP radar ORB alerts: {alert_counts.get('ep_orb', 0)}")
    return "\n".join(lines)


def prior_performance_conclusion(path=Path("artifacts/performance_summary.txt")):
    try:
        text = path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return "No prior-day performance summary available."
    return text or "No prior-day performance summary available."


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
        "iep": float(ltpc["iep"]) if ltpc.get("iep") is not None else None,
        "volume": float(volume) if volume is not None else 0.0,
        "ohlc": {field: float(daily.get(field) or 0) for field in ("open", "high", "low")},
        "timestamp": str(ltpc.get("ltt") or feed.get("currentTs", "")),
    }

def _atr_turnover_line(r):
    adx_value = r.get("adx14")
    adx_text = f"{adx_value:.1f}" if adx_value is not None else "N/A"
    return (f"ATR {r['atr_days']}d {r['atr_pct']:.2f}% | "
            f"ADX14 {adx_text} | "
            f"Turnover {r['turnover_period_days']}d avg Rs {r['turnover_cr']:.1f} Cr")


def _strength_line(r):
    if not r.get("strength_ready", False):
        return (
            f"Strength: warm-up ({r['history_days']}/"
            f"{settings.MOMENTUM_STRENGTH_START_SESSIONS} completed sessions)"
        )
    matched = ", ".join(f"{label} {move:+.1f}% (>{threshold:g}%)"
                        for label, move, threshold in r["swing_moves_matched"])
    return f"Strength: {matched or 'none'}"


def _performance_line(r):
    # EP does not require swing strength, so show every available period; ✓ marks a passed threshold.
    matched = {label for label, _, _ in r["swing_moves_matched"]}
    moves = [
        f"{label} {move:+.1f}%{' ✓' if label in matched else ''}"
        for label, move in r["swing_moves"].items() if move is not None
    ]
    return f"Perf: {' | '.join(moves)}" if moves else f"Perf: NA ({r['history_days']} sessions)"


def fmt_alert(kind, r):
    market_details = r.get("market_context") or format_market_context(
        r.get("nifty_trend", "NA"),
        r.get("stocks_above_200ma"),
        r.get("breadth_date"),
    )[1]
    market_details = alert_market_context(market_details)
    ranges = ", ".join(f"{window}m" for window in r["orb_windows"])
    momentum_tag = "Momentum Stock\n" if r.get("momentum_stock") else ""
    if kind == "EP":
        return (
            f"EPISODIC PIVOT BUY - {r['symbol']}\n"
            f"Sector: {r.get('sector', UNAVAILABLE_SECTOR)}\n"
            f"{momentum_tag}"
            f"Price Rs {r['price']:.2f} | Gap {(r['day_open'] / r['prev_close'] - 1) * 100:+.2f}%\n"
            f"Prev Day Low SL Rs {r['prev_low']:.2f}\n"
            f"{_atr_turnover_line(r)}\n"
            f"{_performance_line(r)}\n"
            f"{market_details}"
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
        f"Sector: {r.get('sector', UNAVAILABLE_SECTOR)}\n"
        f"{momentum_tag}"
        f"Price Rs {r['price']:.2f} | Prev Day Low SL Rs {r['prev_low']:.2f}\n"
        f"Max Position {r['position_size_pct']:.2f}%\n"
        f"Gap {(r['day_open'] / r['prev_close'] - 1) * 100:+.2f}% | "
        f"Move {r['intraday_move']:+.2f}% from open | {r['intraday_move_from_low']:+.2f}% from low\n"
        f"{_atr_turnover_line(r)}\n"
        f"52W HI {-r['from_high']:+.2f}% | 52W LO {r['swing']:+.2f}%\n"
        f"{_strength_line(r)}"
        f"{volume_line}\n"
        f"{market_details}"
    )


def fmt_qullamagie_alert(r):
    """Format a criteria-qualified Qullamagie entry with context and chart."""
    market_details = r.get("market_context") or format_market_context(
        r.get("nifty_trend", "NA"),
        r.get("stocks_above_200ma"),
        r.get("breadth_date"),
    )[1]
    market_details = alert_market_context(market_details)
    performance = []
    for label in ("1W", "1M", "3M", "6M", "1Y"):
        move = r.get("swing_moves", {}).get(label)
        performance.append(f"{ '12M' if label == '1Y' else label} {move:+.1f}%" if move is not None
                           else f"{ '12M' if label == '1Y' else label} N/A")
    adx_value = r.get("adx14")
    adx_text = f"{adx_value:.1f}" if adx_value is not None else "N/A"
    return (
        f"Qullamagie Buy Alert - {r['symbol']}\n"
        f"Setup: {r['qullamagie_setup']} | ORB: {', '.join(str(x) + 'm' for x in r['orb_windows'])}\n"
        f"Entry Rs {r['price']:.2f} | Entry-day low SL Rs {r['day_low']:.2f}\n"
        f"SL risk {r['qullamagie_sl_pct']:.2f}% | ADR cap {r['adr_pct']:.2f}% | "
        f"Turnover Rs {r['turnover_cr']:.1f} Cr avg\n"
        f"ATR14 {r['atr_pct']:.2f}% | ADX14 {adx_text}\n"
        f"Performance: {' | '.join(performance)}\n"
        f"Sector: {r.get('sector', UNAVAILABLE_SECTOR)}\n"
        f"{market_details}"
    )


def market_open_close():
    now = datetime.now(IST)
    start = datetime.combine(now.date(), dtime(9, 15), tzinfo=IST)
    end = datetime.combine(now.date(), dtime(15, 20), tzinfo=IST)
    return start, end


def fmt_ep_orb_alert(state, window, breakout):
    gap = (state["ep_radar_open"] / state["prev_close"] - 1) * 100
    spot = breakout["orb_mode"] == "spot"
    price_label = "Spot Price" if spot else "Breakout Close"
    time_line = f"Spot quote time: {breakout['quote_time']}\n" if spot else ""
    adx_value = state.get("adx14")
    adx_text = f"{adx_value:.1f}" if adx_value is not None else "N/A"
    market_details = alert_market_context(
        state.get("market_context") or format_market_context(
            state.get("nifty_trend", "NA"),
            state.get("stocks_above_200ma"),
            state.get("breadth_date"),
        )[1]
    )
    return (
        f"EP RADAR ORB BUY - {state['symbol']} | {window}m\n"
        f"Sector: {state.get('sector', UNAVAILABLE_SECTOR)}\n"
        f"{price_label} Rs {breakout['price']:.2f} | Opening Gap {gap:+.2f}%\n"
        f"Opening {window}m High Rs {breakout['range_high']:.2f}\n"
        f"ORB confirmation: {breakout['orb_mode']}\n"
        f"ADX14 {adx_text}\n"
        f"{time_line}"
        f"Prev Day Low SL Rs {state['prev_low']:.2f}\n"
        f"{market_details}"
    )


def run():
    signal_history = SignalHistory(SIGNAL_HISTORY_FILE)
    alert_log = Path("artifacts/live_alerts.csv")
    with alert_log.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([
            "alert_date", "alert_time_ist", "alert_type", "symbol",
            "alert_price", "sl_price", "market_context", "sector",
        ])
    universe = load_nse_equities()
    benchmark_name = "NIFTY 500"
    try:
        index_keys = find_market_index_keys(load_instruments())
        print(f"Resolved Upstox market indexes: {', '.join(sorted(index_keys)) or 'none'}")
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
            state = build_index_state(frame)
            if state is None:
                print(f"{name} history unavailable: fewer than 20 completed candles.")
                continue
            index_frames[name] = frame
            index_states[name] = state
        except Exception as exc:
            print(f"{name} history unavailable: {exc}")

    nifty_key = index_keys.get("NIFTY 500")
    nifty_df = index_frames.get("NIFTY 500", pd.DataFrame())
    completed_nifty = _completed(nifty_df)
    if not completed_nifty.empty:
        signal_history.record_sessions(completed_nifty["timestamp"])
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
    qullamagie_histories = {}
    for _, row in universe.iterrows():
        symbol = row.trading_symbol
        _, df, error = data.get(row.instrument_key, (symbol, pd.DataFrame(), "missing"))
        if error or df.empty:
            continue
        qullamagie_histories[symbol] = df
        try:
            state = build_state(symbol, df, nifty_df)
            if state:
                states[row.instrument_key] = state
        except Exception as exc:
            print(f"[{symbol}] state build failed: {exc}")

    print(f"Live-ready symbols: {len(states)}")
    if not states:
        raise RuntimeError("No symbols have completed historical data for live monitoring.")

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
    sent_qullamagie = set()
    sent_ep = set()
    sent_ep_orb = set()
    latest_quotes = {}
    preopen_iep = {}
    preopen_ep_candidates = set()
    post_open_quotes = set()
    index_quotes = {}
    index_name_by_key = {key: name for name, key in index_keys.items()}
    quote_lock = Lock()
    pending = {}
    next_refresh = {}
    pool = ThreadPoolExecutor(max_workers=settings.UPSTOX_MAX_WORKERS)
    market_context = {
        "above_ema": None,
        "regime": "NA",
        "stocks_above_200ma": None,
        "breadth_date": None,
        "overall_market": None,
        "nifty_summary_trend": None,
        "breadth_read": None,
        "highs_lows_read": None,
        "ma_judgement": None,
    }
    last_log = 0.0
    open_summary_sent = False
    start_notification_sent = False
    closing_index_refresh_done = False
    start, end = market_open_close()
    preopen_start = start.replace(hour=9, minute=0)

    def on_open():
        nonlocal start_notification_sent
        print(f"WebSocket connected; subscribed to {len(keys)} instruments.")
        if not start_notification_sent:
            start_notification_sent = True
            send(
                f"\U0001f7e2 NSE LIVE SCANNER STARTED. Monitoring {len(states)} symbols via Upstox WebSocket.\n"
                "No orders will be placed."
            )

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
                    market_context["regime"], live_emas = classify_nifty500(
                        benchmark, quote["last_price"],
                    )
                    market_context["above_ema"] = (
                        quote["last_price"] > live_emas[1] if live_emas else None
                    )
                    continue
                if instrument_key in states:
                    with quote_lock:
                        latest_quotes[instrument_key] = quote
                        if quote["iep"] is not None and datetime.now(IST) < start:
                            preopen_iep[instrument_key] = quote["iep"]
                        if datetime.now(IST) >= start:
                            post_open_quotes.add(instrument_key)
            except Exception as exc:
                print(f"[{instrument_key}] tick processing failed: {exc}")

    def fetch_signal_data(instrument_key):
        return intraday_minutes(instrument_key)

    streamer.on("open", on_open)
    streamer.on("message", on_message)
    streamer.on("error", on_error)
    streamer.on("close", on_close)

    try:
        while datetime.now(IST) < preopen_start:
            time.sleep(5)
        streamer.connect()
        preopen_scan_done = False
        while datetime.now(IST) < end:
            if (not closing_index_refresh_done
                    and datetime.now(IST) >= end - CLOSING_INDEX_REFRESH_BEFORE):
                closing_index_refresh_done = True
                try:
                    refreshed_index_quotes = {}
                    refreshed_count = refresh_index_quotes(
                        index_keys, index_name_by_key, refreshed_index_quotes,
                    )
                    with quote_lock:
                        index_quotes.update(refreshed_index_quotes)
                    print(
                        f"Pre-close REST index quote refresh: "
                        f"{refreshed_count}/{len(index_keys)} indexes returned quotes."
                    )
                except Exception as exc:
                    print(f"Pre-close index quote refresh failed: {exc}")
            if not preopen_scan_done and datetime.now(IST) >= start:
                with quote_lock:
                    opening_indications = dict(preopen_iep)
                preopen_ep_candidates = {
                    instrument_key
                    for instrument_key, iep in opening_indications.items()
                    if iep >= states[instrument_key]["prev_close"] * (
                        1 + settings.EP_MIN_GAP_PCT / 100
                    )
                }
                candidate_symbols = sorted(
                    states[instrument_key]["symbol"] for instrument_key in preopen_ep_candidates
                )
                print(
                    f"Pre-open EP gap candidates ({len(preopen_ep_candidates)}): "
                    f"{', '.join(candidate_symbols) or 'none'}; waiting for regular-session confirmation."
                )
                preopen_scan_done = True
            for instrument_key, future in list(pending.items()):
                if not future.done():
                    continue
                del pending[instrument_key]
                state = states[instrument_key]
                try:
                    candles = future.result()
                    update_intraday(state, candles)
                except Exception as exc:
                    print(f"[{state['symbol']}] intraday candle refresh failed; EP needs completed candles, BUY needs ORB: {exc}")
            with quote_lock:
                quotes = dict(latest_quotes)
                current_index_quotes = dict(index_quotes)
                opened_quotes = set(post_open_quotes)
            if not open_summary_sent and datetime.now(IST) >= start + MARKET_CONTEXT_DELAY:
                # Retry index history after the open and use a REST snapshot
                # if the WebSocket has not delivered index ticks.
                for name, _ in MARKET_INDICES:
                    if name in index_states:
                        continue
                    key = index_keys.get(name)
                    if not key:
                        continue
                    try:
                        frame = historical_daily(key, years=2)
                        state = build_index_state(frame)
                        if state is not None:
                            index_frames[name] = frame
                            index_states[name] = state
                    except Exception as exc:
                        print(f"{name} delayed history retry failed: {exc}")
                try:
                    snapshots = full_market_quotes(list(index_keys.values()))
                    for key, snapshot in snapshots.items():
                        name = index_name_by_key.get(key)
                        if name and snapshot.get("last_price") is not None:
                            current_index_quotes[name] = quote_from_snapshot(snapshot)
                except Exception as exc:
                    print(f"Delayed index quote snapshot failed: {exc}")
                delayed_nifty = current_index_quotes.get(benchmark_name)
                if benchmark and delayed_nifty and delayed_nifty.get("last_price", 0) > 0:
                    market_context["regime"], live_emas = classify_nifty500(
                        benchmark, delayed_nifty["last_price"],
                    )
                    market_context["above_ema"] = (
                        delayed_nifty["last_price"] > live_emas[1] if live_emas else None
                    )
                market_context["overall_market"] = overall_market_conclusion(
                    index_states, current_index_quotes,
                )
                nifty_state = index_states.get(benchmark_name)
                nifty_quote = current_index_quotes.get(benchmark_name)
                market_context["nifty_summary_trend"] = (
                    classify_index(nifty_state, nifty_quote)[0]
                    if nifty_state and nifty_quote and nifty_quote.get("last_price", 0) > 0
                    else None
                )
                for name, _ in MARKET_INDICES:
                    quote = current_index_quotes.get(name)
                    if name not in index_states:
                        print(f"{name} index state unavailable; trend cannot be classified.")
                    if not quote or quote.get("last_price", 0) <= 0:
                        print(f"{name} live quote unavailable after WebSocket and REST fallback.")
                send(
                    market_summary(
                        "OPEN",
                        index_states,
                        current_index_quotes,
                        previous_conclusion=prior_performance_conclusion(),
                    )
                )
                try:
                    breadth = send_breadth_alerts(datetime.now(IST).date()) or {}
                    market_context["stocks_above_200ma"] = breadth.get("200-day MA")
                    breadth_read = breadth.get("advance_decline_read")
                    highs_lows_read = breadth.get("highs_lows_read")
                    ma_judgement = breadth.get("ma_judgement")
                    market_context["breadth_date"] = (
                        datetime.now(IST).date().isoformat()
                        if market_context["stocks_above_200ma"] is not None else None
                    )
                except Exception as exc:
                    print(f"Market breadth alerts failed: {exc}")
                    breadth_read = None
                    highs_lows_read = None
                    ma_judgement = None
                send(build_qullamagie_report(
                    qullamagie_histories,
                    sector_lookup=get_sector,
                ))
                delayed_category, delayed_details = format_market_context(
                    market_context["regime"],
                    market_context["stocks_above_200ma"],
                    market_context["breadth_date"],
                )
                update_alert_log_context(
                    alert_log,
                    datetime.now(IST).date(),
                    delayed_details,
                    overall_market_conclusion(index_states, current_index_quotes),
                    breadth_read,
                    highs_lows_read,
                    ma_judgement,
                    market_context.get("nifty_summary_trend"),
                )
                market_context["breadth_read"] = breadth_read
                market_context["highs_lows_read"] = highs_lows_read
                market_context["ma_judgement"] = ma_judgement
                open_summary_sent = True
            evaluated = []
            ep_evaluated = []
            for instrument_key, quote in quotes.items():
                if instrument_key not in opened_quotes:
                    continue
                state = states[instrument_key]
                update_ep_radar_spot(state, quote)
                alert_key = (datetime.now(IST).date(), state["symbol"])
                if (datetime.now(IST) >= start
                    and instrument_key in preopen_ep_candidates
                    and instrument_key in opened_quotes
                        and alert_key not in sent_ep and quote["last_price"] > 0
                        and (quote.get("ohlc") or {}).get("open", 0) > 0):
                    state["ep_quote"] = {
                        **quote,
                        "timestamp": datetime.now(IST).isoformat(),
                    }
                if market_context["above_ema"] is not None:
                    state["market_above_ema"] = market_context["above_ema"]
                state["market_regime"] = market_context["regime"]
                state["market_benchmark"] = benchmark_name
                state["nifty_trend"] = market_context["regime"]
                state["stocks_above_200ma"] = market_context["stocks_above_200ma"]
                state["breadth_date"] = market_context["breadth_date"]
                state["market_context_category"], state["market_context"] = format_market_context(
                    state["nifty_trend"], state["stocks_above_200ma"], state["breadth_date"],
                )
                state["market_context"] = append_market_conclusions(
                    state["market_context"],
                    market_context.get("overall_market"),
                    market_context.get("breadth_read"),
                    market_context.get("highs_lows_read"),
                    market_context.get("ma_judgement"),
                    market_context.get("nifty_summary_trend"),
                )
                ep_quote = state["ep_quote"]
                ep_result = None
                if ep_quote is not None and alert_key not in sent_ep:
                    ep_result = evaluate(state, ep_quote)
                    if ep_result:
                        ep_evaluated.append((instrument_key, alert_key, ep_result))
                        if (not ep_result["episodic_pivot"]
                                and not state.get("ep_rejection_logged")):
                            reasons = ", ".join(ep_result["ep_rejection_reasons"]) or "unknown"
                            print(f"[{state['symbol']}] EP rejected: {reasons}")
                            state["ep_rejection_logged"] = True
                result = evaluate(state, quote)
                if result:
                    evaluated.append((instrument_key, alert_key, result))

                gap_reference = state["prev_close"] * (1 + settings.EP_MIN_GAP_PCT / 100)
                day_open = float((quote.get("ohlc") or {}).get("open") or 0)
                ep_gap_candidate = (
                    ep_quote["ohlc"]["open"] >= gap_reference
                    if ep_quote is not None else
                    day_open <= 0 or max(day_open, quote.get("last_price") or 0) >= gap_reference
                )
                needs_ep_data = (
                    alert_key not in sent_ep and ep_gap_candidate
                    and datetime.now(IST) >= start + timedelta(minutes=1)
                    and instrument_key not in preopen_ep_candidates
                    and not (ep_result and ep_result["episodic_pivot"])
                )
                needs_orb_data = (
                    result is not None and not result["buy"]
                    and all(passed for name, passed in result["checks"].items()
                            if name not in {"orb", "volume_surge"})
                    and (len(state["orb_highs"]) < 5
                         or (result["orb_mode"] == "close" and not result["orb_windows"]))
                )
                needs_ep_orb_data = (
                    (state["ep_radar_open"] is not None or ep_gap_candidate)
                    and any((*alert_key, window) not in sent_ep_orb for window in EP_ORB_WINDOWS)
                    and datetime.now(IST) >= start + timedelta(minutes=1)
                )
                if ((needs_ep_data or needs_orb_data or needs_ep_orb_data) and instrument_key not in pending
                        and time.monotonic() >= next_refresh.get(instrument_key, 0)):
                    pending[instrument_key] = pool.submit(fetch_signal_data, instrument_key)
                    next_refresh[instrument_key] = time.monotonic() + 60

            # Keep the delivery order explicit: market context/breadth has
            # already been sent above, then EP, then Momentum BUY. This does
            # not alter either signal's checks or once-per-day suppression.
            for instrument_key, alert_key, result in ep_evaluated:
                if result["episodic_pivot"] and alert_key not in sent_ep:
                    result["sector"] = get_sector(result["symbol"])
                    sent_ep.add(alert_key)
                    alert_time = datetime.now(IST)
                    with alert_log.open("a", newline="", encoding="utf-8") as f:
                        csv.writer(f).writerow([
                            alert_time.date().isoformat(),
                            alert_time.isoformat(),
                            "EP",
                            result["symbol"],
                            f'{result["price"]:.2f}',
                            f'{result["prev_low"]:.2f}',
                            result.get("market_context", ""),
                            result["sector"],
                        ])
                    frequency = signal_history.record(result["symbol"], "EP", alert_time)
                    send(fmt_alert("EP", result) + frequency, chart_symbol=result["symbol"])

            for instrument_key, alert_key, result in evaluated:
                if result.get("qullamagie_buy") and alert_key not in sent_qullamagie:
                    result["sector"] = get_sector(result["symbol"])
                    alert_time = datetime.now(IST)
                    send(fmt_qullamagie_alert(result), chart_symbol=result["symbol"])
                    sent_qullamagie.add(alert_key)
                if result["buy"] and alert_key not in sent_buy:
                    result["sector"] = get_sector(result["symbol"])
                    sent_buy.add(alert_key)
                    alert_time = datetime.now(IST)
                    with alert_log.open("a", newline="", encoding="utf-8") as f:
                        csv.writer(f).writerow([
                            alert_time.date().isoformat(),
                            alert_time.isoformat(),
                            "BUY",
                            result["symbol"],
                            f'{result["price"]:.2f}',
                            f'{result["prev_low"]:.2f}',
                            result.get("market_context", ""),
                            result["sector"],
                        ])
                    frequency = signal_history.record(result["symbol"], "BUY", alert_time)
                    send(fmt_alert("BUY", result) + frequency, chart_symbol=result["symbol"])

            for instrument_key in quotes:
                state = states[instrument_key]
                for window, breakout in state["ep_orb_breakouts"].items():
                    alert_key = (datetime.now(IST).date(), state["symbol"], window)
                    if alert_key in sent_ep_orb:
                        continue
                    state["sector"] = get_sector(state["symbol"])
                    alert_time = datetime.now(IST)
                    with alert_log.open("a", newline="", encoding="utf-8") as f:
                        csv.writer(f).writerow([
                            alert_time.date().isoformat(),
                            alert_time.isoformat(),
                            f"EP_ORB_{window}M",
                            state["symbol"],
                            f'{breakout["price"]:.2f}',
                            f'{state["prev_low"]:.2f}',
                            state.get("market_context", ""),
                            state["sector"],
                        ])
                    frequency = signal_history.record(state["symbol"], f"EP_ORB_{window}M", alert_time)
                    send(fmt_ep_orb_alert(state, window, breakout) + frequency, chart_symbol=state["symbol"])
                    sent_ep_orb.add(alert_key)

            now = time.time()
            if now - last_log >= 60:
                last_log = now
                print(f"Heartbeat {datetime.now(IST).strftime('%H:%M:%S')} | buy alerts={len(sent_buy)} | EP alerts={len(sent_ep)} | pending candles={len(pending)}")
            time.sleep(1)
    except KeyboardInterrupt:
        print("Scanner stop requested; closing the session gracefully.")
    finally:
        try:
            try:
                streamer.disconnect()
            except Exception as exc:
                print(f"WebSocket disconnect failed: {exc}")
            pool.shutdown(wait=True, cancel_futures=True)
            if open_summary_sent:
                closing_index_states = dict(index_states)
                closing_index_quotes = dict(index_quotes)
                for name, _ in MARKET_INDICES:
                    if name in closing_index_states:
                        continue
                    key = index_keys.get(name)
                    if not key:
                        continue
                    try:
                        frame = historical_daily(key, years=2)
                        state = build_index_state(frame)
                        if state is not None:
                            closing_index_states[name] = state
                    except Exception as exc:
                        print(f"Closing {name} history retry failed: {exc}")
                missing_index_keys = [
                    key for name, key in index_keys.items()
                    if name not in closing_index_quotes
                    or closing_index_quotes[name].get("last_price", 0) <= 0
                ]
                if missing_index_keys:
                    try:
                        missing_keys_by_name = {
                            name: key for name, key in index_keys.items()
                            if key in missing_index_keys
                        }
                        refreshed_count = refresh_index_quotes(
                            missing_keys_by_name, index_name_by_key, closing_index_quotes,
                        )
                        if refreshed_count == 0:
                            print("Final index quote fallback returned no usable quotes.")
                    except Exception as exc:
                        print(f"Closing index quote snapshot failed: {exc}")
                send(
                    market_summary(
                        "CLOSE",
                        closing_index_states,
                        closing_index_quotes,
                        alert_counts={"buy": len(sent_buy), "ep": len(sent_ep), "ep_orb": len(sent_ep_orb)},
                    )
                )
            else:
                print("NSE live scanner stopped before a complete market summary was available.")
        finally:
            send("\U0001f534 NSE LIVE SCANNER STOPPED for the session.")

if __name__ == "__main__":
    run()
