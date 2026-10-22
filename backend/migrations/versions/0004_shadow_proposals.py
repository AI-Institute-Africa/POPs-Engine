"""shadow-mode advisory proposals (blueprint phase 4, innovation 8)

Append-only like decisions: proposals are measurement data for the scorecard, and no
decision path reads them.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shadow_proposals",
        sa.Column("proposal_id", sa.UUID(), nullable=False),
        sa.Column("claim_id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=True),
        sa.Column("proposer", sa.String(length=120), nullable=False),
        sa.Column("proposed_outcome", sa.String(length=20), nullable=False),
        sa.Column("proposed_credit_id", sa.UUID(), nullable=True),
        sa.Column("risk_flags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["claims.claim_id"],
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organisations.org_id"],
        ),
        sa.ForeignKeyConstraint(
            ["proposed_credit_id"],
            ["credits.credit_id"],
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["verification_runs.run_id"],
        ),
        sa.PrimaryKeyConstraint("proposal_id"),
    )
    op.create_index(
        "uq_shadow_claim_proposer", "shadow_proposals", ["claim_id", "proposer"], unique=True
    )
    op.create_index(
        "ix_shadow_org_created", "shadow_proposals", ["org_id", "created_at"], unique=False
    )
    # same append-only guard as allocations/decisions/audit_events (migration 0002)
    op.execute(
        """
        CREATE TRIGGER shadow_proposals_append_only
        BEFORE UPDATE OR DELETE ON shadow_proposals
        FOR EACH ROW EXECUTE FUNCTION pops_forbid_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS shadow_proposals_append_only ON shadow_proposals")
    op.drop_index("ix_shadow_org_created", table_name="shadow_proposals")
    op.drop_index("uq_shadow_claim_proposer", table_name="shadow_proposals")
    op.drop_table("shadow_proposals")
