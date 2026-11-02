"""CSV export of allocations and reversals, for finance and bookkeeping.

One row per ledger entry (allocation or reversal), signed so a column total is the net amount
paid. Cells that begin with a character spreadsheets treat as a formula are defused, because
names and references can originate with payers.
"""

import csv
import io
import re
import uuid
from datetime import UTC, datetime, time, timedelta
from datetime import date as date_type
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.money import CURRENCY_EXPONENT
from app.models import Allocation, Credit, Obligation

COLUMNS = [
    "allocation_id",
    "created_at_utc",
    "kind",
    "claim_id",
    "obligation_id",
    "external_ref",
    "learner_ref",
    "payment_code",
    "credit_id",
    "credit_source",
    "provider",
    "provider_txn_id",
    "currency",
    "amount",
    "amount_minor",
    "reverses",
    "reason",
    "created_by",
]
MAX_ROWS = 100_000
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")
_PLAIN_NUMBER = re.compile(r"-?\d+(\.\d+)?")


def _decimal(amount_minor: int, currency: str) -> str:
    """A plain signed number in major units (-40.00), without a currency symbol or grouping."""
    exp = CURRENCY_EXPONENT.get(currency, 2)
    return f"{Decimal(amount_minor).scaleb(-exp):.{exp}f}"


class TooManyRows(Exception):
    pass


def safe_cell(value: object) -> object:
    """Prefix text a spreadsheet would run as a formula; numbers and dates pass through."""
    if (
        isinstance(value, str)
        and value.startswith(_FORMULA_STARTS)
        and not _PLAIN_NUMBER.fullmatch(value)
    ):
        return "'" + value
    return "" if value is None else value


def _bounds(
    since: date_type | None, until: date_type | None
) -> tuple[datetime | None, datetime | None]:
    lo = datetime.combine(since, time.min, tzinfo=UTC) if since else None
    hi = datetime.combine(until, time.min, tzinfo=UTC) + timedelta(days=1) if until else None
    return lo, hi


def export_csv(
    session: Session,
    org_id: uuid.UUID,
    *,
    since: date_type | None = None,
    until: date_type | None = None,
    kind: str | None = None,
) -> tuple[bytes, int]:
    """Return (csv bytes, row count) for the dates given, inclusive, in UTC."""
    q = (
        select(Allocation, Credit, Obligation)
        .join(Credit, Credit.credit_id == Allocation.credit_id)
        .join(Obligation, Obligation.obligation_id == Allocation.obligation_id)
        .where(Allocation.org_id == org_id)
        .order_by(Allocation.created_at, Allocation.allocation_id)
        .limit(MAX_ROWS + 1)
    )
    lo, hi = _bounds(since, until)
    if lo is not None:
        q = q.where(Allocation.created_at >= lo)
    if hi is not None:
        q = q.where(Allocation.created_at < hi)
    if kind is not None:
        q = q.where(Allocation.kind == kind)
    rows = session.execute(q).all()
    if len(rows) > MAX_ROWS:
        raise TooManyRows(f"more than {MAX_ROWS} rows; narrow the date range")

    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    for alloc, credit, ob in rows:
        signed = -alloc.amount_minor if alloc.kind == "reversal" else alloc.amount_minor
        writer.writerow(
            [
                safe_cell(v)
                for v in (
                    alloc.allocation_id,
                    alloc.created_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    alloc.kind,
                    alloc.claim_id,
                    ob.obligation_id,
                    ob.external_ref,
                    ob.learner_ref,
                    ob.payment_code,
                    credit.credit_id,
                    credit.source,
                    credit.provider,
                    credit.provider_txn_id,
                    alloc.currency,
                    _decimal(signed, alloc.currency),
                    signed,
                    alloc.reverses,
                    alloc.reason,
                    alloc.created_by,
                )
            ]
        )
    # UTF-8 with a byte-order mark so Excel reads names correctly
    return ("﻿" + out.getvalue()).encode("utf-8"), len(rows)
