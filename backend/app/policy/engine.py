"""Versioned payment policy (blueprint 4.5, error layer 6).

Policy values are configuration records, never hardcoded in business logic (App. F.1 rule 2).
The defaults below seed the platform policy row; organisations may override with their own.
"""

import uuid
from typing import Any

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ApprovalTier, EvidenceTier, PaymentPolicy

# Default weight (0-100) of each fake-POP signal; organisations tune them in their policy.
DEFAULT_FRAUD_WEIGHTS: dict[str, int] = {
    "REUSED_RECEIPT": 70,
    "AMOUNT_EDIT_SUSPECTED": 70,
    "AI_GENERATED_MARKERS": 60,
    "NO_CREDIT_AFTER_STATEMENT": 45,
    "DATE_EDIT_SUSPECTED": 40,
    "WRONG_RECIPIENT": 40,
    "FUTURE_DATE": 40,
    "EDITING_SOFTWARE": 35,
    "PROMPT_INJECTION_TEXT": 30,
    "PDF_EDITED_AFTER_CREATION": 30,
    "PAYER_HISTORY": 30,
    "EDITABLE_DOCUMENT": 30,
    "PROOFS_DISAGREE": 35,
    "PASTED_TEXT": 25,
    "ELA_LOCAL_EDIT": 25,
    "REFERENCE_FORMAT_INVALID": 25,
    "AI_SUSPECTS_EDITING": 15,
    "READERS_DISAGREE": 15,
    "STATUS_NOT_SUCCESS": 15,
    "VELOCITY": 15,
    "DATE_BEFORE_INVOICE": 15,
    "TYPED_DETAILS_DIFFER": 10,
}


class PolicyConfig(BaseModel):
    # automation is off in slice 1; there is no code path that reads this as true
    auto_approve_enabled: bool = False
    # our receiving accounts / merchant codes; a credit must land on one of these
    beneficiary_aliases: list[str] = Field(default_factory=lambda: ["MAIN"])
    success_statuses: list[str] = Field(default_factory=lambda: ["success"])
    date_window_hours: int = 72
    amount_tolerance_minor: int = 0  # fee tolerance; 0 = exact
    phash_duplicate_distance: int = 6
    min_image_side_px: int = 300
    min_sharpness: float = 40.0  # variance of Laplacian
    # per currency, in minor units. A currency with no limit always needs a second approver
    # (fail closed): finance must set e.g. a ZWG limit explicitly.
    second_approver_above_minor: dict[str, int] = Field(default_factory=lambda: {"USD": 50_000})
    # who may record attested manual credits
    manual_credit_min_role: str = "approver"
    # pilot scoping: claims on other channels go to review with CHANNEL_NOT_IN_PILOT (None = all)
    enabled_channels: list[str] | None = None
    critical_fields: list[str] = Field(
        default_factory=lambda: ["amount", "transaction_id", "date", "beneficiary"]
    )
    single_reader_min_confidence: float = 0.97
    # fraud risk (app/fraud/risk.py): weights per signal, merged over DEFAULT_FRAUD_WEIGHTS
    fraud_weights: dict[str, int] = Field(default_factory=dict)
    fraud_high_score: int = 60  # suspected fake: admin alert, confirmation blocked
    fraud_medium_score: int = 30  # suspicious: shown to reviewers
    velocity_claims_per_day: int = 5
    ranking_weights: dict[str, float] = Field(
        default_factory=lambda: {"R": 0.35, "A": 0.25, "B": 0.20, "T": 0.10, "P": 0.10}
    )
    # tamper forensics (blueprint 8, layer 6): a risk flag only. A hit routes the claim to a
    # reviewer; it can never approve, reject or allocate on its own.
    tamper_enabled: bool = True
    tamper_review_score: float = Field(default=0.6, ge=0.0, le=1.0)

    @field_validator("second_approver_above_minor", mode="before")
    @classmethod
    def _legacy_single_limit(cls, v: Any) -> Any:
        return {"USD": v} if isinstance(v, int) else v

    @field_validator("manual_credit_min_role")
    @classmethod
    def _known_role(cls, v: str) -> str:
        if v not in ("reviewer", "approver", "admin"):
            raise ValueError("manual_credit_min_role must be reviewer, approver or admin")
        return v

    def fraud_weight(self, code: str) -> int:
        return self.fraud_weights.get(code, DEFAULT_FRAUD_WEIGHTS.get(code, 0))

    def needs_second_approver(self, amount_minor: int, currency: str | None) -> bool:
        limit = self.second_approver_above_minor.get(currency or "")
        return limit is None or amount_minor > limit


class Policy(BaseModel):
    version: str
    config: PolicyConfig


DEFAULT_VERSION = "2026.10-v1"


def load_policy(session: Session, org_id: uuid.UUID) -> Policy:
    """Most recent active org policy, else the platform default, else built-in defaults."""
    for scope in (PaymentPolicy.org_id == org_id, PaymentPolicy.org_id.is_(None)):
        row = session.scalars(
            select(PaymentPolicy)
            .where(scope, PaymentPolicy.active.is_(True))
            .order_by(PaymentPolicy.created_at.desc())
            .limit(1)
        ).first()
        if row:
            return Policy(version=row.version, config=PolicyConfig.model_validate(row.config))
    return Policy(version=DEFAULT_VERSION, config=PolicyConfig())


def evidence_tier_for_source(source: str) -> EvidenceTier:
    return {
        "statement": EvidenceTier.E3,
        "gateway": EvidenceTier.E3,
        "alert": EvidenceTier.E2,
        "manual": EvidenceTier.E2,
    }.get(source, EvidenceTier.E1)


# Reason codes that force Red handling (blueprint 4.5 approval tiers).
RED_REASONS = {
    "DUPLICATE_FILE",
    "DUPLICATE_NEAR_FILE",
    "DUPLICATE_REFERENCE",
    "REFERENCE_ALREADY_CONSUMED",
    "CREDIT_NOT_SUCCESS",
    "WRONG_BENEFICIARY",
    "CREDIT_FULLY_ALLOCATED",
    "CURRENCY_MISMATCH",
    "AMOUNT_MISMATCH",
}


def approval_tier(reasons: list[str], evidence_tier: EvidenceTier, gates_passed: bool) -> str:
    if RED_REASONS.intersection(reasons):
        return ApprovalTier.RED
    if gates_passed and evidence_tier in (EvidenceTier.E2, EvidenceTier.E3) and not reasons:
        return ApprovalTier.GREEN
    return ApprovalTier.AMBER


def policy_snapshot(policy: Policy) -> dict[str, Any]:
    return {"version": policy.version, "config": policy.config.model_dump()}
