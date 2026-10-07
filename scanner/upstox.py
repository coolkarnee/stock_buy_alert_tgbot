import pandas as pd
import requests
import upstox_client
from config import settings
from datetime import datetime, timedelta
import concurrent.futures

BASE = "https://api.upstox.com/v2"

def get_client():
    configuration = upstox_client.Configuration()
    configuration.access_token = settings.UPSTOX_ACCESS_TOKEN
    return upstox_client.ApiClient(configuration)

def _request_json(endpoint, params=None):
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {settings.UPSTOX_ACCESS_TOKEN}"
    }
    url = f"{BASE}{endpoint}"
    try:
        response = requests.get(url, headers=headers, params=params, timeout=settings.UPSTOX_REQUEST_TIMEOUT)
        if response.status_code == 200:
            return response.json().get("data", {})
    except Exception as exc:
        print(f"Upstox API request failed for {endpoint}: {exc}")
    return {}

def load_instruments():
    api_instance = upstox_client.InstrumentCsvApi(get_client())
    try:
        api_response = api_instance.get_instruments("NSE")
        return [i.to_dict() for i in api_response] if isinstance(api_response, list) else []
    except Exception as exc:
        print(f"Error loading instruments: {exc}")
        return []

def load_nse_equities():
    print("Fetching active NSE Equities from Upstox...")
    data = load_instruments()
    rows = []
    
    for x in data:
        if x.get('segment') == 'NSE_EQ' and x.get('instrument_type') == 'EQ' and x.get('security_type', 'NORMAL') == 'NORMAL':
            if x.get('series') in ['EQ', 'BE']:
                rows.append({
                    'instrument_key': x.get('instrument_key'),
                    'trading_symbol': x.get('trading_symbol')
                })
            
    universe = pd.DataFrame(rows).drop_duplicates('instrument_key').reset_index(drop=True)
    print(f"Total filtered NSE Equities loaded: {len(universe)}")
    return universe

def historical_daily(instrument_key, years=1):
    api_instance = upstox_client.HistoryApi(get_client())
    to_date = datetime.now().strftime("%Y-%m-%d")
    from_date = (datetime.now() - timedelta(days=years * 365)).strftime("%Y-%m-%d")
    
    try:
        api_response = api_instance.get_historical_candle_data1(
            instrument_key=instrument_key,
            interval="day",
            to_date=to_date,
            from_date=from_date
        )
        data = api_response.to_dict() if hasattr(api_response, 'to_dict') else {}
        candles = data.get('data', {}).get('candles', [])
        if not candles:
            return pd.DataFrame()
            
        df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'oi'])
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.sort_values('timestamp').reset_index(drop=True)
        return df
    except Exception as exc:
        return pd.DataFrame()

def historical_many(universe, years=1, max_workers=8):
    results = {}
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