"""Provider layout library (blueprint 5.5): deterministic patterns, easier to audit than a model.

Patterns are deliberately conservative. They are an independent second reader for the VLM, so a
missed field is fine (it goes to review); a wrong field is not.
"""

import re
from dataclasses import dataclass, field


@dataclass
class ProviderTemplate:
    provider: str
    document_type: str
    keywords: list[str]
    txn_patterns: list[str] = field(default_factory=list)


GENERIC_TXN = [
    r"(?:txn|trans(?:action)?)\s*(?:id|ref(?:erence)?|no\.?|number)\s*[:#.]?\s*([A-Z0-9][A-Z0-9.\-]{5,30})",
    r"(?:ref(?:erence)?|approval\s*code)\s*(?:no\.?|number)?\s*[:#]?\s*([A-Z0-9][A-Z0-9.\-]{5,30})",
]

TEMPLATES: list[ProviderTemplate] = [
    ProviderTemplate(
        "ECOCASH",
        "mobile_money_receipt",
        ["ecocash"],
        [r"\b((?:PP|MP|CO)\d{6}\.\d{4}\.[A-Z0-9]{6})\b"],
    ),
    ProviderTemplate("ONEMONEY", "mobile_money_receipt", ["onemoney", "one money"]),
    ProviderTemplate("INNBUCKS", "mobile_money_receipt", ["innbucks"]),
    ProviderTemplate("CBZ", "bank_transfer", ["cbz"]),
    ProviderTemplate("STANBIC", "bank_transfer", ["stanbic"]),
    ProviderTemplate("FBC", "bank_transfer", ["fbc bank", "fbc"]),
    ProviderTemplate("STEWARD", "bank_transfer", ["steward bank"]),
    ProviderTemplate("CABS", "bank_transfer", ["cabs"]),
    ProviderTemplate("ZB", "bank_transfer", ["zb bank"]),
    ProviderTemplate("NMB", "bank_transfer", ["nmb"]),
    ProviderTemplate("ZIPIT", "bank_transfer", ["zipit"]),
    ProviderTemplate("RTGS", "bank_transfer", ["rtgs"]),
    ProviderTemplate("PAYNOW", "gateway_receipt", ["paynow"]),
]

PROVIDER_ALIASES = {t.provider.lower(): t.provider for t in TEMPLATES} | {
    k: t.provider for t in TEMPLATES for k in t.keywords
}

AMOUNT_RE = re.compile(
    r"(?:amount|amt|total|paid|sent|transfer(?:red)?)\s*[:\-]?\s*"
    r"(USD|US\$|ZWG|ZiG|ZAR|\$)?\s*([0-9][0-9,\s]*\.[0-9]{2})",
    re.IGNORECASE,
)
CURRENCY_AMOUNT_RE = re.compile(r"(USD|US\$|ZWG|ZiG|ZAR|\$)\s*([0-9][0-9,]*\.[0-9]{2})", re.I)
DATE_RE = re.compile(
    r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?"
    r"|\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?"
    r"|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}"
    r"(?:\s+\d{1,2}:\d{2})?)\b",
    re.IGNORECASE,
)
STATUS_RE = re.compile(
    r"\b(successful|success|completed|approved|pending|processing|failed|declined|reversed)\b",
    re.IGNORECASE,
)
BENEFICIARY_RE = re.compile(
    r"(?:to|paid to|recipient|beneficiary|merchant)\s*[:\-]\s*([^\n]{3,60})", re.IGNORECASE
)


def normalise_provider(raw: str | None) -> str | None:
    if not raw:
        return None
    low = raw.strip().lower()
    for key, provider in PROVIDER_ALIASES.items():
        if key in low:
            return provider
    return raw.strip().upper()


def classify(text: str) -> ProviderTemplate | None:
    low = text.lower()
    for tpl in TEMPLATES:
        if any(k in low for k in tpl.keywords):
            return tpl
    return None


def parse_text(text: str) -> dict[str, str]:
    """Pull receipt fields out of OCR text with conservative patterns."""
    out: dict[str, str] = {}
    tpl = classify(text)
    if tpl:
        out["provider"] = tpl.provider
        out["document_type"] = tpl.document_type
    for pattern in (tpl.txn_patterns if tpl else []) + GENERIC_TXN:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            out["transaction_id"] = m.group(1).strip(".-")
            break
    m = AMOUNT_RE.search(text) or CURRENCY_AMOUNT_RE.search(text)
    if m:
        if m.group(1):
            out["currency"] = m.group(1)
        out["amount"] = m.group(2).replace(" ", "")
    if "currency" not in out:
        c = re.search(r"\b(USD|ZWG|ZiG|ZAR)\b", text, re.IGNORECASE)
        if c:
            out["currency"] = c.group(1)
    m = DATE_RE.search(text)
    if m:
        out["date"] = m.group(1)
    m = STATUS_RE.search(text)
    if m:
        out["status"] = m.group(1)
    m = BENEFICIARY_RE.search(text)
    if m:
        out["beneficiary"] = m.group(1).strip()
    return out
