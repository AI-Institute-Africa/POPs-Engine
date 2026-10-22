"""Pasted text, forwarded emails, and several proofs for one payment."""

import uuid
from email.message import EmailMessage

from sqlalchemy import select

from app.models import ExtractedField
from tests.conftest import login, make_receipt
from tests.test_documents import RECEIPT_TEXT, _internal, _pdf
from tests.test_flow import TXN, _import, _obligation, _stmt_row

SMS = (
    f"Confirmed. {TXN}. You have paid USD 40.00 to COGNIFY MAIN on 21/10/2026 14:32. "
    "EcoCash Payment Successful. Txn ID: " + TXN + " Amount: USD 40.00"
)


def _submit(client, h, ob, files=None, text=None, expect=201):
    r = client.post(
        "/claims",
        headers=h,
        files=files or None,
        data={
            "obligation_id": ob["obligation_id"],
            "claimant_ref": "learner-1",
            "channel": "ecocash",
            **({"text": text} if text is not None else {}),
        },
    )
    assert r.status_code == expect, r.text
    return r.json()


def _signals(client, claim):
    return {s["code"] for s in _internal(client, claim["claim_id"])["risk_signals"]}


def test_pasted_sms_text_is_read_and_matched(client):
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    ob = _obligation(client, sub)
    _import(client, rev, [_stmt_row()])
    claim = _submit(client, sub, ob, text="\n".join(RECEIPT_TEXT))
    assert claim["state"] == "FINANCIALLY_CONFIRMED", claim["reason_codes"]
    # pasted text is weak on its own, so it is a (dampened) signal, never a block
    assert "PASTED_TEXT" in _signals(client, claim)


def test_pasted_text_without_a_bank_record_waits_for_the_money(client):
    sub = login(client, "submitter")
    ob = _obligation(client, sub)
    claim = _submit(client, sub, ob, text="\n".join(RECEIPT_TEXT))
    assert claim["state"] == "AWAITING_FINANCIAL_CONFIRMATION", claim["reason_codes"]


def test_a_claim_needs_a_file_or_text(client):
    sub = login(client, "submitter")
    ob = _obligation(client, sub)
    _submit(client, sub, ob, text="   ", expect=422)


def test_forwarded_email_body_and_attachment_are_read(client, session):
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    ob = _obligation(client, sub)
    _import(client, rev, [_stmt_row()])
    msg = EmailMessage()
    msg["From"] = "payer@gmail.com"
    msg["To"] = "fees@cognify.example"
    msg["Subject"] = "Fwd: proof of payment"
    msg["Date"] = "Wed, 21 Oct 2026 15:00:00 +0200"
    msg.set_content("Hi, please find my proof of payment attached.")
    # the fake readers only see the top-level file, so the receipt rides as a digital PDF
    msg.add_attachment(
        _pdf([RECEIPT_TEXT]), maintype="application", subtype="pdf", filename="pop.pdf"
    )
    claim = _submit(client, sub, ob, files=[("file", ("fwd.eml", bytes(msg), "message/rfc822"))])
    assert claim["state"] == "FINANCIALLY_CONFIRMED", claim["reason_codes"]
    fields = session.scalars(
        select(ExtractedField).where(ExtractedField.claim_id == uuid.UUID(claim["claim_id"]))
    ).all()
    assert {f.name for f in fields} >= {"amount", "transaction_id"}


def test_several_proofs_for_one_payment_are_read_together(client, session):
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    ob = _obligation(client, sub)
    _import(client, rev, [_stmt_row()])
    # a photo the readers cannot read, plus the confirmation SMS pasted as text
    blank = make_receipt({})
    claim = _submit(client, sub, ob, files=[("file", ("photo.png", blank, "image/png"))], text=SMS)
    assert claim["state"] == "FINANCIALLY_CONFIRMED", claim["reason_codes"]
    detail = client.get(f"/review/claims/{claim['claim_id']}", headers=rev).json()
    assert len(detail["evidence"]) == 2


def test_proofs_that_disagree_go_to_review_with_a_signal(client):
    sub = login(client, "submitter")
    ob = _obligation(client, sub)
    a = make_receipt({"amount": "40.00"})
    b = make_receipt({"amount": "400.00"})
    claim = _submit(
        client,
        sub,
        ob,
        files=[("file", ("a.png", a, "image/png")), ("file", ("b.png", b, "image/png"))],
    )
    assert claim["state"] != "FINANCIALLY_CONFIRMED"
    internal = _internal(client, claim["claim_id"])
    assert "PROOFS_DISAGREE" in internal["reason_codes"]
    assert "PROOFS_DISAGREE" in _signals(client, claim)


def test_too_many_files_are_refused(client):
    sub = login(client, "submitter")
    ob = _obligation(client, sub)
    files = [
        ("file", (f"{i}.png", make_receipt({"amount": f"{i}.00"}), "image/png")) for i in range(6)
    ]
    r = _submit(client, sub, ob, files=files, expect=422)
    assert r["detail"]["code"] == "TOO_MANY_FILES"


def test_confirmed_claims_export_as_a_labelled_set(client, tmp_path, monkeypatch):
    import json
    import sys

    from scripts import export_labelled_set

    sub, rev = login(client, "submitter"), login(client, "reviewer")
    ob = _obligation(client, sub)
    _import(client, rev, [_stmt_row()])
    claim = _submit(client, sub, ob, text="\n".join(RECEIPT_TEXT))
    detail = client.get(f"/review/claims/{claim['claim_id']}", headers=rev).json()
    credit_id = detail["candidates"][0]["credit"]["credit_id"]
    r = client.post(
        f"/review/claims/{claim['claim_id']}/confirm",
        headers=rev,
        json={"credit_id": credit_id, "txn_last4": TXN[-4:]},
    )
    assert r.status_code == 200, r.text
    monkeypatch.setattr(sys, "argv", ["export", str(tmp_path)])
    export_labelled_set.main()
    labels = [json.loads(x) for x in (tmp_path / "labels.jsonl").read_text().splitlines()]
    assert labels == [
        {
            "file": f"{claim['claim_id']}.txt",
            "amount": "40.00",
            "currency": "USD",
            "transaction_id": TXN,
            "channel": "ecocash",
            "provider": "BANK",
            "content_type": "text/plain",
        }
    ]
    assert (tmp_path / labels[0]["file"]).read_bytes().startswith(b"EcoCash")
