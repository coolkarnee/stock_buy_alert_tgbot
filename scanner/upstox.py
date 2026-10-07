import pandas as pd
import requests
from config import settings
from datetime import datetime, timedelta
import concurrent.futures

BASE = "https://api.upstox.com/v2"

def _request_json(endpoint, params=None):
    """Direct HTTP request handler to avoid SDK version conflicts."""
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
    """
    Downloads the official Upstox Instrument Master CSV directly.
    """
    try:
        url = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.csv.gz"
        df = pd.read_csv(url)
        
        # Normalize the raw CSV column names to match what the rest of the application expects
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
        
        # The raw CSV uses 'EQUITY', but we also check 'EQ' just in case of future Upstox API changes
        if segment == 'NSE_EQ' and inst_type in ['EQUITY', 'EQ']:
            rows.append({
                'instrument_key': x.get('instrument_key'),
                'trading_symbol': x.get('trading_symbol')
            })
            
    universe = pd.DataFrame(rows).drop_duplicates('instrument_key').reset_index(drop=True)
    print(f"Total filtered NSE Equities loaded: {len(universe)}")
    return universe

def historical_daily(instrument_key, years=1):
    """Fetches historical candles via direct HTTP request."""
    to_date = datetime.now().strftime("%Y-%m-%d")
    from_date = (datetime.now() - timedelta(days=years * 365)).strftime("%Y-%m-%d")
    
    endpoint = f"/historical-candle/{instrument_key}/day/{to_date}/{from_date}"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {settings.UPSTOX_ACCESS_TOKEN}"
    }
    url = f"{BASE}{endpoint}"
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code == 200:
            data = response.json().get("data", {}).get("candles", [])
            if not data:
                return pd.DataFrame()
                
            df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'oi'])
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df = df.sort_values('timestamp').reset_index(drop=True)
            return df
    except Exception as exc:
        pass
    
    return pd.DataFrame()

def historical_many(universe, years=1, max_workers=8):
    results = {}
    # Increased max_workers to 12 to speed up cloud processing
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
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