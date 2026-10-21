"""Turn stored extracted fields (plus typed fields and reviewer corrections) into ClaimFacts."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Claim, ExtractedField, Obligation, VerificationRun
from app.normalise.fields import normalise_reference
from app.reconcile.matching import ClaimFacts


@dataclass
class FieldView:
    value: str | None
    agreed: bool
    corrected: bool


def current_fields(session: Session, claim: Claim) -> dict[str, FieldView]:
    """Latest run's fields, overridden by any later reviewer correction."""
    run_id = session.scalar(
        select(VerificationRun.run_id)
        .where(VerificationRun.claim_id == claim.claim_id)
        .order_by(VerificationRun.created_at.desc())
        .limit(1)
    )
    rows = session.scalars(
        select(ExtractedField)
        .where(ExtractedField.claim_id == claim.claim_id)
        .order_by(ExtractedField.created_at)
    ).all()
    out: dict[str, FieldView] = {}
    for row in rows:
        if row.corrected_by:
            out[row.name] = FieldView(row.normalised_value, True, True)
        elif row.run_id == run_id and not (row.name in out and out[row.name].corrected):
            out[row.name] = FieldView(row.normalised_value, row.agreed, False)
    return out


def build_facts(
    session: Session, claim: Claim, obligation: Obligation
) -> tuple[ClaimFacts, list[str]]:
    """Facts for matching, plus cross-check flags (pipeline stage 5)."""
    fields = current_fields(session, claim)
    flags: list[str] = []

    def agreed(name: str) -> str | None:
        f = fields.get(name)
        return f.value if f and f.agreed else None

    amount_s = agreed("amount")
    amount = int(amount_s) if amount_s else None
    currency = agreed("currency") or claim.declared_currency
    reference = agreed("transaction_id")
    declared_ref = normalise_reference(claim.declared_reference)

    # typed-field cross-check: any disagreement goes to review
    if claim.declared_amount_minor is not None:
        if amount is not None and amount != claim.declared_amount_minor:
            flags.append("TYPED_AMOUNT_DISAGREES")
        amount = amount if amount is not None else claim.declared_amount_minor
    if declared_ref:
        if reference and reference != declared_ref:
            flags.append("TYPED_REFERENCE_DISAGREES")
        reference = reference or declared_ref

    for name in ("amount", "transaction_id", "date"):
        f = fields.get(name)
        if f and f.value and not f.agreed:
            flags.append("EXTRACTION_UNCONFIRMED")
            break

    # "missing" means nobody could read it; a disputed reading is a review case, not missing
    if amount is None and not (fields.get("amount") and fields["amount"].value):
        flags.append("MISSING_AMOUNT")
    if reference is None and not (fields.get("transaction_id") and fields["transaction_id"].value):
        flags.append("MISSING_REFERENCE")

    date_s = agreed("date")
    occurred = datetime.fromisoformat(date_s) if date_s else None
    status = (fields.get("status") or FieldView(None, False, False)).value
    if status and status != "success":
        flags.append("RECEIPT_STATUS_NOT_SUCCESS")

    facts = ClaimFacts(
        org_id=claim.org_id,
        amount_minor=amount,
        currency=currency,
        reference=reference,
        occurred_at=occurred,
        sender=agreed("sender"),
        payment_code=obligation.payment_code,
    )
    return facts, flags
