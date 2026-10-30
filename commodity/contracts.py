"""Active MCX futures discovery and rollover selection."""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from commodity import commodity_settings as settings

IST = ZoneInfo("Asia/Kolkata")


def _text(value):
    return "".join(character for character in str(value or "").upper() if character.isalnum())


def _expiry(instrument):
    value = instrument.get("expiry")
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone(IST).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    try:
        timestamp_value = float(text)
    except ValueError:
        timestamp_value = None
    if timestamp_value is not None:
        timestamp = int(timestamp_value)
        # Upstox BOD instrument files represent futures expiry as epoch milliseconds.
        seconds = timestamp / 1000 if timestamp >= 100_000_000_000 else timestamp
        return datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone(IST).date()
    return date.fromisoformat(text[:10])


def commodity_candidates(instruments, symbol, as_of=None):
    as_of = as_of or date.today()
    wanted = _text(symbol)
    candidates = []
    for instrument in instruments:
        exchange = instrument.get("segment") or instrument.get("exchange")
        instrument_type = str(instrument.get("instrument_type") or "").upper()
        trading_symbol = instrument.get("trading_symbol") or instrument.get("tradingsymbol")
        if exchange != "MCX_FO" or not instrument.get("instrument_key"):
            continue
        if not instrument_type.startswith("FUT"):
            continue
        if all(wanted not in _text(value) for value in (
            trading_symbol, instrument.get("name"), instrument.get("underlying_symbol")
        )):
            continue
        expiry = _expiry(instrument)
        if expiry and expiry >= as_of:
            candidates.append((expiry, instrument))
    return sorted(candidates, key=lambda item: item[0])


def select_contract(instruments, symbol, as_of=None, minimum_days=None):
    """Select front month unless it expires within the configured rollover window."""
    as_of = as_of or date.today()
    minimum_days = settings.ROLL_MIN_DAYS_TO_EXPIRY if minimum_days is None else minimum_days
    candidates = commodity_candidates(instruments, symbol, as_of)
    if not candidates:
        raise LookupError("No active MCX futures contract found for %s" % symbol)
    for expiry, instrument in candidates:
        if (expiry - as_of).days > minimum_days:
            result = dict(instrument)
            result["expiry"] = expiry.isoformat()
            return result
    expiry, instrument = candidates[0]
    result = dict(instrument)
    result["expiry"] = expiry.isoformat()
    return result


def select_all(instruments, as_of=None):
    selected = {}
    for symbol in settings.COMMODITIES:
        try:
            candidates = commodity_candidates(instruments, symbol, as_of)
            if not candidates:
                print(f"[MCX][CONTRACT] {symbol}: no active futures matched the instrument master.")
                continue
            contract = select_contract(instruments, symbol, as_of)
            selected[symbol] = contract
            print(
                f"[MCX][CONTRACT] {symbol}: selected "
                f"{contract.get('trading_symbol') or contract.get('tradingsymbol')} "
                f"expiry={contract['expiry']} key={contract['instrument_key']} "
                f"({len(candidates)} active candidates)."
            )
        except Exception as exc:
            print(f"[MCX][CONTRACT] {symbol}: selection failed: {type(exc).__name__}: {exc}")
    if not selected:
        raise LookupError("No supported MCX commodity futures contracts could be selected.")
    return selected

