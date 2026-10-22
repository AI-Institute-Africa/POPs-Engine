"""Parse recipient-side payment alerts (merchant SMS, bank/mobile-money notification emails).

Only *incoming* credits become credits. Debits, one-time passwords, balance notices and
promotions are ignored. Templates are configuration: the built-ins below are starting guesses
and must be confirmed against real alerts from each provider (POPS_ALERT_TEMPLATES_FILE adds or
overrides them without a code change).
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

from app.core.config import get_settings
from app.core.money import MoneyError, normalise_currency, parse_minor
from app.extraction.templates import DATE_RE, GENERIC_TXN
from app.normalise.fields import normalise_name, normalise_reference, parse_datetime

CREDIT_WORDS = re.compile(
    r"\b(received|credited|credit of|deposit(?:ed)?|incoming|you have been paid|paid you|"
    r"payment from|payment of .{1,40}? from|has been paid to your|merchant payment)\b",
    re.I,
)
NOT_A_CREDIT = re.compile(
    r"\b(debited|you have sent|you sent|sent to|withdraw(?:n|al)?|purchase|airtime|bundle|"
    r"one[- ]time|otp|verification code|\bpin\b|reversed|reversal|failed|declined|"
    r"insufficient|balance enquiry|promo)\b",
    re.I,
)
AMOUNT = re.compile(r"(USD|US\$|ZWG|ZiG|ZAR|\$)\s?([0-9][0-9,]*(?:\.[0-9]{2})?)", re.I)
PAYER = re.compile(
    r"\bfrom\s+([A-Za-z][A-Za-z .'-]{1,60}?)(?=\s*(?:\(|\+?\d{9,}|[.,;]|\s+on\b|\s+ref|\s+txn|$))",
    re.I,
)
PHONE = re.compile(r"(\+?263\d{9}|0[7][0-9]{8})")


class AlertTemplate(BaseModel):
    name: str
    provider: str
    sender_pattern: str | None = None  # regex on sender id / From address
    # named groups: amount (required), currency, reference, payer, phone, when
    pattern: str


BUILTIN_TEMPLATES: list[AlertTemplate] = [
    AlertTemplate(
        name="ecocash_merchant",
        provider="ECOCASH",
        sender_pattern=r"ecocash|\+?263\d+|164",
        pattern=r"(?:received|payment of)\s+(?P<currency>USD|ZWG|ZiG|\$)\s?"
        r"(?P<amount>[\d,]+\.\d{2})\s+from\s+(?P<payer>[A-Za-z .'-]+?)\s*"
        r"(?:\(?(?P<phone>\+?\d{9,12})\)?)?[.,]"
        r".*?(?:approval code|txn id|trans(?:action)? id|ref(?:erence)?)[:\s]*"
        r"(?P<reference>[A-Z0-9.]{6,30})",
    ),
    AlertTemplate(
        name="bank_credit",
        provider="BANK",
        pattern=r"(?:credited with|credit of|received)\s+(?P<currency>USD|ZWG|ZiG|ZAR|\$)\s?"
        r"(?P<amount>[\d,]+\.\d{2}).*?(?:ref(?:erence)?|narr(?:ative)?)[:\s]*"
        r"(?P<reference>[A-Z0-9/.-]{4,40})",
    ),
]


@lru_cache
def templates() -> list[AlertTemplate]:
    by_name = {t.name: t for t in BUILTIN_TEMPLATES}
    path = get_settings().alert_templates_file
    if path:
        for item in json.loads(Path(path).read_text()):
            t = AlertTemplate.model_validate(item)
            re.compile(t.pattern)
            by_name[t.name] = t
    return list(by_name.values())


@dataclass
class ParsedAlert:
    amount_minor: int
    currency: str
    reference: str | None
    payer: str | None
    occurred_at: datetime | None
    template: str
    provider: str | None


class NotACredit(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _finish(m: dict, text: str, template: str, provider: str | None) -> ParsedAlert:
    currency = normalise_currency(m.get("currency") or "") or "USD"
    try:
        amount = parse_minor(m["amount"], currency)
    except (MoneyError, KeyError) as exc:
        raise NotACredit("amount could not be read") from exc
    if amount <= 0:
        raise NotACredit("amount is zero")
    when = None
    if m.get("when"):
        when = parse_datetime(m["when"])
    elif dm := DATE_RE.search(text):
        when = parse_datetime(dm.group(1))
    payer = normalise_name(m.get("payer"))
    if m.get("phone"):
        payer = f"{payer or ''} {m['phone']}".strip()
    return ParsedAlert(
        amount_minor=amount,
        currency=currency,
        reference=(m.get("reference") or "").strip(".") or None,
        payer=payer,
        occurred_at=when,
        template=template,
        provider=provider,
    )


def parse(text: str, sender: str = "", template_name: str | None = None) -> ParsedAlert:
    """Parse one alert into a credit, or raise NotACredit with the reason it was ignored."""
    flat = " ".join(text.split())
    if NOT_A_CREDIT.search(flat) and not CREDIT_WORDS.search(flat):
        raise NotACredit("not an incoming payment (debit, OTP, balance or promotion)")
    if NOT_A_CREDIT.search(flat) and re.search(
        r"\b(sent to|debited|reversal|reversed)\b", flat, re.I
    ):
        raise NotACredit("outgoing or reversed transaction")
    if not CREDIT_WORDS.search(flat):
        raise NotACredit("no incoming-payment wording")

    candidates = [t for t in templates() if template_name in (None, t.name)]
    for t in candidates:
        if t.sender_pattern and sender and not re.search(t.sender_pattern, sender, re.I):
            continue
        m = re.search(t.pattern, flat, re.I)
        if m:
            return _finish(m.groupdict(), flat, t.name, t.provider)

    # generic fallback: incoming wording, an amount with currency, and a reference
    am = AMOUNT.search(flat)
    if not am:
        raise NotACredit("no amount found")
    groups: dict = {"currency": am.group(1), "amount": am.group(2)}
    for p in GENERIC_TXN:
        rm = re.search(p, flat, re.I)
        if rm:
            groups["reference"] = rm.group(1)
            break
    if pm := PAYER.search(flat):
        groups["payer"] = pm.group(1)
    if ph := PHONE.search(flat):
        groups["phone"] = ph.group(1)
    return _finish(groups, flat, "generic", None)


def reference_key(reference: str | None) -> str | None:
    return normalise_reference(reference)
