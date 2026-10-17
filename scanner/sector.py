"""Sector metadata shared by alerts and performance reports."""

from functools import lru_cache

from requests import RequestException

from scanner.upstox import BASE, _request_json, load_instruments

UNAVAILABLE_SECTOR = "NA"


def known_sector(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or value.lower() in {"nan", "none", "na", "n/a", "unavailable"}:
        return None
    return value


@lru_cache(maxsize=1)
def _symbol_isins():
    """Resolve NSE equity symbols to ISINs from the Upstox instrument master."""
    try:
        instruments = load_instruments()
        return {
            str(item.get("trading_symbol") or "").strip().upper(): str(item["isin"]).strip().upper()
            for item in instruments
            if item.get("segment") == "NSE_EQ"
            and item.get("instrument_type") == "EQ"
            and item.get("security_type", "NORMAL") == "NORMAL"
            and item.get("trading_symbol")
            and known_sector(item.get("isin"))
        }
    except (RequestException, RuntimeError, ValueError, TypeError, AttributeError) as exc:
        print(f"Upstox instrument ISIN lookup failed; sectors will be NA: {exc}")
        return {}


@lru_cache(maxsize=2048)
def get_sector(symbol):
    """Fetch the company profile's sector using the stock's ISIN."""
    isin = _symbol_isins().get(str(symbol).strip().upper())
    if not isin:
        print(f"[{symbol}] no Upstox NSE equity ISIN available; sector is NA.")
        return UNAVAILABLE_SECTOR
    try:
        payload = _request_json(f"{BASE}/v2/fundamentals/{isin}/profile")
        if payload.get("status") != "success":
            raise ValueError("Upstox company-profile response was not successful")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError("Upstox company-profile data was not an object")
        sector = known_sector(data.get("sector"))
        if sector is None:
            print(f"[{symbol}] Upstox company-profile sector unavailable; using NA.")
            return UNAVAILABLE_SECTOR
        return sector
    except (RequestException, RuntimeError, ValueError, TypeError, AttributeError) as exc:
        print(f"[{symbol}] Upstox company-profile sector lookup failed; using NA: {exc}")
        return UNAVAILABLE_SECTOR


def resolve_sector(symbol, recorded=None):
    """Preserve an alert's recorded classification; backfill older logs."""
    return known_sector(recorded) or get_sector(symbol)
