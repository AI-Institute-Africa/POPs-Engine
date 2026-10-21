"""Payment ledger (blueprint 4.6, error layer 7).

available  = confirmed credit - prior allocations + reversals
allocation = min(available, obligation outstanding, amount authorised)

Writes lock the credit and obligation rows; the database trigger re-checks every invariant.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.models import Allocation, Credit, Obligation

_NET = func.coalesce(
    func.sum(
        case(
            (Allocation.kind == "allocation", Allocation.amount_minor),
            else_=-Allocation.amount_minor,
        )
    ),
    0,
)


class LedgerError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def credit_allocated(session: Session, credit_id: uuid.UUID) -> int:
    return int(session.scalar(select(_NET).where(Allocation.credit_id == credit_id)) or 0)


def credit_available(session: Session, credit: Credit) -> int:
    return credit.amount_minor - credit_allocated(session, credit.credit_id)


def obligation_paid(session: Session, obligation_id: uuid.UUID) -> int:
    return int(session.scalar(select(_NET).where(Allocation.obligation_id == obligation_id)) or 0)


@dataclass
class Balance:
    obligation_id: uuid.UUID
    amount_minor: int
    paid_minor: int
    currency: str

    @property
    def outstanding_minor(self) -> int:
        return self.amount_minor - self.paid_minor

    @property
    def status(self) -> str:
        if self.paid_minor <= 0:
            return "unpaid"
        return "paid" if self.outstanding_minor == 0 else "partially_paid"


def balance(session: Session, obligation: Obligation) -> Balance:
    return Balance(
        obligation_id=obligation.obligation_id,
        amount_minor=obligation.amount_minor,
        paid_minor=obligation_paid(session, obligation.obligation_id),
        currency=obligation.currency,
    )


def allocate(
    session: Session,
    *,
    credit_id: uuid.UUID,
    obligation_id: uuid.UUID,
    authorised_minor: int,
    idempotency_key: str,
    actor: str,
    claim_id: uuid.UUID | None = None,
    correlation_id: uuid.UUID | None = None,
) -> Allocation:
    """Allocate up to `authorised_minor` of a credit to an obligation. Idempotent per key.

    Runs in the caller's transaction; the caller commits.
    """
    existing = session.scalars(
        select(Allocation).where(Allocation.idempotency_key == idempotency_key)
    ).first()
    if existing:
        return existing

    credit = session.scalars(
        select(Credit).where(Credit.credit_id == credit_id).with_for_update()
    ).one()
    obligation = session.scalars(
        select(Obligation).where(Obligation.obligation_id == obligation_id).with_for_update()
    ).one()

    if credit.org_id != obligation.org_id:
        raise LedgerError("CROSS_ORG", "credit and obligation belong to different organisations")
    if credit.currency != obligation.currency:
        raise LedgerError("CURRENCY_MISMATCH", "credit and obligation currencies differ")
    if credit.status != "success":
        raise LedgerError("CREDIT_NOT_SUCCESS", f"credit status is {credit.status}")
    if authorised_minor <= 0:
        raise LedgerError("NOTHING_AUTHORISED", "authorised amount must be positive")

    available = credit_available(session, credit)
    outstanding = obligation.amount_minor - obligation_paid(session, obligation_id)
    amount = min(available, outstanding, authorised_minor)
    if available <= 0:
        raise LedgerError("CREDIT_FULLY_ALLOCATED", "credit has no unallocated balance")
    if outstanding <= 0:
        raise LedgerError("OBLIGATION_PAID", "obligation has no outstanding balance")

    alloc = Allocation(
        org_id=credit.org_id,
        credit_id=credit_id,
        obligation_id=obligation_id,
        claim_id=claim_id,
        kind="allocation",
        amount_minor=amount,
        currency=credit.currency,
        idempotency_key=idempotency_key,
        created_by=actor,
    )
    session.add(alloc)
    session.flush()
    audit(
        session,
        actor=actor,
        action="allocation.created",
        entity_type="allocation",
        entity_id=alloc.allocation_id,
        org_id=credit.org_id,
        after={
            "credit_id": str(credit_id),
            "obligation_id": str(obligation_id),
            "amount_minor": amount,
            "currency": credit.currency,
            "available_before": available,
            "outstanding_before": outstanding,
        },
        correlation_id=correlation_id,
    )
    return alloc


def reverse(
    session: Session,
    *,
    allocation_id: uuid.UUID,
    actor: str,
    reason: str,
    correlation_id: uuid.UUID | None = None,
) -> Allocation:
    """Append a compensating entry; the original row is never edited (blueprint 4.6)."""
    key = f"reversal:{allocation_id}"
    existing = session.scalars(select(Allocation).where(Allocation.idempotency_key == key)).first()
    if existing:
        return existing
    original = session.get(Allocation, allocation_id)
    if original is None or original.kind != "allocation":
        raise LedgerError("NOT_FOUND", "allocation not found")
    rev = Allocation(
        org_id=original.org_id,
        credit_id=original.credit_id,
        obligation_id=original.obligation_id,
        claim_id=original.claim_id,
        kind="reversal",
        amount_minor=original.amount_minor,
        currency=original.currency,
        reverses=original.allocation_id,
        idempotency_key=key,
        created_by=actor,
        reason=reason,
    )
    session.add(rev)
    session.flush()
    audit(
        session,
        actor=actor,
        action="allocation.reversed",
        entity_type="allocation",
        entity_id=original.allocation_id,
        org_id=original.org_id,
        after={"reversal_id": str(rev.allocation_id), "reason": reason},
        correlation_id=correlation_id,
    )
    return rev
