"""Document type: from the MRZ when there is one, else from what the readers saw."""

from app.kyc.doc.mrz import MrzResult
from app.kyc.schema import DOC_TYPES


def classify(mrz: MrzResult | None, reader_types: list[str | None]) -> str | None:
    """MRZ first (the document code is protected by the layout), then the readers' votes.
    Returns None when nothing identifies the document, which sends it to a person."""
    if mrz is not None and mrz.valid:
        return mrz.doc_type
    votes = [t for t in (_norm(t) for t in reader_types) if t]
    if not votes:
        return mrz.doc_type if mrz is not None else None
    best = max(set(votes), key=votes.count)
    return best


def _norm(raw: str | None) -> str | None:
    if not raw:
        return None
    v = raw.strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "id": "national_id",
        "id_card": "national_id",
        "identity_card": "national_id",
        "drivers_license": "drivers_licence",
        "driving_licence": "drivers_licence",
        "driver's_licence": "drivers_licence",
        "drivers_licence": "drivers_licence",
        "driving_license": "drivers_licence",
    }
    v = aliases.get(v, v)
    return v if v in DOC_TYPES else None
