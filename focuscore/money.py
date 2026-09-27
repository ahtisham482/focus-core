"""Phase 9: money helpers. Binding rule (Qwen audit, constraint A):
money is NEVER a float. Rates and amounts are integer minor units
(e.g. cents); all arithmetic uses Decimal with half-up rounding."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CURRENCY_SYMBOLS = {
    "USD": "$",
    "EUR": "\u20ac",
    "GBP": "\u00a3",
    "PKR": "Rs ",
    "INR": "\u20b9",
}


def format_minor(amount_minor, currency="USD"):
    """Format integer minor units as a money string, e.g. 950000 -> $9,500.00."""
    if amount_minor is None:
        return "\u2014"
    currency = (currency or "USD").upper()
    symbol = CURRENCY_SYMBOLS.get(currency, currency + " ")
    negative = amount_minor < 0
    value = Decimal(abs(int(amount_minor))) / Decimal(100)
    text = "%s%s" % (symbol, format(value, ",.2f"))
    return ("-" + text) if negative else text


def amount_minor_for(duration_seconds, rate_minor):
    """Cost of duration_seconds at rate_minor (per hour), half-up to 1 minor unit."""
    if duration_seconds is None or rate_minor is None:
        return None
    if duration_seconds <= 0 or rate_minor <= 0:
        return 0
    return int(
        (
            Decimal(int(duration_seconds)) * Decimal(int(rate_minor)) / Decimal(3600)
        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


def parse_rate_to_minor(text):
    """Parse '95.50' -> 9550. Returns None for blank/invalid."""
    if text is None:
        return None
    text = str(text).strip().replace(",", "")
    if not text:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    if value < 0 or value > Decimal("1000000"):
        return None
    return int((value * Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def parse_hours_to_seconds(text):
    """Parse '7.5' hours -> 27000 seconds. Returns None for blank/invalid."""
    if text is None:
        return None
    text = str(text).strip().replace(",", "")
    if not text:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    if value < 0 or value > Decimal("10000"):
        return None
    return int((value * Decimal(3600)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def format_hours(total_seconds):
    """27000 -> '7.5h'; 5400 -> '1.5h'; 3600 -> '1h'."""
    if total_seconds is None:
        return "\u2014"
    hours = Decimal(int(total_seconds)) / Decimal(3600)
    text = format(hours.normalize(), "f")
    return "%sh" % text


def format_duration(total_seconds):
    """5400 -> '1h 30m'; 3000 -> '50m'."""
    if total_seconds is None:
        return "\u2014"
    total = int(total_seconds)
    hours, remainder = divmod(total, 3600)
    minutes = remainder // 60
    if hours:
        return "%dh %02dm" % (hours, minutes)
    return "%dm" % minutes
