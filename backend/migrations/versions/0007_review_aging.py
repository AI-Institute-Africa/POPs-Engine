"""review aging

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-02 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("claims", sa.Column("review_entered_at", sa.DateTime(timezone=True)))
    op.add_column(
        "claims",
        sa.Column("review_escalation", sa.Integer(), nullable=False, server_default="0"),
    )
    # claims already waiting for a human start their clock at the last update
    op.execute(
        "UPDATE claims SET review_entered_at = updated_at "
        "WHERE state IN ('PENDING_REVIEW', 'DUPLICATE_HOLD')"
    )


def downgrade() -> None:
    op.drop_column("claims", "review_escalation")
    op.drop_column("claims", "review_entered_at")
