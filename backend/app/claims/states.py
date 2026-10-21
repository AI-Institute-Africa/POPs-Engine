"""Claim state machine (blueprint App. B.1)."""

from app.models import ClaimState as S

TRANSITIONS: dict[S, set[S]] = {
    S.RECEIVED: {S.PROCESSING},
    S.PROCESSING: {
        S.NEEDS_INFORMATION,
        S.DOCUMENT_VERIFIED,
        S.DUPLICATE_HOLD,
        S.PENDING_REVIEW,
        S.AWAITING_FINANCIAL_CONFIRMATION,
        S.FINANCIALLY_CONFIRMED,
    },
    S.NEEDS_INFORMATION: {S.RECEIVED, S.REJECTED},  # RECEIVED: payer resubmitted evidence
    S.DOCUMENT_VERIFIED: {
        S.AWAITING_FINANCIAL_CONFIRMATION,
        S.FINANCIALLY_CONFIRMED,
        S.PENDING_REVIEW,
    },
    S.AWAITING_FINANCIAL_CONFIRMATION: {
        S.FINANCIALLY_CONFIRMED,
        S.PENDING_REVIEW,
        S.NEEDS_INFORMATION,
        S.REJECTED,
        S.DUPLICATE_HOLD,
    },
    S.FINANCIALLY_CONFIRMED: {
        S.APPROVED,
        S.PARTIALLY_ALLOCATED,
        S.PENDING_REVIEW,
        S.REJECTED,
        S.NEEDS_INFORMATION,
        S.DUPLICATE_HOLD,
    },
    S.PENDING_REVIEW: {
        S.APPROVED,
        S.PARTIALLY_ALLOCATED,
        S.REJECTED,
        S.NEEDS_INFORMATION,
        S.DUPLICATE_HOLD,
        S.AWAITING_FINANCIAL_CONFIRMATION,
        S.FINANCIALLY_CONFIRMED,
    },
    S.DUPLICATE_HOLD: {S.PENDING_REVIEW, S.REJECTED, S.APPROVED, S.PARTIALLY_ALLOCATED},
    S.PARTIALLY_ALLOCATED: {S.APPROVED, S.REVERSED_OR_REFUNDED},
    S.APPROVED: {S.REVERSED_OR_REFUNDED},
    S.REJECTED: set(),
    S.REVERSED_OR_REFUNDED: set(),
}

# States a reviewer can act on (confirm / reject / request info)
REVIEWABLE = {
    S.DOCUMENT_VERIFIED,
    S.AWAITING_FINANCIAL_CONFIRMATION,
    S.FINANCIALLY_CONFIRMED,
    S.PENDING_REVIEW,
    S.DUPLICATE_HOLD,
}

# Operational outcomes (blueprint 4.5) shown to payers
OUTCOME = {
    S.APPROVED: "approved",
    S.PARTIALLY_ALLOCATED: "approved",
    S.PENDING_REVIEW: "pending_review",
    S.AWAITING_FINANCIAL_CONFIRMATION: "pending_review",
    S.FINANCIALLY_CONFIRMED: "pending_review",
    S.DOCUMENT_VERIFIED: "pending_review",
    S.RECEIVED: "pending_review",
    S.PROCESSING: "pending_review",
    S.NEEDS_INFORMATION: "needs_information",
    S.REJECTED: "rejected",
    S.DUPLICATE_HOLD: "rejected_or_duplicate_hold",
    S.REVERSED_OR_REFUNDED: "reversed",
}


class InvalidTransition(Exception):
    pass


def check_transition(current: str, target: S) -> None:
    if target not in TRANSITIONS[S(current)]:
        raise InvalidTransition(f"{current} -> {target} is not allowed")
