"""Versioned payment policy (blueprint 4.5, error layer 6).

Policy values are configuration records, never hardcoded in business logic (App. F.1 rule 2).
The defaults below seed the platform policy row; organisations may override with their own.
"""

import uuid
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator
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
    # Rules-based approval of clear claims (app/review/auto.py). The AI never approves:
    # the readers only extract, and these deterministic rules decide. Off by default;
    # enabling needs a cap per currency and the channels it applies to.
    auto_approve_enabled: bool = False
    auto_approve_max_minor: dict[str, int] = Field(default_factory=lambda: {"USD": 5_000})
    auto_approve_channels: list[str] = Field(default_factory=list)
    auto_approve_min_tier: str = "E2"  # E2 = merchant alert, E3 = bank statement
    auto_approve_max_risk: int = Field(default=15, ge=0, le=100)  # fake-proof score
    auto_approve_audit_pct: int = Field(default=5, ge=0, le=100)  # sampled for a human
    # AI temporary approval with mandatory audit and learning (app/review/ai_temp.py).
    # The model only judges recognition; it never bypasses hard gates or the ledger.
    # Off by default; enabling needs channels and caps per currency.
    ai_temp_approve_enabled: bool = False
    ai_temp_approve_disabled: bool = False  # org-level kill switch
    ai_temp_channels: list[str] = Field(default_factory=list)
    ai_temp_min_confidence: float = Field(default=0.9, ge=0.0, le=1.0)
    ai_temp_max_minor: dict[str, int] = Field(default_factory=lambda: {"USD": 2000})
    ai_temp_min_tier: str = "E2"
    ai_temp_max_risk: int = Field(default=10, ge=0, le=100)
    ai_temp_require_human_within_hours: int = Field(default=2, ge=0)
    ai_temp_audit_pct: int = Field(default=100, ge=0, le=100)
    ai_temp_learn: bool = True
    ai_temp_block_on_tamper: bool = True
    # ID/KYC (app/kyc). The AI only reads the document; these deterministic rules decide.
    # off: people decide everything. shadow: the rules are evaluated and recorded but change
    # nothing (calibration). on: a clear case is verified automatically, with a sampled human
    # audit. Never rejects automatically; anything uncertain goes to a reviewer.
    kyc_auto_verify_mode: str = "off"
    # who may be auto-verified: payer | staff | org (staff/org onboarding is for people by default)
    kyc_auto_verify_subjects: list[str] = Field(default_factory=lambda: ["payer"])
    kyc_allowed_doc_types: list[str] = Field(
        default_factory=lambda: ["national_id", "passport", "drivers_licence"]
    )
    # ISO 3166 alpha-3 issuing countries accepted for auto-verification (empty = none)
    kyc_allowed_countries: list[str] = Field(default_factory=list)
    kyc_require_mrz: bool = False  # passports always carry one; many national IDs do not
    kyc_min_ocr_agreement: float = Field(default=1.0, ge=0.0, le=1.0)  # critical fields agreed
    kyc_min_face_score: float = Field(default=0.45, ge=0.0, le=1.0)  # ID photo vs selfie cosine
    kyc_min_liveness: float = Field(default=0.7, ge=0.0, le=1.0)
    kyc_min_expiry_days: int = Field(default=30, ge=0)  # document must be valid this much longer
    kyc_audit_pct: int = Field(default=10, ge=0, le=100)
    kyc_validity_days: int = Field(default=365, gt=0)  # how long a verification stays valid
    kyc_retention_days: int = Field(default=90, gt=0)  # then the images are deleted
    kyc_max_attempts_per_day: int = Field(default=5, gt=0)
    # payments gate: a payer must hold a valid KYC verification before a claim may be
    # auto-approved (review/auto.py). A claim without it simply goes to a person.
    kyc_required_for_auto_approve: bool = False
    # staff gate: APPROVER/ADMIN roles may only be granted to a KYC-verified user
    kyc_required_for_staff: bool = False
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
    # who may allocate a recorded, unclaimed credit to an obligation without a payer claim
    manual_allocation_min_role: str = "approver"
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
    # provisional (time-boxed, capped) access while a receipt waits for the money. Off by
    # default. Caps are per currency in minor units; a currency with no cap is never
    # eligible (fail closed). The host system decides what the access means.
    provisional_enabled: bool = False
    provisional_ttl_hours: int = Field(default=24, gt=0, le=24 * 14)
    provisional_max_minor: dict[str, int] = Field(default_factory=lambda: {"USD": 5_000})
    provisional_learner_cap_minor: dict[str, int] = Field(default_factory=lambda: {"USD": 10_000})
    provisional_org_daily_cap_minor: dict[str, int] = Field(default_factory=lambda: {"USD": 10_000})
    # minutes a claim may wait for a human before admins are nudged again, one nudge per
    # level (ascending)
    review_sla_minutes: list[int] = Field(default_factory=lambda: [60, 240, 1440])
    # tamper forensics (blueprint 8, layer 6): a risk flag only. A hit routes the claim to a
    # reviewer; it can never approve, reject or allocate on its own.
    tamper_enabled: bool = True
    tamper_review_score: float = Field(default=0.6, ge=0.0, le=1.0)

    @field_validator("second_approver_above_minor", mode="before")
    @classmethod
    def _legacy_single_limit(cls, v: Any) -> Any:
        return {"USD": v} if isinstance(v, int) else v

    @field_validator(
        "provisional_max_minor",
        "provisional_learner_cap_minor",
        "provisional_org_daily_cap_minor",
        "auto_approve_max_minor",
        "ai_temp_max_minor",
    )
    @classmethod
    def _positive_caps(cls, v: dict[str, int]) -> dict[str, int]:
        if any(n <= 0 for n in v.values()):
            raise ValueError("provisional caps must be positive minor-unit amounts")
        return v

    @field_validator("auto_approve_min_tier")
    @classmethod
    def _tier_with_independent_confirmation(cls, v: str) -> str:
        if v not in ("E2", "E3"):
            raise ValueError(
                "auto_approve_min_tier must be E2 or E3: a receipt alone is never enough"
            )
        return v

    @field_validator("ai_temp_min_tier")
    @classmethod
    def _ai_temp_tier_with_independent_confirmation(cls, v: str) -> str:
        if v not in ("E2", "E3"):
            raise ValueError("ai_temp_min_tier must be E2 or E3: a receipt alone is never enough")
        return v

    @model_validator(mode="after")
    def _auto_approve_is_scoped(self) -> "PolicyConfig":
        if self.auto_approve_enabled and not (
            self.auto_approve_channels and self.auto_approve_max_minor
        ):
            raise ValueError(
                "auto_approve_enabled needs auto_approve_channels and auto_approve_max_minor"
            )
        if self.ai_temp_approve_enabled and not (self.ai_temp_channels and self.ai_temp_max_minor):
            raise ValueError("ai_temp_approve_enabled needs ai_temp_channels and ai_temp_max_minor")
        return self

    @field_validator("kyc_auto_verify_mode")
    @classmethod
    def _kyc_mode(cls, v: str) -> str:
        if v not in ("off", "shadow", "on"):
            raise ValueError("kyc_auto_verify_mode must be off, shadow or on")
        return v

    @field_validator("kyc_auto_verify_subjects")
    @classmethod
    def _kyc_subjects(cls, v: list[str]) -> list[str]:
        if not set(v) <= {"payer", "staff", "org"}:
            raise ValueError("kyc_auto_verify_subjects may only contain payer, staff, org")
        return v

    @field_validator("kyc_allowed_doc_types")
    @classmethod
    def _kyc_doc_types(cls, v: list[str]) -> list[str]:
        if not set(v) <= {"national_id", "passport", "drivers_licence"}:
            raise ValueError("unknown KYC document type")
        return v

    @field_validator("kyc_allowed_countries")
    @classmethod
    def _kyc_countries(cls, v: list[str]) -> list[str]:
        out = [c.strip().upper() for c in v]
        if any(len(c) != 3 or not c.isalpha() for c in out):
            raise ValueError("kyc_allowed_countries must be ISO 3166 alpha-3 codes, e.g. ZWE")
        return out

    @model_validator(mode="after")
    def _kyc_auto_verify_is_scoped(self) -> "PolicyConfig":
        if self.kyc_auto_verify_mode != "off" and not (
            self.kyc_auto_verify_subjects
            and self.kyc_allowed_doc_types
            and self.kyc_allowed_countries
        ):
            raise ValueError(
                "KYC auto-verification needs kyc_auto_verify_subjects, kyc_allowed_doc_types "
                "and kyc_allowed_countries"
            )
        return self

    @field_validator("review_sla_minutes")
    @classmethod
    def _ascending_positive(cls, v: list[int]) -> list[int]:
        if any(m <= 0 for m in v) or v != sorted(set(v)):
            raise ValueError("review_sla_minutes must be positive and strictly ascending")
        return v

    @field_validator("manual_credit_min_role", "manual_allocation_min_role")
    @classmethod
    def _known_role(cls, v: str) -> str:
        if v not in ("reviewer", "approver", "admin"):
            raise ValueError("role must be reviewer, approver or admin")
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
