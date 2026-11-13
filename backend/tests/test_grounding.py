"""Grounding gate: a single confident reader's amount/reference must appear in the document text."""

from app.extraction.agreement import combine
from app.extraction.grounding import amount_grounded, is_grounded, reference_grounded
from app.extraction.schema import FieldReading

CRITICAL = ["amount", "transaction_id"]


def _vlm_only(amount: str, ref: str) -> dict:
    return {
        "vlm": (
            "v1",
            {
                "amount": FieldReading("amount", amount, 0.99),
                "currency": FieldReading("currency", "USD", 0.99),
                "transaction_id": FieldReading("transaction_id", ref, 0.99),
            },
        )
    }


def test_amount_grounding_handles_formats():
    assert amount_grounded(12050, "USD", "Paid USD 120.50 to ACME")
    assert amount_grounded(12050, "USD", "Total 120,50")
    assert not amount_grounded(12050, "USD", "Paid USD 150.20")


def test_reference_grounding_ignores_spacing_and_case():
    assert reference_grounded("TXN8841ABC", "Ref: txn 8841-abc done")
    assert not reference_grounded("TXN8841ABC", "Ref: TXN8841ABD")
    assert not reference_grounded("AB", "AB AB")  # too short to ground anything


def test_other_fields_not_gated():
    assert is_grounded("date", "2026-01-01", None, "anything")
    assert not is_grounded("amount", None, "USD", "120.50")


def test_ungrounded_single_reader_value_is_not_agreed():
    out = combine(_vlm_only("999.00", "TXN9999ZZZ"), CRITICAL, 0.97, "Paid 120.50 ref TXN8841ABC")
    assert not out["amount"].agreed
    assert not out["transaction_id"].agreed


def test_grounded_single_reader_value_is_agreed():
    out = combine(_vlm_only("120.50", "TXN8841ABC"), CRITICAL, 0.97, "Paid 120.50 ref TXN8841ABC")
    assert out["amount"].agreed
    assert out["transaction_id"].agreed


def test_no_source_text_keeps_existing_behaviour():
    out = combine(_vlm_only("999.00", "TXN9999ZZZ"), CRITICAL, 0.97, "")
    assert out["amount"].agreed
