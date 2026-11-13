"""Bulk allocation: one recorded credit (an employer payment) split across many obligations.

Every row goes through the same `allocate_credit` as a single allocation, so the typed last
four characters, separation of duties, the second approver above the limit and the ledger
trigger all still apply. Nothing here can approve more than a single allocation could.

- `plan_rows` is pure: it previews each row as ready / check / fix against running balances.
- `apply` never trusts a preview. It re-runs every row through `allocate_credit`.
- Each row's request id is derived from (run_id, row number, obligation, amount), so retrying
  the same run after a crash or timeout allocates each row at most once and resumes the rest.
- A wrong last-4 or missing permission stops the whole run; any other row error is recorded
  and the run continues with the next row.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.claims.states import REVIEWABLE
from app.core.security import Principal
from app.ledger import service as ledger
from app.ledger.admin import AllocationError, allocate_credit
from app.models import Allocation, Claim, Credit, Obligation

MAX_ROWS = 500
RUN_ABORT_CODES = {"LAST4_MISMATCH", "FORBIDDEN", "SEPARATION_OF_DUTIES", "NOT_FOUND"}
_NAMESPACE = uuid.UUID("6f1c3a52-8f43-4c1e-9a55-2b3d7d1f0c11")


@dataclass
class RowIn:
    payment_code: str | None = None
    obligation_id: uuid.UUID | None = None
    amount_minor: int | None = None  # None = as much as the credit and invoice allow
    note: str | None = None


@dataclass
class RowPlan:
    index: int
    status: str  # ready | check | fix
    reason: str | None
    obligation_id: uuid.UUID | None
    amount_minor: int | None


def row_request_id(run_id: uuid.UUID, index: int, obligation_id: uuid.UUID, amount: int | None):
    return uuid.uuid5(_NAMESPACE, f"{run_id}:{index}:{obligation_id}:{amount}")


def plan_rows(
    rows: list[RowIn],
    *,
    credit_available: int,
    credit_currency: str,
    by_code: dict[str, Obligation],
    by_id: dict[uuid.UUID, Obligation],
    outstanding: dict[uuid.UUID, int],
    waiting: set[uuid.UUID],
) -> list[RowPlan]:
    """Preview every row against running balances. Pure: no database access."""
    left = credit_available
    owed = dict(outstanding)
    plans: list[RowPlan] = []
    for i, row in enumerate(rows):
        obl = by_id.get(row.obligation_id) if row.obligation_id else None
        if obl is None and row.payment_code:
            obl = by_code.get(row.payment_code.strip().upper())
        if obl is None:
            plans.append(RowPlan(i, "fix", "no invoice matches this code or id", None, None))
            continue
        oid = obl.obligation_id
        if obl.currency != credit_currency:
            plans.append(
                RowPlan(
                    i,
                    "fix",
                    f"invoice is in {obl.currency}, credit in {credit_currency}",
                    oid,
                    None,
                )
            )
            continue
        if oid in waiting:
            plans.append(
                RowPlan(
                    i,
                    "fix",
                    "a claim is waiting on this invoice; confirm it from the queue",
                    oid,
                    None,
                )
            )
            continue
        room = min(left, owed.get(oid, 0))
        if room <= 0:
            reason = "the credit is used up" if left <= 0 else "this invoice is already paid"
            plans.append(RowPlan(i, "fix", reason, oid, None))
            continue
        if row.amount_minor is not None and row.amount_minor <= 0:
            plans.append(RowPlan(i, "fix", "amount must be more than zero", oid, None))
            continue
        if row.amount_minor is not None and row.amount_minor > room:
            plans.append(RowPlan(i, "fix", f"at most {room} minor units can go here", oid, None))
            continue
        amount = row.amount_minor if row.amount_minor is not None else room
        left -= amount
        owed[oid] = owed.get(oid, 0) - amount
        if row.amount_minor is None:
            plans.append(
                RowPlan(i, "check", "no amount given: the most allowed will be used", oid, amount)
            )
        else:
            plans.append(RowPlan(i, "ready", None, oid, amount))
    return plans


@dataclass
class RowResult:
    index: int
    status: str  # allocated | needs_second_approver | failed | skipped
    detail: str | None = None
    allocation_id: uuid.UUID | None = None
    amount_minor: int | None = None


def _context(session: Session, p: Principal, credit: Credit, rows: list[RowIn]):
    codes = {r.payment_code.strip().upper() for r in rows if r.payment_code}
    ids = {r.obligation_id for r in rows if r.obligation_id}
    found: list[Obligation] = []
    if codes or ids:
        found = list(
            session.scalars(
                select(Obligation).where(
                    Obligation.org_id == p.org_id,
                    (Obligation.payment_code.in_(codes)) | (Obligation.obligation_id.in_(ids)),
                )
            )
        )
    by_code = {o.payment_code.upper(): o for o in found}
    by_id = {o.obligation_id: o for o in found}
    outstanding = {o.obligation_id: ledger.balance(session, o).outstanding_minor for o in found}
    waiting: set[uuid.UUID] = set()
    if found:
        waiting = set(
            session.scalars(
                select(Claim.obligation_id).where(
                    Claim.org_id == p.org_id,
                    Claim.obligation_id.in_(list(by_id)),
                    Claim.state.in_([s.value for s in REVIEWABLE]),
                )
            )
        )
    return by_code, by_id, outstanding, waiting


def preview(
    session: Session, p: Principal, credit_id: uuid.UUID, rows: list[RowIn]
) -> list[RowPlan]:
    credit = session.scalars(
        select(Credit).where(Credit.credit_id == credit_id, Credit.org_id == p.org_id)
    ).first()
    if credit is None:
        raise AllocationError("NOT_FOUND", "credit not found", 404)
    by_code, by_id, outstanding, waiting = _context(session, p, credit, rows)
    return plan_rows(
        rows,
        credit_available=ledger.credit_available(session, credit),
        credit_currency=credit.currency,
        by_code=by_code,
        by_id=by_id,
        outstanding=outstanding,
        waiting=waiting,
    )


def apply(
    session: Session,
    p: Principal,
    credit_id: uuid.UUID,
    run_id: uuid.UUID,
    txn_last4: str,
    rows: list[RowIn],
) -> tuple[list[RowResult], bool]:
    """Returns (per-row results, aborted). Commits after each allocation so progress is kept."""
    credit = session.scalars(
        select(Credit).where(Credit.credit_id == credit_id, Credit.org_id == p.org_id)
    ).first()
    if credit is None:
        raise AllocationError("NOT_FOUND", "credit not found", 404)
    by_code, by_id, _, _ = _context(session, p, credit, rows)
    results: list[RowResult] = []
    for i, row in enumerate(rows):
        obl = by_id.get(row.obligation_id) if row.obligation_id else None
        if obl is None and row.payment_code:
            obl = by_code.get(row.payment_code.strip().upper())
        if obl is None:
            results.append(RowResult(i, "failed", "no invoice matches this code or id"))
            continue
        request_id = row_request_id(run_id, i, obl.obligation_id, row.amount_minor)
        done = session.scalars(
            select(Allocation).where(
                Allocation.idempotency_key
                == f"admin-alloc:{credit_id}:{obl.obligation_id}:{request_id}"
            )
        ).first()
        if done is not None:  # this row already went through in an earlier attempt of the run
            results.append(
                RowResult(i, "allocated", "already done", done.allocation_id, done.amount_minor)
            )
            continue
        try:
            res = allocate_credit(
                session,
                p,
                credit_id=credit_id,
                obligation_id=obl.obligation_id,
                amount_minor=row.amount_minor,
                txn_last4=txn_last4,
                note=row.note,
                request_id=request_id,
            )
        except AllocationError as exc:
            session.rollback()
            if exc.code in RUN_ABORT_CODES:
                results.append(RowResult(i, "failed", f"{exc.code}: {exc}"))
                results.extend(
                    RowResult(j, "skipped", "run stopped") for j in range(i + 1, len(rows))
                )
                return results, True
            results.append(RowResult(i, "failed", f"{exc.code}: {exc}"))
            continue
        session.commit()
        if res.allocation is None:
            results.append(RowResult(i, "needs_second_approver", "first approval recorded"))
        else:
            results.append(
                RowResult(
                    i, "allocated", None, res.allocation.allocation_id, res.allocation.amount_minor
                )
            )
    return results, False
