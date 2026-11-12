"""Cross-source agreement for identity fields.

Sources are the independent readers (VLM, OCR) and, when its check digits all pass, the MRZ. A
critical field counts as agreed only when two sources give the same normalised value, the same
rule the payment engine uses for receipts. A source that contradicts the MRZ is a conflict: a
printed zone that disagrees with the machine-readable one is a classic sign of an edited ID.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime

from app.extraction.schema import FieldReading
from app.kyc.doc.classify import _norm as norm_doc_type
from app.kyc.doc.mrz import MrzResult
from app.kyc.schema import CRITICAL_FIELDS, FIELD_NAMES
from app.kyc.templates import normalise_country
from app.normalise import fields as nf

MRZ_SOURCE = "mrz"


@dataclass
class IdReading:
    fields: dict[str, str | None] = field(default_factory=dict)
    agreed: dict[str, bool] = field(default_factory=dict)
    sources: dict[str, dict[str, str | None]] = field(default_factory=dict)  # field -> source -> v
    conflicts: list[str] = field(default_factory=list)
    agreement: float = 0.0  # share of critical fields two sources agree on
    mrz: MrzResult | None = None


def _name(raw: str) -> str | None:
    v = re.sub(r"[^A-Z \-']", " ", raw.upper().replace("<", " "))
    return re.sub(r"\s+", " ", v).strip() or None


def calendar_date(raw: str) -> str | None:
    """A printed date as an ISO calendar date. Day-first, like the payment engine, but with no
    timezone shift: a birth date is a calendar date, not an instant (the receipt parser converts
    local midnight to UTC, which would move it back a day). The MRZ, when present, is ISO and
    settles any day/month ambiguity: a wrong guess shows up as a conflict, not a silent match."""
    text = nf._english_months(raw.strip())
    for fmt in nf._DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()  # noqa: DTZ007
        except ValueError:
            continue
    return None


def normalise(name: str, raw: str | None) -> str | None:
    if not raw or not raw.strip():
        return None
    if name in ("surname", "given_names", "full_name"):
        return _name(raw)
    if name == "document_number":
        return re.sub(r"[^A-Z0-9]", "", raw.upper()) or None
    if name in ("date_of_birth", "expiry_date"):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw.strip()):
            return raw.strip()
        return calendar_date(raw)
    if name == "sex":
        s = raw.strip().upper()[:1]
        return s if s in ("M", "F") else None
    if name in ("issuing_country", "nationality"):
        return normalise_country(raw)
    if name == "doc_type":
        return norm_doc_type(raw)
    return raw.strip() or None


def _same(name: str, a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return False
    if name == "full_name":
        ta, tb = set(a.split()), set(b.split())
        small, big = sorted((ta, tb), key=len)
        # a missing middle name is fine; a different name is not
        return ta == tb or (len(small) >= 2 and small <= big)
    return a == b


def _per_source(readings: dict[str, dict[str, FieldReading]]) -> dict[str, dict[str, str | None]]:
    out: dict[str, dict[str, str | None]] = {}
    for src, data in readings.items():
        vals = {n: normalise(n, r.raw) for n, r in data.items() if n in FIELD_NAMES}
        if not vals.get("full_name"):
            parts = " ".join(p for p in (vals.get("given_names"), vals.get("surname")) if p)
            vals["full_name"] = parts or None
        out[src] = vals
    return out


def _mrz_values(m: MrzResult) -> dict[str, str | None]:
    return {
        "doc_type": m.doc_type,
        "issuing_country": normalise_country(m.issuing_country),
        "document_number": normalise("document_number", m.document_number),
        "surname": _name(m.surname),
        "given_names": _name(m.given_names),
        "full_name": _name(m.full_name),
        "date_of_birth": m.date_of_birth,
        "expiry_date": m.expiry_date,
        "sex": m.sex,
        "nationality": normalise_country(m.nationality),
    }


def combine(readings: dict[str, dict[str, FieldReading]], mrz: MrzResult | None) -> IdReading:
    per = _per_source(readings)
    if mrz is not None and mrz.valid:
        per[MRZ_SOURCE] = _mrz_values(mrz)
    out = IdReading(mrz=mrz)
    for name in FIELD_NAMES:
        if name == "mrz":
            continue
        values = {src: v[name] for src, v in per.items() if v.get(name)}
        out.sources[name] = values
        if not values:
            out.fields[name] = None
            out.agreed[name] = False
            continue
        agreeing = [
            s
            for s, v in values.items()
            if any(_same(name, v, v2) for s2, v2 in values.items() if s2 != s)
        ]
        # the MRZ is checksummed, so when it agrees with anyone it is the value we keep
        chosen_src = (
            MRZ_SOURCE
            if MRZ_SOURCE in agreeing
            else (agreeing[0] if agreeing else next(iter(values)))
        )
        out.fields[name] = values[chosen_src]
        out.agreed[name] = bool(agreeing)
        distinct = {v for v in values.values()}
        if len(distinct) > 1 and not all(_same(name, a, b) for a in distinct for b in distinct):
            out.conflicts.append(name)
    critical = [out.agreed.get(n, False) for n in CRITICAL_FIELDS]
    out.agreement = round(sum(critical) / len(CRITICAL_FIELDS), 3)
    return out
