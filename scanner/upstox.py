import pandas as pd
import requests
import time
from config import settings
from datetime import datetime, timedelta
import concurrent.futures

BASE = "https://api.upstox.com/v2"

def _request_json(endpoint, params=None):
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {settings.UPSTOX_ACCESS_TOKEN}"
    }
    url = f"{BASE}{endpoint}"
    try:
        response = requests.get(url, headers=headers, params=params, timeout=15)
        if response.status_code == 200:
            return response.json().get("data", {})
    except Exception as exc:
        print(f"Upstox API request failed for {endpoint}: {exc}")
    return {}

def load_instruments():
    try:
        url = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.csv.gz"
        df = pd.read_csv(url)
        df = df.rename(columns={
            'tradingsymbol': 'trading_symbol',
            'exchange': 'segment'
        })
        return df.to_dict('records')
    except Exception as exc:
        print(f"Error loading instrument CSV: {exc}")
        return []

def load_nse_equities():
    print("Fetching active NSE Equities directly from Upstox Master CSV...")
    data = load_instruments()
    rows = []
    
    for x in data:
        segment = str(x.get('segment', ''))
        inst_type = str(x.get('instrument_type', ''))
        # Standardize lot size string to check for standard retail equities
        lot_size = str(x.get('lot_size', '1')).split('.')[0]
        
        # Filter for standard equities (Lot size 1 drops SME stocks and bonds)
        if segment == 'NSE_EQ' and inst_type in ['EQUITY', 'EQ'] and lot_size == '1':
            rows.append({
                'instrument_key': x.get('instrument_key'),
                'trading_symbol': x.get('trading_symbol')
            })
            
    universe = pd.DataFrame(rows).drop_duplicates('instrument_key').reset_index(drop=True)
    print(f"Total filtered standard NSE Equities loaded: {len(universe)}")
    return universe

def historical_daily(instrument_key, years=1):
    to_date = datetime.now().strftime("%Y-%m-%d")
    from_date = (datetime.now() - timedelta(days=years * 365)).strftime("%Y-%m-%d")
    
    endpoint = f"/historical-candle/{instrument_key}/day/{to_date}/{from_date}"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {settings.UPSTOX_ACCESS_TOKEN}"
    }
    url = f"{BASE}{endpoint}"
    
    # Retry mechanism to handle Upstox 429 Rate Limits
    for attempt in range(5):
        try:
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code == 200:
                data = response.json().get("data", {}).get("candles", [])
                if not data: return pd.DataFrame()
                
                df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'oi'])
                df['timestamp'] = pd.to_datetime(df['timestamp'])
                df = df.sort_values('timestamp').reset_index(drop=True)
                return df
            elif response.status_code == 429: # Rate Limited!
                time.sleep(2) # Wait 2 seconds and try again
                continue
            else:
                break
        except Exception:
            time.sleep(1)
            continue
            
    return pd.DataFrame()

def historical_many(universe, years=1, max_workers=5):
    results = {}
    # Lowered max_workers to 5 to pace requests below Upstox limits
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_symbol = {
            executor.submit(historical_daily, row.instrument_key, years): row
            for _, row in universe.iterrows()
        }
        for future in concurrent.futures.as_completed(future_to_symbol):
            row = future_to_symbol[future]
            try:
                df = future.result()
                if df.empty:
                    results[row.instrument_key] = (row.trading_symbol, df, "empty")
                else:
                    results[row.instrument_key] = (row.trading_symbol, df, None)
            except Exception as exc:
                results[row.instrument_key] = (row.trading_symbol, pd.DataFrame(), str(exc))
    return results