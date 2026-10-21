"""Money helpers. Amounts are integer minor units with an explicit ISO currency; never floats."""

import re
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

# ISO 4217 exponents for currencies Cognify handles. ZiG is ISO "ZWG".
CURRENCY_EXPONENT: dict[str, int] = {"USD": 2, "ZWG": 2, "ZAR": 2, "BWP": 2, "GBP": 2, "EUR": 2}

CURRENCY_ALIASES: dict[str, str] = {
    "USD": "USD",
    "US$": "USD",
    "$": "USD",
    "US": "USD",
    "ZWG": "ZWG",
    "ZIG": "ZWG",
    "ZWL": "ZWG",  # legacy label still printed on some slips; treated as ZiG by policy
    "ZAR": "ZAR",
    "R": "ZAR",
    "BWP": "BWP",
    "P": "BWP",
    "GBP": "GBP",
    "£": "GBP",
    "EUR": "EUR",
    "€": "EUR",
}


class MoneyError(ValueError):
    pass


def normalise_currency(raw: str | None) -> str | None:
    if raw is None:
        return None
    key = raw.strip().upper().replace(" ", "")
    return CURRENCY_ALIASES.get(key)


_AMOUNT_RE = re.compile(r"[-+]?\d[\d,\s.']*")


def parse_amount(raw: str) -> Decimal:
    """Parse a human amount such as '1,234.50', '1 234,50', 'USD 40' into a Decimal."""
    text = raw.strip()
    match = _AMOUNT_RE.search(text)
    if not match:
        raise MoneyError(f"no amount in {raw!r}")
    num = match.group(0).strip().replace(" ", "").replace("'", "")
    if "," in num and "." in num:
        # whichever separator comes last is the decimal mark
        if num.rfind(",") > num.rfind("."):
            num = num.replace(".", "").replace(",", ".")
        else:
            num = num.replace(",", "")
    elif "," in num:
        head, _, tail = num.rpartition(",")
        # '40,00' -> decimal comma; '1,200' -> thousands
        num = f"{head.replace(',', '')}.{tail}" if len(tail) in (1, 2) else num.replace(",", "")
    try:
        return Decimal(num)
    except InvalidOperation as exc:
        raise MoneyError(f"bad amount {raw!r}") from exc


def to_minor(amount: Decimal, currency: str) -> int:
    exp = CURRENCY_EXPONENT.get(currency)
    if exp is None:
        raise MoneyError(f"unsupported currency {currency}")
    scaled = amount * (Decimal(10) ** exp)
    if scaled != scaled.to_integral_value():
        quantised = scaled.quantize(Decimal(1), rounding=ROUND_HALF_EVEN)
        raise MoneyError(f"{amount} has more precision than {currency} allows ({quantised})")
    return int(scaled)


def parse_minor(raw: str, currency: str) -> int:
    return to_minor(parse_amount(raw), currency)


def format_minor(amount_minor: int, currency: str) -> str:
    exp = CURRENCY_EXPONENT.get(currency, 2)
    value = Decimal(amount_minor) / (Decimal(10) ** exp)
    return f"{currency} {value:,.{exp}f}"
