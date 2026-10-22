"""Recipient-side SMS and email alerts become trusted credits and confirm claims quickly."""

import uuid
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

import dkim
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select, update

from app.alerts import service as alerts
from app.alerts.parse import NotACredit, parse
from app.core.db import get_sessionmaker
from app.models import AlertSource, Credit, OutboxEvent
from tests.conftest import ecocash_fields, login, make_receipt
from tests.test_flow import TXN, _import, _obligation, _stmt_row, _submit

ECOCASH_SMS = (
    f"You have received USD40.00 from TENDAI MOYO (0771234567). Approval Code: {TXN}. "
    "New wallet balance: USD1,234.00"
)


def _source(client, kind="sms", **over):
    admin = login(client, "admin")
    body = {
        "kind": kind,
        "name": "Merchant phone",
        "provider": "ECOCASH",
        "account_alias": "MAIN",
        "allowed_senders": ["EcoCash"],
        **over,
    }
    r = client.post("/alerts/sources", headers=admin, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _sms(client, key, text=ECOCASH_SMS, sender="EcoCash"):
    return client.post(
        "/alerts/sms", headers={"X-POPs-Source-Key": key}, json={"from": sender, "text": text}
    )


@pytest.mark.parametrize(
    "text",
    [
        ECOCASH_SMS,
        "Payment of USD 25.50 from Rudo Chikwanha 263771112233. Txn ID: MP251021.1501.B77881",
        "Your account has been credited with USD 120.00 on 21/10/2026. Ref: FT2629400012",
    ],
)
def test_parser_reads_incoming_credits(text):
    p = parse(text, "EcoCash")
    assert p.amount_minor > 0 and p.reference


@pytest.mark.parametrize(
    "text",
    [
        "You have sent USD10.00 to JOHN. Approval Code: PP1234567",
        "Your EcoCash one-time PIN is 482910",
        "Your balance is USD 300.00",
    ],
)
def test_parser_ignores_debits_otps_and_balances(text):
    with pytest.raises(NotACredit):
        parse(text, "EcoCash")


def test_sms_alert_confirms_a_waiting_claim(client):
    sub = login(client, "submitter")
    ob = _obligation(client, sub)
    claim = _submit(client, sub, ob, make_receipt(ecocash_fields()))
    assert claim["state"] == "AWAITING_FINANCIAL_CONFIRMATION"
    src = _source(client)
    r = _sms(client, src["key"])
    assert r.status_code == 200 and r.json()["status"] == "credited"
    after = client.get(f"/claims/{claim['claim_id']}", headers=sub).json()
    assert after["state"] == "FINANCIALLY_CONFIRMED"
    assert after["evidence_tier"] == "E2"  # alerts are capped below a bank statement


def test_alert_first_then_claim_matches_immediately(client):
    src = _source(client)
    _sms(client, src["key"])
    sub = login(client, "submitter")
    claim = _submit(client, sub, _obligation(client, sub), make_receipt(ecocash_fields()))
    assert claim["state"] == "FINANCIALLY_CONFIRMED"


def test_untrusted_alerts_are_refused(client, session):
    src = _source(client)
    assert _sms(client, "pops_ak_wrong").status_code == 401
    r = _sms(client, src["key"], sender="+263779999999")  # not the EcoCash sender id
    assert r.json()["status"] == "rejected"
    email_try = client.post(
        "/alerts/email", headers={"X-POPs-Source-Key": src["key"]}, content=b"From: x@y\r\n\r\nhi"
    )
    assert email_try.status_code == 403
    assert session.scalars(select(Credit)).all() == []


def test_duplicates_otps_and_reference_less_alerts_never_create_credits(client, session):
    src = _source(client)
    assert _sms(client, src["key"]).json()["status"] == "credited"
    assert _sms(client, src["key"]).json()["status"] == "credited"  # same message: replayed
    assert (
        _sms(client, src["key"], "Your EcoCash one-time PIN is 482910").json()["status"]
        == "ignored"
    )
    r = _sms(client, src["key"], "You have received USD5.00 from JANE DOE.")
    assert r.json()["status"] == "unparsed"
    assert len(session.scalars(select(Credit)).all()) == 1
    msgs = client.get("/alerts/messages", headers=login(client, "reviewer")).json()
    assert {m["status"] for m in msgs} == {"credited", "ignored", "unparsed"}


def test_statement_confirms_alert_credit_without_double_counting(client, session):
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    src = _source(client)
    _sms(client, src["key"])
    claim = _submit(client, sub, _obligation(client, sub), make_receipt(ecocash_fields()))
    r = _import(client, rev, [_stmt_row()])
    assert r.json()["confirmed_alerts"] == 1 and r.json()["created"] == 0
    credits = session.scalars(select(Credit)).all()
    assert len(credits) == 1 and credits[0].source == "statement"
    assert client.get(f"/claims/{claim['claim_id']}", headers=sub).json()["evidence_tier"] == "E3"


def test_statement_contradicting_alert_freezes_it(client, session):
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    src = _source(client)
    # a spoofed alert claims USD 400, the bank later shows USD 40 for the same reference
    _sms(client, src["key"], ECOCASH_SMS.replace("USD40.00", "USD400.00"))
    ob = _obligation(client, sub, amount=40000)
    claim = _submit(client, sub, ob, make_receipt(ecocash_fields(amount="400.00")))
    assert claim["state"] == "FINANCIALLY_CONFIRMED"
    r = _import(client, rev, [_stmt_row()])
    assert any("contradicts an SMS/email alert" in e for e in r.json()["errors"])
    after = client.get(f"/claims/{claim['claim_id']}", headers=rev).json()
    assert after["state"] == "PENDING_REVIEW" and "CREDIT_DISPUTED" in after["reason_codes"]
    statuses = sorted(c.status for c in session.scalars(select(Credit)))
    assert statuses == ["disputed", "success"]
    assert (
        session.scalars(
            select(OutboxEvent).where(OutboxEvent.event_type == "ops.alert_credit_disputed")
        ).first()
        is not None
    )


def _dkim_keys():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    pub = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    import base64

    txt = b"v=DKIM1; k=rsa; p=" + base64.b64encode(pub)
    return pem, txt


def _email(body: str, sender="alerts@cbz.co.zw") -> bytes:
    m = EmailMessage()
    m["From"] = f"CBZ Alerts <{sender}>"
    m["To"] = "finance@cognify.test"
    m["Subject"] = "Credit alert"
    m["Date"] = "Wed, 21 Oct 2026 14:40:00 +0200"
    m.set_content(body)
    return m.as_bytes()


def test_email_alerts_need_a_valid_dkim_signature(client, session, monkeypatch):
    src = _source(client, kind="email", provider="CBZ", allowed_senders=["@cbz.co.zw"])
    body = f"Your account has been credited with USD 40.00. Ref: {TXN}"
    unsigned = _email(body)
    r = client.post("/alerts/email", headers={"X-POPs-Source-Key": src["key"]}, content=unsigned)
    assert r.json()["status"] == "rejected" and "DKIM" in r.json()["reason"]

    pem, txt = _dkim_keys()
    signed = dkim.sign(
        _email(body + " "),
        b"mail",
        b"cbz.co.zw",
        pem,
        include_headers=[b"from", b"to", b"subject", b"date"],
    ) + _email(body + " ")
    monkeypatch.setattr(
        alerts, "_dkim_ok", lambda raw: dkim.verify(raw, dnsfunc=lambda name, timeout=5: txt)
    )
    r = client.post("/alerts/email", headers={"X-POPs-Source-Key": src["key"]}, content=signed)
    assert r.json()["status"] == "credited", r.json()
    credit = session.scalars(select(Credit)).one()
    assert credit.source == "alert" and credit.amount_minor == 4000


def test_imap_poller_ingests_unseen_messages(client, session, monkeypatch):
    from app.core.config import get_settings

    src = _source(
        client, kind="email", provider="CBZ", allowed_senders=["@cbz.co.zw"], require_dkim=False
    )
    raw = _email(f"Your account has been credited with USD 40.00. Ref: {TXN}")

    class FakeImap:
        flagged: list = []

        def __init__(self, host):
            pass

        def login(self, u, p): ...
        def select(self, folder): ...
        def search(self, charset, criterion):
            return "OK", [b"1"]

        def fetch(self, num, what):
            return "OK", [(b"1 (RFC822)", raw)]

        def store(self, num, op, flag):
            FakeImap.flagged.append(num)

        def logout(self): ...

    s = get_settings()
    monkeypatch.setattr(s, "alert_imap_host", "imap.test")
    monkeypatch.setattr(s, "alert_imap_source_id", src["source_id"])
    with get_sessionmaker()() as db:
        assert alerts.poll_imap(db, imap_factory=FakeImap) == 1
        db.commit()
    assert FakeImap.flagged == ["1"]
    assert session.scalars(select(Credit)).one().provider == "CBZ"


def test_silent_forwarder_alerts_admins_once(client, session):
    src = _source(client)
    _sms(client, src["key"])
    with get_sessionmaker()() as db:
        db.execute(update(AlertSource).values(last_seen_at=datetime.now(UTC) - timedelta(hours=2)))
        db.commit()
        assert alerts.check_stale_sources(db) == 1
        assert alerts.check_stale_sources(db) == 0  # not repeated until it checks in again
        db.commit()
    listing = client.get("/alerts/sources", headers=login(client, "reviewer")).json()
    assert listing[0]["stale"] is True
    client.post("/alerts/heartbeat", headers={"X-POPs-Source-Key": src["key"]})
    assert (
        client.get("/alerts/sources", headers=login(client, "reviewer")).json()[0]["stale"] is False
    )
    assert (
        session.scalars(
            select(OutboxEvent).where(OutboxEvent.event_type == "ops.alert_source_stale")
        ).first()
        is not None
    )


def test_only_admins_register_sources_and_keys_rotate(client):
    rev = login(client, "reviewer")
    assert (
        client.post(
            "/alerts/sources",
            headers=rev,
            json={
                "kind": "sms",
                "name": "x phone",
                "provider": "ECOCASH",
                "account_alias": "MAIN",
                "allowed_senders": ["EcoCash"],
            },
        ).status_code
        == 403
    )
    src = _source(client)
    admin = login(client, "admin")
    new = client.post(f"/alerts/sources/{src['source_id']}/rotate-key", headers=admin).json()
    assert _sms(client, src["key"]).status_code == 401  # old key dead
    assert _sms(client, new["key"]).status_code == 200
    client.post(f"/alerts/sources/{src['source_id']}/deactivate", headers=admin)
    assert _sms(client, new["key"]).status_code == 401


def test_sms_forwarder_form_payloads(client):
    src = _source(client)
    r = client.post(
        "/alerts/sms",
        headers={"X-POPs-Source-Key": src["key"]},
        data={"sender": "EcoCash", "message": ECOCASH_SMS},
    )
    assert r.json()["status"] == "credited"
    assert uuid.UUID(r.json()["credit_id"])
