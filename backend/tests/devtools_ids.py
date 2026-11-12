"""Small builders over `app.devtools` so KYC tests read as scenarios."""

from app.devtools import id_fields, make_id_card, make_selfie

GOOD_FIELDS = id_fields()


def front(
    *,
    face_seed: int = 1,
    fields: dict | None = None,
    ocr: dict | None = None,
    number: str | None = None,
    blank_face: bool = False,
    **kw,
) -> bytes:
    """An ID card whose two fake readers agree, unless `ocr` overrides the second reader."""
    base = {**GOOD_FIELDS, **({"document_number": number} if number else {})}
    vlm = {**base, **(fields or {})}
    second = {**vlm, **(ocr or {})}
    return make_id_card(vlm, second, face_seed=face_seed, blank_face=blank_face, **kw)


def selfie(face_seed: int = 1, **kw) -> bytes:
    return make_selfie(face_seed, **kw)
