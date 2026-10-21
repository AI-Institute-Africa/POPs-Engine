"""Finance review actions (blueprint 5.8, App. E.2).

Approval always needs a credit that passes every hard gate at the moment of confirmation. A
reviewer cannot skip a gate; the only way to approve a receipt with no statement line is for an
approver to record an attested manual credit, which a *different* person then confirms.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.claims.facts import build_facts
from app.claims.service import record_decision, transition
from app.claims.states import REVIEWABLE, TRANSITIONS
from app.core.audit import audit
from app.core.security import Principal, Role
from app.entitlements.outbox import enqueue_entitlement
from app.extraction.agreement import normalise_field
from app.ledger import service as ledger
from app.models import (
    Allocation,
    Claim,
    ClaimState,
    ConsumedReference,
    Credit,
    Decision,
    ExtractedField,
    Obligation,
)
from app.policy.engine import evidence_tier_for_source, load_policy
from app.reconcile.matching import GATE_REASON, evaluate

REJECT_REASONS = {
    "DUPLICATE_CONFIRMED",
    "PAYMENT_NOT_RECEIVED",
    "PAYMENT_FAILED_OR_REVERSED",
    "WRONG_BENEFICIARY",
    "AMOUNT_MISMATCH",
    "CURRENCY_MISMATCH",
    "TAMPERED_DOCUMENT",
    "WRONG_OBLIGATION",
    "OTHER",
}


class ReviewError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass
class ConfirmResult:
    claim: Claim
    allocation: Allocation | None
    needs_second_approval: bool = False


def _load(session: Session, p: Principal, claim_id: uuid.UUID) -> Claim:
    claim = session.scalars(
        select(Claim).where(Claim.claim_id == claim_id, Claim.org_id == p.org_id).with_for_update()
    ).first()
    if claim is None:
        raise ReviewError("NOT_FOUND", "claim not found", 404)
    return claim


def _first_approval(session: Session, claim: Claim, credit_id: uuid.UUID) -> Decision | None:
    return session.scalars(
        select(Decision)
        .where(
            Decision.claim_id == claim.claim_id,
            Decision.outcome == "FIRST_APPROVAL",
            Decision.credit_id == credit_id,
        )
        .order_by(Decision.created_at.desc())
    ).first()


def confirm(
    session: Session,
    p: Principal,
    claim_id: uuid.UUID,
    credit_id: uuid.UUID,
    txn_last4: str,
    note: str | None = None,
) -> ConfirmResult:
    claim = _load(session, p, claim_id)
    if claim.state not in REVIEWABLE:
        raise ReviewError("NOT_REVIEWABLE", f"claim is {claim.state}")
    credit = session.scalars(
        select(Credit).where(Credit.credit_id == credit_id, Credit.org_id == p.org_id)
    ).first()
    if credit is None:
        raise ReviewError("NOT_FOUND", "credit not found", 404)
    # typed check against the source system forces the reviewer to look at the real record
    if txn_last4.strip().upper() != credit.provider_txn_id[-4:].upper():
        raise ReviewError("LAST4_MISMATCH", "last four characters do not match the credit", 422)
    if credit.source == "manual" and credit.created_by == p.actor:
        raise ReviewError(
            "SEPARATION_OF_DUTIES", "you cannot confirm against a credit you recorded", 403
        )

    policy = load_policy(session, claim.org_id)
    obligation = session.get(Obligation, claim.obligation_id)
    assert obligation is not None
    facts, flags = build_facts(session, claim, obligation)
    cand = evaluate(session, facts, obligation, credit, policy.config)
    if not cand.passed:
        raise ReviewError(
            "GATES_FAILED",
            "hard gates failed: " + ", ".join(GATE_REASON.get(g, g) for g in cand.failed),
            422,
        )
    ref_key = f"ref:{facts.reference}" if facts.reference else None
    if ref_key:
        consumed = session.get(ConsumedReference, ref_key)
        if consumed and consumed.claim_id != claim.claim_id:
            raise ReviewError("REFERENCE_ALREADY_CONSUMED", "reference already credited")

    authorised = facts.amount_minor or 0
    snapshot = {
        "gates": [g.__dict__ for g in cand.gates],
        "score": cand.score,
        "flags": flags,
        "facts": {"amount_minor": facts.amount_minor, "reference": facts.reference},
    }

    # second approver above the policy limit (blueprint 4.7, reviewer misuse)
    if authorised > policy.config.second_approver_above_minor:
        first = _first_approval(session, claim, credit_id)
        if first is None or first.actor_id == p.actor:
            if first is not None:
                raise ReviewError("SECOND_APPROVER_REQUIRED", "a different approver must confirm")
            record_decision(
                session,
                claim,
                outcome="FIRST_APPROVAL",
                reasons=flags,
                policy_version=policy.version,
                actor_type="reviewer",
                actor_id=p.actor,
                snapshot=snapshot,
                credit_id=credit_id,
                note=note,
            )
            if claim.state != ClaimState.PENDING_REVIEW:
                transition(session, claim, ClaimState.PENDING_REVIEW, actor=p.actor)
            return ConfirmResult(claim, None, needs_second_approval=True)
        if not p.at_least(Role.APPROVER):
            raise ReviewError(
                "SECOND_APPROVER_REQUIRED", "second approval needs approver role", 403
            )

    alloc = ledger.allocate(
        session,
        credit_id=credit_id,
        obligation_id=obligation.obligation_id,
        authorised_minor=authorised,
        idempotency_key=f"claim:{claim.claim_id}:credit:{credit_id}",
        actor=p.actor,
        claim_id=claim.claim_id,
        correlation_id=claim.correlation_id,
    )
    if ref_key and session.get(ConsumedReference, ref_key) is None:
        session.add(
            ConsumedReference(reference_key=ref_key, org_id=claim.org_id, claim_id=claim.claim_id)
        )
    bal = ledger.balance(session, obligation)
    reasons = list(flags)
    if bal.outstanding_minor > 0:
        reasons.append("PARTIAL_PAYMENT")
    if alloc.amount_minor < authorised:
        reasons.append("OVERPAYMENT")
    partial = {"PARTIAL_PAYMENT", "OVERPAYMENT"}.intersection(reasons)
    target = ClaimState.PARTIALLY_ALLOCATED if partial else ClaimState.APPROVED
    claim.candidate_credit_id = credit_id
    claim.evidence_tier = evidence_tier_for_source(credit.source)
    transition(session, claim, target, actor=p.actor, reasons=reasons)
    record_decision(
        session,
        claim,
        outcome=target,
        reasons=reasons,
        policy_version=policy.version,
        actor_type="reviewer",
        actor_id=p.actor,
        snapshot=snapshot,
        credit_id=credit_id,
        note=note,
    )
    enqueue_entitlement(session, obligation, bal, f"allocation:{alloc.allocation_id}", "allocation")
    return ConfirmResult(claim, alloc)


def reject(session: Session, p: Principal, claim_id: uuid.UUID, reason: str, note: str | None):
    if reason not in REJECT_REASONS:
        raise ReviewError("BAD_REASON", f"reason must be one of {sorted(REJECT_REASONS)}", 422)
    claim = _load(session, p, claim_id)
    if ClaimState.REJECTED not in _allowed(claim):
        raise ReviewError("NOT_REVIEWABLE", f"claim is {claim.state}")
    policy = load_policy(session, claim.org_id)
    transition(
        session, claim, ClaimState.REJECTED, actor=p.actor, reasons=[*claim.reason_codes, reason]
    )
    record_decision(
        session,
        claim,
        outcome=ClaimState.REJECTED,
        reasons=[reason],
        policy_version=policy.version,
        actor_type="reviewer",
        actor_id=p.actor,
        snapshot={"reason_codes": claim.reason_codes},
        note=note,
    )
    return claim


def request_info(session: Session, p: Principal, claim_id: uuid.UUID, message: str):
    claim = _load(session, p, claim_id)
    if ClaimState.NEEDS_INFORMATION not in _allowed(claim):
        raise ReviewError("NOT_REVIEWABLE", f"claim is {claim.state}")
    policy = load_policy(session, claim.org_id)
    transition(
        session,
        claim,
        ClaimState.NEEDS_INFORMATION,
        actor=p.actor,
        reasons=[*claim.reason_codes, "REVIEWER_REQUESTED_INFO"],
    )
    record_decision(
        session,
        claim,
        outcome=ClaimState.NEEDS_INFORMATION,
        reasons=["REVIEWER_REQUESTED_INFO"],
        policy_version=policy.version,
        actor_type="reviewer",
        actor_id=p.actor,
        snapshot={},
        note=message,
    )
    return claim


def correct_field(
    session: Session, p: Principal, claim_id: uuid.UUID, name: str, value: str, reason: str
) -> ExtractedField:
    """Keep the original reading; add a corrected row (labelled example for the learning loop)."""
    claim = _load(session, p, claim_id)
    if claim.state not in REVIEWABLE:
        raise ReviewError("NOT_REVIEWABLE", f"claim is {claim.state}")
    original = session.scalars(
        select(ExtractedField)
        .where(ExtractedField.claim_id == claim.claim_id, ExtractedField.name == name)
        .order_by(ExtractedField.created_at.desc())
    ).first()
    if original is None:
        raise ReviewError("NOT_FOUND", "no extracted field with that name", 404)
    currency = claim.declared_currency
    if name == "amount":
        cur = session.scalars(
            select(ExtractedField.normalised_value)
            .where(ExtractedField.claim_id == claim.claim_id, ExtractedField.name == "currency")
            .order_by(ExtractedField.created_at.desc())
        ).first()
        currency = cur or currency
    normalised = normalise_field(name, value, currency)
    if normalised is None:
        raise ReviewError("BAD_VALUE", f"could not normalise {name} value {value!r}", 422)
    row = ExtractedField(
        claim_id=claim.claim_id,
        evidence_id=original.evidence_id,
        run_id=original.run_id,
        name=name,
        raw_value=value,
        normalised_value=normalised,
        confidence=1.0,
        extractor="reviewer",
        extractor_version="human",
        region=original.region,
        agreed=True,
        corrected_by=p.actor,
        correction_reason=reason,
    )
    session.add(row)
    audit(
        session,
        actor=p.actor,
        action="field.corrected",
        entity_type="claim",
        entity_id=claim.claim_id,
        org_id=claim.org_id,
        before={"name": name, "value": original.normalised_value},
        after={"name": name, "value": normalised, "reason": reason},
        correlation_id=claim.correlation_id,
    )
    return row


def reverse_allocation(session: Session, p: Principal, allocation_id: uuid.UUID, reason: str):
    alloc = session.scalars(
        select(Allocation).where(
            Allocation.allocation_id == allocation_id, Allocation.org_id == p.org_id
        )
    ).first()
    if alloc is None or alloc.kind != "allocation":
        raise ReviewError("NOT_FOUND", "allocation not found", 404)
    rev = ledger.reverse(session, allocation_id=allocation_id, actor=p.actor, reason=reason)
    obligation = session.get(Obligation, alloc.obligation_id)
    assert obligation is not None
    if alloc.claim_id:
        claim = session.get(Claim, alloc.claim_id)
        if claim and claim.state in (ClaimState.APPROVED, ClaimState.PARTIALLY_ALLOCATED):
            transition(
                session,
                claim,
                ClaimState.REVERSED_OR_REFUNDED,
                actor=p.actor,
                reasons=[*claim.reason_codes, "REVERSED"],
            )
            policy = load_policy(session, claim.org_id)
            record_decision(
                session,
                claim,
                outcome=ClaimState.REVERSED_OR_REFUNDED,
                reasons=["REVERSED"],
                policy_version=policy.version,
                actor_type="reviewer",
                actor_id=p.actor,
                snapshot={"allocation_id": str(allocation_id)},
                note=reason,
            )
    enqueue_entitlement(
        session,
        obligation,
        ledger.balance(session, obligation),
        f"reversal:{rev.allocation_id}",
        "reversal",
    )
    return rev


def _allowed(claim: Claim) -> set[ClaimState]:
    return TRANSITIONS[ClaimState(claim.state)]
