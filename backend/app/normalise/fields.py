"""Normalisation of extracted and imported values (pipeline stage 4). Raw values are kept too."""

import re
from datetime import UTC, datetime, timedelta, timezone

from app.core.money import MoneyError, normalise_currency, parse_minor

# Zimbabwe uses CAT (UTC+2) year-round
LOCAL_TZ = timezone(timedelta(hours=2), "CAT")

STATUS_SYNONYMS = {
    "success": "success",
    "successful": "success",
    "completed": "success",
    "complete": "success",
    "approved": "success",
    "paid": "success",
    "confirmed": "success",
    "settled": "success",
    "credited": "success",
    "pending": "pending",
    "processing": "pending",
    "initiated": "pending",
    "submitted": "pending",
    "in progress": "pending",
    "failed": "failed",
    "declined": "failed",
    "rejected": "failed",
    "cancelled": "failed",
    "reversed": "reversed",
    "refunded": "reversed",
}

_DATE_FORMATS = [
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
    "%d-%m-%Y %H:%M",
    "%d-%m-%Y",
    "%d %b %Y %H:%M",
    "%d %b %Y",
    "%d %B %Y %H:%M",
    "%d %B %Y",
    "%d/%m/%y %H:%M",
    "%d/%m/%y",
]


def normalise_status(raw: str | None) -> str | None:
    if not raw:
        return None
    return STATUS_SYNONYMS.get(raw.strip().lower())


def normalise_reference(raw: str | None) -> str | None:
    """Uppercase, strip spaces and punctuation that payers or OCR add around references."""
    if not raw:
        return None
    ref = re.sub(r"[^A-Za-z0-9]", "", raw).upper()
    return ref or None


def parse_datetime(raw: str | None, fmt: str | None = None) -> datetime | None:
    """Parse a date; naive values are local Zimbabwe time. Day-first is assumed (local usage)."""
    if not raw:
        return None
    text = raw.strip()
    for f in [fmt] if fmt else _DATE_FORMATS:
        try:
            dt = datetime.strptime(text, f)  # noqa: DTZ007
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=LOCAL_TZ)
        return dt.astimezone(UTC)
    return None


_NUMERIC_DATE = re.compile(r"^\s*(\d{1,2})([/-])(\d{1,2})\2(\d{2,4})(.*)$")


def date_candidates(raw: str | None, order: str = "DMY") -> list[datetime]:
    """All plausible readings of a date, preferred order first.

    04/05/2026 is 4 May (DMY) or 5 April (MDY). Zimbabwean apps print day-first, but some
    international apps do not, so the matcher may accept either reading and flags the ambiguity.
    """
    if not raw:
        return []
    first = parse_datetime(raw)
    m = _NUMERIC_DATE.match(raw)
    if not m:
        return [first] if first else []
    a, sep, b, year, rest = m.groups()
    if first is None:
        # e.g. 10/21/2026: only valid month-first
        swapped = parse_datetime(f"{b}{sep}{a}{sep}{year}{rest}")
        return [swapped] if swapped else []
    if int(a) > 12 or int(b) > 12 or a == b:
        return [first]
    swapped = parse_datetime(f"{b}{sep}{a}{sep}{year}{rest}")
    if swapped is None or swapped == first:
        return [first]
    return [swapped, first] if order == "MDY" else [first, swapped]


def normalise_amount(raw: str | None, currency: str | None) -> int | None:
    if not raw or not currency:
        return None
    try:
        return parse_minor(raw, currency)
    except MoneyError:
        return None


def normalise_phone(raw: str | None) -> str | None:
    """E.164 for Zimbabwean numbers (07x xxx xxxx -> +2637x...)."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("263"):
        return f"+{digits}"
    if digits.startswith("0") and len(digits) == 10:
        return f"+263{digits[1:]}"
    return f"+{digits}" if digits else None


def normalise_name(raw: str | None) -> str | None:
    if not raw:
        return None
    return re.sub(r"\s+", " ", raw).strip().upper() or None


__all__ = [
    "LOCAL_TZ",
    "normalise_amount",
    "normalise_currency",
    "normalise_name",
    "normalise_phone",
    "normalise_reference",
    "normalise_status",
    "parse_datetime",
]
