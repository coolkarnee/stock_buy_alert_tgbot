import io
import re
from datetime import date
import pandas as pd
import requests

INDEX_URLS = {
    "NIFTY500": "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv",
    "MIDSMALLCAP400": "https://www.niftyindices.com/IndexConstituent/ind_niftymidsmallcap400list.csv",
    "MICROCAP250": "https://www.niftyindices.com/IndexConstituent/ind_niftymicrocap250_list.csv",
    "SMALLCAP250": "https://www.niftyindices.com/IndexConstituent/ind_niftysmallcap250list.csv",
}
IPO_START = date(2026, 1, 1)

def _headers():
    return {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Accept": "text/csv,application/json,text/plain,*/*",
        "Referer": "https://www.niftyindices.com/",
    }

def _index_symbols(url):
    r = requests.get(url, headers=_headers(), timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.BytesIO(r.content))
    col = next((c for c in df.columns if str(c).strip().lower() == "symbol"), None)
    if col is None:
        raise RuntimeError(f"Symbol column not found in index file: {url}")
    return {
        str(s).strip().upper()
        for s in df[col].dropna()
        if str(s).strip()
    }

def _ipo_symbols():
    # NSE's public IPO tracker endpoint. We use listed-on date and keep
    # every NSE equity IPO from 2026-01-01 onward.
    url = "https://www.nseindia.com/api/ipo-tracker?type=gain_issue_price"
    session = requests.Session()
    session.headers.update({
        "User-Agent": _headers()["User-Agent"],
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://www.nseindia.com/ipo-tracker?type=gain_issue_price",
        "Accept-Language": "en-US,en;q=0.9",
    })
    session.get("https://www.nseindia.com/", timeout=20)
    r = session.get(url, timeout=30)
    r.raise_for_status()
    payload = r.json()
    records = payload.get("data", payload if isinstance(payload, list) else [])
    symbols = set()

    for item in records:
        symbol = str(item.get("symbol") or item.get("SYMBOL") or "").strip().upper()
        listed = str(
            item.get("listedOn") or item.get("listed_on") or
            item.get("LISTED ON") or item.get("listingDate") or ""
        ).strip()
        if not symbol or not listed:
            continue
        parsed = pd.to_datetime(listed, errors="coerce", dayfirst=True)
        if pd.notna(parsed) and parsed.date() >= IPO_START:
            symbols.add(symbol)

    return symbols

def load_requested_universe():
    symbols = set()
    failures = []

    for name, url in INDEX_URLS.items():
        try:
            current = _index_symbols(url)
            symbols.update(current)
            print(f"Universe {name}: {len(current)} symbols")
        except Exception as exc:
            failures.append(f"{name}: {exc}")

    try:
        ipo = _ipo_symbols()
        symbols.update(ipo)
        print(f"Universe recent IPOs since 2026-01-01: {len(ipo)} symbols")
    except Exception as exc:
        failures.append(f"IPO: {exc}")

    if failures:
        print("Universe source warnings:")
        for failure in failures:
            print(f"  {failure}")

    if not symbols:
        raise RuntimeError("Requested universe could not be loaded from NSE/Nifty sources.")

    print(f"Final requested universe after de-duplication: {len(symbols)} symbols")
    return symbols
