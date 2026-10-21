import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.claims.states import check_transition
from app.core.audit import audit
from app.models import Claim, ClaimState, Decision


def transition(
    session: Session,
    claim: Claim,
    target: ClaimState,
    *,
    actor: str,
    reasons: list[str] | None = None,
) -> None:
    check_transition(claim.state, target)
    before = {"state": claim.state, "reason_codes": claim.reason_codes}
    claim.state = target
    if reasons is not None:
        claim.reason_codes = reasons
    audit(
        session,
        actor=actor,
        action="claim.state_changed",
        entity_type="claim",
        entity_id=claim.claim_id,
        org_id=claim.org_id,
        before=before,
        after={"state": target, "reason_codes": claim.reason_codes},
        correlation_id=claim.correlation_id,
    )


def record_decision(
    session: Session,
    claim: Claim,
    *,
    outcome: str,
    reasons: list[str],
    policy_version: str,
    actor_type: str,
    actor_id: str,
    snapshot: dict[str, Any],
    credit_id: uuid.UUID | None = None,
    note: str | None = None,
) -> Decision:
    decision = Decision(
        claim_id=claim.claim_id,
        org_id=claim.org_id,
        outcome=outcome,
        reason_codes=reasons,
        policy_version=policy_version,
        actor_type=actor_type,
        actor_id=actor_id,
        credit_id=credit_id,
        evidence_snapshot=snapshot,
        note=note,
    )
    session.add(decision)
    return decision


# Plain-language reasons that can be sent to the payer (blueprint 5.10).
REASON_TEXT = {
    "CAPTURE_TOO_SMALL": "The image is too small to read. Please upload a full-size screenshot.",
    "CAPTURE_BLURRY": "The image is blurry. Please upload a clearer photo or screenshot.",
    "UNREADABLE": "We could not read the payment details. Please upload a clearer copy.",
    "MISSING_AMOUNT": "We could not find the amount on your receipt.",
    "MISSING_REFERENCE": "We could not find a transaction reference on your receipt.",
    "TYPED_AMOUNT_DISAGREES": "The amount you typed does not match the receipt.",
    "TYPED_REFERENCE_DISAGREES": "The reference you typed does not match the receipt.",
    "DUPLICATE_FILE": "This receipt has already been submitted.",
    "DUPLICATE_NEAR_FILE": "A very similar receipt has already been submitted.",
    "DUPLICATE_REFERENCE": "This transaction reference has already been submitted.",
    "REFERENCE_ALREADY_CONSUMED": "This transaction has already been credited.",
    "RECEIPT_STATUS_NOT_SUCCESS": "The receipt shows the payment as not completed.",
    "AWAITING_CREDIT": "Your payment is waiting for confirmation from our bank records.",
    "EXTRACTION_UNCONFIRMED": "Some payment details need a quick check by our finance team.",
    "CURRENCY_MISMATCH": "The currency paid does not match the invoice currency.",
    "WRONG_BENEFICIARY": "The payment does not appear to have been sent to our account.",
    "AMOUNT_MISMATCH": "The amount received differs from the amount claimed.",
    "PARTIAL_PAYMENT": "Your payment was recorded, but the invoice is not yet fully paid.",
    "OVERPAYMENT": "Your payment exceeds the amount due; the remainder is held for you.",
    # neutral wording on purpose: payers are never told a claim is suspected (blueprint 4.5)
    "SUSPECTED_FAKE": "Your payment needs a manual check by our finance team.",
    "NO_CREDIT_FOUND": "We have not yet seen this payment in our bank records.",
}


def explain(reasons: list[str]) -> list[str]:
    return [REASON_TEXT[r] for r in reasons if r in REASON_TEXT]
