import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.claims.service import transition
from app.core.audit import audit
from app.core.config import get_settings
from app.core.security import Principal
from app.intake.files import DOCX, EML, TEXT, RejectedFile, phash_to_int, prepare
from app.intake.scan import InfectedFile, scan
from app.intake.storage import get_storage
from app.models import Claim, ClaimState, Evidence

# one payment, several proofs: e.g. the bank app screenshot plus the confirmation SMS
MAX_FILES_PER_SUBMISSION = 5


def store_evidence(
    session: Session,
    claim: Claim,
    data: bytes,
    source_type: str,
    submission_id: uuid.UUID | None = None,
    index: int = 0,
) -> Evidence:
    prepared = prepare(data, get_settings().max_upload_bytes)
    prepared.meta["submission_id"] = str(submission_id or uuid.uuid4())
    prepared.meta["submission_index"] = index
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
        TEXT: "txt",
        EML: "eml",
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
    data: bytes | list[bytes],
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
    evs = store_submission(session, claim, data, source_type)
    audit(
        session,
        actor=p.actor,
        action="claim.received",
        entity_type="claim",
        entity_id=claim.claim_id,
        org_id=p.org_id,
        after={
            "evidence_ids": [str(e.evidence_id) for e in evs],
            "sha256": [e.sha256 for e in evs],
            "channel": channel,
        },
        correlation_id=claim.correlation_id,
    )
    return claim


def store_submission(
    session: Session, claim: Claim, data: bytes | list[bytes], source_type: str
) -> list[Evidence]:
    """Store one submission: every file in it shares a submission id and is read together."""
    files = [data] if isinstance(data, bytes) else list(data)
    if not files:
        raise RejectedFile("NO_EVIDENCE", "attach a proof of payment or paste the message text")
    if len(files) > MAX_FILES_PER_SUBMISSION:
        raise RejectedFile(
            "TOO_MANY_FILES", f"send at most {MAX_FILES_PER_SUBMISSION} files for one payment"
        )
    submission_id = uuid.uuid4()
    return [
        store_evidence(session, claim, f, source_type, submission_id, i)
        for i, f in enumerate(files)
    ]


def submission_evidence(session: Session, claim_id: uuid.UUID) -> list[Evidence]:
    """The claim's latest submission (all its files), oldest file first."""
    latest = session.scalars(
        select(Evidence).where(Evidence.claim_id == claim_id).order_by(Evidence.received_at.desc())
    ).first()
    if latest is None:
        return []
    sid = (latest.meta or {}).get("submission_id")
    if sid is None:
        return [latest]
    evs = session.scalars(
        select(Evidence).where(
            Evidence.claim_id == claim_id, Evidence.meta["submission_id"].astext == sid
        )
    ).all()
    return sorted(evs, key=lambda e: (e.meta or {}).get("submission_index", 0))


def resubmit(session: Session, p: Principal, claim: Claim, data: bytes | list[bytes]) -> Claim:
    """New evidence for a NEEDS_INFORMATION claim: store it and run the pipeline again."""
    store_submission(session, claim, data, "upload")
    transition(session, claim, ClaimState.RECEIVED, actor=p.actor, reasons=[])
    return claim
