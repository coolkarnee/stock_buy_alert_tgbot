import time
import csv
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time as dtime
from threading import Lock
from zoneinfo import ZoneInfo
import pandas as pd
import upstox_client
from google.protobuf.json_format import MessageToDict

from config import settings
from scanner.alerts import send
from scanner.live_engine import build_state, evaluate, update_intraday, _completed
from scanner.live_data import find_smallcap250_key, intraday_minutes
from scanner.indicators import ema
from scanner.upstox import load_nse_equities, historical_daily, historical_many

IST = ZoneInfo("Asia/Kolkata")

def as_dict(message):
    if isinstance(message, dict): return message
    try: return MessageToDict(message, preserving_proto_field_name=False)
    except Exception: return {}

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
    if volume is None: volume = daily.get("vol") or daily.get("volume")
    
    return {
        "last_price": float(ltp) if ltp is not None else 0.0,
        "volume": float(volume) if volume is not None else 0.0,
        "ohlc": {field: float(daily.get(field) or 0) for field in ("open", "high", "low")},
        "timestamp": str(ltpc.get("ltt") or feed.get("currentTs", "")),
    }

def market_open_close():
    now = datetime.now(IST)
    start = datetime.combine(now.date(), dtime(9, 15), tzinfo=IST)
    end = datetime.combine(now.date(), dtime(15, 20), tzinfo=IST)
    return start, end

def run():
    alert_log = Path("live_alerts.csv")
    if not alert_log.exists():
        with alert_log.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["alert_date", "alert_time_ist", "symbol", "alert_price", "alert_type"])

    universe = load_nse_equities()
    nifty_key = None
    market_context = {"idxC": 0.0, "idxE21": 0.0, "idxE10": 0.0, "prev_idxE10": 0.0}
    b_ema21 = b_ema10 = b_prev_ema10 = None
    
    try:
        nifty_key = find_smallcap250_key()
        nifty_df = historical_daily(nifty_key, years=2)
        completed_index = _completed(nifty_df)
        if len(completed_index) >= 21:
            idx_close = completed_index.close
            idxE21_s = ema(idx_close, 21)
            idxE10_s = ema(idx_close, 10)
            b_ema21 = float(idxE21_s.iloc[-1])
            b_ema10 = float(idxE10_s.iloc[-1])
            b_prev_ema10 = float(idxE10_s.iloc[-2]) if len(idxE10_s) > 1 else b_ema10
            market_context.update({"idxE21": b_ema21, "idxE10": b_ema10, "prev_idxE10": b_prev_ema10})
    except Exception as exc:
        print(f"Smallcap 250 context unavailable: {exc}")

    print(f"Preparing live states for {len(universe)} NSE equities...")
    data = historical_many(universe, years=2, max_workers=settings.UPSTOX_MAX_WORKERS)
    states = {}
    for _, row in universe.iterrows():
        symbol = row.trading_symbol
        _, df, error = data.get(row.instrument_key, (symbol, pd.DataFrame(), "missing"))
        if error or df.empty: continue
        try:
            state = build_state(symbol, df, nifty_df)
            if state: states[row.instrument_key] = state
        except Exception as exc:
            print(f"[{symbol}] state build failed: {exc}")

    print(f"Live-ready symbols: {len(states)}")
    if not states: raise RuntimeError("No symbols have completed historical data for live monitoring.")
    
    send(f"🚀 NSE LIVE SCANNER STARTED\nMonitoring {len(states)} symbols via Upstox WebSocket.")

    configuration = upstox_client.Configuration()
    configuration.access_token = settings.UPSTOX_ACCESS_TOKEN
    client = upstox_client.ApiClient(configuration)
    keys = list(states.keys())
    if nifty_key: keys.append(nifty_key)

    streamer = upstox_client.MarketDataStreamerV3(client, keys, "full")
    streamer.auto_reconnect(True, 10, 20)
    
    sent_alerts = set()
    latest_quotes = {}
    quote_lock = Lock()
    pending = {}
    next_refresh = {}
    pool = ThreadPoolExecutor(max_workers=settings.UPSTOX_MAX_WORKERS)
    last_log = 0.0

    def on_open():
        print(f"WebSocket connected; subscribed to {len(keys)} instruments.")
    def on_error(*args):
        print(f"WebSocket error: {args}")
    def on_close(*args):
        print(f"WebSocket closed: {args}")

    def on_message(message):
        raw = as_dict(message)
        for instrument_key, feed in (raw.get("feeds") or {}).items():
            try:
                quote = extract_quote(feed)
                if instrument_key == nifty_key:
                    if b_ema21 is not None and quote["last_price"] > 0:
                        market_context["idxC"] = quote["last_price"]
                        market_context["idxE10"] = b_ema10 + 2 / 11 * (quote["last_price"] - b_ema10)
                        market_context["idxE21"] = b_ema21 + 2 / 22 * (quote["last_price"] - b_ema21)
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
                if not future.done(): continue
                del pending[instrument_key]
                state = states[instrument_key]
                try:
                    candles = future.result()
                    update_intraday(state, candles)
                except Exception as exc:
                    print(f"[{state['symbol']}] opening candle refresh failed: {exc}")

            with quote_lock:
                quotes = dict(latest_quotes)

            for instrument_key, quote in quotes.items():
                state = states[instrument_key]
                result = evaluate(state, quote, market_context)
                if not result: continue

                # Evaluate returning an alert dictionary means we hit a milestone!
                if result.get("alert_type"):
                    alert_key = f"{datetime.now(IST).date()}_{state['symbol']}_{result['alert_type']}"
                    if alert_key in sent_alerts: continue
                    sent_alerts.add(alert_key)
                    
                    with alert_log.open("a", newline="", encoding="utf-8") as f:
                        csv.writer(f).writerow([
                            datetime.now(IST).date().isoformat(),
                            datetime.now(IST).isoformat(),
                            result["symbol"],
                            f'{result["price"]:.2f}',
                            result["alert_type"]
                        ])
                    send(result["alert_message"])
                    continue

                # Trigger ORB data fetching if condition looks ripe but misses the ORB block
                eligible = all(passed for name, passed in result["checks"].items() if name not in {"orb", "volume_surge"})
                needs_data = len(state["orb_highs"]) < 5
                if eligible and needs_data and instrument_key not in pending and time.monotonic() >= next_refresh.get(instrument_key, 0):
                    pending[instrument_key] = pool.submit(fetch_signal_data, instrument_key)
                    next_refresh[instrument_key] = time.monotonic() + 60

            now = time.time()
            if now - last_log >= 60:
                last_log = now
                print(f"Heartbeat {datetime.now(IST).strftime('%H:%M:%S')} | sent alerts={len(sent_alerts)} | pending ORB={len(pending)}")
            time.sleep(1)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        send("🛑 NSE LIVE SCANNER STOPPED for the session.")
        try: streamer.disconnect()
        except Exception: pass

if __name__ == "__main__":
    run()