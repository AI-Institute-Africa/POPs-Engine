"""Normalisation of extracted and imported values (pipeline stage 4). Raw values are kept too."""

import json
import re
from datetime import UTC, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from app.core.config import get_settings
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

# Month names in Shona and isiNdebele, mapped to English before parsing. Receipts and SMS from
# local banks are mostly English, but payers' own apps and typed messages are not always.
# Have a first-language speaker review these; extend with POPS_STATUS_SYNONYMS_FILE for statuses.
LOCAL_MONTHS = {
    # Shona
    "ndira": "January",
    "kukadzi": "February",
    "kurume": "March",
    "kubvumbi": "April",
    "chivabvu": "May",
    "chikumi": "June",
    "chikunguru": "July",
    "nyamavhuvhu": "August",
    "gunyana": "September",
    "gumiguru": "October",
    "mbudzi": "November",
    "zvita": "December",
    # isiNdebele
    "uzibandlela": "January",
    "unhlolanja": "February",
    "umbimbitho": "March",
    "umabasa": "April",
    "unkwenkwezi": "May",
    "unhlangula": "June",
    "untulikazi": "July",
    "uncwabakazi": "August",
    "umpandula": "September",
    "umfumfu": "October",
    "ulwezi": "November",
    "umpalakazi": "December",
}
_MONTH_WORD = re.compile(r"[A-Za-z]{4,}")


def _english_months(text: str) -> str:
    return _MONTH_WORD.sub(lambda m: LOCAL_MONTHS.get(m.group(0).lower(), m.group(0)), text)


@lru_cache
def status_synonyms() -> dict[str, str]:
    """Built-in status words plus POPS_STATUS_SYNONYMS_FILE ({"word": "success", ...})."""
    out = dict(STATUS_SYNONYMS)
    path = get_settings().status_synonyms_file
    if path:
        for word, status in json.loads(Path(path).read_text(encoding="utf-8")).items():
            if status not in ("success", "pending", "failed", "reversed"):
                raise ValueError(f"status synonym {word!r} maps to unknown status {status!r}")
            out[word.strip().lower()] = status
    return out


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
    return status_synonyms().get(raw.strip().lower())


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
    text = _english_months(raw.strip())
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
