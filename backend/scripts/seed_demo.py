"""Seed a running dev server with demo claims and a statement (needs POPS_VLM_BACKEND=fake).

python scripts/seed_demo.py http://127.0.0.1:8000
"""

import sys

import httpx

from app.devtools import ecocash_fields, make_receipt, statement_csv

base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
c = httpx.Client(base_url=base, timeout=60)


def token(email: str, role: str) -> dict:
    r = c.post("/auth/dev-login", json={"email": email, "role": role})
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


sub = token("cognify-backend@cognify.dev", "submitter")
rev = token("reviewer@cognify.dev", "reviewer")


def obligation(learner: str, amount: int) -> dict:
    r = c.post(
        "/obligations",
        headers=sub,
        json={
            "learner_ref": learner,
            "type": "instalment",
            "amount_minor": amount,
            "currency": "USD",
            "description": "Data Science course — instalment 1 of 3",
        },
    )
    r.raise_for_status()
    return r.json()


def claim(ob: dict, png: bytes, **form) -> dict:
    r = c.post(
        "/claims",
        headers=sub,
        data={
            "obligation_id": ob["obligation_id"],
            "claimant_ref": ob["learner_ref"],
            "channel": "ecocash",
            **form,
        },
        files={"file": ("pop.png", png, "image/png")},
    )
    r.raise_for_status()
    return r.json()


def receipt(fields: dict, ocr: dict | None = None) -> bytes:
    lines = [
        "EcoCash",
        "Payment Successful",
        f"Txn ID: {fields.get('transaction_id', '')}",
        f"Amount: USD {fields['amount']}",
        "Paid to: COGNIFY MAIN",
        fields["date"],
    ]
    return make_receipt(fields, ocr, lines=lines)


a, b, d, e = (obligation(n, 4000) for n in ("tendai.m", "rudo.c", "farai.n", "chipo.z"))
claim(a, receipt(ecocash_fields("PP251021.1432.A12345")), declared_amount="40.00")
claim(
    b,
    receipt(
        ecocash_fields("PP251021.1501.B77881"),
        ocr=ecocash_fields("PP251021.1501.B77881", amount="46.00"),
    ),
)
claim(d, receipt(ecocash_fields("PP251021.1611.C40022", status="Pending")))
# an edited receipt: the bank shows USD 40.00 for this reference, the screenshot says 400.00
g = obligation("gift.k", 40000)
claim(
    g,
    make_receipt(
        ecocash_fields("PP251021.1450.E55501", amount="400.00"),
        meta={"Software": "Adobe Photoshop 25.1"},
        lines=[
            "EcoCash",
            "Payment Successful",
            "Txn ID: PP251021.1450.E55501",
            "Amount: USD 400.00",
            "Paid to: COGNIFY MAIN",
            "21/10/2026 14:50",
        ],
    ),
)
dup = receipt(ecocash_fields("PP251021.1702.D99120"))
claim(e, dup)
claim(e, dup)
r = c.post(
    "/credits/imports",
    headers=rev,
    data={"account_alias": "MAIN", "profile": "generic"},
    files={
        "file": (
            "stmt.csv",
            statement_csv(
                [
                    {
                        "date": "21/10/2026 14:35",
                        "narrative": "EcoCash merchant credit",
                        "reference": "PP251021.1432.A12345",
                        "credit": "40.00",
                    },
                    {
                        "date": "21/10/2026 15:03",
                        "narrative": "EcoCash merchant credit",
                        "reference": "PP251021.1501.B77881",
                        "credit": "40.00",
                    },
                    {
                        "date": "21/10/2026 16:00",
                        "narrative": f"school fees {d['payment_code']}",
                        "reference": "FT2629400012",
                        "credit": "40.00",
                    },
                    {
                        "date": "21/10/2026 14:51",
                        "narrative": "EcoCash merchant credit",
                        "reference": "PP251021.1450.E55501",
                        "credit": "40.00",
                    },
                ]
            ),
            "text/csv",
        )
    },
)
r.raise_for_status()
print("import:", r.json())

# recipient-side alert: register the merchant phone and forward one EcoCash credit SMS
admin = token("admin@cognify.dev", "admin")
src = c.post("/alerts/sources", headers=admin, json={
    "kind": "sms", "name": "Merchant phone (EcoCash)", "provider": "ECOCASH",
    "account_alias": "MAIN", "allowed_senders": ["EcoCash"]}).json()
n = obligation("nyasha.p", 4000)
claim(n, receipt(ecocash_fields("PP251021.1810.F70001")))
r = c.post("/alerts/sms", headers={"X-POPs-Source-Key": src["key"]}, json={
    "from": "EcoCash",
    "text": "You have received USD40.00 from NYASHA PFEBVE (0772223344). "
            "Approval Code: PP251021.1810.F70001. New wallet balance: USD1,400.00"})
print("sms alert:", r.json()["status"])
c.post("/alerts/sms", headers={"X-POPs-Source-Key": src["key"]},
       json={"from": "EcoCash", "text": "Your EcoCash one-time PIN is 482910"})

# a two-page PDF where the receipt is on page 2
import pymupdf  # noqa: E402

doc = pymupdf.open()
doc.new_page().insert_text((72, 72), "Proof of payment for Tariro's course fees", fontsize=14)
pg = doc.new_page()
for i, line in enumerate(["EcoCash", "Payment Successful", "Txn ID: PP251021.1900.G80002",
                          "Amount: USD 40.00", "Paid to: COGNIFY MAIN", "21/10/2026 19:00"]):
    pg.insert_text((72, 72 + 24 * i), line, fontsize=14)
t = obligation("tariro.m", 4000)
r = c.post("/claims", headers=sub, files={"file": ("pop.pdf", doc.tobytes(), "application/pdf")},
           data={"obligation_id": t["obligation_id"], "claimant_ref": "tariro.m",
                 "channel": "ecocash"})
print("pdf claim:", r.json()["state"])
