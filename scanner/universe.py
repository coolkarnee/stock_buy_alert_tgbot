import io
import re
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import requests
from config import settings

IST = ZoneInfo("Asia/Kolkata")

INDEX_URLS = {
    "NIFTY500": "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv",
    "MIDSMALLCAP400": "https://www.niftyindices.com/IndexConstituent/ind_niftymidsmallcap400list.csv",
    "MICROCAP250": "https://www.niftyindices.com/IndexConstituent/ind_niftymicrocap250_list.csv",
    "SMALLCAP250": "https://www.niftyindices.com/IndexConstituent/ind_niftysmallcap250list.csv",
}

def ipo_cutoff_date(today=None):
    today = pd.Timestamp(today or datetime.now(IST).date())
    return (today - pd.DateOffset(months=settings.IPO_LOOKBACK_MONTHS)).date()


def recent_ipo_listing_dates(listing_dates, today=None):
    today_date = pd.Timestamp(today or datetime.now(IST).date()).date()
    cutoff = ipo_cutoff_date(today_date)
    return {
        symbol: parsed.date().isoformat()
        for symbol, value in listing_dates.items()
        for parsed in [pd.to_datetime(value, errors="coerce")]
        if pd.notna(parsed) and cutoff <= parsed.date() <= today_date
    }

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

def _listed_ipo_dates():
    """
    Fetch listed IPOs from Upstox's supported IPO API.

    We page through listed IPOs and retain their listing dates. The caller
    applies the configured rolling window for universe membership and live
    IPO exemptions. This replaces the unreliable NSE public IPO URL.
    """
    from scanner.upstox import _request_json

    url = "https://api.upstox.com/v2/ipos"

    listing_dates = {}
    page = 1
    records = 30

    while True:
        payload = _request_json(
            url, timeout=20, retries=2,
            params={"status": "listed", "page_number": page, "records": records},
        )

        page_data = payload.get("data", [])
        if not page_data:
            break

        for item in page_data:
            symbol = str(item.get("symbol") or "").strip().upper()
            listing_date = str(item.get("listing_date") or "").strip()

            if not symbol or not listing_date:
                continue

            parsed = pd.to_datetime(listing_date, errors="coerce")
            if pd.notna(parsed):
                listing_dates[symbol] = parsed.date().isoformat()

        page_info = payload.get("meta_data", {}).get("page", {})
        total_pages = int(page_info.get("total_pages") or page)

        if page >= total_pages:
            break

        page += 1

    return listing_dates

def load_requested_universe(today=None):
    symbols = set()
    ipo_listing_dates = {}
    failures = []

    for name, url in INDEX_URLS.items():
        try:
            current = _index_symbols(url)
            symbols.update(current)
            print(f"Universe {name}: {len(current)} symbols")
        except Exception as exc:
            failures.append(f"{name}: {exc}")

    try:
        ipo_listing_dates = _listed_ipo_dates()
        recent_ipos = recent_ipo_listing_dates(ipo_listing_dates, today)
        cutoff = ipo_cutoff_date(today)
        symbols.update(recent_ipos)
        print(
            f"Universe IPOs listed since {cutoff}: {len(recent_ipos)} symbols"
        )
    except Exception as exc:
        failures.append(f"IPO: {exc}")

    if failures:
        print("Universe source warnings:")
        for failure in failures:
            print(f"  {failure}")

    if not symbols:
        raise RuntimeError("Requested universe could not be loaded from NSE/Nifty sources.")

    print(f"Final requested universe after de-duplication: {len(symbols)} symbols")
    return symbols, ipo_listing_dates
