"""Phase 9: money helpers. Binding rule (Qwen audit, constraint A):
money is NEVER a float. Rates and amounts are integer minor units
(e.g. cents); all arithmetic uses Decimal with half-up rounding."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CURRENCY_SYMBOLS: dict[str, str] = {
    "USD": "$",
    "EUR": "\u20ac",
    "GBP": "\u00a3",
    "PKR": "Rs ",
    "INR": "\u20b9",
}


def format_minor(amount_minor: int | None, currency: str = "USD") -> str:
    """Format integer minor units as a money string, e.g. 950000 -> $9,500.00."""
    if amount_minor is None:
        return "\u2014"
    currency = (currency or "USD").upper()
    symbol = CURRENCY_SYMBOLS.get(currency, currency + " ")
    negative = amount_minor < 0
    value = Decimal(abs(int(amount_minor))) / Decimal(100)
    text = "%s%s" % (symbol, format(value, ",.2f"))
    return ("-" + text) if negative else text


def amount_minor_for(
    duration_seconds: int | None, rate_minor: int | None
) -> int | None:
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


def pct_of_minor(amount_minor: int | None, pct: int) -> int:
    """Integer percent of integer minor units, truncated (Qwen Q12).

    pct is an integer percentage (20 = 20%). Pure integer arithmetic,
    no floats: 750 * 20 // 100 = 150. Used for invoice tax/discount.
    """
    if amount_minor is None:
        return 0
    return int(amount_minor) * int(pct) // 100


def parse_rate_to_minor(text: object | None) -> int | None:
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


def parse_hours_to_seconds(text: object | None) -> int | None:
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


def format_hours(total_seconds: int | None) -> str:
    """27000 -> '7.5h'; 5400 -> '1.5h'; 3600 -> '1h'."""
    if total_seconds is None:
        return "\u2014"
    hours = Decimal(int(total_seconds)) / Decimal(3600)
    text = format(hours.normalize(), "f")
    return "%sh" % text


def format_duration(total_seconds: int | None) -> str:
    """5400 -> '1h 30m'; 3000 -> '50m'."""
    if total_seconds is None:
        return "\u2014"
    total = int(total_seconds)
    hours, remainder = divmod(total, 3600)
    minutes = remainder // 60
    if hours:
        return "%dh %02dm" % (hours, minutes)
    return "%dm" % minutes
