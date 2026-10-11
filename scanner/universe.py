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
    """
    Fetch listed IPOs from Upstox's supported IPO API.

    We page through all listed IPOs, keep IPOs whose bidding started on/after
    IPO_START, and later let load_nse_equities() restrict them to NSE_EQ
    instruments. This replaces the unreliable NSE public IPO-tracker URL.
    """
    import os
    import time

    token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
    if not token:
        raise RuntimeError("UPSTOX_ACCESS_TOKEN is not configured.")

    url = "https://api.upstox.com/v2/ipos"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    symbols = set()
    page = 1
    records = 30

    while True:
        payload = None
        last_error = None

        for attempt in range(3):
            try:
                response = requests.get(
                    url,
                    params={
                        "status": "listed",
                        "page_number": page,
                        "records": records,
                    },
                    headers=headers,
                    timeout=20,
                )

                if response.status_code in {429, 500, 502, 503, 504}:
                    last_error = RuntimeError(
                        f"HTTP {response.status_code}"
                    )
                    if attempt < 2:
                        time.sleep(2 * (attempt + 1))
                        continue

                response.raise_for_status()
                payload = response.json()
                break

            except requests.RequestException as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))

        if payload is None:
            raise RuntimeError(
                f"Upstox IPO API failed on page {page}: {last_error}"
            )

        page_data = payload.get("data", [])
        if not page_data:
            break

        for item in page_data:
            symbol = str(item.get("symbol") or "").strip().upper()
            bidding_start = str(
                item.get("bidding_start_date") or ""
            ).strip()

            if not symbol or not bidding_start:
                continue

            parsed = pd.to_datetime(bidding_start, errors="coerce")
            if pd.notna(parsed) and parsed.date() >= IPO_START:
                symbols.add(symbol)

        page_info = payload.get("meta_data", {}).get("page", {})
        total_pages = int(page_info.get("total_pages") or page)

        if page >= total_pages:
            break

        page += 1

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
