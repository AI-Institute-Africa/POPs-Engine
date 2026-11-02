"""provisional grants

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-02 11:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("claims", sa.Column("provisional_until", sa.DateTime(timezone=True)))
    op.create_table(
        "provisional_grants",
        sa.Column("grant_id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("obligation_id", sa.UUID(), nullable=False),
        sa.Column("claim_id", sa.UUID(), nullable=False),
        sa.Column("learner_ref", sa.String(length=120), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("exposure_minor", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_reason", sa.String(length=40)),
        sa.CheckConstraint(
            "status in ('active','confirmed','revoked','expired')", name="provisional_status"
        ),
        sa.CheckConstraint("exposure_minor > 0", name="provisional_exposure_positive"),
        sa.ForeignKeyConstraint(["claim_id"], ["claims.claim_id"]),
        sa.ForeignKeyConstraint(["obligation_id"], ["obligations.obligation_id"]),
        sa.ForeignKeyConstraint(["org_id"], ["organisations.org_id"]),
        sa.PrimaryKeyConstraint("grant_id"),
    )
    op.create_index(
        "uq_provisional_active_claim",
        "provisional_grants",
        ["claim_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index("ix_provisional_org_status", "provisional_grants", ["org_id", "status"])
    op.create_index("ix_provisional_obligation", "provisional_grants", ["obligation_id"])


def downgrade() -> None:
    op.drop_index("ix_provisional_obligation", table_name="provisional_grants")
    op.drop_index("ix_provisional_org_status", table_name="provisional_grants")
    op.drop_index("uq_provisional_active_claim", table_name="provisional_grants")
    op.drop_table("provisional_grants")
    op.drop_column("claims", "provisional_until")
