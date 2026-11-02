"""Payers on WhatsApp hear the outcome once, in neutral words, through the retried outbox."""

import uuid

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.entitlements import outbox
from app.models import OutboxEvent
from tests.conftest import ecocash_fields, login, make_receipt
from tests.test_flow import _import, _obligation, _stmt_row

WA = "263771112233"


def _whatsapp_claim(client, obligation, *, png=None) -> dict:
    sub = login(client, "submitter")
    r = client.post(
        "/claims",
        headers=sub,
        data={
            "obligation_id": obligation["obligation_id"],
            "claimant_ref": "learner-1",
            "channel": "whatsapp",
        },
        files={"file": ("pop.png", png or make_receipt(ecocash_fields()), "image/png")},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _as_whatsapp_sender(session, claim_id: str) -> None:
    """The route sets submitted_by from the caller; WhatsApp claims carry the sender's number."""
    from app.models import Claim

    row = session.get(Claim, uuid.UUID(claim_id))
    row.submitted_by = f"whatsapp:{WA}"
    session.commit()


def _payer_events(session) -> list[OutboxEvent]:
    session.expire_all()
    return list(
        session.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.event_type == "payer.outcome")
            .order_by(OutboxEvent.created_at)
        )
    )


@pytest.fixture
def wa_configured(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "whatsapp_access_token", "token")
    monkeypatch.setattr(s, "whatsapp_phone_number_id", "12345")
    monkeypatch.setattr(s, "whatsapp_graph_url", "https://graph.test/v21.0")


def _deliver(session, handler) -> int:
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return outbox.deliver_pending(session, client=http)


def test_only_whatsapp_claims_are_messaged(client, session):
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    ob = _obligation(client, sub)
    web = client.post(
        "/claims",
        headers=sub,
        data={"obligation_id": ob["obligation_id"], "claimant_ref": "x", "channel": "ecocash"},
        files={"file": ("pop.png", make_receipt(ecocash_fields()), "image/png")},
    ).json()
    client.post(f"/review/claims/{web['claim_id']}/reject", headers=rev, json={"reason": "OTHER"})
    assert _payer_events(session) == []


def test_rejection_is_reported_without_saying_why(client, session):
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    claim = _whatsapp_claim(client, _obligation(client, sub))
    r = client.post(
        f"/review/claims/{claim['claim_id']}/reject",
        headers=rev,
        json={"reason": "TAMPERED_DOCUMENT", "note": "edited in Canva"},
    )
    assert r.status_code == 200, r.text
    (event,) = _payer_events(session)
    text = event.payload["text"]
    assert "could not confirm" in text
    for word in ("fake", "tamper", "edited", "Canva", "suspect", "fraud"):
        assert word.lower() not in text.lower()
    assert WA not in str(event.payload)  # the number is looked up at send time, never stored


def test_approval_message_is_sent_to_the_sender_once(client, session, wa_configured):
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    ob = _obligation(client, sub)
    claim = _whatsapp_claim(client, ob)
    _as_whatsapp_sender(session, claim["claim_id"])
    _import(client, rev, [_stmt_row()])
    cid = client.get(f"/review/claims/{claim['claim_id']}", headers=rev).json()["candidates"][0][
        "credit"
    ]["credit_id"]
    done = client.post(
        f"/review/claims/{claim['claim_id']}/confirm",
        headers=rev,
        json={"credit_id": cid, "txn_last4": "2345"},
    )
    assert done.json()["claim"]["state"] == "APPROVED"

    sent: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        sent.append(req)
        return httpx.Response(200, json={})

    assert _deliver(session, handler) == 1  # only the payer message: no host webhook configured
    (req,) = sent
    assert req.url.host == "graph.test" and req.url.path.endswith("/12345/messages")
    assert req.headers["Authorization"] == "Bearer token"
    body = req.read().decode()
    assert WA in body and "payment has been confirmed" in body
    assert _deliver(session, handler) == 0  # delivered once


def test_a_failed_send_is_retried_not_lost(client, session, wa_configured):
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    claim = _whatsapp_claim(client, _obligation(client, sub))
    _as_whatsapp_sender(session, claim["claim_id"])
    client.post(f"/review/claims/{claim['claim_id']}/reject", headers=rev, json={"reason": "OTHER"})

    assert _deliver(session, lambda req: httpx.Response(500)) == 0
    (event,) = _payer_events(session)
    assert event.status == "pending" and event.attempts == 1 and event.last_error

    assert _deliver(session, lambda req: httpx.Response(200, json={})) == 1
    assert _payer_events(session)[0].status == "delivered"


def test_without_whatsapp_credentials_messages_wait_instead_of_failing(client, session):
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    claim = _whatsapp_claim(client, _obligation(client, sub))
    client.post(f"/review/claims/{claim['claim_id']}/reject", headers=rev, json={"reason": "OTHER"})
    assert _deliver(session, lambda req: httpx.Response(200)) == 0
    (event,) = _payer_events(session)
    assert event.status == "pending" and event.attempts == 0


def test_more_information_asks_for_a_clearer_copy(client, session):
    sub = login(client, "submitter")
    claim = _whatsapp_claim(
        client, _obligation(client, sub), png=make_receipt(ecocash_fields(), blur=12)
    )
    assert claim["state"] == "NEEDS_INFORMATION"
    (event,) = _payer_events(session)
    assert "blurry" in event.payload["text"].lower()


def test_provisional_access_is_announced_in_neutral_words(client, session):
    admin = login(client, "admin")
    client.post(
        "/admin/policy",
        headers=admin,
        json={"version": "prov-wa", "config": {"provisional_enabled": True}},
    )
    sub = login(client, "submitter")
    claim = _whatsapp_claim(client, _obligation(client, sub))
    assert claim["provisional_until"] is not None
    (event,) = _payer_events(session)
    assert event.payload["kind"] == "provisional"
    assert "Access is open until" in event.payload["text"]


def test_the_message_is_not_duplicated_on_replay(client, session):
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    claim = _whatsapp_claim(client, _obligation(client, sub))
    client.post(f"/review/claims/{claim['claim_id']}/reject", headers=rev, json={"reason": "OTHER"})
    assert (
        outbox.enqueue_event(
            session,
            uuid.UUID(client.get("/me", headers=rev).json()["org_id"]),
            "payer.outcome",
            f"payer:{claim['claim_id']}:rejected",
            {"text": "again", "claim_id": claim["claim_id"], "kind": "rejected"},
        )
        is False
    )
    assert len(_payer_events(session)) == 1
