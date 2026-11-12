"""KYC service: intake, the processing pipeline, reviewer decisions, expiry and retention.

Order of work for one verification (`process`): capture quality -> card detection -> readers ->
MRZ -> cross-source agreement -> face match -> passive liveness -> integrity checks -> rules
(`auto.maybe_auto_verify`). Every outcome is an append-only `KycDecision`; the pipeline itself
never rejects and never touches the money ledger.
"""

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.config import get_settings
from app.core.security import Principal, Role
from app.extraction.readers.base import ReaderUnavailable
from app.extraction.schema import FieldReading
from app.intake.files import RejectedFile, phash_to_int, prepare
from app.intake.scan import scan
from app.intake.storage import get_storage
from app.kyc import agreement, auto, readers, states, templates
from app.kyc.auto import Inputs, KycError
from app.kyc.cv import docintegrity, face, liveness
from app.kyc.doc import classify, detect
from app.kyc.doc import mrz as mrz_mod
from app.models import (
    KycDecision,
    KycEvidence,
    KycState,
    KycSubject,
    KycVerification,
)
from app.policy.engine import PolicyConfig, load_policy

log = logging.getLogger(__name__)

ROLES = ("front", "back", "selfie")
REQUIRED_ROLES = ("front", "selfie")
IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/heic": "heic"}
PIPELINE_ACTOR = "rule:kyc-pipeline"
# capture problems the subject can fix by retaking the photo
RETAKE = {
    "CAPTURE_TOO_SMALL",
    "CAPTURE_BLURRY",
    "CAPTURE_GLARE",
    "NO_FACE_IN_SELFIE",
    "NO_FACE_ON_ID",
    "SELFIE_TOO_SMALL",
}


# --- helpers -----------------------------------------------------------------------------------


def record_decision(
    session: Session,
    kyc: KycVerification,
    outcome: str,
    reasons: list[str],
    policy_version: str,
    actor_type: str,
    actor_id: str,
    snapshot: dict[str, Any],
    note: str | None = None,
) -> KycDecision:
    d = KycDecision(
        kyc_id=kyc.kyc_id,
        org_id=kyc.org_id,
        outcome=outcome,
        reason_codes=reasons,
        policy_version=policy_version,
        actor_type=actor_type,
        actor_id=actor_id,
        snapshot=snapshot,
        note=note,
    )
    session.add(d)
    return d


def _move(session: Session, kyc: KycVerification, dst: str, actor: str) -> None:
    src = kyc.state
    try:
        states.check_transition(src, dst)
    except states.IllegalTransition as exc:
        raise KycError("ILLEGAL_TRANSITION", str(exc), 409) from exc
    kyc.state = dst
    audit(
        session,
        actor=actor,
        action=f"kyc.{src}->{dst}"[:60],
        entity_type="kyc",
        entity_id=kyc.kyc_id,
        org_id=kyc.org_id,
        before={"state": src},
        after={"state": dst},
        correlation_id=kyc.correlation_id,
    )


def set_verified(
    session: Session,
    kyc: KycVerification,
    cfg: PolicyConfig,
    policy_version: str,
    actor_type: str,
    actor_id: str,
    snapshot: dict[str, Any],
    *,
    note: str | None,
    reasons: list[str],
) -> None:
    _move(session, kyc, KycState.VERIFIED, actor_id)
    now = datetime.now(UTC)
    kyc.verified_at = now
    kyc.expires_at = auto.validity_end(cfg, now)
    kyc.reason_codes = reasons
    record_decision(
        session,
        kyc,
        KycState.VERIFIED,
        reasons,
        policy_version,
        actor_type,
        actor_id,
        snapshot,
        note,
    )


def is_own_identity(p: Principal, kyc: KycVerification) -> bool:
    """Separation of duties: nobody reviews their own identity check or one they submitted."""
    return (kyc.subject_type == KycSubject.STAFF and kyc.subject_ref == str(p.user_id)) or (
        kyc.submitted_by == p.actor
    )


def authorise_subject(p: Principal, subject_type: str, subject_ref: str | None) -> str:
    """Who may submit for whom. Payers: any submitter+ for any payer ref (the host system
    vouches for it). Staff: yourself, or an admin for someone else. Org: admins only."""
    if subject_type not in {s.value for s in KycSubject}:
        raise KycError("BAD_SUBJECT", "subject_type must be payer, staff or org", 422)
    if subject_type == KycSubject.PAYER:
        if not subject_ref or not subject_ref.strip():
            raise KycError("BAD_SUBJECT", "subject_ref is required for a payer", 422)
        return subject_ref.strip()[:120]
    if subject_type == KycSubject.STAFF:
        if subject_ref and subject_ref != str(p.user_id) and not p.at_least(Role.ADMIN):
            raise KycError("FORBIDDEN", "you may only verify yourself", 403)
        return (subject_ref or str(p.user_id))[:120]
    if not p.at_least(Role.ADMIN):
        raise KycError("FORBIDDEN", "organisation verification needs the admin role", 403)
    return (subject_ref or str(p.org_id))[:120]


# --- intake ------------------------------------------------------------------------------------


def create_verification(
    session: Session,
    p: Principal,
    *,
    subject_type: str,
    subject_ref: str | None,
    files: dict[str, bytes],
) -> KycVerification:
    ref = authorise_subject(p, subject_type, subject_ref)
    unknown = set(files) - set(ROLES)
    missing = [r for r in REQUIRED_ROLES if not files.get(r)]
    if unknown or missing:
        raise KycError(
            "BAD_FILES", f"send front and selfie (back optional); got {sorted(files)}", 422
        )
    cfg = load_policy(session, p.org_id).config
    since = datetime.now(UTC) - timedelta(days=1)
    attempts = session.scalar(
        select(func.count())
        .select_from(KycVerification)
        .where(
            KycVerification.org_id == p.org_id,
            KycVerification.subject_type == subject_type,
            KycVerification.subject_ref == ref,
            KycVerification.created_at >= since,
        )
    )
    if (attempts or 0) >= cfg.kyc_max_attempts_per_day:
        raise KycError("RATE_LIMITED", "too many verification attempts today", 429)

    settings = get_settings()
    kyc = KycVerification(
        org_id=p.org_id,
        subject_type=subject_type,
        subject_ref=ref,
        submitted_by=p.actor,
        state=KycState.SUBMITTED,
        fields={},
        checks=[],
        reason_codes=[],
    )
    session.add(kyc)
    session.flush()
    storage = get_storage()
    for role, data in files.items():
        scan(data)  # raises InfectedFile / ScannerUnavailable; nothing is parsed before the scan
        prepared = prepare(data, settings.max_upload_bytes)
        ext = IMAGE_TYPES.get(prepared.content_type)
        if ext is None:
            raise RejectedFile(
                "IMAGE_REQUIRED", "identity documents must be photos (PNG/JPEG/WEBP/HEIC)"
            )
        key = f"kyc/{p.org_id}/{kyc.kyc_id}/{role}.{ext}"
        storage.put(key, data, prepared.content_type)
        session.add(
            KycEvidence(
                kyc_id=kyc.kyc_id,
                org_id=p.org_id,
                role=role,
                object_key=key,
                content_type=prepared.content_type,
                size_bytes=len(data),
                sha256=prepared.sha256,
                phash_int=phash_to_int(prepared.phash),
            )
        )
    audit(
        session,
        actor=p.actor,
        action="kyc.submitted",
        entity_type="kyc",
        entity_id=kyc.kyc_id,
        org_id=p.org_id,
        after={"subject_type": subject_type, "files": sorted(files)},
        correlation_id=kyc.correlation_id,
    )
    return kyc


# --- pipeline ----------------------------------------------------------------------------------


def _evidence(session: Session, kyc: KycVerification) -> dict[str, KycEvidence]:
    rows = session.scalars(
        select(KycEvidence).where(KycEvidence.kyc_id == kyc.kyc_id, KycEvidence.purged_at.is_(None))
    )
    return {e.role: e for e in rows}


def _expiry_days(expiry: str | None) -> int | None:
    if not expiry:
        return None
    try:
        return (date.fromisoformat(expiry) - datetime.now(UTC).date()).days
    except ValueError:
        return None


def _read_all(
    cards: dict[str, detect.Card], raws: dict[str, bytes]
) -> tuple[dict[str, dict[str, FieldReading]], dict[str, str]]:
    """Run every reader over the document images. A reader that cannot run is recorded and the
    others still count; the rules fail closed when too few sources remain."""
    results: dict[str, dict[str, FieldReading]] = {}
    failures: dict[str, str] = {}
    for reader in readers.build_id_readers():
        merged: dict[str, FieldReading] = {}
        try:
            for role in ("front", "back"):
                if role in cards:
                    for name, reading in reader.read(cards[role].image, raws[role]).items():
                        merged.setdefault(name, reading)
        except ReaderUnavailable as exc:
            failures[reader.name] = str(exc)
            continue
        results[reader.name] = merged
    return results, failures


def _pick_mrz(readings: dict[str, dict[str, FieldReading]]) -> mrz_mod.MrzResult | None:
    best = None
    for data in readings.values():
        raw = data.get("mrz")
        parsed = mrz_mod.read(raw.raw) if raw and raw.raw else None
        if parsed is not None and parsed.valid:
            return parsed
        best = best or parsed
    return best


def process(session: Session, kyc_id: uuid.UUID) -> None:
    kyc = session.scalars(
        select(KycVerification).where(KycVerification.kyc_id == kyc_id).with_for_update()
    ).first()
    if kyc is None or kyc.state not in (KycState.SUBMITTED, KycState.PROCESSING):
        return
    policy = load_policy(session, kyc.org_id)
    cfg = policy.config
    if kyc.state == KycState.SUBMITTED:
        _move(session, kyc, KycState.PROCESSING, PIPELINE_ACTOR)
    kyc.pipeline_attempts += 1

    storage = get_storage()
    ev = _evidence(session, kyc)
    if not all(r in ev for r in REQUIRED_ROLES):
        raise RuntimeError("evidence missing or already purged")
    max_bytes = get_settings().max_upload_bytes
    raws = {role: storage.get(e.object_key) for role, e in ev.items()}
    images = {role: prepare(raw, max_bytes).image for role, raw in raws.items()}
    checks: list[dict[str, Any]] = []

    # 1. capture quality: problems the subject can fix go back to them, not to a reviewer
    retake: list[str] = []
    for role, img in images.items():
        cap = detect.capture_quality(img, cfg.min_image_side_px, cfg.min_sharpness)
        problems = [c for c in cap.problems if not (role == "selfie" and c == "CAPTURE_GLARE")]
        if role == "selfie":
            problems = ["SELFIE_TOO_SMALL" if c == "CAPTURE_TOO_SMALL" else c for c in problems]
        retake += problems
        checks.append(
            {
                "name": f"capture_{role}",
                "ok": not problems,
                "sharpness": cap.sharpness,
                "glare": cap.glare_fraction,
            }
        )

    # 2. find and flatten the card
    cards: dict[str, detect.Card] = {
        role: detect.find_card(images[role]) for role in ("front", "back") if role in images
    }
    checks.append(
        {
            "name": "card_detection",
            "ok": cards["front"].found,
            "method": cards["front"].method,
            "area": cards["front"].area_fraction,
        }
    )

    # 3. face match, liveness
    fm: face.FaceMatch | None = None
    face_error = None
    try:
        fm = face.match(face.build_matcher(), cards["front"].image, images["selfie"])
    except face.FaceUnavailable as exc:
        face_error = str(exc)
    if fm is not None:
        retake += [c for c in fm.problems if c in RETAKE]
    if retake:
        _needs_information(session, kyc, policy.version, sorted(set(retake)), checks)
        return

    live = liveness.build_checker().check(images["selfie"], fm.selfie_face if fm else None)
    checks.append(
        {
            "name": "face_match",
            "ok": fm is not None and fm.score is not None,
            "score": fm.score if fm else None,
            "error": face_error,
        }
    )
    checks.append(
        {
            "name": "liveness",
            "ok": live.score >= cfg.kyc_min_liveness,
            "score": live.score,
            "signals": live.signals,
        }
    )

    # 4. read the document: readers -> MRZ -> agreement
    readings, reader_failures = _read_all(cards, raws)
    mrz = _pick_mrz(readings)
    reading = agreement.combine(readings, mrz)
    doc_type = classify.classify(mrz, [reading.fields.get("doc_type")])
    country = reading.fields.get("issuing_country") or (
        templates.normalise_country(mrz.issuing_country) if mrz else None
    )
    number = reading.fields.get("document_number")
    expiry = reading.fields.get("expiry_date")
    checks.append(
        {
            "name": "readers",
            "ok": bool(readings),
            "sources": sorted(readings),
            "failures": reader_failures,
        }
    )
    checks.append(
        {"name": "mrz", "ok": mrz.valid if mrz else None, "checks": mrz.checks if mrz else None}
    )
    checks.append(
        {
            "name": "agreement",
            "ok": reading.agreement >= cfg.kyc_min_ocr_agreement,
            "score": reading.agreement,
            "conflicts": reading.conflicts,
        }
    )

    # 5. integrity: tamper, reused images, the same ID on two people
    tamper_score, tamper_signals = docintegrity.tamper_score(cards["front"].image)
    checks.append(
        {
            "name": "tamper",
            "ok": tamper_score is not None and tamper_score < cfg.tamper_review_score,
            "score": tamper_score,
            "signals": tamper_signals,
        }
    )
    kyc.doc_type, kyc.issuing_country = doc_type, country
    kyc.doc_number_hash = docintegrity.doc_number_hash(country, number)
    session.flush()
    flags = docintegrity.portrait_copy_flag(
        fm.portrait_phash_source if fm else None, fm.selfie_face if fm else None
    )
    flags += docintegrity.duplicate_flags(
        session, kyc, own_hashes={r: e.sha256 for r, e in ev.items()}
    )
    if fm is not None and "MULTIPLE_FACES_IN_SELFIE" in fm.problems:
        flags.append("MULTIPLE_FACES_IN_SELFIE")
    checks.append({"name": "integrity", "ok": not flags, "flags": flags})

    kyc.fields = {"values": reading.fields, "agreed": reading.agreed, "sources": reading.sources}
    kyc.checks = checks
    kyc.ocr_agreement = reading.agreement
    kyc.mrz_valid = mrz.valid if mrz else None
    kyc.face_score = fm.score if fm else None
    kyc.liveness_score = live.score
    inputs = Inputs(
        subject_type=kyc.subject_type,
        doc_type=doc_type,
        issuing_country=country,
        mrz_valid=kyc.mrz_valid,
        ocr_agreement=reading.agreement,
        conflicts=reading.conflicts,
        days_to_expiry=_expiry_days(expiry),
        number_format_ok=templates.number_format_ok(country, doc_type, number),
        face_score=kyc.face_score,
        liveness_score=live.score,
        tamper_score=tamper_score,
        integrity_flags=flags,
        readers_ok=bool(readings),
    )
    snapshot = {
        "inputs": inputs.__dict__,
        "checks": [{k: v for k, v in c.items() if k != "checks"} for c in checks],
    }
    if auto.maybe_auto_verify(session, kyc, inputs, snapshot):
        return
    _move(session, kyc, KycState.NEEDS_REVIEW, PIPELINE_ACTOR)
    record_decision(
        session,
        kyc,
        KycState.NEEDS_REVIEW,
        list(kyc.reason_codes),
        policy.version,
        "rule",
        PIPELINE_ACTOR,
        snapshot,
        "left for a person",
    )


def _needs_information(
    session: Session,
    kyc: KycVerification,
    policy_version: str,
    reasons: list[str],
    checks: list[dict[str, Any]],
) -> None:
    kyc.checks = checks
    kyc.reason_codes = reasons
    _move(session, kyc, KycState.NEEDS_INFORMATION, PIPELINE_ACTOR)
    record_decision(
        session,
        kyc,
        KycState.NEEDS_INFORMATION,
        reasons,
        policy_version,
        "rule",
        PIPELINE_ACTOR,
        {"checks": checks},
        "capture could not be used",
    )


def record_failure(session: Session, kyc_id: uuid.UUID, error: str) -> None:
    """A crashed attempt is counted; after the limit a person takes over (fail closed)."""
    kyc = session.scalars(
        select(KycVerification).where(KycVerification.kyc_id == kyc_id).with_for_update()
    ).first()
    if kyc is None or kyc.state not in (KycState.SUBMITTED, KycState.PROCESSING):
        return
    kyc.pipeline_attempts += 1
    kyc.last_pipeline_error = error[:2000]
    if kyc.pipeline_attempts >= get_settings().pipeline_max_attempts:
        if kyc.state == KycState.SUBMITTED:
            _move(session, kyc, KycState.PROCESSING, PIPELINE_ACTOR)
        _move(session, kyc, KycState.NEEDS_REVIEW, PIPELINE_ACTOR)
        kyc.reason_codes = ["PIPELINE_FAILED"]
        record_decision(
            session,
            kyc,
            KycState.NEEDS_REVIEW,
            ["PIPELINE_FAILED"],
            load_policy(session, kyc.org_id).version,
            "rule",
            PIPELINE_ACTOR,
            {"error": error[:500]},
            "processing failed repeatedly",
        )


def stuck(session: Session, older_than_seconds: int | None = None) -> list[uuid.UUID]:
    """Verifications whose processing task was lost; the sweeper re-dispatches them."""
    cutoff = datetime.now(UTC) - timedelta(
        seconds=older_than_seconds or get_settings().stuck_after_seconds
    )
    return list(
        session.scalars(
            select(KycVerification.kyc_id).where(
                KycVerification.state.in_([KycState.SUBMITTED, KycState.PROCESSING]),
                KycVerification.updated_at < cutoff,
            )
        )
    )


# --- reviewers ---------------------------------------------------------------------------------


def _locked(session: Session, p: Principal, kyc_id: uuid.UUID) -> KycVerification:
    kyc = session.scalars(
        select(KycVerification)
        .where(KycVerification.kyc_id == kyc_id, KycVerification.org_id == p.org_id)
        .with_for_update()
    ).first()
    if kyc is None:
        raise KycError("NOT_FOUND", "verification not found", 404)
    return kyc


def decide(
    session: Session, p: Principal, kyc_id: uuid.UUID, outcome: str, note: str | None
) -> KycVerification:
    """A named reviewer's decision. Rejecting, revoking or asking for a retake needs a note."""
    if outcome not in states.REVIEWER_OUTCOMES:
        raise KycError("BAD_OUTCOME", f"outcome must be one of {sorted(states.REVIEWER_OUTCOMES)}")
    kyc = _locked(session, p, kyc_id)
    if is_own_identity(p, kyc):
        raise KycError("SELF_REVIEW", "you cannot review your own identity check", 403)
    if kyc.subject_type != KycSubject.PAYER and not p.at_least(Role.APPROVER):
        raise KycError("FORBIDDEN", "staff and organisation checks need the approver role", 403)
    revoking = kyc.state == KycState.VERIFIED and outcome == KycState.REJECTED
    if kyc.state not in states.REVIEWABLE and not revoking:
        raise KycError("NOT_REVIEWABLE", f"a {kyc.state} verification cannot be decided", 409)
    if outcome != KycState.VERIFIED and not (note and note.strip()):
        raise KycError("NOTE_REQUIRED", "give a reason", 422)
    policy = load_policy(session, kyc.org_id)
    if outcome == KycState.VERIFIED:
        set_verified(
            session,
            kyc,
            policy.config,
            policy.version,
            "reviewer",
            p.actor,
            {"reviewed_at": datetime.now(UTC).isoformat()},
            note=note,
            reasons=["REVIEWER_VERIFIED"],
        )
    else:
        _move(session, kyc, outcome, p.actor)
        reasons = ["REVOKED"] if revoking else [f"REVIEWER_{outcome}"]
        kyc.reason_codes = reasons
        record_decision(
            session,
            kyc,
            outcome,
            reasons,
            policy.version,
            "reviewer",
            p.actor,
            {"reviewed_at": datetime.now(UTC).isoformat()},
            note,
        )
    return kyc


# --- queries -----------------------------------------------------------------------------------


def latest(
    session: Session, org_id: uuid.UUID, subject_type: str, subject_ref: str
) -> KycVerification | None:
    return session.scalars(
        select(KycVerification)
        .where(
            KycVerification.org_id == org_id,
            KycVerification.subject_type == subject_type,
            KycVerification.subject_ref == subject_ref,
        )
        .order_by(KycVerification.created_at.desc())
        .limit(1)
    ).first()


def is_verified(session: Session, org_id: uuid.UUID, subject_type: str, subject_ref: str) -> bool:
    """True while the subject's most recent verification is VERIFIED and unexpired. A later
    NEEDS_REVIEW or REJECTED attempt does not undo an earlier valid verification, but a
    REJECTED (revoked) latest one does."""
    now = datetime.now(UTC)
    rows = session.scalars(
        select(KycVerification)
        .where(
            KycVerification.org_id == org_id,
            KycVerification.subject_type == subject_type,
            KycVerification.subject_ref == subject_ref,
            KycVerification.state.in_([KycState.VERIFIED, KycState.REJECTED]),
        )
        .order_by(KycVerification.created_at.desc())
        .limit(1)
    ).first()
    return bool(
        rows and rows.state == KycState.VERIFIED and rows.expires_at and rows.expires_at > now
    )


def status_for(session: Session, kyc: KycVerification | None) -> dict[str, Any]:
    if kyc is None:
        return {"verified": False, "state": None}
    now = datetime.now(UTC)
    return {
        "verified": kyc.state == KycState.VERIFIED
        and bool(kyc.expires_at and kyc.expires_at > now),
        "state": kyc.state,
        "kyc_id": str(kyc.kyc_id),
        "verified_at": kyc.verified_at.isoformat() if kyc.verified_at else None,
        "expires_at": kyc.expires_at.isoformat() if kyc.expires_at else None,
    }


def subject_message(kyc: KycVerification) -> str | None:
    if kyc.state == KycState.NEEDS_INFORMATION:
        texts = [states.REASON_TEXT[c] for c in kyc.reason_codes if c in states.REASON_TEXT]
        return " ".join(texts) or "Please retake the photos and submit again."
    if kyc.state == KycState.NEEDS_REVIEW:
        return states.DEFAULT_REVIEW_TEXT
    if kyc.state == KycState.REJECTED:
        return "We could not verify your identity. Contact support if you think this is wrong."
    return None


# --- maintenance (Celery beat) -------------------------------------------------------------------


def expire_due(session: Session) -> int:
    """Verifications past their validity become EXPIRED (one decision each)."""
    now = datetime.now(UTC)
    due = session.scalars(
        select(KycVerification)
        .where(KycVerification.state == KycState.VERIFIED, KycVerification.expires_at <= now)
        .with_for_update(skip_locked=True)
    ).all()
    for kyc in due:
        _move(session, kyc, KycState.EXPIRED, "rule:kyc-expiry")
        record_decision(
            session,
            kyc,
            KycState.EXPIRED,
            ["VALIDITY_ENDED"],
            load_policy(session, kyc.org_id).version,
            "rule",
            "rule:kyc-expiry",
            {},
            None,
        )
    return len(due)


def purge_evidence(session: Session) -> int:
    """Delete the image bytes of finished verifications once the retention window passes. The
    rows stay (with `purged_at`) so the audit trail still shows what was held, and for how long."""
    now = datetime.now(UTC)
    storage = get_storage()
    purged = 0
    org_ids = session.scalars(
        select(KycEvidence.org_id).where(KycEvidence.purged_at.is_(None)).distinct()
    )
    for org_id in list(org_ids):
        days = load_policy(session, org_id).config.kyc_retention_days
        rows = session.scalars(
            select(KycEvidence)
            .join(KycVerification, KycVerification.kyc_id == KycEvidence.kyc_id)
            .where(
                KycEvidence.org_id == org_id,
                KycEvidence.purged_at.is_(None),
                KycEvidence.received_at < now - timedelta(days=days),
                KycVerification.state.in_(
                    [
                        KycState.VERIFIED,
                        KycState.REJECTED,
                        KycState.EXPIRED,
                        KycState.NEEDS_INFORMATION,
                    ]
                ),
            )
        ).all()
        for e in rows:
            storage.delete(e.object_key)
            e.purged_at = now
            audit(
                session,
                actor="rule:kyc-retention",
                action="kyc.evidence_purged",
                entity_type="kyc",
                entity_id=e.kyc_id,
                org_id=org_id,
                after={"role": e.role},
            )
            purged += 1
    return purged
