"""Reconciliation (pipeline stage 7, error layers 4-5).

Hard gates first, all pass/fail; only survivors are ranked with
S = 0.35R + 0.25A + 0.20B + 0.10T + 0.10P (blueprint App. B.2). The score orders candidates for
the reviewer; it never replaces a gate.
"""

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.orm import Session

from app.ledger.service import credit_available
from app.models import Credit, Obligation
from app.normalise.fields import normalise_name, normalise_reference
from app.payment_codes.codes import find_codes
from app.policy.engine import PolicyConfig


@dataclass
class ClaimFacts:
    """What the claim says, after extraction, agreement and typed-field cross-check."""

    org_id: uuid.UUID
    amount_minor: int | None
    currency: str | None
    reference: str | None  # normalised
    occurred_at: datetime | None
    sender: str | None
    payment_code: str  # the obligation's code


@dataclass
class Gate:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class Candidate:
    credit_id: uuid.UUID
    gates: list[Gate]
    score: float
    available_minor: int
    reference_kind: str  # txn_id | payment_code | none
    components: dict[str, float] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(g.passed for g in self.gates)

    @property
    def failed(self) -> list[str]:
        return [g.name for g in self.gates if not g.passed]

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["credit_id"] = str(self.credit_id)
        d["passed"] = self.passed
        return d


GATE_REASON = {
    "currency": "CURRENCY_MISMATCH",
    "reference": "REFERENCE_MISMATCH",
    "amount": "AMOUNT_MISMATCH",
    "beneficiary": "WRONG_BENEFICIARY",
    "status": "CREDIT_NOT_SUCCESS",
    "unallocated": "CREDIT_FULLY_ALLOCATED",
    "date_window": "OUTSIDE_DATE_WINDOW",
}


def _reference_kind(facts: ClaimFacts, credit: Credit) -> str:
    txn = credit.reference_norm
    narrative = normalise_reference(credit.narrative) or ""
    if facts.reference and (facts.reference == txn or facts.reference in narrative):
        return "txn_id"
    if facts.payment_code in find_codes(credit.narrative):
        return "payment_code"
    return "none"


def evaluate(
    session: Session,
    facts: ClaimFacts,
    obligation: Obligation,
    credit: Credit,
    policy: PolicyConfig,
) -> Candidate:
    available = credit_available(session, credit)
    ref_kind = _reference_kind(facts, credit)
    gates = [
        Gate(
            "currency",
            credit.currency == obligation.currency
            and (facts.currency is None or facts.currency == credit.currency),
            f"credit {credit.currency}, claim {facts.currency}, invoice {obligation.currency}",
        ),
        Gate("reference", ref_kind != "none", ref_kind),
        Gate(
            "amount",
            facts.amount_minor is not None
            and abs(facts.amount_minor - credit.amount_minor) <= policy.amount_tolerance_minor,
            f"credit {credit.amount_minor}, claim {facts.amount_minor}",
        ),
        Gate("beneficiary", credit.beneficiary in policy.beneficiary_aliases, credit.beneficiary),
        Gate("status", credit.status in policy.success_statuses, credit.status),
        Gate("unallocated", available > 0, f"available {available}"),
    ]
    window = timedelta(hours=policy.date_window_hours)
    if facts.occurred_at is not None:
        delta = abs(credit.occurred_at - facts.occurred_at)
        gates.append(Gate("date_window", delta <= window, f"delta {delta}"))
        t = max(0.0, 1 - delta / window)
    else:
        gates.append(Gate("date_window", False, "claim date unknown"))
        t = 0.0

    r = {"txn_id": 1.0, "payment_code": 0.9, "none": 0.0}[ref_kind]
    if facts.amount_minor and credit.amount_minor:
        diff = abs(facts.amount_minor - credit.amount_minor)
        a = max(0.0, 1 - diff / max(facts.amount_minor, credit.amount_minor))
    else:
        a = 0.0
    b = 1.0 if credit.beneficiary in policy.beneficiary_aliases else 0.0
    sender, payer = normalise_name(facts.sender), normalise_name(credit.payer)
    p = SequenceMatcher(None, sender, payer).ratio() if sender and payer else 0.5
    w = policy.ranking_weights
    comps = {"R": r, "A": round(a, 3), "B": b, "T": round(t, 3), "P": round(p, 3)}
    score = sum(w[k] * v for k, v in comps.items())
    return Candidate(
        credit_id=credit.credit_id,
        gates=gates,
        score=round(score, 4),
        available_minor=available,
        reference_kind=ref_kind,
        components=comps,
    )


def find_candidates(
    session: Session,
    facts: ClaimFacts,
    obligation: Obligation,
    policy: PolicyConfig,
    limit: int = 10,
) -> list[Candidate]:
    """Pre-filter credits in the organisation, then gate and rank them."""
    conds: list[ColumnElement[bool]] = [Credit.narrative.ilike(f"%{facts.payment_code}%")]
    if facts.reference:
        conds.append(Credit.reference_norm == facts.reference)
        conds.append(Credit.narrative.ilike(f"%{facts.reference}%"))
    if facts.amount_minor:
        conds.append(Credit.amount_minor == facts.amount_minor)
    q = select(Credit).where(Credit.org_id == facts.org_id, or_(*conds))
    if facts.occurred_at is not None:
        span = timedelta(hours=policy.date_window_hours * 2)
        q = q.where(Credit.occurred_at.between(facts.occurred_at - span, facts.occurred_at + span))
    credits = session.scalars(q.order_by(Credit.occurred_at.desc()).limit(200)).all()
    ranked = [evaluate(session, facts, obligation, c, policy) for c in credits]
    ranked.sort(key=lambda c: (c.passed, c.score), reverse=True)
    return ranked[:limit]
