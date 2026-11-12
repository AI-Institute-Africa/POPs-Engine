"""Identity verification (KYC) API.

Submitters (the host system or a payer-facing client) upload the ID and a selfie and read back a
status; reviewers work the queue; admins read the auto-verification audit. The verification
itself runs in the pipeline, never in the request, and no endpoint changes money.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import ratelimit
from app.core.config import get_settings
from app.core.db import get_session
from app.core.security import Principal, Role, current_principal, require
from app.intake.files import RejectedFile
from app.intake.scan import InfectedFile, ScannerUnavailable
from app.intake.storage import get_storage
from app.kyc import auto, service
from app.kyc.auto import KycError
from app.models import KycDecision, KycEvidence, KycState, KycVerification
from app.workers.tasks import dispatch_kyc

router = APIRouter(prefix="/kyc", tags=["kyc"])
DB = Annotated[Session, Depends(get_session)]
Any_ = Annotated[Principal, Depends(current_principal)]
Reviewer = Annotated[Principal, Depends(require(Role.REVIEWER))]


def _err(exc: KycError) -> HTTPException:
    return HTTPException(exc.status, {"code": exc.code, "message": str(exc)})


def _out(kyc: KycVerification, *, detail: bool, evidence: list[KycEvidence] | None = None) -> dict:
    base = {
        "kyc_id": str(kyc.kyc_id),
        "subject_type": kyc.subject_type,
        "subject_ref": kyc.subject_ref,
        "state": kyc.state,
        "message": service.subject_message(kyc),
        "created_at": kyc.created_at.isoformat(),
        "verified_at": kyc.verified_at.isoformat() if kyc.verified_at else None,
        "expires_at": kyc.expires_at.isoformat() if kyc.expires_at else None,
    }
    if not detail:
        return base
    return {
        **base,
        "doc_type": kyc.doc_type,
        "issuing_country": kyc.issuing_country,
        "fields": kyc.fields,
        "checks": kyc.checks,
        "reason_codes": kyc.reason_codes,
        "scores": {
            "ocr_agreement": kyc.ocr_agreement,
            "mrz_valid": kyc.mrz_valid,
            "face": kyc.face_score,
            "liveness": kyc.liveness_score,
        },
        "submitted_by": kyc.submitted_by,
        "evidence": [{"role": e.role, "purged": e.purged_at is not None} for e in (evidence or [])],
    }


def _limit(p: Principal, subject_ref: str) -> None:
    s = get_settings()
    try:
        ratelimit.check("kyc_uploads", str(p.user_id), s.rate_limit_uploads_per_minute, 60)
        ratelimit.check(
            "kyc_subject", f"{p.org_id}:{subject_ref}", s.rate_limit_kyc_per_subject_per_day, 86400
        )
    except ratelimit.RateLimited as exc:
        raise HTTPException(
            429,
            {"code": "RATE_LIMITED", "message": "too many verification attempts; slow down"},
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc


@router.post("/verifications", status_code=201)
async def submit(
    db: DB,
    p: Any_,
    subject_type: Annotated[str, Form()],
    front: Annotated[UploadFile, File()],
    selfie: Annotated[UploadFile, File()],
    back: Annotated[UploadFile | None, File()] = None,
    subject_ref: Annotated[str | None, Form()] = None,
) -> dict:
    """Upload the ID (front; back optional) and a selfie. Staff verify themselves by default."""
    limit = get_settings().max_upload_bytes + 1
    files = {"front": await front.read(limit), "selfie": await selfie.read(limit)}
    if back is not None:
        files["back"] = await back.read(limit)
    try:
        ref = service.authorise_subject(p, subject_type, subject_ref)
        _limit(p, ref)
        kyc = service.create_verification(
            db, p, subject_type=subject_type, subject_ref=ref, files=files
        )
    except KycError as exc:
        raise _err(exc) from exc
    except InfectedFile as exc:
        raise HTTPException(
            422, {"code": "MALWARE_DETECTED", "message": "a file failed the malware scan"}
        ) from exc
    except ScannerUnavailable as exc:
        raise HTTPException(
            503,
            {"code": "SCANNER_UNAVAILABLE", "message": "upload could not be scanned; try again"},
            headers={"Retry-After": "30"},
        ) from exc
    except RejectedFile as exc:
        raise HTTPException(422, {"code": exc.code, "message": str(exc)}) from exc
    db.commit()
    dispatch_kyc(kyc.kyc_id)
    db.expire_all()
    return _out(db.get(KycVerification, kyc.kyc_id), detail=False)  # type: ignore[arg-type]


def _get(db: Session, p: Principal, kyc_id: uuid.UUID) -> KycVerification:
    kyc = db.scalars(
        select(KycVerification).where(
            KycVerification.kyc_id == kyc_id, KycVerification.org_id == p.org_id
        )
    ).first()
    if kyc is None:
        raise HTTPException(404, "verification not found")
    return kyc


@router.get("/verifications/{kyc_id}")
def get_verification(kyc_id: uuid.UUID, db: DB, p: Any_) -> dict:
    """Reviewers see everything; a submitter sees the status of what they submitted."""
    kyc = _get(db, p, kyc_id)
    if p.at_least(Role.REVIEWER):
        ev = list(db.scalars(select(KycEvidence).where(KycEvidence.kyc_id == kyc_id)))
        return _out(kyc, detail=True, evidence=ev)
    if kyc.submitted_by != p.actor:
        raise HTTPException(404, "verification not found")
    return _out(kyc, detail=False)


@router.get("/subjects/{subject_type}/{subject_ref}/status")
def subject_status(subject_type: str, subject_ref: str, db: DB, p: Any_) -> dict:
    """Is this subject KYC-verified right now? For the host system and for the payer's app."""
    return service.status_for(db, service.latest(db, p.org_id, subject_type, subject_ref))


@router.get("/verifications/{kyc_id}/evidence/{role}")
def evidence_image(kyc_id: uuid.UUID, role: str, db: DB, p: Reviewer) -> Response:
    _get(db, p, kyc_id)
    ev = db.scalars(
        select(KycEvidence).where(KycEvidence.kyc_id == kyc_id, KycEvidence.role == role)
    ).first()
    if ev is None or ev.purged_at is not None:
        raise HTTPException(404, "image not available")
    from app.core.audit import audit

    audit(
        db,
        actor=p.actor,
        action="kyc.evidence_viewed",
        entity_type="kyc",
        entity_id=kyc_id,
        org_id=p.org_id,
        after={"role": role},
    )
    db.commit()
    return Response(
        get_storage().get(ev.object_key),
        media_type=ev.content_type,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/review/queue")
def review_queue(db: DB, p: Reviewer, limit: int = 100) -> list[dict]:
    rows = db.scalars(
        select(KycVerification)
        .where(KycVerification.org_id == p.org_id, KycVerification.state == KycState.NEEDS_REVIEW)
        .order_by(KycVerification.created_at)
        .limit(min(limit, 500))
    ).all()
    return [
        {
            **_out(k, detail=False),
            "doc_type": k.doc_type,
            "issuing_country": k.issuing_country,
            "reason_codes": k.reason_codes,
            "face_score": k.face_score,
        }
        for k in rows
    ]


@router.post("/review/{kyc_id}/decision")
def decide(kyc_id: uuid.UUID, body: dict, db: DB, p: Reviewer) -> dict:
    """body: {"outcome": "VERIFIED" | "REJECTED" | "NEEDS_INFORMATION", "note": "..."}"""
    try:
        kyc = service.decide(db, p, kyc_id, str(body.get("outcome", "")), body.get("note"))
    except KycError as exc:
        raise _err(exc) from exc
    db.commit()
    return _out(kyc, detail=False)


@router.get("/review/auto-verified")
def auto_verified(db: DB, p: Reviewer) -> dict:
    return auto.list_auto_verified(db, p.org_id)


@router.post("/review/{kyc_id}/audit-verdict")
def audit_verdict(kyc_id: uuid.UUID, body: dict, db: DB, p: Reviewer) -> dict:
    """body: {"verdict": "ok" | "overturned", "note": "..."}"""
    try:
        d: KycDecision = auto.record_verdict(
            db, p, kyc_id, str(body.get("verdict", "")), body.get("note")
        )
    except KycError as exc:
        raise _err(exc) from exc
    db.commit()
    return {"kyc_id": str(kyc_id), "outcome": d.outcome}
