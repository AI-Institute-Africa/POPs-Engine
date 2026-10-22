from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

FIELD_NAMES = (
    "provider",
    "document_type",
    "transaction_id",
    "amount",
    "currency",
    "date",
    "sender",
    "beneficiary",
    "status",
    "narrative",
    "tampering_notes",
)


class ReceiptFields(BaseModel):
    """Strict schema the VLM must fill. Every value is a verbatim string copied from the image."""

    provider: str | None = Field(None, description="Payment provider or bank, e.g. EcoCash, CBZ")
    document_type: str | None = Field(
        None,
        description="mobile_money_receipt | bank_transfer | gateway_receipt | pos_slip | "
        "deposit_slip | sms_notice | statement | handwritten | other",
    )
    transaction_id: str | None = Field(
        None, description="Transaction ID / reference / approval code"
    )
    amount: str | None = Field(None, description="Amount paid exactly as printed, e.g. '40.00'")
    currency: str | None = Field(None, description="Currency as printed, e.g. USD, ZiG, $")
    date: str | None = Field(None, description="Transaction date and time as printed")
    sender: str | None = Field(None, description="Payer name or number")
    beneficiary: str | None = Field(None, description="Recipient name, merchant code or account")
    status: str | None = Field(None, description="Status as printed, e.g. Successful, Pending")
    narrative: str | None = Field(None, description="Reference/narrative text the payer entered")
    tampering_notes: str | None = Field(
        None,
        description="Visible signs this image was edited or fabricated (mismatched fonts or "
        "spacing in amounts, misaligned digits, smudged or pasted patches, inconsistent "
        "layout for this provider). null if none are visible.",
    )


def receipt_json_schema() -> dict[str, Any]:
    return ReceiptFields.model_json_schema()


@dataclass
class FieldReading:
    name: str
    raw: str | None
    confidence: float
    region: dict[str, float] | None = None  # {page, x, y, w, h} in 0..1 image coordinates
