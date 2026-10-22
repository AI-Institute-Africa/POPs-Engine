"""Authorised statement import (pipeline stage 6), CSV or PDF -> normalised credits.

Column layouts differ per bank and export, so each layout is a configurable profile rather than
code. Only credit lines become credits; debits are counted in the control totals and ignored.
A PDF statement is read from its digital tables using the same profile column names; scanned
(image-only) statements are refused rather than guessed at.
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

from app.alerts.service import reconcile_with_statement
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


def _key(header: str) -> str:
    return " ".join(header.split()).casefold()


def _profile_columns(profile: StatementProfile) -> list[str]:
    cols = [
        profile.date_col,
        profile.narrative_col,
        profile.reference_col,
        profile.credit_col,
        profile.debit_col,
        profile.amount_col,
        profile.currency_col,
        profile.payer_col,
        profile.status_col,
    ]
    return [c for c in cols if c]


def pdf_rows(data: bytes, profile: StatementProfile, max_pages: int = 200) -> list[dict[str, str]]:
    """Rows of the statement tables in a digital PDF, keyed by the profile's column names.

    A table is used when its header row names the profile's date and narrative columns (case
    and spacing ignored). Tables that continue on the next page without a header reuse the last
    one. Cell text is read from each cell's area, which keeps references and amounts exact.
    """
    import pymupdf

    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001 - any parse failure is a bad file
        raise ImportError_("BAD_PDF", "the PDF statement could not be opened") from exc
    if doc.needs_pass:
        raise ImportError_("ENCRYPTED_PDF", "remove the password from the PDF statement first")
    wanted = {_key(c): c for c in _profile_columns(profile)}
    required = {_key(profile.date_col), _key(profile.narrative_col)}
    # the header's columns as x-ranges: continuation tables on later pages are often split into
    # different cells, so each cell is placed under the header column its centre falls in
    header: list[tuple[float, float, str]] | None = None
    rows: list[dict[str, str]] = []
    saw_text = False
    for page_no in range(min(doc.page_count, max_pages)):
        page = doc[page_no]
        saw_text = saw_text or bool(page.get_text("text").strip())
        for table in page.find_tables().tables:
            for row in table.rows:
                cells = [
                    (c, " ".join(page.get_text("text", clip=c).split())) for c in row.cells if c
                ]
                keys = {_key(t) for _, t in cells}
                if required <= keys:
                    header = [(c[0], c[2], wanted[_key(t)]) for c, t in cells if _key(t) in wanted]
                    continue
                if header is None or not any(t for _, t in cells):
                    continue
                out: dict[str, str] = {}
                for c, t in cells:
                    mid = (c[0] + c[2]) / 2
                    col = next((name for x0, x1, name in header if x0 <= mid <= x1), None)
                    if col and t:
                        out[col] = f"{out[col]} {t}" if col in out else t
                rows.append(out)
    if not rows:
        if not saw_text:
            raise ImportError_(
                "SCANNED_PDF",
                "this PDF is a scan with no digital text; download the statement as CSV "
                "or a digital PDF from online banking",
            )
        raise ImportError_(
            "NO_STATEMENT_TABLE",
            f"no table with the columns {profile.date_col!r} and {profile.narrative_col!r} "
            f"was found for profile {profile.name!r}",
        )
    return rows


@dataclass
class ImportResult:
    statement_import: StatementImport
    created: list[uuid.UUID]
    duplicates: int
    errors: list[str]
    confirmed_alerts: int = 0


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

    if data.startswith(b"%PDF-"):
        rows = pdf_rows(data, profile)
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ImportError_("BAD_ENCODING", "statement must be a UTF-8 CSV or a PDF") from exc
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
    confirmed_alerts = 0
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
        # money an SMS/email alert already reported: confirm that credit instead of adding one
        handled = reconcile_with_statement(
            session,
            {
                "org_id": org_id,
                "reference_norm": normalise_reference(ref) if ref else None,
                "narrative_norm": normalise_reference(narrative) or "",
                "amount_minor": amount,
                "currency": currency,
                "import_id": imp.import_id,
                "created_by": actor,
            },
        )
        if handled == "upgraded":
            confirmed_alerts += 1
            continue
        if handled == "disputed":
            errors.append(
                f"line {i}: contradicts an SMS/email alert for the same reference; "
                "the alert credit is frozen for review"
            )
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

    imp.credit_rows = len(created) + duplicates + confirmed_alerts
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
            "confirmed_alerts": confirmed_alerts,
            "errors": len(errors),
            "control_totals": totals,
        },
    )
    return ImportResult(imp, created, duplicates, errors, confirmed_alerts)


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
