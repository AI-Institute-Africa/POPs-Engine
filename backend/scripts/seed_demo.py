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
