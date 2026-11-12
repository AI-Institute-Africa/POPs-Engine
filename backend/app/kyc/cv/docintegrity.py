"""Document and capture integrity: tamper signals, reused images, one ID used by two people.

Everything here is a risk flag. It can send a verification to a reviewer; it can never approve one.
"""

import hashlib
import hmac

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.extraction import tamper
from app.intake.files import phash_to_int
from app.models import KycEvidence, KycState, KycVerification


def doc_number_hash(country: str | None, number: str | None) -> str | None:
    """Keyed hash: lets us find the same ID on two subjects without keeping the number
    searchable. The key is the deployment secret, so hashes are not portable or guessable."""
    if not number:
        return None
    key = get_settings().jwt_secret.encode()
    return hmac.new(key, f"{country or '?'}:{number}".encode(), hashlib.sha256).hexdigest()


def tamper_score(img: Image.Image) -> tuple[float | None, list[str]]:
    """(score, signals); score is None when the analysis could not run (the rules then fail
    closed to a person)."""
    report = tamper.analyse(img)
    return (None if report.skipped else report.score), report.signals


def _hamming(a: int, b: int) -> int:
    return bin((a ^ b) & 0xFFFFFFFFFFFFFFFF).count("1")


def duplicate_flags(
    session: Session,
    kyc: KycVerification,
    *,
    own_hashes: dict[str, str],
) -> list[str]:
    """Flags from comparing this submission with the organisation's other verifications.

    own_hashes: role -> sha256 of each uploaded image.
    """
    flags: list[str] = []
    # the selfie must not be the ID image itself, nor a copy of the printed portrait
    if own_hashes.get("selfie") and own_hashes["selfie"] in {
        h for r, h in own_hashes.items() if r != "selfie"
    }:
        flags.append("SELFIE_IS_ID_IMAGE")
    # the same image already used by a different subject
    others = session.scalars(
        select(KycEvidence)
        .join(KycVerification, KycVerification.kyc_id == KycEvidence.kyc_id)
        .where(
            KycEvidence.org_id == kyc.org_id,
            KycEvidence.kyc_id != kyc.kyc_id,
            KycEvidence.sha256.in_(list(own_hashes.values())),
            (KycVerification.subject_type != kyc.subject_type)
            | (KycVerification.subject_ref != kyc.subject_ref),
        )
    ).first()
    if others is not None:
        flags.append("IMAGE_REUSED_BY_OTHER_SUBJECT")
    # the same ID number registered to a different subject (and not rejected)
    if kyc.doc_number_hash:
        other_subject = session.scalars(
            select(KycVerification.kyc_id).where(
                KycVerification.org_id == kyc.org_id,
                KycVerification.doc_number_hash == kyc.doc_number_hash,
                KycVerification.kyc_id != kyc.kyc_id,
                KycVerification.state.in_([KycState.VERIFIED, KycState.NEEDS_REVIEW]),
                (KycVerification.subject_type != kyc.subject_type)
                | (KycVerification.subject_ref != kyc.subject_ref),
            )
        ).first()
        if other_subject is not None:
            flags.append("ID_NUMBER_USED_BY_OTHER_SUBJECT")
    return flags


def phash_int_of(img: Image.Image) -> int:
    import imagehash

    return phash_to_int(str(imagehash.phash(img)))


def portrait_copy_flag(portrait: Image.Image | None, face: Image.Image | None) -> list[str]:
    """A live selfie differs from the printed portrait (pose, light, background). A near-identical
    perceptual hash means the 'selfie' is a crop of the ID photo."""
    if portrait is None or face is None:
        return []
    a, b = phash_int_of(portrait), phash_int_of(face)
    return ["SELFIE_COPIED_FROM_ID_PHOTO"] if _hamming(a, b) <= 3 else []
