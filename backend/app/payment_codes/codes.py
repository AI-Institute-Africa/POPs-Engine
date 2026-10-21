"""Cognify payment codes (blueprint 5.3): short, unambiguous, with a check character."""

import re
import secrets

# Crockford base32 without I, L, O, U so codes survive being read aloud or typed on a phone
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
PREFIX = "CG"
BODY_LEN = 6
_PATTERN = re.compile(rf"\b{PREFIX}-?([{ALPHABET}]{{{BODY_LEN}}})([{ALPHABET}])\b")


def _check_char(body: str) -> str:
    total = sum((i + 1) * ALPHABET.index(ch) for i, ch in enumerate(body))
    return ALPHABET[total % len(ALPHABET)]


def new_code() -> str:
    body = "".join(secrets.choice(ALPHABET) for _ in range(BODY_LEN))
    return f"{PREFIX}-{body}{_check_char(body)}"


def _canon(text: str) -> str:
    # payers mistype O/0 and I/1/L; map them the Crockford way
    return text.upper().replace("O", "0").replace("I", "1").replace("L", "1")


def find_codes(text: str | None) -> list[str]:
    """Return valid payment codes found in free text (e.g. a statement narrative)."""
    if not text:
        return []
    found = []
    for m in _PATTERN.finditer(_canon(text)):
        body, check = m.group(1), m.group(2)
        if _check_char(body) == check:
            found.append(f"{PREFIX}-{body}{check}")
    return found


def is_valid(code: str) -> bool:
    return len(find_codes(code.strip())) == 1
