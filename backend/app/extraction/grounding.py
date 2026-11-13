"""Grounding: a value a single reader reports must be visible in the document's own text.

A vision model can invent a plausible amount or reference. When only one reader vouches for a
critical field, the claim is only as good as that reader's honesty, so the value must also occur
in the text we recovered independently (OCR output or a PDF text layer). A value that cannot be
found there is kept for the reviewer but is not treated as agreed.

When no independent text exists at all (no OCR available, blank image) grounding cannot be
checked and is skipped. The existing agreement rules still apply in that case.
"""

import re

from app.normalise.fields import normalise_amount, normalise_reference

_NUMBER = re.compile(r"\d[\d.,'  ]*\d|\d")
_MIN_REFERENCE_LEN = 4


def _amount_tokens(text: str) -> set[str]:
    return {tok.strip() for tok in _NUMBER.findall(text)}


def amount_grounded(minor: int, currency: str | None, text: str) -> bool:
    cur = currency or "USD"
    for tok in _amount_tokens(text):
        if normalise_amount(tok, cur) == minor:
            return True
        # OCR often glues a currency symbol or trailing word to the number; try the digits only
        digits = re.sub(r"[^\d.,]", "", tok)
        if digits and digits != tok and normalise_amount(digits, cur) == minor:
            return True
    return False


def reference_grounded(reference: str, text: str) -> bool:
    ref = normalise_reference(reference)
    if not ref or len(ref) < _MIN_REFERENCE_LEN:
        return False
    # references are matched ignoring spaces and punctuation, as OCR splits and joins them
    return ref in (normalise_reference(text) or "")


def is_grounded(name: str, normalised: str | None, currency: str | None, text: str) -> bool:
    """True when `normalised` (the reader's value for field `name`) occurs in `text`."""
    if normalised is None:
        return False
    if name == "amount":
        try:
            return amount_grounded(int(normalised), currency, text)
        except ValueError:
            return False
    if name == "transaction_id":
        return reference_grounded(normalised, text)
    return True  # other fields are not grounded by this gate
