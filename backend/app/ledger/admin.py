"""Admin allocation of a recorded credit to an obligation, with no payer claim involved.

For money that arrived and is on our books (statement line, alert, attested manual credit) but
that nobody has claimed yet. It keeps the same human controls as a claim confirmation: the
typed last four characters of the transaction id, separation of duties on manual credits, and
two different approvers above the policy's high-value limit. The ledger trigger re-checks every
invariant, so this can never over-allocate or mix currencies.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.claims.states import REVIEWABLE
from app.core.audit import audit
from app.core.security import Principal, Role
from app.entitlements.outbox import enqueue_entitlement
from app.ledger import service as ledger
from app.models import Allocation, AuditEvent, Claim, Credit, Obligation
from app.policy.engine import load_policy

FIRST_APPROVAL = "credit.allocation_first_approval"


class AllocationError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass
class AllocateResult:
    allocation: Allocation | None
    needs_second_approval: bool
    outstanding_minor: int
    payment_status: str


def _first_approvers(
    session: Session, p: Principal, credit_id: uuid.UUID, obligation_id: uuid.UUID, amount: int
) -> set[str]:
    rows = session.scalars(
        select(AuditEvent).where(
            AuditEvent.org_id == p.org_id,
            AuditEvent.action == FIRST_APPROVAL,
            AuditEvent.entity_type == "credit",
            AuditEvent.entity_id == str(credit_id),
        )
    ).all()
    return {
        r.actor
        for r in rows
        if (r.after or {}).get("obligation_id") == str(obligation_id)
        and (r.after or {}).get("amount_minor") == amount
    }


def allocate_credit(
    session: Session,
    p: Principal,
    *,
    credit_id: uuid.UUID,
    obligation_id: uuid.UUID,
    amount_minor: int | None,
    txn_last4: str,
    note: str | None,
    request_id: uuid.UUID | None = None,
) -> AllocateResult:
    policy = load_policy(session, p.org_id)
    min_role = Role(policy.config.manual_allocation_min_role)
    if not p.at_least(min_role):
        raise AllocationError(
            "FORBIDDEN", f"allocating a credit requires role {min_role.value}", 403
        )
    credit = session.scalars(
        select(Credit).where(Credit.credit_id == credit_id, Credit.org_id == p.org_id)
    ).first()
    obligation = session.scalars(
        select(Obligation).where(
            Obligation.obligation_id == obligation_id, Obligation.org_id == p.org_id
        )
    ).first()
    if credit is None or obligation is None:
        raise AllocationError("NOT_FOUND", "credit or obligation not found", 404)
    # typed check against the source record forces a look at the real transaction
    if txn_last4.strip().upper() != credit.provider_txn_id[-4:].upper():
        raise AllocationError("LAST4_MISMATCH", "last characters do not match the transaction id")
    if credit.source == "manual" and credit.created_by == p.actor:
        raise AllocationError(
            "SEPARATION_OF_DUTIES", "a manual credit must be allocated by someone else", 403
        )
    waiting = session.scalars(
        select(Claim.claim_id).where(
            Claim.org_id == p.org_id,
            Claim.obligation_id == obligation_id,
            Claim.state.in_([s.value for s in REVIEWABLE]),
        )
    ).first()
    if waiting is not None:
        raise AllocationError(
            "CLAIM_WAITING",
            f"claim {waiting} is waiting on this obligation; confirm it from the review queue",
            409,
        )

    available = ledger.credit_available(session, credit)
    outstanding = ledger.balance(session, obligation).outstanding_minor
    most = min(available, outstanding)
    if most <= 0:
        raise AllocationError(
            "NOTHING_TO_ALLOCATE", "the credit is used up or the obligation is already paid", 409
        )
    amount = amount_minor if amount_minor is not None else most
    if amount > most:
        raise AllocationError("AMOUNT_EXCEEDS", f"at most {most} minor units can be allocated")

    if policy.config.needs_second_approver(amount, credit.currency):
        firsts = _first_approvers(session, p, credit_id, obligation_id, amount)
        if not firsts:
            audit(
                session,
                actor=p.actor,
                action=FIRST_APPROVAL,
                entity_type="credit",
                entity_id=credit_id,
                org_id=p.org_id,
                after={
                    "obligation_id": str(obligation_id),
                    "amount_minor": amount,
                    "note": note,
                },
            )
            return AllocateResult(
                None, True, outstanding, ledger.balance(session, obligation).status
            )
        if firsts == {p.actor}:
            raise AllocationError(
                "SECOND_APPROVER_REQUIRED",
                "a different approver must complete this allocation",
                409,
            )
        if not p.at_least(Role.APPROVER):
            raise AllocationError("FORBIDDEN", "the second approval needs the approver role", 403)

    try:
        alloc = ledger.allocate(
            session,
            credit_id=credit_id,
            obligation_id=obligation_id,
            authorised_minor=amount,
            idempotency_key=f"admin-alloc:{credit_id}:{obligation_id}:{request_id or uuid.uuid4()}",
            actor=p.actor,
            claim_id=None,
        )
    except ledger.LedgerError as exc:
        raise AllocationError(exc.code, str(exc), 409) from exc
    bal = ledger.balance(session, obligation)
    audit(
        session,
        actor=p.actor,
        action="allocation.admin_created",
        entity_type="allocation",
        entity_id=alloc.allocation_id,
        org_id=p.org_id,
        after={
            "credit_id": str(credit_id),
            "obligation_id": str(obligation_id),
            "amount_minor": alloc.amount_minor,
            "note": note,
        },
    )
    enqueue_entitlement(session, obligation, bal, f"allocation:{alloc.allocation_id}", "allocation")
    return AllocateResult(alloc, False, bal.outstanding_minor, bal.status)
