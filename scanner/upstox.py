import gzip,json,time,requests,pandas as pd
from datetime import date,timedelta
from concurrent.futures import ThreadPoolExecutor,as_completed
from threading import Lock
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from config import settings

BASE='https://api.upstox.com'
RETRY_STATUS={429,500,502,503,504}

class RateLimiter:
    def __init__(self,per_minute,per_second=None,per_30_minutes=None):
        per_second = settings.UPSTOX_REQUESTS_PER_SECOND if per_second is None else per_second
        per_30_minutes = settings.UPSTOX_REQUESTS_PER_30_MINUTES if per_30_minutes is None else per_30_minutes
        if not (0 < per_second <= 10 and 0 < per_minute <= 500 and 0 < per_30_minutes <= 2000):
            raise ValueError("Upstox budgets must be positive and within 10/s, 500/min, 2000/30min.")
        self.interval=max(1.0/per_second,60.0/per_minute,1800.0/per_30_minutes)
        self.lock=Lock()
        self.next_time=0.0
        self.blocked_until=0.0

    def wait(self):
        while True:
            with self.lock:
                now=time.monotonic()
                delay=max(self.next_time,self.blocked_until)-now
                if delay<=0:
                    self.next_time=now+self.interval
                    return
            time.sleep(delay)

    def cooldown(self,seconds):
        with self.lock:
            self.blocked_until=max(self.blocked_until,time.monotonic()+seconds)

rate_limiter=RateLimiter(settings.UPSTOX_REQUESTS_PER_MINUTE)

def _retry_delay(value):
    if not value:
        return 2.0
    try:
        delay=float(value)
    except ValueError:
        try:
            retry_date=parsedate_to_datetime(value)
            delay=(retry_date-datetime.now(timezone.utc)).total_seconds()
        except (TypeError,ValueError,OverflowError):
            print(f"Invalid Upstox Retry-After {value!r}; using 2 seconds.")
            return 2.0
    if not 0 <= delay < float("inf"):
        print(f"Invalid Upstox Retry-After {value!r}; using 2 seconds.")
        return 2.0
    return delay

def headers():
    if not settings.UPSTOX_ACCESS_TOKEN:
        raise RuntimeError('UPSTOX_ACCESS_TOKEN is not configured.')
    return {'Authorization':f'Bearer {settings.UPSTOX_ACCESS_TOKEN}','Accept':'application/json'}

def _request_json(url,timeout=None,retries=None,params=None):
    timeout=timeout or settings.UPSTOX_REQUEST_TIMEOUT
    retries=settings.UPSTOX_MAX_RETRIES if retries is None else retries
    last=None
    for attempt in range(retries+1):
        rate_limiter.wait()
        try:
            r=requests.get(url,headers=headers(),timeout=timeout,params=params)
            if r.status_code in RETRY_STATUS:
                last=RuntimeError(f'HTTP {r.status_code}')
                delay=_retry_delay(r.headers.get('Retry-After'))
                rate_limiter.cooldown(delay)
                print(f"Upstox HTTP {r.status_code}; shared request cooldown {delay:.1f}s (attempt {attempt+1}).")
                if attempt>=retries:
                    raise last
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            last=exc
            if attempt>=retries:
                raise
            time.sleep(1.0)
    raise last or RuntimeError('request failed')

def load_instruments():
    url='https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz'
    r=requests.get(url,timeout=60)
    r.raise_for_status()
    return json.loads(gzip.decompress(r.content))

def load_nse_equities():
    # Universe is the union of the requested NSE broad-market indices plus
    # NSE IPO listings within the configured rolling listing-age window.
    from scanner.universe import load_requested_universe
    symbols, ipo_listing_dates = load_requested_universe()

    data = load_instruments()
    instrument_map = {
        x.get('trading_symbol'): x.get('instrument_key')
        for x in data
        if x.get('segment') == 'NSE_EQ'
        and x.get('instrument_type') == 'EQ'
        and x.get('security_type', 'NORMAL') == 'NORMAL'
    }

    rows = [
        {
            'instrument_key': instrument_map[s],
            'trading_symbol': s,
            'listing_date': ipo_listing_dates.get(s),
        }
        for s in sorted(symbols)
        if s in instrument_map
    ]
    return pd.DataFrame(rows).drop_duplicates('instrument_key').reset_index(drop=True)

def historical_daily(key,years=2):
    end=date.today()
    start=end-timedelta(days=365*years)
    url=f'{BASE}/v3/historical-candle/{key}/days/1/{end}/{start}'
    payload=_request_json(url)
    candles=payload.get('data',{}).get('candles',[])
    if not candles:
        return pd.DataFrame()
    df=pd.DataFrame(candles,columns=['timestamp','open','high','low','close','volume','oi'])
    df['timestamp']=pd.to_datetime(df['timestamp'])
    for col in ['open','high','low','close','volume']:
        df[col]=pd.to_numeric(df[col],errors='coerce')
    return df.sort_values('timestamp').reset_index(drop=True)

def current_day_daily(key):
    # Upstox V3 intraday endpoint returns the current trading day's
    # day-level OHLC candle. Use this for today's performance lookup.
    url=f'{BASE}/v3/historical-candle/intraday/{key}/days/1'
    payload=_request_json(url)
    candles=payload.get('data',{}).get('candles',[])
    if not candles:
        return pd.DataFrame()
    df=pd.DataFrame(candles,columns=['timestamp','open','high','low','close','volume','oi'])
    df['timestamp']=pd.to_datetime(df['timestamp'])
    for col in ['open','high','low','close','volume']:
        df[col]=pd.to_numeric(df[col],errors='coerce')
    return df.sort_values('timestamp').reset_index(drop=True)

def full_market_quotes(instrument_keys):
    if not instrument_keys:
        return {}
    out={}
    for i in range(0,len(instrument_keys),500):
        batch=instrument_keys[i:i+500]
        params=','.join(batch)
        url=f'{BASE}/v3/market-quote/quotes?instrument_key={params}'
        payload=_request_json(url,timeout=15,retries=1)
        for key,value in payload.get('data',{}).items():
            instrument_key=value.get('instrument_token') or key.replace(':','|',1)
            out[instrument_key]=value
    return out

def historical_many(rows,years=2,max_workers=None):
    results={}
    workers=max_workers or settings.UPSTOX_MAX_WORKERS
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(historical_daily,row.instrument_key,years): row for _,row in rows.iterrows()}
        for future in as_completed(futures):
            row=futures[future]
            try:
                results[row.instrument_key]=(row.trading_symbol,future.result(),None)
            except Exception as exc:
                results[row.instrument_key]=(row.trading_symbol,pd.DataFrame(),str(exc))
    return results
