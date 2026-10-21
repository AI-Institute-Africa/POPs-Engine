"""Ledger invariants and append-only guards (blueprint 7.2, error layer 7).

These triggers are the last line of defence: even a buggy service cannot over-allocate a credit
or an obligation, mix currencies, or rewrite financial history.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY = ("allocations", "decisions", "audit_events", "consumed_references")


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION pops_forbid_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'append-only table %: % is not allowed', TG_TABLE_NAME, TG_OP
            USING ERRCODE = 'P0001';
        END $$ LANGUAGE plpgsql;
        """
    )
    for table in APPEND_ONLY:
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION pops_forbid_mutation();"
        )

    op.execute(
        """
        CREATE FUNCTION pops_check_allocation() RETURNS trigger AS $$
        DECLARE
          c credits%ROWTYPE;
          o obligations%ROWTYPE;
          orig allocations%ROWTYPE;
          credit_net bigint;
          obligation_net bigint;
        BEGIN
          -- row locks serialise concurrent allocations against the same credit/obligation
          SELECT * INTO c FROM credits WHERE credit_id = NEW.credit_id FOR UPDATE;
          SELECT * INTO o FROM obligations WHERE obligation_id = NEW.obligation_id FOR UPDATE;

          IF c.org_id <> NEW.org_id OR o.org_id <> NEW.org_id THEN
            RAISE EXCEPTION 'allocation crosses organisations' USING ERRCODE = 'P0002';
          END IF;
          IF NEW.currency <> c.currency OR NEW.currency <> o.currency THEN
            RAISE EXCEPTION 'currency mismatch' USING ERRCODE = 'P0003';
          END IF;

          IF NEW.kind = 'reversal' THEN
            SELECT * INTO orig FROM allocations WHERE allocation_id = NEW.reverses;
            IF orig.kind <> 'allocation' OR orig.credit_id <> NEW.credit_id
               OR orig.obligation_id <> NEW.obligation_id OR orig.amount_minor <> NEW.amount_minor THEN
              RAISE EXCEPTION 'reversal does not match its original' USING ERRCODE = 'P0004';
            END IF;
            RETURN NEW;
          END IF;

          IF c.status <> 'success' THEN
            RAISE EXCEPTION 'credit % is not settled (%)', c.credit_id, c.status
              USING ERRCODE = 'P0005';
          END IF;

          SELECT COALESCE(SUM(CASE WHEN kind = 'allocation' THEN amount_minor ELSE -amount_minor END), 0)
            INTO credit_net FROM allocations WHERE credit_id = NEW.credit_id;
          IF credit_net + NEW.amount_minor > c.amount_minor THEN
            RAISE EXCEPTION 'over-allocation of credit %', c.credit_id USING ERRCODE = 'P0006';
          END IF;

          SELECT COALESCE(SUM(CASE WHEN kind = 'allocation' THEN amount_minor ELSE -amount_minor END), 0)
            INTO obligation_net FROM allocations WHERE obligation_id = NEW.obligation_id;
          IF obligation_net + NEW.amount_minor > o.amount_minor THEN
            RAISE EXCEPTION 'over-allocation of obligation %', o.obligation_id USING ERRCODE = 'P0007';
          END IF;
          RETURN NEW;
        END $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER allocations_invariants BEFORE INSERT ON allocations "
        "FOR EACH ROW EXECUTE FUNCTION pops_check_allocation();"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS allocations_invariants ON allocations")
    op.execute("DROP FUNCTION IF EXISTS pops_check_allocation()")
    for table in APPEND_ONLY:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    op.execute("DROP FUNCTION IF EXISTS pops_forbid_mutation()")
