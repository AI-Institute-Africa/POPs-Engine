"""Relational model (blueprint 4.2, 7.2, App. D.2).

Money is stored as BIGINT minor units plus a CHAR(3) currency. Ledger, decision and audit
tables are append-only; database triggers in the initial migration enforce this.
"""

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ClaimState(StrEnum):
    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    NEEDS_INFORMATION = "NEEDS_INFORMATION"
    DOCUMENT_VERIFIED = "DOCUMENT_VERIFIED"
    AWAITING_FINANCIAL_CONFIRMATION = "AWAITING_FINANCIAL_CONFIRMATION"
    FINANCIALLY_CONFIRMED = "FINANCIALLY_CONFIRMED"
    PARTIALLY_ALLOCATED = "PARTIALLY_ALLOCATED"
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    DUPLICATE_HOLD = "DUPLICATE_HOLD"
    REVERSED_OR_REFUNDED = "REVERSED_OR_REFUNDED"


class EvidenceTier(StrEnum):
    E0 = "E0"  # claim only
    E1 = "E1"  # document-consistent
    E2 = "E2"  # source-corroborated (alert, attested manual confirmation)
    E3 = "E3"  # settled (statement line, signed gateway callback, provider record)


class ApprovalTier(StrEnum):
    GREEN = "GREEN"
    AMBER = "AMBER"
    RED = "RED"


class ObligationType(StrEnum):
    COURSE_FEE = "course_fee"
    INSTALMENT = "instalment"
    SUBSCRIPTION = "subscription"
    USAGE = "usage"


class CreditSource(StrEnum):
    STATEMENT = "statement"
    ALERT = "alert"
    GATEWAY = "gateway"
    MANUAL = "manual"


class Organisation(Base):
    __tablename__ = "organisations"
    org_id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = _created()


class User(Base):
    __tablename__ = "users"
    user_id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organisations.org_id"), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = _created()


class PaymentPolicy(Base):
    """Versioned policy config. org_id NULL is the platform default."""

    __tablename__ = "payment_policies"
    policy_id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organisations.org_id"))
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_by: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("org_id", "version"),)


class Obligation(Base):
    __tablename__ = "obligations"
    obligation_id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organisations.org_id"), nullable=False)
    external_ref: Mapped[str | None] = mapped_column(String(120))  # Cognify invoice id
    learner_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    payment_code: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    due_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="obligation_amount_positive"),
        UniqueConstraint("org_id", "external_ref"),
    )


class Claim(Base):
    __tablename__ = "claims"
    claim_id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organisations.org_id"), nullable=False)
    obligation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("obligations.obligation_id"), nullable=False
    )
    claimant_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    submitted_by: Mapped[str] = mapped_column(String(80), nullable=False)
    channel: Mapped[str] = mapped_column(String(40), nullable=False)  # ecocash, bank_transfer…
    declared_amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    declared_currency: Mapped[str | None] = mapped_column(String(3))
    declared_reference: Mapped[str | None] = mapped_column(String(80))
    state: Mapped[str] = mapped_column(String(40), nullable=False, default=ClaimState.RECEIVED)
    evidence_tier: Mapped[str] = mapped_column(String(2), nullable=False, default=EvidenceTier.E0)
    approval_tier: Mapped[str | None] = mapped_column(String(5))
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    candidate_credit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("credits.credit_id"))
    # fraud risk (app/fraud): a flag for humans, never an approval input
    risk_score: Mapped[int | None] = mapped_column(Integer)
    risk_level: Mapped[str | None] = mapped_column(String(10))
    risk_signals: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    pipeline_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_pipeline_error: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=_uuid)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    evidence: Mapped[list["Evidence"]] = relationship(back_populates="claim")
    __table_args__ = (Index("ix_claims_org_state", "org_id", "state"),)


class Evidence(Base):
    __tablename__ = "evidence"
    evidence_id: Mapped[uuid.UUID] = _pk()
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.claim_id"), nullable=False)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organisations.org_id"), nullable=False)
    object_key: Mapped[str] = mapped_column(String(300), nullable=False)
    preview_key: Mapped[str | None] = mapped_column(String(300))  # rendered PNG for viewer
    forensics_key: Mapped[str | None] = mapped_column(String(300))  # ELA heatmap for admins
    content_type: Mapped[str] = mapped_column(String(80), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    phash: Mapped[str | None] = mapped_column(String(32))
    # same 64-bit hash as a signed integer so Hamming distance runs in SQL (bit_count)
    phash_int: Mapped[int | None] = mapped_column(BigInteger)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False, default="upload")
    received_at: Mapped[datetime] = _created()

    claim: Mapped[Claim] = relationship(back_populates="evidence")


class VerificationRun(Base):
    __tablename__ = "verification_runs"
    run_id: Mapped[uuid.UUID] = _pk()
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.claim_id"), nullable=False)
    pipeline_version: Mapped[str] = mapped_column(String(40), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(40), nullable=False)
    model_versions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    flags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = _created()


class ExtractedField(Base):
    __tablename__ = "extracted_fields"
    field_id: Mapped[uuid.UUID] = _pk()
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.claim_id"), nullable=False)
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence.evidence_id"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("verification_runs.run_id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    raw_value: Mapped[str | None] = mapped_column(Text)
    normalised_value: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    extractor: Mapped[str] = mapped_column(String(40), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(80), nullable=False)
    region: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # {page,x,y,w,h} 0..1
    agreed: Mapped[bool] = mapped_column(default=False, nullable=False)
    # reviewer corrections keep the original row and add a new one with corrected_by set
    corrected_by: Mapped[str | None] = mapped_column(String(80))
    correction_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class StatementImport(Base):
    __tablename__ = "statement_imports"
    import_id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organisations.org_id"), nullable=False)
    account_alias: Mapped[str] = mapped_column(String(80), nullable=False)
    profile: Mapped[str] = mapped_column(String(40), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    imported_by: Mapped[str] = mapped_column(String(80), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    credit_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    duplicate_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    control_totals: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("org_id", "file_sha256"),)


class Credit(Base):
    __tablename__ = "credits"
    credit_id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organisations.org_id"), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_txn_id: Mapped[str] = mapped_column(String(120), nullable=False)
    reference_norm: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    beneficiary: Mapped[str] = mapped_column(String(120), nullable=False)  # our account alias
    payer: Mapped[str | None] = mapped_column(String(200))
    narrative: Mapped[str | None] = mapped_column(Text)
    source_record_id: Mapped[str | None] = mapped_column(String(120))
    import_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("statement_imports.import_id"))
    created_by: Mapped[str] = mapped_column(String(80), nullable=False)
    attestation: Mapped[str | None] = mapped_column(Text)  # manual credits: evidence reference
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        UniqueConstraint("provider", "provider_txn_id", name="uq_credit_provider_txn"),
        CheckConstraint("amount_minor > 0", name="credit_amount_positive"),
        Index("ix_credits_org_occurred", "org_id", "occurred_at"),
    )


class Allocation(Base):
    """Append-only. A reversal is a new row with kind='reversal' and reverses=<original>."""

    __tablename__ = "allocations"
    allocation_id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organisations.org_id"), nullable=False)
    credit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("credits.credit_id"), nullable=False)
    obligation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("obligations.obligation_id"), nullable=False
    )
    claim_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("claims.claim_id"))
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default="allocation")
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    reverses: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("allocations.allocation_id"), unique=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    created_by: Mapped[str] = mapped_column(String(80), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="allocation_amount_positive"),
        CheckConstraint("kind in ('allocation','reversal')", name="allocation_kind"),
        CheckConstraint(
            "(kind = 'reversal') = (reverses IS NOT NULL)", name="reversal_links_original"
        ),
        Index("ix_allocations_credit", "credit_id"),
        Index("ix_allocations_obligation", "obligation_id"),
    )


class ConsumedReference(Base):
    """Global registry (cross-organisation) of transaction references already credited."""

    __tablename__ = "consumed_references"
    reference_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organisations.org_id"), nullable=False)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.claim_id"), nullable=False)
    created_at: Mapped[datetime] = _created()


class Decision(Base):
    __tablename__ = "decisions"
    decision_id: Mapped[uuid.UUID] = _pk()
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.claim_id"), nullable=False)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organisations.org_id"), nullable=False)
    outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    policy_version: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(10), nullable=False)  # rule | reviewer
    actor_id: Mapped[str] = mapped_column(String(80), nullable=False)
    credit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("credits.credit_id"))
    evidence_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class AuditEvent(Base):
    __tablename__ = "audit_events"
    event_id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organisations.org_id"))
    actor: Mapped[str] = mapped_column(String(80), nullable=False)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(80), nullable=False)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _created()
    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id"),)


class OutboxEvent(Base):
    """Transactional outbox for Cognify entitlement webhooks; event_key dedupes."""

    __tablename__ = "outbox_events"
    event_id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organisations.org_id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    event_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class GuardrailRule(Base):
    """An admin-managed guardrail on the AI readers (see app/guardrails).

    Guardrails can only make the pipeline stricter: every action escalates a claim to review or
    asks for information; none can approve, allocate or relax a hard gate. Removal is a soft delete
    so past verification runs stay explainable.
    """

    __tablename__ = "guardrail_rules"
    rule_id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organisations.org_id"))
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_by: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = _created()
    removed_by: Mapped[str | None] = mapped_column(String(80))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_guardrails_org_active", "org_id", "removed_at"),)


class FraudAlert(Base):
    """A claim the risk engine thinks may be fake, escalated to an admin.

    One alert per claim; re-assessment updates it. While a high-risk alert is open the claim
    cannot be confirmed. Admins resolve it as confirmed_fake (claim rejected) or false_positive
    (claim returns to normal review); resolutions are labelled data for a future fraud model.
    """

    __tablename__ = "fraud_alerts"
    alert_id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organisations.org_id"), nullable=False)
    claim_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("claims.claim_id"), nullable=False, unique=True
    )
    level: Mapped[str] = mapped_column(String(10), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    signals: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    resolved_by: Mapped[str | None] = mapped_column(String(80))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    __table_args__ = (
        CheckConstraint(
            "status in ('open','confirmed_fake','false_positive')", name="fraud_alert_status"
        ),
        Index("ix_fraud_alerts_org_status", "org_id", "status"),
    )


class AlertSource(Base):
    """A registered, trusted origin of recipient-side payment alerts.

    An SMS forwarder on the merchant phone, or a mailbox that receives bank/mobile-money credit
    notifications. Only registered sources with a valid key are accepted (threat model 4.7:
    spoofed alerts), and alert credits are capped at evidence tier E2.
    """

    __tablename__ = "alert_sources"
    source_id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organisations.org_id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # sms | email
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)  # e.g. ECOCASH, CBZ
    account_alias: Mapped[str] = mapped_column(String(80), nullable=False)  # our account
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # SMS sender IDs (e.g. "EcoCash", "+263...") or email addresses/domains; empty = any
    allowed_senders: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    require_dkim: Mapped[bool] = mapped_column(default=True, nullable=False)
    template: Mapped[str | None] = mapped_column(String(40))  # None = try all templates
    active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_by: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = _created()
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stale_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("kind in ('sms','email')", name="alert_source_kind"),)


class AlertMessage(Base):
    """Every alert received, kept for audit whether or not it became a credit."""

    __tablename__ = "alert_messages"
    message_id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organisations.org_id"), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("alert_sources.source_id"), nullable=False
    )
    sender: Mapped[str] = mapped_column(String(200), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    body_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # credited | duplicate | ignored | rejected | unparsed | conflict
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(200))
    parsed: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    credit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("credits.credit_id"))
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        UniqueConstraint("source_id", "body_sha256", name="uq_alert_message_dedupe"),
        Index("ix_alert_messages_org_created", "org_id", "created_at"),
    )
