import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import ObligationType


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class DevLoginIn(BaseModel):
    email: str
    role: str = "reviewer"
    org_name: str = "Cognify"


class TokenOut(BaseModel):
    access_token: str
    role: str
    org_id: uuid.UUID


class ObligationIn(BaseModel):
    learner_ref: str
    type: ObligationType
    amount_minor: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    description: str = ""
    external_ref: str | None = None
    due_date: date | None = None


class ObligationOut(Out):
    obligation_id: uuid.UUID
    learner_ref: str
    type: str
    description: str
    amount_minor: int
    currency: str
    payment_code: str
    external_ref: str | None
    due_date: date | None
    created_at: datetime


class BalanceOut(BaseModel):
    obligation_id: uuid.UUID
    amount_minor: int
    paid_minor: int
    outstanding_minor: int
    currency: str
    payment_status: str


class ClaimOut(Out):
    claim_id: uuid.UUID
    obligation_id: uuid.UUID
    claimant_ref: str
    channel: str
    declared_amount_minor: int | None
    declared_currency: str | None
    declared_reference: str | None
    state: str
    outcome: str
    evidence_tier: str
    approval_tier: str | None
    reason_codes: list[str]
    messages: list[str]
    candidate_credit_id: uuid.UUID | None
    # fraud risk: filled only for reviewers and admins
    risk_score: int | None = None
    risk_level: str | None = None
    risk_signals: list[dict[str, Any]] = []
    created_at: datetime
    updated_at: datetime


class CreditIn(BaseModel):
    """Manual, attested credit: a named finance user saw the money on the recipient side."""

    provider: str
    provider_txn_id: str
    amount_minor: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    occurred_at: datetime
    beneficiary: str
    payer: str | None = None
    narrative: str | None = None
    attestation: str = Field(min_length=10, description="where the credit was seen, by whom")


class CreditOut(Out):
    credit_id: uuid.UUID
    source: str
    provider: str
    provider_txn_id: str
    amount_minor: int
    currency: str
    occurred_at: datetime
    status: str
    beneficiary: str
    payer: str | None
    narrative: str | None
    created_by: str
    available_minor: int | None = None


class ImportOut(BaseModel):
    import_id: uuid.UUID
    rows: int
    created: int
    duplicates: int
    errors: list[str]
    control_totals: dict[str, int]
    rematched_claims: int | None


class FieldOut(Out):
    field_id: uuid.UUID
    name: str
    raw_value: str | None
    normalised_value: str | None
    confidence: float
    extractor: str
    extractor_version: str
    region: dict[str, Any] | None
    agreed: bool
    corrected_by: str | None
    correction_reason: str | None


class EvidenceOut(BaseModel):
    evidence_id: uuid.UUID
    content_type: str
    sha256: str
    preview_url: str
    original_url: str
    forensics_url: str | None = None  # ELA heatmap (JPEG uploads)
    meta: dict[str, Any]
    received_at: datetime


class CandidateOut(BaseModel):
    credit: CreditOut
    passed: bool
    score: float
    gates: list[dict[str, Any]]
    components: dict[str, float]
    reference_kind: str


class DecisionOut(Out):
    decision_id: uuid.UUID
    outcome: str
    reason_codes: list[str]
    policy_version: str
    actor_type: str
    actor_id: str
    credit_id: uuid.UUID | None
    note: str | None
    created_at: datetime


class AllocationOut(Out):
    allocation_id: uuid.UUID
    credit_id: uuid.UUID
    obligation_id: uuid.UUID
    claim_id: uuid.UUID | None
    kind: str
    amount_minor: int
    currency: str
    reverses: uuid.UUID | None
    created_by: str
    created_at: datetime


class ReviewDetailOut(BaseModel):
    claim: ClaimOut
    obligation: ObligationOut
    balance: BalanceOut
    evidence: list[EvidenceOut]
    fields: list[FieldOut]
    candidates: list[CandidateOut]
    decisions: list[DecisionOut]
    allocations: list[AllocationOut]
    related_claims: list[uuid.UUID]


class ConfirmIn(BaseModel):
    credit_id: uuid.UUID
    txn_last4: str = Field(min_length=4, max_length=4)
    note: str | None = None


class ConfirmOut(BaseModel):
    claim: ClaimOut
    allocation: AllocationOut | None
    needs_second_approval: bool


class RejectIn(BaseModel):
    reason: str
    note: str | None = None


class RequestInfoIn(BaseModel):
    message: str = Field(min_length=3)


class CorrectFieldIn(BaseModel):
    name: str
    value: str
    reason: str = Field(min_length=3)


class ReverseIn(BaseModel):
    reason: str = Field(min_length=3)


class PolicyIn(BaseModel):
    version: str
    config: dict[str, Any]
