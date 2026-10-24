"""Active MCX futures discovery and rollover selection."""

from datetime import date

from commodity import commodity_settings as settings


def _text(value):
    return "".join(character for character in str(value or "").upper() if character.isalnum())


def _expiry(instrument):
    value = instrument.get("expiry")
    if not value:
        return None
    return date.fromisoformat(str(value)[:10])


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
        if wanted not in _text(trading_symbol) and wanted not in _text(instrument.get("name")):
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
    return {symbol: select_contract(instruments, symbol, as_of) for symbol in settings.COMMODITIES}

