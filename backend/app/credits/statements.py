"""Authorised statement CSV import (pipeline stage 6) -> normalised credits.

Column layouts differ per bank and export, so each layout is a configurable profile rather than
code. Only credit lines become credits; debits are counted in the control totals and ignored.
"""

import csv
import hashlib
import io
import json
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.config import get_settings
from app.core.money import MoneyError, normalise_currency, parse_minor
from app.models import Credit, StatementImport
from app.normalise.fields import normalise_reference, normalise_status, parse_datetime


class StatementProfile(BaseModel):
    name: str
    provider: str
    date_col: str
    date_format: str | None = None
    narrative_col: str
    reference_col: str | None = None
    credit_col: str | None = None  # separate credit/debit columns...
    debit_col: str | None = None
    amount_col: str | None = None  # ...or one signed amount column
    currency_col: str | None = None
    currency: str | None = None  # fixed currency for single-currency accounts
    payer_col: str | None = None
    status_col: str | None = None


# Starter profiles; add real bank layouts from Phase 0 samples as configuration.
BUILTIN_PROFILES: dict[str, StatementProfile] = {
    "generic": StatementProfile(
        name="generic",
        provider="BANK",
        date_col="date",
        narrative_col="narrative",
        reference_col="reference",
        credit_col="credit",
        debit_col="debit",
        currency_col="currency",
    ),
    "ecocash_merchant": StatementProfile(
        name="ecocash_merchant",
        provider="ECOCASH",
        date_col="Date",
        narrative_col="Description",
        reference_col="Transaction ID",
        amount_col="Amount",
        currency_col="Currency",
        payer_col="Counterparty",
        status_col="Status",
    ),
}


@lru_cache
def profiles() -> dict[str, StatementProfile]:
    """Built-in layouts plus POPS_STATEMENT_PROFILES_FILE (JSON list of profiles)."""
    out = dict(BUILTIN_PROFILES)
    path = get_settings().statement_profiles_file
    if path:
        for item in json.loads(Path(path).read_text()):
            prof = StatementProfile.model_validate(item)
            out[prof.name] = prof
    return out


class ImportError_(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class ImportResult:
    statement_import: StatementImport
    created: list[uuid.UUID]
    duplicates: int
    errors: list[str]


def import_statement(
    session: Session,
    *,
    org_id: uuid.UUID,
    data: bytes,
    profile: StatementProfile,
    account_alias: str,
    actor: str,
) -> ImportResult:
    file_hash = hashlib.sha256(data).hexdigest()
    if session.scalars(
        select(StatementImport).where(
            StatementImport.org_id == org_id, StatementImport.file_sha256 == file_hash
        )
    ).first():
        raise ImportError_("DUPLICATE_IMPORT", "this statement file was already imported")

    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportError_("BAD_ENCODING", "statement must be UTF-8 CSV") from exc
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise ImportError_("EMPTY", "no rows in statement")

    imp = StatementImport(
        org_id=org_id,
        account_alias=account_alias,
        profile=profile.name,
        file_sha256=file_hash,
        imported_by=actor,
        row_count=len(rows),
        credit_rows=0,
        duplicate_rows=0,
        control_totals={},
    )
    session.add(imp)
    session.flush()

    created: list[uuid.UUID] = []
    errors: list[str] = []
    totals: dict[str, int] = {}
    duplicates = 0
    for i, row in enumerate(rows, start=2):  # line 1 is the header
        try:
            parsed = _parse_row(row, profile)
        except (MoneyError, ValueError) as exc:
            errors.append(f"line {i}: {exc}")
            continue
        if parsed is None:
            continue  # debit or zero line
        amount, currency, occurred, narrative, ref, payer, status = parsed
        totals[currency] = totals.get(currency, 0) + amount
        if not ref:
            # no bank reference: a stable synthetic key so re-imports of overlapping periods dedupe
            ref = (
                "SYN"
                + hashlib.sha256(
                    f"{account_alias}|{occurred.isoformat()}|{amount}|{currency}|{narrative}".encode()
                )
                .hexdigest()[:24]
                .upper()
            )
        stmt = (
            insert(Credit)
            .values(
                credit_id=uuid.uuid4(),
                org_id=org_id,
                source="statement",
                provider=profile.provider,
                provider_txn_id=ref,
                reference_norm=normalise_reference(ref) or ref,
                amount_minor=amount,
                currency=currency,
                occurred_at=occurred,
                status=status,
                beneficiary=account_alias,
                payer=payer,
                narrative=narrative,
                source_record_id=f"line:{i}",
                import_id=imp.import_id,
                created_by=actor,
            )
            .on_conflict_do_nothing(constraint="uq_credit_provider_txn")
            .returning(Credit.credit_id)
        )
        credit_id = session.execute(stmt).scalar()
        if credit_id is None:
            duplicates += 1
        else:
            created.append(credit_id)

    imp.credit_rows = len(created) + duplicates
    imp.duplicate_rows = duplicates
    imp.control_totals = totals
    audit(
        session,
        actor=actor,
        action="statement.imported",
        entity_type="statement_import",
        entity_id=imp.import_id,
        org_id=org_id,
        after={
            "rows": len(rows),
            "created": len(created),
            "duplicates": duplicates,
            "errors": len(errors),
            "control_totals": totals,
        },
    )
    return ImportResult(imp, created, duplicates, errors)


def _parse_row(row: dict[str, str], p: StatementProfile):
    def col(name: str | None) -> str:
        return (row.get(name) or "").strip() if name else ""

    currency = normalise_currency(col(p.currency_col)) if p.currency_col else None
    currency = currency or (normalise_currency(p.currency) if p.currency else None)
    if not currency:
        raise ValueError("unknown currency")

    if p.amount_col:
        raw = col(p.amount_col)
        if not raw:
            return None
        amount = parse_minor(raw, currency)
    else:
        credit_raw, debit_raw = col(p.credit_col), col(p.debit_col)
        if not credit_raw or parse_minor(credit_raw, currency) == 0:
            return None
        if debit_raw and parse_minor(debit_raw, currency) != 0:
            raise ValueError("row has both credit and debit")
        amount = parse_minor(credit_raw, currency)
    if amount <= 0:
        return None

    occurred = parse_datetime(col(p.date_col), p.date_format)
    if occurred is None:
        raise ValueError(f"bad date {col(p.date_col)!r}")
    status = normalise_status(col(p.status_col)) if p.status_col else "success"
    if status is None:
        raise ValueError(f"unknown status {col(p.status_col)!r}")
    return (
        amount,
        currency,
        occurred,
        col(p.narrative_col) or None,
        col(p.reference_col) or None,
        col(p.payer_col) or None,
        status,
    )
