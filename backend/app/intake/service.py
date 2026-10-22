import uuid

from sqlalchemy.orm import Session

from app.claims.service import transition
from app.core.audit import audit
from app.core.config import get_settings
from app.core.security import Principal
from app.intake.files import DOCX, RejectedFile, phash_to_int, prepare
from app.intake.scan import InfectedFile, scan
from app.intake.storage import get_storage
from app.models import Claim, ClaimState, Evidence


def store_evidence(session: Session, claim: Claim, data: bytes, source_type: str) -> Evidence:
    prepared = prepare(data, get_settings().max_upload_bytes)
    # scan before anything touches storage; ScannerUnavailable propagates (fail closed)
    try:
        scan_result = scan(data)
    except InfectedFile as exc:
        raise RejectedFile("MALWARE_DETECTED", "the file failed the malware scan") from exc
    prepared.meta["malware_scan"] = scan_result
    ext = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/webp": "webp",
        "image/heic": "heic",
        "application/pdf": "pdf",
        DOCX: "docx",
    }[prepared.content_type]
    evidence_id = uuid.uuid4()
    base = f"{claim.org_id}/{claim.claim_id}/{evidence_id}"
    storage = get_storage()
    storage.put(f"{base}/original.{ext}", data, prepared.content_type)
    storage.put(f"{base}/preview.png", prepared.preview_png, "image/png")
    ev = Evidence(
        evidence_id=evidence_id,
        claim_id=claim.claim_id,
        org_id=claim.org_id,
        object_key=f"{base}/original.{ext}",
        preview_key=f"{base}/preview.png",
        content_type=prepared.content_type,
        size_bytes=len(data),
        sha256=prepared.sha256,
        phash=prepared.phash,
        phash_int=phash_to_int(prepared.phash),
        meta=prepared.meta,
        source_type=source_type,
    )
    session.add(ev)
    return ev


def create_claim(
    session: Session,
    p: Principal,
    *,
    obligation_id: uuid.UUID,
    claimant_ref: str,
    channel: str,
    data: bytes,
    declared_amount_minor: int | None,
    declared_currency: str | None,
    declared_reference: str | None,
    source_type: str = "upload",
) -> Claim:
    claim = Claim(
        claim_id=uuid.uuid4(),
        org_id=p.org_id,
        obligation_id=obligation_id,
        claimant_ref=claimant_ref,
        submitted_by=p.actor,
        channel=channel,
        declared_amount_minor=declared_amount_minor,
        declared_currency=declared_currency,
        declared_reference=declared_reference,
        state=ClaimState.RECEIVED,
        reason_codes=[],
    )
    session.add(claim)
    session.flush()
    ev = store_evidence(session, claim, data, source_type)
    audit(
        session,
        actor=p.actor,
        action="claim.received",
        entity_type="claim",
        entity_id=claim.claim_id,
        org_id=p.org_id,
        after={"evidence_id": str(ev.evidence_id), "sha256": ev.sha256, "channel": channel},
        correlation_id=claim.correlation_id,
    )
    return claim


def resubmit(session: Session, p: Principal, claim: Claim, data: bytes) -> Claim:
    """New evidence for a NEEDS_INFORMATION claim: store it and run the pipeline again."""
    store_evidence(session, claim, data, "upload")
    transition(session, claim, ClaimState.RECEIVED, actor=p.actor, reasons=[])
    return claim
