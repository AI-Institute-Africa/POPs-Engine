"""identity verification (KYC): verifications, evidence, append-only decisions

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-12 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "kyc_verifications",
        sa.Column("kyc_id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("subject_type", sa.String(length=10), nullable=False),
        sa.Column("subject_ref", sa.String(length=120), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("submitted_by", sa.String(length=80), nullable=False),
        sa.Column("doc_type", sa.String(length=20)),
        sa.Column("issuing_country", sa.String(length=3)),
        sa.Column("doc_number_hash", sa.String(length=64)),
        sa.Column("fields", postgresql.JSONB(), nullable=False),
        sa.Column("checks", postgresql.JSONB(), nullable=False),
        sa.Column("reason_codes", postgresql.JSONB(), nullable=False),
        sa.Column("ocr_agreement", sa.Float()),
        sa.Column("mrz_valid", sa.Boolean()),
        sa.Column("face_score", sa.Float()),
        sa.Column("liveness_score", sa.Float()),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("pipeline_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_pipeline_error", sa.Text()),
        sa.Column("correlation_id", sa.UUID()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("subject_type in ('payer','staff','org')", name="kyc_subject_type"),
        sa.CheckConstraint(
            "state in ('SUBMITTED','PROCESSING','NEEDS_INFORMATION','NEEDS_REVIEW',"
            "'VERIFIED','REJECTED','EXPIRED')",
            name="kyc_state",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organisations.org_id"]),
        sa.PrimaryKeyConstraint("kyc_id"),
    )
    op.create_index("ix_kyc_org_state", "kyc_verifications", ["org_id", "state"])
    op.create_index(
        "ix_kyc_subject", "kyc_verifications", ["org_id", "subject_type", "subject_ref"]
    )
    op.create_index("ix_kyc_doc_hash", "kyc_verifications", ["org_id", "doc_number_hash"])

    op.create_table(
        "kyc_evidence",
        sa.Column("evidence_id", sa.UUID(), nullable=False),
        sa.Column("kyc_id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(length=10), nullable=False),
        sa.Column("object_key", sa.String(length=300), nullable=False),
        sa.Column("content_type", sa.String(length=80), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("phash_int", sa.BigInteger()),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("purged_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("role in ('front','back','selfie')", name="kyc_evidence_role"),
        sa.ForeignKeyConstraint(["kyc_id"], ["kyc_verifications.kyc_id"]),
        sa.ForeignKeyConstraint(["org_id"], ["organisations.org_id"]),
        sa.PrimaryKeyConstraint("evidence_id"),
    )
    op.create_index("ix_kyc_evidence_kyc", "kyc_evidence", ["kyc_id"])
    op.create_index("ix_kyc_evidence_sha256", "kyc_evidence", ["sha256"])

    op.create_table(
        "kyc_decisions",
        sa.Column("decision_id", sa.UUID(), nullable=False),
        sa.Column("kyc_id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("outcome", sa.String(length=40), nullable=False),
        sa.Column("reason_codes", postgresql.JSONB(), nullable=False),
        sa.Column("policy_version", sa.String(length=40), nullable=False),
        sa.Column("actor_type", sa.String(length=10), nullable=False),
        sa.Column("actor_id", sa.String(length=80), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["kyc_id"], ["kyc_verifications.kyc_id"]),
        sa.ForeignKeyConstraint(["org_id"], ["organisations.org_id"]),
        sa.PrimaryKeyConstraint("decision_id"),
    )
    op.create_index("ix_kyc_decisions_kyc", "kyc_decisions", ["kyc_id", "created_at"])
    # same append-only guard as decisions/audit_events (migration 0002)
    op.execute(
        """
        CREATE TRIGGER kyc_decisions_append_only
        BEFORE UPDATE OR DELETE ON kyc_decisions
        FOR EACH ROW EXECUTE FUNCTION pops_forbid_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS kyc_decisions_append_only ON kyc_decisions")
    op.drop_index("ix_kyc_decisions_kyc", table_name="kyc_decisions")
    op.drop_table("kyc_decisions")
    op.drop_index("ix_kyc_evidence_sha256", table_name="kyc_evidence")
    op.drop_index("ix_kyc_evidence_kyc", table_name="kyc_evidence")
    op.drop_table("kyc_evidence")
    op.drop_index("ix_kyc_doc_hash", table_name="kyc_verifications")
    op.drop_index("ix_kyc_subject", table_name="kyc_verifications")
    op.drop_index("ix_kyc_org_state", table_name="kyc_verifications")
    op.drop_table("kyc_verifications")
