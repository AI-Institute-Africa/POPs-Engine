"""HTTP API. Every query is scoped to the caller's organisation; the frontend is never trusted."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.schemas import (
    AllocationOut,
    BalanceOut,
    CandidateOut,
    ClaimOut,
    ConfirmIn,
    ConfirmOut,
    CorrectFieldIn,
    CreditIn,
    CreditOut,
    DecisionOut,
    DevLoginIn,
    EvidenceOut,
    FieldOut,
    ImportOut,
    ObligationIn,
    ObligationOut,
    RejectIn,
    RequestInfoIn,
    ReverseIn,
    ReviewDetailOut,
    TokenOut,
)
from app.claims.facts import build_facts
from app.claims.service import explain
from app.claims.states import OUTCOME, REVIEWABLE
from app.core import observability as obs
from app.core import ratelimit
from app.core.audit import audit
from app.core.config import get_settings
from app.core.db import get_session
from app.core.money import MoneyError, normalise_currency, parse_minor
from app.core.security import (
    Principal,
    Role,
    current_principal,
    issue_token,
    require,
    sign_evidence,
    verify_evidence_signature,
)
from app.credits.statements import ImportError_, import_statement, profiles
from app.intake.files import RejectedFile
from app.intake.scan import ScannerUnavailable
from app.intake.service import create_claim, resubmit
from app.intake.storage import get_storage
from app.ledger import service as ledger
from app.models import (
    Allocation,
    AuditEvent,
    Claim,
    ClaimState,
    Credit,
    Decision,
    Evidence,
    ExtractedField,
    Obligation,
    Organisation,
    User,
)
from app.normalise.fields import normalise_reference
from app.payment_codes.codes import find_codes, new_code
from app.policy.engine import load_policy
from app.reconcile.matching import evaluate, find_candidates
from app.review import service as review
from app.workers.tasks import dispatch_pipeline, dispatch_rematch

router = APIRouter()
DB = Annotated[Session, Depends(get_session)]
Any_ = Annotated[Principal, Depends(current_principal)]
Reviewer = Annotated[Principal, Depends(require(Role.REVIEWER))]
Approver = Annotated[Principal, Depends(require(Role.APPROVER))]
Admin = Annotated[Principal, Depends(require(Role.ADMIN))]


# never shown to submitters (Cognify, and through it payers): no accusation from a score
INTERNAL_REASONS = {"SUSPECTED_FAKE"}


def claim_out(c: Claim, internal: bool = False) -> ClaimOut:
    out = ClaimOut.model_validate(
        {
            **{k: getattr(c, k) for k in ClaimOut.model_fields if hasattr(c, k)},
            "outcome": OUTCOME[ClaimState(c.state)],
            "messages": explain(c.reason_codes),
        }
    )
    if not internal:
        out.risk_score = None
        out.risk_level = None
        out.risk_signals = []
        out.reason_codes = [r for r in out.reason_codes if r not in INTERNAL_REASONS]
    return out


def _balance_out(b: ledger.Balance) -> BalanceOut:
    return BalanceOut(
        obligation_id=b.obligation_id,
        amount_minor=b.amount_minor,
        paid_minor=b.paid_minor,
        outstanding_minor=b.outstanding_minor,
        currency=b.currency,
        payment_status=b.status,
    )


def _claim(db: Session, p: Principal, claim_id: uuid.UUID) -> Claim:
    c = db.scalars(
        select(Claim).where(Claim.claim_id == claim_id, Claim.org_id == p.org_id)
    ).first()
    if c is None:
        raise HTTPException(404, "claim not found")
    return c


def _obligation(db: Session, p: Principal, obligation_id: uuid.UUID) -> Obligation:
    o = db.scalars(
        select(Obligation).where(
            Obligation.obligation_id == obligation_id, Obligation.org_id == p.org_id
        )
    ).first()
    if o is None:
        raise HTTPException(404, "obligation not found")
    return o


def _review_error(exc: review.ReviewError) -> HTTPException:
    return HTTPException(exc.status, {"code": exc.code, "message": str(exc)})


def _limit_uploads(p: Principal, claimant_ref: str) -> None:
    s = get_settings()
    try:
        ratelimit.check("uploads", str(p.user_id), s.rate_limit_uploads_per_minute, 60)
        ratelimit.check(
            "claimant",
            f"{p.org_id}:{claimant_ref}",
            s.rate_limit_claims_per_claimant_per_day,
            86400,
        )
    except ratelimit.RateLimited as exc:
        obs.UPLOADS_REJECTED.labels(f"RATE_LIMIT_{exc.scope.upper()}").inc()
        raise HTTPException(
            429,
            {"code": "RATE_LIMITED", "message": "too many uploads; slow down"},
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc


# --- auth -------------------------------------------------------------------------------------


@router.post("/auth/dev-login", response_model=TokenOut, tags=["auth"])
def dev_login(body: DevLoginIn, db: DB) -> TokenOut:
    """Development only: issue a token for a user, creating the org/user if needed."""
    if not get_settings().is_dev:
        raise HTTPException(404)
    role = Role(body.role)
    user = db.scalars(select(User).where(User.email == body.email)).first()
    if user is None:
        org = db.scalars(select(Organisation).where(Organisation.name == body.org_name)).first()
        if org is None:
            org = Organisation(name=body.org_name)
            db.add(org)
            db.flush()
        user = User(org_id=org.org_id, email=body.email, role=role.value)
        db.add(user)
        db.commit()
    token = issue_token(user.user_id, user.org_id, Role(user.role), user.email)
    return TokenOut(access_token=token, role=user.role, org_id=user.org_id)


@router.get("/me", tags=["auth"])
def me(p: Any_) -> dict:
    return {"user_id": p.user_id, "org_id": p.org_id, "role": p.role, "email": p.email}


# --- obligations ------------------------------------------------------------------------------


@router.post("/obligations", response_model=ObligationOut, status_code=201, tags=["obligations"])
def create_obligation(body: ObligationIn, db: DB, p: Any_) -> Obligation:
    currency = normalise_currency(body.currency)
    if currency is None:
        raise HTTPException(422, "unsupported currency")
    # 32^6 codes; a collision is caught by the unique constraint and surfaces as a 409 retry
    ob = Obligation(
        org_id=p.org_id,
        learner_ref=body.learner_ref,
        type=body.type.value,
        description=body.description,
        amount_minor=body.amount_minor,
        currency=currency,
        payment_code=new_code(),
        external_ref=body.external_ref,
        due_date=body.due_date,
    )
    db.add(ob)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            409, "obligation already exists (external_ref or code clash); retry"
        ) from exc
    audit(
        db,
        actor=p.actor,
        action="obligation.created",
        entity_type="obligation",
        entity_id=ob.obligation_id,
        org_id=p.org_id,
        after={
            "amount_minor": ob.amount_minor,
            "currency": ob.currency,
            "payment_code": ob.payment_code,
        },
    )
    db.commit()
    return ob


@router.get("/obligations", response_model=list[ObligationOut], tags=["obligations"])
def list_obligations(
    db: DB, p: Any_, learner_ref: str | None = None, limit: int = Query(100, le=500)
) -> list[Obligation]:
    q = select(Obligation).where(Obligation.org_id == p.org_id)
    if learner_ref:
        q = q.where(Obligation.learner_ref == learner_ref)
    return list(db.scalars(q.order_by(Obligation.created_at.desc()).limit(limit)))


@router.get("/obligations/{obligation_id}/balance", response_model=BalanceOut, tags=["obligations"])
def obligation_balance(obligation_id: uuid.UUID, db: DB, p: Any_) -> BalanceOut:
    return _balance_out(ledger.balance(db, _obligation(db, p, obligation_id)))


# --- claims -----------------------------------------------------------------------------------


@router.post("/claims", response_model=ClaimOut, status_code=201, tags=["claims"])
async def submit_claim(
    db: DB,
    p: Any_,
    obligation_id: Annotated[uuid.UUID, Form()],
    claimant_ref: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    channel: Annotated[str, Form()] = "unknown",
    declared_amount: Annotated[str | None, Form()] = None,
    declared_currency: Annotated[str | None, Form()] = None,
    declared_reference: Annotated[str | None, Form()] = None,
) -> ClaimOut:
    obligation = _obligation(db, p, obligation_id)
    _limit_uploads(p, claimant_ref)
    currency = normalise_currency(declared_currency) if declared_currency else obligation.currency
    if currency is None:
        raise HTTPException(422, "unsupported currency")
    amount = None
    if declared_amount:
        try:
            amount = parse_minor(declared_amount, currency)
        except MoneyError as exc:
            raise HTTPException(422, str(exc)) from exc
    data = await file.read(get_settings().max_upload_bytes + 1)
    try:
        claim = create_claim(
            db,
            p,
            obligation_id=obligation_id,
            claimant_ref=claimant_ref,
            channel=channel,
            data=data,
            declared_amount_minor=amount,
            declared_currency=currency,
            declared_reference=declared_reference,
        )
    except RejectedFile as exc:
        obs.UPLOADS_REJECTED.labels(exc.code).inc()
        raise HTTPException(422, {"code": exc.code, "message": str(exc)}) from exc
    except ScannerUnavailable as exc:
        obs.UPLOADS_REJECTED.labels("SCANNER_UNAVAILABLE").inc()
        raise HTTPException(
            503,
            {
                "code": "SCANNER_UNAVAILABLE",
                "message": "upload could not be scanned; try again shortly",
            },
            headers={"Retry-After": "30"},
        ) from exc
    db.commit()
    dispatch_pipeline(claim.claim_id)
    db.expire_all()
    return claim_out(_claim(db, p, claim.claim_id), p.at_least(Role.REVIEWER))


@router.get("/claims", response_model=list[ClaimOut], tags=["claims"])
def list_claims(
    db: DB,
    p: Any_,
    obligation_id: uuid.UUID | None = None,
    claimant_ref: str | None = None,
    limit: int = Query(100, le=500),
) -> list[ClaimOut]:
    q = select(Claim).where(Claim.org_id == p.org_id)
    if obligation_id:
        q = q.where(Claim.obligation_id == obligation_id)
    if claimant_ref:
        q = q.where(Claim.claimant_ref == claimant_ref)
    internal = p.at_least(Role.REVIEWER)
    return [
        claim_out(c, internal) for c in db.scalars(q.order_by(Claim.created_at.desc()).limit(limit))
    ]


@router.get("/claims/{claim_id}", response_model=ClaimOut, tags=["claims"])
def get_claim(claim_id: uuid.UUID, db: DB, p: Any_) -> ClaimOut:
    return claim_out(_claim(db, p, claim_id), p.at_least(Role.REVIEWER))


@router.post("/claims/{claim_id}/evidence", response_model=ClaimOut, tags=["claims"])
async def add_evidence(
    claim_id: uuid.UUID, db: DB, p: Any_, file: Annotated[UploadFile, File()]
) -> ClaimOut:
    claim = _claim(db, p, claim_id)
    if claim.state != ClaimState.NEEDS_INFORMATION:
        raise HTTPException(409, "evidence can only be added when information was requested")
    _limit_uploads(p, claim.claimant_ref)
    data = await file.read(get_settings().max_upload_bytes + 1)
    try:
        resubmit(db, p, claim, data)
    except RejectedFile as exc:
        obs.UPLOADS_REJECTED.labels(exc.code).inc()
        raise HTTPException(422, {"code": exc.code, "message": str(exc)}) from exc
    except ScannerUnavailable as exc:
        raise HTTPException(
            503,
            {
                "code": "SCANNER_UNAVAILABLE",
                "message": "upload could not be scanned; try again shortly",
            },
            headers={"Retry-After": "30"},
        ) from exc
    db.commit()
    dispatch_pipeline(claim.claim_id)
    db.expire_all()
    return claim_out(_claim(db, p, claim_id), p.at_least(Role.REVIEWER))


@router.get("/evidence/{evidence_id}/content", tags=["claims"])
def evidence_content(
    evidence_id: uuid.UUID,
    sig: str,
    db: DB,
    variant: str = Query("preview", pattern="^(preview|original|forensics)$"),
) -> Response:
    """Short-lived signed URL; usable from <img> tags without a bearer token."""
    if not verify_evidence_signature(evidence_id, sig):
        raise HTTPException(403, "link expired or invalid")
    ev = db.get(Evidence, evidence_id)
    if ev is None:
        raise HTTPException(404)
    if variant == "forensics" and ev.forensics_key:
        key, ctype = ev.forensics_key, "image/png"
    elif variant == "preview" and ev.preview_key:
        key, ctype = ev.preview_key, "image/png"
    else:
        key, ctype = ev.object_key, ev.content_type
    return Response(
        get_storage().get(key),
        media_type=ctype,
        headers={
            "Cache-Control": "private, max-age=60",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
        },
    )


# --- credits ----------------------------------------------------------------------------------


def _credit_out(db: Session, c: Credit) -> CreditOut:
    out = CreditOut.model_validate(c)
    out.available_minor = ledger.credit_available(db, c)
    return out


@router.post("/credits/imports", response_model=ImportOut, status_code=201, tags=["credits"])
async def import_credits(
    db: DB,
    p: Reviewer,
    file: Annotated[UploadFile, File()],
    account_alias: Annotated[str, Form()],
    profile: Annotated[str, Form()] = "generic",
) -> ImportOut:
    prof = profiles().get(profile)
    if prof is None:
        raise HTTPException(422, f"unknown profile; use one of {sorted(profiles())}")
    data = await file.read(20 * 1024 * 1024)
    try:
        result = import_statement(
            db, org_id=p.org_id, data=data, profile=prof, account_alias=account_alias, actor=p.actor
        )
    except ImportError_ as exc:
        raise HTTPException(
            409 if exc.code == "DUPLICATE_IMPORT" else 422, {"code": exc.code, "message": str(exc)}
        ) from exc
    db.commit()
    before = _confirmed_count(db, p.org_id)
    ran_now = dispatch_rematch(p.org_id)
    db.expire_all()
    imp = result.statement_import
    return ImportOut(
        import_id=imp.import_id,
        rows=imp.row_count,
        created=len(result.created),
        duplicates=result.duplicates,
        errors=result.errors,
        control_totals=imp.control_totals,
        # with background workers the re-match is queued, so the count is not known yet
        rematched_claims=_confirmed_count(db, p.org_id) - before if ran_now else None,
    )


def _confirmed_count(db: Session, org_id: uuid.UUID) -> int:
    return len(
        db.scalars(
            select(Claim.claim_id).where(
                Claim.org_id == org_id, Claim.state == ClaimState.FINANCIALLY_CONFIRMED
            )
        ).all()
    )


@router.post("/credits", response_model=CreditOut, status_code=201, tags=["credits"])
def manual_credit(body: CreditIn, db: DB, p: Reviewer) -> CreditOut:
    min_role = Role(load_policy(db, p.org_id).config.manual_credit_min_role)
    if not p.at_least(min_role):
        raise HTTPException(403, f"recording manual credits requires role {min_role.value}")
    currency = normalise_currency(body.currency)
    if currency is None:
        raise HTTPException(422, "unsupported currency")
    credit = Credit(
        org_id=p.org_id,
        source="manual",
        provider=body.provider.upper(),
        provider_txn_id=body.provider_txn_id,
        reference_norm=normalise_reference(body.provider_txn_id) or body.provider_txn_id,
        amount_minor=body.amount_minor,
        currency=currency,
        occurred_at=body.occurred_at,
        status="success",
        beneficiary=body.beneficiary,
        payer=body.payer,
        narrative=body.narrative,
        created_by=p.actor,
        attestation=body.attestation,
    )
    db.add(credit)
    try:
        db.flush()
    except IntegrityError as exc:
        raise HTTPException(409, "a credit with this provider and transaction id exists") from exc
    audit(
        db,
        actor=p.actor,
        action="credit.manual_attested",
        entity_type="credit",
        entity_id=credit.credit_id,
        org_id=p.org_id,
        after={"amount_minor": credit.amount_minor, "attestation": body.attestation},
    )
    db.commit()
    dispatch_rematch(p.org_id)
    return _credit_out(db, credit)


@router.get("/credits", response_model=list[CreditOut], tags=["credits"])
def list_credits(
    db: DB, p: Reviewer, unallocated: bool = False, limit: int = Query(200, le=1000)
) -> list[CreditOut]:
    rows = db.scalars(
        select(Credit)
        .where(Credit.org_id == p.org_id)
        .order_by(Credit.occurred_at.desc())
        .limit(limit)
    ).all()
    out = [_credit_out(db, c) for c in rows]
    return [c for c in out if c.available_minor] if unallocated else out


@router.get("/credits/unclaimed", tags=["credits"])
def unclaimed_credits(db: DB, p: Reviewer) -> list[dict]:
    """Credits with unallocated money and no claim pointing at them, with code-based suggestions."""
    claimed = set(
        db.scalars(
            select(Claim.candidate_credit_id).where(
                Claim.org_id == p.org_id, Claim.candidate_credit_id.is_not(None)
            )
        ).all()
    )
    out = []
    for c in db.scalars(
        select(Credit).where(Credit.org_id == p.org_id).order_by(Credit.occurred_at)
    ).all():
        if c.credit_id in claimed:
            continue
        available = ledger.credit_available(db, c)
        if available <= 0:
            continue
        codes = find_codes(c.narrative)
        suggestions = (
            db.scalars(
                select(Obligation).where(
                    Obligation.org_id == p.org_id, Obligation.payment_code.in_(codes)
                )
            ).all()
            if codes
            else []
        )
        out.append(
            {
                "credit": _credit_out(db, c).model_dump(mode="json"),
                "suggested_obligations": [
                    ObligationOut.model_validate(o).model_dump(mode="json") for o in suggestions
                ],
            }
        )
    return out


# --- review -----------------------------------------------------------------------------------


@router.get("/review/queue", response_model=list[ClaimOut], tags=["review"])
def review_queue(
    db: DB,
    p: Reviewer,
    state: str | None = None,
    channel: str | None = None,
    limit: int = Query(200, le=1000),
) -> list[ClaimOut]:
    states = [state] if state else [s.value for s in REVIEWABLE]
    q = select(Claim).where(Claim.org_id == p.org_id, Claim.state.in_(states))
    if channel:
        q = q.where(Claim.channel == channel)
    # oldest first: the queue is worked against an SLA
    return [claim_out(c, True) for c in db.scalars(q.order_by(Claim.created_at).limit(limit))]


@router.get("/review/claims/{claim_id}", response_model=ReviewDetailOut, tags=["review"])
def review_detail(claim_id: uuid.UUID, db: DB, p: Reviewer) -> ReviewDetailOut:
    claim = _claim(db, p, claim_id)
    obligation = _obligation(db, p, claim.obligation_id)
    policy = load_policy(db, claim.org_id)
    facts, _ = build_facts(db, claim, obligation)
    cands = find_candidates(db, facts, obligation, policy.config)
    if claim.candidate_credit_id and all(c.credit_id != claim.candidate_credit_id for c in cands):
        credit = db.get(Credit, claim.candidate_credit_id)
        if credit:
            cands.insert(0, evaluate(db, facts, obligation, credit, policy.config))
    ids = [c.credit_id for c in cands]
    credits = {c.credit_id: c for c in db.scalars(select(Credit).where(Credit.credit_id.in_(ids)))}
    evidence = db.scalars(
        select(Evidence).where(Evidence.claim_id == claim_id).order_by(Evidence.received_at.desc())
    ).all()
    fields = db.scalars(
        select(ExtractedField)
        .where(ExtractedField.claim_id == claim_id)
        .order_by(ExtractedField.created_at)
    ).all()
    decisions = db.scalars(
        select(Decision).where(Decision.claim_id == claim_id).order_by(Decision.created_at)
    ).all()
    allocations = db.scalars(
        select(Allocation).where(Allocation.claim_id == claim_id).order_by(Allocation.created_at)
    ).all()
    shas = [e.sha256 for e in evidence]
    related = set(
        db.scalars(
            select(Evidence.claim_id).where(
                Evidence.sha256.in_(shas),
                Evidence.claim_id != claim_id,
                Evidence.org_id == p.org_id,
            )
        ).all()
    )
    return ReviewDetailOut(
        claim=claim_out(claim, True),
        obligation=ObligationOut.model_validate(obligation),
        balance=_balance_out(ledger.balance(db, obligation)),
        evidence=[
            EvidenceOut(
                evidence_id=e.evidence_id,
                content_type=e.content_type,
                sha256=e.sha256,
                meta=e.meta,
                received_at=e.received_at,
                preview_url=f"/evidence/{e.evidence_id}/content?variant=preview&sig={sign_evidence(e.evidence_id)}",
                original_url=f"/evidence/{e.evidence_id}/content?variant=original&sig={sign_evidence(e.evidence_id)}",
                forensics_url=(
                    f"/evidence/{e.evidence_id}/content?variant=forensics"
                    f"&sig={sign_evidence(e.evidence_id)}"
                    if e.forensics_key
                    else None
                ),
            )
            for e in evidence
        ],
        fields=[FieldOut.model_validate(f) for f in fields],
        candidates=[
            CandidateOut(
                credit=_credit_out(db, credits[c.credit_id]),
                passed=c.passed,
                score=c.score,
                gates=[g.__dict__ for g in c.gates],
                components=c.components,
                reference_kind=c.reference_kind,
            )
            for c in cands
        ],
        decisions=[DecisionOut.model_validate(d) for d in decisions],
        allocations=[AllocationOut.model_validate(a) for a in allocations],
        related_claims=sorted(related),
    )


@router.post("/review/claims/{claim_id}/confirm", response_model=ConfirmOut, tags=["review"])
def confirm_claim(claim_id: uuid.UUID, body: ConfirmIn, db: DB, p: Reviewer) -> ConfirmOut:
    try:
        result = review.confirm(db, p, claim_id, body.credit_id, body.txn_last4, body.note)
    except review.ReviewError as exc:
        db.rollback()
        raise _review_error(exc) from exc
    except ledger.LedgerError as exc:
        db.rollback()
        raise HTTPException(409, {"code": exc.code, "message": str(exc)}) from exc
    db.commit()
    return ConfirmOut(
        claim=claim_out(result.claim, True),
        allocation=AllocationOut.model_validate(result.allocation) if result.allocation else None,
        needs_second_approval=result.needs_second_approval,
    )


@router.post("/review/claims/{claim_id}/reject", response_model=ClaimOut, tags=["review"])
def reject_claim(claim_id: uuid.UUID, body: RejectIn, db: DB, p: Reviewer) -> ClaimOut:
    try:
        claim = review.reject(db, p, claim_id, body.reason, body.note)
    except review.ReviewError as exc:
        db.rollback()
        raise _review_error(exc) from exc
    db.commit()
    return claim_out(claim, True)


@router.post("/review/claims/{claim_id}/request-info", response_model=ClaimOut, tags=["review"])
def request_info(claim_id: uuid.UUID, body: RequestInfoIn, db: DB, p: Reviewer) -> ClaimOut:
    try:
        claim = review.request_info(db, p, claim_id, body.message)
    except review.ReviewError as exc:
        db.rollback()
        raise _review_error(exc) from exc
    db.commit()
    return claim_out(claim, True)


@router.post("/review/claims/{claim_id}/fields", response_model=FieldOut, tags=["review"])
def correct_field(claim_id: uuid.UUID, body: CorrectFieldIn, db: DB, p: Reviewer) -> FieldOut:
    try:
        row = review.correct_field(db, p, claim_id, body.name, body.value, body.reason)
    except review.ReviewError as exc:
        db.rollback()
        raise _review_error(exc) from exc
    db.commit()
    return FieldOut.model_validate(row)


@router.post("/allocations/{allocation_id}/reverse", response_model=AllocationOut, tags=["ledger"])
def reverse_allocation(
    allocation_id: uuid.UUID, body: ReverseIn, db: DB, p: Approver
) -> AllocationOut:
    try:
        rev = review.reverse_allocation(db, p, allocation_id, body.reason)
    except (review.ReviewError, ledger.LedgerError) as exc:
        db.rollback()
        raise HTTPException(
            404 if exc.code == "NOT_FOUND" else 409, {"code": exc.code, "message": str(exc)}
        ) from exc
    db.commit()
    return AllocationOut.model_validate(rev)


# --- audit and policy -------------------------------------------------------------------------


@router.get("/audit", tags=["audit"])
def audit_trail(db: DB, p: Reviewer, entity_id: str, limit: int = Query(200, le=1000)) -> list:
    rows = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.org_id == p.org_id, AuditEvent.entity_id == entity_id)
        .order_by(AuditEvent.created_at)
        .limit(limit)
    ).all()
    return [
        {
            "actor": r.actor,
            "action": r.action,
            "before": r.before,
            "after": r.after,
            "created_at": r.created_at,
        }
        for r in rows
    ]


@router.get("/ops/stuck", tags=["ops"])
def ops_stuck(db: DB, p: Admin) -> list[dict]:
    """Claims the pipeline has not finished; the sweeper retries them every minute."""
    from app.workers.pipeline import stuck_claims

    return [
        {
            "claim_id": c.claim_id,
            "org_id": c.org_id,
            "attempts": c.pipeline_attempts,
            "last_error": c.last_pipeline_error,
            "updated_at": c.updated_at,
        }
        for c in stuck_claims(db)
        if c.org_id == p.org_id
    ]
