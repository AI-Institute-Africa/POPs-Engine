"""Multi-reader agreement (error layer 2).

A critical field is accepted only when two independent readers agree on its normalised value,
or one reader is above the policy's single-reader confidence floor. Everything else is kept for
the reviewer but marked unagreed.
"""

from dataclasses import dataclass

from app.core.money import normalise_currency
from app.extraction import templates
from app.extraction.schema import FIELD_NAMES, FieldReading
from app.normalise.fields import (
    normalise_amount,
    normalise_name,
    normalise_reference,
    normalise_status,
    parse_datetime,
)


@dataclass
class FinalField:
    name: str
    raw: str | None
    normalised: str | None
    confidence: float
    extractor: str
    extractor_version: str
    region: dict[str, float] | None
    agreed: bool
    readings: dict[str, str | None]  # reader -> normalised value, for the reviewer


def normalise_field(name: str, raw: str | None, currency: str | None) -> str | None:
    if raw is None:
        return None
    if name == "amount":
        minor = normalise_amount(raw, currency or "USD")
        return str(minor) if minor is not None else None
    if name == "currency":
        return normalise_currency(raw)
    if name == "transaction_id":
        return normalise_reference(raw)
    if name == "date":
        dt = parse_datetime(raw)
        return dt.isoformat() if dt else None
    if name == "status":
        return normalise_status(raw)
    if name == "provider":
        return templates.normalise_provider(raw)
    if name in ("sender", "beneficiary"):
        return normalise_name(raw)
    return raw.strip() or None


def _same(name: str, a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return False
    if name == "date":
        # receipts often omit seconds or the time; agree on the calendar date
        return a[:10] == b[:10]
    if name == "beneficiary":
        return a in b or b in a
    return a == b


def combine(
    readings: dict[str, tuple[str, dict[str, FieldReading]]],
    critical: list[str],
    single_reader_min_conf: float,
) -> dict[str, FinalField]:
    """readings: reader name -> (version, field readings). The first reader is primary."""
    order = list(readings)

    def currency_of(reader: str) -> str | None:
        r = readings[reader][1].get("currency")
        return normalise_currency(r.raw) if r and r.raw else None

    out: dict[str, FinalField] = {}
    for name in FIELD_NAMES:
        normed: dict[str, str | None] = {}
        for reader in order:
            reading = readings[reader][1].get(name)
            if reading is not None:
                normed[reader] = normalise_field(name, reading.raw, currency_of(reader))
        if not normed:
            continue
        values = [(r, v) for r, v in normed.items() if v is not None]
        agreeing = [
            (r, v)
            for r, v in values
            if sum(1 for r2, v2 in values if r2 != r and _same(name, v, v2)) > 0
        ]
        chosen_reader = agreeing[0][0] if agreeing else (values[0][0] if values else order[0])
        chosen = readings[chosen_reader][1].get(name) or next(readings[r][1][name] for r in normed)
        # prefer a region from any reader that has one (OCR supplies boxes, VLM does not)
        region = chosen.region or next(
            (readings[r][1][name].region for r in normed if readings[r][1][name].region), None
        )
        agreed = bool(agreeing)
        confidence = chosen.confidence
        if agreed:
            confidence = max(readings[r][1][name].confidence for r, _ in agreeing)
        elif name in critical and confidence >= single_reader_min_conf:
            agreed = True
        elif name not in critical:
            agreed = normed.get(chosen_reader) is not None
        out[name] = FinalField(
            name=name,
            raw=chosen.raw,
            normalised=normed.get(chosen_reader),
            confidence=round(confidence, 3),
            extractor=chosen_reader,
            extractor_version=readings[chosen_reader][0],
            region=region,
            agreed=agreed,
            readings=normed,
        )
    return out
