import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.claims.states import check_transition
from app.core.audit import audit
from app.entitlements.outbox import enqueue_event, enqueue_payer_message
from app.models import Claim, ClaimState, Decision
from app.provisional.service import resolve_for_state

# states in which a claim is waiting for a human (admins are told once on entry)
REVIEW_WAITING = {ClaimState.PENDING_REVIEW, ClaimState.DUPLICATE_HOLD}


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
    was_waiting = claim.state in REVIEW_WAITING
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
    _track_review_wait(session, claim, target, was_waiting)
    resolve_for_state(session, claim, ClaimState(target))
    _tell_payer(session, claim, ClaimState(target))


# Neutral wording only: a payer is never told a claim is suspected (blueprint 4.5), and a
# rejection never says why. Sent only to claims that arrived over WhatsApp.
def _payer_text(claim: Claim, target: ClaimState) -> str | None:
    if target == ClaimState.APPROVED:
        return "Thank you. Your payment has been confirmed."
    if target == ClaimState.PARTIALLY_ALLOCATED:
        return REASON_TEXT["PARTIAL_PAYMENT"]
    if target == ClaimState.REJECTED:
        return "We could not confirm your payment. Please contact our finance team."
    if target == ClaimState.NEEDS_INFORMATION:
        hints = " ".join(explain(list(claim.reason_codes)))
        return hints or (
            "We need a little more information about your payment. Please send a clearer copy "
            "of your proof of payment."
        )
    return None


def _tell_payer(session: Session, claim: Claim, target: ClaimState) -> None:
    text = _payer_text(claim, target)
    if text is not None:
        enqueue_payer_message(session, claim, target.value.lower(), text)


def _track_review_wait(
    session: Session, claim: Claim, target: ClaimState, was_waiting: bool
) -> None:
    now_waiting = target in REVIEW_WAITING
    if now_waiting and not was_waiting:
        now = datetime.now(UTC)
        claim.review_entered_at = now
        claim.review_escalation = 0
        codes = [str(c) for c in claim.reason_codes]
        # ids and reason codes only: no payer name, number or amount in the message
        enqueue_event(
            session,
            claim.org_id,
            "ops.review_pending",
            f"review:{claim.claim_id}:{target}:{now.isoformat()}",
            {
                "text": f"A proof of payment needs review ({target.value.lower()}"
                + (f": {', '.join(codes)}" if codes else "")
                + f"). Claim {claim.claim_id}.",
                "claim_id": str(claim.claim_id),
                "state": target.value,
                "reason_codes": codes,
            },
        )
    elif was_waiting and not now_waiting:
        claim.review_entered_at = None


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
    "MANY_LOW_CONFIDENCE_FIELDS": "Some details were hard to read, so our finance team will check.",
    "TAMPER_SUSPECTED": "The receipt image needs a manual check by our finance team.",
    "CURRENCY_MISMATCH": "The currency paid does not match the invoice currency.",
    "WRONG_BENEFICIARY": "The payment does not appear to have been sent to our account.",
    "AMOUNT_MISMATCH": "The amount received differs from the amount claimed.",
    "PARTIAL_PAYMENT": "Your payment was recorded, but the invoice is not yet fully paid.",
    "OVERPAYMENT": "Your payment exceeds the amount due; the remainder is held for you.",
    # neutral wording on purpose: payers are never told a claim is suspected (blueprint 4.5)
    "SUSPECTED_FAKE": "Your payment needs a manual check by our finance team.",
    "NO_CREDIT_FOUND": "We have not yet seen this payment in our bank records.",
    "DOCUMENT_TRUNCATED": "Your document is long; our finance team will check the remaining pages.",
    "CREDIT_DISPUTED": "Your payment needs a manual check by our finance team.",
}


def explain(reasons: list[str]) -> list[str]:
    return [REASON_TEXT[r] for r in reasons if r in REASON_TEXT]
